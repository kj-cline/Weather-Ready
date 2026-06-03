from __future__ import annotations

import os
from pathlib import Path

from stormready_v3.config.env import load_workspace_env


class ConfigError(RuntimeError):
    """Raised when environment configuration is present but invalid."""


LOADED_ENV_FILES = load_workspace_env()

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT.parent
WORKSPACE_ROOT = SRC_ROOT.parent
DB_ROOT = WORKSPACE_ROOT / "db"
MIGRATIONS_ROOT = DB_ROOT / "migrations"
RUNTIME_DATA_ROOT = WORKSPACE_ROOT / "runtime_data"
LOCAL_RUNTIME_ROOT = RUNTIME_DATA_ROOT / "local"
DEFAULT_DB_PATH = Path(os.getenv("STORMREADY_V3_DB_PATH", str(LOCAL_RUNTIME_ROOT / "stormready_v3.duckdb")))
CONNECTOR_SNAPSHOT_ROOT = RUNTIME_DATA_ROOT / "connectors"

ACTIONABLE_HORIZON_DAYS = 14
WORKING_HORIZON_DAYS = 21
NOTIFICATION_HORIZON_DAYS = 3
SCHEDULED_REFRESH_WINDOWS = ("morning", "midday", "pre_dinner")


def _normalized_env_choice(name: str, *, default: str, allowed: set[str]) -> str:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in allowed:
        return normalized
    allowed_list = ", ".join(sorted(allowed))
    raise ConfigError(f"{name} must be one of: {allowed_list}. Got: {raw_value!r}")


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, *, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    if minimum is not None:
        return max(minimum, parsed)
    return parsed


SOURCE_MODE = _normalized_env_choice(
    "STORMREADY_V3_SOURCE_MODE",
    default="live",
    allowed={"live", "mock", "hybrid", "detailed_mock"},
)
CONNECTOR_MODE = _normalized_env_choice(
    "STORMREADY_V3_CONNECTOR_MODE",
    default="snapshot",
    allowed={"snapshot", "default", "hybrid", "live"},
)
OPENTABLE_PARTNER_TOKEN = os.getenv("STORMREADY_V3_OPENTABLE_PARTNER_TOKEN")
OPENTABLE_LOCATION_ID = os.getenv("STORMREADY_V3_OPENTABLE_LOCATION_ID")
TOAST_PARTNER_TOKEN = os.getenv("STORMREADY_V3_TOAST_PARTNER_TOKEN")
TOAST_RESTAURANT_GUID = os.getenv("STORMREADY_V3_TOAST_RESTAURANT_GUID")
OPEN_METEO_BASE_URL = os.getenv("STORMREADY_V3_OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
NWS_BASE_URL = os.getenv("STORMREADY_V3_NWS_BASE_URL", "https://api.weather.gov")
CAPITAL_BIKESHARE_GBFS_URL = os.getenv(
    "STORMREADY_V3_CAPITAL_BIKESHARE_GBFS_URL",
    "https://gbfs.capitalbikeshare.com/gbfs/2.3/gbfs.json",
)
INDEGO_GBFS_URL = os.getenv(
    "STORMREADY_V3_INDEGO_GBFS_URL",
    "https://gbfs.bcycle.com/bcycle_indego/gbfs.json",
)
SEPTA_ALERTS_URL = os.getenv(
    "STORMREADY_V3_SEPTA_ALERTS_URL",
    "https://www3.septa.org/hackathon/Alerts/get_alert_data.php?req1=all",
)
HRT_ALERTS_URL = os.getenv(
    "STORMREADY_V3_HRT_ALERTS_URL",
    "https://gtfs.gohrt.com/gtfs-rt/Alerts.json",
)
DDOT_TOPS_BASE_URL = os.getenv("STORMREADY_V3_DDOT_TOPS_BASE_URL", "https://topsapi.ddot.dc.gov")
DDOT_TOPS_LICENSE_KEY = os.getenv("STORMREADY_V3_DDOT_TOPS_LICENSE_KEY")
CENSUS_GEOCODER_BASE_URL = os.getenv(
    "STORMREADY_V3_CENSUS_GEOCODER_BASE_URL",
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
)
HTTP_TIMEOUT_SECONDS = float(os.getenv("STORMREADY_V3_HTTP_TIMEOUT_SECONDS", "10"))
HTTP_USER_AGENT = os.getenv("STORMREADY_V3_HTTP_USER_AGENT", "stormready-v3/0.1")
ENABLE_SETUP_WEATHER_BASELINE = os.getenv("STORMREADY_V3_ENABLE_SETUP_WEATHER_BASELINE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("STORMREADY_V3_OPENAI_API_KEY")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("STORMREADY_V3_AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("STORMREADY_V3_AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("STORMREADY_V3_AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("STORMREADY_V3_AZURE_OPENAI_DEPLOYMENT")
OPENAI_MODEL = os.getenv("STORMREADY_V3_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
OPENAI_BASE_URL = os.getenv("STORMREADY_V3_OPENAI_BASE_URL")
AGENT_MODEL_PROVIDER = os.getenv("STORMREADY_V3_AGENT_MODEL_PROVIDER", "auto")
AGENT_REASONING_EFFORT = os.getenv("STORMREADY_V3_AGENT_REASONING_EFFORT", "medium").strip().lower() or "medium"
AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("STORMREADY_V3_AI_REQUEST_TIMEOUT_SECONDS", "20"))
AI_GOVERNANCE_TIMEOUT_SECONDS = float(os.getenv("STORMREADY_V3_AI_GOVERNANCE_TIMEOUT_SECONDS", "2.5"))
AI_ENRICHMENT_TIMEOUT_SECONDS = float(os.getenv("STORMREADY_V3_AI_ENRICHMENT_TIMEOUT_SECONDS", "8"))
AI_MAX_RETRIES = int(os.getenv("STORMREADY_V3_AI_MAX_RETRIES", "0"))
EXTERNAL_SOURCE_AI_GOVERNANCE_MODE = os.getenv("STORMREADY_V3_EXTERNAL_SOURCE_AI_GOVERNANCE_MODE", "disabled")


def background_supervisor_enabled() -> bool:
    return _env_flag("STORMREADY_V3_BACKGROUND_SUPERVISOR")


def background_supervisor_interval_seconds() -> int:
    return _env_int(
        "STORMREADY_V3_BACKGROUND_SUPERVISOR_INTERVAL_SECONDS",
        default=300,
        minimum=1,
    )
