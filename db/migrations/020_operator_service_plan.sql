CREATE TABLE IF NOT EXISTS operator_service_plan (
    plan_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    planned_service_state TEXT NOT NULL,
    planned_total_covers INTEGER,
    estimated_reduction_pct DOUBLE,
    raw_note TEXT,
    confirmed_by_operator BOOLEAN DEFAULT TRUE,
    entry_mode TEXT DEFAULT 'manual_structured',
    review_window_start DATE,
    review_window_end DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operator_id, service_date, service_window),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS operator_service_plan_seq START 1;
ALTER TABLE operator_service_plan ALTER COLUMN plan_id SET DEFAULT nextval('operator_service_plan_seq');

CREATE INDEX IF NOT EXISTS idx_operator_service_plan_operator_date
    ON operator_service_plan(operator_id, service_date, service_window);
