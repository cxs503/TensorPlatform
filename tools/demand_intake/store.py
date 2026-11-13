"""store —— 传输层 + DDL + SQL builder + 幂等落库（demand_intake 的唯一写路径）。

三层分工
--------
- :class:`Transport` 抽象只两件事：``query``（只读，返回 dict 列表）/ ``execute``（可写）。
  ``query`` 有一道硬闸：非 SELECT 直接抛 :class:`ReadOnlyViolation`——不靠约定靠断言。
- :class:`PsqlTransport`：本机惯例（与 ``tools/blocked_node_triage.py`` 同口径）——
  有 ``TP_PG_PASSWORD`` 走 TCP，否则回落到 ``su postgres`` 本地 peer 认证；结果统一经
  ``json_agg`` 单行输出再 ``json.loads``，避免字段分隔符漂移。
- SQL 一律经 :func:`q` 做 **schema 限定**（``schema=""`` 走 search_path 缺省）：这样红队
  能在临时 schema 里对同构表跑**真 PG** 全链路，跑完 ``ROLLBACK``/``DROP``，绝不碰生产行。

幂等与冲突（两条独立保险）
--------------------------
1. ``demand_intake_log.cand_key`` 唯一（``sha1(source_kind|source_ref|slug)``）：同一张
   mission 被反复扫（sweeper 每分钟跑一次）也只入账一次。
2. ``feature_ledger.slug`` 本身唯一：即使 cand_key 变了（换了 mission 提出同一个 slug），
   也 ``ON CONFLICT (slug) DO NOTHING``，并记 ``duplicate``。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import gate as G
from .parser import Candidate

LEDGER_GROUP = "demand-intake"
LEDGER_SOURCE = "agent-proposed"
STATUS_PENDING = "pending_review"

#: 事后抽样审计（audit）日帽：审计是抽样不是全量，帽的存在让「未覆盖」这个数字可以
#: 被诚实报出来（见 :func:`tools.demand_intake.audit.run_audit` 的 ``uncovered``）。
AUDIT_DAILY_CAP = 20
#: 事后审计的验收字符下限（事前门是 4，见 gate.MIN_ACCEPTANCE_CHARS）：先上车后查票，
#: 票价必须更贵，否则审计帽（20/日）撑不起覆盖。
AUDIT_MIN_ACCEPTANCE_CHARS = 8
#: 单子的终态：审计回撤**只动未终态单**（``revoked`` 自身也在终态里，避免重复改）
TERMINAL_MISSION_STATUSES = ("succeeded", "failed", "stopped", "revoked", "orphan")


class StoreError(RuntimeError):
    pass


class ReadOnlyViolation(StoreError):
    pass


def q(table: str, schema: str = "") -> str:
    """表名限定（``schema`` 为空则走缺省 search_path）。"""
    return f"{schema}.{table}" if schema else table


def _is_select(sql: str) -> bool:
    head = " ".join(sql.strip().split()[:2]).lower()
    return head.startswith("select") or head.startswith("with")


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
    """psql 子进程传输（TCP 有密码 / 本地 peer 两模式）。"""

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
            peer = f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc -f {path}"
            if self.password:
                env["PGPASSWORD"] = self.password
                cmd = (f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc "
                       f"-f {path} -h {self.host} -U {self.user}")
                proc = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                      text=True, env=env, timeout=600)
            elif shutil.which("su"):
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


# ── DDL ─────────────────────────────────────────────────────────────────────
def ddl(schema: str = "") -> List[str]:
    """建表/建索引（幂等）。返回语句列表，便于逐条执行与红队断言。"""
    t_log = q("demand_intake_log", schema)
    t_backlog = q("demand_backlog", schema)
    return [
        f"""CREATE TABLE IF NOT EXISTS {t_log} (
  id bigserial PRIMARY KEY,
  cand_key text NOT NULL UNIQUE,
  source_kind text NOT NULL,
  source_ref text NOT NULL,
  slug text NOT NULL DEFAULT '',
  title text NOT NULL DEFAULT '',
  acceptance text NOT NULL DEFAULT '',
  domain text NOT NULL DEFAULT 'platform',
  confidence text NOT NULL DEFAULT '',
  src_field text NOT NULL DEFAULT '',
  status text NOT NULL,
  reason text NOT NULL DEFAULT '',
  ledger_id integer,
  raw jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now())""",
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_status_idx ON {t_log} (status)",
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_slug_idx ON {t_log} (slug)",
        f"""CREATE TABLE IF NOT EXISTS {t_backlog} (
  id bigserial PRIMARY KEY,
  source_kind text NOT NULL DEFAULT 'tensor_report',
  source_ref text NOT NULL,
  section text NOT NULL DEFAULT '',
  item text NOT NULL,
  item_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'candidate',
  created_at timestamptz NOT NULL DEFAULT now())""",
        f"CREATE INDEX IF NOT EXISTS demand_backlog_status_idx ON {t_backlog} (status)",
    ]


def ensure_schema(t: Transport, schema: str = "") -> None:
    if schema:                      # 临时 schema（红队）需要先建容器；生产走缺省 search_path
        t.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
    for stmt in ddl(schema):
        t.execute(stmt + ";")


def audit_ddl(schema: str = "") -> List[str]:
    """事后审计所需的加列/加索引（**只加不删**，幂等）。

    与 ``backend/migrations/124_demand_intake_audit.sql`` 一一对应：迁移文件是生产路径，
    本函数是测试/红队与 ``cli audit`` 的自愈路径，两边同形状才不会「测试绿、生产缺列」。
    """
    t_log = q("demand_intake_log", schema)
    return [
        f"ALTER TABLE {t_log} ADD COLUMN IF NOT EXISTS mission_id bigint",
        f"ALTER TABLE {t_log} ADD COLUMN IF NOT EXISTS "
        f"audit_verdict text NOT NULL DEFAULT ''",
        f"ALTER TABLE {t_log} ADD COLUMN IF NOT EXISTS "
        f"audit_reason text NOT NULL DEFAULT ''",
        f"ALTER TABLE {t_log} ADD COLUMN IF NOT EXISTS audited_at timestamptz",
        f"CREATE INDEX IF NOT EXISTS demand_intake_log_audit_idx "
        f"ON {t_log} (audited_at) WHERE audit_verdict <> ''",
    ]


def ensure_audit_schema(t: Transport, schema: str = "") -> None:
    """建审计列/索引（幂等）。生产由迁移文件提供，这里供自愈与测试用。"""
    ensure_schema(t, schema)
    for stmt in audit_ddl(schema):
        t.execute(stmt + ";")


# ── 读：来源与既有锚 ────────────────────────────────────────────────────────
def fetch_missions(t: Transport, *, since_id: int = 0, mission_id: Optional[int] = None,
                   limit: int = 500, schema: str = "") -> List[Dict[str, Any]]:
    """取 succeeded 单的 ``(id, goal, result)``；``since_id`` 为增量水位。"""
    tbl = q("tensor_dev_missions", schema)
    where = ["status = 'succeeded'"]
    if mission_id is not None:
        where.append(f"id = {int(mission_id)}")
    elif since_id:
        where.append(f"id > {int(since_id)}")
    sql = (f"SELECT id, goal, result FROM {tbl} WHERE {' AND '.join(where)} "
           f"ORDER BY id LIMIT {int(limit)}")
    return t.query(sql)


def fetch_existing_anchors(t: Transport, schema: str = "") -> List[Dict[str, Any]]:
    tbl = q("feature_ledger", schema)
    return t.query(f"SELECT slug, title, status FROM {tbl} ORDER BY slug")


# ── 读：事后审计面（可领锚 → 抽样待审） ─────────────────────────────────────
def fetch_audit_candidates(t: Transport, limit: int, schema: str = "",
                           slugs: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """待审锚：日志与账本**同为 open** 且从未审计（``audit_verdict = ''``）。

    为什么要求 ``f.status = 'open'``：审计对象是**已进可领面**的建议（v1 的 pending_review
    还没上架就被别的路径处理，不属于本模块的事）；``audit_verdict = ''`` 让重跑幂等——
    审过的行不会再被抽中，帽的记账与审计动作一一对应。
    """
    t_log, t_led = q("demand_intake_log", schema), q("feature_ledger", schema)
    where = ["l.status = 'open'", "l.audit_verdict = ''", "f.status = 'open'"]
    if slugs:
        vals = ",".join(sql_literal(s) for s in slugs)
        where.append(f"l.slug IN ({vals})")
    return t.query(
        f"SELECT l.id AS log_id, l.slug, l.title, l.acceptance, l.cand_key, "
        f"       l.mission_id, f.id AS ledger_id, f.status AS ledger_status "
        f"  FROM {t_log} l JOIN {t_led} f ON f.slug = l.slug "
        f" WHERE {' AND '.join(where)} ORDER BY l.id LIMIT {int(limit)}")


def count_audits_today(t: Transport, schema: str = "") -> int:
    """今日已用审计额（按 ``audited_at`` 的自然日计，跨日自动归零）。"""
    t_log = q("demand_intake_log", schema)
    rows = t.query(f"SELECT count(*) AS n FROM {t_log} "
                   f"WHERE audited_at IS NOT NULL AND audited_at::date = current_date")
    return int(rows[0]["n"]) if rows else 0


def fetch_slug_mission_ids(t: Transport, slug: str, schema: str = "") -> List[int]:
    """该 slug 在账本上关联的单子 id（审计回撤时要一并撤）。"""
    t_led = q("feature_ledger", schema)
    rows = t.query(f"SELECT mission_id FROM {t_led} "
                   f"WHERE slug = {sql_literal(slug)} AND mission_id IS NOT NULL")
    return [int(r["mission_id"]) for r in rows]


# ── 写：入榜 ────────────────────────────────────────────────────────────────
@dataclass
class IngestResult:
    inserted: int = 0
    duplicate: int = 0
    rejected: int = 0
    rows: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = []


def ingest_candidates(t: Transport, cands: Sequence[Candidate], *,
                      mission_id: Optional[int] = None, dry_run: bool = False,
                      schema: str = "") -> IngestResult:
    """候选 → ``demand_intake_log`` + ``feature_ledger``(status=pending_review)。

    幂等：``cand_key`` 撞唯一键即跳过并记 ``duplicate``；``feature_ledger.slug`` 撞唯一键
    记 ``duplicate``（已存在别的来源占了这个 slug）。全部走同一事务语义：先 SELECT
    日志水位，再 INSERT ... ON CONFLICT DO NOTHING RETURNING——只有真插入的行才写账本。
    """
    t_log, t_led = q("demand_intake_log", schema), q("feature_ledger", schema)
    res = IngestResult()
    existing = {str(r.get("slug")): r for r in fetch_existing_anchors(t, schema)}
    for c in cands:
        row = c.as_row()
        key = cand_key(row["source_kind"], row["source_ref"], row["slug"])
        res.rows.append({"cand_key": key, **row})
        if not row["slug"]:
            res.rejected += 1
            if not dry_run:
                t.execute(
                    f"INSERT INTO {t_log} (cand_key, source_kind, source_ref, slug, title, "
                    f"acceptance, domain, confidence, src_field, status, reason, raw) VALUES "
                    f"({sql_literal(key)}, {sql_literal(row['source_kind'])}, "
                    f"{sql_literal(row['source_ref'])}, '', {sql_literal(row['title'][:400])}, "
                    f"{sql_literal(row['acceptance'][:2000])}, {sql_literal(row['domain'])}, "
                    f"{sql_literal(row['confidence'])}, {sql_literal(row['field'])}, "
                    f"'rejected', 'slug_empty', {sql_json(row['raw'])}) "
                    f"ON CONFLICT (cand_key) DO NOTHING;")
            continue
        prior = existing.get(row["slug"])
        if prior is not None:
            res.duplicate += 1
            if not dry_run:
                t.execute(
                    f"INSERT INTO {t_log} (cand_key, source_kind, source_ref, slug, title, "
                    f"acceptance, domain, confidence, src_field, status, reason, raw) VALUES "
                    f"({sql_literal(key)}, {sql_literal(row['source_kind'])}, "
                    f"{sql_literal(row['source_ref'])}, {sql_literal(row['slug'])}, "
                    f"{sql_literal(row['title'][:400])}, "
                    f"{sql_literal(row['acceptance'][:2000])}, {sql_literal(row['domain'])}, "
                    f"{sql_literal(row['confidence'])}, {sql_literal(row['field'])}, "
                    f"'duplicate', {sql_literal('ledger_conflict:' + str(prior.get('status')))}, "
                    f"{sql_json(row['raw'])}) ON CONFLICT (cand_key) DO NOTHING;")
            continue
        if dry_run:
            res.inserted += 1
            continue
        evidence = json.dumps({"intake": "demand-auto", "source_kind": row["source_kind"],
                               "source_ref": row["source_ref"],
                               "field": row["field"], "confidence": row["confidence"]},
                              ensure_ascii=False)
        # 三段写 + 回查账实：一条数据修改 CTE 里同时 INSERT 两张表时，PostgreSQL 的
        # RETURNING 可见性会让 ledger_id 回填成 NULL（实测：红队里 2 条合格锚 status 已
        # open 但 ledger_id 为空，inserted 记账为 0）。改成显式事务三段式，账本行的
        # 唯一键冲突（别的来源已占 slug）与日志行的幂等冲突各管各的。
        t.execute(
            "BEGIN;\n"
            f"INSERT INTO {t_log} (cand_key, source_kind, source_ref, slug, title, acceptance, "
            f"domain, confidence, src_field, status, reason, raw) VALUES "
            f"({sql_literal(key)}, {sql_literal(row['source_kind'])}, "
            f"{sql_literal(row['source_ref'])}, {sql_literal(row['slug'])}, "
            f"{sql_literal(row['title'][:400])}, {sql_literal(row['acceptance'][:2000])}, "
            f"{sql_literal(row['domain'])}, {sql_literal(row['confidence'])}, "
            f"{sql_literal(row['field'])}, '{STATUS_PENDING}', '', {sql_json(row['raw'])}) "
            f"ON CONFLICT (cand_key) DO NOTHING;\n"
            f"INSERT INTO {t_led} (slug, title, domain, source, acceptance, ledger_group, "
            f"status, evidence, mission_id) VALUES "
            f"({sql_literal(row['slug'])}, "
            f"{sql_literal(row['title'][:400] or row['slug'])}, {sql_literal(row['domain'])}, "
            f"'{LEDGER_SOURCE}', {sql_literal(row['acceptance'][:4000] or 'TBD')}, "
            f"'{LEDGER_GROUP}', '{STATUS_PENDING}', {sql_literal(evidence)}, "
            f"{int(mission_id) if mission_id is not None else 'NULL'}) "
            f"ON CONFLICT (slug) DO NOTHING;\n"
            f"UPDATE {t_log} SET ledger_id = "
            f"(SELECT f.id FROM {t_led} f WHERE f.slug = {sql_literal(row['slug'])}), "
            f"updated_at = now() WHERE cand_key = {sql_literal(key)};\n"
            "COMMIT;")
        # 账实一致：不信 psql 的 "UPDATE n" 回显（CTE 里两个改动子句时回显不可靠），
        # 直接回查日志行的 ledger_id——只有真链上账本才算 inserted。
        chk = t.query(f"SELECT ledger_id FROM {t_log} "
                      f"WHERE cand_key = {sql_literal(key)}")
        if chk and chk[0].get("ledger_id") is not None:
            res.inserted += 1
            existing[row["slug"]] = {"slug": row["slug"], "title": row["title"],
                                     "status": STATUS_PENDING}
        else:
            res.duplicate += 1
    return res


# ── 写：审计门 ──────────────────────────────────────────────────────────────
def pending_rows(t: Transport, schema: str = "") -> List[Dict[str, Any]]:
    t_log, t_led = q("demand_intake_log", schema), q("feature_ledger", schema)
    return t.query(
        f"SELECT l.id AS log_id, l.slug, l.title, l.acceptance, l.cand_key, "
        f"       f.id AS ledger_id, f.status AS ledger_status "
        f"  FROM {t_log} l JOIN {t_led} f ON f.slug = l.slug "
        f" WHERE l.status = '{STATUS_PENDING}' AND f.status = '{STATUS_PENDING}' "
        f" ORDER BY l.id")


def apply_gate(t: Transport, *, dry_run: bool = False,
               schema: str = "") -> Dict[str, Any]:
    """pending_review → open / dropped。返回裁决统计与原因码分布。"""
    t_log, t_led = q("demand_intake_log", schema), q("feature_ledger", schema)
    rows = pending_rows(t, schema)
    existing = fetch_existing_anchors(t, schema)
    decisions: List[G.Decision] = []
    for row in rows:
        others = [r for r in existing if str(r.get("slug")) != row["slug"]]
        d = G.evaluate({"slug": row["slug"], "title": row["title"],
                        "acceptance": row["acceptance"]}, others)
        decisions.append(d)
        if dry_run:
            continue
        new_status = "open" if d.ok else ("dropped" if d.verdict == G.DROPPED
                                          else row["ledger_status"])
        reason = ",".join(d.reasons) if d.reasons else "passed"
        gate_ev = json.dumps({"gate": "demand-intake", "verdict": d.verdict,
                              "reasons": d.reasons, **d.evidence}, ensure_ascii=False)
        t.execute(
            f"UPDATE {t_led} SET status = {sql_literal(new_status)}, "
            f"evidence = coalesce(evidence, '') || {sql_literal(' | ' + gate_ev)} "
            f"WHERE id = {int(row['ledger_id'])};")
        t.execute(
            f"UPDATE {t_log} SET status = {sql_literal(d.verdict)}, "
            f"reason = {sql_literal(reason)}, updated_at = now() "
            f"WHERE id = {int(row['log_id'])};")
        if d.ok:
            existing.append({"slug": row["slug"], "title": row["title"], "status": "open"})
    return {"pending": len(rows),
            "opened": sum(1 for d in decisions if d.ok),
            "dropped": sum(1 for d in decisions if d.verdict == G.DROPPED),
            "duplicate": sum(1 for d in decisions if d.verdict == G.DUPLICATE),
            "reasons": G.gate_reason_codes(decisions),
            "decisions": [{"slug": d.slug, "verdict": d.verdict,
                           "reasons": d.reasons} for d in decisions]}


# ── 写：报告待办 → demand_backlog ───────────────────────────────────────────
def ingest_backlog_items(t: Transport, items: Sequence[Dict[str, Any]], *,
                         dry_run: bool = False, schema: str = "") -> int:
    tbl = q("demand_backlog", schema)
    n = 0
    for it in items:
        key = hashlib.sha1((f"{it['source_kind']}|{it['source_ref']}|{it['section']}|"
                            f"{it['item'].strip()}").encode("utf-8")).hexdigest()
        if dry_run:
            n += 1
            continue
        out = t.execute(
            f"INSERT INTO {tbl} (source_kind, source_ref, section, item, item_key) VALUES "
            f"({sql_literal(it['source_kind'])}, {sql_literal(it['source_ref'])}, "
            f"{sql_literal(it['section'])}, {sql_literal(it['item'])}, {sql_literal(key)}) "
            f"ON CONFLICT (item_key) DO NOTHING RETURNING id;")
        if "1" in (out or ""):
            n += 1
    return n


# ── 度量（入榜率口径，报告与红队共用） ──────────────────────────────────────
def measure(t: Transport, schema: str = "") -> Dict[str, Any]:
    """入榜率口径：detected（解析出候选）→ landed（写进日志）→ open（可领）。"""
    t_log = q("demand_intake_log", schema)
    rows = t.query(f"SELECT status, count(*) AS n FROM {t_log} GROUP BY 1")
    by = {r["status"]: int(r["n"]) for r in rows}
    detected = int(t.query(f"SELECT count(*) AS n FROM {t_log}")[0]["n"])
    landed = sum(v for k, v in by.items() if k != "rejected")
    return {"detected": detected, "landed": landed, "by_status": by,
            "intake_rate": round(landed / detected, 4) if detected else 0.0,
            "claimable_rate": round(by.get("open", 0) / landed, 4) if landed else 0.0}


# ── chronicle 留痕 ──────────────────────────────────────────────────────────
def chronicle(t: Transport, phase: str, payload: Dict[str, Any],
              schema: str = "") -> None:
    """往 ``chronicle_snapshots`` 追加一条事件（失败不抛：留痕不能反过来卡主链）。"""
    body = {"kind": "demand_intake", "phase": phase, "at": "now()"}
    body.update(payload)
    sql = (f"INSERT INTO {q('chronicle_snapshots', schema)} (ts, payload) "
           f"VALUES (now(), {sql_json(body)});")
    try:
        t.execute(sql)
    except StoreError:
        pass