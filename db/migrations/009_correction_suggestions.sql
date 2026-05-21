CREATE TABLE IF NOT EXISTS correction_suggestions (
    suggestion_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE,
    service_window TEXT,
    source_type TEXT NOT NULL,
    source_note_id BIGINT,
    suggested_fields_json TEXT,
    suggested_service_state TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    decision_note TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS correction_suggestion_seq START 1;
ALTER TABLE correction_suggestions ALTER COLUMN suggestion_id SET DEFAULT nextval('correction_suggestion_seq');
