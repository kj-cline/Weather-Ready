CREATE TABLE IF NOT EXISTS external_source_catalog (
    source_key TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_bucket TEXT NOT NULL,
    scan_scope TEXT,
    source_category TEXT NOT NULL,
    source_kind TEXT,
    source_class TEXT,
    discovery_mode TEXT NOT NULL,
    trust_class TEXT,
    cadence_hint TEXT,
    status TEXT NOT NULL,
    endpoint_hint TEXT,
    entity_label TEXT,
    geo_scope TEXT,
    metadata_json TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    first_activated_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    last_scanned_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE INDEX IF NOT EXISTS idx_external_source_catalog_operator
ON external_source_catalog(operator_id, source_bucket, status, source_category);

CREATE TABLE IF NOT EXISTS external_scan_run_log (
    scan_run_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    run_date DATE NOT NULL,
    refresh_reason TEXT NOT NULL,
    refresh_window TEXT,
    scan_mode TEXT NOT NULL,
    curated_seed_count INTEGER DEFAULT 0,
    broad_discovery_count INTEGER DEFAULT 0,
    active_curated_count INTEGER DEFAULT 0,
    active_discovered_count INTEGER DEFAULT 0,
    summary_json TEXT,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);
