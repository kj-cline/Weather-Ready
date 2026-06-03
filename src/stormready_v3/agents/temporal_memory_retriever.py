"""Temporal Memory Retriever — curates the historical grounding digest.

See ``policies/temporal_memory_retriever.md`` for the role contract. Fires on
actual-record, note-save, hypothesis-change, and learning-agenda events. Never
runs on the chat critical path. Output is a single ``TemporalContextDigest``
persisted to ``operator_context_digest`` with kind='temporal'.

Two paths like the current state retriever:
1. LLM path: asks the model to compress the structured history into the digest
   shape, validates against caps.
2. Deterministic fallback: picks rows by the policy's selection rules without
   any model call. Used when provider is unavailable or returns garbage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)
from .digests import (
    MAX_ACTIVE_HYPOTHESES,
    MAX_DISCLAIMERS,
    MAX_OPEN_QUESTIONS,
    MAX_OPERATOR_FACTS,
    MAX_RECENT_MISSES,
    MAX_RECENT_PATTERNS,
    TemporalContextDigest,
)


_ALLOWED_STATES = {"cold_start", "active", "follow_up"}
_ALLOWED_CONFIDENCE = {"low", "medium", "high"}
_HYPOTHESIS_ACTIVE_STATUSES = {"open", "confirmed"}
_MIN_MISS_MAGNITUDE = 0.10
_BANNED_VOCABULARY = {
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
}


class TemporalMemoryRetrieverAgent(BaseAgent):
    role = AgentRole.TEMPORAL_MEMORY_RETRIEVER

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = dict(ctx.payload)

        llm_digest: TemporalContextDigest | None = None
        llm_error: str | None = None
        try:
            response = self.provider.structured_json_call(
                system_prompt=self.policy.system_prompt_body,
                user_prompt=self._build_user_prompt(payload),
                max_output_tokens=self.policy.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            llm_error = f"{type(exc).__name__}: {exc}"
            response = None

        if response is not None:
            try:
                llm_digest = self._parse_digest(response, payload)
            except Exception as exc:  # noqa: BLE001
                llm_error = f"parse failed: {type(exc).__name__}: {exc}"
                llm_digest = None

        digest = llm_digest or self._deterministic_digest(payload)
        if digest is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale=llm_error or "deterministic fallback produced no digest",
            )

        rationale_parts = ["llm path" if llm_digest is not None else "deterministic fallback"]
        if llm_error:
            rationale_parts.append(llm_error)
        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[json.loads(digest.to_json())],
            rationale="; ".join(rationale_parts),
        )

    def _build_user_prompt(self, payload: dict[str, Any]) -> str:
        compact = {
            "recent_misses_raw": payload.get("recent_misses_raw") or [],
            "open_hypotheses": payload.get("open_hypotheses") or [],
            "recent_patterns_raw": payload.get("recent_patterns_raw") or [],
            "operator_facts_raw": payload.get("operator_facts_raw") or [],
            "learning_agenda_rows": payload.get("learning_agenda_rows") or [],
            "actual_count_total": payload.get("actual_count_total", 0),
            "last_conversation_at": _stringify(payload.get("last_conversation_at")),
            "demoted_sources": payload.get("demoted_sources") or [],
        }
        return json.dumps(compact, default=str, ensure_ascii=False, indent=2)

    def _parse_digest(
        self, response: dict[str, Any], payload: dict[str, Any]
    ) -> TemporalContextDigest:
        raw = response.get("digest")
        if not isinstance(raw, dict):
            raise ValueError("response.digest is not an object")

        state = raw.get("conversation_state")
        if state not in _ALLOWED_STATES:
            state = _deterministic_conversation_state(payload)

        recent_misses = _clamp_dict_list(raw.get("recent_misses"), MAX_RECENT_MISSES)
        active_hypotheses = _clamp_dict_list(raw.get("active_hypotheses"), MAX_ACTIVE_HYPOTHESES)
        for h in active_hypotheses:
            conf = h.get("confidence")
            if conf not in _ALLOWED_CONFIDENCE:
                h["confidence"] = "low"

        recent_patterns_raw = raw.get("recent_patterns") or []
        recent_patterns = _clamp_str_list(recent_patterns_raw, MAX_RECENT_PATTERNS)

        operator_facts = _clamp_dict_list(raw.get("operator_facts"), MAX_OPERATOR_FACTS)
        operator_facts = [
            f for f in operator_facts
            if f.get("confidence") in {"medium", "high"}
        ]

        learning_maturity = raw.get("learning_maturity")
        if not isinstance(learning_maturity, dict):
            learning_maturity = _deterministic_learning_maturity(payload)

        open_questions = _clamp_dict_list(raw.get("open_questions"), MAX_OPEN_QUESTIONS)
        disclaimers = _clamp_str_list(raw.get("disclaimers"), MAX_DISCLAIMERS)

        return TemporalContextDigest(
            produced_at=datetime.now(timezone.utc),
            source_hash=_hash_payload(payload),
            conversation_state=state,
            recent_misses=recent_misses,
            active_hypotheses=active_hypotheses,
            recent_patterns=recent_patterns,
            operator_facts=operator_facts,
            learning_maturity=learning_maturity,
            open_questions=open_questions,
            disclaimers=disclaimers,
        )

    def _deterministic_digest(
        self, payload: dict[str, Any]
    ) -> TemporalContextDigest:
        state = _deterministic_conversation_state(payload)

        # Recent misses: |err_pct| >= 0.10, normal service, top 3 by magnitude.
        misses_raw = payload.get("recent_misses_raw") or []
        misses = []
        for row in misses_raw:
            if not isinstance(row, dict):
                continue
            try:
                err = float(row.get("err_pct", 0.0))
            except (TypeError, ValueError):
                continue
            if abs(err) < _MIN_MISS_MAGNITUDE:
                continue
            if str(row.get("service_state", "normal")).lower() != "normal":
                continue
            misses.append({
                "service_date": _stringify(row.get("service_date")),
                "err_pct": round(err, 3),
                "state": "normal",
                "short_label": _clean_string(row.get("short_label") or _label_for_miss(row), 200),
            })
        misses.sort(key=lambda m: abs(m["err_pct"]), reverse=True)
        misses = misses[:MAX_RECENT_MISSES]

        # Active hypotheses: status open/confirmed, sorted by confidence then recency.
        hyp_raw = payload.get("open_hypotheses") or []
        hyps = []
        for row in hyp_raw:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "open")).lower()
            if status not in _HYPOTHESIS_ACTIVE_STATUSES:
                continue
            conf = row.get("confidence", "low")
            if conf not in _ALLOWED_CONFIDENCE:
                conf = "low"
            hyps.append({
                "hypothesis_key": row.get("hypothesis_key", ""),
                "proposition": _clean_string(row.get("proposition", ""), 200),
                "status": status,
                "confidence": conf,
            })
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        hyps.sort(key=lambda h: conf_rank.get(h["confidence"], 99))
        hyps = [h for h in hyps if h["proposition"]][:MAX_ACTIVE_HYPOTHESES]

        # Patterns: clean strings only, cap 3.
        patterns_raw = payload.get("recent_patterns_raw") or []
        patterns: list[str] = []
        for p in patterns_raw[:MAX_RECENT_PATTERNS * 2]:
            text = p if isinstance(p, str) else (p.get("summary", "") if isinstance(p, dict) else "")
            cleaned = _clean_string(text, 200)
            if cleaned:
                patterns.append(cleaned)
            if len(patterns) >= MAX_RECENT_PATTERNS:
                break

        # Operator facts: confidence in {medium, high}, cap 6.
        facts_raw = payload.get("operator_facts_raw") or []
        facts: list[dict[str, Any]] = []
        for row in facts_raw:
            if not isinstance(row, dict):
                continue
            if row.get("confidence") not in {"medium", "high"}:
                continue
            facts.append({
                "key": row.get("key", ""),
                "value": _clean_string(row.get("value", ""), 200),
                "confidence": row.get("confidence"),
            })
            if len(facts) >= MAX_OPERATOR_FACTS:
                break

        # Open questions: pick ready agenda items.
        agenda_raw = payload.get("learning_agenda_rows") or []
        questions: list[dict[str, Any]] = []
        for row in agenda_raw:
            if not isinstance(row, dict):
                continue
            if not row.get("ready_to_ask", True):
                continue
            questions.append({
                "agenda_key": row.get("agenda_key", ""),
                "prompt": _clean_string(row.get("prompt", ""), 200),
            })
            if len(questions) >= MAX_OPEN_QUESTIONS:
                break

        # Disclaimers.
        disclaimers: list[str] = []
        if int(payload.get("actual_count_total") or 0) < 10:
            disclaimers.append("Learning is early — patterns may shift as more actuals arrive.")
        if payload.get("demoted_sources"):
            disclaimers.append("Some external sources are muted while they re-prove.")
        disclaimers = disclaimers[:MAX_DISCLAIMERS]

        learning_maturity = _deterministic_learning_maturity(payload)

        return TemporalContextDigest(
            produced_at=datetime.now(timezone.utc),
            source_hash=_hash_payload(payload),
            conversation_state=state,
            recent_misses=misses,
            active_hypotheses=hyps,
            recent_patterns=patterns,
            operator_facts=facts,
            learning_maturity=learning_maturity,
            open_questions=questions,
            disclaimers=disclaimers,
        )


def _clamp_dict_list(value: Any, cap: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:cap]:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def _clamp_str_list(value: Any, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:cap]:
        if not isinstance(item, str):
            continue
        cleaned = _clean_string(item, 200)
        if cleaned:
            out.append(cleaned)
    return out


def _clean_string(value: Any, max_len: int) -> str:
    s = str(value or "").strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    lowered = s.lower()
    for word in _BANNED_VOCABULARY:
        if word in lowered:
            return ""
    return s


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _hash_payload(payload: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(payload, default=str, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _label_for_miss(row: dict[str, Any]) -> str:
    try:
        err = float(row.get("err_pct", 0.0))
    except (TypeError, ValueError):
        return ""
    direction = "under" if err < 0 else "over"
    return f"{row.get('service_date', 'recent night')} {direction}performed"


def _deterministic_conversation_state(payload: dict[str, Any]) -> str:
    total = int(payload.get("actual_count_total") or 0)
    last_chat = payload.get("last_conversation_at")
    follow_up_flag = payload.get("has_pending_followup")
    if follow_up_flag:
        return "follow_up"
    if total < 3 or not last_chat:
        return "cold_start"
    return "active"


def _deterministic_learning_maturity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "samples": int(payload.get("actual_count_total") or 0),
        "cascades_live": list(payload.get("cascades_live") or []),
        "demoted_sources": list(payload.get("demoted_sources") or []),
    }


__all__ = ["TemporalMemoryRetrieverAgent"]
