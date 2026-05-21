-- Task 17: role-specific agent framework — run log and signal origin columns.
-- Run log for every dispatch (ok / empty / blocked / failed).
CREATE TABLE IF NOT EXISTS agent_run_log (
    run_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    status TEXT NOT NULL,
    triggered_at TIMESTAMP NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    outputs_count INTEGER DEFAULT 0,
    error TEXT,
    blocked_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_run_log_operator_role
    ON agent_run_log (operator_id, role, triggered_at DESC);

-- Staging fields for agent-produced external signals.
-- 'observed' (default) = flows into the live refresh via the existing cascade stack.
-- 'proposed' = written for operator review only; the live refresh ignores these rows.
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'observed';
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS origin_agent TEXT;
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS staged_at TIMESTAMP;
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
ALTER TABLE external_signal_log ADD COLUMN IF NOT EXISTS review_resolution TEXT;

CREATE INDEX IF NOT EXISTS idx_external_signal_log_status_origin
    ON external_signal_log (operator_id, status, origin_agent);
