"""Offline publication quality guard tests. No network or model calls."""
import unittest

from quality_gate import QualityAssessment, assess


class QualityGateTests(unittest.TestCase):
    def test_clean_review_is_not_a_quality_hold(self):
        self.assertEqual(assess([], {}).unanchored_count, 0)
        self.assertFalse(assess([], {}).requires_manual_review)

    def test_concrete_quote_passes_even_with_low_confidence(self):
        source = {"src/core/x.py": {42: "if operation not in allowed_operations:"}}
        finding = {"path": "src/core/x.py", "line": 42,
                   "evidence": "if operation not in allowed_operations:",
                   "confidence": "low", "severity": "minor"}
        state = assess([finding], source)
        self.assertEqual(state, QualityAssessment(checked_count=1, unanchored_count=0))
        self.assertFalse(state.requires_manual_review)

    def test_unanchored_and_mixed_findings_require_manual_review(self):
        source = {"x.py": {3: "if operation not in allowed_operations:"}}
        good = {"path": "x.py", "line": 3,
                "evidence": "if operation not in allowed_operations:"}
        for bad in [
            {"path": "x.py", "line": 3, "evidence": "unproven speculation"},
            {"path": "x.py", "line": 9, "evidence": good["evidence"]},
            {"path": "other.py", "line": 3, "evidence": good["evidence"]},
            {"path": "x.py", "line": None, "evidence": good["evidence"]},
            {"path": "x.py", "line": 3, "evidence": "if operation in allowed_operations:"},
        ]:
            with self.subTest(bad=bad):
                result = assess([good, bad], source)
                self.assertEqual(result.checked_count, 2)
                self.assertEqual(result.unanchored_count, 1)
                self.assertTrue(result.requires_manual_review)

    def test_short_code_and_invalid_inputs_fail_safely(self):
        self.assertTrue(assess([{"path": "x.py", "line": 1, "evidence": "pass"}],
                               {"x.py": {1: "pass"}}).requires_manual_review)
        for findings, lines in [
            ("INVALID", {}),
            ([{}] * 26, {}),
            ([], "INVALID"),
        ]:
            with self.subTest(findings=findings):
                with self.assertRaises(ValueError):
                    assess(findings, lines)


if __name__ == "__main__":
    unittest.main()
