from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.evaluation.service import evaluate_latest_published_state
from stormready_v3.learning.update import (
    update_component_learning_state_from_actual,
    update_confidence_calibration_for_prediction_run,
    update_effect_learning_for_prediction_run,
    update_prediction_adaptation_for_prediction_run,
    update_service_state_risk_from_observation,
    update_source_reliability_for_prediction_run,
    upsert_baseline_learning_state,
)
from stormready_v3.mvp_scope import ensure_runtime_window_supported
from stormready_v3.prediction.calendar import holiday_service_risk
from stormready_v3.prediction.engine import day_group_for_date
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import AgentFrameworkRepository, ForecastRepository

if TYPE_CHECKING:
    from stormready_v3.agents.base import AgentDispatcher


def record_actual_row(
    db: Database,
    *,
    operator_id: str,
    service_date: str,
    service_window: ServiceWindow,
    realized_total_covers: int,
    realized_reserved_covers: int | None = None,
    realized_walk_in_covers: int | None = None,
    realized_waitlist_converted_covers: int | None = None,
    inside_covers: int | None = None,
    outside_covers: int | None = None,
    reservation_no_show_covers: int | None = None,
    reservation_cancellation_covers: int | None = None,
    service_state: ServiceState = ServiceState.NORMAL,
    entry_mode: str = "manual_minimal",
    note: str | None = None,
) -> None:
    ensure_runtime_window_supported(service_window, context="actual logging")
    now = datetime.now(UTC)
    existing = db.fetchone(
        """
        SELECT actual_row_id
        FROM operator_actuals
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        ORDER BY entered_at DESC
        LIMIT 1
        """,
        [operator_id, service_date, service_window.value],
    )
    if existing is None:
        db.execute(
            """
            INSERT INTO operator_actuals (
                operator_id, service_date, service_window, realized_total_covers, realized_reserved_covers,
                realized_walk_in_covers, realized_waitlist_converted_covers, inside_covers, outside_covers,
                reservation_no_show_covers, reservation_cancellation_covers, service_state, entry_mode, note, entered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                realized_total_covers,
                realized_reserved_covers,
                realized_walk_in_covers,
                realized_waitlist_converted_covers,
                inside_covers,
                outside_covers,
                reservation_no_show_covers,
                reservation_cancellation_covers,
                service_state.value,
                entry_mode,
                note,
                now,
            ],
        )
        return

    db.execute(
        """
        UPDATE operator_actuals
        SET realized_total_covers = ?,
            realized_reserved_covers = ?,
            realized_walk_in_covers = ?,
            realized_waitlist_converted_covers = ?,
            inside_covers = ?,
            outside_covers = ?,
            reservation_no_show_covers = ?,
            reservation_cancellation_covers = ?,
            service_state = ?,
            entry_mode = ?,
            note = ?,
            corrected_at = ?
        WHERE actual_row_id = ?
        """,
        [
            realized_total_covers,
            realized_reserved_covers,
            realized_walk_in_covers,
            realized_waitlist_converted_covers,
            inside_covers,
            outside_covers,
            reservation_no_show_covers,
            reservation_cancellation_covers,
            service_state.value,
            entry_mode,
            note,
            now,
            existing[0],
        ],
    )


def record_actual_total(
    db: Database,
    operator_id: str,
    service_date: str,
    service_window: ServiceWindow,
    realized_total_covers: int,
    service_state: ServiceState = ServiceState.NORMAL,
    entry_mode: str = "manual_minimal",
    note: str | None = None,
) -> None:
    record_actual_row(
        db=db,
        operator_id=operator_id,
        service_date=service_date,
        service_window=service_window,
        realized_total_covers=realized_total_covers,
        service_state=service_state,
        entry_mode=entry_mode,
        note=note,
    )


def record_actual_total_and_update(
    db: Database,
    *,
    operator_id: str,
    service_date: date,
    service_window: ServiceWindow,
    realized_total_covers: int,
    realized_reserved_covers: int | None = None,
    realized_walk_in_covers: int | None = None,
    realized_waitlist_converted_covers: int | None = None,
    inside_covers: int | None = None,
    outside_covers: int | None = None,
    reservation_no_show_covers: int | None = None,
    reservation_cancellation_covers: int | None = None,
    service_state: ServiceState = ServiceState.NORMAL,
    entry_mode: str = "manual_minimal",
    note: str | None = None,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, bool]:
    prediction_run_row = db.fetchone(
        """
        SELECT source_prediction_run_id
        FROM published_forecast_state
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        """,
        [operator_id, service_date, service_window.value],
    )
    actual_preexisting = db.fetchone(
        """
        SELECT actual_row_id
        FROM operator_actuals
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        ORDER BY COALESCE(corrected_at, entered_at) DESC
        LIMIT 1
        """,
        [operator_id, service_date, service_window.value],
    ) is not None
    record_actual_row(
        db=db,
        operator_id=operator_id,
        service_date=str(service_date),
        service_window=service_window,
        realized_total_covers=realized_total_covers,
        realized_reserved_covers=realized_reserved_covers,
        realized_walk_in_covers=realized_walk_in_covers,
        realized_waitlist_converted_covers=realized_waitlist_converted_covers,
        inside_covers=inside_covers,
        outside_covers=outside_covers,
        reservation_no_show_covers=reservation_no_show_covers,
        reservation_cancellation_covers=reservation_cancellation_covers,
        service_state=service_state,
        entry_mode=entry_mode,
        note=note,
    )
    evaluation_status = evaluate_latest_published_state(
        db,
        operator_id=operator_id,
        service_date=service_date,
        service_window=service_window,
        actual_total_covers=realized_total_covers,
        learning_eligibility="normal" if service_state is ServiceState.NORMAL else "excluded",
    )
    evaluated = evaluation_status is not None
    first_evaluation = evaluation_status == "inserted"
    update_component_learning_state_from_actual(
        db,
        operator_id=operator_id,
        service_date=str(service_date),
        service_window=service_window.value,
    )
    if first_evaluation and prediction_run_row and prediction_run_row[0]:
        update_source_reliability_for_prediction_run(
            db,
            operator_id=operator_id,
            prediction_run_id=str(prediction_run_row[0]),
        )
        update_effect_learning_for_prediction_run(
            db,
            operator_id=operator_id,
            prediction_run_id=str(prediction_run_row[0]),
        )
        update_confidence_calibration_for_prediction_run(
            db,
            operator_id=operator_id,
            prediction_run_id=str(prediction_run_row[0]),
        )
        update_prediction_adaptation_for_prediction_run(
            db,
            operator_id=operator_id,
            prediction_run_id=str(prediction_run_row[0]),
        )
    learned = False
    if not actual_preexisting:
        day_group = day_group_for_date(service_date)
        calendar_risk = holiday_service_risk(service_date)
        calendar_explains_closed_actual = (
            service_state is ServiceState.CLOSED
            and calendar_risk is not None
            and str(calendar_risk.get("risk_state")) == ServiceState.CLOSED.value
            and float(calendar_risk.get("risk_score") or 0.0) >= 0.30
        )
        if not calendar_explains_closed_actual:
            update_service_state_risk_from_observation(
                db,
                operator_id=operator_id,
                service_window=service_window.value,
                day_group=day_group,
                service_state=service_state.value,
                service_date=service_date.isoformat(),
            )
        if service_state is ServiceState.NORMAL:
            upsert_baseline_learning_state(
                db,
                operator_id=operator_id,
                service_window=service_window.value,
                day_group=day_group,
                actual_total_covers=realized_total_covers,
            )
            learned = True
    if agent_dispatcher is not None and service_state is ServiceState.NORMAL and prediction_run_row is not None and prediction_run_row[0]:
        _run_anomaly_explainer_hook(
            db=db,
            dispatcher=agent_dispatcher,
            operator_id=operator_id,
            prediction_run_id=str(prediction_run_row[0]),
            service_date=service_date,
            service_window=service_window,
            realized_total_covers=realized_total_covers,
        )
    return {"evaluated": evaluated, "learned": learned, "corrected_existing": actual_preexisting}


def _run_anomaly_explainer_hook(
    *,
    db: Database,
    dispatcher: "AgentDispatcher",
    operator_id: str,
    prediction_run_id: str,
    service_date: date,
    service_window: ServiceWindow,
    realized_total_covers: int,
) -> None:
    import uuid

    from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus

    context = _fetch_anomaly_context(
        db=db,
        operator_id=operator_id,
        prediction_run_id=prediction_run_id,
        service_date=service_date,
        service_window=service_window,
        actual_total=realized_total_covers,
    )
    if context is None:
        return

    ctx = AgentContext(
        role=AgentRole.ANOMALY_EXPLAINER,
        operator_id=operator_id,
        run_id=str(uuid.uuid4()),
        triggered_at=datetime.now(UTC),
        payload=context,
    )
    result = dispatcher.dispatch(ctx)
    if result.status is not AgentStatus.OK:
        return
    for hypothesis in result.outputs:
        _insert_anomaly_hypothesis(
            db=db,
            operator_id=operator_id,
            hypothesis=hypothesis,
        )


def _fetch_anomaly_context(
    *,
    db: Database,
    operator_id: str,
    prediction_run_id: str,
    service_date: date,
    service_window: ServiceWindow,
    actual_total: int,
) -> dict[str, object] | None:
    evaluation_row = db.fetchone(
        """
        SELECT error_pct, forecast_expected, forecast_low, forecast_high
        FROM prediction_evaluations
        WHERE operator_id = ?
          AND prediction_run_id = ?
          AND service_date = ?
          AND service_window = ?
        ORDER BY evaluated_at DESC
        LIMIT 1
        """,
        [operator_id, prediction_run_id, service_date, service_window.value],
    )
    if evaluation_row is None:
        return None

    digest = ForecastRepository(db).fetch_engine_digest(prediction_run_id)
    if digest is None:
        return None

    repo = AgentFrameworkRepository(db)
    forecast_expected = digest.get("forecast_expected", evaluation_row[1])
    forecast_interval = digest.get("forecast_interval")
    if forecast_interval is None:
        forecast_interval = {
            "low": evaluation_row[2],
            "high": evaluation_row[3],
        }
    return {
        "prediction_run_id": prediction_run_id,
        "service_date": service_date,
        "service_window": service_window.value,
        "service_state": "normal",
        "error_pct": evaluation_row[0],
        "forecast_expected": forecast_expected,
        "forecast_interval": forecast_interval,
        "actual_total": actual_total,
        "forecast_digest": digest,
        "recent_notes": repo.fetch_recent_notes_for_window(
            operator_id=operator_id,
            start_date=service_date - timedelta(days=6),
            end_date=service_date,
        ),
        "open_hypotheses": repo.fetch_open_hypotheses_compact(operator_id=operator_id),
    }


def _insert_anomaly_hypothesis(
    *,
    db: Database,
    operator_id: str,
    hypothesis: dict[str, object],
) -> None:
    normalized = dict(hypothesis)
    evidence = normalized.get("evidence")
    if isinstance(evidence, (dict, list)):
        normalized["evidence"] = json.dumps(evidence, default=str)
    AgentFrameworkRepository(db).insert_anomaly_hypothesis(
        operator_id=operator_id,
        hypothesis=normalized,
    )
