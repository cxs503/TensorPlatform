-- 014: persist the training -> MLflow -> model version -> deployment links.
--
-- All columns are nullable so existing rows remain valid.  The backfills only use
-- links already recorded by the platform; they do not invent model/run versions.

ALTER TABLE training_jobs
    ADD COLUMN IF NOT EXISTS mlflow_run_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS registered_model_id INTEGER;

ALTER TABLE model_versions
    ADD COLUMN IF NOT EXISTS mlflow_run_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS mlflow_model_version VARCHAR(32);

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS model_version_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_training_jobs_registered_model'
          AND conrelid = 'training_jobs'::regclass
    ) THEN
        ALTER TABLE training_jobs
            ADD CONSTRAINT fk_training_jobs_registered_model
            FOREIGN KEY (registered_model_id) REFERENCES models(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_deployments_model_version'
          AND conrelid = 'deployments'::regclass
    ) THEN
        ALTER TABLE deployments
            ADD CONSTRAINT fk_deployments_model_version
            FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_training_jobs_mlflow_run_id
    ON training_jobs (mlflow_run_id) WHERE mlflow_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_training_jobs_registered_model_id
    ON training_jobs (registered_model_id) WHERE registered_model_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_model_versions_mlflow_run_id
    ON model_versions (mlflow_run_id) WHERE mlflow_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deployments_model_version_id
    ON deployments (model_version_id) WHERE model_version_id IS NOT NULL;

-- Recover links from the JSON metadata written by older platform versions.
UPDATE training_jobs
SET mlflow_run_id = hyperparameters::jsonb ->> 'mlflow_run_id'
WHERE mlflow_run_id IS NULL
  AND NULLIF(hyperparameters::jsonb ->> 'mlflow_run_id', '') IS NOT NULL;

UPDATE training_jobs
SET registered_model_id = m.id
FROM models m
WHERE training_jobs.registered_model_id IS NULL
  AND (training_jobs.hyperparameters::jsonb ->> 'registered_model_id') ~ '^[0-9]+$'
  AND m.id = (training_jobs.hyperparameters::jsonb ->> 'registered_model_id')::INTEGER;

-- model_versions.parameters has been the durable job/model bridge historically.
WITH latest_job_model AS (
    SELECT DISTINCT ON (tj.id) tj.id AS training_job_pk, mv.model_id
    FROM training_jobs tj
    JOIN model_versions mv
      ON mv.parameters::jsonb ->> 'job_id' = tj.training_job_id
    ORDER BY tj.id, mv.id DESC
)
UPDATE training_jobs tj
SET registered_model_id = COALESCE(tj.registered_model_id, link.model_id),
    model_id = COALESCE(tj.model_id, link.model_id)
FROM latest_job_model link
WHERE tj.id = link.training_job_pk
  AND (tj.registered_model_id IS NULL OR tj.model_id IS NULL);

UPDATE model_versions mv
SET mlflow_run_id = tj.mlflow_run_id
FROM training_jobs tj
WHERE mv.mlflow_run_id IS NULL
  AND tj.mlflow_run_id IS NOT NULL
  AND mv.parameters::jsonb ->> 'job_id' = tj.training_job_id;

-- Backfill only unique exact version matches. Ambiguous legacy labels remain
-- NULL for manual review instead of linking to an arbitrary version row.
WITH unique_model_versions AS (
    SELECT model_id, version_number, MIN(id) AS id
    FROM model_versions
    GROUP BY model_id, version_number
    HAVING COUNT(*) = 1
)
UPDATE deployments d
SET model_version_id = mv.id
FROM unique_model_versions mv
WHERE d.model_version_id IS NULL
  AND mv.model_id = d.model_id
  AND mv.version_number = d.version;

-- is_deployed is derived from active deployment facts, not a separate source
-- of truth. Normalize stale flags left by legacy create/delete/rollback paths.
UPDATE model_versions mv
SET is_deployed = EXISTS (
    SELECT 1
    FROM deployments d
    WHERE d.model_version_id = mv.id
      AND LOWER(d.status) IN
          ('running', 'healthy', 'active', 'deployed', 'serving', 'rolling_back')
)
WHERE mv.is_deployed IS DISTINCT FROM EXISTS (
    SELECT 1
    FROM deployments d
    WHERE d.model_version_id = mv.id
      AND LOWER(d.status) IN
          ('running', 'healthy', 'active', 'deployed', 'serving', 'rolling_back')
);
