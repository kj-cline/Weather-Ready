from __future__ import annotations

from datetime import UTC, date, datetime
import json
from typing import Any

from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.storage.db import Database
from stormready_v3.workflows.actuals import record_actual_total_and_update


def _db_timestamp(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def mark_actual_corrected(db: Database, actual_row_id: int, note: str | None = None) -> None:
    db.execute(
        """
        UPDATE operator_actuals
        SET corrected_at = ?, note = COALESCE(?, note)
        WHERE actual_row_id = ?
        """,
        [_db_timestamp(), note, actual_row_id],
    )


def stage_correction_suggestion(
    db: Database,
    *,
    operator_id: str,
    service_date: date | None,
    service_window: ServiceWindow | None,
    source_type: str,
    suggested_fields: dict[str, Any] | None = None,
    suggested_service_state: str | None = None,
    source_note_id: int | None = None,
) -> int | None:
    suggested_fields = suggested_fields or {}
    if not suggested_fields and not suggested_service_state:
        return None
    db.execute(
        """
        INSERT INTO correction_suggestions (
            operator_id, service_date, service_window, source_type, source_note_id,
            suggested_fields_json, suggested_service_state, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            operator_id,
            service_date,
            service_window.value if service_window is not None else None,
            source_type,
            source_note_id,
            json.dumps(suggested_fields),
            suggested_service_state,
            "pending",
            _db_timestamp(),
        ],
    )
    row = db.fetchone("SELECT max(suggestion_id) FROM correction_suggestions")
    return int(row[0]) if row is not None and row[0] is not None else None


def list_pending_corrections(
    db: Database,
    *,
    operator_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if operator_id is None:
        rows = db.fetchall(
            """
            SELECT suggestion_id, operator_id, service_date, service_window, source_type,
                   source_note_id, suggested_fields_json, suggested_service_state, created_at
            FROM correction_suggestions
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            [limit],
        )
    else:
        rows = db.fetchall(
            """
            SELECT suggestion_id, operator_id, service_date, service_window, source_type,
                   source_note_id, suggested_fields_json, suggested_service_state, created_at
            FROM correction_suggestions
            WHERE status = 'pending' AND operator_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            [operator_id, limit],
        )
    return [
        {
            "suggestion_id": row[0],
            "operator_id": row[1],
            "service_date": row[2],
            "service_window": row[3],
            "source_type": row[4],
            "source_note_id": row[5],
            "suggested_fields": json.loads(row[6]) if row[6] else {},
            "suggested_service_state": row[7],
            "created_at": row[8],
        }
        for row in rows
    ]


def apply_correction_suggestion(
    db: Database,
    *,
    suggestion_id: int,
    decision_note: str | None = None,
) -> bool:
    row = db.fetchone(
        """
        SELECT operator_id, service_date, service_window, suggested_fields_json, suggested_service_state, status
        FROM correction_suggestions
        WHERE suggestion_id = ?
        """,
        [suggestion_id],
    )
    if row is None:
        return False
    operator_id = str(row[0])
    service_date = row[1]
    service_window_value = row[2]
    suggested_fields = json.loads(row[3]) if row[3] else {}
    suggested_service_state = row[4]
    status = str(row[5])
    if status != "pending" or service_date is None or service_window_value is None:
        return False

    actual_row = db.fetchone(
        """
        SELECT realized_total_covers, realized_reserved_covers, realized_walk_in_covers,
               realized_waitlist_converted_covers, inside_covers, outside_covers,
               reservation_no_show_covers, reservation_cancellation_covers, service_state
        FROM operator_actuals
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        ORDER BY COALESCE(corrected_at, entered_at) DESC
        LIMIT 1
        """,
        [operator_id, service_date, service_window_value],
    )

    def _field(name: str, index: int) -> int | None:
        if name in suggested_fields and suggested_fields[name] not in {None, ""}:
            return int(suggested_fields[name])
        if actual_row is not None and actual_row[index] is not None:
            return int(actual_row[index])
        return None

    realized_total_covers = _field("realized_total_covers", 0)
    if realized_total_covers is None:
        return False

    service_state = ServiceState(str(suggested_service_state)) if suggested_service_state else (
        ServiceState(str(actual_row[8])) if actual_row is not None and actual_row[8] is not None else ServiceState.NORMAL
    )

    record_actual_total_and_update(
        db,
        operator_id=operator_id,
        service_date=service_date,
        service_window=ServiceWindow(str(service_window_value)),
        realized_total_covers=realized_total_covers,
        realized_reserved_covers=_field("realized_reserved_covers", 1),
        realized_walk_in_covers=_field("realized_walk_in_covers", 2),
        realized_waitlist_converted_covers=_field("realized_waitlist_converted_covers", 3),
        inside_covers=_field("inside_covers", 4),
        outside_covers=_field("outside_covers", 5),
        reservation_no_show_covers=_field("reservation_no_show_covers", 6),
        reservation_cancellation_covers=_field("reservation_cancellation_covers", 7),
        service_state=service_state,
        entry_mode="suggested_correction",
        note=decision_note or "applied_correction_suggestion",
    )
    db.execute(
        """
        UPDATE correction_suggestions
        SET status = 'applied', decided_at = ?, decision_note = ?
        WHERE suggestion_id = ?
        """,
        [_db_timestamp(), decision_note, suggestion_id],
    )
    return True


def reject_correction_suggestion(
    db: Database,
    *,
    suggestion_id: int,
    decision_note: str | None = None,
) -> bool:
    existing = db.fetchone(
        "SELECT suggestion_id FROM correction_suggestions WHERE suggestion_id = ? AND status = 'pending'",
        [suggestion_id],
    )
    if existing is None:
        return False
    db.execute(
        """
        UPDATE correction_suggestions
        SET status = 'rejected', decided_at = ?, decision_note = ?
        WHERE suggestion_id = ?
        """,
        [_db_timestamp(), decision_note, suggestion_id],
    )
    return True
