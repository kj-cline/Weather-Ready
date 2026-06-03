from __future__ import annotations

from dataclasses import dataclass

from stormready_v3.config.settings import CENSUS_GEOCODER_BASE_URL
from stormready_v3.sources.http import JsonHttpClient, UrllibJsonClient, build_url


@dataclass(slots=True)
class GeocodeResult:
    canonical_address: str
    lat: float
    lon: float
    city: str | None = None
    state_code: str | None = None
    geocoder_source: str = "census_geocoder"
    geocode_confidence: str = "matched"


@dataclass(slots=True)
class CensusGeocoder:
    http_client: JsonHttpClient
    base_url: str = CENSUS_GEOCODER_BASE_URL

    @classmethod
    def with_default_client(cls) -> "CensusGeocoder":
        return cls(http_client=UrllibJsonClient())

    def geocode(self, address: str) -> GeocodeResult | None:
        url = build_url(
            self.base_url,
            {
                "address": address,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
        )
        raw = self.http_client.get_json(url)
        matches = raw.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        match = matches[0]
        coords = match.get("coordinates", {})
        components = match.get("addressComponents", {})
        if "y" not in coords or "x" not in coords:
            return None
        return GeocodeResult(
            canonical_address=str(match.get("matchedAddress", address)),
            lat=float(coords["y"]),
            lon=float(coords["x"]),
            city=components.get("city"),
            state_code=components.get("state"),
        )
