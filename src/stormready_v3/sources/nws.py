from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from stormready_v3.config.settings import NWS_BASE_URL
from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.sources.contracts import SourcePayload
from stormready_v3.sources.http import JsonHttpClient, UrllibJsonClient, build_url


def _severity_rank(severity: str) -> int:
    lookup = {
        "minor": 1,
        "moderate": 2,
        "severe": 3,
        "extreme": 4,
    }
    return lookup.get(severity.lower(), 0)


def _pick_top_alert(features: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not features:
        return None
    return sorted(
        features,
        key=lambda feature: _severity_rank(feature.get("properties", {}).get("severity", "")),
        reverse=True,
    )[0]


@dataclass(slots=True)
class NWSActiveAlertsSource:
    http_client: JsonHttpClient
    source_name: str = "nws_active_alerts"
    source_class: str = "weather_alert"
    base_url: str = NWS_BASE_URL

    @classmethod
    def with_default_client(cls) -> "NWSActiveAlertsSource":
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
            return SourcePayload(
                source_name=self.source_name,
                source_class=self.source_class,
                retrieved_at=at,
                payload={"operator_id": operator_id, "active_alert_count": 0, "available": False},
                freshness="unavailable",
                service_date=service_date,
                service_window=service_window.value if service_window else None,
                source_bucket="weather_core",
                scan_scope="weather_core_scan",
                provenance={"mode": "live", "reason": "missing_location", "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
            )
        url = build_url(
            f"{self.base_url}/alerts/active",
            {"point": f"{profile.lat},{profile.lon}"},
        )
        raw = self.http_client.get_json(url)
        features = raw.get("features", [])
        top = _pick_top_alert(features)
        properties = top.get("properties", {}) if top else {}
        payload = {
            "operator_id": operator_id,
            "available": True,
            "active_alert_count": len(features),
            "event": properties.get("event"),
            "severity": properties.get("severity"),
            "urgency": properties.get("urgency"),
            "certainty": properties.get("certainty"),
            "headline": properties.get("headline"),
        }
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload=payload,
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket="weather_core",
            scan_scope="weather_core_scan",
            provenance={"mode": "live", "provider": "nws", "url": url, "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
        )
