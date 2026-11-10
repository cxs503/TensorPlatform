"""只读语料快照：从线上库导出一份可离线复算的 JSON（PG → snapshot），再纯本地装载。

为何要快照
----------
回测必须**可复算**：判据数字要能被任何人用同一份输入重跑出来。线上库是可变状态
（节点状态每天在变），直接把结果挂在库上等于把证据钉在流沙上。因此本模块提供
``snapshot`` 子命令（唯一联网步骤，纯 SELECT）与 ``load_snapshot``（纯本地）。

快照只保留结构化字段 + 证据文本字段；后者（node name / reason_text）**不参与任何判定**，
落在 ``evidence`` 子键下，便于审计与零领域语义的行为测试（隐去后输出必须不变）。

安全边界
--------
* 只读：``snapshot`` 仅执行 SELECT，不建表、不写库。
* 连接：优先 ``TP_PG_PASSWORD`` 走 TCP；否则回落 ``su postgres -c psql`` 本机 peer 认证。
* 不臆造：库里没有的字段就留 null，不做插值补全。
"""

from __future__ import annotations

import hashlib
import json
import shlex
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .model import Node

SCHEMA = "campaign_optimizer_corpus.v1"
DB = os.environ.get("TP_PG_DATABASE", "tensorplatform")

_NODE_SQL = """
SELECT json_agg(x ORDER BY x.node_id)
FROM (
  SELECT cn.id                                   AS node_id,
         cn.plan_id                              AS plan_id,
         cn.node_type                            AS node_type,
         cn.status                               AS status,
         cn.mission_id                           AS mission_id,
         cn.dependencies                         AS dependencies,
         cn.consecutive_fails                    AS consecutive_fails,
         to_char(cn.created_at, 'YYYY-MM-DD"T"HH24:MI:SSOF')  AS created_at,
         to_char(cn.updated_at, 'YYYY-MM-DD"T"HH24:MI:SSOF')  AS updated_at,
         cn.block_reason                         AS reason_text,
         cn.name                                 AS name,
         m.status                                AS mission_status,
         to_char(m.created_at,  'YYYY-MM-DD"T"HH24:MI:SSOF')  AS mission_created_at,
         to_char(m.heartbeat_at,'YYYY-MM-DD"T"HH24:MI:SSOF')  AS mission_heartbeat_at,
         to_char(m.finished_at, 'YYYY-MM-DD"T"HH24:MI:SSOF')  AS mission_finished_at,
         (m.result IS NOT NULL)                  AS mission_has_result,
         m.repo                                  AS mission_repo
    FROM campaign_nodes cn
    LEFT JOIN tensor_dev_missions m ON m.id = cn.mission_id
) x
"""

_PLAN_SQL = """
SELECT json_agg(p ORDER BY p.plan_id)
FROM (
  SELECT id AS plan_id, status, to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SSOF') AS created_at
    FROM campaign_plans
) p
"""


class SnapshotError(RuntimeError):
    pass


def _psql(sql: str) -> str:
    password = os.environ.get("TP_PG_PASSWORD")
    if password:
        env = dict(os.environ, PGPASSWORD=password)
        cmd = [
            "psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
            "-p", os.environ.get("TP_PG_PORT", "5432"),
            "-U", os.environ.get("TP_PG_USER", "postgres"),
            "-d", DB, "-tAc", sql,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    else:
        if shutil.which("su") is None:
            raise SnapshotError("no TP_PG_PASSWORD and no 'su' available for peer auth")
        flat = " ".join(sql.split())
        proc = subprocess.run(
            ["su", "postgres", "-c",
             f"psql -d {DB} -tAc {shlex.quote(flat)}"],
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        raise SnapshotError(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def _duration_s(row: Dict[str, Any]) -> float:
    """节点工期 = mission 墙钟（finished_at|now − created_at）；缺时间戳则 0（由装载层兜底）。"""
    start = row.get("mission_created_at") or row.get("created_at")
    end = row.get("mission_finished_at") or row.get("mission_heartbeat_at")
    if not start or not end:
        return 0.0
    from datetime import datetime

    def parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    try:
        return max(0.0, (parse(end) - parse(start)).total_seconds())
    except ValueError:
        return 0.0


def build_snapshot(extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_nodes = json.loads(_psql(_NODE_SQL) or "[]")
    raw_plans = json.loads(_psql(_PLAN_SQL) or "[]")
    rows: List[Dict[str, Any]] = []
    for r in raw_nodes:
        r = dict(r)
        r["duration_s"] = round(_duration_s(r), 3)
        rows.append(r)
    payload = {
        "schema": SCHEMA,
        "source": {"database": DB, "kind": "pg-readonly",
                   "statements": ["campaign_nodes LEFT JOIN tensor_dev_missions", "campaign_plans"]},
        "plans": raw_plans,
        "nodes": rows,
    }
    if extra_meta:
        payload["meta"] = extra_meta
    payload["content_hash"] = content_hash(payload)
    return payload


def content_hash(payload: Dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k not in ("content_hash", "generated_utc")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def write_snapshot(path: str, extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = build_snapshot(extra_meta)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return payload


@dataclass
class Corpus:
    """装载后的语料：节点（含证据）与 plan 元数据。"""

    nodes: List[Node]
    plan_meta: Dict[int, Dict[str, Any]]
    content_hash: str
    default_duration_s: float = 10800.0
    min_duration_s: float = 60.0

    def by_plan(self) -> Dict[int, List[Node]]:
        out: Dict[int, List[Node]] = {}
        for n in self.nodes:
            out.setdefault(n.plan_id, []).append(n)
        return {k: sorted(v, key=lambda n: n.node_id) for k, v in sorted(out.items())}

    def effective_duration(self, node: Node) -> float:
        d = node.duration_s
        if d <= 0:
            d = self.default_duration_s
        return max(d, self.min_duration_s)


def load_snapshot(path: str, default_duration_s: float = 10800.0,
                  min_duration_s: float = 60.0) -> Corpus:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("schema") != SCHEMA:
        raise SnapshotError(f"unexpected snapshot schema: {payload.get('schema')!r}")
    nodes: List[Node] = []
    for r in payload["nodes"]:
        dur = float(r.get("duration_s") or 0.0)
        nodes.append(
            Node(
                node_id=int(r["node_id"]),
                plan_id=int(r["plan_id"]),
                status=str(r["status"]),
                node_type=str(r.get("node_type") or "task"),
                mission_id=(int(r["mission_id"]) if r.get("mission_id") is not None else None),
                dependencies=tuple(sorted(int(d) for d in (r.get("dependencies") or []))),
                consecutive_fails=int(r.get("consecutive_fails") or 0),
                duration_s=dur,
                mission_status=str(r.get("mission_status") or ""),
                mission_has_result=bool(r.get("mission_has_result")),
                updated_at=str(r.get("updated_at") or ""),
                name=str(r.get("name") or ""),
                reason_text=str(r.get("reason_text") or ""),
            )
        )
    plan_meta = {int(p["plan_id"]): p for p in payload.get("plans", [])}
    return Corpus(
        nodes=nodes,
        plan_meta=plan_meta,
        content_hash=str(payload.get("content_hash") or content_hash(payload)),
        default_duration_s=default_duration_s,
        min_duration_s=min_duration_s,
    )