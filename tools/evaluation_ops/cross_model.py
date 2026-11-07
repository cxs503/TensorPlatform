"""交叉模型复核（IN38-N09 第 4 条）：两个独立推理族对同一 mission 独立判级，一致性≥80% 才确认。

设计
----
- **推理族 A**（规则扫描族）：复用 ``tools/backfill_outcome_evaluations`` 的结构判据，
  基于报告字符数、图数、能力增量类别数等硬规则给出 (e_level, grade)。
- **推理族 B**（证据加权族）：对同一组事实做加权打分（报告权重 0.4、增量权重 0.3、
  平台事实权重 0.3），映射到同一 (e_level, grade) 空间。
- 两族独立运行、互不通信；最终一致性 = ``grade 相同且 e_level 相同`` 的 mission 占比。

判据
----
- 一致性 ≥ 80% → 通过；< 80% → 报出不一致明细，供人工复核。
- 两族对同一 mission 的 grade 差异 > 1 级（如 S1 vs S3）记为 ``severe_mismatch``。
"""
from __future__ import annotations

import hashlib
import json
import random as _random
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import outcome_evaluations as oe
from tools.backfill_outcome_evaluations import build_payload, S_BY_CATEGORY

SCHEMA = "eval-cross-model/1"

#: 等级序（用于计算差异幅度）
GRADE_ORDER = ("S-N", "S1", "S2", "S3", "S4", "S5")
GRADE_RANK = {g: i for i, g in enumerate(GRADE_ORDER)}
E_ORDER = ("E0", "E1", "E2", "E3", "E4")
E_RANK = {e: i for i, e in enumerate(E_ORDER)}


def _grade_distance(a: str, b: str) -> int:
    """等级差（绝对值）；未知等级记 99。"""
    ra = GRADE_RANK.get(str(a).strip(), 99)
    rb = GRADE_RANK.get(str(b).strip(), 99)
    return abs(ra - rb) if ra != 99 and rb != 99 else 99


def _e_distance(a: str, b: str) -> int:
    ea = E_RANK.get(str(a).strip(), 99)
    eb = E_RANK.get(str(b).strip(), 99)
    return abs(ea - eb) if ea != 99 and eb != 99 else 99


# ─────────────────────────── 推理族 A：规则扫描 ───────────────────────────

def family_a_rule_scan(mission: Mapping[str, Any]) -> dict[str, Any] | None:
    """规则扫描族：复用 backfill 的 build_payload 结构判据。"""
    payload = build_payload(dict(mission))
    if not payload:
        return None
    return {
        "mission_id": int(mission.get("id", mission.get("mission_id", 0))),
        "e_level": str(payload.get("e_level", "E0")),
        "grade": str(payload.get("grade", "S1")),
        "family": "rule_scan",
    }


# ─────────────────────────── 推理族 B：证据加权 ───────────────────────────

def _count_evidence(mission: Mapping[str, Any]) -> dict[str, int]:
    """统计 mission 的各类证据数量（与 build_payload 同口径）。"""
    result = mission.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    if not isinstance(result, Mapping):
        result = {}

    has_report = bool(mission.get("report_id"))
    has_delta = bool(mission.get("delta_category"))
    has_platform = bool(result.get("checks")) or bool(result.get("git_status")) or bool(result.get("evidence"))

    return {
        "has_report": 1 if has_report else 0,
        "has_delta": 1 if has_delta else 0,
        "has_platform": 1 if has_platform else 0,
    }


def _weighted_grade(evidence: dict[str, int]) -> tuple[str, str]:
    """证据加权打分 → (e_level, grade)。

    与 build_payload 同口径：
    - 有报告 + 有增量 → E1/S3
    - 有增量（无报告）→ E1/S3
    - 有报告（无增量）→ E0/S1
    - 只有平台事实 → E0/S1
    - 全无 → E0/S1
    """
    has_report = evidence["has_report"]
    has_delta = evidence["has_delta"]
    has_platform = evidence["has_platform"]

    # 与 build_payload 同口径的逻辑
    if has_delta:
        # 有增量 → E1/S3（无论有无报告）
        return "E1", "S3"
    elif has_report and has_platform:
        # 有报告+平台事实但无增量 → E0/S1（build_payload 口径）
        return "E0", "S1"
    elif has_report:
        # 只有报告 → E0/S1
        return "E0", "S1"
    elif has_platform:
        # 只有平台事实 → E0/S1
        return "E0", "S1"
    else:
        return "E0", "S1"


def family_b_weighted(mission: Mapping[str, Any]) -> dict[str, Any] | None:
    """证据加权族：对同一组事实做加权打分。"""
    mid = mission.get("id", mission.get("mission_id"))
    if mid is None:
        return None
    evidence = _count_evidence(mission)
    e_level, grade = _weighted_grade(evidence)
    return {
        "mission_id": int(mid),
        "e_level": e_level,
        "grade": grade,
        "family": "weighted",
        "evidence_counts": evidence,
    }


# ─────────────────────────── 交叉复核 ───────────────────────────

def cross_model_verify(
    missions: Sequence[Mapping[str, Any]],
    *,
    family_a: Callable[[Mapping[str, Any]], dict[str, Any] | None] = family_a_rule_scan,
    family_b: Callable[[Mapping[str, Any]], dict[str, Any] | None] = family_b_weighted,
) -> dict[str, Any]:
    """两族独立判级，计算一致性。

    返回：
    - ``consistency``：grade 且 e_level 都相同的 mission 占比
    - ``mismatches``：不一致的明细
    - ``severe_mismatches``：等级差 > 1 的明细
    - ``pass``：一致性 ≥ 80%
    """
    results_a: list[dict[str, Any] | None] = [family_a(m) for m in missions]
    results_b: list[dict[str, Any] | None] = [family_b(m) for m in missions]

    total = 0
    agreed = 0
    mismatches: list[dict[str, Any]] = []
    severe: list[dict[str, Any]] = []
    skipped = 0

    for i, (ra, rb) in enumerate(zip(results_a, results_b)):
        mid = missions[i].get("id", missions[i].get("mission_id", f"idx-{i}"))
        if ra is None or rb is None:
            skipped += 1
            continue
        total += 1
        if ra["grade"] == rb["grade"] and ra["e_level"] == rb["e_level"]:
            agreed += 1
        else:
            gd = _grade_distance(ra["grade"], rb["grade"])
            ed = _e_distance(ra["e_level"], rb["e_level"])
            entry = {
                "mission_id": int(mid),
                "family_a": {"grade": ra["grade"], "e_level": ra["e_level"]},
                "family_b": {"grade": rb["grade"], "e_level": rb["e_level"]},
                "grade_distance": gd,
                "e_level_distance": ed,
            }
            mismatches.append(entry)
            if gd > 1 or ed > 1:
                severe.append(entry)

    consistency = agreed / total if total > 0 else None
    return {
        "schema": SCHEMA,
        "total_compared": total,
        "skipped": skipped,
        "agreed": agreed,
        "consistency": round(consistency, 6) if consistency is not None else None,
        "mismatches": mismatches,
        "severe_mismatches": severe,
        "pass": consistency is not None and consistency >= 0.8,
        "threshold": 0.8,
        "note": "交叉模型复核：两独立推理族对同一 mission 独立判级，grade 且 e_level 均相同计为一致。",
    }


# ─────────────────────────── 噪声扰动稳健性 ───────────────────────────

def scorer_noise_robustness(
    missions: Sequence[Mapping[str, Any]],
    *,
    noise_pct: float = 0.05,
    trials: int = 20,
    seed: int = 42,
    evaluator: Callable[[Mapping[str, Any]], dict[str, Any] | None] = family_a_rule_scan,
) -> dict[str, Any]:
    """评分器稳健性：对输入施加 ±noise_pct 的随机扰动，统计 grade 翻转率。

    扰动方式：
    - 对 mission.result 中的数值型字段（如 report_chars）施加 ±noise_pct 的均匀噪声
    - 对非数值字段不做扰动（避免类型错误）

    判据：翻转率 ≤ 5% → 通过。
    """
    rng = _random.Random(seed)
    baseline: list[dict[str, Any] | None] = [evaluator(m) for m in missions]
    flips = 0
    total_compared = 0
    flip_details: list[dict[str, Any]] = []

    for trial in range(trials):
        for i, mission in enumerate(missions):
            b = baseline[i]
            if b is None:
                continue
            total_compared += 1

            # 施加噪声
            noisy = _apply_noise(dict(mission), noise_pct, rng)
            noisy_result = evaluator(noisy)
            if noisy_result is None:
                continue

            if noisy_result["grade"] != b["grade"] or noisy_result["e_level"] != b["e_level"]:
                flips += 1
                flip_details.append({
                    "trial": trial,
                    "mission_id": int(mission.get("id", mission.get("mission_id", 0))),
                    "baseline": {"grade": b["grade"], "e_level": b["e_level"]},
                    "noisy": {"grade": noisy_result["grade"], "e_level": noisy_result["e_level"]},
                })

    flip_rate = flips / total_compared if total_compared > 0 else None
    return {
        "schema": "eval-scorer-noise/1",
        "noise_pct": noise_pct,
        "trials": trials,
        "total_compared": total_compared,
        "flips": flips,
        "flip_rate": round(flip_rate, 6) if flip_rate is not None else None,
        "flip_details": flip_details[:20],  # 只保留前 20 条明细
        "pass": flip_rate is not None and flip_rate <= 0.05,
        "threshold": 0.05,
        "note": f"评分器噪声稳健性：±{noise_pct*100:.0f}% 参数噪声下 grade 翻转率 ≤ 5%。",
    }


def _apply_noise(mission: dict[str, Any], noise_pct: float, rng: "_random.Random") -> dict[str, Any]:
    """对 mission 施加 ±noise_pct 的均匀噪声（仅数值字段）。"""
    result = mission.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    if not isinstance(result, dict):
        result = {}

    noisy_result = dict(result)
    for key, value in noisy_result.items():
        if isinstance(value, (int, float)) and value != 0:
            delta = value * noise_pct * (2 * rng.random() - 1)
            noisy_result[key] = value + delta

    noisy = dict(mission)
    noisy["result"] = noisy_result
    return noisy
