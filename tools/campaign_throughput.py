#!/usr/bin/env python3
"""campaign_throughput —— 战役波次吞吐报表（自回归燃料③：看增长）。

为什么要它（审计事实，2026-11-07）
----------------------------------
自回归规模要"看得见增长"，就必须有**按 campaign plan 聚合的吞吐报表**：mission 成功率、
墙钟分布、失败回流率、worker 领取时延 P50/P95。此前平台只有逐单视图（``/tensor/missions``）
与职责归因（``tools/validation/mission_attribution.py``），没有任何一条**按 plan 聚合、
可 SQL 回算、可 pytest 复现**的吞吐口径；战役 node 的验收要求「首份真实报表 + 与 SQL 手算
对拍一致」，缺仪表则每班只能手抄数字。

它做三件事
----------
1. **纯函数统计层**（无 IO，可单测）：百分位（线性插值，等价 SQL ``percentile_cont``）、
   成功率、墙钟、领取时延、回流率、增长序列 —— 定义写在 ``DEFINITIONS`` 里，代码即口径。
2. **取数层**：只读 ``tensor_dev_missions`` + 领取证据（``tensor_agent_session_events``
   的 ``note/mission_claimed``）+ ``mission_lifecycle_log``（``custody_requeue``）。
   传输优先 ``TP_PG_PASSWORD`` 环境变量走 TCP，否则回落 ``su postgres -c psql``（本机 peer
   认证）；也可 ``--source fixture`` 读离线 JSON，测试与演示不需要数据库。
3. **对拍层**：``--crosscheck`` 用**独立 SQL**（``percentile_cont`` + ``FILTER`` + 半连接）
   重算同一批关键量，与 Python 结果逐项比对并落盘（``python / sql / rel_err / status``）。

不变式（纪律）
--------------
* **只增不改**：不建表、不改既有表结构、不动冻结 API 面；本工具只读，唯一写盘目标是
  ``--out-json`` / ``--out-md`` / ``--figure-dir``。
* **终态词表不复制**：从 ``backend/contracts/mission_status_vocab.v1.json``（G-A 单一契约源）
  读取 terminal 集合，避免第二份真相。
* **分母为 0 返回 None 而不是 0**（借 ``tools/guide_kpi_ledger.py``）：没有失败样本时
  "回流率 0%" 是假话，NULL + 样本数才是实话。
* ``--dry-run``：算完只打印，不写任何文件（含图）。

用法
----
    python3 tools/campaign_throughput.py --dry-run                 # 打印 11/12/13 报表
    python3 tools/campaign_throughput.py --plans 11,12,13 \\
        --out-json artifacts/campaign_throughput/report.json \\
        --out-md  docs/campaign_throughput/THROUGHPUT_REPORT.md \\
        --figure-dir artifacts/campaign_throughput
    python3 tools/campaign_throughput.py --plans all --crosscheck --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "backend" / "contracts" / "mission_status_vocab.v1.json"
DEFAULT_PLANS = "11,12,13"
CROSSCHECK_REL_TOL = 1e-9
SCHEMA_VERSION = "campaign_throughput.v1"

# 契约文件的兜底副本（仅在文件缺失时使用；tests 断言两者相等，防止漂移）。
FALLBACK_TERMINAL = ("succeeded", "failed", "stopped", "revoked", "orphan")

DEFINITIONS = {
    "plan_linkage": "task_key = ^p<plan_id>-<node_id>-a<attempt>$（战役派发器写入口径），"
                    "plan_id 即 campaign_plans.id；无该前缀的 mission 不计入任何 plan。",
    "terminal": "status ∈ 契约 mission_status_vocab.v1.json 的 terminal 集合",
    "success_rate": "#succeeded / #terminal（分母只含终态，在飞单不进分母）",
    "claim_latency_s": "首个 claimed 证据 − created_at（排队等待 = worker 领取时延）",
    "wall_clock_s": "finished_at − 首个 claimed 证据（执行墙钟）；无领取证据时回落 "
                    "finished_at − created_at 并以 wall_fallback_n 显式计数",
    "cycle_time_s": "finished_at − created_at（含排队的总在制时间）",
    "retry_share": "attempt ≥ 2 的 mission 数 / 该 plan 全部 mission 数",
    "failed_return_rate": "同 plan+node 存在更晚 attempt 的 failed 单数 / 该 plan failed 单数"
                          "（分母为 0 时为 null）",
    "custody_requeue": "mission_lifecycle_log.action='custody_requeue' 事件数（平台级回流通道）",
    "throughput_per_hour": "#terminal / (末个终态 finished_at − 首个终态 finished_at)（小时）",
    "percentile": "线性插值（h=(n−1)q），与 PostgreSQL percentile_cont 同构，故对拍容差 1e-9",
}


# ─────────────────────────── 契约 ───────────────────────────
def load_contract(path: Optional[pathlib.Path] = None) -> dict:
    """读终态词表契约（G-A 单一契约源）。缺失时用兜底副本并标记 source。"""
    p = pathlib.Path(path or CONTRACT_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "source": str(p),
            "version": data.get("version"),
            "statuses": tuple(data.get("statuses") or ()),
            "terminal": tuple(data.get("terminal") or FALLBACK_TERMINAL),
        }
    except (OSError, json.JSONDecodeError):
        return {"source": "fallback", "version": None, "statuses": (),
                "terminal": FALLBACK_TERMINAL}


# ─────────────────────────── 纯函数统计层 ───────────────────────────
def percentile_linear(values: Sequence[float], q: float) -> Optional[float]:
    """线性插值百分位，与 SQL ``percentile_cont(q)`` 同构；空序列返回 None。"""
    xs = sorted(float(v) for v in values if v is not None and not math.isnan(float(v)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    if q <= 0:
        return xs[0]
    if q >= 1:
        return xs[-1]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[int(pos)]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _r(x: Optional[float], ndigits: int = 3) -> Optional[float]:
    return None if x is None else round(float(x), ndigits)


def summarize(values: Iterable[Optional[float]], ndigits: Optional[int] = None) -> dict:
    """n / p50 / p95 / mean / min / max；空集时全部 None（不伪造 0）。

    默认**不在统计层取整**：取整必须发生在渲染层，否则 Python 侧是"先舍入再统计"，
    与 SQL ``percentile_cont``（全精度）永远对不齐（实测 rel_err ~1e-6）。
    """
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "min": None, "max": None}
    out = {
        "n": len(xs),
        "p50": percentile_linear(xs, 0.50),
        "p95": percentile_linear(xs, 0.95),
        "mean": sum(xs) / len(xs),
        "min": min(xs),
        "max": max(xs),
    }
    return {k: (v if (k == "n" or ndigits is None or v is None) else _r(v, ndigits))
            for k, v in out.items()}


def ratio(num: Any, den: Any, ndigits: Optional[int] = None) -> Optional[float]:
    """安全比率；分母为 0/None → None（0 会被误读成"实测为零"）。默认不取整。"""
    try:
        n, d = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    return (n / d) if ndigits is None else _r(n / d, ndigits)


ATTEMPT_RE = re.compile(r"^p(\d+)-(.+)-a(\d+)$")


def parse_task_key(task_key: Optional[str]) -> Optional[dict]:
    """``p11-IN10-L1-a1`` → {plan_id:11, node:'IN10-L1', attempt:1}；不匹配返回 None。"""
    if not task_key:
        return None
    m = ATTEMPT_RE.match(task_key.strip())
    if not m:
        return None
    return {"plan_id": int(m.group(1)), "node": m.group(2), "attempt": int(m.group(3))}


def _ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _secs(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def normalize_mission(raw: dict) -> Optional[dict]:
    """把一行原始记录（DB JSON 或 fixture）规整成统计层输入；无 plan 归属返回 None。"""
    parsed = parse_task_key(raw.get("task_key"))
    if parsed is None:
        return None
    created = _ts(raw.get("created_at"))
    finished = _ts(raw.get("finished_at"))
    claimed = _ts(raw.get("claimed_at"))
    claim_latency = _secs(claimed, created)
    wall = _secs(finished, claimed)
    wall_fallback = wall is None and finished is not None
    if wall_fallback:
        wall = _secs(finished, created)
    return {
        "id": raw.get("id"),
        "task_key": raw.get("task_key"),
        "plan_id": parsed["plan_id"],
        "node": parsed["node"],
        "attempt": parsed["attempt"],
        "status": (raw.get("status") or "").strip(),
        "repo": raw.get("repo"),
        "claimed_by": raw.get("claimed_by"),
        "session_id": raw.get("session_id"),
        "created_at": created.isoformat() if created else None,
        "claimed_at": claimed.isoformat() if claimed else None,
        "finished_at": finished.isoformat() if finished else None,
        "claim_latency_s": claim_latency,
        "wall_clock_s": wall,
        "wall_fallback": bool(wall_fallback),
        "cycle_time_s": _secs(finished, created),
        "custody_requeue_events": int(raw.get("requeued") or 0),
    }


def _plan_label(plan_id: int) -> str:
    return f"p{plan_id}"


def _growth_series(term_rows: Sequence[dict]) -> dict:
    """终态单按 finished_at 升序的累计增长序列（增长曲线数据源）。"""
    points = []
    cum = 0
    for row in sorted(term_rows, key=lambda r: (r["finished_at"] or "", r["id"] or 0)):
        cum += 1
        points.append({"utc": row["finished_at"], "mission_id": row["id"], "cum_terminal": cum})
    return {"points": points, "n": cum}


def _day_buckets(term_rows: Sequence[dict]) -> list:
    buckets: dict[str, int] = {}
    for row in term_rows:
        ts = row["finished_at"]
        if not ts:
            continue
        key = ts[:10]
        buckets[key] = buckets.get(key, 0) + 1
    return [{"date": k, "terminal": buckets[k]} for k in sorted(buckets)]


def aggregate_plan(plan_id: int, missions: Sequence[dict], terminal_set: Sequence[str]) -> dict:
    """单 plan 聚合（纯函数）。missions 必须已由 normalize_mission 规整。"""
    term_set = set(terminal_set)
    rows = [m for m in missions if m["plan_id"] == plan_id]
    term_rows = [m for m in rows if m["status"] in term_set]
    status_counts: dict[str, int] = {}
    for m in rows:
        status_counts[m["status"]] = status_counts.get(m["status"], 0) + 1

    n_succ = sum(1 for m in term_rows if m["status"] == "succeeded")
    n_failed = sum(1 for m in term_rows if m["status"] == "failed")

    attempts_by_node: dict[str, list] = {}
    for m in rows:
        attempts_by_node.setdefault(m["node"], []).append(m["attempt"])
    failed_rows = [m for m in term_rows if m["status"] == "failed"]
    failed_returned = sum(1 for m in failed_rows
                          if max(attempts_by_node[m["node"]]) > m["attempt"])
    retry_rows = [m for m in rows if m["attempt"] > 1]

    term_fin = [m["finished_at"] for m in term_rows if m["finished_at"]]
    span_s = None
    if len(term_fin) >= 2:
        span_s = _secs(_ts(max(term_fin)), _ts(min(term_fin)))
    per_hour = None
    if span_s and span_s > 0:
        per_hour = len(term_rows) / (span_s / 3600.0)

    return {
        "plan_id": plan_id,
        "plan_label": _plan_label(plan_id),
        "n_missions": len(rows),
        "n_terminal": len(term_rows),
        "n_active": len(rows) - len(term_rows),
        "n_succeeded": n_succ,
        "n_failed": n_failed,
        "status_counts": dict(sorted(status_counts.items())),
        "success_rate": ratio(n_succ, len(term_rows)),
        "fail_rate": ratio(n_failed, len(term_rows)),
        "claim_latency_s": summarize([m["claim_latency_s"] for m in rows]),
        "claim_evidence_coverage": ratio(sum(1 for m in rows if m["claimed_at"]), len(rows)),
        "wall_clock_s": summarize([m["wall_clock_s"] for m in term_rows]),
        "wall_fallback_n": sum(1 for m in term_rows if m["wall_fallback"]),
        "cycle_time_s": summarize([m["cycle_time_s"] for m in term_rows]),
        "retry": {
            "n_attempt_gt1": len(retry_rows),
            "retry_share": ratio(len(retry_rows), len(rows)),
            "n_failed": n_failed,
            "n_failed_returned": failed_returned,
            "failed_return_rate": ratio(failed_returned, n_failed),
            "custody_requeue_events": sum(m["custody_requeue_events"] for m in rows),
        },
        "throughput": {
            "n_terminal": len(term_rows),
            "span_seconds": _r(span_s, 3),
            "per_hour": per_hour,
            "first_terminal_utc": min(term_fin) if term_fin else None,
            "last_terminal_utc": max(term_fin) if term_fin else None,
            "day_buckets": _day_buckets(term_rows),
        },
        "growth": _growth_series(term_rows),
    }


def aggregate(missions: Sequence[dict], plans: Optional[Sequence[int]] = None,
              contract: Optional[dict] = None, source: str = "") -> dict:
    """多 plan 聚合 → 报表 dict（含口径、诊断、逐 plan 指标）。"""
    ctr = contract or load_contract()
    terminal_set = ctr["terminal"]
    norm = [m for m in (normalize_mission(r) for r in missions) if m is not None]
    available = sorted({m["plan_id"] for m in norm})
    chosen = sorted({int(p) for p in plans}) if plans else available
    per_session: dict[Any, int] = {}
    for m in norm:
        if m["claimed_at"]:
            sid = m.get("session_id")
            per_session[sid] = per_session.get(sid, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "contract": {"source": ctr["source"], "version": ctr["version"],
                     "terminal": list(terminal_set)},
        "plans_requested": chosen,
        "plans_with_data": available,
        "definitions": DEFINITIONS,
        "plan_reports": [aggregate_plan(p, norm, terminal_set) for p in chosen],
        "diagnostics": {
            "n_missions_scanned": len(missions),
            "n_missions_with_plan_linkage": len(norm),
            "n_missions_unlinked": len(missions) - len(norm),
            "claim_evidence_coverage": ratio(sum(1 for m in norm if m["claimed_at"]), len(norm)),
            "sessions_with_claim_evidence": len(per_session),
            "n_missions_with_claim_evidence": sum(1 for m in norm if m["claimed_at"]),
            # session↔mission 1:1 时 min(claimed) 归因无歧义；否则对有领取证据的 mission 偏早
            "claim_attribution_1to1": (len(per_session) ==
                                       sum(1 for m in norm if m["claimed_at"])),
        },
    }


# ─────────────────────────── 取数层（只读） ───────────────────────────
MISSION_SQL = """SELECT json_build_object(
  'id', m.id, 'task_key', m.task_key, 'status', m.status, 'repo', m.repo,
  'claimed_by', m.claimed_by, 'session_id', m.session_id,
  'created_at', m.created_at, 'finished_at', m.finished_at,
  'claimed_at', cl.claimed_at, 'requeued', coalesce(rq.n, 0))::text
FROM tensor_dev_missions m
LEFT JOIN LATERAL (
  SELECT min(e.created_at) AS claimed_at FROM tensor_agent_session_events e
   WHERE e.session_id = m.session_id AND e.kind = 'note'
     AND e.payload->>'subtype' = 'mission_claimed') cl ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM mission_lifecycle_log l
   WHERE l.mission_id = m.id AND l.action = 'custody_requeue') rq ON true
WHERE m.status IS NOT NULL
ORDER BY m.id"""


def _psql(sql: str, db: str = "") -> str:
    """只读 psql 执行：优先 TCP+密码，否则 su postgres 本地 peer 认证。"""
    db = db or os.environ.get("TP_PG_DB", "tensorplatform")
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
        fh.write(sql)
        path = fh.name
    os.chmod(path, 0o644)  # postgres 用户需可读
    try:
        pwd = os.environ.get("TP_PG_PASSWORD", "")
        if pwd:
            env = dict(os.environ, PGPASSWORD=pwd, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
            proc = subprocess.run(
                ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
                 "-U", os.environ.get("TP_PG_USER", "foundation"), "-d", db,
                 "-tA", "-f", path],
                capture_output=True, text=True, env=env, timeout=600)
            if proc.returncode == 0:
                return proc.stdout
            raise RuntimeError(f"psql(TCP) failed: {proc.stderr[:400]}")
        if shutil.which("su") and shutil.which("psql"):
            proc = subprocess.run(["su", "postgres", "-c", f"psql -d {db} -tA -f {path}"],
                                  capture_output=True, text=True, timeout=600)
            if proc.returncode == 0:
                return proc.stdout
            raise RuntimeError(f"psql(peer) failed: {proc.stderr[:400]}")
        raise RuntimeError("no psql transport available (set TP_PG_PASSWORD)")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def fetch_missions(sql: str = MISSION_SQL, db: str = "") -> list:
    rows = []
    for line in _psql(sql, db).splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ─────────────────────────── 对拍层（独立 SQL 重算） ───────────────────────────
def crosscheck_sql(plan_ids: Sequence[int]) -> str:
    """独立 SQL：用 FILTER / percentile_cont / EXISTS 半连接重算关键量（不复用 Python 逻辑）。"""
    ids = ",".join(str(int(p)) for p in plan_ids) or "-1"
    return f"""
WITH base AS (
  SELECT m.id, m.status, m.task_key,
         substring(m.task_key from '^p([0-9]+)-')::int AS plan_id,
         substring(m.task_key from '^p[0-9]+-(.+)-a[0-9]+$') AS node,
         substring(m.task_key from '-a([0-9]+)$')::int AS attempt,
         m.created_at, m.finished_at,
         (SELECT min(e.created_at) FROM tensor_agent_session_events e
           WHERE e.session_id = m.session_id AND e.kind = 'note'
             AND e.payload->>'subtype' = 'mission_claimed') AS claimed_at
    FROM tensor_dev_missions m
   WHERE m.task_key ~ '^p[0-9]+-.+-a[0-9]+$'
), scoped AS (
  SELECT * FROM base WHERE plan_id IN ({ids})
), per_plan AS (
  SELECT plan_id,
         count(*) AS n_missions,
         count(*) FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan'))
           AS n_terminal,
         count(*) FILTER (WHERE status = 'succeeded') AS n_succeeded,
         count(*) FILTER (WHERE status = 'failed') AS n_failed,
         count(*) FILTER (WHERE attempt > 1) AS n_attempt_gt1,
         count(*) FILTER (WHERE claimed_at IS NOT NULL) AS n_claim_evidence,
         percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (claimed_at - created_at))) AS claim_p50,
         percentile_cont(0.95) WITHIN GROUP (
           ORDER BY extract(epoch FROM (claimed_at - created_at))) AS claim_p95,
         percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (finished_at - claimed_at)))
           FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan')
                       AND claimed_at IS NOT NULL) AS wall_p50,
         percentile_cont(0.95) WITHIN GROUP (
           ORDER BY extract(epoch FROM (finished_at - claimed_at)))
           FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan')
                       AND claimed_at IS NOT NULL) AS wall_p95,
         min(finished_at) FILTER (
           WHERE status IN ('succeeded','failed','stopped','revoked','orphan')) AS first_terminal,
         max(finished_at) FILTER (
           WHERE status IN ('succeeded','failed','stopped','revoked','orphan')) AS last_terminal
    FROM scoped GROUP BY plan_id
), returned AS (
  SELECT b.plan_id, count(*) AS n_failed_returned
    FROM base b
   WHERE b.status = 'failed' AND b.plan_id IN ({ids})
     AND EXISTS (SELECT 1 FROM base b2
                  WHERE b2.plan_id = b.plan_id AND b2.node = b.node
                    AND b2.attempt > b.attempt)
   GROUP BY b.plan_id
), requeue AS (
  SELECT substring(m.task_key from '^p([0-9]+)-')::int AS plan_id, count(*) AS n
    FROM mission_lifecycle_log l JOIN tensor_dev_missions m ON m.id = l.mission_id
   WHERE l.action = 'custody_requeue' AND m.task_key ~ '^p[0-9]+-.+-a[0-9]+$'
   GROUP BY 1
)
SELECT json_build_object(
  'plan_id', p.plan_id, 'n_missions', p.n_missions, 'n_terminal', p.n_terminal,
  'n_succeeded', p.n_succeeded, 'n_failed', p.n_failed,
  'n_attempt_gt1', p.n_attempt_gt1, 'n_claim_evidence', p.n_claim_evidence,
  'claim_p50', p.claim_p50, 'claim_p95', p.claim_p95,
  'wall_p50', p.wall_p50, 'wall_p95', p.wall_p95,
  'first_terminal', p.first_terminal, 'last_terminal', p.last_terminal,
  'n_failed_returned', coalesce(r.n_failed_returned, 0),
  'custody_requeue_events', coalesce(q.n, 0))::text
FROM per_plan p
LEFT JOIN returned r USING (plan_id)
LEFT JOIN requeue q USING (plan_id)
ORDER BY p.plan_id""".strip()


CC_METRICS = (
    ("n_missions", "n_missions", "num"),
    ("n_terminal", "n_terminal", "num"),
    ("n_succeeded", "n_succeeded", "num"),
    ("n_failed", "n_failed", "num"),
    ("success_rate", None, "rate_succ"),
    ("retry_share", None, "rate_retry"),
    ("claim_latency_s.p50", "claim_p50", "num"),
    ("claim_latency_s.p95", "claim_p95", "num"),
    ("wall_clock_s.p50", "wall_p50", "num"),
    ("wall_clock_s.p95", "wall_p95", "num"),
    ("failed_return_rate", None, "rate_return"),
    ("retry.n_failed_returned", "n_failed_returned", "num"),
    ("retry.custody_requeue_events", "custody_requeue_events", "num"),
    ("throughput.first_terminal_utc", "first_terminal", "ts"),
    ("throughput.last_terminal_utc", "last_terminal", "ts"),
)


def _py_value(plan: dict, path: str) -> Any:
    cur: Any = plan
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def compare_crosscheck(report: dict, sql_rows: Sequence[dict],
                       tol: float = CROSSCHECK_REL_TOL) -> list:
    """逐项比对 Python vs SQL → [{plan_id, metric, python, sql, rel_err, status}]。"""
    sql_by_plan = {int(r["plan_id"]): r for r in sql_rows}
    out = []
    for plan in report.get("plan_reports", []):
        pid = int(plan["plan_id"])
        if pid not in sql_by_plan:          # SQL 侧没有该 plan 的行 → 显式标注，不算通过
            out.append({"plan_id": pid, "metric": "*", "python": None, "sql": None,
                        "rel_err": None, "status": "sql_missing"})
            continue
        srow = sql_by_plan[pid]
        for py_path, sql_key, kind in CC_METRICS:
            if kind == "rate_succ":
                pv = plan.get("success_rate")
                sv = ratio(srow.get("n_succeeded"), srow.get("n_terminal"))
            elif kind == "rate_retry":
                pv = plan["retry"]["retry_share"]
                sv = ratio(srow.get("n_attempt_gt1"), srow.get("n_missions"))
            elif kind == "rate_return":
                pv = plan["retry"]["failed_return_rate"]
                sv = ratio(srow.get("n_failed_returned"), srow.get("n_failed"))
            else:
                pv, sv = _py_value(plan, py_path), srow.get(sql_key)
            out.append({"plan_id": pid, "metric": py_path,
                        "python": pv, "sql": sv, **_cmp(pv, sv, tol, kind)})
    return out


def _cmp(pv: Any, sv: Any, tol: float, kind: str) -> dict:
    if pv is None and sv is None:
        return {"rel_err": None, "status": "both_null"}
    if pv is None or sv is None:
        return {"rel_err": None, "status": "one_null"}
    if kind == "ts":
        a, b = _ts(pv), _ts(sv)
        if a is None or b is None:
            return {"rel_err": None, "status": "one_null"}
        diff = abs((a - b).total_seconds())
        return {"rel_err": round(diff, 6), "status": "pass" if diff <= 1e-6 else "FAIL"}
    try:
        fa, fb = float(pv), float(sv)
    except (TypeError, ValueError):
        return {"rel_err": None, "status": "unparsable"}
    diff = abs(fa - fb)
    rel = diff / max(abs(fa), abs(fb), 1e-12)
    if diff <= 1e-9 or rel <= tol:
        return {"rel_err": _r(rel, 12), "status": "pass"}
    return {"rel_err": _r(rel, 12), "status": "FAIL"}


def crosscheck_verdict(rows: Sequence[dict]) -> dict:
    """PASS 判定：无 FAIL，且所有请求的 plan 都在 SQL 侧有行（sql_missing 视为不完整）。"""
    fails = [r for r in rows if r["status"] == "FAIL"]
    both_null = [r for r in rows if r["status"] == "both_null"]
    missing = [r for r in rows if r["status"] == "sql_missing"]
    one_null = [r for r in rows if r["status"] == "one_null"]
    compared = len(rows) - len(both_null) - len(missing)
    return {"pairs": len(rows), "compared": compared, "both_null": len(both_null),
            "one_null": len(one_null), "sql_missing": len(missing),
            "failures": len(fails), "ok": not fails and not missing and not one_null,
            "failure_metrics": [f"{r['plan_id']}:{r['metric']}" for r in fails]
                               + [f"{r['plan_id']}:{r['status']}"
                                  for r in missing + one_null]}


# ─────────────────────────── markdown 渲染 ───────────────────────────
def _fmt(v: Any, nd: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}{unit}"
    return f"{v}{unit}"


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{100.0 * v:.1f}%"


def render_markdown(report: dict) -> str:
    plans = report["plan_reports"]
    ctr = report["contract"]
    diag = report.get("diagnostics", {})
    L: list[str] = []
    A = L.append
    A("# 战役波次吞吐报表（campaign throughput · 自回归燃料③）")
    A("")
    A(f"- 生成时间（UTC）：`{report['generated_utc']}`　·　schema：`{report['schema_version']}`")
    A(f"- 数据源：`{report.get('source') or 'n/a'}`　·　口径定义见 §7 与 JSON 的 `definitions`")
    A(f"- plan 范围：{', '.join(map(str, report['plans_requested']))}；"
      f"库内可见 plan：{', '.join(map(str, report.get('plans_with_data', []))) or '—'}")
    A(f"- 终态词表来源：`{ctr['source']}`（version {ctr['version']}）→ terminal = "
      f"`{', '.join(ctr['terminal'])}`")
    A("")
    A("## 1. 主表（按 plan 聚合）")
    A("")
    A("| plan | mission | 终态 | 在飞 | 成功率 | 墙钟 P50 (s) | 墙钟 P95 (s) | "
      "领取 P50 (s) | 领取 P95 (s) | 吞吐 (/h) |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for p in plans:
        A(f"| {p['plan_label']} | {p['n_missions']} | {p['n_terminal']} | {p['n_active']} | "
          f"{_pct(p['success_rate'])} | {_fmt(p['wall_clock_s']['p50'], 1)} | "
          f"{_fmt(p['wall_clock_s']['p95'], 1)} | {_fmt(p['claim_latency_s']['p50'], 1)} | "
          f"{_fmt(p['claim_latency_s']['p95'], 1)} | {_fmt(p['throughput']['per_hour'], 2)} |")
    A("")
    A("## 2. 失败回流率与领取证据覆盖")
    A("")
    A("| plan | 重试单(attempt>1) | 重试占比 | failed | 已回流 failed | 回流率 | "
      "custody_requeue 事件 | 领取证据覆盖 | 墙钟回落单数 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for p in plans:
        rt = p["retry"]
        A(f"| {p['plan_label']} | {rt['n_attempt_gt1']} | {_pct(rt['retry_share'])} | "
          f"{rt['n_failed']} | {rt['n_failed_returned']} | {_pct(rt['failed_return_rate'])} | "
          f"{rt['custody_requeue_events']} | {_pct(p['claim_evidence_coverage'])} | "
          f"{p['wall_fallback_n']} |")
    A("")
    A("> 分母为 0 时回流率写 `—`（NULL），**不写 0%**：没有失败样本并不等于「回流率 = 0」，"
      "样本量见 failed 列。")
    A("")
    A("## 3. 状态分布")
    A("")
    for p in plans:
        dist = ", ".join(f"`{k}`:{v}" for k, v in p["status_counts"].items()) or "—"
        A(f"- **{p['plan_label']}**（n={p['n_missions']}，终态 {p['n_terminal']}）：{dist}")
    A("")
    A("## 4. 增长曲线数据源（自回归规模证据）")
    A("")
    A("| plan | 首个终态 (UTC) | 末个终态 (UTC) | 跨度 (s) | 终态数 | 吞吐 (/h) | 日桶 |")
    A("|---|---|---|---|---|---|---|")
    for p in plans:
        tp = p["throughput"]
        buckets = ", ".join(f"{b['date']}:{b['terminal']}" for b in tp["day_buckets"]) or "—"
        A(f"| {p['plan_label']} | {tp['first_terminal_utc'] or '—'} | "
          f"{tp['last_terminal_utc'] or '—'} | {_fmt(tp['span_seconds'], 1)} | "
          f"{tp['n_terminal']} | {_fmt(tp['per_hour'], 2)} | {buckets} |")
    A("")
    A("累计增长序列（逐终态单）写在 JSON 的 `plan_reports[].growth.points`，"
      "绘图脚本/看板可直接消费，无需二次解析任务书文本。")
    A("")
    if report.get("crosscheck"):
        cc = report["crosscheck"]
        verdict = cc.get("verdict", {})
        A("## 5. Python ↔ SQL 对拍")
        A("")
        A(f"- 比对项 {verdict.get('pairs')} 对：实比 {verdict.get('compared')}，"
          f"双空 {verdict.get('both_null')}，失败 {verdict.get('failures')}")
        A(f"- 结论：**{'一致（PASS）' if verdict.get('ok') else '不一致（FAIL）'}**"
          f"（容差 rel ≤ {cc.get('tol')}，时间戳比到 1e-6 s）")
        A("")
        A("| plan | 指标 | Python | SQL | rel_err | 判定 |")
        A("|---|---|---|---|---|---|")
        for r in cc["rows"]:
            A(f"| {r['plan_id']} | `{r['metric']}` | {_fmt(r['python'], 6)} | "
              f"{_fmt(r['sql'], 6)} | {_fmt(r['rel_err'], 12)} | {r['status']} |")
        A("")
        if cc.get("sql"):
            A("<details><summary>对拍所用独立 SQL（原文，可直接 psql 复算）</summary>")
            A("")
            A("```sql")
            A(cc["sql"].strip())
            A("```")
            A("")
            A("</details>")
            A("")
    A("## 6. 诊断与局限")
    A("")
    A(f"- 扫描 mission {diag.get('n_missions_scanned')} 条，其中可归入 plan 的 "
      f"{diag.get('n_missions_with_plan_linkage')} 条；无 plan 归属 "
      f"{diag.get('n_missions_unlinked')} 条（task_key 为空或非 `p<id>-<node>-a<n>` 形态）"
      "——本报表**不替它们猜归属**。")
    A(f"- 领取证据覆盖 {_pct(diag.get('claim_evidence_coverage'))}"
      f"（证据源：`tensor_agent_session_events` 的 `note/mission_claimed`，"
      f"{diag.get('sessions_with_claim_evidence')} 个 session）。缺失时墙钟回落到 "
      "`created_at → finished_at` 并于 `wall_fallback_n` 计数。")
    A("- 百分位为线性插值（与 `percentile_cont` 同构）；n<20 时 P95 属**指示性**，"
      "不要当 SLA 承诺。")
    A(f"- 领取时延以 session 的首个 `mission_claimed` 事件归因：有领取证据的 mission "
      f"{diag.get('n_missions_with_claim_evidence')} 条 / session "
      f"{diag.get('sessions_with_claim_evidence')} 个 → "
      f"{'严格 1:1，归因无歧义' if diag.get('claim_attribution_1to1') else '非 1:1，个别值可能偏早'}。")
    A("- 回流率只在 `p<plan>-<node>-a<n>` 结构上可定义；自启单（`selfstart-*`、空 task_key）"
      "没有稳定 node 身份，回流率对其为 NULL。")
    A("")
    A("## 7. 口径定义（代码即口径）")
    A("")
    for k, v in report["definitions"].items():
        A(f"- **{k}**：{v}")
    A("")
    A("---")
    A("复算方式：`python3 tools/campaign_throughput.py --plans "
      f"{','.join(map(str, report['plans_requested']))} --crosscheck`（只读；"
      "`--dry-run` 只打印不落盘）。")
    return "\n".join(L) + "\n"


# ─────────────────────────── 图 ───────────────────────────
def render_figures(report: dict, outdir: pathlib.Path) -> list:
    """生成 2 张图：①累计吞吐增长曲线 ②领取时延/墙钟 P50/P95 对比。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - 无 matplotlib 时如实降级
        print(f"[warn] matplotlib 不可用，跳过绘图：{exc}", file=sys.stderr)
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    figs = []
    plans = report["plan_reports"]

    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=110)
    for p in plans:
        pts = p["growth"]["points"]
        if not pts:
            continue
        xs = [_ts(pt["utc"]) for pt in pts]
        if any(x is None for x in xs):
            continue
        t0 = min(xs)
        ax.plot([(x - t0).total_seconds() / 60.0 for x in xs],
                [pt["cum_terminal"] for pt in pts], marker="o", ms=3,
                label=f"{p['plan_label']} (n={p['n_terminal']})")
    ax.set_xlabel("minutes since first terminal mission")
    ax.set_ylabel("cumulative terminal missions")
    ax.set_title("Campaign throughput growth (plans 11/12/13)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    g1 = outdir / "fig1_growth_curve.png"
    fig.savefig(g1)
    plt.close(fig)
    figs.append({"name": "fig1_growth_curve.png", "path": str(g1),
                 "caption": "Cumulative terminal missions vs wall time per campaign plan "
                            "(growth-curve data source for the next autoregressive wave)."})

    labels, c50, c95, w50, w95 = [], [], [], [], []
    for p in plans:
        if p["n_missions"] == 0:
            continue
        labels.append(p["plan_label"])
        c50.append(p["claim_latency_s"]["p50"] or 0.0)
        c95.append(p["claim_latency_s"]["p95"] or 0.0)
        w50.append(p["wall_clock_s"]["p50"] or 0.0)
        w95.append(p["wall_clock_s"]["p95"] or 0.0)
    if labels:
        fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=110)
        idx = list(range(len(labels)))
        w = 0.2
        ax.bar([i - 1.5 * w for i in idx], c50, w, label="claim P50")
        ax.bar([i - 0.5 * w for i in idx], c95, w, label="claim P95")
        ax.bar([i + 0.5 * w for i in idx], w50, w, label="wall P50")
        ax.bar([i + 1.5 * w for i in idx], w95, w, label="wall P95")
        ax.set_xticks(idx)
        ax.set_xticklabels(labels)
        ax.set_ylabel("seconds")
        ax.set_title("Worker claim latency vs execution wall clock (P50/P95)")
        ax.grid(alpha=0.3, axis="y")
        ax.legend(fontsize=8)
        fig.tight_layout()
        g2 = outdir / "fig2_latency_p50_p95.png"
        fig.savefig(g2)
        plt.close(fig)
        figs.append({"name": "fig2_latency_p50_p95.png", "path": str(g2),
                     "caption": "Per-plan worker claim latency (queue wait) and execution wall "
                                "clock, P50/P95 linear-interpolated."})
    return figs


# ─────────────────────────── CLI ───────────────────────────
def parse_plans(spec: str, missions: Sequence[dict]) -> list:
    """'11,12,13' | 'all' | '5-9' → plan_id 列表。all = 库内有 plan 归属的全部 plan。"""
    spec = (spec or "").strip()
    if spec in ("", "all"):
        return sorted({m["plan_id"] for m in (normalize_mission(r) for r in missions)
                       if m is not None})
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def load_fixture(path: pathlib.Path) -> list:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return data.get("missions", data) if isinstance(data, dict) else data


def build_report(args) -> dict:
    if args.source == "fixture":
        missions = load_fixture(pathlib.Path(args.fixture))
        source = f"fixture:{args.fixture}"
    else:
        missions = fetch_missions(MISSION_SQL, args.db)
        source = f"postgres:{args.db or os.environ.get('TP_PG_DB', 'tensorplatform')}"
    plans = parse_plans(args.plans, missions)
    report = aggregate(missions, plans, load_contract(), source=source)
    if args.crosscheck and args.source == "db":
        sql = crosscheck_sql(plans)
        rows = [json.loads(line) for line in _psql(sql, args.db).splitlines() if line.strip()]
        cc_rows = compare_crosscheck(report, rows, args.tol)
        report["crosscheck"] = {"sql": sql, "tol": args.tol, "rows": cc_rows,
                                "sql_rows": rows, "verdict": crosscheck_verdict(cc_rows)}
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="战役波次吞吐报表（只读）")
    ap.add_argument("--plans", default=DEFAULT_PLANS, help="11,12,13 | all | 5-9")
    ap.add_argument("--source", choices=("db", "fixture"), default="db")
    ap.add_argument("--fixture", default="", help="--source fixture 时的 JSON 路径")
    ap.add_argument("--db", default="", help="库名（默认 $TP_PG_DB 或 tensorplatform）")
    ap.add_argument("--out-json", default="", help="报表 JSON 落盘路径")
    ap.add_argument("--out-md", default="", help="报表 markdown 落盘路径")
    ap.add_argument("--figure-dir", default="", help="PNG 图输出目录（缺省不画图）")
    ap.add_argument("--crosscheck", action="store_true", help="用独立 SQL 逐项对拍")
    ap.add_argument("--tol", type=float, default=CROSSCHECK_REL_TOL)
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写任何文件")
    args = ap.parse_args(argv)

    if args.source == "fixture" and not args.fixture:
        ap.error("--source fixture 需要 --fixture <path>")
    if args.crosscheck and args.source == "fixture":
        print("[warn] fixture 模式无 DB，跳过 --crosscheck", file=sys.stderr)
        args.crosscheck = False

    report = build_report(args)
    md = render_markdown(report)
    verdict = (report.get("crosscheck") or {}).get("verdict")

    if args.dry_run:
        print(md)
        if verdict:
            print(f"[crosscheck] {verdict}", file=sys.stderr)
        print("[dry-run] 未写入任何文件", file=sys.stderr)
        return 0 if (verdict is None or verdict.get("ok")) else 1

    if args.out_json:
        p = pathlib.Path(args.out_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"[ok] JSON → {p}", file=sys.stderr)
    if args.out_md:
        p = pathlib.Path(args.out_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        print(f"[ok] MD → {p}", file=sys.stderr)
    if args.figure_dir:
        figs = render_figures(report, pathlib.Path(args.figure_dir))
        report["figures"] = [{k: v for k, v in f.items() if k != "path"} for f in figs]
        if args.out_json:
            pathlib.Path(args.out_json).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for f in figs:
            print(f"[ok] FIG → {f['path']}", file=sys.stderr)
    return 0 if (verdict is None or verdict.get("ok")) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())