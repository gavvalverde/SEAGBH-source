"""All 23 benchmark fixtures: integrity and evaluator controls, NO model calls.

This is NOT a live accuracy benchmark. It intentionally tests synthetic
empty/noisy outputs without inventing synthetic true positives.
"""
import json
import unittest

from leak_guard import check_leak
from prompt_builder import build_request
from run_bench import load_cases, load_ground_truth, evaluate_review
from fixture_syntax import validate_case_syntax, SYNTAX_MODES


class SuiteContractTests(unittest.TestCase):
    def test_every_case_has_valid_distinct_ground_truth(self):
        cases = load_cases()
        self.assertEqual(len(cases), 23)
        self.assertEqual(len({x["id"] for x in cases}), len(cases))
        self.assertEqual(set(SYNTAX_MODES), {x["id"] for x in cases})
        labels = {True: 0, False: 0}
        for case in cases:
            with self.subTest(case=case["id"]):
                cid = case["id"]
                gt = load_ground_truth(cid)
                self.assertIsNotNone(gt)
                self.assertEqual(cid, gt["id"])
                self.assertIs(type(gt["expected"]["find"]), bool)
                labels[gt["expected"]["find"]] += 1
                self.assertFalse(check_leak(case, cid))
                self.assertIsInstance(case.get("diff"), str)
                self.assertTrue(case["diff"])
                self.assertIn(validate_case_syntax(case),
                              {"validated", "fragment_unverifiable", "non_python"})
                self.assertIsInstance(case.get("pr_title"), str)
                self.assertIsInstance(case.get("pr_body"), str)
                payload = build_request(
                    case["pr_title"], case["pr_body"], case["diff"],
                    case.get("context", "")
                )
                self.assertNotIn("ground_truth", json.dumps(payload))
                self.assertNotIn("confidence_expected", json.dumps(payload))
                # A label never appears in the user-message input.
                self.assertNotIn('"expected":', payload["messages"][1]["content"])
        self.assertGreater(labels[True], 0)
        self.assertGreater(labels[False], 0)

    def test_all_23_cases_distinguish_silent_misses_and_noisy_reports(self):
        cases = load_cases()
        self.assertEqual(len(cases), 23)
        misses = 0
        noisy_false_positives = 0
        for case in cases:
            gt = load_ground_truth(case["id"])
            clean = {"summary": "Synthetic clean", "findings": []}
            result = evaluate_review(clean, gt)
            if gt["expected"]["find"]:
                self.assertEqual((result["tp"], result["fp"], result["fn"]),
                                 (0, 0, 1))
                misses += 1
            else:
                self.assertEqual((result["tp"], result["fp"], result["fn"]),
                                 (0, 0, 0))
                noisy = {"summary": "Synthetic finding", "findings": [{
                    "path": "synthetic/path.py", "line": 1,
                    "severity": "minor", "category": "other",
                    "title": "Fake", "body": "Synthetic report",
                    "evidence": "synthetic", "confidence": "low",
                }]}
                flagged = evaluate_review(noisy, gt)
                self.assertEqual((flagged["tp"], flagged["fp"], flagged["fn"]),
                                 (0, 1, 0))
                noisy_false_positives += 1
        self.assertGreater(misses, 0)
        self.assertGreater(noisy_false_positives, 0)
        self.assertEqual(misses + noisy_false_positives, len(cases))


if __name__ == "__main__":
    unittest.main()
