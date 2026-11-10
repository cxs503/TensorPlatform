#!/usr/bin/env python3
"""campaign_integration_audit —— 战役级**全链路集成审计**（post-hoc, whole-plan, 可复算）。

为什么还要这一层
----------------
战役推进期间已有的四层各管一段，但**没有一层回答"这场战役整体能不能收官"**：

* ``governance/machine_gate.py``：**播种时**对 seed 做结构/治理硬校验（ext_id 撞车、
  仓白名单、环、goal 太薄…）——只看"种子合不合规"，不看"跑完之后成没成"；
* ``inheritance_gate.py``：**单节点**验收 + 同一 key 跨节点数值互裁；
* ``campaign_gate_snapshot.py``：把 DB 五表拼成快照 + 门节点任务书判据叠层；
* ``campaign_integration_verify.py``：**接口面**（artifact schema / DAG 边 produces⊇requires /
  pass·honest_fail·unproven·fail 四分类预算）。

缺口是**战役级汇总闭环**：把 N1…Nn 的验收结论、能力台账、谱系（parent_plan 链）、
全库 ext_id 唯一性、仓白名单、DAG 无环性、精度带分布**放进一份可机裁的报告**，
并对"有失败但没申报"的情形 fail-closed。本模块补这一层。

八条机裁判据（``verdict=ready`` 当且仅当八条全过；未就绪绝不返回 0）
------------------------------------------------------------------
| id | 判据 | 语义 |
|---|---|---|
| ``g1_lineage_intact`` | 谱系链可解析 | plan.metadata.parent_plan 逐级可查、链上无环、父计划状态合法 |
| ``g2_ext_id_unique`` | ext_id 唯一 | 计划内不重复，且不与全库其它节点撞车 |
| ``g3_repo_whitelist`` | 仓白名单 | 每个节点 repo（别名归一后）∈ 注入白名单 |
| ``g4_dag_acyclic`` | DAG 无环 | 复用 ``governance.dag.check_dag``：环/外引用/自指/重复 id 全零 |
| ``g5_node_evidence`` | 证据充分 | 证据合格（class=pass）的节点数 ≥ ``min_pass_nodes`` |
| ``g6_ledger_coverage`` | 台账覆盖 | 每个节点声明的能力都在能力台账里有着落（等级可复算） |
| ``g7_precision_band`` | 精度带 | 已量测判据里 ``rel_error > 15%`` 的条目数为 0（>15% 不可接受） |
| ``g8_no_undocumented_fail`` | 失败申报 | 失败态节点必须进 honest-FAIL 台账；未申报的失败一律阻塞 |

四分类（与 ``campaign_integration_verify`` 同一纪律）
---------------------------------------------------
``pass`` / ``honest_fail``（有据可查的诚实失败）/ ``unproven``（在飞·无证据·判据未测）/
``fail``（失败但**没申报**）。honest FAIL **不可覆盖**：一旦节点被判 ``fail``，其能力条目不得
计为台账 covered；台账若给失败节点标 ``REAL``，一律记 ``honest_fail_violations``。

零领域语义（TensorPlatform 铁律）
-------------------------------
模块内**没有**任何学科词条/域判据/域算例：节点名、判据文本、能力 id、白名单、honest-FAIL
台账、能力映射全部来自 DB 快照或注入的 ``config``/``ledger``；判据求值委托
``inheritance_gate.evaluate_node``（不重复实现）。测试夹具用合成域名（``N-A``/``alpha``/
``unit_x``），故任何域可直接复用。

确定性
------
不取 wall clock；``input_digest`` 由 (plans, nodes, missions, reports, ledger, registry,
attestations, config) 的规范化 JSON 派生 → 同一输入连跑两次逐字节相同。

CLI
---
    python3 ops/taskplane/campaign_integration_audit.py \
        --plan 9044 --from-db \
        --ledger artifacts/capability_ledger/capability_ledger.json \
        --config artifacts/p9044w5_n08/audit_config.json \
        --json-out artifacts/p9044w5_n08/integration_audit.json \
        --md-out   artifacts/p9044w5_n08/integration_audit.md \
        --fig-dir  artifacts/p9044w5_n08/figures

退出码：0 = 全链路就绪；3 = 未就绪（仅 --strict）；2 = 输入不可用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "tp.campaign.integration_audit/v1"

#: 精度带：rel_error ≤3% 优秀（平台验收线），≤15% 可接受，>15% **不可接受**（平台铁律）。
BAND_EDGES: tuple[tuple[str, float], ...] = (
    ("excellent", 0.03),
    ("acceptable", 0.15),
)
UNACCEPTABLE_BAND = "unacceptable"
UNMEASURED_BAND = "unmeasured"

#: 仓白名单默认值（治理参数，非学科判据）；可被 config 覆盖。
DEFAULT_REPO_WHITELIST: tuple[str, ...] = (
    "TensorPlatform", "TensorPhysics", "TensorLBM", "TensorFVM", "TensorFEM",
    "TensorDEM", "TensorBEM", "TensorSPH", "TensorDG", "TensorCAD", "TensorPapers",
    "ship-performance-platform",
)
REPO_ALIASES: dict[str, str] = {
    "platform": "TensorPlatform", "tensorplatform": "TensorPlatform",
    "physics": "TensorPhysics", "tensorphysics": "TensorPhysics",
    "ship": "ship-performance-platform",
    "shipperformanceplatform": "ship-performance-platform",
}

PASS = "pass"
HONEST_FAIL = "honest_fail"
UNPROVEN = "unproven"
FAIL = "fail"

DEFAULT_CFG: dict[str, Any] = {
    "done_states": ["done"],
    "failed_missions": ["failed", "cancelled", "canceled", "error", "aborted", "timeout"],
    "inflight_missions": ["running", "in_flight", "in-flight", "queued", "pending", "dispatched"],
    "pass_verdicts": ["pass", "passed", "ok"],
    "fail_verdicts": ["fail", "failed", "reject", "rejected"],
    "lineage_states": ["active", "done", "completed", "draft"],
    "max_lineage_depth": 16,
    "min_pass_nodes": "all_peers",
    "exclude_node_ids": [],
    "min_ledger_coverage": 1.0,
    "max_unacceptable": 0,
    # g7: 哪些判据 key 的值本身就是「相对误差」（域无关注入；空=只认 attestation 的 rel_error）
    "error_metric_keys": [],
    "min_precision_items": 1,
    "require_evidence": True,
    "roots": ["."],
}

CRITERIA_SPEC: tuple[tuple[str, str], ...] = (
    ("g1_lineage_intact", "谱系链（parent_plan 逐级）可解析且链上无环"),
    ("g2_ext_id_unique", "ext_id 计划内唯一且不与全库其它节点撞车"),
    ("g3_repo_whitelist", "所有节点 repo 在注入白名单内（别名归一后）"),
    ("g4_dag_acyclic", "DAG 无环、无自指、无外计划引用、id 不重复"),
    ("g5_node_evidence", "证据合格节点数达到下界（判据实测 + 工件可解析）"),
    ("g6_ledger_coverage", "节点声明能力全部在能力台账有着落"),
    ("g7_precision_band", "已量测判据中 rel_error>15% 条目数为 0"),
    ("g8_no_undocumented_fail", "失败态节点均已进 honest-FAIL 台账（不覆盖）"),
)

_DETAIL_KEYS = {
    "g1_lineage_intact": "lineage",
    "g2_ext_id_unique": "ext_ids",
    "g3_repo_whitelist": "repos",
    "g4_dag_acyclic": "dag",
    "g6_ledger_coverage": "ledger",
    "g7_precision_band": "precision",
}


# ─────────────────────────── 小工具 ───────────────────────────

def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def metadata(row: Mapping[str, Any]) -> dict:
    meta = row.get("metadata")
    if isinstance(meta, Mapping):
        return dict(meta)
    if isinstance(meta, str) and meta.strip():
        try:
            loaded = json.loads(meta)
        except json.JSONDecodeError:
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def norm_repo(repo: Any, aliases: Mapping[str, str] | None = None) -> str:
    text = str(repo or "").strip()
    table = {str(k).strip().lower(): v for k, v in (aliases or REPO_ALIASES).items()}
    return table.get(text.lower(), text)


def band_of(rel_error: float | None) -> str:
    if rel_error is None:
        return UNMEASURED_BAND
    err = abs(rel_error)
    for name, edge in BAND_EDGES:
        if err <= edge:
            return name
    return UNACCEPTABLE_BAND


def _dep_list(raw: Any) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, int) and not isinstance(raw, bool):
        return [raw]
    if isinstance(raw, str):
        text = raw.strip().strip("{}[]")
        out: list[int] = []
        for part in text.split(","):
            part = part.strip().strip("'\"")
            if part:
                out.append(int(part))
        return out
    if isinstance(raw, Iterable):
        acc: list[int] = []
        for item in raw:
            acc.extend(_dep_list(item))
        return acc
    return []


def _cfg(config: Mapping[str, Any] | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CFG)
    for key, value in (config or {}).items():
        cfg[key] = value
    return cfg


def _plan_map(plans: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for row in plans or []:
        pid = as_int(row.get("id"))
        if pid is not None:
            out[pid] = row
    return out


# ─────────────────────────── g1 谱系 ───────────────────────────

def check_lineage(plans: Sequence[Mapping[str, Any]], plan_id: int,
                  config: Mapping[str, Any] | None = None) -> dict:
    """parent_plan 链逐级解析 + 无环 + 状态合法。返回链与问题项（确定性顺序）。"""
    cfg = _cfg(config)
    table = _plan_map(plans)
    allowed = {str(s).lower() for s in cfg["lineage_states"]}
    max_depth = int(cfg["max_lineage_depth"])
    chain: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[int] = set()
    current = plan_id
    for _step in range(max_depth + 1):
        plan = table.get(current)
        if plan is None:
            problems.append(f"plan_not_found:{current}")
            break
        if current in seen:
            problems.append(f"lineage_cycle:{current}")
            break
        seen.add(current)
        chain.append({"plan_id": current, "status": plan.get("status"), "name": plan.get("name")})
        parent = as_int(metadata(plan).get("parent_plan"))
        if parent is None:
            break
        if parent not in table:
            problems.append(f"parent_plan_missing:{parent}")
            break
        current = parent
    else:
        # 循环跑完仍未到根：要么链比上限还深，要么尾部成环（下一轮会在 seen 里被抓到）。
        problems.append(f"lineage_unresolved_within_depth:{max_depth}")

    resolved = [e for e in chain if e.get("name") is not None]
    for entry in resolved:
        status = str(entry.get("status") or "").lower()
        if status and status not in allowed:
            problems.append(f"lineage_state_not_allowed:{entry['plan_id']}={status}")
    return {
        "ok": not problems,
        "plan_id": plan_id,
        "chain": resolved,
        "depth": len(resolved),
        "problems": sorted(problems),
    }


# ─────────────────────────── g2 ext_id 唯一性 ───────────────────────────

def check_ext_ids(nodes: Sequence[Mapping[str, Any]],
                  registry: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """计划内 ext_id 唯一 + 与全库注册表（可含本计划行）不撞车。

    ``registry`` 形如 DB 导出行 ``{id, plan_id, ext_id}`` 或 ``{id, plan_id, metadata:{ext_id}}``。
    """
    plan_ids = {as_int(n.get("id")) for n in nodes or []}
    in_plan: dict[str, list[int]] = {}
    missing: list[int] = []
    for node in nodes or []:
        ext = str(metadata(node).get("ext_id") or node.get("ext_id") or "").strip()
        nid = as_int(node.get("id"))
        if not ext:
            if nid is not None:
                missing.append(nid)
            continue
        in_plan.setdefault(ext, []).append(nid if nid is not None else -1)
    duplicates = sorted(ext for ext, ids in in_plan.items() if len(ids) > 1)
    collisions: list[dict[str, Any]] = []
    if registry:
        outside: dict[str, list[int]] = {}
        for row in registry:
            nid = as_int(row.get("id"))
            if nid is None or nid in plan_ids:
                continue
            ext = str(metadata(row).get("ext_id") or row.get("ext_id") or "").strip()
            if ext:
                outside.setdefault(ext, []).append(nid)
        for ext in sorted(in_plan):
            if ext in outside:
                collisions.append({"ext_id": ext, "plan_node_ids": sorted(in_plan[ext]),
                                   "other_node_ids": sorted(outside[ext])})
    return {
        "ok": not duplicates and not collisions and not missing,
        "n_in_plan": len(in_plan),
        "n_unique": len(in_plan) - len(duplicates),
        "n_registry": len(registry or []),
        "duplicates": duplicates,
        "missing_ext_id": sorted(missing),
        "collisions": collisions,
    }


# ─────────────────────────── g3 仓白名单 ───────────────────────────

def check_repos(nodes: Sequence[Mapping[str, Any]],
                whitelist: Sequence[str] | None = None,
                aliases: Mapping[str, str] | None = None) -> dict:
    allowed = [str(x) for x in (whitelist or DEFAULT_REPO_WHITELIST)]
    allowed_set = set(allowed)
    violations: list[dict[str, Any]] = []
    for node in nodes or []:
        raw = metadata(node).get("repo") or node.get("repo")
        norm = norm_repo(raw, aliases)
        if norm not in allowed_set:
            violations.append({"node_id": as_int(node.get("id")), "repo": str(raw or ""),
                               "normalized": norm})
    violations.sort(key=lambda v: (v["node_id"] is None, v["node_id"] or 0, v["repo"]))
    return {"ok": not violations, "n_nodes": len(list(nodes or [])),
            "whitelist": allowed, "violations": violations}


# ─────────────────────────── g4 DAG 无环 ───────────────────────────

def check_dag(nodes: Sequence[Mapping[str, Any]]) -> dict:
    """复用 ``governance.dag.check_dag``（环/外引用/自指/重复 id），不重复实现。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from governance import dag as dag_mod  # noqa: PLC0415  延迟导入：允许离线复用

    rows = [{"id": as_int(n.get("id")), "dependencies": _dep_list(n.get("dependencies"))}
            for n in nodes or []]
    report = dag_mod.check_dag(rows)  # type: ignore[arg-type]
    return {
        "ok": bool(report.ok),
        "n_nodes": int(report.n_nodes),
        "order": list(report.order),
        "cycles": list(report.cycles),
        "out_of_plan": {int(k): [int(x) for x in v] for k, v in sorted(report.out_of_plan.items())},
        "self_loops": list(report.self_loops),
        "problems": list(report.problems),
    }


# ─────────────────────────── 单节点证据裁决（四分类） ───────────────────────────

def evaluate_nodes(nodes: Sequence[Mapping[str, Any]],
                   missions: Sequence[Mapping[str, Any]],
                   reports: Sequence[Mapping[str, Any]],
                   config: Mapping[str, Any] | None = None,
                   honest_fail_registry: Sequence[str] | None = None) -> list[dict]:
    """逐节点裁决 → 四分类 + 判据计数 + 最差相对误差（供 g5/g7/g8 汇总）。

    判据求值委托 ``inheritance_gate.evaluate_node``；本层只做分类与汇总，不改判据语义。
    """
    cfg = _cfg(config)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import inheritance_gate as ig  # noqa: PLC0415

    mission_map: dict[str, Mapping[str, Any]] = {}
    for m in missions or []:
        mid = as_int(m.get("id"))
        if mid is not None:
            mission_map[str(mid)] = m
    report_map: dict[str, list[dict]] = {}
    for rep in reports or []:
        mid = as_int(rep.get("mission_id"))
        if mid is not None:
            report_map.setdefault(str(mid), []).append(dict(rep))
    honest = {str(x) for x in (honest_fail_registry or [])}
    roots = list(cfg.get("roots") or ["."])
    failed_missions = {str(s).lower() for s in cfg["failed_missions"]}
    inflight = {str(s).lower() for s in cfg["inflight_missions"]}
    fail_verdicts = {str(s).lower() for s in cfg["fail_verdicts"]}
    pass_verdicts = {str(s).lower() for s in cfg["pass_verdicts"]}
    capability_map = dict(cfg.get("capability_map") or {})

    rows: list[dict] = []
    for node in nodes or []:
        nid = as_int(node.get("id"))
        meta = metadata(node)
        ext = str(meta.get("ext_id") or node.get("ext_id") or "").strip()
        mid = as_int(node.get("mission_id"))
        mission = mission_map.get(str(mid)) if mid is not None else None
        mission_status = str((mission or {}).get("status") or "").lower() or None
        verdict = node.get("reproduction_verdict")
        verdict_s = str(verdict).lower() if verdict is not None else None
        reps = report_map.get(str(mid), []) if mid is not None else []

        ev = ig.evaluate_node(node, mission, reps, cfg, roots)
        crit = list(ev.get("criteria") or [])
        n_passed = sum(1 for c in crit if c.get("status") == "passed")
        n_failed = sum(1 for c in crit if c.get("status") == "failed")
        n_unproven = len(crit) - n_passed - n_failed
        # 精度带只认「值本身就是相对误差」的判据（key 由 config.error_metric_keys 注入）；
        # measured 与 threshold 的差**不是**误差（判据形如 err<=3%，阈值不是参考值）——
        # 这正是本层与 tools/integration_gate 的相对误差语义的分界，写死在此防止误判。
        error_keys = {str(k) for k in (cfg.get("error_metric_keys") or [])}
        rels: list[float] = [abs(float(c["measured"])) for c in crit
                             if c.get("key") in error_keys and as_float(c.get("measured")) is not None]
        worst = max(rels) if rels else None
        declared_honest = ext in honest or bool(meta.get("honest_fail"))
        caps = [str(c) for c in (meta.get("capability_ids") or capability_map.get(ext, []))]

        if mission_status in failed_missions or verdict_s in fail_verdicts:
            klass = HONEST_FAIL if (declared_honest and reps) else FAIL
            reason = "declared_honest_fail" if klass == HONEST_FAIL else "failure_not_declared"
        elif mission_status is not None and mission_status in inflight:
            klass, reason = UNPROVEN, "in_flight"
        elif mid is not None and mission is None:
            klass, reason = UNPROVEN, "mission_missing"
        elif verdict_s is None or verdict_s not in pass_verdicts:
            klass, reason = UNPROVEN, "verdict_missing"
        elif not reps and cfg.get("require_evidence", True):
            klass, reason = UNPROVEN, "no_report_evidence"
        elif crit and (n_passed < len(crit) or n_failed):
            klass, reason = FAIL, "criteria_not_all_passed"
        elif not crit and cfg.get("require_criteria", False):
            klass, reason = UNPROVEN, "no_criteria_declared"
        else:
            klass, reason = PASS, "criteria_all_passed"

        rows.append({
            "node_id": nid,
            "ext_id": ext,
            "repo": norm_repo(meta.get("repo") or node.get("repo")),
            "node_status": str(node.get("status") or "").lower() or None,
            "mission_id": mid,
            "mission_status": mission_status,
            "verdict": verdict_s,
            "class": klass,
            "class_reason": reason,
            "n_criteria": len(crit),
            "n_passed": n_passed,
            "n_failed": n_failed,
            "n_unproven": n_unproven,
            "worst_rel_error": worst,
            "band": band_of(worst),
            "blockers": sorted(str(b) for b in (ev.get("blockers") or [])),
            "capability_ids": caps,
            "honest_fail_declared": declared_honest,
            "has_report": bool(reps),
        })
    rows.sort(key=lambda r: (r["node_id"] is None, r["node_id"] or 0, r["ext_id"]))
    return rows


# ─────────────────────────── g6 能力台账覆盖 ───────────────────────────

def check_ledger(node_rows: Sequence[Mapping[str, Any]],
                 ledger: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
                 config: Mapping[str, Any] | None = None) -> dict:
    """每个节点声明的能力 id 都必须能在台账里找到；失败节点不得被标 REAL。"""
    cfg = _cfg(config)
    if isinstance(ledger, Mapping):
        caps = ledger.get("capabilities") or []
    elif isinstance(ledger, Sequence) and not isinstance(ledger, (str, bytes)):
        caps = list(ledger)
    else:
        caps = []
    index: dict[str, dict] = {}
    for cap in caps:
        if not isinstance(cap, Mapping):
            continue
        cid = str(cap.get("id") or cap.get("capability_id") or "").strip()
        if cid:
            index[cid] = dict(cap)
    level_keys = ("level", "resolved_level", "claimed_level", "evidence_level")
    required: list[dict[str, Any]] = []
    missing: list[str] = []
    violations: list[dict[str, Any]] = []
    for row in node_rows or []:
        for cid in row.get("capability_ids") or []:
            entry = index.get(cid)
            level = None
            if entry:
                level = next((str(entry[k]) for k in level_keys if entry.get(k)), None)
            required.append({"ext_id": row.get("ext_id"), "capability_id": cid,
                             "level": level, "present": entry is not None})
            if entry is None or str(level or "").strip().upper() in ("", "NONE"):
                missing.append(f"{row.get('ext_id')}::{cid}")
            elif row.get("class") in (FAIL, HONEST_FAIL) and str(level).upper() == "REAL":
                violations.append({"ext_id": row.get("ext_id"), "capability_id": cid,
                                   "level": level,
                                   "reason": "ledger_claims_real_for_non_pass_node"})
    violating = {(v["ext_id"], v["capability_id"]) for v in violations}
    n_req = len(required)
    n_cov = sum(1 for r in required
                if r["present"] and (r["ext_id"], r["capability_id"]) not in violating
                and str(r["level"] or "").strip().upper() not in ("", "NONE"))
    coverage = (n_cov / n_req) if n_req else 0.0
    need = float(cfg.get("min_ledger_coverage", 1.0))
    return {
        "ok": n_req > 0 and coverage >= need,
        "n_required": n_req,
        "n_covered": n_cov,
        "coverage": round(coverage, 6),
        "min_coverage": need,
        "n_ledger_entries": len(index),
        "required": sorted(required, key=lambda r: (str(r["ext_id"]), r["capability_id"])),
        "missing": sorted(missing),
        "honest_fail_violations": sorted(violations,
                                         key=lambda v: (str(v["ext_id"]), v["capability_id"])),
        "note": "" if n_req else "no_capability_declared_by_any_node",
    }


# ─────────────────────────── g7 精度带 ───────────────────────────

def precision_summary(node_rows: Sequence[Mapping[str, Any]],
                      attestations: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """精度带汇总：优先用外部 attestation 的 rel_error / measured-vs-threshold，其次节点聚合值。"""
    band_names = [name for name, _ in BAND_EDGES] + [UNACCEPTABLE_BAND, UNMEASURED_BAND]
    bands = {name: 0 for name in band_names}
    items: list[dict[str, Any]] = []
    for att in attestations or []:
        ext = str(att.get("ext_id") or "")
        for crit in att.get("criteria") or []:
            if not isinstance(crit, Mapping):
                continue
            rel = as_float(crit.get("rel_error"))
            if rel is None:
                measured, thr = as_float(crit.get("measured")), as_float(crit.get("threshold"))
                if measured is not None and thr not in (None, 0.0):
                    rel = abs(measured - thr) / abs(thr)
            items.append({"ext_id": ext, "criterion_id": str(crit.get("id") or ""),
                          "rel_error": rel, "band": band_of(rel),
                          "verdict": crit.get("verdict")})
    for row in node_rows or []:
        if not row.get("n_criteria") or row.get("band") == UNMEASURED_BAND:
            continue
        items.append({"ext_id": row.get("ext_id"), "criterion_id": "*aggregate*",
                      "rel_error": row.get("worst_rel_error"), "band": row.get("band"),
                      "verdict": row.get("class")})
    for item in items:
        bands[item["band"]] = bands.get(item["band"], 0) + 1
    measured_vals = [i["rel_error"] for i in items if i["rel_error"] is not None]
    ordered = sorted(measured_vals)
    return {
        "n_items": len(items),
        "n_measured": len(measured_vals),
        "bands": bands,
        "worst_rel_error": max(ordered) if ordered else None,
        "median_rel_error": ordered[len(ordered) // 2] if ordered else None,
        "items": sorted(items, key=lambda i: (str(i["ext_id"]), i["criterion_id"])),
        "source": "attestations" if attestations else "node_aggregate",
    }


# ─────────────────────────── 汇总裁决 ───────────────────────────

def audit(plans: Sequence[Mapping[str, Any]],
          nodes: Sequence[Mapping[str, Any]],
          missions: Sequence[Mapping[str, Any]] | None = None,
          reports: Sequence[Mapping[str, Any]] | None = None,
          config: Mapping[str, Any] | None = None,
          ledger: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
          ext_id_registry: Sequence[Mapping[str, Any]] | None = None,
          attestations: Sequence[Mapping[str, Any]] | None = None,
          plan_id: int | None = None) -> dict:
    """纯函数入口：战役五表 + 台账 + 全库注册表 + attestation → 战役级集成审计报告。"""
    cfg = _cfg(config)
    plan_id = as_int(plan_id if plan_id is not None else cfg.get("plan_id"))
    if plan_id is None:
        raise ValueError("plan_id 必须由参数或 config.plan_id 给出")
    plan_nodes = [n for n in nodes or [] if as_int(n.get("plan_id")) in (None, plan_id)]
    excluded = {int(x) for x in (cfg.get("exclude_node_ids") or [])}
    peers = [n for n in plan_nodes if as_int(n.get("id")) not in excluded]

    lineage = check_lineage(plans, plan_id, cfg)
    ext_ids = check_ext_ids(plan_nodes, ext_id_registry)
    repos = check_repos(plan_nodes, cfg.get("repo_whitelist"), cfg.get("repo_aliases"))
    dag = check_dag(plan_nodes)
    node_rows = evaluate_nodes(peers, missions or [], reports or [], cfg,
                               cfg.get("honest_fail_registry"))
    ledger_res = check_ledger(node_rows, ledger, cfg)
    precision = precision_summary(node_rows, attestations)

    classes = {PASS: 0, HONEST_FAIL: 0, UNPROVEN: 0, FAIL: 0}
    for row in node_rows:
        classes[row["class"]] = classes.get(row["class"], 0) + 1

    n_peers = len(node_rows)
    min_pass = cfg.get("min_pass_nodes", "all_peers")
    min_pass_n = n_peers if min_pass == "all_peers" else int(min_pass)
    n_unacceptable = int(precision["bands"].get(UNACCEPTABLE_BAND, 0))
    min_precision = int(cfg.get("min_precision_items", 1))
    precision_note = (f"bands={precision['bands']} n_measured={precision['n_measured']}"
                      f" (min={min_precision}, source={precision['source']})")
    undocumented = sorted(str(r["ext_id"] or r["node_id"]) for r in node_rows
                          if r["class"] == FAIL)

    measured = [
        ("g1_lineage_intact", len(lineage["problems"]), 0, "eq", "; ".join(lineage["problems"])),
        ("g2_ext_id_unique",
         len(ext_ids["duplicates"]) + len(ext_ids["collisions"]) + len(ext_ids["missing_ext_id"]),
         0, "eq", "; ".join(list(ext_ids["duplicates"])
                            + [f"node:{n}" for n in ext_ids["missing_ext_id"]]
                            + [c["ext_id"] for c in ext_ids["collisions"]])),
        ("g3_repo_whitelist", len(repos["violations"]), 0, "eq",
         "; ".join(f"{v['node_id']}:{v['normalized']}" for v in repos["violations"])),
        ("g4_dag_acyclic",
         len(dag["cycles"]) + sum(len(v) for v in dag["out_of_plan"].values())
         + len(dag["self_loops"]) + len(dag["problems"]),
         0, "eq", "; ".join([str(c) for c in dag["cycles"]]
                            + [f"node:{k}->{v}" for k, v in sorted(dag["out_of_plan"].items())]
                            + [f"self_loop:{s}" for s in dag["self_loops"]]
                            + [str(p) for p in dag["problems"]])),
        ("g5_node_evidence", classes[PASS], min_pass_n, "ge",
         f"pass={classes[PASS]}/{n_peers} unproven={classes[UNPROVEN]} "
         f"honest_fail={classes[HONEST_FAIL]} fail={classes[FAIL]}"),
        ("g6_ledger_coverage", ledger_res["coverage"], ledger_res["min_coverage"], "ge",
         ledger_res["note"] or f"covered {ledger_res['n_covered']}/{ledger_res['n_required']}"),
        ("g7_precision_band", n_unacceptable, int(cfg.get("max_unacceptable", 0)), "le",
         precision_note),
        ("g8_no_undocumented_fail", len(undocumented), 0, "eq", "; ".join(undocumented)),
    ]
    titles = dict(CRITERIA_SPEC)
    detail = {"lineage": lineage, "ext_ids": ext_ids, "repos": repos, "dag": dag,
              "ledger": ledger_res, "precision": precision}
    criteria = []
    for cid, value, thr, op, note in measured:
        ok = (value >= thr) if op == "ge" else (value <= thr) if op == "le" else (value == thr)
        if cid == "g7_precision_band" and precision["n_measured"] < min_precision:
            ok = False  # fail-closed：一条精度证据都没有 = 不可判为通过
            note += "; no_precision_evidence"
        criteria.append({"id": cid, "title": titles.get(cid, ""), "op": op,
                         "measured": value, "threshold": thr, "passed": bool(ok),
                         "note": note, "detail_key": _DETAIL_KEYS.get(cid)})

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "plan_name": (lineage["chain"][0] if lineage["chain"] else {}).get("name"),
        "plan_status": (lineage["chain"][0] if lineage["chain"] else {}).get("status"),
        "verdict": "ready" if all(c["passed"] for c in criteria) else "not_ready",
        "criteria": criteria,
        "n_nodes_total": len(plan_nodes),
        "n_nodes_audited": n_peers,
        "excluded_node_ids": sorted(excluded),
        "classes": classes,
        "nodes": node_rows,
        "honest_fail": {
            "declared": sorted(str(r["ext_id"]) for r in node_rows
                               if r["class"] == HONEST_FAIL),
            "undocumented": undocumented,
            "policy": "honest FAIL 不可覆盖：失败态节点必须申报，且台账等级不得高于 MOCK",
        },
        "lineage": lineage,
        "ext_ids": ext_ids,
        "repos": repos,
        "dag": dag,
        "ledger": ledger_res,
        "precision": precision,
        "input_digest": digest({"plans": plans, "nodes": plan_nodes, "missions": missions,
                                "reports": reports, "ledger": ledger,
                                "registry": ext_id_registry, "attestations": attestations,
                                "config": cfg, "plan_id": plan_id}),
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    """确定性 Markdown 渲染（无 wall clock）。"""
    lines: list[str] = []
    lines.append(f"# 战役级集成审计 · plan {report.get('plan_id')} — {report.get('verdict')}")
    lines.append("")
    lines.append(f"- schema: `{report.get('schema_version')}`")
    lines.append(f"- plan: `{report.get('plan_name')}`（status={report.get('plan_status')}）")
    lines.append(f"- 审计节点: {report.get('n_nodes_audited')} / 计划节点 {report.get('n_nodes_total')}"
                 f"（排除 {report.get('excluded_node_ids')}）")
    lines.append(f"- input_digest: `{report.get('input_digest')}`")
    lines.append("")
    lines.append("## 1. 八条机裁判据")
    lines.append("")
    lines.append("| 判据 | 实测 | 阈值 | 判定 | 注 |")
    lines.append("|---|---|---|---|---|")
    for c in report.get("criteria") or []:
        lines.append(f"| `{c['id']}` {c['title']} | {c['measured']} | {c['op']} {c['threshold']} "
                     f"| {'PASS' if c['passed'] else 'FAIL'} | {c['note']} |")
    lines.append("")
    lines.append("## 2. 节点能力矩阵")
    lines.append("")
    lines.append("| 节点 | ext_id | 仓 | 节点/mission | 分类 | 判据 pass/all | 最差 rel_err | 精度带 | 台账能力 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report.get("nodes") or []:
        rel = r.get("worst_rel_error")
        rel_s = "—" if rel is None else f"{float(rel) * 100:.2f}%"
        lines.append(
            f"| {r.get('node_id')} | `{r.get('ext_id')}` | {r.get('repo')} "
            f"| {r.get('node_status')}/{r.get('mission_status')} | **{r.get('class')}** "
            f"| {r.get('n_passed')}/{r.get('n_criteria')} | {rel_s} | {r.get('band')} "
            f"| {len(r.get('capability_ids') or [])} |")
    lines.append("")
    classes = report.get("classes") or {}
    lines.append(f"四分类：pass={classes.get('pass', 0)} · honest_fail={classes.get('honest_fail', 0)}"
                 f" · unproven={classes.get('unproven', 0)} · fail={classes.get('fail', 0)}")
    lines.append("")
    lines.append("## 3. 谱系 / ext_id / 白名单 / DAG")
    lines.append("")
    lineage = report.get("lineage") or {}
    chain = " → ".join(str(e.get("plan_id")) for e in lineage.get("chain") or [])
    lines.append(f"- 谱系链: {chain}（depth={lineage.get('depth')}, ok={lineage.get('ok')}）"
                 f" problems={lineage.get('problems')}")
    ext = report.get("ext_ids") or {}
    lines.append(f"- ext_id: 计划内 {ext.get('n_in_plan')} 条 / 注册表 {ext.get('n_registry')} 行；"
                 f"duplicates={ext.get('duplicates')} "
                 f"collisions={[c['ext_id'] for c in ext.get('collisions') or []]}")
    repos = report.get("repos") or {}
    lines.append(f"- 仓白名单: {repos.get('n_nodes')} 节点，violations={repos.get('violations')}")
    dag = report.get("dag") or {}
    lines.append(f"- DAG: ok={dag.get('ok')} n_nodes={dag.get('n_nodes')} cycles={dag.get('cycles')} "
                 f"out_of_plan={dag.get('out_of_plan')} self_loops={dag.get('self_loops')} "
                 f"order={dag.get('order')}")
    lines.append("")
    lines.append("## 4. 精度带汇总")
    lines.append("")
    precision = report.get("precision") or {}
    lines.append(f"- 条目 {precision.get('n_items')}（已量测 {precision.get('n_measured')}），"
                 f"bands={precision.get('bands')}，worst={precision.get('worst_rel_error')}，"
                 f"median={precision.get('median_rel_error')}，来源={precision.get('source')}")
    lines.append("")
    lines.append("## 5. 能力台账覆盖")
    lines.append("")
    ledger = report.get("ledger") or {}
    lines.append(f"- 需求 {ledger.get('n_required')} 条 / 覆盖 {ledger.get('n_covered')} "
                 f"(coverage={ledger.get('coverage')}, min={ledger.get('min_coverage')}, "
                 f"台账条目={ledger.get('n_ledger_entries')})")
    lines.append(f"- missing={ledger.get('missing')}")
    lines.append(f"- honest-fail 冲突={ledger.get('honest_fail_violations')}")
    lines.append("")
    lines.append("## 6. honest FAIL 声明")
    lines.append("")
    honest = report.get("honest_fail") or {}
    lines.append(f"- 已申报: {honest.get('declared')}")
    lines.append(f"- 未申报（阻塞）: {honest.get('undocumented')}")
    lines.append(f"- 纪律: {honest.get('policy')}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ─────────────────────────── DB 输入装载（只读） ───────────────────────────

def _psql_json(sql: str, psql_cmd: Sequence[str] | None = None) -> Any:
    cmd = list(psql_cmd or ["runuser", "-u", "postgres", "--", "psql",
                            "-d", "tensorplatform", "-t", "-A"])
    out = subprocess.run(cmd + ["-c", sql], capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:400]}")
    raw = out.stdout.strip()
    return json.loads(raw) if raw else []


def load_inputs_from_db(plan_id: int, psql_cmd: Sequence[str] | None = None,
                        include_registry: bool = True) -> dict:
    """只读导出：全计划行 + 本计划节点/mission/report + 全库 ext_id 注册表。"""
    plans_sql = ("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM "
                 "(SELECT id,name,status,metadata FROM campaign_plans ORDER BY id) t;")
    nodes_sql = ("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM "
                 "(SELECT id,plan_id,status,reproduction_verdict,dependencies,mission_id,"
                 f"metadata FROM campaign_nodes WHERE plan_id={int(plan_id)} ORDER BY id) t;")
    miss_sql = ("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM "
                "(SELECT id,status,task_key,report_owed,report_id,fail_stage,result "
                f"FROM tensor_dev_missions WHERE task_key LIKE 'p{int(plan_id)}-%' ORDER BY id) t;")
    rep_sql = ("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM "
               "(SELECT r.id,r.mission_id,r.status,r.markdown FROM tensor_reports r "
               "JOIN tensor_dev_missions m ON m.id=r.mission_id "
               f"WHERE m.task_key LIKE 'p{int(plan_id)}-%' ORDER BY r.id) t;")
    reg_sql = ("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM "
               "(SELECT id,plan_id,metadata->>'ext_id' AS ext_id FROM campaign_nodes) t;")
    return {
        "plan_id": int(plan_id),
        "plans": _psql_json(plans_sql, psql_cmd),
        "nodes": _psql_json(nodes_sql, psql_cmd),
        "missions": _psql_json(miss_sql, psql_cmd),
        "reports": _psql_json(rep_sql, psql_cmd),
        "ext_id_registry": _psql_json(reg_sql, psql_cmd) if include_registry else [],
    }


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="战役级全链路集成审计（八条机裁判据）")
    parser.add_argument("--plan", type=int, required=True)
    parser.add_argument("--config")
    parser.add_argument("--ledger")
    parser.add_argument("--inputs", help="离线输入 JSON（load_inputs_from_db 的产物）")
    parser.add_argument("--from-db", action="store_true")
    parser.add_argument("--attestations", action="append", default=[])
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    parser.add_argument("--fig-dir")
    parser.add_argument("--strict", action="store_true", help="未就绪时返回 3")
    args = parser.parse_args(argv)

    try:
        if args.inputs:
            inputs = _load_json(args.inputs)
        elif args.from_db:
            inputs = load_inputs_from_db(args.plan)
        else:
            parser.error("需要 --from-db 或 --inputs")
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"输入不可用: {exc}", file=sys.stderr)
        return 2

    cfg = _load_json(args.config) if args.config else {}
    cfg.setdefault("plan_id", args.plan)
    ledger = _load_json(args.ledger) if args.ledger else None
    attestations: list[dict] = []
    for path in args.attestations:
        payload = _load_json(path)
        attestations.extend(payload if isinstance(payload, list) else [payload])

    report = audit(inputs.get("plans") or [], inputs.get("nodes") or [],
                   inputs.get("missions") or [], inputs.get("reports") or [],
                   cfg, ledger=ledger,
                   ext_id_registry=inputs.get("ext_id_registry") or [],
                   attestations=attestations, plan_id=args.plan)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    if args.md_out:
        out = Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
    if args.fig_dir:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import campaign_integration_audit_figures as figs  # noqa: PLC0415
        figs.render_all(report, Path(args.fig_dir))

    print(json.dumps({"plan_id": report["plan_id"], "verdict": report["verdict"],
                      "classes": report["classes"],
                      "failed": [c["id"] for c in report["criteria"] if not c["passed"]]},
                     ensure_ascii=False))
    if args.strict and report["verdict"] != "ready":
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())