from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import json
from typing import Any

from stormready_v3.conversation.equation_links import equation_link_for_runtime_target
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import ConversationMemoryRepository


def _db_timestamp(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _json_loads(blob: str | None, default: Any) -> Any:
    if not blob:
        return default
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return default


def _hydrate_learning_agenda_item(row: Any) -> dict[str, Any]:
    return {
        "agenda_key": str(row[0]),
        "agenda_type": str(row[1]),
        "status": str(row[2] or "open"),
        "priority": int(row[3] or 50),
        "question_kind": str(row[4] or "free_text"),
        "communication_payload": _json_loads(row[5], None),
        "rationale": str(row[6]) if row[6] is not None else None,
        "expected_impact": str(row[7]) if row[7] is not None else None,
        "hypothesis_key": str(row[8]) if row[8] is not None else None,
        "service_date": row[9],
        "target_fact_key": str(row[10]) if row[10] is not None else None,
        "proposed_true_value": _json_loads(row[11], None),
        "proposed_false_value": _json_loads(row[12], None),
        "cooldown_until": row[13],
        "last_asked_at": row[14],
        "asked_count": int(row[15] or 0),
        "created_at": row[16],
        "last_updated_at": row[17],
    }


class ConversationMemoryService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = ConversationMemoryRepository(db)

    def load_active_facts(self, operator_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT fact_key, fact_value_json, confidence, provenance, source_ref,
                   valid_from_date, expires_at, last_confirmed_at
            FROM operator_fact_memory
            WHERE operator_id = ?
              AND status = 'active'
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY last_updated_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        return [
            {
                "fact_key": str(row[0]),
                "fact_value": _json_loads(row[1], None),
                "confidence": str(row[2] or "medium"),
                "provenance": str(row[3] or ""),
                "source_ref": str(row[4]) if row[4] is not None else None,
                "valid_from_date": row[5],
                "expires_at": row[6],
                "last_confirmed_at": row[7],
            }
            for row in rows
        ]

    def record_observations(
        self,
        *,
        operator_id: str,
        observations: list[dict[str, Any]],
        source_note_id: int | None = None,
        service_date: date | None = None,
        service_window: str | None = None,
    ) -> None:
        if source_note_id is not None:
            self.db.execute(
                """
                DELETE FROM operator_observation_log
                WHERE operator_id = ?
                  AND source_note_id = ?
                """,
                [operator_id, source_note_id],
            )
        for observation in observations:
            self.db.execute(
                """
                INSERT INTO operator_observation_log (
                    operator_id, source_note_id, service_date, service_window,
                    observation_type, dependency_group, component_scope, direction,
                    strength, recurrence_hint, runtime_target, question_target,
                    promotion_mode, observation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    source_note_id,
                    service_date,
                    service_window,
                    observation.get("observation_type"),
                    observation.get("dependency_group"),
                    observation.get("component_scope"),
                    observation.get("direction"),
                    observation.get("strength") or "medium",
                    observation.get("recurrence_hint") or "possible_recurring",
                    observation.get("runtime_target"),
                    observation.get("question_target"),
                    observation.get("promotion_mode") or "qualitative_only",
                    _json_dumps(observation),
                ],
            )

    def load_recent_observations(self, operator_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT observation_type, dependency_group, component_scope, direction,
                   strength, recurrence_hint, runtime_target, question_target,
                   promotion_mode, observation_json, service_date, service_window, created_at
            FROM operator_observation_log
            WHERE operator_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        return [
            {
                "observation_type": str(row[0] or ""),
                "dependency_group": str(row[1] or ""),
                "component_scope": str(row[2] or ""),
                "direction": str(row[3] or ""),
                "strength": str(row[4] or "medium"),
                "recurrence_hint": str(row[5] or "possible_recurring"),
                "runtime_target": str(row[6] or ""),
                "question_target": str(row[7] or ""),
                "promotion_mode": str(row[8] or "qualitative_only"),
                "observation": _json_loads(row[9], {}),
                "service_date": row[10],
                "service_window": row[11],
                "created_at": row[12],
            }
            for row in rows
        ]

    def aggregate_recent_observations(
        self,
        operator_id: str,
        *,
        lookback_days: int = 45,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT runtime_target,
                   question_target,
                   observation_type,
                   dependency_group,
                   component_scope,
                   direction,
                   promotion_mode,
                   COUNT(*) AS observation_count,
                   MAX(created_at) AS last_seen_at,
                   MAX(service_date) AS last_service_date
            FROM operator_observation_log
            WHERE operator_id = ?
              AND created_at >= CURRENT_TIMESTAMP - (? * INTERVAL '1 day')
            GROUP BY 1, 2, 3, 4, 5, 6, 7
            ORDER BY observation_count DESC, last_seen_at DESC
            LIMIT ?
            """,
            [operator_id, lookback_days, limit],
        )
        return [
            {
                "runtime_target": str(row[0] or ""),
                "question_target": str(row[1] or ""),
                "observation_type": str(row[2] or ""),
                "dependency_group": str(row[3] or ""),
                "component_scope": str(row[4] or ""),
                "direction": str(row[5] or ""),
                "promotion_mode": str(row[6] or "qualitative_only"),
                "observation_count": int(row[7] or 0),
                "last_seen_at": row[8],
                "last_service_date": row[9],
            }
            for row in rows
        ]

    def upsert_fact(
        self,
        *,
        operator_id: str,
        fact_key: str,
        fact_value: Any,
        confidence: str = "high",
        provenance: str,
        source_ref: str | None = None,
        valid_from_date: date | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        payload = _json_dumps(fact_value)
        self.repo.upsert_fact(
            operator_id=operator_id,
            fact_key=fact_key,
            fact_value_json=payload,
            confidence=confidence,
            provenance=provenance,
            source_ref=source_ref,
            valid_from_date=valid_from_date,
            expires_at=expires_at,
        )

    def load_open_hypotheses(self, operator_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT hypothesis_key, status, confidence, hypothesis_value_json, evidence_json,
                   trigger_count, last_triggered_at
            FROM operator_hypothesis_state
            WHERE operator_id = ?
              AND status = 'open'
            ORDER BY trigger_count DESC, last_triggered_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        return [
            {
                "hypothesis_key": str(row[0]),
                "status": str(row[1] or "open"),
                "confidence": str(row[2] or "low"),
                "hypothesis_value": _json_loads(row[3], None),
                "evidence": _json_loads(row[4], {}),
                "trigger_count": int(row[5] or 0),
                "last_triggered_at": row[6],
            }
            for row in rows
        ]

    def get_hypothesis(self, operator_id: str, hypothesis_key: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT hypothesis_key, status, confidence, hypothesis_value_json, evidence_json,
                   trigger_count, last_triggered_at
            FROM operator_hypothesis_state
            WHERE operator_id = ? AND hypothesis_key = ?
            """,
            [operator_id, hypothesis_key],
        )
        if row is None:
            return None
        return {
            "hypothesis_key": str(row[0]),
            "status": str(row[1] or "open"),
            "confidence": str(row[2] or "low"),
            "hypothesis_value": _json_loads(row[3], None),
            "evidence": _json_loads(row[4], {}),
            "trigger_count": int(row[5] or 0),
            "last_triggered_at": row[6],
        }

    def log_learning_decision(
        self,
        *,
        operator_id: str,
        decision_type: str,
        status: str,
        rationale: str,
        hypothesis_key: str | None = None,
        agenda_key: str | None = None,
        runtime_target: str | None = None,
        equation_terms: list[str] | None = None,
        promotion_policy: str | None = None,
        evidence: Any = None,
        action: Any = None,
        source_ref: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO learning_decision_log (
                operator_id, decision_type, status, hypothesis_key, agenda_key,
                runtime_target, promotion_policy, rationale, evidence_json, action_json, source_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                decision_type,
                status,
                hypothesis_key,
                agenda_key,
                runtime_target,
                promotion_policy,
                rationale,
                _json_dumps({"equation_terms": equation_terms or [], **(evidence or {})}),
                _json_dumps(action or {}),
                source_ref,
            ],
        )

    def load_recent_learning_decisions(self, operator_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT decision_type, status, hypothesis_key, agenda_key, runtime_target,
                   promotion_policy, rationale, evidence_json, action_json, source_ref, created_at
            FROM learning_decision_log
            WHERE operator_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        return [
            _hydrate_learning_decision(
                {
                "decision_type": str(row[0] or ""),
                "status": str(row[1] or ""),
                "hypothesis_key": str(row[2]) if row[2] is not None else None,
                "agenda_key": str(row[3]) if row[3] is not None else None,
                "runtime_target": str(row[4]) if row[4] is not None else None,
                "promotion_policy": str(row[5]) if row[5] is not None else None,
                "rationale": str(row[6] or ""),
                "evidence": _json_loads(row[7], {}),
                "action": _json_loads(row[8], {}),
                "source_ref": str(row[9]) if row[9] is not None else None,
                "created_at": row[10],
                }
            )
            for row in rows
        ]

    def upsert_hypothesis(
        self,
        *,
        operator_id: str,
        hypothesis_key: str,
        confidence: str = "low",
        hypothesis_value: Any = None,
        evidence: Any = None,
        increment_trigger: bool = True,
    ) -> None:
        existing = self.db.fetchone(
            """
            SELECT trigger_count
            FROM operator_hypothesis_state
            WHERE operator_id = ? AND hypothesis_key = ?
            """,
            [operator_id, hypothesis_key],
        )
        hypothesis_value_json = _json_dumps(hypothesis_value) if hypothesis_value is not None else None
        evidence_json = _json_dumps(evidence or {})
        if existing is None:
            self.db.execute(
                """
                INSERT INTO operator_hypothesis_state (
                    operator_id, hypothesis_key, status, confidence, hypothesis_value_json,
                    evidence_json, trigger_count, last_triggered_at, created_at, last_updated_at
                ) VALUES (?, ?, 'open', ?, ?, ?, 1, ?, ?, ?)
                """,
                [
                    operator_id,
                    hypothesis_key,
                    confidence,
                    hypothesis_value_json,
                    evidence_json,
                    _db_timestamp(),
                    _db_timestamp(),
                    _db_timestamp(),
                ],
            )
            return
        prior_trigger_count = int(existing[0] or 0)
        next_trigger_count = prior_trigger_count + 1 if increment_trigger else prior_trigger_count
        self.db.execute(
            """
            UPDATE operator_hypothesis_state
            SET status = 'open',
                confidence = ?,
                hypothesis_value_json = ?,
                evidence_json = ?,
                trigger_count = ?,
                last_triggered_at = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND hypothesis_key = ?
            """,
            [
                confidence,
                hypothesis_value_json,
                evidence_json,
                next_trigger_count,
                _db_timestamp(),
                _db_timestamp(),
                operator_id,
                hypothesis_key,
            ],
        )

    def resolve_hypothesis(self, *, operator_id: str, hypothesis_key: str, status: str, resolution_note: str | None = None) -> None:
        self.db.execute(
            """
            UPDATE operator_hypothesis_state
            SET status = ?,
                resolved_at = ?,
                resolution_note = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND hypothesis_key = ?
            """,
            [
                status,
                _db_timestamp(),
                resolution_note,
                _db_timestamp(),
                operator_id,
                hypothesis_key,
            ],
        )

    def upsert_agenda_item(
        self,
        *,
        operator_id: str,
        agenda_key: str,
        agenda_type: str,
        question_kind: str,
        communication_payload: Any = None,
        priority: int,
        rationale: str | None = None,
        expected_impact: str | None = None,
        hypothesis_key: str | None = None,
        service_date: date | None = None,
        target_fact_key: str | None = None,
        proposed_true_value: Any = None,
        proposed_false_value: Any = None,
        cooldown_until: datetime | None = None,
    ) -> None:
        existing = self.db.fetchone(
            """
            SELECT agenda_id, status, last_asked_at, asked_count
            FROM learning_agenda
            WHERE operator_id = ? AND agenda_key = ?
            """,
            [operator_id, agenda_key],
        )
        communication_blob = _json_dumps(communication_payload) if communication_payload is not None else None
        true_blob = _json_dumps(proposed_true_value) if proposed_true_value is not None else None
        false_blob = _json_dumps(proposed_false_value) if proposed_false_value is not None else None
        if existing is None:
            self.db.execute(
                """
                INSERT INTO learning_agenda (
                    operator_id, agenda_key, agenda_type, status, priority, question_kind,
                    communication_payload_json, rationale, expected_impact, hypothesis_key, service_date, target_fact_key,
                    proposed_true_value_json, proposed_false_value_json, cooldown_until,
                    created_at, last_updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    agenda_key,
                    agenda_type,
                    priority,
                    question_kind,
                    communication_blob,
                    rationale,
                    expected_impact,
                    hypothesis_key,
                    service_date,
                    target_fact_key,
                    true_blob,
                    false_blob,
                    cooldown_until,
                    _db_timestamp(),
                    _db_timestamp(),
                ],
            )
            return
        self.db.execute(
            """
            UPDATE learning_agenda
            SET agenda_type = ?,
                status = CASE WHEN status = 'resolved' THEN status ELSE 'open' END,
                priority = ?,
                question_kind = ?,
                communication_payload_json = ?,
                rationale = ?,
                expected_impact = ?,
                hypothesis_key = ?,
                service_date = ?,
                target_fact_key = ?,
                proposed_true_value_json = ?,
                proposed_false_value_json = ?,
                cooldown_until = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND agenda_key = ?
            """,
            [
                agenda_type,
                priority,
                question_kind,
                communication_blob,
                rationale,
                expected_impact,
                hypothesis_key,
                service_date,
                target_fact_key,
                true_blob,
                false_blob,
                cooldown_until,
                _db_timestamp(),
                operator_id,
                agenda_key,
            ],
        )

    def resolve_agenda_item(
        self,
        *,
        operator_id: str,
        agenda_key: str,
        resolution_note: str | None = None,
        status: str = "resolved",
    ) -> None:
        self.db.execute(
            """
            UPDATE learning_agenda
            SET status = ?,
                resolved_at = ?,
                resolution_note = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND agenda_key = ?
            """,
            [
                status,
                _db_timestamp(),
                resolution_note,
                _db_timestamp(),
                operator_id,
                agenda_key,
            ],
        )

    def mark_agenda_asked(
        self,
        *,
        operator_id: str,
        agenda_key: str,
        cooldown_hours: int = 24,
    ) -> None:
        self.db.execute(
            """
            UPDATE learning_agenda
            SET last_asked_at = ?,
                asked_count = COALESCE(asked_count, 0) + 1,
                cooldown_until = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND agenda_key = ?
            """,
            [
                _db_timestamp(),
                _db_timestamp(datetime.now(UTC) + timedelta(hours=cooldown_hours)),
                _db_timestamp(),
                operator_id,
                agenda_key,
            ],
        )

    def load_learning_agenda(
        self,
        operator_id: str,
        *,
        include_resolved: bool = False,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        where_clause = "" if include_resolved else "AND status IN ('open', 'snoozed')"
        rows = self.db.fetchall(
            f"""
            SELECT agenda_key, agenda_type, status, priority, question_kind,
                   communication_payload_json, rationale, expected_impact, hypothesis_key, service_date, target_fact_key,
                   proposed_true_value_json, proposed_false_value_json, cooldown_until,
                   last_asked_at, asked_count, created_at, last_updated_at
            FROM learning_agenda
            WHERE operator_id = ?
              {where_clause}
            ORDER BY
                CASE status WHEN 'open' THEN 0 WHEN 'snoozed' THEN 1 ELSE 2 END,
                priority ASC,
                last_updated_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        return [_hydrate_learning_agenda_item(row) for row in rows]

    def learning_agenda_item(
        self,
        operator_id: str,
        agenda_key: str,
        *,
        include_resolved: bool = False,
    ) -> dict[str, Any] | None:
        where_clause = "" if include_resolved else "AND status IN ('open', 'snoozed')"
        row = self.db.fetchone(
            f"""
            SELECT agenda_key, agenda_type, status, priority, question_kind,
                   communication_payload_json, rationale, expected_impact, hypothesis_key, service_date, target_fact_key,
                   proposed_true_value_json, proposed_false_value_json, cooldown_until,
                   last_asked_at, asked_count, created_at, last_updated_at
            FROM learning_agenda
            WHERE operator_id = ?
              AND agenda_key = ?
              {where_clause}
            LIMIT 1
            """,
            [operator_id, agenda_key],
        )
        return _hydrate_learning_agenda_item(row) if row is not None else None

    def current_learning_question(self, operator_id: str) -> dict[str, Any] | None:
        items = [
            item
            for item in self.load_learning_agenda(operator_id, limit=10)
            if item.get("status") == "open"
            and item.get("question_kind") in {"yes_no", "free_text"}
            and not self._is_in_cooldown(item)
        ]
        return items[0] if items else None

    def current_reminders(self, operator_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        reminders = [
            item
            for item in self.load_learning_agenda(operator_id, limit=limit * 3)
            if item.get("status") == "open"
            and item.get("question_kind") == "reminder"
            and not self._is_in_cooldown(item)
        ]
        return reminders[:limit]

    def most_recent_asked_question(self, operator_id: str, *, max_age_hours: int = 72) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT agenda_key, agenda_type, status, priority, question_kind,
                   communication_payload_json, rationale, expected_impact, hypothesis_key, service_date, target_fact_key,
                   proposed_true_value_json, proposed_false_value_json, cooldown_until,
                   last_asked_at, asked_count, created_at, last_updated_at
            FROM learning_agenda
            WHERE operator_id = ?
              AND status = 'open'
              AND last_asked_at IS NOT NULL
            ORDER BY last_asked_at DESC
            LIMIT 1
            """,
            [operator_id],
        )
        if row is None:
            return None
        item = _hydrate_learning_agenda_item(row)
        asked_at = item.get("last_asked_at")
        if asked_at is None:
            return None
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=max_age_hours)
        if asked_at < cutoff:
            return None
        return item

    @staticmethod
    def _is_in_cooldown(item: dict[str, Any]) -> bool:
        cooldown_until = item.get("cooldown_until")
        if cooldown_until is None:
            return False
        return cooldown_until > datetime.now(UTC).replace(tzinfo=None)


def _hydrate_learning_decision(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("evidence") or {}
    runtime_target = record.get("runtime_target")
    link = equation_link_for_runtime_target(str(runtime_target) if runtime_target else None)
    record["equation_terms"] = list(evidence.get("equation_terms") or link.get("equation_terms", []))
    record["equation_path"] = str(evidence.get("equation_path") or link.get("equation_path") or "")
    record["equation_influence_mode"] = str(
        evidence.get("equation_influence_mode") or link.get("influence_mode") or "qualitative_only"
    )
    return record
