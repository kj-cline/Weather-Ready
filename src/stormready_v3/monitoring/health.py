from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from stormready_v3.storage.db import Database


@dataclass(slots=True)
class RuntimeHealthSummary:
    reference_time: datetime
    active_operator_count: int
    stale_operator_count: int
    pending_notification_count: int
    pending_correction_count: int
    pending_refresh_request_count: int
    latest_supervisor_tick: dict[str, Any] | None = None
    stale_operators: list[dict[str, Any]] = field(default_factory=list)


class RuntimeHealthService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build_summary(self, *, reference_time: datetime | None = None) -> RuntimeHealthSummary:
        reference_time = reference_time or datetime.now(UTC)
        active_operator_count = self._count_active_operators()
        stale_operators = self._load_stale_operators(reference_time)
        return RuntimeHealthSummary(
            reference_time=reference_time,
            active_operator_count=active_operator_count,
            stale_operator_count=len(stale_operators),
            pending_notification_count=self._count_pending_notifications(),
            pending_correction_count=self._count_pending_corrections(),
            pending_refresh_request_count=self._count_pending_refresh_requests(),
            latest_supervisor_tick=self._load_latest_supervisor_tick(),
            stale_operators=stale_operators,
        )

    def _count_active_operators(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) FROM operators WHERE status = 'active'")
        return int(row[0]) if row is not None else 0

    def _count_pending_notifications(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) FROM notification_events WHERE status = 'pending'")
        return int(row[0]) if row is not None else 0

    def _count_pending_corrections(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) FROM correction_suggestions WHERE status = 'pending'")
        return int(row[0]) if row is not None else 0

    def _count_pending_refresh_requests(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) FROM refresh_request_queue WHERE status = 'pending'")
        return int(row[0]) if row is not None else 0

    def _load_latest_supervisor_tick(self) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT started_at, completed_at, tick_mode, summary_json, status, failure_reason
            FROM supervisor_tick_log
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        if row is None:
            return None
        return {
            "started_at": row[0],
            "completed_at": row[1],
            "tick_mode": row[2],
            "summary": json.loads(row[3]) if row[3] else {},
            "status": row[4],
            "failure_reason": row[5],
        }

    def _load_stale_operators(self, reference_time: datetime) -> list[dict[str, Any]]:
        stale_before = self._db_timestamp(reference_time - timedelta(hours=18))
        rows = self.db.fetchall(
            """
            WITH latest_refresh AS (
                SELECT operator_id, max(completed_at) AS last_refresh_at
                FROM forecast_refresh_runs
                WHERE status = 'completed'
                GROUP BY operator_id
            )
            SELECT o.operator_id, o.restaurant_name, lr.last_refresh_at
            FROM operators o
            LEFT JOIN latest_refresh lr ON lr.operator_id = o.operator_id
            WHERE o.status = 'active'
              AND (lr.last_refresh_at IS NULL OR lr.last_refresh_at < ?)
            ORDER BY o.operator_id
            """,
            [stale_before],
        )
        return [
            {
                "operator_id": row[0],
                "restaurant_name": row[1],
                "last_refresh_at": row[2],
            }
            for row in rows
        ]

    @staticmethod
    def _db_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
