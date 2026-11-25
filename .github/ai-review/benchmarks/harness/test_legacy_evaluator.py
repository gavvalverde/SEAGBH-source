"""Regression tests for the standalone legacy harness; no network."""
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import compare
import run_bench as bench


CLEAN = {"summary": "Synthetic review complete", "findings": []}
POSITIVE = {"type": "bug", "expected": {"find": True}}
NEGATIVE = {"type": "clean", "expected": {"find": False}}


def envelope(content=None, finish="stop"):
    return {"choices": [{"message": {"content": json.dumps(CLEAN) if content is None else content},
                         "finish_reason": finish}]}


def response(data=None, status="200", returncode=0):
    body = json.dumps(data) if data is not None else ""
    return subprocess.CompletedProcess([], returncode, stdout=f"{body}\n{status}", stderr="synthetic")


class LegacyEvaluatorTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.socket", side_effect=AssertionError("Real network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def run_suite(self, proc_results, ground_truths, tempdir):
        cases = [{"id": f"case-{i}"} for i in range(len(proc_results))]
        gt_by_id = dict(zip((case["id"] for case in cases), ground_truths))
        output = io.StringIO()
        with (patch.object(bench, "RESULTS_DIR", tempdir),
              patch.object(bench, "load_cases", return_value=cases),
              patch.object(bench, "load_ground_truth", side_effect=gt_by_id.get),
              patch.object(bench, "build_request", return_value={"model": "synthetic/model"}),
              patch.dict(bench.os.environ, {"OPENROUTER_API_KEY": ""}, clear=True),
              patch.object(bench.subprocess, "run", side_effect=[r for r, gt in zip(proc_results, ground_truths) if gt is not None]) as mock_run,
              redirect_stdout(output)):
            bench.run_suite(Namespace(case=None, filter=None, model="synthetic/model"))
            self.assertEqual(mock_run.call_count, sum(gt is not None for gt in ground_truths))
        return output.getvalue()

    def artifacts(self, tempdir):
        summary, = tempdir.rglob("*.json")
        rows, = tempdir.rglob("*.jsonl")
        return json.loads(summary.read_text(encoding="utf-8")), compare.load_run(rows)

    def test_end_to_end_operational_failures_not_scored(self):
        proc = [subprocess.TimeoutExpired(["synthetic"], 1),
                response(envelope(), "403"),
                response(envelope('{"summary":"broken","findings":[')),
                response(envelope(finish="length")),
                response(envelope()),
                response(envelope()),
                response(envelope())]
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            output = self.run_suite(proc, [POSITIVE, NEGATIVE, POSITIVE, NEGATIVE,
                                          None, POSITIVE, NEGATIVE], path)
            summary, rows = self.artifacts(path)
            self.assertEqual(summary["benchmark_version"], "2.0.0")
            self.assertEqual((summary["cases_run"], summary["cases_evaluable"],
                              summary["cases_not_evaluable"]), (7, 2, 5))
            self.assertEqual((summary["TP"], summary["FP"], summary["FN"]), (0, 0, 1))
            self.assertEqual([row["status"] for row in rows],
                             ["timeout", "http_error", "invalid_json", "incomplete",
                              "no_ground_truth", "evaluated", "evaluated"])
            self.assertEqual([row["match"] for row in rows[:5]], [None] * 5)
            self.assertEqual({row["benchmark_version"] for row in rows}, {"2.0.0"})
            self.assertEqual(rows[5]["match"]["fn"], 1)
            self.assertIn("FN", output)

    def test_no_evaluable_case_means_not_applicable(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            output = self.run_suite([response(envelope(), "403")], [NEGATIVE], path)
            summary, rows = self.artifacts(path)
            self.assertIsNone(summary["precision"])
            self.assertIsNone(summary["recall"])
            self.assertIsNone(summary["F1"])
            self.assertIsNone(rows[0]["match"])
            self.assertIn("não aplicável", output)
            self.assertNotIn("-> OK", output)
            self.assertIsNone(compare.summarize(rows)["precision"])

    def test_finish_and_explicit_incompleteness_precede_scoring(self):
        details = json.dumps(dict(CLEAN, analysis_status="incomplete"))
        proc = [response(envelope(details)), response(envelope(finish=None)),
                response(envelope('{"summary":"ok","findings":[]} trailing')),
                response(envelope('{"summary":"ok","findings":[null]}')),
                response(status="000", returncode=28)]
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.run_suite(proc, [NEGATIVE] * 5, path)
            _, rows = self.artifacts(path)
            self.assertEqual([row["status"] for row in rows],
                             ["incomplete", "incomplete", "invalid_json", "invalid_schema", "timeout"])
            self.assertTrue(all(row["match"] is None for row in rows))

    def test_compare_versioned_reports_and_preserve_historical_metrics(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.run_suite([response(envelope(), "403")], [NEGATIVE], path)
            self.run_suite([response(envelope())], [POSITIVE], path)
            first, second = sorted(path.rglob("*.jsonl"))
            output = io.StringIO()
            with redirect_stdout(output):
                compare.compare(first, second)
            self.assertIn("cases_evaluable", output.getvalue())
            self.assertIn("N/A", output.getvalue())
            legacy = path / "historic.jsonl"
            legacy.write_text(json.dumps({
                "case_id": "old", "type": "bug", "output_tokens": None,
                "match": {"tp": 0, "fp": 0, "fn": 1, "severity_match": 0,
                          "category_match": 0, "location_accuracy": 0},
            }) + "\n", encoding="utf-8")
            old = legacy.read_bytes()
            self.assertEqual(compare.summarize(compare.load_run(legacy))["FN"], 1)
            with self.assertRaisesRegex(ValueError, "versões"):
                compare.compare(legacy, first)
            self.assertEqual(legacy.read_bytes(), old)

    def test_same_day_runs_do_not_replace_previous_reports(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.run_suite([response(envelope())], [POSITIVE], path)
            before = {f: f.read_bytes() for f in path.rglob("*") if f.is_file()}
            self.run_suite([response(envelope())], [NEGATIVE], path)
            after = {f: f.read_bytes() for f in path.rglob("*") if f.is_file()}
            self.assertEqual(len(before), 2)
            self.assertEqual(len(after), 4)
            self.assertEqual(before, {f: after[f] for f in before})


if __name__ == "__main__":
    unittest.main()
