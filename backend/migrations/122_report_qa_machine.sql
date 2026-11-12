-- 122: QA 产线机器门（FEAT-QA-FACTORY 第 1 轮）
-- 目的：给 tensor_reports 增加机器三查的审计列，使 qa_status 清账可区分「机器审」与「人工/Agent 深检审」，
-- 并把逐篇判定证据（分档+三查指标+反例指针）落库，便于事后复核与回滚（qa_status 历史值可从审计 JSON 重建）。
-- 预注册判据：docs/qa_factory/PREREG_QA_FACTORY.md；执行器：tools/qa_factory/

ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS qa_machine_tier VARCHAR(20);
ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS qa_machine_audit JSONB;
ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS qa_machine_at TIMESTAMPTZ;
-- 'machine' = 机器门授权清账；NULL/'human' = 人工/Agent 深检路径
ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS qa_audit_source VARCHAR(20);

ALTER TABLE tensor_reports DROP CONSTRAINT IF EXISTS tensor_reports_qa_machine_tier_check;
ALTER TABLE tensor_reports ADD CONSTRAINT tensor_reports_qa_machine_tier_check
    CHECK (qa_machine_tier IS NULL OR qa_machine_tier IN ('pass', 'machine-flag', 'suspect'));

-- 清账扫描：pending + machine-pass，存量优先
CREATE INDEX IF NOT EXISTS ix_tensor_reports_qa_machine_pending
    ON tensor_reports (created_at ASC, id ASC)
    WHERE qa_status = 'pending' AND qa_machine_tier = 'pass';

-- 深检队列：pending + 非 pass 档
CREATE INDEX IF NOT EXISTS ix_tensor_reports_qa_deep_queue
    ON tensor_reports (created_at ASC, id ASC)
    WHERE qa_status = 'pending' AND qa_machine_tier IN ('machine-flag', 'suspect');