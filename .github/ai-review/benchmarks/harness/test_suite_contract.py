"""All 23 benchmark fixtures: integrity and evaluator controls, NO model calls.

This is NOT a live accuracy benchmark. It intentionally tests synthetic
empty/noisy outputs without inventing synthetic true positives.
"""
import json
import sqlite3
import unittest

from leak_guard import check_leak
from prompt_builder import build_request
from run_bench import load_cases, load_ground_truth, evaluate_review, match_score
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

    def test_positive_ground_truth_target_is_in_current_diff(self):
        for case in load_cases():
            gt = load_ground_truth(case["id"])
            if not gt["expected"]["find"]:
                continue
            target = "+++ b/" + gt["expected"]["path"] + "\n"
            with self.subTest(case=case["id"]):
                self.assertIn(target, case["diff"])

    def test_explicit_mechanism_contracts_refuse_generic_claims(self):
        scenarios = (
            ("off-001",
             "Na fronteira exata de 24 horas, trocar horas <= 24 por horas < 24 faz revogacao passar de grace para expired.",
             "Alterou o grace period de 24 horas."),
            ("dep-001",
             "self.entry_busca.textChanged.connect(self._atualizar_contador) aponta para metodo inexistente, causando AttributeError.",
             "self._atualizar_contador atualiza o contador quando o textChanged dispara."),
            ("tst-001",
             "Em limpar_auditoria, limite=-1 resulta em LIMIT -1, preserva todos os registros e retorna quantidade removida incorreta.",
             "A funcao limpar_auditoria usa LIMIT -1."),
        )
        for cid, good, bad in scenarios:
            gt = load_ground_truth(cid)
            self.assertIn(gt["expected"].get("mechanism_contract"), {
                "exact_24h_grace_boundary", "missing_ui_callback",
                "negative_sqlite_limit",
            })
            for text, expected in ((good, True), (bad, False)):
                with self.subTest(case=cid, positive=expected):
                    f = {
                        "path": gt["expected"]["path"], "line": 1,
                        "title": "Revisao", "body": text, "evidence": text,
                        "category": gt["expected"]["category"],
                        "severity": gt["expected"]["severity"],
                    }
                    self.assertEqual(match_score(f, gt) >= 0, expected)
                    # Matching must reject a plausible mechanism at wrong path.
                    if expected:
                        self.assertEqual(match_score({**f, "path": "other.py"}, gt), -1)

    def test_fixture_specific_regressions_are_independent_and_observable(self):
        frk = load_cases(case_id="frk-001")[0]
        self.assertIn("-        if self.entry_user.text() == os.environ.get(", frk["diff"])
        self.assertIn('+        if usuario == "admin" and senha == "seguranca123":', frk["diff"])
        self.assertNotIn('-        if self.entry_user.text() == "admin"', frk["diff"])
        dep = load_cases(case_id="dep-001")[0]
        dep_context = json.loads(dep["context"])["src/ui/tab_equipamentos.py"]
        self.assertIn("não está definido", dep_context["description"])
        self.assertEqual(load_ground_truth("dep-001")["expected"]["path"],
                         "src/ui/tab_equipamentos.py")
        pth = load_cases(case_id="pth-001")[0]
        self.assertIn("+    def __init__(self, parent=None):", pth["diff"])
        self.assertIn("+        super().__init__(parent)", pth["diff"])
        self.assertIn("thread.pronto.connect", pth["context"])
        self.assertEqual(load_ground_truth("pth-001")["expected"]["category"],
                         "broken-api")
        mnt = load_cases(case_id="mnt-001")[0]
        self.assertIn("3600 segundos", mnt["context"])
        self.assertIn("único chamador", mnt["context"])
        osc = load_ground_truth("osc-001")
        self.assertEqual(osc["expected"]["severity"], "important")
        self.assertEqual(osc["expected"]["category"], "regression")

    def test_sqlite_negative_limit_is_an_actual_count_mismatch(self):
        # Deterministic witness for the new tst-001 target, with no model call.
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE auditoria (id INTEGER PRIMARY KEY)")
            conn.executemany("INSERT INTO auditoria (id) VALUES (?)",
                             [(1,), (2,), (3,)])
            limite = -1
            total = conn.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0]
            conn.execute(
                "DELETE FROM auditoria WHERE id NOT IN "
                "(SELECT id FROM auditoria ORDER BY id DESC LIMIT ?)",
                (limite,),
            )
            after = conn.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0]
            self.assertEqual((total, after), (3, 3))
            self.assertEqual(total - limite, 4)
            self.assertNotEqual(total - limite, total - after)

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
