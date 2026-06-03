from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.domain.enums import ServiceState


CANONICAL_CONNECTOR_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "realized_total_covers": ("realized_total_covers", "total_covers", "guest_count", "covers"),
    "realized_reserved_covers": ("realized_reserved_covers", "reserved_covers", "seated_reservation_covers"),
    "realized_walk_in_covers": ("realized_walk_in_covers", "walkin_covers", "walk_in_covers"),
    "realized_waitlist_converted_covers": (
        "realized_waitlist_converted_covers",
        "waitlist_covers",
        "waitlist_converted_covers",
    ),
    "inside_covers": ("inside_covers",),
    "outside_covers": ("outside_covers", "patio_covers"),
    "booked_reservation_covers": ("booked_reservation_covers", "booked_covers", "reservation_party_size"),
    "reservation_no_show_covers": ("reservation_no_show_covers", "no_show_covers"),
    "reservation_cancellation_covers": ("reservation_cancellation_covers", "cancellation_covers"),
    "service_state": ("service_state", "operating_state", "shift_state"),
}


@dataclass(slots=True)
class NormalizedConnectorTruth:
    system_name: str
    system_type: str
    canonical_fields: dict[str, Any]
    field_quality: dict[str, str]


def _coerce_value(canonical_name: str, value: Any) -> Any:
    if value in {None, ""}:
        return None
    if canonical_name == "service_state":
        if isinstance(value, ServiceState):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            for state in ServiceState:
                if state.value == normalized:
                    return state
    if canonical_name.endswith("_covers") or canonical_name in {"inside_covers", "outside_covers"}:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return value
    return value


def normalize_connector_truth(candidate: ConnectorTruthCandidate) -> NormalizedConnectorTruth:
    canonical_fields: dict[str, Any] = {}
    field_quality: dict[str, str] = {}
    for canonical_name, aliases in CANONICAL_CONNECTOR_FIELD_ALIASES.items():
        for alias in aliases:
            if alias not in candidate.fields:
                continue
            coerced = _coerce_value(canonical_name, candidate.fields[alias])
            if coerced is None:
                continue
            canonical_fields[canonical_name] = coerced
            field_quality[canonical_name] = candidate.field_quality.get(alias, "mapped")
            break
    return NormalizedConnectorTruth(
        system_name=candidate.system_name,
        system_type=candidate.system_type,
        canonical_fields=canonical_fields,
        field_quality=field_quality,
    )


def normalize_import_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for canonical_name, aliases in CANONICAL_CONNECTOR_FIELD_ALIASES.items():
        for alias in aliases:
            alias_key = alias.lower()
            if alias_key in lowered:
                coerced = _coerce_value(canonical_name, lowered[alias_key])
                if coerced is None:
                    break
                normalized[canonical_name] = coerced
                break
    return normalized
