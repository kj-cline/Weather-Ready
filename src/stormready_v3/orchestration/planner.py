from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS, WORKING_HORIZON_DAYS
from stormready_v3.domain.enums import RefreshReason


@dataclass(slots=True)
class RefreshPlan:
    reason: RefreshReason
    run_date: date
    actionable_dates: list[date]
    working_dates: list[date]
    refresh_window: str | None = None
    event_mode_active: bool = False


def _date_span(run_date: date, start_offset: int, end_offset: int) -> list[date]:
    return [run_date + timedelta(days=offset) for offset in range(start_offset, end_offset + 1)]


def plan_refresh_cycle(
    *,
    run_date: date,
    reason: RefreshReason,
    refresh_window: str | None = None,
    event_mode_active: bool = False,
) -> RefreshPlan:
    actionable_dates = _date_span(run_date, 0, ACTIONABLE_HORIZON_DAYS - 1)
    working_dates = _date_span(run_date, ACTIONABLE_HORIZON_DAYS, WORKING_HORIZON_DAYS - 1)

    return RefreshPlan(
        reason=reason,
        run_date=run_date,
        actionable_dates=actionable_dates,
        working_dates=working_dates,
        refresh_window=refresh_window,
        event_mode_active=event_mode_active,
    )
