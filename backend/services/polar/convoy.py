"""Escort convoy spacing optimisation: the total convoy energy E(s) has an
interior minimum in the spacing ``s`` behind the icebreaker.

Physics of the two competing mechanisms
---------------------------------------
* **Refreezing / brash consolidation** (penalty that grows with ``s``): the
  channel behind the escort refreezes while the follower waits.  With Stefan's
  law the regrown ice thickness after a residence time ``t = s / v`` is
  ``delta_h = a_f sqrt(t)``, ``a_f = sqrt(2 k_i dT / (rho_i L_f))``
  (k_i = 2.2 W/mK, dT = 20 K, L_f = 3.33e5 J/kg -> a_f ~ 5.4e-4 m/sqrt(s)).
  Thicker brash -> larger channel resistance, so the follower burns more fuel.
* **Proximity / brash-ploughing** (penalty that grows as ``s`` shrinks): right
  behind the escort the follower ploughs through freshly broken, not yet
  cleared brash and works in the escort's broken-ice wake; this is modelled as
  ``R_prox = kappa_p R_brash exp(-s / s_0)`` with ``s_0`` the wake decay length.

Because the refreezing term grows like ``sqrt(s)`` and the proximity term decays
exponentially, ``E(s)`` is unimodal with an interior minimum, which is what
criterion (c) verifies (numerically scanned minimum, golden-section optimum, and
the root of dE/ds compared against each other).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .channel import channel_resistance
from .constants import G, ICE_CONDUCTIVITY, ICE_LATENT_HEAT, KNOT, RHO_ICE
from .resistance import holtrop_open_water_resistance, ice_components
from .ships import ShipGeometry

#: Reference temperature difference across the ice sheet [K] (air - water).
DELTA_T_K = 20.0
#: Wake decay length of the proximity penalty [m].
WAKE_DECAY_M = 120.0
#: Proximity penalty coefficient relative to the follower brash resistance [-].
KAPPA_PROX = 0.55
#: Minimum safe spacing (documented operational constraint) [m].
S_MIN_M = 100.0
#: Upper end of the search window [m].
S_MAX_M = 4000.0


def stefan_growth_coefficient(delta_t_k: float = DELTA_T_K) -> float:
    """``a_f`` in ``delta_h = a_f sqrt(t)``  [m/sqrt(s)]."""
    return math.sqrt(2.0 * ICE_CONDUCTIVITY * delta_t_k / (RHO_ICE * ICE_LATENT_HEAT))


@dataclass
class ConvoyConfig:
    """Escort convoy definition.

    escort        icebreaker leading the convoy (breaks intact ice)
    followers     number of cargo ships following in the channel
    follower      representative cargo-ship geometry
    h_level_m     level-ice thickness the escort is breaking [m]
    h_brash_m     base brash-layer thickness of the channel right behind the escort [m]
    concentration brash concentration in the channel [-]
    speed_kn      convoy speed (through water) [kn]
    """

    escort: ShipGeometry
    follower: ShipGeometry
    h_level_m: float = 0.90
    h_brash_m: float = 0.80
    concentration: float = 0.45
    speed_kn: float = 5.0
    followers: int = 2

    @property
    def v_ms(self) -> float:
        return self.speed_kn * KNOT

    def as_dict(self) -> Dict[str, Any]:
        return {
            "escort": self.escort.key,
            "follower": self.follower.key,
            "followers": self.followers,
            "h_level_m": self.h_level_m,
            "h_brash_m": self.h_brash_m,
            "concentration": self.concentration,
            "speed_kn": self.speed_kn,
        }


def _channel_power_kw(ship: ShipGeometry, v_ms: float, h_b_m: float, c: float) -> float:
    r = channel_resistance(ship, v_ms, h_b_m, c)
    return r["total_n"] * v_ms / ship.eta_propulsive / 1000.0


def convoy_energy_per_m(
    s_m: float,
    cfg: ConvoyConfig,
    return_parts: bool = False,
) -> Any:
    """Total convoy energy per unit ground distance [J/m].

    Energy per unit distance for one ship is ``R v / eta_propulsive / v``, i.e.
    ``R / eta``; summed over the escort and the followers.
    """
    if s_m <= 0.0:
        raise ValueError("spacing must be positive")
    v = cfg.v_ms
    t_res = s_m / v                                    # residence time [s]
    dh = stefan_growth_coefficient() * math.sqrt(t_res)  # refrozen thickness [m]
    h_b_eff = cfg.h_brash_m + dh

    # escort: breaking intact level ice
    esc_ow = holtrop_open_water_resistance(cfg.escort, v)["total"]
    esc_ice = ice_components(cfg.escort, v, cfg.h_level_m, _sigma_for(h_level_m=cfg.h_level_m))["total"]
    r_esc = esc_ow + esc_ice

    fol_ow = holtrop_open_water_resistance(cfg.follower, v)["total"]
    fol_ch = channel_resistance(cfg.follower, v, h_b_eff, cfg.concentration, include_open_water=False)
    r_prox = KAPPA_PROX * fol_ch["brash_n"] * math.exp(-s_m / WAKE_DECAY_M)
    r_fol = fol_ow + fol_ch["brash_n"] + r_prox

    e_esc = r_esc / cfg.escort.eta_propulsive
    e_fol = cfg.followers * r_fol / cfg.follower.eta_propulsive
    total = e_esc + e_fol

    if return_parts:
        return {
            "energy_per_m_j": total,
            "escort_n": r_esc,
            "follower_n": r_fol,
            "refrozen_thickness_m": dh,
            "h_b_eff_m": h_b_eff,
            "proximity_n": r_prox,
            "brash_n": fol_ch["brash_n"],
            "energy_escort_j_per_m": e_esc,
            "energy_followers_j_per_m": e_fol,
        }
    return total


def _sigma_for(h_level_m: float) -> float:
    """Ice compressive strength assigned to a level-ice thickness [MPa].

    Uses the same documented relation as the synthetic scenario generator so
    that escort physics and route physics see one ice-strength model.
    """
    return 0.9 + 0.55 * min(max(h_level_m / 1.6, 0.0), 1.0)


def convoy_energy_curve(
    cfg: ConvoyConfig,
    s_min_m: float = S_MIN_M,
    s_max_m: float = S_MAX_M,
    n: int = 80,
) -> List[Dict[str, float]]:
    """Sample E(s) on a log-spaced grid (used for the report figure)."""
    lo, hi = math.log(s_min_m), math.log(s_max_m)
    out = []
    for k in range(n):
        s = math.exp(lo + (hi - lo) * k / (n - 1))
        parts = convoy_energy_per_m(s, cfg, return_parts=True)
        out.append(
            {
                "s_m": s,
                "energy_mj_per_km": parts["energy_per_m_j"] / 1e3,
                "proximity_n": parts["proximity_n"],
                "h_b_eff_m": parts["h_b_eff_m"],
                "refrozen_mm": parts["refrozen_thickness_m"] * 1000.0,
            }
        )
    return out


def _golden_section(f, a: float, b: float, tol: float = 1e-3, max_iter: int = 200) -> Tuple[float, float]:
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - gr * (b - a)
    d = a + gr * (b - a)
    fc, fd = f(c), f(d)
    it = 0
    while abs(b - a) > tol and it < max_iter:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = f(d)
        it += 1
    return 0.5 * (a + b), f(0.5 * (a + b))


def _numeric_derivative(f, x: float, h_rel: float = 1e-4) -> float:
    h = max(abs(x) * h_rel, 1e-6)
    return (f(x + h) - f(x - h)) / (2.0 * h)


def optimize_spacing(
    cfg: ConvoyConfig,
    s_min_m: float = S_MIN_M,
    s_max_m: float = S_MAX_M,
    curve_points: int = 60,
) -> Dict[str, Any]:
    """Find the energy-optimal escort spacing and verify it is an interior minimum.

    Returns the optimum, the E(s) curve, and three independent consistency checks:

    * ``interior``: the optimum is strictly inside (s_min, s_max);
    * ``derivative_sign_change``: dE/ds < 0 before and > 0 after the optimum;
    * ``scan_matches``: the brute-force log-grid scan minimum is within 2 % of
      the golden-section optimum.
    """
    f = lambda s: convoy_energy_per_m(s, cfg)

    s_opt, e_opt = _golden_section(f, s_min_m, s_max_m, tol=1e-2)
    curve = convoy_energy_curve(cfg, s_min_m, s_max_m, n=curve_points)

    scan_best = min(curve, key=lambda row: row["energy_mj_per_km"])["s_m"]

    d_left = _numeric_derivative(f, max(s_opt * 0.7, s_min_m * 1.0001))
    d_right = _numeric_derivative(f, s_opt * 1.3)

    e_at_min = f(s_min_m)
    e_at_max = f(s_max_m)

    return {
        "spacing_opt_m": round(s_opt, 2),
        "energy_opt_mj_per_km": round(e_opt / 1e3, 3),
        "energy_at_s_min_mj_per_km": round(e_at_min / 1e3, 3),
        "energy_at_s_max_mj_per_km": round(e_at_max / 1e3, 3),
        "saving_vs_s_min_pct": round(100.0 * (e_at_min - e_opt) / e_at_min, 3),
        "saving_vs_s_max_pct": round(100.0 * (e_at_max - e_opt) / e_at_max, 3),
        "interior_minimum": bool(s_min_m * 1.05 < s_opt < s_max_m * 0.95),
        "derivative_sign_change": bool(d_left < 0.0 < d_right),
        "derivative_at_opt_j_per_m2": round(_numeric_derivative(f, s_opt), 6),
        "scan_minimum_m": round(scan_best, 2),
        "scan_agrees_within_2pct": bool(abs(scan_best - s_opt) / s_opt < 0.02),
        "refrozen_at_opt_mm": round(
            stefan_growth_coefficient() * math.sqrt(s_opt / cfg.v_ms) * 1000.0, 3
        ),
        "config": cfg.as_dict(),
        "curve": curve,
    }


def verify_escort_optimum(cfg: ConvoyConfig) -> Dict[str, Any]:
    """Criterion (c): E(s) has a genuine interior minimum."""
    res = optimize_spacing(cfg)
    ok = res["interior_minimum"] and res["derivative_sign_change"] and res["scan_agrees_within_2pct"]
    return {
        "criterion": "c",
        "description": "convoy energy E(s) has an interior minimum in escort spacing",
        "pass": bool(ok),
        **{k: v for k, v in res.items() if k != "curve"},
        "curve": res["curve"],
    }