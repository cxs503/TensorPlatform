-- 106_failure_postmortems.sql
-- 失败事后复盘（postmortem）：表 + 假设登记 + 可引用规则 + 引用台账
--
-- 机制（代码）：tools/failure_postmortems.py（枚举/抽取/双闸/渲染）
--             tools/harvest_failure_postmortems.py（存储层/收割/引用）
--             backend/services/failure_postmortem_service.py（终态结算钩子接线）
--             backend/api/failure_postmortems.py（API）
--
-- 七环枚举：intake / plan / setup / implement / verify / deliver / accept
-- 五类归因：theory_error / implementation_error / experiment_design_error
--           / resource_limit / endpoint_disease
-- 纪律：draft 必须由 challenger/coordinator（非起草者）定稿；规则 append-only
--       注入 bootstrap PLAYBOOK；引用逐条落 postmortem_rule_references。

BEGIN;

CREATE TABLE IF NOT EXISTS hypothesis_registry (
    hypothesis_id   TEXT PRIMARY KEY,
    mission_id      BIGINT,
    statement       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'mission_goal',
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT hypothesis_registry_status_check
        CHECK (status IN ('open','refuted','supported','inconclusive'))
);

CREATE TABLE IF NOT EXISTS failure_postmortems (
    postmortem_id      TEXT PRIMARY KEY,
    mission_id         BIGINT NOT NULL,
    organization_id    BIGINT,
    repo               TEXT,
    hypothesis_id      TEXT REFERENCES hypothesis_registry(hypothesis_id),
    initial_hypothesis TEXT NOT NULL,
    failed_step        TEXT NOT NULL,
    failure_stage      TEXT NOT NULL,
    legacy_stage       TEXT,
    error_class        TEXT NOT NULL,
    invalidated_paths  TEXT[] NOT NULL DEFAULT '{}',
    avoidance_rules    TEXT[] NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'draft',
    harvested_by       TEXT NOT NULL,
    challenge          JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_by       TEXT,
    confirm_role       TEXT,
    confirmed_at       TIMESTAMPTZ,
    rejected_reason    TEXT,
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT failure_postmortems_mission_uq UNIQUE (mission_id),
    CONSTRAINT failure_postmortems_stage_check
        CHECK (failure_stage IN ('intake','plan','setup','implement','verify','deliver','accept')),
    CONSTRAINT failure_postmortems_class_check
        CHECK (error_class IN ('theory_error','implementation_error','experiment_design_error',
                               'resource_limit','endpoint_disease')),
    CONSTRAINT failure_postmortems_status_check
        CHECK (status IN ('draft','disputed','confirmed','rejected')),
    CONSTRAINT failure_postmortems_confirm_consistent
        CHECK (confirmed_by IS NULL OR confirm_role IS NOT NULL),
    -- 定稿行必须有定稿者/时间；未定稿行不得预填定稿字段（防"半定稿"状态）
    CONSTRAINT failure_postmortems_confirmed_fields
        CHECK ((status <> 'confirmed')
               OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
    -- 起草者不得自审
    CONSTRAINT failure_postmortems_no_self_review
        CHECK (confirmed_by IS NULL OR confirmed_by <> harvested_by)
);

CREATE INDEX IF NOT EXISTS idx_fpm_stage ON failure_postmortems (failure_stage, status);
CREATE INDEX IF NOT EXISTS idx_fpm_class ON failure_postmortems (error_class, status);
CREATE INDEX IF NOT EXISTS idx_fpm_mission ON failure_postmortems (mission_id);
CREATE INDEX IF NOT EXISTS idx_fpm_repo ON failure_postmortems (repo, status);

CREATE TABLE IF NOT EXISTS postmortem_rules (
    rule_id        TEXT PRIMARY KEY,
    postmortem_id  TEXT NOT NULL REFERENCES failure_postmortems(postmortem_id),
    mission_id     BIGINT,
    rule_index     INTEGER NOT NULL,
    rule_text      TEXT NOT NULL,
    failure_stage  TEXT NOT NULL,
    error_class    TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT postmortem_rules_uq UNIQUE (postmortem_id, rule_index)
);

CREATE INDEX IF NOT EXISTS idx_pmr_stage ON postmortem_rules (failure_stage, error_class);
CREATE INDEX IF NOT EXISTS idx_pmr_created ON postmortem_rules (created_at);

CREATE TABLE IF NOT EXISTS postmortem_rule_references (
    reference_id           BIGSERIAL PRIMARY KEY,
    rule_id                TEXT NOT NULL REFERENCES postmortem_rules(rule_id),
    referencing_mission_id BIGINT,
    reference_kind         TEXT NOT NULL,
    evidence               TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT postmortem_rule_references_uq
        UNIQUE (rule_id, referencing_mission_id, reference_kind),
    CONSTRAINT postmortem_rule_references_kind_check
        CHECK (reference_kind IN ('later_postmortem_consult','mission_text_citation','claim_injection'))
);

CREATE INDEX IF NOT EXISTS idx_pmrr_rule ON postmortem_rule_references (rule_id);
CREATE INDEX IF NOT EXISTS idx_pmrr_kind ON postmortem_rule_references (reference_kind);

COMMIT;

-- 回滚（手工，非自动化）：
--   DROP TABLE IF EXISTS postmortem_rule_references, postmortem_rules,
--        failure_postmortems, hypothesis_registry;