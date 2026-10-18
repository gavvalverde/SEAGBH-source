"""Offline orchestration, HTTP and workflow security contracts. Network is blocked."""
import copy
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
PATCH = "@@ -1,2 +1,2 @@\n keep\n-old\n+new"
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
             "title": "Synthetic defect", "body": "Synthetic concrete failure", "confidence": "high", "evidence": "new(value)"}
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
        self.assertEqual((expected["model"], expected["temperature"], expected["max_tokens"]), ("minimax/minimax-m2.7", 0.3, 2500))

    def test_valid_empty_review_can_publish(self):
        out, gh, ai = self.run_case(model=FakeModel(review([])))
        self.assertTrue(out.published)
        self.assertIn("Nenhum finding no escopo", gh.posts[0][1])

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
        with self.assertRaises(Problem): r.render(raw, collected, pinned, BOT, "v1")


class Transport(Offline):
    def test_openrouter_200_returns_content_for_core_validation(self):
        http = FakeHTTP([envelope()]);model = OpenRouter("synthetic-key", http=http)
        self.assertEqual(model.complete(core.build_payload("t", "b", "d")), review())
        self.assertEqual(http.calls[0][0:3], ("openrouter.ai", "POST", "/api/v1/chat/completions"))

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

    def test_oversized_response_is_rejected(self):
        http = FakeHTTP([HttpReply(200, b'x' * (OpenRouter.MAX_RESPONSE + 1))])
        with self.assertRaises(Problem) as got: OpenRouter("x", http=http).complete({})
        self.assertEqual(got.exception.code, "response_too_large")

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
