"""Prediction Governor — BaseAgent implementation for forecast-pre-publish governance.

See ``policies/prediction_governor.md`` for the full role contract.

This agent takes a serialized forecast candidate, optionally asks the model to
emphasize up to 3 drivers and produce an operator-facing explanation, and
enforces the strict-subset guardrail on emphasized drivers. It also emits the
legacy governance fields the publish pipeline still consumes.
"""

from __future__ import annotations

import json
from typing import Any

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)


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
_MAX_EXPLANATION_CHARS = 360
_MAX_EMPHASIZED = 3
_DRIVER_PRIORITY = {
    "service_state_override": 0,
    "booked_reservation_anchor": 1,
    "brooklyn_weather_reference": 2,
    "nws_active_alert": 3,
    "weather_alert": 3,
    "weather_disruption_risk": 4,
    "precip_overlap": 5,
    "snow_risk": 6,
    "transit_disruption": 7,
    "baseline service window pattern": 99,
}


class PredictionGovernorAgent(BaseAgent):
    role = AgentRole.PREDICTION_GOVERNOR

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = dict(ctx.payload)
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="candidate missing or malformed",
            )

        driver_keys = _driver_keys(candidate.get("top_drivers") or [])
        valid_drivers = set(driver_keys)
        if not valid_drivers:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="candidate.top_drivers is empty",
            )

        heuristic_summary = _heuristic_summary(candidate, payload.get("heuristic_summary"))

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
                status=AgentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
        if response is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="provider returned None",
            )

        baseline_emphasized = _baseline_emphasized_drivers(driver_keys)
        emphasized_raw = response.get("emphasized_drivers") or []
        if not isinstance(emphasized_raw, list):
            emphasized_raw = []
        emphasized: list[str] = []
        for item in emphasized_raw:
            key = str(item)
            if key in valid_drivers and key not in emphasized:
                emphasized.append(key)
            if len(emphasized) >= _MAX_EMPHASIZED:
                break
        if not emphasized:
            emphasized = list(baseline_emphasized)

        explanation = _clean_explanation(response.get("explanation", ""))
        clarification_needed = bool(heuristic_summary["clarification_needed"])
        clarification_question = str(heuristic_summary["clarification_question"] or "").strip() or None
        response_clarification = str(response.get("clarification_question") or "").strip() or None
        if clarification_needed and response_clarification:
            clarification_question = response_clarification

        uncertainty_notes = list(heuristic_summary["uncertainty_notes"])
        response_notes = response.get("uncertainty_notes") or []
        if isinstance(response_notes, list) and response_notes:
            clean_response_notes = [str(item).strip() for item in response_notes if str(item).strip()]
            if clean_response_notes:
                uncertainty_notes = list(dict.fromkeys([*clean_response_notes, *uncertainty_notes]))
        uncertainty_notes = uncertainty_notes[:3]

        emphasized_indices = [
            index for index, driver in enumerate(driver_keys) if driver in set(emphasized)
        ][: _MAX_EMPHASIZED]

        if not emphasized and not explanation and not uncertainty_notes and not clarification_question:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="response produced no usable output",
            )

        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[{
                "emphasized_drivers": emphasized,
                "emphasized_driver_indices": emphasized_indices,
                "explanation": explanation,
                "clarification_needed": clarification_needed,
                "clarification_question": clarification_question,
                "uncertainty_notes": uncertainty_notes,
                "governance_path": "ai",
            }],
            rationale=f"{len(emphasized)} driver(s) emphasized",
        )

    def _build_user_prompt(self, payload: dict[str, Any]) -> str:
        compact = {
            "service_date": _stringify(payload.get("service_date")),
            "service_window": payload.get("service_window"),
            "candidate": payload.get("candidate"),
            "recent_actuals_summary": payload.get("recent_actuals_summary"),
            "service_state": payload.get("service_state"),
            "phase": payload.get("phase"),
            "heuristic_summary": payload.get("heuristic_summary"),
            "learning_context": payload.get("learning_context"),
        }
        return json.dumps(compact, default=str, ensure_ascii=False, indent=2)


def _driver_keys(drivers: list[Any]) -> list[str]:
    out: list[str] = []
    for d in drivers:
        if isinstance(d, str):
            out.append(d)
        elif isinstance(d, dict):
            key = d.get("key") or d.get("name")
            if key:
                out.append(str(key))
    return out


def _baseline_emphasized_drivers(driver_keys: list[str]) -> list[str]:
    ranked_indices = sorted(
        range(len(driver_keys)),
        key=lambda index: (_DRIVER_PRIORITY.get(driver_keys[index], 50), driver_keys[index]),
    )
    return [driver_keys[index] for index in ranked_indices[: min(_MAX_EMPHASIZED, len(ranked_indices))]]


def _heuristic_summary(candidate: dict[str, Any], payload_summary: Any) -> dict[str, Any]:
    if isinstance(payload_summary, dict):
        clarification_needed = bool(payload_summary.get("clarification_needed"))
        clarification_question = str(payload_summary.get("clarification_question") or "").strip() or None
        payload_notes = payload_summary.get("uncertainty_notes") or []
        uncertainty_notes = [
            str(item).strip() for item in payload_notes if str(item).strip()
        ][:3]
        return {
            "clarification_needed": clarification_needed,
            "clarification_question": clarification_question,
            "uncertainty_notes": uncertainty_notes,
        }

    clarification_needed = False
    clarification_question = None
    uncertainty_notes = list(dict.fromkeys(_string_list(candidate.get("major_uncertainties"))))

    if str(candidate.get("target_definition_confidence") or "") in {"low", "low_medium"}:
        clarification_needed = True
        clarification_question = (
            "If you have reservation, walk-in, or waitlist detail for this service, logging it will improve future forecasts."
        )
        uncertainty_notes.append("component truth is still developing")

    service_state_reason = str(candidate.get("service_state_reason") or "")
    if (
        _service_state_value(candidate) != "normal_service"
        and service_state_reason
        and "suggestion" in service_state_reason.lower()
    ):
        clarification_needed = True
        clarification_question = "The service state looks abnormal. Confirming whether service was limited will improve forecast reliability."
        uncertainty_notes.append("service state may still need operator confirmation")

    if str(candidate.get("confidence_tier") or "") == "very_low":
        uncertainty_notes.append("treat this forecast as directional rather than precise")

    return {
        "clarification_needed": clarification_needed,
        "clarification_question": clarification_question,
        "uncertainty_notes": list(dict.fromkeys(uncertainty_notes))[:3],
    }


def _clean_explanation(value: Any) -> str:
    s = str(value or "").strip()
    if len(s) > _MAX_EXPLANATION_CHARS:
        s = s[:_MAX_EXPLANATION_CHARS].rstrip()
    lowered = s.lower()
    for word in _BANNED_VOCABULARY:
        if word in lowered:
            return ""
    return s


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _service_state_value(candidate: dict[str, Any]) -> str:
    value = candidate.get("service_state")
    if value is None:
        return ""
    return str(value)


__all__ = ["PredictionGovernorAgent"]
