from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from stormready_v3.storage.db import Database
from stormready_v3.operator_text import communication_payload, communication_text_from_payload

from .equation_links import equation_priority_for_runtime_target
from .hypotheses import LearningHypothesisService
from .memory import ConversationMemoryService


class LearningAgendaService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.memory = ConversationMemoryService(db)
        self.hypotheses = LearningHypothesisService(db)

    def sync(self, *, operator_id: str, reference_date: date) -> None:
        self.hypotheses.sync(operator_id=operator_id)
        self._retire_legacy_hypotheses(operator_id=operator_id)
        self._sync_missing_actuals_reminder(operator_id=operator_id, reference_date=reference_date)
        self._sync_pending_correction_reminder(operator_id=operator_id)
        self._sync_recent_miss_question(operator_id=operator_id)
        self._sync_structured_hypothesis_questions(operator_id=operator_id)
        self._retire_legacy_relevance_agenda(operator_id=operator_id)

    def _sync_missing_actuals_reminder(self, *, operator_id: str, reference_date: date) -> None:
        lookback_start = reference_date - timedelta(days=7)
        lookback_end = reference_date - timedelta(days=1)
        rows = self.db.fetchall(
            """
            SELECT p.service_date, p.forecast_expected
            FROM published_forecast_state p
            LEFT JOIN operator_actuals a
              ON a.operator_id = p.operator_id
             AND a.service_date = p.service_date
             AND a.service_window = p.service_window
            WHERE p.operator_id = ?
              AND p.service_window = 'dinner'
              AND p.service_date BETWEEN ? AND ?
              AND a.actual_row_id IS NULL
            ORDER BY p.service_date DESC
            LIMIT 5
            """,
            [operator_id, lookback_start, lookback_end],
        )
        if not rows:
            self.memory.resolve_agenda_item(
                operator_id=operator_id,
                agenda_key="missing_actuals",
                status="expired",
                resolution_note="no missing actuals remain",
            )
            return
        newest_date = rows[0][0]
        missing_count = len(rows)
        payload = _missing_actuals_payload(
            missing_count=missing_count,
            through_date=newest_date,
        )
        self.memory.upsert_agenda_item(
            operator_id=operator_id,
            agenda_key="missing_actuals",
            agenda_type="missing_actuals",
            question_kind="reminder",
            priority=10,
            communication_payload=payload,
            rationale="Learning quality drops when actual totals are missing.",
            expected_impact="Keeps baseline and calibration learning current.",
        )

    def _sync_pending_correction_reminder(self, *, operator_id: str) -> None:
        row = self.db.fetchone(
            """
            SELECT COUNT(*)
            FROM correction_suggestions
            WHERE operator_id = ?
              AND status = 'pending'
            """,
            [operator_id],
        )
        pending_count = int(row[0] or 0) if row is not None else 0
        if pending_count <= 0:
            self.memory.resolve_agenda_item(
                operator_id=operator_id,
                agenda_key="pending_corrections",
                status="expired",
                resolution_note="no pending corrections remain",
            )
            return
        payload = _pending_correction_payload(pending_count=pending_count)
        self.memory.upsert_agenda_item(
            operator_id=operator_id,
            agenda_key="pending_corrections",
            agenda_type="pending_corrections",
            question_kind="reminder",
            priority=20,
            communication_payload=payload,
            rationale="Accepted corrections improve historical truth quality.",
            expected_impact="Prevents stale wrong labels from staying in learning state.",
        )

    def _sync_recent_miss_question(self, *, operator_id: str) -> None:
        row = self.db.fetchone(
            """
            SELECT e.service_date, e.actual_total_covers, e.forecast_expected, e.error_pct
            FROM prediction_evaluations e
            WHERE e.operator_id = ?
              AND e.service_state_learning_eligibility = 'normal'
              AND ABS(COALESCE(e.error_pct, 0.0)) >= 0.18
              AND NOT EXISTS (
                SELECT 1
                FROM conversation_note_log n
                WHERE n.operator_id = e.operator_id
                  AND n.service_date = e.service_date
              )
            ORDER BY e.evaluated_at DESC
            LIMIT 1
            """,
            [operator_id],
        )
        if row is None:
            return
        service_date = row[0]
        actual_total = int(row[1] or 0)
        forecast_expected = int(row[2] or 0)
        agenda_key = f"recent_miss::{service_date.isoformat()}"
        hypothesis_key = agenda_key
        payload = _recent_miss_payload(
            service_date=service_date,
            forecast_expected=forecast_expected,
            actual_total=actual_total,
        )
        self.memory.upsert_hypothesis(
            operator_id=operator_id,
            hypothesis_key=hypothesis_key,
            confidence="medium",
            hypothesis_value={
                "service_date": service_date.isoformat(),
                "actual_total_covers": actual_total,
                "forecast_expected": forecast_expected,
                "communication_payload": payload,
            },
            evidence={
                "error_pct": float(row[3] or 0.0),
                "source": "prediction_evaluation",
            },
            increment_trigger=False,
        )
        self.memory.upsert_agenda_item(
            operator_id=operator_id,
            agenda_key=agenda_key,
            agenda_type="recent_miss_explanation",
            question_kind="free_text",
            priority=30,
            communication_payload=payload,
            rationale="Recent misses are more useful when the operator can label what changed operationally.",
            expected_impact="Improves future qualitative explanations and correction capture.",
            hypothesis_key=hypothesis_key,
            service_date=service_date,
            cooldown_until=self._hours_from_now(18),
        )

    def _sync_structured_hypothesis_questions(self, *, operator_id: str) -> None:
        hypotheses = self.memory.load_open_hypotheses(operator_id, limit=20)
        for item in hypotheses:
            hypothesis_key = str(item.get("hypothesis_key") or "")
            if not hypothesis_key.startswith("learn::"):
                continue
            hypothesis_value = item.get("hypothesis_value") or {}
            if not isinstance(hypothesis_value, dict):
                continue
            agenda_key = str(hypothesis_value.get("agenda_key") or "")
            question_payload = hypothesis_value.get("communication_payload")
            rendered_question = communication_text_from_payload(question_payload, include_question=True).strip()
            question_kind = str(hypothesis_value.get("question_kind") or "free_text")
            if not agenda_key or not rendered_question:
                continue
            if not bool(hypothesis_value.get("question_ready", True)):
                self.memory.resolve_agenda_item(
                    operator_id=operator_id,
                    agenda_key=agenda_key,
                    status="expired",
                    resolution_note="structured hypothesis no longer marked question_ready",
                )
                continue
            evidence = item.get("evidence") or {}
            count = int(evidence.get("observation_count") or hypothesis_value.get("observation_count") or 0)
            service_date = hypothesis_value.get("last_service_date")
            if isinstance(service_date, str) and service_date:
                try:
                    service_date = date.fromisoformat(service_date)
                except ValueError:
                    service_date = None
            runtime_target = str(hypothesis_value.get("runtime_target") or "")
            target_fact_key = None
            proposed_true_value = None
            proposed_false_value = None
            if runtime_target == "transit_relevance":
                target_fact_key = "transit_relevance"
                proposed_true_value = True
                proposed_false_value = False
            elif runtime_target == "venue_relevance":
                target_fact_key = "venue_relevance"
                proposed_true_value = True
                proposed_false_value = False
            elif runtime_target == "hotel_travel_relevance":
                target_fact_key = "hotel_travel_relevance"
                proposed_true_value = True
                proposed_false_value = False

            self.memory.upsert_agenda_item(
                operator_id=operator_id,
                agenda_key=agenda_key,
                agenda_type="hypothesis_confirmation",
                question_kind=question_kind,
                priority=int(hypothesis_value.get("agenda_priority") or equation_priority_for_runtime_target(runtime_target)),
                communication_payload=question_payload,
                rationale=str(hypothesis_value.get("justification") or f"Repeated operator observations supported this hypothesis {count} times."),
                expected_impact=str(hypothesis_value.get("expected_impact") or ""),
                hypothesis_key=hypothesis_key,
                service_date=service_date,
                target_fact_key=target_fact_key,
                proposed_true_value=proposed_true_value,
                proposed_false_value=proposed_false_value,
                cooldown_until=self._hours_from_now(48 if question_kind == "yes_no" else 36),
            )
            existing = self.db.fetchone(
                """
                SELECT 1
                FROM learning_decision_log
                WHERE operator_id = ?
                  AND decision_type = 'agenda_generated'
                  AND hypothesis_key = ?
                  AND agenda_key = ?
                LIMIT 1
                """,
                [operator_id, hypothesis_key, agenda_key],
            )
            if existing is None:
                self.memory.log_learning_decision(
                    operator_id=operator_id,
                    decision_type="agenda_generated",
                    status="ready",
                    hypothesis_key=hypothesis_key,
                    agenda_key=agenda_key,
                    runtime_target=runtime_target or None,
                    equation_terms=list(hypothesis_value.get("equation_terms") or []),
                    promotion_policy=str(hypothesis_value.get("promotion_policy") or ""),
                    rationale=str(hypothesis_value.get("justification") or "Agenda item generated from a structured learning hypothesis."),
                    evidence=item.get("evidence") or {},
                    action={
                        "question_kind": question_kind,
                        "target_fact_key": target_fact_key,
                        "learning_stage": hypothesis_value.get("learning_stage"),
                        "evidence_sources": hypothesis_value.get("evidence_sources", []),
                    },
                    source_ref="learning_hypothesis_service",
                )

    def _retire_legacy_relevance_agenda(self, *, operator_id: str) -> None:
        for agenda_key in ("confirm_relevance::transit", "confirm_relevance::venue", "confirm_relevance::travel"):
            self.memory.resolve_agenda_item(
                operator_id=operator_id,
                agenda_key=agenda_key,
                status="expired",
                resolution_note="superseded by unified structured location hypothesis flow",
            )

    def _retire_legacy_hypotheses(self, *, operator_id: str) -> None:
        legacy_to_structured = {
            "pattern::transit_or_access_issue": "learn::transit_relevance",
            "pattern::event_impact": "learn::venue_relevance",
            "pattern::hotel_or_travel_pull": "learn::hotel_travel_relevance",
            "pattern::weather_patio_risk": "learn::weather_patio_profile",
            "pattern::staffing_constraint": "learn::service_constraints",
            "pattern::weather_negative": "learn::weather_profile_review",
            "pattern::weather_positive": "learn::weather_profile_review",
            "relevance_mismatch::transit_relevance": "learn::transit_relevance",
            "relevance_mismatch::venue_relevance": "learn::venue_relevance",
            "relevance_mismatch::hotel_travel_relevance": "learn::hotel_travel_relevance",
        }
        for legacy_key, structured_key in legacy_to_structured.items():
            if self.memory.get_hypothesis(operator_id, structured_key) is None:
                continue
            self.memory.resolve_hypothesis(
                operator_id=operator_id,
                hypothesis_key=legacy_key,
                status="superseded",
                resolution_note=f"superseded by {structured_key}",
            )

    @staticmethod
    def _hours_from_now(hours: int) -> datetime:
        return (datetime.now(UTC) + timedelta(hours=hours)).replace(tzinfo=None)


def _missing_actuals_payload(*, missing_count: int, through_date: date) -> dict[str, Any]:
    return communication_payload(
        category="workflow_obligation",
        what_is_true_now=(
            f"I am still missing actual covers for {missing_count} recent dinner"
            f"{'' if missing_count == 1 else 's'}."
        ),
        why_it_matters="Without those actuals, the next forecasts stay looser than they should.",
        what_i_need_from_you=f"Please log actual covers through {through_date}.",
        facts={"missing_count": missing_count, "through_date": through_date.isoformat()},
    )


def _pending_correction_payload(*, pending_count: int) -> dict[str, Any]:
    return communication_payload(
        category="workflow_obligation",
        what_is_true_now=(
            f"I have {pending_count} correction{'' if pending_count == 1 else 's'} waiting for review."
        ),
        why_it_matters="Reviewing them keeps the historical record clean.",
        what_i_need_from_you="Please review the staged corrections.",
        facts={"pending_count": pending_count},
    )


def _recent_miss_payload(
    *,
    service_date: date,
    forecast_expected: int,
    actual_total: int,
) -> dict[str, Any]:
    return communication_payload(
        category="open_question",
        what_is_true_now=(
            f"On {service_date}, the forecast was {forecast_expected} covers and actual covers came in at {actual_total}."
        ),
        why_it_matters="If you label what drove that gap, I can use it next time.",
        one_question="What most explains that gap",
        facts={
            "service_date": service_date.isoformat(),
            "forecast_expected": forecast_expected,
            "actual_total": actual_total,
        },
    )
