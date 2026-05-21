CREATE TABLE IF NOT EXISTS connector_truth_log (
    connector_truth_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    system_name TEXT NOT NULL,
    system_type TEXT NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    service_date DATE,
    service_window TEXT,
    canonical_fields_json TEXT,
    field_quality_json TEXT,
    provenance_json TEXT,
    source_prediction_run_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
    FOREIGN KEY(source_prediction_run_id) REFERENCES prediction_runs(prediction_run_id)
);
