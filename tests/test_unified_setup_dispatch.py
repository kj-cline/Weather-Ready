from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentResult, AgentRole, AgentStatus
from stormready_v3.agents.tools import ToolResult
from stormready_v3.agents.unified import UnifiedAgentService
from stormready_v3.domain.enums import OnboardingState
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


class FakeProvider:
    def is_available(self) -> bool:
        return True


class RecordingDispatcher:
    def __init__(self, first_output: dict[str, Any], second_output: dict[str, Any]) -> None:
        self.first_output = first_output
        self.second_output = second_output
        self.calls: list[AgentContext] = []

    def dispatch(self, ctx: AgentContext) -> AgentResult:
        self.calls.append(ctx)
        output = self.first_output if len(self.calls) == 1 else self.second_output
        return AgentResult(
            role=ctx.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=[output],
        )


class UnifiedSetupDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "unified_setup_dispatch.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        OperatorRepository(self.db).upsert_operator(
            OperatorProfile(
                operator_id="setup_bistro",
                restaurant_name="Setup Bistro",
                canonical_address="10 Main St",
                timezone="America/New_York",
                onboarding_state=OnboardingState.INCOMPLETE,
            )
        )

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_setup_phase_routes_through_conversation_orchestrator(self) -> None:
        dispatcher = RecordingDispatcher(
            first_output={
                "text": "I will check readiness.",
                "tool_calls": [{"name": "check_readiness", "arguments": {}}],
                "suggested_messages": [],
            },
            second_output={
                "text": "You still need typical dinner cover counts.",
                "tool_calls": [],
                "suggested_messages": ["Add cover counts"],
            },
        )
        service = UnifiedAgentService(self.db, provider=FakeProvider(), agent_dispatcher=dispatcher)  # type: ignore[arg-type]
        executed: list[tuple[str | None, str, dict[str, Any]]] = []

        def fake_execute(operator_id: str | None, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            executed.append((operator_id, tool_name, arguments))
            return ToolResult(
                tool_name=tool_name,
                success=True,
                data={
                    "forecast_ready": False,
                    "has_address": True,
                    "has_baselines": False,
                    "improvements": ["Add typical dinner cover counts."],
                },
                message="I checked the setup state.",
            )

        service.executor.execute = fake_execute  # type: ignore[method-assign]

        response = service.respond(
            operator_id="setup_bistro",
            message="Am I ready to forecast?",
            reference_date=date(2026, 4, 14),
        )

        self.assertEqual(response.phase, "setup")
        self.assertEqual(response.text, "You still need typical dinner cover counts.")
        self.assertEqual([item.label for item in response.suggested_messages], ["Add cover counts"])
        self.assertEqual(executed[0][1], "check_readiness")
        self.assertEqual(len(dispatcher.calls), 2)
        first_payload = dict(dispatcher.calls[0].payload)
        self.assertEqual(first_payload["current_state_digest"]["phase"], "setup")
        self.assertEqual(first_payload["current_state_digest"]["identity"]["venue_name"], "Setup Bistro")

        digest_rows = self.db.fetchall(
            """
            SELECT DISTINCT kind
            FROM operator_context_digest
            WHERE operator_id = ?
            ORDER BY kind
            """,
            ["setup_bistro"],
        )
        self.assertEqual([row[0] for row in digest_rows], ["current_state", "temporal"])

    def test_update_profile_tool_forces_readiness_check(self) -> None:
        dispatcher = RecordingDispatcher(
            first_output={
                "text": "I saved that.",
                "tool_calls": [
                    {
                        "name": "update_profile",
                        "arguments": {
                            "restaurant_name": "Setup Bistro",
                            "canonical_address": "10 Main St",
                            "weekly_baselines": {"mon_thu": 90, "fri": 110, "sat": 130, "sun": 80},
                        },
                    }
                ],
                "suggested_messages": [],
            },
            second_output={
                "text": "I saved that and checked readiness.",
                "tool_calls": [],
                "suggested_messages": [],
            },
        )
        service = UnifiedAgentService(self.db, provider=FakeProvider(), agent_dispatcher=dispatcher)  # type: ignore[arg-type]
        executed: list[str] = []

        def fake_execute(operator_id: str | None, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            del arguments
            executed.append(tool_name)
            if tool_name == "update_profile":
                return ToolResult(
                    tool_name=tool_name,
                    success=True,
                    data={"operator_id": operator_id or "setup_bistro"},
                    message="I saved the profile.",
                )
            return ToolResult(
                tool_name=tool_name,
                success=True,
                data={"forecast_ready": True, "has_baselines": True},
                message="I checked the setup state.",
            )

        service.executor.execute = fake_execute  # type: ignore[method-assign]

        response = service.respond(
            operator_id="setup_bistro",
            message="Mon-Thu 90, Friday 110, Saturday 130, Sunday 80.",
            reference_date=date(2026, 4, 14),
        )

        self.assertEqual(response.text, "I saved that and checked readiness.")
        self.assertEqual(executed, ["update_profile", "check_readiness"])
        followup_payload = dict(dispatcher.calls[1].payload)
        self.assertEqual(
            [result["tool"] for result in followup_payload["tool_results"]],
            ["update_profile", "check_readiness"],
        )


if __name__ == "__main__":
    unittest.main()
