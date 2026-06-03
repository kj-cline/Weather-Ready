from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass
from typing import Any

from stormready_v3.config import settings


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


@dataclass(slots=True)
class RuntimeConfigurationSummary:
    workspace_root: str
    db_path: str
    source_mode: str
    connector_mode: str
    actionable_horizon_days: int
    working_horizon_days: int
    notification_horizon_days: int
    scheduled_refresh_windows: list[str]
    loaded_env_files: list[str]
    fastapi_available: bool
    uvicorn_available: bool
    node_available: bool
    npm_available: bool
    openai_package_available: bool
    agent_model_provider: str
    openai_model: str | None
    ai_required: bool
    openai_configured: bool
    azure_openai_configured: bool
    live_partner_credentials: dict[str, bool]


def build_runtime_configuration_summary() -> RuntimeConfigurationSummary:
    return RuntimeConfigurationSummary(
        workspace_root=str(settings.WORKSPACE_ROOT),
        db_path=str(settings.DEFAULT_DB_PATH),
        source_mode=settings.SOURCE_MODE,
        connector_mode=settings.CONNECTOR_MODE,
        actionable_horizon_days=settings.ACTIONABLE_HORIZON_DAYS,
        working_horizon_days=settings.WORKING_HORIZON_DAYS,
        notification_horizon_days=settings.NOTIFICATION_HORIZON_DAYS,
        scheduled_refresh_windows=list(settings.SCHEDULED_REFRESH_WINDOWS),
        loaded_env_files=list(settings.LOADED_ENV_FILES),
        fastapi_available=_module_available("fastapi"),
        uvicorn_available=_module_available("uvicorn"),
        node_available=shutil.which("node") is not None,
        npm_available=shutil.which("npm") is not None,
        openai_package_available=_module_available("openai"),
        agent_model_provider=settings.AGENT_MODEL_PROVIDER,
        openai_model=settings.OPENAI_MODEL,
        ai_required=True,
        openai_configured=bool(settings.OPENAI_API_KEY),
        azure_openai_configured=bool(
            settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_VERSION
        ),
        live_partner_credentials={
            "opentable": bool(settings.OPENTABLE_PARTNER_TOKEN and settings.OPENTABLE_LOCATION_ID),
            "toast": bool(settings.TOAST_PARTNER_TOKEN and settings.TOAST_RESTAURANT_GUID),
        },
    )


def runtime_configuration_dict() -> dict[str, Any]:
    return asdict(build_runtime_configuration_summary())
