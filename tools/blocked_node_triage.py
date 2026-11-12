#!/usr/bin/env python3
"""blocked_node_triage —— 战役 blocked 节点的五类分诊 + 有预算复活出口。

为什么需要本模块（实测证据，2026-11-12）
----------------------------------------
``campaign_nodes`` 快照 done 3077 / blocked 1014 / pending 582；blocked 分类为
``mission failed x2`` 606、``infra x5 (agent_lost)`` 263、``infra x5 (llm_error)`` 117、
``mission stopped x2`` 16、``hollow success x2`` 4、人工验收门 8。

``ops/taskplane/campaign_dispatcher.py`` 的 ``sync_phase()`` 对基础设施失败给最多 4 次
免费重排（``infra_retries < 4``），耗尽后写 ``block_reason='infra x5 (...)'`` 并置
``blocked``；mission 失败两次同样置 blocked。**这条路径之后仓库内没有任何 node 级复活
代码**，于是 blocked 成了吸收态：近 30 天 ``campaign_events`` 里
``event_type='node_status_change' AND detail->>'from'='blocked'`` 为 0 条，
``block_detected`` / ``block_resolved`` 全表 0 条。

与既有机制的分工（本模块**不重做**它们）
----------------------------------------
- ``tools/validation/infra_replay.py``：mission 级「诊断→重播」，按 mission 的
  ``fail_stage`` / ``stop_condition`` 判可复活性并派生新 mission；它**不改**
  ``campaign_nodes.status``，所以节点仍停在 blocked。本模块补的正是这一层的出口。
- dispatcher 的 ``infra_retries`` 免费重排：最多 4 次，属**阻塞前**预算；本模块管
  **阻塞后**的复活预算（``--max-revivals``，默认 3），两套预算互不覆盖。
- honest-FAIL / 人工验收门：只出建议，永不自动放行。

五类分诊（按 ``block_reason`` 前缀，全部机器可判）
--------------------------------------------------
``INFRA_RETRYABLE``  以 ``infra x`` 开头 → 可复活（受预算与批次上限约束）
``SUBSTANTIVE_FAIL`` 以 ``mission failed x`` 开头 → 实质失败，只出建议不改状态
``STOPPED``          以 ``mission stopped x`` 开头 → 被人/流程叫停，自动重排等于对抗停机
``HOLLOW``           以 ``hollow success x`` 开头 → 零提交空转成功，重试只会烧预算
``MANUAL_GATE``      其余一切（中文门禁语义或不可识别者）→ ``never``，永不动

决策只有三种：``requeue`` / ``stay_blocked`` / ``never``。

安全边界（写路径）
------------------
1. 默认 ``--dry-run``：只发 SELECT；``Transport.query`` 有一道硬闸，非 SELECT 语句直接
   抛 ``ReadOnlyViolation``——不靠约定靠断言。
2. ``--apply`` 才写：仅 ``INFRA_RETRYABLE`` 且 ``revivals < --max-revivals``；单次运行
   ``--max-batch``（默认 50）硬上限，超出部分记 ``deferred_by_batch`` 留在 blocked。
3. 每条复活是一条 ``WITH upd AS (UPDATE ... WHERE id=.. AND status='blocked' RETURNING ..)
   INSERT INTO campaign_events ... SELECT .. FROM upd``：**只有状态真的翻成功了才写事件**，
   并发下别的进程已解封的节点不会被重复记账。
4. 事件 detail 含契约字段 ``from/to/cause/revival``（``cause='infra_retry'``，
   ``revival`` = 本次是第几次复活），另附 ``node_id`` / ``node`` / ``block_reason`` 供
   审计与复活计数回溯（``revivals`` 取 ``metadata->>'infra_revivals'`` 与事件计数两者的
   较大值，任一来源缺失都能自洽）。

平台中性纪律
------------
本模块是**共性机制**：不含任何领域词；域数据（节点名/目标）一律经 DB 注入，测试夹具
用合成节点名。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ── 分类常量 ────────────────────────────────────────────────────────────────
INFRA_RETRYABLE = "INFRA_RETRYABLE"
SUBSTANTIVE_FAIL = "SUBSTANTIVE_FAIL"
STOPPED = "STOPPED"
HOLLOW = "HOLLOW"
MANUAL_GATE = "MANUAL_GATE"
CLASSES: Tuple[str, ...] = (
    INFRA_RETRYABLE, SUBSTANTIVE_FAIL, STOPPED, HOLLOW, MANUAL_GATE,
)

# 前缀表：顺序即优先级（先匹配者胜）。比对前已规整空白并 casefold。
_PREFIX_TABLE: Tuple[Tuple[str, str], ...] = (
    ("infra x", INFRA_RETRYABLE),
    ("mission failed x", SUBSTANTIVE_FAIL),
    ("mission stopped x", STOPPED),
    ("hollow success x", HOLLOW),
)

REQUEUE = "requeue"
STAY_BLOCKED = "stay_blocked"
NEVER = "never"

DEFAULT_MAX_REVIVALS = 3
DEFAULT_MAX_BATCH = 50

# 事件/原因码（对外契约）
CAUSE_INFRA_RETRY = "infra_retry"
EVENT_BLOCK_RESOLVED = "block_resolved"

# 复活计数在节点 metadata 里的键（与 dispatcher 的 infra_retries 分开，互不覆盖）
REVIVAL_META_KEY = "infra_revivals"


def _norm_reason(reason: Optional[str]) -> str:
    """规整 block_reason：折叠空白 + casefold；None → 空串。"""
    if reason is None:
        return ""
    return " ".join(str(reason).split()).casefold()


def classify_block_reason(reason: Optional[str]) -> str:
    """按 ``block_reason`` 前缀分五类。不可识别/为空一律 ``MANUAL_GATE``（永不自动处理）。"""
    text = _norm_reason(reason)
    for prefix, cls in _PREFIX_TABLE:
        if text.startswith(prefix):
            return cls
    return MANUAL_GATE


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def revivals_of(row: Mapping[str, Any]) -> int:
    """已用掉的复活次数：metadata 计数与事件计数取大者。

    两个来源都缺失时返回 0；任一来源存在即以其为准——这使本模块对「事件表被清空」
    与「metadata 被别的路径重写」两种漂移都保持单调，不会因丢失一侧而超发复活。
    """
    return max(
        _as_int(row.get("infra_revivals")),
        _as_int(row.get("event_revivals")),
    )


def _advice(cls: str, block_reason: Optional[str], revivals: int, max_revivals: int) -> str:
    if cls == INFRA_RETRYABLE:
        if revivals < max_revivals:
            return (f"infra failure is non-substantive; revival {revivals + 1}/{max_revivals} "
                    f"requeues the node (pre-block free retries already spent)")
        return (f"infra revival budget exhausted ({revivals}/{max_revivals}); "
                f"needs operator decision, not another blind retry")
    if cls == SUBSTANTIVE_FAIL:
        return ("substantive failure (attempt budget spent on real work); auto-requeue would "
                "mask a failed result -> plan-level re-scope required, status untouched")
    if cls == STOPPED:
        return "stopped deliberately (operator/flow); auto-requeue would fight the stop"
    if cls == HOLLOW:
        return ("hollow success twice (zero committed artifacts); retry burns budget "
                "without new signal")
    return (f"manual gate or unrecognised reason {block_reason!r}; "
            f"never auto-processed by design")


def decide(row: Mapping[str, Any], *, max_revivals: int = DEFAULT_MAX_REVIVALS) -> Dict[str, Any]:
    """单节点决策（纯函数）。返回带 class/decision/reason/revivals 的字典。"""
    reason = row.get("block_reason")
    cls = classify_block_reason(reason)
    revivals = revivals_of(row)
    if cls == INFRA_RETRYABLE:
        decision = REQUEUE if revivals < max_revivals else STAY_BLOCKED
    elif cls == MANUAL_GATE:
        decision = NEVER
    else:
        decision = STAY_BLOCKED
    out: Dict[str, Any] = {
        "id": _as_int(row.get("id")),
        "plan_id": _as_int(row.get("plan_id")),
        "node": row.get("node") or row.get("ext") or "?",
        "name": row.get("name") or "",
        "block_reason": reason,
        "class": cls,
        "decision": decision,
        "revivals": revivals,
        "revival_index": (revivals + 1) if decision == REQUEUE else None,
        "infra_retries": _as_int(row.get("infra_retries")),
        "reason": _advice(cls, reason, revivals, max_revivals),
    }
    if decision == REQUEUE:
        out["event_detail"] = {
            "from": "blocked",
            "to": "pending",
            "cause": CAUSE_INFRA_RETRY,
            "revival": revivals + 1,
            "node_id": out["id"],
            "node": out["node"],
            "block_reason": reason,
        }
    return out


def build_plan(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_revivals: int = DEFAULT_MAX_REVIVALS,
    max_batch: int = DEFAULT_MAX_BATCH,
    total_blocked: Optional[int] = None,
) -> Dict[str, Any]:
    """全量分诊 + 批次上限（纯函数，确定性：按节点 id 升序消费批次预算）。

    ``total_blocked`` 传同一快照的 ``count(*)`` 用于覆盖度校验（分类数之和必须等于它）；
    不传则以行数为准。
    """
    if max_revivals < 0:
        raise ValueError("max_revivals must be >= 0")
    if max_batch < 0:
        raise ValueError("max_batch must be >= 0")

    decisions = [decide(row, max_revivals=max_revivals) for row in rows]
    decisions.sort(key=lambda d: d["id"])

    candidates = [d for d in decisions if d["decision"] == REQUEUE]
    selected_ids = {d["id"] for d in candidates[:max_batch]}
    for d in candidates[max_batch:]:
        d["decision"] = STAY_BLOCKED
        d["revival_index"] = None
        d.pop("event_detail", None)
        d["deferred_by_batch"] = True
        d["reason"] = (f"requeue candidate ({d['revivals']}/{max_revivals} revivals used) but "
                       f"--max-batch={max_batch} reached this run; stays blocked for next run")

    by_class: Dict[str, int] = {c: 0 for c in CLASSES}
    by_decision: Dict[str, int] = {REQUEUE: 0, STAY_BLOCKED: 0, NEVER: 0}
    for d in decisions:
        by_class[d["class"]] += 1
        by_decision[d["decision"]] += 1

    classified = sum(by_class.values())
    expected = len(rows) if total_blocked is None else int(total_blocked)
    selected = [d for d in decisions if d["id"] in selected_ids]

    return {
        "max_revivals": max_revivals,
        "max_batch": max_batch,
        "blocked_total": expected,
        "classified_total": classified,
        "coverage_ok": classified == expected == len(rows),
        "by_class": by_class,
        "by_decision": by_decision,
        "selected_for_requeue": selected,
        "selected_count": len(selected),
        "deferred_by_batch": sum(1 for d in decisions if d.get("deferred_by_batch")),
        "decisions": decisions,
    }


# ── SQL 渲染（写路径的唯一出口；纯字符串函数便于审计与夹具断言）─────────────
def _lit(value: Any) -> str:
    """SQL 字面量（standard_conforming_strings 下的单引号双写转义）。"""
    text = "" if value is None else str(value)
    return "'" + text.replace("'", "''") + "'"


def _sql_value(value: Any) -> str:
    return str(int(value)) if isinstance(value, bool) is False and isinstance(value, int) else _lit(value)


def requeue_sql(decision: Mapping[str, Any]) -> str:
    """单节点复活语句：UPDATE 成功（守卫 ``status='blocked'``）才写事件。"""
    nid = _as_int(decision["id"])
    revival = _as_int(decision.get("revival_index"))
    detail = dict(decision.get("event_detail") or {})
    detail.setdefault("node_id", nid)
    detail.setdefault("revival", revival)
    pairs = ", ".join(f"{_lit(k)}, {_sql_value(v)}" for k, v in detail.items())
    return (
        "WITH upd AS (\n"
        "  UPDATE campaign_nodes\n"
        "     SET status = 'pending', mission_id = NULL, block_reason = NULL,\n"
        "         actual_end = NULL, updated_at = now(),\n"
        f"         metadata = jsonb_set(coalesce(metadata, '{{}}'::jsonb), "
        f"'{{{REVIVAL_META_KEY}}}', to_jsonb({revival}::int), true)\n"
        f"   WHERE id = {nid} AND status = 'blocked'\n"
        "  RETURNING id, plan_id\n"
        ")\n"
        "INSERT INTO campaign_events (organization_id, plan_id, event_type, detail)\n"
        "SELECT p.organization_id, upd.plan_id, "
        f"{_lit(EVENT_BLOCK_RESOLVED)},\n"
        f"       jsonb_build_object({pairs})\n"
        "  FROM upd\n"
        "  JOIN campaign_plans p ON p.id = upd.plan_id AND p.organization_id IS NOT NULL;"
    )


def apply_sql(plan: Mapping[str, Any], *, wrap_transaction: bool = True) -> str:
    """把计划里的 requeue 决策渲染成一条事务脚本（默认 BEGIN/COMMIT 包裹）。"""
    stmts = [requeue_sql(d) for d in plan.get("selected_for_requeue") or []]
    if not stmts:
        return ""
    head = ["BEGIN;"] if wrap_transaction else []
    tail = ["COMMIT;"] if wrap_transaction else []
    return "\n".join(head + stmts + tail) + "\n"


# ── 传输层 ──────────────────────────────────────────────────────────────────
class TriageError(RuntimeError):
    """本模块的可预期错误（连不上库 / 只读违例 / 复核不一致）。"""


class ReadOnlyViolation(TriageError):
    """dry-run 路径上出现了非 SELECT 语句。"""


def _is_select(sql: str) -> bool:
    head = sql.lstrip().lstrip("(").lstrip()
    return head[:6].casefold() == "select" or head[:4].casefold() == "with"


class Transport:
    """极小 SQL 传输接口：``query`` 只读（返回 dict 列表）、``execute`` 可写。"""

    name = "abstract"

    def query(self, sql: str) -> List[Dict[str, Any]]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def execute(self, sql: str) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError


def _json_agg(sql: str) -> str:
    """把任意只读 SELECT 包成单行 JSON 数组输出（免分隔符漂移，空集也给 ``[]``）。

    ``replace(..., E'\\n', '')`` 是必须的：psql 打印 json/jsonb 时会**按元素换行**
    （实测 1014 行 blocked 查询被拆成 1014 行），服务端先转 text 再去掉格式换行，
    客户端拿到的才是真正单行。字符串内的换行在 JSON 里是转义序列 ``\\n``，不受影响。
    """
    return (f"SELECT replace(coalesce(json_agg(t), '[]'::json)::text, E'\\n', '') "
            f"FROM (\n{sql}\n) t;")


class PsqlTransport(Transport):
    """psql 子进程传输：有密码走 TCP，无密码回落 ``su postgres`` 本地 peer 认证。

    与 ``tools/campaign_throughput.py::_psql`` 同口径（本机部署惯例），使本工具在
    有/无 PG 凭据的两种工作树里都能跑，且不依赖驱动版本。结果统一经 ``json_agg``
    单行输出再 ``json.loads``——不用字段分隔符，节点名/原因里的任何字符都不会串列。
    """

    def __init__(self, db: str = "", host: str = "", user: str = "",
                 password: str = "", peer_user: str = "postgres",
                 statement_timeout_ms: int = 30000) -> None:
        self.db = db or os.environ.get("TP_PG_DB", "tensorplatform")
        self.host = host or os.environ.get("TP_PG_HOST", "127.0.0.1")
        self.user = user or os.environ.get("TP_PG_USER", "foundation")
        self.password = password if password != "" else os.environ.get("TP_PG_PASSWORD", "")
        self.peer_user = peer_user
        self.statement_timeout_ms = int(statement_timeout_ms)
        self.name = (f"psql(tcp:{self.user}@{self.host})" if self.password
                     else f"psql(peer:{peer_user})")

    def _run(self, sql: str) -> str:
        if not shutil.which("psql"):
            raise TriageError("psql not found on PATH")
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
            fh.write(sql)
            path = fh.name
        os.chmod(path, 0o644)  # peer 认证下 postgres 用户需可读
        # 控制面纪律：任何一次调用都不许把调用方吊死（锁等待/长扫）——30s 硬超时
        env = dict(os.environ, PGOPTIONS=f"-c statement_timeout={self.statement_timeout_ms}",
                   PATH=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
        try:
            peer = f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc -f {path}"
            if self.password:
                env["PGPASSWORD"] = self.password
                cmd = (f"psql -d {self.db} -tA -v ON_ERROR_STOP=1 --no-psqlrc "
                       f"-f {path} -h {self.host} -U {self.user}")
                proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                                      env=env, timeout=600)
            elif shutil.which("su"):
                proc = subprocess.run(["su", self.peer_user, "-c", peer],
                                      capture_output=True, text=True, env=env, timeout=600)
            else:
                raise TriageError("no psql transport available (set TP_PG_PASSWORD)")
            if proc.returncode != 0:
                raise TriageError(f"psql failed (rc={proc.returncode}): {proc.stderr[:400]}")
            return proc.stdout
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def query(self, sql: str) -> List[Dict[str, Any]]:
        if not _is_select(sql):
            raise ReadOnlyViolation(f"query() rejects non-SELECT: {sql[:60]!r}")
        out = self._run(_json_agg(sql)).strip()
        if not out:
            raise TriageError("empty psql output for read-only query")
        # 客户端再兜一次：只在 JSON 结构空白处可能出现的换行，去掉不改变语义
        payload = json.loads("".join(out.splitlines()))
        if not isinstance(payload, list):
            raise TriageError(f"unexpected JSON payload type {type(payload).__name__}")
        return payload

    def execute(self, sql: str) -> str:
        return self._run(sql)


# ── 存储层 ──────────────────────────────────────────────────────────────────
BLOCKED_SQL = """
SELECT n.id,
       n.plan_id,
       coalesce(n.metadata->>'ext_id', '?')                        AS ext_id,
       coalesce(n.name, '')                                        AS name,
       coalesce(n.block_reason, '')                                AS block_reason,
       coalesce(n.metadata->>'infra_retries', '0')                 AS infra_retries,
       coalesce(n.metadata->>'infra_revivals', '0')                AS infra_revivals,
       COALESCE(ev.n, 0)                                           AS event_revivals,
       count(*) OVER ()                                            AS blocked_total
  FROM campaign_nodes n
  LEFT JOIN LATERAL (
        SELECT count(*) AS n
          FROM campaign_events e
         WHERE e.event_type = 'block_resolved'
           AND e.detail->>'node_id' = n.id::text
       ) ev ON true
 WHERE n.status = 'blocked'
 ORDER BY n.id
"""

BLOCKED_COUNT_SQL = "SELECT count(*) AS blocked_total FROM campaign_nodes WHERE status = 'blocked'"


class Store:
    """分诊所需的最小存储接口（PG 实现给真跑，内存实现给单测）。"""

    name = "abstract"

    def fetch_blocked(self) -> Tuple[List[Dict[str, Any]], int]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def blocked_count(self) -> int:  # pragma: no cover - 抽象
        raise NotImplementedError

    def apply(self, plan: Mapping[str, Any]) -> Dict[str, Any]:  # pragma: no cover - 抽象
        raise NotImplementedError


class PgStore(Store):
    """生产 store：只读查询走 ``query``，``--apply`` 时把事务脚本交给 ``execute``。"""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.name = transport.name

    def fetch_blocked(self) -> Tuple[List[Dict[str, Any]], int]:
        rows = self.transport.query(BLOCKED_SQL)
        out: List[Dict[str, Any]] = []
        total = 0
        for r in rows:
            total = max(total, _as_int(r.get("blocked_total")))
            out.append({
                "id": _as_int(r.get("id")),
                "plan_id": _as_int(r.get("plan_id")),
                "node": r.get("ext_id") or "?",
                "name": r.get("name") or "",
                "block_reason": r.get("block_reason") or None,
                "infra_retries": _as_int(r.get("infra_retries")),
                "infra_revivals": _as_int(r.get("infra_revivals")),
                "event_revivals": _as_int(r.get("event_revivals")),
            })
        if not out:  # 空集时窗口函数拿不到总数，单独补一次
            total = self.blocked_count()
        return out, total

    def blocked_count(self) -> int:
        rows = self.transport.query(BLOCKED_COUNT_SQL)
        return _as_int(rows[0].get("blocked_total")) if rows else 0

    def apply(self, plan: Mapping[str, Any]) -> Dict[str, Any]:
        script = apply_sql(plan, wrap_transaction=True)
        if not script:
            return {"applied": 0, "sql": "", "output": ""}
        output = self.transport.execute(script)
        return {"applied": int(plan.get("selected_count") or 0), "sql": script, "output": output}


# ── 编排 ────────────────────────────────────────────────────────────────────
def run(
    store: Store,
    *,
    apply: bool = False,
    max_revivals: int = DEFAULT_MAX_REVIVALS,
    max_batch: int = DEFAULT_MAX_BATCH,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """一次分诊（dry-run 绝不写库）。返回可机检的 JSON 结果字典。"""
    rows, total = store.fetch_blocked()
    plan = build_plan(rows, max_revivals=max_revivals, max_batch=max_batch, total_blocked=total)

    result: Dict[str, Any] = {
        "tool": "tools/blocked_node_triage.py",
        "mode": "apply" if apply else "dry-run",
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "store": store.name,
        "max_revivals": plan["max_revivals"],
        "max_batch": plan["max_batch"],
        "blocked_total": plan["blocked_total"],
        "classified_total": plan["classified_total"],
        "coverage_ok": plan["coverage_ok"],
        "by_class": plan["by_class"],
        "by_decision": plan["by_decision"],
        "selected_count": plan["selected_count"],
        "deferred_by_batch": plan["deferred_by_batch"],
        "requeued": 0,
        "decisions": plan["decisions"],
    }

    result["blocked_before"] = total
    if apply and plan["selected_count"]:
        applied = store.apply(plan)
        result["requeued"] = applied["applied"]
        result["applied_sql"] = applied.get("sql", "")
    result["blocked_after"] = store.blocked_count()
    result["read_only_ok"] = (not apply) and result["blocked_after"] == result["blocked_before"]
    return result


def summarize_text(result: Mapping[str, Any]) -> str:
    lines = [
        f"blocked_node_triage  mode={result['mode']}  store={result['store']}",
        f"generated_at={result['generated_at']}",
        f"blocked_total={result['blocked_total']}  classified={result['classified_total']}  "
        f"coverage_ok={result['coverage_ok']}",
        "by_class: " + "  ".join(f"{k}={v}" for k, v in result["by_class"].items()),
        "by_decision: " + "  ".join(f"{k}={v}" for k, v in result["by_decision"].items()),
        f"requeue_batch={result['selected_count']}/{result['max_batch']}  "
        f"max_revivals={result['max_revivals']}  deferred={result['deferred_by_batch']}",
    ]
    if result["mode"] == "apply":
        lines.append(f"requeued={result['requeued']}  blocked {result['blocked_before']}"
                     f" -> {result['blocked_after']}")
    else:
        lines.append(f"read_only_ok={result['read_only_ok']}  blocked {result['blocked_before']}"
                     f" -> {result['blocked_after']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blocked_node_triage.py",
        description="按 block_reason 前缀对 campaign_nodes 的 blocked 节点做五类分诊；"
                    "默认 --dry-run 只读，--apply 才在复活预算与批次上限内解封 infra 类节点。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="apply", action="store_false",
                   help="只读分诊：只发 SELECT，绝不写库（默认）")
    g.add_argument("--apply", dest="apply", action="store_true",
                   help="执行写库：仅 INFRA_RETRYABLE 且未超复活预算的节点置回 pending 并写 block_resolved 事件")
    p.add_argument("--max-revivals", type=int, default=DEFAULT_MAX_REVIVALS,
                   help="每节点复活预算上限（达到上限后不再 requeue）")
    p.add_argument("--max-batch", type=int, default=DEFAULT_MAX_BATCH,
                   help="单次运行最多解封多少个节点（硬上限）")
    p.add_argument("--db", default=os.environ.get("TP_PG_DB", "tensorplatform"), help="数据库名")
    p.add_argument("--host", default=os.environ.get("TP_PG_HOST", "127.0.0.1"), help="PG 主机")
    p.add_argument("--user", default=os.environ.get("TP_PG_USER", "foundation"), help="PG 用户")
    p.add_argument("--peer-user", default="postgres",
                   help="无密码时的本地 peer 认证 OS 用户（经 su 执行 psql）")
    p.add_argument("--json-out", default="", help="把结果 JSON 写到此路径（默认打到 stdout）")
    p.add_argument("--format", choices=("json", "text"), default="json", help="stdout 输出格式")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        transport = PsqlTransport(db=args.db, host=args.host, user=args.user,
                                  peer_user=args.peer_user)
        result = run(PgStore(transport), apply=args.apply, max_revivals=args.max_revivals,
                     max_batch=args.max_batch)
    except TriageError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    if args.format == "text":
        print(summarize_text(result))
    else:
        print(json.dumps({k: v for k, v in result.items() if k != "applied_sql"},
                         ensure_ascii=False, indent=2))
    if not result["coverage_ok"]:
        print("ERROR coverage mismatch: classified != blocked count", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())