from __future__ import annotations

from dataclasses import dataclass
import uuid
from datetime import date
from typing import Any

from stormready_v3.domain.enums import ComponentState, ForecastRegime, HorizonMode, NeighborhoodType, PredictionCase, ServiceState, SignalRole
from stormready_v3.domain.models import CandidateForecastState, ComponentPrediction, PredictionContext
from stormready_v3.external_intelligence.signal_policy import summarize_dependency_group_corroboration
from stormready_v3.prediction.priors import (
    CONTEXT_PCT_CAP,
    DEMAND_MIX_SCALERS,
    INTERVAL_HALF_WIDTHS,
    LOCAL_WEATHER_PCT_CAP,
    REGIME_GRADUATION_THRESHOLDS,
    RESERVATION_REALIZATION_PRIORS,
    seasonal_pct_for_date,
)
from stormready_v3.reference.brooklyn import active_reference_model_name, brooklyn_delta_uc_prediction, season_from_service_date
from stormready_v3.sources.weather_archive import weather_anomaly_score

WEATHER_SIGNATURE_LEARNING_CAP = 0.035
WEATHER_SIGNATURE_PER_SIGNAL_CAP = 0.015
EXTERNAL_SCAN_LEARNING_CAP = 0.025
EXTERNAL_SCAN_PER_SIGNAL_CAP = 0.012
BROOKLYN_REFERENCE_DELTA_PCT_CAP = 0.35


@dataclass(slots=True)
class DriverImpact:
    driver_name: str
    impact_score: float
    contribution_pct: float = 0.0


def day_group_for_date(service_date: date) -> str:
    weekday = service_date.weekday()
    if weekday <= 3:
        return "mon_thu"
    if weekday == 4:
        return "fri"
    if weekday == 5:
        return "sat"
    return "sun"


def horizon_mode_for_day_offset(day_offset: int) -> HorizonMode:
    if day_offset <= 3:
        return HorizonMode.NEAR
    if day_offset <= 7:
        return HorizonMode.MID
    return HorizonMode.LONG
def build_baseline_level(context: PredictionContext) -> int:
    if context.baseline_total_covers is not None:
        return int(round(context.baseline_total_covers))
    raise ValueError("Weekly baseline is required before forecasting can proceed.")


def _safe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _trust_weight(trust_level: str) -> float:
    return {
        "high": 1.2,
        "medium": 1.0,
        "low": 0.8,
    }.get(trust_level, 1.0)


def _bucket_weight(source_bucket: str) -> float:
    return {
        "weather_core": 1.1,
        "curated_local": 1.12,
        "broad_proxy": 0.88,
    }.get(source_bucket, 1.0)


def _role_floor(signal) -> float:
    if signal.role is SignalRole.SERVICE_STATE_MODIFIER:
        return 0.045
    if signal.role is SignalRole.CONFIDENCE_MOVER:
        return 0.025
    return 0.0


def _location_relevance_weight(signal, context: PredictionContext) -> float:
    """Amplify/dampen signal based on how relevant it is to the operator's location."""
    loc = context.location_context
    dep = signal.dependency_group

    if dep == "access":
        # Transit disruptions: commuter-heavy locations are more affected
        if loc.transit_relevance:
            return 1.0 + min(0.5, (loc.commuter_intensity or 0.5) * 0.6)
        return 0.5  # Suburban/non-transit location — transit signals matter less

    if dep in {"venue", "events"}:
        return 1.4 if loc.venue_relevance else 0.6

    if dep in {"tourism", "hotel", "travel"}:
        return 1.3 if loc.hotel_travel_relevance else 0.5

    return 1.0


def _source_reliability_weight(signal, context: PredictionContext) -> float:
    """Amplify/dampen signal based on learned source reliability for this operator."""
    reliability = context.source_reliability.get(signal.signal_type)
    if not reliability:
        return 1.0
    usefulness = reliability.get("usefulness_score")
    if usefulness is None:
        return 1.0
    # Map usefulness [0, 1] → weight [0.5, 1.3]
    return max(0.5, min(1.3, 0.5 + float(usefulness) * 0.8))


def _signal_impact_score(signal, contribution_pct: float, context: PredictionContext | None = None) -> float:
    overlap = max(0.35, min(1.0, float(signal.service_window_overlap or 1.0)))
    base = max(abs(contribution_pct), _role_floor(signal))
    score = base * _trust_weight(signal.trust_level) * _bucket_weight(signal.source_bucket) * overlap
    if context is not None:
        if signal.dependency_group != "weather":
            score *= _location_relevance_weight(signal, context)
        score *= _source_reliability_weight(signal, context)
    return score


def _learning_confidence_weight(confidence: str) -> float:
    return {
        "very_low": 0.0,
        "low": 0.2,
        "medium": 0.45,
        "high": 0.7,
        "very_high": 0.85,
    }.get(confidence, 0.2)


def _learning_sample_weight(sample_size: int) -> float:
    if sample_size <= 1:
        return 0.0
    return min(1.0, sample_size / 8.0)


def _blend_learning_values(matches: list[dict[str, Any]], field_name: str) -> float:
    weighted_total = 0.0
    total_weight = 0.0
    for match in matches:
        value = _safe_float(match.get(field_name))
        if value is None:
            continue
        confidence = str(match.get("confidence") or "low")
        sample_size = int(match.get("sample_size") or 0)
        weight = _learning_confidence_weight(confidence) * _learning_sample_weight(sample_size)
        if weight <= 0.0:
            continue
        weighted_total += value * weight
        total_weight += weight
    if total_weight <= 0.0:
        return 0.0
    return weighted_total / total_weight


def _prediction_adaptation_value(context: PredictionContext, adaptation_key: str, *, max_abs: float) -> float:
    state = context.prediction_adaptation_learning.get(adaptation_key)
    if not state:
        return 0.0
    raw = _safe_float(state.get("adjustment_mid"))
    if raw is None or raw == 0.0:
        return 0.0
    confidence = str(state.get("confidence") or "low")
    sample_size = int(state.get("sample_size") or 0)
    weight = _learning_confidence_weight(confidence) * _learning_sample_weight(sample_size)
    if weight <= 0.0:
        return 0.0
    return _bounded(raw * weight, -max_abs, max_abs)


def _learned_brooklyn_trust_adjustment(context: PredictionContext) -> float:
    return _prediction_adaptation_value(context, "brooklyn_weight_adjustment", max_abs=0.14)


def _learned_weather_profile_adjustment(context: PredictionContext) -> float:
    return _prediction_adaptation_value(context, "weather_profile_adjustment", max_abs=0.18)


def _learned_interval_evidence_adjustment(context: PredictionContext) -> float:
    learned = _prediction_adaptation_value(context, "interval_evidence_adjustment", max_abs=0.16)
    if learned == 0.0:
        return 0.0
    calibration_samples = int(context.confidence_calibration.get("sample_size", 0) or 0)
    fade = max(0.0, 1.0 - min(1.0, calibration_samples / 12.0))
    return learned * fade


def _weather_signature_learning_for_signal(signal, context: PredictionContext) -> float:
    signatures = signal.details.get("learning_signatures") or []
    if not signatures:
        return 0.0
    matches = [
        context.weather_signature_learning[signature]
        for signature in signatures
        if signature in context.weather_signature_learning
    ]
    if not matches:
        return 0.0
    learned = _blend_learning_values(matches, "sensitivity_mid")
    if learned == 0.0:
        return 0.0
    return max(-WEATHER_SIGNATURE_PER_SIGNAL_CAP, min(WEATHER_SIGNATURE_PER_SIGNAL_CAP, learned * 0.35))


def _external_scan_learning_for_signal(signal, context: PredictionContext) -> float:
    key = f"{signal.source_bucket}|{signal.scan_scope or ''}|{signal.dependency_group}"
    state = context.external_scan_learning.get(key)
    if not state:
        return 0.0
    learned = _blend_learning_values([state], "estimated_effect")
    usefulness = _safe_float(state.get("usefulness_score")) or 0.0
    usefulness_weight = max(0.0, min(1.0, usefulness))
    adjusted = learned * usefulness_weight * 0.4
    return max(-EXTERNAL_SCAN_PER_SIGNAL_CAP, min(EXTERNAL_SCAN_PER_SIGNAL_CAP, adjusted))


def _primary_context_numeric_signals(context: PredictionContext) -> list:
    corroboration = summarize_dependency_group_corroboration(context.normalized_signals)
    primary_by_group: dict[str, Any] = {}
    for signal in context.normalized_signals:
        if signal.role.value != "numeric_mover":
            continue
        if signal.dependency_group == "weather":
            continue
        support = corroboration.get(signal.dependency_group)
        if signal.source_bucket == "broad_proxy" and support is not None and not support.broad_numeric_eligible:
            continue
        current = primary_by_group.get(signal.dependency_group)
        if current is None:
            primary_by_group[signal.dependency_group] = signal
            continue
        current_score = _signal_impact_score(current, current.estimated_pct, context)
        candidate_score = _signal_impact_score(signal, signal.estimated_pct, context)
        if candidate_score > current_score:
            primary_by_group[signal.dependency_group] = signal
    return list(primary_by_group.values())


def _current_weather_anomaly(context: PredictionContext) -> float | None:
    normals = context.weather_baseline_normals
    if not normals:
        return None
    current_temp = None
    for signal in context.normalized_signals:
        if signal.dependency_group == "weather":
            current_temp = _safe_float(signal.details.get("apparent_temp_7pm")) or _safe_float(signal.details.get("temp_f"))
            if current_temp is not None:
                break
    if current_temp is None:
        return None
    return weather_anomaly_score(normals, context.service_date.month, current_temp)


def _anomaly_amplifier(context: PredictionContext) -> float:
    """Amplify weather impact when current conditions deviate from monthly normals.

    Returns a multiplier ≥ 1.0 (anomalous weather amplifies) or ≤ 1.0 (normal weather dampens slightly).
    Bounded to [0.85, 1.30] to prevent runaway amplification.
    """
    anomaly = _current_weather_anomaly(context)
    if anomaly is None:
        return 1.0
    # Beyond ±1.5 std devs → amplify; within ±0.5 → slight dampen
    abs_anomaly = abs(anomaly)
    if abs_anomaly >= 1.5:
        return min(1.30, 1.0 + (abs_anomaly - 1.0) * 0.15)
    if abs_anomaly <= 0.5:
        return 0.95
    return 1.0


def _profile_weather_sensitivity_multiplier(context: PredictionContext) -> float:
    loc = context.location_context
    hint = loc.weather_sensitivity_hint
    if hint is None:
        hint = 1.0
        hint += max(0.0, (loc.commuter_intensity or 0.0) - 0.45) * 0.18
        hint -= max(0.0, (loc.residential_intensity or 0.0) - 0.65) * 0.12
        if loc.transit_relevance:
            hint += 0.04
        if loc.venue_relevance:
            hint += 0.04
        if loc.hotel_travel_relevance:
            hint += 0.03
        if context.operator_profile.neighborhood_type is NeighborhoodType.DESTINATION_NIGHTLIFE:
            hint += 0.05
        elif context.operator_profile.neighborhood_type is NeighborhoodType.RESIDENTIAL:
            hint -= 0.04
    onboarding_deviation = float(hint) - 1.0
    learning_state = context.prediction_adaptation_learning.get("weather_profile_adjustment") or {}
    learning_samples = int(learning_state.get("sample_size") or 0)
    decay_weight = min(0.75, (learning_samples / 12.0) * 0.75)
    learned_adjustment = _learned_weather_profile_adjustment(context)
    adjusted = 1.0 + (onboarding_deviation * (1.0 - decay_weight)) + learned_adjustment
    return _bounded(float(adjusted), 0.82, 1.35)


def _patio_weather_multiplier(context: PredictionContext) -> float:
    """Amplify weather impact for restaurants with outdoor exposure.

    Prefer learned outside-cover share when available. Fall back to coarse patio
    seat tiers instead of depending on indoor seat capacity.
    """
    profile = context.operator_profile
    if not profile.patio_enabled or not profile.patio_seat_capacity:
        return 1.0

    learned_outside = context.source_summary.get("component_learning", {}).get("outside_covers") or {}
    learned_outside_share = _safe_float(learned_outside.get("observed_outside_share"))
    learned_outside_obs = int(learned_outside.get("observation_count", 0) or 0)
    if learned_outside_share is not None and learned_outside_obs >= 3:
        patio_exposure = _bounded(learned_outside_share, 0.08, 0.60)
    else:
        patio_seats = int(profile.patio_seat_capacity or 0)
        if patio_seats >= 40:
            patio_exposure = 0.40
        elif patio_seats >= 24:
            patio_exposure = 0.30
        elif patio_seats >= 12:
            patio_exposure = 0.20
        else:
            patio_exposure = 0.12

    sensitivity = context.location_context.patio_sensitivity_hint or 1.5
    return min(1.8, 1.0 + patio_exposure * sensitivity)


def _learned_demand_mix_scaler(context: PredictionContext) -> float | None:
    """Compute a weather scaler from observed reservation share if enough data exists.

    Component learning tracks realized_reserved_covers. If we have enough
    observations, we can compute the actual reservation share and derive
    a more accurate weather sensitivity scaler.
    """
    reserved_learning = context.source_summary.get("component_learning", {}).get("realized_reserved_covers")
    if not reserved_learning:
        return None
    obs_count = reserved_learning.get("observation_count", 0)
    if obs_count < 3:
        return None
    # observed_share comes from the learning state
    observed_share = reserved_learning.get("observed_reservation_share")
    if observed_share is None:
        return None
    # Interpolate scaler: share 0.0 → walk-in-led (1.35), 0.6+ → reservation-led (0.60)
    # Linear: scaler = 1.35 - (observed_share / 0.60) * 0.75
    return max(0.55, min(1.40, 1.35 - (float(observed_share) / 0.60) * 0.75))


def compute_weather_pct(context: PredictionContext) -> tuple[float, float]:
    # Use learned scaler if available, otherwise use declared demand mix
    learned_scaler = _learned_demand_mix_scaler(context)
    scaler = learned_scaler if learned_scaler is not None else DEMAND_MIX_SCALERS[context.operator_profile.demand_mix]
    value = 0.0
    learning_adjustment = 0.0
    for signal in context.normalized_signals:
        if signal.role.value != "numeric_mover":
            continue
        if signal.dependency_group != "weather":
            continue
        value += signal.estimated_pct
        learning_adjustment += _weather_signature_learning_for_signal(signal, context)
    scaled = value * scaler
    scaled *= _profile_weather_sensitivity_multiplier(context)
    # Amplify for patio-heavy restaurants and anomalous weather
    scaled *= _patio_weather_multiplier(context)
    scaled *= _anomaly_amplifier(context)
    learning_adjustment = max(-WEATHER_SIGNATURE_LEARNING_CAP, min(WEATHER_SIGNATURE_LEARNING_CAP, learning_adjustment))
    total = _bounded(scaled + learning_adjustment, -LOCAL_WEATHER_PCT_CAP, LOCAL_WEATHER_PCT_CAP)
    return total, learning_adjustment


def compute_context_pct(context: PredictionContext) -> tuple[float, float]:
    value = 0.0
    learning_adjustment = 0.0
    for signal in _primary_context_numeric_signals(context):
        value += signal.estimated_pct * _location_relevance_weight(signal, context)
        learning_adjustment += _external_scan_learning_for_signal(signal, context)
    value = max(-CONTEXT_PCT_CAP, min(CONTEXT_PCT_CAP, value))
    learning_adjustment = max(-EXTERNAL_SCAN_LEARNING_CAP, min(EXTERNAL_SCAN_LEARNING_CAP, learning_adjustment))
    total = max(-CONTEXT_PCT_CAP, min(CONTEXT_PCT_CAP, value + learning_adjustment))
    return total, learning_adjustment


def _ranked_driver_names(
    context: PredictionContext,
    *,
    baseline_level: int,
    reserved_expected: int,
    used_reservation_anchor: bool,
    brooklyn_effect_pct: float | None,
    weather_learning_pct: float,
    context_learning_pct: float,
    operator_plan_effect_pct: float | None,
) -> list[str]:
    impacts: list[DriverImpact] = []
    selected_context_ids = {id(signal) for signal in _primary_context_numeric_signals(context)}
    weather_scaler = DEMAND_MIX_SCALERS[context.operator_profile.demand_mix]

    for signal in context.normalized_signals:
        if signal.role.value == "numeric_mover":
            if signal.dependency_group == "weather":
                contribution_pct = signal.estimated_pct * weather_scaler
            elif id(signal) in selected_context_ids:
                contribution_pct = signal.estimated_pct
            else:
                continue
        else:
            contribution_pct = 0.0
        impacts.append(
            DriverImpact(
                driver_name=signal.signal_type,
                contribution_pct=contribution_pct,
                impact_score=_signal_impact_score(signal, contribution_pct, context),
            )
        )

    if used_reservation_anchor:
        anchor_pct = abs(reserved_expected / max(1.0, baseline_level))
        impacts.append(
            DriverImpact(
                driver_name="booked_reservation_anchor",
                contribution_pct=anchor_pct,
                impact_score=max(0.04, anchor_pct),
            )
        )
    if brooklyn_effect_pct is not None and abs(brooklyn_effect_pct) >= 0.005:
        impacts.append(
            DriverImpact(
                driver_name="brooklyn_weather_reference",
                contribution_pct=brooklyn_effect_pct,
                impact_score=max(0.03, abs(brooklyn_effect_pct)),
            )
        )
    if abs(weather_learning_pct) >= 0.005:
        impacts.append(
            DriverImpact(
                driver_name="weather_signature_learning",
                contribution_pct=weather_learning_pct,
                impact_score=max(0.02, abs(weather_learning_pct)),
            )
        )
    if abs(context_learning_pct) >= 0.005:
        impacts.append(
            DriverImpact(
                driver_name="external_scan_learning",
                contribution_pct=context_learning_pct,
                impact_score=max(0.02, abs(context_learning_pct)),
            )
        )
    if operator_plan_effect_pct is not None and abs(operator_plan_effect_pct) >= 0.005:
        impacts.append(
            DriverImpact(
                driver_name="operator_service_plan",
                contribution_pct=operator_plan_effect_pct,
                impact_score=max(0.05, abs(operator_plan_effect_pct)),
            )
        )

    ranked: list[str] = []
    for impact in sorted(impacts, key=lambda item: (-item.impact_score, -abs(item.contribution_pct), item.driver_name)):
        if impact.driver_name in ranked:
            continue
        ranked.append(impact.driver_name)
        if len(ranked) >= 5:
            break
    return ranked or ["baseline service window pattern"]


def _apply_operator_service_plan(
    context: PredictionContext,
    *,
    expected: int,
) -> tuple[int, dict[str, Any] | None]:
    plan = dict(context.operator_service_plan or {})
    if not plan:
        return expected, None

    planned_total = plan.get("planned_total_covers")
    reduction_pct = _safe_float(plan.get("estimated_reduction_pct"))
    state = context.resolved_service_state.service_state

    if state is ServiceState.CLOSED:
        return 0, {
            "applied": "closed_override",
            "planned_total_covers": 0,
            "estimated_reduction_pct": None,
            "effect_covers": -expected,
        }

    if planned_total is not None:
        adjusted = max(0, int(round(float(planned_total))))
        return adjusted, {
            "applied": "planned_total_covers",
            "planned_total_covers": adjusted,
            "estimated_reduction_pct": reduction_pct,
            "effect_covers": adjusted - expected,
        }

    if reduction_pct is not None and reduction_pct > 0:
        bounded_pct = _bounded(reduction_pct / 100.0, 0.0, 0.95)
        adjusted = max(0, round(expected * (1.0 - bounded_pct)))
        return adjusted, {
            "applied": "estimated_reduction_pct",
            "planned_total_covers": None,
            "estimated_reduction_pct": round(bounded_pct * 100.0, 1),
            "effect_covers": adjusted - expected,
        }

    return expected, None


def confidence_tier_for_mode(mode: HorizonMode) -> str:
    if mode is HorizonMode.NEAR:
        return "medium"
    if mode is HorizonMode.MID:
        return "low"
    return "very_low"


def adjust_confidence_tier(base_tier: str, context: PredictionContext) -> str:
    order = ["very_low", "low", "medium", "high"]
    index = order.index(base_tier) if base_tier in order else 1

    # Promotion: sustained accuracy can promote tier by 1
    cal = context.confidence_calibration
    sample_size = int(cal.get("sample_size", 0) or 0)
    error_pct = _safe_float(cal.get("mean_abs_pct_error"))
    coverage = _safe_float(cal.get("interval_coverage_rate"))
    if (
        sample_size >= 8
        and error_pct is not None and error_pct <= 0.12
        and coverage is not None and coverage >= 0.80
    ):
        index = min(len(order) - 1, index + 1)

    # Demotion: confidence movers from signals
    if any(signal.role.value == "confidence_mover" for signal in context.normalized_signals):
        index = max(0, index - 1)

    service_state_penalty = _service_state_confidence_penalty(context)
    index = max(0, index - service_state_penalty)

    # Demotion: calibration penalty from repeated misses
    penalty_steps = int(cal.get("confidence_penalty_steps", 0) or 0)
    index = max(0, index - penalty_steps)
    return order[index]


def _operator_confirmed_normal_service(context: PredictionContext) -> bool:
    state = context.resolved_service_state
    return state.service_state is ServiceState.NORMAL and state.state_source in {"operator", "connected_truth"}


def _authoritative_service_state(context: PredictionContext) -> bool:
    return context.resolved_service_state.state_source in {"operator", "connected_truth", "calendar_rule"}


def _service_state_risk_score(context: PredictionContext) -> float:
    if _authoritative_service_state(context):
        return 0.0
    if _operator_confirmed_normal_service(context):
        return 0.0
    score = _safe_float(context.service_state_risk.get("risk_score")) or 0.0
    state = context.resolved_service_state
    if state.service_state in {ServiceState.WEATHER_DISRUPTION, ServiceState.PARTIAL, ServiceState.PATIO_CONSTRAINED}:
        score = max(score, 0.28 if state.state_source in {"disruption_suggestion", "operator_note"} else 0.18)
    elif state.service_state is ServiceState.HOLIDAY_MODIFIED:
        score = max(score, 0.20)
    elif state.service_state is ServiceState.PRIVATE_EVENT:
        score = max(score, 0.25)
    elif state.service_state is ServiceState.CLOSED:
        score = max(score, 0.60)
    return _bounded(score, 0.0, 0.90)


def _service_state_interval_multiplier(context: PredictionContext) -> float:
    score = _service_state_risk_score(context)
    multiplier = 1.0 + min(0.55, score * 0.85)
    state = context.resolved_service_state
    if state.state_source == "disruption_suggestion":
        multiplier += 0.12
    if state.state_source == "operator_note":
        multiplier += 0.08
    if state.service_state is ServiceState.PRIVATE_EVENT and state.state_source not in {"operator", "connected_truth"}:
        multiplier += 0.08
    return _bounded(multiplier, 1.0, 1.75)


def _service_state_confidence_penalty(context: PredictionContext) -> int:
    if _operator_confirmed_normal_service(context):
        return 0
    score = _service_state_risk_score(context)
    state = context.resolved_service_state
    if state.state_source in {"disruption_suggestion", "operator_note"}:
        return 2 if score >= 0.40 else 1
    if score >= 0.45:
        return 2
    if score >= 0.18:
        return 1
    return 0


def _apply_service_state_lower_tail(
    context: PredictionContext,
    *,
    expected: int,
    low: int,
    interval_half_width: float,
) -> int:
    if _operator_confirmed_normal_service(context):
        return low
    risk_state = str(context.service_state_risk.get("risk_state") or "")
    risk_score = _service_state_risk_score(context)
    if risk_state == ServiceState.CLOSED.value and risk_score >= 0.30:
        return 0
    if context.resolved_service_state.service_state is ServiceState.CLOSED:
        return 0
    if risk_score < 0.18:
        return low
    if risk_state == ServiceState.PRIVATE_EVENT.value:
        asymmetric_width = min(0.90, interval_half_width + 0.12 + risk_score * 0.95)
    elif risk_state in {
        ServiceState.PARTIAL.value,
        ServiceState.WEATHER_DISRUPTION.value,
        ServiceState.PATIO_CONSTRAINED.value,
    }:
        asymmetric_width = min(0.85, interval_half_width + 0.08 + risk_score * 0.75)
    else:
        asymmetric_width = min(0.85, interval_half_width + risk_score * 0.45)
    return min(low, max(0, round(expected * (1.0 - asymmetric_width))))


def posture_for_expected_pct(total_pct: float, service_state: ServiceState) -> str:
    if service_state is ServiceState.CLOSED:
        return "DISRUPTED"
    if service_state in {ServiceState.PARTIAL, ServiceState.WEATHER_DISRUPTION}:
        return "SOFT"
    if total_pct <= -0.10:
        return "SOFT"
    if total_pct >= 0.10:
        return "ELEVATED"
    return "NORMAL"


def _booked_reservation_commit(context: PredictionContext) -> float | None:
    booked = _safe_float(context.resolved_truth_fields.get("booked_reservation_covers"))
    if booked is None:
        return None
    cancellations = _safe_float(context.resolved_truth_fields.get("reservation_cancellation_covers")) or 0.0
    return max(0.0, booked - cancellations)


def _forecast_reserved_component(
    context: PredictionContext,
) -> tuple[int, bool]:
    booked_commit = _booked_reservation_commit(context)
    if booked_commit is None:
        return 0, False
    realization = RESERVATION_REALIZATION_PRIORS[context.operator_profile.demand_mix]
    return max(0, round(booked_commit * realization)), True


def _brooklyn_relevance_weight(context: PredictionContext) -> float:
    """How much to trust the Brooklyn reference model for this operator.

    Uses weather similarity score: high similarity → weight up to 0.85,
    low similarity → weight drops to 0.25.  Without a score, default 0.55.
    """
    score = context.brooklyn_similarity_score
    if score is None:
        base_weight = 0.55  # No baseline yet — moderate trust
    else:
        # Linear mapping: score 0.0→0.25, 1.0→0.85
        base_weight = max(0.25, min(0.85, 0.25 + score * 0.60))
    return _bounded(base_weight + _learned_brooklyn_trust_adjustment(context), 0.18, 0.90)


def _reference_interval_multiplier(
    context: PredictionContext,
    *,
    reference_prediction: float | None,
) -> float:
    """Tighten intervals modestly when Brooklyn materially informs weather shape."""
    if reference_prediction is None:
        return 1.0
    score = context.brooklyn_similarity_score
    if score is None:
        multiplier = 0.86
    else:
        bounded_score = max(0.0, min(1.0, float(score)))
        multiplier = max(0.68, 0.86 - (0.18 * bounded_score))
    learned_trust = _learned_brooklyn_trust_adjustment(context)
    if learned_trust < 0.0:
        multiplier += min(0.06, abs(learned_trust) * 0.35)
    elif learned_trust > 0.0:
        multiplier -= min(0.03, learned_trust * 0.18)
    return _bounded(multiplier, 0.68, 0.94)


def _location_profile_evidence_score(context: PredictionContext) -> float:
    loc = context.location_context
    score = 0.0
    if loc.commuter_intensity is not None:
        score += 0.04
    if loc.residential_intensity is not None:
        score += 0.04
    if loc.weather_sensitivity_hint is not None:
        score += 0.05
    if loc.demand_volatility_hint is not None:
        score += 0.05
    if loc.transit_relevance or loc.venue_relevance or loc.hotel_travel_relevance:
        score += 0.04
    profile = context.operator_profile
    if profile.patio_enabled and profile.patio_seat_capacity:
        score += 0.04
    return score


def _weather_baseline_evidence_score(context: PredictionContext, *, reference_prediction: float | None) -> float:
    normals = context.weather_baseline_normals or []
    if len(normals) < 12:
        return 0.0
    score = 0.14
    temp_std_values = [float(item.temp_std) for item in normals if float(item.temp_std or 0.0) > 0]
    if len(temp_std_values) >= 8:
        score += 0.03
    if context.reference_feature_vector is not None:
        score += 0.03
    if reference_prediction is not None:
        score += 0.05
        if context.brooklyn_similarity_score is not None:
            score += 0.05 * _bounded(float(context.brooklyn_similarity_score), 0.0, 1.0)
    return score


def _signal_freshness_evidence_score(context: PredictionContext) -> float:
    source_freshness = dict(context.source_summary.get("source_freshness", {}) or {})
    fresh_sources = [name for name, freshness in source_freshness.items() if str(freshness) == "fresh"]
    score = 0.0
    if "open_meteo_forecast" in fresh_sources:
        score += 0.03
    if "nws_active_alerts" in fresh_sources:
        score += 0.02
    numeric_groups = {
        signal.dependency_group
        for signal in context.normalized_signals
        if signal.role is SignalRole.NUMERIC_MOVER and abs(float(signal.estimated_pct or 0.0)) >= 0.005
    }
    score += min(0.05, 0.02 * len(numeric_groups))
    return score


def _evidence_score_for_interval(
    context: PredictionContext,
    *,
    reference_prediction: float | None,
    used_reservation_anchor: bool,
) -> float:
    score = 0.0
    if context.prediction_case is PredictionCase.BASIC_PROFILE:
        score += 0.24
    elif context.prediction_case is PredictionCase.AMBIGUOUS:
        score += 0.02
    elif context.prediction_case in {PredictionCase.RESERVATION_ONLY, PredictionCase.POS_ONLY}:
        score += 0.18
    else:
        score += 0.28
    score += _location_profile_evidence_score(context)
    score += _weather_baseline_evidence_score(context, reference_prediction=reference_prediction)
    score += _signal_freshness_evidence_score(context)
    if used_reservation_anchor:
        score += 0.07
    sample_size = int(context.confidence_calibration.get("sample_size", 0) or 0)
    score += min(0.14, sample_size * 0.012)
    score += _learned_interval_evidence_adjustment(context)
    return _bounded(score, 0.0, 0.82)


def _location_volatility_multiplier(context: PredictionContext) -> float:
    loc = context.location_context
    hint = loc.demand_volatility_hint
    if hint is None:
        hint = 1.0
        if context.operator_profile.neighborhood_type is NeighborhoodType.DESTINATION_NIGHTLIFE:
            hint += 0.12
        elif context.operator_profile.neighborhood_type is NeighborhoodType.OFFICE_HEAVY:
            hint += 0.08
        elif context.operator_profile.neighborhood_type is NeighborhoodType.RESIDENTIAL:
            hint -= 0.08
        elif context.operator_profile.neighborhood_type is NeighborhoodType.TRAVEL_HOTEL_STATION:
            hint += 0.10
        if loc.venue_relevance:
            hint += 0.04
        if loc.hotel_travel_relevance:
            hint += 0.05
        if (loc.residential_intensity or 0.0) >= 0.65:
            hint -= 0.05
        if (loc.commuter_intensity or 0.0) >= 0.65:
            hint += 0.03
    return _bounded(float(hint), 0.88, 1.18)


def _climate_volatility_multiplier(context: PredictionContext) -> float:
    normals = context.weather_baseline_normals or []
    if len(normals) < 12:
        return 1.02
    temp_std_values = [float(item.temp_std) for item in normals if float(item.temp_std or 0.0) > 0]
    precip_values = [float(item.precip_frequency or 0.0) for item in normals]
    cloud_values = [float(item.cloudiness_frequency or 0.0) for item in normals]
    avg_temp_std = _mean(temp_std_values)
    avg_precip = _mean(precip_values)
    avg_cloud = _mean(cloud_values)

    multiplier = 1.0
    if avg_temp_std >= 14.0:
        multiplier += 0.04
    elif avg_temp_std <= 9.0 and avg_temp_std > 0:
        multiplier -= 0.03
    if avg_precip >= 0.32:
        multiplier += 0.03
    elif avg_precip <= 0.16:
        multiplier -= 0.02
    if avg_cloud >= 0.62:
        multiplier += 0.02
    anomaly = _current_weather_anomaly(context)
    if anomaly is not None:
        if abs(anomaly) >= 1.8:
            multiplier += 0.05
        elif abs(anomaly) <= 0.5:
            multiplier -= 0.02
    return _bounded(multiplier, 0.90, 1.12)


def _interval_half_width(
    context: PredictionContext,
    *,
    reference_prediction: float | None,
    used_reservation_anchor: bool,
) -> tuple[float, dict[str, float]]:
    base_half_width = INTERVAL_HALF_WIDTHS.get(
        (context.forecast_regime, context.horizon_mode),
        0.30,
    )
    evidence_score = _evidence_score_for_interval(
        context,
        reference_prediction=reference_prediction,
        used_reservation_anchor=used_reservation_anchor,
    )
    evidence_multiplier = 1.0 - (0.45 * evidence_score)
    location_volatility = _location_volatility_multiplier(context)
    climate_volatility = _climate_volatility_multiplier(context)
    calibration_multiplier = float(context.confidence_calibration.get("width_multiplier", 1.0) or 1.0)
    reference_multiplier = _reference_interval_multiplier(context, reference_prediction=reference_prediction)
    service_state_multiplier = _service_state_interval_multiplier(context)

    interval_half_width = base_half_width
    interval_half_width *= evidence_multiplier
    interval_half_width *= location_volatility
    interval_half_width *= climate_volatility
    interval_half_width *= calibration_multiplier
    interval_half_width *= reference_multiplier
    interval_half_width *= service_state_multiplier
    interval_half_width = max(0.08, interval_half_width)
    return interval_half_width, {
        "base_half_width": round(base_half_width, 3),
        "evidence_score": round(evidence_score, 3),
        "evidence_multiplier": round(evidence_multiplier, 3),
        "location_volatility_multiplier": round(location_volatility, 3),
        "climate_volatility_multiplier": round(climate_volatility, 3),
        "calibration_multiplier": round(calibration_multiplier, 3),
        "reference_interval_multiplier": round(reference_multiplier, 3),
        "service_state_interval_multiplier": round(service_state_multiplier, 3),
    }


def _brooklyn_reference_weather_pct(
    context: PredictionContext,
    *,
    baseline_component: int,
) -> tuple[float | None, float | None]:
    if not context.reference_feature_vector or context.service_window.value != "dinner":
        return None, None

    reference_prediction = brooklyn_delta_uc_prediction(
        season=season_from_service_date(context.service_date),
        baseline_uc=float(max(1, baseline_component)),
        feature_vector=context.reference_feature_vector,
    )
    if reference_prediction is None:
        return None, None

    reference_weather_pct = (reference_prediction - baseline_component) / max(1.0, baseline_component)
    reference_weather_pct = max(
        -BROOKLYN_REFERENCE_DELTA_PCT_CAP,
        min(BROOKLYN_REFERENCE_DELTA_PCT_CAP, reference_weather_pct),
    )
    return reference_prediction, reference_weather_pct


def _forecast_weather_shaped_component(
    context: PredictionContext,
    *,
    baseline_component: int,
    seasonal_pct: float,
    weather_pct: float,
    weather_learning_pct: float,
    context_pct: float,
) -> tuple[int, float | None, float, float | None]:
    weather_shape_pct = weather_pct - weather_learning_pct
    reference_prediction, reference_weather_pct = _brooklyn_reference_weather_pct(
        context,
        baseline_component=baseline_component,
    )

    if reference_weather_pct is not None:
        # Blend the Brooklyn weather shape with the local weather signal, then
        # add learned local weather adjustment and context effects separately.
        brooklyn_weight = _brooklyn_relevance_weight(context)
        blended_weather_shape_pct = (
            brooklyn_weight * reference_weather_pct
            + (1 - brooklyn_weight) * weather_shape_pct
        )
        applied_weather_pct = blended_weather_shape_pct + weather_learning_pct
        total_pct = seasonal_pct + applied_weather_pct + context_pct
        expected = round(baseline_component + baseline_component * total_pct)
        brooklyn_effect_pct = applied_weather_pct - weather_pct
        return max(0, expected), reference_prediction, applied_weather_pct, brooklyn_effect_pct

    applied_weather_pct = weather_shape_pct + weather_learning_pct
    total_pct = seasonal_pct + applied_weather_pct + context_pct
    expected = round(baseline_component + baseline_component * total_pct)
    return max(0, expected), None, applied_weather_pct, None


def _build_component_predictions(
    *,
    prediction_run_id: str,
    total_expected: int,
    reserved_expected: int,
    used_reservation_anchor: bool,
) -> list[ComponentPrediction]:
    if not used_reservation_anchor:
        return [
            ComponentPrediction(
                prediction_run_id=prediction_run_id,
                component_name="realized_reserved_covers",
                component_state=ComponentState.UNSUPPORTED,
                predicted_value=None,
                is_operator_visible=False,
                is_learning_eligible=False,
            ),
            ComponentPrediction(
                prediction_run_id=prediction_run_id,
                component_name="realized_walk_in_covers",
                component_state=ComponentState.UNSUPPORTED,
                predicted_value=None,
                is_operator_visible=False,
                is_learning_eligible=False,
            ),
            ComponentPrediction(
                prediction_run_id=prediction_run_id,
                component_name="realized_waitlist_converted_covers",
                component_state=ComponentState.UNSUPPORTED,
                predicted_value=None,
                is_operator_visible=False,
                is_learning_eligible=False,
            ),
        ]

    anchored_reserved = min(total_expected, reserved_expected)
    flex_expected = max(0, total_expected - anchored_reserved)
    return [
        ComponentPrediction(
            prediction_run_id=prediction_run_id,
            component_name="realized_reserved_covers",
            component_state=ComponentState.OBSERVABLE,
            predicted_value=anchored_reserved,
            is_operator_visible=False,
            is_learning_eligible=False,
        ),
        ComponentPrediction(
            prediction_run_id=prediction_run_id,
            component_name="realized_walk_in_covers",
            component_state=ComponentState.PROVISIONAL,
            predicted_value=flex_expected,
            is_operator_visible=False,
            is_learning_eligible=False,
        ),
        ComponentPrediction(
            prediction_run_id=prediction_run_id,
            component_name="realized_waitlist_converted_covers",
            component_state=ComponentState.UNSUPPORTED,
            predicted_value=None,
            is_operator_visible=False,
            is_learning_eligible=False,
        ),
    ]


def run_forecast(context: PredictionContext) -> tuple[CandidateForecastState, dict[str, Any]]:
    """Run the forecast engine and return (candidate, engine_digest).

    The engine_digest is a compact dict (~500 chars serialized) summarizing what
    the engine computed and why — designed for the conversation agent to explain
    forecasts without needing the full PredictionContext.
    """
    corroboration = summarize_dependency_group_corroboration(context.normalized_signals)
    baseline_level = build_baseline_level(context)
    seasonal_pct = seasonal_pct_for_date(context.service_date, context.operator_profile.neighborhood_type)
    weather_pct, weather_learning_pct = compute_weather_pct(context)
    context_pct, context_learning_pct = compute_context_pct(context)

    reserved_expected, used_reservation_anchor = _forecast_reserved_component(context)
    forecastable_baseline = baseline_level

    if used_reservation_anchor:
        flex_baseline = max(0, baseline_level - reserved_expected)
        forecastable_baseline = flex_baseline
        flex_expected, reference_prediction, applied_weather_pct, brooklyn_effect_pct = _forecast_weather_shaped_component(
            context,
            baseline_component=flex_baseline,
            seasonal_pct=seasonal_pct,
            weather_pct=weather_pct,
            weather_learning_pct=weather_learning_pct,
            context_pct=context_pct,
        )
        expected = reserved_expected + flex_expected
    else:
        expected, reference_prediction, applied_weather_pct, brooklyn_effect_pct = _forecast_weather_shaped_component(
            context,
            baseline_component=baseline_level,
            seasonal_pct=seasonal_pct,
            weather_pct=weather_pct,
            weather_learning_pct=weather_learning_pct,
            context_pct=context_pct,
        )

    expected_before_plan = expected
    model_total_pct = ((expected_before_plan - baseline_level) / max(1.0, baseline_level))
    expected, operator_plan_adjustment = _apply_operator_service_plan(
        context,
        expected=expected,
    )
    total_pct = ((expected - baseline_level) / max(1.0, baseline_level))
    reference_status = "used" if reference_prediction is not None else ("available_not_used" if context.reference_feature_vector else "not_available")
    reference_model = active_reference_model_name() if reference_prediction is not None else None

    interval_half_width, interval_debug = _interval_half_width(
        context,
        reference_prediction=reference_prediction,
        used_reservation_anchor=used_reservation_anchor,
    )
    low = max(0, round(expected * (1 - interval_half_width)))
    high = max(low, round(expected * (1 + interval_half_width)))
    low = _apply_service_state_lower_tail(
        context,
        expected=expected,
        low=low,
        interval_half_width=interval_half_width,
    )

    top_drivers = _ranked_driver_names(
        context,
        baseline_level=baseline_level,
        reserved_expected=reserved_expected,
        used_reservation_anchor=used_reservation_anchor,
        brooklyn_effect_pct=brooklyn_effect_pct,
        weather_learning_pct=weather_learning_pct,
        context_learning_pct=context_learning_pct,
        operator_plan_effect_pct=((expected - expected_before_plan) / max(1.0, baseline_level)) if operator_plan_adjustment is not None else None,
    )

    major_uncertainties = []
    if context.horizon_mode is not HorizonMode.NEAR:
        major_uncertainties.append("longer horizon uncertainty")
    if context.resolved_target.target_definition_confidence != "high":
        major_uncertainties.append("target definition still stabilizing")
    if used_reservation_anchor and "reservation_no_show_covers" not in context.resolved_truth_fields:
        major_uncertainties.append("reservation realization may vary")
    if float(context.confidence_calibration.get("width_multiplier", 1.0) or 1.0) > 1.0:
        major_uncertainties.append("recent forecast variability remains elevated")
    if any("broad_proxy" in item.source_buckets and not item.broad_numeric_eligible for item in corroboration.values()):
        major_uncertainties.append("some broad outside signals are still unconfirmed")
    service_state_risk_score = _service_state_risk_score(context)
    if service_state_risk_score >= 0.18:
        risk_state = str(context.service_state_risk.get("risk_state") or context.resolved_service_state.service_state.value)
        major_uncertainties.append(f"{risk_state.replace('_', ' ')} risk may change the usable service pattern")
    if operator_plan_adjustment is not None and operator_plan_adjustment.get("applied") == "estimated_reduction_pct":
        major_uncertainties.append("the planned service reduction still depends on how the shift actually runs")

    # --- Build engine digest ---
    # Top signals: the 4 highest-impact normalized signals with their contribution
    signal_impacts: list[dict[str, Any]] = []
    selected_context_ids = {id(s) for s in _primary_context_numeric_signals(context)}
    weather_scaler = DEMAND_MIX_SCALERS[context.operator_profile.demand_mix]
    for signal in context.normalized_signals:
        if signal.role.value == "numeric_mover":
            if signal.dependency_group == "weather":
                cpct = signal.estimated_pct * weather_scaler
            elif id(signal) in selected_context_ids:
                cpct = signal.estimated_pct
            else:
                continue
        else:
            cpct = 0.0
        score = _signal_impact_score(signal, cpct, context)
        if score > 0.005:
            signal_impacts.append({
                "name": signal.signal_type,
                "pct": round(cpct, 4),
                "role": signal.role.value,
                "group": signal.dependency_group,
            })
    signal_impacts.sort(key=lambda s: -abs(s["pct"]))

    # Regime progress
    cal = context.confidence_calibration
    sample_size = int(cal.get("sample_size", 0) or 0)
    thresholds = REGIME_GRADUATION_THRESHOLDS
    if context.forecast_regime in {ForecastRegime.MINIMAL_COLD_START, ForecastRegime.PROFILED_COLD_START, ForecastRegime.FAST_TRACKED_COLD_START}:
        regime_progress = f"{sample_size}/{thresholds['early_learning_min_samples']} actuals to early_learning"
    elif context.forecast_regime is ForecastRegime.EARLY_LEARNING:
        regime_progress = f"{sample_size}/{thresholds['mature_min_samples']} actuals to mature"
    else:
        regime_progress = "mature"

    digest: dict[str, Any] = {
        "regime": context.forecast_regime.value,
        "regime_progress": regime_progress,
        "baseline": baseline_level,
        "seasonal_pct": round(seasonal_pct, 4),
        "weather_pct": round(applied_weather_pct, 4),
        "weather_learning_pct": round(weather_learning_pct, 4),
        "context_pct": round(context_pct, 4),
        "context_learning_pct": round(context_learning_pct, 4),
        "model_total_pct": round(model_total_pct, 4),
        "total_pct": round(total_pct, 4),
        "top_signals": signal_impacts[:4],
        "service_state": context.resolved_service_state.service_state.value,
        "service_state_source": context.resolved_service_state.state_source,
        "reference_status": reference_status,
        "reference_model": reference_model,
        "source_failure_count": len(context.source_summary.get("source_failures", []) or []),
        "connector_failure_count": len(context.source_summary.get("connector_failures", []) or []),
        "source_freshness": context.source_summary.get("source_freshness", {}),
        "brooklyn_delta": (
            round(forecastable_baseline * brooklyn_effect_pct, 1)
            if brooklyn_effect_pct is not None
            else None
        ),
        "brooklyn_similarity": round(context.brooklyn_similarity_score, 2) if context.brooklyn_similarity_score is not None else None,
        "interval_half_width": round(interval_half_width, 3),
        "width_multiplier": float(cal.get("width_multiplier", 1.0) or 1.0),
        "reference_interval_multiplier": interval_debug["reference_interval_multiplier"],
        "interval_base_half_width": interval_debug["base_half_width"],
        "interval_evidence_score": interval_debug["evidence_score"],
        "interval_evidence_multiplier": interval_debug["evidence_multiplier"],
        "location_volatility_multiplier": interval_debug["location_volatility_multiplier"],
        "climate_volatility_multiplier": interval_debug["climate_volatility_multiplier"],
        "service_state_interval_multiplier": interval_debug["service_state_interval_multiplier"],
        "service_state_risk_score": round(service_state_risk_score, 3),
        "service_state_risk": context.service_state_risk,
        "operator_service_plan": context.operator_service_plan,
        "operator_plan_adjustment": operator_plan_adjustment,
        "weather_profile_multiplier": round(_profile_weather_sensitivity_multiplier(context), 3),
        "learned_brooklyn_adjustment": round(_learned_brooklyn_trust_adjustment(context), 4),
        "learned_interval_adjustment": round(_learned_interval_evidence_adjustment(context), 4),
        "learned_weather_profile_adjustment": round(_learned_weather_profile_adjustment(context), 4),
        "brooklyn_weight": round(_brooklyn_relevance_weight(context), 3) if reference_prediction is not None else None,
    }
    if used_reservation_anchor:
        anchored_reserved = min(expected, reserved_expected)
        digest["component_split"] = {
            "reserved": anchored_reserved,
            "flex": max(0, expected - anchored_reserved),
        }

    prediction_run_id = f"pred_{uuid.uuid4().hex[:12]}"
    candidate = CandidateForecastState(
        operator_id=context.operator_profile.operator_id,
        service_date=context.service_date,
        service_window=context.service_window,
        target_name=context.resolved_target.target_name,
        forecast_expected=expected,
        forecast_low=low,
        forecast_high=high,
        confidence_tier=adjust_confidence_tier(confidence_tier_for_mode(context.horizon_mode), context),
        posture=posture_for_expected_pct(total_pct, context.resolved_service_state.service_state),
        service_state=context.resolved_service_state.service_state,
        service_state_reason=context.resolved_service_state.state_resolution_reason,
        prediction_case=context.prediction_case,
        forecast_regime=context.forecast_regime,
        horizon_mode=context.horizon_mode,
        top_drivers=top_drivers,
        major_uncertainties=major_uncertainties,
        target_definition_confidence=context.resolved_target.target_definition_confidence,
        resolved_source_summary=context.source_summary,
        source_prediction_run_id=prediction_run_id,
        reference_status=reference_status,
        reference_model=reference_model,
        component_predictions=_build_component_predictions(
            prediction_run_id=prediction_run_id,
            total_expected=expected,
            reserved_expected=reserved_expected,
            used_reservation_anchor=used_reservation_anchor,
        ),
    )
    return candidate, digest


def default_regime_for_case(prediction_case: PredictionCase) -> ForecastRegime:
    if prediction_case is PredictionCase.AMBIGUOUS:
        return ForecastRegime.MINIMAL_COLD_START
    if prediction_case in {PredictionCase.BASIC_PROFILE, PredictionCase.RESERVATION_ONLY}:
        return ForecastRegime.PROFILED_COLD_START
    if prediction_case in {
        PredictionCase.IMPORTED_TOTAL_HISTORY,
        PredictionCase.IMPORTED_HISTORY_WITH_RESERVATIONS,
        PredictionCase.IMPORTED_DECOMPOSED_HISTORY,
        PredictionCase.POS_ONLY,
        PredictionCase.POS_AND_RESERVATION,
        PredictionCase.RICH_MANUAL,
    }:
        return ForecastRegime.FAST_TRACKED_COLD_START
    return ForecastRegime.PROFILED_COLD_START


def determine_regime(
    prediction_case: PredictionCase,
    calibration: dict[str, Any],
    baseline_history_depth: int = 0,
) -> ForecastRegime:
    """Graduate regime based on accumulated learning evidence.

    Progression: cold-start base → EARLY_LEARNING (≥5 actuals) → MATURE (≥15 actuals + accuracy).
    """
    base = default_regime_for_case(prediction_case)
    thresholds = REGIME_GRADUATION_THRESHOLDS

    sample_size = int(calibration.get("sample_size", 0) or 0)
    effective_samples = max(sample_size, baseline_history_depth)

    if effective_samples < thresholds["early_learning_min_samples"]:
        return base  # Not enough data to graduate

    # Check for mature
    if effective_samples >= thresholds["mature_min_samples"]:
        error_pct = _safe_float(calibration.get("mean_abs_pct_error"))
        coverage = _safe_float(calibration.get("interval_coverage_rate"))
        if (
            error_pct is not None
            and coverage is not None
            and error_pct <= thresholds["mature_max_error_pct"]
            and coverage >= thresholds["mature_min_coverage"]
        ):
            return ForecastRegime.MATURE_LOCAL_MODEL

    return ForecastRegime.EARLY_LEARNING
