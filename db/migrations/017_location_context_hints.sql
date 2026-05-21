ALTER TABLE location_context_profile
ADD COLUMN IF NOT EXISTS weather_sensitivity_hint DOUBLE;

ALTER TABLE location_context_profile
ADD COLUMN IF NOT EXISTS demand_volatility_hint DOUBLE;
