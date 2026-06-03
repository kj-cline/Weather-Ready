from __future__ import annotations

from typing import Any


_PREDICTION_EQUATION = {
    "model_midpoint_formula": "midpoint_before_plan = baseline_component * (1 + seasonal_pct + weather_pct + context_pct)",
    "reservation_anchor_formula": "if reservation_anchor: midpoint_before_plan = reserved_expected + flex_baseline * (1 + seasonal_pct + weather_pct + context_pct)",
    "final_midpoint_formula": "forecast_midpoint = operator_plan_override(midpoint_before_plan)",
    "engine_reference": "prediction/engine.py::run_forecast",
}


_TERM_METADATA: dict[str, dict[str, Any]] = {
    "baseline_component": {
        "formula_role": "base level of expected covers before modeled deltas",
        "description": "Weekly baselines provide the starting cover level that seasonal, weather, and context adjustments act on.",
        "digest_field": "baseline",
        "value_type": "covers",
    },
    "seasonal_pct": {
        "formula_role": "calendar / seasonality adjustment",
        "description": "A bounded percentage adjustment from seasonal date priors before plan override.",
        "digest_field": "seasonal_pct",
        "value_type": "pct",
    },
    "weather_pct": {
        "formula_role": "weather-driven percentage adjustment",
        "description": "A bounded percentage adjustment from live weather signals, Brooklyn blending, and learned weather calibration.",
        "digest_field": "weather_pct",
        "value_type": "pct",
    },
    "context_pct": {
        "formula_role": "outside-context percentage adjustment",
        "description": "A bounded percentage adjustment from transit, venue, hotel/travel, and other relevant outside signals.",
        "digest_field": "context_pct",
        "value_type": "pct",
    },
    "reservation_anchor": {
        "formula_role": "branch that protects booked demand and weather-shapes only the flex portion",
        "description": "When booked reservation truth exists, the midpoint uses reserved_expected + flex forecast instead of shaping the whole baseline.",
        "digest_field": "component_split",
        "value_type": "branch",
    },
    "operator_plan_override": {
        "formula_role": "final explicit operator override after model expectation",
        "description": "Future closures, planned totals, or planned reductions can override the model midpoint after all modeled adjustments.",
        "digest_field": "operator_plan_adjustment",
        "value_type": "override",
    },
}


_DEFAULT_LINK = {
    "equation_terms": [],
    "equation_path": "qualitative memory only",
    "influence_mode": "qualitative_only",
    "priority": 60,
    "explanation": "This learning target does not directly mutate the midpoint formula.",
}


_RUNTIME_TARGET_LINKS: dict[str, dict[str, Any]] = {
    "weather_patio_profile": {
        "equation_terms": ["weather_pct"],
        "equation_path": "weather_pct -> profile_weather_sensitivity_multiplier / patio_weather_multiplier",
        "influence_mode": "direct_bounded_runtime_input",
        "priority": 45,
        "explanation": "Confirmed patio/weather behavior can update bounded weather-profile hints used inside weather_pct.",
    },
    "weather_profile_review": {
        "equation_terms": ["weather_pct"],
        "equation_path": "weather_pct -> qualitative interpretation only",
        "influence_mode": "qualitative_only",
        "priority": 46,
        "explanation": "Weather response notes inform explanations and future review, but do not directly change the formula yet.",
    },
    "transit_relevance": {
        "equation_terms": ["context_pct"],
        "equation_path": "context_pct -> location_relevance_weight(access/transit)",
        "influence_mode": "direct_bounded_runtime_input",
        "priority": 35,
        "explanation": "Confirmed transit relevance changes how access-related external signals weight into context_pct.",
    },
    "venue_relevance": {
        "equation_terms": ["context_pct"],
        "equation_path": "context_pct -> location_relevance_weight(venue/events)",
        "influence_mode": "direct_bounded_runtime_input",
        "priority": 35,
        "explanation": "Confirmed venue relevance changes how event-related external signals weight into context_pct.",
    },
    "hotel_travel_relevance": {
        "equation_terms": ["context_pct"],
        "equation_path": "context_pct -> location_relevance_weight(travel/hotel)",
        "influence_mode": "direct_bounded_runtime_input",
        "priority": 35,
        "explanation": "Confirmed hotel/travel relevance changes how travel-related external signals weight into context_pct.",
    },
    "walk_in_mix_review": {
        "equation_terms": ["weather_pct", "reservation_anchor"],
        "equation_path": "weather_pct and reservation_anchor -> qualitative demand-mix review only",
        "influence_mode": "qualitative_only",
        "priority": 48,
        "explanation": "Walk-in softness may imply demand-mix drift, but it stays qualitative until a safe deterministic path exists.",
    },
    "reservation_anchor_review": {
        "equation_terms": ["reservation_anchor"],
        "equation_path": "reservation_anchor -> qualitative reservation-falloff review only",
        "influence_mode": "qualitative_only",
        "priority": 47,
        "explanation": "Reservation falloff may explain misses around booked demand, but it does not directly retune the anchor yet.",
    },
    "service_constraints": {
        "equation_terms": ["operator_plan_override"],
        "equation_path": "operator_plan_override -> qualitative service-constraint review only",
        "influence_mode": "qualitative_only",
        "priority": 50,
        "explanation": "Staffing or service constraints are tracked for explanations and future workflow prompts, not direct midpoint edits.",
    },
}


def equation_link_for_runtime_target(runtime_target: str | None) -> dict[str, Any]:
    if not runtime_target:
        return dict(_DEFAULT_LINK)
    return dict(_RUNTIME_TARGET_LINKS.get(str(runtime_target), _DEFAULT_LINK))


def equation_priority_for_runtime_target(runtime_target: str | None) -> int:
    link = equation_link_for_runtime_target(runtime_target)
    return int(link.get("priority") or _DEFAULT_LINK["priority"])


def prediction_equation_contract() -> dict[str, Any]:
    return {
        **_PREDICTION_EQUATION,
        "terms": [
            {
                "equation_term": equation_term,
                "formula_role": metadata["formula_role"],
                "description": metadata["description"],
                "value_type": metadata["value_type"],
            }
            for equation_term, metadata in _TERM_METADATA.items()
        ],
    }


def summarize_equation_learning_state(
    *,
    open_hypotheses: list[dict[str, Any]],
    learning_agenda: list[dict[str, Any]],
    recent_learning_decisions: list[dict[str, Any]],
    engine_digests: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for hypothesis in open_hypotheses:
        value = hypothesis.get("hypothesis_value") or {}
        if not isinstance(value, dict):
            continue
        for equation_term in value.get("equation_terms", []) or []:
            state = states.setdefault(str(equation_term), _empty_state(str(equation_term)))
            state["open_hypotheses"].append(str(hypothesis.get("hypothesis_key") or ""))
            if value.get("equation_path"):
                state["paths_under_review"].append(str(value.get("equation_path")))
            if value.get("equation_influence_mode"):
                state["influence_modes"].append(str(value.get("equation_influence_mode")))

    for item in learning_agenda:
        hypothesis_key = str(item.get("hypothesis_key") or "")
        if not hypothesis_key:
            continue
        matching = next((h for h in open_hypotheses if str(h.get("hypothesis_key") or "") == hypothesis_key), None)
        value = (matching or {}).get("hypothesis_value") or {}
        if not isinstance(value, dict):
            continue
        for equation_term in value.get("equation_terms", []) or []:
            state = states.setdefault(str(equation_term), _empty_state(str(equation_term)))
            state["agenda_keys"].append(str(item.get("agenda_key") or ""))

    for decision in recent_learning_decisions:
        runtime_target = decision.get("runtime_target")
        link = equation_link_for_runtime_target(str(runtime_target) if runtime_target else None)
        for equation_term in link.get("equation_terms", []) or []:
            state = states.setdefault(str(equation_term), _empty_state(str(equation_term)))
            state["recent_decisions"].append(
                {
                    "decision_type": decision.get("decision_type"),
                    "status": decision.get("status"),
                    "runtime_target": runtime_target,
                }
            )
            if decision.get("equation_path"):
                state["paths_under_review"].append(str(decision.get("equation_path")))
            if decision.get("equation_influence_mode"):
                state["influence_modes"].append(str(decision.get("equation_influence_mode")))

    for equation_term, state in list(states.items()):
        state["open_hypotheses"] = sorted({item for item in state["open_hypotheses"] if item})
        state["agenda_keys"] = sorted({item for item in state["agenda_keys"] if item})
        state["paths_under_review"] = sorted({item for item in state["paths_under_review"] if item})
        state["influence_modes"] = sorted({item for item in state["influence_modes"] if item})
        state["current_engine_state"] = _summarize_engine_state_for_term(
            equation_term=equation_term,
            engine_digests=engine_digests or [],
        )

    return sorted(states.values(), key=lambda item: item["equation_term"])


def _empty_state(equation_term: str) -> dict[str, Any]:
    metadata = _TERM_METADATA.get(equation_term, {})
    return {
        "equation_term": equation_term,
        "formula_role": metadata.get("formula_role"),
        "term_description": metadata.get("description"),
        "open_hypotheses": [],
        "agenda_keys": [],
        "paths_under_review": [],
        "influence_modes": [],
        "recent_decisions": [],
        "current_engine_state": None,
    }


def _summarize_engine_state_for_term(
    *,
    equation_term: str,
    engine_digests: list[dict[str, Any]],
) -> dict[str, Any] | None:
    metadata = _TERM_METADATA.get(equation_term)
    if metadata is None or not engine_digests:
        return None

    digest_field = metadata.get("digest_field")
    if equation_term in {"seasonal_pct", "weather_pct", "context_pct"} and isinstance(digest_field, str):
        rows = [
            (str(digest.get("service_date") or ""), float(digest[digest_field]))
            for digest in engine_digests
            if digest.get(digest_field) is not None
        ]
        if not rows:
            return None
        values = [value for _, value in rows]
        return {
            "value_type": "pct",
            "sample_size": len(values),
            "latest": round(values[0], 4),
            "avg": round(sum(values) / len(values), 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "service_dates": [service_date for service_date, _ in rows[:4] if service_date],
        }

    if equation_term == "reservation_anchor":
        anchored = [
            digest
            for digest in engine_digests
            if isinstance(digest.get("component_split"), dict)
        ]
        if not anchored:
            return {
                "value_type": "branch",
                "active_count": 0,
                "sample_size": len(engine_digests),
            }
        latest_component = anchored[0].get("component_split") or {}
        return {
            "value_type": "branch",
            "active_count": len(anchored),
            "sample_size": len(engine_digests),
            "service_dates": [str(digest.get("service_date") or "") for digest in anchored[:4] if digest.get("service_date")],
            "latest_reserved": latest_component.get("reserved"),
            "latest_flex": latest_component.get("flex"),
        }

    if equation_term == "operator_plan_override":
        overridden = [
            digest
            for digest in engine_digests
            if digest.get("operator_plan_adjustment") is not None
        ]
        if not overridden:
            return {
                "value_type": "override",
                "active_count": 0,
                "sample_size": len(engine_digests),
            }
        latest_adjustment = overridden[0].get("operator_plan_adjustment") or {}
        return {
            "value_type": "override",
            "active_count": len(overridden),
            "sample_size": len(engine_digests),
            "service_dates": [str(digest.get("service_date") or "") for digest in overridden[:4] if digest.get("service_date")],
            "latest_applied": latest_adjustment.get("applied"),
        }

    if equation_term == "baseline_component" and isinstance(digest_field, str):
        rows = [
            (str(digest.get("service_date") or ""), float(digest[digest_field]))
            for digest in engine_digests
            if digest.get(digest_field) is not None
        ]
        if not rows:
            return None
        values = [value for _, value in rows]
        return {
            "value_type": "covers",
            "sample_size": len(values),
            "latest": round(values[0], 1),
            "avg": round(sum(values) / len(values), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "service_dates": [service_date for service_date, _ in rows[:4] if service_date],
        }

    return None
