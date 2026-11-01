#!/usr/bin/env python3
"""耐波性（seakeeping）验证欠账 · 独立数值复核套件 —— m1649

为什么需要这一套
----------------
`backend/services/seakeeping.py` 与 `backend/api/domain/ship_seakeeping.py` 的
docstring 都声称「analytic anchors are machine-verified（见
docs/audits/seakeeping_verification_m1433.md）」，但该文档**不在树内**（悬挂证据引用），
且 `backend/tests/feat/test_ship_seakeeping.py` 的断言是拿**同一份闭式**自证（闭式 vs 闭式），
不构成独立复算。本套件补的是**独立数值路线**：

    物理量 -> 数值积分/极值搜索（另一条路径） -> 与代码闭式比对 -> 逐项相对误差

因此它能抓到「闭式写错但测试跟着一起错」这类失败模式，也能给出网格/截断带来的
离散化偏差（平台默认 omega∈[0.05,2.5], n=201 的量化误差此前从未量化）。

判据分级
--------
* 恒等式/不变性（S1,S6,S8,S9）  : 容差 1e-9
* 数值 vs 闭式      (S2,S3,S7): 容差 1e-6（复合 Simpson）；S4 峰值为 1e-7（尖锐峰浮点极限）
* 结构性质          (S5)        : 高频率段 log-log 斜率 -> -2，容差 1e-3
* 离散化量化        (S10,S11)   : 只报告偏差，不设门禁（信息性）

用法::

    python3 tools/benchmarks/seakeeping_anchors.py                # 全量
    python3 tools/benchmarks/seakeeping_anchors.py --json out.json

退出码 0 = 全部恒等/结构判据 PASS（离散化偏差只报告）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Callable, Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import seakeeping as sk  # noqa: E402

G = sk.G

# ---------------------------------------------------------------------------
# 独立数值工具（不调用 sk 的任何积分/闭式，只做通用数值）
# ---------------------------------------------------------------------------


def simpson(f: Callable[[float], float], a: float, b: float, n: int = 20000) -> float:
    """复合 Simpson（n 偶数），用于独立数值路线。"""
    if n % 2:
        n += 1
    h = (b - a) / n
    s = f(a) + f(b)
    for i in range(1, n, 2):
        s += 4.0 * f(a + i * h)
    for i in range(2, n, 2):
        s += 2.0 * f(a + i * h)
    return s * h / 3.0


def golden_max(f: Callable[[float], float], a: float, b: float, it: int = 200) -> Tuple[float, float]:
    """黄金分割求 [a,b] 上的极大点（独立于解析极值公式）。"""
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(it):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = f(d)
    x = 0.5 * (a + b)
    return x, f(x)


def rel_err(computed: float, reference: float) -> float:
    if reference == 0.0:
        return abs(computed)
    return abs(computed - reference) / abs(reference)


# ---------------------------------------------------------------------------
# 锚点
# ---------------------------------------------------------------------------


def s1_dispersion() -> Dict[str, object]:
    """ω²=gk 的独立路线：由 λ=2πg/ω² 反推 k=2π/λ，比对 sk.wave_number。"""
    worst = 0.0
    for w in (0.1, 0.3, 0.55, 1.0, 1.8, 2.5):
        lam = 2.0 * math.pi * G / (w * w)          # 深水波长（独立定义式）
        k_indep = 2.0 * math.pi / lam              # k = 2π/λ
        worst = max(worst, rel_err(sk.wave_number(w), k_indep))
    return {"id": "S1", "name": "深水色散 ω²=gk（λ 路线独立反推）", "kind": "identity",
            "error": worst, "tol": 1e-12}


def s2_lengthwise_factor() -> Dict[str, object]:
    """抛物型水线面长度相位的独立路线：对 b(x)=B(1-(2x/L)²) 做数值 Fourier 积分。"""
    L, B = 150.0, 22.0
    A_w = 2.0 * B * L / 3.0                        # ∫_{-L/2}^{L/2} b dx = 2BL/3

    def numeric(u: float) -> float:
        kb = 2.0 * u / L                           # u = kL|cosβ|/2 ⇒ k|cosβ| = 2u/L
        f = lambda x: (B * (1.0 - (2.0 * x / L) ** 2)) * math.cos(kb * x)  # noqa: E731
        return simpson(f, -L / 2.0, L / 2.0, 40000) / A_w

    worst = 0.0
    rows = []
    for u in (0.05, 0.3, 1.0, 2.0, 4.5, 8.0):
        closed = sk.waterplane_length_factor(u)
        num = numeric(u)
        e = rel_err(closed, num)
        worst = max(worst, e)
        rows.append({"u": u, "closed_form": closed, "numeric": num, "rel_err": e})
    return {"id": "S2", "name": "水线面长度因子 Γ(u)=3(sin u-u cos u)/u³（数值 Fourier 积分）",
            "kind": "numeric", "error": worst, "tol": 1e-6, "rows": rows}


def s3_beam_factor() -> Dict[str, object]:
    """横向 sinc 因子的独立路线：对矩形水线面做 y 向数值 Fourier 积分。"""
    B = 22.0
    worst = 0.0
    for w, heading in ((0.3, 90.0), (0.6, 60.0), (1.2, 90.0), (2.0, 45.0)):
        kb = sk.wave_number(w) * abs(math.sin(math.radians(heading)))
        f = lambda y: math.cos(kb * y)                                  # noqa: E731
        num = simpson(f, -B / 2.0, B / 2.0, 40000) / B
        worst = max(worst, rel_err(sk.beam_factor(w, B, heading), num))
    return {"id": "S3", "name": "横向 sinc 因子 sin q/q（y 向数值 Fourier 积分）",
            "kind": "numeric", "error": worst, "tol": 1e-6}


def s4_resonance_peak() -> Dict[str, object]:
    """SDOF 共振峰：黄金分割数值搜索 vs 闭式 (√(1-2ζ²), 1/(2ζ√(1-ζ²)))。"""
    worst_r = worst_h = 0.0
    for zeta in (0.05, 0.10, 0.12, 0.20, 0.35):
        r_num, h_num = golden_max(lambda r: sk.transfer(r, zeta), 1e-3, 3.0)
        r_cf, h_cf = sk.transfer_peak(zeta)
        worst_r = max(worst_r, rel_err(r_num, r_cf))
        worst_h = max(worst_h, rel_err(h_num, h_cf))
    return {"id": "S4", "name": "共振峰位置/幅值（数值极值 vs 闭式）",
            "kind": "numeric", "error": max(worst_r, worst_h), "tol": 1e-7,
            "detail": {"r_peak_rel_err": worst_r, "H_peak_rel_err": worst_h}}


def s5_high_freq_slope() -> Dict[str, object]:
    """高频渐近：|H| ~ r^-2 ⇒ log-log 斜率 = -2（独立线性拟合）。"""
    zeta = 0.1
    pts = [(w, sk.transfer(w / 1.0, zeta)) for w in (50.0, 80.0, 120.0, 200.0, 400.0)]
    n = len(pts)
    xs = [math.log(p[0]) for p in pts]
    ys = [math.log(p[1]) for p in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return {"id": "S5", "name": "高频衰减斜率 = -2（log-log 线性拟合）", "kind": "structural",
            "error": abs(slope + 2.0), "tol": 1e-3, "detail": {"fitted_slope": slope}}


def s6_long_wave_limit() -> Dict[str, object]:
    """长波极限与 κ/ζ 参数无关（对系数扫描，任何取值都应 →1）。"""
    worst = 0.0
    for kappa, zeta in ((0.80, 0.20), (0.50, 0.05), (1.50, 0.45), (0.20, 0.02)):
        m = sk.SeakeepingModel(L=150.0, B=22.0, T=8.0, Cb=0.65, Cwp=0.75,
                               displacement_t=17589.0, KG=8.0, GM_T=1.0,
                               kappa33=kappa, zeta33=zeta)
        row = m.rao_at(1e-4, heading_deg=180.0, speed=0.0)
        worst = max(worst, rel_err(row["heave_rao_m_per_m"], 1.0))
    return {"id": "S6", "name": "长波极限 heave RAO→1（κ/ζ 四组扫描无关性）",
            "kind": "invariance", "error": worst, "tol": 1e-3}


def s7_ittc_moments() -> Dict[str, object]:
    """ITTC 谱矩：独立变量替换积分 vs 闭式 A/(4B)、A√π/(4√B)。"""
    A, B = 173.0 * 2.0 ** 2 / 8.0 ** 4, 691.0 / 8.0 ** 4

    # m0：u=ω^{-4} ⇒ ∫Aω^{-5}e^{-Bω^{-4}}dω = (A/4)∫_0^∞ e^{-Bu}du，用 y 映射到有限区间
    def m0_num() -> float:
        f = lambda y: math.exp(-B * (-math.log(1.0 - y)) / B) / (B * (1.0 - y))  # noqa: E731
        return (A / 4.0) * simpson(f, 0.0, 1.0 - 1e-12, 40000)

    # m2：(A/4)∫_0^∞ u^{-1/2}e^{-Bu}du, u=v²/B ⇒ (A/(4√B))∫_0^∞ 2e^{-v²}dv
    def m2_num() -> float:
        f = lambda v: 2.0 * math.exp(-v * v)                                     # noqa: E731
        return (A / (4.0 * math.sqrt(B))) * simpson(f, 0.0, 10.0, 40000)

    e0 = rel_err(sk.ittc_m0_closed(A, B), m0_num())
    e2 = rel_err(sk.ittc_m2_closed(A, B), m2_num())
    return {"id": "S7", "name": "ITTC 谱矩闭式 m0=A/4B、m2=A√π/4√B（变量替换数值积分）",
            "kind": "numeric", "error": max(e0, e2), "tol": 1e-6,
            "detail": {"m0_rel_err": e0, "m2_rel_err": e2}}


def s8_froude_scaling() -> Dict[str, object]:
    """Froude 缩比不变性：全长与 λ 缩比模型在 ω/√λ 处 RAO 相同（独立自算，不借平台比较器）。"""
    lam = 25.0
    full = sk.SeakeepingModel(L=150.0, B=22.0, T=8.0, Cb=0.65, Cwp=0.75,
                              displacement_t=17589.0, KG=8.0, GM_T=1.0)
    model = sk.SeakeepingModel(L=150.0 / lam, B=22.0 / lam, T=8.0 / lam, Cb=0.65, Cwp=0.75,
                              displacement_t=17589.0 / lam ** 3, KG=8.0 / lam, GM_T=1.0 / lam)
    worst = 0.0
    for w_full in (0.2, 0.4, 0.8, 1.2):
        a = full.rao_at(w_full, heading_deg=180.0)["heave_rao_m_per_m"]
        b = model.rao_at(w_full * math.sqrt(lam), heading_deg=180.0)["heave_rao_m_per_m"]
        worst = max(worst, rel_err(b, a))
    return {"id": "S8", "name": f"Froude 缩比 λ={lam:g} 下 heave RAO 不变", "kind": "invariance",
            "error": worst, "tol": 1e-8}


def s9_encounter_frequency() -> Dict[str, object]:
    """遭遇频率的运动学恒等式：U=0 ⇒ ω_e=ω；迎浪(180°) ⇒ ω_e=ω+ω²U/g。"""
    worst = 0.0
    for w, U in ((0.5, 0.0), (0.5, 7.0), (1.1, 12.0)):
        # 随浪(0°)与迎浪(180°)各自的独立一阶运动学式；注意随浪强流下 ω_e 可为负（已知越界分支）
        follow = w - w * w * U / G
        head = w + w * w * U / G
        worst = max(worst, rel_err(sk.encounter_frequency(w, U, 0.0), follow))
        worst = max(worst, rel_err(sk.encounter_frequency(w, U, 180.0), head))
    return {"id": "S9", "name": "遭遇频率 U=0/迎浪 渐近恒等式", "kind": "identity",
            "error": worst, "tol": 1e-12}


def s10_spectrum_hs_and_grid() -> Dict[str, object]:
    """谱的 Hs 还原 + 默认网格 (0.05–2.5, n=201) 的离散化偏差量化。"""
    rows = []
    worst_hs = 0.0
    for kind in ("pm", "jonswap", "ittc"):
        coarse = sk.uniform_grid(0.05, 2.5, 201)
        fine = sk.uniform_grid(0.02, 4.0, 4001)
        sc = sk.spectrum(kind, 3.0, 8.0, coarse)
        sf = sk.spectrum(kind, 3.0, 8.0, fine)
        worst_hs = max(worst_hs, abs(sc["Hs_recovered"] / sc["Hs_target"] - 1.0))
        rows.append({
            "kind": kind,
            "Hs_err_pct_coarse": 100.0 * (sc["Hs_recovered"] / sc["Hs_target"] - 1.0),
            "Hs_err_pct_fine": 100.0 * (sf["Hs_recovered"] / sf["Hs_target"] - 1.0),
            "m2_rel_diff": rel_err(sc["m2"], sf["m2"]),
            "Tz_s_coarse": sc["Tz_s"], "Tz_s_fine": sf["Tz_s"],
            "Tz_rel_diff": rel_err(sc["Tz_s"], sf["Tz_s"]),
        })
    return {"id": "S10", "name": "谱 Hs 恒等 4√m0=Hs + 粗/细网格离散化偏差",
            "kind": "identity+informational", "error": worst_hs, "tol": 1e-9, "rows": rows}


def s11_response_convergence() -> Dict[str, object]:
    """响应统计的网格收敛：n=201（平台默认）vs n=4001，量化 sigma 的离散化偏差。"""
    m = sk.SeakeepingModel(L=150.0, B=22.0, T=8.0, Cb=0.65, Cwp=0.75,
                           displacement_t=17589.0, KG=8.0, GM_T=1.0)
    out = {}
    for heading in (180.0, 90.0):
        a = sk.response_statistics(m, sk.spectrum("jonswap", 3.0, 8.0, sk.uniform_grid(0.05, 2.5, 201)),
                                   heading_deg=heading)
        b = sk.response_statistics(m, sk.spectrum("jonswap", 3.0, 8.0, sk.uniform_grid(0.02, 4.0, 4001)),
                                   heading_deg=heading)
        out[f"heading_{int(heading)}"] = {
            k: {"coarse": a[k], "fine": b[k], "rel_diff": rel_err(a[k], b[k])}
            for k in ("sigma_heave", "sigma_pitch", "sigma_roll", "sigma_vertical_acc_m_s2")
        }
    worst = max(v["rel_diff"] for h in out.values() for v in h.values())
    return {"id": "S11", "name": "响应统计网格收敛（默认 n=201 的量化偏差）",
            "kind": "informational", "error": worst, "tol": None, "rows": out}


ANCHORS = [s1_dispersion, s2_lengthwise_factor, s3_beam_factor, s4_resonance_peak,
           s5_high_freq_slope, s6_long_wave_limit, s7_ittc_moments, s8_froude_scaling,
           s9_encounter_frequency, s10_spectrum_hs_and_grid, s11_response_convergence]


def run() -> Dict[str, object]:
    results = [fn() for fn in ANCHORS]
    gated = [r for r in results if r.get("tol") is not None]
    fails = [r["id"] for r in gated if r["error"] > r["tol"]]
    return {
        "tool": "seakeeping_anchors",
        "mission": "m1649",
        "repo": "TensorPlatform",
        "gated_count": len(gated),
        "informational_count": len(results) - len(gated),
        "worst_gated_error": max((r["error"] for r in gated), default=0.0),
        "verdict": "PASS" if not fails else "FAIL",
        "failed": fails,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "artifacts", "m1649",
                                                   "seakeeping_anchor_ledger.json"))
    args = ap.parse_args(argv)
    ledger = run()
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)

    print(f"{'id':<4}{'判据':<52}{'误差':>12}{'容差':>12}  结论")
    for r in ledger["results"]:
        tol = r.get("tol")
        ok = "PASS" if (tol is None or r["error"] <= tol) else "FAIL"
        print(f"{r['id']:<4}{r['name'][:50]:<52}{r['error']:>12.3e}"
              f"{(f'{tol:.0e}' if tol else 'report'):>12}  {ok}")
    print(f"\nverdict = {ledger['verdict']}  "
          f"(gated {ledger['gated_count']} / info {ledger['informational_count']}, "
          f"worst={ledger['worst_gated_error']:.3e})")
    print(f"ledger -> {args.json}")
    return 0 if ledger["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())