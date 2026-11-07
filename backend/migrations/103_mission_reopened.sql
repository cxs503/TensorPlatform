-- 103: REOPENED 状态正式化（SPEC §4.2 / 战役 IN19-Q7）
--
-- 缺陷背景：合并后回归发现问题（回归红、证据失效、审查结论被推翻）时，平台没有
-- 正式重开口径——历史做法是库外守护/人工直接把 mission 拉回可执行态，状态词表里
-- 既看不到「这是一张被重开的单」，也查不到重开主体、原验收记录与理由。SPEC §4.2
-- 要求：REOPENED 由独立治理侧（非生产者本人）触发，必须引用原验收记录并说明理由。
--
-- 本迁移做四件事（棘轮不缩词：101/102 已增的词全数保留）：
--   1) tensor_dev_missions.status CHECK 增补非终态 reopened（终态集不变）；
--   2) 增 reopened_from / reopen_ref / reopen_reason / reopened_at 四列，
--      承载「从哪个终态重开、引用哪份原验收记录、理由、时刻」；
--   3) 加 DB 级硬约束：status='reopened' 时 reopened_from/reopen_ref/reopen_reason
--      必须非空——「重开必须引用原验收记录」不靠代码自觉，靠约束；
--   4) mission_lifecycle_log.action CHECK 增补 reopen（与 101 pr_gate /
--      102 custody_requeue 同型棘轮），每次重开留一行 append-only 审计。
--
-- 词表唯一事实源：backend/contracts/mission_status_vocab.v1.json (v3)
-- 迁移图唯一事实源：backend/contracts/mission_state_machine.v1.json (v1)
-- （backend/tests/test_mission_reopened_state.py 对拍锁定；改词表/改图须先改契约）
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_status_check
    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                      'awaiting_pr_merge','succeeded','failed','stopped',
                      'revoked','suspended','reopened','orphan'));

ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS reopened_from VARCHAR(32);
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS reopen_ref TEXT;
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS reopen_reason TEXT;
ALTER TABLE tensor_dev_missions
    ADD COLUMN IF NOT EXISTS reopened_at TIMESTAMPTZ;

ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_reopen_evidence_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_reopen_evidence_check
    CHECK (status <> 'reopened'
           OR (reopened_from IS NOT NULL
               AND reopen_ref IS NOT NULL
               AND reopen_reason IS NOT NULL));

ALTER TABLE mission_lifecycle_log
    DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check;
ALTER TABLE mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_action_check
    CHECK (action IN ('suspend','resume','write_off','orphan_detect',
                      'reclaim','necessity_gate','pr_gate','custody_requeue',
                      'reopen'));

CREATE INDEX IF NOT EXISTS ix_mission_reopened_at
    ON tensor_dev_missions (reopened_at)
    WHERE status = 'reopened';