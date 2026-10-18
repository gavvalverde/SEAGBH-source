"""Deterministic integration contracts: synthetic data, no network or APIs.

Run with the isolated environment: python -B production/test_core.py
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import core


SHA = "a" * 40
OTHER_SHA = "b" * 40
PATH = "src/example.py"
BUILDER_PATH = Path(__file__).resolve().parent.parent / "benchmarks/harness/prompt_builder.py"
spec = importlib.util.spec_from_file_location("_contract_expected_prompt", BUILDER_PATH)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def finding(**overrides):
    value = {
        "severity": "important", "category": "regression", "path": PATH,
        "line": 10, "title": "Synthetic defect", "body": "Concrete synthetic failure.",
        "confidence": "high", "evidence": "example(value)",
    }
    value.update(overrides)
    return value


def review(findings=None, **overrides):
    value = {
        "summary": "Synthetic review", "findings": [finding()] if findings is None else findings,
        "reviewed_scope": "Only the provided synthetic lines", "confidence_overall": "high",
    }
    value.update(overrides)
    return value


def scope(**overrides):
    value = {PATH: core.FileScope(diff_ranges=((10, 12),), context_ranges=((20, 22),))}
    value.update(overrides)
    return core.ReviewScope(SHA, value)


def process(value, supplied=None, **overrides):
    kwargs = {"expected_sha": SHA, "response_sha": SHA}
    kwargs.update(overrides)
    return core.process_response(
        json.dumps(value, ensure_ascii=True), scope() if supplied is None else supplied, **kwargs
    )


class IntegrationContracts(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket", side_effect=AssertionError("Network disabled in offline tests"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def assert_error(self, result, status):
        self.assertEqual(result.status, status)
        self.assertFalse(result.ok)
        self.assertFalse(result.publishable)
        self.assertIsNone(result.review)

    def test_payload_default_is_exact_published_contract(self):
        expected = builder.build_request("Title", "Body", "diff")
        self.assertEqual(core.build_payload("Title", "Body", "diff"), expected)
        self.assertEqual(list(core.build_payload("Title", "Body", "diff")), list(expected))

    def test_payload_optional_arguments_and_message_order(self):
        options = [
            {}, {"context": '{"file":"src/a.py"}'}, {"trunc_note": "[DIFF TRUNCATED]"},
            {"omit_info": "src/omitted.py"}, {"model": None}, {"model": ""},
            {"model": "synthetic/alternate"},
            {"context": "Unicode: a??o ??", "trunc_note": "partial", "omit_info": "omitted", "model": "x/y"},
        ]
        for kwargs in options:
            with self.subTest(kwargs=kwargs):
                expected = builder.build_request("T", "B", "D", **kwargs)
                actual = core.build_payload("T", "B", "D", **kwargs)
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                self.assertEqual([m["role"] for m in actual["messages"]], ["system", "user"])

    def test_untrusted_strings_remain_data_in_payload(self):
        text = '"a??o"\n`Markdown` $(echo never-executed) ${TOKEN} ??'
        self.assertEqual(
            core.build_payload(text, text, text, context=text, omit_info=text),
            builder.build_request(text, text, text, context=text, omit_info=text),
        )

    def test_valid_review_with_findings(self):
        value = review()
        result = process(value)
        self.assertEqual(result.status, core.Status.REVIEW)
        self.assertTrue(result.publishable)
        self.assertEqual(result.sha, SHA)
        self.assertEqual(result.review, value)

    def test_valid_empty_review_is_clean_only_after_scope_and_sha(self):
        value = {"summary": "No actionable issues", "findings": []}
        intermediate = core.validate_review(json.dumps(value))
        self.assertEqual(intermediate.status, core.Status.CLEAN)
        self.assertFalse(intermediate.publishable)
        result = process(value)
        self.assertEqual(result.status, core.Status.CLEAN)
        self.assertTrue(result.publishable)
        self.assertEqual(result.review, value)

    def test_malformed_json(self):
        result = core.validate_review('{"summary":, "findings":[]}')
        self.assert_error(result, core.Status.PARSE_ERROR)

    def test_no_prefix_recovery_or_fences(self):
        valid = json.dumps(review())
        for text in [valid + ' trailing', valid + '{}', '```json\n' + valid + '\n```', 'prefix ' + valid]:
            with self.subTest(text_type=text[:8]):
                self.assert_error(core.validate_review(text), core.Status.PARSE_ERROR)

    def test_schema_requires_object_and_correct_root_types(self):
        for value in [[], 42, True, None, "text", review(summary=42), review(findings={})]:
            with self.subTest(value_type=type(value).__name__):
                self.assert_error(process(value), core.Status.SCHEMA_ERROR)

    def test_all_required_fields_are_enforced(self):
        for name in ["summary", "findings"]:
            value = review()
            del value[name]
            with self.subTest(root_field=name):
                self.assert_error(process(value), core.Status.SCHEMA_ERROR)
        required = ["severity", "category", "path", "line", "title", "body", "confidence", "evidence"]
        for name in required:
            value = review()
            del value["findings"][0][name]
            with self.subTest(finding_field=name):
                self.assert_error(process(value), core.Status.SCHEMA_ERROR)

    def test_every_enum_is_enforced(self):
        for name in ["severity", "category", "confidence"]:
            with self.subTest(field=name):
                self.assert_error(process(review([finding(**{name: "invalid"})])), core.Status.SCHEMA_ERROR)
        self.assert_error(process(review(confidence_overall="invalid")), core.Status.SCHEMA_ERROR)

    def test_additional_properties_are_rejected(self):
        for value in [review(extra="forbidden"), review([finding(extra="forbidden")])]:
            self.assert_error(process(value), core.Status.SCHEMA_ERROR)

    def test_string_bounds_and_item_limit(self):
        for value in [review(summary=""), review(summary="s" * 1501), review(reviewed_scope="s" * 2001)]:
            self.assert_error(process(value), core.Status.SCHEMA_ERROR)
        limits = {"path": (1, None), "title": (3, 160), "body": (10, 1500), "evidence": (5, 2000)}
        for name, (minimum, maximum) in limits.items():
            for length in [minimum - 1] + ([maximum + 1] if maximum else []):
                with self.subTest(field=name, length=length):
                    self.assert_error(process(review([finding(**{name: "x" * length})])), core.Status.SCHEMA_ERROR)
        for name, maximum in [("id", 100), ("suggested_fix", 1000), ("out_of_diff_justification", 500)]:
            self.assert_error(process(review([finding(**{name: "x" * (maximum + 1)})])), core.Status.SCHEMA_ERROR)
        self.assert_error(process(review([finding() for _ in range(26)])), core.Status.SCHEMA_ERROR)

    def test_line_types_and_minimum(self):
        for value in [True, False, 0, -1, 1.5, "10"]:
            with self.subTest(line_type=type(value).__name__, line=value):
                self.assert_error(process(review([finding(line=value)])), core.Status.SCHEMA_ERROR)
        # Draft 7 integer includes numbers with zero fractional part.
        self.assertTrue(process(review([finding(line=10.0)])).publishable)

    def test_optional_field_types(self):
        for name, value in [("id", 1), ("suggested_fix", []), ("out_of_diff", "yes"),
                            ("out_of_diff_justification", 42)]:
            self.assert_error(process(review([finding(**{name: value})])), core.Status.SCHEMA_ERROR)

    def test_empty_response(self):
        for value in [None, "", " \n\t"]:
            self.assert_error(core.validate_review(value), core.Status.EMPTY_RESPONSE)

    def test_known_incomplete_response_never_becomes_clean(self):
        valid = json.dumps(review([]))
        self.assert_error(core.validate_review(valid, complete=False), core.Status.INCOMPLETE_RESPONSE)
        self.assert_error(process(review([]), complete=False), core.Status.INCOMPLETE_RESPONSE)

    def test_truncated_json(self):
        for text in ['{"summary":', '{"summary":"unfinished', '{"summary":"ok","findings":[']:
            self.assert_error(core.validate_review(text), core.Status.INCOMPLETE_RESPONSE)

    def test_duplicate_keys_and_nonfinite_values(self):
        for text in ['{"summary":"a","summary":"b","findings":[]}',
                     '{"summary":NaN,"findings":[]}', '{"summary":Infinity,"findings":[]}',
                     '{"summary":1e9999,"findings":[]}']:
            self.assert_error(core.validate_review(text), core.Status.PARSE_ERROR)

    def test_transport_and_http_errors_cannot_be_clean(self):
        self.assert_error(process(review([]), transport_error=True), core.Status.TRANSPORT_ERROR)
        for status in [400, 401, 429, 500, 503, 204, None, True, "200"]:
            with self.subTest(http_status=status):
                result = process(review([]), http_status=status)
                self.assert_error(result, core.Status.HTTP_ERROR)

    def test_sha_checks_include_empty_reviews_and_scope(self):
        self.assert_error(process(review([]), response_sha=OTHER_SHA), core.Status.SHA_MISMATCH)
        self.assert_error(process(review([]), expected_sha="bad", response_sha="bad"), core.Status.SHA_MISMATCH)
        other_scope = core.ReviewScope(OTHER_SHA, scope().files)
        self.assert_error(process(review([]), supplied=other_scope), core.Status.SHA_MISMATCH)

    def test_bound_result_cannot_be_relabelled_to_another_sha(self):
        result = process(review())
        other_scope = core.ReviewScope(OTHER_SHA, scope().files)
        filtered = core.filter_findings(result, other_scope, expected_sha=OTHER_SHA, response_sha=OTHER_SHA)
        self.assert_error(filtered, core.Status.SHA_MISMATCH)

    def test_unknown_and_unsafe_paths(self):
        for path in ["src/unknown.py", "../private.py", "/absolute.py", "C:/private.py", "src/../example.py", "src\\example.py"]:
            result = process(review([finding(path=path)]))
            self.assert_error(result, core.Status.FILTER_ERROR)
            self.assertEqual(result.rejected[0].reason, "path_not_supplied")

    def test_only_sent_lines_and_boundaries_are_allowed(self):
        for line in [10, 12]:
            self.assertTrue(process(review([finding(line=line)])).publishable)
        for line in [9, 13, 20, 500]:
            result = process(review([finding(line=line)]))
            self.assert_error(result, core.Status.FILTER_ERROR)
            self.assertEqual(result.rejected[0].reason, "diff_line_not_supplied")

    def test_context_only_requires_explicit_out_of_diff_and_justification(self):
        good = finding(line=20, out_of_diff=True, out_of_diff_justification="Synthetic supplied dependency")
        self.assertTrue(process(review([good])).publishable)
        for line in [19, 23, 10]:
            self.assert_error(process(review([{**good, "line": line}])), core.Status.FILTER_ERROR)
        for modification in [{"out_of_diff_justification": "short"}, {"out_of_diff": "true"}]:
            self.assert_error(process(review([{**good, **modification}])), core.Status.SCHEMA_ERROR)
        missing = dict(good)
        del missing["out_of_diff_justification"]
        self.assert_error(process(review([missing])), core.Status.SCHEMA_ERROR)

    def test_file_level_requires_explicit_permission(self):
        value = review([finding(line=None)])
        self.assert_error(process(value), core.Status.FILTER_ERROR)
        allowed = scope(**{PATH: core.FileScope(allow_file_level=True)})
        self.assertEqual(process(value, supplied=allowed).review, value)

    def test_mixed_findings_return_only_accepted_copies(self):
        good, bad = finding(), finding(path="src/not-sent.py", title="Rejected defect")
        original = review([good, bad])
        before = copy.deepcopy(original)
        result = process(original)
        self.assertTrue(result.publishable)
        self.assertEqual(result.review["findings"], [good])
        self.assertEqual([(r.index, r.reason) for r in result.rejected], [(1, "path_not_supplied")])
        self.assertEqual(original, before)
        exposed = result.review
        exposed["findings"].clear()
        self.assertEqual(result.review["findings"], [good])

    def test_all_rejected_is_error_not_clean(self):
        result = process(review([finding(line=999)]))
        self.assert_error(result, core.Status.FILTER_ERROR)
        self.assertEqual(result.reason, "all_findings_rejected")

    def test_errors_survive_filtering(self):
        for status in [s for s in core.Status if s not in (core.Status.REVIEW, core.Status.CLEAN)]:
            error = core.ReviewResult(status, "synthetic_error")
            self.assertIs(core.filter_findings(error, scope(), expected_sha=SHA, response_sha=SHA), error)

    def test_diagnostics_are_sanitized(self):
        sentinel = "SYNTHETIC_PRIVATE_SENTINEL"
        invalid = review([finding(path=sentinel, evidence=sentinel, body=sentinel)])
        result = process(invalid)
        self.assert_error(result, core.Status.FILTER_ERROR)
        self.assertNotIn(sentinel, repr(result))
        schema_error = process(review([finding(extra=sentinel)]))
        self.assertNotIn(sentinel, repr(schema_error))
        self.assertIsNone(schema_error.review)
        self.assertNotIn(sentinel, repr(core.validate_review(json.dumps(invalid))))

    def test_unicode_markdown_quotes_and_unusual_filename_are_preserved(self):
        path = 'src/relat?rio [v2] "a??o" $(not-executed).py'
        text = '# Markdown\n"aspas" e a??o ??; `code`'
        value = review([finding(path=path, body=text, evidence=text)], summary=text)
        supplied = scope(**{path: core.FileScope(diff_ranges=((10, 10),))})
        self.assertEqual(process(value, supplied=supplied).review, value)

    def test_scope_is_detached_and_invalid_scope_is_rejected(self):
        ranges = [[10, 12]]
        file_scope = core.FileScope(diff_ranges=ranges)
        ranges[0][0] = 999
        self.assertEqual(file_scope.diff_ranges, ((10, 12),))
        files = {PATH: file_scope}
        supplied = core.ReviewScope(SHA, files)
        files.clear()
        self.assertTrue(process(review(), supplied=supplied).publishable)
        with self.assertRaises(TypeError):
            supplied.files["new"] = file_scope
        for bad in [((0, 2),), ((2, 1),), ((True, 2),), ((1, 2, 3),)]:
            with self.assertRaises(ValueError):
                core.FileScope(diff_ranges=bad)
        for path in ["../x", "/x", "C:/x", "x\\y", "x//y", "x/./y", "x\ny"]:
            with self.assertRaises(ValueError):
                core.ReviewScope(SHA, {path: file_scope})
        self.assert_error(process(review(), supplied={}), core.Status.FILTER_ERROR)

    def test_forged_intermediate_result_still_requires_schema(self):
        forged = core.ReviewResult(core.Status.REVIEW, _review_json=json.dumps({"summary": 42, "findings": []}))
        self.assert_error(
            core.filter_findings(forged, scope(), expected_sha=SHA, response_sha=SHA), core.Status.SCHEMA_ERROR
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
