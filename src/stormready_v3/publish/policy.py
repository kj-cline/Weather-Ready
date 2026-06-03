from __future__ import annotations

from datetime import date

from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS, NOTIFICATION_HORIZON_DAYS
from stormready_v3.domain.enums import PublishDecision, RefreshReason, StateDestination
from stormready_v3.domain.models import CandidateForecastState, PublicationDecision


def day_offset(today: date, service_date: date) -> int:
    return (service_date - today).days


def destination_for_day_offset(offset: int) -> StateDestination:
    if offset < ACTIONABLE_HORIZON_DAYS:
        return StateDestination.PUBLISHED
    return StateDestination.WORKING


def notification_allowed(offset: int) -> bool:
    return offset <= NOTIFICATION_HORIZON_DAYS


def decide_publication(
    candidate: CandidateForecastState,
    today: date,
    current_state: tuple | None = None,
    refresh_reason: RefreshReason = RefreshReason.SCHEDULED,
) -> PublicationDecision:
    offset = day_offset(today, candidate.service_date)
    destination = destination_for_day_offset(offset)

    should_notify = False
    snapshot_reasons: list[str] = []

    if destination is StateDestination.PUBLISHED:
        publish_decision = PublishDecision.PUBLISH
        if current_state is None:
            snapshot_reasons.append("initial_publication")
        else:
            prior_expected = int(current_state[6])
            prior_low = int(current_state[7])
            prior_high = int(current_state[8])
            prior_confidence = str(current_state[9])
            prior_posture = str(current_state[10])
            prior_service_state = str(current_state[11])

            midpoint_changed = abs(prior_expected - candidate.forecast_expected) >= 10
            interval_widened = ((candidate.forecast_high - candidate.forecast_low) - (prior_high - prior_low)) >= 10
            confidence_dropped = _confidence_rank(candidate.confidence_tier) < _confidence_rank(prior_confidence)
            posture_changed = prior_posture != candidate.posture
            service_state_changed = prior_service_state != candidate.service_state.value

            if midpoint_changed:
                snapshot_reasons.append("material_change")
            if interval_widened:
                snapshot_reasons.append("interval_widened")
            if confidence_dropped:
                snapshot_reasons.append("confidence_drop")
            if posture_changed:
                snapshot_reasons.append("posture_change")
            if service_state_changed:
                snapshot_reasons.append("service_state_change")

            if notification_allowed(offset) and (
                midpoint_changed
                or interval_widened
                or confidence_dropped
                or posture_changed
                or service_state_changed
            ):
                should_notify = True
                publish_decision = PublishDecision.PUBLISH_AND_NOTIFY
    else:
        publish_decision = PublishDecision.PUBLISH

    if refresh_reason is RefreshReason.EVENT_MODE and destination is StateDestination.PUBLISHED and notification_allowed(offset):
        should_notify = True
        publish_decision = PublishDecision.PUBLISH_AND_NOTIFY
        if "event_mode_refresh" not in snapshot_reasons:
            snapshot_reasons.append("event_mode_refresh")

    return PublicationDecision(
        destination=destination,
        publish_decision=publish_decision,
        should_notify=should_notify,
        snapshot_reason="+".join(snapshot_reasons) if snapshot_reasons else None,
    )


def apply_notification_sensitivity(
    decision: PublicationDecision,
    *,
    notification_sensitivity: float | None,
) -> PublicationDecision:
    if not decision.should_notify or notification_sensitivity is None:
        return decision

    if notification_sensitivity <= 0.3 and not _is_critical_notification_reason(decision.snapshot_reason):
        decision.should_notify = False
        if decision.publish_decision is PublishDecision.PUBLISH_AND_NOTIFY:
            decision.publish_decision = PublishDecision.PUBLISH
    return decision


def _confidence_rank(confidence_tier: str) -> int:
    ranking = {
        "very_low": 0,
        "low": 1,
        "medium": 2,
        "high": 3,
    }
    return ranking.get(confidence_tier, 1)


def _is_critical_notification_reason(snapshot_reason: str | None) -> bool:
    if not snapshot_reason:
        return False
    critical_tokens = {"event_mode_refresh", "service_state_change", "material_change"}
    return any(token in critical_tokens for token in snapshot_reason.split("+"))
