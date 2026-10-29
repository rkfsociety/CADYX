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
        for index in range(column, columns):
            matrix[rank][index] /= scale
        for row in range(row_count):
            if row == rank:
                continue
            factor = matrix[row][column]
            if factor:
                for index in range(column, columns):
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
            pivot = max(row, key=lambda column: (abs(row[column]), -column))
            basis = self._basis.get(pivot)
            if basis is None:
                scale = row[pivot]
                self._basis[pivot] = {column: value / scale for column, value in row.items()}
                return True
            factor = row[pivot]
            for column, value in basis.items():
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
    diagonal = [matrix.get(index, {}).get(index, 0.0) for index in range(size)]
    if any(value <= 0.0 or not math.isfinite(value) for value in diagonal):
        return None
    limit = max_iterations if max_iterations is not None else max(1, size * 2)
    solution = [0.0] * size
    residual = right_hand_side.copy()
    preconditioned = [residual[i] / diagonal[i] for i in range(size)]
    direction = preconditioned.copy()
    product = sum(residual[i] * preconditioned[i] for i in range(size))
    initial_norm = max(1.0, sum(value * value for value in right_hand_side) ** 0.5)
    if sum(value * value for value in residual) ** 0.5 <= tolerance * initial_norm:
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
        if sum(value * value for value in residual) ** 0.5 <= tolerance * initial_norm:
            return solution
        next_preconditioned = [residual[i] / diagonal[i] for i in range(size)]
        next_product = sum(residual[i] * next_preconditioned[i] for i in range(size))
        if product <= 0.0 or not math.isfinite(next_product):
            return None
        ratio = next_product / product
        direction = [next_preconditioned[i] + ratio * direction[i] for i in range(size)]
        product = next_product
    return None
