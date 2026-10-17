"""Shared numeric validation for command families."""
from __future__ import annotations

import math
from typing import Any
from .errors import ValidationError


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be a finite number.") from None
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be a finite number.")
    return result

def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be a finite positive number.") from None
    if not math.isfinite(result) or result <= 0:
        raise ValidationError(f"{name} must be a finite positive number.")
    return result
