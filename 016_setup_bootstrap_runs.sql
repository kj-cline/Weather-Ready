CREATE TABLE IF NOT EXISTS setup_bootstrap_runs (
    operator_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    message TEXT,
    steps_json TEXT,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    failed_at TIMESTAMP,
    failure_reason TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
