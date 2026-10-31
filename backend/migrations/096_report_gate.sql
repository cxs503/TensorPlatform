-- 095: report gate — track missions that completed without a report.
-- When a mission completes with status='succeeded' but no report_id in result,
-- it gets marked as report_owed=true and enters the report backlog queue.
-- This makes missing reports visible without blocking the delivery flow.

-- Add report_owed flag to missions
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS report_owed BOOLEAN NOT NULL DEFAULT FALSE;

-- Add report_id to missions (set when report is published for this mission)
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS report_id INT REFERENCES tensor_reports(id) ON DELETE SET NULL;

-- Index for the report owed queue
CREATE INDEX IF NOT EXISTS ix_tensor_missions_report_owed
    ON tensor_dev_missions (organization_id, finished_at DESC)
    WHERE report_owed = TRUE AND status = 'succeeded';

-- Add report_qa_status to reports (for quality gate tracking)
ALTER TABLE tensor_reports
    ADD COLUMN IF NOT EXISTS qa_status VARCHAR(20) NOT NULL DEFAULT 'pending'
    CHECK (qa_status IN ('pending', 'passed', 'rejected'));

-- Add qa_rejection_reason for tracking why a report was rejected
ALTER TABLE tensor_reports
    ADD COLUMN IF NOT EXISTS qa_rejection_reason TEXT;

-- Add qa_checked_at timestamp
ALTER TABLE tensor_reports
    ADD COLUMN IF NOT EXISTS qa_checked_at TIMESTAMPTZ;

-- Index for QA queue
CREATE INDEX IF NOT EXISTS ix_tensor_reports_qa_pending
    ON tensor_reports (organization_id, created_at DESC)
    WHERE qa_status = 'pending';
