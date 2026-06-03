"""Runtime bridge utilities shared by the API and orchestration tools.

These helpers used to live inside the Streamlit package. They are now UI-agnostic
so the API and any frontend can reuse the same refresh and notification behavior.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.conversation.attention import ordered_attention_sections
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.operator_text import communication_text_from_state, translate_operator_text
from stormready_v3.storage.db import Database


def maybe_run_supervisor_tick(
    db: Database,
    operator_id: str,
    profile: OperatorProfile | None = None,
    now: datetime | None = None,
    *,
    force: bool = False,
    refresh_reason: str = "scheduled",
) -> dict[str, Any]:
    """Run the canonical supervisor path for one operator."""
    if now is None:
        now = datetime.now(UTC)

    status: dict[str, Any] = {
        "ran_refresh": False,
        "reason": None,
        "error": None,
        "queued_requests_completed": 0,
        "signal_monitor_runs": 0,
        "scheduled_runs": 0,
        "event_mode_runs": 0,
    }

    try:
        from stormready_v3.domain.enums import RefreshReason
        from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
        from stormready_v3.orchestration.supervisor import SupervisorService
        from stormready_v3.storage.repositories import OperatorRepository

        orchestrator = DeterministicOrchestrator(db)
        operators = OperatorRepository(db)

        if profile is None:
            profile = operators.load_operator_profile(operator_id)
        if profile is None:
            status["reason"] = "no_profile"
            return status

        supervisor = SupervisorService(orchestrator)
        if force:
            resolved_refresh_reason = RefreshReason(refresh_reason)
            supervisor.enqueue_operator_refresh_request(
                operator_id=operator_id,
                note=f"runtime_bridge::{resolved_refresh_reason.value}",
            )
            tick_result = supervisor.run_operator_tick(
                operator_id=operator_id,
                now=now,
                process_queue=True,
                process_scheduled=False,
                process_event_mode=False,
            )
            status["reason"] = resolved_refresh_reason.value if tick_result.queued_requests_completed else "refresh_in_progress"
        else:
            tick_result = supervisor.run_operator_tick(
                operator_id=operator_id,
                now=now,
                process_queue=True,
                process_scheduled=True,
                process_event_mode=True,
            )
            if tick_result.queued_requests_completed:
                status["reason"] = RefreshReason.OPERATOR_REQUESTED.value
            elif tick_result.scheduled_runs:
                status["reason"] = RefreshReason.SCHEDULED.value
            elif tick_result.event_mode_runs:
                status["reason"] = RefreshReason.EVENT_MODE.value
            elif tick_result.signal_monitor_runs:
                status["reason"] = "signal_monitor_only"
            else:
                status["reason"] = "not_due"
        status["queued_requests_completed"] = tick_result.queued_requests_completed
        status["signal_monitor_runs"] = tick_result.signal_monitor_runs
        status["scheduled_runs"] = tick_result.scheduled_runs
        status["event_mode_runs"] = tick_result.event_mode_runs
        status["ran_refresh"] = bool(
            tick_result.queued_requests_completed
            or tick_result.scheduled_runs
            or tick_result.event_mode_runs
        )
    except Exception as exc:
        status["error"] = str(exc)

    return status


def load_pending_notifications(db: Database, operator_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT notification_id, service_date, service_window, notification_type, publish_reason, payload_json, created_at
        FROM notification_events
        WHERE operator_id = ? AND status = 'pending'
        ORDER BY created_at ASC
        LIMIT 5
        """,
        [operator_id],
    )
    return [
        {
            "notification_id": row[0],
            "service_date": row[1],
            "service_window": row[2],
            "notification_type": row[3],
            "publish_reason": row[4],
            "payload": json.loads(row[5]) if row[5] else {},
            "created_at": row[6],
        }
        for row in rows
    ]


def mark_notifications_delivered(db: Database, notification_ids: list[int]) -> None:
    if not notification_ids:
        return
    placeholders = ",".join("?" for _ in notification_ids)
    db.execute(
        f"""
        UPDATE notification_events
        SET status = 'delivered', delivered_at = ?
        WHERE notification_id IN ({placeholders})
        """,
        [datetime.now(UTC), *notification_ids],
    )


def notifications_to_chat_message(
    notifications: list[dict[str, Any]],
    operator_attention_summary: dict[str, Any] | None,
) -> str | None:
    if not notifications:
        return None

    summary = operator_attention_summary or {}
    parts: list[str] = []
    seen: set[str] = set()

    def add_part(value: Any) -> None:
        if not isinstance(value, dict):
            return
        text = communication_text_from_state(value, include_question=False)
        if not text or text in seen:
            return
        seen.add(text)
        parts.append(text)

    for key, value in ordered_attention_sections(summary):
        if key == "best_next_question":
            continue
        add_part(value)

    if not parts:
        notification_types = sorted({str(item.get("notification_type") or "update") for item in notifications})
        if len(notification_types) == 1:
            return translate_operator_text(f"New operator update: {notification_types[0].replace('_', ' ')}.")
        return translate_operator_text("New operator updates are available.")
    moment_label = str(summary.get("moment_label") or "").strip()
    if len(parts) == 1:
        message = f"{moment_label}: {parts[0]}" if moment_label else parts[0]
        return translate_operator_text(message)
    if moment_label:
        return translate_operator_text(f"{moment_label}: " + " ".join(parts[:3]))
    return translate_operator_text(" ".join(parts[:3]))


def load_snapshot_deltas(db: Database, operator_id: str) -> dict[str, int]:
    rows = db.fetchall(
        """
        WITH ranked AS (
            SELECT service_date, service_window, forecast_expected,
                   ROW_NUMBER() OVER (
                       PARTITION BY service_date, service_window
                       ORDER BY snapshot_at DESC
                   ) AS rn
            FROM forecast_publication_snapshots
            WHERE operator_id = ?
        )
        SELECT a.service_date, a.service_window,
               a.forecast_expected - b.forecast_expected AS delta
        FROM ranked a
        JOIN ranked b ON a.service_date = b.service_date
                     AND a.service_window = b.service_window
                     AND a.rn = 1 AND b.rn = 2
        WHERE a.forecast_expected != b.forecast_expected
        """,
        [operator_id],
    )
    return {f"{row[0]}|{row[1]}": row[2] for row in rows}


def enrich_cards_with_deltas(cards: list[dict[str, Any]], deltas: dict[str, int]) -> None:
    for card in cards:
        d = card.get("service_date", "")
        w = card.get("service_window", "dinner")
        key = f"{d}|{w}"
        delta = deltas.get(key)
        if delta:
            card["change_delta"] = delta
