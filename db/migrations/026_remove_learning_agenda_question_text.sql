DROP INDEX IF EXISTS idx_learning_agenda_operator;

ALTER TABLE learning_agenda
DROP COLUMN IF EXISTS question_text;

CREATE INDEX IF NOT EXISTS idx_learning_agenda_operator
    ON learning_agenda(operator_id, status, priority, last_updated_at);
