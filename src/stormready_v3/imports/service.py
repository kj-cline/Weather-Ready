from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from stormready_v3.connectors.mapping import normalize_import_row
from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.mvp_scope import ensure_runtime_window_supported, is_runtime_window_supported
from stormready_v3.prediction.engine import day_group_for_date
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository
from stormready_v3.workflows.actuals import record_actual_row


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _parse_service_window(value: Any, default_window: ServiceWindow) -> ServiceWindow:
    if isinstance(value, ServiceWindow):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        for window in ServiceWindow:
            if window.value == normalized:
                return window
    return default_window


def _parse_service_state(value: Any) -> ServiceState:
    if isinstance(value, ServiceState):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        for state in ServiceState:
            if state.value == normalized:
                return state
    return ServiceState.NORMAL


def _maybe_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class HistoricalImportResult:
    rows_seen: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    rows_unsupported_window_skipped: int = 0
    baseline_rows_written: int = 0


class HistoricalImportService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.operators = OperatorRepository(db)

    def ingest_rows(
        self,
        *,
        operator_id: str,
        rows: list[dict[str, Any]],
        default_service_window: ServiceWindow = ServiceWindow.DINNER,
        entry_mode: str = "historical_import",
        derive_baselines: bool = True,
    ) -> HistoricalImportResult:
        ensure_runtime_window_supported(default_service_window, context="historical import default_service_window")
        result = HistoricalImportResult(rows_seen=len(rows))
        baseline_buckets: dict[tuple[ServiceWindow, str], list[int]] = {}

        for raw_row in rows:
            service_date = _parse_date(raw_row.get("service_date") or raw_row.get("date"))
            if service_date is None:
                result.rows_skipped += 1
                continue

            normalized = normalize_import_row(raw_row)
            realized_total_covers = _maybe_int(normalized.get("realized_total_covers"))
            if realized_total_covers is None:
                result.rows_skipped += 1
                continue

            service_window = _parse_service_window(
                raw_row.get("service_window") or normalized.get("service_window"),
                default_service_window,
            )
            if not is_runtime_window_supported(service_window):
                result.rows_skipped += 1
                result.rows_unsupported_window_skipped += 1
                continue
            service_state = _parse_service_state(raw_row.get("service_state") or normalized.get("service_state"))

            record_actual_row(
                self.db,
                operator_id=operator_id,
                service_date=str(service_date),
                service_window=service_window,
                realized_total_covers=realized_total_covers,
                realized_reserved_covers=_maybe_int(normalized.get("realized_reserved_covers")),
                realized_walk_in_covers=_maybe_int(normalized.get("realized_walk_in_covers")),
                realized_waitlist_converted_covers=_maybe_int(normalized.get("realized_waitlist_converted_covers")),
                inside_covers=_maybe_int(normalized.get("inside_covers")),
                outside_covers=_maybe_int(normalized.get("outside_covers")),
                reservation_no_show_covers=_maybe_int(normalized.get("reservation_no_show_covers")),
                reservation_cancellation_covers=_maybe_int(normalized.get("reservation_cancellation_covers")),
                service_state=service_state,
                entry_mode=entry_mode,
                note="imported historical row",
            )
            result.rows_imported += 1

            if derive_baselines and service_state is ServiceState.NORMAL:
                baseline_buckets.setdefault(
                    (service_window, day_group_for_date(service_date)),
                    [],
                ).append(realized_total_covers)

        if derive_baselines:
            for (service_window, day_group), values in baseline_buckets.items():
                if not values:
                    continue
                self.operators.upsert_weekly_baseline(
                    operator_id=operator_id,
                    service_window=service_window,
                    day_group=day_group,
                    baseline_total_covers=int(round(sum(values) / len(values))),
                    source_type="imported_history",
                )
                result.baseline_rows_written += 1

        return result
