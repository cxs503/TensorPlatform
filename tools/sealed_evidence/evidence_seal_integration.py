#!/usr/bin/env python3
"""evidence_seal_integration —— 证据封存闸门与既有通道的集成钩子。

提供三个集成点：
  1. worktree_gc: 回收前检查证据封存闸门
  2. merge_governor: 合并前检查证据封存闸门
  3. repro_audit: KPI 回写（drift 归因结果）

设计原则：
  - 零领域语义（只用合成域名）
  - 幂等（同输入 → 同输出）
  - 失败降级（闸门不可用时不阻塞主流程）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ─────────────────────────── 1. worktree_gc 集成 ───────────────────────────

def check_worktree_evidence_seal(worktree_path: str) -> Dict[str, Any]:
    """检查 worktree 是否通过证据封存闸门。

    Args:
        worktree_path: worktree 路径

    Returns:
        {recyclable: bool, reason: str, gate_result: dict|None}
    """
    # 导入闸门模块（失败降级）
    try:
        from tools.sealed_evidence.recovery_gate import run_recovery_gate, GateVerdict
    except ImportError:
        return {
            "recyclable": True,  # 降级：闸门不可用时允许回收
            "reason": "evidence_seal_gate_not_available",
            "gate_result": None,
        }

    # 检查 sealed_evidence.json 是否存在
    seal_path = os.path.join(worktree_path, "sealed_evidence.json")
    if not os.path.isfile(seal_path):
        return {
            "recyclable": False,
            "reason": "sealed_evidence.json not found",
            "gate_result": None,
        }

    # 提取 mission_id
    try:
        with open(seal_path, "r", encoding="utf-8") as f:
            contract = json.load(f)
        mission_id = contract.get("mission_id", "unknown")
    except (OSError, json.JSONDecodeError, KeyError):
        return {
            "recyclable": False,
            "reason": "invalid sealed_evidence.json",
            "gate_result": None,
        }

    # 运行闸门
    try:
        result = run_recovery_gate(
            workdir=worktree_path,
            mission_id=mission_id,
            skip_replay=True,  # 回收时跳过重放（节省时间）
        )

        recyclable = result.verdict == GateVerdict.PASS
        return {
            "recyclable": recyclable,
            "reason": result.verdict.value if not recyclable else "pass",
            "gate_result": {
                "verdict": result.verdict.value,
                "reasons": result.reasons,
                "missing_artifacts": len(result.missing_artifacts),
                "sha256_mismatches": len(result.sha256_mismatches),
            },
        }
    except Exception as e:
        return {
            "recyclable": False,
            "reason": f"gate_error: {e}",
            "gate_result": None,
        }


# ─────────────────────────── 2. merge_governor 集成 ───────────────────────────

def check_merge_evidence_seal(worktree_path: str, mission_id: str) -> Dict[str, Any]:
    """检查 PR 是否通过证据封存闸门（合并前检查）。

    Args:
        worktree_path: worktree 路径
        mission_id: 任务 ID

    Returns:
        {mergeable: bool, reason: str, gate_result: dict|None}
    """
    # 导入闸门模块（失败降级）
    try:
        from tools.sealed_evidence.recovery_gate import run_recovery_gate, GateVerdict
    except ImportError:
        return {
            "mergeable": True,  # 降级：闸门不可用时允许合并
            "reason": "evidence_seal_gate_not_available",
            "gate_result": None,
        }

    # 检查 sealed_evidence.json 是否存在
    seal_path = os.path.join(worktree_path, "sealed_evidence.json")
    if not os.path.isfile(seal_path):
        return {
            "mergeable": False,
            "reason": "sealed_evidence.json not found",
            "gate_result": None,
        }

    # 运行闸门
    try:
        result = run_recovery_gate(
            workdir=worktree_path,
            mission_id=mission_id,
            skip_replay=False,  # 合并前执行重放
            replay_timeout_s=300,
        )

        mergeable = result.verdict == GateVerdict.PASS
        return {
            "mergeable": mergeable,
            "reason": result.verdict.value if not mergeable else "pass",
            "gate_result": {
                "verdict": result.verdict.value,
                "reasons": result.reasons,
                "missing_artifacts": len(result.missing_artifacts),
                "sha256_mismatches": len(result.sha256_mismatches),
                "replay_result": result.replay_result,
            },
        }
    except Exception as e:
        return {
            "mergeable": False,
            "reason": f"gate_error: {e}",
            "gate_result": None,
        }


# ─────────────────────────── 3. repro_audit 集成 ───────────────────────────

def writeback_drift_attribution(
    worktree_path: str,
    drift_entries: list[Dict[str, Any]],
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """将 drift 归因结果回写到 repro_audit KPI。

    Args:
        worktree_path: worktree 路径
        drift_entries: drift 条目列表
        output_path: 输出路径（可选，默认 artifacts/drift_cause.json）

    Returns:
        {success: bool, drift_causes: list, summary: dict}
    """
    # 导入归因器（失败降级）
    try:
        from tools.sealed_evidence.drift_attributor import attribute_drift_causes
    except ImportError:
        return {
            "success": False,
            "reason": "drift_attributor_not_available",
            "drift_causes": [],
            "summary": {},
        }

    # 默认输出路径
    if output_path is None:
        output_path = os.path.join(worktree_path, "artifacts", "drift_cause.json")

    # 执行归因
    try:
        causes = attribute_drift_causes(
            drift_entries=drift_entries,
            workdir=worktree_path,
            output_path=output_path,
        )

        # 统计
        from tools.sealed_evidence.drift_attributor import DRIFT_CATEGORIES
        category_counts = {}
        for c in DRIFT_CATEGORIES:
            category_counts[c] = sum(1 for r in causes if r["category"] == c)

        summary = {
            "total": len(causes),
            "by_category": category_counts,
            "unknown": sum(1 for r in causes if r["category"] not in DRIFT_CATEGORIES),
            "all_classified": all(r["category"] in DRIFT_CATEGORIES for r in causes),
        }

        return {
            "success": True,
            "drift_causes": causes,
            "summary": summary,
            "output_path": output_path,
        }
    except Exception as e:
        return {
            "success": False,
            "reason": f"attribution_error: {e}",
            "drift_causes": [],
            "summary": {},
        }


# ─────────────────────────── 4. 综合闸门 ───────────────────────────

def run_comprehensive_gate(
    worktree_path: str,
    mission_id: str,
    skip_replay: bool = False,
) -> Dict[str, Any]:
    """运行综合闸门（证据封存 + drift 归因）。

    Args:
        worktree_path: worktree 路径
        mission_id: 任务 ID
        skip_replay: 是否跳过重放

    Returns:
        {pass: bool, evidence_seal: dict, drift_attribution: dict|None}
    """
    # 1. 证据封存闸门
    seal_result = check_worktree_evidence_seal(worktree_path)

    # 2. 如果有 drift，执行归因
    drift_result = None
    if seal_result.get("gate_result") and seal_result["gate_result"].get("missing_artifacts", 0) > 0:
        # 从 debt_ledger 加载 drift
        debt_path = os.path.join(worktree_path, "artifacts", "debt_ledger.json")
        if os.path.isfile(debt_path):
            try:
                with open(debt_path, "r", encoding="utf-8") as f:
                    debt_ledger = json.load(f)
                drift_result = writeback_drift_attribution(
                    worktree_path=worktree_path,
                    drift_entries=debt_ledger,
                )
            except (OSError, json.JSONDecodeError):
                pass

    return {
        "pass": seal_result["recyclable"],
        "evidence_seal": seal_result,
        "drift_attribution": drift_result,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ─────────────────────────── CLI ───────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evidence Seal Integration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # check-worktree
    wt_parser = subparsers.add_parser("check-worktree", help="Check worktree evidence seal")
    wt_parser.add_argument("--worktree", required=True, help="Worktree path")
    wt_parser.set_defaults(func=lambda args: check_worktree_evidence_seal(args.worktree))

    # check-merge
    mg_parser = subparsers.add_parser("check-merge", help="Check merge evidence seal")
    mg_parser.add_argument("--worktree", required=True, help="Worktree path")
    mg_parser.add_argument("--mission", required=True, help="Mission ID")
    mg_parser.set_defaults(func=lambda args: check_merge_evidence_seal(args.worktree, args.mission))

    # writeback-drift
    wd_parser = subparsers.add_parser("writeback-drift", help="Writeback drift attribution")
    wd_parser.add_argument("--worktree", required=True, help="Worktree path")
    wd_parser.add_argument("--ledger", required=True, help="Drift ledger path")
    wd_parser.add_argument("--output", help="Output path")
    def _writeback(args):
        with open(args.ledger, "r") as f:
            entries = json.load(f)
        return writeback_drift_attribution(args.worktree, entries, args.output)
    wd_parser.set_defaults(func=_writeback)

    # comprehensive
    comp_parser = subparsers.add_parser("comprehensive", help="Run comprehensive gate")
    comp_parser.add_argument("--worktree", required=True, help="Worktree path")
    comp_parser.add_argument("--mission", required=True, help="Mission ID")
    comp_parser.add_argument("--skip-replay", action="store_true", help="Skip replay")
    comp_parser.set_defaults(func=lambda args: run_comprehensive_gate(args.worktree, args.mission, args.skip_replay))

    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result.get("pass", result.get("recyclable", result.get("mergeable", True))) else 1)


if __name__ == "__main__":
    main()
