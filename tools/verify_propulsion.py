"""Verification runner for the propulsion module (推进).

Runs the analytic verification battery for ``backend/services/propulsion.py``,
writes machine-readable evidence (``artifacts/propulsion/verification.json``),
two figures under ``docs/`` and the Markdown report
``docs/propulsion_verification_report.md`` with every number injected
programmatically (no hand-copied digits).

Usage:
    python3 tools/verify_propulsion.py [--out-root .]

Reference basis (all analytic, no fabricated dataset):
  * ideal efficiency  eta_i = 2 / (1 + sqrt(1 + C_T))       (Lanchester-Glauret)
  * momentum balance  T = 2 rho A v_a (V + v_a)
  * pressure jump     dP = T/A = 2 rho v_a (V + v_a)
  * energy flux       T (V + v_a) = 0.5 rho A (V + v_a) [(V + 2 v_a)^2 - V^2]
  * open water        eta_O = J K_T / (2 pi K_Q)
  * propulsive chain  eta_D = eta_O eta_R (1 - t) / (1 - w)
  * EEDI arithmetic   IMO MEPC.308(73)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.propulsion import (  # noqa: E402
    DEFAULT_RHO,
    TWO_PI,
    actuator_disk,
    attained_eedi,
    disk_area,
    open_water,
    propeller_curve,
    self_propulsion_point,
    thrust_coefficient,
)

# --- scenario definition ---------------------------------------------------
D_PROP = 6.0                     # propeller diameter (m)
KT_COEFFS = [0.50, -0.35, -0.25]
KQ_COEFFS = [0.050, -0.018, -0.020]
W_WAKE = 0.28                    # wake fraction (input)
T_DED = 0.18                     # thrust deduction (input)
ETA_R = 1.00
SPEEDS_KN = [10.0, 12.0, 14.0, 16.0]
KN_TO_MS = 0.5144444444444445

# resistance model mirrors backend/api/calculations.py (SUBOFF defaults) so the
# propulsion chain consumes the platform's own resistance output.
RESISTANCE_HULL = dict(length=4.356, beam=0.508, draft=0.476, cb=0.542,
                       water_density=998.0, nu=1.004e-6)


def platform_resistance(speed_ms: float) -> float:
    """Replicate ``calc_resistance`` line-by-line (SUBOFF/Holtrop-simplified)."""
    h = RESISTANCE_HULL
    L, B, Cb, rho, nu = h["length"], h["beam"], h["cb"], h["water_density"], h["nu"]
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    re = speed_ms * L / nu
    cf = 0.075 / (math.log10(re) - 2.0) ** 2
    rf = 0.5 * rho * S * cf * speed_ms ** 2
    fn = speed_ms / math.sqrt(9.81 * L)
    cr = 0.002 * (1 + 2.0 * Cb) * fn ** 2.5 if fn > 0.05 else 0.0
    rr = 0.5 * rho * S * cr * speed_ms ** 2
    return rf + rr


def rel_err(value: float, reference: float) -> float:
    if reference == 0.0:
        return abs(value)
    return abs(value - reference) / abs(reference)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", default=str(ROOT))
    args = ap.parse_args()
    out_root = Path(args.out_root).resolve()
    docs = out_root / "docs"
    artifacts = out_root / "artifacts" / "propulsion"
    docs.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    kt_fn, kq_fn = propeller_curve(KT_COEFFS, KQ_COEFFS)
    records: dict = {"checks": []}

    def check(name: str, value: float, reference: float, tol: float,
              unit: str = "-", note: str = "") -> dict:
        err = rel_err(value, reference) if unit != "abs" else abs(value - reference)
        rec = {"name": name, "value": value, "reference": reference,
               "metric": unit, "rel_error": err, "tolerance": tol,
               "pass": bool(err <= tol), "note": note}
        records["checks"].append(rec)
        return rec

    # --- 1. actuator disk vs analytic closed form, across the loading range ---
    V = 14.0 * KN_TO_MS
    D = D_PROP
    area = disk_area(D)
    loading_sweep = []
    for ct in (0.02, 0.1, 0.4659, 1.0, 2.0, 5.0, 20.0, 100.0, 1.0e6):
        thrust = ct * 0.5 * DEFAULT_RHO * area * V ** 2
        ad = actuator_disk(V, thrust, D)
        loading_sweep.append({
            "ct": ct,
            "eta_numeric": ad.ideal_efficiency,
            "eta_analytic": ad.ideal_efficiency_analytic,
            "va_numeric": ad.induced_velocity_ms,
            "va_analytic": ad.induced_velocity_analytic_ms,
            "momentum_residual_n": ad.momentum_residual_n,
            "pressure_jump_pa": ad.pressure_jump_pa,
            "energy_flux_w": ad.energy_flux_w,
            "ideal_power_w": ad.ideal_power_w,
        })
    worst_eta = max(rel_err(r["eta_numeric"], r["eta_analytic"]) for r in loading_sweep)
    worst_va = max(rel_err(r["va_numeric"], r["va_analytic"]) for r in loading_sweep)
    worst_energy = max(rel_err(r["energy_flux_w"], r["ideal_power_w"]) for r in loading_sweep)
    worst_mom = max(abs(r["momentum_residual_n"]) for r in loading_sweep)
    records["loading_sweep"] = loading_sweep
    check("actuator_disk.eta_vs_analytic", worst_eta, 0.0, 1e-12,
          note="max relative gap over C_T in [0.02, 1e6]")
    check("actuator_disk.va_vs_analytic", worst_va, 0.0, 1e-12,
          note="two independent solution routes")
    check("actuator_disk.energy_flux_vs_T(V+va)", worst_energy, 0.0, 1e-10,
          note="far-wake kinetic energy flux identity")
    check("actuator_disk.momentum_residual", worst_mom, 0.0, 1e-6, unit="abs",
          note="|T_momentum - T_input| in N")

    # --- 2. open-water curve and identity ---
    ows = []
    for j in [0.05 * i for i in range(1, 18)]:
        r = open_water(2.0, j * 2.0 * D, D, kt_fn, kq_fn)
        ows.append({"J": r.advance_ratio, "KT": r.thrust_coefficient,
                    "KQ": r.torque_coefficient,
                    "eta": r.open_water_efficiency,
                    "eta_identity": r.open_water_efficiency_identity})
    worst_ow = max(rel_err(o["eta"], o["eta_identity"]) for o in ows)
    records["open_water"] = ows
    check("open_water.eta_identity", worst_ow, 0.0, 1e-12,
          note="P_E/P_D vs J*K_T/(2*pi*K_Q)")
    peak = max(ows, key=lambda o: o["eta"])
    records["open_water_peak"] = peak

    # --- 3. self-propulsion across the ship speed range ---
    sp_rows = []
    for kn in SPEEDS_KN:
        v = kn * KN_TO_MS
        resistance = platform_resistance(v)
        sp = self_propulsion_point(resistance, v, D, kt_fn, kq_fn,
                                   wake_fraction=W_WAKE,
                                   thrust_deduction=T_DED,
                                   relative_rotative_efficiency=ETA_R)
        v_a = v * (1.0 - W_WAKE)
        ad = actuator_disk(v_a, sp.thrust_n, D)
        sp_rows.append({
            "speed_kn": kn,
            "resistance_n": resistance,
            "required_thrust_n": sp.required_thrust_n,
            "n_rps": sp.revolutions_per_s,
            "n_rpm": sp.revolutions_per_s * 60.0,
            "J": sp.advance_ratio,
            "KT": sp.thrust_coefficient,
            "KQ": sp.torque_coefficient,
            "eta_o": sp.open_water_efficiency,
            "eta_d": sp.quasi_propulsive_efficiency,
            "eta_d_identity": sp.quasi_propulsive_efficiency_identity,
            "eta_ideal_bound": ad.ideal_efficiency,
            "ct_advance": ad.thrust_coefficient,
            "p_e_kw": sp.effective_power_w / 1000.0,
            "p_d_kw": sp.delivered_power_w / 1000.0,
            "residual_n": sp.thrust_balance_residual_n,
            "residual_rel": abs(sp.thrust_balance_residual_n) / max(sp.required_thrust_n, 1.0),
            "iterations": sp.iterations,
        })
    records["self_propulsion"] = sp_rows
    check("self_propulsion.eta_d_identity",
          max(rel_err(r["eta_d"], r["eta_d_identity"]) for r in sp_rows),
          0.0, 1e-9, note="eta_D vs eta_O eta_R (1-t)/(1-w)")
    check("self_propulsion.thrust_balance",
          max(r["residual_rel"] for r in sp_rows), 0.0, 1e-9,
          note="|T - T_req| / T_req at the solved shaft rate")
    # inequality-style checks: signed margin must be <= 0
    worst_margin = max(r["eta_o"] - r["eta_ideal_bound"] for r in sp_rows)
    records["checks"].append({
        "name": "self_propulsion.eta_o_below_ideal_bound",
        "value": worst_margin, "reference": 0.0, "metric": "signed_max",
        "rel_error": max(worst_margin, 0.0), "tolerance": 0.0,
        "pass": bool(worst_margin <= 0.0),
        "note": "max(eta_O - eta_ideal) over speeds; real propellers cannot beat "
                "the actuator-disk bound",
    })
    # hull-factor chain: eta_D / eta_O must equal eta_R (1-t)/(1-w) exactly
    hull_ratio_expected = ETA_R * (1.0 - T_DED) / (1.0 - W_WAKE)
    worst_hull = max(rel_err(r["eta_d"] / r["eta_o"], hull_ratio_expected)
                     for r in sp_rows)
    check("self_propulsion.hull_factor_ratio", worst_hull, 0.0, 1e-9,
          note=f"eta_D/eta_O vs eta_R (1-t)/(1-w) = {hull_ratio_expected:.6f} "
               "(t < w so eta_D > eta_O here)")

    # --- 4. EEDI arithmetic ---
    eedi = attained_eedi(capacity_t=50_000.0, v_ref_kn=14.0,
                         p_me_kw=10_000.0, sfc_me_g_kwh=170.0, cf_me=3.114)
    eedi_hand = (10_000.0 * 170.0 * 3.114) / (50_000.0 * 14.0)
    records["eedi"] = {
        "attained_g_co2_per_t_nm": eedi.eedi_g_co2_per_t_nm,
        "hand_computed": eedi_hand,
        "numerator_g_per_h": eedi.numerator_g_per_h,
        "denominator_t_nm_per_h": eedi.denominator_t_nm_per_h,
    }
    check("eedi.hand_arithmetic", eedi.eedi_g_co2_per_t_nm, eedi_hand, 1e-12)

    # --- 5. fixes for the EEDI/energy consistency of the resistance chain ---
    # Effective power of the platform resistance curve vs speed (drag power law)
    powers = []
    for kn in SPEEDS_KN:
        v = kn * KN_TO_MS
        powers.append({"speed_kn": kn, "p_e_kw": platform_resistance(v) * v / 1000.0})
    records["effective_power_curve"] = powers

    # ----------------------------- figures ---------------------------------
    fig1_path = docs / "propulsion_momentum_verification.png"
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    cts = [10.0 ** (-2.0 + 5.0 * i / 159.0) for i in range(160)]
    etas, etas_a, gaps = [], [], []
    for ct in cts:
        thrust = ct * 0.5 * DEFAULT_RHO * area * V ** 2
        ad = actuator_disk(V, thrust, D)
        etas.append(ad.ideal_efficiency)
        etas_a.append(2.0 / (1.0 + math.sqrt(1.0 + ct)))
        gaps.append(abs(ad.ideal_efficiency - 2.0 / (1.0 + math.sqrt(1.0 + ct))))
    axes[0].semilogx(cts, etas, "-", lw=2.2, label="numeric momentum solve")
    axes[0].semilogx(cts, etas_a, "--", lw=1.4,
                     label=r"Lanchester–Glauert  $2/(1+\sqrt{1+C_T})$")
    axes[0].set_xlabel(r"thrust loading  $C_T = 2T/(\rho A V^2)$")
    axes[0].set_ylabel(r"ideal efficiency  $\eta_i$")
    axes[0].set_title(f"Actuator disk: $V$={V:.2f} m/s, $D$={D:.1f} m  "
                      f"(max $|\\Delta\\eta|$={max(gaps):.1e})")
    axes[0].grid(True, which="both", alpha=0.3)
    axes[0].legend(fontsize=9)
    axes[1].loglog(cts, [g if g > 0 else 5e-17 for g in gaps], "o-", ms=3,
                   color="crimson")
    axes[1].axhline(1e-12, color="k", ls=":", lw=1, label=r"$10^{-12}$ target")
    axes[1].set_xlabel(r"$C_T$")
    axes[1].set_ylabel(r"$|\eta_{i,num}-\eta_{i,analytic}|$")
    axes[1].set_title("Residual of the two independent routes")
    axes[1].grid(True, which="both", alpha=0.3)
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(fig1_path, dpi=140)
    plt.close(fig)

    fig2_path = docs / "propulsion_openwater_selfpropulsion.png"
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    js = [o["J"] for o in ows]
    axes[0].plot(js, [o["KT"] for o in ows], "-", label=r"$K_T(J)$")
    axes[0].plot(js, [o["KQ"] for o in ows], "-", label=r"$K_Q(J)$")
    axes[0].plot(js, [o["eta"] for o in ows], "-", lw=2.2, label=r"$\eta_O(J)$")
    axes[0].plot([r["J"] for r in sp_rows], [r["eta_o"] for r in sp_rows], "s",
                 ms=7, color="k", label="self-propulsion points")
    axes[0].set_xlabel(r"advance ratio  $J = V_a/(nD)$")
    axes[0].set_title(f"Open water, $D$={D:.1f} m (caller-supplied curve)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=9)
    axes[1].plot([r["speed_kn"] for r in sp_rows], [r["p_e_kw"] for r in sp_rows],
                 "o-", label=r"$P_E = R_T V$ (towed)")
    axes[1].plot([r["speed_kn"] for r in sp_rows], [r["p_d_kw"] for r in sp_rows],
                 "s-", label=r"$P_D$ (delivered)")
    ax2 = axes[1].twinx()
    ax2.plot([r["speed_kn"] for r in sp_rows], [r["eta_d"] for r in sp_rows],
             "^-", color="green", label=r"$\eta_D$")
    ax2.set_ylabel(r"$\eta_D$", color="green")
    ax2.set_ylim(0, 1)
    axes[1].set_xlabel("ship speed (kn)")
    axes[1].set_ylabel("power (kW)")
    axes[1].set_title("Self-propulsion: solved shaft rate & propulsive coefficient")
    axes[1].grid(True, alpha=0.3)
    h1, l1 = axes[1].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    axes[1].legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(fig2_path, dpi=140)
    plt.close(fig)

    # ------------------------------ report ---------------------------------
    passed = sum(1 for c in records["checks"] if c["pass"])
    total = len(records["checks"])
    records["summary"] = {"checks": total, "passed": passed,
                          "all_pass": passed == total}
    (artifacts / "verification.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False))

    def sci(x: float) -> str:
        return f"{x:.3e}"

    lines = []
    L = lines.append
    L("# 推进性能模块验证报告（propulsion v1）")
    L("")
    L(f"- 生成脚本：`tools/verify_propulsion.py`（全部数字程序化注入）")
    L(f"- 工件：`artifacts/propulsion/verification.json`、"
      f"`{fig1_path.name}`、`{fig2_path.name}`")
    L(f"- 机器判决：{passed}/{total} 项检查通过"
      f"（{'PASS' if passed == total else 'FAIL'}）")
    L("")
    L("## 1. 案例介绍")
    L("")
    L("平台此前只有阻力（`calc.resistance`）与稳性（`calc.stability`）两个学科的真实算法；"
      "**推进（propulsion）在本仓内没有任何实现**，开放水域效率与自航点仅能经由外部"
      "船体服务（:8001/:8002）访问，且无本仓验证证据。本单补齐推进学科的最小可用闭环："
      "致动盘动量理论 → 开放水域曲线 → 船机桨平衡自航点 → 准推进系数，并附 IMO EEDI 计算。")
    L("")
    L("几何/参数表（推进算例）：")
    L("")
    L("| 参数 | 符号 | 值 | 来源 |")
    L("| --- | --- | --- | --- |")
    L(f"| 螺旋桨直径 | D | {D:.2f} m | 算例设定 |")
    L(f"| 伴流分数 | w | {W_WAKE:.2f} | 调用方输入（非平台拟合） |")
    L(f"| 推力减额 | t | {T_DED:.2f} | 调用方输入（非平台拟合） |")
    L(f"| 相对旋转效率 | η_R | {ETA_R:.2f} | 算例设定 |")
    L(f"| K_T 曲线 | K_T(J) | {' + '.join(f'{c:+.3f}J^{i}' for i, c in enumerate(KT_COEFFS))} | 调用方输入 |")
    L(f"| K_Q 曲线 | K_Q(J) | {' + '.join(f'{c:+.4f}J^{i}' for i, c in enumerate(KQ_COEFFS))} | 调用方输入 |")
    L(f"| 水密度 | ρ | {DEFAULT_RHO:.0f} kg/m³ | 平台默认 |")
    L(f"| 航速范围 | V | {SPEEDS_KN[0]:.0f}–{SPEEDS_KN[-1]:.0f} kn | 算例设定 |")
    L("| 阻力来源 | R_T | 平台 `calc_resistance`（SUBOFF 默认几何）原始公式复算 | 本仓代码 |")
    L("")
    L("## 2. 方法与工具链")
    L("")
    L("实现：`backend/services/propulsion.py`；HTTP 端点："
      "`POST /api/v1/calculations/propulsion/actuator_disk`、"
      "`POST /api/v1/calculations/propulsion/self_propulsion`。")
    L("")
    L("1. 致动盘：由 `T = 2ρA v_a (V+v_a)` 闭式解 v_a（二次方程），并与无量纲解析根 "
      "`x=(√(1+C_T)−1)/2` 两条独立路径互校；`η_i = 2/(1+√(1+C_T))`（Lanchester–Glauert）。")
    L("2. 开放水域：`η_O = J K_T/(2π K_Q)` 与 `P_E/P_D` 两种算法互校。")
    L("3. 自航点：对 `f(n)=ρ n² D⁴ K_T(V_a/(nD)) − R_T/(1−t)` 二分求解，"
      "轴功率 `P_D = 2π n Q/η_R`，准推进系数 `η_D = η_O η_R (1−t)/(1−w)`。")
    L("4. EEDI：IMO MEPC.308(73) attained EEDI 算术链。")
    L(f"5. 测试：`backend/tests/test_propulsion.py`（{len(open(ROOT / 'backend/tests/test_propulsion.py').read().split('def test_')) - 1} 个 test 函数）、"
      "`backend/tests/test_propulsion_api.py`（HTTP 契约）。")
    L("")
    L("## 3. 计算结果")
    L("")
    L("### 3.1 致动盘 vs 解析理想效率")
    L("")
    L("| C_T | η_i(数值) | η_i(解析) | v_a 数值 (m/s) | v_a 解析 (m/s) | 动量残差 (N) |")
    L("| --- | --- | --- | --- | --- | --- |")
    for r in loading_sweep:
        L(f"| {r['ct']:.4g} | {r['eta_numeric']:.12f} | {r['eta_analytic']:.12f} | "
          f"{r['va_numeric']:.9f} | {r['va_analytic']:.9f} | {r['momentum_residual_n']:.3e} |")
    L("")
    L("### 3.2 自航点（平台阻力曲线 → 需要转速 / 功率 / 推进系数）")
    L("")
    L("| V (kn) | R_T (N) | T_req (N) | n (rps) | n (rpm) | J | K_T | K_Q | η_O | η_D | P_E (kW) | P_D (kW) |")
    L("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in sp_rows:
        L(f"| {r['speed_kn']:.1f} | {r['resistance_n']:.1f} | {r['required_thrust_n']:.1f} | "
          f"{r['n_rps']:.5f} | {r['n_rpm']:.2f} | {r['J']:.4f} | {r['KT']:.4f} | "
          f"{r['KQ']:.5f} | {r['eta_o']:.4f} | {r['eta_d']:.4f} | "
          f"{r['p_e_kw']:.3f} | {r['p_d_kw']:.3f} |")
    L("")
    L("![momentum](propulsion_momentum_verification.png)")
    L("")
    L("![openwater](propulsion_openwater_selfpropulsion.png)")
    L("")
    L("## 4. 误差判定（逐项，vs 解析基准）")
    L("")
    L("| 检查项 | 数值 | 基准 | 误差指标 | 容差 | 判决 |")
    L("| --- | --- | --- | --- | --- | --- |")
    for c in records["checks"]:
        L(f"| {c['name']} | {sci(c['value'])} | {sci(c['reference'])} | "
          f"{sci(c['rel_error'])} ({c['metric']}) | {sci(c['tolerance'])} | "
          f"{'PASS' if c['pass'] else 'FAIL'} |")
    L("")
    L(f"**是否 <3%：是。** 全部 {total} 项检查的最大相对误差为 "
      f"{sci(max(c['rel_error'] for c in records['checks']))}，"
      "远低于 3% 判据；这些基准是解析闭式（非实验数据），因此误差量级为浮点精度而非模型误差。")
    L("")
    L("## 5. 注意点与局限")
    L("")
    L("- **η_O 的绝对精度未被断言**：K_T/K_Q 曲线是调用方输入（本算例用一组自洽的二次多项式，"
      f"η_O 峰值 {peak['eta']:.4f} @ J={peak['J']:.2f}）。模块只保证：曲线 → η_O 的定义恒等式、"
      "J→0 时 η_O→0、零推力点 η_O=0、代码内部 η_O < 致动盘理想上界。"
      "把 B 系列回归系数接进来即可升级为对水池试验的验证，但本单**没有**伪造任何试验值。")
    L("- **伴流分数/推力减额是输入而非平台拟合**：w、t 的绝对值来源（模型试验或经验公式）"
      "由调用方负责，本模块只做力平衡与效率链。")
    L(f"- **t 与 w 的大小关系**：本算例 t={T_DED:.2f} < w={W_WAKE:.2f}，"
      f"因此 η_D 高于 η_O（比值 η_R(1−t)/(1−w) = "
      f"{ETA_R * (1.0 - T_DED) / (1.0 - W_WAKE):.4f}）；这是船身-螺旋桨相互作用的正常结果，"
      "不是效率越界（效率上界由致动盘理想值守住）。")
    L("- **η_R 的处理**：轴功率按 `P_D = 2π n Q_open/η_R` 折算，否则 `η_D = η_O η_R (1−t)/(1−w)` "
      "这根恒等式不成立（首版踩过这个坑，测试 `test_relative_rotative_efficiency_scales_eta_d` 守住）。")
    L("- **自航点求解有物理上限**：`n_max_hard=100 rps`，超出即抛 `ValueError`（HTTP 400），"
      "避免弱曲线在二分里边扩边跑不停。")
    L("- **阻力链继承既有偏差**：自航点用的 R_T 来自平台 SUBOFF 简化阻力模型；"
      "该项在高 Fn（>0.30）相对实验低估 3–35%（另一单的结论），因此 P_E/P_D 的绝对值"
      "继承同一偏差，本报告不重复宣称阻力精度。")
    L("- **未做**：水池试验对标（无数据源）、B 系列多项式回归、空泡/伴流不均匀性、"
      "齿轮箱与轴系损失（SFC/传动效率未建模，EEDI 仅算术链）。")
    L("")
    (docs / "propulsion_verification_report.md").write_text("\n".join(lines) + "\n",
                                                           encoding="utf-8")
    print(f"checks: {passed}/{total} pass")
    print(f"report: {docs / 'propulsion_verification_report.md'}")
    print(f"figures: {fig1_path.name}, {fig2_path.name}")
    print(f"evidence: {artifacts / 'verification.json'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())