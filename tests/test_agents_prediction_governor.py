from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.policy_loader import load_policy
from stormready_v3.agents.prediction_governor import PredictionGovernorAgent


class FakeProvider:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def structured_json_call(self, *, system_prompt, user_prompt, max_output_tokens=800):
        del system_prompt, user_prompt, max_output_tokens
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.response


def _ctx(payload: dict[str, Any]) -> AgentContext:
    return AgentContext(
        role=AgentRole.PREDICTION_GOVERNOR,
        operator_id="op1",
        run_id="run_pg",
        triggered_at=datetime.now(UTC),
        payload=payload,
    )


def _base_payload() -> dict[str, Any]:
    return {
        "service_date": "2026-04-14",
        "service_window": "dinner",
        "candidate": {
            "forecast_expected": 112,
            "forecast_low": 98,
            "forecast_high": 126,
            "confidence_tier": "medium",
            "top_drivers": ["weather_risk", "walk_in_trend", "service_state_normal", "seasonality"],
        },
        "recent_actuals_summary": "last 7 nights averaged 108 covers",
        "service_state": "normal",
        "phase": "operations",
    }


class PredictionGovernorAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(AgentRole.PREDICTION_GOVERNOR)

    def test_well_formed_response_passes_subset_check(self) -> None:
        provider = FakeProvider(response={
            "emphasized_drivers": ["weather_risk", "walk_in_trend"],
            "explanation": "Thursday dinner is holding steady with weather risk in play.",
        })
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        out = result.outputs[0]
        self.assertEqual(out["emphasized_drivers"], ["weather_risk", "walk_in_trend"])
        self.assertIn("Thursday", out["explanation"])

    def test_driver_not_in_candidate_is_dropped(self) -> None:
        provider = FakeProvider(response={
            "emphasized_drivers": ["weather_risk", "made_up_driver"],
            "explanation": "Short take.",
        })
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["emphasized_drivers"], ["weather_risk"])

    def test_emphasis_capped_at_three(self) -> None:
        provider = FakeProvider(response={
            "emphasized_drivers": ["weather_risk", "walk_in_trend", "service_state_normal", "seasonality"],
            "explanation": "All four matter.",
        })
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(len(result.outputs[0]["emphasized_drivers"]), 3)

    def test_banned_vocab_in_explanation_clears_it(self) -> None:
        provider = FakeProvider(response={
            "emphasized_drivers": ["weather_risk"],
            "explanation": "The brooklyn_delta is shifting the regime upward.",
        })
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["explanation"], "")

    def test_drivers_as_dicts_are_accepted(self) -> None:
        payload = _base_payload()
        payload["candidate"]["top_drivers"] = [
            {"key": "weather_risk", "magnitude": 0.3},
            {"key": "walk_in_trend", "magnitude": 0.2},
        ]
        provider = FakeProvider(response={
            "emphasized_drivers": ["weather_risk"],
            "explanation": "Weather is the main mover.",
        })
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["emphasized_drivers"], ["weather_risk"])

    def test_missing_candidate_returns_empty(self) -> None:
        provider = FakeProvider(response={"emphasized_drivers": [], "explanation": ""})
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx({"service_date": "2026-04-14"}))
        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_empty_top_drivers_returns_empty(self) -> None:
        payload = _base_payload()
        payload["candidate"]["top_drivers"] = []
        provider = FakeProvider(response={"emphasized_drivers": [], "explanation": ""})
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_provider_exception_returns_failed(self) -> None:
        provider = FakeProvider(raises=RuntimeError("boom"))
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIn("RuntimeError", result.error or "")

    def test_response_none_returns_empty(self) -> None:
        provider = FakeProvider(response=None)
        agent = PredictionGovernorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.EMPTY)


if __name__ == "__main__":
    unittest.main()
