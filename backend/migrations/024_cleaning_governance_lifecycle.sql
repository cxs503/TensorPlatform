ALTER TABLE dataset_governance_reports
    ADD COLUMN IF NOT EXISTS source_dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS output_dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS cleaning_run_id VARCHAR(36) REFERENCES data_cleaning_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_governance_reports_source
    ON dataset_governance_reports(source_dataset_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_governance_reports_output
    ON dataset_governance_reports(output_dataset_id, checked_at DESC);

ALTER TABLE data_cleaning_runs DROP CONSTRAINT IF EXISTS ck_data_cleaning_run_status;
ALTER TABLE data_cleaning_runs
    ADD CONSTRAINT ck_data_cleaning_run_status
        CHECK (status IN ('running', 'success', 'rejected', 'failed'));

UPDATE dataset_governance_reports r
SET source_dataset_id = COALESCE(d.governed_from, r.dataset_id),
    output_dataset_id = CASE WHEN d.governed_from IS NOT NULL THEN r.dataset_id ELSE NULL END
FROM datasets d
WHERE d.id = r.dataset_id
  AND r.source_dataset_id IS NULL;
