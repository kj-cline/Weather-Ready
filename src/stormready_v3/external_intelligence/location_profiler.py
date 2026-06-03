"""AI-powered location profiling for operator onboarding.

Given a restaurant address, uses the AI provider to identify nearby demand-relevant
entities (metro stations, stadiums, hotels, universities, etc.) and derive structured
location context that shapes how external signals affect forecasts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from stormready_v3.ai.contracts import AgentModelProvider, LocationProfilingResult
from stormready_v3.domain.models import LocationContextProfile, OperatorProfile
from stormready_v3.external_intelligence.catalog import ExternalCatalogEntry
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


# Map entity demand_category to source_category for catalog seeding
_ENTITY_CATEGORY_MAP = {
    "traffic_access": "traffic_access",
    "events_venues": "events_venues",
    "tourism_hospitality": "tourism_hospitality",
    "civic_campus": "civic_campus",
    "neighborhood_demand_proxy": "neighborhood_demand_proxy",
    "incidents_safety": "incidents_safety",
}

# Map entity type to source_kind for catalog entries
_ENTITY_TYPE_TO_SOURCE_KIND = {
    "metro_station": "transit_station_monitor",
    "transit_hub": "transit_hub_monitor",
    "stadium": "venue_event_monitor",
    "arena": "venue_event_monitor",
    "convention_center": "convention_schedule_monitor",
    "university": "campus_activity_monitor",
    "hotel_cluster": "hospitality_occupancy_monitor",
    "theater": "venue_event_monitor",
    "park": "neighborhood_activity_monitor",
    "government_complex": "civic_activity_monitor",
    "hospital": "civic_activity_monitor",
    "office_complex": "commuter_flow_monitor",
}


@dataclass(slots=True)
class LocationProfileOutput:
    location_context: LocationContextProfile
    catalog_seeds: list[ExternalCatalogEntry]
    profiling_source: str  # "ai"
    reasoning: str | None = None
    raw_entities: list[dict[str, Any]] = field(default_factory=list)


class LocationProfiler:
    def __init__(self, provider: AgentModelProvider | None = None) -> None:
        self.provider = provider

    def profile_location(
        self,
        profile: OperatorProfile,
    ) -> LocationProfileOutput:
        ai_result = self._try_ai_profiling(profile)
        if ai_result is None:
            raise RuntimeError("AI location profiling did not return a result.")
        return self._build_output_from_ai(profile, ai_result)

    def _try_ai_profiling(self, profile: OperatorProfile) -> LocationProfilingResult | None:
        if self.provider is None or not self.provider.is_available():
            return None
        if not profile.canonical_address:
            return None
        try:
            return self.provider.location_profiling(
                address=profile.canonical_address,
                city=profile.city,
                neighborhood_type=profile.neighborhood_type.value if profile.neighborhood_type else None,
                lat=profile.lat,
                lon=profile.lon,
            )
        except Exception:
            return None

    def _build_output_from_ai(
        self,
        profile: OperatorProfile,
        result: LocationProfilingResult,
    ) -> LocationProfileOutput:
        location_context = LocationContextProfile(
            operator_id=profile.operator_id,
            neighborhood_archetype=profile.neighborhood_type,
            transit_relevance=result.transit_relevance,
            venue_relevance=result.venue_relevance,
            hotel_travel_relevance=result.hotel_travel_relevance,
            commuter_intensity=result.commuter_intensity,
            residential_intensity=result.residential_intensity,
            patio_sensitivity_hint=result.patio_sensitivity_hint,
            weather_sensitivity_hint=result.weather_sensitivity_hint,
            demand_volatility_hint=result.demand_volatility_hint,
        )
        catalog_seeds = self._entities_to_catalog_seeds(
            profile.operator_id,
            result.nearby_entities,
        )
        return LocationProfileOutput(
            location_context=location_context,
            catalog_seeds=catalog_seeds,
            profiling_source="ai",
            reasoning=result.reasoning,
            raw_entities=result.nearby_entities,
        )

    @staticmethod
    def _entities_to_catalog_seeds(
        operator_id: str,
        entities: list[dict[str, Any]],
    ) -> list[ExternalCatalogEntry]:
        seeds: list[ExternalCatalogEntry] = []
        for entity in entities:
            name = entity.get("name", "")
            entity_type = entity.get("type", "unknown")
            demand_category = entity.get("demand_category", "neighborhood_demand_proxy")
            source_category = _ENTITY_CATEGORY_MAP.get(demand_category, "neighborhood_demand_proxy")
            source_kind = _ENTITY_TYPE_TO_SOURCE_KIND.get(entity_type, "entity_proximity_monitor")
            # Build a stable source_name from entity name
            safe_name = name.lower().replace(" ", "_").replace("'", "").replace("-", "_")[:40]
            source_name = f"entity_{safe_name}"

            distance = entity.get("distance_hint", "unknown")
            trust = "high" if distance in {"adjacent", "1_block"} else "medium"
            cadence = "every_refresh" if distance in {"adjacent", "1_block", "2_blocks"} else "scheduled_refresh"

            seeds.append(
                ExternalCatalogEntry(
                    operator_id=operator_id,
                    source_name=source_name,
                    source_bucket="curated_local",
                    scan_scope="curated_local_scan",
                    source_category=source_category,
                    discovery_mode="ai_location_profiling",
                    source_kind=source_kind,
                    source_class="local_context",
                    trust_class=trust,
                    cadence_hint=cadence,
                    status="curated",
                    entity_label=name,
                    geo_scope="neighborhood",
                    metadata={
                        "entity_type": entity_type,
                        "distance_hint": distance,
                        "impact_note": entity.get("impact_note", ""),
                        "seed_reason": "ai_location_profiling",
                    },
                )
            )
        return seeds

    def apply_profile(
        self,
        output: LocationProfileOutput,
        operators: OperatorRepository,
    ) -> None:
        """Persist the profiled LocationContextProfile to the database."""
        operators.upsert_location_context(output.location_context)
