"""Droplet water-entry anomaly: case construction, measurement chain, acceptance.

Physical setting
----------------
Single-component two-phase (liquid/gas) system in a doubly periodic box.  A
liquid droplet travels downwards through the gas and impinges on a quiescent
liquid pool.  This is the classical *droplet water-entry* configuration
(Worthington / Aristoff & Bush) reduced to 2D so that every QoI has an
independent analytic anchor:

* **Laplace law** ``Delta p = sigma / R`` (2D circular interface).  Exact in the
  sharp-interface limit; the discrete residual is a *measured* quantity, not an
  assumption.
* **Impulsive added mass.**  For a circular cylinder (2D) in an ideal fluid,
  ``C_A = 1`` measured with the displaced-fluid mass.  Momentum conservation of
  the impulsive entry therefore predicts ``u_after/u_before = 1/(1 + C_A) = 0.5``
  when the drop and the pool have the same density (which they do in a
  single-component model).  This is the sharpest analytic anchor for the entry
  event itself.
* **Closed-box conservation.**  With periodic boundaries and no body force the
  total momentum ``sum(rho u)`` must be *exactly* conserved; every deviation is
  spurious (a discrete force-coupling anomaly).  Same for mass.

Measured anomaly metrics
------------------------
``u_max``       max |u_raw| in the box.  For a static droplet the exact answer is
                0; any non-zero value is a parasitic (spurious) current.
``mom_drift``   |P(t) - P(0)| / (M cs).  Exact answer 0.
``impact_ratio`` measured marker-cloud velocity ratio across the entry event,
                compared with the potential-flow value 0.5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from .sc_multiphase import (
    CS2,
    ShanChen,
    equilibrium,
    advect_markers,
    eos_pressure,
    pseudopotential,
)

__all__ = [
    "coexistence_densities",
    "slab_field",
    "droplet_field",
    "run_static_droplet",
    "run_entry",
    "potential_flow_impact_ratio",
    "CYLINDER_ADDED_MASS",
]

#: added-mass coefficient of a circular cylinder in unbounded potential flow,
#: normalised by the displaced-fluid mass (Lamb 1932, §71).
CYLINDER_ADDED_MASS = 1.0


def potential_flow_impact_ratio(rho_drop: float, rho_ambient: float,
                                C_A: float = CYLINDER_ADDED_MASS) -> float:
    """``u_after/u_before`` for an impulsive entry with constant added mass.

    Momentum before ``= m_d u0``; momentum of the *system* after the impulse
    ``= (m_d + m_a) u1`` with ``m_a = C_A rho_ambient V``, ``m_d = rho_drop V``::

        u1/u0 = rho_drop / (rho_drop + C_A rho_ambient)
    """
    return rho_drop / (rho_drop + C_A * rho_ambient)


def _tanh_profile(r: np.ndarray, R: float, width: float) -> np.ndarray:
    return 0.5 * (1.0 - np.tanh((r - R) / width))


def coexistence_densities(
    *, G: float = -5.0, rho0: float = 1.0, tau: float = 1.0, scheme: str = "guo",
    nx: int = 48, ny: int = 64, steps: int = 2400, seed: int = 3,
    noise: float = 0.05, g_ramp: int = 800, slab_steps: int = 800,
    width: float = 3.0,
) -> Dict[str, object]:
    """Measure the liquid/gas coexistence densities and the flat-interface tension.

    Stage 1: spinodal decomposition from a *uniform* density + 5% noise while the
    interaction strength is up-ramped (starting an SC run at ``G = -5`` from a
    sharp interface gives a lattice force ~0.4, Mach ~0.3, and blows up).
    Stage 2: rebuild a planar slab from the measured densities and read off the
    interface thickness and the planar-interface tension.

    No Maxwell construction is used: both phases and the tension are *measured*,
    so no assumption about the discrete free energy enters the error budget.
    """
    rng = np.random.default_rng(seed)
    rho0_field = 1.0 + noise * (rng.random((ny, nx)) - 0.5)
    sim = ShanChen(nx, ny, G=G, rho0=rho0, tau=tau, scheme=scheme,
                   g_ramp=g_ramp, rho_init=rho0_field)
    sim.step(steps)
    r = sim.rho
    lo, hi = np.percentile(r, 5), np.percentile(r, 95)
    thr = 0.5 * (lo + hi)
    rho_gas = float(r[r < thr].mean())
    rho_liquid = float(r[r > thr].mean())
    u_max_spinodal = float(sim.max_spurious_speed())

    # ---- stage 2: planar slab from the measured coexistence pair -------------
    y = np.arange(ny) + 0.5
    y_if = 0.5 * ny
    prof = 0.5 * (1.0 - np.tanh((y - y_if) / width))  # 1 above, 0 below
    rho_slab = rho_gas + (rho_liquid - rho_gas) * prof[:, None] * np.ones((1, nx))
    sim2 = ShanChen(nx, ny, G=G, rho0=rho0, tau=tau, scheme=scheme,
                    g_ramp=g_ramp, rho_init=rho_slab)
    sim2.step(slab_steps)
    y_prof = sim2.rho.mean(axis=1)
    p_prof = eos_pressure(y_prof, G, rho0)
    p_liq = float(eos_pressure(np.array(rho_liquid), G, rho0))
    # 10%-90% interface thickness and the normal-pressure deficit integral
    lo_v, hi_v = rho_gas + 0.1 * (rho_liquid - rho_gas), rho_gas + 0.9 * (rho_liquid - rho_gas)
    idx10 = int(np.argmax(y_prof < lo_v))
    idx90 = int(np.argmax(y_prof > hi_v))
    thickness = float(abs(idx90 - idx10))
    lo_i, hi_i = max(idx90 - 15, 0), min(idx10 + 15, ny)
    sigma_flat = float((p_liq - p_prof[lo_i:hi_i]).sum())
    return {
        "rho_liquid": rho_liquid,
        "rho_gas": rho_gas,
        "density_ratio": rho_liquid / rho_gas if rho_gas > 0 else float("inf"),
        "interface_thickness": thickness,
        "p_liquid": p_liq,
        "sigma_flat_interface": sigma_flat,
        "sigma_flat_window": [int(lo_i), int(hi_i)],
        "u_max_spinodal": u_max_spinodal,
        "u_max_after_relax": float(sim2.max_spurious_speed()),
        "steps": steps,
        "slab_steps": slab_steps,
        "g_ramp": g_ramp,
        "scheme": scheme,
        "G": G,
        "tau": tau,
        "rho_min_seen": float(np.min(sim.rho)),
        "rho_max_seen": float(np.max(sim.rho)),
    }


def droplet_field(
    *, nx: int, ny: int, R: float, rho_liquid: float, rho_gas: float,
    width: float = 1.5, cx: Optional[float] = None, cy: Optional[float] = None,
    uy: float = 0.0, rotate_deg: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Circular droplet (optionally lattice-rotated) + a velocity that is smeared
    with the same density profile, so the initial data carry no vortex sheet."""
    cx = 0.5 * nx if cx is None else cx
    cy = 0.5 * ny if cy is None else cy
    yy, xx = np.meshgrid(np.arange(ny) + 0.5, np.arange(nx) + 0.5, indexing="ij")
    dx, dy = xx - cx, yy - cy
    if rotate_deg:
        a = math.radians(rotate_deg)
        # rotate the *sample points* so that a 45-degree figure is produced on the
        # square lattice (no interpolation of the discrete field itself)
        dx, dy = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
    r = np.sqrt(dx * dx + dy * dy)
    phi = _tanh_profile(r, R, width)
    rho = rho_gas + (rho_liquid - rho_gas) * phi
    uy_field = uy * phi
    return rho, np.zeros_like(rho), uy_field


def slab_field(
    *, nx: int, ny: int, rho_liquid: float, rho_gas: float, y_interface: float,
    width: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Quiescent liquid pool occupying ``y < y_interface`` (periodic floor)."""
    y = np.arange(ny) + 0.5
    prof = 0.5 * (1.0 + np.tanh((y_interface - y) / width))
    rho = rho_gas + (rho_liquid - rho_gas) * prof[:, None] * np.ones((1, nx))
    return rho, np.zeros_like(rho), np.zeros_like(rho)


def run_static_droplet(
    *, R: float, rho_liquid: float, rho_gas: float, G: float = -5.0,
    rho0: float = 1.0, tau: float = 1.0, scheme: str = "guo",
    nx: int = 96, ny: int = 96, steps: int = 1500, rotate_deg: float = 0.0,
    width: float = 3.0, diag_every: int = 100, g_ramp: int = 600,
) -> Dict[str, object]:
    """Static droplet: parasitic currents, Laplace law, momentum drift."""
    rho, ux0, uy0 = droplet_field(
        nx=nx, ny=ny, R=R, rho_liquid=rho_liquid, rho_gas=rho_gas,
        width=width, rotate_deg=rotate_deg,
    )
    sim = ShanChen(nx, ny, G=G, rho0=rho0, tau=tau, scheme=scheme,
                   g_ramp=g_ramp, rho_init=rho, ux_init=ux0, uy_init=uy0)
    m0 = float(sim.rho.sum())
    ux, uy = sim.u_raw
    P0 = float((sim.rho * uy).sum())
    hist: List[Dict[str, float]] = []
    diverged = False
    kk = max(nx // 12, 3)
    for k in range(steps // diag_every):
        sim.step(diag_every)
        if not np.isfinite(sim.rho).all():
            diverged = True
            break
        ux, uy = sim.u_raw
        P = float((sim.rho * uy).sum())
        pp = eos_pressure(sim.rho, G, rho0)
        icx, icy = nx // 2, ny // 2
        p_in_k = float(pp[icy - 2: icy + 3, icx - 2: icx + 3].mean())
        corners = np.concatenate([pp[:kk, :kk].ravel(), pp[:kk, -kk:].ravel(),
                                  pp[-kk:, :kk].ravel(), pp[-kk:, -kk:].ravel()])
        hist.append({"step": sim.steps_done, "u_max": sim.max_spurious_speed(),
                     "pressure_drift": P - P0, "p_in": p_in_k,
                     "p_out": float(corners.mean())})
    rho_end = sim.rho
    thr = 0.5 * (rho_liquid + rho_gas)
    R_eff = sim.blob_radius(thr)
    # pressure: time-averaged over the last 40% of the run.  The instantaneous
    # field is dominated by the parasitic-current acoustic noise, so a single
    # snapshot is not a measurement (see the post-mortem).
    tail = hist[int(0.6 * len(hist)):] if hist else []
    if tail:
        p_in = float(np.mean([h["p_in"] for h in tail]))
        p_out = float(np.mean([h["p_out"] for h in tail]))
        p_in_std = float(np.std([h["p_in"] for h in tail]))
        p_out_std = float(np.std([h["p_out"] for h in tail]))
    else:
        p_in = p_out = p_in_std = p_out_std = float("nan")
    dP = p_in - p_out
    ux, uy = sim.u_raw
    speed = np.sqrt(ux * ux + uy * uy)
    interior = np.zeros_like(speed, dtype=bool)
    interior[k:-k, k:-k] = True
    # exclude the drop interior (where u must be ~0 by symmetry) from the metric?
    # No: include everything, the exact answer is zero everywhere.
    P_final = float((rho_end * uy).sum())
    M = float(rho_end.sum())
    return {
        "R": R,
        "R_eff": R_eff,
        "scheme": scheme,
        "tau": tau,
        "steps": steps,
        "G": G,
        "nx": nx,
        "ny": ny,
        "rho_liquid": rho_liquid,
        "rho_gas": rho_gas,
        "rotate_deg": rotate_deg,
        "delta_p": dP,
        "p_in": p_in,
        "p_out": p_out,
        "p_in_std": p_in_std,
        "p_out_std": p_out_std,
        "dp_uncertainty": float(np.hypot(p_in_std, p_out_std)) if hist else float("nan"),
        "sigma_laplace": 0.5 * dP * R_eff,          # 2D: Delta p = sigma / R
        "u_max": float(sim.max_spurious_speed()),
        "u_max_late": float(hist[-1]["u_max"]) if hist else float("nan"),
        "mass_drift": abs(M - m0) / m0,
        "mom_drift_frac": abs(P_final - P0) / (M * math.sqrt(CS2)),
        "rho_min": float(rho_end.min()),
        "rho_max": float(rho_end.max()),
        "history": hist,
        "diverged": diverged,
    }


def run_entry(
    *, rho_liquid: float, rho_gas: float, G: float = -5.0, rho0: float = 1.0,
    tau: float = 1.0, scheme: str = "guo", nx: int = 128, ny: int = 192,
    R_drop: float = 10.0, y_interface: float = 60.0, y_drop: float = 88.0,
    u0: float = 0.02, steps: int = 700, diag_every: int = 10,
    n_markers: int = 240, width: float = 3.0, g_ramp: int = 500,
    relax_steps: int = 600, spin_steps: int = 300,
) -> Dict[str, object]:
    """Droplet water entry: momentum budget, added-mass impact ratio, spurious peaks.

    The droplet is a Lagrangian marker cloud advected by the half-force velocity,
    which makes "how fast does the droplet move" well defined even after it
    coalesces with the pool.
    """
    rho_pool, ux0, uy0 = slab_field(nx=nx, ny=ny, rho_liquid=rho_liquid,
                                    rho_gas=rho_gas, y_interface=y_interface,
                                    width=width)
    rho_d, uxd, uyd = droplet_field(nx=nx, ny=ny, R=R_drop, rho_liquid=rho_liquid,
                                    rho_gas=rho_gas, width=width,
                                    cx=0.5 * nx, cy=y_drop, uy=-u0)
    # combine: the pool wins where it is dense, the droplet elsewhere
    phi_d = (rho_d - rho_gas) / max(rho_liquid - rho_gas, 1e-12)
    phi_p = (rho_pool - rho_gas) / max(rho_liquid - rho_gas, 1e-12)
    phi = np.maximum(phi_d, phi_p)
    rho = rho_gas + (rho_liquid - rho_gas) * phi
    sim = ShanChen(nx, ny, G=G, rho0=rho0, tau=tau, scheme=scheme,
                   g_ramp=relax_steps, rho_init=rho, ux_init=ux0, uy_init=uy0)
    # (i) relax both interfaces at rest, with the interaction strength up-ramped
    #     (a cold start at full G diverges: |F| ~ 0.4 lattice units, Mach ~ 0.3)
    sim.step(relax_steps)
    spurious_post_relax = float(sim.max_spurious_speed())
    # (ii) spin the droplet up to its impact velocity with a smooth body force
    #      confined to the droplet region.  Resetting the populations instead
    #      (f = feq(rho, u0)) puts a vortex sheet on the interface and diverges.
    phi_now = np.clip((sim.rho - rho_gas) / max(rho_liquid - rho_gas, 1e-12), 0.0, 1.0)
    yy, xx = np.meshgrid(np.arange(ny) + 0.5, np.arange(nx) + 0.5, indexing="ij")
    in_drop = (((xx - 0.5 * nx) ** 2 + (yy - y_drop) ** 2) < (1.3 * R_drop) ** 2)
    weight = phi_now * in_drop
    a_spin = u0 * (rho_liquid / max(rho_liquid - rho_gas, 1e-12)) / float(spin_steps)
    sim.body_F = np.stack([np.zeros_like(weight), -a_spin * weight])
    sim.G_ramp = 0
    sim.step(spin_steps)
    sim.body_F = np.zeros_like(sim.body_F)
    sim._refresh()
    spurious_post_spin = float(sim.max_spurious_speed())
    mach_post_spin = float(sim.stats().mach)
    M0 = float(sim.rho.sum())
    M0 = float(sim.rho.sum())
    ux_r, uy_r = sim.u_raw
    P0 = (float((sim.rho * ux_r).sum()), float((sim.rho * uy_r).sum()))
    # marker cloud: concentric rings filling the droplet
    rings = 6
    radii = np.linspace(0.25, 0.85, rings) * R_drop
    per = max(n_markers // rings, 8)
    th = np.linspace(0.0, 2 * math.pi, per, endpoint=False)
    px = np.concatenate([0.5 * nx + r * np.cos(th) for r in radii])
    py = np.concatenate([y_drop + r * np.sin(th) for r in radii])
    hist: List[Dict[str, float]] = []
    diverged = False
    for _ in range(steps // diag_every):
        sim.step(diag_every)
        if not np.isfinite(sim.rho).all():
            diverged = True
            break
        ux_p, uy_p = sim.u_phys
        px, py = advect_markers(px, py, ux_p, uy_p, dt=float(diag_every))
        ux_r, uy_r = sim.u_raw
        Px = float((sim.rho * ux_r).sum())
        Py = float((sim.rho * uy_r).sum())
        hist.append({
            "step": sim.steps_done,
            "marker_uy": float(np.mean(uy_p)),
            "marker_y": float(np.mean(py)),
            "u_max": sim.max_spurious_speed(),
            "mom_drift": math.hypot(Px - P0[0], Py - P0[1]),
            "rho_min": float(sim.rho.min()),
            "rho_max": float(sim.rho.max()),
        })
    M = float(sim.rho.sum())
    arr_u = np.array([h["marker_uy"] for h in hist])
    arr_y = np.array([h["marker_y"] for h in hist])
    arr_s = np.array([h["step"] for h in hist], dtype=float)
    # --- windows: before impact / fully immersed -------------------------------
    y_lead = arr_y
    immersed = y_lead < (y_interface - R_drop)     # marker centroid below interface
    if arr_u.size == 0:
        return {"scheme": scheme, "tau": tau, "G": G, "diverged": True,
                "impact_ratio": float("nan"), "u_before": float("nan"),
                "u_after": float("nan"), "peak_spurious_speed": float("nan"),
                "impact_ratio_predicted": potential_flow_impact_ratio(rho_liquid, rho_liquid),
                "impact_ratio_error_pct": float("nan"), "history": [],
                "final_mom_drift": float("nan"), "mass_drift": float("nan"),
                "u0": u0, "R_drop": R_drop, "nx": nx, "ny": ny, "steps": steps,
                "y_interface": y_interface, "rho_min": float("nan"),
                "rho_max": float("nan"), "u_after_late": float("nan"),
                "impact_step": float("nan"), "max_mom_drift": float("nan"),
                "breakdown_step": int(sim.steps_done),
                "spurious_post_relax": spurious_post_relax,
                "spurious_post_spin": spurious_post_spin,
                "mach_post_spin": mach_post_spin}
    impact_detected = bool(immersed.any())
    i_imm = int(np.argmax(immersed)) if impact_detected else len(arr_u) - 1
    pre = arr_u[arr_s < max(arr_s[i_imm] - 150.0, arr_s.min())]
    u_before = float(np.median(pre)) if pre.size else float("nan")
    j = i_imm + int(50 // diag_every)
    j = min(j, len(arr_u) - 1)
    u_after = float(arr_u[j])
    u_after_late = float(np.median(arr_u[-max(20, len(arr_u) // 8):]))
    peak_spur = float(np.max(np.array([h["u_max"] for h in hist])))
    mom = np.array([h["mom_drift"] for h in hist])
    pred = potential_flow_impact_ratio(rho_liquid, rho_liquid)
    return {
        "scheme": scheme,
        "tau": tau,
        "G": G,
        "nx": nx,
        "ny": ny,
        "R_drop": R_drop,
        "u0": u0,
        "y_interface": y_interface,
        "steps": steps,
        "impact_step": float(arr_s[i_imm]) if immersed.any() else float("nan"),
        "u_before": u_before,
        "u_after": u_after,
        "u_after_late": u_after_late,
        "impact_detected": impact_detected,
        "impact_ratio": (u_after / u_before) if (impact_detected and u_before) else float("nan"),
        "impact_ratio_predicted": pred,
        "impact_ratio_error_pct": (100.0 * (u_after / u_before - pred) / pred)
                                  if (impact_detected and u_before) else float("nan"),
        "peak_spurious_speed": peak_spur,
        "max_mom_drift": float(mom.max()),
        "final_mom_drift": float(mom[-1]),
        "mass_drift": abs(M - M0) / M0,
        "rho_min": float(min(h["rho_min"] for h in hist)),
        "rho_max": float(max(h["rho_max"] for h in hist)),
        "history": hist,
        "diverged": diverged,
        "breakdown_step": int(sim.steps_done) if diverged else None,
        "spurious_post_relax": spurious_post_relax,
        "spurious_post_spin": spurious_post_spin,
        "mach_post_spin": mach_post_spin,
    }