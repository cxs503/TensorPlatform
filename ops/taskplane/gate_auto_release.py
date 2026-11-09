#!/usr/bin/env python3
"""gate_auto_release —— 放门自动化 daemon（战役 DAG 门自动放行 / IN20-R2）。

替代「人工 UPDATE campaign_nodes SET status='pending'」的放门动作。设计约束来自
owner 渐退令（假设明天没有人类来）：

  * 机制替身独立于任何人类会话与 AI 协调者会话：无常驻 LLM、无平台 API 令牌，
    只读 `campaign_nodes` + 本地文件系统，可作容器 daemon / systemd 单元常跑；
  * 决策是纯函数 `evaluate_gate()`：可单测、可离线复算，同一输入必得同一结论；
  * 写路径（释放/标记/回滚）全部落到 `gate_release_audit` 审计表 + JSONL 审计流；
  * 每次自动释放都可用 `--rollback <audit_id>` 回到 blocked（回滚也进审计）；
  * 治理规则变更类门（规范修订/规范落实/SPEC 等）**永不自动放行**，只显式写
    `metadata.owner_only=1` 留人工位；
  * 判据不满足 → 继续等，绝不写 `failed`（门失败只能由人/裁决链判）。

放门判据（全部满足才释放，否则等）：

  C1 上游就绪：`dependencies` 里每个节点都存在且 `status='done'`；
  C2 判据工件可解析：门 metadata 声明的验收证据路径（`gate_criteria.artifacts`
     或 `acceptance_artifacts`，缺省从 `goal` / `acceptance_criteria` 文本里
     抽取 `artifacts/...|runs/...|docs/...` 路径）在配置的 roots 里能解析到
     非空文件；`.json` 工件还必须能 json 解析（「可解析」按字面执行）；
     `metadata.repo` 存在时，命中路径必须落在这个仓里（防同名工件跨仓误判）；
  C3 非治理门：`metadata.owner_only` 非真，且门名/goal 不含治理标记。

C2b 判据内容（增量，仅当门 metadata 声明了 `gate_criteria.checks` /
`acceptance_checks` 时生效）：逐条求值「工件里的某值必须满足某算子阈值」，三态裁决
PASS / FAIL / WITHHELD（证据不足绝不折算成 PASS），任一非 PASS 即继续等，
理由为 `content_fail` / `content_withheld`。未声明 checks 的门行为**完全不变**。
实现见 `ops/taskplane/gate_content_criteria.py`（纯函数，可离线复算）。

`no_criteria_declared`（一条判据路径都没声明）按「不满足」处理（只等不放），
因为不可验证的判据不能自动放行。

CLI：
    python3 ops/taskplane/gate_auto_release.py --once --dry-run
    python3 ops/taskplane/gate_auto_release.py --once --json-out artifacts/gate_drill.json
    python3 ops/taskplane/gate_auto_release.py --loop --interval 300
    python3 ops/taskplane/gate_auto_release.py --rollback 42
"""
from __future__ import annotations

import argparse
import dataclasses
import glob as globmod
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # 包内导入（python3 -m ops.taskplane.gate_auto_release / from ops.taskplane import ...）
    from . import gate_content_criteria as content_criteria
except ImportError:  # 直接以脚本或顶层模块方式加载（sys.path 含 ops/taskplane）
    import gate_content_criteria as content_criteria  # type: ignore[no-redef]

# ─────────────────────────── 常量 ───────────────────────────

GATE_NODE_TYPE = "gate"
FROM_STATUS = "blocked"          # 只处理被阻塞的门
RELEASED_STATUS = "pending"      # 门放行后的状态（下游派发器据此可见）

GOVERNANCE_CLASSES = {"governance", "spec_revision", "policy"}
GOVERNANCE_MARKERS = ("SPEC军", "规范修订", "规范落实", "治理规则", "spec_revision", "governance")

ARTIFACT_TOKEN_RE = re.compile(
    r"(?<![\w/.\-])((?:artifacts|runs|docs|reports)/[A-Za-z0-9_][A-Za-z0-9_./*\-]*)"
)

TRUTHY = {"1", "true", "yes", "on"}

ACTION_RELEASE = "release"
ACTION_WAIT = "wait"
ACTION_HOLD_OWNER_ONLY = "hold_owner_only"
ACTION_ROLLBACK = "rollback"

AUDIT_DDL = """CREATE TABLE IF NOT EXISTS {audit} (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    node_id BIGINT NOT NULL,
    plan_id BIGINT,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    reason TEXT,
    detail JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    actor TEXT NOT NULL,
    run_id TEXT,
    rolled_back BOOLEAN NOT NULL DEFAULT false,
    rolled_back_at TIMESTAMPTZ,
    rollback_of BIGINT
);
CREATE INDEX IF NOT EXISTS idx_gate_audit_node ON {audit}(node_id, created_at DESC);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────── 纯决策层 ───────────────────────────

@dataclasses.dataclass(frozen=True)
class Criteria:
    """门的判据声明（从 metadata 解析，不改库）。"""
    artifacts: tuple[str, ...]
    owner_only: bool
    owner_only_reason: str
    declared_by: str

    def to_dict(self) -> dict:
        return {
            "artifacts": list(self.artifacts),
            "owner_only": self.owner_only,
            "owner_only_reason": self.owner_only_reason,
            "declared_by": self.declared_by,
        }


@dataclasses.dataclass(frozen=True)
class ArtifactCheck:
    pattern: str
    resolved: bool
    path: str | None
    detail: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Decision:
    node_id: int
    plan_id: int | None
    name: str
    action: str
    reason: str
    criteria: Criteria
    checks: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "plan_id": self.plan_id,
            "name": self.name,
            "action": self.action,
            "reason": self.reason,
            "criteria": self.criteria.to_dict(),
            "checks": self.checks,
        }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    return False


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def parse_criteria(metadata: Mapping[str, Any] | None, name: str = "") -> Criteria:
    """把门 metadata 解析成结构化判据（纯函数，无 IO）。"""
    meta = dict(metadata or {})
    artifacts: list[str] = []
    declared_by = "none"

    gate_criteria = meta.get("gate_criteria") or {}
    if isinstance(gate_criteria, dict):
        artifacts.extend(_as_text_list(gate_criteria.get("artifacts")))
    if artifacts:
        declared_by = "metadata.gate_criteria"

    if not artifacts:
        artifacts.extend(_as_text_list(meta.get("acceptance_artifacts")))
        if artifacts:
            declared_by = "metadata.acceptance_artifacts"

    if not artifacts:
        blob_parts = [str(meta.get("goal") or "")]
        blob_parts.extend(_as_text_list(meta.get("acceptance_criteria")))
        seen: list[str] = []
        for part in blob_parts:
            for tok in ARTIFACT_TOKEN_RE.findall(part):
                tok = tok.rstrip(".,;:)]}、，。")
                if tok and tok not in seen:
                    seen.append(tok)
        artifacts = seen
        if artifacts:
            declared_by = "goal_text"

    uniq: list[str] = []
    for a in artifacts:
        a = a.strip()
        if a and a not in uniq:
            uniq.append(a)

    # ── owner_only / 治理门判定 ──
    owner_only = False
    reason = ""
    if _truthy(meta.get("owner_only")):
        owner_only = True
        reason = str(meta.get("owner_only_reason") or "metadata_owner_only")
    elif str(meta.get("gate_class") or "").strip().lower() in GOVERNANCE_CLASSES:
        owner_only = True
        reason = f"gate_class={meta.get('gate_class')}"
    else:
        haystack = " ".join([name or "", str(meta.get("goal") or ""),
                             str(meta.get("gate_class") or "")])
        for marker in GOVERNANCE_MARKERS:
            if marker in haystack:
                owner_only = True
                reason = f"governance_marker={marker}"
                break

    return Criteria(tuple(uniq), owner_only, reason, declared_by)


def _repo_of(metadata: Mapping[str, Any] | None) -> str | None:
    repo = (metadata or {}).get("repo")
    return (str(repo).strip() or None) if repo else None


def resolve_artifact(pattern: str, roots: Sequence[str], repo: str | None = None) -> ArtifactCheck:
    """解析单条判据工件路径：存在 + 非空 +（json 可解析）+（仓归属匹配）。"""
    if os.path.isabs(pattern):
        candidates = [pattern]
    else:
        candidates = [os.path.join(root, pattern) for root in roots]

    hits: list[str] = []
    for cand in candidates:
        for hit in globmod.glob(cand):
            if os.path.isfile(hit) and os.path.getsize(hit) > 0:
                hits.append(hit)

    if not hits:
        return ArtifactCheck(pattern, False, None, "not_found")

    if repo:
        in_repo = [h for h in hits if f"/{repo}/" in h]
        if not in_repo:
            return ArtifactCheck(pattern, False, hits[0], f"repo_mismatch(expect {repo})")
        hits = in_repo

    hits.sort(key=len)
    hit = hits[0]
    if hit.endswith(".json"):
        try:
            with open(hit, "r", encoding="utf-8") as fh:
                json.load(fh)
        except Exception as exc:  # noqa: BLE001 - 损坏工件按不满足
            return ArtifactCheck(pattern, False, hit, f"json_invalid: {type(exc).__name__}")
    return ArtifactCheck(pattern, True, hit, "ok")


def evaluate_gate(
    node: Mapping[str, Any],
    nodes_by_id: Mapping[int, Mapping[str, Any]],
    roots: Sequence[str],
    resolver=resolve_artifact,
) -> Decision:
    """对单个门节点下判（纯函数）。action ∈ {release, wait, hold_owner_only}。"""
    node_id = int(node["id"])
    plan_id = node.get("plan_id")
    name = str(node.get("name") or "")
    meta = node.get("metadata") or {}
    crit = parse_criteria(meta, name)
    repo = _repo_of(meta)

    # C1 上游就绪
    deps = [int(d) for d in (node.get("dependencies") or [])]
    missing = [d for d in deps if d not in nodes_by_id]
    pending = [
        {"node_id": d, "status": nodes_by_id[d].get("status")}
        for d in deps
        if d in nodes_by_id and nodes_by_id[d].get("status") != "done"
    ]
    upstream_ok = not missing and not pending

    # C2 判据工件
    artifact_checks = [resolver(p, roots, repo) for p in crit.artifacts]
    unresolved = [c for c in artifact_checks if not c.resolved]
    artifacts_ok = bool(artifact_checks) and not unresolved

    # C2b 判据内容（仅在门显式声明 checks 时生效；声明不合法 → 只等不放，不静默跳过）
    content_error: str | None = None
    try:
        content = content_criteria.evaluate_content(meta, roots, repo)
    except content_criteria.SpecError as exc:
        content = None
        content_error = f"content_spec_invalid: {exc}"

    checks = {
        "upstream": {"dependencies": deps, "missing": missing,
                     "not_done": pending, "ok": upstream_ok},
        "artifacts": {"declared_by": crit.declared_by,
                      "items": [c.to_dict() for c in artifact_checks],
                      "ok": artifacts_ok},
        "content": {"ok": True, "declared": False, "verdict": "NOT_DECLARED"}
                   if (content is not None and not content.declared) else {
            "ok": bool(content is not None and content.passed),
            "error": content_error,
            "declared": bool(content is not None and content.declared),
            "verdict": content.verdict if content is not None else "SPEC_INVALID",
            "digest": content.digest() if content is not None else None,
            "checks": [c.to_dict() for c in (content.checks if content is not None else [])],
        },
        "owner_only": {"flag": crit.owner_only, "reason": crit.owner_only_reason,
                       "ok": not crit.owner_only},
    }

    if crit.owner_only:
        return Decision(node_id, plan_id, name, ACTION_HOLD_OWNER_ONLY,
                        crit.owner_only_reason, crit, checks)
    if not crit.artifacts:
        return Decision(node_id, plan_id, name, ACTION_WAIT, "no_criteria_declared", crit, checks)
    if not upstream_ok:
        return Decision(node_id, plan_id, name, ACTION_WAIT,
                        "upstream_missing" if missing else "upstream_pending", crit, checks)
    if not artifacts_ok:
        return Decision(node_id, plan_id, name, ACTION_WAIT, "artifact_missing", crit, checks)
    if content_error is not None:
        return Decision(node_id, plan_id, name, ACTION_WAIT, "content_spec_invalid", crit, checks)
    if content is not None and content.blocking:
        reason = ("content_fail" if content.verdict == content_criteria.VERDICT_FAIL
                  else "content_withheld")
        return Decision(node_id, plan_id, name, ACTION_WAIT, reason, crit, checks)
    return Decision(node_id, plan_id, name, ACTION_RELEASE, "satisfied", crit, checks)


# ─────────────────────────── 存储层 ───────────────────────────

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _lit(value: Any) -> str:
    """SQL 字面量（单引号转义；None → NULL）。"""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _lit_json(value: Any) -> str:
    return _lit(json.dumps(value, ensure_ascii=False, default=str))


class Store:
    """放门存储接口（节点状态 + 审计）。决策逻辑不感知后端。"""

    def nodes(self) -> list[dict]:
        raise NotImplementedError

    def release(self, node_id: int, *, actor: str, run_id: str, reason: str,
                detail: Mapping[str, Any]) -> int:
        raise NotImplementedError

    def mark_owner_only(self, node_id: int, *, actor: str, run_id: str, reason: str,
                        detail: Mapping[str, Any]) -> int:
        raise NotImplementedError

    def audit_wait(self, node_id: int, plan_id: int | None, *, actor: str, run_id: str,
                   reason: str, detail: Mapping[str, Any]) -> int:
        raise NotImplementedError

    def rollback(self, audit_id: int, *, actor: str, run_id: str) -> dict:
        raise NotImplementedError

    def audit_rows(self, *, limit: int = 200) -> list[dict]:
        raise NotImplementedError

    def last_reasons(self) -> dict[str, str]:
        """审计表里每个节点最近一条判据原因（跨进程重启的去重锚）。"""
        return {}

    def snapshot(self) -> str:
        rows = sorted((int(n["id"]), str(n.get("status")), str(n.get("node_type")))
                      for n in self.nodes())
        return json.dumps(rows, separators=(",", ":"))


class MemoryStore(Store):
    """测试/离线演习后端：与 PsqlStore 同一套语义（条件更新 + 审计链）。"""

    def __init__(self, nodes: Iterable[Mapping[str, Any]]):
        self._nodes = {int(n["id"]): dict(n) for n in nodes}
        self.audit: list[dict] = []

    def nodes(self) -> list[dict]:
        return [dict(n) for n in self._nodes.values()]

    def _record(self, node_id: int, plan_id: int | None, action: str, from_status: str,
                to_status: str, reason: str, detail: Mapping[str, Any], actor: str,
                run_id: str, rollback_of: int | None = None) -> int:
        row = {
            "id": len(self.audit) + 1,
            "created_at": _utcnow(),
            "node_id": node_id,
            "plan_id": plan_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "detail": dict(detail),
            "actor": actor,
            "run_id": run_id,
            "rolled_back": False,
            "rolled_back_at": None,
            "rollback_of": rollback_of,
        }
        self.audit.append(row)
        return int(row["id"])

    def release(self, node_id: int, *, actor: str, run_id: str, reason: str,
                detail: Mapping[str, Any]) -> int:
        node = self._nodes.get(int(node_id))
        if not node or node.get("node_type") != GATE_NODE_TYPE or node.get("status") != FROM_STATUS:
            return 0
        node["status"] = RELEASED_STATUS
        node["updated_at"] = _utcnow()
        return self._record(int(node_id), node.get("plan_id"), ACTION_RELEASE, FROM_STATUS,
                            RELEASED_STATUS, reason, detail, actor, run_id)

    def mark_owner_only(self, node_id: int, *, actor: str, run_id: str, reason: str,
                        detail: Mapping[str, Any]) -> int:
        node = self._nodes.get(int(node_id))
        if not node or node.get("node_type") != GATE_NODE_TYPE or node.get("status") != FROM_STATUS:
            return 0
        meta = dict(node.get("metadata") or {})
        if _truthy(meta.get("owner_only")):
            return 0
        meta["owner_only"] = 1
        meta["owner_only_reason"] = reason
        meta["owner_only_marked_at"] = _utcnow()
        meta["owner_only_marked_by"] = actor
        node["metadata"] = meta
        node["updated_at"] = _utcnow()
        return self._record(int(node_id), node.get("plan_id"), ACTION_HOLD_OWNER_ONLY,
                            FROM_STATUS, FROM_STATUS, reason, detail, actor, run_id)

    def audit_wait(self, node_id: int, plan_id: int | None, *, actor: str, run_id: str,
                   reason: str, detail: Mapping[str, Any]) -> int:
        return self._record(int(node_id), plan_id, ACTION_WAIT, FROM_STATUS, FROM_STATUS,
                            reason, detail, actor, run_id)

    def rollback(self, audit_id: int, *, actor: str, run_id: str) -> dict:
        target = next((r for r in self.audit if int(r["id"]) == int(audit_id)), None)
        if not target or target["action"] != ACTION_RELEASE or target["rolled_back"]:
            return {"ok": False, "reason": "audit_row_not_rollbackable"}
        node = self._nodes.get(int(target["node_id"]))
        if not node or node.get("status") != RELEASED_STATUS:
            return {"ok": False, "reason": "node_not_in_released_state"}
        node["status"] = FROM_STATUS
        node["updated_at"] = _utcnow()
        target["rolled_back"] = True
        target["rolled_back_at"] = _utcnow()
        new_id = self._record(int(target["node_id"]), target.get("plan_id"), ACTION_ROLLBACK,
                              RELEASED_STATUS, FROM_STATUS, "manual_rollback",
                              {"rollback_of": int(audit_id)}, actor, run_id, int(audit_id))
        return {"ok": True, "rollback_audit_id": new_id, "node_id": int(target["node_id"]),
                "status": node["status"]}

    def audit_rows(self, *, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.audit[-limit:]]

    def last_reasons(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in self.audit:            # 顺序扫描 → 后写覆盖，天然「最近一条」
            out[str(row["node_id"])] = str(row.get("reason"))
        return out


class PsqlStore(Store):
    """PostgreSQL 后端（与 ops/taskplane 其它守护同款 psql 通道，免额外依赖）。

    写路径全部是「条件 UPDATE + 审计 INSERT」的**单条语句 CTE**：条件不满足时
    审计也不会插入，天然幂等（重复跑不会二次放门/二次记账）。
    """

    def __init__(self, *, table: str = "campaign_nodes", audit: str = "gate_release_audit",
                 host: str = "127.0.0.1", user: str = "foundation",
                 dbname: str = "tensorplatform", password: str | None = None,
                 timeout: int = 60):
        self.table = _ident(table)
        self.audit = _ident(audit)
        # host 为空 → 走本地 unix socket（部署机上 daemon 常以 postgres 角色跑）
        self.conn = ((["-h", host] if host else []) + ["-U", user, "-d", dbname])
        self.timeout = timeout
        self.env = dict(os.environ)
        pw = password if password is not None else (os.environ.get("PGPASSWORD")
                                                    or os.environ.get("TP_PG_PASSWORD"))
        if pw:
            self.env["PGPASSWORD"] = pw

    def _query(self, sql: str) -> str:
        proc = subprocess.run(
            ["psql", "-X", "-q", "-tA", "-v", "ON_ERROR_STOP=1", *self.conn, "-c", sql],
            capture_output=True, text=True, env=self.env, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed: {proc.stderr.strip()[:500]}")
        return proc.stdout.strip()

    def ensure_audit_table(self) -> None:
        self._query(AUDIT_DDL.format(audit=self.audit))

    def nodes(self) -> list[dict]:
        out = self._query(
            "SELECT coalesce(jsonb_agg(row_to_json(t)), '[]'::jsonb) FROM ("
            " SELECT id, plan_id, name, node_type, status, dependencies, metadata"
            f" FROM {self.table}) t;"
        )
        return json.loads(out or "[]")

    def audit_rows(self, *, limit: int = 200) -> list[dict]:
        out = self._query(
            "SELECT coalesce(jsonb_agg(row_to_json(t)), '[]'::jsonb) FROM ("
            f" SELECT * FROM {self.audit} ORDER BY id DESC LIMIT {int(limit)}) t;"
        )
        return json.loads(out or "[]")

    def last_reasons(self) -> dict[str, str]:
        out = self._query(
            "SELECT coalesce(jsonb_agg(row_to_json(t)), '[]'::jsonb) FROM ("
            f" SELECT DISTINCT ON (node_id) node_id, reason FROM {self.audit}"
            " ORDER BY node_id, id DESC) t;"
        )
        return {str(r["node_id"]): str(r["reason"]) for r in json.loads(out or "[]")}

    def release(self, node_id: int, *, actor: str, run_id: str, reason: str,
                detail: Mapping[str, Any]) -> int:
        sql = (
            "WITH upd AS ("
            f" UPDATE {self.table} SET status={_lit(RELEASED_STATUS)}, updated_at=now()"
            f" WHERE id={int(node_id)} AND node_type={_lit(GATE_NODE_TYPE)}"
            f" AND status={_lit(FROM_STATUS)} RETURNING id, plan_id),"
            " ins AS ("
            f" INSERT INTO {self.audit}(node_id, plan_id, action, from_status, to_status,"
            " reason, detail, actor, run_id) SELECT upd.id, upd.plan_id,"
            f" {_lit(ACTION_RELEASE)}, {_lit(FROM_STATUS)}, {_lit(RELEASED_STATUS)},"
            f" {_lit(reason)}, {_lit_json(detail)}, {_lit(actor)}, {_lit(run_id)} FROM upd"
            " RETURNING id)"
            " SELECT coalesce(max(id), 0) FROM ins;"
        )
        return int(self._query(sql) or 0)

    def mark_owner_only(self, node_id: int, *, actor: str, run_id: str, reason: str,
                        detail: Mapping[str, Any]) -> int:
        sql = (
            "WITH upd AS ("
            f" UPDATE {self.table} SET"
            " metadata = metadata || jsonb_build_object("
            f"'owner_only', 1, 'owner_only_reason', {_lit(reason)},"
            f" 'owner_only_marked_at', now()::text, 'owner_only_marked_by', {_lit(actor)}),"
            " updated_at = now()"
            f" WHERE id={int(node_id)} AND node_type={_lit(GATE_NODE_TYPE)}"
            f" AND status={_lit(FROM_STATUS)}"
            " AND coalesce(metadata->>'owner_only', '') NOT IN ('1','true','True')"
            " RETURNING id, plan_id),"
            " ins AS ("
            f" INSERT INTO {self.audit}(node_id, plan_id, action, from_status, to_status,"
            " reason, detail, actor, run_id) SELECT upd.id, upd.plan_id,"
            f" {_lit(ACTION_HOLD_OWNER_ONLY)}, {_lit(FROM_STATUS)}, {_lit(FROM_STATUS)},"
            f" {_lit(reason)}, {_lit_json(detail)}, {_lit(actor)}, {_lit(run_id)} FROM upd"
            " RETURNING id)"
            " SELECT coalesce(max(id), 0) FROM ins;"
        )
        return int(self._query(sql) or 0)

    def audit_wait(self, node_id: int, plan_id: int | None, *, actor: str, run_id: str,
                   reason: str, detail: Mapping[str, Any]) -> int:
        plan_sql = int(plan_id) if plan_id is not None else "NULL"
        sql = (
            f"INSERT INTO {self.audit}(node_id, plan_id, action, from_status, to_status,"
            " reason, detail, actor, run_id, rollback_of) VALUES ("
            f"{int(node_id)}, {plan_sql}, {_lit(ACTION_WAIT)}, {_lit(FROM_STATUS)},"
            f" {_lit(FROM_STATUS)}, {_lit(reason)}, {_lit_json(detail)},"
            f" {_lit(actor)}, {_lit(run_id)}, NULL) RETURNING id;"
        )
        return int(self._query(sql) or 0)

    def rollback(self, audit_id: int, *, actor: str, run_id: str) -> dict:
        sql = (
            "WITH last AS ("
            f" SELECT * FROM {self.audit} WHERE id={int(audit_id)}"
            f" AND action={_lit(ACTION_RELEASE)} AND rolled_back = false),"
            " upd AS ("
            f" UPDATE {self.table} SET status={_lit(FROM_STATUS)}, updated_at=now()"
            " WHERE id=(SELECT node_id FROM last)"
            f" AND node_type={_lit(GATE_NODE_TYPE)}"
            f" AND status={_lit(RELEASED_STATUS)} RETURNING id, plan_id),"
            " ins AS ("
            f" INSERT INTO {self.audit}(node_id, plan_id, action, from_status, to_status,"
            " reason, detail, actor, run_id, rollback_of)"
            f" SELECT last.node_id, last.plan_id, {_lit(ACTION_ROLLBACK)},"
            f" {_lit(RELEASED_STATUS)}, {_lit(FROM_STATUS)}, 'manual_rollback',"
            f" jsonb_build_object('rollback_of', last.id), {_lit(actor)},"
            f" {_lit(run_id)}, last.id"
            " FROM last WHERE EXISTS (SELECT 1 FROM upd) RETURNING id, node_id, rollback_of),"
            " mark AS ("
            " UPDATE " + self.audit + " SET rolled_back = true, rolled_back_at = now()"
            " WHERE id=(SELECT rollback_of FROM ins) RETURNING id)"
            " SELECT coalesce(jsonb_agg(row_to_json(ins)), '[]'::jsonb) FROM ins;"
        )
        rows = json.loads(self._query(sql) or "[]")
        if not rows:
            return {"ok": False, "reason": "audit_row_not_rollbackable_or_node_not_released"}
        return {"ok": True, "rollback_audit_id": rows[0]["id"],
                "node_id": rows[0]["node_id"], "status": FROM_STATUS}


# ─────────────────────────── 守护循环 ───────────────────────────

def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 状态缺失/损坏 → 冷启动
        return {}


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def run_once(
    store: Store,
    *,
    roots: Sequence[str],
    actor: str = "gate-auto-release",
    run_id: str | None = None,
    dry_run: bool = False,
    state_path: Path | None = None,
    audit_waits: bool = True,
    jsonl_path: Path | None = None,
) -> dict:
    """一轮放门：扫 blocked 门 → 下判 → 释放/标记/等待 → 审计。"""
    run_id = run_id or (f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                        f"-{uuid.uuid4().hex[:6]}")
    nodes = store.nodes()
    by_id = {int(n["id"]): n for n in nodes}
    gates = [n for n in nodes
             if n.get("node_type") == GATE_NODE_TYPE and n.get("status") == FROM_STATUS]
    gates.sort(key=lambda n: int(n["id"]))

    state = _load_state(state_path) if state_path else {}
    prev_reasons: dict[str, str] = dict(store.last_reasons())
    prev_reasons.update(state.get("last_reason") or {})

    results: list[dict] = []
    counts = {ACTION_RELEASE: 0, ACTION_WAIT: 0, ACTION_HOLD_OWNER_ONLY: 0}
    snapshot_before = store.snapshot()

    for gate in gates:
        decision = evaluate_gate(gate, by_id, roots)
        rec = decision.to_dict()
        rec.update({"audit_id": 0, "applied": False})
        if not dry_run:
            if decision.action == ACTION_RELEASE:
                aid = store.release(decision.node_id, actor=actor, run_id=run_id,
                                    reason=decision.reason, detail=rec["checks"])
                rec["audit_id"], rec["applied"] = aid, bool(aid)
            elif decision.action == ACTION_HOLD_OWNER_ONLY:
                aid = store.mark_owner_only(decision.node_id, actor=actor, run_id=run_id,
                                            reason=decision.reason, detail=rec["checks"])
                rec["audit_id"], rec["applied"] = aid, bool(aid)
            else:
                key = str(decision.node_id)
                flipped = prev_reasons.get(key) != decision.reason
                if audit_waits and flipped:
                    aid = store.audit_wait(decision.node_id, decision.plan_id, actor=actor,
                                           run_id=run_id, reason=decision.reason,
                                           detail=rec["checks"])
                    rec["audit_id"] = aid
                rec["audit_id"] = rec["audit_id"] or 0
            prev_reasons[str(decision.node_id)] = decision.reason
        counts[decision.action] = counts.get(decision.action, 0) + 1
        results.append(rec)

    snapshot_after = store.snapshot()
    summary = {
        "run_id": run_id,
        "generated_at": _utcnow(),
        "actor": actor,
        "dry_run": dry_run,
        "roots": list(roots),
        "blocked_gates": len(gates),
        "counts": counts,
        "released": [r["node_id"] for r in results if r["action"] == ACTION_RELEASE],
        "held_owner_only": [r["node_id"] for r in results
                            if r["action"] == ACTION_HOLD_OWNER_ONLY],
        "waiting": [{"node_id": r["node_id"], "reason": r["reason"]}
                    for r in results if r["action"] == ACTION_WAIT],
        "snapshot_changed": snapshot_before != snapshot_after,
        "nodes": results,
    }

    if state_path and not dry_run:
        _save_state(state_path, {
            "run_id": run_id,
            "updated_at": summary["generated_at"],
            "last_reason": prev_reasons,
        })

    if jsonl_path and not dry_run:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as fh:
            for r in results:
                if r["action"] != ACTION_WAIT or r["audit_id"]:
                    fh.write(json.dumps({"ts": summary["generated_at"], "actor": actor,
                                         "run_id": run_id, **r}, ensure_ascii=False) + "\n")
    return summary


# ─────────────────────────── CLI ───────────────────────────

def build_store(args: argparse.Namespace) -> Store:
    table = args.table
    audit = args.audit_table
    if args.sandbox_schema:
        table = f"{args.sandbox_schema}.{table}"
        audit = f"{args.sandbox_schema}.{audit}"
    return PsqlStore(table=table, audit=audit, host=args.pg_host, user=args.pg_user,
                     dbname=args.pg_db, password=args.pg_password)


def _default_roots() -> list[str]:
    env = os.environ.get("GATE_ARTIFACT_ROOTS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [os.getcwd(), "/root/tp-agent/work/*"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="放门自动化 daemon（战役 DAG 门）")
    parser.add_argument("--once", action="store_true", help="跑一轮（默认）")
    parser.add_argument("--loop", action="store_true", help="常驻轮询")
    parser.add_argument("--interval", type=int, default=300, help="轮询间隔秒（默认 300）")
    parser.add_argument("--dry-run", action="store_true", help="只下判不写库")
    parser.add_argument("--rollback", type=int, metavar="AUDIT_ID",
                        help="按审计 id 回滚一次自动释放")
    parser.add_argument("--artifact-roots", default=None,
                        help="判据工件解析根目录（os.pathsep 分隔，支持 glob）")
    parser.add_argument("--state-dir", default="artifacts/gate_auto_release",
                        help="状态/审计落盘目录")
    parser.add_argument("--json-out", default=None, help="本轮结果 JSON 路径")
    parser.add_argument("--no-audit-waits", action="store_true",
                        help="不为等待态写审计（默认写：只在判据原因变化时记一条）")
    parser.add_argument("--actor", default="gate-auto-release")
    parser.add_argument("--pg-host", default=os.environ.get("TP_PG_HOST", "127.0.0.1"))
    parser.add_argument("--pg-user", default=os.environ.get("TP_PG_USER", "foundation"))
    parser.add_argument("--pg-db", default=os.environ.get("TP_PG_DB", "tensorplatform"))
    parser.add_argument("--pg-password", default=None)
    parser.add_argument("--table", default="campaign_nodes")
    parser.add_argument("--audit-table", default="gate_release_audit")
    parser.add_argument("--sandbox-schema", default=None,
                        help="演练用 schema（把 table/audit 都限定到该 schema）")
    args = parser.parse_args(argv)

    roots = ([p for p in args.artifact_roots.split(os.pathsep) if p]
             if args.artifact_roots else _default_roots())
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    jsonl_path = state_dir / "audit.jsonl"

    store = build_store(args)
    if not args.dry_run and not args.sandbox_schema:
        if hasattr(store, "ensure_audit_table"):
            store.ensure_audit_table()

    if args.rollback is not None:
        out = store.rollback(args.rollback, actor=f"{args.actor}-manual",
                             run_id=f"rollback-{int(time.time())}")
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2

    def cycle() -> dict:
        return run_once(
            store, roots=roots, actor=args.actor, dry_run=args.dry_run,
            state_path=None if args.dry_run else state_path,
            audit_waits=not args.no_audit_waits,
            jsonl_path=None if args.dry_run else jsonl_path,
        )

    if args.loop:
        stop = {"flag": False}

        def _term(signum, frame):  # noqa: ARG001
            stop["flag"] = True

        signal.signal(signal.SIGTERM, _term)
        signal.signal(signal.SIGINT, _term)
        while not stop["flag"]:
            try:
                summary = cycle()
                print(json.dumps({"ts": summary["generated_at"], "counts": summary["counts"],
                                  "released": summary["released"]}, ensure_ascii=False),
                      flush=True)
                if args.json_out:
                    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                    Path(args.json_out).write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - 守护不因单轮异常退出
                print(f"[gate-auto-release] cycle error: {exc}", file=sys.stderr, flush=True)
            for _ in range(max(1, args.interval)):
                if stop["flag"]:
                    break
                time.sleep(1)
        return 0

    summary = cycle()
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps({k: summary[k] for k in
                      ("run_id", "dry_run", "blocked_gates", "counts", "released",
                       "held_owner_only", "waiting", "snapshot_changed")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())