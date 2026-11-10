"""
Worktree Governance API

提供工作树容量治理的 REST API 接口。
"""

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/worktree-governance", tags=["工作树容量治理"])


class GovernanceRequest(BaseModel):
    worktree_root: str = "."
    retention_days: int = 7
    sample_ratio: float = 0.1
    model_type: str = "linear"


class GovernanceResponse(BaseModel):
    status: str
    reachability: Optional[dict] = None
    reclaim: Optional[dict] = None
    recovery: Optional[dict] = None
    disk_prediction: Optional[dict] = None
    error: Optional[str] = None


@router.get("/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "module": "worktree_governor"}


@router.post("/scan", response_model=GovernanceResponse)
def scan_reachability(req: GovernanceRequest = None):
    """
    扫描工作树可达性。

    构建 git ref → blob hash → file path 可达性图，
    识别所有活跃分支/标签引用的文件和不可达文件。
    """
    if req is None:
        req = GovernanceRequest()

    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(req.worktree_root, req.retention_days)
        report = governor.scan_reachability()
        return GovernanceResponse(
            status="success",
            reachability=report.to_dict(),
        )
    except Exception as e:
        return GovernanceResponse(status="error", error=str(e))


@router.post("/reclaim", response_model=GovernanceResponse)
def reclaim_unreachable(req: GovernanceRequest = None):
    """
    安全回收不可达文件。

    将不可达文件移至 .recycle/ 目录（软删除），
    保留期后执行硬删除。
    """
    if req is None:
        req = GovernanceRequest()

    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(req.worktree_root, req.retention_days)

        # 先扫描
        reach_report = governor.scan_reachability()
        unreachable = set(reach_report.unreachable_paths)

        # 执行回收
        reclaim_report = governor.reclaim_unreachable(unreachable)

        return GovernanceResponse(
            status="success",
            reachability=reach_report.to_dict(),
            reclaim={
                "files_moved": reclaim_report.files_moved,
                "bytes_freed": reclaim_report.bytes_freed,
                "blocked_paths": reclaim_report.blocked_paths,
                "timestamp": reclaim_report.timestamp,
            },
        )
    except Exception as e:
        return GovernanceResponse(status="error", error=str(e))


@router.post("/drill", response_model=GovernanceResponse)
def run_recovery_drill(req: GovernanceRequest = None):
    """
    执行恢复演练。

    备份→删除→恢复→行级 checksum 校验，
    验证 RPO/RTO 指标。
    """
    if req is None:
        req = GovernanceRequest()

    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(req.worktree_root, req.retention_days)
        recovery_report = governor.run_recovery_drill(req.sample_ratio)

        return GovernanceResponse(
            status="success",
            recovery={
                "total_files": recovery_report.total_files,
                "checksum_matches": recovery_report.checksum_matches,
                "checksum_mismatches": recovery_report.checksum_mismatches,
                "rpo_seconds": round(recovery_report.rpo_seconds, 3),
                "rto_seconds": round(recovery_report.rto_seconds, 3),
                "passed": recovery_report.passed,
                "timestamp": recovery_report.timestamp,
            },
        )
    except Exception as e:
        return GovernanceResponse(status="error", error=str(e))


@router.post("/predict", response_model=GovernanceResponse)
def predict_disk(req: GovernanceRequest = None):
    """
    磁盘耗尽预测。

    基于历史增长率建立线性/指数预测模型，
    提前预警磁盘耗尽风险。
    """
    if req is None:
        req = GovernanceRequest()

    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(req.worktree_root, req.retention_days)
        disk_pred = governor.predict_disk(req.model_type)

        return GovernanceResponse(
            status="success",
            disk_prediction={
                "current_usage_bytes": disk_pred.current_usage_bytes,
                "total_disk_bytes": disk_pred.total_disk_bytes,
                "growth_rate_bytes_per_day": round(disk_pred.growth_rate_bytes_per_day, 2),
                "model_type": disk_pred.model_type,
                "days_until_full": round(disk_pred.days_until_full, 1) if disk_pred.days_until_full != float("inf") else "inf",
                "predicted_full_date": disk_pred.predicted_full_date,
                "confidence": disk_pred.confidence,
                "history_points": disk_pred.history_points,
            },
        )
    except Exception as e:
        return GovernanceResponse(status="error", error=str(e))


@router.post("/full-cycle", response_model=GovernanceResponse)
def full_governance_cycle(req: GovernanceRequest = None):
    """
    执行完整治理周期。

    包含: 可达性扫描 → 安全回收 → 恢复演练 → 磁盘预测
    """
    if req is None:
        req = GovernanceRequest()

    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(req.worktree_root, req.retention_days)
        results = governor.full_governance_cycle()

        return GovernanceResponse(
            status="success",
            reachability=results.get("reachability"),
            reclaim=results.get("reclaim"),
            recovery=results.get("recovery"),
            disk_prediction=results.get("disk_prediction"),
        )
    except Exception as e:
        return GovernanceResponse(status="error", error=str(e))


@router.get("/status")
def get_governance_status(root: str = Query(".", description="Worktree root")):
    """获取当前治理状态摘要"""
    try:
        from tools.worktree_governor import WorktreeGovernor
        governor = WorktreeGovernor(root)

        # 快速扫描
        reach = governor.scan_reachability()
        disk = governor.predict_disk()

        return {
            "worktree_root": root,
            "total_files": reach.total_files,
            "reachable_files": reach.reachable_files,
            "unreachable_files": reach.unreachable_files,
            "unreachable_bytes": reach.unreachable_bytes,
            "disk_usage_bytes": disk.current_usage_bytes,
            "days_until_full": round(disk.days_until_full, 1) if disk.days_until_full != float("inf") else "inf",
            "prediction_confidence": disk.confidence,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
