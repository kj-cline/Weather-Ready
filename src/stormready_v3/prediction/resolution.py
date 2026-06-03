from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.connectors.mapping import normalize_connector_truth
from stormready_v3.domain.enums import ServiceState
from stormready_v3.domain.models import NormalizedSignal, ResolvedServiceState, ResolvedTarget


REALIZED_TOTAL_FIELDS = ("realized_total_covers", "total_covers")
COMPONENT_FIELDS = (
    "realized_reserved_covers",
    "realized_walk_in_covers",
    "realized_waitlist_converted_covers",
    "inside_covers",
    "outside_covers",
)
RESERVATION_DETAIL_FIELDS = (
    "booked_reservation_covers",
    "reservation_no_show_covers",
    "reservation_cancellation_covers",
)
CONTROL_FIELDS = ("service_state",)

FIELD_PRIORITY_BY_TYPE: dict[str, tuple[str, ...]] = {
    "realized_total_covers": ("pos_connector", "reservation_connector"),
    "realized_reserved_covers": ("pos_connector", "reservation_connector"),
    "realized_walk_in_covers": ("pos_connector",),
    "realized_waitlist_converted_covers": ("reservation_connector", "pos_connector"),
    "inside_covers": ("pos_connector", "reservation_connector"),
    "outside_covers": ("pos_connector", "reservation_connector"),
    "booked_reservation_covers": ("reservation_connector", "pos_connector"),
    "reservation_no_show_covers": ("reservation_connector", "pos_connector"),
    "reservation_cancellation_covers": ("reservation_connector", "pos_connector"),
    "service_state": ("reservation_connector", "pos_connector"),
}


@dataclass(slots=True)
class SourceResolutionResult:
    authoritative_fields: dict[str, Any] = field(default_factory=dict)
    resolved_source_summary: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def _pick_realized_total_source(
    manual_truth: dict[str, Any],
    connector_truths: list[ConnectorTruthCandidate],
) -> tuple[str | None, Any]:
    if "realized_total_covers" in manual_truth:
        return "manual_truth", manual_truth["realized_total_covers"]

    normalized_truths = [normalize_connector_truth(c) for c in connector_truths]
    pos_candidate = next(
        (
            c
            for c in normalized_truths
            if c.system_type == "pos_connector"
            and "realized_total_covers" in c.canonical_fields
        ),
        None,
    )
    if pos_candidate is not None:
        return pos_candidate.system_name, pos_candidate.canonical_fields["realized_total_covers"]

    generic_candidate = next(
        (c for c in normalized_truths if "realized_total_covers" in c.canonical_fields),
        None,
    )
    if generic_candidate is not None:
        return generic_candidate.system_name, generic_candidate.canonical_fields["realized_total_covers"]

    return None, None


def _pick_field_value(
    field_name: str,
    *,
    manual_truth: dict[str, Any],
    normalized_truths,
) -> tuple[str | None, Any, list[str]]:
    if field_name in manual_truth:
        return "manual_truth", manual_truth[field_name], []

    preferred_types = FIELD_PRIORITY_BY_TYPE.get(field_name, tuple())
    ordered_candidates = sorted(
        normalized_truths,
        key=lambda truth: (
            preferred_types.index(truth.system_type)
            if truth.system_type in preferred_types
            else len(preferred_types)
        ),
    )
    chosen_source: str | None = None
    chosen_value: Any = None
    conflicts: list[str] = []
    for candidate in ordered_candidates:
        if field_name not in candidate.canonical_fields:
            continue
        value = candidate.canonical_fields[field_name]
        if chosen_source is None:
            chosen_source = candidate.system_name
            chosen_value = value
            continue
        if chosen_value != value:
            conflicts.append(field_name)
    return chosen_source, chosen_value, conflicts


def resolve_sources(
    *,
    manual_truth: dict[str, Any] | None = None,
    connector_truths: list[ConnectorTruthCandidate] | None = None,
) -> SourceResolutionResult:
    manual_truth = manual_truth or {}
    connector_truths = connector_truths or []
    normalized_truths = [normalize_connector_truth(c) for c in connector_truths]
    result = SourceResolutionResult()

    total_source, total_value = _pick_realized_total_source(manual_truth, connector_truths)
    if total_source is not None:
        result.authoritative_fields["realized_total_covers"] = total_value
        result.resolved_source_summary["realized_total_covers"] = total_source

    for field_name in (*COMPONENT_FIELDS, *RESERVATION_DETAIL_FIELDS, *CONTROL_FIELDS):
        source_name, value, conflicts = _pick_field_value(
            field_name,
            manual_truth=manual_truth,
            normalized_truths=normalized_truths,
        )
        if source_name is None:
            continue
        result.authoritative_fields[field_name] = value
        result.resolved_source_summary[field_name] = source_name
        for conflict in conflicts:
            if conflict not in result.conflicts:
                result.conflicts.append(conflict)

    return result


def resolve_target(
    *,
    manual_truth: dict[str, Any] | None = None,
    connector_truths: list[ConnectorTruthCandidate] | None = None,
) -> ResolvedTarget:
    source_result = resolve_sources(manual_truth=manual_truth, connector_truths=connector_truths)
    available_components = [field for field in COMPONENT_FIELDS if field in source_result.authoritative_fields]

    confidence = "low"
    if "realized_total_covers" in source_result.authoritative_fields:
        confidence = "high" if not source_result.conflicts else "medium"
    elif available_components:
        confidence = "low_medium"
    elif "booked_reservation_covers" in source_result.authoritative_fields:
        confidence = "low_medium"

    return ResolvedTarget(
        target_name="realized_total_covers",
        available_components=available_components,
        target_definition_confidence=confidence,
    )


def resolve_service_state(
    *,
    operator_state: ServiceState | None = None,
    operator_note_state: ServiceState | None = None,
    operator_note_reason: str | None = None,
    connected_state: ServiceState | None = None,
    calendar_state: ServiceState | None = None,
    disruption_suggestion: ServiceState | None = None,
) -> ResolvedServiceState:
    if operator_state is not None:
        return ResolvedServiceState(
            service_state=operator_state,
            state_confidence="high",
            state_source="operator",
            state_resolution_reason="explicit operator input",
            learning_eligibility="normal" if operator_state is ServiceState.NORMAL else "excluded",
        )
    if operator_note_state is not None:
        return ResolvedServiceState(
            service_state=operator_note_state,
            state_confidence="medium",
            state_source="operator_note",
            state_resolution_reason=operator_note_reason or "recent operator note",
            learning_eligibility="normal" if operator_note_state is ServiceState.NORMAL else "excluded",
        )
    if connected_state is not None:
        return ResolvedServiceState(
            service_state=connected_state,
            state_confidence="high",
            state_source="connected_truth",
            state_resolution_reason="connected system truth",
            learning_eligibility="normal" if connected_state is ServiceState.NORMAL else "excluded",
        )
    if calendar_state is not None:
        return ResolvedServiceState(
            service_state=calendar_state,
            state_confidence="medium",
            state_source="calendar_rule",
            state_resolution_reason="calendar or holiday rule",
            learning_eligibility="normal" if calendar_state is ServiceState.NORMAL else "excluded",
        )
    if disruption_suggestion is not None:
        return ResolvedServiceState(
            service_state=disruption_suggestion,
            state_confidence="low",
            state_source="disruption_suggestion",
            state_resolution_reason="weather or disruption suggestion",
            learning_eligibility="excluded" if disruption_suggestion is not ServiceState.NORMAL else "normal",
        )
    return ResolvedServiceState(
        service_state=ServiceState.NORMAL,
        state_confidence="medium",
        state_source="default",
        state_resolution_reason="no abnormal service input",
        learning_eligibility="normal",
    )


def suggest_service_state_from_signals(
    normalized_signals: list[NormalizedSignal],
) -> ServiceState | None:
    if any(signal.role.value == "service_state_modifier" for signal in normalized_signals):
        return ServiceState.WEATHER_DISRUPTION
    return None
