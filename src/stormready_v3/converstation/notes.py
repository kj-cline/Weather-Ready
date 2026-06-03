from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
import uuid

from stormready_v3.agents.base import AgentContext, AgentDispatcher, AgentRole, AgentStatus
from stormready_v3.agents.contracts import ConversationCapture
from stormready_v3.conversation.memory import ConversationMemoryService
from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.learning.update import update_service_state_risk_from_observation
from stormready_v3.prediction.engine import day_group_for_date
from stormready_v3.storage.db import Database
from stormready_v3.workflows.corrections import stage_correction_suggestion


def _capture_from_agent_output(
    output: dict[str, object],
    *,
    fallback_note: str,
) -> ConversationCapture | None:
    if not isinstance(output, dict):
        return None

    raw_correction = output.get("suggested_correction")
    suggested_correction: dict[str, str] = {}
    if isinstance(raw_correction, dict):
        suggested_correction = {
            str(key): str(value)
            for key, value in raw_correction.items()
            if value is not None and str(value).strip()
        }

    qualitative_themes = [
        str(item).strip()
        for item in (output.get("qualitative_themes") or [])
        if isinstance(item, str) and item.strip()
    ]
    extracted_facts = output.get("extracted_facts")
    if not isinstance(extracted_facts, dict):
        extracted_facts = {}
    hypothesis_hints = [
        str(item).strip()
        for item in (output.get("hypothesis_hints") or [])
        if isinstance(item, str) and item.strip()
    ]
    observations = [
        dict(item)
        for item in (output.get("observations") or [])
        if isinstance(item, dict)
    ]
    suggested_service_state = output.get("suggested_service_state")
    if suggested_service_state is not None:
        suggested_service_state = str(suggested_service_state)

    return ConversationCapture(
        note=str(output.get("note") or fallback_note).strip() or None,
        suggested_service_state=suggested_service_state,
        suggested_correction=suggested_correction,
        qualitative_themes=qualitative_themes,
        extracted_facts=dict(extracted_facts),
        hypothesis_hints=hypothesis_hints,
        observations=observations,
    )


@dataclass(slots=True)
class ConversationNoteResult:
    capture: ConversationCapture
    service_state_logged: bool = False
    correction_suggestion_id: int | None = None


class ConversationNoteService:
    def __init__(
        self,
        db: Database,
        *,
        agent_dispatcher: AgentDispatcher | None = None,
    ) -> None:
        self.db = db
        self.agent_dispatcher = agent_dispatcher
        self.memory = ConversationMemoryService(db)

    def record_note(
        self,
        *,
        operator_id: str,
        note: str,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
        service_state_override: str | None = None,
    ) -> ConversationNoteResult:
        capture = self._capture_note(
            operator_id=operator_id,
            note=note,
            service_date=service_date,
            service_window=service_window,
        )
        # If the chat tool supplied an explicit service state, prefer it.
        if service_state_override:
            capture = ConversationCapture(
                note=capture.note,
                suggested_service_state=service_state_override,
                suggested_correction=capture.suggested_correction,
                qualitative_themes=capture.qualitative_themes,
                extracted_facts=capture.extracted_facts,
                hypothesis_hints=capture.hypothesis_hints,
                observations=capture.observations,
            )
        self.db.execute(
            """
            INSERT INTO conversation_note_log (
                operator_id, service_date, service_window, raw_note, suggested_service_state, suggested_correction_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value if service_window is not None else None,
                note,
                capture.suggested_service_state,
                json.dumps(capture.suggested_correction),
            ],
        )
        note_row = self.db.fetchone("SELECT max(note_id) FROM conversation_note_log")
        note_id = int(note_row[0]) if note_row is not None and note_row[0] is not None else None

        logged = False
        if capture.suggested_service_state and service_date is not None and service_window is not None:
            operator_confirmed = service_state_override is not None
            self.db.execute(
                """
                INSERT INTO service_state_log (
                    operator_id, service_date, service_window, service_state, source_type, source_name,
                    confidence, operator_confirmed, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    service_date,
                    service_window.value,
                    capture.suggested_service_state,
                    "operator_chat" if operator_confirmed else "conversation_capture",
                    "chat_note" if operator_confirmed else "conversation_note_extractor",
                    "high" if operator_confirmed else "low",
                    operator_confirmed,
                    note,
                ],
            )
            logged = True
            if capture.suggested_service_state != ServiceState.NORMAL.value and not operator_confirmed:
                update_service_state_risk_from_observation(
                    self.db,
                    operator_id=operator_id,
                    service_window=service_window.value,
                    day_group=day_group_for_date(service_date),
                    service_state=capture.suggested_service_state,
                    service_date=service_date.isoformat(),
                    observation_weight=0.35,
                )

        correction_suggestion_id = None
        if capture.suggested_correction and service_date is not None and service_window is not None:
            correction_suggestion_id = stage_correction_suggestion(
                self.db,
                operator_id=operator_id,
                service_date=service_date,
                service_window=service_window,
                source_type="conversation_capture",
                suggested_fields=capture.suggested_correction,
                suggested_service_state=capture.suggested_service_state,
                source_note_id=note_id,
            )

        self._record_qualitative_memory(
            operator_id=operator_id,
            note_id=note_id,
            service_date=service_date,
            service_window=service_window,
            capture=capture,
        )

        return ConversationNoteResult(
            capture=capture,
            service_state_logged=logged,
            correction_suggestion_id=correction_suggestion_id,
        )

    def _capture_note(
        self,
        *,
        operator_id: str,
        note: str,
        service_date: date | None,
        service_window: ServiceWindow | None,
    ) -> ConversationCapture:
        note_text = (note or "").strip()
        if not note_text:
            return ConversationCapture(note=None)
        if self.agent_dispatcher is not None:
            ctx = AgentContext(
                role=AgentRole.CONVERSATION_NOTE_EXTRACTOR,
                operator_id=operator_id,
                run_id=f"conversation_note_extractor::{uuid.uuid4()}",
                triggered_at=datetime.now(UTC),
                payload={
                    "note": note_text,
                    "service_date": service_date.isoformat() if service_date is not None else None,
                    "service_window": service_window.value if service_window is not None else None,
                },
            )
            result = self.agent_dispatcher.dispatch(ctx)
            if result.status is AgentStatus.OK and result.outputs:
                capture = _capture_from_agent_output(result.outputs[0], fallback_note=note_text)
                if capture is not None:
                    return capture
        return ConversationCapture(note=note_text)

    def _record_qualitative_memory(
        self,
        *,
        operator_id: str,
        note_id: int | None,
        service_date: date | None,
        service_window: ServiceWindow | None,
        capture: ConversationCapture,
    ) -> None:
        if not capture.note:
            return
        fact_key = (
            f"service_note::{service_date.isoformat()}::{service_window.value if service_window is not None else 'unknown'}"
            if service_date is not None
            else f"conversation_note::{note_id or 'latest'}"
        )
        fact_payload = {
            "note": capture.note,
            "service_date": service_date.isoformat() if service_date is not None else None,
            "service_window": service_window.value if service_window is not None else None,
            "suggested_service_state": capture.suggested_service_state,
            "suggested_correction": capture.suggested_correction,
            "qualitative_themes": capture.qualitative_themes,
            "extracted_facts": capture.extracted_facts,
            "observations": capture.observations,
        }
        self.memory.upsert_fact(
            operator_id=operator_id,
            fact_key=fact_key,
            fact_value=fact_payload,
            confidence="medium",
            provenance="operator_note",
            source_ref=f"conversation_note::{note_id}" if note_id is not None else "conversation_note",
            valid_from_date=service_date,
        )
        self.memory.record_observations(
            operator_id=operator_id,
            source_note_id=note_id,
            service_date=service_date,
            service_window=service_window.value if service_window is not None else None,
            observations=capture.observations,
        )
        for hint in capture.hypothesis_hints:
            self.memory.upsert_hypothesis(
                operator_id=operator_id,
                hypothesis_key=hint,
                confidence="medium",
                hypothesis_value={
                    "service_date": service_date.isoformat() if service_date is not None else None,
                    "themes": capture.qualitative_themes,
                },
                evidence={
                    "note_id": note_id,
                    "source": "operator_note",
                    "extracted_facts": capture.extracted_facts,
                },
                increment_trigger=True,
            )
