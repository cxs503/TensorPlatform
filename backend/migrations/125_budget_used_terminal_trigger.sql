-- 125: 预算记账落库 —— 终态触发器（FP15-BUDGET-ACCOUNTING v2，M-R15-01 续）
--
-- 背景（锚点事实）：平台上所有终态单的 budget_used 都是 {}（2026-11-13 全库
-- succeeded 11601 行里 11591 行为空），于是「预算耗尽 / no_progress」这类终局裁决
-- 的「到底花了多少」外部不可复核。v1（m17391）把**推导**（services/budget_accounting.py）
-- 与**逐点落库**做了，但落库是「每个终态代码路径各自记得写」——只要有一条路径漏写、
-- 或者代码还没部署到在跑的服务上，账就还是空的。本迁移把落库从「调用点纪律」升级为
-- 「数据库不变量」：终态行的 budget_used 由数据库自己补，不依赖任何一个应用版本。
--
-- 语义（零领域语义，只碰平台账本字段）：
--   * 行进入终态（vocab 契约 mission_status_vocab.v1.json 的 terminal +
--     budget_exhausted / no_progress 两个既有裁决词）且 budget_used 为空（NULL 或 {}）
--     → 从 result 事实 + 服务端时钟推导 {tool_calls, tokens, wall_seconds, source}。
--   * 已经有记账（应用侧 /complete 声明值等）→ 一律不动（DB 只补空，不覆盖真值）。
--   * 任一异常 → RETURN NEW，记账绝不阻断终态流转（fail-open）。
--   * 历史键 tool_calls_used / tokens_used / wall_seconds_used 同步写，兼容
--     suspend/resume 续算与 credit_ledger 的口径。
--
-- 幂等：CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS / CREATE TRIGGER。
-- 回滚：见文件末尾 ROLLBACK 段（DROP TRIGGER + DROP FUNCTION）。
--
-- 与代码的对应：本文件 = 生产迁移路径；tests/test_budget_used_terminal_trigger_pg.py
-- 在隔离库里原样执行本文件后跑 5 种终态场景；tools/backfill_budget_used.py 用同一套
-- 口径补历史行。三处同口径，避免「测试绿、生产空」。

BEGIN;

-- ── 0. 安全取整数：jsonb 标量 → 非负整数 / NULL（脏类型一律当没有，不抛错） ──
CREATE OR REPLACE FUNCTION tp_budget_count(v jsonb)
RETURNS integer AS $$
DECLARE
    txt text;
    num numeric;
BEGIN
    IF v IS NULL OR jsonb_typeof(v) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(v) = 'boolean' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(v) = 'number' THEN
        num := (v #>> '{}')::numeric;
    ELSIF jsonb_typeof(v) = 'string' THEN
        txt := trim(both '"' from v::text);
        IF txt !~ '^[0-9]+$' THEN
            RETURN NULL;
        END IF;
        num := txt::numeric;
    ELSE
        RETURN NULL;
    END IF;
    IF num < 0 OR num > 1000000000 THEN
        RETURN NULL;
    END IF;
    RETURN floor(num)::integer;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ── 1. 终态记账函数 ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION tp_stamp_budget_used()
RETURNS trigger AS $$
DECLARE
    -- vocab 契约 terminal（single source: backend/contracts/mission_status_vocab.v1.json）
    -- + 两个既有裁决词（历史/外部导入会用 budget_exhausted / no_progress 落 status）
    terminal_statuses constant text[] := ARRAY[
        'succeeded', 'failed', 'stopped', 'revoked', 'orphan',
        'budget_exhausted', 'no_progress'
    ];
    res        jsonb;
    progress   jsonb;
    tc         integer; tk integer; ws integer;
    src_tc     text; src_tk text; src_ws text;
    detail_n   integer;
    seconds    integer;
    stamp      text;
BEGIN
    -- 记过账的行一概不动
    IF NEW.budget_used IS NOT NULL AND NEW.budget_used <> '{}'::jsonb THEN
        RETURN NEW;
    END IF;
    IF NEW.status IS NULL OR NOT (NEW.status = ANY (terminal_statuses)) THEN
        RETURN NEW;
    END IF;

    res := CASE WHEN jsonb_typeof(NEW.result) = 'object' THEN NEW.result ELSE '{}'::jsonb END;
    progress := CASE WHEN jsonb_typeof(res->'progress') = 'object'
                     THEN res->'progress' ELSE '{}'::jsonb END;

    -- tool_calls：result 事实 > 心跳 progress > runner detail 字符串（与 Python 同序）
    tc := tp_budget_count(res->'tool_calls');
    IF tc IS NOT NULL THEN
        src_tc := 'result_fact';
    ELSE
        tc := tp_budget_count(progress->'tool_calls');
        IF tc IS NOT NULL THEN
            src_tc := 'heartbeat_progress';
        ELSE
            detail_n := NULL;
            IF jsonb_typeof(res->'detail') = 'string' THEN
                detail_n := substring(res->>'detail' from 'tool_calls=([0-9]+)')::integer;
            END IF;
            IF detail_n IS NOT NULL THEN
                tc := detail_n; src_tc := 'runner_detail';
            ELSE
                tc := 0; src_tc := 'unavailable';
            END IF;
        END IF;
    END IF;

    -- tokens
    tk := tp_budget_count(res->'tokens');
    IF tk IS NOT NULL THEN
        src_tk := 'result_fact';
    ELSE
        tk := tp_budget_count(progress->'tokens');
        IF tk IS NOT NULL THEN
            src_tk := 'heartbeat_progress';
        ELSE
            tk := 0; src_tk := 'unavailable';
        END IF;
    END IF;

    -- wall_seconds：声明/事实 > 心跳 > 服务端时钟上界（finished_at - created_at）
    ws := tp_budget_count(res->'wall_seconds');
    IF ws IS NOT NULL AND ws > 0 THEN
        src_ws := 'result_fact';
    ELSE
        ws := tp_budget_count(progress->'wall_seconds');
        IF ws IS NOT NULL AND ws > 0 THEN
            src_ws := 'heartbeat_progress';
        ELSE
            seconds := NULL;
            IF NEW.created_at IS NOT NULL THEN
                seconds := floor(extract(epoch FROM
                    (COALESCE(NEW.finished_at, now()) - NEW.created_at)))::integer;
            END IF;
            ws := GREATEST(COALESCE(seconds, 0), 0);
            src_ws := 'server_clock_upper_bound';
        END IF;
    END IF;

    stamp := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00';

    NEW.budget_used := jsonb_build_object(
        'tool_calls',        tc,
        'tokens',            tk,
        'wall_seconds',      ws,
        'source',            'tool_calls=' || src_tc
                             || ';tokens=' || src_tk
                             || ';wall_seconds=' || src_ws,
        'sources',           jsonb_build_object(
                                 'tool_calls',   src_tc,
                                 'tokens',       src_tk,
                                 'wall_seconds', src_ws),
        'tool_calls_used',   tc,
        'tokens_used',       tk,
        'wall_seconds_used', ws,
        'recorded_at',       stamp,
        'recorded_by',       'terminal_trigger'
    );
    RETURN NEW;
EXCEPTION
    -- 记账永远不许成为终态流转的单点故障
    WHEN OTHERS THEN
        RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_missions_budget_used ON tensor_dev_missions;
CREATE TRIGGER trg_missions_budget_used
    BEFORE INSERT OR UPDATE ON tensor_dev_missions
    FOR EACH ROW
    EXECUTE FUNCTION tp_stamp_budget_used();

COMMIT;

-- ── ROLLBACK ──────────────────────────────────────────────────────────────
-- BEGIN;
--   DROP TRIGGER IF EXISTS trg_missions_budget_used ON tensor_dev_missions;
--   DROP FUNCTION IF EXISTS tp_stamp_budget_used();
--   DROP FUNCTION IF EXISTS tp_budget_count(jsonb);
-- COMMIT;