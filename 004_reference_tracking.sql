ALTER TABLE prediction_runs
ADD COLUMN IF NOT EXISTS reference_status TEXT;

ALTER TABLE prediction_runs
ADD COLUMN IF NOT EXISTS reference_model TEXT;

ALTER TABLE prediction_runs
ADD COLUMN IF NOT EXISTS reference_details_json TEXT;

ALTER TABLE published_forecast_state
ADD COLUMN IF NOT EXISTS reference_status TEXT;

ALTER TABLE published_forecast_state
ADD COLUMN IF NOT EXISTS reference_model TEXT;

ALTER TABLE working_forecast_state
ADD COLUMN IF NOT EXISTS reference_status TEXT;

ALTER TABLE working_forecast_state
ADD COLUMN IF NOT EXISTS reference_model TEXT;

ALTER TABLE forecast_publication_snapshots
ADD COLUMN IF NOT EXISTS reference_status TEXT;

ALTER TABLE forecast_publication_snapshots
ADD COLUMN IF NOT EXISTS reference_model TEXT;
