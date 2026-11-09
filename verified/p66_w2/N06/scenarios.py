"""End-to-end integration scenarios for the P66 multi-physics chain.

Each scenario is one full pass of the chain

``N03 abrasion-field conditions -> N05 abrasion evolution -> N04 resistance
chain -> interface exchange -> contract validation``

on one ice condition, and returns its own numbers *and* its own verdict.  The
scenario table is the gate's "does the integrated thing actually run" evidence:
criterion (4) requires at least five of them to pass, and there is deliberately
one **expected-refusal** scenario, because a chain that cannot say "no" outside
its validity domain is not integrable -- it would silently extrapolate.

Scenario kinds
--------------
``nominal``     inside the resistance chain's validity domain, expected ``PASS``.
``truncation``  outside the domain, evaluated non-strictly: the chain must flag
                ``in_domain=False`` and the scenario then reports ``FLAGGED``
                (an honest, machine-checkable non-pass -- never a silent PASS).
``refusal``     outside the domain, evaluated strictly: ``IceDomainError`` must
                be raised; the scenario passes only if the refusal happens.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from ...ice.resistance import (
    DomainLimits,
    IceDomainError,
    ShipGeometry,
    delivered_power_MW,
)
from . import conservation as K
from . import contracts as C
from . import producers as P
from . import parameter_space as PS


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    label: str
    h_m: float
    V_ms: float
    kind: str = "nominal"
    note: str = ""


SCENARIO_SPECS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("S1", "level ice design point (h = 1.0 m, V = 3.9 kn)", 1.0, 2.0,
                 note="campaign design condition of the PC6 reference ship"),
    ScenarioSpec("S2", "thin ice, high speed (h = 0.35 m, V = 7.8 kn)", 0.35, 4.0,
                 note="submersion/friction dominated branch (Fr_h large)"),
    ScenarioSpec("S3", "heavy ice near the validity limit (h = 2.2 m, V = 2.9 kn)", 2.2, 1.5,
                 note="h/h_limit = 0.92: crushing dominated, strongest abrasion field"),
    ScenarioSpec("S4", "minimum speed / hovering (h = 1.5 m, V = 1.0 kn)", 1.5, 0.5,
                 note="quasi-static crushing, wear-rate floor of the operating envelope"),
    ScenarioSpec("S5", "high-speed edge (h = 0.8 m, V = 15.6 kn)", 0.8, 8.0,
                 note="Fr_L = 0.21, below the 0.25 Froude limit of the empirical laws"),
    ScenarioSpec("S6", "thick ice off-domain, non-strict (h = 2.8 m > h_limit)", 2.8, 1.5,
                 kind="truncation",
                 note="chain must flag in_domain=False and truncate h, never extrapolate"),
    ScenarioSpec("S7", "thick ice off-domain, strict (h = 2.8 m > h_limit)", 2.8, 1.5,
                 kind="refusal",
                 note="strict evaluation must refuse with IceDomainError"),
)


@dataclass
class ScenarioResult:
    scenario_id: str
    label: str
    kind: str
    h_m: float
    V_ms: float
    status: str
    numbers: dict[str, float] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status in ("PASS", "PASS(expected-refusal)")

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "kind": self.kind,
            "h_m": self.h_m,
            "V_ms": self.V_ms,
            "V_kn": self.V_ms / 0.514444,
            "status": self.status,
            "passed": self.passed,
            "numbers": self.numbers,
            "checks": self.checks,
            "notes": self.notes,
        }


def _check(name: str, ok: bool, value: float | None = None, limit: float | None = None,
           detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "value": value, "limit": limit, "detail": detail}


def run_scenario(spec: ScenarioSpec, *, ship: ShipGeometry, belt: P.BeltGeometry,
                 limits: DomainLimits | None = None,
                 coarse: K.InterfaceMesh | None = None,
                 fine: K.InterfaceMesh | None = None) -> ScenarioResult:
    """Run one end-to-end scenario."""
    limits = limits or DomainLimits()
    coarse = coarse or K.InterfaceMesh(belt.edges(), belt.height_m)
    fine = fine or K.graded_interface_mesh(0.0, belt.length_m, 4 * belt.n_patches, belt.height_m)
    result = ScenarioResult(spec.scenario_id, spec.label, spec.kind, spec.h_m, spec.V_ms, status="UNSET")

    # ---- N04 in strict mode first: the refusal scenario stops here ----------
    if spec.kind == "refusal":
        try:
            P.produce_n04(ship, [(spec.h_m, spec.V_ms)], strict=True, limits=limits)
            result.status = "FAIL"
            result.checks.append(_check("strict_refusal", False, None, None,
                                        "chain evaluated an out-of-domain ice state instead of refusing"))
        except IceDomainError as exc:
            result.status = "PASS(expected-refusal)"
            result.checks.append(_check("strict_refusal", True, None, None, str(exc)))
            result.notes.append("expected refusal: out-of-domain ice state rejected by the chain")
        return result

    strict = False
    n03, n04, n05 = P.produce_chain(ship, [(spec.h_m, spec.V_ms)], belt=belt, strict=strict)

    # ---- (3) contracts -----------------------------------------------------
    contracts = C.validate_cross_module(n03, n04, n05)
    failed = [c.check_id for c in contracts.checks if not c.ok]
    result.checks.append(_check("contracts", contracts.ok, float(contracts.n_pass),
                               float(len(contracts.checks)),
                               "failed: " + ", ".join(failed[:5]) if failed else "all checks pass"))

    # ---- (1) interface conservation ---------------------------------------
    exchange = K.build_exchange_payload(n03, 0, coarse=coarse, fine=fine)
    exchange_checks = K.validate_exchange_payload(exchange)
    result.checks.append(_check("exchange_contract", all(c.ok for c in exchange_checks),
                               float(sum(1 for c in exchange_checks if c.ok)),
                               float(len(exchange_checks)),
                               "failed: " + ", ".join(c.check_id for c in exchange_checks if not c.ok)
                               if any(not c.ok for c in exchange_checks) else "all checks pass"))
    cons = K.exchange_conservation(n03, 0, coarse=coarse, fine=fine, tolerance=K.CONSERVATION_TOL)
    result.checks.append(_check("mass_conservation", cons.mass_residual < cons.tolerance,
                               cons.mass_residual, cons.tolerance))
    result.checks.append(_check("momentum_conservation", cons.momentum_residual < cons.tolerance,
                                cons.momentum_residual, cons.tolerance))
    result.checks.append(_check("third_law", cons.third_law_residual < cons.tolerance,
                                cons.third_law_residual, cons.tolerance))
    result.checks.append(_check("negative_controls_discriminating", cons.discriminating,
                                min(cons.negative_controls.values()), K.NEGATIVE_CONTROL_FLOOR))

    # ---- physics numbers of the scenario ----------------------------------
    sample03 = n03["samples"][0]
    sample04 = n04["samples"][0]
    sample05 = n05["samples"][0]
    f_c = float(n03["contact_area_ratio"])
    mu = float(n03["ice"]["mu_hull_ice"])
    areas = np.asarray(sample03["area_m2"], dtype=float)
    p = np.asarray(sample03["p_ice_Pa"], dtype=float)
    theta = np.asarray(sample03["alpha_rad"], dtype=float)
    v_slip = np.asarray(sample03["v_slip_ms"], dtype=float)
    mass_flux = float(f_c * float(n03["ice"]["rho_ice_kg_m3"]) * np.sum(areas * np.asarray(sample03["u_n_ms"], dtype=float)))
    normal_force = float(f_c * np.sum(areas * p))
    tangential_force = float(f_c * mu * np.sum(areas * p))
    power_mw = delivered_power_MW(sample04["R_total_N"], sample04["V_ms"], ship.eta_D)

    result.numbers = {
        "R_crush_N": sample04["R_crush_N"],
        "R_break_N": sample04["R_break_N"],
        "R_submersion_N": sample04["R_submersion_N"],
        "R_ice_N": sample04["R_ice_N"],
        "R_ow_N": sample04["R_ow_N"],
        "R_total_N": sample04["R_total_N"],
        "R_ow_fraction": sample04["R_ow_N"] / sample04["R_total_N"],
        "p_ice_mean_Pa": float(np.mean(p)),
        "p_ice_peak_Pa": float(np.max(p)),
        "p_contact_mean_Pa": float(np.mean(p) / f_c),
        "v_slip_mean_ms": float(np.mean(v_slip)),
        "normal_force_N": normal_force,
        "tangential_force_N": tangential_force,
        "mass_flux_kg_s": mass_flux,
        "mean_depth_mm": sample05["mean_depth_m"] * 1e3,
        "peak_depth_mm": float(np.max(sample05["depth_m"])) * 1e3,
        "mass_loss_kg": sample05["mass_loss_kg"],
        "delivered_power_MW": power_mw,
        "mass_residual": cons.mass_residual,
        "momentum_residual": cons.momentum_residual,
        "third_law_residual": cons.third_law_residual,
        "quadrature_residual": cons.quadrature_residual,
        "observed_order": None if cons.observed_order is None else cons.observed_order,
        "nc_nearest_mass": cons.negative_controls["nearest_neighbour_mass"],
        "nc_belt_mean_normal": cons.negative_controls["belt_mean_normal_momentum"],
    }

    result.checks.append(_check("in_domain", bool(sample04["in_domain"]), None, None,
                                sample04["domain_note"]))
    result.checks.append(_check("positive_resistance", sample04["R_total_N"] > 0.0,
                                sample04["R_total_N"], 0.0))
    # delivered power is an *operational* note, not an integration verdict: the
    # scenarios test whether the chain is consistent, not whether this hull has
    # the power plant for that ice condition (ram capability is a design study).
    if power_mw > ship.installed_power_MW:
        result.notes.append(
            f"delivered power {power_mw:.1f} MW exceeds installed {ship.installed_power_MW:.1f} MW "
            f"-- outside the ship's power capability, recorded as an operational note")
    result.checks.append(_check("abrasion_depth_band", 0.0 < result.numbers["peak_depth_mm"] < 500.0,
                                result.numbers["peak_depth_mm"], 500.0,
                                "peak wear depth over the 100 h exposure"))

    hard_failures = [c for c in result.checks if not c["ok"]]
    if spec.kind == "truncation":
        # non-strict out-of-domain evaluation must never report a clean pass
        result.status = "FLAGGED"
        result.notes.append(
            "non-strict out-of-domain evaluation: chain truncated the ice state and "
            "flagged in_domain=False; the (h,V) key itself changes, which the "
            "cross-module key-consistency check reports")
    else:
        result.status = "PASS" if not hard_failures else "FAIL"
    if hard_failures:
        result.notes.extend(f"{c['name']}: {c['detail']}" for c in hard_failures)
    return result


def run_all_scenarios(*, ship: ShipGeometry | None = None,
                      belt: P.BeltGeometry | None = None) -> list[ScenarioResult]:
    ship = ship or P.reference_ship()
    belt = belt or P.belt_geometry(ship)
    coarse = K.InterfaceMesh(belt.edges(), belt.height_m)
    fine = K.graded_interface_mesh(0.0, belt.length_m, 4 * belt.n_patches, belt.height_m)
    return [run_scenario(spec, ship=ship, belt=belt, coarse=coarse, fine=fine)
            for spec in SCENARIO_SPECS]