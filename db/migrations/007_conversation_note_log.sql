CREATE TABLE IF NOT EXISTS conversation_note_log (
    note_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE,
    service_window TEXT,
    raw_note TEXT NOT NULL,
    suggested_service_state TEXT,
    suggested_correction_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS conversation_note_seq START 1;
ALTER TABLE conversation_note_log ALTER COLUMN note_id SET DEFAULT nextval('conversation_note_seq');
