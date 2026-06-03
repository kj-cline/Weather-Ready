"""Conversation Note Extractor — structured note capture behind capture_note.

See ``policies/conversation_note_extractor.md`` for the role contract.

This agent replaces the legacy direct provider-backed note capture path. It
extracts the existing ``ConversationCapture`` shape only; conversation ownership
remains with ``ConversationOrchestratorAgent``.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from stormready_v3.agents.contracts import ConversationCapture
from stormready_v3.conversation.equation_links import equation_link_for_runtime_target
from stormready_v3.conversation.qualitative import observation_summary
from stormready_v3.domain.enums import ServiceState

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)


ALLOWED_RUNTIME_TARGETS = {
    "weather_patio_profile",
    "weather_profile_review",
    "transit_relevance",
    "venue_relevance",
    "hotel_travel_relevance",
    "walk_in_mix_review",
    "reservation_anchor_review",
    "service_constraints",
}
ALLOWED_DIRECTIONS = {"positive", "negative", "mixed"}
ALLOWED_STRENGTHS = {"low", "medium", "high"}
ALLOWED_FACT_KEYS = {
    "weather_mentioned",
    "weather_demand_impact",
    "patio_mentioned",
    "patio_operating_state",
    "patio_demand_state",
    "walk_in_state",
    "reservation_falloff",
    "staffing_constraint",
    "access_issue",
    "travel_mentioned",
    "event_demand_impact",
}
ALLOWED_CORRECTION_KEYS = {"realized_total_covers", "realized_reserved_covers"}

_AI_TARGET_TEMPLATES = {
    "weather_patio_profile": {
        "observation_type": "patio_weather_response",
        "dependency_group": "weather",
        "component_scope": "patio",
        "question_target": "weather_patio_profile",
        "promotion_mode": "deterministic_profile_hint",
    },
    "weather_profile_review": {
        "observation_type": "weather_response",
        "dependency_group": "weather",
        "component_scope": "total",
        "question_target": "weather_response_pattern",
        "promotion_mode": "qualitative_memory",
    },
    "transit_relevance": {
        "observation_type": "access_signal_relevance",
        "dependency_group": "access",
        "component_scope": "total",
        "question_target": "transit_relevance",
        "promotion_mode": "deterministic_relevance_flag",
    },
    "venue_relevance": {
        "observation_type": "venue_signal_relevance",
        "dependency_group": "venue",
        "component_scope": "total",
        "question_target": "venue_relevance",
        "promotion_mode": "deterministic_relevance_flag",
    },
    "hotel_travel_relevance": {
        "observation_type": "travel_signal_relevance",
        "dependency_group": "travel",
        "component_scope": "total",
        "question_target": "hotel_travel_relevance",
        "promotion_mode": "deterministic_relevance_flag",
    },
    "walk_in_mix_review": {
        "observation_type": "walk_in_mix_signal",
        "dependency_group": "walk_in",
        "component_scope": "walk_in",
        "question_target": "walk_in_mix_review",
        "promotion_mode": "qualitative_memory",
    },
    "reservation_anchor_review": {
        "observation_type": "reservation_anchor_signal",
        "dependency_group": "reservation",
        "component_scope": "reserved",
        "question_target": "reservation_falloff_pattern",
        "promotion_mode": "qualitative_memory",
    },
    "service_constraints": {
        "observation_type": "service_constraint_signal",
        "dependency_group": "service_state",
        "component_scope": "service_capacity",
        "question_target": "staffing_pattern",
        "promotion_mode": "qualitative_memory",
    },
}


class ConversationNoteExtractorAgent(BaseAgent):
    role = AgentRole.CONVERSATION_NOTE_EXTRACTOR

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = dict(ctx.payload)
        note = str(payload.get("note") or "").strip()
        if not note:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="note missing or blank",
            )

        try:
            response = self.provider.structured_json_call(
                system_prompt=self.policy.system_prompt_body,
                user_prompt=self._build_user_prompt(payload, note),
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

        capture = capture_from_payload(note=note, payload=response)
        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[asdict(capture)],
            rationale=_capture_rationale(capture),
        )

    def _build_user_prompt(self, payload: dict[str, Any], note: str) -> str:
        compact = {
            "note": note,
            "service_date": _stringify(payload.get("service_date")),
            "service_window": _stringify(payload.get("service_window")),
            "allowed_service_states": [state.value for state in ServiceState],
            "allowed_fact_keys": sorted(ALLOWED_FACT_KEYS),
            "allowed_runtime_targets": sorted(ALLOWED_RUNTIME_TARGETS),
        }
        return json.dumps(compact, default=str, ensure_ascii=False, indent=2)


def capture_from_payload(*, note: str, payload: dict[str, Any]) -> ConversationCapture:
    suggested_service_state = payload.get("suggested_service_state")
    if suggested_service_state is not None:
        suggested_service_state = str(suggested_service_state)
        if suggested_service_state not in {state.value for state in ServiceState}:
            suggested_service_state = None

    suggested_correction = _normalize_suggested_correction(payload.get("suggested_correction"))
    qualitative_themes = [
        str(item).strip()
        for item in list(payload.get("qualitative_themes") or [])[:6]
        if isinstance(item, str) and str(item).strip()
    ]
    extracted_facts = normalize_extracted_facts(payload.get("extracted_facts"))
    observations = normalize_ai_observations(payload.get("observations"))
    if not observations:
        observations = synthesize_observations_from_facts(
            extracted_facts=extracted_facts,
            qualitative_themes=qualitative_themes,
        )
    hypothesis_hints = synthesize_hypothesis_hints(observations)

    return ConversationCapture(
        note=note or None,
        suggested_service_state=suggested_service_state,
        suggested_correction=suggested_correction,
        qualitative_themes=qualitative_themes,
        extracted_facts=extracted_facts,
        hypothesis_hints=hypothesis_hints,
        observations=observations,
    )


def normalize_extracted_facts(raw_facts: object) -> dict[str, object]:
    if not isinstance(raw_facts, dict):
        return {}
    normalized: dict[str, object] = {}
    for key in ALLOWED_FACT_KEYS:
        if key not in raw_facts:
            continue
        value = raw_facts.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                normalized[key] = text
        elif isinstance(value, bool):
            normalized[key] = value
    return normalized


def normalize_ai_observations(raw_observations: object) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    if not isinstance(raw_observations, list):
        return observations
    for item in raw_observations[:3]:
        if not isinstance(item, dict):
            continue
        runtime_target = str(item.get("runtime_target") or "").strip()
        if runtime_target not in ALLOWED_RUNTIME_TARGETS:
            continue
        direction = str(item.get("direction") or "mixed").strip().lower()
        if direction not in ALLOWED_DIRECTIONS:
            direction = "mixed"
        strength = str(item.get("strength") or "medium").strip().lower()
        if strength not in ALLOWED_STRENGTHS:
            strength = "medium"
        summary = str(item.get("summary") or "").strip()
        observations.append(
            build_observation_from_target(
                runtime_target=runtime_target,
                direction=direction,
                strength=strength,
                summary=summary or observation_summary(runtime_target, direction),
            )
        )
    return observations


def synthesize_observations_from_facts(
    *,
    extracted_facts: dict[str, object],
    qualitative_themes: list[str],
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    if str(extracted_facts.get("patio_demand_state") or "").lower() == "strong":
        observations.append(
            build_observation_from_target(
                runtime_target="weather_patio_profile",
                direction="positive",
                strength="high",
                summary=observation_summary("weather_patio_profile", "positive"),
            )
        )
    if str(extracted_facts.get("patio_operating_state") or "").lower() == "closed_or_constrained":
        observations.append(
            build_observation_from_target(
                runtime_target="weather_patio_profile",
                direction="negative",
                strength="high",
                summary=observation_summary("weather_patio_profile", "negative"),
            )
        )
    walk_in_state = str(extracted_facts.get("walk_in_state") or "").lower()
    if walk_in_state in {"strong", "soft"}:
        direction = "positive" if walk_in_state == "strong" else "negative"
        observations.append(
            build_observation_from_target(
                runtime_target="walk_in_mix_review",
                direction=direction,
                strength="medium",
                summary=observation_summary("walk_in_mix_review", direction),
            )
        )
    weather_impact = str(extracted_facts.get("weather_demand_impact") or "").lower()
    if weather_impact in {"positive", "negative"}:
        observations.append(
            build_observation_from_target(
                runtime_target="weather_profile_review",
                direction=weather_impact,
                strength="medium",
                summary=observation_summary("weather_profile_review", weather_impact),
            )
        )
    if bool(extracted_facts.get("reservation_falloff")):
        observations.append(
            build_observation_from_target(
                runtime_target="reservation_anchor_review",
                direction="negative",
                strength="medium",
                summary=observation_summary("reservation_anchor_review", "negative"),
            )
        )
    if bool(extracted_facts.get("staffing_constraint")):
        observations.append(
            build_observation_from_target(
                runtime_target="service_constraints",
                direction="negative",
                strength="medium",
                summary=observation_summary("service_constraints", "negative"),
            )
        )
    if bool(extracted_facts.get("access_issue")):
        observations.append(
            build_observation_from_target(
                runtime_target="transit_relevance",
                direction="negative",
                strength="medium",
                summary=observation_summary("transit_relevance", "negative"),
            )
        )
    if bool(extracted_facts.get("travel_mentioned")) and "travel_pull" in qualitative_themes:
        observations.append(
            build_observation_from_target(
                runtime_target="hotel_travel_relevance",
                direction="positive",
                strength="medium",
                summary=observation_summary("hotel_travel_relevance", "positive"),
            )
        )
    if str(extracted_facts.get("event_demand_impact") or "").lower() == "positive":
        observations.append(
            build_observation_from_target(
                runtime_target="venue_relevance",
                direction="positive",
                strength="medium",
                summary=observation_summary("venue_relevance", "positive"),
            )
        )

    deduped: list[dict[str, object]] = []
    seen_targets: set[str] = set()
    for item in observations:
        runtime_target = str(item.get("runtime_target") or "")
        if not runtime_target or runtime_target in seen_targets:
            continue
        seen_targets.add(runtime_target)
        deduped.append(item)
    return deduped[:3]


def build_observation_from_target(
    *,
    runtime_target: str,
    direction: str,
    strength: str,
    summary: str,
) -> dict[str, object]:
    template = _AI_TARGET_TEMPLATES[runtime_target]
    equation_link = equation_link_for_runtime_target(runtime_target)
    return {
        **template,
        "direction": direction,
        "strength": strength,
        "recurrence_hint": "possible_recurring",
        "runtime_target": runtime_target,
        "equation_terms": equation_link.get("equation_terms", []),
        "equation_path": equation_link.get("equation_path"),
        "equation_influence_mode": equation_link.get("influence_mode"),
        "summary": summary,
    }


def synthesize_hypothesis_hints(observations: list[dict[str, object]]) -> list[str]:
    hint_map = {
        "weather_patio_profile": "pattern::weather_patio_risk",
        "weather_profile_review": "pattern::weather_response",
        "transit_relevance": "pattern::transit_or_access_issue",
        "venue_relevance": "pattern::event_impact",
        "hotel_travel_relevance": "pattern::hotel_or_travel_pull",
        "walk_in_mix_review": "pattern::walk_in_mix",
        "reservation_anchor_review": "pattern::reservation_falloff",
        "service_constraints": "pattern::staffing_constraint",
    }
    hints: list[str] = []
    for item in observations:
        runtime_target = str(item.get("runtime_target") or "")
        hint = hint_map.get(runtime_target)
        if hint and hint not in hints:
            hints.append(hint)
    return hints


def _normalize_suggested_correction(raw_correction: object) -> dict[str, str]:
    suggested_correction: dict[str, str] = {}
    if not isinstance(raw_correction, dict):
        return suggested_correction
    for key in ALLOWED_CORRECTION_KEYS:
        value = raw_correction.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text.isdigit():
            suggested_correction[key] = text
    return suggested_correction


def _capture_rationale(capture: ConversationCapture) -> str:
    structured_count = int(capture.suggested_service_state is not None)
    structured_count += len(capture.suggested_correction)
    structured_count += len(capture.qualitative_themes)
    structured_count += len(capture.extracted_facts)
    structured_count += len(capture.observations)
    if structured_count == 0:
        return "note retained without structured fields"
    return f"{structured_count} structured field(s) extracted"


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "ConversationNoteExtractorAgent",
    "capture_from_payload",
    "normalize_ai_observations",
    "normalize_extracted_facts",
    "synthesize_hypothesis_hints",
    "synthesize_observations_from_facts",
]
