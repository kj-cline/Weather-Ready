from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import asin, cos, radians, sin, sqrt
import re
from typing import Any
from urllib.error import HTTPError

from stormready_v3.config.settings import (
    CAPITAL_BIKESHARE_GBFS_URL,
    DDOT_TOPS_BASE_URL,
    DDOT_TOPS_LICENSE_KEY,
    HRT_ALERTS_URL,
    INDEGO_GBFS_URL,
    SEPTA_ALERTS_URL,
)
from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.sources.contracts import SourcePayload
from stormready_v3.sources.http import JsonHttpClient, UrllibJsonClient, build_url


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
HAMPTON_ROADS_CITY_TOKENS = {
    "norfolk",
    "virginia beach",
    "hampton",
    "newport news",
    "chesapeake",
    "portsmouth",
}

CURATED_SOURCE_BUCKET = "curated_local"
CURATED_SCAN_SCOPE = "curated_local_scan"


def _profile_tokens(profile: OperatorProfile | None) -> set[str]:
    if profile is None:
        return set()
    tokens: set[str] = set()
    for raw in (profile.city, profile.canonical_address):
        if not raw:
            continue
        lowered = str(raw).lower().strip()
        tokens.add(lowered)
        parts = [part.strip() for part in lowered.replace("/", ",").split(",") if part.strip()]
        tokens.update(parts)
    return tokens


def _city_match(profile: OperatorProfile | None, aliases: set[str]) -> bool:
    tokens = _profile_tokens(profile)
    return any(token in aliases for token in tokens)


def _local_today(profile: OperatorProfile | None, at: datetime) -> date:
    if profile is not None and profile.timezone:
        try:
            from zoneinfo import ZoneInfo

            return at.astimezone(ZoneInfo(profile.timezone)).date()
        except Exception:
            pass
    return at.astimezone(UTC).date()


def _unavailable_payload(
    *,
    source_name: str,
    source_class: str,
    operator_id: str,
    at: datetime,
    service_date: date | None,
    service_window: ServiceWindow | None,
    reason: str,
    provider: str,
) -> SourcePayload:
    return SourcePayload(
        source_name=source_name,
        source_class=source_class,
        retrieved_at=at,
        payload={"operator_id": operator_id, "available": False, "signals": []},
        freshness="unavailable",
        service_date=service_date,
        service_window=service_window.value if service_window else None,
        source_bucket=CURATED_SOURCE_BUCKET,
        scan_scope=CURATED_SCAN_SCOPE,
        provenance={
            "mode": "live",
            "provider": provider,
            "reason": reason,
            "source_bucket": CURATED_SOURCE_BUCKET,
            "scan_scope": CURATED_SCAN_SCOPE,
        },
    )


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    return 2 * radius_miles * asin(sqrt(a))


def _gbfs_feed_url(root: dict[str, Any], feed_name: str) -> str | None:
    data = root.get("data") or {}
    for language_payload in data.values():
        feeds = language_payload.get("feeds")
        if not isinstance(feeds, list):
            continue
        for feed in feeds:
            if str(feed.get("name")) == feed_name and feed.get("url"):
                return str(feed["url"])
    return None


def _translation_text(payload: dict[str, Any] | None, fallback: str = "") -> str:
    translations = (payload or {}).get("translation")
    if isinstance(translations, list):
        for item in translations:
            text = item.get("text")
            if text:
                return str(text)
    return fallback


def _parse_date_candidates(raw_text: str | None) -> list[date]:
    if not raw_text:
        return []
    text = str(raw_text)
    patterns = [
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}-\d{1,2}-\d{2,4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]
    found: list[date] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d"):
                try:
                    found.append(datetime.strptime(match, fmt).date())
                    break
                except ValueError:
                    continue
    return found


def _coerce_json_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


@dataclass(slots=True)
class GBFSStationPressureSource:
    http_client: JsonHttpClient
    root_feed_url: str
    city_aliases: set[str]
    source_name: str
    entity_label: str
    system_name: str
    source_category: str = "neighborhood_demand_proxy"
    source_class: str = "local_context"
    radius_miles: float = 0.75

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        if profile is None or profile.lat is None or profile.lon is None:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="missing_location",
                provider=self.system_name,
            )
        if not _city_match(profile, self.city_aliases):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="outside_supported_region",
                provider=self.system_name,
            )
        local_today = _local_today(profile, at)
        if service_date is not None and service_date != local_today:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="same_day_only_source",
                provider=self.system_name,
            )

        root = self.http_client.get_json(self.root_feed_url)
        station_information_url = _gbfs_feed_url(root, "station_information")
        station_status_url = _gbfs_feed_url(root, "station_status")
        if not station_information_url or not station_status_url:
            raise ValueError(f"{self.system_name} GBFS feed missing station endpoints")

        station_information = self.http_client.get_json(station_information_url)
        station_status = self.http_client.get_json(station_status_url)
        info_rows = {
            str(row.get("station_id")): row
            for row in _coerce_json_list((station_information.get("data") or {}).get("stations"))
        }
        status_rows = {
            str(row.get("station_id")): row
            for row in _coerce_json_list((station_status.get("data") or {}).get("stations"))
        }

        nearby: list[dict[str, Any]] = []
        for station_id, info in info_rows.items():
            try:
                station_lat = float(info.get("lat"))
                station_lon = float(info.get("lon"))
            except (TypeError, ValueError):
                continue
            distance = _haversine_miles(float(profile.lat), float(profile.lon), station_lat, station_lon)
            if distance > self.radius_miles:
                continue
            status = status_rows.get(station_id, {})
            capacity = int(info.get("capacity") or status.get("num_bikes_available", 0) or 0) + int(
                status.get("num_docks_available", 0) or 0
            )
            if capacity <= 0:
                continue
            bikes = int(status.get("num_bikes_available", 0) or 0)
            docks = int(status.get("num_docks_available", 0) or 0)
            nearby.append(
                {
                    "station_id": station_id,
                    "name": info.get("name") or station_id,
                    "distance_miles": round(distance, 3),
                    "capacity": capacity,
                    "bikes": bikes,
                    "docks": docks,
                    "bike_ratio": bikes / capacity,
                    "dock_ratio": docks / capacity,
                }
            )

        if len(nearby) < 2:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="insufficient_nearby_stations",
                provider=self.system_name,
            )

        low_bike_count = sum(1 for row in nearby if row["bike_ratio"] <= 0.2)
        low_dock_count = sum(1 for row in nearby if row["dock_ratio"] <= 0.2)
        active_pressure_share = max(low_bike_count, low_dock_count) / max(1, len(nearby))
        scarce_type = "bike_scarcity" if low_bike_count >= low_dock_count else "dock_scarcity"

        signals: list[dict[str, Any]] = [
            {
                "signal_type": "bikeshare_station_density",
                "dependency_group": "proxy_demand",
                "role": "confidence_mover",
                "estimated_pct": 0.0,
                "trust_level": "low" if len(nearby) < 4 else "medium",
                "direction": "uncertain",
                "details": {
                    "source_category": self.source_category,
                    "entity_label": self.entity_label,
                    "station_count": len(nearby),
                    "closest_station": nearby[0]["name"],
                    "pressure_type": scarce_type,
                    "active_pressure_share": round(active_pressure_share, 3),
                },
            }
        ]
        if len(nearby) >= 3 and active_pressure_share >= 0.5:
            signals.append(
                {
                    "signal_type": "district_bikeshare_pressure",
                    "dependency_group": "proxy_demand",
                    "role": "numeric_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "low" if active_pressure_share < 0.7 else "medium",
                    "direction": "up",
                    "details": {
                        "source_category": self.source_category,
                        "entity_label": self.entity_label,
                        "station_count": len(nearby),
                        "pressure_type": scarce_type,
                        "active_pressure_share": round(active_pressure_share, 3),
                        "nearby_stations": [row["name"] for row in nearby[:5]],
                    },
                }
            )

        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload={
                "operator_id": operator_id,
                "available": True,
                "signals": signals,
                "station_count": len(nearby),
                "pressure_type": scarce_type,
                "active_pressure_share": round(active_pressure_share, 3),
            },
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket=CURATED_SOURCE_BUCKET,
            scan_scope=CURATED_SCAN_SCOPE,
            provenance={
                "mode": "live",
                "provider": self.system_name,
                "url": self.root_feed_url,
                "source_bucket": CURATED_SOURCE_BUCKET,
                "scan_scope": CURATED_SCAN_SCOPE,
            },
        )


@dataclass(slots=True)
class SEPTAAlertSource:
    http_client: JsonHttpClient
    source_name: str = "septa_realtime_arrivals_and_alerts"
    source_class: str = "local_context"
    alerts_url: str = SEPTA_ALERTS_URL

    @classmethod
    def with_default_client(cls) -> "SEPTAAlertSource":
        return cls(http_client=UrllibJsonClient())

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        if profile is None or not _city_match(profile, PHILADELPHIA_CITY_TOKENS):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="outside_supported_region",
                provider="septa",
            )
        if service_date is not None and service_date != _local_today(profile, at):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="same_day_only_source",
                provider="septa",
            )

        rows = _coerce_json_list(self.http_client.get_json(self.alerts_url))
        active_rows: list[dict[str, Any]] = []
        severe_count = 0
        for row in rows:
            advisory_message = row.get("advisory_message")
            detour_message = row.get("detour_message")
            current_message = row.get("current_message")
            if not any([advisory_message, detour_message, current_message]):
                continue
            message = " ".join(str(value or "") for value in [advisory_message, detour_message, current_message]).lower()
            if any(term in message for term in ("sinkhole", "construction", "detour", "closure", "track work")):
                severe_count += 1
            active_rows.append(row)

        signals: list[dict[str, Any]] = []
        if active_rows:
            signals.append(
                {
                    "signal_type": "septa_network_alerts",
                    "dependency_group": "access",
                    "role": "confidence_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "down",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "SEPTA city network",
                        "active_alert_count": len(active_rows),
                        "severe_alert_count": severe_count,
                        "sample_routes": [str(row.get("route_name")) for row in active_rows[:5]],
                    },
                }
            )
        if severe_count >= 3:
            signals.append(
                {
                    "signal_type": "septa_access_friction",
                    "dependency_group": "access",
                    "role": "numeric_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "down",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "SEPTA access friction",
                        "active_alert_count": len(active_rows),
                        "severe_alert_count": severe_count,
                    },
                }
            )

        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload={
                "operator_id": operator_id,
                "available": True,
                "signals": signals,
                "active_alert_count": len(active_rows),
                "severe_alert_count": severe_count,
            },
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket=CURATED_SOURCE_BUCKET,
            scan_scope=CURATED_SCAN_SCOPE,
            provenance={
                "mode": "live",
                "provider": "septa",
                "url": self.alerts_url,
                "source_bucket": CURATED_SOURCE_BUCKET,
                "scan_scope": CURATED_SCAN_SCOPE,
            },
        )


@dataclass(slots=True)
class HRTAlertSource:
    http_client: JsonHttpClient
    source_name: str = "hampton_roads_transit_gtfs_rt"
    source_class: str = "local_context"
    alerts_url: str = HRT_ALERTS_URL

    @classmethod
    def with_default_client(cls) -> "HRTAlertSource":
        return cls(http_client=UrllibJsonClient())

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        if profile is None or not _city_match(profile, HAMPTON_ROADS_CITY_TOKENS):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="outside_supported_region",
                provider="hrt",
            )
        if service_date is not None and service_date != _local_today(profile, at):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="same_day_only_source",
                provider="hrt",
            )

        raw = self.http_client.get_json(self.alerts_url)
        entities = _coerce_json_list(raw.get("entity"))
        active_entities: list[dict[str, Any]] = []
        for row in entities:
            alert = row.get("alert")
            if isinstance(alert, dict):
                active_entities.append(alert)

        severe_count = 0
        for alert in active_entities:
            header_text = _translation_text(alert.get("header_text"))
            description_text = _translation_text(alert.get("description_text"))
            combined = f"{header_text} {description_text}".lower()
            if any(term in combined for term in ("detour", "construction", "stop service adjustment", "stop moved")):
                severe_count += 1

        signals: list[dict[str, Any]] = []
        if active_entities:
            signals.append(
                {
                    "signal_type": "hrt_network_alerts",
                    "dependency_group": "access",
                    "role": "confidence_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "down",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "Hampton Roads Transit network",
                        "active_alert_count": len(active_entities),
                        "severe_alert_count": severe_count,
                        "sample_alerts": [_translation_text(alert.get("header_text")) for alert in active_entities[:5]],
                    },
                }
            )
        if severe_count >= 2:
            signals.append(
                {
                    "signal_type": "hrt_access_friction",
                    "dependency_group": "access",
                    "role": "numeric_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "down",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "Hampton Roads Transit access friction",
                        "active_alert_count": len(active_entities),
                        "severe_alert_count": severe_count,
                    },
                }
            )

        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload={
                "operator_id": operator_id,
                "available": True,
                "signals": signals,
                "active_alert_count": len(active_entities),
                "severe_alert_count": severe_count,
            },
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket=CURATED_SOURCE_BUCKET,
            scan_scope=CURATED_SCAN_SCOPE,
            provenance={
                "mode": "live",
                "provider": "hrt",
                "url": self.alerts_url,
                "source_bucket": CURATED_SOURCE_BUCKET,
                "scan_scope": CURATED_SCAN_SCOPE,
            },
        )


@dataclass(slots=True)
class DDOTOccupancyPermitSource:
    http_client: JsonHttpClient
    source_name: str = "ddot_tops_occupancy_permits"
    source_class: str = "local_context"
    base_url: str = DDOT_TOPS_BASE_URL
    license_key: str | None = DDOT_TOPS_LICENSE_KEY
    radius_miles: float = 0.6

    @classmethod
    def with_default_client(cls) -> "DDOTOccupancyPermitSource":
        return cls(http_client=UrllibJsonClient())

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        if profile is None or profile.lat is None or profile.lon is None:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="missing_location",
                provider="ddot_tops",
            )
        if not _city_match(profile, DC_REGION_CITY_TOKENS):
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="outside_supported_region",
                provider="ddot_tops",
            )
        if service_date is None:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="missing_service_date",
                provider="ddot_tops",
            )
        if not self.license_key:
            return _unavailable_payload(
                source_name=self.source_name,
                source_class=self.source_class,
                operator_id=operator_id,
                at=at,
                service_date=service_date,
                service_window=service_window,
                reason="missing_license_key",
                provider="ddot_tops",
            )

        permit_date = service_date.strftime("%m/%d/%Y")
        url = build_url(
            f"{self.base_url.rstrip('/')}/api/Occupancy",
            {
                "StartRow": 1,
                "PageSize": 300,
                "SortColumn": "LastUpdateDate",
                "SortDirection": "DESC",
                "PermitDateRange": f"{permit_date}-{permit_date}",
            },
        )
        try:
            rows = _coerce_json_list(
                self.http_client.get_json(url, headers={"Authorization": f"Bearer {self.license_key}"})
            )
        except HTTPError as exc:
            if exc.code in {401, 403}:
                return _unavailable_payload(
                    source_name=self.source_name,
                    source_class=self.source_class,
                    operator_id=operator_id,
                    at=at,
                    service_date=service_date,
                    service_window=service_window,
                    reason=f"authorization_failed:{exc.code}",
                    provider="ddot_tops",
                )
            raise

        nearby: list[dict[str, Any]] = []
        for row in rows:
            try:
                permit_lat = float(row.get("Latitude"))
                permit_lon = float(row.get("Longitude"))
            except (TypeError, ValueError):
                continue
            distance = _haversine_miles(float(profile.lat), float(profile.lon), permit_lat, permit_lon)
            if distance > self.radius_miles:
                continue
            nearby.append(row)

        road_closure_count = sum(1 for row in nearby if str(row.get("RoadClosureYN", "")).upper() == "Y")
        event_like_count = sum(
            1
            for row in nearby
            if any(
                term in str(row.get("EventTypes", "")).lower()
                for term in ("block", "festival", "parade", "market", "special event")
            )
        )

        signals: list[dict[str, Any]] = []
        if nearby:
            signals.append(
                {
                    "signal_type": "ddot_permit_cluster",
                    "dependency_group": "access",
                    "role": "confidence_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "high",
                    "direction": "uncertain",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "DDOT permits nearby",
                        "nearby_permit_count": len(nearby),
                        "road_closure_count": road_closure_count,
                        "event_like_count": event_like_count,
                    },
                }
            )
        if road_closure_count >= 1:
            signals.append(
                {
                    "signal_type": "ddot_access_friction",
                    "dependency_group": "access",
                    "role": "numeric_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "high",
                    "direction": "down",
                    "details": {
                        "source_category": "traffic_access",
                        "entity_label": "DDOT road closures",
                        "nearby_permit_count": len(nearby),
                        "road_closure_count": road_closure_count,
                    },
                }
            )
        if event_like_count >= 2:
            signals.append(
                {
                    "signal_type": "ddot_permitted_event_pull",
                    "dependency_group": "venue",
                    "role": "numeric_mover",
                    "estimated_pct": 0.0,
                    "trust_level": "medium",
                    "direction": "up",
                    "details": {
                        "source_category": "events_venues",
                        "entity_label": "DDOT permitted events",
                        "nearby_permit_count": len(nearby),
                        "event_like_count": event_like_count,
                    },
                }
            )

        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload={
                "operator_id": operator_id,
                "available": True,
                "signals": signals,
                "nearby_permit_count": len(nearby),
                "road_closure_count": road_closure_count,
                "event_like_count": event_like_count,
            },
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket=CURATED_SOURCE_BUCKET,
            scan_scope=CURATED_SCAN_SCOPE,
            provenance={
                "mode": "live",
                "provider": "ddot_tops",
                "url": url,
                "source_bucket": CURATED_SOURCE_BUCKET,
                "scan_scope": CURATED_SCAN_SCOPE,
            },
        )


def build_live_local_context_sources() -> list[object]:
    client = UrllibJsonClient()
    return [
        GBFSStationPressureSource(
            http_client=client,
            root_feed_url=CAPITAL_BIKESHARE_GBFS_URL,
            city_aliases=DC_REGION_CITY_TOKENS,
            source_name="capital_bikeshare_station_pressure",
            entity_label="Capital Bikeshare pressure",
            system_name="capital_bikeshare",
        ),
        GBFSStationPressureSource(
            http_client=client,
            root_feed_url=INDEGO_GBFS_URL,
            city_aliases=PHILADELPHIA_CITY_TOKENS,
            source_name="indego_station_pressure",
            entity_label="Indego pressure",
            system_name="indego",
        ),
        SEPTAAlertSource(http_client=client),
        HRTAlertSource(http_client=client),
        DDOTOccupancyPermitSource(http_client=client),
    ]
