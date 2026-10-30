from __future__ import annotations

import itertools
import math
import unittest

from cadyx.numerical import SparseRankAccumulator, matrix_rank, solve_sparse_positive_definite


class NumericalRankTests(unittest.TestCase):
    def test_sparse_accumulator_matches_dense_rank_on_dependent_rows(self):
        rows = [[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [0.0, 1.0, 1.0]]
        dense_rank = matrix_rank(rows)
        sparse = SparseRankAccumulator()
        for row in rows:
            sparse.add_row({index: value for index, value in enumerate(row) if value})
        self.assertEqual(dense_rank, 2)
        self.assertEqual(sparse.rank, dense_rank)

    def test_sparse_accumulator_reports_each_added_independent_row(self):
        sparse = SparseRankAccumulator()
        self.assertTrue(sparse.add_row({0: 1.0, 1: 1.0}))
        self.assertFalse(sparse.add_row({0: 2.0, 1: 2.0}))
        self.assertTrue(sparse.add_row({0: 1.0, 1: -1.0}))
        self.assertEqual(sparse.rank, 2)

    def test_sparse_accumulator_reduces_dependent_rows_in_any_order(self):
        # The third row is minus half the first row minus the second row.
        # A magnitude-selected pivot incorrectly reported rank three.
        rows = [[-2.0, -2.0, 0.0], [1.0, 2.0, 2.0], [0.0, -1.0, -2.0]]
        self.assertEqual(matrix_rank(rows), 2)
        for ordered_rows in itertools.permutations(rows):
            for columns in itertools.permutations(range(3)):
                sparse = SparseRankAccumulator()
                for row in ordered_rows:
                    sparse.add_row({index: row[column] for index, column in enumerate(columns)
                                    if row[column]})
                self.assertEqual(sparse.rank, 2, (ordered_rows, columns))

    def test_sparse_accumulator_preserves_rank_with_extreme_finite_scales(self):
        first = {0: 1e-8, 1: 1e301}
        dependent = {0: 2e-8, 1: 2e301}
        for rows in ((first, dependent), (dependent, first)):
            with self.subTest(rows=rows):
                sparse = SparseRankAccumulator()
                self.assertTrue(sparse.add_row(rows[0]))
                self.assertFalse(sparse.add_row(rows[1]))
                self.assertEqual(sparse.rank, 1)

    def test_sparse_positive_definite_solver(self):
        matrix = {0: {0: 4.0, 1: 1.0}, 1: {0: 1.0, 1: 3.0}}
        result = solve_sparse_positive_definite(matrix, [1.0, 2.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 1 / 11)
        self.assertAlmostEqual(result[1], 7 / 11)
        self.assertIsNone(solve_sparse_positive_definite({0: {0: 0.0}}, [1.0]))

    def test_sparse_solver_rejects_out_of_range_indices(self):
        for matrix in (
            {0: {0: 2.0, -1: 1.0}, 1: {1: 2.0}},
            {0: {0: 2.0, 2: 1.0}, 1: {1: 2.0}},
            {0: {0: 2.0}, 1: {1: 2.0}, 2: {0: 1.0}},
        ):
            with self.subTest(matrix=matrix):
                self.assertIsNone(solve_sparse_positive_definite(matrix, [1.0, 1.0]))

    def test_sparse_solver_rejects_nonfinite_inputs_and_invalid_limits(self):
        matrix = {0: {0: 2.0}}
        for rhs in ([math.nan], [math.inf], [-math.inf]):
            with self.subTest(rhs=rhs):
                self.assertIsNone(solve_sparse_positive_definite(matrix, rhs))
        self.assertIsNone(solve_sparse_positive_definite({0: {0: 2.0, 1: math.nan}}, [1.0]))
        self.assertIsNone(solve_sparse_positive_definite(matrix, [1.0], tolerance=math.nan))
        self.assertIsNone(solve_sparse_positive_definite(matrix, [1.0], tolerance=0.0))
        self.assertIsNone(solve_sparse_positive_definite(matrix, [1.0], max_iterations=0))


if __name__ == "__main__":
    unittest.main()
