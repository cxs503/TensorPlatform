"""Contract-conforming producers for the P66 multi-physics chain.

Three producers feed the coupling gate:

=================================  ==============================================================
producer                           physics
=================================  ==============================================================
:func:`produce_n03` (REFERENCE)     ice **abrasion-field conditions** on the hull ice belt: the
                                   nominal belt-averaged normal stress field ``p(s)``, the slip
                                   velocity ``v_slip(s) = V cos(theta(s))``, the normal approach
                                   ``u_n(s) = V sin(theta(s))`` and the local plating angle
                                   ``theta(s)``.  ``p`` is normalised at a *reference* ice condition
                                   by the quasi-static crushing closure ``R_crush =
                                   0.5*sigma_c*h^2*(tan(alpha)+mu/cos(alpha))`` and then follows the
                                   ``h^2`` law along the sweep, so the ``(h, V)`` dependence is a
                                   prediction, not a fit.
:func:`produce_n04` (MERGED)        the full-ship **resistance chain**:
                                   ``tensorphysics.ice.resistance.level_ice_resistance`` (Lindqvist
                                   1989, three components) plus an ITTC-57 open-water friction term.
:func:`produce_n05` (REFERENCE)     spatio-temporal **abrasion-field evolution** by the
                                   Archard-type wear law ``dh/dt = k_w p v_slip / H_hull``
                                   integrated over the voyage time with a contact duty factor.
=================================  ==============================================================

Evidence grading
----------------
* N04 implementation: REAL (closed-form, literature coefficients, validity
  domain enforced by :class:`tensorphysics.ice.resistance.DomainLimits`).
* N03 / N05 implementation: REAL arithmetic, but the *closure constants*
  (``contact_area_ratio``, ``c_p`` calibration band, ``k_w``, ``H_hull``,
  ``duty_factor``) are SEMI-EMPIRICAL and the absolute abrasion depth is
  **WITHHELD** -- this repository holds no digitised full-scale ice-abrasion
  record, so no absolute accuracy is claimed.  What *is* verified here is the
  numerical coupling behaviour (conservation, quadrature, parameter-space
  consistency, contract conformance), which is what the gate exists for.
* N03 / N05 are DEM-side modules that are not merged in ``main``.  These are
  **reference producers**: they are analytic, deterministic and side-effect
  free, they are *not* solver kernels, and they are replaced 1:1 by the real
  producers once those deliver the same payload contract.

Units: SI throughout; every emitted field name carries its unit suffix and the
payload's ``units`` map declares it, so the contract validator can check the
dimension *and* the scale.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from ...ice.resistance import (
    PARAMETER_PROVENANCE,
    DomainLimits,
    IceDomainError,
    ShipGeometry,
    level_ice_resistance,
)
from . import contracts as C

# ---------------------------------------------------------------------------
# physical constants / documented assumptions (all SI)
# ---------------------------------------------------------------------------

RHO_ICE = 900.0            # kg/m^3, level ice, ISO 19906 range 900-920
RHO_SEAWATER = 1025.0      # kg/m^3
NU_SEAWATER = 1.19e-6      # m^2/s, kinematic viscosity at ~15 degC
RHO_STEEL = 7850.0         # kg/m^3, hull steel
C_B = 0.75                 # block coefficient of the PC6 reference ship (documented assumption)
FORM_FACTOR = 1.15         # (1+k) ice-class hull, ITTC-57 recommended band 1.10-1.25
H_HULL_PA = 2.1e9          # Pa, hull-steel hardness used in the Archard law
K_WEAR = 1.0e-3            # -, dimensionless ice-on-steel wear coefficient (band 1e-4..1e-3)
DUTY_FACTOR = 0.15         # -, fraction of voyage time in ice contact (documented assumption)
DURATION_S = 100.0 * 3600.0  # 100 h of voyage exposure
CONTACT_AREA_RATIO = 0.35  # -, DEM contact-area fraction on the ice belt (assumption, +-50%)
H_REF_M = 1.0              # m, reference ice thickness for the N03 pressure calibration

#: documented assumption bands, propagated into the report
ASSUMPTIONS: dict[str, dict[str, Any]] = {
    "contact_area_ratio": {"value": CONTACT_AREA_RATIO, "band": [0.15, 0.55], "grade": "ASSUMPTION"},
    "k_w": {"value": K_WEAR, "band": [1e-4, 1e-3], "grade": "SEMI-EMPIRICAL"},
    "H_hull_Pa": {"value": H_HULL_PA, "band": [1.5e9, 2.5e9], "grade": "LITERATURE"},
    "duty_factor": {"value": DUTY_FACTOR, "band": [0.05, 0.30], "grade": "ASSUMPTION"},
    "C_B": {"value": C_B, "band": [0.70, 0.80], "grade": "ASSUMPTION"},
    "form_factor": {"value": FORM_FACTOR, "band": [1.10, 1.25], "grade": "LITERATURE"},
}


# ---------------------------------------------------------------------------
# hull ice-belt geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeltGeometry:
    """Parametrisation of the hull ice belt as a one-dimensional strip.

    ``s`` is the arc length along the *waterline* of the ice belt, measured from
    the stem aft.  The belt is unrolled into a strip of constant height, so a
    patch is a rectangle of measure ``ds * height``.  This is the surface on
    which the DEM contact field and the LBM surface facets are both defined --
    the coupling interface of P66.
    """

    length_m: float = 60.0
    height_m: float = 4.0
    n_patches: int = 16
    flare_deg: float = float(PARAMETER_PROVENANCE["flare_deg"]["value"])
    alpha_min_deg: float = 5.0

    def edges(self, n: int | None = None) -> np.ndarray:
        count = int(self.n_patches if n is None else n)
        if count < 1:
            raise ValueError("belt needs at least one patch")
        return np.linspace(0.0, self.length_m, count + 1)

    def centers(self, n: int | None = None) -> np.ndarray:
        e = self.edges(n)
        return 0.5 * (e[:-1] + e[1:])

    def widths(self, n: int | None = None) -> np.ndarray:
        e = self.edges(n)
        return np.diff(e)

    def areas(self, n: int | None = None) -> np.ndarray:
        return self.widths(n) * self.height_m

    def alpha(self, s: np.ndarray | float) -> np.ndarray:
        """Local plating angle from the horizontal [rad].

        Maximum at the stem (the flare angle), decaying linearly to
        ``alpha_min_deg`` aft over the belt length: the ice is pushed *under*
        the bow, not against a vertical wall, which is the same geometry the
        Lindqvist flare term encodes.
        """
        s_arr = np.asarray(s, dtype=float)
        a_max = math.radians(self.flare_deg)
        a_min = math.radians(self.alpha_min_deg)
        frac = np.clip(s_arr / self.length_m, 0.0, 1.0)
        return a_max + (a_min - a_max) * frac

    def pressure_shape(self, s: np.ndarray | float, decay: float = 0.25) -> np.ndarray:
        """Waterline contact-intensity envelope, mean 1 over the belt.

        ``phi(s) = exp(-s/lambda)`` with ``lambda = decay * length``: ice contact
        intensity concentrates at the stem and decays aft (DEM waterline
        concentration).  Normalised on the belt so that
        ``(1/L) * int_0^L phi ds = 1`` and ``phi`` can only redistribute the
        force, never create it.
        """
        s_arr = np.asarray(s, dtype=float)
        lam = decay * self.length_m
        raw = np.exp(-s_arr / lam)
        mean = (lam / self.length_m) * (1.0 - math.exp(-self.length_m / lam))
        return raw / mean


def belt_geometry(ship: ShipGeometry, *, n_patches: int = 16,
                  length_fraction: float = 0.4, height_m: float = 4.0) -> BeltGeometry:
    """Ice belt covering the forward 40 % of the waterline (documented choice)."""
    return BeltGeometry(
        length_m=length_fraction * ship.L_pp,
        height_m=height_m,
        n_patches=n_patches,
        flare_deg=ship.flare_deg,
    )


def geometry_factor(belt: BeltGeometry, contact_area_ratio: float,
                    mu: float, n: int | None = None) -> float:
    """``K = f_c * sum_i A_i phi_i (sin theta_i + mu cos theta_i)`` [m^2].

    The belt-integrated horizontal force for a unit nominal normal stress:
    ``F = K * p_ref``.  ``K`` is the only place where the strip geometry enters
    the producing side and the gate recomputes it independently.
    """
    s = belt.centers(n)
    areas = belt.areas(n)
    theta = belt.alpha(s)
    phi = belt.pressure_shape(s)
    return float(contact_area_ratio * np.sum(areas * phi * (np.sin(theta) + mu * np.cos(theta))))


# ---------------------------------------------------------------------------
# N03: abrasion-field conditions
# ---------------------------------------------------------------------------


def produce_n03(ship: ShipGeometry, samples: Sequence[tuple[float, float]], *,
                belt: BeltGeometry | None = None,
                contact_area_ratio: float = CONTACT_AREA_RATIO,
                sigma_c: float | None = None,
                sigma_f: float | None = None,
                mu: float | None = None,
                producer: str = "P66-W2-N03 reference-producer (DEM abrasion field not merged in main)",
                ) -> dict[str, Any]:
    """DEM-side abrasion-field conditions on the belt, one record per ``(h, V)``."""
    belt = belt or belt_geometry(ship)
    sigma_c = float(PARAMETER_PROVENANCE["sigma_c_Pa"]["value"] if sigma_c is None else sigma_c)
    sigma_f = float(PARAMETER_PROVENANCE["sigma_f_Pa"]["value"] if sigma_f is None else sigma_f)
    mu = float(PARAMETER_PROVENANCE["mu_hull_ice"]["value"] if mu is None else mu)

    s = belt.centers()
    areas = belt.areas()
    theta = belt.alpha(s)
    phi = belt.pressure_shape(s)

    # Calibration of the nominal belt stress at the reference ice thickness:
    # p_ref = R_crush_ref / K  =>  the field reproduces the crushing closure at
    # h_ref and predicts it elsewhere through the h^2 law.
    r_ref = level_ice_resistance(H_REF_M, 1.0, ship, sigma_c=sigma_c, mu=mu)["R_crush_N"]
    k_factor = geometry_factor(belt, contact_area_ratio, mu)
    p_ref = r_ref / k_factor

    records = []
    for h, V in samples:
        h = float(h)
        V = float(V)
        p_ice = p_ref * (h / H_REF_M) ** 2 * phi
        records.append({
            "h_m": h,
            "V_ms": V,
            "s_m": [float(x) for x in s],
            "area_m2": [float(x) for x in areas],
            "p_ice_Pa": [float(x) for x in p_ice],
            "v_slip_ms": [float(x) for x in (V * np.cos(theta))],
            "u_n_ms": [float(x) for x in (V * np.sin(theta))],
            "alpha_rad": [float(x) for x in theta],
        })

    return {
        "schema": C.SCHEMA_N03,
        "producer": producer,
        "ship": _ship_dict(ship),
        "belt": {"length_m": belt.length_m, "height_m": belt.height_m,
                 "n_patches": belt.n_patches},
        "ice": {"rho_ice_kg_m3": RHO_ICE, "sigma_c_Pa": sigma_c,
                "sigma_f_Pa": sigma_f, "mu_hull_ice": mu},
        "contact_area_ratio": float(contact_area_ratio),
        "samples": records,
        "units": {
            "L_pp_m": "m", "B_m": "m", "T_m": "m", "bow_angle_deg": "deg", "flare_deg": "deg",
            "length_m": "m", "height_m": "m", "n_patches": "-",
            "rho_ice_kg_m3": "kg/m^3", "sigma_c_Pa": "Pa", "sigma_f_Pa": "Pa",
            "mu_hull_ice": "-", "contact_area_ratio": "-",
            "h_m": "m", "V_ms": "m/s", "s_m": "m", "area_m2": "m^2",
            "p_ice_Pa": "Pa", "v_slip_ms": "m/s", "u_n_ms": "m/s", "alpha_rad": "rad",
        },
    }


# ---------------------------------------------------------------------------
# N04: full-ship resistance chain (merged module)
# ---------------------------------------------------------------------------


def open_water_resistance(V: float, ship: ShipGeometry, *,
                          rho: float = RHO_SEAWATER, nu: float = NU_SEAWATER,
                          c_b: float = C_B, form_factor: float = FORM_FACTOR) -> dict[str, float]:
    """ITTC-57 friction resistance with a form factor; wave resistance neglected.

    ``S = L (1.7 T + C_B B)`` is the standard wetted-surface estimate (band
    +-10 %); wave resistance is neglected because the ice-going Froude range is
    ``Fr_L < 0.1`` (``R_w`` is O(1 %) of ``R_ow`` there).  Both limitations are
    recorded, not hidden.
    """
    if V <= 0.0:
        raise ValueError("V must be positive")
    S = ship.L_pp * (1.7 * ship.T + c_b * ship.B)
    re = V * ship.L_pp / nu
    c_f = 0.075 / (math.log10(re) - 2.0) ** 2
    r_ow = 0.5 * rho * V * V * S * form_factor * c_f
    return {"S_m2": S, "Re": re, "C_f": c_f, "R_ow_N": r_ow}


def produce_n04(ship: ShipGeometry, samples: Sequence[tuple[float, float]], *,
                limits: DomainLimits | None = None,
                strict: bool = False,
                mu: float | None = None,
                producer: str = "P66-W2-N04 resistance chain (tensorphysics.ice.resistance, merged)",
                ) -> dict[str, Any]:
    """Full-ship resistance on the ``(h, V)`` grid, with validity-domain flags."""
    limits = limits or DomainLimits()
    mu = float(PARAMETER_PROVENANCE["mu_hull_ice"]["value"] if mu is None else mu)
    h_lim = limits.h_limit(ship)
    v_lim = limits.v_limit(ship)
    records = []
    for h, V in samples:
        h = float(h)
        V = float(V)
        state = level_ice_resistance(h, V, ship, limits=limits, strict=strict, mu=mu)
        ow = open_water_resistance(V, ship)
        in_domain = bool(state["domain_ok"]) and h <= h_lim and V <= v_lim
        note = "in domain" if in_domain else f"OUTSIDE validity domain (h<={h_lim:.3f} m, V<={v_lim:.3f} m/s)"
        records.append({
            "h_m": state["h_m"],
            "V_ms": state["V_ms"],
            "R_crush_N": state["R_crush_N"],
            "R_break_N": state["R_break_N"],
            "R_submersion_N": state["R_submersion_N"],
            "R_ice_N": state["R_total_N"],
            "R_ow_N": ow["R_ow_N"],
            "R_total_N": state["R_total_N"] + ow["R_ow_N"],
            "in_domain": in_domain,
            "domain_note": note,
        })
    return {
        "schema": C.SCHEMA_N04,
        "producer": producer,
        "ship": _ship_dict(ship),
        "method": "Lindqvist(1989) three-component level-ice law + ITTC-57 open-water friction",
        "samples": records,
        "units": {
            "L_pp_m": "m", "B_m": "m", "T_m": "m", "bow_angle_deg": "deg", "flare_deg": "deg",
            "h_m": "m", "V_ms": "m/s", "R_crush_N": "N", "R_break_N": "N",
            "R_submersion_N": "N", "R_ice_N": "N", "R_ow_N": "N", "R_total_N": "N",
        },
    }


# ---------------------------------------------------------------------------
# N05: spatio-temporal abrasion-field evolution
# ---------------------------------------------------------------------------


def produce_n05(n03: dict[str, Any], *, wear: dict[str, float] | None = None,
                producer: str = "P66-W2-N05 reference-producer (DEM abrasion evolution not merged in main)",
                ) -> dict[str, Any]:
    """Time-integrated Archard wear field consistent with the N03 conditions.

    ``dh/dt = k_w p_contact v_slip / H_hull`` per patch, integrated over
    ``duration_s`` with the contact ``duty_factor``; the wear volume uses the
    *contacted* area ``contact_area_ratio * area``.  The pressure used is the
    contact pressure ``p_contact = p_ice / contact_area_ratio`` (the N03 field is
    belt-area averaged), so the archived wear depth is a *contact*-scale depth.
    """
    wear = dict(wear or {})
    k_w = float(wear.get("k_w", K_WEAR))
    hardness = float(wear.get("H_hull_Pa", H_HULL_PA))
    duty = float(wear.get("duty_factor", DUTY_FACTOR))
    duration = float(wear.get("duration_s", DURATION_S))
    f_c = float(n03["contact_area_ratio"])

    records = []
    for sample in n03["samples"]:
        p_ice = np.asarray(sample["p_ice_Pa"], dtype=float)
        v_slip = np.asarray(sample["v_slip_ms"], dtype=float)
        areas = np.asarray(sample["area_m2"], dtype=float)
        p_contact = p_ice / f_c
        dh_dt = k_w * p_contact * v_slip / hardness
        depth = dh_dt * duration * duty
        mass_loss = RHO_STEEL * float(np.sum(f_c * areas * depth))
        records.append({
            "h_m": float(sample["h_m"]),
            "V_ms": float(sample["V_ms"]),
            "depth_m": [float(x) for x in depth],
            "dh_dt_ms": [float(x) for x in dh_dt],
            "area_m2": [float(x) for x in areas],
            "mean_depth_m": float(np.mean(depth)),
            "mass_loss_kg": mass_loss,
        })

    return {
        "schema": C.SCHEMA_N05,
        "producer": producer,
        "wear": {"k_w": k_w, "H_hull_Pa": hardness, "duty_factor": duty,
                 "duration_s": duration},
        "samples": records,
        "units": {
            "h_m": "m", "V_ms": "m/s", "depth_m": "m", "dh_dt_ms": "m/s",
            "area_m2": "m^2", "mean_depth_m": "m", "mass_loss_kg": "kg",
            "k_w": "-", "H_hull_Pa": "Pa", "duty_factor": "-", "duration_s": "s",
            "rho_steel_kg_m3": "kg/m^3", "p_ice_Pa": "Pa", "v_slip_ms": "m/s",
        },
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ship_dict(ship: ShipGeometry) -> dict[str, Any]:
    return {
        "name": ship.name,
        "L_pp_m": ship.L_pp,
        "B_m": ship.B,
        "T_m": ship.T,
        "bow_angle_deg": ship.bow_angle_deg,
        "flare_deg": ship.flare_deg,
    }


def reference_ship() -> ShipGeometry:
    """PC6 reference ship used by the P66 sweep (the ``ice.resistance`` default)."""
    return ShipGeometry()


def parameter_sweep(h_values: Iterable[float], v_values: Iterable[float]) -> list[tuple[float, float]]:
    """Cartesian ``(h, V)`` sample list, h-major and deterministic."""
    return [(float(h), float(v)) for h in h_values for v in v_values]


def produce_chain(ship: ShipGeometry, samples: Sequence[tuple[float, float]], *,
                  belt: BeltGeometry | None = None,
                  contact_area_ratio: float = CONTACT_AREA_RATIO,
                  strict: bool = False,
                  wear: dict[str, float] | None = None,
                  ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run N03 -> N05 and N04 on the same samples; return the three payloads."""
    n03 = produce_n03(ship, samples, belt=belt, contact_area_ratio=contact_area_ratio)
    n05 = produce_n05(n03, wear=wear)
    n04 = produce_n04(ship, samples, strict=strict)
    return n03, n04, n05