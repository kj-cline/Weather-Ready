"""Event-driven hooks that fire the two retriever agents and persist their
typed digests.

Called by the refresh orchestrator (after a cycle publishes) and the actuals
workflow (after a record + cascades complete). Never on the chat critical path.
Failures are swallowed — a digest write is best-effort; the caller's primary
work must not be rolled back by a model hiccup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Iterable

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import (
    OperatorContextDigestRepository,
    OperatorRepository,
)
from stormready_v3.surfaces.operator_state_packet import (
    OperatorStatePacketService,
    resolve_operator_current_time,
)

if TYPE_CHECKING:
    from stormready_v3.agents.base import AgentDispatcher
    from stormready_v3.conversation.service import ConversationContext
    from stormready_v3.domain.models import OperatorProfile


RetrieverKind = str  # 'current_state' | 'temporal'


def run_retriever_hooks(
    *,
    db: Database,
    dispatcher: "AgentDispatcher | None",
    operator_id: str,
    reference_date: date | None = None,
    kinds: Iterable[RetrieverKind] = ("current_state", "temporal"),
) -> None:
    """Fire the requested retrievers and persist their digests.

    Best-effort: exceptions are caught and logged upstream only. The function
    returns on any failure to avoid cascading retrievers if the first one blows
    up on a bad payload.
    """

    if dispatcher is None:
        return
    try:
        profile = OperatorRepository(db).load_operator_profile(operator_id)
    except Exception:
        return
    if profile is None:
        return

    resolved_reference = reference_date or date.today()
    try:
        current_time = resolve_operator_current_time(
            timezone=profile.timezone,
            active_date=resolved_reference,
        )
        packet = OperatorStatePacketService(db).build_packet(
            operator_id=operator_id,
            reference_date=resolved_reference,
            current_time=current_time,
        )
    except Exception:
        return

    conversation = packet.conversation
    digest_repo = OperatorContextDigestRepository(db)

    wanted = set(kinds)
    if "current_state" in wanted:
        _fire_current_state(
            db=db,
            dispatcher=dispatcher,
            digest_repo=digest_repo,
            operator_id=operator_id,
            reference_date=resolved_reference,
            profile=profile,
            conversation=conversation,
        )
    if "temporal" in wanted:
        _fire_temporal(
            db=db,
            dispatcher=dispatcher,
            digest_repo=digest_repo,
            operator_id=operator_id,
            conversation=conversation,
        )


def _fire_current_state(
    *,
    db: Database,
    dispatcher: "AgentDispatcher",
    digest_repo: OperatorContextDigestRepository,
    operator_id: str,
    reference_date: date,
    profile: "OperatorProfile",
    conversation: "ConversationContext",
) -> None:
    try:
        payload = _build_current_state_payload(
            operator_id=operator_id,
            reference_date=reference_date,
            profile=profile,
            conversation=conversation,
        )
    except Exception:
        return

    ctx = AgentContext(
        role=AgentRole.CURRENT_STATE_RETRIEVER,
        operator_id=operator_id,
        run_id=str(uuid.uuid4()),
        triggered_at=datetime.now(UTC),
        payload=payload,
    )
    try:
        result = dispatcher.dispatch(ctx)
    except Exception:
        return
    if result.status is not AgentStatus.OK or not result.outputs:
        return

    digest_body = result.outputs[0]
    _persist_digest(
        digest_repo=digest_repo,
        operator_id=operator_id,
        kind="current_state",
        digest_body=digest_body,
        run_id=ctx.run_id,
    )


def _fire_temporal(
    *,
    db: Database,
    dispatcher: "AgentDispatcher",
    digest_repo: OperatorContextDigestRepository,
    operator_id: str,
    conversation: "ConversationContext",
) -> None:
    try:
        payload = _build_temporal_payload(db=db, operator_id=operator_id, conversation=conversation)
    except Exception:
        return

    ctx = AgentContext(
        role=AgentRole.TEMPORAL_MEMORY_RETRIEVER,
        operator_id=operator_id,
        run_id=str(uuid.uuid4()),
        triggered_at=datetime.now(UTC),
        payload=payload,
    )
    try:
        result = dispatcher.dispatch(ctx)
    except Exception:
        return
    if result.status is not AgentStatus.OK or not result.outputs:
        return

    digest_body = result.outputs[0]
    _persist_digest(
        digest_repo=digest_repo,
        operator_id=operator_id,
        kind="temporal",
        digest_body=digest_body,
        run_id=ctx.run_id,
    )


def _persist_digest(
    *,
    digest_repo: OperatorContextDigestRepository,
    operator_id: str,
    kind: str,
    digest_body: dict[str, Any],
    run_id: str,
) -> None:
    produced_at_raw = digest_body.get("produced_at")
    produced_at = _parse_datetime(produced_at_raw) or datetime.now(UTC)
    source_hash = str(digest_body.get("source_hash") or "")
    import json

    try:
        payload_json = json.dumps(digest_body, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    try:
        digest_repo.insert_digest(
            operator_id=operator_id,
            kind=kind,
            produced_at=produced_at,
            source_hash=source_hash,
            payload_json=payload_json,
            agent_run_id=run_id,
        )
    except Exception:
        return


def _build_current_state_payload(
    *,
    operator_id: str,
    reference_date: date,
    profile: "OperatorProfile",
    conversation: "ConversationContext",
) -> dict[str, Any]:
    from stormready_v3.domain.enums import OnboardingState

    identity = {
        "operator_id": operator_id,
        "venue_name": profile.restaurant_name,
        "city": profile.city,
        "timezone": profile.timezone,
    }

    forecasts = list(conversation.actionable_forecasts or [])
    phase = "operations" if forecasts else _phase_for_profile(profile)
    headline = _headline_forecast(forecasts, reference_date)
    near_horizon = _near_horizon_rows(forecasts)
    open_action = _open_action_items(conversation)
    active_signals = _active_signals_from_conversation(conversation)
    missing = [
        _coerce_date(item.get("service_date"))
        for item in (conversation.missing_actuals or [])
    ]
    missing_dates = [d for d in missing if d is not None]

    maturity_hint = _maturity_hint(profile, conversation)
    has_demoted = any(
        str(src.get("status", "")).lower() == "demoted"
        for src in (conversation.watched_external_sources or [])
    )

    return {
        "reference_date": reference_date,
        "phase": phase,
        "identity": identity,
        "published_forecast": headline,
        "near_horizon_rows": near_horizon,
        "open_action_items": open_action,
        "active_signals": active_signals,
        "missing_actuals_dates": missing_dates,
        "operator_maturity_hint": maturity_hint,
        "has_demoted_sources": has_demoted,
    }


def _build_temporal_payload(
    *,
    db: Database,
    operator_id: str,
    conversation: "ConversationContext",
) -> dict[str, Any]:
    recent_misses_raw = [
        {
            "service_date": ev.get("date"),
            "err_pct": ev.get("error_pct"),
            "service_state": "normal",
            "short_label": _short_miss_label(ev),
        }
        for ev in (conversation.recent_evaluations or [])
        if ev.get("error_pct") is not None
    ]

    open_hypotheses = [
        {
            "hypothesis_key": h.get("hypothesis_key") or h.get("key") or "",
            "proposition": h.get("proposition") or h.get("summary") or "",
            "status": h.get("status", "open"),
            "confidence": h.get("confidence", "low"),
        }
        for h in (conversation.open_hypotheses or [])
    ]

    recent_patterns_raw: list[str] = []
    for decision in (conversation.recent_learning_decisions or [])[:6]:
        summary = decision.get("summary") or decision.get("decision_note")
        if isinstance(summary, str) and summary.strip():
            recent_patterns_raw.append(summary.strip())

    operator_facts_raw = [
        {
            "key": fact.get("fact_key") or fact.get("key") or "",
            "value": fact.get("fact_value") or fact.get("value") or "",
            "confidence": fact.get("confidence", "low"),
        }
        for fact in (conversation.operator_facts or [])
    ]

    learning_agenda_rows = [
        {
            "agenda_key": item.get("agenda_key") or item.get("key") or "",
            "prompt": item.get("prompt") or item.get("question_text") or "",
            "ready_to_ask": bool(item.get("ready_to_ask", True)),
        }
        for item in (conversation.learning_agenda or [])
    ]

    actual_count_total = _count_actuals(db, operator_id)
    last_conversation_at = _last_conversation_at(db, operator_id)
    demoted = [
        src.get("source_name") or src.get("name")
        for src in (conversation.watched_external_sources or [])
        if str(src.get("status", "")).lower() == "demoted"
    ]

    return {
        "recent_misses_raw": recent_misses_raw,
        "open_hypotheses": open_hypotheses,
        "recent_patterns_raw": recent_patterns_raw,
        "operator_facts_raw": operator_facts_raw,
        "learning_agenda_rows": learning_agenda_rows,
        "actual_count_total": actual_count_total,
        "last_conversation_at": last_conversation_at,
        "demoted_sources": [d for d in demoted if d],
        "has_pending_followup": bool(conversation.pending_corrections),
        "cascades_live": [],
    }


def _phase_for_profile(profile: "OperatorProfile") -> str:
    from stormready_v3.domain.enums import OnboardingState

    state = getattr(profile, "onboarding_state", None)
    if state == OnboardingState.INCOMPLETE:
        return "setup"
    if state == OnboardingState.SETTLED:
        return "operations"
    return "enrichment"


def _headline_forecast(
    forecasts: list[dict[str, Any]], reference_date: date
) -> dict[str, Any] | None:
    if not forecasts:
        return None
    chosen = forecasts[0]
    return {
        "service_date": str(chosen.get("service_date", "")),
        "expected": chosen.get("forecast_expected"),
        "low": chosen.get("forecast_low"),
        "high": chosen.get("forecast_high"),
        "confidence": chosen.get("confidence_tier"),
    }


def _near_horizon_rows(forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in forecasts[:5]:
        rows.append(
            {
                "date": str(f.get("service_date", "")),
                "expected": f.get("forecast_expected"),
                "band": f"{f.get('forecast_low', '?')}–{f.get('forecast_high', '?')}",
                "state": f.get("service_state") or "normal",
            }
        )
    return rows


def _open_action_items(conversation: "ConversationContext") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for miss in (conversation.missing_actuals or [])[:2]:
        items.append(
            {
                "kind": "log_actual",
                "prompt": f"Log covers for {miss.get('service_date')}",
                "urgency": "medium",
            }
        )
    plan_window = conversation.service_plan_window
    if isinstance(plan_window, dict) and plan_window.get("due_count"):
        items.append(
            {
                "kind": "service_plan",
                "prompt": f"{plan_window.get('due_count')} service plan(s) due",
                "urgency": "medium",
            }
        )
    for corr in (conversation.pending_corrections or [])[:1]:
        items.append(
            {
                "kind": "review_correction",
                "prompt": corr.get("summary") or "Review pending correction",
                "urgency": "high",
            }
        )
    return items


def _active_signals_from_conversation(
    conversation: "ConversationContext",
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for sig in (conversation.runtime_external_signals or [])[:5]:
        label = sig.get("short_label") or sig.get("rationale") or sig.get("source_name")
        if not label:
            continue
        signals.append(
            {
                "short_label": str(label),
                "direction": sig.get("direction"),
                "strength": sig.get("strength"),
            }
        )
    return signals


def _maturity_hint(
    profile: "OperatorProfile",
    conversation: "ConversationContext",
) -> str:
    observations = len(conversation.recent_observations or [])
    evaluations = len(conversation.recent_evaluations or [])
    if observations + evaluations < 5:
        return "early"
    return "established"


def _short_miss_label(evaluation: dict[str, Any]) -> str:
    err = evaluation.get("error_pct")
    date_label = evaluation.get("date")
    if err is None:
        return str(date_label or "")
    try:
        err_val = float(err)
    except (TypeError, ValueError):
        return str(date_label or "")
    direction = "under" if err_val < 0 else "over"
    return f"{date_label} {direction}performed"


def _count_actuals(db: Database, operator_id: str) -> int:
    try:
        row = db.fetchone(
            "SELECT COUNT(*) FROM operator_actuals WHERE operator_id = ?",
            [operator_id],
        )
    except Exception:
        return 0
    if row is None:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return 0


def _last_conversation_at(db: Database, operator_id: str) -> datetime | None:
    try:
        row = db.fetchone(
            """
            SELECT created_at FROM conversation_message
            WHERE operator_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [operator_id],
        )
    except Exception:
        return None
    if row is None:
        return None
    value = row[0]
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = ["run_retriever_hooks"]
