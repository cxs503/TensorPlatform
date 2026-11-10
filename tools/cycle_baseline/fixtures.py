"""cycle_baseline.fixtures —— 带标签的合成夹具（检测器判分的唯一合法证据面）。

为什么必须是合成
----------------
真实语料里没有人工标注的"饥饿/优先级反转"标签，任何在真实数据上宣称的 F1 都是自欺。
所以判据 (b) 的 F1 只在**知道 ground truth 的合成夹具**上计算，并在报告里显式声明
"F1 来自合成夹具，真实语料 F1 未知（无标签）"。

夹具设计要点（让判分不平凡）
--------------------------
* **种植正例**：极端超阈等待且非成功终态；计划序与领取序倒置。
* **诱饵负例**：等待超阈但成功的（不算饥饿）；等待接近阈但未超的失败单；
  计划间隔小于判定阈（gap）的乱序（不算反转）。
* **可复现**：固定 seed，纯 random.Random，跨机器同结果。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from . import spec

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def starvation_fixture(n_normal: int = 400, n_planted: int = 12,
                       n_decoy_succeeded_long: int = 8, n_decoy_failed_short: int = 12,
                       n_borderline: int = 10,
                       seed: int = spec.DEFAULT_SEED
                       ) -> tuple[list[dict], dict[Any, Optional[bool]]]:
    """排队时长夹具。

    * 正例（True）：超阈排队 **且** 非成功终态 —— 由生成器注入极端等待（30–60×中位）。
    * 负例（False）：超阈但成功（等很久但完成了）、未超阈的快速失败。
    * **歧义（None）**：贴着阈值的边界样本（0.97–1.03 × 参照 p95）—— 判分时整条剔除，
      避免把边界样本算成假阳/假阴来给 F1 注水。
    """
    rng = random.Random(seed)
    plan: list[tuple[float, str, Optional[bool]]] = []
    for _ in range(n_normal):
        plan.append((float(rng.lognormvariate(0, 0.6)) * 900.0,
                     rng.choice(["succeeded", "succeeded", "succeeded", "failed"]), False))
    for _ in range(n_planted):
        plan.append((float(rng.uniform(30, 60)) * 900.0,
                     rng.choice(["failed", "stopped", "revoked"]), True))
    for _ in range(n_decoy_succeeded_long):
        plan.append((float(rng.uniform(30, 60)) * 900.0, "succeeded", False))
    for _ in range(n_decoy_failed_short):
        plan.append((float(rng.uniform(0.5, 2.0)) * 900.0,
                     rng.choice(["failed", "stopped"]), False))
    # 参照阈值：先在无边界样本上取 p95，再把边界样本贴上去（保持"歧义"语义）
    ref = sorted(w for w, _, _ in plan)[int(round(0.95 * (len(plan) - 1)))]
    for _ in range(n_borderline):
        plan.append((ref * rng.uniform(0.97, 1.03),
                     rng.choice(["failed", "stopped"]), None))

    records: list[dict] = []
    labels: dict[Any, Optional[bool]] = {}
    mid = 900000
    for wait_s, status, starved in plan:
        created = T0 + timedelta(seconds=rng.uniform(0, 50000))
        claimed = created + timedelta(seconds=wait_s)
        finished = claimed + timedelta(seconds=rng.uniform(300, 2400))
        records.append({
            "mission_id": mid, "repo": rng.choice(["r0", "r1", "r2", "r3"]),
            "status": status, "worker": f"w{rng.randrange(6)}",
            "created_at": _iso(created), "claimed_at": _iso(claimed),
            "finished_at": _iso(finished),
            "merge_at": None, "report_at": None, "plan_id": None, "planned_start": None,
        })
        labels[mid] = starved
        mid += 1
    return records, labels


def inversion_fixture(n_plans: int = 24, nodes_per_plan: int = 5,
                      n_inverted: int = 14, n_gap_decoys: int = 6,
                      seed: int = spec.DEFAULT_SEED) -> tuple[list[dict], dict[Any, bool]]:
    """优先级反转夹具：正例=计划序在前却被后来者抢先领取；诱饵=计划间隔 < gap 的乱序。"""
    rng = random.Random(seed)
    records: list[dict] = []
    labels: dict[Any, bool] = {}
    mid = 950000
    plan_ids = list(range(500, 500 + n_plans + n_gap_decoys))
    inverted_plans = set(rng.sample(plan_ids[:n_plans], n_inverted))
    for plan_id in plan_ids:
        base = T0 + timedelta(seconds=rng.uniform(0, 40000))
        is_gap_decoy = plan_id not in plan_ids[:n_plans]
        spacing = 10.0 if is_gap_decoy else 3600.0
        claim_times = [base + timedelta(seconds=spacing * k + rng.uniform(0, 60))
                       for k in range(nodes_per_plan)]
        victim_k = None
        if plan_id in inverted_plans:
            victim_k = rng.randrange(0, nodes_per_plan - 1)
            claim_times[victim_k], claim_times[victim_k + 1] = \
                claim_times[victim_k + 1], claim_times[victim_k]
        elif is_gap_decoy:
            claim_times.reverse()
        for k in range(nodes_per_plan):
            created = claim_times[k] - timedelta(seconds=rng.uniform(100, 900))
            records.append({
                "mission_id": mid, "repo": rng.choice(["r0", "r1"]),
                "status": "succeeded", "worker": f"w{rng.randrange(4)}",
                "created_at": _iso(created), "claimed_at": _iso(claim_times[k]),
                "finished_at": _iso(claim_times[k] + timedelta(seconds=1200)),
                "merge_at": None, "report_at": None, "plan_id": plan_id,
                "planned_start": _iso(base + timedelta(seconds=spacing * k)),
                "node": f"n{k}",
            })
            labels[mid] = (victim_k is not None and k == victim_k)
            mid += 1
    return records, labels


def labeled_traces(seed: int = spec.DEFAULT_SEED) -> dict[str, Any]:
    starved, s_labels = starvation_fixture(seed=seed)
    inverted, i_labels = inversion_fixture(seed=seed)
    return {"starvation": (starved, s_labels), "inversion": (inverted, i_labels)}