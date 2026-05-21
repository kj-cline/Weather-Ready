-- Engine digest: compact summary of what the prediction engine computed and why.
-- One row per prediction run — the conversation agent reads this to explain forecasts
-- without needing access to the full PredictionContext.

CREATE TABLE IF NOT EXISTS engine_digest (
    prediction_run_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    digest_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE INDEX IF NOT EXISTS idx_engine_digest_operator_date
    ON engine_digest(operator_id, service_date DESC);
