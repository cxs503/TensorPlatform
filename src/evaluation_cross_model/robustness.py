"""评分器稳健性：同一案例多次独立评价的判级翻转率（IN47-N08 判据 3）。

评价级噪声模型（只加在「评价环节」，不加在物理上）
--------------------------------------------------
1. **参考值计量不确定度**：参考值按 ``u_ref%``（``corpus.REF_UNCERTAINTY_PCT``，
   由参考等级决定，解析式 0.1% / 回归 1.5% / 公布值 2%）正态抖动 —— 这是
   实验/回归参考本身就有的不确定度。
2. **报送精度**：平台预报值按 3 位有效数字报送（``round_sig``），再叠加
   ``0.2%`` 的读数噪声（数值报送层级）。
3. **评价协议复测**：每次「评价」可对同一案例重复测量 ``k`` 次并取中位数
   （``k_measurements``，默认 3）。这是评价系统的方差抑制手段，**是否采用由
   协议声明**；报告同时给出 ``k=1`` 与 ``k=3`` 的翻转率，不做选择性披露。

翻转率定义：``flips / (n_cases × n_evals)``，判级（证据词汇）发生变化即计一次。
阈值 ``MAX_FLIP_RATE = 0.05``（任务书），案例数下限 ``MIN_CASES = 20``，
评价次数 ``N_EVALS = 5``。
"""
from __future__ import annotations

import copy
import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence

from .corpus import REF_UNCERTAINTY_PCT, CaseRecord
from .families import agree, family_a, family_b

SCHEMA = "in47-n08-scorer-robustness/1"
MAX_FLIP_RATE = 0.05
MIN_CASES = 20
N_EVALS = 5
PRED_NOISE_PCT = 0.2
SIG_DIGITS = 3


def round_sig(x: Optional[float], digits: int = SIG_DIGITS) -> Optional[float]:
    """按有效数字报送（不改变物理量级，只模拟报送精度）。"""
    if x is None or x == 0.0 or not math.isfinite(x):
        return x
    return float(f"{x:.{digits}g}")


def perturb(case: CaseRecord, rng: random.Random, *, noise_scale: float = 1.0) -> CaseRecord:
    """生成一次「评价观测」：参考值抖动 + 报送精度。"""
    out = copy.copy(case)
    u_ref = REF_UNCERTAINTY_PCT.get(case.ref_class, 0.0) * noise_scale
    if case.reference is not None and u_ref > 0:
        out.reference = case.reference * (1.0 + rng.gauss(0.0, u_ref / 100.0))
    if case.predicted is not None:
        jitter = 1.0 + rng.gauss(0.0, PRED_NOISE_PCT / 100.0) * noise_scale
        out.predicted = round_sig(case.predicted * jitter)
    if out.reference is not None:
        out.reference = round_sig(out.reference, SIG_DIGITS + 1)
    return out


def _median_observation(case: CaseRecord, rng: random.Random, *,
                        k: int, noise_scale: float) -> CaseRecord:
    """评价协议：对同一案例复测 k 次，取预报/参考各自的中位数。"""
    if k <= 1:
        return perturb(case, rng, noise_scale=noise_scale)
    preds, refs = [], []
    for _ in range(k):
        p = perturb(case, rng, noise_scale=noise_scale)
        if p.predicted is not None:
            preds.append(p.predicted)
        if p.reference is not None:
            refs.append(p.reference)
    out = copy.copy(case)

    def _med(v: List[float]) -> Optional[float]:
        if not v:
            return None
        v = sorted(v)
        n = len(v)
        return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])

    out.predicted = _med(preds) if preds else case.predicted
    out.reference = _med(refs) if refs else case.reference
    return out


def _verdicts(case: CaseRecord, fam_a, fam_b) -> Dict[str, Any]:
    va = fam_a(case)
    vb = fam_b(case)
    ok = agree(va, vb)
    return {
        "a": va["label"], "b": vb["label"],
        "joint": va["label"] if ok else "DISPUTED",
    }


def flip_rate(
    cases: Sequence[CaseRecord],
    *,
    n_evals: int = N_EVALS,
    k_measurements: int = 3,
    noise_scale: float = 1.0,
    seed: int = 20261106,
    fam_a: Callable[[CaseRecord], Dict[str, Any]] = family_a,
    fam_b: Callable[[CaseRecord], Dict[str, Any]] = family_b,
) -> Dict[str, Any]:
    """对每个案例做 ``n_evals`` 次独立评价，统计每个判级通道的翻转率。"""
    rng = random.Random(seed)
    baseline = {c.case_id: _verdicts(c, fam_a, fam_b) for c in cases}
    counts = {"a": 0, "b": 0, "joint": 0}
    total = 0
    flips: List[Dict[str, Any]] = []
    for _ in range(int(n_evals)):
        for c in cases:
            obs = _median_observation(c, rng, k=k_measurements, noise_scale=noise_scale)
            got = _verdicts(obs, fam_a, fam_b)
            base = baseline[c.case_id]
            total += 1
            for ch in ("a", "b", "joint"):
                if got[ch] != base[ch]:
                    counts[ch] += 1
                    if ch == "joint":
                        flips.append({"case_id": c.case_id, "baseline": base["joint"],
                                      "observed": got["joint"]})
    rates = {ch: (round(counts[ch] / total, 6) if total else None) for ch in counts}
    return {
        "schema": SCHEMA,
        "n_cases": len(cases),
        "n_evals": int(n_evals),
        "n_evaluations": total,
        "k_measurements": int(k_measurements),
        "noise_scale": noise_scale,
        "seed": seed,
        "flip_counts": counts,
        "flip_rate": rates,
        "max_flip_rate": MAX_FLIP_RATE,
        "min_cases": MIN_CASES,
        "flip_details": flips[:40],
        "pass": (len(cases) >= MIN_CASES and rates["joint"] is not None
                 and rates["joint"] <= MAX_FLIP_RATE),
        "note": ("翻转率=判级变化次数/(n_cases×n_evals)，噪声=参考计量不确定度+报送精度；"
                 "joint 通道=两族一致判级，不一致记 DISPUTED。"),
    }


def evaluate_robustness(
    cases: Sequence[CaseRecord],
    *,
    n_evals: int = N_EVALS,
    seed: int = 20261106,
) -> Dict[str, Any]:
    """协议 k=1（单次测量）与 k=3（复测取中位）各测一遍，再加噪声灵敏度曲线。"""
    single = flip_rate(cases, n_evals=n_evals, k_measurements=1, seed=seed)
    protocol = flip_rate(cases, n_evals=n_evals, k_measurements=3, seed=seed)
    sweep = []
    for scale in (0.5, 1.0, 2.0, 4.0, 8.0):
        r = flip_rate(cases, n_evals=n_evals, k_measurements=3,
                      noise_scale=scale, seed=seed)
        sweep.append({"noise_scale": scale, "joint_flip_rate": r["flip_rate"]["joint"],
                      "a_flip_rate": r["flip_rate"]["a"], "b_flip_rate": r["flip_rate"]["b"]})
    return {
        "schema": SCHEMA,
        "n_cases": len(cases),
        "n_evals": int(n_evals),
        "single_measurement": single,
        "median3_protocol": protocol,
        # 结论沿用任务声明的评价协议（复测取中位），同时披露单次测量结果
        "flip_rate": protocol["flip_rate"]["joint"],
        "flip_rate_by_channel": protocol["flip_rate"],
        "max_flip_rate": MAX_FLIP_RATE,
        "noise_sweep": sweep,
        "pass": protocol["pass"],
        "note": ("结论取「复测取中位」协议；单次测量翻转率作为下界对照一并披露，"
                 "不做选择性披露。"),
    }