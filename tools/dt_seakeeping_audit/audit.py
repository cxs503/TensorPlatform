"""DT 耐波代理（数字孪生 seakeeping surrogate）谱积分验证欠账审计。

被测对象：``backend/services/digital_twin/models/seakeeping.py``（已合并，服务于
``online_predictor`` 与 ``surrogate_registry`` 的耐波通道）。它把不规则波响应算成
"24 点均匀频率栅格上的 JONSWAP 加权积分"：

    m0 = sum_i S(w_i) * RAO(w_i)^2 * dw ,  w_i in 2*pi*[0.03, 0.50] Hz

本审计不改被测代码（只 import 其公开函数），用**独立收敛的细网格积分**作参考离散
（异离散交叉路线），判定该 24 点积分是否把 RMS 运动压进 3% 精度带，并自查三件可
机器裁的物理性质：遭遇频率开关、增阻经验律的 Hs^2 恒等式与声明标定带、自然周期工程带。

用法：``python3 -m tools.dt_seakeeping_audit.audit --out artifacts/dt_seakeeping_audit``
"""
from __future__ import annotations

import json
import math
import os
import sys
from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.digital_twin.models.geometry import HullForm               # noqa: E402
from services.digital_twin.models.resistance import holtrop_resistance   # noqa: E402
from services.digital_twin.models.seakeeping import (                    # noqa: E402
    G, added_resistance, irregular_seastate, jonswap, regular_response,
)

#: 出厂栅格（与 seakeeping.irregular_seastate 的默认一致）
PLATFORM_GRID_HZ = (0.03, 0.50, 24)
#: 参考积分域：覆盖 JONSWAP 双侧尾部（0.02 rad/s ≈ 0.003 Hz … 4.0 rad/s ≈ 0.64 Hz）
REF_OMEGA = (0.02, 4.0)
#: 参考栅格基准点数（收敛研究在此栅格上做 16 倍抽稀）
BASE_N = 8192

SHIPS: Dict[str, Dict[str, float]] = {
    "KCS":       dict(L=230.0, B=32.2, T=10.8, Cb=0.651, KG=8.0, V=7.716),
    "KVLCC2":    dict(L=320.0, B=58.0, T=20.8, Cb=0.8098, KG=12.0, V=7.973),
    "Feeder120": dict(L=120.0, B=19.0, T=7.0, Cb=0.70, KG=6.0, V=7.202),
    "Aframax":   dict(L=250.0, B=44.0, T=14.5, Cb=0.82, KG=9.0, V=7.459),
    "Ferry":     dict(L=160.0, B=26.0, T=6.0, Cb=0.62, KG=6.5, V=10.289),
}

#: (Hs [m], Tp [s], heading [deg], U [m/s])
SEASTATES: List[Tuple[float, float, float, float]] = [
    (4.0, 10.0, 180.0, 0.0),
    (4.0, 10.0, 180.0, 7.716),
    (6.0, 8.0, 180.0, 7.716),
    (2.0, 6.0, 180.0, 7.716),
    (5.0, 14.0, 180.0, 7.716),
    (4.0, 10.0, 135.0, 7.716),
    (0.5, 5.0, 180.0, 7.716),
]


def hull_of(ship: str) -> HullForm:
    s = SHIPS[ship]
    return HullForm(s["L"], s["B"], s["T"], s["Cb"])


def ref_omega_grid(n: int = BASE_N) -> np.ndarray:
    lo, hi = REF_OMEGA
    return np.geomspace(lo, hi, int(n))


@lru_cache(maxsize=None)
def _rao_curves(ship: str, heading: float, U: float, use_encounter: bool, n: int = BASE_N):
    """在参考栅格上一次性求 RAO 曲线（被测模块的 regular_response，逐点）。"""
    hull, KG = hull_of(ship), SHIPS[ship]["KG"]
    w = ref_omega_grid(n)
    h = np.empty(n)
    p = np.empty(n)
    for i, wi in enumerate(w):
        r = regular_response(float(wi), heading, hull, KG, U,
                             use_encounter_frequency=use_encounter)
        h[i] = r["heave_rao"]
        p[i] = r["pitch_rao"] * (float(wi) ** 2 / G)     # rad/m，与出厂还原方式一致
    return w, h, p


def _integrate(ship: str, Hs: float, Tp: float, heading: float, U: float,
               use_encounter: bool, decim: int = 1) -> Dict[str, float]:
    """参考离散：在参考栅格（抽稀 decim 倍）上做梯形谱积分。"""
    w, h, p = _rao_curves(ship, heading, U, use_encounter, BASE_N)
    w, h, p = w[::decim], h[::decim], p[::decim]
    S = jonswap(w, Hs, Tp)                 # 与出厂同一定义（细栅格上归一化到 Hs^2/16）
    m0_h = float(np.trapezoid(S * h * h, w))
    m0_p = float(np.trapezoid(S * p * p, w))
    return {"heave_rms_m": math.sqrt(max(m0_h, 0.0)),
            "pitch_rms_deg": math.degrees(math.sqrt(max(m0_p, 0.0))),
            "peak_period_s": float(2 * math.pi / w[int(np.argmax(h))])}


def reference_irregular(ship: str, Hs: float, Tp: float, heading: float, U: float,
                        use_encounter: bool = False) -> Dict[str, float]:
    return _integrate(ship, Hs, Tp, heading, U, use_encounter, 1)


def platform_irregular(ship: str, Hs: float, Tp: float, heading: float, U: float,
                       use_encounter: bool = False,
                       n_freq: int = PLATFORM_GRID_HZ[2]) -> Dict[str, float]:
    """出厂路径：直接调用被测模块（不重写、不旁路）。"""
    hull, KG = hull_of(ship), SHIPS[ship]["KG"]
    return irregular_seastate(Hs, Tp, heading, hull, KG, U,
                              n_freq=n_freq, use_encounter_frequency=use_encounter)


def jonswap_band_fraction(Hs: float, Tp: float, n: int = 65536) -> Dict[str, float]:
    """细栅格上落在出厂栅格 [0.03, 0.50] Hz 之外的能量占比（栅格截断量）。"""
    w = ref_omega_grid(n)
    S = jonswap(w, Hs, Tp)
    lo, hi = 2 * math.pi * PLATFORM_GRID_HZ[0], 2 * math.pi * PLATFORM_GRID_HZ[1]
    m0 = float(np.trapezoid(S, w))
    inside = float(np.trapezoid(np.where((w >= lo) & (w <= hi), S, 0.0), w))
    return {"m0_total": m0, "m0_inside_band": inside,
            "outside_fraction": 1.0 - inside / m0 if m0 > 0 else 0.0,
            "outside_equiv_hs": 4.0 * math.sqrt(max(m0 - inside, 0.0))}


def convergence_study(ship: str = "KCS", Hs: float = 4.0, Tp: float = 10.0,
                      heading: float = 180.0, U: float = 7.716,
                      decims: Sequence[int] = (16, 8, 4, 2, 1)) -> Dict[str, object]:
    """参考离散自身的收敛性：相邻两级的相对变化（最末级 <0.1% 才算收敛）。"""
    levels = []
    for d in decims:
        r = _integrate(ship, Hs, Tp, heading, U, False, d)
        levels.append({"n": BASE_N // d, "heave_rms_m": r["heave_rms_m"],
                       "pitch_rms_deg": r["pitch_rms_deg"]})
    steps = []
    for a, b in zip(levels, levels[1:]):
        steps.append({"n": b["n"],
                      "d_heave_rel": abs(b["heave_rms_m"] - a["heave_rms_m"]) / abs(b["heave_rms_m"]),
                      "d_pitch_rel": abs(b["pitch_rms_deg"] - a["pitch_rms_deg"]) / abs(b["pitch_rms_deg"])})
    return {"levels": levels, "steps": steps,
            "heave_converged_rel": steps[-1]["d_heave_rel"],
            "pitch_converged_rel": steps[-1]["d_pitch_rel"]}


def added_resistance_law_checks(ship: str, heading: float = 180.0) -> Dict[str, object]:
    """增阻经验律三条可机器裁的性质 + 模块自述标定带自查。"""
    s = SHIPS[ship]
    hull = hull_of(ship)
    r4 = added_resistance(4.0, heading, hull, s["V"])
    r2 = added_resistance(2.0, heading, hull, s["V"])
    r0 = added_resistance(0.0, heading, hull, s["V"])
    port = added_resistance(4.0, 180.0 - 35.0, hull, s["V"])
    stbd = added_resistance(4.0, 180.0 + 35.0, hull, s["V"])
    r_calm = holtrop_resistance(s["V"], s["L"], s["B"], s["T"], s["Cb"])["RT_N"]
    return {"R_aw_head_Hs4_N": r4,
            "quadratic_ratio_4_over_2": r4 / max(r2, 1e-12),      # 精确应为 4.0
            "zero_Hs_N": r0,                                       # 精确应为 0.0
            "mirror_rel_diff": abs(port - stbd) / max(abs(stbd), 1e-12),
            "R_calm_N": r_calm,
            "added_over_calm": r4 / max(r_calm, 1e-12),
            "declared_band": [0.05, 0.15]}


def natural_period_bands(ship: str) -> Dict[str, float]:
    """自然周期：垂荡非物理下界（A33 = 0）+ 附加质量比工程带自检。"""
    s = SHIPS[ship]
    hull = hull_of(ship)
    r = regular_response(0.5, 180.0, hull, s["KG"], 0.0)
    m = hull.volume * hull.rho
    t_min = 2 * math.pi * math.sqrt(m / (hull.rho * G * hull.waterplane_area))
    gm = _gm_rect(s)
    t44 = 2 * math.pi * 0.36 * s["B"] / math.sqrt(G * gm) if gm > 0.05 else float("nan")
    return {"T33_s": r["natural_heave_period_s"], "T55_s": r["natural_pitch_period_s"],
            "T33_lower_bound_s": t_min,
            "T33_over_lower_bound": r["natural_heave_period_s"] / t_min,
            "T44_ref_036B_s": t44, "GM_rect_m": gm,
            "A33_over_m": r["A33_total_kg"] / m}


def _gm_rect(s: Dict[str, float]) -> float:
    """矩形截面近似 GM = KB + BM - KG（仅作横摇周期工程基准，不用于判分）。"""
    kb = 0.5 * s["T"]
    bm = (s["B"] ** 2 / 12.0) / s["T"]
    return kb + bm - s["KG"]


def judge(rel_err: float, bar: float = 0.03, cap: float = 0.15) -> str:
    """三态判据：<bar PASS；bar..cap FAIL（超精度带）；>cap FAIL_GT15（不可接受）。"""
    if rel_err != rel_err:            # NaN
        return "WITHHELD"
    if rel_err < bar:
        return "PASS"
    return "FAIL" if rel_err <= cap else "FAIL_GT15"


def run_all(out_dir: str, want_figure: bool = True) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for ship in SHIPS:
        for (Hs, Tp, hd, U) in SEASTATES:
            ref = reference_irregular(ship, Hs, Tp, hd, U)
            got = platform_irregular(ship, Hs, Tp, hd, U)
            e_h = abs(got["heave_rms_m"] - ref["heave_rms_m"]) / max(ref["heave_rms_m"], 1e-12)
            e_p = abs(got["pitch_rms_deg"] - ref["pitch_rms_deg"]) / max(ref["pitch_rms_deg"], 1e-12)
            rows.append({"ship": ship, "Hs": Hs, "Tp": Tp, "heading": hd, "U": U,
                         "platform_heave_rms": got["heave_rms_m"],
                         "ref_heave_rms": ref["heave_rms_m"], "heave_rel_err": e_h,
                         "heave_verdict": judge(e_h),
                         "platform_pitch_rms_deg": got["pitch_rms_deg"],
                         "ref_pitch_rms_deg": ref["pitch_rms_deg"], "pitch_rel_err": e_p,
                         "pitch_verdict": judge(e_p),
                         "ref_peak_period_s": ref["peak_period_s"],
                         "platform_peak_period_s": got["peak_period_s"]})
    conv = convergence_study()
    bands = {f"Hs={Hs}/Tp={Tp}": jonswap_band_fraction(Hs, Tp)
             for (Hs, Tp) in [(2.0, 6.0), (4.0, 10.0), (5.0, 14.0), (6.0, 8.0)]}
    enc = []
    for ship in SHIPS:
        U = SHIPS[ship]["V"]
        off = platform_irregular(ship, 4.0, 10.0, 180.0, U, use_encounter=False)
        on = platform_irregular(ship, 4.0, 10.0, 180.0, U, use_encounter=True)
        ref_on = reference_irregular(ship, 4.0, 10.0, 180.0, U, use_encounter=True)
        enc.append({"ship": ship, "U": U,
                    "heave_rms_default_off": off["heave_rms_m"],
                    "heave_rms_flag_on": on["heave_rms_m"],
                    "heave_rms_ref_on": ref_on["heave_rms_m"],
                    "doppler_rel": abs(off["heave_rms_m"] - ref_on["heave_rms_m"]) / ref_on["heave_rms_m"],
                    "peak_period_off": off["peak_period_s"],
                    "peak_period_ref_on": ref_on["peak_period_s"]})
    added = {ship: added_resistance_law_checks(ship) for ship in SHIPS}
    periods = {ship: natural_period_bands(ship) for ship in SHIPS}
    summary = {"n_cases": len(rows),
               "heave_pass": sum(r["heave_verdict"] == "PASS" for r in rows),
               "pitch_pass": sum(r["pitch_verdict"] == "PASS" for r in rows),
               "max_heave_rel_err": max(r["heave_rel_err"] for r in rows),
               "max_pitch_rel_err": max(r["pitch_rel_err"] for r in rows),
               "median_heave_rel_err": float(np.median([r["heave_rel_err"] for r in rows])),
               "median_pitch_rel_err": float(np.median([r["pitch_rel_err"] for r in rows]))}
    result = {"platform_grid": PLATFORM_GRID_HZ, "ref_grid": REF_OMEGA, "base_n": BASE_N,
              "cases": rows, "convergence": conv, "band_fraction": bands,
              "encounter": enc, "added_resistance": added, "natural_periods": periods,
              "summary": summary}
    with open(os.path.join(out_dir, "audit.json"), "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    if want_figure:
        _figure(result, out_dir)
    return result


def _figure(result: Dict[str, object], out_dir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    w, h, _ = _rao_curves("KCS", 180.0, SHIPS["KCS"]["V"], False, BASE_N)
    S = jonswap(w, 4.0, 10.0)
    wf = 2 * math.pi * np.linspace(PLATFORM_GRID_HZ[0], PLATFORM_GRID_HZ[1], PLATFORM_GRID_HZ[2])
    hull, KG, U = hull_of("KCS"), SHIPS["KCS"]["KG"], SHIPS["KCS"]["V"]
    hf = np.array([regular_response(float(x), 180.0, hull, KG, U)["heave_rao"] for x in wf])
    Sf = jonswap(wf, 4.0, 10.0)
    fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.3))
    ax[0].plot(w, S * h * h, lw=1.6, label="converged ref (8192 pts)")
    ax[0].plot(wf, Sf * hf ** 2, "o-", ms=4, lw=1.2, label="shipped 24-pt grid")
    ax[0].set_xlabel("omega [rad/s]"); ax[0].set_ylabel("S(w) * RAO_heave^2  [m^2 s]")
    ax[0].set_title("KCS  Hs=4 m  Tp=10 s  head seas:\nheave spectral integrand under test")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
    ships = list(SHIPS)
    eh = [max(r["heave_rel_err"] for r in result["cases"] if r["ship"] == s) for s in ships]
    ep = [max(r["pitch_rel_err"] for r in result["cases"] if r["ship"] == s) for s in ships]
    idx = np.arange(len(ships))
    ax[1].bar(idx - 0.2, np.array(eh) * 100, 0.4, label="heave RMS")
    ax[1].bar(idx + 0.2, np.array(ep) * 100, 0.4, label="pitch RMS")
    ax[1].axhline(3, color="g", ls="--", lw=1.2, label="3 % bar")
    ax[1].axhline(15, color="r", ls="--", lw=1.2, label="15 % bar")
    ax[1].set_xticks(idx); ax[1].set_xticklabels(ships, fontsize=8)
    ax[1].set_ylabel("max rel. error vs converged ref [%]")
    ax[1].set_title("shipped integration vs converged reference")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=.3, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig1_spectral_integral.png"), dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_HERE, "..", "..", "artifacts", "dt_seakeeping_audit"))
    a = ap.parse_args()
    res = run_all(os.path.abspath(a.out))
    print(json.dumps(res["summary"], indent=1, ensure_ascii=False))
    print(json.dumps(res["convergence"]["steps"][-1], ensure_ascii=False))