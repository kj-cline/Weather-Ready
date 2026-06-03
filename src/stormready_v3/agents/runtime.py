from __future__ import annotations

from stormready_v3.agents.contracts import PublishGovernorOutput
from stormready_v3.domain.enums import ServiceState
from stormready_v3.domain.models import CandidateForecastState


class PublishGovernor:
    """Bounded publication governor.

    It never suppresses deterministic publication. It only escalates notify or
    adds a reason token when the candidate looks materially risky for operator
    action.
    """

    def govern(self, candidate: CandidateForecastState) -> PublishGovernorOutput:
        if candidate.service_state in {
            ServiceState.CLOSED,
            ServiceState.PARTIAL,
            ServiceState.WEATHER_DISRUPTION,
            ServiceState.PRIVATE_EVENT,
        }:
            return PublishGovernorOutput(
                override_notify=True,
                additional_publish_reason="governor_service_state_attention",
            )
        if candidate.horizon_mode.value == "near_0_3" and candidate.confidence_tier == "very_low":
            return PublishGovernorOutput(
                override_notify=True,
                additional_publish_reason="governor_low_confidence_attention",
            )
        if candidate.horizon_mode.value == "near_0_3" and candidate.posture in {"SOFT", "ELEVATED"}:
            return PublishGovernorOutput(
                override_notify=None,
                additional_publish_reason="governor_actionable_posture",
            )
        return PublishGovernorOutput()
