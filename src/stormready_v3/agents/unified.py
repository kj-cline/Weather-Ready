"""Unified StormReady agent — handles onboarding and operations in one conversation.

The agent maintains phase awareness (setup vs operations) and generates suggested
messages so the operator can tap or type. It uses tool-use to map operator language
to system contracts, with the model owning the operator-facing conversation path.

Conversation history is persisted in DuckDB so it survives page refreshes and
feeds future context. Operator behavior preferences are loaded into the prompt
and updated from conversation signals.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentDispatcher, AgentRole
from stormready_v3.ai.contracts import AgentModelProvider
from stormready_v3.agents.tools import ToolExecutor, ToolResult
from stormready_v3.conversation.memory import ConversationMemoryService
from stormready_v3.conversation.promotion import LearningPromotionService
from stormready_v3.operator_text import communication_payload, render_communication_payload
from stormready_v3.setup.readiness import summarize_setup_readiness
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorContextDigestRepository, OperatorRepository
from stormready_v3.workflows.setup_context_digests import ensure_setup_context_digests


# ---------------------------------------------------------------------------
# Agent response model
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SuggestedMessage:
    """A quick-reply button the operator can tap."""
    label: str
    value: str
    category: str = "action"  # action, yes_no, skip


@dataclass(slots=True)
class AgentResponse:
    """What the agent returns to the UI."""
    text: str
    tool_results: list[ToolResult] = field(default_factory=list)
    suggested_messages: list[SuggestedMessage] = field(default_factory=list)
    operator_id: str | None = None
    phase: str = "setup"  # setup, enrichment, operations


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------

def detect_phase(db: Database, operator_id: str | None) -> str:
    """Determine conversation phase from operator state."""
    if operator_id is None:
        return "setup"
    repo = OperatorRepository(db)
    profile = repo.load_operator_profile(operator_id)
    if profile is None:
        return "setup"
    baseline_row = db.fetchone(
        "SELECT COUNT(*) FROM operator_weekly_baselines WHERE operator_id = ? AND baseline_total_covers > 0",
        [operator_id],
    )
    has_baselines = baseline_row is not None and baseline_row[0] > 0
    summary = summarize_setup_readiness(profile, primary_window_has_baseline=has_baselines)
    if not summary.forecast_ready:
        return "setup"
    forecast_row = db.fetchone(
        "SELECT COUNT(*) FROM published_forecast_state WHERE operator_id = ?",
        [operator_id],
    )
    has_forecasts = forecast_row is not None and forecast_row[0] > 0
    return "operations" if has_forecasts else "enrichment"


def _resolve_agent_reference_date(db: Database, operator_id: str | None, reference_date: date | None) -> date:
    """Use the latest published strip start when no explicit reference date is provided.

    This keeps the agent grounded when working against historical replay databases,
    where `date.today()` would otherwise point outside the available forecast window.
    """
    if reference_date is not None or operator_id is None:
        return reference_date or date.today()
    latest_strip_row = db.fetchone(
        """
        SELECT MIN(service_date)
        FROM published_forecast_state
        WHERE operator_id = ?
          AND last_published_at = (
            SELECT MAX(last_published_at)
            FROM published_forecast_state
            WHERE operator_id = ?
          )
        """,
        [operator_id, operator_id],
    )
    if latest_strip_row is not None and latest_strip_row[0] is not None:
        return latest_strip_row[0]
    actuals_row = db.fetchone(
        "SELECT MAX(service_date) FROM operator_actuals WHERE operator_id = ?",
        [operator_id],
    )
    if actuals_row is not None and actuals_row[0] is not None:
        return actuals_row[0]
    return date.today()


# ---------------------------------------------------------------------------
# Conversation persistence
# ---------------------------------------------------------------------------

def _save_message(db: Database, operator_id: str, role: str, content: str, phase: str,
                  tool_calls: list[dict] | None = None, tool_results: list[ToolResult] | None = None) -> None:
    """Persist a conversation message to DuckDB."""
    tool_calls_json = json.dumps(tool_calls, default=str) if tool_calls else None
    results_json = None
    if tool_results:
        results_json = json.dumps(
            [{"tool": r.tool_name, "ok": r.success, "msg": r.message} for r in tool_results],
            default=str,
        )
    try:
        db.execute(
            """INSERT INTO conversation_messages (operator_id, role, content, tool_calls_json, tool_results_json, phase)
            VALUES (?, ?, ?, ?, ?, ?)""",
            [operator_id, role, content, tool_calls_json, results_json, phase],
        )
    except Exception:
        pass  # Table may not exist yet — don't break the conversation


def _load_message_page(
    db: Database,
    operator_id: str,
    *,
    limit: int = 30,
    before_id: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Load a chronologically ordered page of conversation messages."""
    try:
        if before_id is None:
            rows = db.fetchall(
                """
                SELECT message_id, role, content, created_at
                FROM conversation_messages
                WHERE operator_id = ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                [operator_id, limit + 1],
            )
        else:
            rows = db.fetchall(
                """
                SELECT message_id, role, content, created_at
                FROM conversation_messages
                WHERE operator_id = ?
                  AND message_id < ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                [operator_id, before_id, limit + 1],
            )
    except Exception:
        return [], False
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    messages = [
        {
            "message_id": row[0],
            "role": row[1],
            "content": row[2],
            "created_at": row[3],
        }
        for row in reversed(page_rows)
    ]
    return messages, has_more


def _load_recent_history(db: Database, operator_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Load recent conversation messages from DuckDB."""
    messages, _ = _load_message_page(db, operator_id, limit=limit)
    return messages


def _format_recent_turns(db: Database, operator_id: str, limit: int = 6) -> list[dict[str, str]]:
    """Return recent turns as the {role, content} shape Agent C expects."""
    messages, _ = _load_message_page(db, operator_id, limit=limit)
    return [
        {"role": str(msg.get("role") or ""), "content": str(msg.get("content") or "")}
        for msg in messages
    ]


def _age_seconds(produced_at: Any, now: datetime) -> float:
    if produced_at is None:
        return 0.0
    if isinstance(produced_at, datetime):
        ts = produced_at
    elif isinstance(produced_at, str):
        try:
            ts = datetime.fromisoformat(produced_at)
        except ValueError:
            return 0.0
    else:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds())


# ---------------------------------------------------------------------------
# Operator behavior preferences
# ---------------------------------------------------------------------------

def _detect_and_update_behavior(db: Database, operator_id: str, message: str, ai_text: str) -> None:
    """Detect preference signals from the conversation and update behavior state."""
    lowered = message.lower()
    updates: dict[str, Any] = {}

    # Brevity signals
    if any(phrase in lowered for phrase in ("too long", "shorter", "just the number", "tldr", "tl;dr", "keep it short")):
        updates["brevity_preference"] = "brief"
    elif any(phrase in lowered for phrase in ("tell me more", "explain more", "why exactly", "break it down", "more detail")):
        updates["brevity_preference"] = "detailed"

    # Staffing risk signals
    if any(phrase in lowered for phrase in ("i'd rather overstaff", "better safe", "staff high", "worst case")):
        updates["staffing_risk_bias"] = 0.8
    elif any(phrase in lowered for phrase in ("don't overstaff", "run lean", "keep it tight", "minimum")):
        updates["staffing_risk_bias"] = 0.3

    # Explanation style signals
    if any(phrase in lowered for phrase in ("just the numbers", "give me the numbers", "how many")):
        updates["preferred_explanation_style"] = "numbers_first"

    # Always increment conversation count
    if not updates:
        try:
            db.execute(
                """UPDATE operator_behavior_state
                SET conversation_count = COALESCE(conversation_count, 0) + 1,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE operator_id = ?""",
                [operator_id],
            )
        except Exception:
            pass
        return

    set_clauses = ["conversation_count = COALESCE(conversation_count, 0) + 1", "last_updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []
    for col, val in updates.items():
        set_clauses.append(f"{col} = ?")
        params.append(val)
    params.append(operator_id)
    try:
        db.execute(f"UPDATE operator_behavior_state SET {', '.join(set_clauses)} WHERE operator_id = ?", params)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Conversation policy helpers
# ---------------------------------------------------------------------------

def _parse_yes_no_reply(message: str) -> bool | None:
    lowered = re.sub(r"[^a-z0-9']+", " ", message.strip().lower()).strip()
    yes_tokens = {
        "yes", "y", "yeah", "yep", "correct", "it does", "definitely", "it matters", "yes it does",
    }
    no_tokens = {
        "no", "n", "nope", "not really", "it doesn't", "it does not", "doesn't matter", "no it doesn't",
    }
    if lowered in yes_tokens or lowered.startswith("yes "):
        return True
    if lowered in no_tokens or lowered.startswith("no "):
        return False
    return None


def _parse_unsure_reply(message: str) -> bool:
    lowered = re.sub(r"[^a-z0-9']+", " ", message.strip().lower()).strip()
    unsure_tokens = {
        "not sure",
        "not certain",
        "unsure",
        "i don't know",
        "i dont know",
        "don't know",
        "dont know",
        "maybe",
        "unclear",
    }
    return lowered in unsure_tokens


def _looks_like_substantive_note_reply(message: str) -> bool:
    stripped = message.strip()
    if len(stripped) < 10:
        return False
    if stripped.endswith("?"):
        return False
    lowered = stripped.lower()
    if lowered.startswith(("what ", "why ", "how ", "show ", "tell ", "can you ", "could you ")):
        return False
    return True


def _maybe_resolve_learning_agenda_reply(
    db: Database,
    executor: ToolExecutor,
    *,
    operator_id: str | None,
    message: str,
    learning_agenda_key: str | None = None,
) -> AgentResponse | None:
    if operator_id is None:
        return None
    memory = ConversationMemoryService(db)
    promotion = LearningPromotionService(db, executor)
    explicit_agenda_key = str(learning_agenda_key or "").strip()
    item = (
        memory.learning_agenda_item(operator_id, explicit_agenda_key)
        if explicit_agenda_key
        else memory.most_recent_asked_question(operator_id)
    )
    if item is None:
        return None

    agenda_key = str(item.get("agenda_key") or "")
    question_kind = str(item.get("question_kind") or "")
    service_date = item.get("service_date")
    tool_results: list[ToolResult] = []

    if _parse_unsure_reply(message):
        if item.get("hypothesis_key"):
            memory.resolve_hypothesis(
                operator_id=operator_id,
                hypothesis_key=str(item["hypothesis_key"]),
                status="resolved",
                resolution_note="operator was not sure",
            )
        memory.resolve_agenda_item(
            operator_id=operator_id,
            agenda_key=agenda_key,
            resolution_note="operator was not sure",
        )
        response_text = _learning_resolution_text(
            recorded_prefix="No problem. I will leave that out for now.",
            semantic_payload={
                "what_is_still_uncertain": "I will not treat it as a confirmed recurring pattern.",
            },
        )
        return AgentResponse(
            text=response_text,
            tool_results=tool_results,
            operator_id=operator_id,
            phase="operations",
            suggested_messages=[
                SuggestedMessage(label="What changed?", value="What changed since the last refresh?", category="action"),
                SuggestedMessage(label="Tonight's forecast", value="What does tonight look like?", category="action"),
            ],
        )

    if question_kind == "yes_no":
        answer = _parse_yes_no_reply(message)
        if answer is None:
            return None
        promotion_result = promotion.resolve_yes_no(
            operator_id=operator_id,
            agenda_item=item,
            answer=answer,
        )
        tool_results.extend(promotion_result.tool_results)
        for fact in promotion_result.fact_updates:
            memory.upsert_fact(
                operator_id=operator_id,
                fact_key=str(fact["fact_key"]),
                fact_value=fact["fact_value"],
                confidence=str(fact.get("confidence") or "high"),
                provenance=str(fact.get("provenance") or "operator_confirmed"),
                source_ref=str(fact.get("source_ref") or f"learning_agenda::{agenda_key}"),
            )
        if item.get("hypothesis_key"):
            memory.resolve_hypothesis(
                operator_id=operator_id,
                hypothesis_key=str(item["hypothesis_key"]),
                status="resolved",
                resolution_note=f"operator answered {'yes' if answer else 'no'}",
            )
        memory.resolve_agenda_item(
            operator_id=operator_id,
            agenda_key=agenda_key,
            resolution_note=f"operator answered {'yes' if answer else 'no'}",
        )
        response_text = _learning_resolution_text(
            recorded_prefix="I recorded your answer.",
            semantic_payload=promotion_result.communication_payload,
        )
        return AgentResponse(
            text=response_text,
            tool_results=tool_results,
            operator_id=operator_id,
            phase="operations",
            suggested_messages=[
                SuggestedMessage(label="What changed?", value="What changed since the last refresh?", category="action"),
                SuggestedMessage(label="Tonight's forecast", value="What does tonight look like?", category="action"),
            ],
        )

    if question_kind == "free_text" and (explicit_agenda_key or _looks_like_substantive_note_reply(message)):
        capture_args: dict[str, Any] = {"note": message.strip()}
        if service_date is not None:
            capture_args["service_date"] = service_date.isoformat()
        capture_result = executor.execute(operator_id, "capture_note", capture_args)
        tool_results.append(capture_result)
        if not capture_result.success:
            return None
        promotion_result = promotion.resolve_free_text(
            operator_id=operator_id,
            agenda_item=item,
            message=message,
        )
        tool_results.extend(promotion_result.tool_results)
        for fact in promotion_result.fact_updates:
            memory.upsert_fact(
                operator_id=operator_id,
                fact_key=str(fact["fact_key"]),
                fact_value=fact["fact_value"],
                confidence=str(fact.get("confidence") or "medium"),
                provenance=str(fact.get("provenance") or "operator_confirmed"),
                source_ref=str(fact.get("source_ref") or f"learning_agenda::{agenda_key}"),
            )
        memory.upsert_fact(
            operator_id=operator_id,
            fact_key=f"agenda_note::{agenda_key}",
            fact_value={
                "note": message.strip(),
                "service_date": service_date.isoformat() if service_date is not None else None,
            },
            confidence="medium",
            provenance="operator_note",
            source_ref=f"learning_agenda::{agenda_key}",
            valid_from_date=service_date if service_date is not None else None,
        )
        if item.get("hypothesis_key"):
            memory.resolve_hypothesis(
                operator_id=operator_id,
                hypothesis_key=str(item["hypothesis_key"]),
                status="resolved",
                resolution_note="operator supplied a qualitative explanation",
            )
        memory.resolve_agenda_item(
            operator_id=operator_id,
            agenda_key=agenda_key,
            resolution_note="operator supplied a qualitative explanation",
        )
        date_fragment = f" for {service_date}" if service_date is not None else ""
        response_text = _learning_resolution_text(
            recorded_prefix=f"I recorded that context{date_fragment}.",
            semantic_payload=promotion_result.communication_payload,
        )
        return AgentResponse(
            text=response_text,
            tool_results=tool_results,
            operator_id=operator_id,
            phase="operations",
            suggested_messages=[
                SuggestedMessage(label="What changed?", value="What changed since the last refresh?", category="action"),
                SuggestedMessage(label="Show forecast", value="What does tonight look like?", category="action"),
            ],
        )
    return None


def _default_empty_state_text(*, phase: str) -> str:
    if phase == "operations":
        return "I do not have a live dinner forecast or active operator task to speak from yet."
    if phase == "enrichment":
        return "Forecasts are ready. You can add optional context or start using the forecast view."
    return "I have the setup context. Tell me the next restaurant detail you want to add."


def _fallback_suggested_messages(phase: str) -> list[SuggestedMessage]:
    if phase == "setup":
        return [
            SuggestedMessage(label="Add cover counts", value="I want to add my typical dinner cover counts.", category="action"),
            SuggestedMessage(label="Check readiness", value="Am I ready to forecast?", category="action"),
            SuggestedMessage(label="Add patio info", value="I want to add patio details.", category="action"),
        ]
    if phase == "enrichment":
        return [
            SuggestedMessage(label="Start forecasts", value="Start using my forecasts.", category="action"),
            SuggestedMessage(label="Upload history", value="I want to upload historical cover data.", category="action"),
            SuggestedMessage(label="Add nearby signals", value="I want to add nearby transit or venue context.", category="action"),
        ]
    return [
        SuggestedMessage(label="Show week ahead", value="Show me the week ahead", category="action"),
        SuggestedMessage(label="Any open questions?", value="Any open questions?", category="action"),
        SuggestedMessage(label="Log a note", value="I have a note to log.", category="action"),
    ]


def _learning_resolution_text(
    *,
    recorded_prefix: str,
    semantic_payload: dict[str, Any] | None = None,
) -> str:
    payload = dict(semantic_payload or {})
    payload.setdefault("category", "learning_resolution")
    payload["what_is_true_now"] = recorded_prefix
    return render_communication_payload(
        communication_payload(**payload),
        include_question=False,
    )


# Unified agent service
# ---------------------------------------------------------------------------

class UnifiedAgentService:
    """Single agent for onboarding + operations conversations."""

    def __init__(
        self,
        db: Database,
        provider: AgentModelProvider | None = None,
        *,
        agent_dispatcher: AgentDispatcher | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.agent_dispatcher = agent_dispatcher
        self.executor = ToolExecutor(db, provider=provider, agent_dispatcher=agent_dispatcher)

    def respond(
        self,
        *,
        operator_id: str | None,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
        uploaded_file_data: dict[str, Any] | None = None,
        reference_date: date | None = None,
        learning_agenda_key: str | None = None,
    ) -> AgentResponse:
        """Process an operator message and return a response with suggested next steps."""
        effective_reference_date = _resolve_agent_reference_date(self.db, operator_id, reference_date)
        self.executor.set_reference_date(effective_reference_date)
        phase = detect_phase(self.db, operator_id)

        return self._respond_via_dispatcher(
            operator_id=operator_id,
            message=message,
            phase=phase,
            reference_date=effective_reference_date,
            learning_agenda_key=learning_agenda_key,
            uploaded_file_data=uploaded_file_data,
        )

    def _respond_via_dispatcher(
        self,
        *,
        operator_id: str | None,
        message: str,
        phase: str,
        reference_date: date,
        learning_agenda_key: str | None,
        uploaded_file_data: dict[str, Any] | None = None,
    ) -> AgentResponse:
        if phase == "operations":
            resolved_agenda_response = _maybe_resolve_learning_agenda_reply(
                self.db,
                self.executor,
                operator_id=operator_id,
                message=message,
                learning_agenda_key=learning_agenda_key,
            )
            if resolved_agenda_response is not None:
                self._persist_exchange(operator_id, message, resolved_agenda_response)
                if operator_id:
                    _detect_and_update_behavior(self.db, operator_id, message, resolved_agenda_response.text)
                return resolved_agenda_response

        if (
            self.agent_dispatcher is None
            or self.provider is None
            or not self.provider.is_available()
            or operator_id is None
        ):
            response = self._ai_unavailable_response(operator_id=operator_id, phase=phase)
            self._persist_exchange(operator_id, message, response)
            return response

        digest_repo = OperatorContextDigestRepository(self.db)
        if phase in {"setup", "enrichment"}:
            ensure_setup_context_digests(
                self.db,
                operator_id=operator_id,
                reference_date=reference_date,
            )
        current_row = digest_repo.fetch_latest(operator_id=operator_id, kind="current_state")
        temporal_row = digest_repo.fetch_latest(operator_id=operator_id, kind="temporal")
        if current_row is None or temporal_row is None:
            response = self._ai_unavailable_response(operator_id=operator_id, phase=phase)
            self._persist_exchange(operator_id, message, response)
            return response

        now = datetime.now(UTC)
        staleness = {
            "current_state_age_seconds": _age_seconds(current_row.get("produced_at"), now),
            "temporal_age_seconds": _age_seconds(temporal_row.get("produced_at"), now),
            "source_hash_match": True,
        }
        recent_turns = _format_recent_turns(self.db, operator_id)

        output, tool_results, final_operator_id = self._dispatch_orchestrator_turn(
            operator_id=operator_id,
            operator_message=message,
            current_digest=current_row.get("payload") or {},
            temporal_digest=temporal_row.get("payload") or {},
            staleness=staleness,
            recent_turns=recent_turns,
            uploaded_file_data=uploaded_file_data,
        )
        response_phase = detect_phase(self.db, final_operator_id) if final_operator_id else phase

        text = str(output.get("text") or "").strip()
        if not text:
            text = _default_empty_state_text(phase=response_phase)

        note_captured = any(tr.tool_name == "capture_note" and tr.success for tr in tool_results)
        if note_captured and not any(word in text.lower() for word in ("recorded", "saved", "logged")):
            text = "I recorded that note. It will be available as context on future forecast refreshes."

        suggestions: list[SuggestedMessage] = []
        for raw in output.get("suggested_messages") or []:
            if isinstance(raw, str) and raw.strip():
                suggestions.append(
                    SuggestedMessage(label=raw.strip(), value=raw.strip(), category="action")
                )
            elif isinstance(raw, dict) and raw.get("label"):
                suggestions.append(
                    SuggestedMessage(
                        label=str(raw["label"]),
                        value=str(raw.get("value", raw["label"])),
                        category=str(raw.get("category", "action")),
                    )
                )
        if not suggestions:
            suggestions = _fallback_suggested_messages(response_phase)

        response = AgentResponse(
            text=text,
            tool_results=tool_results,
            suggested_messages=suggestions,
            operator_id=final_operator_id,
            phase=response_phase,
        )

        self._persist_exchange(final_operator_id, message, response)
        if final_operator_id:
            _detect_and_update_behavior(self.db, final_operator_id, message, response.text)

        if note_captured:
            from stormready_v3.workflows.retriever_hooks import run_retriever_hooks

            try:
                run_retriever_hooks(
                    db=self.db,
                    dispatcher=self.agent_dispatcher,
                    operator_id=operator_id,
                    reference_date=reference_date,
                    kinds=("temporal",),
                )
            except Exception:
                pass
        if response_phase in {"setup", "enrichment"} and tool_results:
            ensure_setup_context_digests(
                self.db,
                operator_id=final_operator_id,
                reference_date=reference_date,
                force=True,
            )

        return response

    def _dispatch_orchestrator_turn(
        self,
        *,
        operator_id: str,
        operator_message: str,
        current_digest: dict[str, Any],
        temporal_digest: dict[str, Any],
        staleness: dict[str, Any],
        recent_turns: list[dict[str, str]],
        uploaded_file_data: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[ToolResult], str]:
        assert self.agent_dispatcher is not None  # guarded by caller
        base_payload: dict[str, Any] = {
            "operator_message": operator_message,
            "current_state_digest": current_digest,
            "temporal_digest": temporal_digest,
            "digest_staleness": staleness,
            "recent_turns": recent_turns,
            "tool_results": [],
        }
        if uploaded_file_data:
            base_payload["uploaded_file"] = {
                "headers": uploaded_file_data.get("headers", []),
                "sample_rows": list(uploaded_file_data.get("sample_rows", []))[:5],
            }
        ctx = AgentContext(
            role=AgentRole.CONVERSATION_ORCHESTRATOR,
            operator_id=operator_id,
            run_id=str(uuid.uuid4()),
            triggered_at=datetime.now(UTC),
            payload=base_payload,
        )
        result = self.agent_dispatcher.dispatch(ctx)
        output: dict[str, Any] = {}
        if result.outputs:
            first = result.outputs[0]
            if isinstance(first, dict):
                output = dict(first)

        tool_calls = output.get("tool_calls") or []
        if not isinstance(tool_calls, list) or not tool_calls:
            return output, [], operator_id

        tool_results: list[ToolResult] = []
        serialized_results: list[dict[str, Any]] = []
        current_operator_id = operator_id
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name") or call.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            args = call.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            tr = self.executor.execute(current_operator_id, name, args)
            tool_results.append(tr)
            if name == "update_profile" and tr.success and tr.data.get("operator_id"):
                current_operator_id = str(tr.data["operator_id"])
            serialized_results.append(
                {
                    "tool": tr.tool_name,
                    "success": tr.success,
                    "message": tr.message,
                    "data": tr.data if isinstance(tr.data, dict) else {},
                }
            )
            if name == "update_profile" and tr.success:
                readiness = self.executor.execute(current_operator_id, "check_readiness", {})
                tool_results.append(readiness)
                serialized_results.append(
                    {
                        "tool": readiness.tool_name,
                        "success": readiness.success,
                        "message": readiness.message,
                        "data": readiness.data if isinstance(readiness.data, dict) else {},
                    }
                )

        if not tool_results:
            return output, [], current_operator_id

        followup_payload = dict(base_payload)
        followup_payload["tool_results"] = serialized_results
        ctx2 = AgentContext(
            role=AgentRole.CONVERSATION_ORCHESTRATOR,
            operator_id=current_operator_id,
            run_id=str(uuid.uuid4()),
            triggered_at=datetime.now(UTC),
            payload=followup_payload,
        )
        result2 = self.agent_dispatcher.dispatch(ctx2)
        if result2.outputs:
            first2 = result2.outputs[0]
            if isinstance(first2, dict):
                return dict(first2), tool_results, current_operator_id
        return output, tool_results, current_operator_id

    @staticmethod
    def _ai_unavailable_response(*, operator_id: str | None, phase: str) -> AgentResponse:
        return AgentResponse(
            text=(
                "The AI copilot is unavailable right now, so chat is temporarily paused. "
                "The forecast view and the plan and actuals panels are still available."
            ),
            operator_id=operator_id,
            phase=phase,
            suggested_messages=[],
        )

    def _persist_exchange(self, operator_id: str | None, message: str, response: AgentResponse) -> None:
        """Save both the operator message and assistant response to DuckDB."""
        if not operator_id:
            return
        _save_message(self.db, operator_id, "operator", message, response.phase)
        tool_calls = [{"tool": r.tool_name, "args": {}} for r in response.tool_results] if response.tool_results else None
        _save_message(self.db, operator_id, "assistant", response.text, response.phase,
                      tool_calls=tool_calls, tool_results=response.tool_results)
