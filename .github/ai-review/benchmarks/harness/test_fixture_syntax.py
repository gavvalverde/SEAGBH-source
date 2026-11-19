"""No-network, no-code-execution tests of complete benchmark Python snippets."""
import ast
import copy
import unittest

from fixture_syntax import (
    SYNTAX_MODES, _parse_single_method, patch_side, validate_case_syntax
)
from run_bench import load_cases


class FixtureSyntaxTests(unittest.TestCase):
    def test_every_fixture_has_an_explicit_mode(self):
        cases = load_cases()
        self.assertEqual(len(cases), 23)
        self.assertEqual(set(SYNTAX_MODES), {item["id"] for item in cases})
        observed = {}
        for item in cases:
            with self.subTest(case=item["id"]):
                status = validate_case_syntax(item)
                observed[item["id"]] = status
                self.assertIn(status, {"validated", "fragment_unverifiable",
                                       "non_python"})
        for key in ("grn-001", "fp-001", "inj-001", "tst-001", "txn-001"):
            self.assertEqual(observed[key], "validated")
        self.assertEqual(observed["seg-001"], "fragment_unverifiable")
        self.assertEqual(observed["r22-ci-001"], "non_python")

    def test_txn_old_and_new_are_syntactically_valid_without_execution(self):
        case = load_cases(case_id="txn-001")[0]
        self.assertEqual(validate_case_syntax(case), "validated")
        old_code = patch_side(case["diff"], side="old")
        new_code = patch_side(case["diff"], side="new")
        old_ast = _parse_single_method(__import__("textwrap").dedent(old_code))
        new_ast = _parse_single_method(__import__("textwrap").dedent(new_code))
        self.assertEqual((old_ast.name, new_ast.name), ("criar_evento", "criar_evento"))
        self.assertTrue(any(isinstance(x, ast.Try) for x in ast.walk(old_ast)))
        self.assertFalse(any(isinstance(x, ast.Try) for x in ast.walk(new_ast)))
        self.assertIn("self.conn.rollback()", old_code)
        self.assertNotIn("self.conn.rollback()", new_code)
        self.assertIn("self.conn.commit()", new_code)
        self.assertNotIn("IndentationError", new_code)

    def test_rejects_regression_reintroducing_syntax_error(self):
        item = copy.deepcopy(load_cases(case_id="txn-001")[0])
        original = "+        self.cursor.execute("
        self.assertIn(original, item["diff"])
        item["diff"] = item["diff"].replace(
            original, "+            self.cursor.execute(", 1
        )
        with self.assertRaises((SyntaxError, ValueError)):
            validate_case_syntax(item)

    def test_fragment_is_never_wrongly_claimed_validated(self):
        partial = load_cases(case_id="seg-001")[0]
        self.assertEqual(validate_case_syntax(partial), "fragment_unverifiable")

    def test_missing_contract_fails_closed(self):
        case = copy.deepcopy(load_cases(case_id="txn-001")[0])
        case["id"] = "future-999"
        with self.assertRaises(ValueError):
            validate_case_syntax(case)


if __name__ == "__main__":
    unittest.main()
