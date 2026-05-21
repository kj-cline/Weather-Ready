CREATE TABLE IF NOT EXISTS historical_cover_uploads (
    upload_token TEXT PRIMARY KEY,
    operator_id TEXT,
    file_name TEXT,
    review_status TEXT NOT NULL,
    review_json TEXT NOT NULL,
    normalized_rows_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_historical_cover_uploads_operator_created
    ON historical_cover_uploads(operator_id, created_at DESC);

CREATE TABLE IF NOT EXISTS operator_reference_assets (
    asset_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    model_name TEXT NOT NULL,
    bundle_path TEXT,
    feature_contract_json TEXT,
    benchmark_json TEXT,
    training_status TEXT NOT NULL,
    selected_for_runtime BOOLEAN DEFAULT FALSE,
    source_upload_token TEXT,
    target_mode TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE INDEX IF NOT EXISTS idx_operator_reference_assets_operator
    ON operator_reference_assets(operator_id, created_at DESC);
