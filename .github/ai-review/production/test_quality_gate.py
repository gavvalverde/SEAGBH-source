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

    def test_unanchored_reasons_do_not_expose_source(self):
        src = {"x.py": {2: "quantity = normalize_quantity(value)", 3: "pass"}}
        findings = [
            {"path": "x.py", "line": 2, "evidence": "PRIVATE_SENTINEL"},
            {"path": "x.py", "line": 80, "evidence": "other"},
            {"path": "x.py", "line": 3, "evidence": "pass"},
            {"path": "x.py", "line": None, "evidence": "PRIVATE"},
        ]
        result = assess(findings, src)
        self.assertTrue(result.requires_manual_review)
        self.assertEqual(result.unanchored_count, 4)
        self.assertEqual((result.quote_mismatch_count, result.source_unavailable_count,
                          result.short_line_count, result.invalid_reference_count), (1, 1, 1, 1))
        self.assertNotIn("PRIVATE", repr(result))

    def test_same_line_collision_requires_manual_review_even_if_both_anchored(self):
        source = {"x.py": {20: "validated_operation = new_operation(value)"}}
        a = {"path": "x.py", "line": 20,
             "evidence": "validated_operation = new_operation(value)",
             "category": "security"}
        b = {**a, "category": "out-of-scope", "confidence": "high"}
        state = assess([a, b], source)
        self.assertEqual(state.checked_count, 2)
        self.assertEqual(state.unanchored_count, 0)
        self.assertEqual(state.duplicate_location_pairs, 1)
        self.assertTrue(state.requires_manual_review)
        self.assertEqual(assess([a, b, a], source).duplicate_location_pairs, 3)

    def test_independent_locations_are_not_suppressed(self):
        source = {"x.py": {
            20: "validated_operation = new_operation(value)",
            25: "other_operation = another_operation(value)",
        }}
        a = {"path": "x.py", "line": 20,
             "evidence": "validated_operation = new_operation(value)"}
        b = {"path": "x.py", "line": 25,
             "evidence": "other_operation = another_operation(value)"}
        state = assess([a, b], source)
        self.assertEqual(state.duplicate_location_pairs, 0)
        self.assertFalse(state.requires_manual_review)

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
