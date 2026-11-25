"""Offline infrastructure controls, not precision measurements of any LLM."""

from copy import deepcopy
from difflib import unified_diff
import json
import unittest
from unittest.mock import patch

import contract_v2 as contract
import planning_v2 as planning
import production_v2 as production
import runner
from test_production_v2 import CLEAN, Model, one_finding, refutation
from test_runner import FakeGitHub, event, file
from transport import Problem


SOURCE = "src/synthetic_policy.py"
TEST_SOURCE = "tests/test_synthetic_policy.py"
BASE = """def meets_minimum(total):
    return total >= 10

def active_names(rows):
    return [name for name, active in rows if active]
"""
HEAD = """def meets_minimum(total):
    return total > 10

def active_names(rows):
    return [row[0] for row in rows if row[1]]
"""
# Only this local test sees the independent oracle label. It is not PR metadata,
# sent to the mock model, nor added to a shared benchmark case.
ORACLE_LABEL = "K68S_PRIVATE_ORACLE_LABEL"


def code_diff(before, after):
    rows = list(unified_diff(before.splitlines(), after.splitlines(), lineterm=""))
    return "\n".join(rows[2:])  # GitHub compare exposes the hunks without file headers.


def context_diff():
    """Force genuine fragment and chunk boundaries using the stock limits."""
    hunks = []
    for i in range(4):
        start = 1 + i * 100
        context = " # synthetic context " + "x" * 170
        hunks.append("\n".join([
            f"@@ -{start},67 +{start + i},68 @@",
            *([context] * 67),
            f"+# synthetic test comment {i}",
        ]))
    return "\n".join(hunks)


class SemanticInfrastructure(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.socket", side_effect=AssertionError("Real network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.ev = event()
        self.ev["pull_request"]["changed_files"] = 2
        self.gh = FakeGitHub()
        self.gh.current["changed_files"] = 2
        self.gh.files = [
            file(SOURCE, code_diff(BASE, HEAD), 2, 2),
            file(TEST_SOURCE, context_diff(), 4, 0),
        ]
        self.pinned = runner.event_snapshot(self.ev, "true")
        self.plan = planning.build_plan(self.gh, self.pinned)
        self.originals = [
            dict(one_finding(self.anchor(SOURCE, 2)),
                 title="Minimum excludes its boundary",
                 trigger="A total exactly equal to the documented minimum",
                 mechanism="Replacing inclusive comparison with strict greater-than rejects equality",
                 impact="A qualifying total is incorrectly rejected"),
            dict(one_finding(self.anchor(SOURCE, 5)),
                 title="Inactive rows are incorrectly included",
                 trigger="The input contains an inactive row",
                 mechanism="Index-based filtering allegedly stops reading the active flag",
                 impact="An inactive name would enter the result"),
        ]
        self.discovery = [
            dict(CLEAN, findings=[item for item in self.originals
                                  if self.plan.anchors[item["anchor_id"]][4] == chunk.index])
            for chunk in self.plan.chunks
        ]

    def anchor(self, path, line):
        return next(row[0] for row in self.plan.anchors.values() if row[1:3] == (path, line))

    def script(self, *replies):
        model = Model([json.dumps(reply) for reply in replies])
        result = production.run_review_v2(self.ev, "true", self.gh, model, version="offline-control")
        return result, model

    def adjudication(self):
        answer = refutation(self.originals, ["retain", "refute"])
        answer["verdicts"][0].update(
            behavior_path="At total 10, the new comparison returns False and the old one returns True",
            countercheck="Neighboring totals 9 and 11 agree; the equality boundary is the regression",
        )
        answer["verdicts"][1].update(
            current_state="documented_constraint",
            behavior_path="For a two-field row, row[1] is still the active predicate",
            countercheck="A false predicate excludes the inactive name before and after the patch",
        )
        return answer

    def test_executable_positive_and_negative_oracles(self):
        # Only fixed, trusted code literals defined in this test are executed.
        before, after = {}, {}
        exec(BASE, before)
        exec(HEAD, after)
        self.assertEqual([before["meets_minimum"](n) for n in (9, 10, 11)],
                         [False, True, True], ORACLE_LABEL)
        self.assertEqual([after["meets_minimum"](n) for n in (9, 10, 11)],
                         [False, False, True], ORACLE_LABEL)
        rows = [("hidden", False), ("shown", True), ("later", True)]
        self.assertEqual(before["active_names"](rows), ["shown", "later"], ORACLE_LABEL)
        self.assertEqual(after["active_names"](rows), ["shown", "later"], ORACLE_LABEL)

    def test_mixed_cases_separate_anchors_adjudication_and_publication(self):
        first = self.plan.chunks[0]
        cited = frozenset(a[0] for a in first.anchors)
        # Both candidates cite actual changed lines, but only one allegation is true.
        valid = contract.validate_reply(json.dumps(self.discovery[0]), cited)
        self.assertEqual(valid["findings"], self.originals)
        for finding, line in zip(self.originals, (2, 5)):
            self.assertEqual(contract.trusted_line(self.plan, finding["anchor_id"]),
                             (HEAD.splitlines()[line - 1], line))
        verdict = self.adjudication()
        result, model = self.script(*self.discovery, verdict)
        self.assertTrue(result.published)
        self.assertEqual(result.model_calls_attempted, len(self.discovery) + 1)
        self.assertEqual(len(self.gh.posts), 1)
        text = self.gh.posts[0][1]
        self.assertIn("Achados (1)", text)
        self.assertIn(self.originals[0]["title"], text)
        self.assertNotIn(self.originals[1]["title"], text)
        self.assertNotIn(verdict["verdicts"][0]["countercheck"], text)

        coverage = result.coverage
        self.assertEqual((coverage["files_supplied"], coverage["test_files_supplied"],
                          coverage["files_omitted"], coverage["test_files_omitted"]),
                         (2, 1, 0, 0))
        self.assertEqual((coverage["hunks_supplied"], coverage["fragments_supplied"]), (5, 9))
        self.assertGreater(coverage["chunks_planned"], 1)
        self.assertEqual(coverage["chunks_planned"], coverage["chunks_completed"])
        data = [json.loads(row["messages"][1]["content"]) for row in model.payloads]
        self.assertTrue(all("candidates_to_refute" not in item for item in data[:-1]))
        self.assertEqual(data[-1]["patch"].count("# File "), coverage["fragments_supplied"])
        for path in (SOURCE, TEST_SOURCE):
            self.assertIn(path, data[-1]["patch"])
        self.assertEqual(
            [entry["candidate_id"] for entry in data[-1]["candidates_to_refute"]],
            [row["candidate_id"] for row in verdict["verdicts"]],
        )
        self.assertTrue(all(ORACLE_LABEL not in json.dumps(row) for row in model.payloads))

    def test_complete_but_empty_mock_can_miss_executable_regression(self):
        # Coverage records that valid responses were supplied, not that the
        # model found this independently demonstrated regression.
        before, after = {}, {}
        exec(BASE, before)
        exec(HEAD, after)
        self.assertTrue(before["meets_minimum"](10))
        self.assertFalse(after["meets_minimum"](10))
        result, model = self.script(*[CLEAN] * len(self.discovery))
        self.assertEqual(result.coverage["chunks_completed"], len(self.discovery))
        self.assertEqual(result.model_calls_attempted, len(self.discovery))
        self.assertEqual(len(model.payloads), len(self.discovery))  # no candidates to refute
        self.assertTrue(result.published)
        self.assertEqual(len(self.gh.posts), 1)
        self.assertIn("Achados (0)", self.gh.posts[0][1])

    def test_anchor_from_different_chunk_stops_before_adjudication(self):
        second = self.plan.chunks[1]
        answer = deepcopy(self.discovery[0])
        answer["findings"][0]["anchor_id"] = second.anchors[0][0]
        result, model = self.script(answer)
        self.assertEqual(result.code, "v2_anchor_out_of_scope")
        self.assertEqual(result.coverage["chunks_completed"], 0)
        self.assertEqual(len(model.payloads), 1)
        self.assertFalse(result.published)
        self.assertEqual(self.gh.posts, [])

    def test_hold_after_discovery_for_uncertainty_contradiction_and_incompletion(self):
        for key, fields, code in (
            ("uncertain", {"decision": "uncertain", "current_state": "insufficient_context"},
             "v2_refutation_uncertain"),
            ("contradiction", {"decision": "retain", "current_state": "fixed_by_patch"},
             "v2_refutation_inconsistent_verdict"),
            ("incomplete", {}, "v2_model_declared_incomplete"),
        ):
            with self.subTest(state=key):
                answer = self.adjudication()
                answer["verdicts"][1].update(fields)
                if key == "incomplete":
                    answer["analysis_status"] = "incomplete"
                result, model = self.script(*self.discovery, answer)
                self.assertEqual(result.code, code)
                self.assertEqual(result.coverage["chunks_completed"], len(self.discovery))
                self.assertEqual(len(model.payloads), len(self.discovery) + 1)
                self.assertFalse(result.published)
                self.assertEqual(self.gh.posts, [])

    def test_later_discovery_incompletion_holds_the_entire_review(self):
        incomplete = dict(self.discovery[1], analysis_status="incomplete")
        result, model = self.script(self.discovery[0], incomplete)
        self.assertEqual(result.code, "v2_model_declared_incomplete")
        self.assertEqual(result.coverage["chunks_completed"], 1)
        self.assertEqual(len(model.payloads), 2)
        self.assertFalse(result.published)
        self.assertEqual(self.gh.posts, [])

    def test_operational_error_after_one_chunk_cannot_be_clean(self):
        class OperationalFailure(Model):
            def complete(self, payload):
                if self.payloads:
                    self.payloads.append(payload)
                    raise Problem("network_timeout")
                return super().complete(payload)

        model = OperationalFailure([json.dumps(self.discovery[0])])
        result = production.run_review_v2(self.ev, "true", self.gh, model, version="offline-control")
        self.assertEqual(result.code, "network_timeout")
        self.assertEqual((result.coverage["chunks_completed"], result.model_calls_attempted), (1, 2))
        self.assertEqual(len(model.payloads), 2)
        self.assertFalse(result.published)
        self.assertEqual(self.gh.posts, [])


if __name__ == "__main__":
    unittest.main()
