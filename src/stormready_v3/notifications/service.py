from __future__ import annotations

import json
from typing import Any

from stormready_v3.storage.db import Database


def list_pending_notifications(
    db: Database,
    operator_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if operator_id is None:
        rows = db.fetchall(
            """
            SELECT notification_id, operator_id, service_date, service_window, notification_type,
                   publish_reason, source_prediction_run_id, payload_json, status, created_at
            FROM notification_events
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            [limit],
        )
    else:
        rows = db.fetchall(
            """
            SELECT notification_id, operator_id, service_date, service_window, notification_type,
                   publish_reason, source_prediction_run_id, payload_json, status, created_at
            FROM notification_events
            WHERE status = 'pending' AND operator_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            [operator_id, limit],
        )
    return [
        {
            "notification_id": row[0],
            "operator_id": row[1],
            "service_date": row[2],
            "service_window": row[3],
            "notification_type": row[4],
            "publish_reason": row[5],
            "source_prediction_run_id": row[6],
            "payload": json.loads(row[7]) if row[7] else {},
            "status": row[8],
            "created_at": row[9],
        }
        for row in rows
    ]


def mark_delivered(db: Database, notification_id: int) -> None:
    db.execute(
        """
        UPDATE notification_events
        SET status = 'delivered',
            delivered_at = CURRENT_TIMESTAMP
        WHERE notification_id = ?
        """,
        [notification_id],
    )
