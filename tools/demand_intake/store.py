"""store —— 需求入榜 v2 的 DDL / SQL builder / psql 传输。

与 v1（m17133，``pending_review`` 事前门）的差别只在一处账本铁律：**提案即单子**。

v2 状态机（锚 = ``feature_ledger`` 行，铸造轨迹 = ``demand_intake_log`` 行）::

    agent finish
      └─► mint（机器门：帽 + slug 唯一）
            ├─ 过 → feature_ledger.status='open' + 同步 POST /tensor/missions（单子即刻存在）
            │       └─► 事后审计（抽样帽 20/日）
            │             ├─ 过 → audit_verdict='pass'（保持 open）
            │             └─ 不过 → feature_ledger.status='dropped'
            │                       + 关联未终态单 revoked + strike + chronicle
            ├─ 帽超限 → status='cap_exceeded'（不建锚、不建单）
            └─ slug 撞既有锚 → status='duplicate'（不建锚、不建单）

为什么帽/唯一性必须由**数据库**原子裁决而不是 Python 先查后写：并发同 slug 的两条
铸造若走「SELECT 查重 → INSERT」，两个进程都能通过查重；唯一违反它的是
``ON CONFLICT (slug) DO NOTHING RETURNING id``（唯一约束在行锁下裁决），配额同理
用 ``INSERT ... ON CONFLICT DO UPDATE ... WHERE minted < cap RETURNING``（原子自增）。
红队 ``concurrent_same_slug`` 就是打这两处的真空档——详见 :mod:`tools.demand_intake.cli`。

零领域语义：表里只有 slug 形状、量化验收、状态与计数；域词条全部来自注入的行。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Sequence

# ── 机门参数（规格即常量，测试逐条打靶） ────────────────────────────────────
DEFAULT_DAILY_CAP = 10          # 每 agent 每日铸造帽
DOWNGRADED_DAILY_CAP = 1        # strike ≥ 阈值后的降级帽
STRIKE_THRESHOLD = 3            # 降级触发线
AUDIT_DAILY_CAP = 20            # 事后审计抽样帽（每日，agent-provided 锚）
DEFAULT_ORG_ID = 0              # 平台全局作用域（与 worker_credit_tiers 口径一致）

LEDGER_SOURCE = "demand-intake-v2"
LEDGER_GROUP = "demand-intake"

#: 单子的非终态（审计不过要 revoke 的范围）与终态（不动）
NON_TERMINAL_MISSION_STATUSES = ("queued", "claimed", "running", "awaiting_acceptance")
TERMINAL_MISSION_STATUSES = ("succeeded", "failed", "stopped", "revoked", "orphan")


class StoreError(RuntimeError):
    """传输/DDL 层错误（含 psql 非零退出）。"""


class ReadOnlyViolation(StoreError):
    """``query()`` 收到非 SELECT 语句（只读硬闸，防误写）。"""


def q(name: str, schema: str = "") -> str:
    """带 schema 前缀的表名（测试用一次性 schema，生产留空走 search_path）。"""
    return f"{schema}.{name}" if schema else name


def _is_select(sql: str) -> bool:
    head = sql.lstrip().split(None, 1)
    return bool(head) and head[0].lower() in ("select", "with")


def cand_key(source_kind: str, source_ref: str, slug: str) -> str:
    return hashlib.sha1(f"{source_kind}|{source_ref}|{slug}".encode("utf-8")).hexdigest()


# ── 传输 ────────────────────────────────────────────────────────────────────
class Transport:
    name = "abstract"

    def query(self, sql: str) -> List[Dict[str, Any]]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def execute(self, sql: str) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError


class PsqlTransport(Transport):
    """psql 子进程传输（TCP 有密码 / 本地 peer 两模式）。

    本项目沙箱内 psycopg2 是 stub（``import psycopg2`` 成功但连接即抛），所以定位在
    subprocess + psql；``query()`` 把结果行包进 ``json_agg`` 拿回 JSON，``execute()``
    原样返回 ``-tA`` 文本输出（数据修改型 CTE 的 RETURNING 就在这里读）。
    """

    def __init__(self, db: str = "", host: str = "", user: str = "",
                 password: Optional[str] = None, peer_user: str = "postgres",
                 statement_timeout_ms: int = 30000) -> None:
        self.db = db or os.environ.get("TP_PG_DB", "tensorplatform")
        self.host = host or os.environ.get("TP_PG_HOST", "127.0.0.1")
        self.user = user or os.environ.get("TP_PG_USER", "foundation")
        self.password = (os.environ.get("TP_PG_PASSWORD", "") if password is None
                         else password)
        self.peer_user = peer_user
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.name = (f"psql(tcp:{self.user}@{self.host})" if self.password
                     else f"psql(peer:{peer_user})")

    def _run(self, sql: str) -> str:
        if not shutil.which("psql"):
            raise StoreError("psql not found on PATH")
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(sql)
            path = fh.name
        os.chmod(path, 0o644)     # peer 认证下 postgres 用户需可读
        env = dict(os.environ,
                   PGOPTIONS=f"-c statement_timeout={self.statement_timeout_ms}",
                   PATH=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
        try:
            if self.password:
                env["PGPASSWORD"] = self.password
                cmd = (f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc "
                       f"-f {path} -h {self.host} -U {self.user}")
                proc = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                      text=True, env=env, timeout=600)
            elif shutil.which("su"):
                peer = (f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc -f {path}")
                proc = subprocess.run(["su", self.peer_user, "-c", peer],
                                      capture_output=True, text=True, env=env, timeout=600)
            else:
                raise StoreError("no psql transport available (set TP_PG_PASSWORD)")
            if proc.returncode != 0:
                raise StoreError(f"psql failed (rc={proc.returncode}): {proc.stderr[:400]}")
            return proc.stdout
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def query(self, sql: str) -> List[Dict[str, Any]]:
        if not _is_select(sql):
            raise ReadOnlyViolation(f"query() rejects non-SELECT: {sql[:80]!r}")
        wrapped = ("SELECT replace(coalesce(json_agg(t), '[]'::json)::text, E'\\n', '') "
                   f"FROM (\n{sql}\n) t;")
        out = self._run(wrapped).strip()
        if not out:
            raise StoreError("empty psql output for read-only query")
        payload = json.loads("".join(out.splitlines()))
        if not isinstance(payload, list):
            raise StoreError(f"unexpected JSON payload type {type(payload).__name__}")
        return payload

    def execute(self, sql: str) -> str:
        return self._run(sql)


def sql_literal(text: Any) -> str:
    """单引号字面量（``standard_conforming_strings=on``，只需转义单引号）。"""
    return "'" + str("" if text is None else text).replace("'", "''") + "'"


def sql_json(payload: Any) -> str:
    """JSONB 字面量：dollar-quoting，避免任何引号/反斜杠转义歧义。"""
    tag = "$j" + uuid.uuid4().hex[:8] + "$"
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"{tag}{body}{tag}::jsonb"


# ── DDL：本包自有表（幂等） ─────────────────────────────────────────────────
def ddl(schema: str = "") -> List[str]:
    """``demand_intake_log``（铸造轨迹）+ ``demand_mint_quota``（帽计数器）。"""
    t_log = q("demand_intake_log", schema)
    return [
        f"""CREATE TABLE IF NOT EXISTS {t_log} (
  id bigserial PRIMARY KEY,
  cand_key text NOT NULL UNIQUE,
  source_kind text NOT NULL DEFAULT 'finish',
  source_ref text NOT NULL DEFAULT '',
  slug text NOT NULL DEFAULT '',
  title text NOT NULL DEFAULT '',
  acceptance text NOT NULL DEFAULT '',
  domain text NOT NULL DEFAULT 'platform',
  repo text NOT NULL DEFAULT '',
  confidence text NOT NULL DEFAULT '',
  src_field text NOT NULL DEFAULT '',
  status text NOT NULL,
  reason text NOT NULL DEFAULT '',
  ledger_id integer,
  mission_id bigint,
  mint_kind text NOT NULL DEFAULT 'agent',
  minted_by text NOT NULL DEFAULT '',
  org_id bigint NOT NULL DEFAULT 0,
  cap_applied integer NOT NULL DEFAULT 0,
  audit_verdict text NOT NULL DEFAULT '',
  audit_reason text NOT NULL DEFAULT '',
  audited_at timestamptz,
  raw jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now())""",
        # v1（m17133）已在生产建过同名表：CREATE IF NOT EXISTS 不会补列，故逐列 ADD
        # IF NOT EXISTS 做自愈式升级（幂等、只加不删）。
        *[f"ALTER TABLE {t_log} ADD COLUMN IF NOT EXISTS {col} {decl}"
          for col, decl in (
              ("repo", "text NOT NULL DEFAULT ''"),
              ("mission_id", "bigint"),
              ("mint_kind", "text NOT NULL DEFAULT 'agent'"),
              ("minted_by", "text NOT NULL DEFAULT ''"),
              ("org_id", "bigint NOT NULL DEFAULT 0"),
              ("cap_applied", "integer NOT NULL DEFAULT 0"),
              ("audit_verdict", "text NOT NULL DEFAULT ''"),
              ("audit_reason", "text NOT NULL DEFAULT ''"),
              ("audited_at", "timestamptz"),
          )],
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_status_idx ON {t_log} (status)",
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_slug_idx ON {t_log} (slug)",
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_mint_idx "
        f"ON {t_log} (mint_kind, created_at DESC)",
        f"""CREATE INDEX IF NOT EXISTS demand_intake_log_audit_idx
  ON {t_log} (audited_at) WHERE audit_verdict <> ''""",
        # 帽计数器：主键 (day, agent) 让「自增 + 上限」成为单行原子操作
        f"""CREATE TABLE IF NOT EXISTS {q('demand_mint_quota', schema)} (
  day date NOT NULL,
  agent text NOT NULL,
  minted integer NOT NULL DEFAULT 0,
  cap integer NOT NULL DEFAULT {DEFAULT_DAILY_CAP},
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, agent))""",
    ]


def create_schema(t: Transport, schema: str) -> None:
    """建一次性 schema（名字做形状校验：只允许 [a-z0-9_]，防注入）。"""
    if not schema or not all(c.isalnum() or c == "_" for c in schema):
        raise StoreError(f"refusing to create schema {schema!r}")
    t.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")


def ensure_schema(t: Transport, schema: str = "") -> None:
    for stmt in ddl(schema):
        t.execute(stmt + ";")


def support_ddl(schema: str = "") -> List[str]:
    """**一次性/隔离 schema** 里补齐邻近表的最小定义（生产 schema 已存在，勿执行）。

    存在的唯一理由：让 ``tests/test_demand_intake_v2.py`` 与 ``cli redteam`` 能在
    一个用完即弃的 schema 里跑真库并发/配额红队，而不碰生产表。
    """
    return [
        f"""CREATE TABLE IF NOT EXISTS {q('feature_ledger', schema)} (
  id serial PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  title text NOT NULL DEFAULT '',
  domain text NOT NULL DEFAULT 'platform',
  source text NOT NULL DEFAULT '',
  acceptance text NOT NULL DEFAULT '',
  ledger_group text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'open',
  evidence text NOT NULL DEFAULT '',
  mission_id bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz)""",
        f"""CREATE TABLE IF NOT EXISTS {q('tensor_dev_missions', schema)} (
  id bigserial PRIMARY KEY,
  goal text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'queued',
  origin text NOT NULL DEFAULT 'agent',
  task_key text,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz)""",
        f"""CREATE TABLE IF NOT EXISTS {q('tensor_agent_integrity', schema)} (
  id bigserial PRIMARY KEY,
  organization_id bigint NOT NULL,
  model varchar(64) NOT NULL,
  strikes integer NOT NULL DEFAULT 0,
  last_strike_at timestamptz,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, model))""",
        f"""CREATE TABLE IF NOT EXISTS {q('worker_credit_tiers', schema)} (
  id bigserial PRIMARY KEY,
  organization_id bigint NOT NULL DEFAULT 0,
  worker_id varchar(120) NOT NULL,
  credit_level smallint NOT NULL,
  rationale text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_worker_credit_tiers_worker UNIQUE (organization_id, worker_id))""",
        f"""CREATE TABLE IF NOT EXISTS {q('chronicle_snapshots', schema)} (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  payload jsonb NOT NULL DEFAULT '{{}}'::jsonb)""",
    ]


def ensure_support_schema(t: Transport, schema: str = "") -> None:
    for stmt in support_ddl(schema):
        t.execute(stmt + ";")


def drop_schema(t: Transport, schema: str) -> None:
    if not schema:
        raise StoreError("refusing to drop the default schema")
    t.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


# ── 读 ──────────────────────────────────────────────────────────────────────
def fetch_existing_anchors(t: Transport, schema: str = "") -> List[Dict[str, Any]]:
    """既有锚 ``[{slug,title,status}]``：去重判据与审计判据共用的污染面快照。"""
    tbl = q("feature_ledger", schema)
    return t.query(f"SELECT slug, title, status FROM {tbl} ORDER BY slug")


def fetch_strikes(t: Transport, agent: str, org_id: int = DEFAULT_ORG_ID,
                  schema: str = "") -> int:
    tbl = q("tensor_agent_integrity", schema)
    rows = t.query(f"SELECT coalesce(max(strikes), 0) AS s FROM {tbl} "
                   f"WHERE organization_id = {int(org_id)} AND model = {sql_literal(agent)}")
    return int(rows[0]["s"]) if rows else 0


def count_audits_today(t: Transport, schema: str = "", day: str = "") -> int:
    t_log = q("demand_intake_log", schema)
    where = "audit_verdict <> ''"
    if day:
        where += f" AND audited_at >= {sql_literal(day)}::date " \
                 f"AND audited_at < {sql_literal(day)}::date + interval '1 day'"
    else:
        where += " AND audited_at >= current_date"
    rows = t.query(f"SELECT count(*) AS n FROM {t_log} WHERE {where}")
    return int(rows[0]["n"]) if rows else 0


def fetch_audit_candidates(t: Transport, limit: int, *, mint_kind: str = "agent",
                           slugs: Optional[Sequence[str]] = None,
                           schema: str = "") -> List[Dict[str, Any]]:
    """待审计锚：agent 铸造、已建单、尚未审计，随机抽样（不按 id 偏好，防操纵顺序）。

    ``slugs`` 给定时只在这些 slug 里抽（定向审计/红队用），否则全池随机——随机而不是
    ``ORDER BY id`` 是有意的：按入库顺序审会让铸造者用「先塞垃圾、后塞好的」操纵抽样。
    """
    t_log = q("demand_intake_log", schema)
    filter_slug = ""
    if slugs:
        listed = ",".join(sql_literal(s) for s in slugs)
        filter_slug = f" AND l.slug IN ({listed}) "
    return t.query(
        f"SELECT l.id AS log_id, l.slug, l.title, l.acceptance, l.mission_id, "
        f"       l.minted_by, l.org_id, l.ledger_id, f.status AS ledger_status "
        f"  FROM {t_log} l JOIN {q('feature_ledger', schema)} f ON f.slug = l.slug "
        f" WHERE l.status = 'minted' AND l.audit_verdict = '' "
        f"   AND l.mint_kind = {sql_literal(mint_kind)} {filter_slug}"
        f" ORDER BY random() LIMIT {int(limit)}")


def fetch_quota(t: Transport, agent: str, schema: str = "") -> Dict[str, Any]:
    rows = t.query(
        f"SELECT day, agent, minted, cap FROM {q('demand_mint_quota', schema)} "
        f"WHERE day = current_date AND agent = {sql_literal(agent)}")
    return rows[0] if rows else {"day": None, "agent": agent, "minted": 0, "cap": 0}


def fetch_log_row(t: Transport, slug: str, schema: str = "") -> Optional[Dict[str, Any]]:
    rows = t.query(
        f"SELECT id, slug, status, mission_id, mint_kind, minted_by, audit_verdict, "
        f"       audit_reason FROM {q('demand_intake_log', schema)} "
        f" WHERE slug = {sql_literal(slug)} ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None

def fetch_missions(t: Transport, *, since_id: int = 0, mission_id: Optional[int] = None,
                   limit: int = 200, statuses: Sequence[str] = ("succeeded",),
                   schema: str = "") -> List[Dict[str, Any]]:
    """取已结算单的 ``(id, goal, result)``：源侧水位（增量扫描的入口）。"""
    tbl = q("tensor_dev_missions", schema)
    listed = ",".join(sql_literal(s) for s in statuses)
    where = [f"status IN ({listed})"]
    if mission_id is not None:
        where.append(f"id = {int(mission_id)}")
    elif since_id:
        where.append(f"id > {int(since_id)}")
    return t.query(f"SELECT id, goal, result FROM {tbl} WHERE {' AND '.join(where)} "
                   f"ORDER BY id LIMIT {int(limit)}")


def fetch_quota_all(t: Transport, *, day: Optional[str] = None,
                    schema: str = "") -> List[Dict[str, Any]]:
    """当日配额快照（agent 维度），供 ``cli status`` 与周报附注。"""
    t_quota = q("demand_mint_quota", schema)
    cond = f"day = {sql_literal(day)}::date" if day else "day = current_date"
    return t.query(f"SELECT day, agent, minted, cap FROM {t_quota} WHERE {cond} "
                   f"ORDER BY agent")
