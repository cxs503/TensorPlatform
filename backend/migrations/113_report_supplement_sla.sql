-- 113: 补报派单 SLA（终态↔报告对账门 IN50-W4-N10 落库面）。
--
-- 背景：112 建了补报外箱（幂等键 = mission_id），但工单本身没有截止时间——
-- 「按 SLA 派单」只能靠派单人记忆，重排/重启后逾期无从机器裁定，逾期的单也
-- 不会被升级。本迁移**只增列**，不改既有语义、不删行：
--   sla_hours        INTEGER NOT NULL DEFAULT 72  工单约定的补报时限（小时）
--   due_at           TIMESTAMP                    截止时刻 = created_at + sla_hours
--   escalation_level INTEGER NOT NULL DEFAULT 0   逾期升级次数（防重复升级）
--   escalated_at     TIMESTAMP                    最近一次升级时刻（幂等闸门）
--
-- 为什么 due_at 落行而不落调度器内存：重启/换派单进程后逾期仍是行属性，
-- 「谁迟到多久」可被 SQL 直接审计（零手抄口径）。
--
-- 可移植性：本文件按单列 ALTER 写（Postgres / SQLite 皆接受 ADD COLUMN）；
-- SQLite 无 ADD COLUMN IF NOT EXISTS，因此重复执行时的「列已存在」报错由
-- audit/report_reconcile.py::ensure_supplement_queue 逐列 try/rollback 吞掉，
-- 即代码侧保证幂等，本文件只需在干净库上正向可执行。
--
-- 存量行：due_at 为空的历史工单由对账器按 created_at + sla_hours 补齐计算
-- （不豁免、不回填写库），因此本迁移对已开工单无破坏。

ALTER TABLE tensor_report_supplement_queue
    ADD COLUMN sla_hours INTEGER NOT NULL DEFAULT 72;

ALTER TABLE tensor_report_supplement_queue
    ADD COLUMN due_at TIMESTAMP;

ALTER TABLE tensor_report_supplement_queue
    ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tensor_report_supplement_queue
    ADD COLUMN escalated_at TIMESTAMP;

-- 逾期扫描：WHERE organization_id=? AND status='pending' AND due_at < now()
CREATE INDEX IF NOT EXISTS ix_report_supplement_due
    ON tensor_report_supplement_queue (organization_id, status, due_at);