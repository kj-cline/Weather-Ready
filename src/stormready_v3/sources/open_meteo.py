from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from stormready_v3.config.settings import OPEN_METEO_BASE_URL
from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.sources.contracts import SourcePayload
from stormready_v3.sources.http import JsonHttpClient, UrllibJsonClient, build_url


def weather_code_to_condition(code: int) -> str:
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {56, 57, 66, 67}:
        return "freezing"
    if code in {51, 53, 55, 61, 63, 65, 80, 81, 82}:
        return "rain"
    if code in {95, 96, 99}:
        return "storm"
    if code in {1, 2, 3, 45, 48}:
        return "cloudy"
    return "clear"


def weather_code_to_precip_type(code: int) -> int:
    if code in {56, 57, 66, 67, 71, 73, 75, 77, 85, 86}:
        return 2
    if code in {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}:
        return 1
    return 0


def cloudcover_bin(value: float) -> int:
    if value < 25:
        return 0
    if value < 50:
        return 1
    if value < 75:
        return 2
    return 3


def _numeric_at(series: list[Any] | None, idx: int, *, default: float = 0.0) -> float:
    if not series or idx >= len(series):
        return float(default)
    value = series[idx]
    if value in {None, ""}:
        return float(default)
    return float(value)


def _integer_at(series: list[Any] | None, idx: int, *, default: int = 0) -> int:
    if not series or idx >= len(series):
        return int(default)
    value = series[idx]
    if value in {None, ""}:
        return int(default)
    return int(value)


def _daily_value_for_date(daily: dict[str, Any], key: str, service_date: date) -> Any | None:
    dates = list(daily.get("time") or [])
    values = list(daily.get(key) or [])
    for idx, raw_date in enumerate(dates):
        try:
            parsed = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        if parsed == service_date and idx < len(values):
            return values[idx]
    return None


def _parse_hourly_block(raw: dict[str, Any], *, service_date: date, timezone_name: str | None) -> dict[str, Any]:
    hourly = raw.get("hourly", {})
    daily = raw.get("daily", {})
    timestamps = hourly.get("time", [])
    timezone = ZoneInfo(timezone_name) if timezone_name else None
    rows: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        dt = datetime.fromisoformat(str(ts))
        if timezone and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone)
        values = {
            "datetime": dt,
            "apparent_temperature": _numeric_at(
                hourly.get("apparent_temperature_2m", hourly.get("apparent_temperature")),
                idx,
                default=0.0,
            ),
            "temperature": _numeric_at(hourly.get("temperature_2m"), idx, default=0.0),
            "precipitation_probability": _numeric_at(hourly.get("precipitation_probability"), idx, default=0.0),
            "precipitation": _numeric_at(hourly.get("precipitation"), idx, default=0.0),
            "weather_code": _integer_at(hourly.get("weather_code", hourly.get("weathercode")), idx, default=0),
            "cloud_cover": _numeric_at(hourly.get("cloud_cover", hourly.get("cloudcover")), idx, default=0.0),
            "wind_speed_10m": _numeric_at(hourly.get("wind_speed_10m", hourly.get("windspeed_10m")), idx, default=0.0),
        }
        if dt.date() == service_date:
            rows.append(values)

    if not rows:
        return {"available": False}

    dinner_rows = [row for row in rows if 17 <= row["datetime"].hour <= 20]
    lunch_rows = [row for row in rows if 11 <= row["datetime"].hour <= 14]
    row_7pm = next((row for row in rows if row["datetime"].hour == 19), dinner_rows[-1] if dinner_rows else rows[-1])

    dinner_precip_max = max((row["precipitation"] for row in dinner_rows), default=0.0)
    dinner_precip_prob = max((row["precipitation_probability"] for row in dinner_rows), default=0.0) / 100.0
    dominant_code = max((row["weather_code"] for row in dinner_rows), default=row_7pm["weather_code"])
    dinner_cloud_mean = mean([row["cloud_cover"] for row in dinner_rows]) if dinner_rows else row_7pm["cloud_cover"]
    dinner_wind_max = max((row["wind_speed_10m"] for row in dinner_rows), default=row_7pm["wind_speed_10m"])

    return {
        "available": True,
        "conditions": weather_code_to_condition(int(dominant_code)),
        "weather_code": int(dominant_code),
        "temp_f": float(row_7pm["temperature"]),
        "temperature_high": float(max((row["temperature"] for row in rows), default=row_7pm["temperature"])),
        "temperature_low": float(min((row["temperature"] for row in rows), default=row_7pm["temperature"])),
        "precip_prob": float(dinner_precip_prob),
        "apparent_temp_7pm": float(row_7pm["apparent_temperature"]),
        "precip_dinner_max": float(dinner_precip_max),
        "wind_speed_mph": float(dinner_wind_max),
        "precip_type_code": int(weather_code_to_precip_type(int(dominant_code))),
        "weekday": float(service_date.isoweekday()),
        "year_code": 1.0,
        "cloudcover_dinner_mean": float(dinner_cloud_mean),
        "cloudcover_bin": float(cloudcover_bin(float(dinner_cloud_mean))),
        "precip_lunch": float(sum(row["precipitation"] for row in lunch_rows)),
        "sunrise": _daily_value_for_date(daily, "sunrise", service_date),
        "sunset": _daily_value_for_date(daily, "sunset", service_date),
    }


def weather_payload_from_hourly_raw(
    raw: dict[str, Any],
    *,
    service_date: date,
    timezone_name: str | None,
) -> dict[str, Any]:
    """Normalize a raw Open-Meteo hourly payload into the live weather payload contract."""
    return _parse_hourly_block(raw, service_date=service_date, timezone_name=timezone_name)


@dataclass(slots=True)
class OpenMeteoForecastSource:
    http_client: JsonHttpClient
    source_name: str = "open_meteo_forecast"
    source_class: str = "weather_forecast"
    base_url: str = OPEN_METEO_BASE_URL

    @classmethod
    def with_default_client(cls) -> "OpenMeteoForecastSource":
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
        if profile is None or profile.lat is None or profile.lon is None or service_date is None:
            return SourcePayload(
                source_name=self.source_name,
                source_class=self.source_class,
                retrieved_at=at,
                payload={"operator_id": operator_id, "available": False},
                freshness="unavailable",
                service_date=service_date,
                service_window=service_window.value if service_window else None,
                source_bucket="weather_core",
                scan_scope="weather_core_scan",
                provenance={"mode": "live", "reason": "missing_location_or_date", "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
            )

        url = build_url(
            self.base_url,
            {
                "latitude": profile.lat,
                "longitude": profile.lon,
                "hourly": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "precipitation_probability",
                        "precipitation",
                        "weather_code",
                        "cloud_cover",
                        "wind_speed_10m",
                    ]
                ),
                "daily": ",".join(["sunrise", "sunset"]),
                "temperature_unit": "fahrenheit",
                "precipitation_unit": "inch",
                "wind_speed_unit": "mph",
                "timezone": profile.timezone or "auto",
                "forecast_days": 16,
            },
        )
        raw = self.http_client.get_json(url)
        payload = _parse_hourly_block(raw, service_date=service_date, timezone_name=profile.timezone)
        payload["operator_id"] = operator_id
        return SourcePayload(
            source_name=self.source_name,
            source_class=self.source_class,
            retrieved_at=at,
            payload=payload,
            freshness="fresh" if payload.get("available") else "unavailable",
            service_date=service_date,
            service_window=service_window.value if service_window else None,
            source_bucket="weather_core",
            scan_scope="weather_core_scan",
            provenance={"mode": "live", "provider": "open-meteo", "url": url, "source_bucket": "weather_core", "scan_scope": "weather_core_scan"},
        )
