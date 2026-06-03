from __future__ import annotations

import json

from stormready_v3.domain.enums import ComponentState, ServiceState
from stormready_v3.storage.db import Database


ABNORMAL_SERVICE_STATES = {
    ServiceState.PARTIAL.value,
    ServiceState.PATIO_CONSTRAINED.value,
    ServiceState.PRIVATE_EVENT.value,
    ServiceState.HOLIDAY_MODIFIED.value,
    ServiceState.WEATHER_DISRUPTION.value,
    ServiceState.CLOSED.value,
}


def upsert_baseline_learning_state(
    db: Database,
    *,
    operator_id: str,
    service_window: str,
    day_group: str,
    actual_total_covers: int,
) -> None:
    row = db.fetchone(
        """
        SELECT baseline_mid, baseline_variability, history_depth
        FROM baseline_learning_state
        WHERE operator_id = ? AND service_window = ? AND day_group = ?
        """,
        [operator_id, service_window, day_group],
    )

    if row is None:
        db.execute(
            """
            INSERT INTO baseline_learning_state (
                operator_id, service_window, day_group, baseline_mid, baseline_variability, history_depth
            ) VALUES (?, ?, ?, ?, 0.0, 1)
            """,
            [operator_id, service_window, day_group, float(actual_total_covers)],
        )
        return

    prior_mid = float(row[0] or 0.0)
    prior_var = float(row[1] or 0.0)
    history_depth = int(row[2] or 0)
    next_depth = history_depth + 1
    next_mid = ((prior_mid * history_depth) + actual_total_covers) / next_depth
    next_var = ((prior_var * history_depth) + abs(actual_total_covers - prior_mid)) / next_depth
    db.execute(
        """
        UPDATE baseline_learning_state
        SET baseline_mid = ?, baseline_variability = ?, history_depth = ?, last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ? AND service_window = ? AND day_group = ?
        """,
        [next_mid, next_var, next_depth, operator_id, service_window, day_group],
    )


def _service_state_risk_score(abnormal_weight: float, normal_weight: float) -> tuple[float, str]:
    """Smoothed risk score so one abnormal service does not dominate a cold-start operator."""
    total_weight = max(0.0, abnormal_weight) + max(0.0, normal_weight)
    score = (max(0.0, abnormal_weight) + 0.15) / (total_weight + 3.0)
    score = max(0.0, min(0.65, score))
    if total_weight >= 8.0 and abnormal_weight >= 2.0:
        confidence = "high"
    elif total_weight >= 4.0 or abnormal_weight >= 1.5:
        confidence = "medium"
    else:
        confidence = "low"
    return score, confidence


def update_service_state_risk_from_observation(
    db: Database,
    *,
    operator_id: str,
    service_window: str,
    day_group: str,
    service_state: str,
    service_date: str,
    observation_weight: float = 1.0,
) -> None:
    """Track repeated abnormal-service risk as uncertainty evidence, not truth.

    Normal observations decay any existing risk for the same day group. Abnormal
    observations increase only the matching risk lane and count as non-observations
    for the other abnormal lanes already known for that day group.
    """
    state = str(service_state or ServiceState.NORMAL.value)
    weight = max(0.0, min(1.0, float(observation_weight or 0.0)))
    if weight <= 0.0:
        return

    existing_rows = db.fetchall(
        """
        SELECT risk_state, abnormal_observation_weight, normal_observation_weight
        FROM service_state_risk_state
        WHERE operator_id = ? AND service_window = ? AND day_group = ?
        """,
        [operator_id, service_window, day_group],
    )

    if state == ServiceState.NORMAL.value:
        for risk_state, abnormal_weight, normal_weight in existing_rows:
            next_abnormal = float(abnormal_weight or 0.0)
            next_normal = float(normal_weight or 0.0) + weight
            next_score, confidence = _service_state_risk_score(next_abnormal, next_normal)
            db.execute(
                """
                UPDATE service_state_risk_state
                SET normal_observation_weight = ?,
                    risk_score = ?,
                    confidence = ?,
                    last_observed_date = ?,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE operator_id = ? AND service_window = ? AND day_group = ? AND risk_state = ?
                """,
                [next_normal, next_score, confidence, service_date, operator_id, service_window, day_group, risk_state],
            )
        return

    if state not in ABNORMAL_SERVICE_STATES:
        return

    matched = False
    for risk_state, abnormal_weight, normal_weight in existing_rows:
        next_abnormal = float(abnormal_weight or 0.0)
        next_normal = float(normal_weight or 0.0)
        if str(risk_state) == state:
            next_abnormal += weight
            matched = True
        else:
            next_normal += weight
        next_score, confidence = _service_state_risk_score(next_abnormal, next_normal)
        db.execute(
            """
            UPDATE service_state_risk_state
            SET abnormal_observation_weight = ?,
                normal_observation_weight = ?,
                risk_score = ?,
                confidence = ?,
                last_observed_date = ?,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE operator_id = ? AND service_window = ? AND day_group = ? AND risk_state = ?
            """,
            [
                next_abnormal,
                next_normal,
                next_score,
                confidence,
                service_date,
                operator_id,
                service_window,
                day_group,
                risk_state,
            ],
        )

    if matched:
        return

    score, confidence = _service_state_risk_score(weight, 0.0)
    db.execute(
        """
        INSERT INTO service_state_risk_state (
            operator_id, service_window, day_group, risk_state, abnormal_observation_weight,
            normal_observation_weight, risk_score, confidence, last_observed_date
        ) VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, ?)
        """,
        [operator_id, service_window, day_group, state, weight, score, confidence, service_date],
    )


def update_component_learning_state_from_actual(
    db: Database,
    *,
    operator_id: str,
    service_date: str,
    service_window: str,
) -> None:
    row = db.fetchone(
        """
        SELECT realized_total_covers,
               realized_reserved_covers,
               outside_covers
        FROM operator_actuals
        WHERE operator_id = ? AND service_date = ? AND service_window = ?
        ORDER BY entered_at DESC
        LIMIT 1
        """,
        [operator_id, service_date, service_window],
    )
    if row is None:
        return

    total = int(row[0] or 0)
    component_map = {
        "realized_reserved_covers": row[1],
        "outside_covers": row[2],
    }

    for component_name, raw_value in component_map.items():
        if raw_value is None:
            continue
        value = int(raw_value)
        row_state = db.fetchone(
            """
            SELECT semantic_clarity_score, reconciliation_quality_score, observation_count, history_depth_days
            FROM component_learning_state
            WHERE operator_id = ? AND component_name = ?
            """,
            [operator_id, component_name],
        )

        current_reconciliation = 1.0 if total <= 0 or value <= total else 0.25

        if row_state is None:
            observation_count = 1
            history_depth_days = 1
            semantic_clarity = 0.35
            reconciliation_quality = current_reconciliation
        else:
            prior_semantic = float(row_state[0] or 0.0)
            prior_reconciliation = float(row_state[1] or 0.0)
            prior_observation_count = int(row_state[2] or 0)
            prior_history_depth_days = int(row_state[3] or 0)
            observation_count = prior_observation_count + 1
            history_depth_days = max(prior_history_depth_days + 1, observation_count)
            semantic_clarity = min(1.0, prior_semantic + 0.12)
            reconciliation_quality = (
                (prior_reconciliation * prior_observation_count) + current_reconciliation
            ) / observation_count

        eligible_for_learning = observation_count >= 5 and semantic_clarity >= 0.7 and reconciliation_quality >= 0.75
        if eligible_for_learning and observation_count >= 10:
            component_state = ComponentState.LEARNED
        elif observation_count >= 3:
            component_state = ComponentState.OBSERVABLE
        else:
            component_state = ComponentState.PROVISIONAL

        if row_state is None:
            db.execute(
                """
                INSERT INTO component_learning_state (
                    operator_id, component_name, component_state, semantic_clarity_score,
                    reconciliation_quality_score, observation_count, history_depth_days,
                    eligible_for_learning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    component_name,
                    component_state.value,
                    semantic_clarity,
                    reconciliation_quality,
                    observation_count,
                    history_depth_days,
                    eligible_for_learning,
                ],
            )
            continue

        db.execute(
            """
            UPDATE component_learning_state
            SET component_state = ?,
                semantic_clarity_score = ?,
                reconciliation_quality_score = ?,
                observation_count = ?,
                history_depth_days = ?,
                eligible_for_learning = ?,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE operator_id = ? AND component_name = ?
            """,
            [
                component_state.value,
                semantic_clarity,
                reconciliation_quality,
                observation_count,
                history_depth_days,
                eligible_for_learning,
                operator_id,
                component_name,
            ],
        )


def update_source_reliability_for_prediction_run(
    db: Database,
    *,
    operator_id: str,
    prediction_run_id: str,
) -> None:
    evaluation = db.fetchone(
        """
        SELECT error_pct, inside_interval, service_state_learning_eligibility
        FROM prediction_evaluations
        WHERE operator_id = ? AND prediction_run_id = ?
        ORDER BY evaluated_at DESC
        LIMIT 1
        """,
        [operator_id, prediction_run_id],
    )
    if evaluation is None or str(evaluation[2]) != "normal":
        return

    error_pct = float(evaluation[0]) if evaluation[0] is not None else 1.0
    inside = bool(evaluation[1])
    usefulness_observation = 1.0 if inside else max(0.0, 1.0 - min(error_pct, 1.0))
    status = "active" if usefulness_observation >= 0.4 else "review"

    signals = db.fetchall(
        """
        SELECT source_name, signal_type, trust_level, scan_scope, source_bucket
        FROM external_signal_log
        WHERE operator_id = ? AND source_prediction_run_id = ?
        """,
        [operator_id, prediction_run_id],
    )

    for source_name, signal_type, trust_level, scan_scope, source_bucket in signals:
        weighting = _source_bucket_weight(str(source_bucket or "weather_core"))
        weighted_usefulness = usefulness_observation * weighting
        prior = db.fetchone(
            """
            SELECT historical_usefulness_score, staleness_penalty
            FROM source_reliability_state
            WHERE operator_id = ? AND source_name = ? AND signal_type = ?
            """,
            [operator_id, str(source_name), str(signal_type)],
        )
        if prior is None:
            db.execute(
                """
                INSERT INTO source_reliability_state (
                    operator_id, source_name, signal_type, historical_usefulness_score,
                    staleness_penalty, trust_class, status, source_bucket, scan_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    str(source_name),
                    str(signal_type),
                    weighted_usefulness,
                    0.0,
                    str(trust_level or "unknown"),
                    status,
                    str(source_bucket or "weather_core"),
                    str(scan_scope) if scan_scope is not None else None,
                ],
            )
            continue

        prior_score = float(prior[0] or 0.0)
        next_score = (prior_score + weighted_usefulness) / 2.0
        db.execute(
            """
            UPDATE source_reliability_state
            SET historical_usefulness_score = ?,
                staleness_penalty = ?,
                trust_class = ?,
                status = ?,
                source_bucket = ?,
                scan_scope = ?,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE operator_id = ? AND source_name = ? AND signal_type = ?
            """,
            [
                next_score,
                0.0,
                str(trust_level or "unknown"),
                "active" if next_score >= 0.4 else "review",
                str(source_bucket or "weather_core"),
                str(scan_scope) if scan_scope is not None else None,
                operator_id,
                str(source_name),
                str(signal_type),
            ],
        )


def update_effect_learning_for_prediction_run(
    db: Database,
    *,
    operator_id: str,
    prediction_run_id: str,
) -> None:
    evaluation = db.fetchone(
        """
        SELECT actual_total_covers, forecast_expected, service_state_learning_eligibility
        FROM prediction_evaluations
        WHERE operator_id = ? AND prediction_run_id = ?
        ORDER BY evaluated_at DESC
        LIMIT 1
        """,
        [operator_id, prediction_run_id],
    )
    if evaluation is None or str(evaluation[2]) != "normal":
        return

    actual_total = int(evaluation[0] or 0)
    forecast_expected = int(evaluation[1] or 0)
    residual_pct = 0.0 if forecast_expected == 0 else (actual_total - forecast_expected) / forecast_expected

    prediction_run = db.fetchone(
        """
        SELECT service_window
        FROM prediction_runs
        WHERE prediction_run_id = ?
        """,
        [prediction_run_id],
    )
    if prediction_run is None:
        return
    service_window = str(prediction_run[0])

    signals = db.fetchall(
        """
        SELECT signal_type, dependency_group, scan_scope, source_bucket, details_json, recommended_role, strength
        FROM external_signal_log
        WHERE operator_id = ? AND source_prediction_run_id = ?
        """,
        [operator_id, prediction_run_id],
    )

    for signal_type, dependency_group, scan_scope, source_bucket, details_json, recommended_role, strength in signals:
        normalized_source_bucket = _normalized_source_bucket(source_bucket)
        normalized_scan_scope = _normalized_scan_scope(scan_scope, normalized_source_bucket)
        if str(recommended_role or "") != "numeric_mover":
            continue
        strength_value = float(strength or 0.0)
        if strength_value <= 0.0:
            continue
        strength_weight = _effect_strength_weight(strength_value)
        weighted_residual = residual_pct * _source_bucket_weight(normalized_source_bucket) * strength_weight
        details = json.loads(details_json) if details_json else {}
        if str(dependency_group) == "weather":
            signal_details = details.get("details") if isinstance(details, dict) else {}
            learning_signatures = signal_details.get("learning_signatures") if isinstance(signal_details, dict) else None
            if isinstance(learning_signatures, list):
                for signature in learning_signatures:
                    _upsert_weather_signature_state(
                        db,
                        operator_id=operator_id,
                        service_window=service_window,
                        weather_signature=str(signature),
                        residual_pct=weighted_residual,
                    )
        else:
            _upsert_external_scan_learning_state(
                db,
                operator_id=operator_id,
                source_bucket=normalized_source_bucket,
                scan_scope=normalized_scan_scope,
                dependency_group=str(dependency_group),
                residual_pct=weighted_residual,
            )


def update_confidence_calibration_for_prediction_run(
    db: Database,
    *,
    operator_id: str,
    prediction_run_id: str,
) -> None:
    evaluation = db.fetchone(
        """
        SELECT e.error_pct, e.inside_interval, e.service_state_learning_eligibility, p.service_window, p.horizon_mode
        FROM prediction_evaluations e
        JOIN prediction_runs p ON p.prediction_run_id = e.prediction_run_id
        WHERE e.operator_id = ? AND e.prediction_run_id = ?
        ORDER BY e.evaluated_at DESC
        LIMIT 1
        """,
        [operator_id, prediction_run_id],
    )
    if evaluation is None or str(evaluation[2]) != "normal":
        return

    error_pct = float(evaluation[0] or 0.0)
    inside = 1.0 if bool(evaluation[1]) else 0.0
    service_window = str(evaluation[3])
    horizon_mode = str(evaluation[4])

    prior = db.fetchone(
        """
        SELECT mean_abs_pct_error, interval_coverage_rate, sample_size
        FROM confidence_calibration_state
        WHERE operator_id = ? AND service_window = ? AND horizon_mode = ?
        """,
        [operator_id, service_window, horizon_mode],
    )
    if prior is None:
        mean_error = error_pct
        coverage = inside
        sample_size = 1
    else:
        prior_mean_error = float(prior[0] or 0.0)
        prior_coverage = float(prior[1] or 0.0)
        prior_sample_size = int(prior[2] or 0)
        sample_size = prior_sample_size + 1
        mean_error = ((prior_mean_error * prior_sample_size) + error_pct) / sample_size
        coverage = ((prior_coverage * prior_sample_size) + inside) / sample_size

    width_multiplier, penalty_steps = _calibration_controls_for_state(
        mean_abs_pct_error=mean_error,
        interval_coverage_rate=coverage,
        sample_size=sample_size,
    )

    if prior is None:
        db.execute(
            """
            INSERT INTO confidence_calibration_state (
                operator_id, service_window, horizon_mode, mean_abs_pct_error,
                interval_coverage_rate, sample_size, width_multiplier, confidence_penalty_steps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_window,
                horizon_mode,
                mean_error,
                coverage,
                sample_size,
                width_multiplier,
                penalty_steps,
            ],
        )
        return

    db.execute(
        """
        UPDATE confidence_calibration_state
        SET mean_abs_pct_error = ?,
            interval_coverage_rate = ?,
            sample_size = ?,
            width_multiplier = ?,
            confidence_penalty_steps = ?,
            last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ? AND service_window = ? AND horizon_mode = ?
        """,
        [
            mean_error,
            coverage,
            sample_size,
            width_multiplier,
            penalty_steps,
            operator_id,
            service_window,
            horizon_mode,
        ],
    )


def _confidence_for_sample_size(sample_size: int) -> str:
    if sample_size >= 12:
        return "high"
    if sample_size >= 5:
        return "medium"
    return "low"


def _upsert_prediction_adaptation_state(
    db: Database,
    *,
    operator_id: str,
    service_window: str,
    horizon_mode: str,
    adaptation_key: str,
    observation: float,
) -> None:
    row = db.fetchone(
        """
        SELECT adjustment_mid, sample_size
        FROM prediction_adaptation_state
        WHERE operator_id = ? AND service_window = ? AND horizon_mode = ? AND adaptation_key = ?
        """,
        [operator_id, service_window, horizon_mode, adaptation_key],
    )
    if row is None:
        db.execute(
            """
            INSERT INTO prediction_adaptation_state (
                operator_id, service_window, horizon_mode, adaptation_key, adjustment_mid, confidence, sample_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_window,
                horizon_mode,
                adaptation_key,
                observation,
                "low",
                1,
            ],
        )
        return

    prior_mid = float(row[0] or 0.0)
    prior_sample_size = int(row[1] or 0)
    next_sample_size = prior_sample_size + 1
    next_mid = ((prior_mid * prior_sample_size) + observation) / next_sample_size
    db.execute(
        """
        UPDATE prediction_adaptation_state
        SET adjustment_mid = ?, confidence = ?, sample_size = ?, last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ? AND service_window = ? AND horizon_mode = ? AND adaptation_key = ?
        """,
        [
            next_mid,
            _confidence_for_sample_size(next_sample_size),
            next_sample_size,
            operator_id,
            service_window,
            horizon_mode,
            adaptation_key,
        ],
    )


def update_prediction_adaptation_for_prediction_run(
    db: Database,
    *,
    operator_id: str,
    prediction_run_id: str,
) -> None:
    row = db.fetchone(
        """
        SELECT e.actual_total_covers,
               e.forecast_expected,
               e.forecast_low,
               e.forecast_high,
               e.inside_interval,
               e.service_state_learning_eligibility,
               p.service_window,
               p.horizon_mode
        FROM prediction_evaluations e
        JOIN prediction_runs p ON p.prediction_run_id = e.prediction_run_id
        WHERE e.operator_id = ? AND e.prediction_run_id = ?
        ORDER BY e.evaluated_at DESC
        LIMIT 1
        """,
        [operator_id, prediction_run_id],
    )
    if row is None or str(row[5]) != "normal":
        return

    actual_total = int(row[0] or 0)
    forecast_expected = int(row[1] or 0)
    forecast_low = int(row[2] or 0)
    forecast_high = int(row[3] or 0)
    inside_interval = bool(row[4])
    service_window = str(row[6] or "")
    horizon_mode = str(row[7] or "")

    digest_row = db.fetchone(
        """
        SELECT digest_json
        FROM engine_digest
        WHERE prediction_run_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        [prediction_run_id],
    )
    if digest_row is None or not digest_row[0]:
        return
    digest = json.loads(digest_row[0])
    signed_residual_pct = 0.0 if forecast_expected == 0 else (actual_total - forecast_expected) / forecast_expected

    brooklyn_delta = float(digest.get("brooklyn_delta") or 0.0)
    if str(digest.get("reference_status")) == "used" and abs(brooklyn_delta) >= 2.0 and forecast_expected > 0:
        brooklyn_influence = min(1.0, abs(brooklyn_delta) / max(4.0, forecast_expected * 0.08))
        brooklyn_observation = max(
            -0.12,
            min(0.12, signed_residual_pct * (1.0 if brooklyn_delta > 0 else -1.0) * brooklyn_influence),
        )
        if abs(brooklyn_observation) >= 0.005:
            _upsert_prediction_adaptation_state(
                db,
                operator_id=operator_id,
                service_window=service_window,
                horizon_mode="",
                adaptation_key="brooklyn_weight_adjustment",
                observation=brooklyn_observation,
            )

    weather_pct = float(digest.get("weather_pct") or 0.0)
    if abs(weather_pct) >= 0.03:
        weather_influence = min(1.0, abs(weather_pct) / 0.10)
        weather_observation = max(
            -0.10,
            min(0.10, signed_residual_pct * (1.0 if weather_pct > 0 else -1.0) * weather_influence * 0.6),
        )
        if abs(weather_observation) >= 0.004:
            _upsert_prediction_adaptation_state(
                db,
                operator_id=operator_id,
                service_window=service_window,
                horizon_mode="",
                adaptation_key="weather_profile_adjustment",
                observation=weather_observation,
            )

    half_range = max(1.0, (forecast_high - forecast_low) / 2.0)
    coverage_utilization = abs(actual_total - forecast_expected) / half_range
    relative_interval_width = (forecast_high - forecast_low) / max(1.0, float(forecast_expected))
    interval_observation = 0.0
    if not inside_interval:
        overflow = max(0.0, coverage_utilization - 1.0)
        interval_observation = -min(0.18, 0.07 + min(0.11, overflow * 0.05))
        if abs(signed_residual_pct) >= 0.25:
            interval_observation -= 0.02
        if relative_interval_width <= 0.22:
            interval_observation -= 0.02
    elif coverage_utilization <= 0.35:
        interval_observation = min(0.08, (0.35 - coverage_utilization) * 0.12)
    elif coverage_utilization >= 0.95:
        interval_observation = -0.02
    interval_observation = max(-0.20, min(0.08, interval_observation))
    if abs(interval_observation) >= 0.004:
        _upsert_prediction_adaptation_state(
            db,
            operator_id=operator_id,
            service_window=service_window,
            horizon_mode=horizon_mode,
            adaptation_key="interval_evidence_adjustment",
            observation=interval_observation,
        )


def _calibration_controls_for_state(
    *,
    mean_abs_pct_error: float,
    interval_coverage_rate: float,
    sample_size: int,
) -> tuple[float, int]:
    if sample_size <= 1:
        if mean_abs_pct_error >= 0.35 or interval_coverage_rate < 0.20:
            return 1.22, 1
        return 1.0, 0
    if sample_size == 2:
        if mean_abs_pct_error >= 0.30 or interval_coverage_rate < 0.35:
            return 1.32, 2
        if mean_abs_pct_error >= 0.20 or interval_coverage_rate < 0.50:
            return 1.16, 1
        return 1.0, 0
    if mean_abs_pct_error >= 0.30 or interval_coverage_rate < 0.45:
        return 1.50, 2
    if mean_abs_pct_error >= 0.20 or interval_coverage_rate < 0.60:
        return 1.24, 1
    if mean_abs_pct_error <= 0.10 and interval_coverage_rate >= 0.85:
        return 0.92, 0
    return 1.0, 0


def _upsert_weather_signature_state(
    db: Database,
    *,
    operator_id: str,
    service_window: str,
    weather_signature: str,
    residual_pct: float,
) -> None:
    prior = db.fetchone(
        """
        SELECT sensitivity_mid, sample_size
        FROM weather_signature_state
        WHERE operator_id = ? AND service_window = ? AND weather_signature = ?
        """,
        [operator_id, service_window, weather_signature],
    )
    if prior is None:
        db.execute(
            """
            INSERT INTO weather_signature_state (
                operator_id, service_window, weather_signature, sensitivity_mid, confidence, sample_size
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [operator_id, service_window, weather_signature, residual_pct, "low", 1],
        )
        return

    prior_mid = float(prior[0] or 0.0)
    prior_sample_size = int(prior[1] or 0)
    next_sample_size = prior_sample_size + 1
    next_mid = ((prior_mid * prior_sample_size) + residual_pct) / next_sample_size
    db.execute(
        """
        UPDATE weather_signature_state
        SET sensitivity_mid = ?, confidence = ?, sample_size = ?, last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ? AND service_window = ? AND weather_signature = ?
        """,
        [
            next_mid,
            _confidence_for_sample_size(next_sample_size),
            next_sample_size,
            operator_id,
            service_window,
            weather_signature,
        ],
    )


def _upsert_external_scan_learning_state(
    db: Database,
    *,
    operator_id: str,
    source_bucket: str,
    scan_scope: str | None,
    dependency_group: str,
    residual_pct: float,
) -> None:
    normalized_source_bucket = _normalized_source_bucket(source_bucket)
    normalized_scan_scope = _normalized_scan_scope(scan_scope, normalized_source_bucket)
    usefulness_score = max(0.0, 1.0 - min(abs(residual_pct), 1.0))
    prior = db.fetchone(
        """
        SELECT estimated_effect, usefulness_score, sample_size
        FROM external_scan_learning_state
        WHERE operator_id = ? AND source_bucket = ? AND scan_scope IS NOT DISTINCT FROM ? AND dependency_group = ?
        """,
        [operator_id, normalized_source_bucket, normalized_scan_scope, dependency_group],
    )
    if prior is None:
        db.execute(
            """
            INSERT INTO external_scan_learning_state (
                operator_id, source_bucket, scan_scope, dependency_group, estimated_effect, usefulness_score, confidence, sample_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                normalized_source_bucket,
                normalized_scan_scope,
                dependency_group,
                residual_pct,
                usefulness_score,
                "low",
                1,
            ],
        )
        return

    prior_effect = float(prior[0] or 0.0)
    prior_usefulness = float(prior[1] or 0.0)
    prior_sample_size = int(prior[2] or 0)
    next_sample_size = prior_sample_size + 1
    next_effect = ((prior_effect * prior_sample_size) + residual_pct) / next_sample_size
    next_usefulness = ((prior_usefulness * prior_sample_size) + usefulness_score) / next_sample_size
    db.execute(
        """
        UPDATE external_scan_learning_state
        SET estimated_effect = ?,
            usefulness_score = ?,
            confidence = ?,
            sample_size = ?,
            last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ? AND source_bucket = ? AND scan_scope IS NOT DISTINCT FROM ? AND dependency_group = ?
        """,
        [
            next_effect,
            next_usefulness,
            _confidence_for_sample_size(next_sample_size),
            next_sample_size,
            operator_id,
            normalized_source_bucket,
            normalized_scan_scope,
            dependency_group,
        ],
    )


def _normalized_source_bucket(source_bucket: str | None) -> str:
    return str(source_bucket or "weather_core")


def _normalized_scan_scope(scan_scope: str | None, source_bucket: str) -> str:
    if scan_scope is not None and str(scan_scope).strip():
        return str(scan_scope)
    if source_bucket in {"weather_core", "curated_local", "broad_proxy"}:
        return f"{source_bucket}_scan"
    return source_bucket


def _source_bucket_weight(source_bucket: str) -> float:
    if source_bucket == "curated_local":
        return 0.85
    if source_bucket == "broad_proxy":
        return 0.6
    return 1.0


def _effect_strength_weight(strength: float) -> float:
    bounded = max(0.0, min(1.0, strength / 0.10))
    return max(0.25, bounded)
