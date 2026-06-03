from __future__ import annotations

from dataclasses import dataclass

from stormready_v3.storage.db import Database


@dataclass(slots=True)
class OperatorBehaviorPreferences:
    operator_id: str
    staffing_risk_bias: float | None = None
    notification_sensitivity: float | None = None
    preferred_explanation_style: str | None = None
    clarification_tolerance: float | None = None


class OperatorBehaviorService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def load_preferences(self, operator_id: str) -> OperatorBehaviorPreferences | None:
        row = self.db.fetchone(
            """
            SELECT staffing_risk_bias, notification_sensitivity, preferred_explanation_style, clarification_tolerance
            FROM operator_behavior_state
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        if row is None:
            return None
        return OperatorBehaviorPreferences(
            operator_id=operator_id,
            staffing_risk_bias=float(row[0]) if row[0] is not None else None,
            notification_sensitivity=float(row[1]) if row[1] is not None else None,
            preferred_explanation_style=row[2],
            clarification_tolerance=float(row[3]) if row[3] is not None else None,
        )

    def upsert_preferences(self, preferences: OperatorBehaviorPreferences) -> None:
        existing = self.db.fetchone(
            "SELECT operator_id FROM operator_behavior_state WHERE operator_id = ?",
            [preferences.operator_id],
        )
        if existing is None:
            self.db.execute(
                """
                INSERT INTO operator_behavior_state (
                    operator_id, staffing_risk_bias, notification_sensitivity,
                    preferred_explanation_style, clarification_tolerance
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    preferences.operator_id,
                    preferences.staffing_risk_bias,
                    preferences.notification_sensitivity,
                    preferences.preferred_explanation_style,
                    preferences.clarification_tolerance,
                ],
            )
            return

        self.db.execute(
            """
            UPDATE operator_behavior_state
            SET staffing_risk_bias = ?,
                notification_sensitivity = ?,
                preferred_explanation_style = ?,
                clarification_tolerance = ?,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE operator_id = ?
            """,
            [
                preferences.staffing_risk_bias,
                preferences.notification_sensitivity,
                preferences.preferred_explanation_style,
                preferences.clarification_tolerance,
                preferences.operator_id,
            ],
        )
