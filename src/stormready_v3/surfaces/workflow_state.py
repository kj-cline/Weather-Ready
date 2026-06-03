from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.storage.db import Database


def service_plan_review_window(reference_date: date) -> dict[str, Any] | None:
    weekday = reference_date.weekday()
    if weekday in {2, 3}:
        window_start = reference_date + timedelta(days=(4 - weekday))
        window_end = window_start + timedelta(days=2)
        label = "Friday-Sunday"
    elif weekday in {5, 6}:
        window_start = reference_date + timedelta(days=(7 - weekday))
        window_end = window_start + timedelta(days=3)
        label = "Monday-Thursday"
    else:
        return None
    return {
        "prompt_date": reference_date,
        "window_start": window_start,
        "window_end": window_end,
        "window_label": label,
    }


def load_service_plan_window(db: Database, operator_id: str, reference_date: date) -> dict[str, Any] | None:
    window = service_plan_review_window(reference_date)
    if window is None:
        return None
    rows = db.fetchall(
        """
        SELECT service_date, planned_service_state, planned_total_covers, estimated_reduction_pct, raw_note, updated_at
        FROM operator_service_plan
        WHERE operator_id = ?
          AND service_window = ?
          AND service_date BETWEEN ? AND ?
        ORDER BY service_date
        """,
        [operator_id, ServiceWindow.DINNER.value, window["window_start"], window["window_end"]],
    )
    existing_by_date = {
        row[0]: {
            "service_date": row[0],
            "service_state": str(row[1] or ServiceState.NORMAL.value),
            "planned_total_covers": int(row[2]) if row[2] is not None else None,
            "estimated_reduction_pct": float(row[3]) if row[3] is not None else None,
            "note": str(row[4] or "").strip() or "",
            "updated_at": row[5],
        }
        for row in rows
    }
    entries: list[dict[str, Any]] = []
    pending_dates: list[date] = []
    current_date = window["window_start"]
    while current_date <= window["window_end"]:
        existing = existing_by_date.get(current_date)
        if existing is None:
            pending_dates.append(current_date)
        entries.append(
            {
                "service_date": current_date,
                "service_state": existing.get("service_state") if existing else ServiceState.NORMAL.value,
                "planned_total_covers": existing.get("planned_total_covers") if existing else None,
                "estimated_reduction_pct": existing.get("estimated_reduction_pct") if existing else None,
                "note": existing.get("note") if existing else "",
                "reviewed": existing is not None,
                "updated_at": existing.get("updated_at") if existing else None,
            }
        )
        current_date += timedelta(days=1)
    if not rows:
        return None
    return {
        **window,
        "entries": entries,
        "due_count": len(pending_dates),
        "pending_dates": pending_dates,
    }


def load_missing_actuals(db: Database, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
    lookback_start = reference_date - timedelta(days=7)
    lookback_end = reference_date - timedelta(days=1)
    rows = db.fetchall(
        """
        SELECT p.service_date, p.service_window, p.forecast_expected, p.confidence_tier
        FROM published_forecast_state p
        LEFT JOIN operator_actuals a
          ON a.operator_id = p.operator_id
         AND a.service_date = p.service_date
         AND a.service_window = p.service_window
        WHERE p.operator_id = ?
          AND p.service_window = 'dinner'
          AND p.service_date BETWEEN ? AND ?
          AND a.actual_row_id IS NULL
        ORDER BY p.service_date DESC
        LIMIT 5
        """,
        [operator_id, lookback_start, lookback_end],
    )
    return [
        {
            "service_date": row[0],
            "service_window": row[1],
            "forecast_expected": row[2],
            "confidence_tier": row[3],
        }
        for row in rows
    ]
