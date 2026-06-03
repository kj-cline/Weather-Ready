from __future__ import annotations

from stormready_v3.domain.enums import SignalRole
from stormready_v3.domain.models import NormalizedSignal
from stormready_v3.prediction.context_effect_mapping import resolve_context_effect_pct
from stormready_v3.prediction.weather_effect_mapping import (
    extreme_cold_effect_pct,
    gray_suppression_effect_pct,
    precip_overlap_effect_pct,
)
from stormready_v3.reference.brooklyn import build_brooklyn_reference_features
from stormready_v3.sources.contracts import SourcePayload


def _payload_bucket(payload: SourcePayload) -> str:
    return str(payload.source_bucket or payload.provenance.get("source_bucket") or "weather_core")


def _payload_scan_scope(payload: SourcePayload) -> str | None:
    return str(payload.scan_scope or payload.provenance.get("scan_scope")) if (payload.scan_scope or payload.provenance.get("scan_scope")) else None


def _weather_learning_signatures(
    *,
    apparent_temp: float,
    precip_dinner_max: float,
    cloudcover_bin: int,
    precip_prob: float,
) -> list[str]:
    if apparent_temp < 35:
        temp_band = "cold_extreme"
    elif apparent_temp < 50:
        temp_band = "cold"
    elif apparent_temp < 68:
        temp_band = "cool"
    elif apparent_temp < 82:
        temp_band = "mild"
    elif apparent_temp < 90:
        temp_band = "warm"
    else:
        temp_band = "hot"

    if precip_dinner_max >= 0.12 or precip_prob >= 0.85:
        precip_band = "heavy"
    elif precip_dinner_max >= 0.04 or precip_prob >= 0.55:
        precip_band = "moderate"
    elif precip_dinner_max > 0.0 or precip_prob >= 0.20:
        precip_band = "light"
    else:
        precip_band = "none"

    cloud_band_lookup = {0: "clear", 1: "mixed", 2: "gray", 3: "dense"}
    cloud_band = cloud_band_lookup.get(int(cloudcover_bin), "mixed")
    return [
        f"temp::{temp_band}",
        f"precip::{precip_band}",
        f"cloud::{cloud_band}",
        f"combo::{temp_band}|{precip_band}|{cloud_band}",
    ]


def weather_payload_to_signals(payload: SourcePayload) -> list[NormalizedSignal]:
    signals: list[NormalizedSignal] = []
    source_bucket = _payload_bucket(payload)
    scan_scope = _payload_scan_scope(payload)
    precip_prob = float(payload.payload.get("precip_prob", 0.0) or 0.0)
    precip_dinner_max = float(payload.payload.get("precip_dinner_max", 0.0) or 0.0)
    apparent_temp = float(
        payload.payload.get(
            "apparent_temp_7pm",
            payload.payload.get("temp_f", 0.0),
        )
        or 0.0
    )
    temp_f = float(payload.payload.get("temp_f", apparent_temp) or apparent_temp or 0.0)
    cloudcover_bin = int(payload.payload.get("cloudcover_bin", 0) or 0)
    condition = str(payload.payload.get("conditions", "unknown"))
    condition_lower = condition.lower()
    learning_signatures = _weather_learning_signatures(
        apparent_temp=apparent_temp,
        precip_dinner_max=precip_dinner_max,
        cloudcover_bin=cloudcover_bin,
        precip_prob=precip_prob,
    )
    precip_signal_strength = max(
        precip_prob,
        min(1.0, precip_dinner_max / 0.10) if precip_dinner_max > 0 else 0.0,
    )

    precip_overlap_pct = precip_overlap_effect_pct(
        precip_prob=precip_prob,
        precip_dinner_max=precip_dinner_max,
    )
    if precip_overlap_pct != 0.0:
        signals.append(
            NormalizedSignal(
                signal_type="precip_overlap",
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group="weather",
                role=SignalRole.NUMERIC_MOVER,
                estimated_pct=precip_overlap_pct,
                trust_level="medium",
                direction="down",
                details={
                    "precip_prob": precip_prob,
                    "precip_dinner_max": precip_dinner_max,
                    "conditions": condition,
                    "learning_signatures": learning_signatures,
                    "effect_mapping_source": "central_weather_map",
                },
            )
        )
    if apparent_temp >= 90:
        signals.append(
            NormalizedSignal(
                signal_type="extreme_heat",
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group="weather",
                role=SignalRole.CONFIDENCE_MOVER,
                estimated_pct=0.0,
                trust_level="medium",
                direction="uncertain",
                details={
                    "temp_f": temp_f,
                    "apparent_temp_7pm": apparent_temp,
                    "learning_signatures": learning_signatures,
                },
            )
        )
    extreme_cold_pct = extreme_cold_effect_pct(apparent_temp=apparent_temp)
    if extreme_cold_pct != 0.0:
        signals.append(
            NormalizedSignal(
                signal_type="extreme_cold",
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group="weather",
                role=SignalRole.NUMERIC_MOVER,
                estimated_pct=extreme_cold_pct,
                trust_level="medium",
                direction="down",
                details={
                    "apparent_temp_7pm": apparent_temp,
                    "learning_signatures": learning_signatures,
                    "effect_mapping_source": "central_weather_map",
                },
            )
        )
    gray_suppression_pct = gray_suppression_effect_pct(
        cloudcover_bin=cloudcover_bin,
        precip_prob=precip_prob,
        precip_dinner_max=precip_dinner_max,
    )
    if gray_suppression_pct != 0.0:
        signals.append(
            NormalizedSignal(
                signal_type="gray_suppression",
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group="weather",
                role=SignalRole.NUMERIC_MOVER,
                estimated_pct=gray_suppression_pct,
                trust_level="low",
                direction="down",
                details={
                    "cloudcover_bin": cloudcover_bin,
                    "learning_signatures": learning_signatures,
                    "effect_mapping_source": "central_weather_map",
                },
            )
        )
    if (
        "snow" in condition_lower
        or "storm" in condition_lower
        or "ice" in condition_lower
        or ("rain" in condition_lower and precip_signal_strength >= 0.9)
    ):
        signals.append(
            NormalizedSignal(
                signal_type="weather_disruption_risk",
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group="weather",
                role=SignalRole.SERVICE_STATE_MODIFIER,
                estimated_pct=0.0,
                trust_level="medium",
                direction="down",
                details={
                    "precip_prob": precip_prob,
                    "precip_dinner_max": precip_dinner_max,
                    "conditions": condition,
                    "learning_signatures": learning_signatures,
                },
            )
        )
    return signals


def transit_payload_to_signals(payload: SourcePayload) -> list[NormalizedSignal]:
    severity = str(payload.payload.get("severity", "unknown")).lower()
    service_reduction = bool(payload.payload.get("service_reduction", False))
    if not service_reduction and severity not in {"major", "severe"}:
        return []
    return [
        NormalizedSignal(
            signal_type="transit_disruption",
            source_name=payload.source_name,
            source_class=payload.source_class,
            source_bucket=_payload_bucket(payload),
            scan_scope=_payload_scan_scope(payload),
            dependency_group="access",
            role=SignalRole.CONFIDENCE_MOVER,
            estimated_pct=0.0,
            trust_level="medium",
            direction="down",
            details={
                "severity": severity,
                "service_reduction": service_reduction,
                "source_category": str(payload.payload.get("source_category", "traffic_access")),
            },
        )
    ]


def local_context_payload_to_signals(payload: SourcePayload) -> list[NormalizedSignal]:
    raw_signals = payload.payload.get("signals") or []
    if not isinstance(raw_signals, list):
        return []
    normalized: list[NormalizedSignal] = []
    source_bucket = _payload_bucket(payload)
    scan_scope = _payload_scan_scope(payload)
    for item in raw_signals:
        if not isinstance(item, dict):
            continue
        signal_type = str(item.get("signal_type", "local_context"))
        role_value = str(item.get("role", SignalRole.CONFIDENCE_MOVER.value))
        try:
            role = SignalRole(role_value)
        except ValueError:
            role = SignalRole.CONFIDENCE_MOVER
        details = dict(item.get("details") or {})
        details.setdefault("source_bucket", source_bucket)
        if scan_scope is not None:
            details.setdefault("scan_scope", scan_scope)
        raw_estimated_pct = float(item.get("estimated_pct", 0.0) or 0.0)
        estimated_pct = raw_estimated_pct
        if role is SignalRole.NUMERIC_MOVER:
            estimated_pct, was_mapped = resolve_context_effect_pct(
                signal_type=signal_type,
                details=details,
                raw_estimated_pct=raw_estimated_pct,
            )
            if was_mapped:
                details.setdefault("effect_mapping_source", "central_context_map")
        normalized.append(
            NormalizedSignal(
                signal_type=signal_type,
                source_name=payload.source_name,
                source_class=payload.source_class,
                source_bucket=source_bucket,
                scan_scope=scan_scope,
                dependency_group=str(item.get("dependency_group", "local_context")),
                role=role,
                estimated_pct=estimated_pct,
                trust_level=str(item.get("trust_level", "medium")),
                direction=str(item.get("direction")) if item.get("direction") is not None else None,
                details=details,
            )
        )
    return normalized


def weather_payload_to_reference_features(payload: SourcePayload) -> dict[str, float] | None:
    return build_brooklyn_reference_features(payload.payload)


def weather_alert_payload_to_signals(payload: SourcePayload) -> list[NormalizedSignal]:
    active_count = int(payload.payload.get("active_alert_count", 0) or 0)
    if active_count <= 0:
        return []
    severity = str(payload.payload.get("severity", "unknown")).lower()
    event = str(payload.payload.get("event", "weather_alert"))
    role = SignalRole.CONFIDENCE_MOVER
    if severity in {"severe", "extreme"}:
        role = SignalRole.SERVICE_STATE_MODIFIER
    return [
        NormalizedSignal(
            signal_type="nws_active_alert",
            source_name=payload.source_name,
            source_class=payload.source_class,
            source_bucket=_payload_bucket(payload),
            scan_scope=_payload_scan_scope(payload),
            dependency_group="weather",
            role=role,
            estimated_pct=0.0,
            trust_level="high",
            direction="down",
            details={
                "active_alert_count": active_count,
                "severity": severity,
                "event": event,
                "headline": payload.payload.get("headline"),
            },
        )
    ]
