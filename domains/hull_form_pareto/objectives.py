"""Objective and constraint layer of the hull-form Pareto study.

Three design goals (all cast as *minimisation*, the NSGA-II convention):

    f1 = R_total(V, geometry)        [kN]   Holtrop–Mennen 1982/1984 (REAL)
    f2 = -GM(geometry, loading)      [m]    hydrostatics (KB + BM - KG)
    f3 = -V_cargo(geometry)          [10^3 m3]  cargo-hold volume integral

Constraints (``g <= 0`` satisfied, feasibility rule of constrained NSGA-II):

    GM      >= 0.15 m        IS Code 2008 general intact-stability criterion
    T_roll  >= 10 s          roll-period comfort floor (caps GM for wide hulls)
    Cb      <= 0.85          Holtrop–Mennen regression domain
    0.55 <= Cp <= 0.87       prismatic-coefficient domain (Cb = Cp Cm)
    Fn      <= 0.45          wave-resistance regression range
    L/B, B/T within regression domain (bounds already inside; kept explicit)

Each constraint is normalised by its own scale so that the total violation is a
dimensionless, comparable quantity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .hull import (DesignSpace, HullForm, GM_MIN_IMO, T_ROLL_MIN,
                   build_hull, hold_volume_integral, resistance, T_ROLL_MIN as _TR)

# Holtrop–Mennen (1982) regression domain
CB_MAX = 0.85
CP_MIN, CP_MAX = 0.55, 0.87
FN_MAX = 0.45
LB_MIN, LB_MAX = 3.9, 9.5
BT_MIN, BT_MAX = 2.1, 4.0

OBJECTIVE_LABELS = (
    "R_total [kN] (min)",
    "-GM [m] (min ≡ GM max)",
    "-V_cargo [10^3 m3] (min ≡ capacity max)",
)


@dataclass
class Evaluation:
    x: np.ndarray
    parameters: Dict[str, float]
    hull: HullForm
    resistance: Dict[str, float]
    objectives: np.ndarray          # (3,), minimised, engineering units
    constraints: Dict[str, float]   # g_i <= 0 satisfied
    violation: float                # sum of positive violations
    feasible: bool
    diagnostics: Dict[str, float]

    def to_row(self) -> Dict[str, object]:
        return {
            "x": [float(v) for v in self.x],
            "parameters": dict(self.parameters),
            "objectives": [float(v) for v in self.objectives],
            "resistance_kn": float(self.resistance["rt_kn"]),
            "gm_m": float(self.hull.gm),
            "cargo_volume_m3": float(self.hull.cargo_volume),
            "constraint_violation": float(self.violation),
            "feasible": bool(self.feasible),
            "constraints": {k: float(v) for k, v in self.constraints.items()},
            "hull": {k: (float(v) if isinstance(v, (int, float)) else v)
                     for k, v in self.hull.as_dict().items()},
            "resistance": {k: (float(v) if isinstance(v, (int, float)) else v)
                           for k, v in self.resistance.items()},
            "diagnostics": {k: float(v) for k, v in self.diagnostics.items()},
        }


def constraints_of(hull: HullForm) -> Dict[str, float]:
    return {
        "gm_min": (GM_MIN_IMO - hull.gm) / GM_MIN_IMO,
        "roll_period_min": (T_ROLL_MIN - hull.roll_period) / T_ROLL_MIN,
        "cb_max": (hull.cb - CB_MAX) / CB_MAX,
        "cp_max": (hull.cp - CP_MAX) / CP_MAX,
        "cp_min": (CP_MIN - hull.cp) / CP_MIN,
        "froude_max": (hull.froude - FN_MAX) / FN_MAX,
        "l_over_b_max": (hull.length / hull.beam - LB_MAX) / LB_MAX,
        "l_over_b_min": (LB_MIN - hull.length / hull.beam) / LB_MIN,
        "b_over_t_max": (hull.beam / hull.draft - BT_MAX) / BT_MAX,
        "b_over_t_min": (BT_MIN - hull.beam / hull.draft) / BT_MIN,
    }


def evaluate(x: Sequence[float], space: Optional[DesignSpace] = None) -> Evaluation:
    """Evaluate one design point: geometry, hydrostatics, resistance, constraints."""
    space = space or DesignSpaceHolder.get()
    x = np.asarray(x, dtype=float)
    hull = build_hull(x, space)
    res = resistance(hull)
    cons = constraints_of(hull)
    violation = float(sum(max(0.0, v) for v in cons.values()))
    objectives = np.array([res["rt_kn"], -hull.gm, -hull.cargo_volume / 1000.0])
    diagnostics = {
        "displacement_t": hull.displacement_t,
        "wetted_surface_m2": hull.wetted_surface,
        "effective_power_mw": res["effective_power_mw"],
        "kb_m": hull.kb,
        "bm_m": hull.bm,
        "kg_m": hull.kg,
        "roll_period_s": hull.roll_period,
        "parallel_body_fraction": hull.parallel_body_fraction,
        "section_integral_area": hold_volume_integral(hull)["contained_section_area"],
        "n_holtrop_warnings": float(len(res["warnings"])),
    }
    return Evaluation(x=x, parameters=space.decode(x), hull=hull, resistance=res,
                      objectives=objectives, constraints=cons,
                      violation=violation, feasible=violation <= 0.0,
                      diagnostics=diagnostics)


class DesignSpaceHolder:
    """Tiny cache so the JSON design space is parsed once per process."""

    _space: Optional[DesignSpace] = None

    @classmethod
    def get(cls) -> DesignSpace:
        if cls._space is None:
            from .hull import load_design_space
            cls._space = load_design_space()
        return cls._space

    @classmethod
    def reset(cls) -> None:
        cls._space = None


def objective_vector(x: Sequence[float], space: Optional[DesignSpace] = None) -> np.ndarray:
    """Vectorised-friendly objective callable for the generic NSGA-II."""
    return evaluate(x, space).objectives


def constraint_vector(x: Sequence[float], space: Optional[DesignSpace] = None) -> np.ndarray:
    """Constraint callable for the generic NSGA-II (``g <= 0`` feasible)."""
    return np.array(list(constraints_of(build_hull(x, space or DesignSpaceHolder.get())).values()),
                    dtype=float)