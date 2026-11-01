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

    def test_dense_rank_preserves_extreme_finite_coefficients(self):
        # Normalizing the 1e-8 pivot by division creates an infinite 1e309
        # coefficient, then NaN, and falsely reports a second independent row.
        dependent = [[1e-8, 1e301], [2e-8, 2e301]]
        independent = [0.0, 1e301]
        for rows, expected in ((dependent, 1), (dependent + [independent], 2)):
            for ordered in itertools.permutations(rows):
                for columns in itertools.permutations(range(2)):
                    with self.subTest(rows=ordered, columns=columns):
                        permuted = [[row[column] for column in columns] for row in ordered]
                        self.assertEqual(matrix_rank(permuted), expected)

    def test_rank_rejects_nonfinite_coefficients(self):
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "finite"):
                    matrix_rank([[1.0, invalid]])
                sparse = SparseRankAccumulator()
                with self.assertRaisesRegex(ValueError, "finite"):
                    sparse.add_row({0: 1.0, 1: invalid})
                self.assertEqual(sparse.rank, 0)
        with self.assertRaisesRegex(ValueError, "finite"):
            SparseRankAccumulator().add_row({0: 10**400})

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

    def test_rank_rejects_invalid_tolerances_and_unrepresentable_entries(self):
        for tolerance in (0.0, -1.0, math.nan, math.inf):
            with self.subTest(tolerance=tolerance):
                with self.assertRaisesRegex(ValueError, "tolerance"):
                    matrix_rank([[1.0]], tolerance=tolerance)
                with self.assertRaisesRegex(ValueError, "tolerance"):
                    SparseRankAccumulator(tolerance=tolerance)
        for value in ("invalid", 10**400):
            with self.subTest(value=type(value)):
                with self.assertRaisesRegex(ValueError, "finite"):
                    matrix_rank([[value]])

    def test_rank_tolerance_rejects_invalid_types_and_huge_integers(self):
        for tolerance in (None, "1e-8", True, 10**400):
            with self.subTest(tolerance=repr(tolerance)):
                with self.assertRaisesRegex(ValueError, "tolerance"):
                    matrix_rank([[1.0]], tolerance=tolerance)
                with self.assertRaisesRegex(ValueError, "tolerance"):
                    SparseRankAccumulator(tolerance=tolerance)
                self.assertIsNone(
                    solve_sparse_positive_definite({0: {0: 2.0}}, [1.0], tolerance=tolerance)
                )

    def test_dense_rank_rejects_elimination_overflow(self):
        # Every source entry is finite, but subtracting opposite extremes
        # would produce infinity and an unreliable pivot.
        with self.assertRaisesRegex(ValueError, "non-finite"):
            matrix_rank([[1e308, 1e308], [-1e308, 1e308]])

    def test_sparse_rank_rejects_invalid_columns_without_changing_basis(self):
        sparse = SparseRankAccumulator()
        for row in ({-1: 1.0}, {"0": 1.0}, {True: 1.0}):
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, "columns"):
                    sparse.add_row(row)
        self.assertEqual(sparse.rank, 0)

    def test_sparse_rank_rejects_overflow_without_corrupting_basis(self):
        sparse = SparseRankAccumulator(tolerance=1e-310)
        first = {0: 1e-300, 1: 1e308}
        self.assertTrue(sparse.add_row(first))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            sparse.add_row({0: 1e308, 1: 1e308})
        self.assertEqual(sparse.rank, 1)
        self.assertFalse(sparse.add_row(first))
        self.assertEqual(sparse.rank, 1)

        # A finite elimination factor can still overflow its subtraction.
        second = SparseRankAccumulator()
        self.assertTrue(second.add_row({0: 1.0, 1: -1e308}))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            second.add_row({0: 1.0, 1: 1e308})
        self.assertEqual(second.rank, 1)

    def test_sparse_positive_definite_solver(self):
        matrix = {0: {0: 4.0, 1: 1.0}, 1: {0: 1.0, 1: 3.0}}
        result = solve_sparse_positive_definite(matrix, [1.0, 2.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 1 / 11)
        self.assertAlmostEqual(result[1], 7 / 11)
        self.assertIsNone(solve_sparse_positive_definite({0: {0: 0.0}}, [1.0]))

    def test_sparse_solver_rejects_asymmetric_matrices(self):
        # A triangular matrix can previously return an apparently converged
        # solution even though conjugate gradients requires symmetry.
        matrices = (
            {0: {0: 4.0, 1: 1.0}, 1: {1: 3.0}},
            {0: {0: 4.0, 1: 2.0}, 1: {0: 1.0, 1: 3.0}},
            {0: {0: 4.0}, 1: {0: 1.0, 1: 3.0}},
        )
        for matrix in matrices:
            with self.subTest(matrix=matrix):
                self.assertIsNone(solve_sparse_positive_definite(matrix, [1.0, 0.0]))

    def test_sparse_solver_accepts_symmetric_roundoff(self):
        matrix = {0: {0: 4.0, 1: 1.0 + 1e-13}, 1: {0: 1.0, 1: 3.0}}
        solution = solve_sparse_positive_definite(matrix, [1.0, 2.0])
        self.assertIsNotNone(solution)
        self.assertAlmostEqual(solution[0], 1 / 11)
        self.assertAlmostEqual(solution[1], 7 / 11)

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

    def test_sparse_solver_rejects_oversized_or_malformed_coefficients(self):
        # Python integers are unbounded, but math.isfinite(10**400)
        # raises OverflowError; malformed solver input must fail closed.
        huge = 10**400
        cases = (
            ({0: {0: huge}}, [1.0], 1e-10),
            ({0: {0: 2.0}}, [huge], 1e-10),
            ({0: {0: 2.0}}, [1.0], huge),
            ({0: {0: 2.0}}, ["invalid"], 1e-10),
            ({0: None}, [1.0], 1e-10),
        )
        for matrix, rhs, tolerance in cases:
            with self.subTest(matrix=matrix, rhs=rhs, tolerance=tolerance):
                self.assertIsNone(solve_sparse_positive_definite(matrix, rhs, tolerance=tolerance))


if __name__ == "__main__":
    unittest.main()
