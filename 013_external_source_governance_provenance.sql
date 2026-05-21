ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS governance_source TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS governance_provider TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS governance_fallback_reason TEXT;
