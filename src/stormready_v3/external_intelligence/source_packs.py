from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stormready_v3.domain.models import LocationContextProfile, OperatorProfile


DC_REGION_CITY_TOKENS = {
    "washington",
    "washington dc",
    "washington, dc",
    "district of columbia",
    "arlington",
    "alexandria",
    "bethesda",
    "silver spring",
}
PHILADELPHIA_CITY_TOKENS = {"philadelphia"}
BALTIMORE_CITY_TOKENS = {"baltimore"}
HAMPTON_ROADS_CITY_TOKENS = {
    "norfolk",
    "virginia beach",
    "hampton",
    "newport news",
    "chesapeake",
    "portsmouth",
}


@dataclass(slots=True)
class SourcePackSeed:
    source_name: str
    source_bucket: str
    scan_scope: str
    source_category: str
    discovery_mode: str
    source_kind: str
    source_class: str
    trust_class: str
    cadence_hint: str
    status: str
    entity_label: str
    geo_scope: str
    endpoint_hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _city_tokens(profile: OperatorProfile) -> set[str]:
    tokens: set[str] = set()
    for raw in (profile.city, profile.canonical_address):
        if not raw:
            continue
        lowered = raw.lower().strip()
        tokens.add(lowered)
        parts = [part.strip() for part in lowered.replace("/", ",").split(",") if part.strip()]
        tokens.update(parts)
    return tokens


def _matches_city_group(tokens: set[str], aliases: set[str]) -> bool:
    return any(token in aliases for token in tokens)


def _base_neighborhood_seed(profile: OperatorProfile, location_context: LocationContextProfile) -> SourcePackSeed:
    city_label = profile.city or "local_district"
    return SourcePackSeed(
        source_name="curated_neighborhood_proxy_pack",
        source_bucket="curated_local",
        scan_scope="curated_local_scan",
        source_category="neighborhood_demand_proxy",
        discovery_mode="curated_seed",
        source_kind="neighborhood_proxy_pack",
        source_class="local_context",
        trust_class="medium",
        cadence_hint="every_refresh",
        status="curated",
        entity_label=f"{city_label} neighborhood demand proxies",
        geo_scope="neighborhood",
        metadata={
            "neighborhood_archetype": location_context.neighborhood_archetype.value,
            "seed_reason": "baseline neighborhood demand context",
            "preferred_source_formats": ["gbfs", "geojson", "json"],
        },
    )


def _base_civic_seed(profile: OperatorProfile) -> SourcePackSeed:
    city_label = profile.city or "local_district"
    return SourcePackSeed(
        source_name="curated_civic_campus_pack",
        source_bucket="curated_local",
        scan_scope="curated_local_scan",
        source_category="civic_campus",
        discovery_mode="curated_seed",
        source_kind="civic_campus_pack",
        source_class="local_context",
        trust_class="medium",
        cadence_hint="scheduled_refresh",
        status="curated",
        entity_label=f"{city_label} civic and campus activity",
        geo_scope="district",
        metadata={
            "seed_reason": "urban dinner district grounding",
            "preferred_source_formats": ["json", "ics", "rss"],
        },
    )


def _ticketmaster_seed(city_label: str) -> SourcePackSeed:
    return SourcePackSeed(
        source_name="ticketmaster_discovery_events",
        source_bucket="curated_local",
        scan_scope="curated_local_scan",
        source_category="events_venues",
        discovery_mode="curated_seed",
        source_kind="ticketmaster_events_api",
        source_class="local_context",
        trust_class="medium",
        cadence_hint="every_refresh",
        status="curated",
        entity_label=f"{city_label} live event inventory",
        geo_scope="city",
        endpoint_hint="https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/",
        metadata={
            "official_source": True,
            "free_tier": True,
            "api_family": "rest_json",
            "seed_reason": "citywide event discovery with location filters",
        },
    )


def _tsa_seed(city_label: str) -> SourcePackSeed:
    return SourcePackSeed(
        source_name="tsa_checkpoint_travel_numbers",
        source_bucket="curated_local",
        scan_scope="curated_local_scan",
        source_category="tourism_hospitality",
        discovery_mode="curated_seed",
        source_kind="national_travel_proxy",
        source_class="local_context",
        trust_class="medium",
        cadence_hint="scheduled_refresh",
        status="curated",
        entity_label=f"{city_label} travel proxy",
        geo_scope="regional",
        endpoint_hint="https://www.tsa.gov/travel/passenger-volumes",
        metadata={
            "official_source": True,
            "free_tier": True,
            "api_family": "daily_dataset",
            "seed_reason": "broad travel demand proxy for hotel and airport-adjacent districts",
        },
    )


def build_permanent_source_seeds(
    profile: OperatorProfile,
    location_context: LocationContextProfile,
) -> list[SourcePackSeed]:
    city_label = profile.city or "local_city"
    tokens = _city_tokens(profile)
    seeds: list[SourcePackSeed] = [
        _base_neighborhood_seed(profile, location_context),
        _base_civic_seed(profile),
    ]

    if location_context.venue_relevance:
        seeds.append(_ticketmaster_seed(city_label))
    if location_context.hotel_travel_relevance:
        seeds.append(_tsa_seed(city_label))

    if _matches_city_group(tokens, DC_REGION_CITY_TOKENS):
        if location_context.transit_relevance:
            seeds.extend(
                [
                    SourcePackSeed(
                        source_name="wmata_realtime_service_alerts",
                        source_bucket="curated_local",
                        scan_scope="curated_local_scan",
                        source_category="traffic_access",
                        discovery_mode="curated_seed",
                        source_kind="gtfs_rt_service_alerts",
                        source_class="local_context",
                        trust_class="high",
                        cadence_hint="every_refresh",
                        status="curated",
                        entity_label=f"{city_label} WMATA access",
                        geo_scope="district",
                        endpoint_hint="https://developer.wmata.com/products",
                        metadata={
                            "official_source": True,
                            "free_tier": True,
                            "api_family": "gtfs_rt_rest",
                            "seed_reason": "Metro and bus disruptions materially affect dinner arrivals",
                        },
                    ),
                    SourcePackSeed(
                        source_name="ddot_tops_occupancy_permits",
                        source_bucket="curated_local",
                        scan_scope="curated_local_scan",
                        source_category="traffic_access",
                        discovery_mode="curated_seed",
                        source_kind="public_space_permit_api",
                        source_class="local_context",
                        trust_class="high",
                        cadence_hint="every_refresh",
                        status="curated",
                        entity_label=f"{city_label} curb and occupancy permits",
                        geo_scope="district",
                        endpoint_hint="https://topsapi.ddot.dc.gov/Help",
                        metadata={
                            "official_source": True,
                            "free_tier": True,
                            "api_family": "rest_json",
                            "seed_reason": "street occupancy and curb permits shift local access and walk-in friction",
                        },
                    ),
                    SourcePackSeed(
                        source_name="ddot_tops_construction_permits",
                        source_bucket="curated_local",
                        scan_scope="curated_local_scan",
                        source_category="incidents_safety",
                        discovery_mode="curated_seed",
                        source_kind="construction_permit_api",
                        source_class="local_context",
                        trust_class="high",
                        cadence_hint="event_mode_priority",
                        status="curated",
                        entity_label=f"{city_label} construction access risk",
                        geo_scope="district",
                        endpoint_hint="https://catalog.data.gov/dataset/construction-permits-via-ddot-tops",
                        metadata={
                            "official_source": True,
                            "free_tier": True,
                            "api_family": "rest_json",
                            "seed_reason": "construction and closures can suppress access or shift service posture",
                        },
                    ),
                ]
            )
        seeds.append(
            SourcePackSeed(
                source_name="capital_bikeshare_station_pressure",
                source_bucket="curated_local",
                scan_scope="curated_local_scan",
                source_category="neighborhood_demand_proxy",
                discovery_mode="curated_seed",
                source_kind="gbfs_station_status",
                source_class="local_context",
                trust_class="medium",
                cadence_hint="every_refresh",
                status="curated",
                entity_label=f"{city_label} micro-mobility pressure",
                geo_scope="district",
                endpoint_hint="https://capitalbikeshare.com/system-data",
                metadata={
                    "official_source": True,
                    "free_tier": True,
                    "api_family": "gbfs",
                    "seed_reason": "bike-share dock pressure is a useful neighborhood footfall proxy",
                },
            )
        )
        if location_context.venue_relevance or location_context.hotel_travel_relevance:
            seeds.append(
                SourcePackSeed(
                    source_name="nps_dc_events",
                    source_bucket="curated_local",
                    scan_scope="curated_local_scan",
                    source_category="civic_campus",
                    discovery_mode="curated_seed",
                    source_kind="nps_events_api",
                    source_class="local_context",
                    trust_class="medium",
                    cadence_hint="scheduled_refresh",
                    status="curated",
                    entity_label=f"{city_label} park and civic events",
                    geo_scope="district",
                    endpoint_hint="https://www.nps.gov/subjects/developer/api-documentation.htm",
                    metadata={
                        "official_source": True,
                        "free_tier": True,
                        "api_family": "rest_json",
                        "seed_reason": "National Mall and federal-core activity can lift or crowd nearby dinner demand",
                    },
                )
            )
        return seeds

    if _matches_city_group(tokens, PHILADELPHIA_CITY_TOKENS):
        if location_context.transit_relevance:
            seeds.extend(
                [
                    SourcePackSeed(
                        source_name="septa_realtime_arrivals_and_alerts",
                        source_bucket="curated_local",
                        scan_scope="curated_local_scan",
                        source_category="traffic_access",
                        discovery_mode="curated_seed",
                        source_kind="septa_realtime_api",
                        source_class="local_context",
                        trust_class="high",
                        cadence_hint="every_refresh",
                        status="curated",
                        entity_label="Philadelphia SEPTA access",
                        geo_scope="district",
                        endpoint_hint="https://wwww.septa.org/developer/",
                        metadata={
                            "official_source": True,
                            "free_tier": True,
                            "api_family": "rest_json",
                            "seed_reason": "SEPTA arrivals and disruptions affect near-term covers in transit-led districts",
                        },
                    ),
                    SourcePackSeed(
                        source_name="philly_street_lane_closures",
                        source_bucket="curated_local",
                        scan_scope="curated_local_scan",
                        source_category="traffic_access",
                        discovery_mode="curated_seed",
                        source_kind="open_data_lane_closure_feed",
                        source_class="local_context",
                        trust_class="medium",
                        cadence_hint="every_refresh",
                        status="curated",
                        entity_label="Philadelphia lane and access closures",
                        geo_scope="district",
                        endpoint_hint="https://opendataphilly.org/datasets/street-lane-closures/",
                        metadata={
                            "official_source": True,
                            "free_tier": True,
                            "api_family": "open_data",
                            "seed_reason": "street closures create access drag and can change walk-in behavior",
                        },
                    ),
                ]
            )
        seeds.append(
            SourcePackSeed(
                source_name="indego_station_pressure",
                source_bucket="curated_local",
                scan_scope="curated_local_scan",
                source_category="neighborhood_demand_proxy",
                discovery_mode="curated_seed",
                source_kind="bike_share_live_data",
                source_class="local_context",
                trust_class="medium",
                cadence_hint="every_refresh",
                status="curated",
                entity_label="Philadelphia neighborhood mobility pressure",
                geo_scope="district",
                endpoint_hint="https://www.rideindego.com/about/data/",
                metadata={
                    "official_source": True,
                    "free_tier": True,
                    "api_family": "geojson_gbfs",
                    "seed_reason": "Indego dock flow can proxy district-level activity around dinner service",
                },
            )
        )
        return seeds

    if _matches_city_group(tokens, BALTIMORE_CITY_TOKENS):
        if location_context.transit_relevance:
            seeds.append(
                SourcePackSeed(
                    source_name="mta_maryland_developer_resources",
                    source_bucket="curated_local",
                    scan_scope="curated_local_scan",
                    source_category="traffic_access",
                    discovery_mode="curated_seed",
                    source_kind="transit_open_data_portal",
                    source_class="local_context",
                    trust_class="high",
                    cadence_hint="every_refresh",
                    status="curated",
                    entity_label="Baltimore MTA access",
                    geo_scope="district",
                    endpoint_hint="https://www.mta.maryland.gov/developer-resources",
                    metadata={
                        "official_source": True,
                        "free_tier": True,
                        "api_family": "gtfs_gtfs_rt",
                        "seed_reason": "rail and bus disruption data is the cleanest free structured access signal in Baltimore",
                    },
                )
            )
        return seeds

    if _matches_city_group(tokens, HAMPTON_ROADS_CITY_TOKENS):
        if location_context.transit_relevance:
            seeds.append(
                SourcePackSeed(
                    source_name="hampton_roads_transit_gtfs_rt",
                    source_bucket="curated_local",
                    scan_scope="curated_local_scan",
                    source_category="traffic_access",
                    discovery_mode="curated_seed",
                    source_kind="gtfs_rt_service_alerts",
                    source_class="local_context",
                    trust_class="high",
                    cadence_hint="every_refresh",
                    status="curated",
                    entity_label="Hampton Roads Transit access",
                    geo_scope="district",
                    endpoint_hint="https://gtfs.gohrt.com/",
                    metadata={
                        "official_source": True,
                        "free_tier": True,
                        "api_family": "gtfs_rt",
                        "seed_reason": "HRT GTFS-RT gives a clean access signal for Norfolk and nearby dinner districts",
                    },
                )
            )
        return seeds

    if location_context.transit_relevance:
        seeds.append(
            SourcePackSeed(
                source_name="generic_transit_open_data_pack",
                source_bucket="curated_local",
                scan_scope="curated_local_scan",
                source_category="traffic_access",
                discovery_mode="curated_seed",
                source_kind="regional_transit_open_data",
                source_class="local_context",
                trust_class="medium",
                cadence_hint="every_refresh",
                status="curated",
                entity_label=f"{city_label} transit access",
                geo_scope="district",
                metadata={
                    "official_source": True,
                    "free_tier": True,
                    "preferred_source_formats": ["gtfs_rt", "rest_json"],
                    "seed_reason": "fallback structured transit source family when city-specific mapping is not yet configured",
                },
            )
        )
    return seeds


def build_broad_discovery_seeds(
    profile: OperatorProfile,
    location_context: LocationContextProfile,
    *,
    refresh_reason: str,
) -> list[SourcePackSeed]:
    city_label = profile.city or "local_city"
    discovery_common = {
        "refresh_reason": refresh_reason,
        "official_only": True,
        "preferred_formats": ["json", "geojson", "gtfs_rt", "arcgis_rest", "socrata"],
    }
    seeds: list[SourcePackSeed] = [
        SourcePackSeed(
            source_name="broad_official_traffic_discovery",
            source_bucket="broad_proxy",
            scan_scope="broad_proxy_scan",
            source_category="traffic_access",
            discovery_mode="broad_discovery",
            source_kind="official_open_data_discovery",
            source_class="local_context",
            trust_class="low",
            cadence_hint="scheduled_refresh",
            status="discovered",
            entity_label=f"{city_label} official traffic discovery",
            geo_scope="city",
            metadata={
                **discovery_common,
                "preferred_domains": ["data.cityofnewyork.us", "opendata.dc.gov", "opendataphilly.org", "opendata.baltimorecity.gov"],
            },
        ),
        SourcePackSeed(
            source_name="broad_official_event_discovery",
            source_bucket="broad_proxy",
            scan_scope="broad_proxy_scan",
            source_category="events_venues",
            discovery_mode="broad_discovery",
            source_kind="official_event_calendar_discovery",
            source_class="local_context",
            trust_class="low",
            cadence_hint="scheduled_refresh",
            status="discovered",
            entity_label=f"{city_label} official events discovery",
            geo_scope="city",
            metadata={
                **discovery_common,
                "preferred_domains": ["developer.ticketmaster.com", "nps.gov", "local venue calendars"],
            },
        ),
        SourcePackSeed(
            source_name="broad_official_mobility_proxy_discovery",
            source_bucket="broad_proxy",
            scan_scope="broad_proxy_scan",
            source_category="neighborhood_demand_proxy",
            discovery_mode="broad_discovery",
            source_kind="mobility_proxy_discovery",
            source_class="local_context",
            trust_class="low",
            cadence_hint="scheduled_refresh",
            status="discovered",
            entity_label=f"{city_label} mobility proxy discovery",
            geo_scope="city",
            metadata={
                **discovery_common,
                "preferred_domains": ["capitalbikeshare.com", "rideindego.com", "gbfs feeds"],
            },
        ),
    ]
    if location_context.hotel_travel_relevance:
        seeds.append(
            SourcePackSeed(
                source_name="broad_travel_and_hospitality_discovery",
                source_bucket="broad_proxy",
                scan_scope="broad_proxy_scan",
                source_category="tourism_hospitality",
                discovery_mode="broad_discovery",
                source_kind="travel_proxy_discovery",
                source_class="local_context",
                trust_class="low",
                cadence_hint="scheduled_refresh",
                status="discovered",
                entity_label=f"{city_label} travel proxy discovery",
                geo_scope="regional",
                metadata={
                    **discovery_common,
                    "preferred_domains": ["tsa.gov", "airport open data", "tourism open data"],
                },
            )
        )
    if location_context.venue_relevance:
        seeds.append(
            SourcePackSeed(
                source_name="broad_civic_and_campus_discovery",
                source_bucket="broad_proxy",
                scan_scope="broad_proxy_scan",
                source_category="civic_campus",
                discovery_mode="broad_discovery",
                source_kind="civic_campus_discovery",
                source_class="local_context",
                trust_class="low",
                cadence_hint="scheduled_refresh",
                status="discovered",
                entity_label=f"{city_label} civic and campus discovery",
                geo_scope="city",
                metadata={
                    **discovery_common,
                    "preferred_domains": ["nps.gov", "university calendars", "municipal event portals"],
                },
            )
        )
    return seeds
