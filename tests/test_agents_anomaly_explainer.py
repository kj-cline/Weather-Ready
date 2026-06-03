from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import tempfile
import unittest
from typing import Any

from stormready_v3.agents.anomaly_explainer import AnomalyExplainerAgent
from stormready_v3.agents.base import AgentContext, AgentDispatcher, AgentRole, AgentStatus
from stormready_v3.agents.policy_loader import load_policy
from stormready_v3.storage.db import Database


class FakeAgentModelProvider:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.response = response or {"hypotheses": []}
        self.raises = raises
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
        if self.raises is not None:
            raise self.raises
        return self.response


class CapturingRunLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_run(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _hypothesis(
    proposition: str = "The miss correlates with unmodeled theater overflow near dinner.",
    *,
    category: str = "external_factor_hypothesis",
    confidence: str = "medium",
    dependency_group: str = "venue",
) -> dict[str, Any]:
    return {
        "category": category,
        "proposition": proposition,
        "evidence": "forecast digest and note log both point to a venue-driven mismatch",
        "confidence": confidence,
        "dependency_group": dependency_group,
    }


class AnomalyExplainerAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "anomaly_explainer.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        self.policy = load_policy(AgentRole.ANOMALY_EXPLAINER)

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def _ctx(
        self,
        *,
        error_pct: float = 0.20,
        service_state: str = "normal",
        open_hypotheses: list[dict[str, Any]] | None = None,
    ) -> AgentContext:
        return AgentContext(
            role=AgentRole.ANOMALY_EXPLAINER,
            operator_id="test_operator",
            run_id="run_anomaly",
            triggered_at=datetime.now(UTC),
            payload={
                "prediction_run_id": "pred_1",
                "service_date": date(2026, 4, 17),
                "service_window": "dinner",
                "service_state": service_state,
                "error_pct": error_pct,
                "forecast_expected": 110,
                "forecast_interval": {"low": 95, "high": 125},
                "actual_total": 88,
                "forecast_digest": {"top_signals": []},
                "recent_notes": [],
                "open_hypotheses": open_hypotheses or [],
            },
        )

    def _agent(self, provider: FakeAgentModelProvider) -> AnomalyExplainerAgent:
        return AnomalyExplainerAgent(self.policy, provider)

    def test_large_normal_miss_dispatches_and_returns_hypothesis(self) -> None:
        provider = FakeAgentModelProvider({"hypotheses": [_hypothesis()]})

        result = self._agent(provider).run(self._ctx())

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(result.outputs), 1)
        self.assertEqual(result.outputs[0]["origin"], "anomaly_explainer")

    def test_small_error_skips_provider(self) -> None:
        provider = FakeAgentModelProvider({"hypotheses": [_hypothesis()]})

        result = self._agent(provider).run(self._ctx(error_pct=0.10))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_abnormal_service_state_skips_provider(self) -> None:
        provider = FakeAgentModelProvider({"hypotheses": [_hypothesis()]})

        result = self._agent(provider).run(self._ctx(service_state="slow_night"))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_duplicate_open_hypothesis_is_deduplicated(self) -> None:
        proposition = "Transit reroute reduced nearby walk ins"
        provider = FakeAgentModelProvider({"hypotheses": [_hypothesis(proposition)]})

        result = self._agent(provider).run(
            self._ctx(
                open_hypotheses=[
                    {
                        "hypothesis_key": "transit_reroute_reduced_nearby_walk_ins",
                        "proposition": proposition,
                    }
                ]
            )
        )

        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_provider_three_hypotheses_accepts_first_two(self) -> None:
        provider = FakeAgentModelProvider(
            {
                "hypotheses": [
                    _hypothesis("First plausible miss driver"),
                    _hypothesis("Second plausible miss driver"),
                    _hypothesis("Third plausible miss driver"),
                ]
            }
        )

        result = self._agent(provider).run(self._ctx())

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(len(result.outputs), 2)

    def test_unknown_category_is_rejected_silently(self) -> None:
        provider = FakeAgentModelProvider(
            {"hypotheses": [_hypothesis(category="unknown_category")]}
        )

        result = self._agent(provider).run(self._ctx())

        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_provider_raises_returns_failed_and_dispatcher_logs(self) -> None:
        provider = FakeAgentModelProvider(raises=RuntimeError("provider boom"))
        logger = CapturingRunLogger()
        dispatcher = AgentDispatcher(
            agents={AgentRole.ANOMALY_EXPLAINER: self._agent(provider)},
            run_logger=logger,
        )

        result = dispatcher.dispatch(self._ctx())

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(len(logger.records), 1)
        self.assertEqual(logger.records[0]["status"], "failed")
        self.assertIn("RuntimeError", logger.records[0]["error"])


if __name__ == "__main__":
    unittest.main()
