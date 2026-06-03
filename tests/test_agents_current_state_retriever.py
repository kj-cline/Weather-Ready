from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.current_state_retriever import CurrentStateRetrieverAgent
from stormready_v3.agents.policy_loader import load_policy


class FakeProvider:
    def __init__(self, response: dict[str, Any] | None = None, available: bool = True, raises: Exception | None = None) -> None:
        self.response = response
        self.available = available
        self.raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def structured_json_call(self, *, system_prompt: str, user_prompt: str, max_output_tokens: int = 800) -> dict[str, Any] | None:
        del system_prompt, user_prompt, max_output_tokens
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.response


def _base_payload() -> dict[str, Any]:
    return {
        "reference_date": date(2026, 4, 14),
        "phase": "operations",
        "identity": {"operator_id": "op1", "venue_name": "Test Bistro", "location": "Brooklyn, NY"},
        "published_forecast": {
            "service_date": "2026-04-14",
            "expected": 112,
            "low": 98,
            "high": 126,
            "confidence": "medium",
        },
        "near_horizon_rows": [
            {"service_date": "2026-04-15", "expected": 108, "low": 92, "high": 124, "state": "normal"},
            {"service_date": "2026-04-16", "expected": 115, "low": 100, "high": 130, "state": "normal"},
        ],
        "open_action_items": [
            {"kind": "submit_actual", "prompt": "Submit last night's covers.", "urgency": "medium"}
        ],
        "active_signals": [
            {"short_label": "Rain risk up for Thursday dinner"},
            {"short_label": "Walk-in mix trending above last month"},
        ],
        "missing_actuals_dates": [date(2026, 4, 12)],
        "operator_maturity_hint": "early — under 10 confirmed nights",
    }


def _ctx(payload: dict[str, Any]) -> AgentContext:
    return AgentContext(
        role=AgentRole.CURRENT_STATE_RETRIEVER,
        operator_id="op1",
        run_id="run_a",
        triggered_at=datetime.now(UTC),
        payload=payload,
    )


class CurrentStateRetrieverAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(AgentRole.CURRENT_STATE_RETRIEVER)

    def test_llm_path_produces_ok_result(self) -> None:
        llm_response = {
            "digest": {
                "reference_date": "2026-04-14",
                "phase": "operations",
                "identity": {"operator_id": "op1"},
                "headline_forecast": {"expected": 112, "low": 98, "high": 126, "confidence": "medium"},
                "near_horizon": [{"service_date": "2026-04-15", "expected": 108}],
                "pending_action": {"kind": "submit_actual", "prompt": "Submit last night", "urgency": "medium"},
                "current_uncertainty": "Weather widening Thursday band.",
                "active_signals_summary": ["Rain risk up Thursday"],
                "missing_actuals": ["2026-04-12"],
                "disclaimers": ["Learning is early."],
            }
        }
        provider = FakeProvider(llm_response)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(provider.calls, 1)
        digest = result.outputs[0]
        self.assertEqual(digest["phase"], "operations")
        self.assertEqual(digest["headline_forecast"]["expected"], 112)
        self.assertIn("rain risk up thursday", digest["active_signals_summary"][0].lower())
        self.assertIn("rationale", result.__dict__)

    def test_deterministic_fallback_on_provider_none(self) -> None:
        provider = FakeProvider(response=None)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        digest = result.outputs[0]
        self.assertEqual(digest["headline_forecast"]["expected"], 112)
        self.assertEqual(digest["pending_action"]["kind"], "submit_actual")
        self.assertTrue(len(digest["active_signals_summary"]) >= 1)

    def test_deterministic_fallback_on_provider_exception(self) -> None:
        provider = FakeProvider(raises=RuntimeError("boom"))
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIn("deterministic fallback", result.rationale)
        self.assertIn("RuntimeError", result.rationale)

    def test_missing_reference_date_returns_empty(self) -> None:
        payload = _base_payload()
        payload["reference_date"] = None
        provider = FakeProvider(response={"digest": {}})
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_near_horizon_is_capped(self) -> None:
        payload = _base_payload()
        payload["near_horizon_rows"] = [
            {"service_date": f"2026-04-{15 + i}", "expected": 100 + i}
            for i in range(10)
        ]
        provider = FakeProvider(response=None)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertLessEqual(len(result.outputs[0]["near_horizon"]), 5)

    def test_banned_vocabulary_in_llm_output_is_scrubbed(self) -> None:
        llm_response = {
            "digest": {
                "reference_date": "2026-04-14",
                "phase": "operations",
                "identity": {},
                "headline_forecast": None,
                "near_horizon": [],
                "pending_action": None,
                "current_uncertainty": None,
                "active_signals_summary": [
                    "Brooklyn_delta shifted upward",
                    "Walk-in trending above last month",
                ],
                "missing_actuals": [],
                "disclaimers": ["Regime progress advancing"],
            }
        }
        provider = FakeProvider(llm_response)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        digest = result.outputs[0]
        self.assertEqual(len(digest["active_signals_summary"]), 1)
        self.assertIn("walk-in", digest["active_signals_summary"][0].lower())
        self.assertEqual(digest["disclaimers"], [])

    def test_uncertainty_flagged_when_band_is_wide(self) -> None:
        payload = _base_payload()
        payload["published_forecast"] = {"expected": 100, "low": 40, "high": 160, "confidence": "low"}
        provider = FakeProvider(response=None)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIsNotNone(result.outputs[0]["current_uncertainty"])

    def test_invalid_phase_defaults_to_operations(self) -> None:
        payload = _base_payload()
        payload["phase"] = "bogus"
        provider = FakeProvider(response=None)
        agent = CurrentStateRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["phase"], "operations")


if __name__ == "__main__":
    unittest.main()
