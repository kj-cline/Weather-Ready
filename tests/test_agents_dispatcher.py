from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from typing import Any

from stormready_v3.agents.base import (
    AgentContext,
    AgentDispatcher,
    AgentPolicy,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)
from stormready_v3.storage.db import Database


class FakeAgentModelProvider:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def structured_json_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 800,
    ) -> dict[str, Any] | None:
        del system_prompt, user_prompt, max_output_tokens
        return {}


class CapturingRunLogger:
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.records: list[dict[str, Any]] = []

    def record_run(self, **kwargs: Any) -> None:
        if self.raises:
            raise RuntimeError("logger failed")
        self.records.append(kwargs)


def _policy(role: AgentRole = AgentRole.SIGNAL_INTERPRETER) -> AgentPolicy:
    return AgentPolicy(
        role=role,
        version=1,
        description="test",
        trigger="test",
        allowed_writes=("agent_run_log",),
        forbidden_writes=(),
        allowed_categories=("local_event_signal",),
        forbidden_source_classes=(),
        max_outputs_per_run=2,
        max_tokens=100,
        tier1_max_strength_per_signal=0.05,
        tier1_max_strength_total=0.15,
        requires_confirmation_when=(),
        system_prompt_body="test",
    )


class RecordingAgent(BaseAgent):
    role = AgentRole.SIGNAL_INTERPRETER

    def __init__(self, provider: FakeAgentModelProvider) -> None:
        super().__init__(_policy(), provider)
        self.calls = 0

    def run(self, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[{"ok": True}],
            tokens_used=3,
        )


class RaisingAgent(RecordingAgent):
    def run(self, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        raise RuntimeError("agent boom")


class AgentDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "dispatcher.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def _ctx(self, role: AgentRole = AgentRole.SIGNAL_INTERPRETER) -> AgentContext:
        return AgentContext(
            role=role,
            operator_id="test_operator",
            run_id="run_dispatcher",
            triggered_at=datetime.now(UTC),
            payload={},
        )

    def test_provider_unavailable_blocks_without_agent_run_and_logs(self) -> None:
        agent = RecordingAgent(FakeAgentModelProvider(available=False))
        logger = CapturingRunLogger()
        dispatcher = AgentDispatcher(
            agents={AgentRole.SIGNAL_INTERPRETER: agent},
            run_logger=logger,
        )

        result = dispatcher.dispatch(self._ctx())

        self.assertEqual(result.status, AgentStatus.BLOCKED)
        self.assertEqual(result.blocked_reason, "provider unavailable")
        self.assertEqual(agent.calls, 0)
        self.assertEqual(logger.records[0]["blocked_reason"], "provider unavailable")

    def test_agent_raises_returns_failed_and_logs_error(self) -> None:
        logger = CapturingRunLogger()
        dispatcher = AgentDispatcher(
            agents={AgentRole.SIGNAL_INTERPRETER: RaisingAgent(FakeAgentModelProvider())},
            run_logger=logger,
        )

        result = dispatcher.dispatch(self._ctx())

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIn("RuntimeError", result.error)
        self.assertEqual(logger.records[0]["status"], "failed")

    def test_run_logger_raises_preserves_original_result(self) -> None:
        dispatcher = AgentDispatcher(
            agents={AgentRole.SIGNAL_INTERPRETER: RecordingAgent(FakeAgentModelProvider())},
            run_logger=CapturingRunLogger(raises=True),
        )

        result = dispatcher.dispatch(self._ctx())

        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs, [{"ok": True}])

    def test_unknown_role_is_blocked_and_logged(self) -> None:
        logger = CapturingRunLogger()
        dispatcher = AgentDispatcher(agents={}, run_logger=logger)

        result = dispatcher.dispatch(self._ctx())

        self.assertEqual(result.status, AgentStatus.BLOCKED)
        self.assertIn("no agent registered", result.blocked_reason or "")
        self.assertEqual(logger.records[0]["status"], "blocked")
        self.assertIn("no agent registered", logger.records[0]["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
