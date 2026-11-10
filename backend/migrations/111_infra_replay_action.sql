-- 111: 战役损耗自动补偿闭环 — 审计词表增补（infra_replay / mission_replay / model_blacklisted）
--
-- 背景（P189-N07 能力成熟化单）：`tools/validation/infra_replay.py` 把「损耗已诊断」
-- 升级为「损耗被补偿且可审计」，每次重播要写两处 append-only 审计：
--   * mission_lifecycle_log.action = 'infra_replay'（含 detail.action=replay_created / replay_denied）
--   * campaign_events.event_type    = 'mission_replay' / 'model_blacklisted'（熔断通知面）
-- 两张表的 CHECK 词表（095 建立，101/102/103 增补）尚无这三个词，真跑必然
-- CheckViolation —— 与 #600 custody_requeue 同类坑：CI 若走 sqlite 无 CHECK，拦不住。
--
-- 棘轮不缩词：既有词全数保留，仅增补。回滚 = 重跑本文件上一版约束定义（词表只增不改）。
ALTER TABLE mission_lifecycle_log
    DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check;
ALTER TABLE mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_action_check
    CHECK (action IN ('suspend','resume','write_off','orphan_detect',
                      'reclaim','necessity_gate','pr_gate','custody_requeue',
                      'reopen','infra_replay'));

ALTER TABLE campaign_events
    DROP CONSTRAINT IF EXISTS campaign_events_event_type_check;
ALTER TABLE campaign_events
    ADD CONSTRAINT campaign_events_event_type_check
    CHECK (event_type IN ('node_status_change','milestone_status_change','block_detected',
                          'block_resolved','spinning_detected','escalation_created',
                          'snapshot_taken','plan_updated','mission_dispatched',
                          'mission_completed','guerrilla_detected',
                          'mission_replay','model_blacklisted'));