from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import json

from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS
from stormready_v3.operator_text import forecast_headline, forecast_summary
from stormready_v3.storage.db import Database


def _coerce_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class OperatorRuntimeSnapshot:
    operator_id: str
    reference_date: date
    horizon_end_date: date
    actionable_cards: list[dict[str, Any]] = field(default_factory=list)
    pending_notification_count: int = 0
    latest_refresh: dict[str, Any] | None = None
    open_service_state_suggestions: list[dict[str, Any]] = field(default_factory=list)
    external_catalog_summary: dict[str, Any] = field(default_factory=dict)
    watched_external_sources: list[dict[str, Any]] = field(default_factory=list)


class OperatorRuntimeSnapshotService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build_snapshot(
        self,
        *,
        operator_id: str,
        reference_date: date,
    ) -> OperatorRuntimeSnapshot:
        horizon_end_date = reference_date.fromordinal(reference_date.toordinal() + ACTIONABLE_HORIZON_DAYS - 1)
        latest_refresh = self._load_latest_refresh(operator_id)
        return OperatorRuntimeSnapshot(
            operator_id=operator_id,
            reference_date=reference_date,
            horizon_end_date=horizon_end_date,
            actionable_cards=self._load_actionable_cards(operator_id, reference_date, horizon_end_date),
            pending_notification_count=self._load_pending_notification_count(operator_id),
            latest_refresh=latest_refresh,
            open_service_state_suggestions=self._load_open_service_state_suggestions(operator_id, reference_date),
            external_catalog_summary=self._load_external_catalog_summary(latest_refresh),
            watched_external_sources=self._load_watched_external_sources(operator_id),
        )

    def _load_actionable_cards(
        self,
        operator_id: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT p.service_date, p.service_window, p.target_name, p.forecast_expected, p.forecast_low, p.forecast_high,
                   p.confidence_tier, p.posture, p.service_state, p.service_state_reason, p.prediction_case,
                   p.forecast_regime, p.top_drivers_json, p.major_uncertainties_json, p.source_prediction_run_id,
                   p.reference_status, p.reference_model, p.publish_reason, p.last_published_at, e.digest_json
            FROM published_forecast_state p
            LEFT JOIN engine_digest e ON e.prediction_run_id = p.source_prediction_run_id
            WHERE p.operator_id = ?
              AND p.service_date BETWEEN ? AND ?
            ORDER BY p.service_date, p.service_window
            """,
            [operator_id, start_date, end_date],
        )
        cards: list[dict[str, Any]] = []
        for row in rows:
            top_drivers = json.loads(row[12]) if row[12] else []
            major_uncertainties = json.loads(row[13]) if row[13] else []
            digest = json.loads(row[19]) if row[19] else {}
            baseline_total_covers = _coerce_int(digest.get("baseline"))
            total_pct = _coerce_float(digest.get("total_pct"))
            forecast_expected = _coerce_int(row[3])
            vs_usual_pct = int(round(total_pct * 100)) if total_pct is not None else None
            vs_usual_covers = (
                forecast_expected - baseline_total_covers
                if forecast_expected is not None and baseline_total_covers is not None
                else None
            )
            cards.append(
                {
                    "service_date": row[0],
                    "service_window": row[1],
                    "target_name": row[2],
                    "forecast_expected": row[3],
                    "forecast_low": row[4],
                    "forecast_high": row[5],
                    "confidence_tier": row[6],
                    "posture": row[7],
                    "service_state": row[8],
                    "service_state_reason": row[9],
                    "service_state_source": digest.get("service_state_source"),
                    "prediction_case": row[10],
                    "forecast_regime": row[11],
                    "top_drivers": top_drivers,
                    "major_uncertainties": major_uncertainties,
                    "source_prediction_run_id": row[14],
                    "reference_status": row[15],
                    "reference_model": row[16],
                    "publish_reason": row[17],
                    "last_published_at": row[18],
                    "baseline_total_covers": baseline_total_covers,
                    "vs_usual_pct": vs_usual_pct,
                    "vs_usual_covers": vs_usual_covers,
                    "headline": forecast_headline(
                        service_window=str(row[1]),
                        posture=str(row[7]),
                        service_state=str(row[8]),
                    ),
                    "summary": forecast_summary(
                        forecast_expected=int(row[3]),
                        forecast_low=int(row[4]),
                        forecast_high=int(row[5]),
                        confidence_tier=str(row[6]),
                        service_state=str(row[8]),
                        top_drivers=top_drivers,
                        major_uncertainties=major_uncertainties,
                    ),
                }
            )
        return cards

    def _load_pending_notification_count(self, operator_id: str) -> int:
        row = self.db.fetchone(
            """
            SELECT COUNT(*)
            FROM notification_events
            WHERE operator_id = ?
              AND status = 'pending'
            """,
            [operator_id],
        )
        return int(row[0]) if row is not None else 0

    def _load_latest_refresh(self, operator_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT refresh_reason, run_date, refresh_window, started_at, completed_at, event_mode_active, source_summary_json
            FROM forecast_refresh_runs
            WHERE operator_id = ?
              AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            [operator_id],
        )
        if row is None:
            return None
        return {
            "refresh_reason": row[0],
            "run_date": row[1],
            "refresh_window": row[2],
            "started_at": row[3],
            "completed_at": row[4],
            "event_mode_active": bool(row[5]),
            "source_summary": json.loads(row[6]) if row[6] else {},
        }

    def _load_open_service_state_suggestions(self, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_date, service_window, service_state, source_type, confidence, note
            FROM service_state_log
            WHERE operator_id = ?
              AND service_date >= ?
              AND service_state NOT IN ('normal_service', 'normal')
              AND operator_confirmed = FALSE
              AND source_type IN ('conversation_capture', 'operator_note', 'disruption_suggestion')
              AND NOT EXISTS (
                  SELECT 1
                  FROM service_state_log confirmed
                  WHERE confirmed.operator_id = service_state_log.operator_id
                    AND confirmed.service_date = service_state_log.service_date
                    AND confirmed.service_window = service_state_log.service_window
                    AND confirmed.service_state = service_state_log.service_state
                    AND confirmed.operator_confirmed = TRUE
              )
            ORDER BY service_date, service_window, created_at DESC
            LIMIT 10
            """,
            [operator_id, reference_date],
        )
        return [
            {
                "service_date": row[0],
                "service_window": row[1],
                "service_state": row[2],
                "source_type": row[3],
                "confidence": row[4],
                "note": row[5],
            }
            for row in rows
        ]

    def _load_external_catalog_summary(self, latest_refresh: dict[str, Any] | None) -> dict[str, Any]:
        if latest_refresh is None:
            return {}
        source_summary = latest_refresh.get("source_summary") or {}
        external_catalog = source_summary.get("external_catalog") or {}
        return external_catalog if isinstance(external_catalog, dict) else {}

    def _load_watched_external_sources(self, operator_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT source_name, source_bucket, source_category, status, recommended_action,
                   priority_score, governance_confidence, entity_label, cadence_hint,
                   governance_source, governance_provider, governance_fallback_reason,
                   endpoint_hint, geo_scope
            FROM external_source_catalog
            WHERE operator_id = ?
            ORDER BY
                CASE WHEN status = 'curated' THEN 0 ELSE 1 END,
                COALESCE(priority_score, 0.0) DESC,
                source_category,
                source_name
            LIMIT 10
            """,
            [operator_id],
        )
        return [
            {
                "source_name": row[0],
                "source_bucket": row[1],
                "source_category": row[2],
                "status": row[3],
                "recommended_action": row[4],
                "priority_score": float(row[5]) if row[5] is not None else None,
                "governance_confidence": row[6],
                "entity_label": row[7],
                "cadence_hint": row[8],
                "governance_source": row[9],
                "governance_provider": row[10],
                "governance_fallback_reason": row[11],
                "endpoint_hint": row[12],
                "geo_scope": row[13],
            }
            for row in rows
        ]
