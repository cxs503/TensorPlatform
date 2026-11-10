#!/usr/bin/env python3
"""campaign_acceptance —— 战役**验收门**（节点状态普查 × 诚实失败台账 × 集成门 × 治理集成 × 台账登记）。

为什么再要一层
--------------
手上已有三层，各自解决一半，但**没有一层回答「这场战役现在能不能验收」**：

* ``inheritance_gate``：单节点「是否真验收」+ 跨节点同一量互裁；
* ``campaign_gate_snapshot.audit_gate_criteria``：门节点任务书四条判据的机裁叠层；
* ``campaign_integration_verify``：跨域产物**可组合性**（形状契约 + 拓扑契约 + 四分类汇总）。

缺口是**战役级汇总裁决**：一场战役的验收结论由五个互相独立的命题合取而成，
任何一条只能用别的手段替代时就会出现「绿皮报告」通道：

| 命题 | 失败长什么样 | 本层怎么裁 |
|---|---|---|
| A. 节点是否都已收口 | 还有 ``pending`` / ``in-flight``，战役却报完成 | ``a_all_task_nodes_closed``（状态普查，closed=failed 也算收口） |
| B. 失败是否**有记录** | 节点 ``failed`` 但没有任何 honest-FAIL 台账条目 | ``b_failed_nodes_documented``（reason+evidence 双必填） |
| C. 收口节点是否**有证据** | 状态 done，零报告/零工件 | ``c_closed_nodes_evidenced``（每节点报告数 ≥ 下限） |
| D. 跨域成果是否**可组合** | 各节点各自通过，下游字段上游没产 | ``d_integration_gate``（引用集成门六判据，不重算） |
| E. 治理工具是否**真进了工具链** | 成熟化节点的模块只在分支里，registry/CI 没挂 | ``e_governance_integrated``（路径+导入+导出符号+测试引用四检） |
| F. 能力是否**已登记** | 做完没人知道，下个战役重复发明 | ``f_capability_ledger_registered``（台账条目 schema + 证据可解析） |

``verdict = "pass"`` 当且仅当六条全过；否则 ``"not_ready"``。退出码 0/2/3，
**未就绪绝不返回 0**（与既有三层同一条纪律：拒绝绿皮报告）。

与既有层的关系（不重算、不覆盖）
--------------------------------
* A/B/C/F 是本层**新增**的战役级普查；D 直接**引用** ``campaign_integration_verify``
  的报告（含其内部六判据与四分类），不再做第二套判据；
* E 只做**工具链在位性**（文件/导入/符号/测试引用），不评价工具本身的算法质量——
  算法质量归各成熟化节点的继承门证据。

零领域语义（TensorPlatform 铁律）
--------------------------------
模块内**不含任何领域词条、域判据、域算例**：状态名集合、节点类型、台账条目、
治理模块清单、能力 id、证据路径全部由 ``config`` / ``governance_spec`` /
``ledger_spec`` 三份注入文档给出；测试夹具用合成名字（``N-A``/``alpha``/``unit_x``）。

确定性
------
报告不取 wall clock；``input_digest`` 由 (节点普查, 集成门报告, 治理规格, 台账规格)
规范化 JSON 派生，同一输入连跑两次逐字节相同。

CLI
---
    python3 ops/taskplane/campaign_acceptance.py --fetch-api --plan 501 \
        --contract artifacts/<campaign>/integration_contract.json \
        --config   artifacts/<campaign>/gate_config.json \
        --governance artifacts/<campaign>/governance_spec.json \
        --ledger-spec artifacts/<campaign>/capability_registration.json \
        --json-out artifacts/<campaign>/acceptance_report.json \
        --md-out   artifacts/<campaign>/acceptance_report.md
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import campaign_integration_verify as civ  # noqa: E402  (同目录模块)
import inheritance_gate as ig  # noqa: E402  (同目录模块)

SCHEMA_VERSION = "campaign_acceptance/v1"

DEFAULT_CONFIG: dict[str, Any] = {
    # 状态词汇表（域中性；由 config 注入，缺省给通用取值）
    "closed_statuses": ["done", "failed", "completed", "cancelled"],
    "failed_statuses": ["failed", "aborted", "error"],
    "open_statuses": ["pending", "in-flight", "in_flight", "in_progress",
                      "not_started", "blocked", "queued"],
    # 哪类节点参与「收口普查」（缺省只有 task）
    "census_node_types": ["task"],
    # C：每个收口节点至少要有多少份报告
    "min_reports_per_closed_node": 1,
    # B：失败节点台账（node_ext_id -> {reason, evidence}），未列入即视为未记录
    "failed_ledger": {},
    # D：是否要求集成门 pass（False 只记录不阻塞，默认阻塞）
    "require_integration_pass": True,
    # E/F 开关
    "require_governance": True,
    "require_ledger": True,
    # 证据根（artifact 路径解析用）
    "roots": ["."],
}


# ─────────────────────────── 小工具 ───────────────────────────

def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")


def node_ext_id(node: Mapping[str, Any]) -> str:
    md = node.get("metadata") or {}
    return str(md.get("ext_id") or md.get("external_id") or node.get("id") or "")


def _reports_for(snapshot: Mapping[str, Any], node: Mapping[str, Any]) -> list[dict]:
    mid = node.get("mission_id")
    reports = snapshot.get("reports") or {}
    if mid is None:
        return []
    items = reports.get(str(mid))
    if items is None:
        items = reports.get(mid)  # type: ignore[arg-type]
    return list(items or [])


def _status_bucket(status: Any, cfg: Mapping[str, Any]) -> str:
    s = str(status or "").strip().lower().replace("_", "-")
    # failed 必须先判：「失败」既是收口也是失败，混淆会把未记录的失败洗成干净收口
    if s in {str(x).strip().lower().replace("_", "-")
             for x in cfg.get("failed_statuses", [])}:
        return "failed"
    if s in {str(x).strip().lower().replace("_", "-")
             for x in cfg.get("closed_statuses", [])}:
        return "closed"
    if s in {str(x).strip().lower().replace("_", "-")
             for x in cfg.get("open_statuses", [])}:
        return "open"
    return "unknown"


# ─────────────────────────── A：状态普查 ───────────────────────────

def census(plan_nodes: Sequence[Mapping[str, Any]],
           cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """按 config 的状态词汇表普查计划内节点，回答「是否全部收口」。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    types = {str(t) for t in cfg.get("census_node_types") or []}
    rows: list[dict[str, Any]] = []
    counts = {"total": 0, "closed": 0, "failed": 0, "open": 0, "unknown": 0}
    open_nodes: list[dict[str, Any]] = []
    failed_nodes: list[dict[str, Any]] = []
    for node in plan_nodes:
        ntype = str(node.get("node_type") or "task")
        if types and ntype not in types:
            continue
        counts["total"] += 1
        bucket = _status_bucket(node.get("status"), cfg)
        # failed 状态同时算收口：战役验收允许诚实失败，但必须另有台账（判据 B）
        if bucket == "failed":
            counts["failed"] += 1
            counts["closed"] += 1
            failed_nodes.append({"node_id": node.get("id"), "ext_id": node_ext_id(node),
                                 "status": node.get("status")})
        else:
            counts[bucket] = counts.get(bucket, 0) + 1
        if bucket in ("open", "unknown"):
            open_nodes.append({"node_id": node.get("id"), "ext_id": node_ext_id(node),
                               "status": node.get("status"), "bucket": bucket})
        rows.append({"node_id": node.get("id"), "ext_id": node_ext_id(node),
                     "node_type": ntype, "status": node.get("status"),
                     "bucket": bucket,
                     "mission_id": node.get("mission_id"),
                     "reproduction_verdict": node.get("reproduction_verdict"),
                     "dependencies": list(node.get("dependencies") or [])})
    criterion = {
        "id": "a_all_task_nodes_closed",
        "passed": bool(rows) and counts["open"] == 0 and counts["unknown"] == 0,
        "detail": {**counts, "open_nodes": open_nodes,
                   "open_statuses": sorted({str(o["status"]) for o in open_nodes})},
    }
    return {"counts": counts, "nodes": rows, "open_nodes": open_nodes,
            "failed_nodes": failed_nodes, "criterion": criterion}


# ─────────────────────────── B：诚实失败台账 ───────────────────────────

def honest_fail_audit(failed_nodes: Sequence[Mapping[str, Any]],
                      ledger: Mapping[str, Any] | None) -> dict[str, Any]:
    """失败节点必须有 ``reason`` + ``evidence`` 双字段，否则算「未记录的失败」。"""
    ledger = ledger or {}
    undocumented: list[dict[str, Any]] = []
    documented: list[dict[str, Any]] = []
    for node in failed_nodes:
        key = str(node.get("ext_id") or node.get("node_id"))
        entry = ledger.get(key) or ledger.get(str(node.get("node_id")))
        if not isinstance(entry, Mapping):
            undocumented.append({"ext_id": key, "why": "no_ledger_entry"})
            continue
        reason = str(entry.get("reason") or "").strip()
        evidence = entry.get("evidence")
        has_ev = bool(evidence) if not isinstance(evidence, str) else bool(evidence.strip())
        if not reason or not has_ev:
            undocumented.append({"ext_id": key,
                                 "why": "missing_reason" if not reason else "missing_evidence"})
            continue
        documented.append({"ext_id": key, "reason": reason, "evidence": evidence})
    # 反向检查：台账里登记的条目必须对应一个真失败的节点（防止事后补白）
    failed_keys = {str(n.get("ext_id") or n.get("node_id")) for n in failed_nodes}
    ghost = sorted(k for k in ledger if str(k) not in failed_keys)
    criterion = {
        "id": "b_failed_nodes_documented",
        "passed": not undocumented,
        "detail": {"failed": len(failed_nodes), "documented": len(documented),
                   "undocumented": undocumented, "ghost_entries": ghost},
    }
    return {"documented": documented, "undocumented": undocumented,
            "ghost_entries": ghost, "criterion": criterion}


# ─────────────────────────── C：收口节点的证据 ───────────────────────────

def evidence_completeness(census_rows: Sequence[Mapping[str, Any]],
                          snapshot: Mapping[str, Any],
                          cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """收口（closed/failed）的节点必须至少有一份平台报告可查。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    need = int(cfg.get("min_reports_per_closed_node") or 0)
    by_id = {n.get("id"): n for n in snapshot.get("nodes") or []}
    gaps: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for row in census_rows:
        if row.get("bucket") not in ("closed", "failed"):
            continue
        node = by_id.get(row.get("node_id")) or {}
        reports = _reports_for(snapshot, node)
        rec = {"ext_id": row.get("ext_id"), "reports": len(reports),
               "report_ids": [r.get("id") for r in reports][:5]}
        if len(reports) < need:
            gaps.append(rec)
        else:
            covered.append(rec)
    criterion = {
        "id": "c_closed_nodes_evidenced",
        "passed": not gaps,
        "detail": {"min_reports": need, "covered": len(covered), "gaps": gaps,
                   "vacuous": not covered and not gaps},
    }
    return {"covered": covered, "gaps": gaps, "criterion": criterion}


# ─────────────────────────── D：集成门引用 ───────────────────────────

def integration_criterion(integration_report: Mapping[str, Any] | None,
                          cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """原样引用跨域集成门结论（六判据 + 四分类），不做第二套判据。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    if not integration_report:
        return {"id": "d_integration_gate", "passed": not cfg.get("require_integration_pass", True),
                "detail": {"status": "absent",
                           "required": bool(cfg.get("require_integration_pass", True))}}
    sub = list(integration_report.get("criteria") or [])
    verdict = str(integration_report.get("verdict") or "")
    passed = verdict == "pass" and all(bool(c.get("passed")) for c in sub)
    return {
        "id": "d_integration_gate",
        "passed": passed if cfg.get("require_integration_pass", True) else True,
        "detail": {"verdict": verdict,
                   "required": bool(cfg.get("require_integration_pass", True)),
                   "counts": integration_report.get("counts"),
                   "criteria": [{"id": c.get("id"), "passed": bool(c.get("passed"))}
                                for c in sub]},
    }


# ───────────────────── E：治理工具进工具链（在位性四检） ─────────────────────

def _module_export_ok(path: Path, module_qual: str, required: Iterable[str],
                      import_name: str | None = None,
                      root: Path | None = None) -> tuple[bool, list[str], str | None]:
    """导入模块并检查导出符号。

    两条导入路径（都试，任一成功即可）：
    1. ``import_name`` 包内导入（``root`` 进 sys.path）—— 有相对导入的包内模块走这条；
    2. ``spec_from_file_location`` 按文件导入 —— 顶层脚本/无包上下文模块走这条。
    """
    if not path.exists():
        return False, [], "module_file_missing"
    errors: list[str] = []
    mod = None
    if import_name:
        root_s = str(root or Path("."))
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        try:
            mod = importlib.import_module(import_name)
        except Exception as exc:  # noqa: BLE001 - 导入失败本身就是判据结论
            errors.append(f"package_import_error:{type(exc).__name__}:{exc}"[:200])
    if mod is None:
        try:
            spec = importlib.util.spec_from_file_location(module_qual, path)
            if spec is None or spec.loader is None:
                return False, [], "spec_unavailable"
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_qual] = mod  # 允许模块内自引用
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"file_import_error:{type(exc).__name__}:{exc}"[:200])
            return False, [], " | ".join(errors)
    names = [n for n in required]
    missing = [n for n in names if not hasattr(mod, n)]
    return (not missing), missing, None


def _run_smoke(item: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """可选端到端冒烟：把模块的测试命令真跑一遍，证明它在工具链里**能执行**。

    静态四检只能证明「文件在、符号在、测试提到它」；冒烟把「能跑」也变成证据。
    未声明 ``smoke`` 的模块跳过（``status=not_declared``，不阻塞）。
    """
    smoke = item.get("smoke")
    if not isinstance(smoke, Mapping) or not smoke.get("argv"):
        return {"status": "not_declared"}
    import subprocess
    argv = [str(a) for a in smoke["argv"]]
    timeout = float(smoke.get("timeout") or 900)
    expect = int(smoke.get("expect_exit") or 0)
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "error": f"{type(exc).__name__}:{exc}"[:200],
                "argv": argv}
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return {"status": "ok" if proc.returncode == expect else "failed",
            "argv": argv, "exit": proc.returncode, "expected_exit": expect,
            "stdout_tail": tail,
            "stderr_tail": (proc.stderr or "").strip().splitlines()[-3:]}


def governance_check(spec: Mapping[str, Any] | None,
                     repo_root: str | Path = ".") -> dict[str, Any]:
    """每个治理模块做四检（在位/导入/导出/测试引用）+ 可选端到端冒烟。"""
    spec = spec or {}
    root = Path(repo_root)
    rows: list[dict[str, Any]] = []
    for item in spec.get("modules") or []:
        rel = str(item.get("path") or "")
        path = (root / rel).resolve() if rel else root
        module_qual = str(item.get("module") or Path(rel).stem or "candidate")
        required = list(item.get("exports") or [])
        ok, missing, err = _module_export_ok(path, module_qual, required,
                                             import_name=item.get("import_name"),
                                             root=root)
        tests = list(item.get("tests") or [])
        test_hits = [t for t in tests if (root / t).exists()]
        # 符号级证据：测试文件文本里必须出现该模块可辨识名（模块名/首导出）
        needles = list({Path(rel).stem, *(required[:2])}) if rel else []
        ref_hits = []
        for t in test_hits:
            try:
                text = (root / t).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(n and n in text for n in needles):
                ref_hits.append(t)
        smoke = _run_smoke(item, root)
        status = "integrated" if (ok and ref_hits) else (
            "unreferenced" if ok else "unavailable")
        if status == "integrated" and smoke["status"] in ("failed", "error"):
            status = "smoke_failed"
        rows.append({
            "key": item.get("key") or rel,
            "path": rel,
            "module": module_qual,
            "import_ok": ok,
            "missing_exports": missing,
            "error": err,
            "tests_declared": tests,
            "tests_existing": test_hits,
            "tests_referencing": ref_hits,
            "smoke": smoke,
            "status": status,
        })
    failed = [r for r in rows if r["status"] != "integrated"]
    criterion = {
        "id": "e_governance_integrated",
        "passed": bool(rows) and not failed,
        "detail": {"modules": len(rows),
                   "integrated": len(rows) - len(failed),
                   "failed": [{"key": r["key"], "status": r["status"],
                               "missing_exports": r["missing_exports"],
                               "error": r["error"],
                               "smoke": (r.get("smoke") or {}).get("status")}
                              for r in failed]},
    }
    return {"modules": rows, "criterion": criterion}


# ───────────────────── F：能力台账登记 ─────────────────────

LEDGER_REQUIRED_FIELDS = ("id", "title", "level", "evidence", "registered_by")


def capability_ledger_check(spec: Mapping[str, Any] | None,
                            repo_root: str | Path = ".") -> dict[str, Any]:
    """台账条目必须存在、字段齐、证据路径可解析、且等级在允许集合内。"""
    spec = spec or {}
    root = Path(repo_root)
    ledger_rel = str(spec.get("ledger_path") or "")
    allowed = {str(x) for x in (spec.get("allowed_levels") or [])}
    problems: list[str] = []
    entry: Mapping[str, Any] | None = None
    entries: list[Mapping[str, Any]] = []
    if not ledger_rel:
        problems.append("ledger_path_not_configured")
    else:
        p = root / ledger_rel
        if not p.exists():
            problems.append("ledger_file_missing")
        else:
            try:
                doc = load_json(p)
            except (OSError, ValueError) as exc:
                problems.append(f"ledger_unreadable:{exc}"[:200])
                doc = {}
            entries = list((doc or {}).get("entries") or []) if isinstance(doc, Mapping) else []
            target = str(spec.get("capability_id") or "")
            entry = next((e for e in entries if str(e.get("id")) == target), None)
            if entry is None:
                problems.append(f"entry_not_found:{target}")
            else:
                missing = [f for f in LEDGER_REQUIRED_FIELDS if not entry.get(f)]
                if missing:
                    problems.append("missing_fields:" + ",".join(missing))
                if allowed and str(entry.get("level")) not in allowed:
                    problems.append(f"level_not_allowed:{entry.get('level')}")
                unresolved = []
                for ev in entry.get("evidence") or []:
                    path = ev.get("path") if isinstance(ev, Mapping) else ev
                    if isinstance(path, str) and path and not (root / path).exists():
                        unresolved.append(path)
                if unresolved:
                    problems.append("evidence_unresolved:" + ",".join(unresolved[:5]))
    criterion = {
        "id": "f_capability_ledger_registered",
        "passed": not problems,
        "detail": {"ledger_path": ledger_rel,
                   "capability_id": spec.get("capability_id"),
                   "entries": len(entries), "problems": problems},
    }
    return {"entry": entry, "problems": problems, "criterion": criterion}


# ─────────────────────────── 汇总裁决 ───────────────────────────

def accept(snapshot: Mapping[str, Any],
           integration_report: Mapping[str, Any] | None = None,
           governance_spec: Mapping[str, Any] | None = None,
           ledger_spec: Mapping[str, Any] | None = None,
           cfg: Mapping[str, Any] | None = None,
           repo_root: str | Path = ".") -> dict[str, Any]:
    """战役验收：A~F 六判据合取，任一不过即 ``not_ready``（fail-closed）。"""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    plan_nodes = list(snapshot.get("nodes") or [])

    cen = census(plan_nodes, cfg)
    hf = honest_fail_audit(cen["failed_nodes"], cfg.get("failed_ledger"))
    ev = evidence_completeness(cen["nodes"], snapshot, cfg)
    d = integration_criterion(integration_report, cfg)
    gov = (governance_check(governance_spec, repo_root)
           if cfg.get("require_governance", True) else {"modules": [], "criterion": {
               "id": "e_governance_integrated", "passed": True, "detail": {"skipped": True}}})
    led = (capability_ledger_check(ledger_spec, repo_root)
           if cfg.get("require_ledger", True) else {"entry": None, "problems": [], "criterion": {
               "id": "f_capability_ledger_registered", "passed": True,
               "detail": {"skipped": True}}})

    criteria = [cen["criterion"], hf["criterion"], ev["criterion"], d,
                gov["criterion"], led["criterion"]]
    verdict = "pass" if all(bool(c["passed"]) for c in criteria) else "not_ready"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan": {"id": (snapshot.get("plan") or {}).get("id"),
                 "name": (snapshot.get("plan") or {}).get("name")
                 or (snapshot.get("plan") or {}).get("title")},
        "verdict": verdict,
        "criteria": criteria,
        "criterion_counts": {
            "total": len(criteria),
            "passed": sum(1 for c in criteria if c["passed"]),
            "failed": [c["id"] for c in criteria if not c["passed"]],
        },
        "census": {"counts": cen["counts"], "open_nodes": cen["open_nodes"],
                   "failed_nodes": cen["failed_nodes"], "nodes": cen["nodes"]},
        "honest_fail": {"documented": hf["documented"],
                        "undocumented": hf["undocumented"],
                        "ghost_entries": hf["ghost_entries"]},
        "evidence": {"covered": ev["covered"], "gaps": ev["gaps"]},
        "integration": ({"plan": integration_report.get("plan"),
                         "verdict": integration_report.get("verdict"),
                         "counts": integration_report.get("counts"),
                         "criteria": integration_report.get("criteria"),
                         "input_digest": integration_report.get("input_digest"),
                         "nodes": integration_report.get("nodes"),
                         "edges_summary": {k: v for k, v in
                                           (integration_report.get("edges") or {}).items()
                                           if k != "edges"}}
                        if integration_report else None),
        "governance": {"modules": gov["modules"]},
        "capability": {"entry": led["entry"], "problems": led["problems"]},
        "input_digest": digest({
            "census": cen["counts"],
            "nodes": [{k: n.get(k) for k in ("node_id", "ext_id", "status", "mission_id")}
                      for n in cen["nodes"]],
            "integration": (integration_report or {}).get("input_digest"),
            "governance": [(m.get("path"), m.get("status")) for m in gov["modules"]],
            "ledger": (led.get("entry") or {}).get("id"),
            "config": cfg,
        }),
    }
    return report


# ─────────────────────────── 渲染 ───────────────────────────

_MARK = {True: "PASS", False: "FAIL"}


def render_markdown(report: Mapping[str, Any]) -> str:
    plan = report.get("plan") or {}
    cen = report.get("census") or {}
    counts = cen.get("counts") or {}
    lines = [
        f"# 战役验收门报告 · plan {plan.get('id')} {plan.get('name') or ''}".rstrip(),
        "",
        f"- verdict: **{report.get('verdict')}**",
        f"- input_digest: `{report.get('input_digest')}`",
        f"- 节点普查: total={counts.get('total')} closed={counts.get('closed')} "
        f"failed={counts.get('failed')} open={counts.get('open')} unknown={counts.get('unknown')}",
        "",
        "## 六条判据",
        "",
        "| id | 结论 | 关键数字 |",
        "|---|---|---|",
    ]
    for c in report.get("criteria") or []:
        detail = json.dumps(c.get("detail") or {}, ensure_ascii=False, sort_keys=True)
        if len(detail) > 300:
            detail = detail[:300] + "…"
        lines.append(f"| `{c.get('id')}` | {_MARK[bool(c.get('passed'))]} | {detail} |")

    lines += ["", "## 节点状态普查", "",
              "| node | ext_id | type | status | bucket | mission |",
              "|---|---|---|---|---|---|"]
    for n in cen.get("nodes") or []:
        lines.append(f"| {n.get('node_id')} | {n.get('ext_id')} | {n.get('node_type')} | "
                     f"{n.get('status')} | {n.get('bucket')} | {n.get('mission_id')} |")

    if cen.get("open_nodes"):
        lines += ["", "## 未收口节点（阻塞项）", ""]
        for o in cen["open_nodes"]:
            lines.append(f"- `{o.get('ext_id')}` status=`{o.get('status')}` "
                         f"({o.get('bucket')})")

    gov = (report.get("governance") or {}).get("modules") or []
    if gov:
        lines += ["", "## 治理工具链在位性", "",
                  "| key | path | import | tests(referencing) | status |",
                  "|---|---|---|---|---|"]
        for m in gov:
            lines.append(f"| {m.get('key')} | `{m.get('path')}` | "
                         f"{'ok' if m.get('import_ok') else 'FAIL'} | "
                         f"{len(m.get('tests_referencing') or [])} | {m.get('status')} |")

    integ = report.get("integration")
    if integ:
        lines += ["", "## 跨域集成门（引用）", "",
                  f"- verdict: **{integ.get('verdict')}**",
                  f"- 分类计数: {json.dumps(integ.get('counts') or {}, ensure_ascii=False)}"]
        for c in integ.get("criteria") or []:
            lines.append(f"- `{c.get('id')}`: {_MARK[bool(c.get('passed'))]}")

    failed = report.get("criterion_counts", {}).get("failed") or []
    lines += ["", "## 裁决", ""]
    if report.get("verdict") == "pass":
        lines.append("六条判据全部通过 —— 战役可验收。")
    else:
        lines.append("判据未通过：" + "、".join(f"`{f}`" for f in failed)
                     + " —— 战役**不可验收**（honest not_ready，不得覆盖）。")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────── CLI ───────────────────────────

def _login(base_url: str, user: str, password: str) -> str | None:
    return civ._login(base_url, user, password)  # noqa: SLF001 - 复用同一只读登录


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="战役验收门（零领域语义）")
    ap.add_argument("--snapshot", help="已落盘的战役快照 JSON")
    ap.add_argument("--fetch-api", action="store_true", help="从 8010 只读拉取快照")
    ap.add_argument("--plan", type=int, help="plan_id（--fetch-api 时必填）")
    ap.add_argument("--base-url", default=os.environ.get(
        "TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"))
    ap.add_argument("--token", default=os.environ.get("TENSOR_TOKEN"))
    ap.add_argument("--user", default=os.environ.get("TENSOR_AGENT_USER"))
    ap.add_argument("--password", default=os.environ.get("TENSOR_AGENT_PASS"))
    ap.add_argument("--contract", help="集成合约 JSON（给了就跑集成门）")
    ap.add_argument("--config", help="门配置 JSON")
    ap.add_argument("--governance", help="治理工具链规格 JSON")
    ap.add_argument("--ledger-spec", help="能力台账登记规格 JSON")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--snapshot-out", help="快照落盘路径（审计留痕）")
    ap.add_argument("--integration-json-out", help="集成门报告落盘路径")
    ap.add_argument("--json-out")
    ap.add_argument("--md-out")
    args = ap.parse_args(argv)

    def _maybe(path: str | None) -> dict | None:
        if not path:
            return None
        try:
            return load_json(path) or {}
        except (OSError, ValueError) as exc:
            print(f"input error ({path}): {exc}", file=sys.stderr)
            raise SystemExit(3)

    cfg = _maybe(args.config) or {}
    contract = _maybe(args.contract)
    gov_spec = _maybe(args.governance)
    ledger_spec = _maybe(args.ledger_spec)

    try:
        if args.fetch_api:
            if not args.plan:
                print("--fetch-api requires --plan", file=sys.stderr)
                return 3
            token = args.token
            if not token and args.user and args.password:
                token = _login(args.base_url, args.user, args.password)
            snapshot = ig.build_snapshot_from_api(args.base_url, args.plan, token)
        elif args.snapshot:
            snapshot = load_json(args.snapshot)
        else:
            print("need --snapshot or --fetch-api", file=sys.stderr)
            return 3
    except Exception as exc:  # noqa: BLE001 - 输入/网络错误统一归 3
        print(f"snapshot error: {exc}", file=sys.stderr)
        return 3

    if args.snapshot_out:
        _write_json(args.snapshot_out, snapshot)

    integration_report = None
    if contract is not None:
        integration_report = civ.verify(snapshot, contract, cfg)
        if args.integration_json_out:
            _write_json(args.integration_json_out, integration_report)

    report = accept(snapshot, integration_report, gov_spec, ledger_spec, cfg, args.repo_root)
    if args.json_out:
        _write_json(args.json_out, report)
    md = render_markdown(report)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
    else:
        print(md)
    print(f"verdict={report['verdict']} "
          f"failed={report['criterion_counts']['failed']}", file=sys.stderr)
    return 0 if report["verdict"] == "pass" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())