#!/usr/bin/env python3
"""m1673 · 已合并功能复核（QR-P 线）：Holtrop–Mennen 服务 vs 正典 SUBOFF 基准。

背景（验证欠账）：
  * 报告 1202/1203/1204 已定量指出 /calculations/resistance 端点的经验 Cr 项
    （Cr = 0.002(1+2Cb)Fn^2.5，无出处曲线拟合）相对库内试验数据偏高 3.7–4.0 倍；
  * backend/services/holtrop_mennen.py（1982+1984 完整回归链）已合入 main，
    但从未对正典基准做过独立复核（本单补上）。

判据（平台标准）：关键量 vs 基准误差 >15% 不可接受。
正典：backend/data/benchmark_reference.json v3.0（m1561 六分支裁决），
SUBOFF 4 航速点为唯一 rt_status 可判决（DERIVED，DTRC-90/012）的算例。

输出：artifacts/m1673/holtrop_suboff_benchmark.json / .png（零手抄，报告直接引用）。
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.holtrop_mennen import holtrop_resistance  # noqa: E402

G = 9.81
OUT_DIR = os.path.join(ROOT, "artifacts", "m1673")


def legacy_endpoint_ct(L, B, T, Cb, V, rho, nu):
    """backend/api/calculations.py::calc_resistance 的公式链（form_factor=1.0 默认）。"""
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2
    Fn = V / math.sqrt(G * L)
    Cr = 0.002 * (1.0 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2
    Rt = Rf + Rr
    Ct = Rt / (0.5 * rho * S * V ** 2)
    return Rt, S, Ct


def main() -> None:
    canon = json.load(open(os.path.join(ROOT, "backend", "data", "benchmark_reference.json")))
    cases = [c for c in canon["cases"] if c.get("ship_type") == "SUBOFF"]
    assert len(cases) == 4, f"expect 4 SUBOFF cases, got {len(cases)}"

    rows = []
    for c in sorted(cases, key=lambda x: x["speed_ms"]):
        g = c["geometry"]
        # SUBOFF 回转体：B = T = D；Cb 取正典/端点默认 0.542；S 用正典实测湿面积
        L = g["length_m"]; B = T = g["diameter_m"]; Cb = 0.542
        S_meas = g["wetted_surface_m2"]
        V = c["speed_ms"]
        rho = c["reference_density_kg_m3"]
        nu = c["reference_kinematic_viscosity_m2_s"]
        ct_ref = c["experimental_ct"]
        rt_ref = c["experimental_rt_n"]

        h = holtrop_resistance(L, B, T, Cb, V, rho=rho, nu=nu, wet_surface=S_meas)
        ct_h = h.ct
        rt_h = h.rt_n

        rt_leg, s_leg, ct_leg = legacy_endpoint_ct(L, B, T, Cb, V, rho, nu)
        ct_leg_smeas = rt_leg / (0.5 * rho * S_meas * V ** 2)  # 同 S 口径，隔离 Cr 项

        rows.append({
            "case_id": c["case_id"],
            "speed_ms": V,
            "fn": round(V / math.sqrt(G * L), 5),
            "re": round(V * L / nu, 3),
            "s_holtrop_m2": round(h.wetted_surface_m2, 4),
            "s_canon_m2": S_meas,
            "s_legacy_m2": round(s_leg, 4),
            "cf": round(h.cf, 8),
            "form_factor_1pk": round(h.form_factor, 5),
            "ct_holtrop": round(ct_h, 6),
            "ct_canon": ct_ref,
            "ct_err_pct": round((ct_h - ct_ref) / ct_ref * 100.0, 2),
            "rt_holtrop_n": round(rt_h, 3),
            "rt_canon_n": rt_ref,
            "rt_err_pct": round((rt_h - rt_ref) / rt_ref * 100.0, 2) if rt_ref else None,
            "ct_legacy_endpoint": round(ct_leg, 6),
            "ct_legacy_err_pct": round((ct_leg - ct_ref) / ct_ref * 100.0, 2),
            "ct_legacy_sameS": round(ct_leg_smeas, 6),
            "ct_legacy_sameS_err_pct": round((ct_leg_smeas - ct_ref) / ct_ref * 100.0, 2),
            "rt_legacy_n": round(rt_leg, 3),
            "rt_legacy_err_pct": round((rt_leg - rt_ref) / rt_ref * 100.0, 2) if rt_ref else None,
            "holtrop_warnings": h.warnings,
            "canon_status": c.get("reference_status"),
        })

    n_pass = sum(1 for r in rows if abs(r["ct_err_pct"]) <= 15.0)
    out = {
        "mission": "m1673",
        "benchmark": "benchmark_reference.json v3.0 (canon, m1561 adjudication)",
        "ship": "SUBOFF (DARPA, DTRC-90/012)",
        "fluid": "fresh water 20C: rho=998.2, nu=1.004e-6",
        "criterion": "|C_T err| <= 15% (platform standard)",
        "rows": rows,
        "n_ct_pass_15pct": n_pass,
        "n_ct_total": len(rows),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    jpath = os.path.join(OUT_DIR, "holtrop_suboff_benchmark.json")
    json.dump(out, open(jpath, "w"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))

    # ---- figure -------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vs = [r["speed_ms"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax1.plot(vs, [r["ct_canon"] for r in rows], "ko-", label="canon DTRC-90/012 (C_T)")
    ax1.plot(vs, [r["ct_holtrop"] for r in rows], "r^--", label="holtrop_mennen service")
    ax1.plot(vs, [r["ct_legacy_endpoint"] for r in rows], "bs:", label="/calculations/resistance (legacy Cr)")
    ax1.set_xlabel("V (m/s)")
    ax1.set_ylabel("C_T")
    ax1.set_title("SUBOFF C_T: merged service vs canon vs legacy endpoint")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    labels = [f"{r['case_id']}\nV={r['speed_ms']:.0f}" for r in rows]
    x = range(len(rows))
    ax2.bar([i - 0.2 for i in x], [r["ct_err_pct"] for r in rows], 0.4, label="holtrop C_T err %")
    ax2.bar([i + 0.2 for i in x], [r["ct_legacy_err_pct"] for r in rows], 0.4, label="legacy endpoint C_T err %")
    ax2.axhline(15, color="g", ls="--", lw=1)
    ax2.axhline(-15, color="g", ls="--", lw=1)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels, fontsize=7)
    ax2.set_ylabel("relative error (%)")
    ax2.set_title("C_T error vs canon (green = ±15% gate)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    ppath = os.path.join(OUT_DIR, "holtrop_suboff_benchmark.png")
    fig.savefig(ppath, dpi=140)
    print("FIG", ppath)


if __name__ == "__main__":
    main()
