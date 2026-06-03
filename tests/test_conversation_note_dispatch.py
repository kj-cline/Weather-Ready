from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentResult, AgentRole, AgentStatus
from stormready_v3.agents.tools import ToolExecutor
from stormready_v3.conversation.notes import ConversationNoteService
from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.storage.db import Database


class FakeDispatcher:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[AgentContext] = []

    def dispatch(self, ctx: AgentContext) -> AgentResult:
        self.calls.append(ctx)
        return self.result


def _result(status: AgentStatus, outputs: list[dict[str, Any]] | None = None) -> AgentResult:
    return AgentResult(
        role=AgentRole.CONVERSATION_NOTE_EXTRACTOR,
        run_id="run_note",
        status=status,
        outputs=outputs or [],
    )


class ConversationNoteDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "conversation_note_dispatch.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        self.db.execute(
            "INSERT INTO operators (operator_id, restaurant_name) VALUES (?, ?)",
            ["test_operator", "Test Restaurant"],
        )

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_note_service_uses_dispatcher_output(self) -> None:
        dispatcher = FakeDispatcher(
            _result(
                AgentStatus.OK,
                [{
                    "note": "Total was 118 and walk-ins were soft.",
                    "suggested_correction": {"realized_total_covers": "118"},
                    "qualitative_themes": ["walk_in_softness"],
                    "extracted_facts": {"walk_in_state": "soft"},
                    "observations": [
                        {
                            "observation_type": "walk_in_mix_signal",
                            "dependency_group": "walk_in",
                            "component_scope": "walk_in",
                            "runtime_target": "walk_in_mix_review",
                            "question_target": "walk_in_mix_review",
                            "promotion_mode": "qualitative_memory",
                            "direction": "negative",
                            "strength": "medium",
                            "recurrence_hint": "possible_recurring",
                            "summary": "Walk-ins were softer than expected.",
                        }
                    ],
                    "hypothesis_hints": ["pattern::walk_in_mix"],
                }],
            )
        )

        result = ConversationNoteService(self.db, agent_dispatcher=dispatcher).record_note(
            operator_id="test_operator",
            note="Total was 118 and walk-ins were soft.",
            service_date=date.fromisoformat("2026-04-13"),
            service_window=ServiceWindow.DINNER,
        )

        self.assertEqual(dispatcher.calls[0].role, AgentRole.CONVERSATION_NOTE_EXTRACTOR)
        self.assertEqual(dispatcher.calls[0].payload["note"], "Total was 118 and walk-ins were soft.")
        self.assertEqual(result.capture.suggested_correction, {"realized_total_covers": "118"})
        self.assertEqual(result.capture.extracted_facts, {"walk_in_state": "soft"})
        self.assertIsNotNone(result.correction_suggestion_id)

        row = self.db.fetchone(
            """
            SELECT raw_note, suggested_correction_json
            FROM conversation_note_log
            WHERE operator_id = ?
            """,
            ["test_operator"],
        )
        self.assertEqual(row[0], "Total was 118 and walk-ins were soft.")
        self.assertEqual(row[1], '{"realized_total_covers": "118"}')

    def test_note_service_falls_back_to_raw_note_when_dispatch_blocks(self) -> None:
        dispatcher = FakeDispatcher(_result(AgentStatus.BLOCKED))

        result = ConversationNoteService(self.db, agent_dispatcher=dispatcher).record_note(
            operator_id="test_operator",
            note="Rain kept the patio closed.",
            service_date=date.fromisoformat("2026-04-13"),
            service_window=ServiceWindow.DINNER,
        )

        self.assertEqual(result.capture.note, "Rain kept the patio closed.")
        self.assertEqual(result.capture.suggested_correction, {})
        self.assertEqual(result.capture.extracted_facts, {})
        self.assertIsNone(result.correction_suggestion_id)

    def test_tool_executor_capture_note_passes_dispatcher(self) -> None:
        dispatcher = FakeDispatcher(
            _result(
                AgentStatus.OK,
                [{"note": "Total was 121.", "suggested_correction": {"realized_total_covers": "121"}}],
            )
        )
        executor = ToolExecutor(self.db, agent_dispatcher=dispatcher)

        result = executor.execute(
            "test_operator",
            "capture_note",
            {"note": "Total was 121.", "service_date": "2026-04-13"},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["correction_staged"], True)
        self.assertEqual(dispatcher.calls[0].role, AgentRole.CONVERSATION_NOTE_EXTRACTOR)


if __name__ == "__main__":
    unittest.main()
