"""Holiday and calendar awareness for service state resolution.

Provides two things:
1. Calendar state — is this date a holiday that changes service patterns?
2. Baseline adjustment — should baseline covers be scaled for known high/low days?

Restaurant-relevant holidays fall into three categories:
- CLOSED: most restaurants close (Christmas, Thanksgiving)
- ELEVATED: high-demand holidays (Valentine's, Mother's Day, NYE)
- MODIFIED: altered patterns but not extreme (July 4th, Memorial Day, Labor Day)
"""
from __future__ import annotations

from datetime import date, timedelta

from stormready_v3.domain.enums import ServiceState


# ---------------------------------------------------------------------------
# Holiday definitions
# ---------------------------------------------------------------------------

# (month, day) → (label, category, baseline_multiplier)
# baseline_multiplier: 1.0 = normal, >1.0 = busier, <1.0 = slower, 0.0 = closed
_FIXED_HOLIDAYS: dict[tuple[int, int], tuple[str, str, float]] = {
    (1, 1): ("new_years_day", "modified", 0.45),
    (2, 14): ("valentines_day", "elevated", 1.25),
    (7, 4): ("july_fourth", "modified", 0.45),
    (10, 31): ("halloween", "modified", 0.85),
    (12, 24): ("christmas_eve", "modified", 0.60),
    (12, 25): ("christmas_day", "closed", 0.0),
    (12, 31): ("new_years_eve", "elevated", 1.30),
}

_FIXED_CLOSURE_RISK: dict[tuple[int, int], tuple[str, float]] = {
    (1, 1): ("new_years_day closure risk", 0.40),
    (7, 4): ("july_fourth closure risk", 0.45),
    (12, 24): ("christmas_eve modified-hours risk", 0.45),
}

# Baseline adjustments for the day before/after major holidays
_HOLIDAY_ADJACENT: dict[tuple[int, int], tuple[str, float]] = {
    (2, 13): ("pre_valentines", 1.08),
    (12, 23): ("pre_christmas_eve", 1.05),
    (12, 26): ("post_christmas", 0.70),
    (1, 2): ("post_new_years", 0.60),
}


def _thanksgiving(year: int) -> date:
    """Fourth Thursday of November."""
    # Nov 1 weekday, find first Thursday, add 3 weeks
    nov1 = date(year, 11, 1)
    first_thu = (3 - nov1.weekday()) % 7
    return date(year, 11, 1 + first_thu + 21)


def _mothers_day(year: int) -> date:
    """Second Sunday of May."""
    may1 = date(year, 5, 1)
    first_sun = (6 - may1.weekday()) % 7
    return date(year, 5, 1 + first_sun + 7)


def _fathers_day(year: int) -> date:
    """Third Sunday of June."""
    jun1 = date(year, 6, 1)
    first_sun = (6 - jun1.weekday()) % 7
    return date(year, 6, 1 + first_sun + 14)


def _memorial_day(year: int) -> date:
    """Last Monday of May."""
    may31 = date(year, 5, 31)
    offset = (may31.weekday() - 0) % 7  # 0 = Monday
    return date(year, 5, 31 - offset)


def _labor_day(year: int) -> date:
    """First Monday of September."""
    sep1 = date(year, 9, 1)
    offset = (0 - sep1.weekday()) % 7
    return date(year, 9, 1 + offset)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm for Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _floating_holidays(year: int) -> dict[date, tuple[str, str, float]]:
    """Compute floating holidays for a given year."""
    holidays: dict[date, tuple[str, str, float]] = {}

    tg = _thanksgiving(year)
    holidays[tg] = ("thanksgiving", "closed", 0.0)
    holidays[tg - timedelta(days=1)] = ("thanksgiving_eve", "modified", 0.75)
    holidays[tg + timedelta(days=1)] = ("black_friday", "modified", 0.60)

    md = _mothers_day(year)
    holidays[md] = ("mothers_day", "elevated", 1.30)

    fd = _fathers_day(year)
    holidays[fd] = ("fathers_day", "elevated", 1.15)

    mem = _memorial_day(year)
    holidays[mem] = ("memorial_day", "modified", 0.75)

    lab = _labor_day(year)
    holidays[lab] = ("labor_day", "modified", 0.75)

    easter = _easter(year)
    holidays[easter] = ("easter_sunday", "elevated", 1.20)

    return holidays


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calendar_state_for_date(service_date: date) -> ServiceState | None:
    """Return the calendar-derived service state for a date, or None if normal."""
    # Check fixed holidays
    key = (service_date.month, service_date.day)
    if key in _FIXED_HOLIDAYS:
        _, category, _ = _FIXED_HOLIDAYS[key]
        if category == "closed":
            return ServiceState.CLOSED
        return ServiceState.HOLIDAY_MODIFIED

    # Check floating holidays
    floating = _floating_holidays(service_date.year)
    if service_date in floating:
        _, category, _ = floating[service_date]
        if category == "closed":
            return ServiceState.CLOSED
        return ServiceState.HOLIDAY_MODIFIED

    return None


def holiday_baseline_multiplier(service_date: date) -> float:
    """Return a baseline multiplier for the date. 1.0 = normal day."""
    # Check fixed holidays
    key = (service_date.month, service_date.day)
    if key in _FIXED_HOLIDAYS:
        _, _, multiplier = _FIXED_HOLIDAYS[key]
        return multiplier

    # Check adjacent days
    if key in _HOLIDAY_ADJACENT:
        _, multiplier = _HOLIDAY_ADJACENT[key]
        return multiplier

    # Check floating holidays
    floating = _floating_holidays(service_date.year)
    if service_date in floating:
        _, _, multiplier = floating[service_date]
        return multiplier

    return 1.0


def holiday_label(service_date: date) -> str | None:
    """Return a human-readable holiday label, or None if normal day."""
    key = (service_date.month, service_date.day)
    if key in _FIXED_HOLIDAYS:
        return _FIXED_HOLIDAYS[key][0]
    if key in _HOLIDAY_ADJACENT:
        return _HOLIDAY_ADJACENT[key][0]
    floating = _floating_holidays(service_date.year)
    if service_date in floating:
        return floating[service_date][0]
    return None


def holiday_service_risk(service_date: date) -> dict[str, object] | None:
    """Return non-canonical service-state risk from calendar context.

    This is uncertainty evidence only. It should not mark an operator closed unless
    the holiday itself is a strong closed rule.
    """
    key = (service_date.month, service_date.day)
    if key in _FIXED_HOLIDAYS:
        label, category, _ = _FIXED_HOLIDAYS[key]
        if category == "closed":
            return {
                "risk_state": ServiceState.CLOSED.value,
                "risk_score": 0.90,
                "confidence": "high",
                "source": "calendar_rule",
                "reason": f"{label} closed rule",
            }
        if key in _FIXED_CLOSURE_RISK:
            reason, score = _FIXED_CLOSURE_RISK[key]
            return {
                "risk_state": ServiceState.CLOSED.value,
                "risk_score": score,
                "confidence": "medium",
                "source": "calendar_risk",
                "reason": reason,
            }
        if category == "modified":
            return {
                "risk_state": ServiceState.HOLIDAY_MODIFIED.value,
                "risk_score": 0.18,
                "confidence": "low",
                "source": "calendar_risk",
                "reason": f"{label} modified service risk",
            }
        return None

    floating = _floating_holidays(service_date.year)
    if service_date not in floating:
        return None
    label, category, _ = floating[service_date]
    if category == "closed":
        return {
            "risk_state": ServiceState.CLOSED.value,
            "risk_score": 0.90,
            "confidence": "high",
            "source": "calendar_rule",
            "reason": f"{label} closed rule",
        }
    if category == "modified":
        return {
            "risk_state": ServiceState.HOLIDAY_MODIFIED.value,
            "risk_score": 0.22,
            "confidence": "medium",
            "source": "calendar_risk",
            "reason": f"{label} modified service risk",
        }
    return None
