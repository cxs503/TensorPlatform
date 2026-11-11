"""KPI-REPRO-95 合成数据生成器（测试模式）。

生成与真实数据分布相似的合成复现任务，用于在无数据库环境下
验证计量逻辑的正确性。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import List as t_list

from kpi_repro.measure import (
    MissionRow, KPIResult,
    ARTIFACT_STRONG, ARTIFACT_MEDIUM, ARTIFACT_NONE,
    ROOT_CAUSE_NO_ARTIFACT, ROOT_CAUSE_DISK_FULL,
    ROOT_CAUSE_BUDGET_EXHAUSTED, ROOT_CAUSE_LLM_ERROR,
    KPI_TARGET,
)


def generate_synthetic_data(
    n_total: int = 82,
    seed: int = 42,
) -> t_list[MissionRow]:
    """生成合成复现任务数据。

    分布设计（参照 m15121 报告的真实分布）：
    - succeeded: ~84% (69/82)
    - 其中 artifact strong: ~55%
    - 其中 artifact medium: ~20%
    - 其中 artifact none: ~25% (自称成功但工件不可证)
    - failed/stopped/revoked: ~16%
    """
    random.seed(seed)
    missions = []

    repos = [
        "TensorPlatform", "TensorDEM", "TensorFEM", "TensorPhysics",
        "TensorLBM", "ship-performance-platform", "TensorBEM",
        "TensorSPH", "TensorDG", "TensorCAD",
    ]

    base_date = datetime(2026, 11, 1, tzinfo=timezone.utc)

    for i in range(n_total):
        mission_id = 1000 + i
        created = base_date + timedelta(days=random.randint(0, 10), hours=random.randint(0, 23))

        # 状态分布
        r = random.random()
        if r < 0.84:
            status = "succeeded"
        elif r < 0.90:
            status = "failed"
        elif r < 0.95:
            status = "stopped"
        else:
            status = "revoked"

        # task_key
        if random.random() < 0.6:
            task_key = f"repro-m{mission_id}"
        elif random.random() < 0.5:
            task_key = f"audit-{mission_id}"
        else:
            task_key = f"audit2-{mission_id}"

        repo = random.choice(repos)

        # 工件分布（仅 succeeded）
        artifact_level = ARTIFACT_NONE
        worktree_exists = False
        report_exists = False
        head_commit = None
        result_data = {}

        if status == "succeeded":
            ar = random.random()
            if ar < 0.55:
                artifact_level = ARTIFACT_STRONG
                worktree_exists = random.random() < 0.8
                report_exists = random.random() < 0.8
                head_commit = f"abc{random.randint(1000, 9999)}def"
                result_data = {
                    "changed_files": [f"docs/reports/m{mission_id}_report.md"],
                    "head_commit": head_commit,
                }
            elif ar < 0.75:
                artifact_level = ARTIFACT_MEDIUM
                worktree_exists = random.random() < 0.3
                report_exists = random.random() < 0.6
                head_commit = f"abc{random.randint(1000, 9999)}def"
                result_data = {
                    "head_commit": head_commit,
                }
            else:
                artifact_level = ARTIFACT_NONE
                worktree_exists = random.random() < 0.2
                report_exists = random.random() < 0.3
                # 部分有空的 head_commit（旧 schema）
                if random.random() < 0.5:
                    result_data = {}
                else:
                    result_data = {"head_commit": None}

        # 失败根因
        root_cause = None
        if status == "succeeded" and artifact_level == ARTIFACT_NONE:
            rc_r = random.random()
            if rc_r < 0.38:
                root_cause = ROOT_CAUSE_NO_ARTIFACT
            elif rc_r < 0.71:
                root_cause = ROOT_CAUSE_DISK_FULL
            elif rc_r < 0.90:
                root_cause = ROOT_CAUSE_BUDGET_EXHAUSTED
            else:
                root_cause = ROOT_CAUSE_LLM_ERROR

        goal = f"[REPRO] Verify claims of parent mission #{mission_id - 500}"
        if random.random() < 0.3:
            goal = f"[交差审计] m{mission_id - 500} 复现验证"

        m = MissionRow(
            id=mission_id,
            task_key=task_key,
            goal=goal,
            repo=repo,
            status=status,
            runner="repro",
            model="repro-agent-default",
            created_at=created.isoformat(),
            finished_at=(created + timedelta(hours=random.randint(1, 48))).isoformat(),
            result=result_data,
            artifact_level=artifact_level,
            worktree_exists=worktree_exists,
            report_exists=report_exists,
            head_commit_reachable=(head_commit is not None and artifact_level == ARTIFACT_MEDIUM),
            root_cause=root_cause,
        )
        missions.append(m)

    return missions


def measure_synthetic(missions: t_list[MissionRow]) -> KPIResult:
    """对合成数据计算 KPI。"""
    from kpi_repro.measure import compute_kpi
    t0 = datetime.now(timezone.utc).isoformat()
    t0_db = t0
    return compute_kpi(missions, t0, t0_db)
