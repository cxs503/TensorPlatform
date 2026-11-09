"""repro_audit.run_audit —— 完整审计执行器（零领域语义）。

在 :mod:`cli.audit_node` 基础上重构，提供更结构化的审计流程：
  1. 扫描候选（scan_select）
  2. 探针预检（probe_runner）
  3. 完整复证（core + cli）
  4. 结果聚合（KPI 计算）

设计约束：
  * **零领域语义**：仓名、学科词全部来自节点元数据。
  * **幂等**：同输入同输出（sort_keys=True，无 wall-clock 排序键）。
  * **可中断**：支持断点续跑（通过 checkpoint 文件）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from . import canonical, core, probe_runner, scan_select


DEFAULT_WORK_ROOT = os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work")
PER_NODE_TIMEOUT = 600
PYTEST_TIMEOUT = 300
MAX_TEST_FILES = 30


def build_audit_plan(nodes: list[dict[str, Any]], n: int, strategy: str = "stratified",
                     seed: str = "repro-audit", work_root: str = DEFAULT_WORK_ROOT,
                     files_by_mission: dict[int, list[str]] | None = None
                     ) -> dict[str, Any]:
    """构建审计计划：扫描 → 选择 → 返回计划对象。

    计划对象包含：candidates（全量候选）、selected（选中集合）、coverage（覆盖度）。
    """
    candidates = scan_select.scan_candidates(nodes, files_by_mission)
    selected = scan_select.select_with_strategy(candidates, n, strategy=strategy, seed=seed)
    coverage = scan_select.compute_coverage(selected, candidates)

    return {
        "candidates_count": len(candidates),
        "selected_count": len(selected),
        "strategy": strategy,
        "seed": seed,
        "coverage": coverage,
        "selected": [{"mission_id": c["mission_id"], "repo": c["repo"],
                      "n_criteria": c["n_criteria"], "n_test_files": c["n_test_files"]}
                     for c in selected],
    }


def run_probe_phase(selected: list[dict[str, Any]], work_root: str = DEFAULT_WORK_ROOT
                    ) -> list[dict[str, Any]]:
    """探针预检阶段：对每个选中节点执行轻量级检查。

    返回 ``[{mission_id, probe_summary, passed_all_probes}]``。
    """
    results = []
    for cand in selected:
        node = cand["node"]
        mid = cand["mission_id"]
        workdir = os.path.join(work_root, f"m{mid}")
        if not os.path.isdir(workdir):
            results.append({
                "mission_id": mid,
                "probe_summary": {"total": 0, "passed": 0, "pass_rate": None},
                "passed_all_probes": False,
                "error": "workdir_not_found",
            })
            continue

        try:
            probes = probe_runner.run_all_probes(node, workdir)
            summary = probe_runner.summarize_probes(probes)
            results.append({
                "mission_id": mid,
                "probe_summary": summary,
                "passed_all_probes": summary["failed_count"] == 0,
            })
        except Exception as exc:
            results.append({
                "mission_id": mid,
                "probe_summary": {"total": 0, "passed": 0, "pass_rate": None},
                "passed_all_probes": False,
                "error": str(exc)[:200],
            })

    return results


def run_full_audit(selected: list[dict[str, Any]], work_root: str = DEFAULT_WORK_ROOT,
                   run: int = 1) -> list[dict[str, Any]]:
    """完整复证阶段：对每个选中节点执行 git archive + pytest + 数值比对。

    复用 cli.audit_node 的核心逻辑，但提供更结构化的输入/输出。
    """
    # 延迟导入避免循环依赖
    from . import cli

    results = []
    for cand in selected:
        mid = cand["mission_id"]
        workdir = os.path.join(work_root, f"m{mid}")
        if not os.path.isdir(workdir):
            results.append({
                "mission_id": mid,
                "verdict": "blocked",
                "blocked_reason": "workdir_not_found",
            })
            continue

        # 构造 cli 期望的 cand 格式
        cli_cand = {
            "node": cand["node"],
            "workdir": workdir,
            "repo": cand.get("repo"),
        }

        try:
            record = cli.audit_node(cli_cand, run=run)
            results.append(record)
        except Exception as exc:
            results.append({
                "mission_id": mid,
                "verdict": "blocked",
                "blocked_reason": f"audit_exception: {str(exc)[:200]}",
            })

    return results


def aggregate_kpi(audit_results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合审计结果为 KPI：repro_rate、blocked_share、计数。"""
    items = []
    for r in audit_results:
        mid = r.get("mission_id")
        verdict = r.get("verdict", "blocked")
        items.append({"mission_id": mid, "verdict": verdict})

    kpi = core.kpi_from_items(items)
    return {
        "total": len(items),
        "counts": kpi["counts"],
        "denominator": kpi["denominator"],
        "repro_rate": kpi["repro_rate"],
        "blocked_share": kpi["blocked_share"],
    }


def run_audit_pipeline(nodes: list[dict[str, Any]], n: int, strategy: str = "stratified",
                       seed: str = "repro-audit", work_root: str = DEFAULT_WORK_ROOT,
                       run_probes: bool = True, run_full: bool = True, run: int = 1
                       ) -> dict[str, Any]:
    """完整审计流水线：计划 → 探针 → 复证 → 聚合。

    返回 ``{plan, probe_results, audit_results, kpi, timestamp}``。
    """
    started = time.time()

    # 1. 构建计划
    plan = build_audit_plan(nodes, n, strategy=strategy, seed=seed, work_root=work_root)
    selected = scan_select.scan_candidates(nodes)
    selected_mids = {s["mission_id"] for s in plan["selected"]}
    selected_full = [c for c in selected if c["mission_id"] in selected_mids]

    # 2. 探针预检
    probe_results = []
    if run_probes:
        probe_results = run_probe_phase(selected_full, work_root=work_root)

    # 3. 完整复证
    audit_results = []
    if run_full:
        audit_results = run_full_audit(selected_full, work_root=work_root, run=run)

    # 4. 聚合 KPI
    kpi = aggregate_kpi(audit_results) if audit_results else None

    return {
        "plan": plan,
        "probe_results": probe_results,
        "audit_results": audit_results,
        "kpi": kpi,
        "duration_sec": round(time.time() - started, 2),
    }


def save_checkpoint(pipeline_result: dict[str, Any], path: str) -> None:
    """保存审计流水线的中间结果（断点续跑用）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pipeline_result, fh, indent=2, sort_keys=True, default=str)


def load_checkpoint(path: str) -> dict[str, Any] | None:
    """加载审计流水线的中间结果。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
