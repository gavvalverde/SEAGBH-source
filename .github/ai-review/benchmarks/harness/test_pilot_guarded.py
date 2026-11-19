"""Testes do piloto A/B: nenhum socket, nenhum pagamento, nenhum log de modelo."""
import copy
import io
import json
import os
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pilot_guarded import (
    CASE_IDS, PROFILES, build_pilot_request, command, encode_request,
    main, planned_cost_usd, run_one, safe_finding_audit, send_single_request,
)
from run_bench import load_cases, load_ground_truth, evaluate_review
from transport import HttpReply, Problem


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def send(self, *args):
        self.calls.append(args)
        return self.replies.pop(0)


def fake_reply(content='{"summary":"Revisao sem achados","findings":[]}', usage=None, finish="stop"):
    data = {
        "choices": [{"finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": usage or {"completion_tokens": 140,
                           "completion_tokens_details": {"reasoning_tokens": 60}},
    }
    return HttpReply(200, json.dumps(data).encode())


class PilotTests(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def test_dry_run_does_not_require_api_key_or_make_network_request(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["calls_attempted"], 0)
        self.assertEqual(result["reasoning"], {"max_tokens": 2048})
        self.assertLessEqual(result["request_bytes"], 65536)

    def test_only_allowlisted_candidate_and_no_ground_truth_in_prompt(self):
        case = load_cases(case_id=CASE_IDS[0])[0]
        gemini = build_pilot_request(case, "gemini-thinking-2048")
        self.assertEqual(gemini["model"], "google/gemini-2.5-flash")
        self.assertEqual(gemini["max_tokens"], 8192)
        self.assertEqual(gemini["reasoning"], {"max_tokens": 2048})
        self.assertEqual(gemini["response_format"], {"type": "json_object"})
        self.assertEqual(gemini["provider"], {"require_parameters": True})
        self.assertEqual(gemini["temperature"], 1.0)
        compact = build_pilot_request(case, "gemini-thinking-4096-compact")
        self.assertEqual(compact["model"], "google/gemini-2.5-flash")
        self.assertEqual(compact["max_tokens"], 6144)
        self.assertEqual(compact["reasoning"], {"max_tokens": 4096})
        self.assertEqual(compact["response_format"], {"type": "json_object"})
        self.assertEqual(compact["provider"], {"require_parameters": True})
        self.assertEqual(compact["temperature"], 1.0)
        self.assertNotIn("ground_truth", json.dumps(compact))
        deepseek = build_pilot_request(case, "deepseek-baseline")
        thinking = build_pilot_request(case, "deepseek-thinking-16k-8k")
        self.assertEqual(thinking["model"], "deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(thinking["reasoning"], {"max_tokens": 16384})
        self.assertEqual(thinking["max_tokens"], 24576)
        self.assertEqual(thinking["response_format"], {"type": "json_object"})
        self.assertEqual(thinking["provider"], {"require_parameters": True})
        self.assertNotIn("ground_truth", json.dumps(thinking))
        self.assertEqual(deepseek["reasoning"], {"effort": "none"})
        self.assertEqual(deepseek["model"], "deepseek/deepseek-v4.1-flash-1017")
        self.assertNotIn("ground_truth", json.dumps(gemini).lower())
        self.assertNotIn("confidence_expected", json.dumps(gemini))
        self.assertNotIn("delta", case)
        with self.assertRaises(ValueError):
            build_pilot_request(case, "attacker/auto")

    def test_paid_mode_is_blocked_without_multiple_independent_gates(self):
        from argparse import Namespace
        args = Namespace(case=CASE_IDS[0], profile="gemini-thinking-2048",
                         paid=True, confirm_single_paid_call=False, max_usd=0.05)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Problem) as ctx:
                command(args)
            self.assertEqual(ctx.exception.code, "pilot_paid_not_authorized")
            args.confirm_single_paid_call = True
            with self.assertRaises(Problem):
                command(args)
            os.environ["SEAGBH_AI_PILOT_PAID_ENABLED"] = "owner_confirmed"
            args.max_usd = 0.000001
            with self.assertRaises(Problem) as x:
                command(args)
            self.assertEqual(x.exception.code, "pilot_budget_not_approved")
            args.max_usd = 0.05
            os.environ["CI"] = "true"
            with self.assertRaises(Problem) as y:
                command(args)
            self.assertEqual(y.exception.code, "pilot_paid_blocked_in_ci")

    def test_ci_paid_requires_owner_dispatched_trusted_workflow(self):
        from argparse import Namespace
        args = Namespace(case=CASE_IDS[0], profile="gemini-thinking-2048",
                         paid=True, confirm_single_paid_call=True, max_usd=0.05)
        authorized = {
            "CI": "true",
            "SEAGBH_AI_PILOT_TRUSTED_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "gavvalverde/SEAGBH-source",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_ACTOR": "gavvalverde",
            "GITHUB_TRIGGERING_ACTOR": "gavvalverde",
            "GITHUB_RUN_ATTEMPT": "1",
            "SEAGBH_AI_PILOT_PAID_ENABLED": "owner_confirmed",
            "OPENROUTER_API_KEY": "synthetic-key",
        }
        for name in ("SEAGBH_AI_PILOT_TRUSTED_ACTIONS",
                     "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY",
                     "GITHUB_REF", "GITHUB_ACTOR",
                     "GITHUB_TRIGGERING_ACTOR", "GITHUB_RUN_ATTEMPT"):
            with self.subTest(key=name):
                with patch.dict(os.environ, {**authorized, name: "bad"}, clear=True):
                    with self.assertRaises(Problem) as result:
                        command(args)
                    self.assertEqual(result.exception.code, "pilot_paid_blocked_in_ci")
        for bad_attempt in ("2", "3", "0", "", "01"):
            with self.subTest(run_attempt=bad_attempt), patch.dict(
                    os.environ, {**authorized, "GITHUB_RUN_ATTEMPT": bad_attempt},
                    clear=True):
                with self.assertRaises(Problem) as denied:
                    command(args)
                self.assertEqual(denied.exception.code, "pilot_paid_blocked_in_ci")
        with patch.dict(os.environ, authorized, clear=True), patch(
            "pilot_guarded.run_one", return_value={"calls_attempted": 1}
        ) as mocked:
            result = command(args)
        self.assertEqual(result["calls_attempted"], 1)
        mocked.assert_called_once_with("gemini-thinking-2048",
                                       CASE_IDS[0], "synthetic-key")

    def test_manual_workflow_is_explicit_owner_only_and_pinned(self):
        from pathlib import Path
        import yaml
        text = (Path(__file__).resolve().parents[3]
                / "workflows" / "ai-review-pilot.yml").read_text()
        value = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(set(value["on"]), {"workflow_dispatch"})
        job = value["jobs"]["single_paid_pilot"]
        self.assertIn("vars.AI_REVIEW_PILOT_ENABLED == 'true'", job["if"])
        self.assertIn("github.actor == 'gavvalverde'", job["if"])
        self.assertIn("github.triggering_actor == 'gavvalverde'", job["if"])
        self.assertIn("github.run_attempt == '1'", job["if"])
        self.assertIn("inputs.mode == 'EXECUTE_PAID'", job["if"])
        self.assertIn("inputs.confirm == 'ONE_PAID_CALL'", job["if"])
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(value["permissions"], {"contents": "read"})
        checkout = job["steps"][0]
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(checkout["with"]["ref"], "${{ github.workflow_sha }}")
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        dispatch_inputs = value["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(dispatch_inputs["case_id"]["options"]), set(CASE_IDS))
        self.assertEqual(len(CASE_IDS), 23)
        self.assertEqual(len(set(CASE_IDS)), 23)
        from pathlib import Path
        all_fixtures = {
            p.stem for p in (Path(__file__).resolve().parents[1] / "cases").glob("*.json")
        }
        all_truth = {
            p.stem for p in (Path(__file__).resolve().parents[2] / "ground-truth").glob("*.json")
        }
        self.assertEqual(set(CASE_IDS), all_fixtures)
        self.assertEqual(set(CASE_IDS), all_truth)
        self.assertEqual(dispatch_inputs["mode"]["default"], "CHECK_ONLY")
        self.assertEqual(set(dispatch_inputs["mode"]["options"]),
                         {"CHECK_ONLY", "EXECUTE_PAID"})
        self.assertEqual(dispatch_inputs["reasoning_profile"]["default"],
                         "gemini-thinking-2048")
        self.assertEqual(set(dispatch_inputs["reasoning_profile"]["options"]),
                         {"gemini-thinking-2048", "gemini-thinking-4096-compact",
                          "deepseek-thinking-16k-8k"})
        diagnostic = value["jobs"]["diagnose_authorization"]
        self.assertEqual(diagnostic["permissions"], {})
        self.assertNotIn("if", diagnostic)
        self.assertEqual(len(diagnostic["steps"]), 1)
        diagnostic_step = diagnostic["steps"][0]
        self.assertNotIn("uses", diagnostic_step)
        self.assertNotIn("secrets.", json.dumps(diagnostic))
        self.assertNotIn("OPENROUTER_API_KEY", json.dumps(diagnostic))
        expected_gates = {
            "GATE_VARIABLE": "vars.AI_REVIEW_PILOT_ENABLED == 'true'",
            "GATE_REPOSITORY": "github.repository == 'gavvalverde/SEAGBH-source'",
            "GATE_ACTOR": "github.actor == 'gavvalverde'",
            "GATE_TRIGGERING_ACTOR": "github.triggering_actor == 'gavvalverde'",
            "GATE_FIRST_ATTEMPT": "github.run_attempt == '1'",
            "GATE_EVENT": "github.event_name == 'workflow_dispatch'",
            "GATE_BRANCH": "github.ref == 'refs/heads/main'",
            "GATE_CONFIRMATION": "inputs.confirm == 'ONE_PAID_CALL'",
            "GATE_PAID_MODE": "inputs.mode == 'EXECUTE_PAID'",
        }
        self.assertEqual(set(diagnostic_step["env"]), set(expected_gates))
        for name, expression in expected_gates.items():
            self.assertEqual(diagnostic_step["env"][name], "${{ " + expression + " }}")
            self.assertIn('"' + "$" + name + '"', diagnostic_step["run"])
        self.assertNotIn("curl", diagnostic_step["run"])
        self.assertNotIn("python", diagnostic_step["run"])
        run_step = job["steps"][-1]
        self.assertNotIn("${{", run_step["run"])
        self.assertIn("--max-usd 0.05", run_step["run"])
        self.assertIn("--confirm-single-paid-call", run_step["run"])
        self.assertEqual(run_step["env"]["PILOT_PROFILE"],
                         "${{ inputs.reasoning_profile }}")
        self.assertIn('--profile "$PILOT_PROFILE"', run_step["run"])
        self.assertNotIn("gemini-thinking-4096-compact", run_step["run"])
        self.assertEqual(run_step["env"]["SEAGBH_AI_PILOT_TRUSTED_ACTIONS"], "true")

    def test_pricing_is_estimate_not_hard_cap(self):
        self.assertIsNone(planned_cost_usd(1000, "deepseek-baseline"))
        self.assertGreater(planned_cost_usd(1000, "gemini-thinking-2048"), 0)
        self.assertGreater(planned_cost_usd(1000, "gemini-thinking-4096-compact"), 0)
        self.assertLess(planned_cost_usd(1000, "gemini-thinking-4096-compact"),
                        planned_cost_usd(1000, "gemini-thinking-2048"))
        self.assertLess(planned_cost_usd(65536, "deepseek-thinking-16k-8k"), 0.05)

    def test_one_wire_post_no_retry_and_strict_review(self):
        case = load_cases(case_id=CASE_IDS[0])[0]
        payload = build_pilot_request(case, "gemini-thinking-2048")
        http = FakeHTTP([fake_reply()])
        review, usage = send_single_request(payload, "synthetic-key", http=http)
        self.assertEqual(review["findings"], [])
        self.assertEqual(usage["reasoning_tokens"], 60)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][:3],
                         ("openrouter.ai", "POST", "/api/v1/chat/completions"))
        sent = json.loads(http.calls[0][4])
        self.assertEqual(sent["reasoning"], {"max_tokens": 2048})
        self.assertNotIn("ground_truth", json.dumps(sent))
        self.assertIn("Authorization", http.calls[0][3])
        self.assertEqual(encode_request(payload), http.calls[0][4])

    def test_invalid_or_costly_response_not_published_and_not_retried(self):
        payload = build_pilot_request(load_cases(case_id=CASE_IDS[0])[0],
                                      "gemini-thinking-2048")
        responses = [
            HttpReply(429, b"TOP_SECRET"),
            fake_reply('{"summary":"SENSITIVE","findings":[]}', finish="length"),
            fake_reply("PRIVATE_PROVIDER_TEXT"),
            fake_reply(usage={"completion_tokens": 3000,
                              "completion_tokens_details": {"reasoning_tokens": 2500}}),
        ]
        for response in responses:
            with self.subTest(status=response.status):
                http = FakeHTTP([response])
                with self.assertRaises(Problem):
                    send_single_request(payload, "synthetic-key", http=http)
                self.assertEqual(len(http.calls), 1)

    def test_incomplete_response_reports_safe_finish_code_and_token_counters(self):
        from pilot_guarded import INCOMPLETE_FINISH_CODES
        for finish, expected in (
            ("length", "pilot_incomplete_length"),
            ("content_filter", "pilot_incomplete_content_filter"),
            ("tool_calls", "pilot_incomplete_tool_calls"),
            ("error", "pilot_incomplete_provider_error"),
            ("UPSTREAM_SECRET_DO_NOT_PRINT", "pilot_incomplete_response"),
        ):
            with self.subTest(finish=finish):
                http = FakeHTTP([fake_reply(
                    content="PRIVATE_RESPONSE_82937", finish=finish,
                    usage={"completion_tokens": 24576,
                           "completion_tokens_details": {"reasoning_tokens": 24500}},
                )])
                env = {
                    "OPENROUTER_API_KEY": "synthetic-key",
                    "SEAGBH_AI_PILOT_PAID_ENABLED": "owner_confirmed",
                }
                with patch.dict(os.environ, env, clear=True), patch(
                    "pilot_guarded.HTTPS", return_value=http
                ), redirect_stdout(io.StringIO()) as out:
                    code = main([
                        "--case", "r22-ddl-001",
                        "--profile", "deepseek-thinking-16k-8k",
                        "--paid", "--confirm-single-paid-call",
                        "--max-usd", "0.05",
                    ])
                self.assertEqual(code, 1)
                self.assertEqual(len(http.calls), 1)
                result = json.loads(out.getvalue())
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["reported_usage"], {
                    "completion_tokens": 24576,
                    "reasoning_tokens": 24500,
                    "visible_tokens": 76,
                })
                self.assertEqual(result["requested_max_completion_tokens"], 24576)
                self.assertEqual(result["requested_reasoning_tokens"], 16384)
                self.assertTrue(result["calls_may_be_billed"])
                self.assertNotIn("PRIVATE_RESPONSE", out.getvalue())
                self.assertNotIn("UPSTREAM_SECRET", out.getvalue())
                self.assertNotIn("synthetic-key", out.getvalue())
        self.assertEqual(len(INCOMPLETE_FINISH_CODES), 4)

    def test_incomplete_response_without_valid_usage_exposes_no_provider_data(self):
        from pilot_guarded import _safe_incomplete_usage
        for usage in (
            None, {}, {"completion_tokens": "STOLEN_TEXT"},
            {"completion_tokens": -1},
            {"completion_tokens": 5, "reasoning_tokens": 10, "visible_tokens": -5},
        ):
            with self.subTest(usage=usage):
                if isinstance(usage, dict) and usage.get("completion_tokens") == 5:
                    self.assertEqual(_safe_incomplete_usage(usage),
                                     {"completion_tokens": 5})
                else:
                    self.assertIsNone(_safe_incomplete_usage(usage))
        http = FakeHTTP([fake_reply(
            content="PRIVATE_RESPONSE_8200", finish="length",
            usage={"completion_tokens": "SECRET_UPSTREAM_USAGE"},
        )])
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "synthetic-key",
            "SEAGBH_AI_PILOT_PAID_ENABLED": "owner_confirmed",
        }, clear=True), patch("pilot_guarded.HTTPS", return_value=http), redirect_stdout(
            io.StringIO()
        ) as out:
            code = main([
                "--case", "r22-ddl-001", "--profile", "deepseek-thinking-16k-8k",
                "--paid", "--confirm-single-paid-call", "--max-usd", "0.05",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(len(http.calls), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["code"], "pilot_incomplete_length")
        self.assertIsNone(result["reported_usage"])
        self.assertNotIn("SECRET", out.getvalue())
        self.assertNotIn("PRIVATE_RESPONSE", out.getvalue())

    def test_compact_profile_limits_are_checked_without_network_or_retry(self):
        case = load_cases(case_id=CASE_IDS[0])[0]
        req = build_pilot_request(case, "gemini-thinking-4096-compact")
        self.assertEqual(req["max_tokens"] - req["reasoning"]["max_tokens"], 2048)
        http = FakeHTTP([fake_reply(usage={"completion_tokens": 4500,
                         "completion_tokens_details": {"reasoning_tokens": 4097}})])
        with self.assertRaises(Problem) as exceeded:
            send_single_request(req, "synthetic-key", http=http)
        self.assertEqual(exceeded.exception.code, "pilot_reasoning_budget_exceeded")
        self.assertEqual(len(http.calls), 1)

        http2 = FakeHTTP([fake_reply(usage={"completion_tokens": 6150,
                          "completion_tokens_details": {"reasoning_tokens": 3800}})])
        with self.assertRaises(Problem) as total:
            send_single_request(req, "synthetic-key", http=http2)
        self.assertEqual(total.exception.code, "pilot_completion_budget_exceeded")
        self.assertEqual(len(http2.calls), 1)

    def test_compact_profile_is_paid_only_with_explicit_pilot_opt_in(self):
        from argparse import Namespace
        args = Namespace(case=CASE_IDS[0], profile="gemini-thinking-4096-compact",
                         paid=True, confirm_single_paid_call=True, max_usd=0.05)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Problem) as blocked:
                command(args)
            self.assertEqual(blocked.exception.code, "pilot_paid_not_authorized")
            os.environ["SEAGBH_AI_PILOT_PAID_ENABLED"] = "owner_confirmed"
            with self.assertRaises(Problem) as missing_key:
                command(args)
            self.assertEqual(missing_key.exception.code, "pilot_authentication_error")
        with patch.dict(os.environ, {}, clear=True):
            args.paid = False
            dry = command(args)
        self.assertEqual(dry["mode"], "dry_run")
        self.assertEqual(dry["reasoning"], {"max_tokens": 4096})
        self.assertEqual(dry["max_completion_tokens"], 6144)
        self.assertEqual(dry["calls_attempted"], 0)

    def test_deepseek_thinking_profile_bounds_without_network_or_retry(self):
        req = build_pilot_request(load_cases(case_id=CASE_IDS[0])[0],
                                  "deepseek-thinking-16k-8k")
        self.assertEqual(req["max_tokens"] - req["reasoning"]["max_tokens"], 8192)
        for usage, expected_code in (
            ({"completion_tokens": 20000,
              "completion_tokens_details": {"reasoning_tokens": 16385}},
             "pilot_reasoning_budget_exceeded"),
            ({"completion_tokens": 24500,
              "completion_tokens_details": {"reasoning_tokens": 16000}},
             "pilot_visible_budget_exceeded"),
            ({"completion_tokens": 24577,
              "completion_tokens_details": {"reasoning_tokens": 16000}},
             "pilot_completion_budget_exceeded"),
        ):
            with self.subTest(expected_code=expected_code):
                http = FakeHTTP([fake_reply(usage=usage)])
                with self.assertRaises(Problem) as exceeded:
                    send_single_request(req, "synthetic-key", http=http)
                self.assertEqual(exceeded.exception.code, expected_code)
                self.assertEqual(len(http.calls), 1)
        http = FakeHTTP([fake_reply(usage={"completion_tokens": 15000,
                         "completion_tokens_details": {"reasoning_tokens": 14500}})])
        review, usage = send_single_request(req, "synthetic-key", http=http)
        self.assertEqual(review["findings"], [])
        self.assertEqual(usage["visible_tokens"], 500)
        self.assertEqual(len(http.calls), 1)

    def test_deepseek_thinking_paid_guard_and_dry_run(self):
        from argparse import Namespace
        args = Namespace(case=CASE_IDS[0], profile="deepseek-thinking-16k-8k",
                         paid=True, confirm_single_paid_call=True, max_usd=0.05)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(Problem) as blocked:
                command(args)
            self.assertEqual(blocked.exception.code, "pilot_paid_not_authorized")
            os.environ["SEAGBH_AI_PILOT_PAID_ENABLED"] = "owner_confirmed"
            with self.assertRaises(Problem) as missing_key:
                command(args)
            self.assertEqual(missing_key.exception.code, "pilot_authentication_error")
        with patch.dict(os.environ, {}, clear=True):
            args.paid = False
            dry = command(args)
        self.assertEqual(dry["mode"], "dry_run")
        self.assertEqual(dry["model"], "deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(dry["reasoning"], {"max_tokens": 16384})
        self.assertEqual(dry["max_completion_tokens"], 24576)
        self.assertEqual(dry["calls_attempted"], 0)

    def test_score_is_numeric_and_raw_provider_content_is_not_exposed(self):
        http = FakeHTTP([fake_reply()])
        result = run_one("gemini-thinking-2048", CASE_IDS[0],
                         "synthetic-key", http=http)
        self.assertEqual((result["tp"], result["fn"]), (0, 1))
        self.assertEqual(result["calls_attempted"], 1)
        self.assertFalse(result["review_published"])
        self.assertTrue(result["reasoning_budget_verified"])
        self.assertNotIn("summary", result)
        self.assertNotIn("review", result)

    def test_audit_is_bounded_structured_and_never_leaks_provider_text(self):
        case_id = "r22-typ-001"
        model_secret = "NEVER_LOG_THIS_PROVIDER_CONTENT_9381"
        finding = {
            "severity": "minor",
            "category": "exception-handling",
            "path": "src/core/sync_outbox.py",
            "line": 109,
            "title": "TypeError on list operation",
            "body": (
                "A lista [] em operacao not in _OPERACOES gera TypeError "
                "antes do ValueError esperado. " + model_secret
            ),
            "evidence": "if operacao not in _OPERACOES:",
            "confidence": "high",
        }
        review = {"summary": "Teste sintético", "findings": [finding]}
        audit = safe_finding_audit(review, load_ground_truth(case_id))
        record = audit["findings"][0]
        self.assertTrue(audit["expected_find"])
        self.assertEqual(audit["finding_count"], 1)
        self.assertTrue(audit["manual_audit_required"])
        self.assertTrue(record["path_matches_expected"])
        self.assertTrue(record["line_near_expected"])
        self.assertTrue(record["semantic_match"])
        self.assertTrue(record["heuristic_match"])
        self.assertEqual(record["causal_cues"], {
            "type_error": True, "unhashable_input": True, "membership_site": True
        })
        rendered = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn(model_secret, rendered)
        self.assertNotIn(finding["path"], rendered)
        self.assertNotIn(finding["title"], rendered)
        self.assertNotIn(finding["body"], rendered)
        self.assertNotIn(finding["evidence"], rendered)
        http = FakeHTTP([fake_reply(content=json.dumps(review))])
        result = run_one("deepseek-thinking-16k-8k", case_id,
                         "synthetic-key", http=http)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 0, 0))
        self.assertEqual(result["audit"], audit)
        self.assertNotIn(model_secret, json.dumps(result))
        self.assertNotIn("review", result)
        self.assertNotIn("summary", result)

    def test_negative_cases_are_clean_and_audit_marks_each_finding_as_unmatched(self):
        for case_id in ("r22-ddl-001", "r22-ci-001"):
            with self.subTest(case_id=case_id):
                self.assertEqual(len(load_cases(case_id=case_id)), 1)
                gt = load_ground_truth(case_id)
                self.assertFalse(gt["expected"]["find"])
                clean = {"summary": "Sem defeitos", "findings": []}
                self.assertEqual(
                    evaluate_review(clean, gt)["fp"], 0
                )
                audit = safe_finding_audit(clean, gt)
                self.assertFalse(audit["expected_find"])
                self.assertEqual(audit["finding_count"], 0)
                self.assertEqual(audit["findings"], [])
                noisy = {
                    "summary": "Alerta sintético", "findings": [{
                        "severity": "minor", "category": "other",
                        "path": "SECRET_NONLOGGED_PATH",
                        "line": 1, "title": "Falso positivo",
                        "body": "Texto privado injetado com fake secret",
                        "evidence": "SUPER_SECRET_EVIDENCE_2951",
                        "confidence": "low",
                    }]
                }
                self.assertEqual(evaluate_review(noisy, gt)["fp"], 1)
                flagged = safe_finding_audit(noisy, gt)
                self.assertFalse(flagged["findings"][0]["heuristic_match"])
                self.assertIsNone(flagged["findings"][0]["path_matches_expected"])
                self.assertIsNone(flagged["findings"][0]["causal_cues"])
                self.assertNotIn("SUPER_SECRET", json.dumps(flagged))
                self.assertNotIn("SECRET_NONLOGGED_PATH", json.dumps(flagged))

    def test_sqlite_default_ddl_autocommits_before_next_rollback(self):
        # Regression witness for r22-ddl-001: no network, no model.
        with sqlite3.connect(":memory:") as conn:
            self.assertFalse(conn.in_transaction)
            conn.execute("CREATE TABLE seagbh_sync_outbox (id INTEGER)")
            self.assertFalse(conn.in_transaction)
            conn.execute("CREATE INDEX ix_seagbh_outbox ON seagbh_sync_outbox(id)")
            self.assertFalse(conn.in_transaction)
            conn.rollback()
            found = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='seagbh_sync_outbox'"
            ).fetchone()
            self.assertEqual(found, ("seagbh_sync_outbox",))

    def test_absent_reasoning_counters_do_not_count_as_verified(self):
        response = fake_reply(usage={"completion_tokens": 130})
        output = run_one("gemini-thinking-2048", CASE_IDS[0],
                         "synthetic-key", http=FakeHTTP([response]))
        self.assertFalse(output["reasoning_budget_verified"])
        self.assertNotIn("reasoning_tokens", output["usage"])

    def test_exceeding_total_completion_budget_fails_closed(self):
        req = build_pilot_request(load_cases(case_id=CASE_IDS[0])[0],
                                  "gemini-thinking-2048")
        response = fake_reply(usage={"completion_tokens": 8200,
                       "completion_tokens_details": {"reasoning_tokens": 1000}})
        with self.assertRaises(Problem) as result:
            send_single_request(req, "synthetic-key", http=FakeHTTP([response]))
        self.assertEqual(result.exception.code, "pilot_completion_budget_exceeded")

    def test_cli_never_prints_sensitive_provider_error(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as capture:
            status = main(["--paid", "--confirm-single-paid-call",
                           "--max-usd", "0.05"])
        self.assertEqual(status, 1)
        self.assertNotIn("OPENROUTER", capture.getvalue())
        self.assertIn("pilot_paid_not_authorized", capture.getvalue())


if __name__ == "__main__":
    unittest.main()
