from __future__ import annotations

from dataclasses import dataclass, field

from .equation_links import equation_link_for_runtime_target


@dataclass(slots=True)
class QualitativeNoteAnalysis:
    themes: list[str] = field(default_factory=list)
    extracted_facts: dict[str, object] = field(default_factory=dict)
    hypothesis_hints: list[str] = field(default_factory=list)
    observations: list[dict[str, object]] = field(default_factory=list)


_OBSERVATION_SUMMARY_TEMPLATES: dict[str, dict[str, str]] = {
    "weather_profile_review": {
        "positive": "Weather seems to be lifting demand.",
        "negative": "Weather seems to be reducing demand.",
        "mixed": "Weather may be affecting demand.",
    },
    "weather_patio_profile": {
        "positive": "Patio demand looks stronger than usual.",
        "negative": "Patio demand looks constrained.",
        "mixed": "Patio demand may be shifting the usual pattern.",
    },
    "service_constraints": {
        "negative": "Service constraints may be limiting covers.",
        "mixed": "Service constraints may be affecting covers.",
    },
    "venue_relevance": {
        "positive": "Nearby events seem to be lifting demand.",
        "mixed": "Nearby events may be affecting demand.",
    },
    "transit_relevance": {
        "negative": "Access conditions may be affecting demand.",
        "mixed": "Access conditions may be affecting demand.",
    },
    "hotel_travel_relevance": {
        "positive": "Hotel or travel demand seems to matter here.",
        "mixed": "Hotel or travel demand may matter here.",
    },
    "reservation_anchor_review": {
        "negative": "Reservation falloff may be affecting realized covers.",
        "mixed": "Reservation behavior may be affecting realized covers.",
    },
    "walk_in_mix_review": {
        "positive": "Walk-in demand looks stronger than usual.",
        "negative": "Walk-in demand looks softer than usual.",
        "mixed": "Walk-in demand may be shifting the usual pattern.",
    },
}

_WEATHER_ANY = ("rain", "storm", "snow", "ice", "weather", "wind", "heat", "cold")
_WEATHER_NEGATIVE = (
    "rain hurt",
    "weather hurt",
    "storm hurt",
    "snow hurt",
    "slow because of rain",
    "slow because of weather",
    "weather kept people away",
    "rain kept people away",
    "down because of rain",
    "weather delay",
)
_WEATHER_POSITIVE = (
    "weather helped",
    "rain helped",
    "heat helped",
    "nice weather helped",
    "patio was packed",
    "outside was packed",
)
_PATIO_ANY = ("patio", "outdoor", "outside seats", "outside dining", "outside covers")
_PATIO_NEGATIVE = (
    "patio closed",
    "patio was closed",
    "outdoor closed",
    "outdoor was closed",
    "outside closed",
    "outside was closed",
    "patio shut",
    "patio constrained",
    "rain cuts patio demand materially",
    "rain usually cuts patio demand materially",
)
_PATIO_POSITIVE = (
    "patio was packed",
    "outside was packed",
    "patio full",
    "outdoor full",
    "good weather creates extra patio covers",
    "good weather creates meaningful extra patio covers",
)
_STAFFING_ANY = ("staffing", "understaffed", "short staffed", "callout", "call-out", "kitchen issue", "server issue")
_EVENT_ANY = ("concert", "game", "event", "arena", "stadium", "theater", "buyout", "private event", "after the show", "show crowd")
_EVENT_POSITIVE = ("helped", "boosted", "lifted", "packed after the game", "concert crowd", "event crowd")
_ACCESS_ANY = ("train", "subway", "metro", "transit", "station", "bus", "road closed", "traffic", "lane closure", "access issue")
_TRAVEL_ANY = ("hotel", "tourist", "tourism", "airport", "travel", "convention")
_RESERVATION_FALLOFF_ANY = ("no-show", "no show", "cancellation", "cancelation", "cancelled reservation", "canceled reservation")
_WALK_IN_SOFT_ANY = ("walk-ins were soft", "walk ins were soft", "walk-ins were dead", "no walk-ins", "walk in was slow", "walk-ins were slow")


def analyze_operator_note(note: str) -> QualitativeNoteAnalysis:
    lowered = note.lower().strip()
    themes: set[str] = set()
    extracted_facts: dict[str, object] = {}
    hypothesis_hints: set[str] = set()
    observations: list[dict[str, object]] = []

    def _has_any(*phrases: str) -> bool:
        return any(phrase in lowered for phrase in phrases)

    if _has_any(*_WEATHER_ANY):
        themes.add("weather_impact")
        extracted_facts["weather_mentioned"] = True
        if _has_any(*_WEATHER_NEGATIVE):
            extracted_facts["weather_demand_impact"] = "negative"
            hypothesis_hints.add("pattern::weather_negative")
            _append_observation(
                observations,
                observation_type="weather_response",
                dependency_group="weather",
                component_scope="total",
                direction="negative",
                strength="medium",
                runtime_target="weather_profile_review",
                question_target="weather_response_pattern",
                promotion_mode="qualitative_memory",
            )
        elif _has_any(*_WEATHER_POSITIVE):
            extracted_facts["weather_demand_impact"] = "positive"
            hypothesis_hints.add("pattern::weather_positive")
            _append_observation(
                observations,
                observation_type="weather_response",
                dependency_group="weather",
                component_scope="total",
                direction="positive",
                strength="medium",
                runtime_target="weather_profile_review",
                question_target="weather_response_pattern",
                promotion_mode="qualitative_memory",
            )

    if _has_any(*_PATIO_ANY):
        themes.add("patio_constraint")
        extracted_facts["patio_mentioned"] = True
        if _has_any(*_PATIO_NEGATIVE):
            extracted_facts["patio_operating_state"] = "closed_or_constrained"
            hypothesis_hints.add("pattern::weather_patio_risk")
            _append_observation(
                observations,
                observation_type="patio_weather_response",
                dependency_group="weather",
                component_scope="patio",
                direction="negative",
                strength="high",
                runtime_target="weather_patio_profile",
                question_target="weather_patio_profile",
                promotion_mode="deterministic_profile_hint",
            )
        if _has_any(*_PATIO_POSITIVE):
            extracted_facts["patio_demand_state"] = "strong"
            hypothesis_hints.add("pattern::weather_patio_risk")
            _append_observation(
                observations,
                observation_type="patio_weather_response",
                dependency_group="weather",
                component_scope="patio",
                direction="positive",
                strength="high",
                runtime_target="weather_patio_profile",
                question_target="weather_patio_profile",
                promotion_mode="deterministic_profile_hint",
            )

    if _has_any(*_STAFFING_ANY):
        themes.add("staffing_constraint")
        extracted_facts["staffing_constraint"] = True
        hypothesis_hints.add("pattern::staffing_constraint")
        _append_observation(
            observations,
            observation_type="staffing_constraint",
            dependency_group="service_state",
            component_scope="service_capacity",
            direction="negative",
            strength="medium",
            runtime_target="service_constraints",
            question_target="staffing_pattern",
            promotion_mode="qualitative_memory",
        )

    if _has_any(*_EVENT_ANY):
        themes.add("event_impact")
        extracted_facts["event_mentioned"] = True
        if _has_any(*_EVENT_POSITIVE):
            extracted_facts["event_demand_impact"] = "positive"
            _append_observation(
                observations,
                observation_type="venue_event_response",
                dependency_group="venue",
                component_scope="total",
                direction="positive",
                strength="medium",
                runtime_target="venue_relevance",
                question_target="venue_relevance",
                promotion_mode="deterministic_relevance_flag",
            )
        hypothesis_hints.add("pattern::event_impact")

    if _has_any(*_ACCESS_ANY):
        themes.add("access_issue")
        extracted_facts["access_issue"] = True
        hypothesis_hints.add("pattern::transit_or_access_issue")
        _append_observation(
            observations,
            observation_type="access_disruption",
            dependency_group="access",
            component_scope="total",
            direction="negative",
            strength="medium",
            runtime_target="transit_relevance",
            question_target="transit_relevance",
            promotion_mode="deterministic_relevance_flag",
        )

    if _has_any(*_TRAVEL_ANY):
        themes.add("travel_pull")
        extracted_facts["travel_mentioned"] = True
        hypothesis_hints.add("pattern::hotel_or_travel_pull")
        _append_observation(
            observations,
            observation_type="travel_linked_demand",
            dependency_group="travel",
            component_scope="total",
            direction="positive",
            strength="medium",
            runtime_target="hotel_travel_relevance",
            question_target="hotel_travel_relevance",
            promotion_mode="deterministic_relevance_flag",
        )

    if _has_any(*_RESERVATION_FALLOFF_ANY):
        themes.add("reservation_falloff")
        extracted_facts["reservation_falloff"] = True
        _append_observation(
            observations,
            observation_type="reservation_falloff",
            dependency_group="reservation",
            component_scope="reserved",
            direction="negative",
            strength="medium",
            runtime_target="reservation_anchor_review",
            question_target="reservation_falloff_pattern",
            promotion_mode="qualitative_memory",
        )

    if _has_any(*_WALK_IN_SOFT_ANY):
        themes.add("walk_in_softness")
        extracted_facts["walk_in_state"] = "soft"
        _append_observation(
            observations,
            observation_type="walk_in_softness",
            dependency_group="walk_in",
            component_scope="walk_in",
            direction="negative",
            strength="medium",
            runtime_target="walk_in_mix_review",
            question_target="walk_in_mix_review",
            promotion_mode="qualitative_memory",
        )

    return QualitativeNoteAnalysis(
        themes=sorted(themes),
        extracted_facts=extracted_facts,
        hypothesis_hints=sorted(hypothesis_hints),
        observations=observations,
    )


def _observation(
    *,
    observation_type: str,
    dependency_group: str,
    component_scope: str,
    direction: str,
    strength: str,
    runtime_target: str,
    question_target: str,
    promotion_mode: str,
    summary: str,
) -> dict[str, object]:
    equation_link = equation_link_for_runtime_target(runtime_target)
    return {
        "observation_type": observation_type,
        "dependency_group": dependency_group,
        "component_scope": component_scope,
        "direction": direction,
        "strength": strength,
        "recurrence_hint": "possible_recurring",
        "runtime_target": runtime_target,
        "question_target": question_target,
        "promotion_mode": promotion_mode,
        "equation_terms": equation_link.get("equation_terms", []),
        "equation_path": equation_link.get("equation_path"),
        "equation_influence_mode": equation_link.get("influence_mode"),
        "summary": summary,
    }


def _append_observation(
    observations: list[dict[str, object]],
    *,
    observation_type: str,
    dependency_group: str,
    component_scope: str,
    direction: str,
    strength: str,
    runtime_target: str,
    question_target: str,
    promotion_mode: str,
) -> None:
    observations.append(
        _observation(
            observation_type=observation_type,
            dependency_group=dependency_group,
            component_scope=component_scope,
            direction=direction,
            strength=strength,
            runtime_target=runtime_target,
            question_target=question_target,
            promotion_mode=promotion_mode,
            summary=observation_summary(runtime_target, direction),
        )
    )


def observation_summary(runtime_target: str, direction: str) -> str:
    templates = _OBSERVATION_SUMMARY_TEMPLATES.get(str(runtime_target or ""))
    if templates:
        return templates.get(direction, templates.get("mixed", "")) or f"Operator note supports {runtime_target.replace('_', ' ')}."
    return f"Operator note supports {runtime_target.replace('_', ' ')}."
