"""Parametric hull-form model for the three-objective Pareto study.

Physical content deliberately kept in the *domain* package
(``domains/hull_form_pareto``): the generic NSGA-II machinery lives in
``optimizer/multiobjective`` and knows nothing about ships.

Geometry model
--------------
A hull is described by 13 design variables (see ``config/hull_design_space.json``).
From them the principal particulars follow:

    B = L / (L/B),      T = B / (B/T),      D = (D/T) * T
    nabla = Cb * L * B * T                  (displacement volume, moulded)
    Cp    = Cb / Cm                          (prismatic coefficient)

Sectional-area distribution
    The longitudinal distribution of sectional area is the trapezoid

        s(x) = x/b                 (0 <= x < b,            aft taper)
        s(x) = 1                   (b <= x < 1-a,          parallel body)
        s(x) = (1-x)/a             (1-a <= x <= 1,         entrance)

    with ``a + b = 1 - f`` and ``f = 2 Cp - 1``, which gives exactly

        integral_0^1 s(x) dx = (1 + f)/2 = Cp ,

    so the displacement volume recomputed from the section model is
    ``Cm * B * T * L * integral s dx = Cb * L * B * T`` — i.e. the geometry is
    self-consistent by construction (verified in the test suite to 1e-9).

Cargo hold volume (舱容积分)
    Hold window along the length: ``[x_er, x_er + l_hold]`` with ``x_er = 0.08``
    the engine-room/aft-tank length fraction (documented constant).  The hold
    volume is a genuine longitudinal integral of the hold section area,

        V_cargo = eta_stow * Cm * (B - 2 t_side) * (D - DB) * L * integral_window s(x) dx

    with a side-shell/hopper deduction ``t_side = max(0.012 B, 0.8)`` m,
    ``DB`` the double-bottom height and ``eta_stow = 0.92`` the stowage/void
    efficiency (documented constant).  Tapered ends therefore reduce capacity:
    fine hulls cannot carry their deadweight in long holds.

Hydrostatics
    * ``Cwp = (1 + 2 Cp)/3`` (Schneekluth estimate; identical to the default
      used by the Holtrop implementation so the two models stay consistent).
    * Waterplane growth is modelled as ``A_w(z) = Cwp L B (z/T)^n`` with
      ``n = Cwp/Cb - 1``, which is exactly volume-consistent
      (``integral A_w dz = Cb L B T = nabla``) and degenerates correctly to the
      box hull (``KB = T/2``).  Hence

          KB = T (n + 1)/(n + 2)

    * ``BM = I_T / nabla`` with ``I_T = Cwp L B^3 / 12`` (rectangular waterplane
      of coefficient Cwp).
    * ``KG = kg_ratio * D`` — design variable covering lightship + outfit +
      cargo vertical centre of gravity.
    * ``GM = KB + BM - KG``; natural roll period ``T_roll = 2 pi k_x/sqrt(g GM)``
      with ``k_x = 0.35 B``.

Resistance
    ``backend/api/domain/holtrop_mennen.py`` (Holtrop–Mennen 1982 + 1984
    additions, REAL implementation, no re-derivation here) is called with the
    hull's own Cm/Cwp/lcb/bulb/transom particulars.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------
# constants (documented design assumptions, not fitted to any benchmark)
# --------------------------------------------------------------------------
RHO_SEAWATER = 1025.0        # kg/m3
G = 9.80665                  # m/s2
HOLD_ER_FRACTION = 0.08      # engine room + aft peak / L
HOLD_FWD_MARGIN = 0.03       # fore peak / L
ETA_STOW = 0.92              # hold stowage / void efficiency
HB_OVER_T = 0.45             # bulb centre height above keel / T
KX_OVER_B = 0.35             # roll radius of gyration / B
GM_MIN_IMO = 0.15            # m, IS Code 2008 general intact-stability criterion
T_ROLL_MIN = 10.0            # s, comfort criterion (cargo ship)
N_STATIONS = 201             # longitudinal stations for the volume integrals

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "hull_design_space.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
HOLTROP_PATH = REPO_ROOT / "backend" / "api" / "domain" / "holtrop_mennen.py"


def load_holtrop_module():
    """Import the platform's Holtrop implementation *without* the FastAPI package
    side effects of ``backend/api/__init__.py`` (module loaded from file)."""
    import importlib.util
    import sys
    name = "_platform_holtrop_mennen"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HOLTROP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# --------------------------------------------------------------------------
# design space
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DesignVariable:
    name: str
    lower: float
    upper: float
    unit: str
    description: str


@dataclass(frozen=True)
class DesignSpace:
    variables: Tuple[DesignVariable, ...]
    fixed: Dict[str, float]
    constants: Dict[str, float]

    @property
    def names(self) -> List[str]:
        return [v.name for v in self.variables]

    @property
    def n_var(self) -> int:
        return len(self.variables)

    @property
    def lower(self) -> np.ndarray:
        return np.array([v.lower for v in self.variables], dtype=float)

    @property
    def upper(self) -> np.ndarray:
        return np.array([v.upper for v in self.variables], dtype=float)

    def to_dict(self) -> Dict[str, object]:
        return {
            "variables": [asdict(v) for v in self.variables],
            "fixed": dict(self.fixed),
            "constants": dict(self.constants),
            "n_var": self.n_var,
        }

    def decode(self, x: Sequence[float]) -> Dict[str, float]:
        x = np.asarray(x, dtype=float)
        if x.shape != (self.n_var,):
            raise ValueError(f"expected {self.n_var} variables, got {x.shape}")
        out = {v.name: float(x[i]) for i, v in enumerate(self.variables)}
        out.update(self.fixed)
        return out

    def clip(self, x: np.ndarray) -> np.ndarray:
        return np.clip(np.asarray(x, dtype=float), self.lower, self.upper)


def load_design_space(path: Optional[Path] = None) -> DesignSpace:
    """Load the hull design space from the domain configuration JSON."""
    p = Path(path) if path is not None else CONFIG_PATH
    raw = json.loads(p.read_text())
    variables = tuple(
        DesignVariable(name=v["name"], lower=float(v["lower"]),
                       upper=float(v["upper"]), unit=v.get("unit", ""),
                       description=v.get("description", ""))
        for v in raw["variables"]
    )
    return DesignSpace(variables=variables,
                       fixed={k: float(v) for k, v in raw.get("fixed", {}).items()},
                       constants={k: float(v) for k, v in raw.get("constants", {}).items()})


# --------------------------------------------------------------------------
# geometry / hydrostatics
# --------------------------------------------------------------------------
@dataclass
class HullForm:
    length: float
    beam: float
    draft: float
    depth: float
    cb: float
    cm: float
    cp: float
    cwp: float
    lcb_frac: float
    kg: float
    kg_ratio: float
    hold_length: float
    hold_length_fraction: float
    double_bottom: float
    bulb_area: float
    bulb_height: float
    transom_area: float
    speed_ms: float
    froude: float
    nabla: float
    displacement_t: float
    wetted_surface: float
    kb: float
    bm: float
    gm: float
    roll_period: float
    cargo_volume: float
    parallel_body_fraction: float
    stations: np.ndarray = field(repr=False, default=None)
    section_fullness: np.ndarray = field(repr=False, default=None)

    def as_dict(self) -> Dict[str, object]:
        d = {k: v for k, v in asdict(self).items()
             if k not in ("stations", "section_fullness")}
        return d


def sectional_fullness_profile(cp: float, n_stations: int = N_STATIONS,
                              entrance_share: float = 0.55
                              ) -> Tuple[np.ndarray, np.ndarray]:
    """Trapezoidal sectional-area distribution with unit integral == Cp."""
    if not 0.2 < cp <= 0.95:
        # above Cp = 0.95 the parallel-body clip (f <= 0.9) would break the exact
        # integral == Cp identity, so the model is not used there
        raise ValueError(f"Cp out of range for the trapezoid section model: {cp}")
    f_parallel = max(0.0, min(0.9, 2.0 * cp - 1.0))
    ends = 1.0 - f_parallel
    a = entrance_share * ends
    b = ends - a
    x = np.linspace(0.0, 1.0, n_stations)
    s = np.ones_like(x)
    if b > 1e-12:
        s = np.where(x < b, x / b, s)
    if a > 1e-12:
        s = np.where(x > 1.0 - a, (1.0 - x) / a, s)
    return x, np.clip(s, 0.0, 1.0)


def trapezoid_integral(y: np.ndarray, x: np.ndarray) -> float:
    """∫ y dx by the trapezoidal rule (uniform or non-uniform x)."""
    return float(np.trapezoid(y, x)) if hasattr(np, "trapezoid") else float(np.trapz(y, x))


def kb_estimate(cb: float, cwp: float, draft: float) -> float:
    """KB from the volume-consistent waterplane-growth model.

    ``A_w(z) = Cwp L B (z/T)^n`` with ``n = Cwp/Cb - 1`` integrates exactly to
    ``Cb L B T`` (i.e. the displacement volume), and gives

        KB = T (n + 1)/(n + 2)      ->  T/2 for the box hull (n = 0).
    """
    n = cwp / cb - 1.0
    return draft * (n + 1.0) / (n + 2.0)


def bm_estimate(cwp: float, length: float, beam: float, nabla: float) -> float:
    """BM = I_T/nabla with a rectangular waterplane of coefficient Cwp."""
    return (cwp * length * beam ** 3 / 12.0) / nabla


def build_hull(x: Sequence[float], space: Optional[DesignSpace] = None) -> HullForm:
    """Map a design vector to the parametric hull (geometry + hydrostatics)."""
    space = space or load_design_space()
    d = space.decode(space.clip(np.asarray(x, dtype=float)))

    L = d["length_m"]
    B = L / d["l_over_b"]
    T = B / d["b_over_t"]
    D = d["d_over_t"] * T
    cb = d["cb"]
    cm = d["cm"]
    cp = cb / cm
    cwp = (1.0 + 2.0 * cp) / 3.0
    lcb_frac = d["lcb_pct"] / 100.0

    nabla = cb * L * B * T
    displacement_t = nabla * RHO_SEAWATER / 1000.0

    # wetted surface: use the resistance module's own formula for consistency
    _s = load_holtrop_module().wetted_surface
    abt = d["bulb_area_ratio"] * B * T
    at = d["transom_area_ratio"] * B * T
    sw = _s(L, B, T, cb, cm, cwp, abt)

    # hydrostatics
    kb = kb_estimate(cb, cwp, T)
    bm = bm_estimate(cwp, L, B, nabla)
    kg = d["kg_ratio"] * D
    gm = kb + bm - kg
    roll = 2.0 * math.pi * (KX_OVER_B * B) / math.sqrt(G * gm) if gm > 0 else float("inf")

    # cargo hold volume: longitudinal integral of the hold section area
    xs, s = sectional_fullness_profile(cp)
    hold_frac = d["hold_len_frac"]
    x_a = HOLD_ER_FRACTION
    x_b = min(x_a + hold_frac, 1.0 - HOLD_FWD_MARGIN)
    window = (xs >= x_a) & (xs <= x_b)
    contained = trapezoid_integral(s[window], xs[window]) if window.any() else 0.0
    t_side = max(0.012 * B, 0.8)
    db = d["double_bottom_ratio"] * D
    cargo = ETA_STOW * cm * (B - 2.0 * t_side) * (D - db) * L * contained

    speed_ms = d["speed_kn"] * 0.514444
    fn = speed_ms / math.sqrt(G * L)

    return HullForm(
        length=L, beam=B, draft=T, depth=D, cb=cb, cm=cm, cp=cp, cwp=cwp,
        lcb_frac=lcb_frac, kg=kg, kg_ratio=d["kg_ratio"],
        hold_length=(x_b - x_a) * L, hold_length_fraction=hold_frac,
        double_bottom=db, bulb_area=abt, bulb_height=HB_OVER_T * T,
        transom_area=at, speed_ms=speed_ms, froude=fn, nabla=nabla,
        displacement_t=displacement_t, wetted_surface=sw, kb=kb, bm=bm,
        gm=gm, roll_period=roll, cargo_volume=cargo,
        parallel_body_fraction=max(0.0, 2.0 * cp - 1.0),
        stations=xs, section_fullness=s,
    )


def hold_volume_integral(hull: HullForm) -> Dict[str, float]:
    """Diagnostics of the 舱容 integral (window bounds and contained area)."""
    xs, s = hull.stations, hull.section_fullness
    x_a = HOLD_ER_FRACTION
    x_b = min(x_a + hull.hold_length_fraction, 1.0 - HOLD_FWD_MARGIN)
    window = (xs >= x_a) & (xs <= x_b)
    return {
        "x_start_fraction": float(x_a),
        "x_end_fraction": float(x_b),
        "contained_section_area": float(trapezoid_integral(s[window], xs[window])),
        "cargo_volume_m3": float(hull.cargo_volume),
    }


def resistance(hull: HullForm) -> Dict[str, float]:
    """Total calm-water resistance via Holtrop-Mennen (REAL implementation)."""
    holtrop_resistance = load_holtrop_module().holtrop_resistance
    r = holtrop_resistance(
        hull.length, hull.beam, hull.draft, hull.cb, hull.speed_ms,
        cm=hull.cm, cwp=hull.cwp, lcb_frac=hull.lcb_frac,
        abt=hull.bulb_area, hb=hull.bulb_height, at=hull.transom_area,
        rho=RHO_SEAWATER,
    )
    return {
        "rt_n": float(r.rt_n),
        "rt_kn": float(r.rt_n / 1000.0),
        "rf_n": float(r.rf_n),
        "rw_n": float(r.rw_n),
        "rbulb_n": float(r.rbulb_n),
        "rtr_n": float(r.rtr_n),
        "ra_n": float(r.ra_n),
        "form_factor": float(r.form_factor),
        "ct": float(r.ct),
        "ie_deg": float(r.ie_deg),
        "froude": float(r.fn),
        "reynolds": float(r.re),
        "warnings": list(r.warnings),
        "effective_power_mw": float(r.rt_n * hull.speed_ms / 1e6),
    }