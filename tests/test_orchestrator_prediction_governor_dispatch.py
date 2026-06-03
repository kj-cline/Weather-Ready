from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from stormready_v3.agents.base import AgentResult, AgentRole, AgentStatus
from stormready_v3.domain.enums import (
    ForecastRegime,
    HorizonMode,
    PredictionCase,
    ServiceState,
    ServiceWindow,
)
from stormready_v3.domain.models import CandidateForecastState
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator


class _FakeDispatcher:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls = 0

    def dispatch(self, ctx):  # noqa: ANN001
        self.calls += 1
        self.last_ctx = ctx
        return self.result


def _candidate(**overrides) -> CandidateForecastState:
    base = CandidateForecastState(
        operator_id="op1",
        service_date=date(2026, 4, 14),
        service_window=ServiceWindow.DINNER,
        target_name="total_covers",
        forecast_expected=112,
        forecast_low=98,
        forecast_high=126,
        confidence_tier="medium",
        posture="STABLE",
        service_state=ServiceState.NORMAL,
        service_state_reason=None,
        prediction_case=PredictionCase.BASIC_PROFILE,
        forecast_regime=ForecastRegime.EARLY_LEARNING,
        horizon_mode=HorizonMode.NEAR,
        top_drivers=["weather_risk", "walk_in_trend", "service_state_override"],
        major_uncertainties=["weather may still shift"],
        target_definition_confidence="medium",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class OrchestratorPredictionGovernorDispatchTests(unittest.TestCase):
    def _orchestrator(self) -> DeterministicOrchestrator:
        orchestrator = DeterministicOrchestrator.__new__(DeterministicOrchestrator)
        orchestrator.db = object()
        orchestrator._provider = object()
        orchestrator._agent_dispatcher = None
        orchestrator._prediction_governor_dispatcher = None
        return orchestrator

    def test_builds_dispatcher_when_none_is_injected(self) -> None:
        orchestrator = self._orchestrator()
        dispatcher = _FakeDispatcher(
            AgentResult(
                role=AgentRole.PREDICTION_GOVERNOR,
                run_id="run_pg",
                status=AgentStatus.OK,
                outputs=[{
                    "emphasized_driver_indices": [2, 0],
                    "clarification_needed": True,
                    "clarification_question": "Confirm service details.",
                    "uncertainty_notes": ["Weather may still shift."],
                    "governance_path": "ai",
                }],
            )
        )
        candidate = _candidate(source_prediction_run_id="pred_run_1")

        with patch("stormready_v3.orchestration.orchestrator.build_agent_dispatcher", return_value=dispatcher) as mocked:
            result = orchestrator._govern_prediction_candidate(
                candidate=candidate,
                learning_context={"baseline_history_depth": 7},
            )

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(dispatcher.calls, 1)
        self.assertEqual(result.emphasized_driver_indices, [2, 0])
        self.assertTrue(result.clarification_needed)
        self.assertEqual(result.clarification_question, "Confirm service details.")
        self.assertEqual(result.governance_path, "ai")

    def test_uses_deterministic_output_when_dispatch_fails(self) -> None:
        orchestrator = self._orchestrator()
        dispatcher = _FakeDispatcher(
            AgentResult(
                role=AgentRole.PREDICTION_GOVERNOR,
                run_id="run_pg",
                status=AgentStatus.BLOCKED,
                blocked_reason="provider unavailable",
            )
        )
        candidate = _candidate(
            top_drivers=["baseline service window pattern", "weather_disruption_risk", "service_state_override"],
            service_state=ServiceState.PARTIAL,
            service_state_reason="suggestion from service notes",
            target_definition_confidence="low",
            confidence_tier="very_low",
            major_uncertainties=["bookings are sparse"],
        )

        with patch("stormready_v3.orchestration.orchestrator.build_agent_dispatcher", return_value=dispatcher):
            result = orchestrator._govern_prediction_candidate(
                candidate=candidate,
                learning_context={"baseline_history_depth": 1},
            )

        self.assertEqual(result.governance_path, "deterministic_base")
        self.assertEqual(result.emphasized_driver_indices, [2, 1, 0])
        self.assertTrue(result.clarification_needed)
        self.assertEqual(
            result.clarification_question,
            "The service state looks abnormal. Confirming whether service was limited will improve forecast reliability.",
        )
        self.assertIn("component truth is still developing", result.uncertainty_notes)
        self.assertIn("service state may still need operator confirmation", result.uncertainty_notes)


if __name__ == "__main__":
    unittest.main()
