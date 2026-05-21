-- 029_operator_context_digest.sql
-- Stores the typed digests produced by current_state_retriever and
-- temporal_memory_retriever. The conversation_orchestrator reads the latest
-- row per (operator_id, kind) at chat turn time and grounds replies against
-- it. Retrievers run on events (forecast refresh, actual record, note save,
-- hypothesis change), never on the chat critical path.

CREATE TABLE IF NOT EXISTS operator_context_digest (
    operator_id     VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL,           -- 'current_state' | 'temporal'
    produced_at     TIMESTAMP NOT NULL,
    source_hash     VARCHAR NOT NULL,           -- content hash of retriever inputs
    agent_run_id    VARCHAR,                    -- links to agent_run_log
    payload_json    JSON NOT NULL,              -- serialized dataclass
    PRIMARY KEY (operator_id, kind, produced_at)
);

CREATE INDEX IF NOT EXISTS idx_operator_context_digest_latest
    ON operator_context_digest (operator_id, kind, produced_at DESC);
