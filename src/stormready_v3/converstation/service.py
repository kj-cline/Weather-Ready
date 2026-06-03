from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Any

from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS
from stormready_v3.conversation.attention import build_operator_attention_summary
from stormready_v3.storage.db import Database
from stormready_v3.surfaces.operator_snapshot import OperatorRuntimeSnapshot, OperatorRuntimeSnapshotService
from stormready_v3.surfaces.workflow_state import load_missing_actuals, load_service_plan_window
from stormready_v3.conversation.agenda import LearningAgendaService
from stormready_v3.conversation.equation_links import prediction_equation_contract, summarize_equation_learning_state
from stormready_v3.conversation.memory import ConversationMemoryService


@dataclass(slots=True)
class ConversationContext:
    operator_id: str
    actionable_forecasts: list[dict[str, Any]] = field(default_factory=list)
    actionable_components: list[dict[str, Any]] = field(default_factory=list)
    confidence_calibration: list[dict[str, Any]] = field(default_factory=list)
    weather_signature_learning: list[dict[str, Any]] = field(default_factory=list)
    external_scan_learning: list[dict[str, Any]] = field(default_factory=list)
    external_catalog_summary: dict[str, Any] = field(default_factory=dict)
    watched_external_sources: list[dict[str, Any]] = field(default_factory=list)
    recent_connector_truths: list[dict[str, Any]] = field(default_factory=list)
    recent_notes: list[dict[str, Any]] = field(default_factory=list)
    pending_corrections: list[dict[str, Any]] = field(default_factory=list)
    missing_actuals: list[dict[str, Any]] = field(default_factory=list)
    service_plan_window: dict[str, Any] | None = None
    open_service_state_suggestions: list[dict[str, Any]] = field(default_factory=list)
    recent_snapshots: list[dict[str, Any]] = field(default_factory=list)
    recent_evaluations: list[dict[str, Any]] = field(default_factory=list)
    operator_preferences: dict[str, Any] = field(default_factory=dict)
    engine_digests: list[dict[str, Any]] = field(default_factory=list)
    runtime_external_signals: list[dict[str, Any]] = field(default_factory=list)
    operator_facts: list[dict[str, Any]] = field(default_factory=list)
    recent_observations: list[dict[str, Any]] = field(default_factory=list)
    open_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    learning_agenda: list[dict[str, Any]] = field(default_factory=list)
    recent_learning_decisions: list[dict[str, Any]] = field(default_factory=list)
    equation_learning_state: list[dict[str, Any]] = field(default_factory=list)
    prediction_equation: dict[str, Any] = field(default_factory=dict)
    operator_attention_summary: dict[str, Any] = field(default_factory=dict)


class ConversationContextService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.snapshot_service = OperatorRuntimeSnapshotService(db)
        self.memory_service = ConversationMemoryService(db)
        self.agenda_service = LearningAgendaService(db)

    def build_context(
        self,
        *,
        operator_id: str,
        reference_date: date,
        snapshot: OperatorRuntimeSnapshot | None = None,
        current_time: datetime | None = None,
    ) -> ConversationContext:
        resolved_snapshot = snapshot or self.snapshot_service.build_snapshot(
            operator_id=operator_id,
            reference_date=reference_date,
        )
        self.agenda_service.sync(operator_id=operator_id, reference_date=reference_date)
        operator_facts = self.memory_service.load_active_facts(operator_id)
        recent_observations = self.memory_service.load_recent_observations(operator_id)
        open_hypotheses = self.memory_service.load_open_hypotheses(operator_id)
        learning_agenda = self.memory_service.load_learning_agenda(operator_id)
        recent_learning_decisions = self.memory_service.load_recent_learning_decisions(operator_id)
        engine_digests = self._load_engine_digests(operator_id, reference_date)
        pending_corrections = self._load_pending_corrections(operator_id)
        recent_snapshots = self._load_recent_snapshots(operator_id)
        missing_actuals = load_missing_actuals(self.db, operator_id, reference_date)
        service_plan_window = load_service_plan_window(self.db, operator_id, reference_date)
        equation_learning_state = summarize_equation_learning_state(
            open_hypotheses=open_hypotheses,
            learning_agenda=learning_agenda,
            recent_learning_decisions=recent_learning_decisions,
            engine_digests=engine_digests,
        )
        return ConversationContext(
            operator_id=operator_id,
            actionable_forecasts=resolved_snapshot.actionable_cards,
            actionable_components=self._load_actionable_components(operator_id, reference_date),
            confidence_calibration=self._load_confidence_calibration(operator_id),
            weather_signature_learning=self._load_weather_signature_learning(operator_id),
            external_scan_learning=self._load_external_scan_learning(operator_id),
            external_catalog_summary=resolved_snapshot.external_catalog_summary,
            watched_external_sources=resolved_snapshot.watched_external_sources,
            recent_connector_truths=self._load_recent_connector_truths(operator_id),
            recent_notes=self._load_recent_notes(operator_id),
            pending_corrections=pending_corrections,
            missing_actuals=missing_actuals,
            service_plan_window=service_plan_window,
            open_service_state_suggestions=resolved_snapshot.open_service_state_suggestions,
            recent_snapshots=recent_snapshots,
            recent_evaluations=self._load_recent_evaluations(operator_id),
            operator_preferences=self._load_operator_preferences(operator_id),
            engine_digests=engine_digests,
            runtime_external_signals=self._load_runtime_external_signals(operator_id, reference_date),
            operator_facts=operator_facts,
            recent_observations=recent_observations,
            open_hypotheses=open_hypotheses,
            learning_agenda=learning_agenda,
            recent_learning_decisions=recent_learning_decisions,
            equation_learning_state=equation_learning_state,
            prediction_equation=prediction_equation_contract(),
            operator_attention_summary=build_operator_attention_summary(
                reference_date=reference_date,
                current_time=current_time,
                actionable_forecasts=resolved_snapshot.actionable_cards,
                recent_snapshots=recent_snapshots,
                open_service_state_suggestions=resolved_snapshot.open_service_state_suggestions,
                pending_corrections=pending_corrections,
                missing_actuals=missing_actuals,
                service_plan_window=service_plan_window,
                learning_agenda=learning_agenda,
                open_hypotheses=open_hypotheses,
                recent_learning_decisions=recent_learning_decisions,
                engine_digests=engine_digests,
            ),
        )

    def _load_actionable_components(self, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT p.service_date, p.service_window, pc.component_name, pc.component_state, pc.predicted_value
            FROM published_forecast_state p
            JOIN prediction_components pc ON pc.prediction_run_id = p.source_prediction_run_id
            WHERE p.operator_id = ?
              AND p.service_date BETWEEN ? AND ?
            ORDER BY p.service_date, p.service_window, pc.component_name
            """,
            [operator_id, reference_date, reference_date.fromordinal(reference_date.toordinal() + ACTIONABLE_HORIZON_DAYS - 1)],
        )
        return [
            {
                "service_date": row[0],
                "service_window": row[1],
                "component_name": row[2],
                "component_state": row[3],
                "predicted_value": row[4],
            }
            for row in rows
        ]

    def _load_confidence_calibration(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_window, horizon_mode, mean_abs_pct_error, interval_coverage_rate,
                   sample_size, width_multiplier, confidence_penalty_steps
            FROM confidence_calibration_state
            WHERE operator_id = ?
            ORDER BY service_window, horizon_mode
            """,
            [operator_id],
        )
        return [
            {
                "service_window": row[0],
                "horizon_mode": row[1],
                "mean_abs_pct_error": row[2],
                "interval_coverage_rate": row[3],
                "sample_size": row[4],
                "width_multiplier": row[5],
                "confidence_penalty_steps": row[6],
            }
            for row in rows
        ]

    def _load_weather_signature_learning(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_window, weather_signature, sensitivity_mid, confidence, sample_size
            FROM weather_signature_state
            WHERE operator_id = ?
            ORDER BY sample_size DESC, weather_signature
            LIMIT 8
            """,
            [operator_id],
        )
        return [
            {
                "service_window": row[0],
                "weather_signature": row[1],
                "sensitivity_mid": row[2],
                "confidence": row[3],
                "sample_size": row[4],
            }
            for row in rows
        ]

    def _load_external_scan_learning(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT source_bucket, scan_scope, dependency_group, estimated_effect, usefulness_score, confidence, sample_size
            FROM external_scan_learning_state
            WHERE operator_id = ?
            ORDER BY sample_size DESC, source_bucket, dependency_group
            LIMIT 8
            """,
            [operator_id],
        )
        return [
            {
                "source_bucket": row[0],
                "scan_scope": row[1],
                "dependency_group": row[2],
                "estimated_effect": row[3],
                "usefulness_score": row[4],
                "confidence": row[5],
                "sample_size": row[6],
            }
            for row in rows
        ]

    def _load_recent_connector_truths(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT system_name, service_date, service_window, canonical_fields_json, source_prediction_run_id
            FROM connector_truth_log
            WHERE operator_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            [operator_id],
        )
        return [
            {
                "system_name": row[0],
                "service_date": row[1],
                "service_window": row[2],
                "canonical_fields": json.loads(row[3]) if row[3] else {},
                "source_prediction_run_id": row[4],
            }
            for row in rows
        ]

    def _load_recent_notes(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_date, service_window, raw_note, suggested_service_state, suggested_correction_json, created_at
            FROM conversation_note_log
            WHERE operator_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            [operator_id],
        )
        return [
            {
                "service_date": row[0],
                "service_window": row[1],
                "raw_note": row[2],
                "suggested_service_state": row[3],
                "suggested_correction": json.loads(row[4]) if row[4] else {},
                "created_at": row[5],
            }
            for row in rows
        ]

    def _load_pending_corrections(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT suggestion_id, service_date, service_window, source_type, suggested_fields_json,
                   suggested_service_state, created_at
            FROM correction_suggestions
            WHERE operator_id = ?
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 5
            """,
            [operator_id],
        )
        return [
            {
                "suggestion_id": row[0],
                "service_date": row[1],
                "service_window": row[2],
                "source_type": row[3],
                "suggested_fields": json.loads(row[4]) if row[4] else {},
                "suggested_service_state": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    def _load_recent_snapshots(self, operator_id: str) -> list[dict[str, Any]]:
        """Load per-date snapshot pairs so the agent can explain forecast changes.

        For each service_date that has multiple snapshots, returns the latest two
        with a computed delta. This lets the agent answer "why did Friday drop?"
        by comparing the current snapshot to the previous one.
        """
        rows = self.db.fetchall(
            """
            WITH ranked AS (
                SELECT
                    service_date, service_window,
                    forecast_expected, forecast_low, forecast_high,
                    confidence_tier, posture, service_state,
                    snapshot_reason, snapshot_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY service_date, service_window
                        ORDER BY snapshot_at DESC
                    ) AS rn
                FROM forecast_publication_snapshots
                WHERE operator_id = ?
            )
            SELECT service_date, service_window,
                   forecast_expected, forecast_low, forecast_high,
                   confidence_tier, posture, service_state,
                   snapshot_reason, snapshot_at, rn
            FROM ranked
            WHERE rn <= 2
            ORDER BY service_date, service_window, rn
            """,
            [operator_id],
        )

        # Group by (date, window) and build entries with deltas
        grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (row[0], row[1])
            grouped[key].append({
                "service_date": row[0],
                "service_window": row[1],
                "forecast_expected": row[2],
                "forecast_low": row[3],
                "forecast_high": row[4],
                "confidence_tier": row[5],
                "posture": row[6],
                "service_state": row[7],
                "snapshot_reason": row[8],
                "snapshot_at": row[9],
                "rn": row[10],
            })

        result: list[dict[str, Any]] = []
        for _key, snapshots in grouped.items():
            current = snapshots[0]  # rn=1, most recent
            entry: dict[str, Any] = {
                "service_date": current["service_date"],
                "service_window": current["service_window"],
                "forecast_expected": current["forecast_expected"],
                "forecast_low": current["forecast_low"],
                "forecast_high": current["forecast_high"],
                "confidence_tier": current["confidence_tier"],
                "posture": current["posture"],
                "service_state": current["service_state"],
                "snapshot_reason": current["snapshot_reason"],
                "snapshot_at": current["snapshot_at"],
            }
            if len(snapshots) > 1:
                prev = snapshots[1]  # rn=2, previous
                prev_exp = prev["forecast_expected"]
                curr_exp = current["forecast_expected"]
                entry["previous_expected"] = prev_exp
                entry["previous_low"] = prev["forecast_low"]
                entry["previous_high"] = prev["forecast_high"]
                entry["previous_posture"] = prev["posture"]
                entry["previous_confidence"] = prev["confidence_tier"]
                entry["delta_expected"] = (
                    curr_exp - prev_exp
                    if curr_exp is not None and prev_exp is not None
                    else None
                )
                entry["changed"] = any(
                    (
                        entry["delta_expected"] not in {None, 0},
                        current["forecast_low"] != prev["forecast_low"],
                        current["forecast_high"] != prev["forecast_high"],
                        current["posture"] != prev["posture"],
                        current["confidence_tier"] != prev["confidence_tier"],
                        current["service_state"] != prev["service_state"],
                    )
                )
            else:
                entry["changed"] = False

            # Enrich with top drivers from published state
            drivers_row = self.db.fetchone(
                """
                SELECT top_drivers_json FROM published_forecast_state
                WHERE operator_id = ? AND service_date = ? AND service_window = ?
                """,
                [operator_id, current["service_date"], current["service_window"]],
            )
            if drivers_row and drivers_row[0]:
                entry["top_drivers"] = json.loads(drivers_row[0])

            result.append(entry)

        return result

    def _load_recent_evaluations(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_date, service_window, actual_total_covers, forecast_expected, error_abs,
                   error_pct, inside_interval, evaluated_at
            FROM prediction_evaluations
            WHERE operator_id = ?
            ORDER BY evaluated_at DESC
            LIMIT 5
            """,
            [operator_id],
        )
        return [
            {
                "service_date": row[0],
                "service_window": row[1],
                "actual_total_covers": row[2],
                "forecast_expected": row[3],
                "error_abs": row[4],
                "error_pct": row[5],
                "inside_interval": row[6],
                "evaluated_at": row[7],
            }
            for row in rows
        ]

    def _load_runtime_external_signals(self, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT p.service_date, p.service_window, e.source_name, e.signal_type, e.dependency_group,
                   e.recommended_role, e.direction, e.strength, e.details_json
            FROM published_forecast_state p
            JOIN external_signal_log e ON e.source_prediction_run_id = p.source_prediction_run_id
            WHERE p.operator_id = ?
              AND p.service_date BETWEEN ? AND ?
            ORDER BY
                p.service_date,
                p.service_window,
                ABS(COALESCE(e.strength, 0.0)) DESC,
                e.source_name,
                e.signal_type
            """,
            [operator_id, reference_date, reference_date.fromordinal(reference_date.toordinal() + ACTIONABLE_HORIZON_DAYS - 1)],
        )
        result: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            details_blob = row[8]
            raw_details: dict[str, Any] = {}
            if details_blob:
                try:
                    parsed = json.loads(details_blob)
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                if isinstance(parsed, dict):
                    raw_details = dict(parsed.get("details") or {})
            key = (row[0], row[1], row[2], row[3], row[5])
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "service_date": row[0],
                    "service_window": row[1],
                    "source_name": row[2],
                    "signal_type": row[3],
                    "dependency_group": row[4],
                    "recommended_role": row[5],
                    "direction": row[6],
                    "strength": row[7],
                    "entity_label": raw_details.get("entity_label"),
                    "details": raw_details,
                }
            )
        return result

    def _load_operator_preferences(self, operator_id: str) -> dict[str, Any]:
        row = self.db.fetchone(
            """
            SELECT staffing_risk_bias, notification_sensitivity, preferred_explanation_style, clarification_tolerance
            FROM operator_behavior_state
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        if row is None:
            return {}
        return {
            "staffing_risk_bias": row[0],
            "notification_sensitivity": row[1],
            "preferred_explanation_style": row[2],
            "clarification_tolerance": row[3],
        }

    def _load_engine_digests(self, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
        """Load the most recent engine digest per service_date in the actionable horizon.

        Each digest is ~500 chars and summarizes what the engine computed: baseline,
        weather/context impacts, top signals, regime, component split, reference model.
        """
        rows = self.db.fetchall(
            """
            WITH latest AS (
                SELECT service_date, service_window, digest_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY service_date, service_window
                           ORDER BY created_at DESC
                       ) AS rn
                FROM engine_digest
                WHERE operator_id = ?
                  AND service_date BETWEEN ? AND ?
            )
            SELECT service_date, service_window, digest_json
            FROM latest
            WHERE rn = 1
            ORDER BY service_date, service_window
            """,
            [operator_id, reference_date, reference_date.fromordinal(reference_date.toordinal() + ACTIONABLE_HORIZON_DAYS - 1)],
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                digest = json.loads(row[2])
            except (json.JSONDecodeError, TypeError):
                continue
            digest["service_date"] = row[0]
            digest["service_window"] = row[1]
            result.append(digest)
        return result
