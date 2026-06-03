from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.policy_loader import load_policy
from stormready_v3.agents.temporal_memory_retriever import TemporalMemoryRetrieverAgent


class FakeProvider:
    def __init__(self, response=None, available=True, raises=None):
        self.response = response
        self.available = available
        self.raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def structured_json_call(self, *, system_prompt, user_prompt, max_output_tokens=800):
        del system_prompt, user_prompt, max_output_tokens
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.response


def _base_payload() -> dict[str, Any]:
    return {
        "recent_misses_raw": [
            {"service_date": "2026-04-11", "err_pct": -0.22, "service_state": "normal", "short_label": "Thursday dinner underperformed"},
            {"service_date": "2026-04-09", "err_pct": 0.05, "service_state": "normal"},  # below threshold
            {"service_date": "2026-04-08", "err_pct": -0.18, "service_state": "normal"},
            {"service_date": "2026-04-07", "err_pct": 0.30, "service_state": "abnormal"},  # wrong state
            {"service_date": "2026-04-06", "err_pct": -0.14, "service_state": "normal"},
            {"service_date": "2026-04-05", "err_pct": 0.25, "service_state": "normal"},
        ],
        "open_hypotheses": [
            {"hypothesis_key": "k1", "proposition": "Rain before 19:00 removes patio covers.", "status": "open", "confidence": "medium"},
            {"hypothesis_key": "k2", "proposition": "Walk-in mix shifted up.", "status": "open", "confidence": "low"},
            {"hypothesis_key": "k3", "proposition": "Stale thing.", "status": "rejected", "confidence": "low"},
            {"hypothesis_key": "k4", "proposition": "Friday baseline trending high.", "status": "confirmed", "confidence": "high"},
        ],
        "recent_patterns_raw": [
            "Friday walk-ins trending above last month by a few covers.",
            "Rainy Thursday dinners averaging lower than forecast.",
        ],
        "operator_facts_raw": [
            {"key": "venue_patio_share", "value": "roughly one-third of dinner capacity", "confidence": "medium"},
            {"key": "weekend_staff", "value": "light Saturday crew", "confidence": "low"},  # low filtered
        ],
        "learning_agenda_rows": [
            {"agenda_key": "confirm_rain_sensitivity", "prompt": "Did the Thursday rain arrive before 19:00?", "ready_to_ask": True},
        ],
        "actual_count_total": 14,
        "last_conversation_at": "2026-04-13T20:00:00",
        "demoted_sources": [],
        "cascades_live": ["baseline", "weather", "walk_in"],
    }


def _ctx(payload: dict[str, Any]) -> AgentContext:
    return AgentContext(
        role=AgentRole.TEMPORAL_MEMORY_RETRIEVER,
        operator_id="op1",
        run_id="run_b",
        triggered_at=datetime.now(UTC),
        payload=payload,
    )


class TemporalMemoryRetrieverAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(AgentRole.TEMPORAL_MEMORY_RETRIEVER)

    def test_llm_path_accepts_well_formed_digest(self) -> None:
        llm_response = {
            "digest": {
                "conversation_state": "active",
                "recent_misses": [
                    {"service_date": "2026-04-11", "err_pct": -0.22, "state": "normal", "short_label": "Thursday underperformed"}
                ],
                "active_hypotheses": [
                    {"hypothesis_key": "k1", "proposition": "Rain removes patio covers.", "status": "open", "confidence": "medium"}
                ],
                "recent_patterns": ["Friday walk-ins trending up"],
                "operator_facts": [
                    {"key": "venue_patio_share", "value": "one-third of dinner", "confidence": "medium"}
                ],
                "learning_maturity": {"samples": 14, "cascades_live": ["baseline"], "demoted_sources": []},
                "open_questions": [
                    {"agenda_key": "confirm_rain", "prompt": "Rain before 19:00?"}
                ],
                "disclaimers": ["Learning still early"],
            }
        }
        provider = FakeProvider(llm_response)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(provider.calls, 1)
        digest = result.outputs[0]
        self.assertEqual(digest["conversation_state"], "active")
        self.assertEqual(len(digest["recent_misses"]), 1)

    def test_deterministic_filters_misses_below_threshold_and_abnormal(self) -> None:
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        misses = result.outputs[0]["recent_misses"]
        # Qualifying: -0.22, -0.18, -0.14, 0.25 (four). Top 3 by magnitude: 0.25, -0.22, -0.18.
        self.assertEqual(len(misses), 3)
        magnitudes = [abs(m["err_pct"]) for m in misses]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))
        self.assertAlmostEqual(magnitudes[0], 0.25)
        self.assertAlmostEqual(magnitudes[1], 0.22)

    def test_deterministic_sorts_hypotheses_by_confidence(self) -> None:
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        hyps = result.outputs[0]["active_hypotheses"]
        # Rejected filtered out, then sorted high→low.
        self.assertEqual(len(hyps), 3)
        self.assertEqual(hyps[0]["confidence"], "high")
        self.assertEqual(hyps[0]["hypothesis_key"], "k4")

    def test_deterministic_drops_low_confidence_facts(self) -> None:
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        facts = result.outputs[0]["operator_facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "venue_patio_share")

    def test_cold_start_when_few_actuals(self) -> None:
        payload = _base_payload()
        payload["actual_count_total"] = 1
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.outputs[0]["conversation_state"], "cold_start")

    def test_follow_up_state_when_flag_set(self) -> None:
        payload = _base_payload()
        payload["has_pending_followup"] = True
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.outputs[0]["conversation_state"], "follow_up")

    def test_early_learning_disclaimer_added(self) -> None:
        payload = _base_payload()
        payload["actual_count_total"] = 5
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        disclaimers = result.outputs[0]["disclaimers"]
        self.assertTrue(any("early" in d.lower() for d in disclaimers))

    def test_banned_vocabulary_in_llm_scrubbed(self) -> None:
        llm_response = {
            "digest": {
                "conversation_state": "active",
                "recent_misses": [],
                "active_hypotheses": [],
                "recent_patterns": ["Brooklyn_delta trending up", "Walk-in mix shifting"],
                "operator_facts": [],
                "learning_maturity": {"samples": 10},
                "open_questions": [],
                "disclaimers": [],
            }
        }
        provider = FakeProvider(llm_response)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        patterns = result.outputs[0]["recent_patterns"]
        self.assertEqual(len(patterns), 1)
        self.assertIn("walk-in", patterns[0].lower())

    def test_deterministic_fallback_on_provider_exception(self) -> None:
        provider = FakeProvider(raises=RuntimeError("boom"))
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIn("deterministic fallback", result.rationale)

    def test_empty_payload_still_produces_deterministic_digest(self) -> None:
        provider = FakeProvider(response=None)
        agent = TemporalMemoryRetrieverAgent(self.policy, provider)
        result = agent.run(_ctx({}))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["conversation_state"], "cold_start")


if __name__ == "__main__":
    unittest.main()
