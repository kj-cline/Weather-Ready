from __future__ import annotations

from datetime import date

from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.evaluation.metrics import absolute_error, absolute_error_pct, inside_interval
from stormready_v3.storage.db import Database


def record_prediction_evaluation(
    db: Database,
    *,
    prediction_run_id: str,
    operator_id: str,
    service_date: str,
    service_window: str,
    actual_total_covers: int,
    forecast_expected: int,
    forecast_low: int,
    forecast_high: int,
    service_state_learning_eligibility: str,
) -> str:
    error_abs = absolute_error(actual_total_covers, forecast_expected)
    error_pct = absolute_error_pct(actual_total_covers, forecast_expected)
    interval_ok = inside_interval(actual_total_covers, forecast_low, forecast_high)
    existing = db.fetchone(
        """
        SELECT evaluation_id
        FROM prediction_evaluations
        WHERE prediction_run_id = ? AND operator_id = ? AND service_date = ? AND service_window = ?
        ORDER BY evaluated_at DESC
        LIMIT 1
        """,
        [prediction_run_id, operator_id, service_date, service_window],
    )
    if existing is not None:
        db.execute(
            """
            UPDATE prediction_evaluations
            SET actual_total_covers = ?,
                forecast_expected = ?,
                forecast_low = ?,
                forecast_high = ?,
                error_abs = ?,
                error_pct = ?,
                inside_interval = ?,
                directional_bucket_correct = ?,
                service_state_learning_eligibility = ?,
                evaluated_at = CURRENT_TIMESTAMP
            WHERE evaluation_id = ?
            """,
            [
                actual_total_covers,
                forecast_expected,
                forecast_low,
                forecast_high,
                error_abs,
                error_pct,
                interval_ok,
                None,
                service_state_learning_eligibility,
                existing[0],
            ],
        )
        return "updated"
    db.execute(
        """
        INSERT INTO prediction_evaluations (
            prediction_run_id, operator_id, service_date, service_window, actual_total_covers,
            forecast_expected, forecast_low, forecast_high, error_abs, error_pct,
            inside_interval, directional_bucket_correct, service_state_learning_eligibility
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            prediction_run_id,
            operator_id,
            service_date,
            service_window,
            actual_total_covers,
            forecast_expected,
            forecast_low,
            forecast_high,
            error_abs,
            error_pct,
            interval_ok,
            None,
            service_state_learning_eligibility,
        ],
    )
    return "inserted"


def evaluate_latest_published_state(
    db: Database,
    *,
    operator_id: str,
    service_date: date,
    service_window: ServiceWindow,
    actual_total_covers: int,
    learning_eligibility: str = "normal",
) -> str | None:
    row = db.fetchone(
        """
        SELECT source_prediction_run_id, forecast_expected, forecast_low, forecast_high
        FROM published_forecast_state
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        """,
        [operator_id, service_date, service_window.value],
    )
    if row is None or row[0] is None:
        return None

    return record_prediction_evaluation(
        db,
        prediction_run_id=str(row[0]),
        operator_id=operator_id,
        service_date=str(service_date),
        service_window=service_window.value,
        actual_total_covers=actual_total_covers,
        forecast_expected=int(row[1]),
        forecast_low=int(row[2]),
        forecast_high=int(row[3]),
        service_state_learning_eligibility=learning_eligibility,
    )
