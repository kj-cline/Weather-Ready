from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.sources.contracts import SourcePayload


@dataclass(slots=True)
class MockWeatherSource:
    source_name: str = "mock_weather"
    source_class: str = "weather_forecast"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={
                "operator_id": operator_id,
                "conditions": "clear",
                "weather_code": 0,
                "temp_f": 62,
                "temperature_high": 68,
                "temperature_low": 48,
                "precip_prob": 0.0,
                "apparent_temp_7pm": 61.0,
                "precip_dinner_max": 0.0,
                "wind_speed_mph": 8.0,
                "cloudcover_bin": 0.0,
                "sunrise": f"{service_date.isoformat() if service_date else at.date().isoformat()}T06:15:00-04:00",
                "sunset": f"{service_date.isoformat() if service_date else at.date().isoformat()}T19:35:00-04:00",
            },
            freshness="fresh",
            source_bucket="weather_core",
            scan_scope="weather_core_scan",
            provenance={"mode": "mock", "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
        )


@dataclass(slots=True)
class MockDetailedWeatherSource:
    source_name: str = "mock_detailed_weather"
    source_class: str = "weather_forecast"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        service_date = service_date or at.date()
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={
                "operator_id": operator_id,
                "conditions": "partly cloudy",
                "weather_code": 2,
                "temp_f": 67,
                "temperature_high": 72,
                "temperature_low": 55,
                "precip_prob": 0.05,
                "apparent_temp_7pm": 67.0,
                "precip_dinner_max": 0.0,
                "wind_speed_mph": 9.0,
                "precip_type_code": 0,
                "weekday": float(service_date.isoweekday()),
                "year_code": 1.0,
                "cloudcover_dinner_mean": 48.0,
                "cloudcover_bin": 1.0,
                "precip_lunch": 0.0,
                "sunrise": f"{service_date.isoformat()}T06:15:00-04:00",
                "sunset": f"{service_date.isoformat()}T19:35:00-04:00",
            },
            freshness="fresh",
            source_bucket="weather_core",
            scan_scope="weather_core_scan",
            provenance={"mode": "mock_detailed", "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
        )


@dataclass(slots=True)
class MockTransitSource:
    source_name: str = "mock_transit"
    source_class: str = "transit_disruption"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={
                "operator_id": operator_id,
                "severity": "major",
                "service_reduction": True,
                "source_category": "traffic_access",
            },
            freshness="fresh",
            source_bucket="curated_local",
            scan_scope="curated_local_scan",
            provenance={"mode": "mock", "source_bucket": "curated_local", "scan_scope": "curated_local_scan"},
        )


@dataclass(slots=True)
class MockCuratedLocalSource:
    source_name: str = "mock_curated_local_scan"
    source_class: str = "local_context"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        weekday = (service_date or at.date()).weekday()
        signals: list[dict[str, object]] = [
            {
                "signal_type": "metro_station_flow",
                "dependency_group": "access",
                "role": "confidence_mover",
                "estimated_pct": 0.0,
                "trust_level": "high",
                "direction": "uncertain",
                "details": {
                    "station": "Foggy Bottom-GWU",
                    "flow_state": "elevated" if weekday < 4 else "mixed",
                    "source_category": "traffic_access",
                },
            }
        ]
        if weekday in {3, 4, 5}:
            signals.append(
                {
                    "signal_type": "venue_cluster_pull",
                    "dependency_group": "venue",
                    "role": "posture_mover",
                    "estimated_pct": 0.03,
                    "trust_level": "high",
                    "direction": "up",
                    "details": {
                        "zone": "kennedy_center_corridor",
                        "intensity": "elevated",
                        "source_category": "events_venues",
                    },
                }
            )
        if weekday in {0, 1, 2}:
            signals.append(
                {
                    "signal_type": "campus_schedule_pull",
                    "dependency_group": "civic",
                    "role": "confidence_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "uncertain",
                    "details": {
                        "zone": "gwu_civic_corridor",
                        "intensity": "mixed",
                        "source_category": "civic_campus",
                    },
                }
            )
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={"operator_id": operator_id, "signals": signals},
            freshness="fresh",
            source_bucket="curated_local",
            scan_scope="curated_local_scan",
            provenance={"mode": "mock", "source_bucket": "curated_local", "scan_scope": "curated_local_scan"},
        )


@dataclass(slots=True)
class MockBroadProxySource:
    source_name: str = "mock_broad_proxy_scan"
    source_class: str = "local_context"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        weekday = (service_date or at.date()).weekday()
        signals: list[dict[str, object]] = [
            {
                "signal_type": "district_proxy_footfall",
                "dependency_group": "proxy_demand",
                "role": "confidence_mover",
                "estimated_pct": 0.0,
                "trust_level": "low",
                "direction": "uncertain",
                "details": {
                    "proxy_kind": "district_footfall",
                    "state": "soft" if weekday in {0, 1} else "mixed",
                    "source_category": "neighborhood_demand_proxy",
                },
            },
            {
                "signal_type": "broad_city_event_noise",
                "dependency_group": "proxy_event",
                "role": "posture_mover",
                "estimated_pct": 0.01 if weekday in {4, 5} else 0.0,
                "trust_level": "low",
                "direction": "up" if weekday in {4, 5} else "uncertain",
                "details": {
                    "proxy_kind": "citywide_event_proxy",
                    "state": "active" if weekday in {4, 5} else "quiet",
                    "source_category": "events_venues",
                },
            },
            {
                "signal_type": "city_incident_noise",
                "dependency_group": "proxy_incident",
                "role": "confidence_mover",
                "estimated_pct": 0.0,
                "trust_level": "low",
                "direction": "uncertain",
                "details": {
                    "proxy_kind": "city_incident_proxy",
                    "state": "active" if weekday in {2, 3} else "quiet",
                    "source_category": "incidents_safety",
                },
            },
        ]
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={"operator_id": operator_id, "signals": signals},
            freshness="fresh",
            source_bucket="broad_proxy",
            scan_scope="broad_proxy_scan",
            provenance={"mode": "mock", "source_bucket": "broad_proxy", "scan_scope": "broad_proxy_scan"},
        )


@dataclass(slots=True)
class MockNarrativeContextSource:
    source_name: str = "mock_narrative_context"
    source_class: str = "local_news"
    source_bucket: str = "broad_proxy"

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        del profile
        resolved_date = service_date or at.date()
        narrative = _select_demo_narrative(resolved_date)
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            payload={
                "operator_id": operator_id,
                "narrative_text": narrative,
            },
            freshness="fresh",
            source_bucket=self.source_bucket,
            scan_scope="broad_proxy_scan",
            provenance={"mode": "mock", "source_bucket": self.source_bucket, "scan_scope": "broad_proxy_scan"},
        )


def _select_demo_narrative(service_date: date) -> str:
    narratives = [
        "Tonight's neighborhood arts walk is expected to bring steady foot traffic from 5pm to 9pm, with several galleries and cafes extending hours near the dinner corridor.",
        "Metro maintenance will close one nearby entrance after 6pm and reroute riders two blocks east; transit officials expect moderate access friction through the dinner period.",
        "A community observance and family program is scheduled in the district this evening, with organizers expecting a larger early-dinner crowd before the main service.",
    ]
    return narratives[service_date.toordinal() % len(narratives)]
