"""三把证伪刀（pure functions，无网络、无全局状态）。

设计原则：**判据机器可执行**。每把刀只吃「响应数值序列」，输出
`{verdict: PASS|KILL|INCONCLUSIVE, evidence: {...}}`，不读取任何自述字段，
因此能力声明无法反过来影响判决（反造假的必要条件）。

刀 A 扰动不变性（perturbation invariance）
    - 任务判据：量级 ×1.0/×0.7/×1.3（+ 几何 Cb∈[0.35,0.90]、L/B∈[3,12]、
      B/T∈[1.5,6.5] 内扰动），输出逐位不变或相对变化 < 1e-12 判 MOCK。
    - 工程化陷阱：平台响应常见 3~6 位小数四舍五入，弱依赖驱动量（如 Ae/Ao）
      在 ±30% 扰动下可能被舍入吃掉 → 会误判 MOCK。故每组扰动都配一组
      **加宽确认扰动**（×0.5/×1.5）。只有主集与加宽集都不变才判 KILL；
      主集不变、加宽集变化 → 判 INCONCLUSIVE_ROUNDING（计入 WITHHELD，
      不诬告也不洗白）。

刀 B 解析解/恒等式对拍（analytic cross-check）
    - 任务判据：与解析值误差 > 15% 判 MOCK。
    - 恒等式检查（eta0 = J·KT/2πKQ、GM = KB+BM−KG、∇ = L·B·T·Cb）为**精确**关系，
      用 1e-3 相对容差（仅为吸收响应里的十进制舍入），不是近似式。
    - Froude 相似 Cr 不变性检查属量纲分析的精确结论（同 Fr、同 Cb、同 L/B、B/T）。

刀 C 单调性（monotonicity）
    - 任务判据：有物理单调性的量反向变化判 MOCK（如 R_t 随 Fr 单调增）。
    - 判据实现：相邻相对步长 < 1e-12 视为「不变」（不变性归刀 A，不重复计罪）；
      方向反转即 KILL；非单调抖动同样 KILL。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------- 判据常量
INVARIANCE_TOL = 1e-12        # 任务判据：相对变化 < 1e-12 视为不变
ANALYTIC_TOL = 0.15           # 任务判据：解析解误差 > 15% 判 MOCK
IDENTITY_TOL = 1e-3           # 恒等式相对容差（吸收响应十进制舍入）
MONOTONE_REL_STEP = 1e-12     # 单调步长下限（小于此视为「未变化」）
KNOT_TO_MS = 0.514444
RHO_DEFAULT = 1025.0
NU_DEFAULT = 1.19e-6

PASS, KILL, INCONCLUSIVE = "PASS", "KILL", "INCONCLUSIVE"


# ---------------------------------------------------------------- 通用工具
def safe_float(v: Any) -> Optional[float]:
    """把响应里的数值安全取成 float；非数值返回 None（缺失即缺失，不当 0）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def rel_diff(a: float, b: float) -> float:
    """对称相对差；两侧都接近 0 时返回 0（由调用方另判零值）。"""
    d = max(abs(a), abs(b))
    if d <= 1e-300:
        return 0.0
    return abs(a - b) / d


def relative_error(observed: Optional[float], reference: Optional[float]) -> Optional[float]:
    if observed is None or reference is None:
        return None
    d = abs(reference)
    if d <= 1e-300:
        return None if observed != 0 else 0.0
    return abs(observed - reference) / d


# ---------------------------------------------------------------- 刀 A
def perturbation_invariance(
    primary: Sequence[Dict[str, Any]],
    confirm: Optional[Sequence[Dict[str, Any]]] = None,
    require: Optional[Sequence[str]] = None,
    tol: float = INVARIANCE_TOL,
) -> Dict[str, Any]:
    """刀 A：输出对驱动量是否「逐位不变」。

    primary/confirm: `[{label, knobs, response:{key: number|None}}]`，同一组样本
    只扰动一个驱动量（组由调用方组织，spec 里写清 driver 名）。
    require: 必须对该驱动量有响应的响应键；缺省=全部出现的键。
    返回 `{verdict, max_rel_change, invariant_keys, responding_keys, detail}`。
    """
    samples = [s for s in primary if isinstance(s.get("response"), dict)]
    keys = list(require) if require else sorted({k for s in samples for k in s["response"]})
    invariant, responding, per_key = [], [], {}
    max_change = 0.0
    for key in keys:
        vals = [(s["label"], safe_float(s["response"].get(key))) for s in samples]
        vals = [(lab, v) for lab, v in vals if v is not None]
        if len(vals) < 2:
            per_key[key] = {"status": "MISSING", "n": len(vals)}
            continue
        peak = 0.0
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                peak = max(peak, rel_diff(vals[i][1], vals[j][1]))
        per_key[key] = {"status": "INVARIANT" if peak < tol else "RESPONDS",
                        "max_rel_change": peak, "n": len(vals)}
        max_change = max(max_change, peak)
        (invariant if peak < tol else responding).append(key)

    verdict = PASS
    resolution = None
    if invariant:
        # 加宽确认：排除「十进制舍入吃掉了弱依赖」造成的假阳性
        if confirm:
            wide_invariant = []
            for key in invariant:
                vals = [(s["label"], safe_float(s["response"].get(key))) for s in confirm]
                vals = [(lab, v) for lab, v in vals if v is not None]
                if len(vals) < 2:
                    continue
                peak = max(rel_diff(vals[i][1], vals[j][1])
                           for i in range(len(vals)) for j in range(i + 1, len(vals)))
                if peak < tol:
                    wide_invariant.append(key)
                else:
                    resolution = {"key": key, "widen_max_rel_change": peak,
                                  "note": "主扰动集不变但加宽集变化 → 响应分辨率不足，不判 MOCK"}
            verdict = KILL if wide_invariant else INCONCLUSIVE
            if verdict == KILL:
                invariant = wide_invariant
            else:
                invariant = []
        else:
            verdict = KILL
    return {
        "knife": "A",
        "verdict": verdict,
        "max_rel_change": max_change,
        "invariant_keys": invariant,
        "responding_keys": responding,
        "per_key": per_key,
        "n_samples": len(samples),
        "resolution_limit": resolution,
        "criterion": f"相对变化 < {tol:g} 判 MOCK（加宽确认集排除舍入假阳性）",
    }


# ---------------------------------------------------------------- 刀 A2（确定性）
def input_output_determinism(repeats, tol: float = INVARIANCE_TOL) -> Dict[str, Any]:
    """刀 A2：同一请求重复调用，输出必须**逐位相同**。

    物理计算的输出是输入的函数：给定相同输入，任何「真实现」都必须给出同一个数。
    同一请求的响应若在 1e-12 以上漂移，说明输出含随机数/未固定权重——这与「对输入
    不敏感」（刀 A）是两个相反方向的伪实现，必须分开抓：
        - 刀 A 抓「输入变了输出不变」（硬编码）；
        - 刀 A2 抓「输入没变输出却变」（随机数冒充物理/未固化模型）。
    repeats: `[{label, response:{key: number}}]`（同一 body 的 n 次独立调用）。
    """
    samples = [s for s in repeats if isinstance(s.get("response"), dict)]
    per_key, bad = {}, []
    for key in sorted({k for s in samples for k in s["response"]}):
        vals = [safe_float(s["response"].get(key)) for s in samples]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            per_key[key] = {"status": "MISSING", "n": len(vals)}
            continue
        peak = max(rel_diff(vals[i], vals[j])
                   for i in range(len(vals)) for j in range(i + 1, len(vals)))
        per_key[key] = {"status": "NONDETERMINISTIC" if peak > tol else "STABLE",
                        "max_rel_spread": peak, "n": len(vals),
                        "values": vals}
        if peak > tol:
            bad.append(key)
    verdict = KILL if bad else (PASS if any(v["status"] == "STABLE"
                                            for v in per_key.values()) else INCONCLUSIVE)
    return {"knife": "A2", "verdict": verdict, "nondeterministic_keys": bad,
            "per_key": per_key, "n_repeats": len(samples),
            "criterion": f"同一请求重复调用相对漂移 < {tol:g} 判确定性；超出即判 MOCK（随机数冒充物理）"}


# ---------------------------------------------------------------- 刀 B
def analytic_crosscheck(checks: Sequence[Dict[str, Any]],
                        default_tol: float = ANALYTIC_TOL) -> Dict[str, Any]:
    """刀 B：对拍解析解/恒等式。

    checks: `[{id, observed, reference, tolerance?, kind?, formula?}]`。
    kind="identity" 时容差取 IDENTITY_TOL（精确关系），否则取 tolerance 或 15%。
    """
    rows, killed = [], []
    for c in checks:
        obs = safe_float(c.get("observed"))
        ref = safe_float(c.get("reference"))
        tol = float(c["tolerance"]) if c.get("tolerance") is not None else (
            IDENTITY_TOL if c.get("kind") == "identity" else default_tol)
        err = relative_error(obs, ref)
        if err is None:
            rows.append({**c, "rel_error": None, "verdict": "MISSING",
                         "tolerance": tol})
            continue
        ok = err <= tol
        rows.append({**c, "rel_error": err, "verdict": PASS if ok else KILL,
                     "tolerance": tol})
        if not ok:
            killed.append(c["id"])
    if killed:
        verdict = KILL
    elif any(r["verdict"] == PASS for r in rows):
        verdict = PASS
    else:
        verdict = INCONCLUSIVE
    return {
        "knife": "B",
        "verdict": verdict,
        "checks": rows,
        "killed_checks": killed,
        "max_rel_error": max([r["rel_error"] for r in rows if r["rel_error"] is not None],
                             default=None),
        "criterion": f"解析/恒等式相对误差 > {default_tol:g} 判 MOCK（恒等式 {IDENTITY_TOL:g}）",
    }


# ---------------------------------------------------------------- 刀 C
def monotonicity(series: Sequence[Dict[str, Any]], direction: str = "increasing",
                 rel_step: float = MONOTONE_REL_STEP) -> Dict[str, Any]:
    """刀 C：单调性。

    series: `[{x, y}]`（x 已按升序排好；x 只用于报告）。
    direction: "increasing" | "decreasing"。
    反方向变化即 KILL；|相对步长| < rel_step 记为 STALL（不变性由刀 A 主判）。
    """
    pts = [(safe_float(p.get("x")), safe_float(p.get("y"))) for p in series]
    pts = [(x, y) for x, y in pts if y is not None]
    if len(pts) < 3:
        return {"knife": "C", "verdict": INCONCLUSIVE, "reason": f"点数不足（{len(pts)}<3）",
                "violations": [], "criterion": f"非单调（{direction}）判 MOCK"}
    sign = 1.0 if direction == "increasing" else -1.0
    steps, violations, stalls = [], [], []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        step = rel_diff(y1, y0) * (1.0 if (y1 - y0) * sign >= 0 else -1.0)
        steps.append({"x0": x0, "x1": x1, "y0": y0, "y1": y1, "signed_rel_step": step})
        if abs(step) < rel_step:
            stalls.append({"x0": x0, "x1": x1})
        elif step < 0:
            violations.append({"x0": x0, "x1": x1, "y0": y0, "y1": y1,
                               "signed_rel_step": step})
    verdict = KILL if violations else PASS
    return {"knife": "C", "verdict": verdict, "direction": direction,
            "violations": violations, "stalls": stalls, "steps": steps,
            "n_points": len(pts),
            "criterion": f"方向 {direction} 违反即 MOCK（步长 < {rel_step:g} 记 STALL 不判罪）"}


# ---------------------------------------------------------------- 解析参照
# 以下为审计侧**独立实现**的解析解（不 import 平台代码，避免同族自证）。
def reynolds(v_ms: float, length: float, nu: float = NU_DEFAULT) -> float:
    return v_ms * length / nu


def ittc57_cf(Re: float) -> float:
    """ITTC-1957 平板摩擦阻力系数（解析式）。"""
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def eta0_identity(J: float, KT: float, KQ: float) -> float:
    """敞水效率恒等式 eta0 = J·KT / (2π·KQ)（精确）。"""
    return J * KT / (2.0 * math.pi * KQ)


def box_volume(length: float, beam: float, draft: float, cb: float) -> float:
    """排水体积恒等式 nabla = L·B·T·Cb（精确）。"""
    return length * beam * draft * cb


def gm_identity(kb: float, bm: float, kg: float) -> float:
    """初稳性高恒等式 GM = KB + BM − KG（精确）。"""
    return kb + bm - kg


def gz_wallsided_max(gm: float, bm: float, phi_max_deg: float = 89.0,
                     step_deg: float = 0.005) -> Dict[str, float]:
    """直壁式（wall-sided）大倾角复原力臂 GZ(phi)=sin(phi)(GM + BM/2·tan^2 phi)
    的极大值，用 0.005° 网格 + 三点抛物线端点精化独立求取。"""
    best = (-1e9, 0.0)
    n = int(phi_max_deg / step_deg)
    prev = None
    for i in range(n + 1):
        phi = math.radians(i * step_deg)
        gz = math.sin(phi) * (gm + 0.5 * bm * math.tan(phi) ** 2)
        if gz > best[0]:
            best = (gz, i * step_deg)
        if prev and prev[1] > gz and prev[1] > 0:
            break
        prev = (i, gz)
    return {"gz_max": best[0], "phi_deg": best[1]}


def aggregate_verdict(knife_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """多刀合议：任一 KILL → 能力的「被推翻等级」；无 KILL 且至少一刀 PASS → 存活。"""
    kills = [r["knife"] for r in knife_results if r.get("verdict") == KILL]
    passes = [r["knife"] for r in knife_results if r.get("verdict") == PASS]
    inconclusive = [r["knife"] for r in knife_results
                    if r.get("verdict") == INCONCLUSIVE]
    if kills:
        state = "KILLED"
    elif passes:
        state = "SURVIVED"
    elif inconclusive:
        state = "INCONCLUSIVE"
    else:
        state = "NO_KNIFE"
    return {"state": state, "kills": kills, "passes": passes,
            "inconclusive": inconclusive, "n_knives": len(knife_results)}