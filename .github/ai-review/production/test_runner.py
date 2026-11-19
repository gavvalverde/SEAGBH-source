"""Offline orchestration, HTTP and workflow security contracts. Network is blocked."""
import copy
import base64
import io
import http.client
import os
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import yaml
import core
import runner as r
from transport import HTTPS, HttpReply, OpenRouter, Problem

BASE = "a" * 40
HEAD = "b" * 40
NEW_HEAD = "c" * 40
FILE = "src/example.py"
PATCH = "@@ -1,2 +1,2 @@\n keep\n-old\n+new_value = new(value)"
BOT = 123


def pr():
    return {"number": 7, "state": "open", "draft": False, "title": "Synthetic PR",
            "body": "Synthetic description", "changed_files": 1,
            "head": {"sha": HEAD, "repo": {"full_name": r.REPOSITORY}},
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": r.REPOSITORY}}}


def event():
    return {"number": 7, "action": "opened", "repository": {"full_name": r.REPOSITORY, "default_branch": "main"}, "pull_request": pr()}


def file(path=FILE, patch=PATCH, additions=1, deletions=1):
    return {"filename": path, "status": "modified", "patch": patch, "additions": additions, "deletions": deletions}


def finding(**changes):
    value = {"severity": "important", "category": "regression", "path": FILE, "line": 2,
             "title": "Synthetic defect", "body": "Synthetic concrete failure", "confidence": "high", "evidence": "new_value = new(value)"}
    value.update(changes)
    return value


def review(findings=None):
    return json.dumps({"summary": "Synthetic review", "findings": [finding()] if findings is None else findings})


def row(body, sha=HEAD, user=None):
    return {"body": body, "commit_id": sha, "state": "COMMENTED",
            "user": user or {"id": BOT, "login": r.BOT_LOGIN, "type": "Bot"}}


class FakeGitHub:
    def __init__(self):
        self.current = pr()
        self.files = [file()]
        self.rows = []
        self.posts = []
        self.comparisons = []
        self.post_error = None
        self.accept_then_error = False
        self.sources = {}
        self.source_requests = []

    def source_at(self, sha, path):
        self.source_requests.append((sha, path))
        return self.sources.get(path)

    def pr(self, number):
        assert number == 7
        return copy.deepcopy(self.current)

    def compare(self, pinned):
        self.comparisons.append(pinned)
        return {"base_commit": {"sha": BASE}, "files": copy.deepcopy(self.files)}

    def bot(self):
        return BOT

    def reviews(self, number):
        return copy.deepcopy(self.rows)

    def post(self, pinned, body):
        if self.post_error and not self.accept_then_error:
            raise self.post_error
        self.posts.append((pinned, body))
        result = row(body)
        self.rows.append(result)
        if self.post_error:
            raise self.post_error
        return copy.deepcopy(result)


class FakeModel:
    def __init__(self, content=None):
        self.content = review() if content is None else content
        self.payloads = []
        self.after = None

    def complete(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        if self.after:
            self.after()
        return self.content


class FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def send(self, *args):
        self.calls.append(args)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def envelope(content=None, **choice_changes):
    choice = {"finish_reason": "stop", "message": {"role": "assistant", "content": review() if content is None else content}}
    choice.update(choice_changes)
    return HttpReply(200, json.dumps({"choices": [choice]}).encode())


class Offline(unittest.TestCase):
    def setUp(self):
        block = patch("socket.socket", side_effect=AssertionError("Real network forbidden"))
        block.start()
        self.addCleanup(block.stop)


class Integration(Offline):
    def run_case(self, ev=None, enabled="true", github=None, model=None, version="test-v1"):
        gh, ai = github or FakeGitHub(), model or FakeModel()
        out = r.run_review(ev or event(), enabled, gh, ai, version=version)
        return out, gh, ai

    def test_internal_pr_publishes_one_validated_review(self):
        out, gh, ai = self.run_case()
        self.assertEqual(out.code, "published")
        self.assertTrue(out.published)
        self.assertEqual(out.exit_code, 0)
        self.assertEqual(len(ai.payloads), 1)
        self.assertEqual(len(gh.posts), 1)
        self.assertEqual(gh.posts[0][0].head_sha, HEAD)

    def test_fork_draft_wrong_base_and_disabled_never_call_model(self):
        for change, code in [("fork", "skip_fork"), ("draft", "skip_draft"), ("base", "skip_base")]:
            ev = event()
            if change == "fork": ev["pull_request"]["head"]["repo"]["full_name"] = "attacker/fork"
            if change == "draft": ev["pull_request"]["draft"] = True
            if change == "base": ev["pull_request"]["base"]["ref"] = "dev"
            out, gh, ai = self.run_case(ev)
            self.assertEqual(out.code, code)
            self.assertFalse(ai.payloads or gh.posts)
        for flag in [None, "", "false", "FALSE", "TRUE", "True", "1", "unexpected", True]:
            out, gh, ai = self.run_case(enabled=flag)
            self.assertEqual(out.code, "skip_disabled")
            self.assertFalse(ai.payloads or gh.posts)

    def test_wrong_repository_or_pr_identity_fails_closed(self):
        ev = event()
        ev["repository"]["full_name"] = "attacker/repo"
        out, gh, ai = self.run_case(ev)
        self.assertEqual(out.exit_code, 1)
        self.assertFalse(ai.payloads or gh.posts)
        ev = event();ev["number"] = 8
        self.assertEqual(self.run_case(ev)[0].code, "invalid_pr_identity")

    def test_fixed_sha_collection_preserves_lines_and_patch(self):
        gh = FakeGitHub()
        pinned = r.event_snapshot(event(), "true")
        got = r.collect(gh, pinned)
        self.assertIn(PATCH, got.diff)
        self.assertEqual(gh.comparisons[0].base_sha, BASE)
        self.assertEqual(gh.comparisons[0].head_sha, HEAD)
        self.assertEqual(got.scope.files[FILE].diff_ranges, ((1, 2),))
        self.assertEqual(got.manifest["files"][0]["added_new"], [[2, 2]])
        self.assertEqual(got.manifest["files"][0]["removed_old"], [[2, 2]])
        self.assertEqual(got.manifest["files"][0]["context_new"], [[1, 1]])
        self.assertFalse(got.manifest["partial"])

    def test_missing_or_truncated_patch_stops_before_paid_request(self):
        for value in [None, "", "@@ -1,2 +1,2 @@\n keep\n-old"]:
            gh = FakeGitHub();gh.files[0]["patch"] = value
            out, gh, ai = self.run_case(github=gh)
            self.assertEqual(out.exit_code, 1)
            self.assertIn(out.code, {"diff_missing", "diff_incomplete"})
            self.assertFalse(ai.payloads or gh.posts)

    def test_patch_counter_mismatch_and_missing_file_are_rejected(self):
        for missing in [False, True]:
            gh = FakeGitHub()
            if missing: gh.files = []
            else: gh.files[0]["additions"] = 2
            out, gh, ai = self.run_case(github=gh)
            self.assertEqual(out.code, "diff_incomplete")
            self.assertFalse(ai.payloads or gh.posts)

    def test_unsafe_or_duplicate_paths_are_collection_errors(self):
        for path in ["../outside.py", "/absolute.py", "C:/secret", "src\\bad.py"]:
            gh = FakeGitHub();gh.files[0]["filename"] = path
            out, gh, ai = self.run_case(github=gh)
            self.assertEqual(out.code, "diff_invalid_path")
            self.assertFalse(ai.payloads or gh.posts)
        gh = FakeGitHub();gh.current["changed_files"] = 2;gh.files *= 2
        ev = event();ev["pull_request"]["changed_files"] = 2
        self.assertEqual(self.run_case(ev, github=gh)[0].code, "diff_invalid_path")

    def test_budget_omission_is_explicit_and_publication_partial(self):
        gh = FakeGitHub();ev = event()
        gh.files = [file("src/large.py", "@@ -0,0 +1 @@\n+" + "x" * 40000, 1, 0), file()]
        gh.current["changed_files"] = 2;ev["pull_request"]["changed_files"] = 2
        out, gh, ai = self.run_case(ev, github=gh, model=FakeModel(review([])))
        self.assertTrue(out.published)
        self.assertIn("PARCIAL", gh.posts[0][1])
        self.assertIn("[PARTIAL REVIEW]", ai.payloads[0]["messages"][1]["content"])
        self.assertNotIn("x" * 40000, ai.payloads[0]["messages"][1]["content"])

    def test_file_limit_stops_before_model(self):
        ev = event();gh = FakeGitHub()
        ev["pull_request"]["changed_files"] = 51;gh.current["changed_files"] = 51
        out, gh, ai = self.run_case(ev, github=gh)
        self.assertEqual(out.code, "diff_file_limit")
        self.assertFalse(ai.payloads or gh.posts)

    def test_payload_is_exact_shared_contract(self):
        gh = FakeGitHub();pinned = r.event_snapshot(event(), "true");collected = r.collect(gh, pinned)
        out, _, ai = self.run_case(github=gh)
        expected = core.build_payload(pinned.title, pinned.body, collected.diff,
            context=json.dumps(collected.manifest, ensure_ascii=True, sort_keys=True), trunc_note="", omit_info="")
        self.assertEqual(ai.payloads[0], expected)
        self.assertEqual((expected["model"], expected["temperature"], expected["max_tokens"]), ("deepseek/deepseek-v4.1-flash-1017", 0.3, 8192))
        self.assertEqual(expected["reasoning"], {"effort": "none"})
        self.assertEqual(expected["response_format"], {"type": "json_object"})
        self.assertEqual(expected["provider"], {"require_parameters": True})

    def test_source_snippet_uses_pinned_sha_and_does_not_expand_review_scope(self):
        gh = FakeGitHub()
        gh.sources[FILE] = "\n".join(f"line_{i} = {i}" for i in range(1, 130)) + "\n"
        gh.sources["src/unmodified.py"] = "UNRELATED_SENTINEL"
        out, gh, ai = self.run_case(github=gh, model=FakeModel(review([])))
        self.assertTrue(out.published)
        self.assertEqual(gh.source_requests, [(HEAD, FILE)])
        prompt = ai.payloads[0]["messages"][1]["content"]
        self.assertIn("source_snippets", prompt)
        self.assertIn("L14: line_14", prompt)
        self.assertNotIn("L80:", prompt)
        self.assertNotIn("UNRELATED_SENTINEL", prompt)
        collected = r.collect(gh, r.event_snapshot(event(), "true"))
        self.assertEqual(collected.scope.files[FILE].diff_ranges, ((1, 2),))
        excerpts = r.source_context(gh, r.event_snapshot(event(), "true"), collected)
        self.assertLessEqual(len(excerpts[0]["source_lines"].encode()), r.MAX_SOURCE_SNIPPET_BYTES)

    def test_source_mismatch_stops_before_model(self):
        gh = FakeGitHub()
        gh.sources[FILE] = "only one line\n"
        out, gh, model = self.run_case(github=gh)
        self.assertEqual((out.code, out.stage), ("source_mismatch", "collection"))
        self.assertFalse(model.payloads or gh.posts)

    def test_source_metadata_is_validated_before_model(self):
        path = "src/example.py"
        value = {"path": path, "type": "file", "size": 8,
                 "encoding": "base64",
                 "content": base64.b64encode(b"hi\nthere").decode()}
        def client(response):
            http = FakeHTTP([HttpReply(200, json.dumps(response).encode())])
            return r.GitHub("token", http=http), http
        github, http = client(value)
        self.assertEqual(github.source_at(HEAD, path), "hi\nthere")
        self.assertIn("?ref=" + HEAD, http.calls[0][2])
        for invalid in ({**value, "path": "src/other.py"},
                        {**value, "size": 7}, {**value, "content": "!!"}):
            with self.assertRaises(Problem):
                client(invalid)[0].source_at(HEAD, path)
        self.assertIsNone(client({**value, "size": 50000})[0].source_at(HEAD, path))
        with self.assertRaises(Problem):
            client(value)[0].source_at(HEAD, "../invalid.py")

    def test_source_fetch_attempts_capped_even_when_all_files_omitted(self):
        gh = FakeGitHub()
        paths = [f"src/feature_{i}.py" for i in range(6)]
        gh.files = [file(path=p) for p in paths]
        gh.current["changed_files"] = len(paths)
        ev = event()
        ev["pull_request"]["changed_files"] = len(paths)
        outcome, gh, ai = self.run_case(
            ev=ev, github=gh, model=FakeModel(review([]))
        )
        self.assertTrue(outcome.published)
        self.assertEqual(gh.source_requests,
                         [(HEAD, p) for p in paths[:r.MAX_SOURCE_FILES]])
        self.assertEqual(len(ai.payloads), 1)

    def test_source_global_bytes_cap(self):
        gh = FakeGitHub()
        names = ["src/first.py", "src/second.py", "src/third.py"]
        gh.files = [file(path=p) for p in names]
        gh.current["changed_files"] = len(names)
        ev = event()
        ev["pull_request"]["changed_files"] = len(names)
        # One very long diff-adjacent function: almost 4 KiB per excerpt.
        for name in names:
            gh.sources[name] = "\n".join("x = '" + "a" * 230 + "'"
                                         for _ in range(40)) + "\n"
        pinned = r.event_snapshot(ev, "true")
        collected = r.collect(gh, pinned)
        excerpts = r.source_context(gh, pinned, collected)
        self.assertLessEqual(sum(len(e["source_lines"].encode())
                                 for e in excerpts), r.MAX_SOURCE_TOTAL_BYTES)
        self.assertLessEqual(len(gh.source_requests), r.MAX_SOURCE_FILES)
        self.assertLessEqual(len(excerpts), r.MAX_SOURCE_FILES)

    def test_verified_and_unverified_textual_evidence_is_labeled_conservatively(self):
        gh = FakeGitHub()
        # A literal line quote is detected only for a concrete changed line.
        gh.files[0]["patch"] = "@@ -1,2 +1,2 @@\n keep\n-old\n+new_func(value)"
        complete = finding(evidence="new_func(value) produces the failure")
        out, gh, _ = self.run_case(github=gh, model=FakeModel(review([complete])))
        self.assertTrue(out.published)
        self.assertIn("citação da linha localizada no diff", gh.posts[0][1])
        self.assertIn("não comprova a existência do defeito", gh.posts[0][1])

        gh2 = FakeGitHub()
        gh2.files[0]["patch"] = "@@ -1,2 +1,2 @@\n keep\n-old\n+new_func(value)"
        out2, gh2, _ = self.run_case(
            github=gh2,
            model=FakeModel(review([finding(evidence="Unrelated problem assumption")]))
        )
        self.assertEqual(out2.code, "quality_manual_review_required")
        self.assertEqual(out2.stage, "quality_gate")
        self.assertEqual(out2.quality_hold_count, 1)
        self.assertFalse(out2.published)
        self.assertFalse(gh2.posts)

    def test_quality_gate_never_silently_approves_unanchored_findings(self):
        for altered in (
            {"evidence": "This is not an exact quote of code"},
            {"line": None, "evidence": "new_value = new(value)"},
        ):
            with self.subTest(altered=altered):
                out, gh, model = self.run_case(
                    model=FakeModel(review([finding(**altered)]))
                )
                # Null line is already rejected by the trusted scope filter.
                self.assertIn(out.code, {
                    "quality_manual_review_required", "filter_error"
                })
                self.assertFalse(gh.posts)
                self.assertEqual(len(model.payloads), 1)
        out, gh, _ = self.run_case(model=FakeModel(review([
            finding(confidence="low", severity="critical")
        ])))
        # Neither declared confidence nor severity is a reason to suppress
        # a concrete, verifiable quote. Human semantic review still required.
        self.assertTrue(out.published)

    def test_quality_gate_holds_entire_review_instead_of_discarding_one_bug(self):
        out, gh, model = self.run_case(model=FakeModel(review([
            finding(), finding(title="Suspicion", evidence="NOT_IN_DIFF")
        ])))
        self.assertEqual(out.code, "quality_manual_review_required")
        self.assertEqual(out.quality_hold_count, 1)
        self.assertEqual(out.exit_code, 1)
        self.assertFalse(gh.posts)
        self.assertEqual(len(model.payloads), 1)

    def test_valid_empty_review_can_publish(self):
        out, gh, ai = self.run_case(model=FakeModel(review([])))
        self.assertTrue(out.published)
        self.assertIn("Nenhum achado validado no escopo", gh.posts[0][1])

    def test_review_is_portuguese_and_uses_configured_model_label(self):
        out, gh, ai = self.run_case(model=FakeModel(review([finding()])))
        self.assertTrue(out.published)
        body = gh.posts[0][1]
        self.assertIn("## Revisão de código por IA | DeepSeek V4.1 Flash 1017", body)
        self.assertNotIn("AI Review (MiniMax M2.7)", body)
        self.assertIn("### Resumo", body)
        self.assertIn("### Achados (1)", body)
        self.assertIn("#### 1. Importante:", body)
        self.assertIn("**Local:** <code>src/example.py</code>, linha 2", body)
        self.assertIn("**Problema, impacto e recomendação**", body)
        self.assertIn("**Evidência no código**", body)
        self.assertIn("**Categoria:** Regressão | **Confiança:** Alta", body)
        self.assertTrue(body.endswith(r.marker(r.event_snapshot(event(), "true"), BOT, "test-v1")))
        self.assertEqual(ai.payloads[0]["model"], "deepseek/deepseek-v4.1-flash-1017")

    def test_model_label_uses_trusted_selected_model_not_pr_text(self):
        pinned = r.event_snapshot(event(), "true")
        collected = r.collect(FakeGitHub(), pinned)
        result = core.process_response(review(), collected.scope,
                                       expected_sha=HEAD, response_sha=HEAD)
        self.assertTrue(result.publishable)
        alt = r.render(result, collected, pinned, BOT, "v1",
                       model_id="minimax/minimax-m2.7")
        self.assertIn("Revisão de código por IA | MiniMax M2.7", alt)
        other = r.render(result, collected, pinned, BOT, "v1",
                         model_id="provider/new-model")
        self.assertIn("Revisão de código por IA | provider/new-model", other)
        for untrusted_id in ["bad\n# Header", "<script>", "", "provider/@someone"]:
            with self.subTest(untrusted_id=untrusted_id):
                with self.assertRaises(Problem) as caught:
                    r.render(result, collected, pinned, BOT, "v1", model_id=untrusted_id)
                self.assertEqual(caught.exception.code, "invalid_model_identifier")

    def test_untrusted_markdown_html_mentions_remain_inert(self):
        payload = json.loads(review([finding(
            title="Defeito [phishing](https://example.invalid) # Teste",
            body="# Cabeçalho\n@everyone <script>alert(1)</script>\n"
                 "[Clique aqui](https://example.invalid) ![img](https://example.invalid)\n"
                 "\x60\x60\x60markdown\n<!-- forged marker -->\n\x60\x60\x60",
            evidence="new_value = new(value)\nquery = '''\n</code></pre><script>@admin</script>\n"
        )]))
        payload["summary"] = "<img src=x onerror=alert(1)> @someone"
        out, gh, _ = self.run_case(model=FakeModel(json.dumps(payload)))
        self.assertTrue(out.published)
        value = gh.posts[0][1]
        for malicious in ["<script>", "<img", "@everyone", "@someone",
                          "[Clique aqui](https://example.invalid)", "<!-- forged marker -->"]:
            self.assertNotIn(malicious, value)
        self.assertIn("&lt;script&gt;", value)
        self.assertIn("&#64;everyone", value)
        self.assertIn("\\[Clique aqui\\]\\(https://example\\.invalid\\)", value)
        self.assertIn("<pre><code>", value)
        self.assertIn("&lt;/code&gt;&lt;/pre&gt;", value)
        self.assertTrue(value.endswith(r.marker(r.event_snapshot(event(), "true"), BOT, "test-v1")))

    def test_invalid_responses_never_publish_or_become_clean(self):
        for content, code in [("", "empty_response"), ('{"summary":42,"findings":[]}', "schema_error"),
                              ('{"summary":,}', "parse_error"), ('{"summary":', "incomplete_response"),
                              (review([finding(line=99)]), "filter_error"),
                              (review([finding(path="src/not-sent.py")]), "filter_error")]:
            out, gh, ai = self.run_case(model=FakeModel(content))
            self.assertEqual(out.code, code)
            self.assertEqual(out.exit_code, 1)
            self.assertFalse(gh.posts)

    def test_removed_findings_do_not_reach_publication(self):
        out, gh, _ = self.run_case(model=FakeModel(review([finding(), finding(line=99, title="REJECTED_SENTINEL")])))
        self.assertTrue(out.published)
        self.assertNotIn("REJECTED_SENTINEL", gh.posts[0][1])
        self.assertIn("Synthetic defect", gh.posts[0][1])

    def test_filtered_review_does_not_republish_original_summary(self):
        value = json.loads(review([finding(), finding(line=99)]))
        value["summary"] = "REJECTED_SUMMARY_SENTINEL"
        out, gh, _ = self.run_case(model=FakeModel(json.dumps(value)))
        self.assertTrue(out.published)
        self.assertNotIn("REJECTED_SUMMARY_SENTINEL", gh.posts[0][1])
        self.assertIn("Synthetic defect", gh.posts[0][1])

    def test_forged_marker_by_other_user_is_not_deduplication(self):
        gh = FakeGitHub();pinned = r.event_snapshot(event(), "true")
        gh.rows = [row("Forged\n" + r.marker(pinned, BOT, "test-v1"), user={"id": 999, "login": "other-user", "type": "User"})]
        self.assertTrue(self.run_case(github=gh)[0].published)

    def test_rerun_same_sha_is_deduplicated(self):
        gh, ai = FakeGitHub(), FakeModel()
        self.assertTrue(self.run_case(github=gh, model=ai)[0].published)
        out, _, _ = self.run_case(github=gh, model=ai)
        self.assertEqual(out.code, "skip_duplicate")
        self.assertEqual(len(gh.posts), 1)
        self.assertEqual(len(ai.payloads), 1)

    def test_old_failure_marker_does_not_block_valid_retry(self):
        gh = FakeGitHub();pinned = r.event_snapshot(event(), "true")
        failure = r.marker(pinned, BOT, "test-v1").replace('"status":"success"', '"status":"error"')
        gh.rows = [row("Failed\n" + failure)]
        self.assertTrue(self.run_case(github=gh)[0].published)

    def test_reviewer_version_is_part_of_deduplication(self):
        gh = FakeGitHub()
        self.assertTrue(self.run_case(github=gh, version="v1")[0].published)
        self.assertTrue(self.run_case(github=gh, version="v2")[0].published)
        self.assertEqual(len(gh.posts), 2)

    def test_pr_updated_before_publication_is_not_published(self):
        gh, ai = FakeGitHub(), FakeModel()
        ai.after = lambda: gh.current["head"].update(sha=NEW_HEAD)
        out, gh, ai = self.run_case(github=gh, model=ai)
        self.assertEqual(out.code, "pr_updated")
        self.assertFalse(gh.posts)

    def test_simultaneous_attempt_is_skipped_before_model(self):
        gh, ai = FakeGitHub(), FakeModel()
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            if not release.wait(5): raise AssertionError("Concurrent test timed out")
        ai.after = block
        results = []
        worker = threading.Thread(target=lambda: results.append(self.run_case(github=gh, model=ai)[0]))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            out, _, second = self.run_case(github=gh)
            self.assertEqual(out.code, "skip_busy")
            self.assertFalse(second.payloads)
        finally:
            release.set();worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0].published)
        self.assertEqual(len(gh.posts), 1)

    def test_publication_failure_is_error_without_retry_post(self):
        gh = FakeGitHub();gh.post_error = Problem("network_timeout")
        out, _, _ = self.run_case(github=gh)
        self.assertEqual(out.code, "publication_uncertain")
        self.assertEqual(out.exit_code, 1)
        self.assertFalse(gh.posts)

    def test_acknowledgment_loss_is_reconciled_then_deduplicated(self):
        gh = FakeGitHub();gh.post_error = Problem("network_timeout");gh.accept_then_error = True
        self.assertEqual(self.run_case(github=gh)[0].code, "published_reconciled")
        self.assertEqual(self.run_case(github=gh)[0].code, "skip_duplicate")
        self.assertEqual(len(gh.posts), 1)

    def test_publication_authentication_failure_remains_distinct(self):
        gh = FakeGitHub();gh.post_error = Problem("authentication_error", 401)
        out, _, _ = self.run_case(github=gh)
        self.assertEqual(out.code, "authentication_error")
        self.assertEqual(out.exit_code, 1)
        self.assertFalse(gh.posts)

    def test_unexpected_failure_has_sanitized_operational_error(self):
        ai = FakeModel()
        ai.after = Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE_SENTINEL"))
        out, gh, _ = self.run_case(model=ai)
        self.assertEqual(out.code, "unexpected_execution_error")
        self.assertEqual(out.exit_code, 1)
        self.assertNotIn("SYNTHETIC_PRIVATE_SENTINEL", repr(out))
        self.assertFalse(gh.posts)

    def test_multiple_hunks_additions_deletions_and_plus_symbols(self):
        patch_text = "@@ -0,0 +1,2 @@\n+one+++two\n+second\n@@ -10 +12,0 @@\n-old"
        coordinates, ranges = r.parse_patch(patch_text, 2, 1)
        self.assertEqual(ranges, [[1, 2]])
        self.assertEqual(coordinates["removed_old"], [[10, 10]])
        gh = FakeGitHub();gh.files = [file(patch=patch_text, additions=2, deletions=1)]
        self.assertIn("one+++two", r.collect(gh, r.event_snapshot(event(), "true")).diff)

    def test_unicode_markdown_quotes_and_shell_strings_are_data(self):
        ev = event();gh = FakeGitHub()
        text = '# Markdown "a??o" ?? $(never-execute) @someone <script>'
        ev["pull_request"]["title"] = text;ev["pull_request"]["body"] = text
        gh.current["title"] = text;gh.current["body"] = text
        name = 'src/a??o [v2] "quoted" $(never-execute).py'
        gh.files[0]["filename"] = name
        out, gh, ai = self.run_case(ev, github=gh, model=FakeModel(review([finding(path=name, body=text)])))
        self.assertTrue(out.published)
        self.assertIn(text, ai.payloads[0]["messages"][1]["content"])
        self.assertIn("&lt;script&gt;", gh.posts[0][1])
        self.assertIn("&#64;someone", gh.posts[0][1])
        self.assertNotIn("<script>", gh.posts[0][1])

    def test_cli_disabled_before_event_clients_or_credentials(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(r, "GitHub") as client, patch("builtins.print"):
            self.assertEqual(r.main(), 0)
            client.assert_not_called()

    def test_cli_rejects_untrusted_execution_context(self):
        with patch.dict("os.environ", {"AI_REVIEW_ENABLED": "true", "GITHUB_EVENT_NAME": "pull_request"}, clear=True), patch.object(r, "GitHub") as client, patch("builtins.print"):
            self.assertEqual(r.main(), 1)
            client.assert_not_called()

    def test_render_cannot_consume_unfiltered_result(self):
        pinned = r.event_snapshot(event(), "true")
        collected = r.collect(FakeGitHub(), pinned)
        raw = core.validate_review(review())
        with self.assertRaises(Problem): r.render(raw, collected, pinned, BOT, "v1", model_id=core.build_payload("t", "b", "d")["model"])


class Transport(Offline):
    def test_model_transport_has_longer_bounded_deadline_than_github(self):
        model = OpenRouter("synthetic-key")
        github = HTTPS()
        self.assertIsInstance(model.http, HTTPS)
        self.assertEqual(model.http.connect_timeout, 5)
        self.assertEqual(model.http.read_timeout, 180)
        self.assertEqual(model.http.total_timeout, 240)
        self.assertEqual(model.http.max_response, OpenRouter.MAX_RESPONSE)
        self.assertEqual((github.connect_timeout, github.read_timeout,
                          github.total_timeout), (5, 30, 60))
        custom_http = FakeHTTP([envelope()])
        self.assertIs(OpenRouter("synthetic-key", http=custom_http).http, custom_http)

    def test_openrouter_200_returns_content_for_core_validation(self):
        http = FakeHTTP([envelope()]);model = OpenRouter("synthetic-key", http=http)
        self.assertEqual(model.complete(core.build_payload("t", "b", "d")), review())
        self.assertEqual(http.calls[0][0:3], ("openrouter.ai", "POST", "/api/v1/chat/completions"))

    def test_openrouter_posts_bounded_nonthinking_payload(self):
        http = FakeHTTP([envelope()])
        payload = core.build_payload("T", "B", "D")
        OpenRouter("synthetic-key", http=http).complete(payload)
        self.assertEqual(len(http.calls), 1)
        sent = json.loads(http.calls[0][4].decode("utf-8"))
        self.assertEqual(sent["reasoning"], {"effort": "none"})
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(sent["provider"], {"require_parameters": True})
        self.assertEqual(sent["max_tokens"], 8192)
        self.assertEqual(sent["model"], "deepseek/deepseek-v4.1-flash-1017")

    def test_json_mode_unavailable_fails_once_without_publication(self):
        http = FakeHTTP([HttpReply(400, b'{"error":"unsupported json mode"}')])
        gh = FakeGitHub()
        result = r.run_review(event(), "true", gh,
                              OpenRouter("synthetic-key", http=http),
                              version="json-mode-test")
        self.assertEqual(result.code, "http_error")
        self.assertEqual(result.stage, "model")
        self.assertFalse(result.published)
        self.assertEqual(len(http.calls), 1)
        self.assertFalse(gh.posts)

    def test_validation_reports_safe_fixed_reason_without_content(self):
        gh = FakeGitHub()
        model = FakeModel("PRIVATE-RAW-PROVIDER-MARKDOWN")
        outcome = r.run_review(event(), "true", gh, model, version="json-diagnostic")
        self.assertEqual(outcome.code, "parse_error")
        self.assertEqual(outcome.stage, "validation")
        self.assertEqual(outcome.validation_reason, "invalid_json")
        self.assertFalse(outcome.published)
        self.assertFalse(gh.posts)

    def test_400_401_do_not_retry(self):
        for code, expected in [(400, "http_error"), (401, "authentication_error")]:
            http = FakeHTTP([HttpReply(code, b'{}')]);sleep = Mock()
            with self.assertRaises(Problem) as got: OpenRouter("x", http=http, sleep=sleep).complete({})
            self.assertEqual(got.exception.code, expected)
            self.assertEqual(len(http.calls), 1);sleep.assert_not_called()

    def test_429_and_5xx_retry_at_most_twice_with_backoff(self):
        for code in [429, 500, 503]:
            http = FakeHTTP([HttpReply(code, b'{}'), envelope()]);sleep = Mock()
            self.assertEqual(OpenRouter("x", http=http, sleep=sleep).complete({}), review())
            self.assertEqual(len(http.calls), 2);sleep.assert_called_once_with(1)
            http = FakeHTTP([HttpReply(code, b'{}'), HttpReply(code, b'{}')])
            with self.assertRaises(Problem): OpenRouter("x", http=http, sleep=Mock()).complete({})
            self.assertEqual(len(http.calls), 2)

    def test_network_failure_is_not_retried(self):
        http = FakeHTTP([Problem("network_timeout")])
        with self.assertRaises(Problem) as got: OpenRouter("x", http=http, sleep=Mock()).complete({})
        self.assertEqual(got.exception.code, "network_timeout")
        self.assertEqual(len(http.calls), 1)

    def test_length_diagnostic_exposes_only_bounded_numeric_usage(self):
        def length_reply(usage):
            value = json.loads(envelope("NEVER-LOG-RESPONSE", finish_reason="length").body)
            value["usage"] = usage
            value["secret_provider_field"] = "PRIVATE-DATA-DO-NOT-LOG"
            return HttpReply(200, json.dumps(value).encode())

        for usage, expected in [
            ({"completion_tokens": 16384,
              "completion_tokens_details": {"reasoning_tokens": 14000},
              "raw_secret": "PRIVATE-UNTRUSTED"},
             {"completion_tokens": 16384, "reasoning_tokens": 14000,
              "visible_tokens": 2384}),
            ({"completion_tokens": 16384,
              "completion_tokens_details": {"reasoning_tokens": "PRIVATE"}},
             {"completion_tokens": 16384}),
            ({"completion_tokens": True}, None),
            ({"completion_tokens": "PRIVATE"}, None),
            ({"completion_tokens": -1}, None),
            ({"completion_tokens": 2000000}, None),
            ({"completion_tokens": 10,
              "completion_tokens_details": {"reasoning_tokens": 20}},
             {"completion_tokens": 10}),
        ]:
            with self.subTest(usage=expected):
                http = FakeHTTP([length_reply(usage)])
                with self.assertRaises(Problem) as caught:
                    OpenRouter("synthetic-key", http=http).complete({})
                self.assertEqual(caught.exception.code, "model_output_limit")
                self.assertEqual(caught.exception.usage, expected)
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(len(http.calls), 1)

        gh = FakeGitHub()
        model_http = FakeHTTP([length_reply({"completion_tokens": 16384,
            "completion_tokens_details": {"reasoning_tokens": 15000}})])
        result = r.run_review(event(), "true", gh,
                              OpenRouter("synthetic-key", http=model_http),
                              version="numeric-diagnostics")
        self.assertEqual(result.code, "model_output_limit")
        self.assertEqual(result.stage, "model")
        self.assertFalse(result.published)
        self.assertEqual(result.usage, {"completion_tokens": 16384,
                                        "reasoning_tokens": 15000,
                                        "visible_tokens": 1384})
        self.assertFalse(gh.posts)

    def test_nonthinking_setting_ignored_by_provider_fails_closed(self):
        reply = json.loads(envelope(review()).body)
        reply["usage"] = {
            "completion_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 91},
        }
        wire = HttpReply(200, json.dumps(reply).encode("utf-8"))
        http = FakeHTTP([wire])
        with self.assertRaises(Problem) as caught:
            OpenRouter("synthetic-key", http=http).complete(
                core.build_payload("T", "B", "D")
            )
        self.assertEqual(caught.exception.code, "reasoning_control_ignored")
        self.assertEqual(caught.exception.usage, {
            "completion_tokens": 100, "reasoning_tokens": 91, "visible_tokens": 9,
        })
        self.assertEqual(len(http.calls), 1)

        github = FakeGitHub()
        result = r.run_review(event(), "true", github,
            OpenRouter("synthetic-key", http=FakeHTTP([wire])),
            version="test-nonthinking")
        self.assertEqual(result.code, "reasoning_control_ignored")
        self.assertFalse(result.published)
        self.assertFalse(github.posts)

    def test_nonthinking_valid_completion_passes_through(self):
        http = FakeHTTP([envelope(review())])
        outcome = r.run_review(event(), "true", FakeGitHub(),
            OpenRouter("synthetic-key", http=http), version="nonthink-ok")
        self.assertTrue(outcome.published)
        self.assertEqual(len(http.calls), 1)

    def test_oversized_response_is_rejected(self):
        http = FakeHTTP([HttpReply(200, b'x' * (OpenRouter.MAX_RESPONSE + 1))])
        with self.assertRaises(Problem) as got: OpenRouter("x", http=http).complete({})
        self.assertEqual(got.exception.code, "response_too_large")

    def test_finish_reasons_are_sanitized_no_retry_and_never_publish(self):
        cases = [
            ("length", "model_output_limit"),
            ("content_filter", "model_content_filter"),
            ("error", "model_generation_error"),
            ("tool_calls", "incomplete_response"),
            ("provider-PRIVATE-SENTINEL", "incomplete_response"),
            (None, "incomplete_response"),
        ]
        for finish_reason, expected_code in cases:
            with self.subTest(finish_reason=finish_reason):
                http = FakeHTTP([envelope("SENSITIVE-RESPONSE-CONTENT",
                                          finish_reason=finish_reason)])
                sleep = Mock()
                model = OpenRouter("synthetic-key", http=http, sleep=sleep)
                with self.assertRaises(Problem) as caught:
                    model.complete(core.build_payload("t", "b", "d"))
                self.assertEqual(caught.exception.code, expected_code)
                self.assertNotIn("SENSITIVE", str(caught.exception))
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(len(http.calls), 1)
                sleep.assert_not_called()

    def test_bounded_model_finish_reason_preserves_fail_closed_runner(self):
        for reason, code in [("length", "model_output_limit"),
                             ("content_filter", "model_content_filter"),
                             ("error", "model_generation_error")]:
            with self.subTest(reason=reason):
                http = FakeHTTP([envelope(review(), finish_reason=reason)])
                github = FakeGitHub()
                outcome = r.run_review(
                    event(), "true", github,
                    OpenRouter("synthetic-key", http=http), version="synthetic-fixture"
                )
                self.assertFalse(github.posts)
                self.assertEqual(outcome.code, code)
                self.assertEqual(outcome.stage, "model")
                self.assertFalse(outcome.published)
                self.assertEqual(outcome.exit_code, 1)
                self.assertEqual(len(http.calls), 1)

    def test_truncation_api_errors_and_unexpected_envelopes(self):
        values = [envelope(review([]), finish_reason="length"), HttpReply(200, b'{"error":{}}'),
                  HttpReply(200, b'[]'), HttpReply(200, b'{"choices":[]}'), HttpReply(200, b'broken'),
                  envelope(message={"role": "assistant", "content": [], "tool_calls": []})]
        for reply in values:
            with self.assertRaises(Problem): OpenRouter("x", http=FakeHTTP([reply])).complete({})

    def test_read_and_connect_limits_without_network(self):
        response = Mock();response.isclosed.return_value = False;response.status = 200;response.getheader.return_value = None
        response.read1.side_effect = [b'abc', b'']
        connection = Mock();connection.getresponse.return_value = response
        factory = Mock(return_value=connection)
        got = HTTPS(factory=factory, max_response=5).send("api.github.com", "GET", "/x", {})
        self.assertEqual(got.body, b'abc')
        factory.assert_called_once_with("api.github.com", timeout=5)
        self.assertEqual(connection.sock.settimeout.call_args_list[0].args, (30,))
        connection.close.assert_called_once()
        response.read1.side_effect = [b'123456']
        with self.assertRaises(Problem) as error: HTTPS(factory=factory, max_response=5).send("api.github.com", "GET", "/x", {})
        self.assertEqual(error.exception.code, "response_too_large")
        connection.connect.side_effect = TimeoutError()
        with self.assertRaises(Problem) as error: HTTPS(factory=factory).send("api.github.com", "GET", "/x", {})
        self.assertEqual(error.exception.code, "network_timeout")

    def test_declared_length_truncation_and_read_timeout_are_errors(self):
        response = Mock();response.isclosed.return_value = False;response.status = 200;response.getheader.return_value = "5"
        response.read1.side_effect = [b"abc", b""]
        connection = Mock();connection.getresponse.return_value = response
        with self.assertRaises(Problem) as error:
            HTTPS(factory=Mock(return_value=connection)).send("api.github.com", "GET", "/x", {})
        self.assertEqual(error.exception.code, "incomplete_response")
        response.getheader.return_value = None;response.read1.side_effect = TimeoutError()
        with self.assertRaises(Problem) as error:
            HTTPS(factory=Mock(return_value=connection)).send("api.github.com", "GET", "/x", {})
        self.assertEqual(error.exception.code, "network_timeout")

    def test_http_redirects_never_forward_credentials(self):
        http = FakeHTTP([HttpReply(302, b'{}')])
        with self.assertRaises(Problem): OpenRouter("x", http=http).complete({})
        self.assertEqual(len(http.calls), 1)

    def test_github_authentication_and_pagination_limits(self):
        api = r.GitHub("synthetic-token", http=FakeHTTP([HttpReply(401, b'{}')]))
        with self.assertRaises(Problem) as error: api.pr(7)
        self.assertEqual(error.exception.code, "authentication_error")
        http = FakeHTTP([HttpReply(200, json.dumps([{}] * 100).encode()) for _ in range(10)])
        with self.assertRaises(Problem): r.GitHub("x", http=http).reviews(7)
        self.assertEqual(len(http.calls), 10)


class Workflow(Offline):
    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).resolve().parents[2] / "workflows/ai-review.yml").read_text(encoding="utf-8")
        cls.data = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.job = cls.data["jobs"]["review"]

    def test_trusted_trigger_and_explicit_activation_gate(self):
        self.assertEqual(set(self.data["on"]), {"pull_request_target"})
        self.assertEqual(self.data["on"]["pull_request_target"]["branches"], ["main"])
        self.assertIn("ready_for_review", self.data["on"]["pull_request_target"]["types"])
        condition = self.job["if"]
        for required in ["vars.AI_REVIEW_ENABLED == 'true'", "head.repo.full_name == 'gavvalverde/SEAGBH-source'",
                         "base.ref == 'main'", "draft == false", "state == 'open'"]:
            self.assertIn(required, condition)
        self.assertNotIn("workflow_dispatch", self.data["on"])

    def test_checkout_is_immutable_and_never_pr_head(self):
        checkout = self.job["steps"][1]
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        values = checkout["with"]
        self.assertEqual(values["repository"], r.REPOSITORY)
        self.assertEqual(values["ref"], "${{ github.workflow_sha }}")
        self.assertEqual(values["persist-credentials"], "false")
        self.assertEqual(values["submodules"], "false")
        self.assertNotIn("allow-unsafe-pr-checkout", values)
        self.assertNotIn("head.sha", self.text)
        self.assertNotIn("refs/pull", self.text)

    def test_minimal_permissions_and_serialized_concurrency(self):
        self.assertEqual(self.data["permissions"], {})
        self.assertEqual(self.job["permissions"], {"contents": "read", "pull-requests": "write"})
        self.assertIn("pull_request.number", self.data["concurrency"]["group"])
        self.assertEqual(self.data["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(self.job["timeout-minutes"], "10")

    def test_no_pr_code_secret_interpolation_or_silent_failure(self):
        steps = self.job["steps"]
        self.assertEqual(len(steps), 4)
        for step in steps:
            if "run" in step:
                self.assertNotIn("${{", step["run"])
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("|| true", step["run"])
                self.assertNotIn("eval ", step["run"])
        self.assertNotIn("secrets", json.dumps(steps[2]))
        self.assertIn("--isolated", steps[2]["run"])
        self.assertIn("python3.12 -m venv", steps[2]["run"])
        self.assertIn("--only-binary=:all:", steps[2]["run"])
        self.assertEqual(steps[3]["env"]["AI_REVIEW_ENABLED"], "${{ vars.AI_REVIEW_ENABLED }}")
        self.assertIn("runner.py", steps[3]["run"])



class MemoryFile(io.BytesIO):
    def __init__(self, owner):
        super().__init__(owner.wire)
        self.owner = owner
        owner.references += 1

    def read1(self, size=-1):
        if self.owner.read_error:
            raise TimeoutError()
        return super().read1(size)

    def close(self):
        if not self.closed:
            self.owner.references -= 1
        super().close()


class MemorySocket:
    """Model socket/file ownership; parsing is the real stdlib HTTPResponse."""
    def __init__(self, wire, read_error=False):
        self.wire, self.read_error = wire, read_error
        self.references = 0
        self.close_requested = False
        self.timeouts = []

    def makefile(self, *args):
        return MemoryFile(self)

    def sendall(self, data):
        pass

    def settimeout(self, value):
        if self.close_requested and self.references == 0:
            raise OSError('synthetic closed socket')
        self.timeouts.append(value)

    def close(self):
        self.close_requested = True


class MemoryConnection(http.client.HTTPConnection):
    def __init__(self, host, timeout, wire, read_error=False):
        super().__init__(host, timeout=timeout)
        self.memory_socket = MemorySocket(wire, read_error)
        self.close_count = 0
        self.response = None

    def connect(self):
        self.sock = self.memory_socket

    def getresponse(self):
        self.response = super().getresponse()
        return self.response

    def close(self):
        self.close_count += 1
        super().close()


def wire_response(body, connection='close', chunked=False, declared=None):
    if chunked:
        framing = b'Transfer-Encoding: chunked\r\n'
        body = format(len(body), 'x').encode() + b'\r\n' + body + b'\r\n0\r\n\r\n'
    else:
        framing = b'Content-Length: ' + str(len(body) if declared is None else declared).encode() + b'\r\n'
    return b'HTTP/1.1 200 OK\r\nConnection: ' + connection.encode() + b'\r\n' + framing + b'\r\n' + body


class AdversarialHTTP(Offline):
    def transport(self, wire, **kwargs):
        connections = []
        read_error = kwargs.pop('read_error', False)
        def factory(host, timeout):
            connection = MemoryConnection(host, timeout, wire, read_error)
            connections.append(connection)
            return connection
        return HTTPS(factory=factory, **kwargs), connections

    def assert_closed(self, connections):
        connection = connections[0]
        self.assertGreaterEqual(connection.close_count, 1)
        self.assertTrue(connection.memory_socket.close_requested)
        self.assertEqual(connection.memory_socket.references, 0)
        self.assertTrue(connection.response.isclosed())

    def test_real_http_parser_same_json_keepalive_and_close(self):
        body = envelope().body
        for mode in ['keep-alive', 'close']:
            with self.subTest(connection=mode):
                transport, connections = self.transport(wire_response(body, mode))
                reply = transport.send('openrouter.ai', 'POST', '/synthetic', {})
                self.assertEqual((reply.status, reply.body), (200, body))
                self.assert_closed(connections)

    def test_real_http_parser_chunked_keepalive_and_close(self):
        for mode in ['keep-alive', 'close']:
            with self.subTest(connection=mode):
                transport, connections = self.transport(wire_response(b'{"ok":true}', mode, chunked=True))
                self.assertEqual(transport.send('api.github.com', 'GET', '/synthetic', {}).body, b'{"ok":true}')
                self.assert_closed(connections)

    def test_real_http_parser_close_body_without_content_length(self):
        body = envelope().body
        wire = b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n' + body
        transport, connections = self.transport(wire)
        self.assertEqual(transport.send('api.github.com', 'GET', '/synthetic', {}).body, body)
        self.assert_closed(connections)

    def test_real_http_parser_incomplete_bodies_close_resources(self):
        for wire in [wire_response(b'{}', declared=20),
                     b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n5\r\n{}']:
            with self.subTest(wire=wire):
                transport, connections = self.transport(wire)
                with self.assertRaises(Problem):
                    transport.send('api.github.com', 'GET', '/synthetic', {})
                self.assert_closed(connections)

    def test_real_http_parser_size_limits_length_and_chunked(self):
        for chunked in [False, True]:
            transport, connections = self.transport(wire_response(b'123456', chunked=chunked), max_response=5)
            with self.assertRaises(Problem) as error:
                transport.send('api.github.com', 'GET', '/synthetic', {})
            self.assertEqual(error.exception.code, 'response_too_large')
            self.assert_closed(connections)

    def test_real_http_parser_read_timeout_and_deadline(self):
        for kwargs in [{'read_error': True}, {'clock': Mock(side_effect=[0, 61])}]:
            transport, connections = self.transport(wire_response(b'{}'), **kwargs)
            with self.assertRaises(Problem) as error:
                transport.send('api.github.com', 'GET', '/synthetic', {})
            self.assertEqual(error.exception.code, 'network_timeout')
            self.assert_closed(connections)

    def test_real_http_parser_timeout_settings(self):
        transport, connections = self.transport(wire_response(b'{}'), clock=Mock(side_effect=[0, 40]))
        self.assertEqual(transport.send('api.github.com', 'GET', '/synthetic', {}).body, b'{}')
        connection = connections[0]
        self.assertEqual(connection.timeout, 5)
        self.assertEqual(connection.memory_socket.timeouts, [30, 20])
        self.assert_closed(connections)

    def test_runner_real_close_response_passes_schema_and_publishes(self):
        transport, connections = self.transport(wire_response(envelope().body))
        gh = FakeGitHub()
        with patch.object(core, 'process_response', wraps=core.process_response) as validate:
            out = r.run_review(event(), 'true', gh, OpenRouter('synthetic-key', http=transport), version='wire-test')
        self.assertEqual(out.code, 'published')
        self.assertTrue(out.published)
        validate.assert_called_once()
        self.assertEqual(len(gh.posts), 1)
        self.assert_closed(connections)

    def test_runner_real_close_invalid_json_never_publishes(self):
        transport, connections = self.transport(wire_response(envelope('{"summary":42,"findings":[]}').body))
        gh = FakeGitHub()
        out = r.run_review(event(), 'true', gh, OpenRouter('synthetic-key', http=transport), version='wire-test')
        self.assertEqual((out.code, out.exit_code, out.published), ('schema_error', 1, False))
        self.assertFalse(gh.posts)
        self.assert_closed(connections)


class AdversarialPatches(Offline):
    def test_unicode_lf_crlf_and_no_newline_marker_preserve_scope(self):
        expected = None
        for content in ['plain', 'a\u2028b']:
            for newline in ['\n', '\r\n']:
                text = newline.join(['@@ -1,2 +1,2 @@', ' keep', '-old'+content, '+new'+content,
                                     r'\ No newline at end of file']) + newline
                gh = FakeGitHub();name = 'src/a\u00e7ao.py';gh.files = [file(name, text)]
                got = r.collect(gh, r.event_snapshot(event(), 'true'))
                self.assertIn(text, got.diff)
                actual = (got.scope.files[name].diff_ranges, got.manifest['files'][0]['added_new'],
                          got.manifest['files'][0]['removed_old'], got.manifest['files'][0]['context_new'])
                if expected is None: expected = actual
                self.assertEqual(actual, expected)
                self.assertEqual(actual, (((1, 2),), [[2, 2]], [[2, 2]], [[1, 1]]))

    def test_invalid_coordinates_and_truncation_still_fail(self):
        for text in ['@@ -0 +1 @@\n-old\n+new', '@@ -1 +0 @@\n-old\n+new',
                     '@@ -1,2 +1,2 @@\n keep\n-old', '@@ -1 +1 @@\n-old\n+new\n+extra']:
            with self.assertRaises(Problem):r.parse_patch(text, 1, 1)


class StrictActivation(Offline):
    def test_actual_yaml_gate_script_accepts_only_literal_true(self):
        data = yaml.load((Path(__file__).resolve().parents[2] / 'workflows/ai-review.yml').read_text(encoding='utf8'), Loader=yaml.BaseLoader)
        steps = data['jobs']['review']['steps']
        gate = steps[0]
        self.assertEqual(gate.get('id'), 'activation')
        self.assertEqual(gate.get('shell'), 'bash')
        self.assertNotIn('secrets', json.dumps(gate))
        self.assertEqual(gate['env'], {'AI_REVIEW_ENABLED': '${{ vars.AI_REVIEW_ENABLED }}'})
        bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
        executable = str(bash) if bash.is_file() else shutil.which('bash')
        self.assertIsNotNone(executable, 'bash is required to exercise the actual workflow gate')
        for value in [None, '', 'false', 'FALSE', 'TRUE', 'True', 'true', '1', 'unexpected']:
            with self.subTest(flag=value), tempfile.TemporaryDirectory() as tmp:
                env = dict(os.environ, GITHUB_OUTPUT='gate-output')
                env.pop('AI_REVIEW_ENABLED', None)
                env.pop('GITHUB_TOKEN', None);env.pop('OPENROUTER_API_KEY', None)
                if value is not None:env['AI_REVIEW_ENABLED'] = value
                result = subprocess.run([executable, '--noprofile', '--norc', '-c', gate['run']],
                    cwd=tmp, env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, '')
                self.assertEqual((Path(tmp)/'gate-output').read_text().strip(),
                                 'enabled=true' if value == 'true' else 'enabled=false')
        for step in steps[1:]:
            self.assertEqual(step['if'], "steps.activation.outputs.enabled == 'true'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
