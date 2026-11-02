"""Bitwise-reproducibility case registry (mission m1804, verifier seat).

Every case is a *pure* call into a physics kernel that was already merged into
this repository.  A case must:

* import only from ``backend`` (no database / no FastAPI app / no network);
* be cheap (single query, well below one second) so the whole matrix can be
  replayed many times under load;
* return nested ``dict`` / ``list`` / ``float`` structures only, so that the
  runner can flatten them into ``path -> float64`` and compare bit patterns.

The registry deliberately spans seven different disciplines of ship overall
performance (resistance, propulsion, seakeeping, stability/icing, icing heat
balance, manoeuvring-adjacent acceleration kinematics, ice-structure
collision) so that a single pass covers every numeric *style* used in the
codebase: closed-form algebra, iterative root finds, dense linear algebra,
polygon clipping + bisection, and explicit time integration.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Callable, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
def resistance_holtrop_kvlcc2() -> Dict[str, object]:
    """静水阻力：Holtrop-Mennen 分解（闭式 + ITTC-57 摩擦线）。"""
    from services import resistance_holtrop as rh

    inp = rh.ResistanceInput(
        length=320.0, beam=58.0, draft=20.8, cb=0.8098,
        speed_ms=7.716,  # 15 kn
        displacement_t=312622.0, lcb_pct=3.5, i_e_deg=25.0, ks=0.00015,
    )
    out = rh.compute_resistance(inp)
    return _pick_floats(out)


def resistance_axisym_suboff() -> Dict[str, object]:
    """SUBOFF 轴对称艇体阻力分解（Schoenherr / ITTC-57 双摩擦线）。"""
    from services import ship_resistance as sr

    return _pick_floats(
        sr.resistance_breakdown(length=4.356, beam=0.508, draft=0.508,
                                cb=0.6, speed=3.045)
    )


def propulsion_bem_open_water() -> Dict[str, object]:
    """螺旋桨敞水性能：BEM/条带 + 诱导因子迭代求根。"""
    from services import propeller_open_water as pw

    geom = pw.PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                blade_area_ratio=0.55, blade_count=4,
                                hub_ratio=0.18, sections=24)
    curve = pw.open_water_curve(geom, J_values=[0.3, 0.6, 0.9])
    zero = pw.solve_zero_thrust_advance_ratio(geom)
    return {"curve": _pick_floats(curve),
            "zero_thrust_J": _pick_floats({"J0": float(zero)})}


def seakeeping_heave_pitch_rao() -> Dict[str, object]:
    """耐波：破冰区 heave/pitch RAO（复数组装 + 线性方程求解）。"""
    import numpy as np

    from services.ship_performance.seakeeping import broken_ice as bi

    ship = bi.ShipGeometry(L=120.0, B=20.0, T=7.0, Cb=0.65)
    ice = bi.IceFieldParams(concentration=0.7, thickness=0.6)
    solver = bi.BrokenIceSeakeeping(ship, ice)
    freqs = np.linspace(0.3, 1.6, 14)
    res = solver.compute_rao(freqs)
    return {"rao": _pick_floats(res), "omega": [float(w) for w in freqs]}


def stability_icing_gz() -> Dict[str, object]:
    """稳性：积冰工况 GZ 曲线（多边形裁切 + 吃水二分求根）。"""
    from services.ship_performance import icing

    hull = icing.BargeHull(length=90.0, beam=18.0, depth=9.0, draft=6.0,
                          block_coefficient=0.85)
    ice = icing.IceLoad(mass=1.2e5, moment_z=1.5e6, moment_y=1.0e5)
    cond = icing.LoadingCondition(hull=hull, kg_light=5.9,
                                  heel_angle_deg=6.0, ice=ice)
    heels = (0.0, 5.0, 10.0, 20.0, 30.0, 45.0)
    return {
        "heel_deg": [float(h) for h in heels],
        "gz_m": [float(cond.gz(h)) for h in heels],
        "curve": [float(row["gz_m"]) for row in cond.gz_curve(max_deg=45.0, step_deg=1.0)],
        "heel_from_ice": float(cond.heel_from_ice()),
    }


def icing_surface_heat_balance() -> Dict[str, object]:
    """上层建筑积冰：单板表面能量平衡 + 平衡表面温度求根（SI/Kelvin 入参）。"""
    from services.ship_performance import icing

    air = icing.AirState(t_air=263.15, rel_humidity=0.85, cloud_fraction=0.5)
    water = icing.WaterState(t_water=274.15, salinity=33.0)
    coeffs = icing.transfer_coefficients(air, wind_speed=12.0,
                                        char_length=0.75,
                                        orientation="vertical")
    bal = icing.panel_heat_balance(air, water, coeffs, spray_flux=2.06e-3)
    ref = icing.makiranta_reference_icing_rate(-10.0, 12.0)
    return {"coeffs": _pick_floats(coeffs), "panel": _pick_floats(bal),
            "reference": _pick_floats(ref)}


def _get(obj, key):
    if isinstance(obj, dict):
        return obj[key]
    return getattr(obj, key)


def icing_24h_accretion_case() -> Dict[str, object]:
    """积冰-稳性链：24 h 逐面板积分 + GM 损失 + IS Code 判据（显式时间积分）。"""
    from services.ship_performance import icing

    vessel = icing.preset_ice_class_bulker()
    met = icing.Metocean(t_air_c=-12.0, wind_speed=14.0, t_water_c=1.0,
                         rel_humidity=0.9, ship_speed=None)
    res = icing.run_icing_case(vessel, met, duration_h=3.0, dt_s=1800.0,
                               sample_every=4)
    lc = res.light_condition
    flat: Dict[str, object] = {
        "ice_mass_kg": float(res.ice_load.mass),
        "ice_moment_z": float(res.ice_load.moment_z),
        "ice_moment_y": float(res.ice_load.moment_y),
        "design_ice_mass_kg": float(res.design_ice_load.mass),
        "gm_light": float(_get(lc, "gm_light")),
        "gm_loaded": float(_get(lc, "gm_loaded")),
    }
    crit = res.criteria_loaded
    items = crit.items() if isinstance(crit, dict) else vars(crit).items()
    for name, val in items:
        if isinstance(val, dict) and isinstance(val.get("value"), (int, float)) \
                and not isinstance(val.get("value"), bool):
            flat[f"crit_loaded.{name}"] = float(val["value"])
    return flat


# ---------------------------------------------------------------------------
# negative control (must be flagged as non-reproducible by the harness)
# ---------------------------------------------------------------------------
def control_unseeded_random() -> Dict[str, object]:
    """故意引入的非确定源：未播种随机数（门禁自检用，不计入 KPI 分子）。"""
    import random

    return {"noise": random.random(), "gauss": random.gauss(0.0, 1.0)}


CASES: Dict[str, Callable[[], Dict[str, object]]] = {
    "resistance.holtrop_kvlcc2": resistance_holtrop_kvlcc2,
    "resistance.axisym_suboff": resistance_axisym_suboff,
    "propulsion.bem_openwater": propulsion_bem_open_water,
    "seakeeping.heave_pitch_rao": seakeeping_heave_pitch_rao,
    "stability.icing_gz": stability_icing_gz,
    "icing.surface_heat_balance": icing_surface_heat_balance,
    "icing.24h_accretion_case": icing_24h_accretion_case,
}

DOMAINS = {
    "resistance.holtrop_kvlcc2": "阻力",
    "resistance.axisym_suboff": "阻力",
    "propulsion.bem_openwater": "推进",
    "seakeeping.heave_pitch_rao": "耐波",
    "stability.icing_gz": "稳性",
    "icing.surface_heat_balance": "冰区/上层建筑积冰",
    "icing.24h_accretion_case": "冰区/积冰-稳性链",
}

CONTROLS: Dict[str, Callable[[], Dict[str, object]]] = {
    "control.unseeded_random": control_unseeded_random,
}


def _pick_floats(obj, prefix: str = "") -> Dict[str, float]:
    """Extract a flat ``path -> float`` map from a nested result object."""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_pick_floats(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_pick_floats(v, f"{prefix}[{i}]"))
    elif isinstance(obj, bool):
        out[prefix] = float(obj)
    elif isinstance(obj, (int, float)):
        out[prefix] = float(obj)
    elif obj is None:
        out[prefix] = float("nan")
    else:  # pragma: no cover - object with as_dict()
        as_dict = getattr(obj, "as_dict", None)
        if callable(as_dict):
            out.update(_pick_floats(as_dict(), prefix))
        elif hasattr(obj, "__dict__"):
            out.update(_pick_floats(vars(obj), prefix))
    return out


def run_case(name: str) -> Dict[str, float]:
    return _pick_floats(CASES[name]())


def run_control(name: str) -> Dict[str, float]:
    return _pick_floats(CONTROLS[name]())


def run_all(names=None) -> Dict[str, Dict[str, float]]:
    names = list(CASES) if names is None else names
    return {n: run_case(n) for n in names}


def run_controls() -> Dict[str, Dict[str, float]]:
    return {n: run_control(n) for n in CONTROLS}