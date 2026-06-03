"""Conversation Orchestrator — operator-facing chat surface.

See ``policies/conversation_orchestrator.md`` for the full role contract.

This is the *least* smart agent in the system by design. The retrievers
(current_state_retriever, temporal_memory_retriever) do the thinking and hand
C two typed digests. C's job is to:

1. Build the prompt = persona + both digests + recent turns + operator message
2. Call the model; parse the structured ``{text, tool_calls, suggested_messages}``
3. Apply line-level grounding — drop sentences whose numeric claims are not
   traceable to the digests or the tool results
4. Apply a banned-vocabulary filter on ``text`` and ``suggested_messages``
5. Return the final envelope

C never invents numbers. If grounding drops the entire reply, C falls back to
an honest deterministic message naming what it does not know.

Tool execution is the caller's job. C emits ``tool_calls`` (parsed and
validated); the wiring layer dispatches them, collects results, and may re-run
the turn with results attached.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Iterable

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)


_BANNED_VOCABULARY = (
    "brooklyn_delta",
    "regime",
    "cascade",
    "rollup",
    "scorer",
    "multiplier",
    "signature_state",
    "fact_memory",
    "engine_digest",
    "weight_",
    "seasonality_",
    "adaptation_",
    "learning_state_",
    "service_state_risk_state",
    "migration",
    "node_",
)
_MAX_SUGGESTED = 3
_MAX_SUGGESTED_LEN = 60
_MAX_TEXT_CHARS = 900
_NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


KNOWN_TOOLS = {
    "update_profile": {
        "description": "Create or update the restaurant profile during setup.",
        "arguments": {
            "restaurant_name": "str | null",
            "canonical_address": "str | null",
            "city": "str | null",
            "timezone": "str | null",
            "neighborhood_type": "str | null",
            "demand_mix": "str | null",
            "patio_enabled": "bool | null",
            "patio_seat_capacity": "int | null",
            "patio_season_mode": "str | null",
            "weekly_baselines": "dict | null",
        },
    },
    "set_location_relevance": {
        "description": "Update nearby transit, venue, hotel, or travel relevance.",
        "arguments": {
            "transit_relevance": "bool | null",
            "venue_relevance": "bool | null",
            "hotel_travel_relevance": "bool | null",
        },
    },
    "check_readiness": {
        "description": "Check setup readiness after profile updates or readiness questions.",
        "arguments": {},
    },
    "interpret_upload": {
        "description": "Interpret uploaded historical cover data headers and sample rows.",
        "arguments": {
            "headers": "list[str]",
            "sample_rows": "list[dict]",
        },
    },
    "query_forecast_detail": {
        "description": "Single-day forecast driver breakdown.",
        "arguments": {"service_date": "str (YYYY-MM-DD)"},
    },
    "query_hypothesis_backlog": {
        "description": "List open/confirmed/rejected hypotheses.",
        "arguments": {"status": "str | null (open|confirmed|rejected)"},
    },
    "query_learning_state": {
        "description": "Current learning state snapshot for a cascade.",
        "arguments": {"cascade": "str | null"},
    },
    "query_actuals_history": {
        "description": "Recent submitted actuals with forecast deltas.",
        "arguments": {"limit": "int", "state_filter": "str | null"},
    },
    "query_recent_signals": {
        "description": "Recent signal log rows.",
        "arguments": {"limit": "int", "dependency_group": "str | null"},
    },
    "capture_note": {
        "description": "Record an operator note about past or upcoming service.",
        "arguments": {
            "note": "str",
            "service_date": "str | null (YYYY-MM-DD)",
            "service_state": "str | null",
        },
    },
    "request_refresh": {
        "description": "Refresh forecasts when the operator asks for an update.",
        "arguments": {"reason": "str | null"},
    },
}


class ConversationOrchestratorAgent(BaseAgent):
    role = AgentRole.CONVERSATION_ORCHESTRATOR

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = dict(ctx.payload)
        operator_message = str(payload.get("operator_message") or "").strip()
        current_digest = payload.get("current_state_digest") or {}
        temporal_digest = payload.get("temporal_digest") or {}
        tool_results = payload.get("tool_results") or []
        staleness = payload.get("digest_staleness") or {}

        try:
            response = self.provider.structured_json_call(
                system_prompt=self.policy.system_prompt_body,
                user_prompt=self._build_user_prompt(payload),
                max_output_tokens=self.policy.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.OK,
                outputs=[
                    self._fallback_envelope(
                        operator_message,
                        current_digest,
                        staleness,
                        payload.get("uploaded_file"),
                        tool_results,
                    )
                ],
                rationale=f"deterministic fallback: {type(exc).__name__}: {exc}",
            )

        if response is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.OK,
                outputs=[
                    self._fallback_envelope(
                        operator_message,
                        current_digest,
                        staleness,
                        payload.get("uploaded_file"),
                        tool_results,
                    )
                ],
                rationale="deterministic fallback: provider returned None",
            )

        envelope = self._parse_envelope(response)
        if not envelope:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.OK,
                outputs=[
                    self._fallback_envelope(
                        operator_message,
                        current_digest,
                        staleness,
                        payload.get("uploaded_file"),
                        tool_results,
                    )
                ],
                rationale="deterministic fallback: envelope parse failed",
            )

        grounded_text = self._apply_line_grounding(
            envelope["text"], current_digest, temporal_digest, tool_results
        )
        grounded_text = _scrub_banned_vocabulary(grounded_text, replacement="")
        if not grounded_text:
            grounded_text = self._fallback_text(current_digest, staleness)
        grounded_text = self._prepend_staleness_notice(grounded_text, staleness)
        grounded_text = grounded_text[:_MAX_TEXT_CHARS].strip()

        suggestions = [
            s for s in envelope["suggested_messages"]
            if _scrub_banned_vocabulary(s, replacement="") == s
        ][:_MAX_SUGGESTED]

        output = {
            "text": grounded_text,
            "tool_calls": self._with_deterministic_tool_calls(
                operator_message=operator_message,
                current_digest=current_digest,
                uploaded_file=payload.get("uploaded_file"),
                tool_results=tool_results,
                tool_calls=envelope["tool_calls"],
            ),
            "suggested_messages": suggestions,
        }

        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[output],
            rationale=f"tool_calls={len(envelope['tool_calls'])} suggestions={len(suggestions)}",
        )

    def _build_user_prompt(self, payload: dict[str, Any]) -> str:
        tool_list = [
            {"name": name, **schema}
            for name, schema in KNOWN_TOOLS.items()
        ]
        compact = {
            "current_state_digest": payload.get("current_state_digest"),
            "temporal_digest": payload.get("temporal_digest"),
            "digest_staleness": payload.get("digest_staleness") or {},
            "recent_turns": payload.get("recent_turns") or [],
            "operator_message": payload.get("operator_message"),
            "available_tools": tool_list,
            "tool_results": payload.get("tool_results") or [],
        }
        return json.dumps(compact, default=str, ensure_ascii=False, indent=2)

    def _parse_envelope(self, response: dict[str, Any]) -> dict[str, Any] | None:
        text = response.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        tool_calls_raw = response.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        if isinstance(tool_calls_raw, list):
            for item in tool_calls_raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("tool_name")
                if not isinstance(name, str):
                    continue
                if name not in KNOWN_TOOLS:
                    continue
                args = item.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}
                tool_calls.append({"name": name, "arguments": args})
        suggestions_raw = response.get("suggested_messages") or []
        suggestions: list[str] = []
        if isinstance(suggestions_raw, list):
            for s in suggestions_raw:
                if not isinstance(s, str):
                    continue
                s = s.strip()
                if not s or len(s) > _MAX_SUGGESTED_LEN:
                    continue
                suggestions.append(s)
                if len(suggestions) >= _MAX_SUGGESTED:
                    break
        return {
            "text": text.strip(),
            "tool_calls": tool_calls,
            "suggested_messages": suggestions,
        }

    def _with_deterministic_tool_calls(
        self,
        *,
        operator_message: str,
        current_digest: dict[str, Any],
        uploaded_file: Any,
        tool_results: list[Any],
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if tool_results:
            return tool_calls
        if isinstance(uploaded_file, dict) and not any(call.get("name") == "interpret_upload" for call in tool_calls):
            headers = uploaded_file.get("headers") or []
            sample_rows = uploaded_file.get("sample_rows") or []
            return [
                *tool_calls,
                {
                    "name": "interpret_upload",
                    "arguments": {"headers": headers, "sample_rows": sample_rows},
                },
            ]
        if _looks_like_readiness_question(operator_message, current_digest) and not any(call.get("name") == "check_readiness" for call in tool_calls):
            return [*tool_calls, {"name": "check_readiness", "arguments": {}}]
        if any(call.get("name") == "capture_note" for call in tool_calls):
            return tool_calls
        capture_args = _capture_note_args(operator_message, current_digest)
        if capture_args is None:
            return tool_calls
        return [*tool_calls, {"name": "capture_note", "arguments": capture_args}]

    def _apply_line_grounding(
        self,
        text: str,
        current_digest: dict[str, Any],
        temporal_digest: dict[str, Any],
        tool_results: list[Any],
    ) -> str:
        allowed_numbers = _collect_numbers(current_digest, temporal_digest, tool_results)
        sentences = _split_sentences(text)
        kept: list[str] = []
        for sentence in sentences:
            numbers_in_sentence = [m.group(1) for m in _NUMBER_RE.finditer(sentence)]
            sentence_ok = True
            for raw in numbers_in_sentence:
                if raw in _CARDINAL_WORDS_STR:
                    continue
                if raw in allowed_numbers:
                    continue
                sentence_ok = False
                break
            if sentence_ok:
                kept.append(sentence)
        return " ".join(kept).strip()

    def _prepend_staleness_notice(self, text: str, staleness: dict[str, Any]) -> str:
        try:
            age = float(staleness.get("current_state_age_seconds", 0) or 0)
        except (TypeError, ValueError):
            age = 0.0
        match = staleness.get("source_hash_match", True)
        if age > 3600 or match is False:
            notice = "Working from a snapshot taken earlier — the latest refresh may shift these numbers."
            if notice not in text:
                return f"{notice} {text}".strip()
        return text

    def _fallback_envelope(
        self,
        operator_message: str,
        current_digest: dict[str, Any],
        staleness: dict[str, Any],
        uploaded_file: Any = None,
        tool_results: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "text": self._prepend_staleness_notice(
                self._fallback_text(current_digest, staleness), staleness
            ),
            "tool_calls": self._with_deterministic_tool_calls(
                operator_message=operator_message,
                current_digest=current_digest,
                uploaded_file=uploaded_file,
                tool_results=tool_results or [],
                tool_calls=[],
            ),
            "suggested_messages": _fallback_suggestions(current_digest),
        }

    def _fallback_text(
        self, current_digest: dict[str, Any], staleness: dict[str, Any]
    ) -> str:
        headline = (current_digest or {}).get("headline_forecast")
        if isinstance(headline, dict) and headline.get("expected") is not None:
            expected = headline.get("expected")
            low = headline.get("low")
            high = headline.get("high")
            if low is not None and high is not None:
                return (
                    f"The latest forecast is about {expected} covers "
                    f"(range {low}–{high}). I do not have more detail available right now."
                )
            return f"The latest forecast is about {expected} covers. I do not have more detail available right now."
        phase = str((current_digest or {}).get("phase") or "")
        pending = (current_digest or {}).get("pending_action")
        if phase == "setup":
            if isinstance(pending, dict) and pending.get("prompt"):
                return str(pending["prompt"])
            return "Tell me the restaurant name, street address, and typical dinner cover counts to finish setup."
        if phase == "enrichment":
            if isinstance(pending, dict) and pending.get("prompt"):
                return str(pending["prompt"])
            return "Forecasts are ready. You can add optional context or start using the forecast view."
        return "I do not have a current forecast snapshot to quote from yet. Run a refresh and I'll have more to say."


_CARDINAL_WORDS_STR = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}


def _collect_numbers(
    current_digest: dict[str, Any],
    temporal_digest: dict[str, Any],
    tool_results: Iterable[Any],
) -> set[str]:
    allowed: set[str] = set(_CARDINAL_WORDS_STR)
    _walk_for_numbers(current_digest, allowed)
    _walk_for_numbers(temporal_digest, allowed)
    for result in tool_results or []:
        _walk_for_numbers(result, allowed)
    return allowed


def _walk_for_numbers(value: Any, out: set[str]) -> None:
    if isinstance(value, (int, float)):
        out.add(_number_key(value))
    elif isinstance(value, str):
        for m in _NUMBER_RE.finditer(value):
            out.add(m.group(1))
    elif isinstance(value, dict):
        for v in value.values():
            _walk_for_numbers(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _walk_for_numbers(v, out)


def _number_key(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _scrub_banned_vocabulary(text: str, replacement: str) -> str:
    lowered = text.lower()
    for banned in _BANNED_VOCABULARY:
        if banned in lowered:
            if replacement == "":
                return ""
            return replacement
    return text


def _fallback_suggestions(current_digest: dict[str, Any]) -> list[str]:
    phase = str((current_digest or {}).get("phase") or "operations")
    if phase == "setup":
        return ["Add cover counts", "Check readiness", "Add patio info"]
    if phase == "enrichment":
        return ["Start using forecasts", "Upload history", "Add nearby signals"]
    suggestions = ["Show me the week ahead", "Any open questions?"]
    pending = (current_digest or {}).get("pending_action")
    if isinstance(pending, dict) and pending.get("kind"):
        suggestions.append(str(pending.get("prompt") or "What needs attention?")[:_MAX_SUGGESTED_LEN])
    else:
        suggestions.append("Log a note")
    return suggestions[:_MAX_SUGGESTED]


def _looks_like_readiness_question(operator_message: str, current_digest: dict[str, Any]) -> bool:
    phase = str((current_digest or {}).get("phase") or "")
    if phase not in {"setup", "enrichment"}:
        return False
    lowered = operator_message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "am i ready",
            "are we ready",
            "ready to forecast",
            "ready for forecasts",
            "check readiness",
            "setup complete",
            "what is missing",
            "what's missing",
        )
    )


def _capture_note_args(operator_message: str, current_digest: dict[str, Any]) -> dict[str, Any] | None:
    text = operator_message.strip()
    if not text:
        return None
    lowered = text.lower()
    note_markers = (
        "note:",
        "note for ",
        "we had ",
        "we were ",
        "we closed",
        "private buyout",
        "buyout",
        "filled the room",
        "patio",
        "staffing",
    )
    if not any(marker in lowered for marker in note_markers):
        return None
    args: dict[str, Any] = {"note": text}
    service_date = _infer_service_date(text, current_digest)
    if service_date is not None:
        args["service_date"] = service_date.isoformat()
    if "buyout" in lowered or "private event" in lowered:
        args["service_state"] = "private_event_or_buyout"
    elif "closed" in lowered:
        args["service_state"] = "closed"
    elif "patio" in lowered:
        args["service_state"] = "patio_closed_or_constrained"
    return args


def _infer_service_date(operator_message: str, current_digest: dict[str, Any]) -> date | None:
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", operator_message)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            return None
    reference = _parse_iso_date((current_digest or {}).get("reference_date"))
    if reference is None:
        headline = (current_digest or {}).get("headline_forecast")
        if isinstance(headline, dict):
            reference = _parse_iso_date(headline.get("service_date"))
    if reference is None:
        return None
    lowered = operator_message.lower()
    if "today" in lowered or "tonight" in lowered:
        return reference
    if "tomorrow" in lowered:
        return reference + timedelta(days=1)
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, weekday in weekdays.items():
        if f"last {name}" in lowered:
            delta = (reference.weekday() - weekday) % 7
            if delta == 0:
                delta = 7
            return reference - timedelta(days=delta)
    return None


def _parse_iso_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


KNOWN_READ_TOOLS = KNOWN_TOOLS

__all__ = ["ConversationOrchestratorAgent", "KNOWN_TOOLS", "KNOWN_READ_TOOLS"]
