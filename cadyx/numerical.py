"""Small deterministic numerical helpers shared by CAD constraint solvers."""
from __future__ import annotations

import math


def matrix_rank(rows: list[list[float]], *, tolerance: float = 1e-8) -> int:
    """Return the pivoted Gaussian-elimination rank of a dense row matrix."""
    if not rows:
        return 0
    columns = len(rows[0])
    if any(len(row) != columns for row in rows):
        raise ValueError("Matrix rows must have a consistent number of columns.")
    if not columns:
        return 0
    matrix = [row[:] for row in rows]
    row_count = len(matrix)
    rank = 0
    for column in range(columns):
        if rank >= row_count:
            break
        pivot = max(range(rank, row_count), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < tolerance:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        scale = matrix[rank][column]
        # Forward elimination needs no normalized pivot row. Dividing a small
        # pivot into a large finite coefficient can overflow to infinity and
        # falsely increase the rank of dependent constraints.
        for row in range(rank + 1, row_count):
            factor = matrix[row][column] / scale
            matrix[row][column] = 0.0
            if factor:
                for index in range(column + 1, columns):
                    matrix[row][index] -= factor * matrix[rank][index]
        rank += 1
    return rank


class SparseRankAccumulator:
    """Incremental sparse row-echelon rank tracker for rigid constraint Jacobians."""

    def __init__(self, *, tolerance: float = 1e-8) -> None:
        self.tolerance = tolerance
        self._basis: dict[int, dict[int, float]] = {}

    @property
    def rank(self) -> int:
        return len(self._basis)

    def add_row(self, source: dict[int, float]) -> bool:
        row = {column: float(value) for column, value in source.items()
               if abs(float(value)) >= self.tolerance}
        while row:
            # Use column-echelon order: a largest-magnitude pivot can skip an
            # existing pivot and count a dependent constraint as independent.
            pivot = min(row)
            basis = self._basis.get(pivot)
            if basis is None:
                # Preserve the source scale: dividing by a tiny pivot can
                # overflow other finite coefficients and invent a new rank.
                self._basis[pivot] = row
                return True
            # Remove the pivot exactly, rather than leaving roundoff that
            # could repeatedly eliminate the same column.
            factor = row.pop(pivot) / basis[pivot]
            for column, value in basis.items():
                if column == pivot:
                    continue
                adjusted = row.get(column, 0.0) - factor * value
                if abs(adjusted) < self.tolerance:
                    row.pop(column, None)
                else:
                    row[column] = adjusted
        return False


def solve_sparse_positive_definite(
    matrix: dict[int, dict[int, float]], right_hand_side: list[float], *,
    tolerance: float = 1e-10, max_iterations: int | None = None,
) -> list[float] | None:
    """Solve a sparse symmetric positive-definite system with diagonal PCG."""
    size = len(right_hand_side)
    if not size:
        return []
    if (not math.isfinite(tolerance) or tolerance <= 0.0
            or (max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1))
            or any(not math.isfinite(value) for value in right_hand_side)):
        return None
    # Invalid sparse indices must not wrap through Python's negative indexing or
    # crash the solver during a matrix-vector product.
    for row_index, row in matrix.items():
        if type(row_index) is not int or not 0 <= row_index < size:
            return None
        if any(type(column) is not int or not 0 <= column < size or not math.isfinite(value)
               for column, value in row.items()):
            return None
    diagonal = [matrix.get(index, {}).get(index, 0.0) for index in range(size)]
    if any(value <= 0.0 or not math.isfinite(value) for value in diagonal):
        return None
    limit = max_iterations if max_iterations is not None else max(1, size * 2)
    solution = [0.0] * size
    residual = right_hand_side.copy()
    preconditioned = [residual[i] / diagonal[i] for i in range(size)]
    direction = preconditioned.copy()
    product = sum(residual[i] * preconditioned[i] for i in range(size))
    if not math.isfinite(product):
        return None
    initial_norm = max(1.0, math.hypot(*right_hand_side))
    if math.hypot(*residual) <= tolerance * initial_norm:
        return solution
    for _ in range(limit):
        applied = [sum(value * direction[column] for column, value in row.items())
                   for row in (matrix.get(index, {}) for index in range(size))]
        denominator = sum(direction[i] * applied[i] for i in range(size))
        if denominator <= 0.0 or not math.isfinite(denominator):
            return None
        step = product / denominator
        solution = [solution[i] + step * direction[i] for i in range(size)]
        residual = [residual[i] - step * applied[i] for i in range(size)]
        if (any(not math.isfinite(value) for value in solution)
                or any(not math.isfinite(value) for value in residual)):
            return None
        if math.hypot(*residual) <= tolerance * initial_norm:
            return solution
        next_preconditioned = [residual[i] / diagonal[i] for i in range(size)]
        next_product = sum(residual[i] * next_preconditioned[i] for i in range(size))
        if product <= 0.0 or not math.isfinite(next_product):
            return None
        ratio = next_product / product
        direction = [next_preconditioned[i] + ratio * direction[i] for i in range(size)]
        product = next_product
    return None
