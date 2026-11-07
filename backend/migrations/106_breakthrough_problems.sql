-- 106: breakthrough_problems — 攻关问题准入与七环状态机落库面。
--
-- 背景：S4/S5 突破/创造类任务此前只有「派发梯度」与「探索预算」两个记账面，
-- 缺少「攻关问题本体」的库内实体：问题定义 / 基线 / 验收标准 / 七环阶段 /
-- 战役关联都没有可查询、可机检的落库。本迁移补齐 breakthrough_problems 表。
--
-- 准入门（admission gate）三要素（缺任一 → 422 并列缺件清单）：
--   1. problem_definition  问题定义（问题树 ≥3 子问题）
--   2. baseline            基线（现状最好方案 + 差距）
--   3. acceptance_criteria 验收标准（可机检处必须机检化）
-- 门必须可失败：三要素不全的攻关任务必须被拒（红队负例）。
-- 普通工程单可显式声明豁免（exemption_declared 声明位）；但科研任务
-- 不得伪装成工程单绕门（exemption_guard 在服务层强制）。
--
-- 可移植性：本文件被单测当作建库脚本直接执行（SQLite），因此只使用
-- TEXT / INTEGER / BOOLEAN / DOUBLE PRECISION / TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP、
-- IF NOT EXISTS 与标准 CHECK —— Postgres 与 SQLite 均接受，保证「迁移 ⇔ 代码」不漂。
-- organization_id / campaign_id 为软引用（不加外键）：攻关问题是多租户共享的
-- 度量面，允许在无组织上下文的部署验证/回放中写入（本仓惯例：度量表不阻塞租户生命周期）。

CREATE TABLE IF NOT EXISTS breakthrough_problems (
    id                  BIGSERIAL PRIMARY KEY,
    -- 战役关联（软引用：campaign_plans / factory_strategy_campaigns）
    campaign_id         BIGINT,
    plan_id             BIGINT,
    -- 挂接 mission（软引用：tensor_dev_missions）
    mission_id          BIGINT,
    organization_id     BIGINT,
    -- 问题本体
    title               TEXT NOT NULL,
    problem_statement   TEXT NOT NULL,
    -- 三要素（准入门）
    problem_tree        TEXT NOT NULL DEFAULT '[]',   -- JSON 数组：子问题清单（≥3）
    baseline_summary    TEXT NOT NULL DEFAULT '',     -- 现状最好方案
    baseline_gap        TEXT NOT NULL DEFAULT '',     -- 现状与目标的差距
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',   -- JSON 数组：可机检判据清单
    -- 任务属性
    difficulty          INTEGER NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
    task_class          TEXT NOT NULL DEFAULT 'engineering'
                         CHECK (task_class IN ('research','engineering')),
    -- 七环状态机（problem_definition→investigation→hypothesis→experiment→
    -- challenge→acceptance→distillation）
    phase               TEXT NOT NULL DEFAULT 'problem_definition'
                         CHECK (phase IN ('problem_definition','investigation','hypothesis',
                                          'experiment','challenge','acceptance','distillation')),
    -- 准入结果
    admission_status    TEXT NOT NULL DEFAULT 'pending'
                         CHECK (admission_status IN ('pending','admitted','rejected','exempted')),
    -- 豁免声明位（普通工程单显式声明；科研任务不得借它绕门）
    exemption_declared  BOOLEAN NOT NULL DEFAULT FALSE,
    -- 缺件清单（准入被拒时并列列出）
    missing_elements    TEXT NOT NULL DEFAULT '[]',
    -- 审计
    created_by          BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_breakthrough_problems_org
    ON breakthrough_problems (organization_id, campaign_id);
CREATE INDEX IF NOT EXISTS ix_breakthrough_problems_phase
    ON breakthrough_problems (phase);
CREATE INDEX IF NOT EXISTS ix_breakthrough_problems_admission
    ON breakthrough_problems (admission_status);
CREATE INDEX IF NOT EXISTS ix_breakthrough_problems_mission
    ON breakthrough_problems (mission_id) WHERE mission_id IS NOT NULL;
