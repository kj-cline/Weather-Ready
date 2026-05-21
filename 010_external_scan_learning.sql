ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS source_bucket TEXT DEFAULT 'weather_core';
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS scan_scope TEXT;

ALTER TABLE source_reliability_state ADD COLUMN IF NOT EXISTS source_bucket TEXT DEFAULT 'weather_core';
ALTER TABLE source_reliability_state ADD COLUMN IF NOT EXISTS scan_scope TEXT;

ALTER TABLE context_effect_state ADD COLUMN IF NOT EXISTS source_bucket TEXT DEFAULT 'weather_core';
ALTER TABLE context_effect_state ADD COLUMN IF NOT EXISTS scan_scope TEXT;

CREATE TABLE IF NOT EXISTS weather_signature_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    weather_signature TEXT NOT NULL,
    sensitivity_mid DOUBLE,
    confidence TEXT,
    sample_size INTEGER,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, weather_signature),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS external_scan_learning_state (
    operator_id TEXT NOT NULL,
    source_bucket TEXT NOT NULL,
    scan_scope TEXT,
    dependency_group TEXT NOT NULL,
    estimated_effect DOUBLE,
    usefulness_score DOUBLE,
    confidence TEXT,
    sample_size INTEGER,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, source_bucket, scan_scope, dependency_group),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
