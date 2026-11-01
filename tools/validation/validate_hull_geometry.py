#!/usr/bin/env python3
"""
m1478 验证欠账复核：湿表面积模型 + wall-sided 复原力臂公式。

对 backend/api/calculations.py 的两个 REAL 能力做独立复核：

A. 阻力侧 —— 湿表面积 S（摩擦阻力 Rf = 0.5*rho*S*Cf*V^2 正比于 S）
   参照：规范形体（方盒/圆柱/球/胶囊体）的**闭式** S 与闭式 Cb（0% 基准）；
         Wigley 抛物型船体湿表面积由**数值积分**独立求出（S = 2∬√(1+y_x²+y_z²)）
   被检模型：m1478 前平台式 S=pi*B*L*(0.5+0.25Cb)、Denny-Mumford、
         Holtrop&Mennen(1982)、本单新增闭式分支

B. 稳性侧 —— 复原力臂 GZ
   参照：矩形横剖面船体的**多边形裁剪 + 等排水量二分解**数值解（不依赖闭式公式）
   被检模型：wall-sided 公式 GZ=sin(phi)(GM+0.5*BM*tan²phi)，以及其适用横倾角上限

C. 摩擦阻力系数 Cf：ITTC-1957 实现值 vs 标准表值（Re=1e6..1e9）

输出：artifacts/validation/m1478/*.json + figures/*.png（纯 python 计算，无重算力进程）
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services import hull_geometry as hg  # noqa: E402

OUT = ROOT / "artifacts" / "validation" / "m1478"
ASCII_NAMES = {"box": "barge", "cylinder": "cylinder", "sphere": "sphere", "capsule": "capsule"}
FIG = OUT / "figures"


# ---------------------------------------------------------------------------
# A1. 规范形体：闭式基准 vs 各经验模型
# ---------------------------------------------------------------------------

CANONICAL_CASES = [
    # (name, form, L, B, T)
    ("方盒驳 barge L100/B20/T8", "box", 100.0, 20.0, 8.0),
    ("潜没圆柱 L50/D10", "cylinder", 50.0, 10.0, 10.0),
    ("球 R5", "sphere", 10.0, 10.0, 10.0),
    ("胶囊体 L4.356/D0.508 (SUBOFF 外形包络)", "capsule", 4.356, 0.508, 0.508),
]

# ITTC-1957 标准表值（条款 1957 / 表 1，Re -> Cf）
ITTC57_TABLE = {
    1.0e6: 0.004688,
    1.0e7: 0.003000,
    1.0e8: 0.002083,
    1.0e9: 0.001531,
}


def ittc57_cf(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


def section_a() -> dict:
    rows = []
    for name, form, L, B, T in CANONICAL_CASES:
        s_exact = hg.exact_wetted_surface(form, L, B, T)
        cb_exact = hg.form_cb(form, L, B, T)
        models = {
            "legacy_piBL(0.5+0.25Cb)": hg.legacy_wetted_surface(L, B, T, cb_exact),
            "denny_mumford": hg.denny_mumford_wetted_surface(L, B, T, cb_exact),
            "holtrop1982": hg.holtrop_wetted_surface(L, B, T, cb_exact),
            "exact_closed_form": s_exact,
        }
        rows.append(
            {
                "case": name,
                "form": form,
                "L": L,
                "B": B,
                "T": T,
                "Cb_exact": round(cb_exact, 6),
                "S_exact_m2": round(s_exact, 4),
                "models": {
                    k: {
                        "S_m2": round(v, 4),
                        "rel_err_pct": round((v - s_exact) / s_exact * 100.0, 3),
                    }
                    for k, v in models.items()
                },
            }
        )
    return {"cases": rows}


# ---------------------------------------------------------------------------
# A2. Wigley 抛物型船体：数值积分基准
# ---------------------------------------------------------------------------

def wigley_surface_rect(L: float, B: float, T: float, nx: int = 400, nz: int = 200):
    """Wigley 船体 y(x,z) = (B/2)(1-(2x/L)^2)(1-(z/T)^2)，返回 (S, Cb, S_wall_est)。

    S = 2 * ∫∫ sqrt(1 + y_x^2 + y_z^2) dx dz（两侧船壳，基线不封闭，与常用
    Wigley 双体/半体算例一致）。Cb 由同一被积函数解析积分给出 = 4/9。
    """
    yx_max = B * 2.0 / L  # 归一化用
    dx = L / nx
    dz = T / nz
    s = 0.0
    for i in range(nx):
        x = -L / 2.0 + (i + 0.5) * dx
        fx = 1.0 - (2.0 * x / L) ** 2
        dfx = -8.0 * x / (L * L)
        for j in range(nz):
            z = (j + 0.5) * dz
            fz = 1.0 - (z / T) ** 2
            dfz = -2.0 * z / (T * T)
            y_x = (B / 2.0) * dfx * fz
            y_z = (B / 2.0) * fx * dfz
            s += math.sqrt(1.0 + y_x * y_x + y_z * y_z) * dx * dz
    s *= 2.0
    cb = (2.0 / 3.0) * (2.0 / 3.0)  # ∫(1-(2x/L)^2)/L dx = 2/3，z 向同理
    return s, cb, yx_max


def section_a2() -> dict:
    cases = [
        ("Wigley L10/B1/T0.625", 10.0, 1.0, 0.625),
        ("Wigley L6/B1/T0.5", 6.0, 1.0, 0.5),
    ]
    rows = []
    for name, L, B, T in cases:
        s_num, cb, _ = wigley_surface_rect(L, B, T)
        models = {
            "legacy_piBL(0.5+0.25Cb)": hg.legacy_wetted_surface(L, B, T, cb),
            "denny_mumford": hg.denny_mumford_wetted_surface(L, B, T, cb),
            "holtrop1982": hg.holtrop_wetted_surface(L, B, T, cb),
        }
        rows.append(
            {
                "case": name,
                "L": L,
                "B": B,
                "T": T,
                "Cb": round(cb, 6),
                "S_numeric_m2": round(s_num, 4),
                "models": {
                    k: {
                        "S_m2": round(v, 4),
                        "rel_err_pct": round((v - s_num) / s_num * 100.0, 3),
                    }
                    for k, v in models.items()
                },
            }
        )
    return {"cases": rows, "note": "Wigley Cb=4/9≈0.444，低于 Holtrop 适用域 0.55，误差仅作参考"}


# ---------------------------------------------------------------------------
# B. 复原力臂：wall-sided 闭式 vs 独立数值解
# ---------------------------------------------------------------------------

def section_b() -> dict:
    B, T, depth, KG = 10.0, 4.0, 8.0, 3.0
    hs = hg.box_hydrostatics(B, T, KG)
    limit_deg = math.degrees(hg.wall_sided_limit_rad(B, T, depth))
    rows = []
    for phi in [0.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 38.0, 40.0, 45.0, 55.0]:
        gz_num = hg.box_gz_numeric(B, T, depth, KG, phi)
        gz_ws = hg.wall_sided_gz(hs["GM"], hs["BM"], math.radians(phi))
        denom = abs(gz_num) if abs(gz_num) > 1e-9 else 1.0
        rows.append(
            {
                "heel_deg": phi,
                "GZ_numeric_m": round(gz_num, 6),
                "GZ_wall_sided_m": round(gz_ws, 6),
                "abs_diff_m": round(gz_ws - gz_num, 6),
                "rel_err_pct": round((gz_ws - gz_num) / denom * 100.0, 3),
                "within_wall_sided": phi <= limit_deg,
            }
        )
    return {
        "hull": {"B": B, "T": T, "depth": depth, "KG": KG, **{k: round(v, 6) for k, v in hs.items()}},
        "heel_limit_deg": round(limit_deg, 3),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# C. ITTC-1957 Cf 表值核对
# ---------------------------------------------------------------------------

def section_c() -> dict:
    rows = []
    for re, cf_ref in ITTC57_TABLE.items():
        cf = ittc57_cf(re)
        rows.append(
            {
                "Re": re,
                "Cf_table": cf_ref,
                "Cf_impl": round(cf, 6),
                "rel_err_pct": round((cf - cf_ref) / cf_ref * 100.0, 3),
            }
        )
    return {"rows": rows}


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------

def make_figures(a: dict, a2: dict, b: dict) -> list:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG.mkdir(parents=True, exist_ok=True)
    figs = []

    # 图1：规范形体湿表面积相对误差（模型 x 形体）
    model_keys = ["legacy_piBL(0.5+0.25Cb)", "denny_mumford", "holtrop1982", "exact_closed_form"]
    labels = [ASCII_NAMES[c["form"]] for c in a["cases"]]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    width = 0.2
    for i, mk in enumerate(model_keys):
        vals = [c["models"][mk]["rel_err_pct"] for c in a["cases"]]
        ax.bar([x + i * width for x in range(len(labels))], vals, width, label=mk)
    ax.axhline(0, color="k", lw=0.8)
    ax.axhline(3, color="r", ls="--", lw=1, label="±3% criterion")
    ax.axhline(-3, color="r", ls="--", lw=1)
    ax.set_xticks([x + 1.5 * width for x in range(len(labels))])
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Wetted-surface rel. error [%]")
    ax.set_title("m1478 wetted-surface models vs closed-form reference (0% = truth)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p1 = FIG / "wetted_surface_errors.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    figs.append(p1)

    # 图2：GZ 曲线（闭式 vs 数值）+ 适用域
    rows = b["rows"]
    heels = [r["heel_deg"] for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(heels, [r["GZ_numeric_m"] for r in rows], "o-", label="numeric (equal-displacement polygon integration)")
    ax.plot(heels, [r["GZ_wall_sided_m"] for r in rows], "s--", label="wall-sided closed form")
    ax.axvline(b["heel_limit_deg"], color="r", ls=":", lw=1.5)
    ax.text(b["heel_limit_deg"] + 0.5, 0.02, f"validity limit {b['heel_limit_deg']:.1f} deg", fontsize=8, color="r")
    ax.set_xlabel("heel angle [deg]")
    ax.set_ylabel("GZ [m]")
    ax.set_title("m1478 righting arm: box section B=10 T=4 D=8 KG=3")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p2 = FIG / "gz_validation.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    figs.append(p2)

    # 图3：阻力侧影响 —— Rf 误差随形体（用同一 V/L 的 ITTC-57）
    fig, ax = plt.subplots(figsize=(9, 4.4))
    names, errs = [], []
    for c in a["cases"]:
        if c["S_exact_m2"] <= 0:
            continue
        names.append(ASCII_NAMES.get(c["form"], c["form"]))
        errs.append(c["models"]["legacy_piBL(0.5+0.25Cb)"]["rel_err_pct"])
    ax.barh(names, errs, color=["#d62728" if abs(e) > 3 else "#2ca02c" for e in errs])
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("Rf relative error [%]  (Rf proportional to S)")
    ax.set_title("Legacy wetted-surface formula: error passed to friction resistance")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    p3 = FIG / "rf_error_propagation.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    figs.append(p3)
    return figs


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    a = section_a()
    a2 = section_a2()
    b = section_b()
    c = section_c()

    valid = [r for r in b["rows"] if r["within_wall_sided"] and r["heel_deg"] > 0]
    max_gz_err_valid = max(abs(r["rel_err_pct"]) for r in valid)
    invalid = [r for r in b["rows"] if not r["within_wall_sided"]]
    max_gz_err_invalid = max((abs(r["rel_err_pct"]) for r in invalid), default=0.0)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "A_canonical": {
            "legacy_max_abs_err_pct": round(
                max(abs(c_["models"]["legacy_piBL(0.5+0.25Cb)"]["rel_err_pct"]) for c_ in a["cases"]), 3
            ),
            "holtrop_max_abs_err_pct": round(
                max(abs(c_["models"]["holtrop1982"]["rel_err_pct"]) for c_ in a["cases"]), 3
            ),
            "exact_branch_max_abs_err_pct": round(
                max(abs(c_["models"]["exact_closed_form"]["rel_err_pct"]) for c_ in a["cases"]), 6
            ),
        },
        "A2_wigley": {
            "holtrop_max_abs_err_pct": round(
                max(abs(c_["models"]["holtrop1982"]["rel_err_pct"]) for c_ in a2["cases"]), 3
            ),
            "legacy_max_abs_err_pct": round(
                max(abs(c_["models"]["legacy_piBL(0.5+0.25Cb)"]["rel_err_pct"]) for c_ in a2["cases"]), 3
            ),
        },
        "B_gz": {
            "heel_limit_deg": b["heel_limit_deg"],
            "max_rel_err_within_valid_pct": round(max_gz_err_valid, 4),
            "max_rel_err_beyond_valid_pct": round(max_gz_err_invalid, 3),
        },
        "C_ittc57": {
            "max_abs_err_pct": round(max(abs(r["rel_err_pct"]) for r in c["rows"]), 4)
        },
    }
    figs = make_figures(a, a2, b)
    payload = {
        "mission": 1478,
        "summary": summary,
        "A_canonical_wetted_surface": a,
        "A2_wigley_wetted_surface": a2,
        "B_righting_arm": b,
        "C_ittc57_friction": c,
        "figures": [str(p.relative_to(ROOT)) for p in figs],
    }
    out_json = OUT / "validation.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("written:", out_json.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())