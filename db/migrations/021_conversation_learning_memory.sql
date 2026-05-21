CREATE TABLE IF NOT EXISTS operator_fact_memory (
    fact_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value_json TEXT NOT NULL,
    confidence TEXT DEFAULT 'medium',
    provenance TEXT NOT NULL,
    source_ref TEXT,
    valid_from_date DATE,
    expires_at TIMESTAMP,
    status TEXT DEFAULT 'active',
    last_confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operator_id, fact_key),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS operator_fact_seq START 1;
ALTER TABLE operator_fact_memory ALTER COLUMN fact_id SET DEFAULT nextval('operator_fact_seq');

CREATE INDEX IF NOT EXISTS idx_operator_fact_memory_operator
    ON operator_fact_memory(operator_id, status, last_updated_at);

CREATE TABLE IF NOT EXISTS operator_hypothesis_state (
    hypothesis_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    hypothesis_key TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    confidence TEXT DEFAULT 'low',
    hypothesis_value_json TEXT,
    evidence_json TEXT,
    trigger_count INTEGER DEFAULT 1,
    last_triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolution_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operator_id, hypothesis_key),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS operator_hypothesis_seq START 1;
ALTER TABLE operator_hypothesis_state ALTER COLUMN hypothesis_id SET DEFAULT nextval('operator_hypothesis_seq');

CREATE INDEX IF NOT EXISTS idx_operator_hypothesis_state_operator
    ON operator_hypothesis_state(operator_id, status, last_triggered_at);

CREATE TABLE IF NOT EXISTS learning_agenda (
    agenda_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    agenda_key TEXT NOT NULL,
    agenda_type TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    priority INTEGER DEFAULT 50,
    question_kind TEXT DEFAULT 'free_text',
    rationale TEXT,
    expected_impact TEXT,
    hypothesis_key TEXT,
    service_date DATE,
    target_fact_key TEXT,
    proposed_true_value_json TEXT,
    proposed_false_value_json TEXT,
    cooldown_until TIMESTAMP,
    last_asked_at TIMESTAMP,
    asked_count INTEGER DEFAULT 0,
    resolved_at TIMESTAMP,
    resolution_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(operator_id, agenda_key),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS learning_agenda_seq START 1;
ALTER TABLE learning_agenda ALTER COLUMN agenda_id SET DEFAULT nextval('learning_agenda_seq');

CREATE INDEX IF NOT EXISTS idx_learning_agenda_operator
    ON learning_agenda(operator_id, status, priority, last_updated_at);
