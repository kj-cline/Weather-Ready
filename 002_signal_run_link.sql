ALTER TABLE external_signal_log
ADD COLUMN IF NOT EXISTS source_prediction_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_external_signal_operator_run
    ON external_signal_log(operator_id, source_prediction_run_id);
