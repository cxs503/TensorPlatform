#!/usr/bin/env python3
"""campaign_gate_snapshot —— 战役继承门「DB 快照构建 + 任务书判据审计」层（零领域语义）。

为什么需要这一层
----------------
``ops/taskplane/inheritance_gate.py`` 的裁决器 ``evaluate_inheritance_gate(snapshot, config)``
已经能对「节点是否真验收 / 跨节点同一量互裁 / 治理成熟度 / 就绪度」做纯函数裁决；但它
**只有一条快照入口** ``build_snapshot_from_api``（从 :8010 只读拉取，需要 token）。

继承门节点（node_type=gate）的验收判据里，除就绪度评分外还额外要求逐条可机验：
  (a) 前置节点 status 全部 done；
  (b) 无「未记录的 honest FAIL」（失败终态但没有报告记录）；
  (c) 战役产物完整性 100%（每个前置节点有报告/工件，且工件路径可解析）；
  (d) 能力台账已更新。
这四条不是平台通用评分分量，需要一层薄薄的**审计叠层**把它机器化，避免人工读报告下判。

本模块做两件事（都做成可复算的纯函数 + 一个只读 DB 装载器）：

1. ``assemble_snapshot`` / ``build_snapshot_from_db``
   把 ``campaign_plans / campaign_milestones / campaign_nodes / tensor_dev_missions /
   tensor_reports`` 五张表拼成 ``inheritance_gate`` 直接可消费的 snapshot（离线、可审计）；
   DB 装载器用 ``psql ... row_to_json`` 只读导出，不写库、不改状态。
2. ``audit_gate_criteria``
   在 gate 报告之上叠加 (a)-(d) 四条判据的机裁结论 + honest-FAIL 台账 + 建议，输出可进 CI
   的 JSON。判据覆写全部由 ``config`` 注入，模块内**不含任何领域词条/判据/算例**（平台铁律）。

确定性
------
``input_digest`` 由内容派生（沿用 inheritance_gate）；审计层不取 wall clock，除 (d) 的台账
新鲜度检查显式以 ``config.ledger_reference_utc`` 为参考时钟（缺省不检查新鲜度，只看存在与
可解析），故同一 DB 快照 + config 连跑两次逐字节相同。

CLI
---
    python3 ops/taskplane/campaign_gate_snapshot.py \
        --plan 185 \
        --config  artifacts/m6836/gate_config.json \
        --snapshot-out artifacts/m6836/plan185_snapshot.json \
        --json-out     artifacts/m6836/inheritance_gate.json \
        --md-out       artifacts/m6836/inheritance_gate.md \
        --audit-out    artifacts/m6836/predecessor_audit.json

退出码：0 = 继承门就绪；2 = 未就绪（含任一条任务书判据未过）；3 = 输入/DB 错误。
**未就绪绝不返回 0。**
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # 同包内直接复用裁决器（正常导入路径）
    from .inheritance_gate import (
        FAILED_MISSION,
        evaluate_inheritance_gate,
        render_markdown,
    )
except ImportError:  # 作为脚本直接运行时（sys.path 含本目录）
    from inheritance_gate import (  # type: ignore
        FAILED_MISSION,
        evaluate_inheritance_gate,
        render_markdown,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMA_VERSION = "1.0.0"

DEFAULT_DONE_STATUSES = frozenset({"done", "completed", "merged", "verified", "accepted"})


# ─────────────────────────── 快照装配（纯函数） ───────────────────────────

def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_node(node: Mapping[str, Any]) -> dict:
    return {
        "id": _as_int(node.get("id")),
        "plan_id": _as_int(node.get("plan_id")),
        "milestone_id": _as_int(node.get("milestone_id")),
        "mission_id": _as_int(node.get("mission_id")),
        "name": node.get("name"),
        "node_type": node.get("node_type") or "task",
        "status": node.get("status"),
        "reproduction_verdict": node.get("reproduction_verdict"),
        "dependencies": list(node.get("dependencies") or []),
        "metadata": dict(node.get("metadata") or {}),
    }


def assemble_snapshot(plan: Mapping[str, Any] | None,
                      milestones: Sequence[Mapping[str, Any]],
                      nodes: Sequence[Mapping[str, Any]],
                      missions: Sequence[Mapping[str, Any]],
                      reports: Sequence[Mapping[str, Any]]) -> dict:
    """把五张表的行（可为 DB 原始导出）装配成 inheritance_gate 的 snapshot。

    missions 以 ``str(id)`` 为键；reports 以 ``str(mission_id)`` 为键聚合成列表。
    任何缺字段一律保守缺省（不猜）。
    """
    mission_map: dict[str, dict] = {}
    for m in missions or []:
        mid = _as_int(m.get("id"))
        if mid is None:
            continue
        mission_map[str(mid)] = {
            "id": mid,
            "status": m.get("status"),
            "task_key": m.get("task_key"),
            "report_owed": bool(m.get("report_owed")),
            "report_id": _as_int(m.get("report_id")),
            "fail_stage": m.get("fail_stage"),
            "result": m.get("result") or {},
        }

    report_map: dict[str, list[dict]] = {}
    for r in reports or []:
        mid = _as_int(r.get("mission_id"))
        if mid is None:
            continue
        report_map.setdefault(str(mid), []).append({
            "id": r.get("id"),
            "mission_id": mid,
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "markdown": r.get("markdown"),
        })
    for k in report_map:
        report_map[k].sort(key=lambda x: str(x.get("created_at") or ""))

    return {
        "snapshot_meta": {"source": "db", "plan_id": _as_int((plan or {}).get("id"))},
        "plan": dict(plan or {}),
        "milestones": [dict(m) for m in milestones or []],
        "nodes": [_norm_node(n) for n in nodes or []],
        "missions": mission_map,
        "reports": report_map,
    }


# ─────────────────────────── DB 只读装载器 ───────────────────────────

def _psql_json(sql: str, psql_cmd: Sequence[str] | None = None) -> Any:
    cmd = list(psql_cmd or ["runuser", "-u", "postgres", "--", "psql",
                            "-d", "tensorplatform", "-t", "-A"])
    full = cmd + ["-c", sql]
    out = subprocess.run(full, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:400]}")
    raw = out.stdout.strip()
    if not raw:
        return []
    return json.loads(raw)


def build_snapshot_from_db(plan_id: int,
                           psql_cmd: Sequence[str] | None = None) -> dict:
    """只读从平台 DB 导出 plan/milestones/nodes/missions/reports 并装配快照。

    只读：全部是 SELECT，绝不 UPDATE。
    """
    plan_sql = (
        "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT id,name,status,"
        "metadata,created_at::text AS created_at FROM campaign_plans WHERE id="
        f"{int(plan_id)}) t;")
    ms_sql = (
        "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT id,plan_id,name,"
        "status,metadata FROM campaign_milestones WHERE plan_id="
        f"{int(plan_id)} ORDER BY id) t;")
    node_sql = (
        "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT id,plan_id,"
        "milestone_id,mission_id,name,node_type,status,reproduction_verdict,dependencies,"
        "metadata FROM campaign_nodes WHERE plan_id="
        f"{int(plan_id)} ORDER BY id) t;")
    miss_sql = (
        "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT id,status,task_key,"
        "report_owed,report_id,fail_stage,result FROM tensor_dev_missions WHERE task_key LIKE "
        f"'p{int(plan_id)}-%' ORDER BY id) t;")
    rep_sql = (
        "SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT r.id,r.mission_id,"
        "r.status,r.created_at::text AS created_at,r.markdown FROM tensor_reports r "
        "JOIN tensor_dev_missions m ON m.id=r.mission_id WHERE m.task_key LIKE "
        f"'p{int(plan_id)}-%' ORDER BY r.id) t;")

    plan_rows = _psql_json(plan_sql, psql_cmd)
    ms_rows = _psql_json(ms_sql, psql_cmd)
    node_rows = _psql_json(node_sql, psql_cmd)
    miss_rows = _psql_json(miss_sql, psql_cmd)
    rep_rows = _psql_json(rep_sql, psql_cmd)
    if not plan_rows:
        raise RuntimeError(f"plan_id={plan_id} 不存在")
    return assemble_snapshot(plan_rows[0], ms_rows, node_rows, miss_rows, rep_rows)


# ─────────────────────────── 任务书判据审计叠层（纯函数） ───────────────────────────

def _resolve_predecessors(snapshot: Mapping[str, Any], config: Mapping[str, Any]) -> list[int]:
    explicit = config.get("predecessor_node_ids")
    if explicit:
        return [_as_int(x) for x in explicit if _as_int(x) is not None]
    gate_id = _as_int(config.get("gate_node_id"))
    nodes = {_as_int(n.get("id")): n for n in snapshot.get("nodes") or []}
    if gate_id in nodes:
        return [_as_int(x) for x in (nodes[gate_id].get("dependencies") or []) if _as_int(x)]
    return []  # 无显式前置且无门节点依赖 → 空（审计会点名）


def _artifact_resolvable(path: str, roots: Sequence[str | Path]) -> bool:
    p = Path(path)
    if p.is_absolute():
        return p.exists()
    return any((Path(r) / p).exists() for r in roots)


def audit_gate_criteria(report: Mapping[str, Any], snapshot: Mapping[str, Any],
                        config: Mapping[str, Any] | None = None,
                        roots: Sequence[str | Path] = ()) -> dict:
    """在 gate 报告之上计算任务书 (a)-(d) 四条继承门判据（机裁）。

    report   : evaluate_inheritance_gate 的输出
    snapshot : assemble_snapshot / build_snapshot_from_db 的输出
    config   : done_statuses / predecessor_node_ids / gate_node_id /
               capability_ledger_path / capability_ledger_min_entries /
               ledger_reference_utc
    """
    cfg = dict(config or {})
    roots = list(roots) or [REPO_ROOT]
    done_statuses = {str(s).lower() for s in cfg.get("done_statuses", DEFAULT_DONE_STATUSES)}

    nodes = {_as_int(n.get("id")): n for n in snapshot.get("nodes") or []}
    evals = {_as_int(ev.get("node_id")): ev for ev in report.get("nodes") or []}
    missions = snapshot.get("missions") or {}
    reports = snapshot.get("reports") or {}

    predecessors = _resolve_predecessors(snapshot, cfg)

    # ---- (a) 前置节点 status 全部 done ----
    a_rows, a_done = [], 0
    for pid in predecessors:
        node = nodes.get(pid, {})
        ev = evals.get(pid, {})
        status = str(ev.get("node_status") or node.get("status") or "unknown").lower()
        is_done = status in done_statuses
        a_done += int(is_done)
        a_rows.append({
            "node_id": pid,
            "ext_id": (node.get("metadata") or {}).get("ext_id"),
            "status": status,
            "mission_id": node.get("mission_id"),
            "done": is_done,
        })
    a_pass = bool(predecessors) and a_done == len(predecessors)

    # ---- (b) 无未记录的 honest FAIL ----
    violations: list[dict] = []
    # b1: 失败终态 mission 且 report_owed 但无报告 = 未记录
    for mid_key, m in missions.items():
        status = str(m.get("status") or "").lower()
        has_report = bool(reports.get(str(mid_key)))
        if status in FAILED_MISSION and (m.get("report_owed") and not has_report):
            violations.append({
                "kind": "unrecorded_failed_mission",
                "mission_id": m.get("id"),
                "task_key": m.get("task_key"),
                "status": status,
                "report_owed": True,
                "has_report": has_report,
            })
    # b2: 自报 done 但判据未全过 = 隐藏未完成（粉饰）
    for pid in predecessors:
        ev = evals.get(pid, {})
        status = str(ev.get("node_status") or "").lower()
        declared = int(ev.get("criteria_declared") or 0)
        passed = int(ev.get("criteria_passed") or 0)
        if status in done_statuses and declared and passed < declared:
            violations.append({
                "kind": "hidden_incomplete",
                "node_id": pid,
                "declared": declared,
                "passed": passed,
            })
    b_pass = not violations

    # ---- (c) 战役产物完整性 ----
    c_rows, c_complete = [], 0
    for pid in predecessors:
        node = nodes.get(pid, {})
        ev = evals.get(pid, {})
        mid = node.get("mission_id")
        has_report = bool(reports.get(str(mid))) if mid is not None else False
        arts = list(ev.get("evidence_artifacts") or [])
        unresolved = [a for a in arts if not _artifact_resolvable(a, roots)]
        has_product = has_report or bool(arts)
        ok = has_product and not unresolved
        c_complete += int(ok)
        c_rows.append({
            "node_id": pid,
            "ext_id": (node.get("metadata") or {}).get("ext_id"),
            "mission_id": mid,
            "has_report": has_report,
            "artifacts": arts,
            "unresolved_artifacts": unresolved,
            "product_ok": ok,
        })
    c_fraction = (c_complete / len(predecessors)) if predecessors else 0.0
    c_pass = bool(predecessors) and c_complete == len(predecessors)

    # ---- (d) 能力台账已更新 ----
    ledger_path = cfg.get("capability_ledger_path")
    min_entries = int(cfg.get("capability_ledger_min_entries", 1))
    d_detail: dict[str, Any] = {"path": ledger_path, "exists": False,
                                "entries": 0, "min_entries": min_entries}
    d_pass = False
    if ledger_path:
        lp = Path(ledger_path)
        if not lp.is_absolute():
            lp = REPO_ROOT / lp
        d_detail["path"] = str(lp)
        if lp.exists():
            d_detail["exists"] = True
            try:
                payload = json.loads(lp.read_text(encoding="utf-8"))
                entries = payload.get("capabilities") or payload.get("entries") or []
                d_detail["entries"] = len(entries)
                d_detail["schema_version"] = payload.get("schema_version")
                ref = cfg.get("ledger_reference_utc")
                if ref:
                    import datetime as _dt
                    mt = _dt.datetime.utcfromtimestamp(lp.stat().st_mtime)
                    d_detail["mtime_utc"] = mt.isoformat() + "Z"
                    d_detail["reference_utc"] = ref
                    d_pass = d_detail["entries"] >= min_entries and \
                        d_detail["mtime_utc"] >= str(ref)
                else:
                    d_pass = d_detail["entries"] >= min_entries
            except (OSError, ValueError) as exc:
                d_detail["error"] = str(exc)

    # ---- honest-FAIL 台账：逐判据记录 已测且失败 / 未测 ----
    honest_fail: list[dict] = []
    for pid in predecessors:
        ev = evals.get(pid, {})
        for c in ev.get("criteria") or []:
            if c.get("status") == "passed":
                continue
            honest_fail.append({
                "node_id": pid,
                "ext_id": (nodes.get(pid, {}).get("metadata") or {}).get("ext_id"),
                "criterion": c.get("raw"),
                "status": c.get("status"),          # passed/failed/unproven
                "reason": c.get("reason"),
                "recorded": bool(ev.get("evidence_artifacts")) or bool(
                    reports.get(str(nodes.get(pid, {}).get("mission_id")))),
            })

    criteria = {
        "a_all_predecessors_done": {"pass": a_pass, "done": a_done,
                                     "total": len(predecessors), "rows": a_rows},
        "b_no_unrecorded_honest_fail": {"pass": b_pass, "violations": violations},
        "c_artifact_completeness": {"pass": c_pass, "completeness": round(c_fraction, 6),
                                     "complete": c_complete, "total": len(predecessors),
                                     "rows": c_rows},
        "d_capability_ledger_updated": {"pass": d_pass, "detail": d_detail},
    }
    gate_ready = all(c["pass"] for c in criteria.values())

    blockers = [k for k, c in criteria.items() if not c["pass"]]
    recommendation = (
        "继承门就绪：全部前置节点已验收、无未记录 honest FAIL、产物完整、能力台账已更新。"
        if gate_ready else
        "继承门未就绪：前置节点尚未全部 done（或存在未记录失败/产物缺失）。"
        "本门按 honest FAIL 纪律如实判 not_ready，不得粉饰；待前置节点验收通过后重跑本审计。"
    )

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "plan_id": (snapshot.get("plan") or {}).get("id"),
        "gate_node_id": _as_int(cfg.get("gate_node_id")),
        "predecessor_node_ids": predecessors,
        "criteria": criteria,
        "gate_ready": gate_ready,
        "blockers": blockers,
        "honest_fail_ledger": honest_fail,
        "recommendation": recommendation,
        "input_digest": report.get("input_digest"),
    }


# ─────────────────────────── 端到端编排 ───────────────────────────

def run_gate(plan_id: int, config: Mapping[str, Any] | None = None,
             psql_cmd: Sequence[str] | None = None,
             roots: Sequence[str | Path] = ()) -> tuple[dict, dict, dict]:
    """装载快照 → 裁决 → 审计，返回 (snapshot, gate_report, audit)。"""
    cfg = dict(config or {})
    snapshot = build_snapshot_from_db(plan_id, psql_cmd=psql_cmd)
    gate_report = evaluate_inheritance_gate(snapshot, cfg)
    audit = audit_gate_criteria(gate_report, snapshot, cfg, roots=roots)
    return snapshot, gate_report, audit


# ─────────────────────────── CLI ───────────────────────────

def _write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                 encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="战役继承门：DB 快照 + 任务书判据审计")
    ap.add_argument("--plan", type=int, required=True, help="战役 plan_id")
    ap.add_argument("--config", help="gate 配置 JSON")
    ap.add_argument("--snapshot-out", help="DB 快照落盘路径")
    ap.add_argument("--json-out", help="gate 裁决报告 JSON 输出")
    ap.add_argument("--md-out", help="gate 裁决摘要 Markdown 输出")
    ap.add_argument("--audit-out", help="任务书判据审计 JSON 输出")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    config: dict = {}
    if args.config:
        try:
            config = json.loads(Path(args.config).read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            print(f"config 读取失败: {exc}", file=sys.stderr)
            return 3
    try:
        snapshot, report, audit = run_gate(args.plan, config)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"DB 装载/裁决失败: {exc}", file=sys.stderr)
        return 3

    if args.snapshot_out:
        _write_json(args.snapshot_out, snapshot)
    if args.json_out:
        _write_json(args.json_out, report)
    if args.audit_out:
        _write_json(args.audit_out, audit)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(report), encoding="utf-8")
    if not args.quiet:
        print(render_markdown(report))
        print(json.dumps({"gate_ready": audit["gate_ready"],
                          "criteria_pass": {k: v["pass"] for k, v in audit["criteria"].items()}},
                         ensure_ascii=False, indent=2))
    ready = audit["gate_ready"] and report.get("verdict") == "ready"
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())