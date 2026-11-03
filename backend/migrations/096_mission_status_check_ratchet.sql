-- 096: tensor_dev_missions status CHECK 棘轮重宽（G-A 单一契约源配套）
--
-- 缺陷背景（2026-11-03 发现）：两份 095 迁移操作同一约束名——
--   095_mission_lifecycle.sql       ADD 10 态（含 suspended/orphan）
--   095_tensor_repro_integrity.sql  DROP 后重 ADD 仅 8 态（缺 suspended/orphan）
-- 按文件字典序后跑 095_tensor_repro_integrity 的新库会终态 8 态，
-- suspend/orphan 写入被 DB 拒绝（活库因应用序侥幸为 10 态）。
-- 本迁移使终态确定性地等于契约词表，与文件序无关。
-- 词表唯一事实源：backend/contracts/mission_status_vocab.v1.json
-- （backend/tests/test_status_contracts.py 对拍锁定，改词表须先改契约）。
ALTER TABLE tensor_dev_missions
    DROP CONSTRAINT IF EXISTS tensor_dev_missions_status_check;
ALTER TABLE tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_status_check
    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                      'succeeded','failed','stopped','revoked',
                      'suspended','orphan'));
