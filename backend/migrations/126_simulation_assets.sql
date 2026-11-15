-- 126: 仿真成果资产楼 —— simulation_assets 登记表（FEAT-SIM-DATAHOUSE / 子件1）
--
-- 背景（锚点事实，owner 定谳）：平台上每次成功计算的**结果场数据没人存**。
-- 2026-11-15 实测 TensorLBM work 树 .npz/.npy/.vtk 计数=0——报告合并进主干、
-- 工作树被回收，场数据随 worktree 一起消失。报告是可读的结论，场数据是
-- 不可再生的证据：重算成本 = 一次完整 HPC 递交。
--
-- 本表只做一件事：给「一次捕获事件」(event) 建档，把**被抢收下来的那些文件**
-- 登记成可复核的指针（磁盘路径 + sha256 + 字节数 + 清单），不复制大文件进库。
--
-- 设计取舍（每条都可被推翻，故写明）：
--   * event_id 由内容摘要派生（m{mission}-{sha12}），天然幂等：同一 mission
--     同内容重复扫描只落一行；内容变了才是新快照（新行）。守护按 15min 轮转，
--     幂等是硬要求，否则表会被重复扫描灌满。
--   * state 三值：
--       captured —— 有产出且全部收下（未触上限）
--       partial  —— 有产出但触到 size/文件数上限（truncated，账要认）；
--       missed   —— 源 worktree 已被回收/不存在，本次抢收落空（=降级记录，
--                    owner 要看的就是「哪一单的结果再也拿不回来了」）。
--   * 清单存 JSONB 数组而不是子表：清单是**一次捕获的不可变快照**，永不单条更新，
--     查询面只需要整体读；子表会带来无收益的 join 与行数膨胀（10 万级文件）。
--   * mission_id/repo 允许 NULL/空：retro 全扫会碰到已不在 missions 表里的孤儿
--     worktree（历史遗留布局），宁可记一行无名资产，也不要丢数据。
--
-- 幂等：CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / DROP+CREATE TRIGGER。
-- 回滚：见文件末尾 ROLLBACK 段。

BEGIN;

CREATE TABLE IF NOT EXISTS simulation_assets (
    id                BIGSERIAL PRIMARY KEY,
    event_id          TEXT        NOT NULL UNIQUE,          -- m{mission}-{sha12} 或 m{mission}-missed
    mission_id        BIGINT,
    repo              VARCHAR(120),
    case_key          TEXT        NOT NULL DEFAULT 'unknown',
    -- 物理/数值参数：mission 预算口径 + 从入口配置里抽出的标量（扁平化，限深限键）
    params            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- 场文件清单 / 曲线文件清单 / 几何文件清单 / 入口配置清单
    -- 元素形如 {"path": "...", "size": 123, "sha256": "..."}
    field_manifest    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    curve_manifest    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    geometry_manifest JSONB       NOT NULL DEFAULT '[]'::jsonb,
    config_manifest   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    report_url        TEXT,                                 -- 平台报告链接（有则填）
    disk_path         TEXT,                                 -- /root/sim-assets/{event_id}（missed 为 NULL）
    bytes_total       BIGINT      NOT NULL DEFAULT 0,
    file_count        INTEGER     NOT NULL DEFAULT 0,
    state             VARCHAR(16) NOT NULL DEFAULT 'captured'
                      CHECK (state IN ('captured', 'partial', 'missed')),
    capture_reason    TEXT,                                 -- missed/partial 的原因（人可读）
    source_worktree   TEXT,                                 -- 抢收来源目录（绝对路径）
    captured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 查询面：/api/v1/assets/sim 分页 + 按 repo/case/state/mission 过滤
CREATE INDEX IF NOT EXISTS ix_simulation_assets_repo_captured
    ON simulation_assets (repo, captured_at DESC);
CREATE INDEX IF NOT EXISTS ix_simulation_assets_mission
    ON simulation_assets (mission_id);
CREATE INDEX IF NOT EXISTS ix_simulation_assets_case
    ON simulation_assets (case_key);
CREATE INDEX IF NOT EXISTS ix_simulation_assets_state_captured
    ON simulation_assets (state, captured_at DESC);

CREATE OR REPLACE FUNCTION simulation_assets_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_simulation_assets_updated_at ON simulation_assets;
CREATE TRIGGER trg_simulation_assets_updated_at
BEFORE UPDATE ON simulation_assets
FOR EACH ROW EXECUTE FUNCTION simulation_assets_touch_updated_at();

COMMIT;

-- ROLLBACK:
-- DROP TRIGGER IF EXISTS trg_simulation_assets_updated_at ON simulation_assets;
-- DROP FUNCTION IF EXISTS simulation_assets_touch_updated_at();
-- DROP TABLE IF EXISTS simulation_assets;
-- DELETE FROM schema_migrations WHERE version = '126_simulation_assets.sql';