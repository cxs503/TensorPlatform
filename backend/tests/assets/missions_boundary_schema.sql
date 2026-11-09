--
-- PostgreSQL database dump
--

-- Dumped from database version 15.5
-- Dumped by pg_dump version 15.5

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: componentstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.componentstatus AS ENUM (
    'DRAFT',
    'REVIEW',
    'PUBLISHED'
);


--
-- Name: modelstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.modelstatus AS ENUM (
    'DRAFT',
    'TRAINING',
    'READY',
    'DEPLOYED',
    'DEPRECATED'
);


--
-- Name: pricingmodel; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.pricingmodel AS ENUM (
    'FREE',
    'SUBSCRIPTION',
    'PER_USE',
    'REVENUE_SHARE'
);


--
-- Name: projectstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.projectstatus AS ENUM (
    'ACTIVE',
    'PAUSED',
    'COMPLETED',
    'ARCHIVED'
);


--
-- Name: trainingstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.trainingstatus AS ENUM (
    'PENDING',
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'CANCELLED'
);


--
-- Name: userrole; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.userrole AS ENUM (
    'ADMIN',
    'MANAGER',
    'ENGINEER',
    'VIEWER'
);


--
-- Name: audit_logs_append_only_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_logs_append_only_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % on row id=% rejected',
        TG_OP, OLD.id;
END;
$$;


--
-- Name: factory_memory_events_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.factory_memory_events_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'factory_memory_events is append-only';
 END IF;
 IF (NEW.event_id, NEW.organization_id, NEW.agent_id, NEW.session_id, NEW.task_id,
     NEW.event_type, NEW.source, NEW.provenance, NEW.payload, NEW.sensitivity,
     NEW.idempotency_key, NEW.created_at)
    IS DISTINCT FROM
    (OLD.event_id, OLD.organization_id, OLD.agent_id, OLD.session_id, OLD.task_id,
     OLD.event_type, OLD.source, OLD.provenance, OLD.payload, OLD.sensitivity,
     OLD.idempotency_key, OLD.created_at) THEN
  RAISE EXCEPTION 'factory_memory_events content is append-only; only verification status may transition';
 END IF;
 RETURN NEW;
END;
$$;


--
-- Name: fn_repro_verdicts_no_modify(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_repro_verdicts_no_modify() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'tensor_repro_verdicts is append-only; UPDATE/DELETE forbidden (evidence preservation)';
    RETURN NULL;
END;
$$;


--
-- Name: tensor_events_forbid_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tensor_events_forbid_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'tensor_agent_session_events is append-only';
END;
$$;


--
-- Name: tensorphysics_runs_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tensorphysics_runs_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: tp_ledger_finish_tag(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tp_ledger_finish_tag() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE gl text; r text; frm timestamptz;
BEGIN
  IF NEW.status = 'succeeded' AND coalesce(OLD.status,'') <> 'succeeded'
     AND coalesce(NEW.task_key,'') LIKE 'selfstart-%' THEN
    SELECT v INTO frm FROM ledger_gate_meta WHERE k='finish_gate_from';
    IF frm IS NOT NULL AND NEW.created_at >= frm THEN
      gl := split_part(coalesce(NEW.goal,''), E'\n', 1);
      r  := coalesce(NEW.result->>'summary', NEW.result::text, '');
      IF gl !~ '【锚点' AND r !~ '【锚点'
         AND NOT EXISTS (SELECT 1 FROM feature_ledger fl
                         WHERE length(fl.slug)>=8
                           AND (r ILIKE '%'||fl.slug||'%' OR gl ILIKE '%'||fl.slug||'%')) THEN
        NEW.result := coalesce(NEW.result,'{}'::jsonb)
                      || '{"ledger_gate":"unanchored"}'::jsonb;
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END
$$;


--
-- Name: tp_ledger_gate(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tp_ledger_gate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE tk text; g text;
BEGIN
  tk := coalesce(NEW.task_key, '');
  g  := coalesce(NEW.goal, '');
  IF tk = '' THEN RETURN NEW; END IF;  -- 机制单（议事会/补报等无 task_key）
  IF tk ~ '^(p[0-9]+-|paper-|pr-|arbiter-|interrogator:|fix:|propose-|develop-|campaign-|council-|merge-|qr-|guide-|sat-|chronicle-|basefix-|bakeoff-|shadowdiag-|verify-|planner-|report-backfill|ship-fix-|case-sweep|repro-)' THEN
    RETURN NEW;  -- 组织线（战役DAG/提案/仲裁/论文/合并/质询/修补/复现官等，自有锚体系）
  END IF;
  IF g ~ '【锚点' THEN RETURN NEW; END IF;  -- 有锚点声明（SLUG 或 NEW:理由）
  IF EXISTS (SELECT 1 FROM feature_ledger WHERE length(slug) >= 8 AND g ILIKE '%'||slug||'%') THEN
    RETURN NEW;  -- goal 直接引用总账 slug
  END IF;
  RAISE EXCEPTION 'LEDGER-GATE 拒绝无锚点任务 %：owner 2026-11-09 不重复/不随机令。goal 首行加【锚点=SLUG】或【锚点=NEW:一句话理由】；可选锚点: SELECT slug,title FROM feature_ledger WHERE status IN (''planned'',''in_progress'')', tk;
END
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ab_experiment_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ab_experiment_requests (
    id bigint NOT NULL,
    request_id character varying(64) NOT NULL,
    experiment_id character varying(64) NOT NULL,
    subject_id character varying(256) NOT NULL,
    assigned_variant character varying(1) NOT NULL,
    served_variant character varying(1),
    assigned_model_id integer,
    served_model_id integer,
    assigned_deployment_id integer,
    served_deployment_id integer,
    features jsonb NOT NULL,
    prediction jsonb,
    actual_value jsonb,
    metric_value double precision,
    fallback_used boolean DEFAULT false NOT NULL,
    latency_ms double precision,
    status character varying(24) NOT NULL,
    error text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    feedback_at timestamp without time zone,
    CONSTRAINT ab_experiment_requests_assigned_variant_check CHECK (((assigned_variant)::text = ANY ((ARRAY['A'::character varying, 'B'::character varying])::text[]))),
    CONSTRAINT ab_experiment_requests_served_variant_check CHECK (((served_variant)::text = ANY ((ARRAY['A'::character varying, 'B'::character varying])::text[]))),
    CONSTRAINT ab_experiment_requests_status_check CHECK (((status)::text = ANY ((ARRAY['success'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: ab_experiment_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ab_experiment_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ab_experiment_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ab_experiment_requests_id_seq OWNED BY public.ab_experiment_requests.id;


--
-- Name: ab_experiments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ab_experiments (
    id bigint NOT NULL,
    experiment_id character varying(64) NOT NULL,
    name character varying(256) NOT NULL,
    model_a_id integer,
    model_b_id integer,
    metric character varying(32) DEFAULT 'accuracy'::character varying NOT NULL,
    traffic_split_a double precision DEFAULT 50.0 NOT NULL,
    status character varying(32) DEFAULT 'created'::character varying NOT NULL,
    winner character varying(16),
    results_a jsonb DEFAULT '[]'::jsonb NOT NULL,
    results_b jsonb DEFAULT '[]'::jsonb NOT NULL,
    analysis jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    deployment_a_id integer,
    deployment_b_id integer,
    organization_id integer,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: ab_experiments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ab_experiments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ab_experiments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ab_experiments_id_seq OWNED BY public.ab_experiments.id;


--
-- Name: access_audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_audits (
    id integer NOT NULL,
    user_id integer,
    username character varying(100),
    resource_type character varying(50),
    resource character varying(255),
    action character varying(50),
    result character varying(20),
    ip_address character varying(50),
    user_agent character varying(500),
    request_method character varying(10),
    request_path character varying(500),
    request_params json,
    response_code integer,
    duration_ms integer,
    policy_matched character varying(100),
    masking_applied integer,
    "timestamp" timestamp without time zone,
    extra_info json
);


--
-- Name: COLUMN access_audits.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.user_id IS '用户ID';


--
-- Name: COLUMN access_audits.username; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.username IS '用户名';


--
-- Name: COLUMN access_audits.resource_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.resource_type IS '资源类型';


--
-- Name: COLUMN access_audits.resource; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.resource IS '资源标识';


--
-- Name: COLUMN access_audits.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.action IS '操作类型';


--
-- Name: COLUMN access_audits.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.result IS '结果: success/denied/failed';


--
-- Name: COLUMN access_audits.ip_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.ip_address IS 'IP地址';


--
-- Name: COLUMN access_audits.user_agent; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.user_agent IS '用户代理';


--
-- Name: COLUMN access_audits.request_method; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.request_method IS '请求方法';


--
-- Name: COLUMN access_audits.request_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.request_path IS '请求路径';


--
-- Name: COLUMN access_audits.request_params; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.request_params IS '请求参数(脱敏后)';


--
-- Name: COLUMN access_audits.response_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.response_code IS '响应码';


--
-- Name: COLUMN access_audits.duration_ms; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.duration_ms IS '耗时(毫秒)';


--
-- Name: COLUMN access_audits.policy_matched; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.policy_matched IS '匹配的策略';


--
-- Name: COLUMN access_audits.masking_applied; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.masking_applied IS '是否应用了脱敏';


--
-- Name: COLUMN access_audits."timestamp"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits."timestamp" IS '时间戳';


--
-- Name: COLUMN access_audits.extra_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_audits.extra_info IS '额外信息';


--
-- Name: access_audits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.access_audits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: access_audits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.access_audits_id_seq OWNED BY public.access_audits.id;


--
-- Name: access_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_policies (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    resource_type character varying(50) NOT NULL,
    resource_pattern character varying(255) NOT NULL,
    action character varying(50) NOT NULL,
    effect character varying(20) NOT NULL,
    conditions json,
    priority integer,
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by integer
);


--
-- Name: COLUMN access_policies.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.name IS '策略名称';


--
-- Name: COLUMN access_policies.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.description IS '策略描述';


--
-- Name: COLUMN access_policies.resource_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.resource_type IS '资源类型: table/column/api/file';


--
-- Name: COLUMN access_policies.resource_pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.resource_pattern IS '资源匹配模式(正则)';


--
-- Name: COLUMN access_policies.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.action IS '操作: read/write/delete/admin';


--
-- Name: COLUMN access_policies.effect; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.effect IS '效果: allow/deny';


--
-- Name: COLUMN access_policies.conditions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.conditions IS '条件(如时间、IP等)';


--
-- Name: COLUMN access_policies.priority; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.priority IS '优先级(数字越小优先级越高)';


--
-- Name: COLUMN access_policies.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.is_active IS '是否启用';


--
-- Name: COLUMN access_policies.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.access_policies.created_by IS '创建者ID';


--
-- Name: access_policies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.access_policies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: access_policies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.access_policies_id_seq OWNED BY public.access_policies.id;


--
-- Name: agent_directory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_directory (
    seat_id text NOT NULL,
    model text,
    role text,
    self_desc text,
    skills text,
    current_goal text,
    needs text,
    offers text,
    recent_missions text,
    lessons text DEFAULT ''::text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: alert_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_rules (
    id integer NOT NULL,
    name character varying(256) NOT NULL,
    metric character varying(64) NOT NULL,
    threshold double precision NOT NULL,
    operator character varying(16) NOT NULL,
    severity character varying(32) NOT NULL,
    enabled boolean,
    created_at timestamp without time zone
);


--
-- Name: alert_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alert_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alert_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alert_rules_id_seq OWNED BY public.alert_rules.id;


--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id integer NOT NULL,
    title character varying(256) NOT NULL,
    severity character varying(32) NOT NULL,
    source character varying(64),
    entity_id character varying(64),
    message text,
    is_resolved boolean,
    resolved_at timestamp without time zone,
    created_at timestamp without time zone
);


--
-- Name: alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alerts_id_seq OWNED BY public.alerts.id;


--
-- Name: algorithm_hyperparam_presets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.algorithm_hyperparam_presets (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    description character varying(500),
    task_type character varying(32) NOT NULL,
    algorithm character varying(64) NOT NULL,
    params jsonb,
    organization_id integer,
    created_by integer,
    is_builtin boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: algorithm_hyperparam_presets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.algorithm_hyperparam_presets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: algorithm_hyperparam_presets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.algorithm_hyperparam_presets_id_seq OWNED BY public.algorithm_hyperparam_presets.id;


--
-- Name: algorithm_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.algorithm_models (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    category character varying(100) NOT NULL,
    subcategory character varying(100),
    description text,
    algorithm character varying(100),
    framework character varying(50),
    difficulty character varying(20),
    version character varying(20),
    status character varying(20),
    downloads integer,
    input_features jsonb,
    output_type character varying(50),
    metrics jsonb,
    use_case character varying(500),
    tags jsonb,
    entry_point character varying(200),
    params_schema text,
    estimated_training_time character varying(50),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: COLUMN algorithm_models.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.name IS '算法名称';


--
-- Name: COLUMN algorithm_models.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.category IS '分类';


--
-- Name: COLUMN algorithm_models.subcategory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.subcategory IS '子分类';


--
-- Name: COLUMN algorithm_models.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.description IS '算法描述';


--
-- Name: COLUMN algorithm_models.algorithm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.algorithm IS '算法类型';


--
-- Name: COLUMN algorithm_models.framework; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.framework IS '框架';


--
-- Name: COLUMN algorithm_models.difficulty; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.difficulty IS '难度';


--
-- Name: COLUMN algorithm_models.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.version IS '版本';


--
-- Name: COLUMN algorithm_models.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.status IS '状态: draft/published/unpublished';


--
-- Name: COLUMN algorithm_models.downloads; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.downloads IS '下载量';


--
-- Name: COLUMN algorithm_models.input_features; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.input_features IS '输入特征列表';


--
-- Name: COLUMN algorithm_models.output_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.output_type IS '输出类型';


--
-- Name: COLUMN algorithm_models.metrics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.metrics IS '性能指标';


--
-- Name: COLUMN algorithm_models.use_case; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.use_case IS '应用场景';


--
-- Name: COLUMN algorithm_models.tags; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.tags IS '标签';


--
-- Name: COLUMN algorithm_models.entry_point; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.entry_point IS '入口文件';


--
-- Name: COLUMN algorithm_models.params_schema; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.params_schema IS '参数schema JSON';


--
-- Name: COLUMN algorithm_models.estimated_training_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.algorithm_models.estimated_training_time IS '预估训练时间';


--
-- Name: algorithm_models_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.algorithm_models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: algorithm_models_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.algorithm_models_id_seq OWNED BY public.algorithm_models.id;


--
-- Name: annotation_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.annotation_tasks (
    id character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    dataset_id character varying(64) NOT NULL,
    annotation_type character varying(50) NOT NULL,
    label_group_id character varying(64),
    assignee_id character varying(64),
    status character varying(30) NOT NULL,
    total_items integer NOT NULL,
    completed_items integer NOT NULL,
    deadline timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: COLUMN annotation_tasks.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.id IS '任务ID(task_xxx)';


--
-- Name: COLUMN annotation_tasks.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.name IS '任务名称';


--
-- Name: COLUMN annotation_tasks.dataset_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.dataset_id IS '数据集ID';


--
-- Name: COLUMN annotation_tasks.annotation_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.annotation_type IS '标注类型';


--
-- Name: COLUMN annotation_tasks.label_group_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.label_group_id IS '关联标签组ID';


--
-- Name: COLUMN annotation_tasks.assignee_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.assignee_id IS '分配人ID';


--
-- Name: COLUMN annotation_tasks.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.status IS '状态: pending/in_progress/submitted/rejected/approved';


--
-- Name: COLUMN annotation_tasks.deadline; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotation_tasks.deadline IS '截止时间';


--
-- Name: annotations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.annotations (
    id character varying(64) NOT NULL,
    task_id character varying(64) NOT NULL,
    data_id character varying(128) NOT NULL,
    annotator_id character varying(64),
    labels json,
    metadata json,
    status character varying(30) NOT NULL,
    created_at timestamp without time zone,
    reviewed_at timestamp without time zone,
    reviewer_id character varying(64),
    review_comment text
);


--
-- Name: COLUMN annotations.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.id IS '标注ID(ann_xxx)';


--
-- Name: COLUMN annotations.data_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.data_id IS '数据项ID';


--
-- Name: COLUMN annotations.annotator_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.annotator_id IS '标注人ID';


--
-- Name: COLUMN annotations.labels; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.labels IS '标签列表';


--
-- Name: COLUMN annotations.metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.metadata IS '元数据';


--
-- Name: COLUMN annotations.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.status IS '状态: submitted/approved/rejected';


--
-- Name: COLUMN annotations.reviewed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.reviewed_at IS '审核时间';


--
-- Name: COLUMN annotations.reviewer_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.reviewer_id IS '审核人ID';


--
-- Name: COLUMN annotations.review_comment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.annotations.review_comment IS '审核意见';


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id integer NOT NULL,
    user_id integer NOT NULL,
    key_hash character varying(256) NOT NULL,
    name character varying(128) NOT NULL,
    is_active boolean,
    expires_at timestamp without time zone,
    last_used_at timestamp without time zone,
    created_at timestamp without time zone
);


--
-- Name: api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_keys_id_seq OWNED BY public.api_keys.id;


--
-- Name: api_usage_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_usage_logs (
    id integer NOT NULL,
    api_endpoint character varying(256) NOT NULL,
    caller_id character varying(128),
    organization_id integer,
    response_time_ms double precision,
    status_code integer,
    created_at timestamp without time zone
);


--
-- Name: api_usage_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_usage_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_usage_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_usage_logs_id_seq OWNED BY public.api_usage_logs.id;


--
-- Name: asset_classifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_classifications (
    asset_id integer NOT NULL,
    classification_id integer NOT NULL
);


--
-- Name: asset_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_metadata (
    id integer NOT NULL,
    asset_id integer NOT NULL,
    key character varying(100) NOT NULL,
    value text,
    source character varying(50),
    confidence double precision,
    created_at timestamp without time zone
);


--
-- Name: COLUMN asset_metadata.asset_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.asset_id IS '资产ID';


--
-- Name: COLUMN asset_metadata.key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.key IS '元数据键';


--
-- Name: COLUMN asset_metadata.value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.value IS '元数据值';


--
-- Name: COLUMN asset_metadata.source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.source IS '来源: manual, auto, system';


--
-- Name: COLUMN asset_metadata.confidence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.confidence IS '置信度 0-1';


--
-- Name: COLUMN asset_metadata.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_metadata.created_at IS '创建时间';


--
-- Name: asset_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_metadata_id_seq OWNED BY public.asset_metadata.id;


--
-- Name: asset_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_registry (
    id integer NOT NULL,
    slug text NOT NULL,
    title text NOT NULL,
    repo text NOT NULL,
    kind text NOT NULL,
    entry text NOT NULL,
    usage_example text,
    verify_evidence text,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: asset_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_registry_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_registry_id_seq OWNED BY public.asset_registry.id;


--
-- Name: asset_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_tags (
    asset_id integer NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id integer NOT NULL,
    action character varying(64),
    entity_type character varying(64),
    entity_id character varying(64),
    detail text,
    user_name character varying(64),
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_id_seq OWNED BY public.audit_logs.id;


--
-- Name: automl_selection_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.automl_selection_records (
    id integer NOT NULL,
    task_id character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    dataset_id integer,
    dataset_name character varying(255),
    target_column character varying(255) NOT NULL,
    task_type character varying(32) NOT NULL,
    optimization_metric character varying(32) NOT NULL,
    candidate_algorithms jsonb DEFAULT '[]'::jsonb NOT NULL,
    results jsonb DEFAULT '[]'::jsonb NOT NULL,
    best_algorithm character varying(128),
    best_score double precision,
    best_metrics jsonb,
    organization_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: automl_selection_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.automl_selection_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: automl_selection_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.automl_selection_records_id_seq OWNED BY public.automl_selection_records.id;


--
-- Name: billing_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.billing_records (
    id integer NOT NULL,
    org_id integer,
    component_id integer,
    pricing_model public.pricingmodel NOT NULL,
    unit_price double precision,
    quantity double precision,
    total_amount double precision,
    created_at timestamp without time zone
);


--
-- Name: billing_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.billing_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: billing_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.billing_records_id_seq OWNED BY public.billing_records.id;


--
-- Name: campaign_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_events (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    plan_id bigint NOT NULL,
    event_type text NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT campaign_events_event_type_check CHECK ((event_type = ANY (ARRAY['node_status_change'::text, 'milestone_status_change'::text, 'block_detected'::text, 'block_resolved'::text, 'spinning_detected'::text, 'escalation_created'::text, 'snapshot_taken'::text, 'plan_updated'::text, 'mission_dispatched'::text, 'mission_completed'::text, 'guerrilla_detected'::text])))
);


--
-- Name: campaign_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_events_id_seq OWNED BY public.campaign_events.id;


--
-- Name: campaign_milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_milestones (
    id bigint NOT NULL,
    plan_id bigint NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text,
    criteria jsonb DEFAULT '{}'::jsonb NOT NULL,
    dependencies integer[] DEFAULT '{}'::integer[],
    planned_start timestamp with time zone,
    planned_end timestamp with time zone,
    actual_start timestamp with time zone,
    actual_end timestamp with time zone,
    status text DEFAULT 'pending'::text NOT NULL,
    progress_pct integer DEFAULT 0 NOT NULL,
    consecutive_fails integer DEFAULT 0 NOT NULL,
    escalation_id bigint,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT campaign_milestones_progress_pct_check CHECK (((progress_pct >= 0) AND (progress_pct <= 100))),
    CONSTRAINT campaign_milestones_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'in-flight'::text, 'done'::text, 'blocked'::text, 'skipped'::text])))
);


--
-- Name: campaign_milestones_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_milestones_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_milestones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_milestones_id_seq OWNED BY public.campaign_milestones.id;


--
-- Name: campaign_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_nodes (
    id bigint NOT NULL,
    plan_id bigint NOT NULL,
    milestone_id bigint,
    mission_id bigint,
    name text NOT NULL,
    node_type text DEFAULT 'task'::text NOT NULL,
    dependencies integer[] DEFAULT '{}'::integer[],
    planned_start timestamp with time zone,
    planned_end timestamp with time zone,
    actual_start timestamp with time zone,
    actual_end timestamp with time zone,
    status text DEFAULT 'pending'::text NOT NULL,
    reproduction_verdict text,
    consecutive_fails integer DEFAULT 0 NOT NULL,
    block_reason text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT campaign_nodes_node_type_check CHECK ((node_type = ANY (ARRAY['task'::text, 'review'::text, 'gate'::text, 'checkpoint'::text]))),
    CONSTRAINT campaign_nodes_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'in-flight'::text, 'done'::text, 'blocked'::text, 'failed'::text, 'orphan'::text])))
);


--
-- Name: campaign_nodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_nodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_nodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_nodes_id_seq OWNED BY public.campaign_nodes.id;


--
-- Name: campaign_plan_dag_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_plan_dag_edges (
    id bigint NOT NULL,
    campaign_plan_id bigint NOT NULL,
    from_node text NOT NULL,
    to_node text NOT NULL
);


--
-- Name: campaign_plan_dag_edges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_plan_dag_edges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_plan_dag_edges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_plan_dag_edges_id_seq OWNED BY public.campaign_plan_dag_edges.id;


--
-- Name: campaign_plan_milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_plan_milestones (
    id bigint NOT NULL,
    campaign_plan_id bigint NOT NULL,
    milestone_id text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    gate_criteria jsonb DEFAULT '[]'::jsonb NOT NULL,
    target_date text,
    status text DEFAULT 'planned'::text NOT NULL
);


--
-- Name: campaign_plan_milestones_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_plan_milestones_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_plan_milestones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_plan_milestones_id_seq OWNED BY public.campaign_plan_milestones.id;


--
-- Name: campaign_plan_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_plan_nodes (
    id bigint NOT NULL,
    campaign_plan_id bigint NOT NULL,
    node_id text NOT NULL,
    source text NOT NULL,
    layer text NOT NULL,
    title text NOT NULL,
    goal text DEFAULT ''::text NOT NULL,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb NOT NULL,
    depends_on jsonb DEFAULT '[]'::jsonb NOT NULL,
    milestone text NOT NULL,
    budget_hours numeric DEFAULT 0 NOT NULL,
    risk text DEFAULT 'medium'::text NOT NULL,
    physics_domain text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'planned'::text NOT NULL
);


--
-- Name: campaign_plan_nodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_plan_nodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_plan_nodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_plan_nodes_id_seq OWNED BY public.campaign_plan_nodes.id;


--
-- Name: campaign_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_plans (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    source_mission_id text NOT NULL,
    schema_version text DEFAULT '2.0.0'::text NOT NULL,
    title text NOT NULL,
    created_utc timestamp with time zone DEFAULT now() NOT NULL,
    owner text,
    physics_scope jsonb DEFAULT '[]'::jsonb NOT NULL,
    coupling_framework_requirements jsonb DEFAULT '[]'::jsonb NOT NULL,
    validation_standards jsonb DEFAULT '[]'::jsonb NOT NULL,
    statistics jsonb DEFAULT '{}'::jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    campaign_id bigint,
    name text NOT NULL,
    description text DEFAULT ''::text,
    sir_master_ref text,
    plan_version text DEFAULT '1.0'::text NOT NULL,
    planned_start timestamp with time zone,
    planned_end timestamp with time zone,
    budget_planned jsonb DEFAULT '{"max_tokens": 0, "max_tool_calls": 0, "max_wall_seconds": 0}'::jsonb NOT NULL,
    budget_actual jsonb DEFAULT '{"max_tokens": 0, "max_tool_calls": 0, "max_wall_seconds": 0}'::jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_campaign_plans_status CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'paused'::text, 'completed'::text, 'archived'::text])))
);


--
-- Name: campaign_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_plans_id_seq OWNED BY public.campaign_plans.id;


--
-- Name: campaign_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_snapshots (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    plan_id bigint NOT NULL,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    total_nodes integer DEFAULT 0 NOT NULL,
    done_nodes integer DEFAULT 0 NOT NULL,
    in_flight_nodes integer DEFAULT 0 NOT NULL,
    blocked_nodes integer DEFAULT 0 NOT NULL,
    failed_nodes integer DEFAULT 0 NOT NULL,
    orphan_nodes integer DEFAULT 0 NOT NULL,
    milestone_progress_pct integer DEFAULT 0 NOT NULL,
    budget_planned jsonb DEFAULT '{}'::jsonb NOT NULL,
    budget_actual jsonb DEFAULT '{}'::jsonb NOT NULL,
    blocked_chains jsonb DEFAULT '[]'::jsonb NOT NULL,
    spinning_nodes jsonb DEFAULT '[]'::jsonb NOT NULL,
    guerrilla_ratio double precision DEFAULT 0.0 NOT NULL,
    snapshot_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: campaign_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.campaign_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: campaign_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.campaign_snapshots_id_seq OWNED BY public.campaign_snapshots.id;


--
-- Name: chronicle_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chronicle_snapshots (
    ts timestamp with time zone DEFAULT now(),
    payload jsonb
);


--
-- Name: cluster_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_jobs (
    id integer NOT NULL,
    repo text NOT NULL,
    ref text DEFAULT 'main'::text,
    mode text DEFAULT 'run'::text,
    cmd text NOT NULL,
    cmd_tpl text,
    shards integer,
    stage_dir text,
    timeout integer DEFAULT 3600,
    cores integer,
    mid bigint,
    submitter text,
    state text DEFAULT 'queued'::text,
    node text,
    attempts integer DEFAULT 0,
    rc integer,
    log_path text,
    note text,
    submitted_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);


--
-- Name: cluster_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_jobs_id_seq OWNED BY public.cluster_jobs.id;


--
-- Name: components; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.components (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    type character varying(64) NOT NULL,
    description text,
    input_schema json,
    output_schema json,
    parameters json,
    status public.componentstatus,
    workspace_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: components_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.components_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: components_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.components_id_seq OWNED BY public.components.id;


--
-- Name: coordinator_shadow_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.coordinator_shadow_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: coordinator_shadow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coordinator_shadow (
    id bigint DEFAULT nextval('public.coordinator_shadow_id_seq'::regclass),
    ts timestamp with time zone DEFAULT now(),
    model text,
    urgent boolean,
    verdict jsonb,
    pulse_digest jsonb,
    schema text,
    beat_id text,
    provenance jsonb,
    filings jsonb DEFAULT '[]'::jsonb,
    suppressed jsonb DEFAULT '[]'::jsonb,
    duration_ms integer,
    error text,
    mode text DEFAULT 'live'::text
);


--
-- Name: council_ledger_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.council_ledger_entries (
    seq bigint NOT NULL,
    schema character varying(64) NOT NULL,
    recorded_at character varying(40) NOT NULL,
    prev_hash character varying(64) NOT NULL,
    entry_hash character varying(64) NOT NULL,
    record json NOT NULL
);


--
-- Name: council_ledger_entries_seq_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.council_ledger_entries_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: council_ledger_entries_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.council_ledger_entries_seq_seq OWNED BY public.council_ledger_entries.seq;


--
-- Name: council_ledger_entries_test; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.council_ledger_entries_test (
    seq bigint NOT NULL,
    schema character varying(64) NOT NULL,
    recorded_at character varying(40) NOT NULL,
    prev_hash character varying(64) NOT NULL,
    entry_hash character varying(64) NOT NULL,
    record json NOT NULL
);


--
-- Name: council_ledger_entries_test_seq_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.council_ledger_entries_test_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: council_ledger_entries_test_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.council_ledger_entries_test_seq_seq OWNED BY public.council_ledger_entries_test.seq;


--
-- Name: credit_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_ledger (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    mission_id bigint NOT NULL,
    session_id bigint,
    entry_type character varying(30) NOT NULL,
    budget_used jsonb DEFAULT '{}'::jsonb NOT NULL,
    budget_limit jsonb DEFAULT '{}'::jsonb NOT NULL,
    wall_seconds_used integer,
    tool_calls_used integer,
    tokens_used bigint,
    fail_stage character varying(60),
    report_owed boolean DEFAULT false NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credit_ledger_entry_type_check CHECK (((entry_type)::text = ANY ((ARRAY['succeeded'::character varying, 'failed'::character varying, 'stopped'::character varying, 'revoked'::character varying, 'suspended'::character varying, 'resumed'::character varying, 'orphan'::character varying, 'mechanical_fix'::character varying])::text[])))
);


--
-- Name: credit_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.credit_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: credit_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.credit_ledger_id_seq OWNED BY public.credit_ledger.id;


--
-- Name: credit_settle_pending; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_settle_pending (
    evaluation_id text NOT NULL,
    grade text,
    status text DEFAULT 'pending_not_independent'::text NOT NULL,
    reason text NOT NULL,
    codes text DEFAULT '[]'::text NOT NULL,
    contributor text,
    reviewer text,
    reviewer_kind text,
    contributor_family text,
    reviewer_family text,
    detail text,
    mission_id bigint,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: credit_settle_pending_v; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.credit_settle_pending_v AS
 SELECT credit_settle_pending.reason,
    COALESCE(credit_settle_pending.grade, '?'::text) AS grade,
    (count(*))::integer AS n,
    min(credit_settle_pending.first_seen_at) AS first_seen_at,
    max(credit_settle_pending.updated_at) AS last_update
   FROM public.credit_settle_pending
  GROUP BY credit_settle_pending.reason, COALESCE(credit_settle_pending.grade, '?'::text);


--
-- Name: tensor_credit_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_credit_ledger (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    model character varying(64) NOT NULL,
    delta integer NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    mission_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: credit_tiers; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.credit_tiers AS
 SELECT tensor_credit_ledger.model AS seat,
    (sum(tensor_credit_ledger.delta))::integer AS total_delta,
        CASE
            WHEN (sum(tensor_credit_ledger.delta) >= 270) THEN 'L5'::text
            WHEN (sum(tensor_credit_ledger.delta) >= 90) THEN 'L4'::text
            WHEN (sum(tensor_credit_ledger.delta) >= 30) THEN 'L3'::text
            WHEN (sum(tensor_credit_ledger.delta) >= 10) THEN 'L2'::text
            ELSE 'L1'::text
        END AS tier,
    count(*) AS entries,
    max(tensor_credit_ledger.created_at) AS last_entry
   FROM public.tensor_credit_ledger
  GROUP BY tensor_credit_ledger.model;


--
-- Name: data_analysis_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_analysis_runs (
    id integer NOT NULL,
    organization_id integer,
    dataset_id integer NOT NULL,
    name character varying(200) NOT NULL,
    configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb NOT NULL,
    created_by character varying(128),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: data_analysis_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_analysis_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_analysis_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_analysis_runs_id_seq OWNED BY public.data_analysis_runs.id;


--
-- Name: data_asset_lineage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_asset_lineage (
    id integer NOT NULL,
    source_id integer NOT NULL,
    target_id integer NOT NULL,
    lineage_type character varying(50),
    transform_logic text,
    column_mapping json,
    created_at timestamp without time zone,
    created_by integer
);


--
-- Name: COLUMN data_asset_lineage.source_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.source_id IS '源资产ID';


--
-- Name: COLUMN data_asset_lineage.target_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.target_id IS '目标资产ID';


--
-- Name: COLUMN data_asset_lineage.lineage_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.lineage_type IS '关系类型: derives, transforms, joins, filters';


--
-- Name: COLUMN data_asset_lineage.transform_logic; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.transform_logic IS '转换逻辑描述';


--
-- Name: COLUMN data_asset_lineage.column_mapping; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.column_mapping IS '字段映射关系';


--
-- Name: COLUMN data_asset_lineage.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.created_at IS '创建时间';


--
-- Name: COLUMN data_asset_lineage.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_asset_lineage.created_by IS '创建者ID';


--
-- Name: data_asset_lineage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_asset_lineage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_asset_lineage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_asset_lineage_id_seq OWNED BY public.data_asset_lineage.id;


--
-- Name: data_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_assets (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    type character varying(50) NOT NULL,
    description text,
    owner character varying(100),
    tags json,
    sensitivity_level character varying(20),
    quality_score integer,
    schema_info json,
    storage_info json,
    source character varying(255),
    status character varying(20),
    version character varying(20),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    user_id integer
);


--
-- Name: COLUMN data_assets.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.name IS '资产名称';


--
-- Name: COLUMN data_assets.type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.type IS '资产类型: table, file, api, model, dataset, stream';


--
-- Name: COLUMN data_assets.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.description IS '资产描述';


--
-- Name: COLUMN data_assets.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.owner IS '资产所有者';


--
-- Name: COLUMN data_assets.tags; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.tags IS '标签列表';


--
-- Name: COLUMN data_assets.sensitivity_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.sensitivity_level IS '敏感级别: public, internal, confidential, restricted';


--
-- Name: COLUMN data_assets.quality_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.quality_score IS '数据质量得分 0-100';


--
-- Name: COLUMN data_assets.schema_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.schema_info IS '数据结构信息';


--
-- Name: COLUMN data_assets.storage_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.storage_info IS '存储信息: location, format, size';


--
-- Name: COLUMN data_assets.source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.source IS '数据来源';


--
-- Name: COLUMN data_assets.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.status IS '状态: active, archived, deprecated';


--
-- Name: COLUMN data_assets.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.version IS '版本号';


--
-- Name: COLUMN data_assets.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.created_at IS '创建时间';


--
-- Name: COLUMN data_assets.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.updated_at IS '更新时间';


--
-- Name: COLUMN data_assets.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_assets.user_id IS '创建者ID';


--
-- Name: data_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_assets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_assets_id_seq OWNED BY public.data_assets.id;


--
-- Name: data_catalog_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_catalog_stats (
    id integer NOT NULL,
    date timestamp without time zone NOT NULL,
    total_assets integer,
    assets_by_type json,
    assets_by_status json,
    assets_by_sensitivity json,
    total_tags integer,
    total_classifications integer,
    avg_quality_score double precision,
    created_at timestamp without time zone
);


--
-- Name: COLUMN data_catalog_stats.date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.date IS '统计日期';


--
-- Name: COLUMN data_catalog_stats.total_assets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.total_assets IS '总资产数';


--
-- Name: COLUMN data_catalog_stats.assets_by_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.assets_by_type IS '按类型统计';


--
-- Name: COLUMN data_catalog_stats.assets_by_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.assets_by_status IS '按状态统计';


--
-- Name: COLUMN data_catalog_stats.assets_by_sensitivity; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.assets_by_sensitivity IS '按敏感级别统计';


--
-- Name: COLUMN data_catalog_stats.total_tags; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.total_tags IS '总标签数';


--
-- Name: COLUMN data_catalog_stats.total_classifications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.total_classifications IS '总分类数';


--
-- Name: COLUMN data_catalog_stats.avg_quality_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.avg_quality_score IS '平均质量得分';


--
-- Name: COLUMN data_catalog_stats.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_catalog_stats.created_at IS '创建时间';


--
-- Name: data_catalog_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_catalog_stats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_catalog_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_catalog_stats_id_seq OWNED BY public.data_catalog_stats.id;


--
-- Name: data_classifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_classifications (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    color character varying(20),
    icon character varying(50),
    rules json,
    parent_id integer,
    sort_order integer,
    created_at timestamp without time zone
);


--
-- Name: COLUMN data_classifications.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.name IS '分类名称';


--
-- Name: COLUMN data_classifications.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.description IS '分类描述';


--
-- Name: COLUMN data_classifications.color; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.color IS '显示颜色';


--
-- Name: COLUMN data_classifications.icon; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.icon IS '图标名称';


--
-- Name: COLUMN data_classifications.rules; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.rules IS '分类规则';


--
-- Name: COLUMN data_classifications.parent_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.parent_id IS '父分类ID';


--
-- Name: COLUMN data_classifications.sort_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.sort_order IS '排序顺序';


--
-- Name: COLUMN data_classifications.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_classifications.created_at IS '创建时间';


--
-- Name: data_classifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_classifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_classifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_classifications_id_seq OWNED BY public.data_classifications.id;


--
-- Name: data_cleaning_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_cleaning_rules (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    source_mode character varying(20) DEFAULT 'guided'::character varying NOT NULL,
    definition jsonb NOT NULL,
    code_text text,
    file_name character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    organization_id integer,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_data_cleaning_rule_mode CHECK (((source_mode)::text = ANY ((ARRAY['guided'::character varying, 'code'::character varying])::text[])))
);


--
-- Name: data_cleaning_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_cleaning_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_cleaning_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_cleaning_rules_id_seq OWNED BY public.data_cleaning_rules.id;


--
-- Name: data_cleaning_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_cleaning_runs (
    id character varying(36) NOT NULL,
    rule_id integer,
    source_dataset_id integer,
    output_dataset_id integer,
    status character varying(20) DEFAULT 'running'::character varying NOT NULL,
    summary jsonb,
    error text,
    organization_id integer,
    created_by integer,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT ck_data_cleaning_run_status CHECK (((status)::text = ANY ((ARRAY['running'::character varying, 'success'::character varying, 'rejected'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: data_products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_products (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    category character varying(64),
    description text,
    format character varying(32),
    size_desc character varying(64),
    price double precision,
    status character varying(32),
    created_at timestamp without time zone
);


--
-- Name: data_products_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_products_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_products_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_products_id_seq OWNED BY public.data_products.id;


--
-- Name: data_security_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_security_policies (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    table_name character varying(100) NOT NULL,
    column_name character varying(100),
    security_level character varying(20),
    masking_rule_id integer,
    access_policy_id integer,
    is_encrypted integer,
    encryption_algorithm character varying(50),
    retention_days integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by integer
);


--
-- Name: COLUMN data_security_policies.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.name IS '策略名称';


--
-- Name: COLUMN data_security_policies.table_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.table_name IS '表名';


--
-- Name: COLUMN data_security_policies.column_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.column_name IS '列名(为空则表示整表)';


--
-- Name: COLUMN data_security_policies.security_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.security_level IS '安全级别: public/internal/confidential/secret';


--
-- Name: COLUMN data_security_policies.masking_rule_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.masking_rule_id IS '关联的脱敏规则';


--
-- Name: COLUMN data_security_policies.access_policy_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.access_policy_id IS '关联的访问策略';


--
-- Name: COLUMN data_security_policies.is_encrypted; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.is_encrypted IS '是否加密存储';


--
-- Name: COLUMN data_security_policies.encryption_algorithm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.encryption_algorithm IS '加密算法';


--
-- Name: COLUMN data_security_policies.retention_days; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_security_policies.retention_days IS '数据保留天数';


--
-- Name: data_security_policies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_security_policies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_security_policies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_security_policies_id_seq OWNED BY public.data_security_policies.id;


--
-- Name: data_standards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_standards (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    version character varying(20),
    description text,
    fields json,
    status character varying(20),
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by integer
);


--
-- Name: COLUMN data_standards.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.name IS '标准名称';


--
-- Name: COLUMN data_standards.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.version IS '版本号';


--
-- Name: COLUMN data_standards.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.description IS '标准描述';


--
-- Name: COLUMN data_standards.fields; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.fields IS '字段定义列表';


--
-- Name: COLUMN data_standards.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.status IS '状态: draft/active/archived';


--
-- Name: COLUMN data_standards.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.is_active IS '是否启用';


--
-- Name: COLUMN data_standards.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.created_at IS '创建时间';


--
-- Name: COLUMN data_standards.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.updated_at IS '更新时间';


--
-- Name: COLUMN data_standards.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_standards.created_by IS '创建者ID';


--
-- Name: data_standards_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_standards_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_standards_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_standards_id_seq OWNED BY public.data_standards.id;


--
-- Name: data_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_tags (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    color character varying(20),
    category character varying(50),
    description text,
    created_at timestamp without time zone
);


--
-- Name: COLUMN data_tags.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_tags.name IS '标签名称';


--
-- Name: COLUMN data_tags.color; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_tags.color IS '显示颜色';


--
-- Name: COLUMN data_tags.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_tags.category IS '标签类别: general, business, technical, security';


--
-- Name: COLUMN data_tags.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_tags.description IS '标签描述';


--
-- Name: COLUMN data_tags.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_tags.created_at IS '创建时间';


--
-- Name: data_tags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_tags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_tags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_tags_id_seq OWNED BY public.data_tags.id;


--
-- Name: data_validation_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_validation_results (
    id integer NOT NULL,
    standard_id integer NOT NULL,
    validation_type character varying(50),
    total_records integer,
    valid_records integer,
    invalid_records integer,
    validation_score integer,
    errors json,
    warnings json,
    validated_at timestamp without time zone,
    validated_by integer,
    data_source character varying(255)
);


--
-- Name: COLUMN data_validation_results.validation_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.validation_type IS '验证类型: schema/data_type/constraint/custom';


--
-- Name: COLUMN data_validation_results.total_records; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.total_records IS '总记录数';


--
-- Name: COLUMN data_validation_results.valid_records; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.valid_records IS '有效记录数';


--
-- Name: COLUMN data_validation_results.invalid_records; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.invalid_records IS '无效记录数';


--
-- Name: COLUMN data_validation_results.validation_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.validation_score IS '验证得分 0-100';


--
-- Name: COLUMN data_validation_results.errors; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.errors IS '错误详情';


--
-- Name: COLUMN data_validation_results.warnings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.warnings IS '警告详情';


--
-- Name: COLUMN data_validation_results.validated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.validated_at IS '验证时间';


--
-- Name: COLUMN data_validation_results.validated_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.validated_by IS '验证者ID';


--
-- Name: COLUMN data_validation_results.data_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.data_validation_results.data_source IS '数据来源';


--
-- Name: data_validation_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_validation_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_validation_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_validation_results_id_seq OWNED BY public.data_validation_results.id;


--
-- Name: dataset_governance_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_governance_reports (
    id character varying(36) NOT NULL,
    dataset_id integer,
    profile character varying(32) NOT NULL,
    quality_rule_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(20) NOT NULL,
    score double precision DEFAULT 0 NOT NULL,
    summary jsonb NOT NULL,
    organization_id integer,
    created_by integer,
    checked_at timestamp with time zone DEFAULT now() NOT NULL,
    source_dataset_id integer,
    output_dataset_id integer,
    cleaning_run_id character varying(36),
    CONSTRAINT ck_dataset_governance_report_status CHECK (((status)::text = ANY ((ARRAY['passed'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: datasets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datasets (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    file_path character varying(512),
    size_bytes integer,
    row_count integer,
    project_id integer,
    stage character varying(32),
    columns_info json,
    quality_score double precision,
    governed_from integer,
    data_blob bytea,
    organization_id integer,
    created_at timestamp without time zone
);


--
-- Name: datasets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.datasets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: datasets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.datasets_id_seq OWNED BY public.datasets.id;


--
-- Name: deployments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deployments (
    id integer NOT NULL,
    model_id integer NOT NULL,
    model_version_id integer,
    name character varying(255),
    version character varying(64),
    status character varying(32) DEFAULT 'running'::character varying,
    config jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: deployments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.deployments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: deployments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.deployments_id_seq OWNED BY public.deployments.id;


--
-- Name: devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devices (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    device_type character varying(64) NOT NULL,
    protocol character varying(32),
    location character varying(256),
    status character varying(32),
    project_id integer,
    last_seen_at timestamp without time zone,
    created_at timestamp without time zone
);


--
-- Name: devices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.devices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: devices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.devices_id_seq OWNED BY public.devices.id;


--
-- Name: eval_cohorts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_cohorts (
    cohort_key text NOT NULL,
    week_start text NOT NULL,
    week_end text NOT NULL,
    repo text,
    missions_total integer DEFAULT 0 NOT NULL,
    missions_decided integer DEFAULT 0 NOT NULL,
    missions_pending integer DEFAULT 0 NOT NULL,
    missions_unrecorded integer DEFAULT 0 NOT NULL,
    coverage double precision,
    p90_latency_hours double precision,
    backlog_rows integer DEFAULT 0 NOT NULL,
    backlog_age_max_hours double precision,
    frozen boolean DEFAULT false NOT NULL,
    frozen_at text,
    revision integer DEFAULT 1 NOT NULL,
    inputs_fingerprint text NOT NULL,
    computed_at text NOT NULL,
    payload text DEFAULT '{}'::text NOT NULL
);


--
-- Name: eval_drain_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_drain_runs (
    run_id text NOT NULL,
    started_at text NOT NULL,
    finished_at text,
    trigger_kind text NOT NULL,
    threshold_rows integer,
    pending_before integer,
    pending_after integer,
    drained integer,
    undecidable integer,
    rejected integer,
    drain_share real,
    elapsed_seconds real,
    target_share real,
    window_hours real,
    met boolean,
    dry_run boolean,
    detail text
);


--
-- Name: eval_inflight_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_inflight_events (
    event_id text NOT NULL,
    mission_id bigint NOT NULL,
    trigger_status text NOT NULL,
    event_at text NOT NULL,
    finished_at text,
    decided_at text,
    outcome text NOT NULL,
    evaluation_status text,
    grade text,
    e_level text,
    latency_seconds real,
    repo text,
    detail text
);


--
-- Name: eval_redteam_rechecks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_redteam_rechecks (
    recheck_id text NOT NULL,
    run_id text NOT NULL,
    evaluation_id text NOT NULL,
    artifact_id text,
    mission_id bigint,
    repo text,
    stratum text NOT NULL,
    blind_order integer NOT NULL,
    declared_grade text,
    declared_e text,
    recomputed_grade text,
    recomputed_e text,
    verdict text NOT NULL,
    action text NOT NULL,
    mismatch_codes text DEFAULT '[]'::text NOT NULL,
    numbers_declared text DEFAULT '{}'::text NOT NULL,
    numbers_recomputed text DEFAULT '{}'::text NOT NULL,
    blind_payload text DEFAULT '{}'::text NOT NULL,
    reviewer text NOT NULL,
    applied integer DEFAULT 0 NOT NULL,
    created_at text NOT NULL
);


--
-- Name: evaluation_guidance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evaluation_guidance (
    guidance_id bigint NOT NULL,
    wave_id text NOT NULL,
    subject_kind text NOT NULL,
    subject text NOT NULL,
    tier text NOT NULL,
    budget_multiplier numeric(6,3) DEFAULT 1.000 NOT NULL,
    admission text DEFAULT 'open'::text NOT NULL,
    priority_delta integer DEFAULT 0 NOT NULL,
    rows_counted integer DEFAULT 0 NOT NULL,
    evidence text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evaluation_guidance_guidance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evaluation_guidance_guidance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evaluation_guidance_guidance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evaluation_guidance_guidance_id_seq OWNED BY public.evaluation_guidance.guidance_id;


--
-- Name: evaluation_waves; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evaluation_waves (
    wave_id text NOT NULL,
    window_hours integer NOT NULL,
    window_start timestamp with time zone,
    window_end timestamp with time zone,
    missions_in_window integer DEFAULT 0 NOT NULL,
    batches integer DEFAULT 0 NOT NULL,
    batch_size integer DEFAULT 0 NOT NULL,
    seats text DEFAULT '[]'::text NOT NULL,
    status text DEFAULT 'planned'::text NOT NULL,
    metrics text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: execution_lineage_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.execution_lineage_records (
    id character varying(36) NOT NULL,
    execution_id integer NOT NULL,
    config_hash character varying(64) NOT NULL,
    env_hash character varying(64) NOT NULL,
    data_hash character varying(64) NOT NULL,
    param_hash character varying(64) NOT NULL,
    scenario_key character varying(120),
    stage character varying(10),
    status character varying(20),
    extra_metadata json,
    created_at timestamp without time zone
);


--
-- Name: COLUMN execution_lineage_records.execution_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.execution_id IS '关联 tensor_executions.id';


--
-- Name: COLUMN execution_lineage_records.config_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.config_hash IS '配置指纹 SHA-256';


--
-- Name: COLUMN execution_lineage_records.env_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.env_hash IS '环境指纹 SHA-256';


--
-- Name: COLUMN execution_lineage_records.data_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.data_hash IS '数据指纹 SHA-256';


--
-- Name: COLUMN execution_lineage_records.param_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.param_hash IS '参数指纹 SHA-256';


--
-- Name: COLUMN execution_lineage_records.scenario_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.scenario_key IS '场景标识';


--
-- Name: COLUMN execution_lineage_records.stage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.stage IS '执行阶段';


--
-- Name: COLUMN execution_lineage_records.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.status IS '执行终态';


--
-- Name: COLUMN execution_lineage_records.extra_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.extra_metadata IS '扩展元数据';


--
-- Name: COLUMN execution_lineage_records.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.execution_lineage_records.created_at IS '创建时间';


--
-- Name: exploration_budget; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exploration_budget (
    entry_id text NOT NULL,
    cycle_id text NOT NULL,
    worker text NOT NULL,
    tier text,
    track text NOT NULL,
    entry_type text NOT NULL,
    allocated double precision DEFAULT 0 NOT NULL,
    consumed double precision DEFAULT 0 NOT NULL,
    refunded double precision DEFAULT 0 NOT NULL,
    remaining double precision DEFAULT 0 NOT NULL,
    multiplier double precision DEFAULT 1 NOT NULL,
    mission_key text,
    outcome text,
    exempt boolean DEFAULT false NOT NULL,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT exploration_budget_entry_type_check CHECK ((entry_type = ANY (ARRAY['account'::text, 'settlement'::text, 's_n'::text])))
);


--
-- Name: factory_agent_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_agent_identities (
    agent_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    owner_user_id bigint NOT NULL,
    worker_id text NOT NULL,
    role text NOT NULL,
    goal text DEFAULT ''::text NOT NULL,
    goal_revision integer DEFAULT 1 NOT NULL,
    constraints jsonb DEFAULT '[]'::jsonb NOT NULL,
    constraint_revision integer DEFAULT 1 NOT NULL,
    policy_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    legacy_session_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_agent_identities_constraint_revision_check CHECK ((constraint_revision >= 1)),
    CONSTRAINT factory_agent_identities_goal_revision_check CHECK ((goal_revision >= 1))
);


--
-- Name: factory_community_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_community_positions (
    topic_id bigint NOT NULL,
    agent_session_id uuid NOT NULL,
    stance text NOT NULL,
    reason text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_community_positions_reason_check CHECK (((length(reason) >= 1) AND (length(reason) <= 2000))),
    CONSTRAINT factory_community_positions_stance_check CHECK ((stance = ANY (ARRAY['interested'::text, 'support'::text, 'oppose'::text, 'abstain'::text])))
);


--
-- Name: factory_community_topics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_community_topics (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    author_user_id bigint NOT NULL,
    author_session_id uuid,
    title text NOT NULL,
    category text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_community_topics_category_check CHECK ((category = ANY (ARRAY['discussion'::text, 'proposal'::text, 'knowledge'::text, 'help'::text, 'retrospective'::text, 'charter'::text]))),
    CONSTRAINT factory_community_topics_title_check CHECK (((length(title) >= 1) AND (length(title) <= 200)))
);


--
-- Name: factory_community_topics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.factory_community_topics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: factory_community_topics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.factory_community_topics_id_seq OWNED BY public.factory_community_topics.id;


--
-- Name: factory_discussion_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_discussion_messages (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    author_user_id bigint NOT NULL,
    author_session_id uuid,
    participant_label text NOT NULL,
    recipient_session_id uuid,
    reply_to bigint,
    kind text NOT NULL,
    body text NOT NULL,
    evidence_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    client_key uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    topic_id bigint,
    CONSTRAINT factory_discussion_messages_body_check CHECK (((length(body) >= 1) AND (length(body) <= 6000))),
    CONSTRAINT factory_discussion_messages_kind_check CHECK ((kind = ANY (ARRAY['question'::text, 'proposal'::text, 'critique'::text, 'evidence'::text, 'reply'::text, 'reflection'::text])))
);


--
-- Name: factory_discussion_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.factory_discussion_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: factory_discussion_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.factory_discussion_messages_id_seq OWNED BY public.factory_discussion_messages.id;


--
-- Name: factory_evidence_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_evidence_records (
    evidence_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    agent_id uuid,
    session_id uuid,
    task_id bigint,
    category text NOT NULL,
    code_sha text,
    environment jsonb DEFAULT '{}'::jsonb NOT NULL,
    validator jsonb DEFAULT '{}'::jsonb NOT NULL,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    artifact_hashes jsonb DEFAULT '[]'::jsonb NOT NULL,
    evidence_level text DEFAULT 'L0'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_evidence_records_category_check CHECK ((category = ANY (ARRAY['unit_test'::text, 'integration_test'::text, 'deployment'::text, 'scientific'::text]))),
    CONSTRAINT factory_evidence_records_code_sha_check CHECK ((code_sha ~ '^[0-9a-f]{7,40}$'::text)),
    CONSTRAINT factory_evidence_records_evidence_level_check CHECK ((evidence_level = ANY (ARRAY['L0'::text, 'L1'::text, 'L2'::text, 'L3'::text, 'L4'::text])))
);


--
-- Name: factory_harness_incidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_harness_incidents (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    campaign_id bigint NOT NULL,
    mission_id bigint,
    kind text NOT NULL,
    detail text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_harness_incidents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.factory_harness_incidents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: factory_harness_incidents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.factory_harness_incidents_id_seq OWNED BY public.factory_harness_incidents.id;


--
-- Name: factory_learning_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_learning_candidates (
    candidate_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    agent_id uuid NOT NULL,
    source_event_ids bigint[] NOT NULL,
    proposed_generalization text NOT NULL,
    scope text DEFAULT 'organization'::text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    evaluation jsonb DEFAULT '{}'::jsonb NOT NULL,
    promotion_status text DEFAULT 'proposed'::text NOT NULL,
    decided_by bigint,
    decided_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    CONSTRAINT factory_learning_candidates_check CHECK (((promotion_status <> 'approved'::text) OR ((evaluation ->> 'passed'::text))::boolean)),
    CONSTRAINT factory_learning_candidates_check1 CHECK (((promotion_status <> 'approved'::text) OR (scope <> 'shared'::text) OR ((evidence ->> 'validator'::text) IS NOT NULL))),
    CONSTRAINT factory_learning_candidates_promotion_status_check CHECK ((promotion_status = ANY (ARRAY['proposed'::text, 'approved'::text, 'rejected'::text, 'superseded'::text]))),
    CONSTRAINT factory_learning_candidates_proposed_generalization_check CHECK (((length(proposed_generalization) >= 1) AND (length(proposed_generalization) <= 4000))),
    CONSTRAINT factory_learning_candidates_scope_check CHECK ((scope = ANY (ARRAY['agent'::text, 'organization'::text, 'shared'::text]))),
    CONSTRAINT factory_learning_candidates_source_event_ids_check CHECK ((array_length(source_event_ids, 1) >= 1))
);


--
-- Name: factory_memory_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_memory_events (
    event_id bigint NOT NULL,
    organization_id bigint NOT NULL,
    agent_id uuid NOT NULL,
    session_id uuid,
    task_id bigint,
    event_type text NOT NULL,
    source text NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    sensitivity text DEFAULT 'private'::text NOT NULL,
    verification_status text DEFAULT 'observed'::text NOT NULL,
    validator jsonb,
    superseded_by bigint,
    idempotency_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_memory_events_check CHECK (((sensitivity <> 'shared'::text) OR (verification_status = 'validated'::text))),
    CONSTRAINT factory_memory_events_check1 CHECK (((verification_status <> 'validated'::text) OR (validator IS NOT NULL))),
    CONSTRAINT factory_memory_events_check2 CHECK (((event_type <> 'fact'::text) OR (verification_status = ANY (ARRAY['validated'::text, 'superseded'::text, 'rejected'::text])))),
    CONSTRAINT factory_memory_events_event_type_check CHECK ((event_type = ANY (ARRAY['observation'::text, 'decision'::text, 'outcome'::text, 'feedback'::text, 'lesson'::text, 'fact'::text]))),
    CONSTRAINT factory_memory_events_idempotency_key_check CHECK (((length(idempotency_key) >= 1) AND (length(idempotency_key) <= 200))),
    CONSTRAINT factory_memory_events_sensitivity_check CHECK ((sensitivity = ANY (ARRAY['private'::text, 'organization'::text, 'shared'::text]))),
    CONSTRAINT factory_memory_events_source_check CHECK (((length(source) >= 1) AND (length(source) <= 200))),
    CONSTRAINT factory_memory_events_verification_status_check CHECK ((verification_status = ANY (ARRAY['observed'::text, 'validated'::text, 'rejected'::text, 'superseded'::text])))
);


--
-- Name: factory_memory_events_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.factory_memory_events_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: factory_memory_events_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.factory_memory_events_event_id_seq OWNED BY public.factory_memory_events.event_id;


--
-- Name: factory_resident_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_resident_workers (
    agent_session_id uuid NOT NULL,
    user_id bigint NOT NULL,
    organization_id bigint NOT NULL,
    worker_id text NOT NULL,
    role text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    state text DEFAULT 'offline'::text NOT NULL,
    model text NOT NULL,
    model_history jsonb DEFAULT '[]'::jsonb NOT NULL,
    memory jsonb DEFAULT '[]'::jsonb NOT NULL,
    heartbeat_at timestamp with time zone,
    current_mission_id bigint,
    last_error text,
    started_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_soul_binding_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_binding_history (
    session_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    binding_id uuid NOT NULL,
    switched_by bigint,
    switched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_soul_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_checkpoints (
    session_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    version integer NOT NULL,
    checksum text NOT NULL,
    state jsonb NOT NULL,
    state_version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_soul_checkpoints_checksum_check CHECK ((length(checksum) = 64)),
    CONSTRAINT factory_soul_checkpoints_state_version_check CHECK ((state_version >= 1)),
    CONSTRAINT factory_soul_checkpoints_version_check CHECK ((version >= 1))
);


--
-- Name: factory_soul_legacy_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_legacy_sessions (
    agent_session_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_soul_model_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_model_bindings (
    binding_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    adapter jsonb DEFAULT '{}'::jsonb NOT NULL,
    capability_limits jsonb DEFAULT '{}'::jsonb NOT NULL,
    version integer NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_soul_model_bindings_model_check CHECK (((length(model) >= 1) AND (length(model) <= 120))),
    CONSTRAINT factory_soul_model_bindings_name_check CHECK (((length(name) >= 1) AND (length(name) <= 80))),
    CONSTRAINT factory_soul_model_bindings_provider_check CHECK (((length(provider) >= 1) AND (length(provider) <= 80))),
    CONSTRAINT factory_soul_model_bindings_version_check CHECK ((version >= 1))
);


--
-- Name: factory_soul_schema_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_schema_versions (
    version text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT factory_soul_schema_versions_checksum_check CHECK ((length(checksum) = 64))
);


--
-- Name: factory_soul_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_soul_sessions (
    session_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    agent_id uuid NOT NULL,
    episode integer NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    current_task_id bigint,
    task_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    active_plan jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_binding_id uuid,
    checkpoint_version integer DEFAULT 0 NOT NULL,
    last_restored_version integer DEFAULT 0 NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT factory_soul_sessions_checkpoint_version_check CHECK ((checkpoint_version >= 0)),
    CONSTRAINT factory_soul_sessions_episode_check CHECK ((episode >= 1)),
    CONSTRAINT factory_soul_sessions_last_restored_version_check CHECK ((last_restored_version >= 0)),
    CONSTRAINT factory_soul_sessions_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text])))
);


--
-- Name: factory_strategy_campaigns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_strategy_campaigns (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    agent_session_id uuid NOT NULL,
    name text NOT NULL,
    objective text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    phase text DEFAULT 'research'::text NOT NULL,
    round integer DEFAULT 1 NOT NULL,
    max_rounds integer DEFAULT 4 NOT NULL,
    next_review_at timestamp with time zone DEFAULT now() NOT NULL,
    last_mission_id bigint,
    plan jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    feedback jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT factory_strategy_campaigns_max_rounds_check CHECK (((max_rounds >= 1) AND (max_rounds <= 4)))
);


--
-- Name: factory_strategy_campaigns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.factory_strategy_campaigns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: factory_strategy_campaigns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.factory_strategy_campaigns_id_seq OWNED BY public.factory_strategy_campaigns.id;


--
-- Name: failure_postmortems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.failure_postmortems (
    postmortem_id text NOT NULL,
    mission_id bigint NOT NULL,
    organization_id bigint,
    repo text,
    hypothesis_id text,
    initial_hypothesis text NOT NULL,
    failed_step text NOT NULL,
    failure_stage text NOT NULL,
    legacy_stage text,
    error_class text NOT NULL,
    invalidated_paths text[] DEFAULT '{}'::text[] NOT NULL,
    avoidance_rules text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    harvested_by text NOT NULL,
    challenge jsonb DEFAULT '{}'::jsonb NOT NULL,
    confirmed_by text,
    confirm_role text,
    confirmed_at timestamp with time zone,
    rejected_reason text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT failure_postmortems_class_check CHECK ((error_class = ANY (ARRAY['theory_error'::text, 'implementation_error'::text, 'experiment_design_error'::text, 'resource_limit'::text, 'endpoint_disease'::text]))),
    CONSTRAINT failure_postmortems_confirm_consistent CHECK (((confirmed_by IS NULL) OR (confirm_role IS NOT NULL))),
    CONSTRAINT failure_postmortems_confirmed_fields CHECK (((status <> 'confirmed'::text) OR ((confirmed_by IS NOT NULL) AND (confirmed_at IS NOT NULL)))),
    CONSTRAINT failure_postmortems_no_self_review CHECK (((confirmed_by IS NULL) OR (confirmed_by <> harvested_by))),
    CONSTRAINT failure_postmortems_stage_check CHECK ((failure_stage = ANY (ARRAY['intake'::text, 'plan'::text, 'setup'::text, 'implement'::text, 'verify'::text, 'deliver'::text, 'accept'::text]))),
    CONSTRAINT failure_postmortems_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'disputed'::text, 'confirmed'::text, 'rejected'::text])))
);


--
-- Name: feature_engineering_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feature_engineering_rules (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    source_mode character varying(20) DEFAULT 'guided'::character varying NOT NULL,
    definition jsonb NOT NULL,
    code_text text,
    file_name character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    organization_id integer,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_feature_engineering_rule_mode CHECK (((source_mode)::text = ANY ((ARRAY['guided'::character varying, 'code'::character varying])::text[])))
);


--
-- Name: feature_engineering_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feature_engineering_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feature_engineering_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feature_engineering_rules_id_seq OWNED BY public.feature_engineering_rules.id;


--
-- Name: feature_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feature_groups (
    id integer NOT NULL,
    group_id character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    entity_name character varying(255),
    features jsonb DEFAULT '[]'::jsonb NOT NULL,
    online_enabled boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: feature_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feature_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feature_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feature_groups_id_seq OWNED BY public.feature_groups.id;


--
-- Name: feature_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feature_ledger (
    id integer NOT NULL,
    slug text NOT NULL,
    title text NOT NULL,
    domain text NOT NULL,
    source text NOT NULL,
    acceptance text NOT NULL,
    ledger_group text,
    status text DEFAULT 'planned'::text NOT NULL,
    evidence text,
    mission_id bigint,
    created_at timestamp with time zone DEFAULT now(),
    done_at timestamp with time zone
);


--
-- Name: feature_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feature_ledger_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feature_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feature_ledger_id_seq OWNED BY public.feature_ledger.id;


--
-- Name: feature_values; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feature_values (
    id bigint NOT NULL,
    group_id character varying(64) NOT NULL,
    entity_key character varying(256) NOT NULL,
    values_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    event_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: feature_values_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feature_values_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feature_values_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feature_values_id_seq OWNED BY public.feature_values.id;


--
-- Name: gate_release_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gate_release_audit (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    node_id bigint NOT NULL,
    plan_id bigint,
    action text NOT NULL,
    from_status text,
    to_status text,
    reason text,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor text NOT NULL,
    run_id text,
    rolled_back boolean DEFAULT false NOT NULL,
    rolled_back_at timestamp with time zone,
    rollback_of bigint
);


--
-- Name: gate_release_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.gate_release_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: gate_release_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.gate_release_audit_id_seq OWNED BY public.gate_release_audit.id;


--
-- Name: governance_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.governance_approvals (
    id integer NOT NULL,
    request_id character varying(64) NOT NULL,
    model_id integer,
    action character varying(64),
    requester character varying(128),
    approver character varying(128),
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    comments text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


--
-- Name: governance_approvals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.governance_approvals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: governance_approvals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.governance_approvals_id_seq OWNED BY public.governance_approvals.id;


--
-- Name: governance_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.governance_audit (
    id integer NOT NULL,
    model_id integer,
    action character varying(128),
    user_name character varying(128),
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: governance_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.governance_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: governance_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.governance_audit_id_seq OWNED BY public.governance_audit.id;


--
-- Name: guidance_cycle; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guidance_cycle (
    cycle_id text NOT NULL,
    stats_digest text NOT NULL,
    total_pool integer NOT NULL,
    yield_bar double precision,
    exploration_share double precision,
    payload text,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: guidance_outcome; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guidance_outcome (
    mission_key text NOT NULL,
    worker text NOT NULL,
    tier text NOT NULL,
    outcome text NOT NULL,
    exempt boolean DEFAULT false NOT NULL,
    cycle_id text,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT guidance_outcome_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'mechanical_fail'::text, 'honest_fail'::text, 'stopped'::text]))),
    CONSTRAINT guidance_outcome_tier_check CHECK ((tier = ANY (ARRAY['S1'::text, 'S2'::text, 'S3'::text, 'S4'::text, 'S5'::text])))
);


--
-- Name: hyperopt_studies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hyperopt_studies (
    id integer NOT NULL,
    study_id character varying(64) NOT NULL,
    name character varying(255),
    dataset_id integer,
    objective_metric character varying(64),
    direction character varying(32),
    search_algorithm character varying(64),
    n_trials integer,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    best_value double precision,
    best_params jsonb,
    best_model_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


--
-- Name: hyperopt_studies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hyperopt_studies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hyperopt_studies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hyperopt_studies_id_seq OWNED BY public.hyperopt_studies.id;


--
-- Name: hyperopt_trials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hyperopt_trials (
    id integer NOT NULL,
    study_id character varying(64) NOT NULL,
    trial_number integer NOT NULL,
    parameters jsonb,
    objective_value double precision,
    metrics jsonb,
    job_id character varying(64),
    status character varying(32),
    started_at timestamp with time zone,
    completed_at timestamp with time zone
);


--
-- Name: hyperopt_trials_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hyperopt_trials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hyperopt_trials_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hyperopt_trials_id_seq OWNED BY public.hyperopt_trials.id;


--
-- Name: hypothesis_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hypothesis_registry (
    hypothesis_id text NOT NULL,
    mission_id bigint,
    statement text NOT NULL,
    source text DEFAULT 'mission_goal'::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hypothesis_registry_status_check CHECK ((status = ANY (ARRAY['open'::text, 'refuted'::text, 'supported'::text, 'inconclusive'::text])))
);


--
-- Name: innovation_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.innovation_proposals (
    proposal_id text NOT NULL,
    title text NOT NULL,
    hypothesis text,
    status text DEFAULT 'submitted'::text NOT NULL,
    review_entry_id text,
    evidence text,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT innovation_proposals_status_check CHECK ((status = ANY (ARRAY['submitted'::text, 'under_review'::text, 'approved'::text, 'rejected'::text, 'parked'::text])))
);


--
-- Name: integrity_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integrity_events (
    id integer NOT NULL,
    mission_id integer NOT NULL,
    event_type character varying(32) NOT NULL,
    self_reported jsonb,
    metered jsonb,
    deviation_pct double precision,
    severity character varying(16),
    resolved boolean,
    notes text,
    created_at timestamp without time zone
);


--
-- Name: integrity_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.integrity_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: integrity_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.integrity_events_id_seq OWNED BY public.integrity_events.id;


--
-- Name: intelligent_data_analysis_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.intelligent_data_analysis_reports (
    id integer NOT NULL,
    organization_id integer,
    dataset_id integer,
    source_name character varying(255) NOT NULL,
    profile jsonb NOT NULL,
    narrative text NOT NULL,
    llm_provider character varying(64),
    sample_shared boolean DEFAULT false NOT NULL,
    created_by character varying(128),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: intelligent_data_analysis_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.intelligent_data_analysis_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: intelligent_data_analysis_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.intelligent_data_analysis_reports_id_seq OWNED BY public.intelligent_data_analysis_reports.id;


--
-- Name: knowledge_base; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_base (
    id integer NOT NULL,
    kb_key text NOT NULL,
    ktype text NOT NULL,
    domain text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    evidence text,
    source text DEFAULT 'platform-ops'::text,
    status text DEFAULT 'active'::text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: knowledge_base_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.knowledge_base_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: knowledge_base_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.knowledge_base_id_seq OWNED BY public.knowledge_base.id;


--
-- Name: knowledge_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_rules (
    id integer NOT NULL,
    domain character varying(128) NOT NULL,
    rule character varying(256) NOT NULL,
    cause character varying(512),
    action character varying(512),
    confidence double precision,
    created_at timestamp without time zone
);


--
-- Name: knowledge_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.knowledge_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: knowledge_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.knowledge_rules_id_seq OWNED BY public.knowledge_rules.id;


--
-- Name: label_group_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.label_group_items (
    id integer NOT NULL,
    group_id character varying(64) NOT NULL,
    label_id character varying(64) NOT NULL,
    "position" integer
);


--
-- Name: COLUMN label_group_items."position"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.label_group_items."position" IS '组内顺序';


--
-- Name: label_group_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.label_group_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: label_group_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.label_group_items_id_seq OWNED BY public.label_group_items.id;


--
-- Name: label_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.label_groups (
    id character varying(64) NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    created_at timestamp without time zone
);


--
-- Name: COLUMN label_groups.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.label_groups.id IS '标签组ID(group_xxx)';


--
-- Name: COLUMN label_groups.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.label_groups.name IS '组名称';


--
-- Name: COLUMN label_groups.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.label_groups.description IS '组描述';


--
-- Name: labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.labels (
    id character varying(64) NOT NULL,
    name character varying(100) NOT NULL,
    category character varying(100) NOT NULL,
    color character varying(20),
    description text,
    status character varying(20) NOT NULL,
    usage_count integer NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: COLUMN labels.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.id IS '标签ID(label_xxx)';


--
-- Name: COLUMN labels.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.name IS '标签名称';


--
-- Name: COLUMN labels.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.category IS '标签分类';


--
-- Name: COLUMN labels.color; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.color IS '颜色';


--
-- Name: COLUMN labels.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.description IS '描述';


--
-- Name: COLUMN labels.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.status IS '状态: draft/active/deprecated';


--
-- Name: COLUMN labels.usage_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.labels.usage_count IS '使用计数';


--
-- Name: ledger_gate_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_gate_meta (
    k text NOT NULL,
    v timestamp with time zone
);


--
-- Name: lineage_change_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lineage_change_logs (
    id character varying(36) NOT NULL,
    node_id character varying(36),
    edge_id character varying(36),
    change_type character varying(50) NOT NULL,
    change_detail json,
    old_value json,
    new_value json,
    created_at timestamp without time zone,
    created_by character varying(100),
    organization_id integer
);


--
-- Name: TABLE lineage_change_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lineage_change_logs IS '数据血缘变更日志表';


--
-- Name: COLUMN lineage_change_logs.node_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.node_id IS '关联节点ID';


--
-- Name: COLUMN lineage_change_logs.edge_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.edge_id IS '关联边ID';


--
-- Name: COLUMN lineage_change_logs.change_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.change_type IS '变更类型: create/update/delete';


--
-- Name: COLUMN lineage_change_logs.change_detail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.change_detail IS '变更详情';


--
-- Name: COLUMN lineage_change_logs.old_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.old_value IS '旧值';


--
-- Name: COLUMN lineage_change_logs.new_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.new_value IS '新值';


--
-- Name: COLUMN lineage_change_logs.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.created_at IS '创建时间';


--
-- Name: COLUMN lineage_change_logs.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.created_by IS '操作人';


--
-- Name: COLUMN lineage_change_logs.organization_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_change_logs.organization_id IS '所属组织';


--
-- Name: lineage_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lineage_edges (
    id character varying(36) NOT NULL,
    source_id character varying(36) NOT NULL,
    target_id character varying(36) NOT NULL,
    edge_type character varying(50),
    transformation text,
    transformation_type character varying(50),
    column_mapping json,
    impact_weight integer,
    description text,
    extra_metadata json,
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by character varying(100),
    organization_id integer
);


--
-- Name: TABLE lineage_edges; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lineage_edges IS '数据血缘关系表';


--
-- Name: COLUMN lineage_edges.source_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.source_id IS '源节点ID';


--
-- Name: COLUMN lineage_edges.target_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.target_id IS '目标节点ID';


--
-- Name: COLUMN lineage_edges.edge_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.edge_type IS '关系类型: derived/transform/aggregate/join/copy';


--
-- Name: COLUMN lineage_edges.transformation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.transformation IS '转换逻辑';


--
-- Name: COLUMN lineage_edges.transformation_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.transformation_type IS '转换类型';


--
-- Name: COLUMN lineage_edges.column_mapping; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.column_mapping IS '列映射关系';


--
-- Name: COLUMN lineage_edges.impact_weight; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.impact_weight IS '影响权重';


--
-- Name: COLUMN lineage_edges.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.description IS '关系描述';


--
-- Name: COLUMN lineage_edges.extra_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.extra_metadata IS '扩展元数据';


--
-- Name: COLUMN lineage_edges.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.is_active IS '是否活跃';


--
-- Name: COLUMN lineage_edges.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.created_at IS '创建时间';


--
-- Name: COLUMN lineage_edges.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.updated_at IS '更新时间';


--
-- Name: COLUMN lineage_edges.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.created_by IS '创建人';


--
-- Name: COLUMN lineage_edges.organization_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_edges.organization_id IS '所属组织';


--
-- Name: lineage_graphs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lineage_graphs (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    graph_data json,
    version character varying(50),
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by character varying(100)
);


--
-- Name: TABLE lineage_graphs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lineage_graphs IS '数据血缘图谱表';


--
-- Name: COLUMN lineage_graphs.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.name IS '图谱名称';


--
-- Name: COLUMN lineage_graphs.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.description IS '图谱描述';


--
-- Name: COLUMN lineage_graphs.graph_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.graph_data IS '图数据';


--
-- Name: COLUMN lineage_graphs.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.version IS '版本号';


--
-- Name: COLUMN lineage_graphs.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.is_active IS '是否活跃';


--
-- Name: COLUMN lineage_graphs.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.created_at IS '创建时间';


--
-- Name: COLUMN lineage_graphs.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.updated_at IS '更新时间';


--
-- Name: COLUMN lineage_graphs.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_graphs.created_by IS '创建人';


--
-- Name: lineage_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lineage_nodes (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    display_name character varying(255),
    node_type character varying(50) NOT NULL,
    system character varying(100),
    database character varying(100),
    schema_name character varying(100),
    full_path character varying(500),
    description text,
    extra_metadata json,
    tags json,
    owner character varying(100),
    quality_score integer,
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by character varying(100),
    resource_type character varying(50),
    resource_id integer,
    organization_id integer
);


--
-- Name: TABLE lineage_nodes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lineage_nodes IS '数据血缘节点表';


--
-- Name: COLUMN lineage_nodes.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.name IS '节点名称';


--
-- Name: COLUMN lineage_nodes.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.display_name IS '显示名称';


--
-- Name: COLUMN lineage_nodes.node_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.node_type IS '节点类型: source/table/column/report/model/pipeline/api';


--
-- Name: COLUMN lineage_nodes.system; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.system IS '所属系统';


--
-- Name: COLUMN lineage_nodes.database; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.database IS '所属数据库';


--
-- Name: COLUMN lineage_nodes.schema_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.schema_name IS '所属Schema';


--
-- Name: COLUMN lineage_nodes.full_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.full_path IS '完整路径';


--
-- Name: COLUMN lineage_nodes.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.description IS '描述';


--
-- Name: COLUMN lineage_nodes.extra_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.extra_metadata IS '扩展元数据';


--
-- Name: COLUMN lineage_nodes.tags; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.tags IS '标签列表';


--
-- Name: COLUMN lineage_nodes.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.owner IS '责任人';


--
-- Name: COLUMN lineage_nodes.quality_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.quality_score IS '数据质量评分';


--
-- Name: COLUMN lineage_nodes.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.is_active IS '是否活跃';


--
-- Name: COLUMN lineage_nodes.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.created_at IS '创建时间';


--
-- Name: COLUMN lineage_nodes.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.updated_at IS '更新时间';


--
-- Name: COLUMN lineage_nodes.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.created_by IS '创建人';


--
-- Name: COLUMN lineage_nodes.resource_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.resource_type IS '关联资源类型: dataset/training_job/model';


--
-- Name: COLUMN lineage_nodes.resource_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.resource_id IS '关联资源ID';


--
-- Name: COLUMN lineage_nodes.organization_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lineage_nodes.organization_id IS '所属组织';


--
-- Name: llm_call_traces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_call_traces (
    id bigint NOT NULL,
    organization_id integer,
    user_id integer,
    username character varying(128),
    provider character varying(64) NOT NULL,
    model character varying(255) NOT NULL,
    source character varying(128) NOT NULL,
    operation character varying(64) DEFAULT 'chat'::character varying NOT NULL,
    status character varying(32) NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    total_tokens integer,
    latency_ms double precision DEFAULT 0 NOT NULL,
    error_message character varying(500),
    request_path character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_call_traces_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_call_traces_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_call_traces_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_call_traces_id_seq OWNED BY public.llm_call_traces.id;


--
-- Name: llm_prompt_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_prompt_templates (
    id bigint NOT NULL,
    organization_id integer NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    template text NOT NULL,
    variables jsonb DEFAULT '[]'::jsonb NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_prompt_templates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_prompt_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_prompt_templates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_prompt_templates_id_seq OWNED BY public.llm_prompt_templates.id;


--
-- Name: llm_rag_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_rag_documents (
    id bigint NOT NULL,
    organization_id integer NOT NULL,
    name character varying(255) NOT NULL,
    source character varying(255) DEFAULT 'manual'::character varying NOT NULL,
    content text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_rag_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_rag_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_rag_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_rag_documents_id_seq OWNED BY public.llm_rag_documents.id;


--
-- Name: masking_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masking_rules (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    field_pattern character varying(255) NOT NULL,
    masking_type character varying(50) NOT NULL,
    masking_strategy character varying(30),
    parameters json,
    is_active integer,
    priority integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by integer
);


--
-- Name: COLUMN masking_rules.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.name IS '规则名称';


--
-- Name: COLUMN masking_rules.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.description IS '规则描述';


--
-- Name: COLUMN masking_rules.field_pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.field_pattern IS '字段匹配模式(正则)';


--
-- Name: COLUMN masking_rules.masking_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.masking_type IS '脱敏类型: phone/id_card/bank_card/email/name/address/custom';


--
-- Name: COLUMN masking_rules.masking_strategy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.masking_strategy IS '脱敏策略: mask/hash/encrypt/replace/shuffle/truncate/fpe';


--
-- Name: COLUMN masking_rules.parameters; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.parameters IS '脱敏参数';


--
-- Name: COLUMN masking_rules.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.is_active IS '是否启用';


--
-- Name: COLUMN masking_rules.priority; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.priority IS '优先级';


--
-- Name: COLUMN masking_rules.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.masking_rules.created_by IS '创建者ID';


--
-- Name: masking_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.masking_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: masking_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.masking_rules_id_seq OWNED BY public.masking_rules.id;


--
-- Name: mechanical_fix_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mechanical_fix_log (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    mission_id bigint,
    fix_type character varying(40) NOT NULL,
    source_mission_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    executed_by character varying(60) DEFAULT 'system'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mechanical_fix_log_fix_type_check CHECK (((fix_type)::text = ANY ((ARRAY['dedup_report'::character varying, 'typo_fix'::character varying, 'link_patch'::character varying, 'format_normalize'::character varying])::text[])))
);


--
-- Name: mechanical_fix_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mechanical_fix_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mechanical_fix_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mechanical_fix_log_id_seq OWNED BY public.mechanical_fix_log.id;


--
-- Name: metered_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metered_usage (
    id integer NOT NULL,
    mission_id integer NOT NULL,
    agent_session_id character varying(64),
    tool_calls integer,
    tokens_used integer,
    wall_seconds double precision,
    api_calls_detail jsonb,
    recorded_by character varying(64),
    created_at timestamp without time zone
);


--
-- Name: metered_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.metered_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: metered_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.metered_usage_id_seq OWNED BY public.metered_usage.id;


--
-- Name: mission_lifecycle_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mission_lifecycle_log (
    id bigint NOT NULL,
    mission_id bigint NOT NULL,
    action character varying(20) NOT NULL,
    actor_user_id bigint,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mission_lifecycle_log_action_check CHECK (((action)::text = ANY ((ARRAY['suspend'::character varying, 'resume'::character varying, 'write_off'::character varying, 'orphan_detect'::character varying, 'reclaim'::character varying, 'necessity_gate'::character varying, 'pr_gate'::character varying, 'custody_requeue'::character varying])::text[])))
);


--
-- Name: mission_lifecycle_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mission_lifecycle_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mission_lifecycle_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mission_lifecycle_log_id_seq OWNED BY public.mission_lifecycle_log.id;


--
-- Name: model_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_artifacts (
    id bigint NOT NULL,
    model_id integer NOT NULL,
    model_version_id integer,
    format character varying(24) NOT NULL,
    file_name character varying(512) NOT NULL,
    file_path text NOT NULL,
    size_bytes bigint DEFAULT 0 NOT NULL,
    sha256 character(64) NOT NULL,
    validation_status character varying(24) DEFAULT 'generated'::character varying NOT NULL,
    validation_report jsonb DEFAULT '{}'::jsonb NOT NULL,
    benchmark_result jsonb,
    organization_id integer,
    created_by integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    validated_at timestamp without time zone
);


--
-- Name: model_artifacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_artifacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_artifacts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_artifacts_id_seq OWNED BY public.model_artifacts.id;


--
-- Name: model_comparison_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_comparison_runs (
    id bigint NOT NULL,
    name character varying(256) NOT NULL,
    model_ids jsonb NOT NULL,
    dataset_id integer,
    task_type character varying(32) NOT NULL,
    target_column character varying(256) NOT NULL,
    feature_columns jsonb NOT NULL,
    primary_metric character varying(32) NOT NULL,
    results jsonb DEFAULT '[]'::jsonb NOT NULL,
    winner_model_id integer,
    status character varying(24) DEFAULT 'completed'::character varying NOT NULL,
    organization_id integer,
    created_by integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: model_comparison_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_comparison_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_comparison_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_comparison_runs_id_seq OWNED BY public.model_comparison_runs.id;


--
-- Name: model_deployments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_deployments (
    id integer NOT NULL,
    model_id integer,
    model_path text,
    status character varying(32) DEFAULT 'running'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: model_deployments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_deployments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_deployments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_deployments_id_seq OWNED BY public.model_deployments.id;


--
-- Name: model_evaluation_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_evaluation_results (
    id integer NOT NULL,
    model_id integer,
    dataset_id integer,
    task_type character varying(64),
    metrics jsonb,
    cross_validation jsonb,
    cv_folds integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    name character varying(255)
);


--
-- Name: model_evaluation_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_evaluation_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_evaluation_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_evaluation_results_id_seq OWNED BY public.model_evaluation_results.id;


--
-- Name: model_folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_folders (
    id integer NOT NULL,
    organization_id integer NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: model_folders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_folders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_folders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_folders_id_seq OWNED BY public.model_folders.id;


--
-- Name: model_training_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_training_jobs (
    id integer NOT NULL,
    name character varying(255),
    dataset_id integer,
    model_type character varying(64),
    status character varying(32) DEFAULT 'running'::character varying,
    model_path text,
    metrics jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: model_training_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_training_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_training_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_training_jobs_id_seq OWNED BY public.model_training_jobs.id;


--
-- Name: model_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_versions (
    id integer NOT NULL,
    model_id integer NOT NULL,
    version_number character varying(32) NOT NULL,
    file_path character varying(512),
    metrics json,
    parameters json,
    mlflow_run_id character varying(64),
    mlflow_model_version character varying(32),
    is_deployed boolean,
    created_at timestamp without time zone
);


--
-- Name: model_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_versions_id_seq OWNED BY public.model_versions.id;


--
-- Name: models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.models (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    model_type character varying(64),
    framework character varying(64),
    status public.modelstatus,
    stage character varying(32),
    project_id integer,
    folder_id integer,
    model_blob bytea,
    mlflow_name character varying(128),
    organization_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: models_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: models_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.models_id_seq OWNED BY public.models.id;


--
-- Name: monitoring_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.monitoring_logs (
    id bigint NOT NULL,
    model_id integer,
    dataset_id integer,
    predictions jsonb,
    actuals jsonb,
    metrics jsonb,
    drift_score double precision,
    drift_detected boolean DEFAULT false NOT NULL,
    sample_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: monitoring_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.monitoring_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: monitoring_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.monitoring_logs_id_seq OWNED BY public.monitoring_logs.id;


--
-- Name: necessity_gate_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.necessity_gate_log (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    task_key character varying(120),
    goal_hash character varying(64),
    repo character varying(120),
    decision character varying(20) NOT NULL,
    reason text,
    matched_mission_id bigint,
    mechanical_fix_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT necessity_gate_log_decision_check CHECK (((decision)::text = ANY ((ARRAY['pass'::character varying, 'reject_duplicate'::character varying, 'reject_covered'::character varying, 'reject_inflight'::character varying, 'downgrade_mechanical'::character varying])::text[])))
);


--
-- Name: necessity_gate_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.necessity_gate_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: necessity_gate_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.necessity_gate_log_id_seq OWNED BY public.necessity_gate_log.id;


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    industry character varying(64),
    plan character varying(32),
    contact_email character varying(128),
    status character varying(32),
    workspace character varying(32) NOT NULL,
    created_at timestamp without time zone
);


--
-- Name: organizations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.organizations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: organizations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.organizations_id_seq OWNED BY public.organizations.id;


--
-- Name: oss_inventory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oss_inventory (
    id integer NOT NULL,
    repo text NOT NULL,
    tier integer DEFAULT 3 NOT NULL,
    focus text,
    status text DEFAULT 'pending'::text NOT NULL,
    task_key text,
    report_id bigint,
    lang text,
    license text,
    methods text,
    multiphase text,
    boundary text,
    gpu text,
    summary text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    cache_nodes text,
    cache_commit text,
    cache_size_mb integer,
    cache_at timestamp with time zone
);


--
-- Name: oss_inventory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.oss_inventory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: oss_inventory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.oss_inventory_id_seq OWNED BY public.oss_inventory.id;


--
-- Name: oss_solutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oss_solutions (
    id integer NOT NULL,
    repo text NOT NULL,
    feature_slug text NOT NULL,
    tag text NOT NULL,
    method text,
    files text,
    conventions text,
    transfer text,
    evidence text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: oss_solutions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.oss_solutions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: oss_solutions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.oss_solutions_id_seq OWNED BY public.oss_solutions.id;


--
-- Name: outcome_evaluations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcome_evaluations (
    evaluation_id text NOT NULL,
    artifact_id text NOT NULL,
    mission_id bigint,
    contributor text NOT NULL,
    grade text,
    e_level text,
    grade_reason text,
    evidence_refs text DEFAULT '[]'::text NOT NULL,
    reviewer text,
    reviewer_kind text,
    review_status text DEFAULT 'unreviewed'::text NOT NULL,
    baseline_or_scope text,
    boundary text,
    status text NOT NULL,
    reason_codes text DEFAULT '[]'::text NOT NULL,
    reason_source text DEFAULT 'manual'::text NOT NULL,
    repo text,
    organization_id bigint,
    window_finished_at text,
    created_at text NOT NULL,
    decided_at text,
    wave_id text,
    batch_id text,
    seat_ids text,
    seat_families text,
    seat_class text,
    contributor_family text,
    cross_model_verified boolean DEFAULT false,
    consensus text,
    citation_check text,
    value_flags text,
    artifact_check text,
    CONSTRAINT ck_outcome_eval_decided_graded CHECK (((status = ANY (ARRAY['pending'::text, 'rejected'::text])) OR ((grade IS NOT NULL) AND (e_level IS NOT NULL)))),
    CONSTRAINT ck_outcome_eval_e_level CHECK (((e_level IS NULL) OR (e_level = ANY (ARRAY['E0'::text, 'E1'::text, 'E2'::text, 'E3'::text, 'E4'::text])))),
    CONSTRAINT ck_outcome_eval_grade CHECK (((grade IS NULL) OR (grade = ANY (ARRAY['S1'::text, 'S2'::text, 'S3'::text, 'S4'::text, 'S5'::text, 'S-N'::text])))),
    CONSTRAINT ck_outcome_eval_pending_blank CHECK (((status <> 'pending'::text) OR ((grade IS NULL) AND (e_level IS NULL)))),
    CONSTRAINT ck_outcome_eval_reviewer_kind CHECK (((reviewer_kind IS NULL) OR (reviewer_kind = ANY (ARRAY['independent'::text, 'auto_scan'::text])))),
    CONSTRAINT ck_outcome_eval_status CHECK ((status = ANY (ARRAY['pending'::text, 'confirmed'::text, 'candidate'::text, 'rejected'::text])))
);


--
-- Name: outcome_evidence_chains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcome_evidence_chains (
    chain_id text NOT NULL,
    evaluation_id text NOT NULL,
    artifact_id text NOT NULL,
    mission_id bigint,
    contributor text,
    conclusion_ref text,
    grade text,
    e_level_requested text,
    e_level_granted text,
    theory_ref text,
    code_ref text,
    config_ref text,
    data_ref text,
    report_ref text,
    present_elements text DEFAULT '[]'::text NOT NULL,
    missing_elements text DEFAULT '[]'::text NOT NULL,
    invalid_elements text DEFAULT '[]'::text NOT NULL,
    completeness real NOT NULL,
    chain_status text NOT NULL,
    capped boolean DEFAULT false NOT NULL,
    codes text DEFAULT '[]'::text NOT NULL,
    repo text,
    organization_id bigint,
    created_at text NOT NULL,
    CONSTRAINT ck_outcome_chain_completeness CHECK (((completeness >= (0.0)::double precision) AND (completeness <= (1.0)::double precision))),
    CONSTRAINT ck_outcome_chain_e_granted CHECK (((e_level_granted IS NULL) OR (e_level_granted = ANY (ARRAY['E0'::text, 'E1'::text, 'E2'::text, 'E3'::text, 'E4'::text])))),
    CONSTRAINT ck_outcome_chain_e_requested CHECK (((e_level_requested IS NULL) OR (e_level_requested = ANY (ARRAY['E0'::text, 'E1'::text, 'E2'::text, 'E3'::text, 'E4'::text])))),
    CONSTRAINT ck_outcome_chain_status CHECK ((chain_status = ANY (ARRAY['complete'::text, 'partial'::text, 'missing'::text])))
);


--
-- Name: outcome_eval_chain_audit; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.outcome_eval_chain_audit AS
 SELECT e.evaluation_id,
    e.artifact_id,
    e.mission_id,
    e.contributor,
    e.grade,
    e.e_level,
    e.status,
    e.organization_id,
    COALESCE(c.chain_status, 'missing'::text) AS chain_status,
    COALESCE(c.completeness, (0.0)::real) AS chain_completeness,
    COALESCE(c.capped, false) AS chain_capped,
        CASE
            WHEN ((e.status = 'confirmed'::text) AND (e.grade = ANY (ARRAY['S3'::text, 'S4'::text, 'S5'::text, 'S-N'::text])) AND (COALESCE(c.chain_status, 'missing'::text) <> 'complete'::text)) THEN true
            ELSE false
        END AS chain_blocked
   FROM (public.outcome_evaluations e
     LEFT JOIN public.outcome_evidence_chains c ON ((c.evaluation_id = e.evaluation_id)));


--
-- Name: pipeline_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_runs (
    id integer NOT NULL,
    pipeline_id integer NOT NULL,
    organization_id integer,
    status character varying(32),
    triggered_by character varying(64),
    parameters json,
    validation_context json,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    duration_seconds integer,
    error_message text,
    created_at timestamp without time zone
);


--
-- Name: pipeline_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_runs_id_seq OWNED BY public.pipeline_runs.id;


--
-- Name: pipeline_task_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_task_runs (
    id integer NOT NULL,
    pipeline_run_id integer NOT NULL,
    task_id integer NOT NULL,
    task_name character varying(255) NOT NULL,
    task_type character varying(64) NOT NULL,
    status character varying(32),
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    logs text,
    error text,
    outputs json,
    celery_task_id character varying(128)
);


--
-- Name: pipeline_task_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_task_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_task_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_task_runs_id_seq OWNED BY public.pipeline_task_runs.id;


--
-- Name: pipeline_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_tasks (
    id integer NOT NULL,
    pipeline_id integer NOT NULL,
    name character varying(255) NOT NULL,
    task_type character varying(64) NOT NULL,
    command text,
    config json,
    dependencies json,
    order_index integer,
    timeout integer,
    retry integer,
    created_at timestamp without time zone
);


--
-- Name: pipeline_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_tasks_id_seq OWNED BY public.pipeline_tasks.id;


--
-- Name: pipelines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipelines (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    trigger_type character varying(32),
    cron_expression character varying(64),
    status character varying(32),
    owner_id integer,
    organization_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: pipelines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipelines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipelines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipelines_id_seq OWNED BY public.pipelines.id;


--
-- Name: policy_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_rules (
    id integer NOT NULL,
    policy_id integer NOT NULL,
    rule_name character varying(100),
    rule_type character varying(50),
    rule_value character varying(255),
    condition_expression text,
    is_active integer,
    created_at timestamp without time zone
);


--
-- Name: COLUMN policy_rules.rule_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.policy_rules.rule_name IS '规则名称';


--
-- Name: COLUMN policy_rules.rule_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.policy_rules.rule_type IS '规则类型: role/user/group/department';


--
-- Name: COLUMN policy_rules.rule_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.policy_rules.rule_value IS '规则值(角色ID、用户ID等)';


--
-- Name: COLUMN policy_rules.condition_expression; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.policy_rules.condition_expression IS '条件表达式';


--
-- Name: policy_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.policy_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: policy_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.policy_rules_id_seq OWNED BY public.policy_rules.id;


--
-- Name: postmortem_rule_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.postmortem_rule_references (
    reference_id bigint NOT NULL,
    rule_id text NOT NULL,
    referencing_mission_id bigint,
    reference_kind text NOT NULL,
    evidence text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT postmortem_rule_references_kind_check CHECK ((reference_kind = ANY (ARRAY['later_postmortem_consult'::text, 'mission_text_citation'::text, 'claim_injection'::text])))
);


--
-- Name: postmortem_rule_references_reference_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.postmortem_rule_references_reference_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: postmortem_rule_references_reference_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.postmortem_rule_references_reference_id_seq OWNED BY public.postmortem_rule_references.reference_id;


--
-- Name: postmortem_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.postmortem_rules (
    rule_id text NOT NULL,
    postmortem_id text NOT NULL,
    mission_id bigint,
    rule_index integer NOT NULL,
    rule_text text NOT NULL,
    failure_stage text NOT NULL,
    error_class text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pricing_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pricing_configs (
    id integer NOT NULL,
    component_id integer NOT NULL,
    model_type character varying(64) NOT NULL,
    price_per_call double precision,
    subscription_monthly double precision,
    revenue_share_pct double precision,
    created_at timestamp without time zone
);


--
-- Name: pricing_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pricing_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pricing_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pricing_configs_id_seq OWNED BY public.pricing_configs.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    status public.projectstatus,
    owner_id integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: quality_anomalies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_anomalies (
    id integer NOT NULL,
    dataset_id integer NOT NULL,
    rule_id integer,
    field character varying(100),
    anomaly_type character varying(50),
    expected_value text,
    actual_value text,
    record_ids json,
    record_count integer,
    severity character varying(20),
    status character varying(20),
    detected_at timestamp without time zone,
    resolved_at timestamp without time zone,
    resolved_by integer,
    resolution_note text
);


--
-- Name: quality_anomalies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quality_anomalies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quality_anomalies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quality_anomalies_id_seq OWNED BY public.quality_anomalies.id;


--
-- Name: quality_check_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_check_results (
    id integer NOT NULL,
    rule_id integer NOT NULL,
    dataset_id integer NOT NULL,
    passed_count integer,
    failed_count integer,
    total_count integer,
    score integer,
    details json,
    checked_at timestamp without time zone,
    checked_by integer
);


--
-- Name: quality_check_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quality_check_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quality_check_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quality_check_results_id_seq OWNED BY public.quality_check_results.id;


--
-- Name: quality_rule_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_rule_revisions (
    id integer NOT NULL,
    rule_id integer NOT NULL,
    version integer NOT NULL,
    payload jsonb NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    note text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone
);


--
-- Name: quality_rule_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quality_rule_revisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quality_rule_revisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quality_rule_revisions_id_seq OWNED BY public.quality_rule_revisions.id;


--
-- Name: quality_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_rules (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    rule_type character varying(50) NOT NULL,
    field character varying(100),
    condition text,
    threshold json,
    severity character varying(20),
    is_active integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    created_by integer
);


--
-- Name: quality_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quality_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quality_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quality_rules_id_seq OWNED BY public.quality_rules.id;


--
-- Name: rbac_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rbac_permissions (
    code character varying(128) NOT NULL,
    name character varying(128) NOT NULL,
    resource_type character varying(64) NOT NULL,
    action character varying(32) NOT NULL,
    description text,
    is_builtin boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rbac_role_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rbac_role_permissions (
    role_code character varying(64) NOT NULL,
    permission_code character varying(128) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE rbac_role_permissions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rbac_role_permissions IS '角色与平台实际接口权限的持久化关联';


--
-- Name: rbac_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rbac_roles (
    code character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    is_builtin boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rbac_roles_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])))
);


--
-- Name: realtime_sensor_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.realtime_sensor_alerts (
    id integer NOT NULL,
    monitor_id character varying(64) NOT NULL,
    organization_id integer NOT NULL,
    alert_type character varying(32) NOT NULL,
    field_name character varying(128),
    level character varying(16) DEFAULT 'warning'::character varying NOT NULL,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    message text NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone
);


--
-- Name: realtime_sensor_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.realtime_sensor_alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: realtime_sensor_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.realtime_sensor_alerts_id_seq OWNED BY public.realtime_sensor_alerts.id;


--
-- Name: realtime_sensor_monitors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.realtime_sensor_monitors (
    id character varying(64) NOT NULL,
    organization_id integer NOT NULL,
    created_by integer NOT NULL,
    name character varying(160) NOT NULL,
    source_id character varying(128) NOT NULL,
    query_sql text NOT NULL,
    time_column character varying(128) NOT NULL,
    fields jsonb DEFAULT '[]'::jsonb NOT NULL,
    poll_interval_seconds integer DEFAULT 60 NOT NULL,
    freeze_points integer DEFAULT 20 NOT NULL,
    jump_sigma double precision DEFAULT 6 NOT NULL,
    stale_seconds integer DEFAULT 300 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    state jsonb DEFAULT '{}'::jsonb NOT NULL,
    latest_timestamp timestamp without time zone,
    last_checked_at timestamp without time zone,
    last_error text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: repro_verdict_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repro_verdict_votes (
    id integer NOT NULL,
    vote_group_id character varying(64) NOT NULL,
    mission_id integer,
    voter_model character varying(64) NOT NULL,
    voter_worktree character varying(256),
    verdict character varying(16) NOT NULL,
    confidence double precision,
    evidence_hash character varying(64),
    reasoning text,
    is_blind boolean,
    created_at timestamp without time zone
);


--
-- Name: repro_verdict_votes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.repro_verdict_votes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: repro_verdict_votes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.repro_verdict_votes_id_seq OWNED BY public.repro_verdict_votes.id;


--
-- Name: review_budget_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_budget_ledger (
    entry_id text NOT NULL,
    seq integer NOT NULL,
    kind text NOT NULL,
    review_type text NOT NULL,
    subject text NOT NULL,
    conclusion text NOT NULL,
    reviewer text NOT NULL,
    duration_s double precision,
    duration_source text DEFAULT 'not_observable'::text NOT NULL,
    source text DEFAULT 'caller'::text NOT NULL,
    note text,
    prev_hash text NOT NULL,
    entry_hash text NOT NULL,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT review_budget_ledger_kind_check CHECK ((kind = ANY (ARRAY['review'::text, 'exemption'::text, 'production'::text]))),
    CONSTRAINT review_budget_ledger_review_type_check CHECK ((review_type = ANY (ARRAY['execution'::text, 'technical'::text, 'outcome'::text, 'innovation'::text])))
);


--
-- Name: saturation_pulse; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saturation_pulse (
    ts timestamp with time zone DEFAULT now(),
    online integer,
    busy integer,
    held integer,
    released text,
    note text
);


--
-- Name: ship_samples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ship_samples (
    id integer NOT NULL,
    calc_type character varying(32) NOT NULL,
    params json,
    result json,
    error text,
    error_pct double precision,
    passed boolean,
    source character varying(64),
    tags json,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ship_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ship_samples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ship_samples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ship_samples_id_seq OWNED BY public.ship_samples.id;


--
-- Name: sidecar_check_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sidecar_check_results (
    id integer NOT NULL,
    mission_id integer,
    checker_name character varying(64) NOT NULL,
    output_type character varying(64) NOT NULL,
    passed boolean NOT NULL,
    details jsonb,
    red_flag boolean,
    created_at timestamp without time zone
);


--
-- Name: sidecar_check_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sidecar_check_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sidecar_check_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sidecar_check_results_id_seq OWNED BY public.sidecar_check_results.id;


--
-- Name: split_verdict_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.split_verdict_events (
    id integer NOT NULL,
    vote_group_id character varying(64) NOT NULL,
    mission_id integer,
    vote_tally jsonb,
    resolution character varying(32),
    notes text,
    created_at timestamp without time zone
);


--
-- Name: split_verdict_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.split_verdict_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: split_verdict_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.split_verdict_events_id_seq OWNED BY public.split_verdict_events.id;


--
-- Name: standard_fields; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.standard_fields (
    id integer NOT NULL,
    standard_id integer NOT NULL,
    name character varying(100) NOT NULL,
    display_name character varying(100),
    data_type character varying(50) NOT NULL,
    length integer,
    "precision" integer,
    nullable integer,
    default_value character varying(500),
    description text,
    validation_rules json,
    is_primary_key integer,
    is_unique integer,
    is_indexed integer,
    sort_order integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: COLUMN standard_fields.standard_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.standard_id IS '所属标准ID';


--
-- Name: COLUMN standard_fields.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.name IS '字段名称';


--
-- Name: COLUMN standard_fields.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.display_name IS '显示名称';


--
-- Name: COLUMN standard_fields.data_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.data_type IS '数据类型: string/integer/float/date/datetime/boolean/json';


--
-- Name: COLUMN standard_fields.length; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.length IS '长度限制';


--
-- Name: COLUMN standard_fields."precision"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields."precision" IS '精度(小数位数)';


--
-- Name: COLUMN standard_fields.nullable; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.nullable IS '是否可空: 0否/1是';


--
-- Name: COLUMN standard_fields.default_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.default_value IS '默认值';


--
-- Name: COLUMN standard_fields.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.description IS '字段描述';


--
-- Name: COLUMN standard_fields.validation_rules; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.validation_rules IS '验证规则';


--
-- Name: COLUMN standard_fields.is_primary_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.is_primary_key IS '是否主键';


--
-- Name: COLUMN standard_fields.is_unique; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.is_unique IS '是否唯一';


--
-- Name: COLUMN standard_fields.is_indexed; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.is_indexed IS '是否索引';


--
-- Name: COLUMN standard_fields.sort_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.standard_fields.sort_order IS '排序顺序';


--
-- Name: standard_fields_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.standard_fields_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: standard_fields_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.standard_fields_id_seq OWNED BY public.standard_fields.id;


--
-- Name: task_gradient; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_gradient (
    cycle_id text NOT NULL,
    tier text NOT NULL,
    base_share double precision NOT NULL,
    calibrated_share double precision NOT NULL,
    dispatched_count integer NOT NULL,
    tier_yield double precision,
    yield_bar double precision,
    multiplier double precision,
    stats_digest text NOT NULL,
    total_pool integer NOT NULL,
    reason text,
    organization_id bigint,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT task_gradient_tier_check CHECK ((tier = ANY (ARRAY['S1'::text, 'S2'::text, 'S3'::text, 'S4'::text, 'S5'::text])))
);


--
-- Name: tensor_agent_integrity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_agent_integrity (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    model character varying(64) NOT NULL,
    strikes integer DEFAULT 0 NOT NULL,
    last_strike_at timestamp with time zone,
    notes text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tensor_agent_integrity_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_agent_integrity_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_agent_integrity_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_agent_integrity_id_seq OWNED BY public.tensor_agent_integrity.id;


--
-- Name: tensor_agent_session_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_agent_session_events (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    seq integer NOT NULL,
    kind character varying(40) NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tensor_agent_session_events_kind_check CHECK (((kind)::text = ANY ((ARRAY['plan'::character varying, 'execution_started'::character varying, 'run_registered'::character varying, 'validation_seen'::character varying, 'decision'::character varying, 'approval_requested'::character varying, 'approval_decided'::character varying, 'note'::character varying, 'error'::character varying, 'tool_call'::character varying, 'tool_result'::character varying, 'web_search'::character varying, 'code_commit'::character varying])::text[])))
);


--
-- Name: tensor_agent_session_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_agent_session_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_agent_session_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_agent_session_events_id_seq OWNED BY public.tensor_agent_session_events.id;


--
-- Name: tensor_agent_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_agent_sessions (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    execution_id bigint,
    agent_name character varying(120) DEFAULT 'tensor-executor'::character varying NOT NULL,
    status character varying(24) DEFAULT 'active'::character varying NOT NULL,
    budget jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT tensor_agent_sessions_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'completed'::character varying, 'failed'::character varying, 'stopped'::character varying])::text[])))
);


--
-- Name: tensor_agent_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_agent_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_agent_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_agent_sessions_id_seq OWNED BY public.tensor_agent_sessions.id;


--
-- Name: tensor_approval_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_approval_requests (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    scenario_id bigint,
    kind character varying(32) NOT NULL,
    from_stage character varying(8),
    to_stage character varying(8),
    reason text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    requested_by character varying(120),
    decided_by bigint,
    decided_at timestamp with time zone,
    decision_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    mission_id bigint,
    CONSTRAINT tensor_approval_requests_kind_check CHECK (((kind)::text = ANY ((ARRAY['stage_promotion'::character varying, 'stop_condition'::character varying, 'dev_acceptance'::character varying])::text[]))),
    CONSTRAINT tensor_approval_requests_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying])::text[])))
);


--
-- Name: tensor_approval_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_approval_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_approval_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_approval_requests_id_seq OWNED BY public.tensor_approval_requests.id;


--
-- Name: tensor_capability_deltas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_capability_deltas (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    mission_id bigint NOT NULL,
    category text NOT NULL,
    summary text NOT NULL,
    reason text,
    artifacts jsonb DEFAULT '[]'::jsonb NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    card_id bigint,
    reviewer text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_capability_delta_category CHECK ((category = ANY (ARRAY['new_capability'::text, 'capability_extension'::text, 'defect_fix'::text, 'reusable_knowledge'::text, 'falsified_hypothesis'::text, 'no_substantive_delta'::text])))
);


--
-- Name: tensor_capability_deltas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_capability_deltas_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_capability_deltas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_capability_deltas_id_seq OWNED BY public.tensor_capability_deltas.id;


--
-- Name: tensor_credit_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_credit_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_credit_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_credit_ledger_id_seq OWNED BY public.tensor_credit_ledger.id;


--
-- Name: tensor_dev_missions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_dev_missions (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    goal text NOT NULL,
    repo character varying(120) NOT NULL,
    base_ref character varying(120) DEFAULT 'main'::character varying NOT NULL,
    budget jsonb DEFAULT '{"max_tokens": 3000000, "max_tool_calls": 80, "max_wall_seconds": 7200}'::jsonb NOT NULL,
    status character varying(24) DEFAULT 'queued'::character varying NOT NULL,
    claim_token uuid,
    claimed_by character varying(120),
    session_id bigint,
    result jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone,
    finished_at timestamp with time zone,
    runner character varying(16) DEFAULT 'native'::character varying NOT NULL,
    origin character varying(16) DEFAULT 'owner'::character varying NOT NULL,
    model character varying(64),
    parent_mission_id integer,
    co_repos text[],
    claimed_user_id bigint,
    worker_role text,
    task_key text,
    agent_session_id uuid,
    campaign_id bigint,
    campaign_phase text,
    suspended_at timestamp with time zone,
    suspend_reason text,
    suspended_by bigint,
    budget_frozen jsonb,
    budget_used jsonb DEFAULT '{}'::jsonb NOT NULL,
    fail_stage character varying(60),
    report_owed boolean DEFAULT false NOT NULL,
    worktree_recyclable boolean DEFAULT false NOT NULL,
    write_off_at timestamp with time zone,
    report_id integer,
    CONSTRAINT tensor_dev_missions_origin_check CHECK (((origin)::text = ANY ((ARRAY['owner'::character varying, 'agent'::character varying])::text[]))),
    CONSTRAINT tensor_dev_missions_status_check CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'claimed'::character varying, 'running'::character varying, 'awaiting_acceptance'::character varying, 'awaiting_pr_merge'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'stopped'::character varying, 'revoked'::character varying, 'suspended'::character varying, 'orphan'::character varying])::text[])))
);


--
-- Name: tensor_dev_missions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_dev_missions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_dev_missions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_dev_missions_id_seq OWNED BY public.tensor_dev_missions.id;


--
-- Name: tensor_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_executions (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    execution_key character varying(200) NOT NULL,
    scenario_key character varying(120) NOT NULL,
    stage character varying(8) NOT NULL,
    status character varying(24) DEFAULT 'queued'::character varying NOT NULL,
    claim_token character varying(64),
    claimed_by character varying(120),
    triggered_by bigint,
    budget jsonb DEFAULT '{}'::jsonb NOT NULL,
    heartbeat_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    log_tail text,
    registered_run_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    result_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tensor_executions_stage_check CHECK (((stage)::text = ANY ((ARRAY['S0'::character varying, 'S1'::character varying, 'S2'::character varying, 'S3'::character varying, 'S4'::character varying])::text[]))),
    CONSTRAINT tensor_executions_status_check CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'claimed'::character varying, 'running'::character varying, 'cancel_requested'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: tensor_executions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_executions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_executions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_executions_id_seq OWNED BY public.tensor_executions.id;


--
-- Name: tensor_executors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_executors (
    executor_id character varying(120) NOT NULL,
    hostname character varying(200),
    version character varying(40),
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    heartbeat_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tensor_guide_tracker; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_guide_tracker (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    metric_key character varying(80) NOT NULL,
    metric_value double precision DEFAULT 0 NOT NULL,
    target_value double precision,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text DEFAULT ''::text NOT NULL
);


--
-- Name: tensor_guide_tracker_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_guide_tracker_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_guide_tracker_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_guide_tracker_id_seq OWNED BY public.tensor_guide_tracker.id;


--
-- Name: tensor_model_blacklist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_model_blacklist (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    model character varying(64) NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    blacklisted_at timestamp with time zone DEFAULT now() NOT NULL,
    released_at timestamp with time zone,
    released_by bigint
);


--
-- Name: tensor_model_blacklist_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_model_blacklist_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_model_blacklist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_model_blacklist_id_seq OWNED BY public.tensor_model_blacklist.id;


--
-- Name: tensor_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_reports (
    id integer NOT NULL,
    organization_id integer NOT NULL,
    title text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    scenario_key text,
    mission_id integer,
    stage text,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    markdown text NOT NULL,
    figures jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'published'::text NOT NULL,
    created_by integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    qa_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    qa_rejection_reason text,
    qa_checked_at timestamp with time zone,
    CONSTRAINT tensor_reports_qa_status_check CHECK (((qa_status)::text = ANY ((ARRAY['pending'::character varying, 'passed'::character varying, 'rejected'::character varying])::text[])))
);


--
-- Name: tensor_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_reports_id_seq OWNED BY public.tensor_reports.id;


--
-- Name: tensor_repro_verdicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_repro_verdicts (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    mission_id bigint NOT NULL,
    repro_model character varying(64) NOT NULL,
    verdict character varying(16) NOT NULL,
    claims_vs_recomputed jsonb DEFAULT '{}'::jsonb NOT NULL,
    evidence_paths jsonb DEFAULT '[]'::jsonb NOT NULL,
    wall_time_sec double precision,
    notes text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tensor_repro_verdicts_verdict_check CHECK (((verdict)::text = ANY ((ARRAY['pass'::character varying, 'partial'::character varying, 'fail'::character varying, 'cheat'::character varying])::text[])))
);


--
-- Name: tensor_repro_verdicts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_repro_verdicts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_repro_verdicts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_repro_verdicts_id_seq OWNED BY public.tensor_repro_verdicts.id;


--
-- Name: tensor_scenarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_scenarios (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    scenario_key character varying(120) NOT NULL,
    display_name character varying(200),
    description text,
    current_stage character varying(8) DEFAULT 'S0'::character varying NOT NULL,
    stage_status jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tensor_scenarios_current_stage_check CHECK (((current_stage)::text = ANY ((ARRAY['S0'::character varying, 'S1'::character varying, 'S2'::character varying, 'S3'::character varying, 'S4'::character varying])::text[])))
);


--
-- Name: tensor_scenarios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensor_scenarios_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensor_scenarios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensor_scenarios_id_seq OWNED BY public.tensor_scenarios.id;


--
-- Name: tensor_workspace_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensor_workspace_state (
    executor_id character varying(120) NOT NULL,
    repos jsonb DEFAULT '[]'::jsonb NOT NULL,
    captured_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tensorphysics_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tensorphysics_runs (
    id bigint NOT NULL,
    organization_id bigint NOT NULL,
    run_id character varying(200) NOT NULL,
    status character varying(24) NOT NULL,
    validation_status character varying(16) NOT NULL,
    manifest jsonb NOT NULL,
    manifest_sha256 character(64) NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tensorphysics_runs_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'validating'::character varying, 'queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'preempted'::character varying])::text[]))),
    CONSTRAINT tensorphysics_runs_validation_status_check CHECK (((validation_status)::text = ANY ((ARRAY['not_run'::character varying, 'passed'::character varying, 'failed'::character varying, 'incomplete'::character varying])::text[])))
);


--
-- Name: tensorphysics_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tensorphysics_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tensorphysics_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tensorphysics_runs_id_seq OWNED BY public.tensorphysics_runs.id;


--
-- Name: training_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.training_jobs (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    model_id integer,
    dataset_id integer,
    training_job_id character varying(64),
    mlflow_run_id character varying(64),
    registered_model_id integer,
    status public.trainingstatus,
    hyperparameters jsonb,
    metrics jsonb,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    error_message text,
    organization_id integer,
    created_at timestamp without time zone
);


--
-- Name: training_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.training_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: training_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.training_jobs_id_seq OWNED BY public.training_jobs.id;


--
-- Name: uploaded_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.uploaded_files (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255) NOT NULL,
    category character varying(50) DEFAULT 'general'::character varying,
    file_path text NOT NULL,
    file_size bigint DEFAULT 0,
    extension character varying(20),
    mime_type character varying(100),
    uploaded_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    uploaded_by integer,
    description text,
    tags jsonb,
    is_public integer DEFAULT 0,
    download_count integer DEFAULT 0,
    checksum character varying(64),
    metadata jsonb,
    status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: uploaded_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.uploaded_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: uploaded_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.uploaded_files_id_seq OWNED BY public.uploaded_files.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(64) NOT NULL,
    email character varying(128) NOT NULL,
    hashed_password character varying(256) NOT NULL,
    full_name character varying(128),
    role public.userrole NOT NULL,
    is_active boolean,
    organization_id integer,
    features jsonb,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: COLUMN users.features; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.features IS '允许的功能路径白名单(null/空=不限制)';


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: v_file_category_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_file_category_stats AS
 SELECT uploaded_files.category,
    count(*) AS file_count,
    COALESCE(sum(uploaded_files.file_size), (0)::numeric) AS total_size,
    count(DISTINCT uploaded_files.uploaded_by) AS unique_uploaders
   FROM public.uploaded_files
  WHERE ((uploaded_files.status)::text = 'active'::text)
  GROUP BY uploaded_files.category;


--
-- Name: ab_experiment_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests ALTER COLUMN id SET DEFAULT nextval('public.ab_experiment_requests_id_seq'::regclass);


--
-- Name: ab_experiments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments ALTER COLUMN id SET DEFAULT nextval('public.ab_experiments_id_seq'::regclass);


--
-- Name: access_audits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_audits ALTER COLUMN id SET DEFAULT nextval('public.access_audits_id_seq'::regclass);


--
-- Name: access_policies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_policies ALTER COLUMN id SET DEFAULT nextval('public.access_policies_id_seq'::regclass);


--
-- Name: alert_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules ALTER COLUMN id SET DEFAULT nextval('public.alert_rules_id_seq'::regclass);


--
-- Name: alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts ALTER COLUMN id SET DEFAULT nextval('public.alerts_id_seq'::regclass);


--
-- Name: algorithm_hyperparam_presets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_hyperparam_presets ALTER COLUMN id SET DEFAULT nextval('public.algorithm_hyperparam_presets_id_seq'::regclass);


--
-- Name: algorithm_models id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_models ALTER COLUMN id SET DEFAULT nextval('public.algorithm_models_id_seq'::regclass);


--
-- Name: api_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys ALTER COLUMN id SET DEFAULT nextval('public.api_keys_id_seq'::regclass);


--
-- Name: api_usage_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage_logs ALTER COLUMN id SET DEFAULT nextval('public.api_usage_logs_id_seq'::regclass);


--
-- Name: asset_metadata id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_metadata ALTER COLUMN id SET DEFAULT nextval('public.asset_metadata_id_seq'::regclass);


--
-- Name: asset_registry id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_registry ALTER COLUMN id SET DEFAULT nextval('public.asset_registry_id_seq'::regclass);


--
-- Name: audit_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN id SET DEFAULT nextval('public.audit_logs_id_seq'::regclass);


--
-- Name: automl_selection_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.automl_selection_records ALTER COLUMN id SET DEFAULT nextval('public.automl_selection_records_id_seq'::regclass);


--
-- Name: billing_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_records ALTER COLUMN id SET DEFAULT nextval('public.billing_records_id_seq'::regclass);


--
-- Name: campaign_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_events ALTER COLUMN id SET DEFAULT nextval('public.campaign_events_id_seq'::regclass);


--
-- Name: campaign_milestones id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_milestones ALTER COLUMN id SET DEFAULT nextval('public.campaign_milestones_id_seq'::regclass);


--
-- Name: campaign_nodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_nodes ALTER COLUMN id SET DEFAULT nextval('public.campaign_nodes_id_seq'::regclass);


--
-- Name: campaign_plan_dag_edges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_dag_edges ALTER COLUMN id SET DEFAULT nextval('public.campaign_plan_dag_edges_id_seq'::regclass);


--
-- Name: campaign_plan_milestones id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_milestones ALTER COLUMN id SET DEFAULT nextval('public.campaign_plan_milestones_id_seq'::regclass);


--
-- Name: campaign_plan_nodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_nodes ALTER COLUMN id SET DEFAULT nextval('public.campaign_plan_nodes_id_seq'::regclass);


--
-- Name: campaign_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plans ALTER COLUMN id SET DEFAULT nextval('public.campaign_plans_id_seq'::regclass);


--
-- Name: campaign_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_snapshots ALTER COLUMN id SET DEFAULT nextval('public.campaign_snapshots_id_seq'::regclass);


--
-- Name: cluster_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_jobs ALTER COLUMN id SET DEFAULT nextval('public.cluster_jobs_id_seq'::regclass);


--
-- Name: components id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.components ALTER COLUMN id SET DEFAULT nextval('public.components_id_seq'::regclass);


--
-- Name: council_ledger_entries seq; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries ALTER COLUMN seq SET DEFAULT nextval('public.council_ledger_entries_seq_seq'::regclass);


--
-- Name: council_ledger_entries_test seq; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries_test ALTER COLUMN seq SET DEFAULT nextval('public.council_ledger_entries_test_seq_seq'::regclass);


--
-- Name: credit_ledger id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_ledger ALTER COLUMN id SET DEFAULT nextval('public.credit_ledger_id_seq'::regclass);


--
-- Name: data_analysis_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_analysis_runs ALTER COLUMN id SET DEFAULT nextval('public.data_analysis_runs_id_seq'::regclass);


--
-- Name: data_asset_lineage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_asset_lineage ALTER COLUMN id SET DEFAULT nextval('public.data_asset_lineage_id_seq'::regclass);


--
-- Name: data_assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_assets ALTER COLUMN id SET DEFAULT nextval('public.data_assets_id_seq'::regclass);


--
-- Name: data_catalog_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_catalog_stats ALTER COLUMN id SET DEFAULT nextval('public.data_catalog_stats_id_seq'::regclass);


--
-- Name: data_classifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_classifications ALTER COLUMN id SET DEFAULT nextval('public.data_classifications_id_seq'::regclass);


--
-- Name: data_cleaning_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_rules ALTER COLUMN id SET DEFAULT nextval('public.data_cleaning_rules_id_seq'::regclass);


--
-- Name: data_products id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_products ALTER COLUMN id SET DEFAULT nextval('public.data_products_id_seq'::regclass);


--
-- Name: data_security_policies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_security_policies ALTER COLUMN id SET DEFAULT nextval('public.data_security_policies_id_seq'::regclass);


--
-- Name: data_standards id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_standards ALTER COLUMN id SET DEFAULT nextval('public.data_standards_id_seq'::regclass);


--
-- Name: data_tags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_tags ALTER COLUMN id SET DEFAULT nextval('public.data_tags_id_seq'::regclass);


--
-- Name: data_validation_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_validation_results ALTER COLUMN id SET DEFAULT nextval('public.data_validation_results_id_seq'::regclass);


--
-- Name: datasets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasets ALTER COLUMN id SET DEFAULT nextval('public.datasets_id_seq'::regclass);


--
-- Name: deployments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployments ALTER COLUMN id SET DEFAULT nextval('public.deployments_id_seq'::regclass);


--
-- Name: devices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices ALTER COLUMN id SET DEFAULT nextval('public.devices_id_seq'::regclass);


--
-- Name: evaluation_guidance guidance_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_guidance ALTER COLUMN guidance_id SET DEFAULT nextval('public.evaluation_guidance_guidance_id_seq'::regclass);


--
-- Name: factory_community_topics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_topics ALTER COLUMN id SET DEFAULT nextval('public.factory_community_topics_id_seq'::regclass);


--
-- Name: factory_discussion_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages ALTER COLUMN id SET DEFAULT nextval('public.factory_discussion_messages_id_seq'::regclass);


--
-- Name: factory_harness_incidents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents ALTER COLUMN id SET DEFAULT nextval('public.factory_harness_incidents_id_seq'::regclass);


--
-- Name: factory_memory_events event_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events ALTER COLUMN event_id SET DEFAULT nextval('public.factory_memory_events_event_id_seq'::regclass);


--
-- Name: factory_strategy_campaigns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns ALTER COLUMN id SET DEFAULT nextval('public.factory_strategy_campaigns_id_seq'::regclass);


--
-- Name: feature_engineering_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_engineering_rules ALTER COLUMN id SET DEFAULT nextval('public.feature_engineering_rules_id_seq'::regclass);


--
-- Name: feature_groups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_groups ALTER COLUMN id SET DEFAULT nextval('public.feature_groups_id_seq'::regclass);


--
-- Name: feature_ledger id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_ledger ALTER COLUMN id SET DEFAULT nextval('public.feature_ledger_id_seq'::regclass);


--
-- Name: feature_values id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_values ALTER COLUMN id SET DEFAULT nextval('public.feature_values_id_seq'::regclass);


--
-- Name: gate_release_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gate_release_audit ALTER COLUMN id SET DEFAULT nextval('public.gate_release_audit_id_seq'::regclass);


--
-- Name: governance_approvals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_approvals ALTER COLUMN id SET DEFAULT nextval('public.governance_approvals_id_seq'::regclass);


--
-- Name: governance_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_audit ALTER COLUMN id SET DEFAULT nextval('public.governance_audit_id_seq'::regclass);


--
-- Name: hyperopt_studies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_studies ALTER COLUMN id SET DEFAULT nextval('public.hyperopt_studies_id_seq'::regclass);


--
-- Name: hyperopt_trials id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_trials ALTER COLUMN id SET DEFAULT nextval('public.hyperopt_trials_id_seq'::regclass);


--
-- Name: integrity_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrity_events ALTER COLUMN id SET DEFAULT nextval('public.integrity_events_id_seq'::regclass);


--
-- Name: intelligent_data_analysis_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.intelligent_data_analysis_reports ALTER COLUMN id SET DEFAULT nextval('public.intelligent_data_analysis_reports_id_seq'::regclass);


--
-- Name: knowledge_base id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_base ALTER COLUMN id SET DEFAULT nextval('public.knowledge_base_id_seq'::regclass);


--
-- Name: knowledge_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules ALTER COLUMN id SET DEFAULT nextval('public.knowledge_rules_id_seq'::regclass);


--
-- Name: label_group_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_group_items ALTER COLUMN id SET DEFAULT nextval('public.label_group_items_id_seq'::regclass);


--
-- Name: llm_call_traces id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_call_traces ALTER COLUMN id SET DEFAULT nextval('public.llm_call_traces_id_seq'::regclass);


--
-- Name: llm_prompt_templates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_prompt_templates ALTER COLUMN id SET DEFAULT nextval('public.llm_prompt_templates_id_seq'::regclass);


--
-- Name: llm_rag_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_rag_documents ALTER COLUMN id SET DEFAULT nextval('public.llm_rag_documents_id_seq'::regclass);


--
-- Name: masking_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rules ALTER COLUMN id SET DEFAULT nextval('public.masking_rules_id_seq'::regclass);


--
-- Name: mechanical_fix_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mechanical_fix_log ALTER COLUMN id SET DEFAULT nextval('public.mechanical_fix_log_id_seq'::regclass);


--
-- Name: metered_usage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metered_usage ALTER COLUMN id SET DEFAULT nextval('public.metered_usage_id_seq'::regclass);


--
-- Name: mission_lifecycle_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_lifecycle_log ALTER COLUMN id SET DEFAULT nextval('public.mission_lifecycle_log_id_seq'::regclass);


--
-- Name: model_artifacts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts ALTER COLUMN id SET DEFAULT nextval('public.model_artifacts_id_seq'::regclass);


--
-- Name: model_comparison_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs ALTER COLUMN id SET DEFAULT nextval('public.model_comparison_runs_id_seq'::regclass);


--
-- Name: model_deployments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_deployments ALTER COLUMN id SET DEFAULT nextval('public.model_deployments_id_seq'::regclass);


--
-- Name: model_evaluation_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_evaluation_results ALTER COLUMN id SET DEFAULT nextval('public.model_evaluation_results_id_seq'::regclass);


--
-- Name: model_folders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_folders ALTER COLUMN id SET DEFAULT nextval('public.model_folders_id_seq'::regclass);


--
-- Name: model_training_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_training_jobs ALTER COLUMN id SET DEFAULT nextval('public.model_training_jobs_id_seq'::regclass);


--
-- Name: model_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions ALTER COLUMN id SET DEFAULT nextval('public.model_versions_id_seq'::regclass);


--
-- Name: models id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models ALTER COLUMN id SET DEFAULT nextval('public.models_id_seq'::regclass);


--
-- Name: monitoring_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.monitoring_logs ALTER COLUMN id SET DEFAULT nextval('public.monitoring_logs_id_seq'::regclass);


--
-- Name: necessity_gate_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.necessity_gate_log ALTER COLUMN id SET DEFAULT nextval('public.necessity_gate_log_id_seq'::regclass);


--
-- Name: organizations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations ALTER COLUMN id SET DEFAULT nextval('public.organizations_id_seq'::regclass);


--
-- Name: oss_inventory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_inventory ALTER COLUMN id SET DEFAULT nextval('public.oss_inventory_id_seq'::regclass);


--
-- Name: oss_solutions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_solutions ALTER COLUMN id SET DEFAULT nextval('public.oss_solutions_id_seq'::regclass);


--
-- Name: pipeline_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs ALTER COLUMN id SET DEFAULT nextval('public.pipeline_runs_id_seq'::regclass);


--
-- Name: pipeline_task_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_task_runs ALTER COLUMN id SET DEFAULT nextval('public.pipeline_task_runs_id_seq'::regclass);


--
-- Name: pipeline_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_tasks ALTER COLUMN id SET DEFAULT nextval('public.pipeline_tasks_id_seq'::regclass);


--
-- Name: pipelines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines ALTER COLUMN id SET DEFAULT nextval('public.pipelines_id_seq'::regclass);


--
-- Name: policy_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules ALTER COLUMN id SET DEFAULT nextval('public.policy_rules_id_seq'::regclass);


--
-- Name: postmortem_rule_references reference_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rule_references ALTER COLUMN reference_id SET DEFAULT nextval('public.postmortem_rule_references_reference_id_seq'::regclass);


--
-- Name: pricing_configs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_configs ALTER COLUMN id SET DEFAULT nextval('public.pricing_configs_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: quality_anomalies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_anomalies ALTER COLUMN id SET DEFAULT nextval('public.quality_anomalies_id_seq'::regclass);


--
-- Name: quality_check_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check_results ALTER COLUMN id SET DEFAULT nextval('public.quality_check_results_id_seq'::regclass);


--
-- Name: quality_rule_revisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rule_revisions ALTER COLUMN id SET DEFAULT nextval('public.quality_rule_revisions_id_seq'::regclass);


--
-- Name: quality_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rules ALTER COLUMN id SET DEFAULT nextval('public.quality_rules_id_seq'::regclass);


--
-- Name: realtime_sensor_alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_sensor_alerts ALTER COLUMN id SET DEFAULT nextval('public.realtime_sensor_alerts_id_seq'::regclass);


--
-- Name: repro_verdict_votes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repro_verdict_votes ALTER COLUMN id SET DEFAULT nextval('public.repro_verdict_votes_id_seq'::regclass);


--
-- Name: ship_samples id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ship_samples ALTER COLUMN id SET DEFAULT nextval('public.ship_samples_id_seq'::regclass);


--
-- Name: sidecar_check_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sidecar_check_results ALTER COLUMN id SET DEFAULT nextval('public.sidecar_check_results_id_seq'::regclass);


--
-- Name: split_verdict_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_verdict_events ALTER COLUMN id SET DEFAULT nextval('public.split_verdict_events_id_seq'::regclass);


--
-- Name: standard_fields id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.standard_fields ALTER COLUMN id SET DEFAULT nextval('public.standard_fields_id_seq'::regclass);


--
-- Name: tensor_agent_integrity id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_integrity ALTER COLUMN id SET DEFAULT nextval('public.tensor_agent_integrity_id_seq'::regclass);


--
-- Name: tensor_agent_session_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_session_events ALTER COLUMN id SET DEFAULT nextval('public.tensor_agent_session_events_id_seq'::regclass);


--
-- Name: tensor_agent_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_sessions ALTER COLUMN id SET DEFAULT nextval('public.tensor_agent_sessions_id_seq'::regclass);


--
-- Name: tensor_approval_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests ALTER COLUMN id SET DEFAULT nextval('public.tensor_approval_requests_id_seq'::regclass);


--
-- Name: tensor_capability_deltas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_capability_deltas ALTER COLUMN id SET DEFAULT nextval('public.tensor_capability_deltas_id_seq'::regclass);


--
-- Name: tensor_credit_ledger id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_credit_ledger ALTER COLUMN id SET DEFAULT nextval('public.tensor_credit_ledger_id_seq'::regclass);


--
-- Name: tensor_dev_missions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions ALTER COLUMN id SET DEFAULT nextval('public.tensor_dev_missions_id_seq'::regclass);


--
-- Name: tensor_executions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executions ALTER COLUMN id SET DEFAULT nextval('public.tensor_executions_id_seq'::regclass);


--
-- Name: tensor_guide_tracker id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_guide_tracker ALTER COLUMN id SET DEFAULT nextval('public.tensor_guide_tracker_id_seq'::regclass);


--
-- Name: tensor_model_blacklist id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_model_blacklist ALTER COLUMN id SET DEFAULT nextval('public.tensor_model_blacklist_id_seq'::regclass);


--
-- Name: tensor_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_reports ALTER COLUMN id SET DEFAULT nextval('public.tensor_reports_id_seq'::regclass);


--
-- Name: tensor_repro_verdicts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_repro_verdicts ALTER COLUMN id SET DEFAULT nextval('public.tensor_repro_verdicts_id_seq'::regclass);


--
-- Name: tensor_scenarios id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_scenarios ALTER COLUMN id SET DEFAULT nextval('public.tensor_scenarios_id_seq'::regclass);


--
-- Name: tensorphysics_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensorphysics_runs ALTER COLUMN id SET DEFAULT nextval('public.tensorphysics_runs_id_seq'::regclass);


--
-- Name: training_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs ALTER COLUMN id SET DEFAULT nextval('public.training_jobs_id_seq'::regclass);


--
-- Name: uploaded_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uploaded_files ALTER COLUMN id SET DEFAULT nextval('public.uploaded_files_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: ab_experiment_requests ab_experiment_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_pkey PRIMARY KEY (id);


--
-- Name: ab_experiment_requests ab_experiment_requests_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_request_id_key UNIQUE (request_id);


--
-- Name: ab_experiments ab_experiments_experiment_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_experiment_id_key UNIQUE (experiment_id);


--
-- Name: ab_experiments ab_experiments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_pkey PRIMARY KEY (id);


--
-- Name: access_audits access_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_audits
    ADD CONSTRAINT access_audits_pkey PRIMARY KEY (id);


--
-- Name: access_policies access_policies_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_policies
    ADD CONSTRAINT access_policies_name_key UNIQUE (name);


--
-- Name: access_policies access_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_policies
    ADD CONSTRAINT access_policies_pkey PRIMARY KEY (id);


--
-- Name: agent_directory agent_directory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_directory
    ADD CONSTRAINT agent_directory_pkey PRIMARY KEY (seat_id);


--
-- Name: alert_rules alert_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_pkey PRIMARY KEY (id);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: algorithm_hyperparam_presets algorithm_hyperparam_presets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_hyperparam_presets
    ADD CONSTRAINT algorithm_hyperparam_presets_pkey PRIMARY KEY (id);


--
-- Name: algorithm_models algorithm_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_models
    ADD CONSTRAINT algorithm_models_pkey PRIMARY KEY (id);


--
-- Name: annotation_tasks annotation_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.annotation_tasks
    ADD CONSTRAINT annotation_tasks_pkey PRIMARY KEY (id);


--
-- Name: annotations annotations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: api_usage_logs api_usage_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage_logs
    ADD CONSTRAINT api_usage_logs_pkey PRIMARY KEY (id);


--
-- Name: asset_classifications asset_classifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_classifications
    ADD CONSTRAINT asset_classifications_pkey PRIMARY KEY (asset_id, classification_id);


--
-- Name: asset_metadata asset_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_metadata
    ADD CONSTRAINT asset_metadata_pkey PRIMARY KEY (id);


--
-- Name: asset_registry asset_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_registry
    ADD CONSTRAINT asset_registry_pkey PRIMARY KEY (id);


--
-- Name: asset_registry asset_registry_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_registry
    ADD CONSTRAINT asset_registry_slug_key UNIQUE (slug);


--
-- Name: asset_tags asset_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags
    ADD CONSTRAINT asset_tags_pkey PRIMARY KEY (asset_id, tag_id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: automl_selection_records automl_selection_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.automl_selection_records
    ADD CONSTRAINT automl_selection_records_pkey PRIMARY KEY (id);


--
-- Name: automl_selection_records automl_selection_records_task_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.automl_selection_records
    ADD CONSTRAINT automl_selection_records_task_id_key UNIQUE (task_id);


--
-- Name: billing_records billing_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_records
    ADD CONSTRAINT billing_records_pkey PRIMARY KEY (id);


--
-- Name: campaign_events campaign_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_events
    ADD CONSTRAINT campaign_events_pkey PRIMARY KEY (id);


--
-- Name: campaign_milestones campaign_milestones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_milestones
    ADD CONSTRAINT campaign_milestones_pkey PRIMARY KEY (id);


--
-- Name: campaign_nodes campaign_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_nodes
    ADD CONSTRAINT campaign_nodes_pkey PRIMARY KEY (id);


--
-- Name: campaign_plan_dag_edges campaign_plan_dag_edges_campaign_plan_id_from_node_to_node_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_dag_edges
    ADD CONSTRAINT campaign_plan_dag_edges_campaign_plan_id_from_node_to_node_key UNIQUE (campaign_plan_id, from_node, to_node);


--
-- Name: campaign_plan_dag_edges campaign_plan_dag_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_dag_edges
    ADD CONSTRAINT campaign_plan_dag_edges_pkey PRIMARY KEY (id);


--
-- Name: campaign_plan_milestones campaign_plan_milestones_campaign_plan_id_milestone_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_milestones
    ADD CONSTRAINT campaign_plan_milestones_campaign_plan_id_milestone_id_key UNIQUE (campaign_plan_id, milestone_id);


--
-- Name: campaign_plan_milestones campaign_plan_milestones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_milestones
    ADD CONSTRAINT campaign_plan_milestones_pkey PRIMARY KEY (id);


--
-- Name: campaign_plan_nodes campaign_plan_nodes_campaign_plan_id_node_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_nodes
    ADD CONSTRAINT campaign_plan_nodes_campaign_plan_id_node_id_key UNIQUE (campaign_plan_id, node_id);


--
-- Name: campaign_plan_nodes campaign_plan_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_nodes
    ADD CONSTRAINT campaign_plan_nodes_pkey PRIMARY KEY (id);


--
-- Name: campaign_plans campaign_plans_organization_id_source_mission_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plans
    ADD CONSTRAINT campaign_plans_organization_id_source_mission_id_key UNIQUE (organization_id, source_mission_id);


--
-- Name: campaign_plans campaign_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plans
    ADD CONSTRAINT campaign_plans_pkey PRIMARY KEY (id);


--
-- Name: campaign_snapshots campaign_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_snapshots
    ADD CONSTRAINT campaign_snapshots_pkey PRIMARY KEY (id);


--
-- Name: cluster_jobs cluster_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_jobs
    ADD CONSTRAINT cluster_jobs_pkey PRIMARY KEY (id);


--
-- Name: components components_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.components
    ADD CONSTRAINT components_pkey PRIMARY KEY (id);


--
-- Name: council_ledger_entries council_ledger_entries_entry_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries
    ADD CONSTRAINT council_ledger_entries_entry_hash_key UNIQUE (entry_hash);


--
-- Name: council_ledger_entries council_ledger_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries
    ADD CONSTRAINT council_ledger_entries_pkey PRIMARY KEY (seq);


--
-- Name: council_ledger_entries_test council_ledger_entries_test_entry_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries_test
    ADD CONSTRAINT council_ledger_entries_test_entry_hash_key UNIQUE (entry_hash);


--
-- Name: council_ledger_entries_test council_ledger_entries_test_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.council_ledger_entries_test
    ADD CONSTRAINT council_ledger_entries_test_pkey PRIMARY KEY (seq);


--
-- Name: credit_ledger credit_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_ledger
    ADD CONSTRAINT credit_ledger_pkey PRIMARY KEY (id);


--
-- Name: credit_settle_pending credit_settle_pending_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_settle_pending
    ADD CONSTRAINT credit_settle_pending_pkey PRIMARY KEY (evaluation_id);


--
-- Name: data_analysis_runs data_analysis_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_analysis_runs
    ADD CONSTRAINT data_analysis_runs_pkey PRIMARY KEY (id);


--
-- Name: data_asset_lineage data_asset_lineage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_asset_lineage
    ADD CONSTRAINT data_asset_lineage_pkey PRIMARY KEY (id);


--
-- Name: data_assets data_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_assets
    ADD CONSTRAINT data_assets_pkey PRIMARY KEY (id);


--
-- Name: data_catalog_stats data_catalog_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_catalog_stats
    ADD CONSTRAINT data_catalog_stats_pkey PRIMARY KEY (id);


--
-- Name: data_classifications data_classifications_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_classifications
    ADD CONSTRAINT data_classifications_name_key UNIQUE (name);


--
-- Name: data_classifications data_classifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_classifications
    ADD CONSTRAINT data_classifications_pkey PRIMARY KEY (id);


--
-- Name: data_cleaning_rules data_cleaning_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_rules
    ADD CONSTRAINT data_cleaning_rules_pkey PRIMARY KEY (id);


--
-- Name: data_cleaning_runs data_cleaning_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_runs
    ADD CONSTRAINT data_cleaning_runs_pkey PRIMARY KEY (id);


--
-- Name: data_products data_products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_products
    ADD CONSTRAINT data_products_pkey PRIMARY KEY (id);


--
-- Name: data_security_policies data_security_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_security_policies
    ADD CONSTRAINT data_security_policies_pkey PRIMARY KEY (id);


--
-- Name: data_standards data_standards_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_standards
    ADD CONSTRAINT data_standards_name_key UNIQUE (name);


--
-- Name: data_standards data_standards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_standards
    ADD CONSTRAINT data_standards_pkey PRIMARY KEY (id);


--
-- Name: data_tags data_tags_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_tags
    ADD CONSTRAINT data_tags_name_key UNIQUE (name);


--
-- Name: data_tags data_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_tags
    ADD CONSTRAINT data_tags_pkey PRIMARY KEY (id);


--
-- Name: data_validation_results data_validation_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_validation_results
    ADD CONSTRAINT data_validation_results_pkey PRIMARY KEY (id);


--
-- Name: dataset_governance_reports dataset_governance_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_pkey PRIMARY KEY (id);


--
-- Name: datasets datasets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_pkey PRIMARY KEY (id);


--
-- Name: deployments deployments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT deployments_pkey PRIMARY KEY (id);


--
-- Name: devices devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (id);


--
-- Name: eval_cohorts eval_cohorts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_cohorts
    ADD CONSTRAINT eval_cohorts_pkey PRIMARY KEY (cohort_key);


--
-- Name: eval_drain_runs eval_drain_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_drain_runs
    ADD CONSTRAINT eval_drain_runs_pkey PRIMARY KEY (run_id);


--
-- Name: eval_inflight_events eval_inflight_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_inflight_events
    ADD CONSTRAINT eval_inflight_events_pkey PRIMARY KEY (event_id);


--
-- Name: eval_redteam_rechecks eval_redteam_rechecks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_redteam_rechecks
    ADD CONSTRAINT eval_redteam_rechecks_pkey PRIMARY KEY (recheck_id);


--
-- Name: evaluation_guidance evaluation_guidance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_guidance
    ADD CONSTRAINT evaluation_guidance_pkey PRIMARY KEY (guidance_id);


--
-- Name: evaluation_waves evaluation_waves_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_waves
    ADD CONSTRAINT evaluation_waves_pkey PRIMARY KEY (wave_id);


--
-- Name: execution_lineage_records execution_lineage_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.execution_lineage_records
    ADD CONSTRAINT execution_lineage_records_pkey PRIMARY KEY (id);


--
-- Name: exploration_budget exploration_budget_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exploration_budget
    ADD CONSTRAINT exploration_budget_pkey PRIMARY KEY (entry_id);


--
-- Name: factory_agent_identities factory_agent_identities_organization_id_worker_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_agent_identities
    ADD CONSTRAINT factory_agent_identities_organization_id_worker_id_key UNIQUE (organization_id, worker_id);


--
-- Name: factory_agent_identities factory_agent_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_agent_identities
    ADD CONSTRAINT factory_agent_identities_pkey PRIMARY KEY (agent_id);


--
-- Name: factory_community_positions factory_community_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_positions
    ADD CONSTRAINT factory_community_positions_pkey PRIMARY KEY (topic_id, agent_session_id);


--
-- Name: factory_community_topics factory_community_topics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_topics
    ADD CONSTRAINT factory_community_topics_pkey PRIMARY KEY (id);


--
-- Name: factory_discussion_messages factory_discussion_messages_organization_id_client_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_organization_id_client_key_key UNIQUE (organization_id, client_key);


--
-- Name: factory_discussion_messages factory_discussion_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_pkey PRIMARY KEY (id);


--
-- Name: factory_evidence_records factory_evidence_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_evidence_records
    ADD CONSTRAINT factory_evidence_records_pkey PRIMARY KEY (evidence_id);


--
-- Name: factory_harness_incidents factory_harness_incidents_campaign_id_mission_id_kind_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents
    ADD CONSTRAINT factory_harness_incidents_campaign_id_mission_id_kind_key UNIQUE (campaign_id, mission_id, kind);


--
-- Name: factory_harness_incidents factory_harness_incidents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents
    ADD CONSTRAINT factory_harness_incidents_pkey PRIMARY KEY (id);


--
-- Name: factory_learning_candidates factory_learning_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_learning_candidates
    ADD CONSTRAINT factory_learning_candidates_pkey PRIMARY KEY (candidate_id);


--
-- Name: factory_memory_events factory_memory_events_organization_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_organization_id_idempotency_key_key UNIQUE (organization_id, idempotency_key);


--
-- Name: factory_memory_events factory_memory_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_pkey PRIMARY KEY (event_id);


--
-- Name: factory_resident_workers factory_resident_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_resident_workers
    ADD CONSTRAINT factory_resident_workers_pkey PRIMARY KEY (agent_session_id);


--
-- Name: factory_resident_workers factory_resident_workers_worker_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_resident_workers
    ADD CONSTRAINT factory_resident_workers_worker_id_key UNIQUE (worker_id);


--
-- Name: factory_soul_binding_history factory_soul_binding_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_binding_history
    ADD CONSTRAINT factory_soul_binding_history_pkey PRIMARY KEY (session_id, switched_at, binding_id);


--
-- Name: factory_soul_checkpoints factory_soul_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_checkpoints
    ADD CONSTRAINT factory_soul_checkpoints_pkey PRIMARY KEY (session_id, version);


--
-- Name: factory_soul_legacy_sessions factory_soul_legacy_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_legacy_sessions
    ADD CONSTRAINT factory_soul_legacy_sessions_pkey PRIMARY KEY (agent_session_id);


--
-- Name: factory_soul_model_bindings factory_soul_model_bindings_organization_id_name_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_model_bindings
    ADD CONSTRAINT factory_soul_model_bindings_organization_id_name_version_key UNIQUE (organization_id, name, version);


--
-- Name: factory_soul_model_bindings factory_soul_model_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_model_bindings
    ADD CONSTRAINT factory_soul_model_bindings_pkey PRIMARY KEY (binding_id);


--
-- Name: factory_soul_schema_versions factory_soul_schema_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_schema_versions
    ADD CONSTRAINT factory_soul_schema_versions_pkey PRIMARY KEY (version);


--
-- Name: factory_soul_sessions factory_soul_sessions_agent_id_episode_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_agent_id_episode_key UNIQUE (agent_id, episode);


--
-- Name: factory_soul_sessions factory_soul_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_pkey PRIMARY KEY (session_id);


--
-- Name: factory_strategy_campaigns factory_strategy_campaigns_organization_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns
    ADD CONSTRAINT factory_strategy_campaigns_organization_id_name_key UNIQUE (organization_id, name);


--
-- Name: factory_strategy_campaigns factory_strategy_campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns
    ADD CONSTRAINT factory_strategy_campaigns_pkey PRIMARY KEY (id);


--
-- Name: failure_postmortems failure_postmortems_mission_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_postmortems
    ADD CONSTRAINT failure_postmortems_mission_uq UNIQUE (mission_id);


--
-- Name: failure_postmortems failure_postmortems_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_postmortems
    ADD CONSTRAINT failure_postmortems_pkey PRIMARY KEY (postmortem_id);


--
-- Name: feature_engineering_rules feature_engineering_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_engineering_rules
    ADD CONSTRAINT feature_engineering_rules_pkey PRIMARY KEY (id);


--
-- Name: feature_groups feature_groups_group_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_groups
    ADD CONSTRAINT feature_groups_group_id_key UNIQUE (group_id);


--
-- Name: feature_groups feature_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_groups
    ADD CONSTRAINT feature_groups_pkey PRIMARY KEY (id);


--
-- Name: feature_ledger feature_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_ledger
    ADD CONSTRAINT feature_ledger_pkey PRIMARY KEY (id);


--
-- Name: feature_ledger feature_ledger_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_ledger
    ADD CONSTRAINT feature_ledger_slug_key UNIQUE (slug);


--
-- Name: feature_values feature_values_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_values
    ADD CONSTRAINT feature_values_pkey PRIMARY KEY (id);


--
-- Name: gate_release_audit gate_release_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gate_release_audit
    ADD CONSTRAINT gate_release_audit_pkey PRIMARY KEY (id);


--
-- Name: governance_approvals governance_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_approvals
    ADD CONSTRAINT governance_approvals_pkey PRIMARY KEY (id);


--
-- Name: governance_approvals governance_approvals_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_approvals
    ADD CONSTRAINT governance_approvals_request_id_key UNIQUE (request_id);


--
-- Name: governance_audit governance_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_audit
    ADD CONSTRAINT governance_audit_pkey PRIMARY KEY (id);


--
-- Name: guidance_cycle guidance_cycle_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guidance_cycle
    ADD CONSTRAINT guidance_cycle_pkey PRIMARY KEY (cycle_id);


--
-- Name: guidance_outcome guidance_outcome_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guidance_outcome
    ADD CONSTRAINT guidance_outcome_pkey PRIMARY KEY (mission_key);


--
-- Name: hyperopt_studies hyperopt_studies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_studies
    ADD CONSTRAINT hyperopt_studies_pkey PRIMARY KEY (id);


--
-- Name: hyperopt_studies hyperopt_studies_study_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_studies
    ADD CONSTRAINT hyperopt_studies_study_id_key UNIQUE (study_id);


--
-- Name: hyperopt_trials hyperopt_trials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_trials
    ADD CONSTRAINT hyperopt_trials_pkey PRIMARY KEY (id);


--
-- Name: hypothesis_registry hypothesis_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hypothesis_registry
    ADD CONSTRAINT hypothesis_registry_pkey PRIMARY KEY (hypothesis_id);


--
-- Name: innovation_proposals innovation_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.innovation_proposals
    ADD CONSTRAINT innovation_proposals_pkey PRIMARY KEY (proposal_id);


--
-- Name: integrity_events integrity_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrity_events
    ADD CONSTRAINT integrity_events_pkey PRIMARY KEY (id);


--
-- Name: intelligent_data_analysis_reports intelligent_data_analysis_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.intelligent_data_analysis_reports
    ADD CONSTRAINT intelligent_data_analysis_reports_pkey PRIMARY KEY (id);


--
-- Name: knowledge_base knowledge_base_kb_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_base
    ADD CONSTRAINT knowledge_base_kb_key_key UNIQUE (kb_key);


--
-- Name: knowledge_base knowledge_base_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_base
    ADD CONSTRAINT knowledge_base_pkey PRIMARY KEY (id);


--
-- Name: knowledge_rules knowledge_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules
    ADD CONSTRAINT knowledge_rules_pkey PRIMARY KEY (id);


--
-- Name: label_group_items label_group_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_group_items
    ADD CONSTRAINT label_group_items_pkey PRIMARY KEY (id);


--
-- Name: label_groups label_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_groups
    ADD CONSTRAINT label_groups_pkey PRIMARY KEY (id);


--
-- Name: labels labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT labels_pkey PRIMARY KEY (id);


--
-- Name: ledger_gate_meta ledger_gate_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_gate_meta
    ADD CONSTRAINT ledger_gate_meta_pkey PRIMARY KEY (k);


--
-- Name: lineage_change_logs lineage_change_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_change_logs
    ADD CONSTRAINT lineage_change_logs_pkey PRIMARY KEY (id);


--
-- Name: lineage_edges lineage_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_edges
    ADD CONSTRAINT lineage_edges_pkey PRIMARY KEY (id);


--
-- Name: lineage_graphs lineage_graphs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_graphs
    ADD CONSTRAINT lineage_graphs_pkey PRIMARY KEY (id);


--
-- Name: lineage_nodes lineage_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_nodes
    ADD CONSTRAINT lineage_nodes_pkey PRIMARY KEY (id);


--
-- Name: llm_call_traces llm_call_traces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_call_traces
    ADD CONSTRAINT llm_call_traces_pkey PRIMARY KEY (id);


--
-- Name: llm_prompt_templates llm_prompt_templates_organization_id_name_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_prompt_templates
    ADD CONSTRAINT llm_prompt_templates_organization_id_name_version_key UNIQUE (organization_id, name, version);


--
-- Name: llm_prompt_templates llm_prompt_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_prompt_templates
    ADD CONSTRAINT llm_prompt_templates_pkey PRIMARY KEY (id);


--
-- Name: llm_rag_documents llm_rag_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_rag_documents
    ADD CONSTRAINT llm_rag_documents_pkey PRIMARY KEY (id);


--
-- Name: masking_rules masking_rules_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rules
    ADD CONSTRAINT masking_rules_name_key UNIQUE (name);


--
-- Name: masking_rules masking_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rules
    ADD CONSTRAINT masking_rules_pkey PRIMARY KEY (id);


--
-- Name: mechanical_fix_log mechanical_fix_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mechanical_fix_log
    ADD CONSTRAINT mechanical_fix_log_pkey PRIMARY KEY (id);


--
-- Name: metered_usage metered_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metered_usage
    ADD CONSTRAINT metered_usage_pkey PRIMARY KEY (id);


--
-- Name: mission_lifecycle_log mission_lifecycle_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_pkey PRIMARY KEY (id);


--
-- Name: model_artifacts model_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_pkey PRIMARY KEY (id);


--
-- Name: model_comparison_runs model_comparison_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs
    ADD CONSTRAINT model_comparison_runs_pkey PRIMARY KEY (id);


--
-- Name: model_deployments model_deployments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_deployments
    ADD CONSTRAINT model_deployments_pkey PRIMARY KEY (id);


--
-- Name: model_evaluation_results model_evaluation_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_evaluation_results
    ADD CONSTRAINT model_evaluation_results_pkey PRIMARY KEY (id);


--
-- Name: model_folders model_folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_folders
    ADD CONSTRAINT model_folders_pkey PRIMARY KEY (id);


--
-- Name: model_training_jobs model_training_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_training_jobs
    ADD CONSTRAINT model_training_jobs_pkey PRIMARY KEY (id);


--
-- Name: model_versions model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_pkey PRIMARY KEY (id);


--
-- Name: models models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_pkey PRIMARY KEY (id);


--
-- Name: monitoring_logs monitoring_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.monitoring_logs
    ADD CONSTRAINT monitoring_logs_pkey PRIMARY KEY (id);


--
-- Name: necessity_gate_log necessity_gate_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.necessity_gate_log
    ADD CONSTRAINT necessity_gate_log_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: oss_inventory oss_inventory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_inventory
    ADD CONSTRAINT oss_inventory_pkey PRIMARY KEY (id);


--
-- Name: oss_inventory oss_inventory_repo_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_inventory
    ADD CONSTRAINT oss_inventory_repo_key UNIQUE (repo);


--
-- Name: oss_solutions oss_solutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_solutions
    ADD CONSTRAINT oss_solutions_pkey PRIMARY KEY (id);


--
-- Name: oss_solutions oss_solutions_repo_feature_slug_tag_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oss_solutions
    ADD CONSTRAINT oss_solutions_repo_feature_slug_tag_key UNIQUE (repo, feature_slug, tag);


--
-- Name: outcome_evaluations outcome_evaluations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcome_evaluations
    ADD CONSTRAINT outcome_evaluations_pkey PRIMARY KEY (evaluation_id);


--
-- Name: outcome_evidence_chains outcome_evidence_chains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcome_evidence_chains
    ADD CONSTRAINT outcome_evidence_chains_pkey PRIMARY KEY (chain_id);


--
-- Name: pipeline_runs pipeline_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_task_runs pipeline_task_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_task_runs
    ADD CONSTRAINT pipeline_task_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_tasks pipeline_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_tasks
    ADD CONSTRAINT pipeline_tasks_pkey PRIMARY KEY (id);


--
-- Name: pipelines pipelines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_pkey PRIMARY KEY (id);


--
-- Name: policy_rules policy_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules
    ADD CONSTRAINT policy_rules_pkey PRIMARY KEY (id);


--
-- Name: postmortem_rule_references postmortem_rule_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rule_references
    ADD CONSTRAINT postmortem_rule_references_pkey PRIMARY KEY (reference_id);


--
-- Name: postmortem_rule_references postmortem_rule_references_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rule_references
    ADD CONSTRAINT postmortem_rule_references_uq UNIQUE (rule_id, referencing_mission_id, reference_kind);


--
-- Name: postmortem_rules postmortem_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rules
    ADD CONSTRAINT postmortem_rules_pkey PRIMARY KEY (rule_id);


--
-- Name: postmortem_rules postmortem_rules_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rules
    ADD CONSTRAINT postmortem_rules_uq UNIQUE (postmortem_id, rule_index);


--
-- Name: pricing_configs pricing_configs_component_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_configs
    ADD CONSTRAINT pricing_configs_component_id_key UNIQUE (component_id);


--
-- Name: pricing_configs pricing_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_configs
    ADD CONSTRAINT pricing_configs_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: quality_anomalies quality_anomalies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_anomalies
    ADD CONSTRAINT quality_anomalies_pkey PRIMARY KEY (id);


--
-- Name: quality_check_results quality_check_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_check_results
    ADD CONSTRAINT quality_check_results_pkey PRIMARY KEY (id);


--
-- Name: quality_rule_revisions quality_rule_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rule_revisions
    ADD CONSTRAINT quality_rule_revisions_pkey PRIMARY KEY (id);


--
-- Name: quality_rule_revisions quality_rule_revisions_rule_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rule_revisions
    ADD CONSTRAINT quality_rule_revisions_rule_id_version_key UNIQUE (rule_id, version);


--
-- Name: quality_rules quality_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rules
    ADD CONSTRAINT quality_rules_pkey PRIMARY KEY (id);


--
-- Name: rbac_permissions rbac_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rbac_permissions
    ADD CONSTRAINT rbac_permissions_pkey PRIMARY KEY (code);


--
-- Name: rbac_role_permissions rbac_role_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rbac_role_permissions
    ADD CONSTRAINT rbac_role_permissions_pkey PRIMARY KEY (role_code, permission_code);


--
-- Name: rbac_roles rbac_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rbac_roles
    ADD CONSTRAINT rbac_roles_pkey PRIMARY KEY (code);


--
-- Name: realtime_sensor_alerts realtime_sensor_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_sensor_alerts
    ADD CONSTRAINT realtime_sensor_alerts_pkey PRIMARY KEY (id);


--
-- Name: realtime_sensor_monitors realtime_sensor_monitors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_sensor_monitors
    ADD CONSTRAINT realtime_sensor_monitors_pkey PRIMARY KEY (id);


--
-- Name: repro_verdict_votes repro_verdict_votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repro_verdict_votes
    ADD CONSTRAINT repro_verdict_votes_pkey PRIMARY KEY (id);


--
-- Name: review_budget_ledger review_budget_ledger_entry_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_budget_ledger
    ADD CONSTRAINT review_budget_ledger_entry_hash_key UNIQUE (entry_hash);


--
-- Name: review_budget_ledger review_budget_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_budget_ledger
    ADD CONSTRAINT review_budget_ledger_pkey PRIMARY KEY (entry_id);


--
-- Name: ship_samples ship_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ship_samples
    ADD CONSTRAINT ship_samples_pkey PRIMARY KEY (id);


--
-- Name: sidecar_check_results sidecar_check_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sidecar_check_results
    ADD CONSTRAINT sidecar_check_results_pkey PRIMARY KEY (id);


--
-- Name: split_verdict_events split_verdict_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_verdict_events
    ADD CONSTRAINT split_verdict_events_pkey PRIMARY KEY (id);


--
-- Name: standard_fields standard_fields_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.standard_fields
    ADD CONSTRAINT standard_fields_pkey PRIMARY KEY (id);


--
-- Name: task_gradient task_gradient_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_gradient
    ADD CONSTRAINT task_gradient_pkey PRIMARY KEY (cycle_id, tier);


--
-- Name: tensor_agent_integrity tensor_agent_integrity_organization_id_model_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_integrity
    ADD CONSTRAINT tensor_agent_integrity_organization_id_model_key UNIQUE (organization_id, model);


--
-- Name: tensor_agent_integrity tensor_agent_integrity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_integrity
    ADD CONSTRAINT tensor_agent_integrity_pkey PRIMARY KEY (id);


--
-- Name: tensor_agent_session_events tensor_agent_session_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_session_events
    ADD CONSTRAINT tensor_agent_session_events_pkey PRIMARY KEY (id);


--
-- Name: tensor_agent_sessions tensor_agent_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_sessions
    ADD CONSTRAINT tensor_agent_sessions_pkey PRIMARY KEY (id);


--
-- Name: tensor_approval_requests tensor_approval_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_pkey PRIMARY KEY (id);


--
-- Name: tensor_capability_deltas tensor_capability_deltas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_capability_deltas
    ADD CONSTRAINT tensor_capability_deltas_pkey PRIMARY KEY (id);


--
-- Name: tensor_credit_ledger tensor_credit_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_credit_ledger
    ADD CONSTRAINT tensor_credit_ledger_pkey PRIMARY KEY (id);


--
-- Name: tensor_dev_missions tensor_dev_missions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_pkey PRIMARY KEY (id);


--
-- Name: tensor_executions tensor_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executions
    ADD CONSTRAINT tensor_executions_pkey PRIMARY KEY (id);


--
-- Name: tensor_executors tensor_executors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executors
    ADD CONSTRAINT tensor_executors_pkey PRIMARY KEY (executor_id);


--
-- Name: tensor_guide_tracker tensor_guide_tracker_organization_id_metric_key_snapshot_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_guide_tracker
    ADD CONSTRAINT tensor_guide_tracker_organization_id_metric_key_snapshot_at_key UNIQUE (organization_id, metric_key, snapshot_at);


--
-- Name: tensor_guide_tracker tensor_guide_tracker_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_guide_tracker
    ADD CONSTRAINT tensor_guide_tracker_pkey PRIMARY KEY (id);


--
-- Name: tensor_model_blacklist tensor_model_blacklist_organization_id_model_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_model_blacklist
    ADD CONSTRAINT tensor_model_blacklist_organization_id_model_key UNIQUE (organization_id, model);


--
-- Name: tensor_model_blacklist tensor_model_blacklist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_model_blacklist
    ADD CONSTRAINT tensor_model_blacklist_pkey PRIMARY KEY (id);


--
-- Name: tensor_reports tensor_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_reports
    ADD CONSTRAINT tensor_reports_pkey PRIMARY KEY (id);


--
-- Name: tensor_repro_verdicts tensor_repro_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_repro_verdicts
    ADD CONSTRAINT tensor_repro_verdicts_pkey PRIMARY KEY (id);


--
-- Name: tensor_scenarios tensor_scenarios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_scenarios
    ADD CONSTRAINT tensor_scenarios_pkey PRIMARY KEY (id);


--
-- Name: tensor_workspace_state tensor_workspace_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_workspace_state
    ADD CONSTRAINT tensor_workspace_state_pkey PRIMARY KEY (executor_id);


--
-- Name: tensorphysics_runs tensorphysics_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensorphysics_runs
    ADD CONSTRAINT tensorphysics_runs_pkey PRIMARY KEY (id);


--
-- Name: training_jobs training_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_pkey PRIMARY KEY (id);


--
-- Name: training_jobs training_jobs_training_job_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_training_job_id_key UNIQUE (training_job_id);


--
-- Name: uploaded_files uploaded_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.uploaded_files
    ADD CONSTRAINT uploaded_files_pkey PRIMARY KEY (id);


--
-- Name: label_group_items uq_group_label; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_group_items
    ADD CONSTRAINT uq_group_label UNIQUE (group_id, label_id);


--
-- Name: algorithm_hyperparam_presets uq_preset_org_name_algo_task; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_hyperparam_presets
    ADD CONSTRAINT uq_preset_org_name_algo_task UNIQUE (organization_id, name, algorithm, task_type);


--
-- Name: tensor_agent_session_events uq_tensor_events_session_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_session_events
    ADD CONSTRAINT uq_tensor_events_session_seq UNIQUE (session_id, seq);


--
-- Name: tensor_executions uq_tensor_executions_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executions
    ADD CONSTRAINT uq_tensor_executions_key UNIQUE (execution_key);


--
-- Name: tensor_scenarios uq_tensor_scenarios_org_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_scenarios
    ADD CONSTRAINT uq_tensor_scenarios_org_key UNIQUE (organization_id, scenario_key);


--
-- Name: tensorphysics_runs uq_tensorphysics_runs_org_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensorphysics_runs
    ADD CONSTRAINT uq_tensorphysics_runs_org_run UNIQUE (organization_id, run_id);


--
-- Name: repro_verdict_votes uq_vote_group_voter; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repro_verdict_votes
    ADD CONSTRAINT uq_vote_group_voter UNIQUE (vote_group_id, voter_model);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: ar_kind_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ar_kind_idx ON public.asset_registry USING btree (kind);


--
-- Name: ar_repo_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ar_repo_idx ON public.asset_registry USING btree (repo);


--
-- Name: cj_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cj_state_idx ON public.cluster_jobs USING btree (state, submitted_at);


--
-- Name: factory_discussion_org_cursor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_discussion_org_cursor ON public.factory_discussion_messages USING btree (organization_id, id);


--
-- Name: factory_discussion_topic; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_discussion_topic ON public.factory_discussion_messages USING btree (organization_id, topic_id, id);


--
-- Name: factory_learning_candidates_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_learning_candidates_org ON public.factory_learning_candidates USING btree (organization_id, promotion_status, created_at DESC);


--
-- Name: factory_memory_events_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_memory_events_agent ON public.factory_memory_events USING btree (organization_id, agent_id, event_id DESC);


--
-- Name: factory_memory_events_shared; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_memory_events_shared ON public.factory_memory_events USING btree (organization_id, event_id DESC) WHERE ((sensitivity = ANY (ARRAY['organization'::text, 'shared'::text])) AND (verification_status = 'validated'::text));


--
-- Name: factory_mission_task_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX factory_mission_task_key ON public.tensor_dev_missions USING btree (organization_id, task_key) WHERE (task_key IS NOT NULL);


--
-- Name: factory_session_one_active_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX factory_session_one_active_mission ON public.tensor_dev_missions USING btree (agent_session_id) WHERE ((agent_session_id IS NOT NULL) AND ((status)::text = ANY ((ARRAY['claimed'::character varying, 'running'::character varying])::text[])));


--
-- Name: factory_soul_checkpoints_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX factory_soul_checkpoints_agent ON public.factory_soul_checkpoints USING btree (agent_id, created_at DESC);


--
-- Name: fl_domain_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fl_domain_idx ON public.feature_ledger USING btree (domain);


--
-- Name: fl_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fl_status_idx ON public.feature_ledger USING btree (status);


--
-- Name: idx_ab_experiments_organization; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ab_experiments_organization ON public.ab_experiments USING btree (organization_id, created_at DESC);


--
-- Name: idx_ab_experiments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ab_experiments_status ON public.ab_experiments USING btree (status);


--
-- Name: idx_ab_requests_experiment_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ab_requests_experiment_created ON public.ab_experiment_requests USING btree (experiment_id, created_at DESC);


--
-- Name: idx_ab_requests_experiment_feedback; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ab_requests_experiment_feedback ON public.ab_experiment_requests USING btree (experiment_id, feedback_at) WHERE (feedback_at IS NOT NULL);


--
-- Name: idx_automl_selection_records_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_automl_selection_records_created ON public.automl_selection_records USING btree (created_at DESC);


--
-- Name: idx_automl_selection_records_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_automl_selection_records_org ON public.automl_selection_records USING btree (organization_id, created_at DESC);


--
-- Name: idx_cpde_campaign; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cpde_campaign ON public.campaign_plan_dag_edges USING btree (campaign_plan_id);


--
-- Name: idx_cpm_campaign; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cpm_campaign ON public.campaign_plan_milestones USING btree (campaign_plan_id);


--
-- Name: idx_cpn_campaign; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cpn_campaign ON public.campaign_plan_nodes USING btree (campaign_plan_id);


--
-- Name: idx_cpn_milestone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cpn_milestone ON public.campaign_plan_nodes USING btree (campaign_plan_id, milestone);


--
-- Name: idx_credit_settle_pending_reason; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_settle_pending_reason ON public.credit_settle_pending USING btree (reason, grade);


--
-- Name: idx_data_analysis_runs_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_analysis_runs_dataset ON public.data_analysis_runs USING btree (dataset_id);


--
-- Name: idx_data_analysis_runs_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_analysis_runs_org ON public.data_analysis_runs USING btree (organization_id);


--
-- Name: idx_data_cleaning_rules_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_cleaning_rules_org ON public.data_cleaning_rules USING btree (organization_id, created_at DESC);


--
-- Name: idx_data_cleaning_runs_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_cleaning_runs_org ON public.data_cleaning_runs USING btree (organization_id, started_at DESC);


--
-- Name: idx_data_cleaning_runs_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_cleaning_runs_rule ON public.data_cleaning_runs USING btree (rule_id, started_at DESC);


--
-- Name: idx_dataset_governance_reports_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dataset_governance_reports_dataset ON public.dataset_governance_reports USING btree (dataset_id, checked_at DESC);


--
-- Name: idx_dataset_governance_reports_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dataset_governance_reports_org ON public.dataset_governance_reports USING btree (organization_id, checked_at DESC);


--
-- Name: idx_datasets_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_datasets_organization_id ON public.datasets USING btree (organization_id);


--
-- Name: idx_deployments_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deployments_model_id ON public.deployments USING btree (model_id);


--
-- Name: idx_deployments_model_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deployments_model_version_id ON public.deployments USING btree (model_version_id) WHERE (model_version_id IS NOT NULL);


--
-- Name: idx_deployments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deployments_status ON public.deployments USING btree (status);


--
-- Name: idx_eval_cohorts_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_cohorts_repo ON public.eval_cohorts USING btree (repo, cohort_key);


--
-- Name: idx_eval_drain_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_drain_started ON public.eval_drain_runs USING btree (started_at);


--
-- Name: idx_eval_inflight_event_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_inflight_event_at ON public.eval_inflight_events USING btree (event_at);


--
-- Name: idx_eval_inflight_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_inflight_mission ON public.eval_inflight_events USING btree (mission_id);


--
-- Name: idx_feature_engineering_rules_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feature_engineering_rules_org ON public.feature_engineering_rules USING btree (organization_id, created_at DESC);


--
-- Name: idx_feature_values_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feature_values_entity ON public.feature_values USING btree (entity_key);


--
-- Name: idx_feature_values_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feature_values_group ON public.feature_values USING btree (group_id);


--
-- Name: idx_fpm_class; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fpm_class ON public.failure_postmortems USING btree (error_class, status);


--
-- Name: idx_fpm_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fpm_mission ON public.failure_postmortems USING btree (mission_id);


--
-- Name: idx_fpm_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fpm_repo ON public.failure_postmortems USING btree (repo, status);


--
-- Name: idx_fpm_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fpm_stage ON public.failure_postmortems USING btree (failure_stage, status);


--
-- Name: idx_gate_audit_node; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gate_audit_node ON public.gate_release_audit USING btree (node_id, created_at DESC);


--
-- Name: idx_governance_approvals_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_governance_approvals_status ON public.governance_approvals USING btree (status);


--
-- Name: idx_governance_audit_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_governance_audit_model ON public.governance_audit USING btree (model_id);


--
-- Name: idx_governance_reports_output; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_governance_reports_output ON public.dataset_governance_reports USING btree (output_dataset_id, checked_at DESC);


--
-- Name: idx_governance_reports_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_governance_reports_source ON public.dataset_governance_reports USING btree (source_dataset_id, checked_at DESC);


--
-- Name: idx_hyperopt_studies_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hyperopt_studies_status ON public.hyperopt_studies USING btree (status);


--
-- Name: idx_hyperopt_trials_study; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hyperopt_trials_study ON public.hyperopt_trials USING btree (study_id, trial_number);


--
-- Name: idx_intelligent_analysis_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_intelligent_analysis_dataset ON public.intelligent_data_analysis_reports USING btree (dataset_id);


--
-- Name: idx_intelligent_analysis_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_intelligent_analysis_org_created ON public.intelligent_data_analysis_reports USING btree (organization_id, created_at DESC);


--
-- Name: idx_lineage_change_logs_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_change_logs_org ON public.lineage_change_logs USING btree (organization_id);


--
-- Name: idx_lineage_edges_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_edges_active ON public.lineage_edges USING btree (is_active);


--
-- Name: idx_lineage_edges_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_edges_org ON public.lineage_edges USING btree (organization_id);


--
-- Name: idx_lineage_edges_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_edges_source ON public.lineage_edges USING btree (source_id);


--
-- Name: idx_lineage_edges_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_edges_target ON public.lineage_edges USING btree (target_id);


--
-- Name: idx_lineage_edges_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_edges_type ON public.lineage_edges USING btree (edge_type);


--
-- Name: idx_lineage_logs_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_logs_created ON public.lineage_change_logs USING btree (created_at);


--
-- Name: idx_lineage_logs_edge; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_logs_edge ON public.lineage_change_logs USING btree (edge_id);


--
-- Name: idx_lineage_logs_node; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_logs_node ON public.lineage_change_logs USING btree (node_id);


--
-- Name: idx_lineage_logs_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_logs_type ON public.lineage_change_logs USING btree (change_type);


--
-- Name: idx_lineage_nodes_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_active ON public.lineage_nodes USING btree (is_active);


--
-- Name: idx_lineage_nodes_database; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_database ON public.lineage_nodes USING btree (database);


--
-- Name: idx_lineage_nodes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_name ON public.lineage_nodes USING btree (name);


--
-- Name: idx_lineage_nodes_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_org ON public.lineage_nodes USING btree (organization_id);


--
-- Name: idx_lineage_nodes_system; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_system ON public.lineage_nodes USING btree (system);


--
-- Name: idx_lineage_nodes_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lineage_nodes_type ON public.lineage_nodes USING btree (node_type);


--
-- Name: idx_llm_call_traces_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_call_traces_org_created ON public.llm_call_traces USING btree (organization_id, created_at DESC);


--
-- Name: idx_llm_call_traces_source_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_call_traces_source_created ON public.llm_call_traces USING btree (source, created_at DESC);


--
-- Name: idx_llm_prompts_org_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_prompts_org_name ON public.llm_prompt_templates USING btree (organization_id, name, version DESC);


--
-- Name: idx_llm_rag_documents_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_rag_documents_org_created ON public.llm_rag_documents USING btree (organization_id, created_at DESC);


--
-- Name: idx_model_artifacts_model_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_artifacts_model_created ON public.model_artifacts USING btree (model_id, created_at DESC);


--
-- Name: idx_model_artifacts_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_artifacts_org_created ON public.model_artifacts USING btree (organization_id, created_at DESC);


--
-- Name: idx_model_comparison_runs_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_comparison_runs_dataset ON public.model_comparison_runs USING btree (dataset_id, created_at DESC);


--
-- Name: idx_model_comparison_runs_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_comparison_runs_org_created ON public.model_comparison_runs USING btree (organization_id, created_at DESC);


--
-- Name: idx_model_deployments_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_deployments_model_id ON public.model_deployments USING btree (model_id);


--
-- Name: idx_model_eval_results_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_eval_results_dataset ON public.model_evaluation_results USING btree (dataset_id);


--
-- Name: idx_model_eval_results_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_eval_results_model ON public.model_evaluation_results USING btree (model_id);


--
-- Name: idx_model_folders_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_folders_organization_id ON public.model_folders USING btree (organization_id);


--
-- Name: idx_model_training_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_training_jobs_status ON public.model_training_jobs USING btree (status);


--
-- Name: idx_model_versions_mlflow_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_versions_mlflow_run_id ON public.model_versions USING btree (mlflow_run_id) WHERE (mlflow_run_id IS NOT NULL);


--
-- Name: idx_models_folder_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_models_folder_id ON public.models USING btree (folder_id);


--
-- Name: idx_models_mlflow_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_models_mlflow_name ON public.models USING btree (mlflow_name) WHERE (mlflow_name IS NOT NULL);


--
-- Name: idx_models_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_models_organization_id ON public.models USING btree (organization_id);


--
-- Name: idx_models_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_models_project_id ON public.models USING btree (project_id);


--
-- Name: idx_models_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_models_status ON public.models USING btree (status);


--
-- Name: idx_monitoring_logs_drift; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_monitoring_logs_drift ON public.monitoring_logs USING btree (model_id, drift_detected);


--
-- Name: idx_monitoring_logs_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_monitoring_logs_model ON public.monitoring_logs USING btree (model_id);


--
-- Name: idx_outcome_chain_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_chain_mission ON public.outcome_evidence_chains USING btree (mission_id);


--
-- Name: idx_outcome_chain_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_chain_status ON public.outcome_evidence_chains USING btree (chain_status, capped);


--
-- Name: idx_outcome_eval_grade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_eval_grade ON public.outcome_evaluations USING btree (status, grade, e_level);


--
-- Name: idx_outcome_eval_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_eval_mission ON public.outcome_evaluations USING btree (mission_id, status);


--
-- Name: idx_outcome_eval_seat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_eval_seat ON public.outcome_evaluations USING btree (seat_class, status);


--
-- Name: idx_outcome_eval_wave; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outcome_eval_wave ON public.outcome_evaluations USING btree (wave_id, status);


--
-- Name: idx_pm_class; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pm_class ON public.failure_postmortems USING btree (error_class, status);


--
-- Name: idx_pm_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pm_mission ON public.failure_postmortems USING btree (mission_id);


--
-- Name: idx_pm_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pm_stage ON public.failure_postmortems USING btree (failure_stage, status);


--
-- Name: idx_pmr_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pmr_created ON public.postmortem_rules USING btree (created_at);


--
-- Name: idx_pmr_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pmr_stage ON public.postmortem_rules USING btree (failure_stage, error_class);


--
-- Name: idx_pmrr_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pmrr_kind ON public.postmortem_rule_references USING btree (reference_kind);


--
-- Name: idx_pmrr_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pmrr_rule ON public.postmortem_rule_references USING btree (rule_id);


--
-- Name: idx_projects_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_created_at ON public.projects USING btree (created_at);


--
-- Name: idx_projects_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_status ON public.projects USING btree (status);


--
-- Name: idx_quality_anomalies_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_anomalies_dataset ON public.quality_anomalies USING btree (dataset_id);


--
-- Name: idx_quality_anomalies_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_anomalies_severity ON public.quality_anomalies USING btree (severity);


--
-- Name: idx_quality_anomalies_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_anomalies_status ON public.quality_anomalies USING btree (status);


--
-- Name: idx_quality_check_results_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_check_results_dataset ON public.quality_check_results USING btree (dataset_id);


--
-- Name: idx_quality_check_results_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_check_results_rule ON public.quality_check_results USING btree (rule_id);


--
-- Name: idx_quality_check_results_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_check_results_time ON public.quality_check_results USING btree (checked_at);


--
-- Name: idx_quality_rule_revisions_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_rule_revisions_rule ON public.quality_rule_revisions USING btree (rule_id, version DESC);


--
-- Name: idx_quality_rules_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_rules_active ON public.quality_rules USING btree (is_active);


--
-- Name: idx_quality_rules_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quality_rules_type ON public.quality_rules USING btree (rule_type);


--
-- Name: idx_rbac_role_permissions_permission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rbac_role_permissions_permission ON public.rbac_role_permissions USING btree (permission_code);


--
-- Name: idx_realtime_alert_monitor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_realtime_alert_monitor ON public.realtime_sensor_alerts USING btree (monitor_id, status, occurred_at DESC);


--
-- Name: idx_realtime_monitor_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_realtime_monitor_org ON public.realtime_sensor_monitors USING btree (organization_id, created_at DESC);


--
-- Name: idx_rules_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rules_stage ON public.postmortem_rules USING btree (failure_stage, error_class);


--
-- Name: idx_saturation_pulse_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_saturation_pulse_ts ON public.saturation_pulse USING btree (ts DESC);


--
-- Name: idx_training_jobs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_created_at ON public.training_jobs USING btree (created_at);


--
-- Name: idx_training_jobs_mlflow_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_mlflow_run_id ON public.training_jobs USING btree (mlflow_run_id) WHERE (mlflow_run_id IS NOT NULL);


--
-- Name: idx_training_jobs_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_model_id ON public.training_jobs USING btree (model_id);


--
-- Name: idx_training_jobs_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_organization_id ON public.training_jobs USING btree (organization_id);


--
-- Name: idx_training_jobs_registered_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_registered_model_id ON public.training_jobs USING btree (registered_model_id) WHERE (registered_model_id IS NOT NULL);


--
-- Name: idx_training_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_status ON public.training_jobs USING btree (status);


--
-- Name: idx_training_jobs_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_jobs_status_created ON public.training_jobs USING btree (status, created_at DESC);


--
-- Name: idx_uploaded_files_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_category ON public.uploaded_files USING btree (category);


--
-- Name: idx_uploaded_files_extension; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_extension ON public.uploaded_files USING btree (extension);


--
-- Name: idx_uploaded_files_original_filename; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_original_filename ON public.uploaded_files USING btree (original_filename);


--
-- Name: idx_uploaded_files_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_status ON public.uploaded_files USING btree (status);


--
-- Name: idx_uploaded_files_uploaded_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_uploaded_at ON public.uploaded_files USING btree (uploaded_at);


--
-- Name: idx_uploaded_files_uploaded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uploaded_files_uploaded_by ON public.uploaded_files USING btree (uploaded_by);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_is_active ON public.users USING btree (is_active);


--
-- Name: idx_users_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_role ON public.users USING btree (role);


--
-- Name: idx_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_username ON public.users USING btree (username);


--
-- Name: ix_access_audits_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_audits_timestamp ON public.access_audits USING btree ("timestamp");


--
-- Name: ix_annotation_tasks_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_annotation_tasks_assignee_id ON public.annotation_tasks USING btree (assignee_id);


--
-- Name: ix_annotation_tasks_dataset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_annotation_tasks_dataset_id ON public.annotation_tasks USING btree (dataset_id);


--
-- Name: ix_annotations_annotator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_annotations_annotator_id ON public.annotations USING btree (annotator_id);


--
-- Name: ix_annotations_data_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_annotations_data_id ON public.annotations USING btree (data_id);


--
-- Name: ix_annotations_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_annotations_task_id ON public.annotations USING btree (task_id);


--
-- Name: ix_blacklist_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_blacklist_active ON public.tensor_model_blacklist USING btree (organization_id, model) WHERE (released_at IS NULL);


--
-- Name: ix_campaign_events_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_events_plan ON public.campaign_events USING btree (plan_id, created_at DESC);


--
-- Name: ix_campaign_milestones_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_milestones_plan ON public.campaign_milestones USING btree (plan_id, status);


--
-- Name: ix_campaign_nodes_milestone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_nodes_milestone ON public.campaign_nodes USING btree (milestone_id) WHERE (milestone_id IS NOT NULL);


--
-- Name: ix_campaign_nodes_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_nodes_mission ON public.campaign_nodes USING btree (mission_id) WHERE (mission_id IS NOT NULL);


--
-- Name: ix_campaign_nodes_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_nodes_plan ON public.campaign_nodes USING btree (plan_id, status);


--
-- Name: ix_campaign_plans_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_plans_org ON public.campaign_plans USING btree (organization_id, campaign_id);


--
-- Name: ix_campaign_snapshots_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_snapshots_org ON public.campaign_snapshots USING btree (organization_id, snapshot_at DESC);


--
-- Name: ix_campaign_snapshots_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_snapshots_plan ON public.campaign_snapshots USING btree (plan_id, snapshot_at DESC);


--
-- Name: ix_capability_delta_org_cat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_capability_delta_org_cat ON public.tensor_capability_deltas USING btree (organization_id, category);


--
-- Name: ix_components_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_components_name ON public.components USING btree (name);


--
-- Name: ix_coordinator_shadow_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_coordinator_shadow_ts ON public.coordinator_shadow USING btree (ts DESC);


--
-- Name: ix_credit_ledger_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_ledger_mission ON public.credit_ledger USING btree (mission_id);


--
-- Name: ix_credit_ledger_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_ledger_model ON public.tensor_credit_ledger USING btree (organization_id, model, created_at DESC);


--
-- Name: ix_credit_ledger_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_ledger_org ON public.credit_ledger USING btree (organization_id, created_at DESC);


--
-- Name: ix_datasets_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_datasets_organization_id ON public.datasets USING btree (organization_id);


--
-- Name: ix_execution_lineage_records_execution_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_execution_lineage_records_execution_id ON public.execution_lineage_records USING btree (execution_id);


--
-- Name: ix_exploration_budget_track; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exploration_budget_track ON public.exploration_budget USING btree (track);


--
-- Name: ix_exploration_budget_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exploration_budget_worker ON public.exploration_budget USING btree (worker, cycle_id);


--
-- Name: ix_guidance_outcome_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_guidance_outcome_tier ON public.guidance_outcome USING btree (tier, outcome);


--
-- Name: ix_guidance_outcome_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_guidance_outcome_worker ON public.guidance_outcome USING btree (worker);


--
-- Name: ix_guide_tracker_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_guide_tracker_org ON public.tensor_guide_tracker USING btree (organization_id, metric_key, snapshot_at DESC);


--
-- Name: ix_innovation_proposals_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_innovation_proposals_status ON public.innovation_proposals USING btree (status);


--
-- Name: ix_integrity_events_mission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integrity_events_mission_id ON public.integrity_events USING btree (mission_id);


--
-- Name: ix_knowledge_rules_domain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_domain ON public.knowledge_rules USING btree (domain);


--
-- Name: ix_label_group_items_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_label_group_items_group_id ON public.label_group_items USING btree (group_id);


--
-- Name: ix_label_group_items_label_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_label_group_items_label_id ON public.label_group_items USING btree (label_id);


--
-- Name: ix_labels_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_labels_category ON public.labels USING btree (category);


--
-- Name: ix_lifecycle_log_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lifecycle_log_mission ON public.mission_lifecycle_log USING btree (mission_id);


--
-- Name: ix_mech_fix_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mech_fix_org ON public.mechanical_fix_log USING btree (organization_id, created_at DESC);


--
-- Name: ix_metered_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_metered_mission ON public.metered_usage USING btree (mission_id);


--
-- Name: ix_metered_usage_mission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_metered_usage_mission_id ON public.metered_usage USING btree (mission_id);


--
-- Name: ix_missions_heartbeat_stale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_missions_heartbeat_stale ON public.tensor_dev_missions USING btree (status, heartbeat_at) WHERE ((status)::text = ANY ((ARRAY['claimed'::character varying, 'running'::character varying])::text[]));


--
-- Name: ix_missions_orphan_candidate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_missions_orphan_candidate ON public.tensor_dev_missions USING btree (status, created_at) WHERE ((status)::text = 'queued'::text);


--
-- Name: ix_models_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_models_organization_id ON public.models USING btree (organization_id);


--
-- Name: ix_necessity_gate_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_necessity_gate_org ON public.necessity_gate_log USING btree (organization_id, created_at DESC);


--
-- Name: ix_pipeline_runs_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_runs_organization_id ON public.pipeline_runs USING btree (organization_id);


--
-- Name: ix_pipeline_runs_pipeline_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_runs_pipeline_created ON public.pipeline_runs USING btree (pipeline_id, created_at DESC);


--
-- Name: ix_pipelines_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipelines_organization_id ON public.pipelines USING btree (organization_id);


--
-- Name: ix_pipelines_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipelines_owner_id ON public.pipelines USING btree (owner_id);


--
-- Name: ix_repro_verdict_votes_mission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repro_verdict_votes_mission_id ON public.repro_verdict_votes USING btree (mission_id);


--
-- Name: ix_repro_verdict_votes_vote_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repro_verdict_votes_vote_group_id ON public.repro_verdict_votes USING btree (vote_group_id);


--
-- Name: ix_repro_verdicts_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repro_verdicts_mission ON public.tensor_repro_verdicts USING btree (mission_id);


--
-- Name: ix_repro_verdicts_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repro_verdicts_org ON public.tensor_repro_verdicts USING btree (organization_id, created_at DESC);


--
-- Name: ix_repro_verdicts_verdict; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repro_verdicts_verdict ON public.tensor_repro_verdicts USING btree (verdict) WHERE ((verdict)::text = ANY ((ARRAY['cheat'::character varying, 'fail'::character varying])::text[]));


--
-- Name: ix_review_budget_ledger_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_budget_ledger_seq ON public.review_budget_ledger USING btree (seq);


--
-- Name: ix_review_budget_ledger_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_budget_ledger_subject ON public.review_budget_ledger USING btree (subject);


--
-- Name: ix_ship_samples_calc_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ship_samples_calc_type ON public.ship_samples USING btree (calc_type);


--
-- Name: ix_ship_samples_calc_type_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ship_samples_calc_type_created ON public.ship_samples USING btree (calc_type, created_at);


--
-- Name: ix_sidecar_check_results_mission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sidecar_check_results_mission_id ON public.sidecar_check_results USING btree (mission_id);


--
-- Name: ix_sidecar_checker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sidecar_checker ON public.sidecar_check_results USING btree (checker_name, output_type);


--
-- Name: ix_split_verdict_events_vote_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_split_verdict_events_vote_group_id ON public.split_verdict_events USING btree (vote_group_id);


--
-- Name: ix_task_gradient_digest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_gradient_digest ON public.task_gradient USING btree (stats_digest);


--
-- Name: ix_tensor_agent_sessions_execution; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_agent_sessions_execution ON public.tensor_agent_sessions USING btree (execution_id) WHERE (execution_id IS NOT NULL);


--
-- Name: ix_tensor_agent_sessions_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_agent_sessions_org ON public.tensor_agent_sessions USING btree (organization_id, id DESC);


--
-- Name: ix_tensor_approvals_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_approvals_mission ON public.tensor_approval_requests USING btree (mission_id) WHERE (mission_id IS NOT NULL);


--
-- Name: ix_tensor_approvals_org_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_approvals_org_status ON public.tensor_approval_requests USING btree (organization_id, status);


--
-- Name: ix_tensor_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_events_session ON public.tensor_agent_session_events USING btree (session_id, seq);


--
-- Name: ix_tensor_executions_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_executions_active ON public.tensor_executions USING btree (organization_id, id) WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'claimed'::character varying, 'running'::character varying, 'cancel_requested'::character varying])::text[]));


--
-- Name: ix_tensor_executions_scenario; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_executions_scenario ON public.tensor_executions USING btree (scenario_key, created_at DESC);


--
-- Name: ix_tensor_missions_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_org ON public.tensor_dev_missions USING btree (organization_id, created_at DESC);


--
-- Name: ix_tensor_missions_org_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_org_claim ON public.tensor_dev_missions USING btree (organization_id, runner, id) WHERE ((status)::text = 'queued'::text);


--
-- Name: ix_tensor_missions_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_parent ON public.tensor_dev_missions USING btree (parent_mission_id) WHERE (parent_mission_id IS NOT NULL);


--
-- Name: ix_tensor_missions_queued; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_queued ON public.tensor_dev_missions USING btree (id) WHERE ((status)::text = 'queued'::text);


--
-- Name: ix_tensor_missions_queued_runner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_queued_runner ON public.tensor_dev_missions USING btree (runner, id) WHERE ((status)::text = 'queued'::text);


--
-- Name: ix_tensor_missions_report_owed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_missions_report_owed ON public.tensor_dev_missions USING btree (organization_id, finished_at DESC) WHERE ((report_owed = true) AND ((status)::text = 'succeeded'::text));


--
-- Name: ix_tensor_reports_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_reports_org_created ON public.tensor_reports USING btree (organization_id, created_at DESC);


--
-- Name: ix_tensor_reports_qa_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_reports_qa_pending ON public.tensor_reports USING btree (organization_id, created_at DESC) WHERE ((qa_status)::text = 'pending'::text);


--
-- Name: ix_tensor_reports_scenario; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensor_reports_scenario ON public.tensor_reports USING btree (scenario_key) WHERE (scenario_key IS NOT NULL);


--
-- Name: ix_tensorphysics_runs_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensorphysics_runs_org_created ON public.tensorphysics_runs USING btree (organization_id, created_at DESC);


--
-- Name: ix_tensorphysics_runs_org_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tensorphysics_runs_org_status ON public.tensorphysics_runs USING btree (organization_id, status, validation_status);


--
-- Name: ix_training_jobs_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_training_jobs_organization_id ON public.training_jobs USING btree (organization_id);


--
-- Name: ix_users_organization_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_organization_id ON public.users USING btree (organization_id);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: ix_verdict_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_verdict_group ON public.repro_verdict_votes USING btree (vote_group_id, verdict);


--
-- Name: kb_domain_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_domain_idx ON public.knowledge_base USING btree (domain);


--
-- Name: kb_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_type_idx ON public.knowledge_base USING btree (ktype);


--
-- Name: oss_sol_slug_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX oss_sol_slug_idx ON public.oss_solutions USING btree (feature_slug);


--
-- Name: uq_campaign_plans_cid_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_campaign_plans_cid_name ON public.campaign_plans USING btree (campaign_id, name);


--
-- Name: uq_capability_delta_mission; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_capability_delta_mission ON public.tensor_capability_deltas USING btree (mission_id);


--
-- Name: uq_eval_redteam_recheck; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_eval_redteam_recheck ON public.eval_redteam_rechecks USING btree (evaluation_id, run_id);


--
-- Name: uq_evaluation_guidance_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_evaluation_guidance_subject ON public.evaluation_guidance USING btree (wave_id, subject_kind, subject);


--
-- Name: uq_outcome_chain_evaluation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_outcome_chain_evaluation ON public.outcome_evidence_chains USING btree (evaluation_id);


--
-- Name: uq_outcome_eval_artifact_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_outcome_eval_artifact_active ON public.outcome_evaluations USING btree (artifact_id) WHERE (status <> 'rejected'::text);


--
-- Name: uq_reports_mission_md5; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_reports_mission_md5 ON public.tensor_reports USING btree (mission_id, md5(markdown));


--
-- Name: uq_tensor_approvals_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_tensor_approvals_pending ON public.tensor_approval_requests USING btree (scenario_id, kind) WHERE ((status)::text = 'pending'::text);


--
-- Name: ux_coordinator_shadow_beat; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_coordinator_shadow_beat ON public.coordinator_shadow USING btree (beat_id);


--
-- Name: audit_logs audit_logs_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_logs_append_only BEFORE DELETE OR UPDATE ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.audit_logs_append_only_guard();


--
-- Name: factory_memory_events trg_factory_memory_events_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_factory_memory_events_guard BEFORE DELETE OR UPDATE ON public.factory_memory_events FOR EACH ROW EXECUTE FUNCTION public.factory_memory_events_guard();


--
-- Name: tensor_dev_missions trg_ledger_finish_tag; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ledger_finish_tag BEFORE UPDATE ON public.tensor_dev_missions FOR EACH ROW EXECUTE FUNCTION public.tp_ledger_finish_tag();


--
-- Name: tensor_dev_missions trg_ledger_gate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ledger_gate BEFORE INSERT ON public.tensor_dev_missions FOR EACH ROW EXECUTE FUNCTION public.tp_ledger_gate();


--
-- Name: tensor_repro_verdicts trg_repro_verdicts_no_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_repro_verdicts_no_delete BEFORE DELETE ON public.tensor_repro_verdicts FOR EACH ROW EXECUTE FUNCTION public.fn_repro_verdicts_no_modify();


--
-- Name: tensor_repro_verdicts trg_repro_verdicts_no_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_repro_verdicts_no_update BEFORE UPDATE ON public.tensor_repro_verdicts FOR EACH ROW EXECUTE FUNCTION public.fn_repro_verdicts_no_modify();


--
-- Name: tensor_agent_session_events trg_tensor_events_no_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tensor_events_no_update BEFORE DELETE OR UPDATE ON public.tensor_agent_session_events FOR EACH ROW EXECUTE FUNCTION public.tensor_events_forbid_mutation();


--
-- Name: tensorphysics_runs trg_tensorphysics_runs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tensorphysics_runs_updated_at BEFORE UPDATE ON public.tensorphysics_runs FOR EACH ROW EXECUTE FUNCTION public.tensorphysics_runs_set_updated_at();


--
-- Name: ab_experiment_requests ab_experiment_requests_assigned_deployment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_assigned_deployment_id_fkey FOREIGN KEY (assigned_deployment_id) REFERENCES public.deployments(id) ON DELETE SET NULL;


--
-- Name: ab_experiment_requests ab_experiment_requests_assigned_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_assigned_model_id_fkey FOREIGN KEY (assigned_model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: ab_experiment_requests ab_experiment_requests_experiment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_experiment_id_fkey FOREIGN KEY (experiment_id) REFERENCES public.ab_experiments(experiment_id) ON DELETE CASCADE;


--
-- Name: ab_experiment_requests ab_experiment_requests_served_deployment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_served_deployment_id_fkey FOREIGN KEY (served_deployment_id) REFERENCES public.deployments(id) ON DELETE SET NULL;


--
-- Name: ab_experiment_requests ab_experiment_requests_served_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiment_requests
    ADD CONSTRAINT ab_experiment_requests_served_model_id_fkey FOREIGN KEY (served_model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: ab_experiments ab_experiments_deployment_a_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_deployment_a_id_fkey FOREIGN KEY (deployment_a_id) REFERENCES public.deployments(id) ON DELETE SET NULL;


--
-- Name: ab_experiments ab_experiments_deployment_b_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_deployment_b_id_fkey FOREIGN KEY (deployment_b_id) REFERENCES public.deployments(id) ON DELETE SET NULL;


--
-- Name: ab_experiments ab_experiments_model_a_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_model_a_id_fkey FOREIGN KEY (model_a_id) REFERENCES public.models(id) ON DELETE CASCADE;


--
-- Name: ab_experiments ab_experiments_model_b_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_model_b_id_fkey FOREIGN KEY (model_b_id) REFERENCES public.models(id) ON DELETE CASCADE;


--
-- Name: ab_experiments ab_experiments_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_experiments
    ADD CONSTRAINT ab_experiments_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: algorithm_hyperparam_presets algorithm_hyperparam_presets_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.algorithm_hyperparam_presets
    ADD CONSTRAINT algorithm_hyperparam_presets_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: annotations annotations_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.annotation_tasks(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: api_usage_logs api_usage_logs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage_logs
    ADD CONSTRAINT api_usage_logs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: asset_classifications asset_classifications_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_classifications
    ADD CONSTRAINT asset_classifications_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.data_assets(id) ON DELETE CASCADE;


--
-- Name: asset_classifications asset_classifications_classification_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_classifications
    ADD CONSTRAINT asset_classifications_classification_id_fkey FOREIGN KEY (classification_id) REFERENCES public.data_classifications(id) ON DELETE CASCADE;


--
-- Name: asset_metadata asset_metadata_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_metadata
    ADD CONSTRAINT asset_metadata_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.data_assets(id) ON DELETE CASCADE;


--
-- Name: asset_tags asset_tags_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags
    ADD CONSTRAINT asset_tags_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.data_assets(id) ON DELETE CASCADE;


--
-- Name: asset_tags asset_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags
    ADD CONSTRAINT asset_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.data_tags(id) ON DELETE CASCADE;


--
-- Name: automl_selection_records automl_selection_records_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.automl_selection_records
    ADD CONSTRAINT automl_selection_records_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: billing_records billing_records_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_records
    ADD CONSTRAINT billing_records_component_id_fkey FOREIGN KEY (component_id) REFERENCES public.components(id);


--
-- Name: billing_records billing_records_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_records
    ADD CONSTRAINT billing_records_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: campaign_events campaign_events_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_events
    ADD CONSTRAINT campaign_events_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: campaign_events campaign_events_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_events
    ADD CONSTRAINT campaign_events_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_milestones campaign_milestones_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_milestones
    ADD CONSTRAINT campaign_milestones_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_nodes campaign_nodes_milestone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_nodes
    ADD CONSTRAINT campaign_nodes_milestone_id_fkey FOREIGN KEY (milestone_id) REFERENCES public.campaign_milestones(id) ON DELETE SET NULL;


--
-- Name: campaign_nodes campaign_nodes_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_nodes
    ADD CONSTRAINT campaign_nodes_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: campaign_nodes campaign_nodes_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_nodes
    ADD CONSTRAINT campaign_nodes_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_plan_dag_edges campaign_plan_dag_edges_campaign_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_dag_edges
    ADD CONSTRAINT campaign_plan_dag_edges_campaign_plan_id_fkey FOREIGN KEY (campaign_plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_plan_milestones campaign_plan_milestones_campaign_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_milestones
    ADD CONSTRAINT campaign_plan_milestones_campaign_plan_id_fkey FOREIGN KEY (campaign_plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_plan_nodes campaign_plan_nodes_campaign_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plan_nodes
    ADD CONSTRAINT campaign_plan_nodes_campaign_plan_id_fkey FOREIGN KEY (campaign_plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: campaign_plans campaign_plans_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_plans
    ADD CONSTRAINT campaign_plans_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: campaign_snapshots campaign_snapshots_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_snapshots
    ADD CONSTRAINT campaign_snapshots_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: campaign_snapshots campaign_snapshots_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_snapshots
    ADD CONSTRAINT campaign_snapshots_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.campaign_plans(id) ON DELETE CASCADE;


--
-- Name: components components_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.components
    ADD CONSTRAINT components_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.projects(id);


--
-- Name: credit_ledger credit_ledger_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_ledger
    ADD CONSTRAINT credit_ledger_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE CASCADE;


--
-- Name: credit_ledger credit_ledger_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_ledger
    ADD CONSTRAINT credit_ledger_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: credit_ledger credit_ledger_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_ledger
    ADD CONSTRAINT credit_ledger_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.tensor_agent_sessions(id) ON DELETE SET NULL;


--
-- Name: data_analysis_runs data_analysis_runs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_analysis_runs
    ADD CONSTRAINT data_analysis_runs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE CASCADE;


--
-- Name: data_analysis_runs data_analysis_runs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_analysis_runs
    ADD CONSTRAINT data_analysis_runs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: data_asset_lineage data_asset_lineage_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_asset_lineage
    ADD CONSTRAINT data_asset_lineage_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.data_assets(id) ON DELETE CASCADE;


--
-- Name: data_asset_lineage data_asset_lineage_target_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_asset_lineage
    ADD CONSTRAINT data_asset_lineage_target_id_fkey FOREIGN KEY (target_id) REFERENCES public.data_assets(id) ON DELETE CASCADE;


--
-- Name: data_classifications data_classifications_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_classifications
    ADD CONSTRAINT data_classifications_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.data_classifications(id);


--
-- Name: data_cleaning_rules data_cleaning_rules_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_rules
    ADD CONSTRAINT data_cleaning_rules_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: data_cleaning_runs data_cleaning_runs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_runs
    ADD CONSTRAINT data_cleaning_runs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: data_cleaning_runs data_cleaning_runs_output_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_runs
    ADD CONSTRAINT data_cleaning_runs_output_dataset_id_fkey FOREIGN KEY (output_dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: data_cleaning_runs data_cleaning_runs_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_runs
    ADD CONSTRAINT data_cleaning_runs_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.data_cleaning_rules(id) ON DELETE SET NULL;


--
-- Name: data_cleaning_runs data_cleaning_runs_source_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_cleaning_runs
    ADD CONSTRAINT data_cleaning_runs_source_dataset_id_fkey FOREIGN KEY (source_dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: data_security_policies data_security_policies_access_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_security_policies
    ADD CONSTRAINT data_security_policies_access_policy_id_fkey FOREIGN KEY (access_policy_id) REFERENCES public.access_policies(id) ON DELETE SET NULL;


--
-- Name: data_security_policies data_security_policies_masking_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_security_policies
    ADD CONSTRAINT data_security_policies_masking_rule_id_fkey FOREIGN KEY (masking_rule_id) REFERENCES public.masking_rules(id) ON DELETE SET NULL;


--
-- Name: data_validation_results data_validation_results_standard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_validation_results
    ADD CONSTRAINT data_validation_results_standard_id_fkey FOREIGN KEY (standard_id) REFERENCES public.data_standards(id) ON DELETE CASCADE;


--
-- Name: dataset_governance_reports dataset_governance_reports_cleaning_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_cleaning_run_id_fkey FOREIGN KEY (cleaning_run_id) REFERENCES public.data_cleaning_runs(id) ON DELETE SET NULL;


--
-- Name: dataset_governance_reports dataset_governance_reports_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: dataset_governance_reports dataset_governance_reports_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: dataset_governance_reports dataset_governance_reports_output_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_output_dataset_id_fkey FOREIGN KEY (output_dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: dataset_governance_reports dataset_governance_reports_source_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_governance_reports
    ADD CONSTRAINT dataset_governance_reports_source_dataset_id_fkey FOREIGN KEY (source_dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: datasets datasets_governed_from_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_governed_from_fkey FOREIGN KEY (governed_from) REFERENCES public.datasets(id);


--
-- Name: datasets datasets_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: datasets datasets_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: deployments deployments_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT deployments_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE CASCADE;


--
-- Name: deployments deployments_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT deployments_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id) ON DELETE SET NULL;


--
-- Name: devices devices_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: factory_agent_identities factory_agent_identities_legacy_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_agent_identities
    ADD CONSTRAINT factory_agent_identities_legacy_session_id_fkey FOREIGN KEY (legacy_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_agent_identities factory_agent_identities_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_agent_identities
    ADD CONSTRAINT factory_agent_identities_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_agent_identities factory_agent_identities_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_agent_identities
    ADD CONSTRAINT factory_agent_identities_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id);


--
-- Name: factory_community_positions factory_community_positions_agent_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_positions
    ADD CONSTRAINT factory_community_positions_agent_session_id_fkey FOREIGN KEY (agent_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_community_positions factory_community_positions_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_positions
    ADD CONSTRAINT factory_community_positions_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.factory_community_topics(id);


--
-- Name: factory_community_topics factory_community_topics_author_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_topics
    ADD CONSTRAINT factory_community_topics_author_session_id_fkey FOREIGN KEY (author_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_community_topics factory_community_topics_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_topics
    ADD CONSTRAINT factory_community_topics_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id);


--
-- Name: factory_community_topics factory_community_topics_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_community_topics
    ADD CONSTRAINT factory_community_topics_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_discussion_messages factory_discussion_messages_author_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_author_session_id_fkey FOREIGN KEY (author_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_discussion_messages factory_discussion_messages_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id);


--
-- Name: factory_discussion_messages factory_discussion_messages_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_discussion_messages factory_discussion_messages_recipient_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_recipient_session_id_fkey FOREIGN KEY (recipient_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_discussion_messages factory_discussion_messages_reply_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_reply_to_fkey FOREIGN KEY (reply_to) REFERENCES public.factory_discussion_messages(id);


--
-- Name: factory_discussion_messages factory_discussion_messages_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_discussion_messages
    ADD CONSTRAINT factory_discussion_messages_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.factory_community_topics(id);


--
-- Name: factory_evidence_records factory_evidence_records_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_evidence_records
    ADD CONSTRAINT factory_evidence_records_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_evidence_records factory_evidence_records_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_evidence_records
    ADD CONSTRAINT factory_evidence_records_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_evidence_records factory_evidence_records_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_evidence_records
    ADD CONSTRAINT factory_evidence_records_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.factory_soul_sessions(session_id) ON DELETE SET NULL;


--
-- Name: factory_evidence_records factory_evidence_records_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_evidence_records
    ADD CONSTRAINT factory_evidence_records_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: factory_harness_incidents factory_harness_incidents_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents
    ADD CONSTRAINT factory_harness_incidents_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.factory_strategy_campaigns(id);


--
-- Name: factory_harness_incidents factory_harness_incidents_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents
    ADD CONSTRAINT factory_harness_incidents_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: factory_harness_incidents factory_harness_incidents_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_harness_incidents
    ADD CONSTRAINT factory_harness_incidents_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_learning_candidates factory_learning_candidates_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_learning_candidates
    ADD CONSTRAINT factory_learning_candidates_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_learning_candidates factory_learning_candidates_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_learning_candidates
    ADD CONSTRAINT factory_learning_candidates_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: factory_learning_candidates factory_learning_candidates_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_learning_candidates
    ADD CONSTRAINT factory_learning_candidates_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_memory_events factory_memory_events_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_memory_events factory_memory_events_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_memory_events factory_memory_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.factory_soul_sessions(session_id) ON DELETE SET NULL;


--
-- Name: factory_memory_events factory_memory_events_superseded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES public.factory_memory_events(event_id);


--
-- Name: factory_memory_events factory_memory_events_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_memory_events
    ADD CONSTRAINT factory_memory_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: factory_resident_workers factory_resident_workers_current_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_resident_workers
    ADD CONSTRAINT factory_resident_workers_current_mission_id_fkey FOREIGN KEY (current_mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: factory_resident_workers factory_resident_workers_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_resident_workers
    ADD CONSTRAINT factory_resident_workers_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_resident_workers factory_resident_workers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_resident_workers
    ADD CONSTRAINT factory_resident_workers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: factory_soul_binding_history factory_soul_binding_history_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_binding_history
    ADD CONSTRAINT factory_soul_binding_history_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_soul_binding_history factory_soul_binding_history_binding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_binding_history
    ADD CONSTRAINT factory_soul_binding_history_binding_id_fkey FOREIGN KEY (binding_id) REFERENCES public.factory_soul_model_bindings(binding_id);


--
-- Name: factory_soul_binding_history factory_soul_binding_history_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_binding_history
    ADD CONSTRAINT factory_soul_binding_history_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.factory_soul_sessions(session_id) ON DELETE CASCADE;


--
-- Name: factory_soul_binding_history factory_soul_binding_history_switched_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_binding_history
    ADD CONSTRAINT factory_soul_binding_history_switched_by_fkey FOREIGN KEY (switched_by) REFERENCES public.users(id);


--
-- Name: factory_soul_checkpoints factory_soul_checkpoints_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_checkpoints
    ADD CONSTRAINT factory_soul_checkpoints_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_soul_checkpoints factory_soul_checkpoints_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_checkpoints
    ADD CONSTRAINT factory_soul_checkpoints_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.factory_soul_sessions(session_id) ON DELETE CASCADE;


--
-- Name: factory_soul_legacy_sessions factory_soul_legacy_sessions_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_legacy_sessions
    ADD CONSTRAINT factory_soul_legacy_sessions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_soul_legacy_sessions factory_soul_legacy_sessions_agent_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_legacy_sessions
    ADD CONSTRAINT factory_soul_legacy_sessions_agent_session_id_fkey FOREIGN KEY (agent_session_id) REFERENCES public.factory_resident_workers(agent_session_id) ON DELETE CASCADE;


--
-- Name: factory_soul_model_bindings factory_soul_model_bindings_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_model_bindings
    ADD CONSTRAINT factory_soul_model_bindings_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: factory_soul_model_bindings factory_soul_model_bindings_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_model_bindings
    ADD CONSTRAINT factory_soul_model_bindings_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_soul_sessions factory_soul_sessions_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.factory_agent_identities(agent_id);


--
-- Name: factory_soul_sessions factory_soul_sessions_current_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_current_task_id_fkey FOREIGN KEY (current_task_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: factory_soul_sessions factory_soul_sessions_model_binding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_model_binding_id_fkey FOREIGN KEY (model_binding_id) REFERENCES public.factory_soul_model_bindings(binding_id);


--
-- Name: factory_soul_sessions factory_soul_sessions_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_soul_sessions
    ADD CONSTRAINT factory_soul_sessions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: factory_strategy_campaigns factory_strategy_campaigns_agent_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns
    ADD CONSTRAINT factory_strategy_campaigns_agent_session_id_fkey FOREIGN KEY (agent_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: factory_strategy_campaigns factory_strategy_campaigns_last_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns
    ADD CONSTRAINT factory_strategy_campaigns_last_mission_id_fkey FOREIGN KEY (last_mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: factory_strategy_campaigns factory_strategy_campaigns_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_strategy_campaigns
    ADD CONSTRAINT factory_strategy_campaigns_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: failure_postmortems failure_postmortems_hypothesis_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_postmortems
    ADD CONSTRAINT failure_postmortems_hypothesis_id_fkey FOREIGN KEY (hypothesis_id) REFERENCES public.hypothesis_registry(hypothesis_id);


--
-- Name: feature_engineering_rules feature_engineering_rules_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_engineering_rules
    ADD CONSTRAINT feature_engineering_rules_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: feature_values feature_values_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_values
    ADD CONSTRAINT feature_values_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.feature_groups(group_id) ON DELETE CASCADE;


--
-- Name: deployments fk_deployments_model_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT fk_deployments_model_version FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id) ON DELETE SET NULL;


--
-- Name: pipeline_runs fk_pipeline_runs_organization; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT fk_pipeline_runs_organization FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: pipelines fk_pipelines_organization; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT fk_pipelines_organization FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: training_jobs fk_training_jobs_registered_model; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT fk_training_jobs_registered_model FOREIGN KEY (registered_model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: users fk_users_organization; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_organization FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: governance_approvals governance_approvals_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_approvals
    ADD CONSTRAINT governance_approvals_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: governance_audit governance_audit_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.governance_audit
    ADD CONSTRAINT governance_audit_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: hyperopt_studies hyperopt_studies_best_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_studies
    ADD CONSTRAINT hyperopt_studies_best_model_id_fkey FOREIGN KEY (best_model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: hyperopt_studies hyperopt_studies_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_studies
    ADD CONSTRAINT hyperopt_studies_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: hyperopt_trials hyperopt_trials_study_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hyperopt_trials
    ADD CONSTRAINT hyperopt_trials_study_id_fkey FOREIGN KEY (study_id) REFERENCES public.hyperopt_studies(study_id) ON DELETE CASCADE;


--
-- Name: intelligent_data_analysis_reports intelligent_data_analysis_reports_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.intelligent_data_analysis_reports
    ADD CONSTRAINT intelligent_data_analysis_reports_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: intelligent_data_analysis_reports intelligent_data_analysis_reports_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.intelligent_data_analysis_reports
    ADD CONSTRAINT intelligent_data_analysis_reports_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: label_group_items label_group_items_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_group_items
    ADD CONSTRAINT label_group_items_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.label_groups(id) ON DELETE CASCADE;


--
-- Name: label_group_items label_group_items_label_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_group_items
    ADD CONSTRAINT label_group_items_label_id_fkey FOREIGN KEY (label_id) REFERENCES public.labels(id) ON DELETE CASCADE;


--
-- Name: lineage_change_logs lineage_change_logs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_change_logs
    ADD CONSTRAINT lineage_change_logs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: lineage_edges lineage_edges_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_edges
    ADD CONSTRAINT lineage_edges_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: lineage_edges lineage_edges_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_edges
    ADD CONSTRAINT lineage_edges_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.lineage_nodes(id) ON DELETE CASCADE;


--
-- Name: lineage_edges lineage_edges_target_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_edges
    ADD CONSTRAINT lineage_edges_target_id_fkey FOREIGN KEY (target_id) REFERENCES public.lineage_nodes(id) ON DELETE CASCADE;


--
-- Name: lineage_nodes lineage_nodes_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lineage_nodes
    ADD CONSTRAINT lineage_nodes_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: llm_call_traces llm_call_traces_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_call_traces
    ADD CONSTRAINT llm_call_traces_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: llm_call_traces llm_call_traces_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_call_traces
    ADD CONSTRAINT llm_call_traces_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: llm_prompt_templates llm_prompt_templates_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_prompt_templates
    ADD CONSTRAINT llm_prompt_templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: llm_prompt_templates llm_prompt_templates_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_prompt_templates
    ADD CONSTRAINT llm_prompt_templates_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: llm_rag_documents llm_rag_documents_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_rag_documents
    ADD CONSTRAINT llm_rag_documents_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: llm_rag_documents llm_rag_documents_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_rag_documents
    ADD CONSTRAINT llm_rag_documents_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: mechanical_fix_log mechanical_fix_log_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mechanical_fix_log
    ADD CONSTRAINT mechanical_fix_log_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: mechanical_fix_log mechanical_fix_log_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mechanical_fix_log
    ADD CONSTRAINT mechanical_fix_log_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: mission_lifecycle_log mission_lifecycle_log_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id);


--
-- Name: mission_lifecycle_log mission_lifecycle_log_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_lifecycle_log
    ADD CONSTRAINT mission_lifecycle_log_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE CASCADE;


--
-- Name: model_artifacts model_artifacts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: model_artifacts model_artifacts_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE CASCADE;


--
-- Name: model_artifacts model_artifacts_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id) ON DELETE SET NULL;


--
-- Name: model_artifacts model_artifacts_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_artifacts
    ADD CONSTRAINT model_artifacts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: model_comparison_runs model_comparison_runs_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs
    ADD CONSTRAINT model_comparison_runs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: model_comparison_runs model_comparison_runs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs
    ADD CONSTRAINT model_comparison_runs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: model_comparison_runs model_comparison_runs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs
    ADD CONSTRAINT model_comparison_runs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: model_comparison_runs model_comparison_runs_winner_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_comparison_runs
    ADD CONSTRAINT model_comparison_runs_winner_model_id_fkey FOREIGN KEY (winner_model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: model_deployments model_deployments_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_deployments
    ADD CONSTRAINT model_deployments_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE CASCADE;


--
-- Name: model_evaluation_results model_evaluation_results_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_evaluation_results
    ADD CONSTRAINT model_evaluation_results_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: model_evaluation_results model_evaluation_results_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_evaluation_results
    ADD CONSTRAINT model_evaluation_results_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: model_folders model_folders_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_folders
    ADD CONSTRAINT model_folders_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: model_versions model_versions_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id);


--
-- Name: models models_folder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.model_folders(id) ON DELETE SET NULL;


--
-- Name: models models_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: models models_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: monitoring_logs monitoring_logs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.monitoring_logs
    ADD CONSTRAINT monitoring_logs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON DELETE SET NULL;


--
-- Name: monitoring_logs monitoring_logs_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.monitoring_logs
    ADD CONSTRAINT monitoring_logs_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id) ON DELETE SET NULL;


--
-- Name: necessity_gate_log necessity_gate_log_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.necessity_gate_log
    ADD CONSTRAINT necessity_gate_log_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: pipeline_runs pipeline_runs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: pipeline_runs pipeline_runs_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES public.pipelines(id);


--
-- Name: pipeline_task_runs pipeline_task_runs_pipeline_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_task_runs
    ADD CONSTRAINT pipeline_task_runs_pipeline_run_id_fkey FOREIGN KEY (pipeline_run_id) REFERENCES public.pipeline_runs(id);


--
-- Name: pipeline_task_runs pipeline_task_runs_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_task_runs
    ADD CONSTRAINT pipeline_task_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.pipeline_tasks(id);


--
-- Name: pipeline_tasks pipeline_tasks_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_tasks
    ADD CONSTRAINT pipeline_tasks_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES public.pipelines(id);


--
-- Name: pipelines pipelines_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: pipelines pipelines_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: policy_rules policy_rules_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules
    ADD CONSTRAINT policy_rules_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.access_policies(id) ON DELETE CASCADE;


--
-- Name: postmortem_rule_references postmortem_rule_references_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rule_references
    ADD CONSTRAINT postmortem_rule_references_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.postmortem_rules(rule_id);


--
-- Name: postmortem_rules postmortem_rules_postmortem_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postmortem_rules
    ADD CONSTRAINT postmortem_rules_postmortem_id_fkey FOREIGN KEY (postmortem_id) REFERENCES public.failure_postmortems(postmortem_id);


--
-- Name: pricing_configs pricing_configs_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_configs
    ADD CONSTRAINT pricing_configs_component_id_fkey FOREIGN KEY (component_id) REFERENCES public.components(id);


--
-- Name: projects projects_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: quality_rule_revisions quality_rule_revisions_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_rule_revisions
    ADD CONSTRAINT quality_rule_revisions_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.quality_rules(id) ON DELETE CASCADE;


--
-- Name: rbac_role_permissions rbac_role_permissions_permission_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rbac_role_permissions
    ADD CONSTRAINT rbac_role_permissions_permission_code_fkey FOREIGN KEY (permission_code) REFERENCES public.rbac_permissions(code) ON DELETE CASCADE;


--
-- Name: rbac_role_permissions rbac_role_permissions_role_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rbac_role_permissions
    ADD CONSTRAINT rbac_role_permissions_role_code_fkey FOREIGN KEY (role_code) REFERENCES public.rbac_roles(code) ON DELETE CASCADE;


--
-- Name: realtime_sensor_alerts realtime_sensor_alerts_monitor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_sensor_alerts
    ADD CONSTRAINT realtime_sensor_alerts_monitor_id_fkey FOREIGN KEY (monitor_id) REFERENCES public.realtime_sensor_monitors(id) ON DELETE CASCADE;


--
-- Name: standard_fields standard_fields_standard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.standard_fields
    ADD CONSTRAINT standard_fields_standard_id_fkey FOREIGN KEY (standard_id) REFERENCES public.data_standards(id) ON DELETE CASCADE;


--
-- Name: tensor_agent_session_events tensor_agent_session_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_session_events
    ADD CONSTRAINT tensor_agent_session_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.tensor_agent_sessions(id) ON DELETE CASCADE;


--
-- Name: tensor_agent_sessions tensor_agent_sessions_execution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_sessions
    ADD CONSTRAINT tensor_agent_sessions_execution_id_fkey FOREIGN KEY (execution_id) REFERENCES public.tensor_executions(id) ON DELETE SET NULL;


--
-- Name: tensor_agent_sessions tensor_agent_sessions_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_sessions
    ADD CONSTRAINT tensor_agent_sessions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tensor_agent_sessions tensor_agent_sessions_started_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_agent_sessions
    ADD CONSTRAINT tensor_agent_sessions_started_by_fkey FOREIGN KEY (started_by) REFERENCES public.users(id);


--
-- Name: tensor_approval_requests tensor_approval_requests_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: tensor_approval_requests tensor_approval_requests_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: tensor_approval_requests tensor_approval_requests_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tensor_approval_requests tensor_approval_requests_scenario_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_approval_requests
    ADD CONSTRAINT tensor_approval_requests_scenario_id_fkey FOREIGN KEY (scenario_id) REFERENCES public.tensor_scenarios(id);


--
-- Name: tensor_capability_deltas tensor_capability_deltas_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_capability_deltas
    ADD CONSTRAINT tensor_capability_deltas_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: tensor_credit_ledger tensor_credit_ledger_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_credit_ledger
    ADD CONSTRAINT tensor_credit_ledger_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: tensor_dev_missions tensor_dev_missions_agent_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_agent_session_id_fkey FOREIGN KEY (agent_session_id) REFERENCES public.factory_resident_workers(agent_session_id);


--
-- Name: tensor_dev_missions tensor_dev_missions_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.factory_strategy_campaigns(id);


--
-- Name: tensor_dev_missions tensor_dev_missions_claimed_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_claimed_user_id_fkey FOREIGN KEY (claimed_user_id) REFERENCES public.users(id);


--
-- Name: tensor_dev_missions tensor_dev_missions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: tensor_dev_missions tensor_dev_missions_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tensor_dev_missions tensor_dev_missions_parent_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_parent_mission_id_fkey FOREIGN KEY (parent_mission_id) REFERENCES public.tensor_dev_missions(id) ON DELETE SET NULL;


--
-- Name: tensor_dev_missions tensor_dev_missions_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.tensor_reports(id) ON DELETE SET NULL;


--
-- Name: tensor_dev_missions tensor_dev_missions_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.tensor_agent_sessions(id) ON DELETE SET NULL;


--
-- Name: tensor_dev_missions tensor_dev_missions_suspended_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_dev_missions
    ADD CONSTRAINT tensor_dev_missions_suspended_by_fkey FOREIGN KEY (suspended_by) REFERENCES public.users(id);


--
-- Name: tensor_executions tensor_executions_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executions
    ADD CONSTRAINT tensor_executions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tensor_executions tensor_executions_triggered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_executions
    ADD CONSTRAINT tensor_executions_triggered_by_fkey FOREIGN KEY (triggered_by) REFERENCES public.users(id);


--
-- Name: tensor_repro_verdicts tensor_repro_verdicts_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_repro_verdicts
    ADD CONSTRAINT tensor_repro_verdicts_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.tensor_dev_missions(id);


--
-- Name: tensor_scenarios tensor_scenarios_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_scenarios
    ADD CONSTRAINT tensor_scenarios_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tensor_workspace_state tensor_workspace_state_executor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tensor_workspace_state
    ADD CONSTRAINT tensor_workspace_state_executor_id_fkey FOREIGN KEY (executor_id) REFERENCES public.tensor_executors(executor_id);


--
-- Name: training_jobs training_jobs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id);


--
-- Name: training_jobs training_jobs_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(id);


--
-- Name: training_jobs training_jobs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: training_jobs training_jobs_registered_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_jobs
    ADD CONSTRAINT training_jobs_registered_model_id_fkey FOREIGN KEY (registered_model_id) REFERENCES public.models(id);


--
-- Name: users users_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: -
--

GRANT ALL ON SCHEMA public TO foundation;


--
-- Name: FUNCTION factory_memory_events_guard(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.factory_memory_events_guard() TO foundation;


--
-- Name: FUNCTION tensor_events_forbid_mutation(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.tensor_events_forbid_mutation() TO foundation;


--
-- Name: FUNCTION tensorphysics_runs_set_updated_at(); Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON FUNCTION public.tensorphysics_runs_set_updated_at() TO foundation;


--
-- Name: TABLE ab_experiment_requests; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.ab_experiment_requests TO foundation;


--
-- Name: SEQUENCE ab_experiment_requests_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.ab_experiment_requests_id_seq TO foundation;


--
-- Name: TABLE ab_experiments; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.ab_experiments TO foundation;


--
-- Name: SEQUENCE ab_experiments_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.ab_experiments_id_seq TO foundation;


--
-- Name: TABLE agent_directory; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.agent_directory TO foundation;


--
-- Name: TABLE asset_registry; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.asset_registry TO foundation;


--
-- Name: SEQUENCE asset_registry_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.asset_registry_id_seq TO foundation;


--
-- Name: TABLE audit_logs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.audit_logs TO foundation;


--
-- Name: SEQUENCE audit_logs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.audit_logs_id_seq TO foundation;


--
-- Name: TABLE automl_selection_records; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.automl_selection_records TO foundation;


--
-- Name: SEQUENCE automl_selection_records_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.automl_selection_records_id_seq TO foundation;


--
-- Name: TABLE campaign_events; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.campaign_events TO foundation;


--
-- Name: SEQUENCE campaign_events_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.campaign_events_id_seq TO foundation;


--
-- Name: TABLE campaign_milestones; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.campaign_milestones TO foundation;


--
-- Name: SEQUENCE campaign_milestones_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.campaign_milestones_id_seq TO foundation;


--
-- Name: TABLE campaign_nodes; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.campaign_nodes TO foundation;


--
-- Name: SEQUENCE campaign_nodes_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.campaign_nodes_id_seq TO foundation;


--
-- Name: TABLE campaign_snapshots; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.campaign_snapshots TO foundation;


--
-- Name: SEQUENCE campaign_snapshots_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.campaign_snapshots_id_seq TO foundation;


--
-- Name: TABLE chronicle_snapshots; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.chronicle_snapshots TO foundation;


--
-- Name: TABLE cluster_jobs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.cluster_jobs TO foundation;


--
-- Name: SEQUENCE cluster_jobs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.cluster_jobs_id_seq TO foundation;


--
-- Name: TABLE coordinator_shadow; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.coordinator_shadow TO foundation;


--
-- Name: TABLE council_ledger_entries; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.council_ledger_entries TO foundation;


--
-- Name: SEQUENCE council_ledger_entries_seq_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.council_ledger_entries_seq_seq TO foundation;


--
-- Name: TABLE council_ledger_entries_test; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.council_ledger_entries_test TO foundation;


--
-- Name: SEQUENCE council_ledger_entries_test_seq_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.council_ledger_entries_test_seq_seq TO foundation;


--
-- Name: TABLE credit_ledger; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.credit_ledger TO foundation;


--
-- Name: SEQUENCE credit_ledger_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.credit_ledger_id_seq TO foundation;


--
-- Name: TABLE credit_settle_pending; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.credit_settle_pending TO foundation;


--
-- Name: TABLE credit_settle_pending_v; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.credit_settle_pending_v TO foundation;


--
-- Name: TABLE tensor_credit_ledger; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_credit_ledger TO foundation;


--
-- Name: TABLE credit_tiers; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.credit_tiers TO foundation;


--
-- Name: TABLE data_analysis_runs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.data_analysis_runs TO foundation;


--
-- Name: SEQUENCE data_analysis_runs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.data_analysis_runs_id_seq TO foundation;


--
-- Name: TABLE data_cleaning_rules; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.data_cleaning_rules TO foundation;


--
-- Name: SEQUENCE data_cleaning_rules_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.data_cleaning_rules_id_seq TO foundation;


--
-- Name: TABLE data_cleaning_runs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.data_cleaning_runs TO foundation;


--
-- Name: TABLE dataset_governance_reports; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.dataset_governance_reports TO foundation;


--
-- Name: TABLE deployments; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.deployments TO foundation;


--
-- Name: SEQUENCE deployments_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.deployments_id_seq TO foundation;


--
-- Name: TABLE eval_cohorts; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.eval_cohorts TO foundation;


--
-- Name: TABLE eval_redteam_rechecks; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.eval_redteam_rechecks TO foundation;


--
-- Name: TABLE evaluation_guidance; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.evaluation_guidance TO foundation;


--
-- Name: SEQUENCE evaluation_guidance_guidance_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.evaluation_guidance_guidance_id_seq TO foundation;


--
-- Name: TABLE evaluation_waves; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.evaluation_waves TO foundation;


--
-- Name: TABLE factory_agent_identities; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_agent_identities TO foundation;


--
-- Name: TABLE factory_community_positions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_community_positions TO foundation;


--
-- Name: TABLE factory_community_topics; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_community_topics TO foundation;


--
-- Name: SEQUENCE factory_community_topics_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.factory_community_topics_id_seq TO foundation;


--
-- Name: TABLE factory_discussion_messages; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_discussion_messages TO foundation;


--
-- Name: SEQUENCE factory_discussion_messages_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.factory_discussion_messages_id_seq TO foundation;


--
-- Name: TABLE factory_evidence_records; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_evidence_records TO foundation;


--
-- Name: TABLE factory_harness_incidents; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_harness_incidents TO foundation;


--
-- Name: SEQUENCE factory_harness_incidents_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.factory_harness_incidents_id_seq TO foundation;


--
-- Name: TABLE factory_learning_candidates; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_learning_candidates TO foundation;


--
-- Name: TABLE factory_memory_events; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_memory_events TO foundation;


--
-- Name: SEQUENCE factory_memory_events_event_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.factory_memory_events_event_id_seq TO foundation;


--
-- Name: TABLE factory_resident_workers; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_resident_workers TO foundation;


--
-- Name: TABLE factory_soul_binding_history; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_binding_history TO foundation;


--
-- Name: TABLE factory_soul_checkpoints; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_checkpoints TO foundation;


--
-- Name: TABLE factory_soul_legacy_sessions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_legacy_sessions TO foundation;


--
-- Name: TABLE factory_soul_model_bindings; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_model_bindings TO foundation;


--
-- Name: TABLE factory_soul_schema_versions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_schema_versions TO foundation;


--
-- Name: TABLE factory_soul_sessions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_soul_sessions TO foundation;


--
-- Name: TABLE factory_strategy_campaigns; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.factory_strategy_campaigns TO foundation;


--
-- Name: SEQUENCE factory_strategy_campaigns_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.factory_strategy_campaigns_id_seq TO foundation;


--
-- Name: TABLE feature_engineering_rules; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.feature_engineering_rules TO foundation;


--
-- Name: SEQUENCE feature_engineering_rules_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.feature_engineering_rules_id_seq TO foundation;


--
-- Name: TABLE feature_groups; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.feature_groups TO foundation;


--
-- Name: SEQUENCE feature_groups_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.feature_groups_id_seq TO foundation;


--
-- Name: TABLE feature_ledger; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.feature_ledger TO foundation;


--
-- Name: SEQUENCE feature_ledger_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.feature_ledger_id_seq TO foundation;


--
-- Name: TABLE feature_values; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.feature_values TO foundation;


--
-- Name: SEQUENCE feature_values_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.feature_values_id_seq TO foundation;


--
-- Name: TABLE gate_release_audit; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.gate_release_audit TO foundation;


--
-- Name: SEQUENCE gate_release_audit_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.gate_release_audit_id_seq TO foundation;


--
-- Name: TABLE governance_approvals; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.governance_approvals TO foundation;


--
-- Name: SEQUENCE governance_approvals_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.governance_approvals_id_seq TO foundation;


--
-- Name: TABLE governance_audit; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.governance_audit TO foundation;


--
-- Name: SEQUENCE governance_audit_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.governance_audit_id_seq TO foundation;


--
-- Name: TABLE hyperopt_studies; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.hyperopt_studies TO foundation;


--
-- Name: SEQUENCE hyperopt_studies_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.hyperopt_studies_id_seq TO foundation;


--
-- Name: TABLE hyperopt_trials; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.hyperopt_trials TO foundation;


--
-- Name: SEQUENCE hyperopt_trials_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.hyperopt_trials_id_seq TO foundation;


--
-- Name: TABLE intelligent_data_analysis_reports; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.intelligent_data_analysis_reports TO foundation;


--
-- Name: SEQUENCE intelligent_data_analysis_reports_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.intelligent_data_analysis_reports_id_seq TO foundation;


--
-- Name: TABLE knowledge_base; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.knowledge_base TO foundation;


--
-- Name: SEQUENCE knowledge_base_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.knowledge_base_id_seq TO foundation;


--
-- Name: TABLE ledger_gate_meta; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.ledger_gate_meta TO foundation;


--
-- Name: TABLE llm_call_traces; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.llm_call_traces TO foundation;


--
-- Name: SEQUENCE llm_call_traces_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.llm_call_traces_id_seq TO foundation;


--
-- Name: TABLE llm_prompt_templates; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.llm_prompt_templates TO foundation;


--
-- Name: SEQUENCE llm_prompt_templates_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.llm_prompt_templates_id_seq TO foundation;


--
-- Name: TABLE llm_rag_documents; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.llm_rag_documents TO foundation;


--
-- Name: SEQUENCE llm_rag_documents_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.llm_rag_documents_id_seq TO foundation;


--
-- Name: TABLE mechanical_fix_log; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.mechanical_fix_log TO foundation;


--
-- Name: SEQUENCE mechanical_fix_log_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.mechanical_fix_log_id_seq TO foundation;


--
-- Name: TABLE mission_lifecycle_log; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.mission_lifecycle_log TO foundation;


--
-- Name: SEQUENCE mission_lifecycle_log_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.mission_lifecycle_log_id_seq TO foundation;


--
-- Name: TABLE model_artifacts; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.model_artifacts TO foundation;


--
-- Name: SEQUENCE model_artifacts_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.model_artifacts_id_seq TO foundation;


--
-- Name: TABLE model_comparison_runs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.model_comparison_runs TO foundation;


--
-- Name: SEQUENCE model_comparison_runs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.model_comparison_runs_id_seq TO foundation;


--
-- Name: TABLE model_deployments; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.model_deployments TO foundation;


--
-- Name: SEQUENCE model_deployments_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.model_deployments_id_seq TO foundation;


--
-- Name: TABLE model_evaluation_results; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.model_evaluation_results TO foundation;


--
-- Name: SEQUENCE model_evaluation_results_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.model_evaluation_results_id_seq TO foundation;


--
-- Name: TABLE model_training_jobs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.model_training_jobs TO foundation;


--
-- Name: SEQUENCE model_training_jobs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.model_training_jobs_id_seq TO foundation;


--
-- Name: TABLE monitoring_logs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.monitoring_logs TO foundation;


--
-- Name: SEQUENCE monitoring_logs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.monitoring_logs_id_seq TO foundation;


--
-- Name: TABLE necessity_gate_log; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.necessity_gate_log TO foundation;


--
-- Name: SEQUENCE necessity_gate_log_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.necessity_gate_log_id_seq TO foundation;


--
-- Name: TABLE oss_inventory; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.oss_inventory TO foundation;


--
-- Name: SEQUENCE oss_inventory_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.oss_inventory_id_seq TO foundation;


--
-- Name: TABLE oss_solutions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.oss_solutions TO foundation;


--
-- Name: SEQUENCE oss_solutions_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.oss_solutions_id_seq TO foundation;


--
-- Name: TABLE outcome_evaluations; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.outcome_evaluations TO foundation;


--
-- Name: TABLE outcome_evidence_chains; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.outcome_evidence_chains TO foundation;


--
-- Name: TABLE outcome_eval_chain_audit; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.outcome_eval_chain_audit TO foundation;


--
-- Name: TABLE quality_rule_revisions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.quality_rule_revisions TO foundation;


--
-- Name: SEQUENCE quality_rule_revisions_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.quality_rule_revisions_id_seq TO foundation;


--
-- Name: TABLE rbac_permissions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.rbac_permissions TO foundation;


--
-- Name: TABLE rbac_role_permissions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.rbac_role_permissions TO foundation;


--
-- Name: TABLE rbac_roles; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.rbac_roles TO foundation;


--
-- Name: TABLE saturation_pulse; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.saturation_pulse TO foundation;


--
-- Name: TABLE tensor_agent_integrity; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_agent_integrity TO foundation;


--
-- Name: SEQUENCE tensor_agent_integrity_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_agent_integrity_id_seq TO foundation;


--
-- Name: TABLE tensor_agent_session_events; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_agent_session_events TO foundation;


--
-- Name: SEQUENCE tensor_agent_session_events_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_agent_session_events_id_seq TO foundation;


--
-- Name: TABLE tensor_agent_sessions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_agent_sessions TO foundation;


--
-- Name: SEQUENCE tensor_agent_sessions_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_agent_sessions_id_seq TO foundation;


--
-- Name: TABLE tensor_approval_requests; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_approval_requests TO foundation;


--
-- Name: SEQUENCE tensor_approval_requests_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_approval_requests_id_seq TO foundation;


--
-- Name: TABLE tensor_capability_deltas; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_capability_deltas TO foundation;


--
-- Name: SEQUENCE tensor_capability_deltas_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_capability_deltas_id_seq TO foundation;


--
-- Name: SEQUENCE tensor_credit_ledger_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_credit_ledger_id_seq TO foundation;


--
-- Name: TABLE tensor_dev_missions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_dev_missions TO foundation;


--
-- Name: SEQUENCE tensor_dev_missions_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_dev_missions_id_seq TO foundation;


--
-- Name: TABLE tensor_executions; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_executions TO foundation;


--
-- Name: SEQUENCE tensor_executions_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_executions_id_seq TO foundation;


--
-- Name: TABLE tensor_executors; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_executors TO foundation;


--
-- Name: TABLE tensor_guide_tracker; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_guide_tracker TO foundation;


--
-- Name: SEQUENCE tensor_guide_tracker_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_guide_tracker_id_seq TO foundation;


--
-- Name: TABLE tensor_model_blacklist; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_model_blacklist TO foundation;


--
-- Name: SEQUENCE tensor_model_blacklist_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_model_blacklist_id_seq TO foundation;


--
-- Name: TABLE tensor_reports; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_reports TO foundation;


--
-- Name: SEQUENCE tensor_reports_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_reports_id_seq TO foundation;


--
-- Name: TABLE tensor_repro_verdicts; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_repro_verdicts TO foundation;


--
-- Name: SEQUENCE tensor_repro_verdicts_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_repro_verdicts_id_seq TO foundation;


--
-- Name: TABLE tensor_scenarios; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_scenarios TO foundation;


--
-- Name: SEQUENCE tensor_scenarios_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensor_scenarios_id_seq TO foundation;


--
-- Name: TABLE tensor_workspace_state; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensor_workspace_state TO foundation;


--
-- Name: TABLE tensorphysics_runs; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.tensorphysics_runs TO foundation;


--
-- Name: SEQUENCE tensorphysics_runs_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.tensorphysics_runs_id_seq TO foundation;


--
-- Name: TABLE uploaded_files; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.uploaded_files TO foundation;


--
-- Name: SEQUENCE uploaded_files_id_seq; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON SEQUENCE public.uploaded_files_id_seq TO foundation;


--
-- Name: TABLE v_file_category_stats; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.v_file_category_stats TO foundation;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: -
--



--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: -
--



--
-- PostgreSQL database dump complete
--

