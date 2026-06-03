from __future__ import annotations

from stormready_v3.domain.enums import ServiceWindow


MVP_PRIMARY_SERVICE_WINDOW = ServiceWindow.DINNER
MVP_SUPPORTED_RUNTIME_WINDOWS: tuple[ServiceWindow, ...] = (ServiceWindow.DINNER,)


def is_runtime_window_supported(service_window: ServiceWindow) -> bool:
    return service_window in MVP_SUPPORTED_RUNTIME_WINDOWS


def ensure_runtime_window_supported(service_window: ServiceWindow, *, context: str) -> None:
    if is_runtime_window_supported(service_window):
        return
    raise ValueError(
        f"StormReady V3 MVP currently supports dinner-only runtime forecasting; "
        f"{context} received unsupported service window '{service_window.value}'."
    )


def runtime_service_windows(service_windows: list[ServiceWindow] | None = None) -> list[ServiceWindow]:
    if service_windows is None:
        return [MVP_PRIMARY_SERVICE_WINDOW]
    supported = [service_window for service_window in service_windows if is_runtime_window_supported(service_window)]
    if supported:
        return supported
    return [MVP_PRIMARY_SERVICE_WINDOW]
