"""派发前可行性先验校准器 —— 数据层（只读战役节点历史，零领域语义）。

数据单元
--------
一个样本 = 一条 ``campaign_nodes`` 行（挂 ``campaign_plans``），字段只有平台共性语义：
状态、节点类型、依赖数组、``metadata``（``acceptance_criteria`` / ``risk`` / ``repo`` /
``is_maturity`` / ``budget_hours``）、创建时刻。**不含任何物理域词条**：判据只按
JSON 结构数数（几条判据、几条带数值阈值、几条带布尔门），不解释判据内容。

标签口径（三档，敏感性对照用；都在报告里报）
--------------------------------------------
* ``primary`` —— ``done`` → 1；``blocked`` → 0。``pending`` / ``in-flight`` / ``orphan``
  **剔除**：它们没有结局（右删失），拿它们当负例等于把「还没跑完」判死。
* ``strict``  —— ``done`` → 1；``blocked`` 且 ``block_reason`` 是**任务本身失败**
  （``mission failed`` / ``hollow success``，即验收没过/空壳）→ 0；``infra x*``（agent 丢失、
  LLM 报错）这类**运维事故**不计为可行性失败，剔除。
* ``merged``  —— 只用于 N 元敏感性检查的计数，不作为主判据。

时间纪律（防泄漏）
------------------
所有时序特征（repo 成熟度、提案新颖度、同计划进度）都只在**严格更早**的样本上计算：
「给前 k 个节点算特征」与「给全部节点算特征后取前 k 个」必须逐值相同
（``tests/test_pre_dispatch_prior.py::test_features_are_causal`` 钉死）。
时间戳先按 ``(created_at, node_id)`` 排序，排序键即因果序。
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Iterable, Sequence

#: campaign_nodes.status 取值（镜像 backend/api/campaign_plans.py 的 CHECK 约束；改约束须同步）
STATUS_SUCCESS = "done"
STATUS_FAILURE = ("failed", "blocked")
STATUS_CENSORED = ("pending", "in-flight", "orphan")

#: block_reason 里代表「任务本身没通过验收」的记号（可行性失败）
FEASIBILITY_FAILURE_MARKERS = ("mission failed", "hollow success")

LABEL_DEFINITIONS = {
    "primary": "done=1 ; blocked|failed=0 ; pending|in-flight|orphan=剔除(右删失)",
    "strict": "done=1 ; blocked 且 block_reason 属可行性失败=0 ; 其余剔除(含 infra 运维事故)",
}


# ───────────────────────────── 记录 ─────────────────────────────

@dataclasses.dataclass(frozen=True)
class RawNode:
    """一条战役节点的只读快照（features 层的输入）。"""

    node_id: int
    plan_id: int
    created_at: str          # ISO8601（排序键之一）
    plan_created_at: str     # ISO8601（计划级时间切分的键）
    status: str
    node_type: str
    text: str
    dependencies: tuple[int, ...]
    metadata: dict[str, Any]

    # -- 派生 --------------------------------------------------------
    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.created_at, self.node_id)

    @property
    def plan_sort_key(self) -> tuple[str, int]:
        return (self.plan_created_at, self.plan_id)

    @property
    def repo(self) -> str:
        return str(self.metadata.get("repo") or "")

    @property
    def block_reason(self) -> str:
        return str(self.metadata.get("_block_reason") or "")


# ───────────────────────────── 标签 ─────────────────────────────

def label_primary(node: RawNode) -> int | None:
    if node.status == STATUS_SUCCESS:
        return 1
    if node.status in STATUS_FAILURE:
        return 0
    return None


def label_strict(node: RawNode) -> int | None:
    if node.status == STATUS_SUCCESS:
        return 1
    if node.status == "blocked" or node.status == "failed":
        reason = node.block_reason.lower()
        if any(m in reason for m in FEASIBILITY_FAILURE_MARKERS):
            return 0
    return None


LABEL_FUNCS = {"primary": label_primary, "strict": label_strict}


def labeled(nodes: Iterable[RawNode], scheme: str = "primary") -> list[RawNode]:
    """按口径选样本（保持因果序）。"""
    fn = LABEL_FUNCS[scheme]
    out = [n for n in nodes if fn(n) is not None]
    out.sort(key=lambda n: n.sort_key)
    return out


def census(nodes: Sequence[RawNode]) -> dict[str, Any]:
    """样本盘点（写进产物的溯源块）。"""
    by_status: dict[str, int] = {}
    for n in nodes:
        by_status[n.status] = by_status.get(n.status, 0) + 1
    out: dict[str, Any] = {
        "nodes": len(nodes),
        "plans": len({n.plan_id for n in nodes}),
        "by_status": dict(sorted(by_status.items())),
        "window": {
            "first_created_at": min((n.created_at for n in nodes), default=None),
            "last_created_at": max((n.created_at for n in nodes), default=None),
        },
    }
    for scheme in LABEL_FUNCS:
        sel = labeled(nodes, scheme)
        pos = sum(1 for n in sel if LABEL_FUNCS[scheme](n) == 1)
        out[f"label_{scheme}"] = {
            "n": len(sel),
            "pos": pos,
            "neg": len(sel) - pos,
            "definition": LABEL_DEFINITIONS[scheme],
            "window": {
                "first_created_at": min((n.created_at for n in sel), default=None),
                "last_created_at": max((n.created_at for n in sel), default=None),
            },
        }
    # 负例来源拆解：运维事故（infra x*）vs 任务本身未过验收
    kinds = {"infra": 0, "feasibility": 0, "other": 0}
    for n in nodes:
        if n.status not in ("blocked", "failed"):
            continue
        reason = n.block_reason.lower()
        if "infra" in reason:
            kinds["infra"] += 1
        elif any(m in reason for m in FEASIBILITY_FAILURE_MARKERS):
            kinds["feasibility"] += 1
        else:
            kinds["other"] += 1
    out["blocked_reason_kinds"] = kinds
    return out


# ───────────────────────────── 取数（只读） ─────────────────────────────

NODE_SQL = """
SELECT to_jsonb(t)::text FROM (
    SELECT n.id              AS node_id,
           n.plan_id         AS plan_id,
           to_char(n.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"') AS created_at,
           to_char(p.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"') AS plan_created_at,
           n.status          AS status,
           n.node_type       AS node_type,
           coalesce(n.name, '') AS name,
           n.dependencies    AS dependencies,
           n.metadata        AS metadata,
           coalesce(n.block_reason, '') AS block_reason
    FROM campaign_nodes n
    JOIN campaign_plans p ON p.id = n.plan_id
) t
ORDER BY t.created_at, t.node_id
"""


def _psql(sql: str, db: str = "") -> str:
    """只读 psql：优先 TCP+密码，否则 ``su postgres`` 本地 peer 认证（借 campaign_throughput 惯例）。"""
    db = db or os.environ.get("TP_PG_DB", "tensorplatform")
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
        fh.write(sql)
        path = fh.name
    os.chmod(path, 0o644)
    try:
        pwd = os.environ.get("TP_PG_PASSWORD", "")
        if pwd:
            env = dict(os.environ, PGPASSWORD=pwd)
            proc = subprocess.run(
                ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
                 "-U", os.environ.get("TP_PG_USER", "foundation"), "-d", db,
                 "-tA", "-f", path],
                capture_output=True, text=True, env=env, timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(f"psql(TCP) failed: {proc.stderr[:400]}")
            return proc.stdout
        if shutil.which("su") and shutil.which("psql"):
            proc = subprocess.run(["su", "postgres", "-c", f"psql -d {db} -tA -f {path}"],
                                  capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(f"psql(peer) failed: {proc.stderr[:400]}")
            return proc.stdout
        raise RuntimeError("no psql transport (set TP_PG_PASSWORD)")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_iso(value: str) -> str:
    """归一化时间戳：去掉 psql 的 ``+00`` 尾巴歧义，统一成 ``+00:00`` 形式，保证可排序/可复算。"""
    dt = _parse_ts(value)
    if dt.tzinfo is None:
        return dt.isoformat()
    offset = dt.utcoffset()
    if offset is not None and offset.total_seconds() == 0:
        return dt.replace(tzinfo=None).isoformat() + "+00:00"
    return dt.isoformat()


def node_text(name: str, meta: dict[str, Any]) -> str:
    """节点文本（仅用于新颖度特征，不落盘）：name + goal + 判据的字面形态。"""
    parts = [name or "", str(meta.get("goal") or "")]
    crit = meta.get("acceptance_criteria")
    if isinstance(crit, (list, tuple, dict)):
        parts.append(json.dumps(crit, ensure_ascii=False, sort_keys=True))
    elif crit is not None:
        parts.append(str(crit))
    return " \n ".join(p for p in parts if p)


def node_from_row(row: dict[str, Any]) -> RawNode:
    """一行（``to_jsonb`` 形态）→ RawNode。单一解析入口，PG 与 fixture 共用。"""
    meta = dict(row.get("metadata") or {})
    block_reason = str(row.get("block_reason") or "")
    if block_reason:
        meta = dict(meta, _block_reason=block_reason)
    deps_raw = row.get("dependencies") or []
    if isinstance(deps_raw, str):            # "{}" / "{1,2}" 兜底
        body = deps_raw.strip("{}")
        deps_raw = [int(x) for x in body.split(",") if x.strip()]
    name = str(row.get("name") or "")
    return RawNode(
        node_id=int(row["node_id"]), plan_id=int(row["plan_id"]),
        created_at=parse_iso(str(row["created_at"])),
        plan_created_at=parse_iso(str(row.get("plan_created_at") or row["created_at"])),
        status=str(row["status"]), node_type=str(row.get("node_type") or "task"),
        text=node_text(name, meta),
        dependencies=tuple(int(x) for x in deps_raw),
        metadata=meta,
    )


def load_pg(db: str = "") -> list[RawNode]:
    """从线上库读全部战役节点（只读，单一 SQL，无副作用）。每行一个 JSON 对象，避免分隔符歧义。"""
    raw = _psql(NODE_SQL, db)
    nodes: list[RawNode] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        nodes.append(node_from_row(json.loads(line)))
    nodes.sort(key=lambda n: n.sort_key)
    return nodes


def load_fixture(path: str) -> list[RawNode]:
    """离线 fixture（测试/演示用）：JSON 列表，字段同 ``RawNode``（text 可省，由 name+goal 生成）。"""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload["nodes"] if isinstance(payload, dict) else payload
    nodes: list[RawNode] = []
    for r in rows:
        meta = dict(r.get("metadata") or {})
        nodes.append(RawNode(
            node_id=int(r["node_id"]), plan_id=int(r["plan_id"]),
            created_at=parse_iso(r["created_at"]),
            plan_created_at=parse_iso(r.get("plan_created_at") or r["created_at"]),
            status=str(r["status"]), node_type=str(r.get("node_type") or "task"),
            text=str(r.get("text") or node_text(str(r.get("name") or ""), meta)),
            dependencies=tuple(int(x) for x in (r.get("dependencies") or ())),
            metadata=meta,
        ))
    nodes.sort(key=lambda n: n.sort_key)
    return nodes


def dump_fixture(nodes: Sequence[RawNode], path: str) -> None:
    """导出离线 fixture（text 不导，只保留重新生成 text 所需的 name/goal/判据结构）。"""
    rows = []
    for n in nodes:
        meta = {k: v for k, v in n.metadata.items() if k != "_block_reason"}
        rows.append({
            "node_id": n.node_id, "plan_id": n.plan_id,
            "created_at": n.created_at, "plan_created_at": n.plan_created_at,
            "status": n.status, "node_type": n.node_type,
            "dependencies": list(n.dependencies), "metadata": meta,
            "text": n.text,
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "pre_dispatch_prior/fixture/v1", "nodes": rows}, fh,
                  ensure_ascii=False, indent=1, sort_keys=True)