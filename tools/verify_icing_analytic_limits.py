#!/usr/bin/env python3
"""m1715 · W3-N09 结冰链（ship_performance/icing）解析极限与第一性不变量复核。

验证欠账：backend/services/ship_performance/icing/（2214 行，已合并）此前只有
集成门测试（test_ice_integration_gate.py），没有任何物理不变量测试。本脚本用
闭式解/第一性定律独立复核，不改任何生产代码：

  A 组 物性锚点（文献值）
    A1 UNESCO(1978) 海水冰点 S=35 psu → -1.91 °C（标准参考值）
    A2 Pierson-Moskowitz  Hs = 0.21·U10²/g
    A3 潜热 L_f(0 °C) ≈ 333.5 kJ/kg
    A4 冰密度 917 kg/m³（海冰典型值）
  B 组 链不变量（机器精度）
    B1 零喷流 → dry_nospray，ice_flux=0，质量/径流恒 0
    B2 逐面板质量守恒  runoff = spray − ice（逐步）
    B3 区制逻辑  dry: λ=1 且 q_avail ≥ m·L_f+q_spray；wet: λ=(q_avail−q_spray)/(m·L_f)∈[0,1]
    B4 第一定律  耗热 = ice_flux·L_f + q_spray_used ≤ q_avail（未截断 wet 取等号）
    B5 厚度一致性  t = m/(ρ_ice·A)
    B6 总质量账本  integrate_icing 终态质量 == 历史账本末值，且单调不减
    B7 导热极限  q_avail 随冰厚单调不增，t→∞ 时 →0
    B8 喷流闭式  m_w = E·W_ref·√(Hs/H_ref)·U·(n·u)（迎风面板 n·u=1；背风=0）
    B9 单调性  迎风喷流随风速单调不降
输出 artifacts/m1715/icing_analytic_limits.json + 1 张 png（3 联）。
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.ship_performance.icing.accumulation import (  # noqa: E402
    PanelIceState,
    integrate_icing,
    step_icing,
    _effective_available_flux,
)
from services.ship_performance.icing.heat_balance import transfer_coefficients  # noqa: E402
from services.ship_performance.icing.mesh import Panel  # noqa: E402
from services.ship_performance.icing.properties import (  # noqa: E402
    RHO_ICE,
    AirState,
    WaterState,
    latent_heat_fusion,
    seawater_freezing_point,
)
from services.ship_performance.icing.spray import (  # noqa: E402
    SprayCondition,
    significant_wave_height,
)

G = 9.80665
OUT = os.path.join(os.path.dirname(__file__), "..", "artifacts", "m1715")
os.makedirs(OUT, exist_ok=True)

results: list[dict] = []


def check(key: str, name: str, got, ref, tol: float, unit: str = "") -> bool:
    err = abs(got - ref) / max(abs(ref), 1e-30)
    ok = err <= tol
    results.append({"key": key, "name": name, "got": got, "ref": ref,
                    "rel_err": err, "tol": tol, "unit": unit, "pass": ok})
    print(f"  [{'PASS' if ok else 'FAIL'}] {key} {name}: got={got:.6g} "
          f"ref={ref:.6g} rel={err:.3e} {unit}")
    return ok


def invariant(key: str, name: str, cond: bool, detail: str = "") -> bool:
    cond = bool(cond)
    results.append({"key": key, "name": name, "got": cond, "ref": True,
                    "rel_err": 0.0 if cond else 1.0, "tol": 0.0, "unit": "",
                    "pass": cond, "detail": detail})
    print(f"  [{'PASS' if cond else 'FAIL'}] {key} {name} {detail}")
    return cond


# ---------------------------------------------------------------------------
# A 组：物性锚点
# ---------------------------------------------------------------------------
print("== A 物性锚点 ==")
t_f35 = seawater_freezing_point(35.0) - 273.15
check("A1", "UNESCO 海水冰点 S=35psu", t_f35, -1.91, 0.02, "degC")
for u in (5.0, 10.0, 15.0, 20.0):
    check(f"A2_U{u:g}", f"PM 谱 Hs(U={u:g})", significant_wave_height(u),
          0.21 * u * u / G, 1e-9, "m")
check("A3", "潜热 L_f(0degC)", latent_heat_fusion(273.15) / 1e3, 333.5, 0.02, "kJ/kg")
check("A4", "冰密度", RHO_ICE, 917.0, 0.02, "kg/m3")

# ---------------------------------------------------------------------------
# 场景与面板
# ---------------------------------------------------------------------------
air_dry = AirState(t_air=258.15, rel_humidity=0.80)          # -15 C
water_dry = WaterState(t_water=275.15, salinity=33.0)        # +2 C
spray_dry = SprayCondition(wind_speed=10.0, wind_dir=(0.0, -1.0, 0.0))

air_wet = AirState(t_air=271.15, rel_humidity=0.90)          # -2 C
water_wet = WaterState(t_water=277.15, salinity=33.0)        # +4 C
spray_wet = SprayCondition(wind_speed=25.0, wind_dir=(0.0, -1.0, 0.0))

panels = [
    Panel(name="p_windward", zone="windward", area=10.0,
          centroid=(0.0, 0.0, 5.0), normal=(0.0, 1.0, 0.0), char_length=5.0),
    Panel(name="p_side", zone="side", area=8.0,
          centroid=(0.0, 2.0, 5.0), normal=(1.0, 0.0, 0.0), char_length=5.0),
    Panel(name="p_deck", zone="horizontal_up", area=12.0,
          centroid=(0.0, 0.0, 8.0), normal=(0.0, 0.0, 1.0), char_length=4.0),
]


def precompute(states, air, water, spray):
    """Step 前逐面板独立重算 (m_spray, q_avail, L_f, q_spray)。"""
    pre = []
    for s in states:
        coeffs = transfer_coefficients(
            air, spray.wind_speed, s.panel.char_length,
            orientation=("vertical" if abs(s.panel.normal[2]) < 0.85
                       else "horizontal_up"))
        m_spray = spray.panel_flux(s.panel.zone, s.panel.normal)
        q_avail, _ = _effective_available_flux(air, water, coeffs, s.thickness,
                                               k_ice=2.2, view_factor_to_sky=0.5)
        l_f = latent_heat_fusion(water.t_freezing)
        q_spray = m_spray * water.specific_heat * max(
            water.t_water - water.t_freezing, 0.0)
        pre.append((s, m_spray, q_avail, l_f, q_spray))
    return pre


def run_scenario(tag, air, water, spray, dt=600.0, steps=10):
    states = [PanelIceState(panel=p) for p in panels]
    ok2 = ok3 = ok4 = ok5 = True
    for _ in range(steps):
        pre = precompute(states, air, water, spray)
        step_icing(states, air, water, spray, dt)
        for s, m_spray, q_avail, l_f, q_spray in pre:
            bal = s.last_balance
            if abs(bal.runoff_flux - (bal.spray_flux - bal.ice_flux)) > 1e-12:
                ok2 = False
            if m_spray <= 0.0:
                if not (s.regime == "dry_nospray" and s.ice_flux == 0.0):
                    ok3 = False
            elif q_avail >= m_spray * l_f + q_spray:
                if not (s.regime == "dry"
                        and abs(s.freezing_fraction - 1.0) < 1e-12):
                    ok3 = False
                if m_spray * l_f + q_spray > q_avail + 1e-9:
                    ok4 = False
            else:
                lam_ref = max(min((q_avail - q_spray)
                                  / max(m_spray * l_f, 1e-30), 1.0), 0.0)
                if not (s.regime == "wet"
                        and abs(s.freezing_fraction - lam_ref) < 1e-9):
                    ok3 = False
                q_used = s.ice_flux * l_f + q_spray
                if q_used > q_avail + 1e-6 * max(q_avail, 1.0):
                    ok4 = False
                if q_avail > q_spray and \
                        abs(q_used - q_avail) > 1e-6 * max(q_avail, 1.0):
                    ok4 = False
            if abs(s.thickness - s.mass / (RHO_ICE * s.panel.area)) > 1e-12:
                ok5 = False
    return states, ok2, ok3, ok4, ok5


print("== B2-B5 逐步不变量（干场景）==")
st_dry, ok2a, ok3a, ok4a, ok5a = run_scenario("dry", air_dry, water_dry, spray_dry)
invariant("B2a", "干场景 runoff=spray−ice", ok2a)
invariant("B3a", "干场景区制/λ 逻辑", ok3a)
invariant("B4a", "干场景第一定律", ok4a)
invariant("B5a", "干场景厚度一致性", ok5a)

print("== B2-B5 逐步不变量（湿场景）==")
st_wet, ok2b, ok3b, ok4b, ok5b = run_scenario("wet", air_wet, water_wet, spray_wet)
invariant("B2b", "湿场景 runoff=spray−ice", ok2b)
invariant("B3b", "湿场景区制/λ 逻辑", ok3b)
invariant("B4b", "湿场景第一定律", ok4b)
invariant("B5b", "湿场景厚度一致性", ok5b)
invariant("B3c", "湿场景确实进入 wet 区制",
          any(s.regime == "wet" for s in st_wet),
          detail=f"regimes={[s.regime for s in st_wet]}")
invariant("B3d", "干场景确实进入 dry 区制",
          any(s.regime == "dry" for s in st_dry),
          detail=f"regimes={[s.regime for s in st_dry]}")

# ---------------------------------------------------------------------------
# B1 零喷流
# ---------------------------------------------------------------------------
print("== B1 零喷流极限 ==")
spray_zero = SprayCondition(wind_speed=0.0, wind_dir=(0.0, -1.0, 0.0))
states0 = [PanelIceState(panel=p) for p in panels]
diag0 = step_icing(states0, air_dry, water_dry, spray_zero, dt=600.0)
invariant("B1a", "零喷流 regime=dry_nospray",
          all(s.regime == "dry_nospray" for s in states0))
invariant("B1b", "零喷流 ice_flux=0 且质量=0",
          all(s.ice_flux == 0.0 and s.mass == 0.0 for s in states0))
invariant("B1c", "零喷流径流=0", diag0["runoff_mass_kg"] == 0.0)

# ---------------------------------------------------------------------------
# B6 总质量账本
# ---------------------------------------------------------------------------
print("== B6 总质量账本 ==")
states_i, hist = integrate_icing(panels, air_wet, water_wet, spray_wet,
                                 duration_s=3600.0, dt_s=600.0)
m_final = sum(s.mass for s in states_i)
m_hist = hist.total_mass[-1]
check("B6", "integrate_icing 终态质量 vs 历史账本", m_final, m_hist, 1e-9, "kg")
mono = all(b >= a - 1e-12 for a, b in zip(hist.total_mass, hist.total_mass[1:]))
invariant("B6b", "总质量单调不减", mono)

# ---------------------------------------------------------------------------
# B7 导热极限
# ---------------------------------------------------------------------------
print("== B7 导热极限 ==")
coeffs = transfer_coefficients(air_dry, spray_dry.wind_speed, 5.0,
                               orientation="vertical")
qs = []
for t in (0.0, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5):
    q, _ = _effective_available_flux(air_dry, water_dry, coeffs, t,
                                     k_ice=2.2, view_factor_to_sky=0.5)
    qs.append(q)
mono_dec = all(b <= a + 1e-9 for a, b in zip(qs, qs[1:]))
invariant("B7a", "q_avail 随冰厚单调不增", mono_dec,
          detail=f"q={['%.4g' % q for q in qs]}")
invariant("B7b", "t→∞ 时 q_avail→0", qs[-1] < 0.05 * qs[0])

# ---------------------------------------------------------------------------
# B8 喷流闭式
# ---------------------------------------------------------------------------
print("== B8 喷流闭式 ==")
u = spray_wet.wind_speed
hs = significant_wave_height(u)
w_eff = 2.0e-4 * math.sqrt(hs / 2.0)
m_closed = 1.0 * w_eff * u * 1.0  # E=1 (windward), n·u=1
m_api = spray_wet.panel_flux("windward", (0.0, 1.0, 0.0))
check("B8", "迎风面板喷流闭式", m_api, m_closed, 1e-9, "kg/m2/s")
m_leeward = spray_wet.panel_flux("leeward", (0.0, -1.0, 0.0))
invariant("B8b", "背风面板捕获=0", m_leeward == 0.0, detail=f"got={m_leeward}")

# ---------------------------------------------------------------------------
# B9 单调性
# ---------------------------------------------------------------------------
print("== B9 单调性 ==")
fluxes = []
for u in (5.0, 10.0, 15.0, 20.0, 25.0):
    sp = SprayCondition(wind_speed=u, wind_dir=(0.0, -1.0, 0.0))
    fluxes.append(sp.panel_flux("windward", (0.0, 1.0, 0.0)))
invariant("B9", "喷流随风速单调不降",
          all(b >= a - 1e-15 for a, b in zip(fluxes, fluxes[1:])),
          detail=f"fluxes={['%.3g' % f for f in fluxes]}")

# ---------------------------------------------------------------------------
# 汇总 + 图
# ---------------------------------------------------------------------------
n_pass = sum(1 for r in results if r["pass"])
n_fail = len(results) - n_pass
summary = {
    "mission": 1715,
    "target": "backend/services/ship_performance/icing (W3-N09, merged)",
    "n_checks": len(results), "n_pass": n_pass, "n_fail": n_fail,
    "checks": results,
    "scenarios": {
        "dry": {"air_c": -15.0, "water_c": 2.0, "U10": 10.0,
                "regimes": [s.regime for s in st_dry],
                "mass_kg": [s.mass for s in st_dry]},
        "wet": {"air_c": -2.0, "water_c": 4.0, "U10": 25.0,
                "regimes": [s.regime for s in st_wet],
                "mass_kg": [s.mass for s in st_wet]},
        "integrated_1h": {"mass_kg": m_final,
                          "history_points": len(hist.total_mass)},
}
with open(os.path.join(OUT, "icing_analytic_limits.json"), "w") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 3, figsize=(13, 3.6))
    t_h = [t / 3600.0 for t in hist.time_s]
    ax[0].plot(t_h, [m / 1000.0 for m in hist.total_mass], "b-", lw=2)
    ax[0].set_xlabel("t [h]"); ax[0].set_ylabel("ice mass [kg]")
    ax[0].set_title("1 h integration (wet scenario)")
    ax[0].grid(alpha=0.3)
    ax[1].plot([0, 1e-3, 5e-3, 0.01, 0.05, 0.1, 0.5], qs, "r-o", lw=2)
    ax[1].set_xscale("log"); ax[1].set_yscale("log")
    ax[1].set_xlabel("ice thickness [m]"); ax[1].set_ylabel("q_avail [W/m2]")
    ax[1].set_title("conduction-limited heat sink"); ax[1].grid(alpha=0.3, which="both")
    us = [5, 10, 15, 20, 25, 30]
    mf = [SprayCondition(wind_speed=u, wind_dir=(0.0, -1.0, 0.0))
           .panel_flux("windward", (0.0, 1.0, 0.0)) for u in us]
    ax[2].plot(us, mf, "g-o", lw=2)
    ax[2].set_xlabel("U10 [m/s]"); ax[2].set_ylabel("spray flux [kg/m2/s]")
    ax[2].set_title("windward spray flux (closed form)"); ax[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "icing_analytic_limits.png"), dpi=140)
    print("figure written")
except Exception as exc:  # pragma: no cover
    print("figure skipped:", exc)

print(f"\nTOTAL: {n_pass}/{len(results)} pass, {n_fail} fail")
sys.exit(1 if n_fail else 0)
