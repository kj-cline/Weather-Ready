CREATE TABLE IF NOT EXISTS confidence_calibration_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    horizon_mode TEXT NOT NULL,
    mean_abs_pct_error DOUBLE,
    interval_coverage_rate DOUBLE,
    sample_size INTEGER DEFAULT 0,
    width_multiplier DOUBLE DEFAULT 1.0,
    confidence_penalty_steps INTEGER DEFAULT 0,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, horizon_mode),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
