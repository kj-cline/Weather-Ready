from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

DEFAULT_RETRIEVAL_PRIORITY = [
    "operator_attention_summary",
    "forecasts",
    "recent_changes",
    "recent_evaluations",
    "engine_digests",
    "runtime_external_signals",
    "operator_facts",
    "recent_observations",
    "open_hypotheses",
    "learning_agenda",
    "recent_learning_decisions",
    "prediction_equation",
]


def build_conversation_policy(
    *,
    message: str,
    phase: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    del message
    attention_summary = context.get("operator_attention_summary") or {}
    pending_action = attention_summary.get("pending_operator_action") or {}
    blocking_action_types = {"open_actuals_form", "review_service_plan", "review_correction"}
    workflow_priority_action = (
        pending_action
        if str(pending_action.get("action_type") or "") in blocking_action_types
        else None
    )

    suggested_learning_question = None
    if phase == "operations" and workflow_priority_action is None:
        open_items = [
            item
            for item in list(context.get("learning_agenda", []) or [])
            if str(item.get("status") or "open") == "open"
            and not _is_in_cooldown(item.get("cooldown_until"))
        ]
        learning_items = sorted(
            [
                item
                for item in open_items
                if str(item.get("question_kind") or "") in {"yes_no", "free_text"}
            ],
            key=_agenda_sort_key,
        )
        suggested_learning_question = learning_items[0] if learning_items else None

    return {
        "mode": "model_first",
        "evidence_priority": list(DEFAULT_RETRIEVAL_PRIORITY),
        "workflow_priority_action": workflow_priority_action,
        "suggested_learning_question": suggested_learning_question,
        "delivery_rules": {
            "operator_language_only": True,
            "use_concrete_dates": True,
            "use_numbers_when_available": True,
            "max_questions": 1,
            "mention_due_workflow_action": workflow_priority_action is not None,
        },
    }


def _is_in_cooldown(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        cooldown_until = value
    else:
        try:
            cooldown_until = datetime.fromisoformat(str(value))
        except ValueError:
            return False
    if cooldown_until.tzinfo is not None:
        cooldown_until = cooldown_until.astimezone(UTC).replace(tzinfo=None)
    return cooldown_until > datetime.now(UTC).replace(tzinfo=None)


def _agenda_sort_key(item: dict[str, Any]) -> tuple[int, date, datetime]:
    priority = item.get("priority")
    if isinstance(priority, (int, float)):
        priority_rank = -int(priority)
    else:
        priority_rank = 0
    service_date = _coerce_service_date(item.get("service_date")) or date.max
    created_at = _coerce_datetime(item.get("asked_at") or item.get("created_at"))
    return (priority_rank, service_date, created_at)


def _coerce_service_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if value is None:
        return datetime.min
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed
