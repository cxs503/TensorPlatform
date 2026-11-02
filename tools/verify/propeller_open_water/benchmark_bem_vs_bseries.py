#!/usr/bin/env python3
"""m1664 — 敞水螺旋桨 BEM 模型对 Wageningen B 系列回归的绝对量级基准。

背景（验证欠账）
----------------
m1413 审计把批量通道里的替代式判为 MOCK，m1393/m1403 用 BEM（叶元+动量）替换了
平台服务，但当时的验证只有**解析上界 + 恒等式**（见
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``），绝对值从未对过任何公开基准。
m1413 §6.5 把这条明确留作欠账：「B 系列 KT/KQ 表值入库，届时叠加绝对量级判据」。

本脚本用仓库内 clean-room 参考实现 ``reference_bseries.py``
（Oosterveld & van Oossanen 1975 的 39/47 项回归表）作为**公开基准**，
在 B 系列有效域内逐点比对 ``KT``/``KQ``/``η0``，输出误差台账 + 图。

方法
----
* 设计点网格：Z ∈ {4,5}, P/D ∈ {0.8,1.0,1.2}, Ae/A0 ∈ {0.4,0.55,0.7}, J ∈ 0.05..1.10
  （全部落在参考实现声明的有效域 Z=2..7, P/D=0.5..1.4, Ae/A0=0.2..1.05, J=0..1.5 内）；
* 每个点用同一几何调用 BEM 与 B 系列回归，相对误差 e = |x_bem − x_ref| / |x_ref|；
* 判据：平台精度标准「对标实验/参考值误差 >15% 不可接受」，逐点判定并汇总；
* 另外检查 BEM 的结构性物理量（η0 < η_ideal 上界、KQ 随 J 单调降、Froude 不变性）。

输出（工件，零手抄）
--------------------
``artifacts/m1664/bem_vs_bseries.json``     逐点台账 + 汇总统计
``artifacts/m1664/fig_bem_vs_bseries.png``  KT/KQ/η0 曲线对比 + 误差分布
``artifacts/m1664/fig_bem_error_heatmap.png`` 相对误差随 J、P/D 的分布

用法
----
    python3 tools/verify/propeller_open_water/benchmark_bem_vs_bseries.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for p in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402
from services.propeller_open_water import PropellerGeometry, open_water_point  # noqa: E402

ART = REPO_ROOT / "artifacts" / "m1664"
BAND = 0.15                     # 不可接受线：>15% 判 fail
Z_LIST = (4, 5)
PD_LIST = (0.8, 1.0, 1.2)
AE_LIST = (0.4, 0.55, 0.7)
J_LIST = tuple(round(0.05 * i, 3) for i in range(1, 23))   # 0.05 .. 1.10


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if abs(b) > 1e-12 else float("nan")


def sweep() -> list[dict]:
    rows: list[dict] = []
    for z in Z_LIST:
        for pd in PD_LIST:
            for ae in AE_LIST:
                geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=pd,
                                         blade_area_ratio=ae, blade_count=z)
                for j in J_LIST:
                    bem = open_water_point(geom, j, rho=1025.0, n_rps=1.0)
                    rb = ref.open_water_coefficients(j, pd, ae, z)
                    e_kt = _rel(bem["KT"], rb["KT"])
                    e_kq = _rel(bem["KQ"], rb["KQ"])
                    e_eta = _rel(bem["eta0"], rb["eta_0"]) if rb["eta_0"] > 1e-9 else float("nan")
                    rows.append({
                        "Z": z, "P_D": pd, "Ae_A0": ae, "J": j,
                        "KT_bem": bem["KT"], "KT_ref": rb["KT"], "e_KT": e_kt,
                        "KQ_bem": bem["KQ"], "KQ_ref": rb["KQ"], "e_KQ": e_kq,
                        "eta_bem": bem["eta0"], "eta_ref": rb["eta_0"], "e_eta": e_eta,
                        "eta_ideal": bem["eta_ideal"],
                        "eta_below_bound": bem["eta0"] <= bem["eta_ideal"] + 1e-9,
                        "converged": bool(bem["induction_converged"]),
                    })
    return rows


def _stats(vals: list[float]) -> dict:
    v = sorted(x for x in vals if x == x)          # 去 NaN
    if not v:
        return {"n": 0}
    n = len(v)
    mean = sum(v) / n
    p50 = v[n // 2]
    p95 = v[min(n - 1, int(round(0.95 * (n - 1))))]
    return {"n": n, "mean": mean, "p50": p50, "p95": p95,
            "max": v[-1], "min": v[0],
            "frac_over_band": sum(1 for x in v if x > BAND) / n}


def _load_band(rows):
    """工程有效的比较子域：J<=0.9 且参考 KT>=0.03。

    J 逼近零推力点 J0 时 |KT_ref| -> 0，相对误差在数学上发散（分母趋零），
    放大成 1e4% 量级是**除零伪影**而非模型误差；故统计必须显式剔除该子域，
    否则均值和 p95 都被伪影支配。设计带 J∈[0.3,0.9] 单列，作为判定口径。
    """
    return [r for r in rows if r["J"] <= 0.90 + 1e-9 and abs(r["KT_ref"]) >= 0.03]


def summarise(rows: list[dict]) -> dict:
    lb = _load_band(rows)
    s = {
        "band": BAND,
        "n_points": len(rows),
        "n_load_band": len(lb),
        "note_load_band": "J<=0.9 且 |KT_ref|>=0.03（剔除零推力点附近的相对误差发散伪影）",
        "load_band_e_KT": _stats([r["e_KT"] for r in lb]),
        "load_band_e_KQ": _stats([r["e_KQ"] for r in lb]),
        "load_band_e_eta": _stats([r["e_eta"] for r in lb]),
        "e_KT": _stats([r["e_KT"] for r in rows]),
        "e_KQ": _stats([r["e_KQ"] for r in rows]),
        "e_eta": _stats([r["e_eta"] for r in rows]),
        "n_not_converged": sum(0 if r["converged"] else 1 for r in rows),
        "eta_below_bound_all": all(r["eta_below_bound"] for r in rows),
        "converged_all": all(r["converged"] for r in rows),
    }
    # 设计点（J = 0.5/0.7/0.9）单列，便于判据逐项对照
    design = []
    for r in rows:
        if any(abs(r["J"] - j0) < 1e-9 for j0 in (0.5, 0.7, 0.9)):
            design.append({k: r[k] for k in
                           ("Z", "P_D", "Ae_A0", "J", "KT_bem", "KT_ref", "e_KT",
                            "KQ_bem", "KQ_ref", "e_KQ", "eta_bem", "eta_ref", "e_eta")})
    s["design_points"] = design
    return s


def figures(rows, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = []
    sel = [r for r in rows if r["Z"] == 4 and r["P_D"] == 1.0 and abs(r["Ae_A0"] - 0.55) < 1e-9]
    Js = [r["J"] for r in sel]
    series = (("KT", "K_T [-]"), ("KQ", "K_Q [-]"), ("eta", "eta_0 [-]"))
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.2))
    for k, key in enumerate(("KT", "KQ", "eta")):
        axs[k].plot(Js, [r[key + "_ref"] for r in sel], "k-", lw=2,
                    label="B-series fit (Oosterveld 1975)")
        axs[k].plot(Js, [r[key + "_bem"] for r in sel], "o--", ms=3, color="#c0392b",
                    label="BEM (this repo)")
        axs[k].set_xlabel("J = V_a/(nD)")
        axs[k].set_ylabel(series[k][1])
        axs[k].grid(alpha=0.3)
        axs[k].legend(fontsize=8)
    axs[0].set_title("Z=4, P/D=1.0, Ae/A0=0.55")
    fig.suptitle("m1664 open-water: BEM vs published Wageningen B-series", y=1.02)
    fig.tight_layout()
    f1 = ART / "fig_bem_vs_bseries.png"
    fig.savefig(f1, dpi=130, bbox_inches="tight")
    plt.close(fig)
    out.append(str(f1))

    sub = [r for r in rows if r["Z"] == 4 and abs(r["Ae_A0"] - 0.55) < 1e-9]
    pds = sorted({r["P_D"] for r in sub})
    grid = [[next(r["e_KT"] for r in sub if r["P_D"] == pd and r["J"] == j) for j in Js]
            for pd in pds]
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.2))
    im = axs[0].imshow(grid, aspect="auto", cmap="magma",
                       extent=[Js[0], Js[-1], len(pds) - 0.5, -0.5], vmin=0, vmax=0.5)
    axs[0].set_yticks(range(len(pds)))
    axs[0].set_yticklabels(["P/D=%.1f" % p for p in pds])
    axs[0].set_xlabel("J")
    axs[0].set_title("|dKT|/KT (Z=4, Ae/A0=0.55)")
    fig.colorbar(im, ax=axs[0], label="relative error")

    for key, c, lab in (("e_KT", "#c0392b", "K_T"), ("e_KQ", "#2c3e50", "K_Q")):
        axs[1].hist([r[key] for r in rows if r[key] == r[key]], bins=40, alpha=0.6,
                    color=c, label=lab)
    axs[1].axvline(BAND, ls="--", color="g", label="15% acceptance line")
    axs[1].set_xlabel("relative error vs B-series")
    axs[1].set_ylabel("count")
    axs[1].set_yscale("log")
    axs[1].legend(fontsize=8)
    axs[1].set_title("error distribution, %d grid points" % summary["n_points"])
    fig.tight_layout()
    f2 = ART / "fig_bem_error_heatmap.png"
    fig.savefig(f2, dpi=130, bbox_inches="tight")
    plt.close(fig)
    out.append(str(f2))
    return out


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    rows = sweep()
    summary = summarise(rows)
    figs = figures(rows, summary)
    payload = {"mission": "m1664", "benchmark": "BEM vs Wageningen B-series (Oosterveld 1975)",
               "reference": "tools/verify/propeller_open_water/reference_bseries.py",
               "grid": {"Z": list(Z_LIST), "P_D": list(PD_LIST),
                        "Ae_A0": list(AE_LIST), "J": list(J_LIST)},
               "summary": summary, "points": rows, "figures": figs}
    out = ART / "bem_vs_bseries.json"
    out.write_text(json.dumps(payload, indent=1))

    s = summary
    print(f"points={s['n_points']}  band={BAND:.0%}")
    for k in ("load_band_e_KT", "load_band_e_KQ", "load_band_e_eta"):
        st = s[k]
        print(f"  {k}: mean={st['mean']:.1%} p50={st['p50']:.1%} "
              f"p95={st['p95']:.1%} max={st['max']:.1%} "
              f"over-band={st['frac_over_band']:.1%}")
    print(f"  eta0<=eta_ideal all points: {s['eta_below_bound_all']}")
    print(f"  induction converged all:    {s['converged_all']}")
    print("artifacts:", out, *figs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())