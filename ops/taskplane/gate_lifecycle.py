#!/usr/bin/env python3
"""gate_lifecycle —— 战役门生命周期机制（scope_hash 绑定 / 失效传播 / 幂等重判 / 三态语义）。

为什么需要这一层
----------------
现状：战役 DAG 的门（node_type=gate / 汇总验收节点）判定是**一次性**的——判定发生在
某个时刻，之后与真实状态脱钩，永不重判。于是出现三类系统性缺陷：

  D1 **静默过期**：上游证据（数据版本、判据常数、脚本 hash）被改动后，门仍保留旧的
     结论，报告里的结论与仓库里的证据不再对应。
  D2 **无传播**：上游节点从 done 变 failed（或补了新证据），依赖该证据的下游门不会被
     牵连重判，DAG 上的"继承"只存在于叙事里。
  D3 **语义糊在一起**：前置没完成、证据不足、判据不满足三种完全不同的情形被压成一个
     "没通过"，报告无法机读，也无法区分"该等"与"该修"。

本模块把门从"一次性判定"升级为**生命周期机制**（纯函数 + 内容寻址，零领域语义）：

  (1) scope_hash 绑定：门的判定域（scope）由三部分组成——上游节点状态/scope_hash、
      自身声明的证据 sha256、判据（含判据文件内容指纹）；三者规范序列化后取 sha256，
      即该门本次判定所依据的**内容指纹**。证据一变 → scope_hash 变。
  (2) 失效传播：ledger 记录每门上次的 scope_hash 与分项指纹；重判时逐门比对，不一致
      即标 ``stale`` 并指出变了哪一项（evidence / upstream / criteria）；上游变化经
      topo 序遍历自动向下游传递（scope 内嵌上游 scope_hash），一轮重判即达不动点。
  (3) 幂等重判：判定不含 wall clock、时间戳不入哈希、遍历与序列化全序化；同一输入连跑
      N 次逐位一致（``result_fingerprint`` 剔除仅诊断用的计时段）。
  (4) 三态语义：PENDING（前置未完成，该等）/ WITHHELD（证据不足或判据不可判，该补证据）
      / FAIL（判据不满足或上游 FAIL 传染，该修）/ PASS（满足）。
  (5) 跨仓接口契约：``verify_contracts`` 对「生产者→消费者」接口工件做机检（生产者已
      PASS / 两侧解析到同一 sha256 / 字段覆盖），仓名与接口全部来自配置。

设计约束（平台铁律）
--------------------
* 零领域语义：模块内不出现任何领域词条、判据或算例；节点/仓/接口/判据全部经 campaign
  快照 JSON、ledger 与配置注入。
* 纯函数可复算：``evaluate_lifecycle`` / ``update_ledger`` / ``verify_contracts`` 只做
  纯计算；落盘与 DB 读取在 CLI / 装载器一层，DB 只读（``row_to_json`` 导出）。
* 不写库、不改节点状态：修的是"门的判定与证据的绑定关系"，不是调度状态。

CLI
---
    # 1) 从只读 DB 快照构建 campaign（示例：某计划）
    python3 ops/taskplane/gate_lifecycle.py --plan 397 \
        --snapshot-out artifacts/gate_lifecycle/plan397_campaign.json

    # 2) 首跑建 ledger / 再跑比对、传播、幂等
    python3 ops/taskplane/gate_lifecycle.py \
        --campaign artifacts/gate_lifecycle/campaign_fixture.json \
        --contracts artifacts/gate_lifecycle/contracts.json \
        --ledger  artifacts/gate_lifecycle/ledger.json \
        --ledger-out artifacts/gate_lifecycle/ledger.json \
        --roots . --json-out artifacts/gate_lifecycle/result.json \
        --md-out artifacts/gate_lifecycle/result.md

退出码：0 = 全部门 PASS 且契约全绿；2 = 存在非 PASS 门 / stale / 契约破约；
3 = 输入错误（文件缺失、JSON 坏、DAG 成环）。**未就绪绝不返回 0。**
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCOPE_VERSION = "gate-scope/1.0"
LEDGER_SCHEMA = "gate-lifecycle-ledger/1.0"
RESULT_SCHEMA = "gate-lifecycle-result/1.0"
CONTRACTS_SCHEMA = "gate-lifecycle-contracts/1.0"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_PENDING = "PENDING"
STATUS_WITHHELD = "WITHHELD"
ALL_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_PENDING, STATUS_WITHHELD)

MISSING_DIGEST = "MISSING"  # 证据缺失在 scope 内的占位指纹（补上文件即改变 scope_hash）

DEFAULT_CONFIG: dict[str, Any] = {
    "done_statuses": ["done", "completed", "passed", "accepted", "pass"],
    "failed_statuses": ["failed", "fail", "rejected"],
    "latency_budget_ms": 1000.0,
    "allow_empty_criteria": False,
    "treat_unknown_dependency_as": STATUS_WITHHELD,
    "block_on_upstream_fail": True,
    "include_upstream_status_in_scope": True,
}


# ───────────────────────── 规范化 / 指纹 ─────────────────────────

def canonical_json(obj: Any) -> str:
    """确定性 JSON 文本：键排序、紧凑分隔、无 NaN/Inf。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def merge_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key, val in (config or {}).items():
        cfg[key] = val
    return cfg


def config_digest(config: Mapping[str, Any] | None) -> str:
    return sha256_text(canonical_json(merge_config(config)))


# ───────────────────────── 证据解析 ─────────────────────────

def _resolve_path(ref: str, roots: Sequence[str | Path]) -> Path | None:
    path = Path(ref)
    if path.is_absolute():
        return path if path.is_file() else None
    for root in roots:
        cand = Path(root) / ref
        if cand.is_file():
            return cand
    return None


def resolve_evidence(evidence: Iterable[Mapping[str, Any]] | None,
                     roots: Sequence[str | Path] = (".",)) -> dict[str, Any]:
    """把证据声明解析成 ref → sha256。

    证据条目（全部由 campaign JSON 注入，模块不预设任何具体 ref）::

        {"ref": "artifacts/a/out.json", "kind": "file"}                     # 内容寻址
        {"ref": "db:table/42/status", "kind": "value", "value": "done"}     # 值寻址
        {"ref": "ext:pkg/field", "kind": "digest", "sha256": "<64 hex>"}    # 外部登记

    缺失/畸形一律给占位指纹并参与 scope_hash（因此"证据补上"也算 scope 变更）。
    显式 ``required: false`` 的条目缺失不阻塞判定（可选证据）。
    """
    digests: dict[str, str] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    malformed: list[str] = []
    optional_missing: list[str] = []
    for item in evidence or []:
        ref = str(item.get("ref", ""))
        if not ref:
            malformed.append("<empty-ref>")
            continue
        kind = str(item.get("kind", "file"))
        required = bool(item.get("required", True))
        if kind == "file":
            path = _resolve_path(ref, roots)
            if path is None:
                missing.append(ref)
                digests[ref] = MISSING_DIGEST
                if not required:
                    optional_missing.append(ref)
                continue
            digests[ref] = sha256_file(path)
            sources[ref] = str(path)
        elif kind == "value":
            if "value" not in item:
                malformed.append(ref)
                digests[ref] = MISSING_DIGEST
                continue
            digests[ref] = sha256_text(canonical_json(item["value"]))
            sources[ref] = "value"
        elif kind == "digest":
            declared = item.get("sha256")
            if not isinstance(declared, str) or len(declared) != 64:
                malformed.append(ref)
                digests[ref] = MISSING_DIGEST
                continue
            digests[ref] = declared.lower()
            sources[ref] = "digest"
        else:
            malformed.append(ref)
            digests[ref] = MISSING_DIGEST
    return {
        "digests": digests,
        "sources": sources,
        "missing": sorted(missing),
        "malformed": sorted(malformed),
        "optional_missing": sorted(optional_missing),
        "required_missing": sorted(set(missing) - set(optional_missing)),
    }


# ───────────────────────── 判据 ─────────────────────────

_OPS = ("<=", ">=", "<", ">", "==", "!=", "abs<=", "abs>=", "true", "false")


def evaluate_check(check: Mapping[str, Any]) -> dict[str, Any]:
    """单条判据机裁。可判 → evaluable=True；不可判（无 value / 未知 op）→ False。"""
    name = str(check.get("name") or check.get("metric") or "<unnamed>")
    op = str(check.get("op", "<="))
    out: dict[str, Any] = {"name": name, "op": op,
                           "target": check.get("target"), "value": check.get("value")}
    if op not in _OPS:
        out.update(evaluable=False, ok=False, reason="unknown_op")
        return out
    if op in ("true", "false"):
        expect = op == "true"
        out.update(evaluable=True, ok=(bool(check.get("value")) is expect))
        return out
    value = check.get("value")
    target = check.get("target")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        out.update(evaluable=False, ok=False, reason="no_numeric_value")
        return out
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        out.update(evaluable=False, ok=False, reason="no_numeric_target")
        return out
    if op == "<=":
        ok = value <= target
    elif op == ">=":
        ok = value >= target
    elif op == "<":
        ok = value < target
    elif op == ">":
        ok = value > target
    elif op == "==":
        ok = value == target
    elif op == "!=":
        ok = value != target
    elif op == "abs<=":
        ok = abs(value) <= target
    else:  # abs>=
        ok = abs(value) >= target
    out.update(evaluable=True, ok=bool(ok))
    return out


def load_criteria(node: Mapping[str, Any],
                  roots: Sequence[str | Path] = (".",)) -> dict[str, Any]:
    """规范化判据声明：内联判据 + 判据文件内容指纹（两者都进 scope）。"""
    crit = dict(node.get("criteria") or {})
    ref = crit.pop("criteria_ref", None)
    file_digest = None
    if ref:
        path = _resolve_path(str(ref), roots)
        if path is None:
            file_digest = MISSING_DIGEST
        else:
            file_digest = sha256_file(path)
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in ("checks", "constants", "note"):
                        if key in loaded and key not in crit:
                            crit[key] = loaded[key]
            except (OSError, json.JSONDecodeError):
                crit.setdefault("load_error", "unparseable")
    crit["checks"] = [dict(c) for c in crit.get("checks") or []]
    return {"criteria": crit, "criteria_ref": ref, "criteria_file_digest": file_digest}


# ───────────────────────── 判定（三态语义） ─────────────────────────

def adjudicate(node: Mapping[str, Any],
               upstream: Mapping[str, Mapping[str, Any]],
               resolved: Mapping[str, Any],
               criteria_bundle: Mapping[str, Any],
               config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """单门裁决 → {status, reasons, checks}。

    上游条目形状 ``{"status": <裁决态>, "declared_status": <快照声明态>}``；只给声明态
    （未经本轮裁决）时按声明态判——两条路径语义一致，便于单点复用。

    优先级（先阻塞后判据，避免把"没到条件"误报成"不达标"）：
      1. 依赖缺失/成环 → WITHHELD（前置关系本身不可解析）
      2. 自身声明态为失败词 → FAIL（declared_failed）
      3. 自身声明态未达完成词 → PENDING（declared_not_done，该等）
      4. 上游裁决 FAIL → FAIL（upstream_failed，传染）
      5. 上游裁决 PENDING → PENDING（prerequisites_incomplete）
      6. 上游裁决 WITHHELD → WITHHELD（upstream_withheld，证据链断裂向上传染）
      7. 必需证据缺失/畸形 → WITHHELD（该补证据）
      8. 无判据 → WITHHELD（不可验证不可放行；config 可放开）
      9. 判据不可判（无 value）→ WITHHELD
     10. 全部满足 → PASS；否则 FAIL（该修）
    """
    cfg = merge_config(config)
    deps = [str(d) for d in node.get("dependencies") or []]
    declared = str(node.get("status") or "")
    done = set(cfg["done_statuses"])
    failed = set(cfg["failed_statuses"])

    def _up(key: str) -> tuple[str, str]:
        entry = upstream[key]
        verdict = str(entry.get("status") or "")
        decl = str(entry.get("declared_status") or "")
        if verdict not in ALL_STATUSES:  # 未经裁决：把声明态当作唯一信息源
            decl = decl or verdict
            verdict = ""
        return verdict, decl

    unknown = [d for d in deps if d not in upstream]
    if unknown:
        return {"status": cfg["treat_unknown_dependency_as"],
                "reasons": [{"code": "unknown_dependency", "dependencies": sorted(unknown)}],
                "checks": []}

    cyc = [d for d in deps if "dependency_cycle" in
           [r.get("code") for r in (upstream[d].get("reasons") or [])]
           or (upstream[d].get("status") == STATUS_WITHHELD
               and any(r.get("code") == "dependency_cycle"
                       for r in upstream[d].get("reasons", [])))]
    if cyc:
        return {"status": STATUS_WITHHELD,
                "reasons": [{"code": "dependency_cycle", "dependencies": sorted(cyc)}],
                "checks": []}

    if declared and declared in failed:
        return {"status": STATUS_FAIL,
                "reasons": [{"code": "declared_failed", "declared_status": declared}],
                "checks": []}
    if declared and declared not in done:
        return {"status": STATUS_PENDING,
                "reasons": [{"code": "declared_not_done", "declared_status": declared}],
                "checks": []}

    upstream_failed = sorted(d for d in deps
                             if _up(d)[0] == STATUS_FAIL
                             or (not _up(d)[0] and _up(d)[1] in failed))
    upstream_pending = sorted(d for d in deps if _up(d)[0] == STATUS_PENDING
                              or (not _up(d)[0] and _up(d)[1] and _up(d)[1] not in done))
    upstream_withheld = sorted(d for d in deps if _up(d)[0] == STATUS_WITHHELD)
    if upstream_failed and cfg["block_on_upstream_fail"]:
        return {"status": STATUS_FAIL,
                "reasons": [{"code": "upstream_failed", "dependencies": upstream_failed}],
                "checks": []}
    if upstream_pending:
        return {"status": STATUS_PENDING,
                "reasons": [{"code": "prerequisites_incomplete",
                             "dependencies": upstream_pending}], "checks": []}
    if upstream_withheld:
        return {"status": STATUS_WITHHELD,
                "reasons": [{"code": "upstream_withheld",
                             "dependencies": upstream_withheld}], "checks": []}

    reasons: list[dict[str, Any]] = []
    if resolved["required_missing"] or resolved["malformed"]:
        if resolved["required_missing"]:
            reasons.append({"code": "evidence_missing",
                            "refs": list(resolved["required_missing"])})
        if resolved["malformed"]:
            reasons.append({"code": "evidence_malformed",
                            "refs": list(resolved["malformed"])})
        return {"status": STATUS_WITHHELD, "reasons": reasons, "checks": []}

    checks = [evaluate_check(c)
              for c in (criteria_bundle.get("criteria", {}).get("checks") or [])]
    if not checks and not cfg["allow_empty_criteria"]:
        reason: dict[str, Any] = {"code": "no_criteria_declared"}
        if criteria_bundle.get("criteria", {}).get("load_error"):
            reason["load_error"] = criteria_bundle["criteria"]["load_error"]
        if criteria_bundle.get("criteria_file_digest") == MISSING_DIGEST:
            reason["criteria_ref_missing"] = criteria_bundle.get("criteria_ref")
        return {"status": STATUS_WITHHELD, "reasons": [reason], "checks": checks}

    unevaluable = [c["name"] for c in checks if not c["evaluable"]]
    if unevaluable:
        return {"status": STATUS_WITHHELD,
                "reasons": [{"code": "criteria_unevaluable",
                             "checks": sorted(unevaluable)}],
                "checks": checks}

    failing = [c for c in checks if not c["ok"]]
    if failing:
        return {"status": STATUS_FAIL,
                "reasons": [{"code": "criteria_not_met",
                             "checks": sorted(c["name"] for c in failing)}],
                "checks": checks}
    return {"status": STATUS_PASS,
            "reasons": [{"code": "criteria_met",
                         "checks": sorted(c["name"] for c in checks)}],
            "checks": checks}


# ───────────────────────── scope 计算 ─────────────────────────

def compute_scope(node: Mapping[str, Any],
                  upstream_states: Sequence[Mapping[str, Any]],
                  digests: Mapping[str, str],
                  criteria_bundle: Mapping[str, Any],
                  config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """scope = 判定域；scope_hash = 该判定域的内容指纹。"""
    cfg = merge_config(config)
    upstream_payload: list[dict[str, Any]] = []
    if cfg.get("include_upstream_status_in_scope", True):
        upstream_payload = [
            {"id": str(u["id"]),
             "status": str(u.get("status")),
             "declared_status": str(u.get("declared_status") or ""),
             "scope_hash": u.get("scope_hash")}
            for u in sorted(upstream_states, key=lambda x: str(x["id"]))
        ]
    payload: dict[str, Any] = {
        "scope_version": SCOPE_VERSION,
        "node": str(node.get("id")),
        "node_kind": str(node.get("kind") or "task"),
        "upstream": upstream_payload,
        "evidence": [{"ref": str(ref), "sha256": str(dig)}
                     for ref, dig in sorted(digests.items())],
        "criteria": {
            "body": criteria_bundle.get("criteria", {}),
            "ref": criteria_bundle.get("criteria_ref"),
            "file_digest": criteria_bundle.get("criteria_file_digest"),
        },
    }
    components = {
        "evidence": sha256_text(canonical_json(payload["evidence"])),
        "upstream": sha256_text(canonical_json(payload["upstream"])),
        "criteria": sha256_text(canonical_json(payload["criteria"])),
    }
    return {"payload": payload, "components": components,
            "scope_hash": sha256_text(canonical_json(payload))}


def _stale_cause(prev: Mapping[str, Any], cur: Mapping[str, Any]) -> dict[str, Any]:
    """指出这次 scope_hash 变化的成因（哪个分项、哪条证据、哪个上游节点）。"""
    prev_comp = prev.get("components", {})
    cur_comp = cur["components"]
    changed = sorted(k for k in cur_comp if prev_comp.get(k) != cur_comp[k])
    detail: list[dict[str, Any]] = []
    prev_payload = prev.get("payload", {})
    cur_payload = cur["payload"]
    if "upstream" in changed:
        before = {u["id"]: u for u in prev_payload.get("upstream", [])}
        after = {u["id"]: u for u in cur_payload.get("upstream", [])}
        for key in sorted(set(before) | set(after)):
            a, b = before.get(key), after.get(key)
            if a != b:
                detail.append({
                    "kind": "upstream",
                    "upstream": key,
                    "from_status": (a or {}).get("status"),
                    "to_status": (b or {}).get("status"),
                    "from_declared_status": (a or {}).get("declared_status"),
                    "to_declared_status": (b or {}).get("declared_status"),
                    "scope_hash_changed": (a or {}).get("scope_hash") != (b or {}).get("scope_hash"),
                })
    if "evidence" in changed:
        before = {e["ref"]: e["sha256"] for e in prev_payload.get("evidence", [])}
        after = {e["ref"]: e["sha256"] for e in cur_payload.get("evidence", [])}
        for ref in sorted(set(before) | set(after)):
            if before.get(ref) != after.get(ref):
                detail.append({"kind": "evidence", "ref": ref,
                               "from_sha256": before.get(ref),
                               "to_sha256": after.get(ref)})
    if "criteria" in changed:
        detail.append({"kind": "criteria",
                       "from_digest": (prev_payload.get("criteria") or {}).get("file_digest"),
                       "to_digest": (cur_payload.get("criteria") or {}).get("file_digest")})
    return {"components": changed, "detail": detail}


# ───────────────────────── DAG ─────────────────────────

def topo_order(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """确定性拓扑序（Kahn，同层按字符串 id 排序）。返回 order / 成环节点 / 邻接。"""
    ids = [str(n.get("id")) for n in nodes]
    id_set = set(ids)
    deps = {str(n.get("id")): sorted({str(d) for d in (n.get("dependencies") or [])
                                      if str(d) in id_set}) for n in nodes}
    indeg = {nid: len(deps[nid]) for nid in ids}
    dependents: dict[str, set[str]] = {nid: set() for nid in ids}
    for nid, ds in deps.items():
        for d in ds:
            dependents[d].add(nid)
    ready = sorted(nid for nid in ids if indeg[nid] == 0)
    order: list[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for child in sorted(dependents[nid]):
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
                ready.sort()
    cyclic = sorted(set(ids) - set(order))
    return {"order": order, "cyclic": cyclic, "dependencies": deps,
            "dependents": {k: sorted(v) for k, v in dependents.items()}}


def _downstream_closure(graph: Mapping[str, Any], seeds: Iterable[str]) -> list[str]:
    seeds = list(seeds)
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        for child in graph["dependents"].get(cur, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return sorted(seen - set(seeds))


def _max_hops(graph: Mapping[str, Any], seeds: Sequence[str]) -> int:
    if not seeds:
        return 0
    depth = {s: 0 for s in seeds}
    frontier = list(seeds)
    best = 0
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            for child in graph["dependents"].get(cur, []):
                d = depth[cur] + 1
                if child not in depth or d > depth[child]:
                    depth[child] = d
                    best = max(best, d)
                    nxt.append(child)
        frontier = nxt
    return best


# ───────────────────────── 生命周期主流程 ─────────────────────────

def evaluate_lifecycle(campaign: Mapping[str, Any],
                       ledger: Mapping[str, Any] | None = None,
                       roots: Sequence[str | Path] = (".",),
                       config: Mapping[str, Any] | None = None,
                       contracts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """一轮完整重判：scope 计算 → 与 ledger 比对（stale）→ 三态裁决 → 失效传播。

    纯函数：不把时钟写进哈希、不写文件、不碰 DB；同一输入逐位一致。
    唯一非确定字段全部收在 ``diagnostics``（``result_fingerprint`` 会剔除）。
    """
    cfg = merge_config(config)
    t_start = time.perf_counter()
    ledger = ledger or {}
    prev_gates = dict(ledger.get("gates") or {})
    nodes = list(campaign.get("nodes") or [])
    graph = topo_order(nodes)
    by_id = {str(n.get("id")): n for n in nodes}
    states: dict[str, dict[str, Any]] = {}
    gate_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    t_pass = time.perf_counter()
    for nid in graph["order"]:
        node = by_id[nid]
        deps = graph["dependencies"][nid]
        upstream_states = [{"id": d,
                            "status": states[d]["status"],
                            "declared_status": states[d]["declared_status"],
                            "scope_hash": states[d]["scope_hash"],
                            "reasons": states[d]["reasons"]} for d in deps]
        resolved = resolve_evidence(node.get("evidence"), roots)
        criteria_bundle = load_criteria(node, roots)
        scope = compute_scope(node, upstream_states, resolved["digests"], criteria_bundle, cfg)
        prev = prev_gates.get(nid)
        stale = bool(prev) and prev.get("scope_hash") != scope["scope_hash"]
        cause = _stale_cause(prev, scope) if stale else {"components": [], "detail": []}
        verdict = adjudicate(node, states, resolved, criteria_bundle, cfg)
        prev_status = (prev or {}).get("status")
        propagated_from = sorted(
            d for d in deps
            if d in states and (states[d]["stale"] or (
                prev_gates.get(d) is not None
                and states[d]["scope_hash"] != (prev_gates.get(d) or {}).get("scope_hash")))
        ) if prev is not None else []
        row = {
            "id": nid,
            "kind": str(node.get("kind") or "task"),
            "status": verdict["status"],
            "declared_status": str(node.get("status") or ""),
            "reasons": verdict["reasons"],
            "checks": verdict["checks"],
            "dependencies": deps,
            "stale": stale,
            "first_adjudication": prev is None,
            "previous_status": prev_status,
            "status_changed": prev is not None and prev_status != verdict["status"],
            "scope_hash": scope["scope_hash"],
            "previous_scope_hash": (prev or {}).get("scope_hash"),
            "components": scope["components"],
            "stale_cause": cause,
            "propagated_from": propagated_from,
            "evidence": resolved["digests"],
            "missing_evidence": resolved["missing"],
            "criteria": criteria_bundle["criteria"],
            "criteria_ref": criteria_bundle.get("criteria_ref"),
            "scope_payload": scope["payload"],
        }
        states[nid] = {"status": row["status"], "scope_hash": row["scope_hash"],
                       "declared_status": str(node.get("status") or ""),
                       "stale": stale, "reasons": row["reasons"]}
        gate_rows.append(row)
    t_pass_done = time.perf_counter()

    for nid in graph["cyclic"]:
        node = by_id[nid]
        row = {
            "id": nid, "kind": str(node.get("kind") or "task"),
            "status": STATUS_WITHHELD,
            "reasons": [{"code": "dependency_cycle"}],
            "checks": [], "dependencies": sorted(graph["dependencies"].get(nid, [])),
            "stale": False, "first_adjudication": nid not in prev_gates,
            "previous_status": (prev_gates.get(nid) or {}).get("status"),
            "status_changed": False,
            "scope_hash": None,
            "previous_scope_hash": (prev_gates.get(nid) or {}).get("scope_hash"),
            "components": {}, "stale_cause": {"components": [], "detail": []},
            "propagated_from": [], "evidence": {}, "missing_evidence": [],
            "criteria": {}, "criteria_ref": None, "scope_payload": {},
        }
        states[nid] = {"status": STATUS_WITHHELD, "scope_hash": None, "stale": False,
                       "declared_status": str(node.get("status") or ""),
                       "reasons": row["reasons"]}
        gate_rows.append(row)
        errors.append({"node": nid, "code": "dependency_cycle"})

    changed = sorted(r["id"] for r in gate_rows
                     if r["stale"] or (r["previous_status"] is not None
                                       and r["previous_status"] != r["status"]))
    affected = _downstream_closure(graph, changed)
    t_end = time.perf_counter()

    counts = {s: 0 for s in ALL_STATUSES}
    for row in gate_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    contract_report = verify_contracts(contracts, gate_rows) if contracts else {
        "schema": CONTRACTS_SCHEMA, "checked": 0, "broken": 0, "all_ok": True,
        "contracts": []}

    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "plan": campaign.get("plan"),
        "campaign_label": campaign.get("label"),
        "config_digest": config_digest(cfg),
        "scope_version": SCOPE_VERSION,
        "gates": gate_rows,
        "summary": {
            "node_count": len(gate_rows),
            "counts": counts,
            "pass_count": counts.get(STATUS_PASS, 0),
            "non_pass": sorted(r["id"] for r in gate_rows if r["status"] != STATUS_PASS),
            "stale_count": sum(1 for r in gate_rows if r["stale"]),
            "stale_ids": sorted(r["id"] for r in gate_rows if r["stale"]),
            "status_changed_ids": sorted(r["id"] for r in gate_rows if r["status_changed"]),
            "first_adjudication_ids": sorted(r["id"] for r in gate_rows
                                             if r["first_adjudication"]),
            "topo_order": graph["order"],
            "cyclic": graph["cyclic"],
            "states_covered": sorted(k for k, v in counts.items() if v > 0),
            "all_states_covered": all(counts.get(s, 0) > 0 for s in ALL_STATUSES),
            "errors": errors,
        },
        "propagation": {
            "changed_nodes": changed,
            "affected_downstream": affected,
            "invalidated_nodes": sorted(set(changed) | set(affected)),
            "max_hops": _max_hops(graph, changed),
            "latency_ms": round((t_end - t_start) * 1000.0, 3),
            "adjudication_ms": round((t_pass_done - t_pass) * 1000.0, 3),
            "budget_ms": float(cfg["latency_budget_ms"]),
            "within_budget": (t_end - t_start) * 1000.0 <= float(cfg["latency_budget_ms"]),
        },
        "contracts": contract_report,
        "diagnostics": {
            "elapsed_ms": round((t_end - t_start) * 1000.0, 3),
            "evaluated_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "roots": [str(r) for r in roots],
        },
    }
    result["fingerprint"] = result_fingerprint(result)
    return result


VOLATILE_PROPAGATION_KEYS = ("latency_ms", "adjudication_ms", "within_budget")


def result_fingerprint(result: Mapping[str, Any]) -> str:
    """剔除纯诊断/计时字段后的结果指纹（幂等重判的比较口径）。

    计时类字段（``diagnostics`` 与 ``propagation`` 里的 ms 值）随机器负载浮动，
    不属于判定内容；其余字段一律入指纹，任何实质差异都会改变指纹。
    """
    trimmed = {k: v for k, v in result.items() if k not in ("diagnostics", "fingerprint")}
    if isinstance(trimmed.get("propagation"), Mapping):
        trimmed["propagation"] = {k: v for k, v in trimmed["propagation"].items()
                                  if k not in VOLATILE_PROPAGATION_KEYS}
    return sha256_text(canonical_json(trimmed))


def diff_results(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """逐节点比较两轮结果（同 scope 逐位一致的机裁口径）。"""
    ra = {r["id"]: r for r in a.get("gates", [])}
    rb = {r["id"]: r for r in b.get("gates", [])}
    diffs: list[dict[str, Any]] = []
    for nid in sorted(set(ra) | set(rb)):
        x, y = ra.get(nid), rb.get(nid)
        if x is None or y is None:
            diffs.append({"id": nid, "field": "presence"})
            continue
        for field in ("status", "scope_hash", "reasons", "checks", "stale",
                      "evidence", "stale_cause"):
            if canonical_json(x.get(field)) != canonical_json(y.get(field)):
                diffs.append({"id": nid, "field": field,
                              "a": x.get(field), "b": y.get(field)})
    return {
        "identical": not diffs,
        "fingerprint_a": a.get("fingerprint"),
        "fingerprint_b": b.get("fingerprint"),
        "diffs": diffs,
    }


# ───────────────────────── ledger ─────────────────────────

def empty_ledger() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "scope_version": SCOPE_VERSION, "gates": {}}


def update_ledger(ledger: Mapping[str, Any] | None,
                  result: Mapping[str, Any]) -> dict[str, Any]:
    """把本轮 scope 指纹/状态写回 ledger。

    幂等：同一输入连写两次，ledger 逐字节相同（revision 只在 scope_hash 或 status
    真正变化时 +1，历史条目不重复追加）。
    """
    new = copy.deepcopy(dict(ledger or empty_ledger()))
    new.setdefault("schema", LEDGER_SCHEMA)
    new.setdefault("scope_version", SCOPE_VERSION)
    gates = new.setdefault("gates", {})
    for row in result.get("gates", []):
        nid = row["id"]
        prev = gates.get(nid) or {}
        entry = {
            "scope_hash": row["scope_hash"],
            "components": row.get("components", {}),
            "status": row["status"],
            "payload": row.get("scope_payload", {}),
            "revision": int(prev.get("revision", 0) or 0),
            "status_history": list(prev.get("status_history") or []),
        }
        if prev.get("scope_hash") != row["scope_hash"] or prev.get("status") != row["status"]:
            entry["revision"] = int(prev.get("revision", 0) or 0) + 1
            entry["status_history"] = entry["status_history"] + [{
                "revision": entry["revision"],
                "scope_hash": row["scope_hash"],
                "from_scope_hash": prev.get("scope_hash"),
                "status": row["status"],
                "from_status": prev.get("status"),
            }]
        gates[nid] = entry
    new["plan"] = result.get("plan")
    new["result_fingerprint"] = result.get("fingerprint")
    return new


# ───────────────────────── 跨仓接口契约 ─────────────────────────

def verify_contracts(contracts: Mapping[str, Any] | None,
                     gate_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """跨仓接口契约机检（仓名/接口/字段全部来自配置，代码零领域语义）。

    契约条目::

        {"id": "iface-a-b", "producer": "<node id>", "consumer": "<node id>",
         "interface": "shared/field.json",
         "producer_ref": "artifacts/.../a_out.json",   # 生产侧证据 ref
         "consumer_ref": "artifacts/.../b_in.json",    # 消费侧声明的同一工件 ref
         "producer_fields": ["f1", "f2"], "consumer_requires": ["f1"]}

    机检项：
      K0 两侧节点在本次生命周期结果里存在；
      K1 生产者已 PASS（未 PASS 的接口不可被消费 → 破约而非静默）；
      K2 两侧 ref 解析到同一 sha256（工件一致性，含"双方都缺"判负）；
      K3 生产侧字段 ⊇ 消费侧所需字段（覆盖性）。
    """
    rows = {r["id"]: r for r in gate_rows}
    items = list((contracts or {}).get("contracts") or [])
    out_items: list[dict[str, Any]] = []
    for spec in items:
        cid = str(spec.get("id") or "<unnamed>")
        producer = str(spec.get("producer"))
        consumer = str(spec.get("consumer"))
        checks: list[dict[str, Any]] = []
        prod, cons = rows.get(producer), rows.get(consumer)
        missing_nodes = sorted(n for n in (producer, consumer) if n not in rows)
        if missing_nodes:
            checks.append({"name": "K0_nodes_present", "ok": False,
                           "detail": {"missing": missing_nodes}})
            out_items.append({"id": cid, "interface": spec.get("interface"),
                              "producer": producer, "consumer": consumer,
                              "ok": False, "checks": checks})
            continue
        checks.append({"name": "K0_nodes_present", "ok": True,
                       "detail": {"producer_status": prod["status"],
                                  "consumer_status": cons["status"]}})
        checks.append({"name": "K1_producer_pass", "ok": prod["status"] == STATUS_PASS,
                       "detail": {"producer_status": prod["status"]}})

        pref, cref = spec.get("producer_ref"), spec.get("consumer_ref")
        pd = prod.get("evidence", {}).get(pref) if pref else None
        cd = cons.get("evidence", {}).get(cref) if cref else None
        same = bool(pd) and bool(cd) and pd == cd and pd != MISSING_DIGEST
        checks.append({"name": "K2_interface_digest", "ok": same,
                       "detail": {"producer_ref": pref, "consumer_ref": cref,
                                  "producer_sha256": pd, "consumer_sha256": cd}})

        pfields = sorted(set(spec.get("producer_fields") or []))
        creq = sorted(set(spec.get("consumer_requires") or []))
        uncovered = sorted(set(creq) - set(pfields))
        checks.append({"name": "K3_field_coverage", "ok": not uncovered,
                       "detail": {"producer_fields": pfields,
                                  "consumer_requires": creq,
                                  "uncovered": uncovered}})
        out_items.append({"id": cid, "interface": spec.get("interface"),
                          "producer": producer, "consumer": consumer,
                          "ok": all(c["ok"] for c in checks), "checks": checks})
    broken = [c["id"] for c in out_items if not c["ok"]]
    return {
        "schema": CONTRACTS_SCHEMA,
        "checked": len(out_items),
        "broken": len(broken),
        "broken_ids": broken,
        "all_ok": not broken,
        "contracts": out_items,
    }


# ───────────────────────── 只读 DB 装载器 ─────────────────────────

def _psql_json(sql: str, psql_cmd: Sequence[str] | None = None) -> Any:
    cmd = list(psql_cmd or ["runuser", "-u", "postgres", "--", "psql",
                            "-d", "tensorplatform", "-tAq", "-c"])
    cmd.append(sql)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:400]}")
    text = out.stdout.strip()
    return json.loads(text) if text else []


def build_campaign_from_db(plan_id: int,
                           psql_cmd: Sequence[str] | None = None) -> dict[str, Any]:
    """只读导出某一计划的节点，装配成 campaign 快照（不写库、不改状态）。

    证据/判据声明从节点 metadata 的 ``lifecycle`` 键读取（缺省则只有依赖状态证据）::

        metadata["lifecycle"] = {
            "evidence": [{"ref": "...", "kind": "file"}],
            "criteria": {"checks": [{"name": "...", "op": "<=", "target": 3, "value": 2.1}]},
            "criteria_ref": "path/to/criteria.json"
        }
    """
    sql = (
        "SELECT coalesce(json_agg(row_to_json(t) ORDER BY t.id), '[]'::json) FROM ("
        " SELECT id, name, node_type, status, dependencies, metadata"
        f" FROM campaign_nodes WHERE plan_id = {int(plan_id)}"
        ") t"
    )
    rows = _psql_json(sql, psql_cmd)
    nodes: list[dict[str, Any]] = []
    for row in rows:
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        life = (meta.get("lifecycle") or {}) if isinstance(meta, dict) else {}
        deps = row.get("dependencies") or []
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except json.JSONDecodeError:
                deps = []
        node = {
            "id": str(row["id"]),
            "kind": row.get("node_type") or "task",
            "label": row.get("name"),
            "status": row.get("status"),
            "dependencies": [str(d) for d in deps],
            "evidence": list(life.get("evidence") or []),
            "criteria": dict(life.get("criteria") or {}),
        }
        if life.get("criteria_ref"):
            node["criteria"]["criteria_ref"] = life["criteria_ref"]
        if not node["evidence"]:
            node["evidence"] = [
                {"ref": f"db:campaign_nodes/{row['id']}/status", "kind": "value",
                 "value": row.get("status")}]
        nodes.append(node)
    return {"schema": "gate-lifecycle-campaign/1.0", "plan": int(plan_id),
            "label": f"plan-{plan_id}", "source": "db-readonly", "nodes": nodes}


# ───────────────────────── 渲染 ─────────────────────────

def render_markdown(result: Mapping[str, Any]) -> str:
    s = result.get("summary", {})
    p = result.get("propagation", {})
    c = result.get("contracts", {})
    lines: list[str] = []
    lines.append(f"# 门生命周期重判结果（plan {result.get('plan')}）\n")
    lines.append(f"- scope 版本：`{result.get('scope_version')}`；结果指纹 "
                 f"`{result.get('fingerprint')}`\n")
    lines.append(f"- 节点 {s.get('node_count', 0)} 个；状态分布 "
                 + "，".join(f"{k}={v}" for k, v in sorted((s.get('counts') or {}).items()))
                 + "\n")
    lines.append(f"- stale {s.get('stale_count', 0)} 个；状态变更 "
                 f"{len(s.get('status_changed_ids') or [])} 个；成环 {len(s.get('cyclic') or [])}\n")
    lines.append(f"- 传播延迟 {p.get('latency_ms')} ms（预算 {p.get('budget_ms')} ms，"
                 f"{'达标' if p.get('within_budget') else '超预算'}）；最大跳数 {p.get('max_hops')}\n")
    if c.get("checked"):
        lines.append(f"- 接口契约 {c.get('checked')} 条，破约 {c.get('broken')} 条"
                     f"（{', '.join(c.get('broken_ids') or []) or '无'}）\n")
    lines.append("\n| 节点 | 类型 | 状态 | stale | 传播自 | 依据 |\n|---|---|---|---|---|---|\n")
    for row in result.get("gates", []):
        reasons = "; ".join(str(r.get("code")) for r in row.get("reasons") or [])
        lines.append(f"| {row['id']} | {row['kind']} | {row['status']} | "
                     f"{'Y' if row['stale'] else '-'} | "
                     f"{','.join(row.get('propagated_from') or []) or '-'} | {reasons} |\n")
    return "".join(lines)


# ───────────────────────── CLI ─────────────────────────

def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="战役门生命周期机制（scope_hash / 传播 / 三态）")
    ap.add_argument("--campaign", help="campaign 快照 JSON（含 nodes）")
    ap.add_argument("--plan", type=int, help="或直接从只读 DB 导出该计划的节点")
    ap.add_argument("--snapshot-out", help="与 --plan 合用：把 DB 快照落盘")
    ap.add_argument("--contracts", help="跨仓接口契约 JSON")
    ap.add_argument("--ledger", help="上一轮 ledger JSON（缺省视为首判）")
    ap.add_argument("--ledger-out", help="本轮 ledger 输出路径")
    ap.add_argument("--config", help="配置 JSON（done/failed 词表、延迟预算等）")
    ap.add_argument("--roots", nargs="*", default=["."], help="证据路径解析根（按序）")
    ap.add_argument("--json-out")
    ap.add_argument("--md-out")
    ap.add_argument("--repeat", type=int, default=1, help="连跑 N 次做幂等自检")
    args = ap.parse_args(argv)

    try:
        if args.plan is not None and not args.campaign:
            campaign = build_campaign_from_db(args.plan)
            if args.snapshot_out:
                Path(args.snapshot_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.snapshot_out).write_text(
                    json.dumps(campaign, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
        elif args.campaign:
            campaign = _load_json(args.campaign)
        else:
            ap.error("需要 --campaign 或 --plan")
        config = _load_json(args.config) if args.config else None
        contracts = _load_json(args.contracts) if args.contracts else None
        ledger = _load_json(args.ledger) if args.ledger else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 3

    results = [evaluate_lifecycle(campaign, ledger, args.roots, config, contracts)
               for _ in range(max(1, args.repeat))]
    result = results[0]
    if len(results) > 1:
        diffs = [diff_results(results[0], r) for r in results[1:]]
        result["idempotency"] = {"runs": len(results),
                                 "identical": all(d["identical"] for d in diffs),
                                 "fingerprints": [r["fingerprint"] for r in results]}

    if args.ledger_out:
        new_ledger = update_ledger(ledger, result)
        Path(args.ledger_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.ledger_out).write_text(
            json.dumps(new_ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    md = render_markdown(result)
    if args.md_out:
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_out).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)

    s = result["summary"]
    c = result["contracts"]
    ready = (s["pass_count"] == s["node_count"] and not s["cyclic"]
             and c.get("all_ok", True) and s["stale_count"] == 0)
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())