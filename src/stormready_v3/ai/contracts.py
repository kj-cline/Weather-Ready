from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ExternalSourceGovernanceItem:
    source_name: str
    recommended_category: str | None = None
    recommended_action: str | None = None
    priority_score: float | None = None
    confidence: str | None = None
    cadence_hint: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LocationProfilingResult:
    neighborhood_archetype: str | None = None
    transit_relevance: bool = False
    venue_relevance: bool = False
    hotel_travel_relevance: bool = False
    commuter_intensity: float | None = None
    residential_intensity: float | None = None
    patio_sensitivity_hint: float | None = None
    weather_sensitivity_hint: float | None = None
    demand_volatility_hint: float | None = None
    nearby_entities: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None


class AgentModelProvider(Protocol):
    def is_available(self) -> bool: ...

    def structured_json_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 800,
    ) -> dict[str, Any] | None: ...

    def external_source_governance(
        self,
        *,
        operator_context: dict[str, Any],
        source_candidates: list[dict[str, Any]],
    ) -> list[ExternalSourceGovernanceItem] | None: ...

    def location_profiling(
        self,
        *,
        address: str,
        city: str | None,
        neighborhood_type: str | None,
        lat: float | None,
        lon: float | None,
    ) -> LocationProfilingResult | None: ...
