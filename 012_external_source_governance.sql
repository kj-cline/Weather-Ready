ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS recommended_category TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS recommended_action TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS priority_score DOUBLE;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS governance_confidence TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS governance_notes_json TEXT;
ALTER TABLE external_source_catalog ADD COLUMN IF NOT EXISTS last_governed_at TIMESTAMP;
