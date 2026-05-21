CREATE TABLE IF NOT EXISTS notification_events (
    notification_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    publish_reason TEXT,
    source_prediction_run_id TEXT,
    payload_json TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS notification_event_seq START 1;
ALTER TABLE notification_events ALTER COLUMN notification_id SET DEFAULT nextval('notification_event_seq');

CREATE INDEX IF NOT EXISTS idx_notification_events_operator_status
    ON notification_events(operator_id, status, created_at);
