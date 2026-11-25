"""Offline regressions for the legacy benchmark's bipartite matching."""

from itertools import product
import unittest
from unittest.mock import patch

import run_bench


def oracle(matrix):
    """Independent exhaustive enumeration for small matrices only."""
    nf, ng = len(matrix), len(matrix[0])
    best = (0, 0)
    for assignment in product(range(-1, ng), repeat=nf):
        paired = [(i, j) for i, j in enumerate(assignment) if j >= 0]
        if len({j for _, j in paired}) != len(paired):
            continue
        if any(matrix[i][j] < 0 for i, j in paired):
            continue
        best = max(best, (len(paired), sum(matrix[i][j] for i, j in paired)))
    return best


def run_matrix(matrix):
    findings = list(range(len(matrix)))
    ground_truths = list(range(len(matrix[0])))
    with patch.object(run_bench, "match_score",
                      side_effect=lambda i, j: matrix[i][j]):
        return run_bench.match_findings_multi(findings, ground_truths)


class LegacyMatching(unittest.TestCase):
    def test_empty_input_counts_unmatched_as_fp_and_fn_never_tp(self):
        for findings, gts, expected in (
            ([], [], (0, 0, 0, [])),
            ([{}, {}], [], (0, 2, 0, [])),
            ([], [{}, {}], (0, 0, 2, [])),
        ):
            with self.subTest(findings=findings, gts=gts):
                self.assertEqual(run_bench.match_findings_multi(findings, gts), expected)

    def test_maximum_cardinality_overrides_greedy_edge(self):
        self.assertEqual(run_matrix([[8, 6], [6, -1]]), (2, 0, 0, [0, 1]))
        self.assertEqual(run_matrix([[99, 0], [0, -1]]), (2, 0, 0, [0, 1]))

    def test_total_score_breaks_tie_in_cardinality(self):
        self.assertEqual(run_matrix([[9, 8], [8, -1], [-1, 0]]), (2, 1, 0, [0, 1]))
        self.assertEqual(run_matrix([[9, -1], [8, -1], [-1, 7]]), (2, 1, 0, [0, 2]))

    def test_deterministic_tie_and_input_order(self):
        matrix = [[4, 4], [4, 4], [4, 4]]
        for _ in range(4):
            self.assertEqual(run_matrix(matrix), (2, 1, 0, [0, 1]))
        self.assertEqual(run_matrix([[1, -1], [-1, 3]]), (2, 0, 0, [0, 1]))

    def test_exhaustive_small_matrices_agree_with_independent_oracle(self):
        # The public output does not expose the paired ground-truth IDs.
        # The reported findings must admit an optimum matching when restricted
        # to those findings, and their cardinality and unmatched counts agree.
        for entries in product((-1, 0, 2), repeat=6):
            matrix = [list(entries[i:i + 2]) for i in range(0, 6, 2)]
            optimum = oracle(matrix)
            tp, fp, fn, matched = run_matrix(matrix)
            if not (tp, fp, fn) == (optimum[0], 3 - optimum[0], 2 - optimum[0]):
                self.fail(f"{matrix}: expected {optimum}, got {(tp, fp, fn, matched)}")
            self.assertEqual(len(set(matched)), tp)
            self.assertEqual(oracle([matrix[i] for i in matched]) if matched else (0, 0),
                             optimum, matrix)


if __name__ == "__main__":
    unittest.main()
