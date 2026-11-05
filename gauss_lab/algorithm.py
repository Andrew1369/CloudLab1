"""Shared numerical operations for sequential and distributed Gauss."""
import base64

import numpy as np


def validate_system(matrix, rhs):
    a = np.array(matrix, dtype=np.float64, copy=True)
    b = np.array(rhs, dtype=np.float64, copy=True)
    if a.ndim != 2 or a.shape[0] != a.shape[1] or a.shape[0] == 0:
        raise ValueError("Matrix must be nonempty and square")
    if b.shape != (len(a),) or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Invalid right-hand side shape or nonfinite values")
    return a, b


def pivot_tolerance(a):
    return float(np.finfo(np.float64).eps * len(a) * np.max(np.abs(a)))


def back_substitute(a, b):
    x = np.empty(len(b), dtype=np.float64)
    for i in range(len(b) - 1, -1, -1):
        x[i] = (b[i] - np.dot(a[i, i + 1:], x[i + 1:])) / a[i, i]
    return x


def eliminate_rows(rows, rhs, indices, k, pivot_row, pivot_rhs):
    """Same per-row arithmetic on the coordinator and each PARCS worker."""
    for local_i in np.flatnonzero(indices > k):
        factor = rows[local_i, k] / pivot_row[k]
        rows[local_i, k + 1:] -= factor * pivot_row[k + 1:]
        rows[local_i, k] = 0.0
        rhs[local_i] -= factor * pivot_rhs


def solve_sequential(matrix, rhs, cancel=None):
    a, b = validate_system(matrix, rhs)
    tolerance = pivot_tolerance(a)
    indices = np.arange(len(b))
    for k in range(len(b)):
        if cancel and cancel():
            raise RuntimeError("Job cancelled")
        pivot = k + int(np.argmax(np.abs(a[k:, k])))
        if abs(a[pivot, k]) <= tolerance:
            raise ValueError("Matrix is singular or numerically ill-conditioned")
        if pivot != k:
            a[[k, pivot]], b[[k, pivot]] = a[[pivot, k]], b[[pivot, k]]
        eliminate_rows(a, b, indices, k, a[k], b[k])
    return back_substitute(a, b)


def generate_system(n, seed=42):
    if n < 1:
        raise ValueError("Size must be positive")
    rng = np.random.default_rng(seed)
    a = rng.uniform(-10.0, 10.0, (n, n))
    np.fill_diagonal(a, 0.0)
    np.fill_diagonal(a, np.sum(np.abs(a), axis=1) + 1.0)
    expected = rng.uniform(-1.0, 1.0, n)
    return a, a @ expected, expected


def solution_metrics(a, b, x, expected=None):
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite solution")
    numerator = float(np.linalg.norm(a @ x - b, ord=np.inf))
    denominator = float(np.linalg.norm(a, ord=np.inf) * np.linalg.norm(x, ord=np.inf)
                        + np.linalg.norm(b, ord=np.inf))
    residual = numerator / denominator if denominator else numerator
    error = float(np.max(np.abs(x - expected))) if expected is not None else None
    if not np.isfinite(residual) or residual > 1e-10 or (error is not None and (not np.isfinite(error) or error > 1e-8)):
        raise ValueError(f"Validation failed: residual={residual}, error={error}")
    return dict(relative_residual=residual, max_solution_error=error)


def pack(array):
    """Portable float64 payload over Serpent; no executable pickle objects."""
    a = np.ascontiguousarray(array, dtype="<f8")
    return dict(shape=list(a.shape), data=base64.b64encode(a.tobytes()).decode("ascii"))


def unpack(payload):
    shape = tuple(int(x) for x in payload["shape"])
    if any(x < 0 for x in shape) or len(shape) not in (1, 2):
        raise ValueError("Invalid array shape")
    return np.frombuffer(base64.b64decode(payload["data"], validate=True), dtype="<f8").reshape(shape).copy()
