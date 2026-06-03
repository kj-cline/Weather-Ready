from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import tempfile
import unittest
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.policy_loader import load_policy
from stormready_v3.agents.signal_interpreter import SignalInterpreterAgent
from stormready_v3.storage.db import Database


class FakeAgentModelProvider:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {"signals": []}
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def structured_json_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 800,
    ) -> dict[str, Any] | None:
        del system_prompt, user_prompt, max_output_tokens
        self.calls += 1
        return self.response


def _signal(
    *,
    category: str = "local_event_signal",
    dependency_group: str = "venue",
    role: str = "numeric_mover",
    direction: str = "up",
    strength: float = 0.03,
    source_name: str = "demo_source",
) -> dict[str, Any]:
    return {
        "category": category,
        "dependency_group": dependency_group,
        "role": role,
        "direction": direction,
        "strength": strength,
        "service_date": "2026-04-17",
        "source_name": source_name,
        "source_bucket": "broad_proxy",
        "rationale": "grounded demo signal",
    }


class SignalInterpreterAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "signal_interpreter.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        self.policy = load_policy(AgentRole.SIGNAL_INTERPRETER)

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def _ctx(self, payloads: list[dict[str, Any]]) -> AgentContext:
        return AgentContext(
            role=AgentRole.SIGNAL_INTERPRETER,
            operator_id="test_operator",
            run_id="run_signal",
            triggered_at=datetime.now(UTC),
            payload={
                "operator_context": {"neighborhood_type": "mixed_urban"},
                "service_date": date(2026, 4, 17),
                "service_window": "dinner",
                "payloads": payloads,
            },
        )

    def _agent(self, response: dict[str, Any]) -> tuple[SignalInterpreterAgent, FakeAgentModelProvider]:
        provider = FakeAgentModelProvider(response)
        return SignalInterpreterAgent(self.policy, provider), provider

    def _eligible_payload(self, source_class: str = "local_news") -> dict[str, Any]:
        return {
            "source_name": "demo_source",
            "source_class": source_class,
            "source_bucket": "broad_proxy",
            "payload": {"narrative_text": "A venue event is drawing dinner traffic tonight."},
        }

    def test_eligible_payload_produces_observed_signal(self) -> None:
        agent, provider = self._agent({"signals": [_signal()]})

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.outputs[0]["status"], "observed")

    def test_weather_forecast_payload_is_filtered_before_provider_call(self) -> None:
        agent, provider = self._agent({"signals": [_signal()]})

        result = agent.run(self._ctx([self._eligible_payload("weather_forecast")]))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_numeric_weather_signal_is_rejected(self) -> None:
        agent, provider = self._agent(
            {"signals": [_signal(dependency_group="weather", role="numeric_mover")]}
        )

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 1)

    def test_narrative_weather_context_with_strength_is_rejected(self) -> None:
        agent, _provider = self._agent(
            {
                "signals": [
                    _signal(
                        category="narrative_weather_context",
                        dependency_group="weather",
                        role="confidence_mover",
                        direction="neutral",
                        strength=0.03,
                    )
                ]
            }
        )

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_narrative_weather_context_is_accepted_as_proposed_only(self) -> None:
        agent, _provider = self._agent(
            {
                "signals": [
                    _signal(
                        category="narrative_weather_context",
                        dependency_group="weather",
                        role="confidence_mover",
                        direction="neutral",
                        strength=0.0,
                    )
                ]
            }
        )

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["status"], "proposed")

    def test_strength_above_per_signal_cap_is_proposed(self) -> None:
        agent, _provider = self._agent({"signals": [_signal(strength=0.08)]})

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["status"], "proposed")

    def test_total_strength_cap_splits_observed_and_proposed(self) -> None:
        signals = [
            _signal(source_name=f"source_{idx}", strength=0.04)
            for idx in range(5)
        ]
        agent, _provider = self._agent({"signals": signals})

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(
            [item["status"] for item in result.outputs],
            ["observed", "observed", "observed", "proposed", "proposed"],
        )

    def test_novel_unmapped_is_always_proposed(self) -> None:
        agent, _provider = self._agent(
            {
                "signals": [
                    _signal(
                        category="novel_unmapped",
                        dependency_group="local_context",
                        role="confidence_mover",
                        direction="neutral",
                        strength=0.0,
                    )
                ]
            }
        )

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["status"], "proposed")

    def test_unknown_category_is_rejected_silently(self) -> None:
        agent, _provider = self._agent({"signals": [_signal(category="unknown_category")]})

        result = agent.run(self._ctx([self._eligible_payload()]))

        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_empty_payload_list_skips_provider(self) -> None:
        agent, provider = self._agent({"signals": [_signal()]})

        result = agent.run(self._ctx([]))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
