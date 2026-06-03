from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .enums import (
    ComponentState,
    DemandMix,
    ForecastRegime,
    HorizonMode,
    NeighborhoodType,
    OnboardingState,
    PredictionCase,
    PublishDecision,
    ServiceState,
    ServiceWindow,
    SignalRole,
    StateDestination,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class OperatorProfile:
    operator_id: str
    restaurant_name: str
    canonical_address: str | None = None
    lat: float | None = None
    lon: float | None = None
    city: str | None = None
    timezone: str | None = None
    primary_service_window: ServiceWindow = ServiceWindow.DINNER
    active_service_windows: list[ServiceWindow] = field(default_factory=lambda: [ServiceWindow.DINNER])
    neighborhood_type: NeighborhoodType = NeighborhoodType.MIXED_URBAN
    demand_mix: DemandMix = DemandMix.MIXED
    indoor_seat_capacity: int | None = None
    patio_enabled: bool = False
    patio_seat_capacity: int | None = None
    patio_season_mode: str | None = None
    setup_mode: str | None = None
    onboarding_state: OnboardingState = OnboardingState.PARTIAL


@dataclass(slots=True)
class LocationContextProfile:
    operator_id: str
    neighborhood_archetype: NeighborhoodType = NeighborhoodType.MIXED_URBAN
    commuter_intensity: float | None = None
    residential_intensity: float | None = None
    transit_relevance: bool = False
    venue_relevance: bool = False
    hotel_travel_relevance: bool = False
    patio_sensitivity_hint: float | None = None
    weather_sensitivity_hint: float | None = None
    demand_volatility_hint: float | None = None


@dataclass(slots=True)
class ResolvedTarget:
    target_name: str = "realized_total_covers"
    available_components: list[str] = field(default_factory=list)
    target_definition_confidence: str = "medium"


@dataclass(slots=True)
class ResolvedServiceState:
    service_state: ServiceState = ServiceState.NORMAL
    state_confidence: str = "medium"
    state_source: str = "default"
    state_resolution_reason: str | None = None
    learning_eligibility: str = "normal"


@dataclass(slots=True)
class NormalizedSignal:
    signal_type: str
    source_name: str
    source_class: str
    dependency_group: str
    role: SignalRole
    source_bucket: str = "weather_core"
    estimated_pct: float = 0.0
    trust_level: str = "medium"
    service_window_overlap: float = 1.0
    scan_scope: str | None = None
    direction: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PredictionContext:
    operator_profile: OperatorProfile
    location_context: LocationContextProfile
    service_date: date
    service_window: ServiceWindow
    resolved_target: ResolvedTarget
    resolved_service_state: ResolvedServiceState
    prediction_case: PredictionCase
    forecast_regime: ForecastRegime
    horizon_mode: HorizonMode
    baseline_total_covers: int | None = None
    resolved_truth_fields: dict[str, Any] = field(default_factory=dict)
    confidence_calibration: dict[str, Any] = field(default_factory=dict)
    normalized_signals: list[NormalizedSignal] = field(default_factory=list)
    current_published_state: dict[str, Any] | None = None
    source_summary: dict[str, Any] = field(default_factory=dict)
    reference_feature_vector: dict[str, float] | None = None
    weather_signature_learning: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_scan_learning: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_reliability: dict[str, dict[str, Any]] = field(default_factory=dict)
    prediction_adaptation_learning: dict[str, dict[str, Any]] = field(default_factory=dict)
    service_state_risk: dict[str, Any] = field(default_factory=dict)
    operator_service_plan: dict[str, Any] = field(default_factory=dict)
    weather_baseline_normals: list[Any] | None = None
    brooklyn_similarity_score: float | None = None


@dataclass(slots=True)
class CandidateForecastState:
    operator_id: str
    service_date: date
    service_window: ServiceWindow
    target_name: str
    forecast_expected: int
    forecast_low: int
    forecast_high: int
    confidence_tier: str
    posture: str
    service_state: ServiceState
    service_state_reason: str | None
    prediction_case: PredictionCase
    forecast_regime: ForecastRegime
    horizon_mode: HorizonMode
    top_drivers: list[str] = field(default_factory=list)
    major_uncertainties: list[str] = field(default_factory=list)
    target_definition_confidence: str = "medium"
    realized_total_truth_quality: str = "estimated"
    component_truth_quality: str = "unknown"
    resolved_source_summary: dict[str, Any] = field(default_factory=dict)
    source_prediction_run_id: str | None = None
    reference_status: str = "not_available"
    reference_model: str | None = None
    component_predictions: list[ComponentPrediction] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PublishedForecastState:
    operator_id: str
    service_date: date
    service_window: ServiceWindow
    state_version: int
    active_service_windows: list[ServiceWindow]
    target_name: str
    forecast_expected: int
    forecast_low: int
    forecast_high: int
    confidence_tier: str
    posture: str
    service_state: ServiceState
    service_state_reason: str | None
    prediction_case: PredictionCase
    forecast_regime: ForecastRegime
    horizon_mode: HorizonMode
    top_drivers: list[str]
    major_uncertainties: list[str]
    target_definition_confidence: str
    realized_total_truth_quality: str
    component_truth_quality: str
    resolved_source_summary: dict[str, Any]
    source_prediction_run_id: str | None
    reference_status: str
    reference_model: str | None
    publish_reason: str
    publish_decision: PublishDecision
    last_published_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PublicationDecision:
    destination: StateDestination
    publish_decision: PublishDecision
    should_notify: bool
    snapshot_reason: str | None = None


@dataclass(slots=True)
class ComponentPrediction:
    prediction_run_id: str
    component_name: str
    component_state: ComponentState
    predicted_value: int | None
    is_operator_visible: bool = False
    is_learning_eligible: bool = False
