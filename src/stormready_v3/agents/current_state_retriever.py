"""Current State Retriever — produces the present-tense CurrentStateDigest.

See ``policies/current_state_retriever.md`` for the role contract.

Runs on events (forecast refresh complete, actual record complete), never on
the chat critical path. The output is a single ``CurrentStateDigest`` which the
wiring layer persists to ``operator_context_digest`` with kind='current_state'.

The agent has two paths:

1. LLM path: asks the model to compress the structured inputs into the digest
   shape, then validates the response against the schema.
2. Deterministic fallback: when the provider is unavailable or returns an
   unusable response, the agent builds the digest directly from the payload
   fields. The fallback is dumb but correct — it never invents values and it
   obeys the same caps as the LLM path.
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
    MAX_ACTIVE_SIGNALS,
    MAX_DISCLAIMERS,
    MAX_NEAR_HORIZON,
    CurrentStateDigest,
)


_ALLOWED_PHASES = {"setup", "enrichment", "operations"}
_ALLOWED_URGENCY = {"low", "medium", "high"}
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
}


class CurrentStateRetrieverAgent(BaseAgent):
    role = AgentRole.CURRENT_STATE_RETRIEVER

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = dict(ctx.payload)
        reference_date = payload.get("reference_date")
        phase = str(payload.get("phase", "operations"))
        if phase not in _ALLOWED_PHASES:
            phase = "operations"
        if reference_date is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="reference_date missing from payload",
            )

        # LLM attempt.
        llm_digest: CurrentStateDigest | None = None
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
                llm_digest = self._parse_digest(response, payload, phase)
            except Exception as exc:  # noqa: BLE001
                llm_error = f"parse failed: {type(exc).__name__}: {exc}"
                llm_digest = None

        digest = llm_digest or self._deterministic_digest(payload, phase)
        if digest is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale=llm_error or "deterministic fallback produced no digest",
            )

        rationale_parts: list[str] = []
        if llm_digest is not None:
            rationale_parts.append("llm path")
        else:
            rationale_parts.append("deterministic fallback")
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
            "reference_date": _stringify(payload.get("reference_date")),
            "phase": payload.get("phase", "operations"),
            "identity": payload.get("identity") or {},
            "published_forecast": payload.get("published_forecast"),
            "near_horizon_rows": payload.get("near_horizon_rows") or [],
            "open_action_items": payload.get("open_action_items") or [],
            "active_signals": payload.get("active_signals") or [],
            "missing_actuals_dates": [
                _stringify(d) for d in (payload.get("missing_actuals_dates") or [])
            ],
            "operator_maturity_hint": payload.get("operator_maturity_hint"),
        }
        return json.dumps(compact, default=str, ensure_ascii=False, indent=2)

    def _parse_digest(
        self,
        response: dict[str, Any],
        payload: dict[str, Any],
        phase: str,
    ) -> CurrentStateDigest:
        raw = response.get("digest")
        if not isinstance(raw, dict):
            raise ValueError("response.digest is not an object")
        reference_date = _parse_date(raw.get("reference_date") or payload.get("reference_date"))
        identity = raw.get("identity") or payload.get("identity") or {}
        if not isinstance(identity, dict):
            identity = {}

        headline = raw.get("headline_forecast")
        if headline is not None and not isinstance(headline, dict):
            headline = None

        near_horizon = _clamp_list(raw.get("near_horizon") or [], MAX_NEAR_HORIZON)
        near_horizon = [n for n in near_horizon if isinstance(n, dict)]

        pending_action = raw.get("pending_action")
        if pending_action is not None and not isinstance(pending_action, dict):
            pending_action = None
        if isinstance(pending_action, dict):
            urgency = pending_action.get("urgency", "medium")
            if urgency not in _ALLOWED_URGENCY:
                pending_action["urgency"] = "medium"

        uncertainty = raw.get("current_uncertainty")
        if uncertainty is not None:
            uncertainty = _clean_string(uncertainty, 160)

        signals = _clamp_list(raw.get("active_signals_summary") or [], MAX_ACTIVE_SIGNALS)
        signals = [_clean_string(s, 160) for s in signals if isinstance(s, str)]
        signals = [s for s in signals if s]

        missing = [_parse_date(d) for d in (raw.get("missing_actuals") or [])]
        missing = [d for d in missing if d is not None]

        disclaimers = _clamp_list(raw.get("disclaimers") or [], MAX_DISCLAIMERS)
        disclaimers = [_clean_string(s, 160) for s in disclaimers if isinstance(s, str)]
        disclaimers = [s for s in disclaimers if s]

        return CurrentStateDigest(
            produced_at=datetime.now(timezone.utc),
            source_hash=_hash_payload(payload),
            reference_date=reference_date,
            phase=phase,
            identity=identity,
            headline_forecast=headline,
            near_horizon=near_horizon,
            pending_action=pending_action,
            current_uncertainty=uncertainty,
            active_signals_summary=signals,
            missing_actuals=missing,
            disclaimers=disclaimers,
        )

    def _deterministic_digest(
        self,
        payload: dict[str, Any],
        phase: str,
    ) -> CurrentStateDigest | None:
        reference_date = _parse_date(payload.get("reference_date"))
        if reference_date is None:
            return None
        identity = payload.get("identity") or {}
        if not isinstance(identity, dict):
            identity = {}

        published = payload.get("published_forecast")
        headline = published if isinstance(published, dict) else None

        near_rows = payload.get("near_horizon_rows") or []
        near_horizon = []
        for row in near_rows[:MAX_NEAR_HORIZON]:
            if isinstance(row, dict):
                near_horizon.append(row)

        open_actions = payload.get("open_action_items") or []
        pending_action: dict[str, Any] | None = None
        if open_actions:
            first = open_actions[0]
            if isinstance(first, dict):
                pending_action = {
                    "kind": first.get("kind", "action"),
                    "prompt": _clean_string(first.get("prompt", ""), 160),
                    "urgency": first.get("urgency", "medium")
                    if first.get("urgency") in _ALLOWED_URGENCY
                    else "medium",
                }

        uncertainty = _deterministic_uncertainty(headline)

        signals_raw = payload.get("active_signals") or []
        signals: list[str] = []
        for sig in signals_raw[:MAX_ACTIVE_SIGNALS]:
            if isinstance(sig, dict):
                label = sig.get("short_label") or sig.get("rationale") or ""
                if label:
                    signals.append(_clean_string(str(label), 160))
            elif isinstance(sig, str):
                signals.append(_clean_string(sig, 160))

        missing_raw = payload.get("missing_actuals_dates") or []
        missing = [d for d in (_parse_date(x) for x in missing_raw) if d is not None]

        disclaimers = _deterministic_disclaimers(payload)

        return CurrentStateDigest(
            produced_at=datetime.now(timezone.utc),
            source_hash=_hash_payload(payload),
            reference_date=reference_date,
            phase=phase if phase in _ALLOWED_PHASES else "operations",
            identity=identity,
            headline_forecast=headline,
            near_horizon=near_horizon,
            pending_action=pending_action,
            current_uncertainty=uncertainty,
            active_signals_summary=signals,
            missing_actuals=missing,
            disclaimers=disclaimers,
        )


def _clamp_list(value: Any, cap: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:cap]


def _clean_string(value: Any, max_len: int) -> str:
    s = str(value or "").strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    lowered = s.lower()
    for word in _BANNED_VOCABULARY:
        if word in lowered:
            return ""
    return s


def _parse_date(value: Any):
    from datetime import date as _date

    if isinstance(value, _date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


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


def _deterministic_uncertainty(headline: dict[str, Any] | None) -> str | None:
    if not isinstance(headline, dict):
        return None
    expected = headline.get("expected")
    low = headline.get("low")
    high = headline.get("high")
    if not isinstance(expected, (int, float)) or expected <= 0:
        return None
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    width = float(high) - float(low)
    if width / float(expected) > 0.4:
        return "Forecast band is wider than usual; expect some swing."
    return None


def _deterministic_disclaimers(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    hint = str(payload.get("operator_maturity_hint") or "").lower()
    if "early" in hint or "cold" in hint:
        out.append("Learning is early — numbers will sharpen as more actuals arrive.")
    if payload.get("has_demoted_sources"):
        out.append("One or more external sources are currently muted while they re-prove.")
    if len(out) > MAX_DISCLAIMERS:
        out = out[:MAX_DISCLAIMERS]
    return out


__all__ = ["CurrentStateRetrieverAgent"]
