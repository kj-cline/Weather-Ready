CREATE TABLE IF NOT EXISTS prediction_adaptation_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    horizon_mode TEXT NOT NULL DEFAULT '',
    adaptation_key TEXT NOT NULL,
    adjustment_mid DOUBLE,
    confidence TEXT,
    sample_size INTEGER DEFAULT 0,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, horizon_mode, adaptation_key),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
