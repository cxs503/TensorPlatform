-- 101: PR merge gate — mission_lifecycle_log.action CHECK 增补 pr_gate
--
-- #598 落地后首次真实回填即暴露：pr-gate 端点向 mission_lifecycle_log 写
-- action='pr_gate' 审计行，而该表的 action CHECK 词表（migrations/095
-- 建立）只有 suspend/resume/write_off/orphan_detect/reclaim/necessity_gate
-- 六词——PostgreSQL CheckViolation，端点 500。CI 为 sqlite 无 CHECK 故
-- 未能拦截；生产 PG 行为级测试（TP_PR_GATE_PG_DSN）将锁住本约束。
--
-- 棘轮不缩词：六词全数保留，仅增补 pr_gate。
ALTER TABLE mission_lifecycle_log
    DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check;
ALTER TABLE mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_action_check
    CHECK (action IN ('suspend','resume','write_off','orphan_detect',
                      'reclaim','necessity_gate','pr_gate'));
