from __future__ import annotations

from dataclasses import dataclass, field

from stormready_v3.domain.enums import OnboardingState, PredictionCase, ServiceWindow
from stormready_v3.domain.models import OperatorProfile


@dataclass(slots=True)
class WindowReadiness:
    service_window: ServiceWindow
    is_ready: bool
    reason: str


@dataclass(slots=True)
class SetupReadinessSummary:
    onboarding_state: OnboardingState
    prediction_case: PredictionCase
    forecast_ready: bool
    has_address: bool
    has_baselines: bool
    has_patio: bool
    has_connectors: bool
    improvements: list[str] = field(default_factory=list)


def window_is_forecast_ready(profile: OperatorProfile, service_window: ServiceWindow, has_weekly_baseline: bool) -> WindowReadiness:
    if has_weekly_baseline:
        return WindowReadiness(service_window=service_window, is_ready=True, reason="weekly_baseline_present")
    return WindowReadiness(service_window=service_window, is_ready=False, reason="missing_weekly_baseline")


def summarize_setup_readiness(
    profile: OperatorProfile,
    primary_window_has_baseline: bool,
    has_active_connectors: bool = False,
) -> SetupReadinessSummary:
    onboarding_state = derive_onboarding_state(profile, primary_window_has_baseline, has_active_connectors)
    readiness = window_is_forecast_ready(profile, profile.primary_service_window, primary_window_has_baseline)
    has_patio = profile.patio_enabled and profile.patio_seat_capacity is not None

    # Determine prediction case from what the operator has provided
    if primary_window_has_baseline:
        if has_active_connectors:
            prediction_case = PredictionCase.POS_AND_RESERVATION
        else:
            prediction_case = PredictionCase.BASIC_PROFILE
    else:
        prediction_case = PredictionCase.AMBIGUOUS

    # Build improvement suggestions — what would tighten bands or unlock features
    improvements: list[str] = []
    if not primary_window_has_baseline:
        improvements.append("Adding typical cover counts per day group is required to start forecasting.")
    if not has_patio and profile.patio_enabled:
        improvements.append("Specifying patio seat count improves weather sensitivity for your outdoor seating.")
    if not has_active_connectors and primary_window_has_baseline:
        improvements.append("Connecting a reservation or POS system would further improve forecast accuracy.")

    return SetupReadinessSummary(
        onboarding_state=onboarding_state,
        prediction_case=prediction_case,
        forecast_ready=readiness.is_ready,
        has_address=bool(profile.canonical_address),
        has_baselines=primary_window_has_baseline,
        has_patio=has_patio,
        has_connectors=has_active_connectors,
        improvements=improvements,
    )


def derive_onboarding_state(
    profile: OperatorProfile,
    primary_window_has_baseline: bool,
    has_active_connectors: bool = False,
) -> OnboardingState:
    readiness = window_is_forecast_ready(profile, profile.primary_service_window, primary_window_has_baseline)
    if readiness.is_ready:
        if has_active_connectors:
            return OnboardingState.CONNECTIONS_PENDING
        return OnboardingState.COLD_START_READY
    if profile.canonical_address:
        return OnboardingState.PARTIAL
    return OnboardingState.INCOMPLETE
