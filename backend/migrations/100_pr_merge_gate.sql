-- 100: PR merge gate — status CHECK 增补 awaiting_pr_merge
--
-- 缺陷背景（owner 2026-11-05 指令「对于开发中的agent凡是推送pr的，
-- 要求它们完成pr合并才算完成任务」）：mission 在 auto_approver 批准时即落
-- 'succeeded'，而其成果 PR 由 tp_pusher 稍后才推送/开出——「完成」发生在
-- PR 存在之前，成功态与落地脱钩。
--
-- 本迁移为词表增加非终态 awaiting_pr_merge：
--   tp_pusher 开出 PR     → succeeded 改 awaiting_pr_merge（成功转为临时）
--   PR 合并（任何合并者） → awaiting_pr_merge 改回 succeeded（带合并证据）
--   PR 被关闭未合并       → awaiting_pr_merge 改 failed（分支保留待打捞）
--
-- 词表唯一事实源：backend/contracts/mission_status_vocab.v1.json (v2)
-- （backend/tests/test_status_contracts.py 对拍锁定；TensorPhysics 侧
--   src/tensorphysics/contracts/ 字节镜像同改，改词表须先改契约）。
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_status_check
    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                      'awaiting_pr_merge','succeeded','failed','stopped',
                      'revoked','suspended','orphan'));
