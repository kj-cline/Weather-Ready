CREATE TABLE IF NOT EXISTS service_state_risk_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    day_group TEXT NOT NULL,
    risk_state TEXT NOT NULL,
    abnormal_observation_weight DOUBLE DEFAULT 0.0,
    normal_observation_weight DOUBLE DEFAULT 0.0,
    risk_score DOUBLE DEFAULT 0.0,
    confidence TEXT DEFAULT 'low',
    last_observed_date DATE,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, day_group, risk_state),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
