"""Pure-Python evaluators for sketch curves (all coordinates are tuples of floats).

These helpers deliberately evaluate geometry only; they do not create CADYX
document objects. Parameters and knot vectors use the contracts in each docstring.
"""

from __future__ import annotations

import math
from typing import Sequence

Point = Sequence[float]


def _points(points: Sequence[Point], minimum: int = 1) -> tuple[tuple[float, ...], ...]:
    result = tuple(tuple(float(v) for v in p) for p in points)
    if len(result) < minimum or not result or not result[0]:
        raise ValueError(f"at least {minimum} non-empty points are required")
    dimension = len(result[0])
    if any(len(p) != dimension or not all(math.isfinite(v) for v in p) for p in result):
        raise ValueError("points must have equal dimensions and finite coordinates")
    return result


def _parameter(value: float, low: float, high: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < low or value > high:
        raise ValueError(f"parameter must be finite and in [{low}, {high}]")
    return value


def evaluate_bezier(control_points: Sequence[Point], parameter: float) -> tuple[float, ...]:
    """Evaluate a polynomial Bezier curve by de Casteljau, for t in [0, 1]."""
    points = _points(control_points, 2)
    t = _parameter(parameter, 0.0, 1.0)
    work = [list(p) for p in points]
    for width in range(len(work) - 1, 0, -1):
        for i in range(width):
            work[i] = [(1 - t) * a + t * b for a, b in zip(work[i], work[i + 1])]
    return tuple(work[0])


def evaluate_bspline(control_points: Sequence[Point], degree: int,
                     knots: Sequence[float], parameter: float) -> tuple[float, ...]:
    """Evaluate a non-rational B-spline by de Boor.

    Knots must be a nondecreasing vector of length n + degree + 2. The valid
    parameter interval is [knots[degree], knots[n + 1]], where n is the final
    control-point index. Repeated knots are supported.
    """
    points = _points(control_points, 1)
    if isinstance(degree, bool) or not isinstance(degree, int) or degree < 0 or degree >= len(points):
        raise ValueError("degree must be an integer in [0, control point count)")
    vector = tuple(float(k) for k in knots)
    n = len(points) - 1
    if len(vector) != n + degree + 2 or any(not math.isfinite(k) for k in vector):
        raise ValueError("knot vector length or values are invalid")
    if any(a > b for a, b in zip(vector, vector[1:])):
        raise ValueError("knots must be nondecreasing")
    low, high = vector[degree], vector[n + 1]
    if not low < high:
        raise ValueError("B-spline parameter domain must have positive length")
    u = _parameter(parameter, low, high)
    span = n if u == high else next(i for i in range(degree, n + 1) if vector[i] <= u < vector[i + 1])
    work = [list(points[span - degree + j]) for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            i = span - degree + j
            denominator = vector[i + degree - r + 1] - vector[i]
            alpha = (u - vector[i]) / denominator if denominator else 0.0
            work[j] = [(1 - alpha) * a + alpha * b for a, b in zip(work[j - 1], work[j])]
    return tuple(work[degree])


def evaluate_nurbs(control_points: Sequence[Point], weights: Sequence[float], degree: int,
                    knots: Sequence[float], parameter: float) -> tuple[float, ...]:
    """Evaluate a rational B-spline; weights must be finite and strictly positive."""
    points = _points(control_points, 1)
    rational_weights = tuple(float(w) for w in weights)
    if len(rational_weights) != len(points) or any(not math.isfinite(w) or w <= 0 for w in rational_weights):
        raise ValueError("one finite positive weight is required per control point")
    homogeneous = tuple(tuple(v * w for v in point) + (w,) for point, w in zip(points, rational_weights))
    evaluated = evaluate_bspline(homogeneous, degree, knots, parameter)
    if evaluated[-1] == 0 or not math.isfinite(evaluated[-1]):
        raise ValueError("rational denominator is zero or non-finite")
    return tuple(v / evaluated[-1] for v in evaluated[:-1])


def evaluate_interpolating_spline(points: Sequence[Point], parameter: float,
                                  *, closed: bool = False) -> tuple[float, ...]:
    """Evaluate piecewise cubic Catmull-Rom through points using uniform t.

    The domain is [0, segment_count]. For closed curves the final point must
    equal the first; the duplicate closing point is omitted internally.
    Open endpoint tangents use reflected phantom points.
    """
    values = _points(points, 3)
    if closed:
        if values[0] != values[-1]:
            raise ValueError("closed spline points must repeat the first point at the end")
        values = values[:-1]
        if len(values) < 3:
            raise ValueError("closed spline requires at least three distinct vertices")
        count = len(values)
        t = _parameter(parameter, 0.0, float(count))
        segment = min(int(t), count - 1)
        u = t - segment
        p0, p1 = values[(segment - 1) % count], values[segment]
        p2, p3 = values[(segment + 1) % count], values[(segment + 2) % count]
    else:
        count = len(values) - 1
        t = _parameter(parameter, 0.0, float(count))
        segment = min(int(t), count - 1)
        u = t - segment
        p1, p2 = values[segment], values[segment + 1]
        p0 = values[segment - 1] if segment else tuple(2 * a - b for a, b in zip(p1, p2))
        p3 = values[segment + 2] if segment + 2 < len(values) else tuple(2 * b - a for a, b in zip(p1, p2))
    u2, u3 = u * u, u * u * u
    return tuple(0.5 * ((2 * b) + (-a + c) * u + (2*a - 5*b + 4*c - d) * u2
                        + (-a + 3*b - 3*c + d) * u3)
                 for a, b, c, d in zip(p0, p1, p2, p3))


def evaluate_parabola(vertex: Point, axis: Point, focal_length: float,
                      parameter: float) -> tuple[float, float]:
    """Evaluate a 2D parabola; axis is its unit opening direction, t is signed distance.

    The focal length p is positive and local coordinates are (t²/(4p), t).
    """
    v, a = _points((vertex,)), _points((axis,))
    if len(v[0]) != 2 or len(a[0]) != 2:
        raise ValueError("parabola requires 2D vertex and axis")
    norm = math.hypot(*a[0])
    if not math.isclose(norm, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("parabola axis must be a unit vector")
    p = float(focal_length)
    if not math.isfinite(p) or p <= 0:
        raise ValueError("focal_length must be finite and positive")
    t = float(parameter)
    if not math.isfinite(t):
        raise ValueError("parameter must be finite")
    vx, vy = v[0]
    ax, ay = a[0]
    # local transverse direction is the +90 degree rotation of the axis.
    return (vx + ax * t*t/(4*p) - ay*t, vy + ay*t*t/(4*p) + ax*t)


def evaluate_hyperbola(center: Point, axis: Point, semi_major: float, semi_minor: float,
                       parameter: float, *, branch: int = 1) -> tuple[float, float]:
    """Evaluate a 2D hyperbola using t in radians, x=a*branch*cosh(t), y=b*sinh(t)."""
    c, a = _points((center,)), _points((axis,))
    if len(c[0]) != 2:
        raise ValueError("hyperbola requires 2D center and axis")
    norm = math.hypot(*a[0])
    if not math.isclose(norm, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("hyperbola axis must be a unit vector")
    major, minor, t = float(semi_major), float(semi_minor), float(parameter)
    if not all(math.isfinite(x) and x > 0 for x in (major, minor)) or not math.isfinite(t):
        raise ValueError("semi axes must be positive and parameter finite")
    if branch not in (-1, 1):
        raise ValueError("branch must be -1 or 1")
    x, y = branch * major * math.cosh(t), minor * math.sinh(t)
    ax, ay = a[0]
    return (c[0][0] + ax*x - ay*y, c[0][1] + ay*x + ax*y)


def evaluate_conic(coefficients: Sequence[float], x: float, *, branch: int = 1) -> tuple[float, float]:
    """Evaluate implicit conic Ax²+Bxy+Cy²+Dx+Ey+F=0 at x.

    Returns one of the real y roots selected by branch (+1 larger, -1 smaller).
    This x-parameterization is singular for vertical tangencies and does not
    impose an ellipse/hyperbola/parabola classification.
    """
    if len(coefficients) != 6:
        raise ValueError("six conic coefficients are required")
    A, B, C, D, E, F = (float(v) for v in coefficients)
    x = float(x)
    if not all(math.isfinite(v) for v in (A, B, C, D, E, F, x)):
        raise ValueError("coefficients and parameter must be finite")
    if branch not in (-1, 1):
        raise ValueError("branch must be -1 or 1")
    q, linear, constant = C, B*x + E, A*x*x + D*x + F
    if abs(q) < 1e-15:
        if abs(linear) < 1e-15:
            raise ValueError("conic has no unique y value at x")
        return x, -constant / linear
    discriminant = linear*linear - 4*q*constant
    if discriminant < 0:
        raise ValueError("no real conic point exists at x")
    y = (-linear + branch * math.sqrt(discriminant)) / (2*q)
    return x, y


def sample_conic_arc(coefficients: Sequence[float], start_x: float, end_x: float,
                     samples: int, *, branch: int = 1) -> tuple[tuple[float, float], ...]:
    """Sample an x-monotone conic arc on one root branch, including both endpoints."""
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 2:
        raise ValueError("samples must be an integer >= 2")
    start, end = float(start_x), float(end_x)
    if not math.isfinite(start) or not math.isfinite(end) or start == end:
        raise ValueError("arc x bounds must be finite and distinct")
    return tuple(evaluate_conic(coefficients, start + (end-start)*i/(samples-1), branch=branch)
                 for i in range(samples))
