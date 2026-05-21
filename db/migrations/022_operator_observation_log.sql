CREATE TABLE IF NOT EXISTS operator_observation_log (
    observation_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    source_note_id BIGINT,
    service_date DATE,
    service_window TEXT,
    observation_type TEXT NOT NULL,
    dependency_group TEXT,
    component_scope TEXT,
    direction TEXT,
    strength TEXT DEFAULT 'medium',
    recurrence_hint TEXT DEFAULT 'possible_recurring',
    runtime_target TEXT,
    question_target TEXT,
    promotion_mode TEXT DEFAULT 'qualitative_only',
    observation_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS operator_observation_seq START 1;
ALTER TABLE operator_observation_log ALTER COLUMN observation_id SET DEFAULT nextval('operator_observation_seq');

CREATE INDEX IF NOT EXISTS idx_operator_observation_log_operator
    ON operator_observation_log(operator_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_operator_observation_log_runtime
    ON operator_observation_log(operator_id, runtime_target, created_at DESC);
