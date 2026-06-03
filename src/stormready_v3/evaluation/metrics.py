from __future__ import annotations


def absolute_error(actual: int, predicted: int) -> int:
    return abs(actual - predicted)


def absolute_error_pct(actual: int, predicted: int) -> float | None:
    if actual == 0:
        return None
    return abs(actual - predicted) / actual


def inside_interval(actual: int, low: int, high: int) -> bool:
    return low <= actual <= high
