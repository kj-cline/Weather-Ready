from __future__ import annotations

from typing import Any, Callable


def _safe_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _district_bikeshare_pressure(details: dict[str, Any]) -> float:
    station_count = _safe_int(details.get("station_count")) or 0
    active_pressure_share = _safe_float(details.get("active_pressure_share")) or 0.0
    if station_count < 3 or active_pressure_share < 0.5:
        return 0.0
    return 0.02


def _septa_access_friction(details: dict[str, Any]) -> float:
    severe_alert_count = _safe_int(details.get("severe_alert_count")) or 0
    return -0.02 if severe_alert_count >= 3 else 0.0


def _hrt_access_friction(details: dict[str, Any]) -> float:
    severe_alert_count = _safe_int(details.get("severe_alert_count")) or 0
    return -0.02 if severe_alert_count >= 2 else 0.0


def _ddot_access_friction(details: dict[str, Any]) -> float:
    road_closure_count = _safe_int(details.get("road_closure_count")) or 0
    if road_closure_count >= 2:
        return -0.03
    if road_closure_count >= 1:
        return -0.02
    return 0.0


def _ddot_permitted_event_pull(details: dict[str, Any]) -> float:
    event_like_count = _safe_int(details.get("event_like_count")) or 0
    return 0.03 if event_like_count >= 2 else 0.0


def _mock_venue_cluster_pull(details: dict[str, Any]) -> float:
    return 0.03 if str(details.get("intensity", "")).lower() == "elevated" else 0.0


CONTEXT_EFFECT_MAP: dict[str, Callable[[dict[str, Any]], float]] = {
    "district_bikeshare_pressure": _district_bikeshare_pressure,
    "septa_access_friction": _septa_access_friction,
    "hrt_access_friction": _hrt_access_friction,
    "ddot_access_friction": _ddot_access_friction,
    "ddot_permitted_event_pull": _ddot_permitted_event_pull,
    "venue_cluster_pull": _mock_venue_cluster_pull,
}


def resolve_context_effect_pct(
    *,
    signal_type: str,
    details: dict[str, Any],
    raw_estimated_pct: float,
) -> tuple[float, bool]:
    resolver = CONTEXT_EFFECT_MAP.get(signal_type)
    if resolver is None:
        return raw_estimated_pct, False
    return float(resolver(details)), True
