CREATE TABLE IF NOT EXISTS learning_decision_log (
    decision_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    status TEXT NOT NULL,
    hypothesis_key TEXT,
    agenda_key TEXT,
    runtime_target TEXT,
    promotion_policy TEXT,
    rationale TEXT,
    evidence_json TEXT,
    action_json TEXT,
    source_ref TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS learning_decision_seq START 1;
ALTER TABLE learning_decision_log ALTER COLUMN decision_id SET DEFAULT nextval('learning_decision_seq');

CREATE INDEX IF NOT EXISTS idx_learning_decision_log_operator
    ON learning_decision_log(operator_id, created_at DESC);
