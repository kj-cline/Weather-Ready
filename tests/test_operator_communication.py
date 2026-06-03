from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from stormready_v3.agents.tools import ToolExecutor
from stormready_v3.agents.unified import (
    _learning_resolution_text,
    _maybe_resolve_learning_agenda_reply,
)
from stormready_v3.api.service import _build_card_status_map, _serialize_semantic_value
from stormready_v3.conversation.attention import build_operator_attention_summary
from stormready_v3.conversation.memory import ConversationMemoryService
from stormready_v3.conversation.notes import ConversationNoteService
from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.operator_text import (
    communication_payload,
    communication_text_from_payload,
    contains_internal_operator_terms,
    translate_operator_text,
)
from stormready_v3.storage.db import Database
from stormready_v3.surfaces.operator_snapshot import OperatorRuntimeSnapshotService
from stormready_v3.surfaces.workflow_state import load_service_plan_window


class OperatorCommunicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "operator_communication_test.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        self.db.execute(
            "INSERT INTO operators (operator_id, restaurant_name) VALUES (?, ?)",
            ["test_operator", "Test Restaurant"],
        )
        self.memory = ConversationMemoryService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_translate_operator_text_removes_internal_language(self) -> None:
        raw = (
            "Watch for: brooklyn_weather_reference is the main driver. "
            "This forecast strip is running with low forecast confidence."
        )

        translated = translate_operator_text(raw)

        self.assertNotIn("brooklyn_weather_reference", translated)
        self.assertNotIn("forecast strip", translated.lower())
        self.assertNotIn("watch for", translated.lower())
        self.assertNotIn("low forecast confidence", translated.lower())
        self.assertIn("Main driver:", translated)
        self.assertIn("weather is pushing demand away from a normal night", translated)
        self.assertIn("is still settling", translated)
        self.assertFalse(contains_internal_operator_terms(translated))

    def test_communication_text_from_payload_prefers_semantic_blocks(self) -> None:
        payload = communication_payload(
            category="open_question",
            what_is_true_now="Walk-in softness has shown up more than once in recent notes.",
            why_it_matters="That may mean the current demand mix is off on softer nights.",
            one_question="Are walk-ins a larger share of demand on those softer nights than I should assume",
        )

        rendered = communication_text_from_payload(payload, include_question=True)

        self.assertEqual(
            rendered,
            "Walk-in softness has shown up more than once in recent notes. "
            "That may mean the current demand mix is off on softer nights. "
            "Are walk-ins a larger share of demand on those softer nights than I should assume?",
        )

    def test_learning_resolution_text_uses_semantic_payload(self) -> None:
        rendered = _learning_resolution_text(
            recorded_prefix="I recorded your answer.",
            semantic_payload={
                "category": "learning_resolution",
                "why_it_matters": "Going forward, I will treat nearby transit or access as relevant.",
            },
        )

        self.assertEqual(
            rendered,
            "I recorded your answer. Going forward, I will treat nearby transit or access as relevant.",
        )

    def test_serialize_semantic_value_dual_writes_communication_payload_casing(self) -> None:
        serialized = _serialize_semantic_value(
            {
                "summary": "legacy",
                "communication_payload": {
                    "category": "working_signal",
                    "what_is_true_now": "Sample truth",
                },
                "nested": [
                    {
                        "communication_payload": {
                            "category": "open_question",
                            "one_question": "Sample question",
                        }
                    }
                ],
            }
        )

        self.assertEqual(serialized["communicationPayload"]["what_is_true_now"], "Sample truth")
        self.assertEqual(serialized["nested"][0]["communicationPayload"]["one_question"], "Sample question")
        self.assertNotIn("communication_payload", serialized)
        self.assertNotIn("summary", serialized)

    def test_learning_agenda_schema_no_longer_has_question_text_column(self) -> None:
        columns = {
            str(row[1])
            for row in self.db.fetchall("PRAGMA table_info('learning_agenda')")
        }

        self.assertIn("communication_payload_json", columns)
        self.assertNotIn("question_text", columns)

    def test_learning_agenda_round_trip_uses_semantic_payload_only(self) -> None:
        payload = communication_payload(
            category="workflow_obligation",
            what_is_true_now="I am still missing actual covers for 1 recent dinner.",
            why_it_matters="Without that actual, the next forecast stays looser than it should.",
            what_i_need_from_you="Please log actual covers for Apr 11.",
        )

        self.memory.upsert_agenda_item(
            operator_id="test_operator",
            agenda_key="missing_actuals",
            agenda_type="missing_actuals",
            question_kind="reminder",
            communication_payload=payload,
            priority=10,
            rationale="Actuals keep learning current.",
            expected_impact="Tightens the next forecast.",
        )

        items = self.memory.load_learning_agenda("test_operator")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["communication_payload"]["what_i_need_from_you"], "Please log actual covers for Apr 11.")
        self.assertNotIn("question_text", item)

    def test_keyed_learning_card_reply_resolves_without_prior_agent_question(self) -> None:
        agenda_key = "qualitative_pattern::staffing"
        self.memory.upsert_agenda_item(
            operator_id="test_operator",
            agenda_key=agenda_key,
            agenda_type="hypothesis_confirmation",
            question_kind="yes_no",
            communication_payload=communication_payload(
                category="open_question",
                what_is_true_now="Staffing constraints have shown up in a few recent notes.",
                one_question="Is that a recurring issue I should keep in mind",
            ),
            priority=35,
            rationale="Repeated staffing observations suggest a possible recurring constraint.",
        )

        response = _maybe_resolve_learning_agenda_reply(
            self.db,
            ToolExecutor(self.db),
            operator_id="test_operator",
            message="Yes",
            learning_agenda_key=agenda_key,
        )

        self.assertIsNotNone(response)
        resolved_item = self.memory.learning_agenda_item("test_operator", agenda_key, include_resolved=True)
        self.assertIsNotNone(resolved_item)
        self.assertEqual(resolved_item["status"], "resolved")
        fact = next(
            item
            for item in self.memory.load_active_facts("test_operator")
            if item["fact_key"] == f"agenda_answer::{agenda_key}"
        )
        self.assertTrue(fact["fact_value"])

    def test_not_sure_learning_card_reply_closes_question_without_fact(self) -> None:
        agenda_key = "qualitative_pattern::staffing"
        self.memory.upsert_agenda_item(
            operator_id="test_operator",
            agenda_key=agenda_key,
            agenda_type="hypothesis_confirmation",
            question_kind="yes_no",
            communication_payload=communication_payload(
                category="open_question",
                what_is_true_now="Staffing constraints have shown up in a few recent notes.",
                one_question="Is that a recurring issue I should keep in mind",
            ),
            priority=35,
            rationale="Repeated staffing observations suggest a possible recurring constraint.",
        )

        response = _maybe_resolve_learning_agenda_reply(
            self.db,
            ToolExecutor(self.db),
            operator_id="test_operator",
            message="Not sure",
            learning_agenda_key=agenda_key,
        )

        self.assertIsNotNone(response)
        resolved_item = self.memory.learning_agenda_item("test_operator", agenda_key, include_resolved=True)
        self.assertIsNotNone(resolved_item)
        self.assertEqual(resolved_item["status"], "resolved")
        self.assertEqual(self.memory.load_active_facts("test_operator"), [])

    def test_service_plan_window_is_optional_without_saved_entries(self) -> None:
        window = load_service_plan_window(self.db, "test_operator", date.fromisoformat("2026-04-12"))
        self.assertIsNone(window)

    def test_confirmed_service_state_suppresses_stale_open_suggestion(self) -> None:
        self.db.execute(
            """
            INSERT INTO service_state_log (
                operator_id, service_date, service_window, service_state, source_type,
                source_name, confidence, operator_confirmed, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "test_operator",
                date.fromisoformat("2026-04-16"),
                ServiceWindow.DINNER.value,
                ServiceState.CLOSED.value,
                "conversation_capture",
                "conversation_agent",
                "low",
                False,
                "Looks closed",
            ],
        )
        self.db.execute(
            """
            INSERT INTO service_state_log (
                operator_id, service_date, service_window, service_state, source_type,
                source_name, confidence, operator_confirmed, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "test_operator",
                date.fromisoformat("2026-04-16"),
                ServiceWindow.DINNER.value,
                ServiceState.CLOSED.value,
                "operator_manual",
                "service_plan",
                "high",
                True,
                "Operator confirmed closed",
            ],
        )

        suggestions = OperatorRuntimeSnapshotService(self.db)._load_open_service_state_suggestions(
            "test_operator",
            date.fromisoformat("2026-04-13"),
        )

        self.assertEqual(suggestions, [])

    def test_chat_confirmed_service_state_is_not_left_as_open_suggestion(self) -> None:
        service_date = date.fromisoformat("2026-04-16")
        result = ConversationNoteService(self.db).record_note(
            operator_id="test_operator",
            note="Apr 16 dinner is closed.",
            service_date=service_date,
            service_window=ServiceWindow.DINNER,
            service_state_override=ServiceState.CLOSED.value,
        )
        self.assertTrue(result.service_state_logged)

        row = self.db.fetchone(
            """
            SELECT source_type, confidence, operator_confirmed
            FROM service_state_log
            WHERE operator_id = ? AND service_date = ? AND service_window = ?
            ORDER BY created_at DESC, state_log_id DESC
            LIMIT 1
            """,
            ["test_operator", service_date, ServiceWindow.DINNER.value],
        )
        self.assertEqual(row[0], "operator_chat")
        self.assertEqual(row[1], "high")
        self.assertTrue(row[2])

        suggestions = OperatorRuntimeSnapshotService(self.db)._load_open_service_state_suggestions(
            "test_operator",
            date.fromisoformat("2026-04-13"),
        )
        self.assertEqual(suggestions, [])

    def test_confirmed_closed_plan_is_not_marked_stale_after_large_forecast_drop(self) -> None:
        service_date = date.fromisoformat("2026-04-16")
        statuses = _build_card_status_map(
            db=self.db,
            operator_id="test_operator",
            reference_date=date.fromisoformat("2026-04-13"),
            cards=[
                {
                    "service_date": service_date,
                    "service_window": ServiceWindow.DINNER.value,
                    "forecast_expected": 0,
                    "confidence_tier": "medium",
                    "service_state": ServiceState.CLOSED.value,
                    "service_state_reason": "explicit operator input",
                    "change_delta": -140,
                    "last_published_at": "2026-04-13T12:05:00",
                    "major_uncertainties": [],
                }
            ],
            service_plan_window={
                "entries": [
                    {
                        "service_date": service_date,
                        "service_state": ServiceState.CLOSED.value,
                        "planned_total_covers": 0,
                        "reviewed": True,
                        "updated_at": "2026-04-13T12:00:00",
                    }
                ]
            },
            learning_agenda=[],
            open_service_state_suggestions=[],
            engine_digests=[
                {
                    "service_date": service_date,
                    "service_window": ServiceWindow.DINNER.value,
                    "service_state_source": "operator",
                }
            ],
            operating_moment="pre_service",
        )

        key = (service_date.isoformat(), ServiceWindow.DINNER.value)
        self.assertEqual(statuses[key]["planStatus"], "submitted")
        self.assertEqual(statuses[key]["watchStatus"], "none")

    def test_attention_does_not_reconfirm_operator_confirmed_closed_service(self) -> None:
        summary = build_operator_attention_summary(
            reference_date=date.fromisoformat("2026-04-13"),
            current_time=None,
            actionable_forecasts=[
                {
                    "service_date": date.fromisoformat("2026-04-16"),
                    "service_window": ServiceWindow.DINNER.value,
                    "service_state": ServiceState.CLOSED.value,
                    "service_state_source": "operator",
                    "service_state_reason": "explicit operator input",
                    "confidence_tier": "medium",
                    "top_drivers": ["operator service plan"],
                    "major_uncertainties": [],
                }
            ],
            recent_snapshots=[],
            open_service_state_suggestions=[],
            pending_corrections=[],
            missing_actuals=[],
            service_plan_window=None,
            learning_agenda=[],
            open_hypotheses=[],
            recent_learning_decisions=[],
            engine_digests=[],
        )

        self.assertIsNone(summary["current_operational_watchout"])


if __name__ == "__main__":
    unittest.main()
