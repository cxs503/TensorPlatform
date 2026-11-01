#!/usr/bin/env python3
"""结构域端点独立解析复核（m1578）。

对 :8001 的板格稳定性 / 船体结构强度 / 疲劳端点逐项做闭式解对照，
输出 artifacts/m1578/struct_audit.json + 3 张图，并打印判定表。

用法：
    python3 tools/audit_struct/struct_audit.py [--out artifacts/m1578] [--api http://127.0.0.1:8001]

判定阈值（平台精度标准）：|rel_err| <= 3e-2 → PASS；> 1.5e-1 → FAIL；介于两者 → WARN。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plate_reference as ref  # noqa: E402

PASS_TOL, FAIL_TOL = 3e-2, 1.5e-1
MPA = 1e6


def post(api: str, path: str, body: dict, tries: int = 3, timeout: float = 30.0):
    last = None
    for _ in range(tries):
        req = urllib.request.Request(
            api + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            last = {"_http": e.code, "_body": e.read().decode()[:300]}
        except Exception as e:  # 超时/连接重置
            last = {"_err": f"{type(e).__name__}: {e}"}
        time.sleep(1.0)
    return last or {"_err": "unknown"}


def verdict(rel: float) -> str:
    if rel <= PASS_TOL:
        return "PASS"
    if rel > FAIL_TOL:
        return "FAIL"
    return "WARN"


class Audit:
    def __init__(self, api: str):
        self.api = api.rstrip("/")
        self.checks: list[dict] = []
        self.findings: list[dict] = []

    def add(self, cid: str, endpoint: str, desc: str, api_val, ref_val, unit: str,
            threshold: str = "3%", note: str = "", x: float | None = None):
        finite = isinstance(api_val, (int, float)) and isinstance(ref_val, (int, float))
        if finite:
            rel = ref.rel_err(float(api_val), float(ref_val))
            v = verdict(rel)
        else:
            rel, v = float("nan"), "N/A"
        self.checks.append({
            "id": cid, "endpoint": endpoint, "desc": desc,
            "api": api_val, "ref": ref_val, "unit": unit,
            "rel_err": None if not finite else round(rel, 6),
            "verdict": v, "threshold": threshold, "note": note, "x": x,
        })
        return self.checks[-1]

    def find(self, fid: str, severity: str, title: str, evidence: str, impact: str):
        self.findings.append({"id": fid, "severity": severity, "title": title,
                              "evidence": evidence, "impact": impact})


def plate_body(**kw) -> dict:
    b = dict(a=1.0, b=1.0, t=0.01, E=ref.E_STEEL, nu=ref.NU_STEEL, sigma_y=235e6,
             edge_condition="simply_supported", axial_load_a=0.0, axial_load_b=0.0,
             shear_load=0.0, lateral_pressure=0.0)
    b.update(kw)
    return b


def run(api: str) -> Audit:
    A = Audit(api)
    EP = "/api/v1/plate-stability/plate"
    T, B = 0.01, 1.0  # 基准板厚/板宽

    # ── A. 单轴受压 (a 向)：a/b 扫描 ─────────────────────────────
    for ratio in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
        r = post(api, EP, plate_body(a=ratio, b=B))
        k, _ = ref.k_uniaxial_simply_supported(ratio, B)
        sig = ref.sigma_cr(k, ref.E_STEEL, T, B, ref.NU_STEEL) / MPA
        A.add(f"A1 sig_cr_a ab={ratio}", EP, f"弹性屈曲 σ_cr,a (a/b={ratio})",
              r.get("critical_stress_a"), round(sig, 4), "MPa", x=ratio)
        A.add(f"A2 k ab={ratio}", EP, f"屈曲系数 k (a/b={ratio})",
              r.get("buckling_coefficient"), round(k, 4), "-", x=ratio)

    # ── B. 单轴受压 (b 向)：宽度 = a ────────────────────────────
    for ratio in (1.0, 1.5, 2.0, 3.0, 4.0):
        r = post(api, EP, plate_body(a=ratio, b=B))
        k_b, _ = ref.k_uniaxial_simply_supported(B, ratio)   # 沿 b 受压，宽 = a
        sig_b = ref.sigma_cr(k_b, ref.E_STEEL, T, ratio, ref.NU_STEEL) / MPA
        A.add(f"B1 sig_cr_b ab={ratio}", EP, f"弹性屈曲 σ_cr,b (a/b={ratio})",
              r.get("critical_stress_b"), round(sig_b, 4), "MPa", x=ratio)

    # ── C. 纯剪 ───────────────────────────────────────────────
    for ratio in (1.0, 1.5, 2.0, 3.0):
        r = post(api, EP, plate_body(a=ratio, b=B))
        tau = ref.tau_cr(ratio, B, ref.E_STEEL, T, ref.NU_STEEL) / MPA
        A.add(f"C1 tau_cr ab={ratio}", EP, f"纯剪屈曲 τ_cr (a/b={ratio})",
              r.get("critical_shear"), round(tau, 4), "MPa", x=ratio)

    # ── D. 板厚扫描 + Johnson-Ostenfeld 弹塑性修正 ──────────────
    for t in (0.008, 0.010, 0.015, 0.020, 0.025, 0.030, 0.040):
        r = post(api, EP, plate_body(t=t))
        sig = ref.sigma_cr(4.0, ref.E_STEEL, t, B, ref.NU_STEEL)
        u = ref.johnson_ostenfeld(sig, 235e6) / MPA
        A.add(f"D1 sig_cr t={t*1000:.0f}mm", EP, f"弹性屈曲 σ_cr (t={t*1000:.0f}mm)",
              r.get("critical_stress_a"), round(sig / MPA, 4), "MPa", x=t * 1000)
        A.add(f"D2 JO t={t*1000:.0f}mm", EP, f"极限强度 σ_u (Johnson-Ostenfeld, t={t*1000:.0f}mm)",
              r.get("ultimate_strength"), round(u, 4), "MPa", x=t * 1000)

    # ── E. 边界条件分支（固支应比简支高 6.97/4 = 1.74x） ────────
    r_ss = post(api, EP, plate_body(edge_condition="simply_supported"))
    r_cl = post(api, EP, plate_body(edge_condition="clamped"))
    ratio_api = (r_cl.get("critical_stress_a") / r_ss.get("critical_stress_a")
                 if r_ss.get("critical_stress_a") else None)
    A.add("E1 clamped/ss ratio", EP, "clamped σ_cr / simply_supported σ_cr",
          round(ratio_api, 4) if ratio_api else ratio_api,
          round(ref.k_uniaxial_clamped(1.0, 1.0) / 4.0, 4), "-")
    if ratio_api is not None and abs(ratio_api - 1.0) < 1e-6:
        A.find("DEF-1", "HIGH", "edge_condition 参数未生效（clamped ≡ simply_supported）",
               f"clamped σ_cr={r_cl.get('critical_stress_a')} MPa 与 simply_supported "
               f"σ_cr={r_ss.get('critical_stress_a')} MPa 完全相同（比值 {ratio_api}）；"
               f"闭式解四边固支方板 k=6.97，σ_cr 应为 "
               f"{ref.k_uniaxial_clamped(1.0,1.0)/4.0*r_ss.get('critical_stress_a'):.2f} MPa，"
               f"端点偏低 {(1-ratio_api/(ref.k_uniaxial_clamped(1.0,1.0)/4.0))*100:.1f}%",
               "固支板格按简支评定：屈曲承载力低估约 43%，且输入参数被静默忽略（契约欺骗）")

    # ── F. 安全系数分支（载荷须作用到对应方向） ─────────────────
    r_a = post(api, EP, plate_body(axial_load_a=50e6))
    r_b = post(api, EP, plate_body(axial_load_b=50e6))
    r_s = post(api, EP, plate_body(shear_load=20e6))
    sig_cr = ref.sigma_cr(4.0, ref.E_STEEL, T, B, ref.NU_STEEL) / MPA
    tau = ref.tau_cr(1.0, 1.0, ref.E_STEEL, T, ref.NU_STEEL) / MPA
    A.add("F1 SF_a", EP, "安全系数（σ_a=50 MPa）", r_a.get("safety_factor_a"),
          round(sig_cr / 50.0, 4), "-")
    A.add("F2 SF_b", EP, "安全系数（σ_b=50 MPa）", r_b.get("safety_factor_b"),
          round(sig_cr / 50.0, 4), "-")
    A.add("F3 SF_tau", EP, "安全系数（τ=20 MPa）", r_s.get("safety_factor_shear"),
          round(tau / 20.0, 4), "-")
    A.add("F4 SF_zero_load", EP, "零载荷安全系数（真值 +∞，端点截断 10）",
          r_ss.get("safety_factor_a"), None, "-",
          note="截断为工程约定，仅记录不判罚" if r_ss.get("safety_factor_a") == 10.0 else "")

    # ── G. 输入域校验：ν 超出 0–0.5 时是否拒绝 ──────────────────
    r_nu = post(api, EP, plate_body(nu=0.9), tries=2, timeout=20.0)
    nu_ok = isinstance(r_nu.get("critical_stress_a"), (int, float)) and \
        math.isfinite(r_nu["critical_stress_a"])
    A.checks.append({
        "id": "G1 nu=0.9 domain check", "endpoint": EP,
        "desc": "泊松比越界（ν=0.9，物理域 0–0.5）应被 4xx 拒绝",
        "api": "finite:" + str(r_nu.get("critical_stress_a")) if nu_ok else str(r_nu)[:120],
        "ref": "400/422", "unit": "-", "rel_err": None,
        "verdict": "FAIL" if nu_ok else "PASS", "threshold": "契约",
        "note": "未做参数域校验" if nu_ok else "已拒绝", "x": None})
    if nu_ok:
        A.find("DEF-3", "LOW", "泊松比未做参数域校验（ν=0.9 照算）",
               f"ν=0.9 仍返回 σ_cr={r_nu.get('critical_stress_a')} MPa（1-ν²=0.19 使刚度虚高），"
               "无 4xx 拒绝", "金属材料误输入会得到偏大承载（不安全方向），建议加 0<ν<0.5 校验")

    # ── H. 船体级板格强度端点（/structure/plate） ───────────────
    HS = "/api/v1/structure/plate"
    hull = dict(L=320.0, B=58.0, T=20.8, D=30.0, displacement=320000.0, Cb=0.81,
                frame_spacing=4.0, plate_thickness=0.022, yield_stress=235e6)
    r_h = post(api, HS, hull)
    pan = ref.panel_critical_stress(4.0, 4.0, 0.022)
    A.add("H1 hull sigma_cr", HS, "船底板格 σ_cr（b=肋距 4 m, t=22 mm）",
          r_h.get("critical_stress"), round(pan["sigma_cr"] / MPA, 4), "MPa")
    A.add("H2 hull sigma_actual", HS, "工作应力 vs σ_y/2", r_h.get("actual_stress"),
          235e6 / 2, "Pa")
    # 由返回值反解隐含板宽 b_impl（k=4, 同一 t）：σ_cr = 4π²E/(12(1-ν²))·(t/b)²
    implied = None
    if isinstance(r_h.get("critical_stress"), (int, float)) and r_h["critical_stress"] > 0:
        s_mpa = r_h["critical_stress"]
        implied = 0.022 / math.sqrt(s_mpa * MPA / (4.0 * math.pi ** 2 * ref.E_STEEL
                                                  / (12.0 * (1 - ref.NU_STEEL ** 2))))
    b_alt = ref.panel_critical_stress(12.0, 12.0, 0.022)  # b = 3×肋距 假设
    A.add("H3 implied panel width", HS, "由 σ_cr 反解隐含板宽 b（解析 kB=3×肋距=12 m → 2.50 MPa）",
          round(implied, 4) if implied else None, 12.0, "m")
    A.add("H4 b=3s hypothesis", HS, "按 b=3×肋距 复算 σ_cr",
          r_h.get("critical_stress"), round(b_alt["sigma_cr"] / MPA, 4), "MPa")
    h_thin = post(api, HS, {**hull, "plate_thickness": 0.011})
    r_t = (h_thin.get("critical_stress", 0) or 0) / (r_h.get("critical_stress") or 1)
    A.add("H5 t^2 law", HS, "板厚减半时 σ_cr 比（解析 0.25）", round(r_t, 4), 0.25, "-")
    if implied and abs(implied - 12.0) / 12.0 < 0.05:
        A.find("DEF-4", "HIGH",
               "船体级板格隐含板宽取 3×肋距（12 m），非构件间距；且与 actual_stress 单位不一致",
               f"输入 t=22 mm/s=4 m 时端点 σ_cr={r_h.get('critical_stress')} MPa，"
               f"反解隐含 b≈{implied:.2f} m（=3×肋距，k=4 复算 {b_alt['sigma_cr']/MPA:.2f} MPa 吻合）；"
               f"按最近构件间距 4 m 的闭式解应为 {pan['sigma_cr']/MPA:.2f} MPa（端点偏低 "
               f"{(1-implied and (1-(r_h.get('critical_stress')/(pan['sigma_cr']/MPA)))*100):.1f}%）；"
               f"同响应 actual_stress={r_h.get('actual_stress')} Pa = σ_y/2 常数，"
               f"与 MPa 量纲的 critical_stress 直接相除得 safety_factor={r_h.get('safety_factor')}",
               "船体级板格屈曲利用率恒判「需加强」，安全系数无物理意义（量纲混用 10^6 倍 + 板宽假设错位）")

    # ── I. 疲劳端点：DNV-RP-C203 曲线 C 对照 ───────────────────
    FT = "/api/v1/structure/fatigue"
    fat = []
    for sy in (235e6, 315e6, 355e6):
        r_f = post(api, FT, {**hull, "yield_stress": sy})
        ds = r_f.get("stress_range")
        n_ref = ref.dnv_c_life(ds / 1.0) if isinstance(ds, (int, float)) else float("nan")
        fat.append({"sigma_y": sy / MPA, "stress_range_mpa": None if ds is None else ds / MPA,
                    "api_life": r_f.get("calculated_life"), "ref_life": n_ref})
        A.add(f"I1 fatigue sy={sy/MPA:.0f}", FT,
              f"疲劳寿命 (Δσ={ds/MPA:.2f} MPa, DNV-RP-C203 曲线 C)" if ds else "疲劳寿命",
              r_f.get("calculated_life"), round(n_ref, 1) if isinstance(ds, (int, float)) else None,
              "cycles", threshold="量级")
    if fat and all(row["api_life"] == 0.0 for row in fat):
        A.find("DEF-5", "HIGH", "疲劳端点恒返回 calculated_life=0.0（S-N 未实现）",
               f"Δσ={fat[0]['stress_range_mpa']:.2f} MPa 时 DNV-RP-C203 曲线 C 参考寿命 "
               f"{fat[0]['ref_life']:.3e} 次（≈{fat[0]['ref_life']/6e6:.0f} 年 @6e6 次/年），"
               f"端点给 0.0；σ_y 三档（235/315/355）扫描寿命全为 0，与 Δσ 无关 → 无 S-N 分支",
               "疲劳寿命列不可用于任何判据；status 文案「需详细分析」掩盖了空实现（应记 STUB）")

    # ── J. 结构分析端点内部一致性 ──────────────────────────────
    AN = "/api/v1/structure/analyze"
    r_an = post(api, AN, hull)
    A.add("J1 utilization", AN, "utilization = max_stress / allowable_stress",
          r_an.get("utilization"),
          round(r_an.get("max_stress", 0) / r_an.get("allowable_stress", 1), 4), "-")
    A.add("J2 allowable stress", AN, "allowable_stress vs 0.6σ_y",
          r_an.get("allowable_stress"), 0.6 * 235e6, "Pa")
    # 疲劳寿命对输入是否真有依赖：板厚 / 屈服 / 排水量三向扫描
    variants = {"t=11mm": {**hull, "plate_thickness": 0.011},
                "sy=355": {**hull, "yield_stress": 355e6},
                "disp=150kt": {**hull, "displacement": 150000.0}}
    lives = {}
    for tag, body in variants.items():
        rv = post(api, AN, body)
        lives[tag] = rv.get("fatigue_life")
    base_life = r_an.get("fatigue_life")
    n_var = sum(1 for v in lives.values() if v is not None and base_life is not None
                and abs(v - base_life) > 1e-9)
    A.checks.append({
        "id": "J3 fatigue_life sensitivity", "endpoint": AN,
        "desc": "fatigue_life 应对 t/σ_y/Δ 至少一项敏感（扫描 3 项）",
        "api": n_var, "ref": ">=1", "unit": "variable responses",
        "rel_err": None, "verdict": "PASS" if n_var >= 1 else "FAIL",
        "threshold": "契约", "note": str(lives), "x": None})
    if n_var == 0:
        A.find("DEF-6", "MEDIUM", "结构分析端点 fatigue_life 对输入不敏感（常数）",
               f"t=11 mm / σ_y=355 / Δ=150 kt 三种扰动下 fatigue_life 恒为 {base_life}",
               "该列实为固定文案值，不宜作为寿命判据")

    return A


def figures(A: Audit, outdir: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(outdir, exist_ok=True)
    figs = []
    byid = {c["id"]: c["api"] for c in A.checks}

    def pts(prefix):
        out = []
        for c in A.checks:
            if c["id"].startswith(prefix) and isinstance(c["api"], (int, float)) \
                    and c["x"] is not None:
                out.append((c["x"], c["api"]))
        return sorted(out)

    # 图1：σ_cr(a/b) 与 τ_cr(a/b) 闭式 vs API
    ratios = np.linspace(0.4, 4.0, 73)
    sig_curve = [ref.sigma_cr(max(ref.k_uniaxial_simply_supported(r, 1.0)[0], 4.0),
                              ref.E_STEEL, 0.01, 1.0, ref.NU_STEEL) / MPA for r in ratios]
    tau_curve = [ref.tau_cr(r, 1.0, ref.E_STEEL, 0.01, ref.NU_STEEL) / MPA for r in ratios]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].plot(ratios, sig_curve, "k-", lw=1.6, label="closed form, Timoshenko k(a/b)")
    p = pts("A1")
    if p:
        ax[0].plot(*zip(*p), "ro", ms=6, label="API /plate-stability")
    ax[0].set_xlabel("a/b"); ax[0].set_ylabel(r"$\sigma_{cr}$ [MPa]")
    ax[0].set_title("Uniaxial buckling, simply supported (t=10 mm, E=206 GPa)")
    ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)
    ax[1].plot(ratios, tau_curve, "k-", lw=1.6, label=r"closed form $k_s=5.34+4(b/a)^2$")
    p = pts("C1")
    if p:
        ax[1].plot(*zip(*p), "b^", ms=7, label="API /plate-stability")
    ax[1].set_xlabel("a/b"); ax[1].set_ylabel(r"$\tau_{cr}$ [MPa]")
    ax[1].set_title("Shear buckling, simply supported")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8)
    fig.tight_layout()
    f1 = os.path.join(outdir, "fig1_plate_buckling_closedform_vs_api.png")
    fig.savefig(f1, dpi=140); plt.close(fig); figs.append(f1)

    # 图2：Johnson-Ostenfeld 与板厚扫描
    ts = np.linspace(0.005, 0.045, 80)
    s_e = np.array([ref.sigma_cr(4.0, ref.E_STEEL, t, 1.0, ref.NU_STEEL) / MPA for t in ts])
    s_u = np.array([ref.johnson_ostenfeld(s * MPA, 235e6) / MPA for s in s_e])
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    ax.plot(ts * 1000, s_e, "k-", label=r"elastic $\sigma_{cr}\propto t^{2}$")
    ax.plot(ts * 1000, s_u, "g--", label=r"Johnson-Ostenfeld $\sigma_u$")
    pe, pu = pts("D1"), pts("D2")
    if pe:
        ax.plot(*zip(*pe), "ro", ms=5, label="API elastic σ_cr")
    if pu:
        ax.plot(*zip(*pu), "b^", ms=5, label="API σ_u")
    ax.axhline(235, color="gray", ls=":", lw=1)
    ax.set_xlabel("plate thickness [mm]"); ax.set_ylabel("stress [MPa]")
    ax.set_title(r"Plate buckling + yield interaction ($\sigma_y$=235 MPa, b=1 m)")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout()
    f2 = os.path.join(outdir, "fig2_johnson_ostenfeld_thickness_sweep.png")
    fig.savefig(f2, dpi=140); plt.close(fig); figs.append(f2)

    # 图3：偏差排名（全 ASCII 标签，避免 CJK 缺字）
    rows = [c for c in A.checks if isinstance(c.get("rel_err"), (int, float))]
    rows = sorted(rows, key=lambda c: -c["rel_err"])[:22]
    labels = [re.sub(r"[^\x20-\x7e]", "", c["id"]).strip() or c["endpoint"] for c in rows]
    vals = [max(c["rel_err"], 1e-7) for c in rows]
    colors = ["#2ca02c" if c["verdict"] == "PASS" else
              ("#ff7f0e" if c["verdict"] == "WARN" else "#d62728") for c in rows]
    fig, ax = plt.subplots(figsize=(8.6, 6.2))
    ax.barh(labels[::-1], vals[::-1], color=colors[::-1])
    ax.axvline(PASS_TOL, color="k", ls=":", lw=1, label="3% gate")
    ax.axvline(FAIL_TOL, color="r", ls="--", lw=1, label="15% unacceptable")
    ax.set_xscale("log"); ax.set_xlabel("relative error vs closed form")
    ax.set_title("m1578 structure-domain audit: worst deviations")
    ax.legend(fontsize=8); ax.grid(alpha=.3, axis="x")
    fig.tight_layout()
    f3 = os.path.join(outdir, "fig3_error_ranking.png")
    fig.savefig(f3, dpi=140); plt.close(fig); figs.append(f3)

    # 图4：船体级端点 vs 解析（表明 b=3s 假设造成的 9 倍偏差）
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    s = np.linspace(0.6, 6.0, 60)
    ax.plot(s, [ref.panel_critical_stress(x, x, 0.022)["sigma_cr"] / MPA for x in s],
            "k-", label="closed form, panel width b = s")
    ax.plot(s, [ref.panel_critical_stress(3 * x, 3 * x, 0.022)["sigma_cr"] / MPA for x in s],
            "b--", label="same form with b = 3s (endpoint hypothesis)")
    api_h = byid.get("H1 hull sigma_cr")
    if isinstance(api_h, (int, float)):
        ax.plot([4.0], [api_h], "r*", ms=16, label=f"API at s=4 m: {api_h} MPa")
    ax.set_xlabel("frame spacing / stiffener spacing s [m]")
    ax.set_ylabel(r"$\sigma_{cr}$ [MPa]")
    ax.set_title("Hull-level panel buckling endpoint (t=22 mm)")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout()
    f4 = os.path.join(outdir, "fig4_hull_panel_implied_width.png")
    fig.savefig(f4, dpi=140); plt.close(fig); figs.append(f4)

    return figs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8001")
    ap.add_argument("--out", default="artifacts/m1578")
    args = ap.parse_args()

    t0 = time.time()
    A = run(args.api)
    figs = figures(A, args.out)

    summary = {"PASS": 0, "WARN": 0, "FAIL": 0, "N/A": 0}
    for c in A.checks:
        summary[c["verdict"]] = summary.get(c["verdict"], 0) + 1
    payload = {
        "meta": {
            "mission": "m1578",
            "title": "结构域端点独立解析复核（板格屈曲 / 弹塑性极限强度 / 疲劳）",
            "api": args.api,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "runtime_s": round(time.time() - t0, 2),
            "reference": "Timoshenko&Gere 1961 Ch.9 / DNV-RP-C201 / DNV-RP-C203 Table 2-1 (curve C)",
            "tolerances": {"pass": PASS_TOL, "fail": FAIL_TOL},
            "n_checks": len(A.checks),
            "figures": [os.path.basename(f) for f in figs],
            "fatigue_reference": {
                "log_a1": ref.DNV_C_LOG_A1, "m1": ref.DNV_C_M1,
                "log_a2": ref.DNV_C_LOG_A2, "m2": ref.DNV_C_M2,
                "knee_N": ref.DNV_C_KNEE_N,
                "sigma_at_2e6_MPa": ref.dnv_c_design_stress(2e6),
            },
        },
        "summary": summary,
        "checks": A.checks,
        "findings": A.findings,
    }
    os.makedirs(args.out, exist_ok=True)
    outp = os.path.join(args.out, "struct_audit.json")
    with open(outp, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{'check':34s} {'api':>16s} {'closed-form':>16s} {'rel_err':>10s} verdict")
    for c in A.checks:
        api_v = c["api"] if not isinstance(c["api"], str) else c["api"][:16]
        r = f"{c['rel_err']:.3e}" if isinstance(c["rel_err"], (int, float)) else "—"
        print(f"{c['id']:34s} {str(api_v):>16s} {str(c['ref']):>16s} {r:>10s} {c['verdict']}")
    print(f"\nsummary: {summary}")
    for f_ in A.findings:
        print(f"[{f_['id']}][{f_['severity']}] {f_['title']}")
    print(f"\nwrote {outp} + {len(figs)} figures in {payload['meta']['runtime_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())