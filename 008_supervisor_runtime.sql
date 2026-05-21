ALTER TABLE forecast_refresh_runs ADD COLUMN IF NOT EXISTS run_date DATE;
ALTER TABLE forecast_refresh_runs ADD COLUMN IF NOT EXISTS refresh_window TEXT;

CREATE TABLE IF NOT EXISTS refresh_request_queue (
    request_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    requested_reason TEXT NOT NULL,
    requested_for_date DATE,
    requested_service_window TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMP,
    completed_at TIMESTAMP,
    failed_at TIMESTAMP,
    failure_reason TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS refresh_request_seq START 1;
ALTER TABLE refresh_request_queue ALTER COLUMN request_id SET DEFAULT nextval('refresh_request_seq');

CREATE TABLE IF NOT EXISTS supervisor_tick_log (
    tick_id BIGINT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    tick_mode TEXT NOT NULL,
    run_date DATE,
    summary_json TEXT,
    status TEXT NOT NULL,
    failure_reason TEXT
);

CREATE SEQUENCE IF NOT EXISTS supervisor_tick_seq START 1;
ALTER TABLE supervisor_tick_log ALTER COLUMN tick_id SET DEFAULT nextval('supervisor_tick_seq');
