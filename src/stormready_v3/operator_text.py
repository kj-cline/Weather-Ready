from __future__ import annotations

import re
from typing import Any


DRIVER_LABELS: dict[str, str] = {
    "booked_reservation_anchor": "the reservation book gives a direct demand anchor",
    "bikeshare_station_density": "bikeshare activity is giving a live neighborhood demand read",
    "brooklyn_weather_reference": "weather is pushing demand away from a normal night",
    "ddot_access_friction": "nearby road closures may make access slower",
    "ddot_permit_cluster": "there is heavier permit activity nearby than usual",
    "ddot_permitted_event_pull": "permitted street activity may add local pull",
    "district_access_incident": "nearby access incident adds downside risk",
    "district_bikeshare_pressure": "district bikeshare demand is running hotter than usual",
    "extreme_cold": "extreme cold may suppress demand",
    "gray_suppression": "a gray weather pattern is softening demand",
    "hrt_access_friction": "transit access looks more fragile than usual",
    "hrt_network_alerts": "live HRT alerts add access risk",
    "kennedy_center_event_cluster": "nearby venue demand is running strong",
    "external_scan_learning": "outside conditions have been moving the same way lately",
    "operator_service_plan": "your saved service plan is overriding a normal night",
    "nws_active_alert": "a live NWS alert adds weather risk",
    "precip_overlap": "rain overlaps dinner hours",
    "snow_risk": "snow may suppress demand",
    "septa_access_friction": "SEPTA access friction may suppress covers",
    "septa_network_alerts": "live SEPTA alerts add access risk",
    "transit_disruption": "transit disruption adds downside risk",
    "weather_alert": "a weather alert raises disruption risk",
    "weather_disruption_risk": "weather may disrupt normal service",
    "weather_signature_learning": "recent weather misses are changing how much weather seems to matter here",
    "baseline service window pattern": "the usual dinner pattern is still the main signal",
}

UNCERTAINTY_LABELS: dict[str, str] = {
    "target definition still stabilizing": "there is not enough recent restaurant history yet",
    "component truth is still developing": "recent operating detail is still thin",
    "If you have reservation, walk-in, or waitlist detail for this service, logging it will improve future forecasts.": "reservation, walk-in, or waitlist detail would help tighten this",
    "service state may still need operator confirmation": "the service plan still needs confirmation",
    "treat this forecast as directional rather than precise": "treat this number as directional, not exact",
}

SERVICE_STATE_LABELS: dict[str, str] = {
    "normal_service": "normal service",
    "partial_service": "partial service",
    "patio_closed_or_constrained": "patio limited service",
    "private_event_or_buyout": "private event or buyout",
    "holiday_modified_service": "holiday-modified service",
    "weather_disruption_service": "weather disruption",
    "closed": "closed service",
}

SOURCE_TYPE_LABELS: dict[str, str] = {
    "conversation_capture": "your note",
    "conversation_note": "your note",
    "operator_note": "your note",
    "operator_manual": "operator input",
    "connected_truth": "connected system",
    "weather_signal": "weather signal",
}

STATUS_PIP_LABELS: dict[str, dict[str, str]] = {
    "plan": {
        "submitted": "Plan saved",
        "pending": "Plan due",
        "stale": "Review plan",
        "not_required": "",
    },
    "learning": {
        "none": "",
        "open_question": "Need your answer",
        "overdue_question": "Still need your answer",
    },
    "actuals": {
        "not_due": "",
        "due": "Actuals due",
        "recorded": "Actuals saved",
        "overdue": "Actuals due",
    },
    "watchout": {
        "none": "",
        "low_confidence": "Needs attention",
        "service_state_risk": "Confirm service",
        "material_uncertainty": "Check again",
    },
}

ATTENTION_SECTION_LABELS: dict[str, str] = {
    "latest_material_change": "What changed",
    "current_operational_watchout": "Needs attention",
    "pending_operator_action": "What I need from you",
    "current_uncertainty": "Still in play",
    "best_next_question": "Need your answer",
}

FOCUS_SECTION_LABELS: dict[str, str] = {
    "pending_operator_action": "Tonight focus",
    "current_operational_watchout": "Needs attention tonight",
    "current_uncertainty": "Still in play tonight",
    "latest_material_change": "What changed",
    "best_next_question": "Need your answer",
}

FORECAST_HERO_EYEBROWS: dict[str, str] = {
    "tonight": "Tonight",
    "tomorrow": "Tomorrow",
}

SERVICE_PLAN_TEXT: dict[str, str] = {
    "header_kicker": "Upcoming nights",
    "header_title": "Review service plans",
    "header_sub": "Save known closures, buyouts, early closes, patio limits, or confirm a night is running normally.",
    "due_banner": "{count} night{plural} still need a saved plan.",
    "reviewed_banner": "These nights are already planned. You can still revise any date below.",
    "tab_aria_label": "Plan dates",
    "reviewed_status": "Reviewed",
    "due_status": "Plan due",
    "field_service_state": "Planned service state",
    "field_planned_total_covers": "Planned total covers",
    "field_estimated_reduction_pct": "Estimated reduction %",
    "field_note": "Plan note",
    "planned_total_placeholder_optional": "Optional if you know it",
    "planned_total_placeholder_default": "Optional",
    "reduction_placeholder_optional": "Optional",
    "reduction_placeholder_locked": "Use abnormal service state first",
    "note_placeholder": "Examples: buyout at 7 PM, patio closed for repairs, closing one hour early, wedding group expected.",
    "helper_normal": "Saving normal service marks this night ready.",
    "helper_adjusted": "If you know the total covers or likely reduction, add it here. Otherwise save the service change and note.",
    "save_button": "Save Plan",
    "saving_button": "Saving plan...",
}

SELECTED_NIGHT_PLAN_TEXT: dict[str, str] = {
    "header_kicker": "Selected night",
    "header_sub": "Use this any time for a known closure, buyout, early close, patio constraint, or other confirmed operating change.",
    "helper_normal": "Saving normal service confirms the night as planned.",
    "helper_adjusted": "If you know the total covers or likely reduction, add it here. Otherwise save the service change and note.",
    "close_button": "Close",
    "save_button": "Save Plan",
    "saving_button": "Saving plan...",
}

ACTUALS_TEXT: dict[str, str] = {
    "header_kicker": "Dinner actuals",
    "header_title": "Log the missing totals",
    "header_sub_singular": "This recent dinner service still needs actual covers. Logging it tightens the next forecasts.",
    "header_sub_plural": "These recent dinner services still need actual covers. Logging them tightens the next forecasts.",
    "tab_aria_label": "Dinner dates",
    "forecast_status": "Forecast {covers} covers",
    "entry_banner": "Logging {date}. We forecast about {covers} covers.",
    "field_total_covers": "Total covers",
    "field_service_state": "Service state",
    "field_reserved_covers": "Reserved covers",
    "field_walk_in_covers": "Walk-in covers",
    "field_outside_covers": "Outside covers",
    "field_note": "Operator note",
    "note_placeholder": "Anything unusual about service, patio, weather, staffing, or traffic?",
    "validation_reserved_walkins": "Reserved plus walk-ins cannot exceed total covers.",
    "validation_outside": "Outside covers cannot exceed total covers.",
    "helper_default": "Save the real totals here. Use the note only for extra context that may matter later.",
    "save_button": "Save Actuals",
    "saving_button": "Saving actuals...",
}

COCKPIT_CHECKPOINT_TEXT: dict[str, dict[str, str | None]] = {
    "tonight_not_published": {
        "title": "Tonight is not published",
        "detail": "No actionable dinner forecast is published for the selected reference date.",
        "action_hint": "Run a dinner refresh before relying on tonight's plan.",
    },
    "tonight_abnormal": {
        "title": "Tonight may run abnormally",
        "detail": "{service_date} is flagged {service_state} with confidence {confidence_tier}.",
        "action_hint": "Confirm whether service is limited or disrupted before dinner.",
    },
    "tonight_confidence_weak": {
        "title": "Tonight confidence is weak",
        "detail": "Expected {forecast_expected} covers, but confidence is {confidence_tier}.{driver_clause}",
        "action_hint": None,
    },
    "tonight_confidence_weather_action": {
        "title": "",
        "detail": "",
        "action_hint": "Check access or weather issues and refresh before locking staffing.",
    },
    "tonight_confidence_generic_action": {
        "title": "",
        "detail": "",
        "action_hint": "Check notes, refresh, and connector truth before locking staffing.",
    },
    "missing_actuals": {
        "title": "Dinner actuals are missing",
        "detail": "{count} recent dinner service{plural} still need actual cover logging. Most recent missing night: {service_date}.",
        "action_hint": "Log the missing dinner totals so learning and confidence stay clean.",
    },
    "pending_corrections": {
        "title": "Corrections need review",
        "detail": "{count} staged correction suggestion{plural} are waiting.",
        "action_hint": "Review and apply or reject staged corrections.",
    },
    "service_state_suggestion": {
        "title": "Service-state suggestion is open",
        "detail": "{service_date} may need to be treated as {service_state} based on {source_type}.",
        "action_hint": "Confirm if service will be limited so the forecast and learning stay aligned.",
    },
    "automation_stale": {
        "title": "Automation looks stale",
        "detail": "The latest completed refresh is older than the normal freshness window.",
        "action_hint": "Run a refresh or inspect the supervisor/runtime health.",
    },
    "planning_window": {
        "title": "A planning window opens today",
        "detail": "The {planning_window_label} planning window should be reviewed for {reference_date}.",
        "action_hint": "Review upcoming nights and save known closures, buyouts, or partial-service plans.",
    },
    "no_urgent_action": {
        "title": "No urgent operator action",
        "detail": "Tonight is published and there are no open corrections or missing dinner actuals.",
        "action_hint": "Use the dashboard for pacing, then log dinner actuals after service.",
    },
}

INTERNAL_OPERATOR_TOKENS: tuple[str, ...] = (
    "brooklyn_",
    "governor_",
    "prediction_case",
    "reference_status",
    "workflow_priority_action",
    "suggested_learning_question",
    "operator_attention_summary",
    "learning_agenda",
    "equation_learning_state",
    "prediction_equation",
    "current turn policy",
    "runtime_bridge::",
    "forecast strip",
)


def driver_label(driver: str) -> str:
    return DRIVER_LABELS.get(driver, driver.replace("_", " "))


def service_state_label(service_state: str) -> str:
    return SERVICE_STATE_LABELS.get(service_state, service_state.replace("_", " "))


def source_type_label(source_type: str) -> str:
    return SOURCE_TYPE_LABELS.get(source_type, source_type.replace("_", " "))


def uncertainty_label(uncertainty: str) -> str:
    return UNCERTAINTY_LABELS.get(uncertainty, uncertainty)


def plan_service_state_options() -> list[dict[str, str]]:
    return [
        {"value": "normal_service", "label": SERVICE_STATE_LABELS["normal_service"]},
        {"value": "partial_service", "label": "partial or early close"},
        {"value": "patio_closed_or_constrained", "label": "patio closed or constrained"},
        {"value": "private_event_or_buyout", "label": SERVICE_STATE_LABELS["private_event_or_buyout"]},
        {"value": "holiday_modified_service", "label": SERVICE_STATE_LABELS["holiday_modified_service"]},
        {"value": "closed", "label": SERVICE_STATE_LABELS["closed"]},
    ]


def actual_service_state_options() -> list[dict[str, str]]:
    return [
        {"value": "normal_service", "label": SERVICE_STATE_LABELS["normal_service"]},
        {"value": "partial_service", "label": SERVICE_STATE_LABELS["partial_service"]},
        {"value": "patio_closed_or_constrained", "label": "patio constrained"},
        {"value": "private_event_or_buyout", "label": SERVICE_STATE_LABELS["private_event_or_buyout"]},
        {"value": "holiday_modified_service", "label": SERVICE_STATE_LABELS["holiday_modified_service"]},
        {"value": "weather_disruption_service", "label": SERVICE_STATE_LABELS["weather_disruption_service"]},
        {"value": "closed", "label": SERVICE_STATE_LABELS["closed"]},
    ]


def operator_text_contract() -> dict[str, Any]:
    return {
        "serviceStateOptions": {
            "plan": plan_service_state_options(),
            "actual": actual_service_state_options(),
        },
        "statusPipLabels": {
            kind: dict(labels)
            for kind, labels in STATUS_PIP_LABELS.items()
        },
        "attentionLabels": {
            "sectionLabels": dict(ATTENTION_SECTION_LABELS),
            "focusSectionLabels": dict(FOCUS_SECTION_LABELS),
            "defaultMomentLabel": "Right now",
        },
        "forecastLabels": {
            "heroEyebrows": dict(FORECAST_HERO_EYEBROWS),
        },
        "workflow": {
            "servicePlan": dict(SERVICE_PLAN_TEXT),
            "selectedNightPlan": dict(SELECTED_NIGHT_PLAN_TEXT),
            "actuals": dict(ACTUALS_TEXT),
        },
    }


def cockpit_checkpoint_copy(checkpoint_key: str, **kwargs: Any) -> dict[str, str | None]:
    template = COCKPIT_CHECKPOINT_TEXT[checkpoint_key]
    formatted: dict[str, str | None] = {}
    for field_name, raw_value in template.items():
        if raw_value is None:
            formatted[field_name] = None
            continue
        formatted[field_name] = str(raw_value).format(**kwargs)
    return formatted


def communication_payload(
    *,
    category: str,
    what_is_true_now: str | None = None,
    why_it_matters: str | None = None,
    what_i_need_from_you: str | None = None,
    what_is_still_uncertain: str | None = None,
    one_question: str | None = None,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "what_is_true_now": str(what_is_true_now or "").strip() or None,
        "why_it_matters": str(why_it_matters or "").strip() or None,
        "what_i_need_from_you": str(what_i_need_from_you or "").strip() or None,
        "what_is_still_uncertain": str(what_is_still_uncertain or "").strip() or None,
        "one_question": str(one_question or "").strip() or None,
        "facts": dict(facts or {}),
    }


def render_communication_payload(
    payload: dict[str, Any] | None,
    *,
    include_question: bool = True,
) -> str:
    if not isinstance(payload, dict):
        return ""
    ordered_parts = [
        _format_payload_part(payload.get("what_is_true_now")),
        _format_payload_part(payload.get("why_it_matters")),
        _format_payload_part(payload.get("what_i_need_from_you")),
        _format_payload_part(payload.get("what_is_still_uncertain")),
    ]
    if include_question:
        ordered_parts.append(_format_payload_part(payload.get("one_question"), allow_question=True))
    text = " ".join(part for part in ordered_parts if part)
    return translate_operator_text(text)


def communication_text_from_state(
    value: dict[str, Any] | None,
    *,
    include_question: bool = True,
) -> str:
    if not isinstance(value, dict):
        return ""
    return render_communication_payload(
        value.get("communication_payload"),
        include_question=include_question,
    ).strip()


def communication_text_from_payload(
    payload: dict[str, Any] | None,
    *,
    include_question: bool = True,
) -> str:
    return render_communication_payload(payload, include_question=include_question).strip()


def _format_payload_part(value: Any, *, allow_question: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text[-1] in ".!?":
        return text
    if allow_question and text.lower().startswith(
        ("what ", "why ", "how ", "when ", "does ", "do ", "is ", "are ", "should ", "could ", "can ")
    ):
        return f"{text}?"
    return f"{text}."


def contains_internal_operator_terms(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in INTERNAL_OPERATOR_TOKENS)


def translate_operator_text(text: str) -> str:
    translated = str(text or "")
    for raw, label in DRIVER_LABELS.items():
        translated = re.sub(rf"\b{re.escape(raw)}\b", label, translated)
    for raw, label in UNCERTAINTY_LABELS.items():
        translated = translated.replace(raw, label)
    phrase_map = {
        "forecast strip": "forecast",
        " the strip ": " the forecast ",
        "Next:": "",
        "Watchout:": "",
        "Watch for:": "",
        "Watch next:": "",
        "What could still move this:": "Still in play:",
        "nothing material changed from the last refresh": "nothing meaningful changed since the last update",
        "nothing material changed from the last publish": "nothing meaningful changed since the last update",
        "The next meaningful forecast moves later in the week are": "Later in the week, the biggest forecast changes are",
        "harder to call than usual": "is still settling",
        "very hard to call": "is still settling",
        "harder to call": "is still settling",
        "fairly steady": "fairly settled",
        "very steady": "well settled",
        "steady": "settled",
        "event_mode_refresh+governor_actionable_posture": "recent update",
        "confidence_drop+governor_actionable_posture": "recent update",
        "material_change+posture_change": "recent update",
        "confidence_drop": "recent update",
        "governor_actionable_posture": "recent update",
        "governor_low_confidence_attention": "recent update",
        "governor_service_state_attention": "recent service update",
        "prediction_case": "forecast",
        "reference_status": "",
        "reference_model": "",
        "close the loop on last service": "finish last night's record and tighten the next forecast",
    }
    for raw, label in phrase_map.items():
        translated = translated.replace(raw, label)
    translated = re.sub(r"\bis running with low forecast confidence\b", "is still settling", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bwas running with low forecast confidence\b", "was still settling", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\blow forecast confidence\b", "still settling", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\blow confidence\b", "still settling", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bvery low confidence\b", "still settling", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bmedium confidence\b", "fairly settled", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bhigh confidence\b", "well settled", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bvery high confidence\b", "very settled", translated, flags=re.IGNORECASE)
    translated = re.sub(
        r"\b([A-Z]?[A-Za-z0-9 ,'\-/]+?) is the main driver\b",
        r"Main driver: \1",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(r"\bstrip\b", "forecast", translated)
    translated = _dedupe_sentences(translated)
    translated = _limit_questions(translated, max_questions=1)
    translated = _capitalize_sentences(translated)
    translated = re.sub(r"\s+([.,!?;:])", r"\1", translated)
    translated = re.sub(r"\s+", " ", translated).strip()
    return translated


def _dedupe_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = re.sub(r"[^a-z0-9]+", "", part.lower())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(part.strip())
    return " ".join(cleaned)


def _limit_questions(text: str, *, max_questions: int) -> str:
    if max_questions < 1:
        return text.replace("?", ".")
    pieces = re.split(r"(\?)", text)
    question_count = 0
    rebuilt: list[str] = []
    for piece in pieces:
        if piece == "?":
            question_count += 1
            rebuilt.append("?" if question_count <= max_questions else ".")
            continue
        rebuilt.append(piece)
    return "".join(rebuilt)


def _capitalize_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    capitalized: list[str] = []
    for part in parts:
        stripped = part.strip()
        if stripped and stripped[0].islower():
            stripped = stripped[0].upper() + stripped[1:]
        capitalized.append(stripped)
    return " ".join(part for part in capitalized if part)


def planning_band(
    *,
    forecast_expected: int,
    forecast_low: int,
    forecast_high: int,
    confidence_tier: str | None,
) -> tuple[int, int]:
    raw_half_width = max(abs(forecast_expected - forecast_low), abs(forecast_high - forecast_expected))
    compression = {
        "very_high": 0.48,
        "high": 0.54,
        "medium": 0.60,
        "low": 0.66,
        "very_low": 0.72,
    }.get(str(confidence_tier or "").lower(), 0.60)
    minimum_half_width = max(6, int(round(max(forecast_expected, 1) * 0.08)))
    planning_half_width = max(minimum_half_width, int(round(raw_half_width * compression)))
    plan_low = max(forecast_low, forecast_expected - planning_half_width)
    plan_high = min(forecast_high, forecast_expected + planning_half_width)
    if plan_low > plan_high:
        return forecast_low, forecast_high
    return int(plan_low), int(plan_high)


def planning_band_label(
    *,
    forecast_expected: int,
    forecast_low: int,
    forecast_high: int,
    confidence_tier: str | None,
) -> str:
    plan_low, plan_high = planning_band(
        forecast_expected=forecast_expected,
        forecast_low=forecast_low,
        forecast_high=forecast_high,
        confidence_tier=confidence_tier,
    )
    return f"{plan_low}-{plan_high}"


def forecast_headline(
    *,
    service_window: str,
    posture: str,
    service_state: str,
) -> str:
    window_label = service_window.replace("_", " ")
    if service_state != "normal_service":
        return f"{window_label.title()} service may change"
    if posture == "ELEVATED":
        return f"{window_label.title()} looks busier than usual"
    if posture == "SOFT":
        return f"{window_label.title()} looks softer than usual"
    if posture == "DISRUPTED":
        return f"{window_label.title()} may be disrupted"
    return f"{window_label.title()} looks close to normal"


def forecast_summary(
    *,
    forecast_expected: int,
    forecast_low: int,
    forecast_high: int,
    confidence_tier: str,
    service_state: str,
    top_drivers: list[str] | None = None,
    major_uncertainties: list[str] | None = None,
) -> str:
    plan_low, plan_high = planning_band(
        forecast_expected=forecast_expected,
        forecast_low=forecast_low,
        forecast_high=forecast_high,
        confidence_tier=confidence_tier,
    )
    clauses = [f"Plan around {plan_low}-{plan_high} covers, centered near {forecast_expected}."]
    if service_state != "normal_service":
        clauses.append(f"Service is planned as {service_state_label(service_state)}.")
    if top_drivers:
        clauses.append(f"Main reason: {driver_label(top_drivers[0])}.")
    return " ".join(clauses)


def forecast_vs_usual_text(delta_pct: int | float | None, *, include_suffix: bool = False) -> str | None:
    if delta_pct is None:
        return None
    rounded = int(round(float(delta_pct)))
    if rounded > 0:
        label = f"+{rounded}%"
    elif rounded < 0:
        label = f"{rounded}%"
    else:
        label = "0%"
    if include_suffix:
        return f"{label} vs usual"
    return label


def forecast_recent_change_text(
    *,
    previous_expected: int | None,
    current_expected: int | None,
    snapshot_reason: str | None,
    compact: bool = False,
) -> str | None:
    if previous_expected is not None and current_expected is not None and previous_expected != current_expected:
        delta = current_expected - previous_expected
        if compact:
            prefix = "+" if delta > 0 else ""
            return f"Revised {prefix}{delta} since last publish"
        direction = "up" if delta > 0 else "down"
        return f"Revised {direction} from {previous_expected} to {current_expected} since last publish."

    reason_tokens = {token for token in str(snapshot_reason or "").split("+") if token}
    if "service_state_change" in reason_tokens:
        return "Service changed since last publish." if compact else "Service plan changed since last publish."
    if "confidence_drop" in reason_tokens:
        return "Confidence fell since last publish." if compact else "Confidence dropped since last publish."
    if "interval_widened" in reason_tokens:
        return "Range widened since last publish."
    if "posture_change" in reason_tokens:
        return "Outlook changed since last publish."
    if "event_mode_refresh" in reason_tokens:
        return "Refreshed from a live event update."
    return None


def notification_payload_with_text(payload: dict[str, Any]) -> dict[str, Any]:
    top_drivers = list(payload.get("top_drivers") or [])
    major_uncertainties = list(payload.get("major_uncertainties") or [])
    payload = dict(payload)
    payload["headline"] = forecast_headline(
        service_window=str(payload.get("service_window", "service")),
        posture=str(payload.get("posture", "NORMAL")),
        service_state=str(payload.get("service_state", "normal_service")),
    )
    payload["summary"] = forecast_summary(
        forecast_expected=int(payload.get("forecast_expected", 0)),
        forecast_low=int(payload.get("forecast_low", 0)),
        forecast_high=int(payload.get("forecast_high", 0)),
        confidence_tier=str(payload.get("confidence_tier", "low")),
        service_state=str(payload.get("service_state", "normal_service")),
        top_drivers=top_drivers,
        major_uncertainties=major_uncertainties,
    )
    return payload
