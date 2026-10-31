"""Acceptance-criteria harness for mission W3-N08 (wind-current-ice manoeuvring).

Criteria implemented
--------------------
(a) ``wind_load``      wind load at 4 relative wind angles vs an independent
                       projected-area formulation, plus the published IMO IS
                       Code 2008 beam-wind pressure anchor; target < 10 %
(b) ``current_load``   current load at 3 current speeds vs an independent strip
                       cross-flow + ITTC-57 reference; target < 10 %
(c) ``turning``        coupled wind/current/ice turning circle: time-domain vs an
                       independent steady-state equilibrium solve (< 10 %),
                       time-step convergence (< 1 %), a second independently
                       derived coefficient set (sensitivity band), the coupled
                       case matrix and physics trend checks
(d) ``pytest``         unit-test suite, >= 12 passing tests (count injected by
                       the runner from the pytest output)

Every row carries its ``reference_basis`` and an evidence grade, so a reader can
separate model-to-reference checks (REAL), model-vs-model uncertainty bands
(WITHHELD) and genuinely unavailable external data (WITHHELD, with the reason).
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from . import data as D
from . import loads as L
from . import mmg as M

MISSION_ID = "W3-N08"
THRESHOLD_PCT = 10.0

WIND_ANGLES = (0.0, 45.0, 90.0, 135.0)
WIND_SPEED = 20.0          # m/s, ~ Beaufort 8 design wind of the validation
CURRENT_SPEEDS = (0.5, 1.0, 2.0)
CURRENT_ANGLE = 60.0       # deg, quartering current (dominant drift case)

COUPLED_CASES = (
    ("calm_water", M.Environment()),
    ("wind_20ms_beam", M.Environment(wind_speed=20.0, wind_from_deg=90.0)),
    ("current_1ms_beam", M.Environment(current_speed=1.0,
                                       current_from_deg=90.0)),
    ("ice_1m", M.Environment(ice_thickness=1.0)),
    ("three_field_coupled", M.Environment(wind_speed=20.0, wind_from_deg=90.0,
                                          current_speed=1.0,
                                          current_from_deg=90.0,
                                          ice_thickness=1.0)),
)


def _rel_err(model: float, ref: float) -> float:
    """Relative error of ``model`` w.r.t. ``ref`` [%] (|ref| based)."""
    if ref is None or abs(ref) < 1e-12:
        return float("nan")
    return abs(model - ref) / abs(ref) * 100.0


# ---------------------------------------------------------------------------
# (a) Wind
# ---------------------------------------------------------------------------
def wind_criterion(vessel: D.Vessel = D.VESSEL_VLCC,
                   speed: float = WIND_SPEED,
                   angles=WIND_ANGLES) -> Dict[str, object]:
    rows: List[Dict[str, float]] = []
    for psi in angles:
        m = L.wind_load(vessel, speed, psi, model="ocimf")
        r = L.wind_load(vessel, speed, psi, model="projected")
        rows.append({
            "psi_deg": psi,
            "model_force_kN": m.magnitude / 1e3,
            "ref_force_kN": r.magnitude / 1e3,
            "force_rel_err_pct": _rel_err(m.magnitude, r.magnitude),
            "model_X_kN": m.X / 1e3, "ref_X_kN": r.X / 1e3,
            "model_Y_kN": m.Y / 1e3, "ref_Y_kN": r.Y / 1e3,
            "model_N_MNm": m.N / 1e6, "ref_N_MNm": r.N / 1e6,
            "moment_rel_err_pct": (_rel_err(m.N, r.N)
                                   if abs(r.N) > 1.0 else None),
        })
    force_errs = [row["force_rel_err_pct"] for row in rows]
    moment_errs = [row["moment_rel_err_pct"] for row in rows
                   if row["moment_rel_err_pct"] is not None]
    max_err = max(force_errs + moment_errs)

    beam = L.wind_load(vessel, speed, 90.0, model="ocimf")
    imo_pressure = L.imo_beam_wind_pressure(speed)
    model_pressure = abs(beam.Y) / vessel.A_L
    return {
        "criterion": "a_wind_load",
        "wind_speed_ms": speed,
        "rows": rows,
        "max_rel_err_pct": max_err,
        "max_force_rel_err_pct": max(force_errs),
        "max_moment_rel_err_pct": max(moment_errs),
        "threshold_pct": THRESHOLD_PCT,
        "passed": max_err < THRESHOLD_PCT,
        "imo_anchor": {
            "psi_deg": 90.0,
            "model_pressure_Pa": model_pressure,
            "imo_pressure_Pa": imo_pressure,
            "rel_err_pct": _rel_err(model_pressure, imo_pressure),
            "reference": "IMO 2008 IS Code, Pt.A ch.3: P = 0.504 V^2 [Pa]",
        },
        "reference_basis": (
            "independent projected-area model (C_D_frontal = 0.80 on A_F, "
            "C_D_lateral = 0.85 on A_L - textbook flat-plate values) at "
            "psi = 0/45/90/135 deg, plus the published IMO IS Code 2008 "
            "beam-wind pressure at psi = 90 deg"
        ),
        "evidence": ("REAL (model-to-independent-formulation + published IMO "
                     "anchor). The OCIMF 1994 guide tables themselves are not "
                     "machine-readable offline, so the literal 'vs OCIMF tables' "
                     "claim is WITHHELD - see LIMITATIONS"),
    }


# ---------------------------------------------------------------------------
# (b) Current
# ---------------------------------------------------------------------------
def current_criterion(vessel: D.Vessel = D.VESSEL_VLCC,
                      speeds=CURRENT_SPEEDS,
                      psi: float = CURRENT_ANGLE) -> Dict[str, object]:
    rows: List[Dict[str, float]] = []
    for v_c in speeds:
        m = L.current_load(vessel, v_c, psi, model="ocimf")
        r = L.current_load(vessel, v_c, psi, model="reference")
        rows.append({
            "current_speed_ms": v_c,
            "psi_deg": psi,
            "model_force_kN": m.magnitude / 1e3,
            "ref_force_kN": r.magnitude / 1e3,
            "force_rel_err_pct": _rel_err(m.magnitude, r.magnitude),
            "model_X_kN": m.X / 1e3, "ref_X_kN": r.X / 1e3,
            "model_Y_kN": m.Y / 1e3, "ref_Y_kN": r.Y / 1e3,
            "model_N_MNm": m.N / 1e6, "ref_N_MNm": r.N / 1e6,
            "moment_rel_err_pct": _rel_err(m.N, r.N),
            "ref_Cf": r.components["Cf"],
            "ref_Re": r.components["Re"],
        })
    force_errs = [row["force_rel_err_pct"] for row in rows]
    moment_errs = [row["moment_rel_err_pct"] for row in rows]
    max_err = max(force_errs + moment_errs)
    return {
        "criterion": "b_current_load",
        "rows": rows,
        "max_rel_err_pct": max_err,
        "max_force_rel_err_pct": max(force_errs),
        "max_moment_rel_err_pct": max(moment_errs),
        "threshold_pct": THRESHOLD_PCT,
        "passed": max_err < THRESHOLD_PCT,
        "reference_basis": (
            "independent formulation: strip cross-flow drag (C_D = 1.0 on the "
            "lateral wetted area) plus ITTC-57 skin friction for the axial "
            "component, Reynolds number evaluated per speed (so the speed "
            "dependence is genuinely independent, not shared by construction)"
        ),
        "evidence": ("REAL (model-to-independent-formulation). OCIMF current "
                     "curves not machine-readable offline -> 'vs OCIMF' WITHHELD"),
    }


# ---------------------------------------------------------------------------
# Ice (supporting evidence - the mission sets no explicit ice-load threshold)
# ---------------------------------------------------------------------------
def ice_uncertainty_band(vessel: D.Vessel = D.VESSEL_VLCC,
                         thicknesses=(0.5, 1.0, 1.5),
                         speeds=(0.0, 1.0, 2.0)) -> Dict[str, object]:
    rows = []
    for h in thicknesses:
        ice = D.IceCondition(thickness=h)
        for v in speeds:
            a = L.riska_ice_resistance(vessel, ice, v)
            b = L.lindqvist_like_ice_resistance(vessel, ice, v)
            rows.append({"thickness_m": h, "speed_ms": v,
                         "riska_kN": a.total / 1e3,
                         "lindqvist_like_kN": b.total / 1e3,
                         "deviation_pct": _rel_err(a.total, b.total)})
    dev = [r["deviation_pct"] for r in rows]
    return {
        "criterion": "ice_resistance_cross_model",
        "rows": rows,
        "max_deviation_pct": max(dev),
        "mean_deviation_pct": sum(dev) / len(dev),
        "threshold_pct": None,
        "passed": None,
        "reference_basis": (
            "two independent semi-empirical decompositions: Riska-type "
            "(crushing/breaking + velocity + friction) vs Lindqvist-type "
            "(stem bending + buoyancy-deficient submergence + sliding)"
        ),
        "evidence": ("WITHHELD for coefficient-level validation (original "
                     "papers unavailable offline). The band is reported as "
                     "model uncertainty; the internal scaling laws are "
                     "verified by unit tests"),
    }


# ---------------------------------------------------------------------------
# (c) Turning circle / zigzag
# ---------------------------------------------------------------------------
def turning_case_matrix(vessel: D.Vessel = D.VESSEL_VLCC,
                        speed: float = 7.717,
                        rudder_deg: float = 35.0,
                        dt: float = 0.5,
                        t_max: float = 3000.0) -> Dict[str, object]:
    """Run every coupled case: time-domain manoeuvre + steady-state solve."""
    rows = []
    detailed = {}
    for name, env in COUPLED_CASES:
        res = M.simulate_turning_circle(vessel, speed=speed,
                                        rudder_deg=rudder_deg, dt=dt,
                                        t_max=t_max, env=env, name=name)
        ss = M.steady_turning_solution(vessel, speed=speed,
                                       rudder_deg=rudder_deg, env=env)
        m = res.metrics
        rows.append({
            "case": name,
            "environment": res.environment,
            "tactical_diameter_over_L": m["tactical_diameter_over_L"],
            "advance_over_L": m["advance_over_L"],
            "transfer_over_L": m["transfer_over_L"],
            "time_domain_drift_deg": m["steady_drift_angle_deg"],
            "time_domain_speed_ratio": m["steady_speed_ratio"],
            "steady_turning_diameter_over_L": ss["steady_turning_diameter_over_L"],
            "steady_speed_ratio": ss["speed_ratio"],
            "steady_drift_angle_deg": ss["drift_angle_deg"],
            "steady_r_prime": ss["r_prime"],
            "steady_yaw_rate_degs": ss["yaw_rate_degs"],
            "time_to_360_deg": m["time_to_360_deg"],
        })
        detailed[name] = {"time_domain": m, "steady_state": ss}
    return {"rows": rows, "detailed": detailed}


def turning_criterion(vessel: D.Vessel = D.VESSEL_VLCC,
                      speed: float = 7.717,
                      rudder_deg: float = 35.0) -> Dict[str, object]:
    base_env = M.Environment()
    td = M.simulate_turning_circle(vessel, speed=speed, rudder_deg=rudder_deg,
                                   dt=0.5, t_max=3000.0, env=base_env)
    ss = M.steady_turning_solution(vessel, speed=speed, rudder_deg=rudder_deg,
                                   env=base_env)
    td_fine = M.simulate_turning_circle(vessel, speed=speed,
                                        rudder_deg=rudder_deg, dt=0.25,
                                        t_max=3000.0, env=base_env)
    coef_b = M.slender_body_coefficients(vessel)
    td_b = M.simulate_turning_circle(vessel, coef=coef_b, speed=speed,
                                     rudder_deg=rudder_deg, dt=0.5,
                                     t_max=3000.0, env=base_env)

    err_td_vs_ss = _rel_err(td.metrics["tactical_diameter"],
                            ss["steady_turning_diameter"])
    err_dt = _rel_err(td_fine.metrics["tactical_diameter"],
                      td.metrics["tactical_diameter"])
    err_coef = _rel_err(td_b.metrics["tactical_diameter"],
                        td.metrics["tactical_diameter"])

    matrix = turning_case_matrix(vessel, speed, rudder_deg)
    rows = matrix["rows"]
    base = next(r for r in rows if r["case"] == "calm_water")
    for r in rows:
        r["delta_steady_diameter_pct"] = _rel_err(
            r["steady_turning_diameter_over_L"],
            base["steady_turning_diameter_over_L"])
        r["delta_drift_deg"] = (r["steady_drift_angle_deg"]
                                - base["steady_drift_angle_deg"])
    by_case = {r["case"]: r for r in rows}

    trends = {
        "ice_reduces_steady_speed":
            by_case["ice_1m"]["steady_speed_ratio"]
            < by_case["calm_water"]["steady_speed_ratio"],
        "ice_increases_steady_turning_diameter":
            by_case["ice_1m"]["steady_turning_diameter_over_L"]
            > by_case["calm_water"]["steady_turning_diameter_over_L"],
        "beam_current_increases_drift_angle":
            abs(by_case["current_1ms_beam"]["steady_drift_angle_deg"])
            > abs(by_case["calm_water"]["steady_drift_angle_deg"]),
        "beam_wind_increases_drift_angle":
            abs(by_case["wind_20ms_beam"]["steady_drift_angle_deg"])
            > abs(by_case["calm_water"]["steady_drift_angle_deg"]),
        "coupled_case_exceeds_calm_turning_diameter":
            by_case["three_field_coupled"]["steady_turning_diameter_over_L"]
            > by_case["calm_water"]["steady_turning_diameter_over_L"],
    }

    verification_checks = [
        {"check": "time_domain_vs_steady_state_equilibrium",
         "value_a": td.metrics["tactical_diameter"],
         "value_b": ss["steady_turning_diameter"],
         "unit": "m",
         "rel_err_pct": err_td_vs_ss, "threshold_pct": THRESHOLD_PCT,
         "passed": err_td_vs_ss < THRESHOLD_PCT,
         "reference_basis": "independent damped-Newton equilibrium solve of the "
                            "same MMG equations (X=0, Y=(m+m_x)ur, N=0) - calm "
                            "water, 35 deg rudder"},
        {"check": "time_step_convergence_dt_0.5_vs_0.25",
         "value_a": td.metrics["tactical_diameter"],
         "value_b": td_fine.metrics["tactical_diameter"],
         "unit": "m",
         "rel_err_pct": err_dt, "threshold_pct": 1.0,
         "passed": err_dt < 1.0,
         "reference_basis": "RK4 time-step refinement"},
    ]
    sensitivity = {
        "check": "coefficient_set_sensitivity",
        "value_a": td.metrics["tactical_diameter"],
        "value_b": td_b.metrics["tactical_diameter"],
        "unit": "m",
        "rel_err_pct": err_coef,
        "threshold_pct": THRESHOLD_PCT,
        "passed": err_coef < THRESHOLD_PCT,
        "reference_basis": ("independent slender-body / cross-flow derivative "
                            "set (kvlcc2_reference vs slender_body) - quantifies "
                            "the uncalibrated hydrodynamic uncertainty"),
    }

    verification_passed = all(c["passed"] for c in verification_checks)
    return {
        "criterion": "c_coupled_turning_circle",
        "verification_checks": verification_checks,
        "sensitivity": sensitivity,
        "verification_passed": verification_passed,
        "case_matrix": rows,
        "trend_checks": trends,
        "all_trends_passed": all(trends.values()),
        "threshold_pct": THRESHOLD_PCT,
        "baseline_steady_state": ss,
        "baseline_time_domain": td.metrics,
        "coupled_steady_state": matrix["detailed"]["three_field_coupled"][
            "steady_state"],
        "passed": verification_passed and all(trends.values()),
        "absolute_accuracy_claim": "WITHHELD",
        "reference_basis": (
            "code-to-code verification against an independent equilibrium "
            "solve and a refined time step; absolute accuracy vs model tests "
            "is NOT claimed"
        ),
        "evidence": (
            "The literal criterion 'turning diameter vs ice-basin manoeuvring "
            "trials, deviation < 10 %' is WITHHELD: no ice-basin/EFD manoeuvring "
            "data exist in the worktree and general network access is blocked, "
            "so that comparison cannot be made honestly. The coefficient-set "
            "sensitivity above ("
            f"{err_coef:.1f} %) quantifies why an absolute <10 % claim would be "
            "unsupported without experimental calibration."
        ),
    }


def zigzag_cases(vessel: D.Vessel = D.VESSEL_VLCC, speed: float = 7.717,
                 rudder_deg: float = 10.0, heading_deg: float = 10.0
                 ) -> Dict[str, object]:
    calm = M.simulate_zigzag(vessel, speed=speed, rudder_deg=rudder_deg,
                             heading_deg=heading_deg, env=M.Environment(),
                             t_max=2000.0)
    coupled = M.simulate_zigzag(
        vessel, speed=speed, rudder_deg=rudder_deg, heading_deg=heading_deg,
        env=M.Environment(wind_speed=20.0, wind_from_deg=90.0,
                          current_speed=1.0, current_from_deg=90.0,
                          ice_thickness=1.0),
        name="zigzag_10_10_coupled", t_max=2000.0)
    return {"calm_water": calm.metrics, "three_field_coupled": coupled.metrics}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def run_validation(vessel: D.Vessel = D.VESSEL_VLCC) -> Dict[str, object]:
    wind = wind_criterion(vessel)
    current = current_criterion(vessel)
    turning = turning_criterion(vessel)
    ice = ice_uncertainty_band(vessel)
    zig = zigzag_cases(vessel)
    return {
        "mission_id": MISSION_ID,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "vessel": {"name": vessel.name, "particulars": vessel.particulars()},
        "criteria": {
            "a_wind_load": wind,
            "b_current_load": current,
            "c_coupled_turning_circle": turning,
            "ice_resistance": ice,
        },
        "zigzag": zig,
        "loader_provenance": {
            "wind_anchor": D.WIND_ANCHOR_PROVENANCE,
            "current_anchor": D.CURRENT_ANCHOR_PROVENANCE,
            "riska": D.RISKA_COEFFICIENTS["provenance"],
            "mmg_coefficients": M.KVLCC2_REFERENCE.as_dict(),
            "mmg_second_set": M.slender_body_coefficients(vessel).as_dict(),
            "rudder_race_max": M.RUDDER_RACE_MAX,
        },
        "checks_passed": {
            "a_wind_load": wind["passed"],
            "b_current_load": current["passed"],
            "c_verification": turning["verification_passed"],
            "c_trends": turning["all_trends_passed"],
        },
        "withheld": [
            "'wind load vs OCIMF 1994 guide tables' - guide tables not "
            "machine-readable offline; substituted by an independent "
            "projected-area formulation + the published IMO IS Code 2008 beam "
            "anchor",
            "'current load vs OCIMF current curves' - same reason; substituted "
            "by strip cross-flow + ITTC-57 reference",
            "'turning diameter vs ice-basin manoeuvring trials < 10%' - no "
            "ice-basin trial data available offline; substituted by code-to-code "
            "verification (equilibrium solve, dt refinement) plus an explicit "
            "coefficient-set sensitivity band",
            "'ice resistance vs original Riska/Lindqvist coefficients' - papers "
            "unavailable offline; reported as a two-model uncertainty band",
        ],
    }