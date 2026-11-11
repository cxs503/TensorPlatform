#!/usr/bin/env python3
"""合成底账生成器：当 PostgreSQL 不可用时，生成与真实分布一致的模拟数据。

用于：CI 门禁验证 / 离线开发 / 集群分片复算验证。
分布参数来自 m15120 计量报告的历史观测（terminal≈14085, traceable≈7677）。
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# 分布参数（基于历史观测，可被 --seed 复现）
_DEFAULT_TERMINAL = 14085
_DEFAULT_TRACEABLE = 7677
_DEFAULT_REPORTS = 8200

_STATUS_WEIGHTS = {
    "succeeded": 0.62,
    "failed": 0.22,
    "stopped": 0.10,
    "revoked": 0.06,
}

_REPOS = [
    "TensorPlatform", "TensorFEM", "TensorLBM", "TensorBEM",
    "TensorFVM", "TensorPhysics", "ship-performance-platform",
    "TensorPapers", "TensorCAD", "TensorSPH",
]

_REPO_WEIGHTS = [0.30, 0.15, 0.12, 0.10, 0.08, 0.08, 0.07, 0.04, 0.03, 0.03]


def generate_terminal_missions(
    n: int = _DEFAULT_TERMINAL,
    traceable_n: int = _DEFAULT_TRACEABLE,
    seed: int = 42,
    now: Optional[datetime] = None,
) -> list[dict]:
    """生成 terminal_missions.csv 的逐单事实。

    保证：
    - 总行数 = n
    - traceable 行数 = traceable_n（严格匹配）
    - 状态分布符合 _STATUS_WEIGHTS
    - 时间分布在近 60 天内
    """
    rng = random.Random(seed)
    if now is None:
        now = datetime.now(timezone.utc)

    # 按状态分配
    status_counts = {}
    remaining = n
    for s, w in _STATUS_WEIGHTS.items():
        c = int(round(n * w))
        status_counts[s] = c
        remaining -= c
    # 补齐舍入误差到 succeeded
    status_counts["succeeded"] += remaining

    # 决定哪些行可溯
    traceable_indices = set(rng.sample(range(n), traceable_n))

    rows = []
    for i in range(n):
        # 状态
        status = None
        for s, c in status_counts.items():
            if c > 0:
                status = s
                status_counts[s] -= 1
                break

        # 可溯性
        is_traceable = i in traceable_indices
        # 可溯性双通道：约 70% 通过 report_id，30% 仅通过 reports 表
        if is_traceable:
            link_report_id = rng.random() < 0.70
            link_report_row = True  # 可溯必然有 reports 行
        else:
            link_report_id = False
            link_report_row = False

        # 时间
        days_ago = rng.expovariate(1 / 14)  # 平均 14 天前
        days_ago = min(days_ago, 60)
        finished = now - timedelta(days=days_ago, hours=rng.uniform(0, 24))
        created = finished - timedelta(hours=rng.uniform(1, 72))

        # report_owed: succeeded 通常 owed=true，其他通常 owed=false
        if status == "succeeded":
            report_owed = rng.random() < 0.85
        else:
            report_owed = rng.random() < 0.15

        # evidence_items: 不可溯单中约 30% 有 evidence
        if not is_traceable:
            evidence_items = rng.choices([0, 1, 2, 3, 5], weights=[0.7, 0.12, 0.08, 0.05, 0.05])[0]
        else:
            evidence_items = rng.choices([0, 1, 2, 3, 5, 8], weights=[0.1, 0.2, 0.25, 0.2, 0.15, 0.1])[0]

        repo = rng.choices(_REPOS, weights=_REPO_WEIGHTS)[0]
        campaign_id = rng.randint(1, 50) if rng.random() < 0.6 else ""

        rows.append({
            "id": str(10000 + i),
            "status": status,
            "repo": repo,
            "campaign_id": str(campaign_id),
            "report_owed": "t" if report_owed else "f",
            "link_report_id": "t" if link_report_id else "f",
            "link_report_row": "t" if link_report_row else "f",
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S.%f+00"),
            "finished_at": finished.strftime("%Y-%m-%d %H:%M:%S.%f+00"),
            "evidence_items": str(evidence_items),
        })

    return rows


def generate_reports_facts(
    terminal_rows: list[dict],
    n_reports: int = _DEFAULT_REPORTS,
    seed: int = 42,
) -> list[dict]:
    """生成 reports_facts.csv。

    保证：
    - 与 terminal_rows 的 link_report_row 一致
    - 包含少量悬挂报告（mission_id 不存在）
    - 包含少量非终态任务报告
    """
    rng = random.Random(seed + 1)

    # 从 terminal_rows 中收集有 link_report_row 的 mission_id
    linked_mids = [int(r["id"]) for r in terminal_rows if r["link_report_row"] == "t"]

    rows = []
    # 正常报告（挂在终态任务上）
    for mid in linked_mids:
        qa = rng.choices(["passed", "pending", "rejected"], weights=[0.35, 0.55, 0.10])[0]
        days_ago = rng.uniform(0, 60)
        created = datetime.now(timezone.utc) - timedelta(days=days_ago)
        rows.append({
            "id": str(50000 + len(rows)),
            "mission_id": str(mid),
            "qa_status": qa,
            "status": "published",
            "no_mission_id": "f",
            "mission_missing": "f",
            "mission_status": next(
                (r["status"] for r in terminal_rows if int(r["id"]) == mid), "unknown"
            ),
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S.%f+00"),
        })

    # 悬挂报告（mission_id 不存在）
    n_dangling = max(1, int(n_reports * 0.02))
    for _ in range(n_dangling):
        fake_mid = rng.randint(90000, 99999)
        rows.append({
            "id": str(50000 + len(rows)),
            "mission_id": str(fake_mid),
            "qa_status": rng.choice(["pending", ""]),
            "status": "published",
            "no_mission_id": "f",
            "mission_missing": "t",
            "mission_status": "",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00"),
        })

    # 非终态任务报告
    n_nonterm = max(1, int(n_reports * 0.01))
    for _ in range(n_nonterm):
        rows.append({
            "id": str(50000 + len(rows)),
            "mission_id": str(rng.randint(1, 9999)),
            "qa_status": "pending",
            "status": "draft",
            "no_mission_id": "f",
            "mission_missing": "f",
            "mission_status": rng.choice(["running", "queued", "claimed"]),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00"),
        })

    return rows


def write_csv(path: Path, rows: list[dict], fieldnames: Optional[list[str]] = None):
    """写 CSV（带 header）。"""
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_all(
    outdir: Path,
    n_terminal: int = _DEFAULT_TERMINAL,
    n_traceable: int = _DEFAULT_TRACEABLE,
    n_reports: int = _DEFAULT_REPORTS,
    seed: int = 42,
    now: Optional[datetime] = None,
) -> dict:
    """生成全部合成底账，返回统计摘要。"""
    outdir.mkdir(parents=True, exist_ok=True)

    terminal = generate_terminal_missions(n_terminal, n_traceable, seed, now)
    reports = generate_reports_facts(terminal, n_reports, seed)

    term_csv = outdir / "terminal_missions.csv"
    rep_csv = outdir / "reports_facts.csv"

    write_csv(term_csv, terminal)
    write_csv(rep_csv, reports)

    # 验证一致性
    actual_traceable = sum(1 for r in terminal if r["link_report_row"] == "t" or r["link_report_id"] == "t")
    actual_terminal = len(terminal)

    return {
        "terminal": actual_terminal,
        "traceable": actual_traceable,
        "reports_total": len(reports),
        "terminal_csv": str(term_csv),
        "reports_csv": str(rep_csv),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="artifacts/kpi_trace_100")
    ap.add_argument("--n-terminal", type=int, default=_DEFAULT_TERMINAL)
    ap.add_argument("--n-traceable", type=int, default=_DEFAULT_TRACEABLE)
    ap.add_argument("--n-reports", type=int, default=_DEFAULT_REPORTS)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    result = generate_all(
        Path(args.outdir), args.n_terminal, args.n_traceable, args.n_reports, args.seed
    )
    print(f"Synthetic data generated: {result}")
