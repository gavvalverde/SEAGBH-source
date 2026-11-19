"""Testes do piloto A/B: nenhum socket, nenhum pagamento, nenhum log de modelo."""
import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pilot_guarded import (
    CASE_IDS, PROFILES, build_pilot_request, command, encode_request,
    main, planned_cost_usd, run_one, send_single_request,
)
from run_bench import load_cases
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
        deepseek = build_pilot_request(case, "deepseek-baseline")
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

    def test_pricing_is_estimate_not_hard_cap(self):
        self.assertIsNone(planned_cost_usd(1000, "deepseek-baseline"))
        self.assertGreater(planned_cost_usd(1000, "gemini-thinking-2048"), 0)

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
