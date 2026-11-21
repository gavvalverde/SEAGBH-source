"""Regressões para evidências de código: offline, sem executar PRs."""
import unittest

from evidence import has_literal_code_anchor, literal_anchor_status, new_file_lines


class CodeAnchorTests(unittest.TestCase):
    def test_extracts_new_lines_across_hunks_and_excludes_removed(self):
        patch = (
            "@@ -1,3 +1,3 @@\n"
            " keep = True\n"
            "-legacy = False\n"
            "+new_value = False\n"
            " return keep\n"
            "@@ -20,1 +20,2 @@\n"
            " old_context = 1\n"
            "+added = old_context + 1\n"
        )
        self.assertEqual(new_file_lines(patch), {
            1: "keep = True",
            2: "new_value = False",
            3: "return keep",
            20: "old_context = 1",
            21: "added = old_context + 1",
        })

    def test_exact_code_quote_anchors_to_same_path_and_line(self):
        source = {"src/example.py": {10: "    if operacao not in _OPERACOES:"}}
        finding = {
            "path": "src/example.py",
            "line": 10,
            "evidence": "Em registrar_alteracao: if operacao not in _OPERACOES: "
                        "lança TypeError com listas.",
        }
        self.assertTrue(has_literal_code_anchor(finding, source))
        for update in (
            {"line": 11},
            {"path": "src/other.py"},
            {"evidence": "if operacao in _OPERACOES:"},
            {"evidence": "hipótese sem código literal"},
        ):
            with self.subTest(update=update):
                self.assertFalse(has_literal_code_anchor({**finding, **update}, source))

    def test_short_or_missing_line_is_not_mistaken_for_proof(self):
        source = {"a.py": {1: "pass", 2: "return None", 3: ""}}
        for line in (1, 2, 3, 4, None):
            with self.subTest(line=line):
                self.assertFalse(has_literal_code_anchor(
                    {"path": "a.py", "line": line,
                     "evidence": "pass return None"}, source
                ))

    def test_whitespace_and_unicode_are_preserved(self):
        source = {"src/x.py": {20: "  mensagem   =  'operação inválida'"}}
        finding = {"path": "src/x.py", "line": 20,
                   "evidence": "mensagem = 'operação inválida' "}
        self.assertTrue(has_literal_code_anchor(finding, source))

    def test_anchor_diagnostics_are_fixed_codes(self):
        src = {"x.py": {2: "quantity = normalize_quantity(value)", 3: "pass"}}
        base = {"path": "x.py", "line": 2, "evidence": "quantity = normalize_quantity(value)"}
        self.assertEqual(literal_anchor_status(base, src), "anchored")
        self.assertEqual(literal_anchor_status({**base, "evidence": "PRIVATE_SENTINEL"}, src), "quote_mismatch")
        self.assertEqual(literal_anchor_status({**base, "line": 80}, src), "source_unavailable")
        self.assertEqual(literal_anchor_status({**base, "line": 3}, src), "short_line")
        self.assertEqual(literal_anchor_status({**base, "line": None}, src), "invalid_reference")

    def test_short_line_can_be_verified_with_a_literal_adjacent_block(self):
        source = {"x.py": {10: "    if resource_is_owned():", 11: "        return",
                           12: "    finish_resource_cleanup()"}}
        for first, last in ((10, 11), (11, 12), (10, 12)):
            with self.subTest(first=first, last=last):
                quote = "\n".join(source["x.py"][number] for number in range(first, last + 1))
                item = {"path": "x.py", "line": 11, "evidence": quote}
                self.assertEqual(literal_anchor_status(item, source), "anchored")
                self.assertTrue(has_literal_code_anchor({**item, "evidence": quote.replace("\n", "\r\n")}, source))

    def test_invented_or_unverified_multiline_evidence_is_not_anchored(self):
        source = {"x.py": {1: "    if resource_is_owned():", 2: "        return",
                           4: "    finish_resource_cleanup()", 5: "    finish_transaction()"},
                  "other.py": {3: "    invented_from_other_file()"}}
        for line, quote in (
            (2, "    if resource_is_owned():\n        break"),
            (2, "if resource_is_owned():\n        return"),
            (2, "        return\n    finish_resource_cleanup()"),
            (2, "        return\n    invented_from_other_file()"),
            (2, "    finish_resource_cleanup()\n    finish_transaction()"),
            (1, "    if resource_is_owned():\n        return\n    fabricate_cleanup()"),
        ):
            with self.subTest(line=line, quote=quote):
                item = {"path": "x.py", "line": line, "evidence": quote}
                self.assertEqual(literal_anchor_status(item, source), "quote_mismatch")

    def test_no_input_is_executed_or_interpreted(self):
        source = {"src/x.py": {2: "$(rm -rf /); <script>alert(1)</script>"}}
        finding = {"path": "src/x.py", "line": 2,
                   "evidence": "$(rm -rf /); <script>alert(1)</script>"}
        self.assertTrue(has_literal_code_anchor(finding, source))


if __name__ == "__main__":
    unittest.main()
