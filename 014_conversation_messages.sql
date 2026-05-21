CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    role TEXT NOT NULL,                 -- 'operator' or 'assistant'
    content TEXT NOT NULL,
    tool_calls_json TEXT,               -- JSON of tool calls made (assistant only)
    tool_results_json TEXT,             -- JSON of tool results (assistant only)
    phase TEXT,                         -- setup, enrichment, operations
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS conversation_message_seq START 1;
ALTER TABLE conversation_messages ALTER COLUMN message_id SET DEFAULT nextval('conversation_message_seq');

CREATE INDEX IF NOT EXISTS idx_conversation_messages_operator
    ON conversation_messages(operator_id, created_at DESC);

-- Add conversation-learned fields to operator_behavior_state
ALTER TABLE operator_behavior_state ADD COLUMN IF NOT EXISTS brevity_preference TEXT DEFAULT 'normal';
ALTER TABLE operator_behavior_state ADD COLUMN IF NOT EXISTS proactive_detail_preference BOOLEAN DEFAULT TRUE;
ALTER TABLE operator_behavior_state ADD COLUMN IF NOT EXISTS conversation_count INTEGER DEFAULT 0;
