"""Offline report aggregation tests. Never open a socket or call a model."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pilot_guarded import (
    CASE_IDS, PROFILES, _fixture_sha256, _ground_truth_sha256, SCORER_REVISION,
)
from pilot_report import main, summarize
from run_bench import load_ground_truth, load_cases


def sample(cid, *, profile="deepseek-thinking-16k-8k"):
    positive = load_ground_truth(cid)["expected"]["find"] is True
    return {
        "case_id": cid,
        "fixture_sha256": _fixture_sha256(load_cases(case_id=cid)[0]),
        "ground_truth_sha256": _ground_truth_sha256(load_ground_truth(cid)),
        "scorer_revision": SCORER_REVISION,
        "syntax_status": "validated" if cid == "txn-001" else "fragment_unverifiable",
        "model": PROFILES[profile]["model"],
        "profile": profile,
        "mode": "paid_result",
        "status": "review_valid",
        "calls_attempted": 1,
        "review_published": False,
        "tp": 1 if positive else 0,
        "fp": 0,
        "fn": 0,
        "usage": {"completion_tokens": 100, "reasoning_tokens": 60,
                  "visible_tokens": 40},
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        stopper = patch("socket.socket", side_effect=AssertionError("network forbidden"))
        stopper.start()
        self.addCleanup(stopper.stop)

    def test_full_23_case_coverage_and_metrics(self):
        results = [sample(cid) for cid in CASE_IDS]
        report = summarize(results, profile="deepseek-thinking-16k-8k")
        self.assertEqual(report["cases_reported"], 23)
        self.assertTrue(report["coverage_complete"])
        self.assertEqual(report["false_positives"], 0)
        self.assertEqual(report["false_negatives"], 0)
        self.assertEqual(report["precision"], 1.0)
        self.assertEqual(report["recall"], 1.0)
        self.assertEqual(report["completion_tokens_total"], 2300)
        self.assertTrue(report["manual_semantic_audit_required"])

    def test_partial_and_false_positive_are_not_mislabeled_clean(self):
        items = [sample("r22-typ-001"), sample("r22-ddl-001")]
        items[1]["fp"] = 1
        report = summarize(items, profile="deepseek-thinking-16k-8k")
        self.assertFalse(report["coverage_complete"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual((report["true_positives"], report["false_positives"]), (1, 1))
        self.assertEqual(report["negative_cases_with_false_positives"], 1)
        self.assertEqual(report["precision"], 0.5)

    def test_rejects_duplicates_mixed_models_inconsistent_and_raw_content(self):
        valid = sample("r22-typ-001")
        for bad in [
            [valid, valid],
            [{**valid, "model": "attacker/model"}],
            [{**valid, "profile": "gemini-thinking-2048"}],
            [{**valid, "fn": 1}],
            [{**valid, "review": {"summary": "SECRET_BACKEND"}}],
            [{**valid, "tp": "1"}],
            [{**valid, "usage": {"completion_tokens": 1, "reasoning_tokens": 2,
                                "visible_tokens": -1}}],
            [{**valid, "calls_attempted": 2}],
            [{**sample("txn-001"), "fixture_sha256": "0" * 64}],
            [{**sample("txn-001"), "syntax_status": "fragment_unverifiable"}],
            [{**sample("dep-001"), "ground_truth_sha256": "0" * 64}],
            [{**sample("dep-001"), "scorer_revision": "old-matcher"}],
            [{k: v for k, v in sample("dep-001").items()
              if k != "ground_truth_sha256"}],
            [{k: v for k, v in sample("off-001").items()
              if k != "scorer_revision"}],
            [{k: v for k, v in sample("osc-001").items()
              if k != "ground_truth_sha256"}],
            [{k: v for k, v in sample("pth-001").items()
              if k != "ground_truth_sha256"}],
            [{k: v for k, v in sample("tst-001").items()
              if k != "ground_truth_sha256"}],
            [{k: v for k, v in sample("txn-001").items() if k != "fixture_sha256"}],
            [{**valid, "review_published": True}],
        ]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    summarize(bad, profile="deepseek-thinking-16k-8k")

    def test_quality_heuristics_are_aggregated_without_changing_tp_fp_fn(self):
        pin = sample("pin-001")
        pin["quality_audit"] = {
            "version": 1, "duplicate_location_pairs": 0,
            "possible_overlap_pairs": 0,
            "severity": {
                "applicable": True, "expected": "critical",
                "actual": "important", "direction": "under",
                "category_exact": False,
            },
            "injection": {
                "applicable": True, "expected_bug_detected": True,
                "approval_marker_present": False, "probe_echo_present": False,
                "passed_heuristic": True,
            },
            "partial": None, "manual_semantic_audit_required": True,
        }
        seg = sample("seg-001")
        seg["quality_audit"] = {
            "version": 1, "duplicate_location_pairs": 0,
            "possible_overlap_pairs": 0, "severity": None, "injection": None,
            "partial": {
                "applicable": True, "scope_acknowledged": False,
                "confidence_low": True, "zero_findings": True,
                "passed_heuristic": False,
            },
            "manual_semantic_audit_required": True,
        }
        report = summarize([pin, seg], profile="deepseek-thinking-16k-8k")
        self.assertEqual((report["true_positives"], report["false_positives"],
                          report["false_negatives"]), (1, 0, 0))
        self.assertEqual(report["quality_audit_records"], 2)
        self.assertEqual(report["severity_evaluated"], 1)
        self.assertEqual(report["severity_under"], 1)
        self.assertEqual(report["injection_cases_evaluated"], 1)
        self.assertEqual(report["injection_passed_heuristic"], 1)
        self.assertEqual(report["partial_cases_evaluated"], 1)
        self.assertEqual(report["partial_passed_heuristic"], 0)
        without = summarize([sample("pin-001")], profile="deepseek-thinking-16k-8k")
        self.assertEqual(without["quality_audit_records"], 0)
        self.assertEqual(without["injection_cases_evaluated"], 0)
        self.assertEqual(without["severity_evaluated"], 0)
        for broken in (
            {**pin["quality_audit"], "additional_raw": "SECRET"},
            {**pin["quality_audit"], "severity": {
                **pin["quality_audit"]["severity"], "direction": "over"}},
            {**pin["quality_audit"], "injection": {
                **pin["quality_audit"]["injection"], "passed_heuristic": False}},
            {**pin["quality_audit"], "duplicate_location_pairs": "1"},
            {**pin["quality_audit"], "injection": None},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError):
                    summarize([{**pin, "quality_audit": broken}],
                              profile="deepseek-thinking-16k-8k")

    def test_cli_emits_only_safe_metrics_without_echoing_raw_strings(self):
        items = [sample("r22-typ-001")]
        items[0]["audit"] = {"untrusted": "SECRET_DO_NOT_ECHO_938"}
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "results.jsonl"
            p.write_text("\n".join(map(json.dumps, items)) + "\n")
            with redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(main(["--profile", "deepseek-thinking-16k-8k",
                                       "--input", str(p)]), 0)
            self.assertNotIn("SECRET_DO_NOT_ECHO_938", captured.getvalue())
            self.assertEqual(json.loads(captured.getvalue())["cases_reported"], 1)
            p.write_text('{"review":"SECRET_RAW_CONTENT","unexpected":1}')
            with redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(main(["--profile", "deepseek-thinking-16k-8k",
                                       "--input", str(p)]), 1)
            self.assertEqual(captured.getvalue().strip(),
                             '{"code":"pilot_report_invalid_input"}')


if __name__ == "__main__":
    unittest.main()
