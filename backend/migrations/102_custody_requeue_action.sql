-- 102: merge custody — mission_lifecycle_log.action CHECK 增补 custody_requeue
--
-- 端点 custody-requeue（owner 2026-11-05「推送 PR 的 agent 管到底」）向
# mission_lifecycle_log 写 action='custody_requeue' 审计行，而该表的 action
# CHECK 词表（095 建立、101 增补 pr_gate）尚无此词——与 #600 首跑实锤同类：
-- PostgreSQL CheckViolation，端点 500；CI 为 sqlite 无 CHECK 故拦不住。
--
-- 棘轮不缩词：101 前词全数保留，仅增补 custody_requeue。
ALTER TABLE mission_lifecycle_log
    DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check;
ALTER TABLE mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_action_check
    CHECK (action IN ('suspend','resume','write_off','orphan_detect',
                      'reclaim','necessity_gate','pr_gate','custody_requeue'));
