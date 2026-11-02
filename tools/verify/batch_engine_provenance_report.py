#!/usr/bin/env python3
"""批量执行器溯源审计：图 + 机器可读汇总 — mission 1640（只读本地代码，轻算力）。

产出
----
``artifacts/m1640/provenance_summary.json``  程序化数字（报告零手抄的唯一来源）
``docs/audits/figs/m1640_kt_kq_eta_vs_J.png``  BEM 与旧代理的 KT/KQ/eta(J) 对照
``docs/audits/figs/m1640_scale_invariance.png`` 相同 J 下换 D/rpm 的 KT/KQ 残差

用法: python3 tools/verify/batch_engine_provenance_report.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "tools" / "batch_scan"))

from batch_executor import ShipCalculationEngine  # noqa: E402
from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
)

KN = 0.5144
ART = REPO / "artifacts" / "m1640"
FIGS = REPO / "docs" / "audits" / "figs"
ART.mkdir(parents=True, exist_ok=True)
FIGS.mkdir(parents=True, exist_ok=True)

GEOM = dict(diameter_m=5.0, pitch_ratio=1.0, blade_area_ratio=0.55, blade_count=4)
RPM = 120.0
N = RPM / 60.0


def legacy(J: float) -> tuple[float, float, float]:
    """m1393 之前的硬编码代理（保留在此作为反例基准）。"""
    KT = max(0.05, 0.5 - 0.3 * J)
    KQ = max(0.01, 0.05 + 0.02 * J)
    eta = min(0.85, max(0.3, (J * KT) / (2 * math.pi * KQ) if KQ > 0 else 0.0))
    return KT, KQ, eta


def bem(J: float) -> dict:
    return open_water_point(
        PropellerGeometry(**GEOM), J, rho=1025.0, n_rps=N
    )


def main() -> int:
    Js = [0.1 * i for i in range(0, 14)]  # 0.0 .. 1.3
    bem_rows = []
    for J in Js:
        pt = bem(J)
        bem_rows.append({"J": J, "KT": pt["KT"], "KQ": pt["KQ"], "eta0": pt["eta0"],
                         "eta_ideal": pt["eta_ideal"]})

    # 零推力推进比（二分）
    lo, hi = 0.0, 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bem(mid)["KT"] > 0.0:
            lo = mid
        else:
            hi = mid
    J0 = 0.5 * (lo + hi)

    # 批量执行器 vs 服务直调（逐位一致性）
    delegated = []
    for J in (0.2, 0.4, 0.6, 0.8):
        params = dict(GEOM, rpm=RPM, advance_speed_kn=J * N * GEOM["diameter_m"] / KN)
        out = ShipCalculationEngine.calc_propeller(params)
        ref = bem(J)
        delegated.append({
            "J": J,
            "engine_KT": out["thrust_coefficient"], "service_KT": ref["KT"],
            "dKT": abs(out["thrust_coefficient"] - ref["KT"]),
            "engine_KQ": out["torque_coefficient"], "service_KQ": ref["KQ"],
            "dKQ": abs(out["torque_coefficient"] - ref["KQ"]),
            "engine_eta": out["efficiency"], "service_eta": ref["eta0"],
            "model": out["model"],
        })

    # 相似律不变性：同 J，换 (D, rpm)
    inv = []
    Jtgt = 0.6
    for D, rpm in ((3.0, 200.0), (5.0, 120.0), (7.5, 80.0)):
        n = rpm / 60.0
        params = dict(GEOM, diameter_m=D, rpm=rpm,
                      advance_speed_kn=Jtgt * n * D / KN)
        out = ShipCalculationEngine.calc_propeller(params)
        inv.append({"D": D, "rpm": rpm, "J": out["advance_coefficient"],
                    "KT": out["thrust_coefficient"],
                    "KQ": out["torque_coefficient"]})
    kt_spread = max(r["KT"] for r in inv) - min(r["KT"] for r in inv)
    kq_spread = max(r["KQ"] for r in inv) - min(r["KQ"] for r in inv)

    # 旧代理的含糊量：KQ 单调方向 + 效率平台
    legacy_rows = [{"J": J, "KT": legacy(J)[0], "KQ": legacy(J)[1], "eta0": legacy(J)[2]}
                   for J in Js]
    legacy_kq_dJ = legacy_rows[-1]["KQ"] - legacy_rows[0]["KQ"]
    plateau = sorted({round(legacy(J)[2], 6) for J in Js})

    summary = {
        "mission": 1640,
        "engine_model": ShipCalculationEngine.calc_propeller(
            dict(GEOM, rpm=RPM, advance_speed_kn=8.0))["model"],
        "geometry": GEOM, "rpm": RPM,
        "bem_curve": bem_rows,
        "legacy_curve": legacy_rows,
        "zero_thrust_J": J0,
        "bem_KT_at_max_J": bem_rows[-1]["KT"],
        "legacy_KT_floor": 0.05,
        "legacy_KQ_delta_over_grid": legacy_kq_dJ,
        "legacy_eta_plateau_values": plateau,
        "delegation": delegated,
        "max_abs_delegation_error_KT": max(r["dKT"] for r in delegated),
        "max_abs_delegation_error_KQ": max(r["dKQ"] for r in delegated),
        "scale_invariance": inv,
        "KT_spread_across_scale": kt_spread,
        "KQ_spread_across_scale": kq_spread,
    }

    # ---- 图 1: KT/KQ/eta vs J ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    Jb = [r["J"] for r in bem_rows]
    fig, ax = plt.subplots(1, 3, figsize=(13.5, 3.9))
    ax[0].plot(Jb, [r["KT"] for r in bem_rows], "o-", label="BEM (engine, REAL)")
    ax[0].plot(Jb, [r["KT"] for r in legacy_rows], "s--", label="legacy surrogate (MOCK)")
    ax[0].axhline(0.0, color="k", lw=0.6)
    ax[0].axvline(J0, color="C3", lw=0.8, ls=":")
    ax[0].annotate(f"$J_0$={J0:.3f}", (J0, 0.02), fontsize=8, color="C3")
    ax[0].set_xlabel("J = Va/(nD)"); ax[0].set_ylabel("$K_T$"); ax[0].set_title("thrust")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3)

    ax[1].plot(Jb, [r["KQ"] for r in bem_rows], "o-", label="BEM (REAL)")
    ax[1].plot(Jb, [r["KQ"] for r in legacy_rows], "s--", label="legacy (MOCK)")
    ax[1].set_xlabel("J"); ax[1].set_ylabel("$K_Q$"); ax[1].set_title("torque (legacy slope sign is wrong)")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=0.3)

    ax[2].plot(Jb, [r["eta0"] for r in bem_rows], "o-", label=r"BEM $\eta_0$")
    ax[2].plot(Jb, [r["eta_ideal"] for r in bem_rows], "-", color="C2", lw=0.9,
               label=r"actuator-disc bound")
    ax[2].plot(Jb, [r["eta0"] for r in legacy_rows], "s--", label="legacy (clipped)")
    ax[2].set_xlabel("J"); ax[2].set_ylabel(r"$\eta_0$"); ax[2].set_title("efficiency")
    ax[2].legend(fontsize=7); ax[2].grid(alpha=0.3)
    fig.suptitle("m1640 · batch engine propeller: REAL BEM vs legacy surrogate (D=5 m, P/D=1, Ae/A0=0.55, Z=4)", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGS / "m1640_kt_kq_eta_vs_J.png", dpi=150)
    plt.close(fig)

    # ---- 图 2: 相似律不变性 + 接线误差 ----
    fig, ax = plt.subplots(1, 2, figsize=(9.6, 3.6))
    labels = [f"D={r['D']:g}\nrpm={r['rpm']:g}" for r in inv]
    x = range(len(inv))
    ax[0].bar([i - 0.18 for i in x], [r["KT"] for r in inv], width=0.36, label="$K_T$")
    ax[0].bar([i + 0.18 for i in x], [r["KQ"] for r in inv], width=0.36, label="$K_Q$")
    ax[0].set_xticks(list(x)); ax[0].set_xticklabels(labels, fontsize=8)
    ax[0].set_title(f"same J=0.6, scaled D & n (KT spread {kt_spread:.1e})", fontsize=9)
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3, axis="y")

    ax[1].semilogy([r["J"] for r in delegated], [max(r["dKT"], 1e-9) for r in delegated],
                   "o-", label="|ΔKT| engine vs service")
    ax[1].semilogy([r["J"] for r in delegated], [max(r["dKQ"], 1e-9) for r in delegated],
                   "s-", label="|ΔKQ| engine vs service")
    ax[1].axhline(5e-5, color="C3", ls=":", lw=0.9, label="rounding floor 5e-5")
    ax[1].set_xlabel("J"); ax[1].set_ylabel("absolute difference")
    ax[1].set_title("delegation residual (4-dp rounding only)", fontsize=9)
    ax[1].legend(fontsize=7); ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(FIGS / "m1640_scale_invariance.png", dpi=150)
    plt.close(fig)

    (ART / "provenance_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items()
                      if not isinstance(v, list)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())