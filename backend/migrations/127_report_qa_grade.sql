-- 127: 报告 QA 常设门 v2（owner 2026-11-11 FEAT-TK-GOV-INFRA 令）
-- 四查 + S1/S2/S3 分级：分级结果与判据理由落库（grade / grade_reason），
-- 供 QA 视图与分级分布报告消费。幂等（IF NOT EXISTS）。
ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS grade VARCHAR(4);
ALTER TABLE tensor_reports ADD COLUMN IF NOT EXISTS grade_reason TEXT;

-- 存量消化按 mission_id 倒序 + 分级分布查询的索引
CREATE INDEX IF NOT EXISTS idx_tensor_reports_qa_status ON tensor_reports (qa_status);
CREATE INDEX IF NOT EXISTS idx_tensor_reports_grade ON tensor_reports (grade);
CREATE INDEX IF NOT EXISTS idx_tensor_reports_mission_id ON tensor_reports (mission_id);
