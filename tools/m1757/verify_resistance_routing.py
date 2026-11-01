"""m1757 验证脚本：`POST /calculations/resistance` 三条阻力路线的对标复核。

背景
----
平台服务的阻力端点历史上只有一条"轴对称体经验式"路线（SUBOFF 标定的湿面积
``S = pi*B*L*(0.5+0.25*Cb)`` 与 ``Cr = 0.002(1+2Cb)Fn^2.5``）。仓内同时存在
一份 Holtrop-Mennen 1982/1984 全量实现（``services/resistance_holtrop.py``）
但从未接进端点。本脚本量化"端点值 vs 仓内 Holtrop 参考值"的偏差，复核
m1757 引入的域内路由（``resistance_method="auto"``）是否把偏差压回可接受带。

运行（无网络、无 DB，psycopg2 以桩模块替代）::

    python3 tools/m1757/verify_resistance_routing.py

产物：``artifacts/m1757/resistance_routing.json`` +
``artifacts/m1757/fig_resistance_routes.png``
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
OUT = os.path.join(ROOT, "artifacts", "m1757")


def install_psycopg2_stub() -> None:
    """端点模块的 import 链会拉 DB 引擎；本脚本不需要 DB，用桩占位。"""

    class _Stub(types.ModuleType):
        def __getattr__(self, name):                     # noqa: D105
            value = type(name, (), {"__init__": lambda *a, **k: None})
            setattr(self, name, value)
            return value

    for name in ("psycopg2", "psycopg2.extensions", "psycopg2.extras"):
        if name not in sys.modules:
            mod = _Stub(name)
            mod.__version__ = "2.9.9"      # SQLAlchemy 需要可解析的版本号
            mod.paramstyle = "pyformat"
            sys.modules[name] = mod


install_psycopg2_stub()
for path in (BACKEND, os.path.join(ROOT, "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from api.calculations import (                                   # noqa: E402
    ResistanceRequest,
    calc_resistance,
)
from services.resistance_holtrop import (                         # noqa: E402
    ResistanceInput,
    applicability_violations,
    compute_resistance,
    sensitivity_band,
)
from ship_perf.resistance import HullForm, predict_resistance     # noqa: E402

RHO = 1025.0
NU = 1.19e-6
G = 9.81

#: 三条真实船型（主尺度取自公开标模/典型设计，湿面积不注入，交由各路自算）
HULLS = {
    "KVLCC2": dict(
        L=320.0, B=58.0, T=20.8, Cb=0.8098, Cm=0.9998, lcb=3.48,
        speeds=[7.20, 7.72, 8.23], s_ref=27000.0, s_ref_note="仓内文档引用的公布值 ~27 000 m²",
    ),
    "KCS": dict(
        L=230.0, B=32.2, T=10.8, Cb=0.651, Cm=0.9849, lcb=-1.48,
        speeds=[5.00, 6.17, 7.40], s_ref=9530.0, s_ref_note="KCS 公布湿面积 9 530 m²（模型报告）",
    ),
    "Bulk180k": dict(
        L=290.0, B=45.0, T=18.0, Cb=0.82, Cm=0.995, lcb=2.5,
        speeds=[6.00, 7.00, 8.00], s_ref=None, s_ref_note="无公布基准（典型 18 万吨散货船设计点）",
    ),
    "SUBOFF": dict(
        L=4.356, B=0.508, T=0.476, Cb=0.542, Cm=1.0, lcb=0.0,
        speeds=[3.0, 4.0, 5.0], s_ref=1.1371 * math.pi * 0.508 * 4.356,
        s_ref_note="回转体 0.86πDL 近似（本仓 SUBOFF 标定口径）",
    ),
}


def endpoint(**kw):
    return asyncio.run(calc_resistance(ResistanceRequest(**kw)))


def main() -> dict:
    rows = []
    for name, h in HULLS.items():
        vol = h["Cb"] * h["L"] * h["B"] * h["T"]
        for v in h["speeds"]:
            common = dict(
                ship_type=name, length=h["L"], beam=h["B"], draft=h["T"],
                cb=h["Cb"], speed_ms=v, water_density=RHO, kinematic_viscosity=NU,
            )
            legacy = endpoint(resistance_method="legacy", **common)
            auto = endpoint(resistance_method="auto", **common)
            ref = compute_resistance(ResistanceInput(
                length=h["L"], beam=h["B"], draft=h["T"], cb=h["Cb"],
                speed_ms=v, rho=RHO, nu=NU, cm=h["Cm"], lcb_pct=h["lcb"],
            ))
            hull = HullForm(
                lwl=h["L"], lpp=h["L"], b=h["B"], t=h["T"], displacement=vol,
                cb=h["Cb"], cm=h["Cm"], lcb_pct=h["lcb"], cstern=10.0,
            )
            try:
                ximpl = predict_resistance(hull, v, submerged=(name == "SUBOFF"))
                rt_x, err_x = round(ximpl.rt, 4), None
            except Exception:                                # 异实现拒算 = 记录，不掩盖
                rt_x, err_x = None, None
            fn = v / math.sqrt(G * h["L"])
            band = sensitivity_band(ResistanceInput(
                length=h["L"], beam=h["B"], draft=h["T"], cb=h["Cb"],
                speed_ms=v, rho=RHO, nu=NU, cm=h["Cm"], lcb_pct=h["lcb"],
            ))
            rt_ref = float(ref["rt"])
            viol = applicability_violations(h["L"], h["B"], h["T"], h["Cb"], v)
            err_x = (None if rt_x is None else round(100 * (rt_x - float(ref["rt"])) / float(ref["rt"]), 2))
            rows.append(dict(
                hull=name, speed_ms=v, fn=round(fn, 4),
                domain_violations=viol,
                route_auto=auto.resistance_route,
                rt_legacy_n=legacy.total_resistance_n,
                rt_auto_n=auto.total_resistance_n,
                rt_holtrop_ref_n=rt_ref,
                rt_ship_perf_n=rt_x,
                err_legacy_vs_holtrop_pct=round(100 * (legacy.total_resistance_n - rt_ref) / rt_ref, 2),
                err_auto_vs_holtrop_pct=round(100 * (auto.total_resistance_n - rt_ref) / rt_ref, 2),
                err_ship_perf_vs_holtrop_pct=err_x,
                s_legacy_m2=round(legacy.wetted_surface_m2, 1),
                s_auto_m2=round(auto.wetted_surface_m2, 1),
                s_holtrop_m2=round(float(ref["wetted_surface"]), 1),
                s_ref_m2=h["s_ref"],
                s_ref_note=h["s_ref_note"],
                ct_auto=auto.total_resistance_coefficient,
                sum_identity_residual_n=round(
                    auto.total_resistance_n
                    - (auto.frictional_resistance_n + auto.form_resistance_n
                       + auto.residual_resistance_n), 6),
                pe_kw=round(auto.total_resistance_n * v / 1000.0, 1),
                band_rt_min_n=round(band["rt_min"], 1),
                band_rt_max_n=round(band["rt_max"], 1),
                band_halfwidth_pct=round(
                    50 * (band["rt_max"] - band["rt_min"]) / rt_ref, 2),
            ))

    os.makedirs(OUT, exist_ok=True)
    payload = {
        "mission": "m1757",
        "what": "端点阻力路线复核：legacy(轴对称体经验式) vs auto(域内 Holtrop) vs "
                "仓内 Holtrop 参考 vs 异实现(ship_perf)",
        "rho": RHO, "nu": NU,
        "n_rows": len(rows),
        "rows": rows,
    }
    with open(os.path.join(OUT, "resistance_routing.json"), "w") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    _figure(rows)
    _print_table(rows)
    return payload


def _figure(rows) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                                  # pragma: no cover
        print(f"[fig] matplotlib 不可用，跳过作图: {exc}")
        return
    hulls = list(dict.fromkeys(r["hull"] for r in rows))
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.4))

    ax = axes[0]
    width = 0.27
    x = range(len(hulls))
    for i, (key, label, color) in enumerate((
            ("rt_legacy_n", "legacy (shipped)", "#c0392b"),
            ("rt_auto_n", "auto (domain-gated)", "#27ae60"),
            ("rt_holtrop_ref_n", "Holtrop reference", "#2c3e50"))):
        vals = [next(r[key] for r in rows if r["hull"] == hn) / 1e6 for hn in hulls]
        ax.bar([xi + (i - 1) * width for xi in x], vals, width, label=label, color=color)
    ax.set_xticks(list(x))
    ax.set_xticklabels(hulls)
    ax.set_ylabel("$R_T$ [MN] (design speed)")
    ax.set_title("(a) Total resistance: three routes")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    for key, label, color in (("err_legacy_vs_holtrop_pct", "legacy", "#c0392b"),
                              ("err_auto_vs_holtrop_pct", "auto", "#27ae60"),
                              ("err_ship_perf_vs_holtrop_pct", "ship_perf (indep.)", "#2980b9")):
        for hn in hulls:
            pts = [(r["fn"], r[key]) for r in rows
                   if r["hull"] == hn and r[key] is not None]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-" if hn == hulls[0] else "s--",
                    color=color, label=label if hn == hulls[0] else None, alpha=0.85)
    ax.axhline(0, color="k", lw=0.8)
    ax.axhline(15, color="#8e44ad", ls=":", lw=1.2, label="±15 % gate")
    ax.axhline(-15, color="#8e44ad", ls=":", lw=1.2)
    ax.set_xlabel("Froude number $Fn$")
    ax.set_ylabel("deviation vs Holtrop [%]")
    ax.set_title("(b) Route deviation (15 % = platform gate)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[2]
    for hn in hulls:
        pts = [(r["fn"], r["s_auto_m2"]) for r in rows if r["hull"] == hn]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-", label=f"{hn} (auto)")
        ref = next(r["s_ref_m2"] for r in rows if r["hull"] == hn)
        if ref:
            ax.axhline(ref, ls=":", lw=1.0, color=ax.lines[-1].get_color(), alpha=0.7)
    ax.set_xlabel("Froude number $Fn$")
    ax.set_ylabel("$S_{wet}$ [m$^2$]")
    ax.set_title("(c) Wetted surface: routed vs published (dotted)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    fig.suptitle("m1757  resistance route verification: served endpoint vs in-repo Holtrop", fontsize=11)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_resistance_routes.png")
    fig.savefig(path, dpi=130)
    print(f"[fig] {path}")


def _print_table(rows) -> None:
    hdr = (f"{'hull':9s} {'Fn':>6s} {'route':9s} {'R_T legacy':>11s} {'R_T auto':>10s} "
           f"{'Holtrop':>10s} {'err_leg%':>9s} {'err_auto%':>10s} {'S auto':>8s} {'S ref':>8s}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        s_ref = f"{r['s_ref_m2']:8.0f}" if r["s_ref_m2"] else "    n/a"
        xp = "   n/a" if r["rt_ship_perf_n"] is None else f"{r['rt_ship_perf_n']/1e6:6.3f}"
        print(f"{r['hull']:9s} {r['fn']:6.3f} {r['route_auto']:9s} "
              f"{r['rt_legacy_n']/1e6:11.3f} {r['rt_auto_n']/1e6:10.3f} "
              f"{r['rt_holtrop_ref_n']/1e6:10.3f} {r['err_legacy_vs_holtrop_pct']:9.2f} "
              f"{r['err_auto_vs_holtrop_pct']:10.2f} {r['s_auto_m2']:8.0f} {s_ref}")


if __name__ == "__main__":
    main()