"""Location-generic weather archive service.

Fetches multi-year hourly weather history from Open-Meteo Archive API for any
lat/lon, computes monthly normals, and stores in weather_baseline_profile.
Works for any operator location worldwide — not tied to any reference city.

The archive data captures weather patterns only (no covers, no revenue).
It tells the system what weather is *normal* for this location so it can
detect anomalies and assess how relevant the Brooklyn reference model is.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from stormready_v3.sources.http import UrllibJsonClient, build_url
from stormready_v3.storage.db import Database

ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Brooklyn, NY — reference location for similarity scoring
BROOKLYN_REFERENCE = {"lat": 40.6782, "lon": -73.9442, "label": "brooklyn_ny"}

# How many years of history to fetch
DEFAULT_ARCHIVE_YEARS = 5

# Hourly fields we request from Open-Meteo Archive
_ARCHIVE_HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "cloud_cover",
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MonthlyWeatherNormal:
    """Weather normals for one month at one location."""
    month: int
    temp_normal_low: float    # 10th percentile dinner-hour temp (°F)
    temp_normal_mid: float    # median dinner-hour temp (°F)
    temp_normal_high: float   # 90th percentile dinner-hour temp (°F)
    temp_std: float           # standard deviation of dinner-hour temp
    precip_frequency: float   # fraction of days with any dinner-hour precip
    cloudiness_frequency: float  # fraction of days with >60% cloud cover at dinner
    extreme_cold_days: float  # fraction of days with temp < 35°F
    extreme_heat_days: float  # fraction of days with temp > 90°F
    heavy_precip_days: float  # fraction of days with precip > 0.1 inch during dinner


@dataclass(slots=True)
class WeatherBaselineProfile:
    """Complete weather baseline for one location."""
    lat: float
    lon: float
    monthly_normals: list[MonthlyWeatherNormal]  # 12 entries, index 0 = January
    archive_years: int = DEFAULT_ARCHIVE_YEARS
    source_version: str = "open_meteo_archive_v1"


@dataclass(slots=True)
class WeatherSimilarityScore:
    """How similar an operator's weather is to a reference location."""
    reference_label: str
    temp_correlation: float      # -1 to 1, monthly median temp correlation
    precip_correlation: float    # -1 to 1, monthly precip frequency correlation
    cloudiness_correlation: float
    seasonal_spread_similarity: float
    overall_similarity: float    # 0 to 1, weighted average
    recommendation: str          # "high_relevance", "moderate_relevance", "low_relevance"


# ---------------------------------------------------------------------------
# Archive fetch — works for any lat/lon
# ---------------------------------------------------------------------------

def _cache_path(cache_root: Path, lat: float, lon: float, year: int) -> Path:
    lat_str = f"{lat:.3f}".replace(".", "_").replace("-", "n")
    lon_str = f"{lon:.3f}".replace(".", "_").replace("-", "n")
    return cache_root / f"weather_archive_{lat_str}_{lon_str}_{year}.json"


def fetch_year_archive(
    *,
    lat: float,
    lon: float,
    year: int,
    timezone: str = "America/New_York",
    cache_root: Path | None = None,
    http_timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch one year of hourly weather data from Open-Meteo Archive API.

    Caches locally to avoid re-fetching. Works for any location worldwide.
    """
    if cache_root is not None:
        cached = _cache_path(cache_root, lat, lon, year)
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

    client = UrllibJsonClient(timeout_seconds=http_timeout)
    url = build_url(
        ARCHIVE_BASE_URL,
        {
            "latitude": str(lat),
            "longitude": str(lon),
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "hourly": ",".join(_ARCHIVE_HOURLY_FIELDS),
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timezone": timezone,
        },
    )
    raw = client.get_json(url)

    if cache_root is not None:
        cached = _cache_path(cache_root, lat, lon, year)
        cached.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    return raw


def fetch_multi_year_archive(
    *,
    lat: float,
    lon: float,
    years: int = DEFAULT_ARCHIVE_YEARS,
    timezone: str = "America/New_York",
    cache_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch multiple years of archive data. Returns list of raw yearly responses."""
    current_year = date.today().year
    # Fetch from (current_year - years - 1) to (current_year - 1)
    # Archive API doesn't have current year complete, so we go up to last year
    archives = []
    for year in range(current_year - years, current_year):
        raw = fetch_year_archive(
            lat=lat,
            lon=lon,
            year=year,
            timezone=timezone,
            cache_root=cache_root,
        )
        archives.append(raw)
    return archives


# ---------------------------------------------------------------------------
# Compute monthly normals from archive data
# ---------------------------------------------------------------------------

def _extract_dinner_observations(archives: list[dict[str, Any]]) -> dict[int, list[dict[str, float]]]:
    """Extract dinner-hour (17:00-21:00) observations grouped by month."""
    by_month: dict[int, list[dict[str, float]]] = {m: [] for m in range(1, 13)}

    for raw in archives:
        hourly = raw.get("hourly", {})
        timestamps = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        apparent_temps = hourly.get("apparent_temperature", [])
        precips = hourly.get("precipitation", [])
        weather_codes = hourly.get("weather_code", [])
        cloud_covers = hourly.get("cloud_cover", [])

        for i, stamp in enumerate(timestamps):
            try:
                dt = datetime.fromisoformat(str(stamp))
            except (ValueError, TypeError):
                continue
            # Dinner hours: 5pm-9pm
            if dt.hour < 17 or dt.hour > 20:
                continue

            obs = {
                "month": dt.month,
                "date_key": dt.date().isoformat(),
                "temp": float(temps[i]) if i < len(temps) and temps[i] is not None else 0.0,
                "apparent_temp": float(apparent_temps[i]) if i < len(apparent_temps) and apparent_temps[i] is not None else 0.0,
                "precip": float(precips[i]) if i < len(precips) and precips[i] is not None else 0.0,
                "weather_code": int(weather_codes[i]) if i < len(weather_codes) and weather_codes[i] is not None else 0,
                "cloud_cover": float(cloud_covers[i]) if i < len(cloud_covers) and cloud_covers[i] is not None else 0.0,
            }
            by_month[dt.month].append(obs)

    return by_month


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = pct / 100.0 * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _std_dev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def compute_monthly_normals(archives: list[dict[str, Any]]) -> list[MonthlyWeatherNormal]:
    """Compute monthly weather normals from multi-year archive data."""
    by_month = _extract_dinner_observations(archives)
    normals = []

    for month in range(1, 13):
        observations = by_month[month]
        if not observations:
            normals.append(MonthlyWeatherNormal(
                month=month,
                temp_normal_low=0.0, temp_normal_mid=0.0, temp_normal_high=0.0,
                temp_std=0.0, precip_frequency=0.0, cloudiness_frequency=0.0,
                extreme_cold_days=0.0, extreme_heat_days=0.0, heavy_precip_days=0.0,
            ))
            continue

        temps = [obs["temp"] for obs in observations]

        # Daily aggregation for frequency metrics
        daily: dict[str, list[dict[str, float]]] = {}
        for obs in observations:
            daily.setdefault(obs["date_key"], []).append(obs)

        total_days = len(daily)
        precip_days = sum(
            1 for day_obs in daily.values()
            if any(o["precip"] > 0.0 for o in day_obs)
        )
        cloudy_days = sum(
            1 for day_obs in daily.values()
            if (sum(o["cloud_cover"] for o in day_obs) / len(day_obs)) > 60.0
        )
        extreme_cold = sum(
            1 for day_obs in daily.values()
            if any(o["temp"] < 35.0 for o in day_obs)
        )
        extreme_heat = sum(
            1 for day_obs in daily.values()
            if any(o["temp"] > 90.0 for o in day_obs)
        )
        heavy_precip = sum(
            1 for day_obs in daily.values()
            if sum(o["precip"] for o in day_obs) > 0.1
        )

        normals.append(MonthlyWeatherNormal(
            month=month,
            temp_normal_low=round(_percentile(temps, 10), 1),
            temp_normal_mid=round(_percentile(temps, 50), 1),
            temp_normal_high=round(_percentile(temps, 90), 1),
            temp_std=round(_std_dev(temps), 2),
            precip_frequency=round(precip_days / max(total_days, 1), 3),
            cloudiness_frequency=round(cloudy_days / max(total_days, 1), 3),
            extreme_cold_days=round(extreme_cold / max(total_days, 1), 3),
            extreme_heat_days=round(extreme_heat / max(total_days, 1), 3),
            heavy_precip_days=round(heavy_precip / max(total_days, 1), 3),
        ))

    return normals


def build_weather_baseline(
    *,
    lat: float,
    lon: float,
    years: int = DEFAULT_ARCHIVE_YEARS,
    timezone: str = "America/New_York",
    cache_root: Path | None = None,
) -> WeatherBaselineProfile:
    """Full pipeline: fetch archives → compute normals → return baseline profile."""
    archives = fetch_multi_year_archive(
        lat=lat, lon=lon, years=years, timezone=timezone, cache_root=cache_root,
    )
    normals = compute_monthly_normals(archives)
    return WeatherBaselineProfile(
        lat=lat,
        lon=lon,
        monthly_normals=normals,
        archive_years=years,
    )


# ---------------------------------------------------------------------------
# Weather similarity scoring
# ---------------------------------------------------------------------------

def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation between two equal-length lists."""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom


def compute_weather_similarity(
    operator_normals: list[MonthlyWeatherNormal],
    reference_normals: list[MonthlyWeatherNormal],
    reference_label: str = "brooklyn_ny",
) -> WeatherSimilarityScore:
    """Compare an operator's weather profile to a reference location."""
    op_temps = [n.temp_normal_mid for n in operator_normals]
    ref_temps = [n.temp_normal_mid for n in reference_normals]
    temp_corr = _pearson_correlation(op_temps, ref_temps)

    op_precip = [n.precip_frequency for n in operator_normals]
    ref_precip = [n.precip_frequency for n in reference_normals]
    precip_corr = _pearson_correlation(op_precip, ref_precip)

    op_cloud = [n.cloudiness_frequency for n in operator_normals]
    ref_cloud = [n.cloudiness_frequency for n in reference_normals]
    cloud_corr = _pearson_correlation(op_cloud, ref_cloud)

    op_spread = [max(0.0, n.temp_normal_high - n.temp_normal_low) for n in operator_normals]
    ref_spread = [max(0.0, n.temp_normal_high - n.temp_normal_low) for n in reference_normals]
    spread_gap = 0.0
    if op_spread and ref_spread:
        spread_gap = sum(abs(op_spread[i] - ref_spread[i]) for i in range(min(len(op_spread), len(ref_spread)))) / max(1, min(len(op_spread), len(ref_spread)))
    spread_similarity = max(0.0, 1.0 - min(1.0, spread_gap / 22.0))

    # Weighted: temperature shape matters most, but cloudiness and seasonal spread
    # help determine whether Brooklyn is a reasonable weather-shape analogue.
    overall = (
        0.45 * max(0.0, temp_corr)
        + 0.20 * max(0.0, precip_corr)
        + 0.15 * max(0.0, cloud_corr)
        + 0.20 * spread_similarity
    )

    if overall >= 0.75:
        recommendation = "high_relevance"
    elif overall >= 0.45:
        recommendation = "moderate_relevance"
    else:
        recommendation = "low_relevance"

    return WeatherSimilarityScore(
        reference_label=reference_label,
        temp_correlation=round(temp_corr, 3),
        precip_correlation=round(precip_corr, 3),
        cloudiness_correlation=round(cloud_corr, 3),
        seasonal_spread_similarity=round(spread_similarity, 3),
        overall_similarity=round(overall, 3),
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# Weather anomaly detection
# ---------------------------------------------------------------------------

def weather_anomaly_score(
    normals: list[MonthlyWeatherNormal],
    month: int,
    current_temp: float,
) -> float:
    """How many standard deviations is current_temp from this month's normal?

    Returns signed value: negative = colder than normal, positive = warmer.
    Useful for amplifying/dampening weather signals.
    """
    if month < 1 or month > 12:
        return 0.0
    normal = normals[month - 1]
    if normal.temp_std <= 0:
        return 0.0
    return round((current_temp - normal.temp_normal_mid) / normal.temp_std, 2)


# ---------------------------------------------------------------------------
# Persistence — store/load from weather_baseline_profile table
# ---------------------------------------------------------------------------

def store_weather_baseline(
    db: Database,
    operator_id: str,
    baseline: WeatherBaselineProfile,
    service_window: str = "dinner",
) -> None:
    """Persist monthly normals to the weather_baseline_profile table."""
    # Clear existing rows for this operator+window
    db.execute(
        "DELETE FROM weather_baseline_profile WHERE operator_id = ? AND service_window = ?",
        [operator_id, service_window],
    )
    for normal in baseline.monthly_normals:
        db.execute(
            """
            INSERT INTO weather_baseline_profile (
                operator_id, month, service_window,
                temp_normal_low, temp_normal_mid, temp_normal_high,
                precip_frequency, cloudiness_frequency,
                humidity_normal, wind_normal, source_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                normal.month,
                service_window,
                normal.temp_normal_low,
                normal.temp_normal_mid,
                normal.temp_normal_high,
                normal.precip_frequency,
                normal.cloudiness_frequency,
                # Repurpose humidity_normal for extreme_cold_days, wind_normal for extreme_heat_days
                # These fields are unused in the original schema — we store extra metrics here
                normal.extreme_cold_days,
                normal.extreme_heat_days,
                baseline.source_version,
            ],
        )


def load_weather_baseline(
    db: Database,
    operator_id: str,
    service_window: str = "dinner",
) -> list[MonthlyWeatherNormal] | None:
    """Load monthly normals from DB. Returns None if not populated."""
    rows = db.fetchall(
        """
        SELECT month, temp_normal_low, temp_normal_mid, temp_normal_high,
               precip_frequency, cloudiness_frequency, humidity_normal, wind_normal
        FROM weather_baseline_profile
        WHERE operator_id = ? AND service_window = ?
        ORDER BY month
        """,
        [operator_id, service_window],
    )
    if not rows or len(rows) < 12:
        return None
    normals = []
    for row in rows:
        low = float(row[1] or 0)
        high = float(row[3] or 0)
        # Estimate std dev from P10/P90 range: spans ~2.56 std devs for normal distribution
        estimated_std = round((high - low) / 2.56, 2) if high > low else 0.0
        normals.append(MonthlyWeatherNormal(
            month=int(row[0]),
            temp_normal_low=low,
            temp_normal_mid=float(row[2] or 0),
            temp_normal_high=high,
            temp_std=estimated_std,
            precip_frequency=float(row[4] or 0),
            cloudiness_frequency=float(row[5] or 0),
            extreme_cold_days=float(row[6] or 0),  # stored in humidity_normal column
            extreme_heat_days=float(row[7] or 0),   # stored in wind_normal column
            heavy_precip_days=0.0,  # Not stored in current schema
        ))
    return normals


# ---------------------------------------------------------------------------
# Brooklyn reference baseline — computed once, stored as _system_brooklyn
# ---------------------------------------------------------------------------

BROOKLYN_SYSTEM_OPERATOR_ID = "_system_brooklyn_reference"


def _ensure_system_operator(db: Database, operator_id: str) -> None:
    """Create a system operator row if needed (satisfies FK constraint)."""
    existing = db.fetchone(
        "SELECT 1 FROM operators WHERE operator_id = ?", [operator_id],
    )
    if existing is None:
        db.execute(
            "INSERT INTO operators (operator_id, restaurant_name) VALUES (?, ?)",
            [operator_id, "_system_reference"],
        )


def ensure_operator_weather_baseline(
    db: Database,
    operator_id: str,
    *,
    lat: float,
    lon: float,
    timezone: str = "America/New_York",
    service_window: str = "dinner",
    cache_root: Path | None = None,
) -> list[MonthlyWeatherNormal]:
    """Load or compute an operator's historical weather baseline."""
    existing = load_weather_baseline(db, operator_id, service_window)
    if existing is not None:
        return existing

    baseline = build_weather_baseline(
        lat=lat,
        lon=lon,
        timezone=timezone,
        cache_root=cache_root,
    )
    store_weather_baseline(db, operator_id, baseline, service_window=service_window)
    return baseline.monthly_normals


def ensure_brooklyn_baseline(
    db: Database,
    cache_root: Path | None = None,
) -> list[MonthlyWeatherNormal]:
    """Load or compute Brooklyn's weather baseline.

    Stored in the same table as operator baselines with a system operator_id.
    Computed once, reused for all similarity comparisons.
    """
    existing = load_weather_baseline(db, BROOKLYN_SYSTEM_OPERATOR_ID)
    if existing is not None:
        return existing

    _ensure_system_operator(db, BROOKLYN_SYSTEM_OPERATOR_ID)
    baseline = build_weather_baseline(
        lat=BROOKLYN_REFERENCE["lat"],
        lon=BROOKLYN_REFERENCE["lon"],
        cache_root=cache_root,
    )
    store_weather_baseline(db, BROOKLYN_SYSTEM_OPERATOR_ID, baseline)
    return baseline.monthly_normals


def compare_to_brooklyn(
    operator_normals: list[MonthlyWeatherNormal],
    db: Database,
    cache_root: Path | None = None,
) -> WeatherSimilarityScore:
    """Compare an operator's weather to Brooklyn and return a similarity score.

    The score determines how much weight the Brooklyn reference model gets
    in the operator's forecast — high similarity = Brooklyn is relevant,
    low similarity = rely more on operator-specific learning.
    """
    brooklyn_normals = ensure_brooklyn_baseline(db, cache_root=cache_root)
    return compute_weather_similarity(
        operator_normals,
        brooklyn_normals,
        reference_label="brooklyn_ny",
    )
