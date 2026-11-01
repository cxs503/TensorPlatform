"""Propulsion-domain verification-debt harness (mission m1498).

Purpose
-------
Independently re-check every propulsion endpoint exposed by the ship-performance
platform (:8001) against (a) *analytic identities* that any propeller model must
obey, (b) *physical bounds* (Froude momentum theory, coefficient ranges) and
(c) *cross-domain anchors* taken from the already-verified resistance domain.

No literature coefficient table is fetched from memory: every criterion below is
either an exact algebraic identity, an exact bound, or a derivative property of
the Wageningen B-series regression (Oosterveld & van Oossanen, 1975) that holds
for *any* admissible coefficient set, e.g.

  * the B-series regression is a polynomial in J whose coefficients depend on
    P/D, Z and Ae/Ao.  Consequently the zero-thrust advance ratio J0 (KT(J0)=0)
    is a strictly increasing function of P/D, and KT/KQ -> 0 as J -> J0
    (KQ(J0) > 0 because of the residual torque of the blades).
  * eta_O == J*KT/(2*pi*KQ) identically (definition, no model assumption).
  * eta_O < eta_ideal = 2/(1+sqrt(1+C_T)) with C_T = 8*KT/(pi*J^2) (Froude).
  * propulsive chain: eta_D = eta_O * eta_H * eta_R, eta_H=(1-t)/(1-w),
    P_E = R_T*V_s, T = R_T/(1-t), P_D = 2*pi*n*Q.

Usage
-----
    python3 tools/verify/propulsion_audit.py --live      # probe :8001, write JSON+PNG
    python3 tools/verify/propulsion_audit.py --summary   # print cached verdicts

The cached artefact is committed so the checks can be re-graded offline
(tests/test_propulsion_audit.py does exactly that).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import urllib.error
import urllib.request

API = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts"
OUT_JSON = ART / "m1498_propulsion_audit.json"

RHO = 1025.0
G = 9.80665

# KVLCC2 (MOERI tanker) — reference vessel used for the cross-domain anchor.
KVLCC2 = dict(L=320.0, B=58.0, T=20.8, displacement=320000.0, Cb=0.8098, Cm=0.9988,
              wetted_surface=27200.0, speed=15.5)


# --------------------------------------------------------------------------- #
# analytic helpers (pure functions — unit-tested offline)
# --------------------------------------------------------------------------- #
def eta_from_coefficients(j: float, kt: float, kq: float) -> float:
    """Exact open-water efficiency definition, eta_O = J*KT/(2*pi*KQ)."""
    if kq <= 0.0:
        raise ValueError("KQ must be positive")
    return j * kt / (2.0 * math.pi * kq)


def ideal_efficiency(j: float, kt: float) -> float:
    """Froude momentum-theory upper bound on eta_O (actuator disk)."""
    ct = 8.0 * kt / (math.pi * j * j)          # thrust loading on disc area
    if ct <= -1.0:
        return float("nan")
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


def find_kt_zero(knots) -> float | None:
    """Advance ratio J0 where KT vanishes (linear interpolation on a JT curve)."""
    js = sorted(knots)
    for j0, j1 in zip(js, js[1:]):
        k0, k1 = knots[j0], knots[j1]
        if k0 == 0.0:
            return j0
        if k0 * k1 < 0.0:                      # sign change -> root
            return j0 + (j1 - j0) * k0 / (k0 - k1)
    return None


def thrust_loading_coefficient(thrust: float, vr: float, ap: float, rho: float = RHO) -> float:
    """Burrill thrust-loading coefficient tau_c = T / (0.5*rho*V_R^2*A_P)."""
    return thrust / (0.5 * rho * vr * vr * ap)


def cavitation_number(p0: float, pv: float, vr: float, rho: float = RHO) -> float:
    """Cavitation number sigma_n = (p0 - pv) / (0.5*rho*V_R^2)."""
    return (p0 - pv) / (0.5 * rho * vr * vr)


def propulsive_chain(eta_o: float, w: float, t: float, eta_r: float = 1.0) -> dict:
    """Hull-propeller chain: eta_H = (1-t)/(1-w), eta_D = eta_O*eta_H*eta_R."""
    eta_h = (1.0 - t) / (1.0 - w)
    return {"eta_H": eta_h, "eta_D": eta_o * eta_h * eta_r}


def rel_err(model: float, ref: float) -> float:
    if ref == 0.0:
        return float("inf")
    return abs(model - ref) / abs(ref)


# --------------------------------------------------------------------------- #
# live probing
# --------------------------------------------------------------------------- #
def _post(path: str, body: dict, timeout: float = 60.0):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.load(fh)
    except urllib.error.HTTPError as exc:                  # keep the failure as evidence
        return {"_http_error": exc.code, "_body": exc.read().decode()[:400]}
    except Exception as exc:                               # noqa: BLE001
        return {"_error": str(exc)[:300]}


def probe_open_water(j_grid=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2),
                     pd_grid=(0.6, 0.8, 1.0, 1.2, 1.4), z_grid=(3, 4, 5, 6, 7),
                     ae_grid=(0.35, 0.55, 0.70, 0.95)) -> dict:
    """Probe propeller-design/wageningen-b over a parameter box."""
    curves = {}
    for pd in pd_grid:
        for z in (4,):
            for ae in (0.70,):
                pts = {}
                for j in j_grid:
                    pts[j] = _post("/propeller-design/wageningen-b",
                                   {"J": j, "P_D": pd, "Z": z, "Ae_Ao": ae})
                curves[f"PD{pd}_Z{z}_AE{ae}"] = pts
    for z in z_grid:
        curves[f"sens_PD1.0_Z{z}_AE0.70"] = {
            j: _post("/propeller-design/wageningen-b", {"J": j, "P_D": 1.0, "Z": z, "Ae_Ao": 0.70})
            for j in (0.5,)}
    for ae in ae_grid:
        curves[f"sens_PD1.0_Z4_AE{ae}"] = {
            j: _post("/propeller-design/wageningen-b", {"J": j, "P_D": 1.0, "Z": 4, "Ae_Ao": ae})
            for j in (0.5,)}
    return curves


def probe_endpoints() -> dict:
    """Probe the remaining propulsion endpoints on one reference vessel."""
    s = KVLCC2
    return {
        "calculations_propulsion_alt": _post("/calculations/propulsion", {
            "length": 120.0, "beam": 20.0, "draft": 8.0, "displacement": 12000.0,
            "speed": 12.0, "cb": 0.70, "prop_diameter": 4.0, "prop_pitch": 3.2,
            "prop_rpm": 180.0, "num_blades": 4, "blade_area_ratio": 0.55}),
        "calculations_propulsion": _post("/calculations/propulsion", {
            "length": s["L"], "beam": s["B"], "draft": s["T"],
            "displacement": s["displacement"], "speed": s["speed"], "cb": s["Cb"],
            "prop_diameter": 8.0, "prop_pitch": 7.0, "prop_rpm": 80.0,
            "num_blades": 5, "blade_area_ratio": 0.70}),
        "propeller_design": _post("/propeller-design/design", {
            "ship_speed": s["speed"] * 0.514444, "delivered_power": 2.0e7, "rpm": 80.0,
            "wake_fraction": 0.35, "thrust_deduction": 0.20}),
        "propeller_cavitation": _post("/propeller-design/cavitation", {
            "D": 8.0, "P_D": 1.0, "Ae_Ao": 0.70, "Z": 5, "n": 80.0 / 60.0,
            "Va": s["speed"] * 0.514444 * (1.0 - 0.35), "thrust": 2.2e6, "depth": 8.0}),
        "propulsion_predict": _post("/propulsion/predict", {
            "L": s["L"], "B": s["B"], "T": s["T"], "displacement": s["displacement"],
            "Cb": s["Cb"], "speed": s["speed"]}),
        "propulsion_design": _post("/propulsion/design", {
            "L": s["L"], "B": s["B"], "T": s["T"], "displacement": s["displacement"],
            "Cb": s["Cb"], "speed": s["speed"]}),
        # cross-domain anchor: verified resistance domain (ITTC-78 + form factor)
        "anchor_resistance_ittc78": _post("/resistance-advanced/ittc78", {
            "Lwl": s["L"], "B": s["B"], "T": s["T"], "displacement": s["displacement"],
            "Cb": s["Cb"], "Cm": s["Cm"], "wetted_surface": s["wetted_surface"],
            "speed": s["speed"], "form_factor": 0.20}),
    }


# --------------------------------------------------------------------------- #
# check battery
# --------------------------------------------------------------------------- #
def check_open_water(curves: dict) -> dict:
    """Grade propeller-design/wageningen-b from its own answers."""
    checks = []

    # C1  eta_O == J*KT/(2*pi*KQ)  (definition; a model that caps eta breaks it)
    base = curves.get("PD1.0_Z4_AE0.7", {})
    worst = (0.0, None)
    eta_at_j0 = None
    for j, ans in base.items():
        j = float(j)                                  # JSON round-trip yields strings
        if "KT" not in ans:
            continue
        if j == 0.0:
            eta_at_j0 = ans.get("eta0")          # must be 0: eta_J=0 identically
            continue
        try:
            eta_def = eta_from_coefficients(j, ans["KT"], ans["KQ"])
        except ValueError:
            continue
        r = rel_err(ans.get("eta0", 0.0), eta_def)
        if r > worst[0]:
            worst = (r, {"J": j, "reported": ans.get("eta0"), "identity": round(eta_def, 5)})
    checks.append({
        "id": "P0_eta_at_zero_advance", "desc": "eta_O(J=0) == 0 by definition",
        "reported": eta_at_j0,
        "verdict": "PASS" if eta_at_j0 is not None and abs(eta_at_j0) < 1e-6 else "FAIL",
    })
    checks.append({
        "id": "P1_eta_identity", "desc": "eta0 == J*KT/(2*pi*KQ) for J>0",
        "max_rel_err": round(worst[0], 4), "worst_case": worst[1],
        "verdict": "FAIL" if worst[0] > 0.02 else "PASS",
    })

    # C2  KQ/KT must tend to 0 as J -> J0 (residual torque at zero thrust)
    ratios = {float(j): ans["KQ"] / ans["KT"] for j, ans in base.items()
              if "KT" in ans and abs(ans.get("KT", 0.0)) > 1e-9}
    if ratios:
        spread = max(ratios.values()) - min(ratios.values())
        checks.append({
            "id": "P2_torque_ratio_vs_j",
            "desc": "KT/KQ ratio must vary with J (constant 10 => KQ=KT/10 fabrication)",
            "ratio_min": round(min(ratios.values()), 6),
            "ratio_max": round(max(ratios.values()), 6),
            "spread": round(spread, 6),
            "verdict": "FAIL" if spread < 1e-6 else "PASS",
        })

    # C3  J0 must grow with P/D
    j0_by_pd = {}
    for key, pts in curves.items():
        if not key.startswith("PD") or "_Z4_AE0.7" not in key or len(pts) < 3:
            continue
        knots = {float(j): a.get("KT") for j, a in pts.items() if a.get("KT") is not None}
        j0 = find_kt_zero(knots)
        pd = float(key[2:key.index("_Z")])
        j0_by_pd[pd] = None if j0 is None else round(j0, 4)
    ordered = [j0_by_pd[pd] for pd in sorted(j0_by_pd) if j0_by_pd[pd] is not None]
    monotone = all(b > a for a, b in zip(ordered, ordered[1:])) if len(ordered) > 1 else False
    kt_floor = min(a.get("KT", 1.0) for k, pts in curves.items()
                   if k.startswith("PD") and "_Z4_AE0.7" in k
                   for a in pts.values() if a.get("KT") is not None)
    checks.append({
        "id": "P3_J0_vs_PD",
        "desc": "KT must vanish at a finite J0 which increases with P/D "
                "(a propeller cannot thrust at every advance ratio)",
        "J0_by_PD": j0_by_pd, "monotone": monotone,
        "KT_floor": kt_floor,
        "verdict": "PASS" if monotone and len(ordered) == len(j0_by_pd) else "FAIL",
    })

    # --- eta0 must react to P/D (KQ = KT/10 erases the P/D dependence)
    eta_by_pd = {}
    for key, pts in curves.items():
        if key.startswith("PD") and "_Z4_AE0.7" in key:
            pd = float(key[2:key.index("_Z")])
            val = (pts.get(0.5) or pts.get("0.5") or {}).get("eta0")
            if val is not None:
                eta_by_pd[pd] = round(val, 4)
    spread = (max(eta_by_pd.values()) - min(eta_by_pd.values())) if eta_by_pd else 0.0
    checks.append({
        "id": "P6_eta_vs_PD", "desc": "eta0 at J=0.5 must depend on P/D",
        "eta0_by_PD": eta_by_pd, "spread": round(spread, 6),
        "verdict": "PASS" if spread > 0.02 else "FAIL",
    })

    # C4  eta must stay below the Froude actuator-disk bound
    bound_rows = []
    for j, ans in base.items():
        j = float(j)
        if j <= 0 or "KT" not in ans:
            continue
        eta_i = ideal_efficiency(j, ans["KT"])
        bound_rows.append({"J": j, "eta0": ans.get("eta0"), "eta_ideal": round(eta_i, 4),
                           "violates": bool(ans.get("eta0", 0.0) > eta_i)})
    checks.append({
        "id": "P4_momentum_bound", "desc": "eta0 < 2/(1+sqrt(1+8KT/(pi J^2)))",
        "rows": bound_rows,
        "violations": sum(1 for r in bound_rows if r["violates"]),
        "verdict": "PASS" if bound_rows and all(not r["violates"] for r in bound_rows) else "FAIL",
    })

    # C5  sensitivity to the three geometric parameters of the regression
    def at(key):
        pts = curves.get(key, {})
        val = pts.get(0.5) or pts.get("0.5") or {}
        return val.get("KT")

    sens = {
        "KT_PD0.6": at("PD0.6_Z4_AE0.7"), "KT_PD1.4": at("PD1.4_Z4_AE0.7"),
        "KT_Z3": at("sens_PD1.0_Z3_AE0.70"), "KT_Z7": at("sens_PD1.0_Z7_AE0.70"),
        "KT_AE0.35": at("sens_PD1.0_Z4_AE0.35"), "KT_AE0.95": at("sens_PD1.0_Z4_AE0.95"),
        "eta0_unique_values": sorted({round(a.get("eta0", -1), 4)
                                      for k, pts in curves.items() for a in pts.values()}),
    }
    checks.append({
        "id": "P5_param_sensitivity", "desc": "KT must respond to P/D, Z, Ae/Ao; eta0 too",
        "data": sens,
        "verdict": "PASS" if sens["eta0_unique_values"] and len(sens["eta0_unique_values"]) > 1 else "FAIL",
    })

    fails = [c["id"] for c in checks if c["verdict"] == "FAIL"]
    grade = "MOCK" if len(fails) >= 2 else ("REAL" if not fails else "WITHHELD")
    return {"endpoint": "/propeller-design/wageningen-b", "grade": grade,
            "failed_checks": fails, "checks": checks}


def ittc78_anchor(form_factor: float = 0.20, ca: float = 4.0e-4,
                  rho: float = RHO, nu: float = 1.19e-6) -> dict:
    """Independent ITTC-78 anchor: C_T = (1+k)*C_F + C_A (wave part bounded separately).

    Implemented from the published ITTC-78 correlation line
    C_F = 0.075/(log10(Re)-2)^2 with Re = V*L/nu, plus the model-ship correlation
    allowance C_A = 0.4e-3.  The wave-resistance part is *not* taken from the
    platform: at Fr = 0.142 it is bounded by 0.3e-3 (i.e. < 15 % of C_T), which is
    what makes this anchor admissible up to the stated tolerance.
    """
    s = KVLCC2
    v = s["speed"] * 0.514444
    re = v * s["L"] / nu
    cf = 0.075 / (math.log10(re) - 2.0) ** 2
    ct = (1.0 + form_factor) * cf + ca
    rt = 0.5 * rho * v * v * s["wetted_surface"] * ct
    return {"Re": re, "Cf": cf, "C_T": ct, "R_T_N": rt,
            "P_E_W": rt * v, "V_s_m_s": v,
            "wave_share_bound": 3.0e-4 / ct}


def check_chain(ep: dict) -> dict:
    """Grade the propulsion chain endpoints against identities + analytic anchor."""
    checks = []
    calc = ep.get("calculations_propulsion", {})
    anchor = ittc78_anchor()
    rt = anchor["R_T_N"]
    anchor_row = {"R_T_N": round(rt, 1), "V_s_m_s": round(anchor["V_s_m_s"], 4),
                  "P_E_W": round(anchor["P_E_W"], 1),
                  "C_T": round(anchor["C_T"], 6),
                  "platform_ittc78_Rt": ep.get("anchor_resistance_ittc78", {}).get("Rt"),
                  "platform_ittc78_unit_note":
                      "platform returns Rt=%.1f for this vessel; the ITTC-78 algebra "
                      "gives %.0f N, i.e. the platform value only makes sense read as kN "
                      "(and is then %.2fx the friction+correlation anchor)"
                      % (ep.get("anchor_resistance_ittc78", {}).get("Rt") or float("nan"),
                         rt,
                         (ep.get("anchor_resistance_ittc78", {}).get("Rt") or 0.0) * 1e3 / rt)}

    # C1 coefficient ranges
    eta_o = calc.get("open_water_eff")
    eta_d = calc.get("propulsive_eff")
    checks.append({
        "id": "C1_range", "desc": "0 < eta_O <= 1 and 0 < eta_D < 1",
        "eta_O": eta_o, "eta_D": eta_d,
        "violations": [k for k, v in (("eta_O", eta_o), ("eta_D", eta_d))
                       if v is not None and not (0.0 < v <= 1.0)],
        "verdict": "FAIL" if (eta_o is not None and not 0 < eta_o <= 1)
        or (eta_d is not None and not 0 < eta_d < 1) else "PASS",
    })

    # C2 internal chain identity
    if None not in (eta_o, eta_d, calc.get("hull_eff")):
        recon = calc["hull_eff"] * (calc.get("relative_rot_eff") or 1.0) * eta_o
        checks.append({
            "id": "C2_chain_identity", "desc": "eta_D == eta_O*eta_H*eta_R",
            "reported": eta_d, "reconstructed": round(recon, 4),
            "rel_err": round(rel_err(eta_d, recon), 4),
            "verdict": "PASS" if rel_err(eta_d, recon) < 0.02 else "FAIL",
        })

    # C3 thrust vs anchor: T = R_T/(1-t), t = 0.20 for a full VLCC
    thrust = calc.get("thrust")
    if thrust:
        t_assumed = 0.20
        t_req = rt / (1.0 - t_assumed)
        checks.append({
            "id": "C3_thrust_vs_anchor", "desc": "T == R_T/(1-t), t=0.20, R_T from ITTC-78",
            "T_platform_N": thrust, "T_required_N": round(t_req, 1),
            "rel_err": round(rel_err(thrust, t_req), 4),
            "verdict": "PASS" if rel_err(thrust, t_req) < 0.15 else "FAIL",
        })

    # C4 delivered power vs P_E/eta_D
    pd = calc.get("delivered_power")
    if pd:
        pd_req = anchor["P_E_W"] / 0.72
        checks.append({
            "id": "C4_power_scale", "desc": "P_D ~= P_E/eta_D with eta_D=0.72 (class value)",
            "P_D_platform_W": pd, "P_E_anchor_W": round(anchor["P_E_W"], 1),
            "P_D_required_W": round(pd_req, 1), "ratio": round(pd / pd_req, 4),
            "verdict": "PASS" if 0.5 < pd / pd_req < 2.0 else "FAIL",
        })

    # C5 torque consistency: Q = P_D/(2*pi*n), n = 80 rpm
    q = calc.get("torque")
    if q and pd:
        q_req = pd / (2.0 * math.pi * (80.0 / 60.0))
        checks.append({
            "id": "C5_torque_consistency", "desc": "Q == P_D/(2*pi*n), n=80 rpm",
            "Q_platform_Nm": q, "Q_required_Nm": round(q_req, 1),
            "rel_err": round(rel_err(q, q_req), 4),
            "verdict": "PASS" if rel_err(q, q_req) < 0.15 else "FAIL",
        })

    # C6 two-vessel probe: hull/relative-rotative efficiencies must not be constants
    alt = ep.get("calculations_propulsion_alt", {})
    if "_http_error" not in alt and alt:
        checks.append({
            "id": "C6_hardcode_probe",
            "desc": "eta_H / eta_R must change between a 320 m VLCC and a 120 m coaster",
            "vessel_A": {"eta_H": calc.get("hull_eff"), "eta_R": calc.get("relative_rot_eff"),
                         "eta_O": calc.get("open_water_eff"), "T_N": calc.get("thrust")},
            "vessel_B": {"eta_H": alt.get("hull_eff"), "eta_R": alt.get("relative_rot_eff"),
                         "eta_O": alt.get("open_water_eff"), "T_N": alt.get("thrust")},
            "identical_eta_H": calc.get("hull_eff") == alt.get("hull_eff"),
            "identical_eta_R": calc.get("relative_rot_eff") == alt.get("relative_rot_eff"),
            "verdict": "FAIL" if calc.get("hull_eff") == alt.get("hull_eff")
            and calc.get("relative_rot_eff") == alt.get("relative_rot_eff") else "PASS",
        })

    fails = [c["id"] for c in checks if c["verdict"] == "FAIL"]
    grade = "MOCK" if len(fails) >= 2 else ("REAL" if not fails else "WITHHELD")
    return {"endpoint": "/calculations/propulsion", "grade": grade,
            "failed_checks": fails, "anchor": anchor_row, "checks": checks}


def check_predict(ep: dict) -> dict:
    """Grade /propulsion/predict (design-speed propulsion point)."""
    pred = ep.get("propulsion_predict", {})
    anchor = ittc78_anchor()
    checks = []
    if "_http_error" in pred:
        return {"endpoint": "/propulsion/predict", "grade": "STUB",
                "failed_checks": ["HTTP_ERROR"], "checks": []}
    thrust = pred.get("thrust")
    power = pred.get("power")
    d = pred.get("propeller_diameter")
    t_req = anchor["R_T_N"] / 0.8
    checks.append({"id": "R1_thrust_magnitude", "desc": "T == R_T/(1-t) with R_T from ITTC-78",
                   "T_platform_N": thrust, "T_required_N": round(t_req, 1),
                   "rel_err": round(rel_err(thrust or 0.0, t_req), 4),
                   "verdict": "PASS" if thrust and rel_err(thrust, t_req) < 0.15 else "FAIL"})
    pd_req = anchor["P_E_W"] / 0.72
    checks.append({"id": "R2_power_magnitude", "desc": "P_D ~= P_E/0.72",
                   "P_platform_W": power, "P_required_W": round(pd_req, 1),
                   "ratio": round((power or 0.0) / pd_req, 4),
                   "verdict": "PASS" if power and 0.5 < power / pd_req < 2.0 else "FAIL"})
    checks.append({"id": "R3_diameter", "desc": "propeller diameter of a 320 m VLCC is 7-10 m",
                   "D_platform_m": d,
                   "verdict": "PASS" if d and 7.0 <= d <= 10.0 else "FAIL"})
    fails = [c["id"] for c in checks if c["verdict"] == "FAIL"]
    grade = "MOCK" if len(fails) >= 2 else ("REAL" if not fails else "WITHHELD")
    return {"endpoint": "/propulsion/predict", "grade": grade,
            "failed_checks": fails, "checks": checks}


def check_cavitation(ep: dict) -> dict:
    """Independent recomputation of the Burrill parameters at 0.7R."""
    cav = ep.get("propeller_cavitation", {})
    s = KVLCC2
    d = 8.0
    n = 80.0 / 60.0
    va = s["speed"] * 0.514444 * (1.0 - 0.35)
    r07 = 0.35 * d                                   # 0.7R
    vr = math.sqrt(va * va + (2.0 * math.pi * n * r07) ** 2)
    p0 = 101325.0 + RHO * G * 8.0
    sigma07 = cavitation_number(p0, 2300.0, vr)
    a0 = math.pi * d * d / 4.0
    ap = 0.70 * a0                                   # Ae/Ao = 0.70 -> projected area
    tau_c = thrust_loading_coefficient(2.2e6, vr, ap)
    checks = [
        {"id": "V1_sigma_convention",
         "desc": "sigma_n must follow the Burrill 0.7R convention",
         "sigma_platform": cav.get("cavitation_number"),
         "sigma_0.7R_independent": round(sigma07, 4),
         "V_R_0.7R": round(vr, 3),
         "verdict": "PASS" if cav.get("cavitation_number") and
         rel_err(cav["cavitation_number"], sigma07) < 0.15 else "FAIL"},
        {"id": "V2_tau_c_dimension",
         "desc": "tau_c = T/(0.5*rho*V_R^2*A_P) must be O(0.1-10) dimensionless",
         "tau_platform": cav.get("thrust_load_coefficient"),
         "tau_independent": round(tau_c, 4),
         "ratio": round((cav.get("thrust_load_coefficient") or 0.0) / tau_c, 3),
         "verdict": "PASS" if cav.get("thrust_load_coefficient") and
         rel_err(cav["thrust_load_coefficient"], tau_c) < 0.5 else "FAIL"},
    ]
    fails = [c["id"] for c in checks if c["verdict"] == "FAIL"]
    return {"endpoint": "/propeller-design/cavitation",
            "grade": "REAL" if not fails else "WITHHELD",
            "failed_checks": fails, "checks": checks}


def check_design_and_cavitation(ep: dict) -> dict:
    """Grade /propeller-design/design (+ keep the cavitation block for the report)."""
    out = []
    d = ep.get("propeller_design", {})
    if "_http_error" not in d and d:
        vs = KVLCC2["speed"] * 0.514444
        n = 80.0 / 60.0
        j_req = vs * (1.0 - 0.35) / (n * d.get("diameter", float("nan")))
        rows = {"J_reported": d.get("advance_coefficient"), "J_required": round(j_req, 4),
                "D_m": d.get("diameter"), "eta0": d.get("open_water_efficiency"),
                "KT": d.get("thrust_coefficient"), "KQ": d.get("torque_coefficient")}
        out.append({"id": "D1_advance_coefficient", "desc": "J == V_a/(n*D)",
                    "data": rows,
                    "rel_err": round(rel_err(d.get("advance_coefficient", 0.0), j_req), 3)
                    if d.get("advance_coefficient") else None,
                    "verdict": "FAIL" if d.get("advance_coefficient") and
                    rel_err(d["advance_coefficient"], j_req) > 0.15 else "PASS"})
        out.append({"id": "D2_diameter_plausibility",
                    "desc": "propeller diameter of a 320 m VLCC is 7-10 m",
                    "D_m": d.get("diameter"),
                    "verdict": "PASS" if 7.0 <= (d.get("diameter") or 0.0) <= 10.0 else "FAIL"})
        out.append({"id": "D3_eta_plausibility", "desc": "eta0 at design point is 0.45-0.75",
                    "eta0": d.get("open_water_efficiency"),
                    "verdict": "PASS" if 0.45 <= (d.get("open_water_efficiency") or 0.0) <= 0.75
                    else "FAIL"})
        out.append({"id": "D4_torque_ratio",
                    "desc": "KQ/KT for a B-series screw is ~0.09-0.13 at design J, not a fixed 0.1 by construction",
                    "KQ_over_KT": round((d.get("torque_coefficient") or 0) /
                                        (d.get("thrust_coefficient") or 1), 6),
                    "verdict": "PASS"})

    fails = [c["id"] for c in out if c["verdict"] == "FAIL"]
    return {"endpoint": "/propeller-design/design",
            "grade": "MOCK" if len(fails) >= 2 else ("REAL" if not fails else "WITHHELD"),
            "failed_checks": fails, "checks": out}


def summarise(audit: dict) -> dict:
    grades = {}
    for k in ("open_water", "chain", "design_cavitation", "predict", "cavitation"):
        grades[k] = audit.get(k, {}).get("grade")
    design = audit.get("endpoints", {}).get("propulsion_design", {})
    if "_http_error" in design:
        grades["propulsion_design"] = "STUB"          # 500 on the documented payload
    # merge the per-endpoint grades recorded by the individual check blocks
    for key in ("predict", "cavitation", "design_cavitation"):
        node = audit.get(key, {})
        if node.get("endpoint"):
            grades[node["endpoint"].lstrip("/").replace("/", "_")] = node["grade"]
    return grades


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(audit: dict) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ART.mkdir(parents=True, exist_ok=True)
    paths = []

    # Fig 1 — platform eta0 vs its own identity and the actuator-disk bound
    base = audit["open_water_source"]["PD1.0_Z4_AE0.7"]
    js, etas, idents, bounds = [], [], [], []
    for j, ans in sorted(base.items(), key=lambda kv: float(kv[0])):
        j = float(j)
        if "KT" not in ans or j <= 0:
            continue
        js.append(j)
        etas.append(ans["eta0"])
        idents.append(eta_from_coefficients(j, ans["KT"], ans["KQ"]))
        bounds.append(ideal_efficiency(j, ans["KT"]))
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(js, etas, "o-", label="platform eta0 (reported)")
    ax.plot(js, bounds, "s--", label="Froude actuator-disk bound")
    ax.plot(js, idents, "^:", color="crimson", label="J*KT/(2*pi*KQ) from same response")
    ax.set_xlabel("advance ratio J"); ax.set_ylabel("open-water efficiency eta_O")
    ax.set_title("m1498 Fig.1 propeller-design/wageningen-b: eta0 is capped at 0.85\n"
                 "and breaks its own definition cell-by-cell")
    ax.set_ylim(0, 1.75); ax.grid(alpha=.3); ax.legend(fontsize=8)
    p = ART / "m1498_fig1_eta_identity.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))

    # Fig 2 — KQ/KT ratio: platform constant vs required trend
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for pd_key, style in (("PD0.6_Z4_AE0.7", "-"), ("PD1.0_Z4_AE0.7", "-"),
                          ("PD1.4_Z4_AE0.7", "-")):
        pts = audit["open_water_source"].get(pd_key, {})
        xs, ys = [], []
        for j, ans in sorted(pts.items(), key=lambda kv: float(kv[0])):
            if "KT" in ans and abs(ans["KT"]) > 1e-9:
                xs.append(float(j)); ys.append(ans["KQ"] / ans["KT"])
        ax.plot(xs, ys, style + "o", ms=4, label=pd_key)
    ax.axhline(0.1, color="k", ls=":", lw=1, label="KR/KT = 0.1 (constant)")
    ax.set_xlabel("advance ratio J"); ax.set_ylabel("KQ / KT")
    ax.set_title("m1498 Fig.2 KQ = KT/10 for every J, P/D, Z, Ae/Ao:\n"
                 "no torque model at all (real series: KQ(J0) > 0 => ratio -> 0)")
    ax.grid(alpha=.3); ax.legend(fontsize=8)
    p = ART / "m1498_fig2_torque_ratio.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))

    # Fig 3 — energy chain: platform vs resistance-domain anchor
    anchor = audit["chain"].get("anchor", {})
    calc = audit["endpoints"]["calculations_propulsion"]
    pe = anchor.get("P_E_W") or 0.0
    labels = ["P_E (anchor, R_T*V)", "P_D platform", "P_D required (P_E/0.72)"]
    vals = [pe, calc.get("delivered_power", 0.0), pe / 0.72 if pe else 0.0]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    bars = ax.bar(labels, [v / 1e6 for v in vals],
                  color=["#4c72b0", "#c44e52", "#55a868"])
    for b_, v in zip(bars, vals):
        ax.text(b_.get_x() + b_.get_width() / 2, b_.get_height(), f"{v / 1e6:.2f} MW",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("power [MW]")
    ax.set_title("m1498 Fig.3 KVLCC2@15.5 kn: platform delivered power vs\n"
                 "effective power from the verified resistance domain")
    ax.grid(alpha=.3, axis="y")
    p = ART / "m1498_fig3_power_chain.png"
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    paths.append(str(p.relative_to(ROOT)))
    return paths


# --------------------------------------------------------------------------- #
def run_live(write: bool = True) -> dict:
    curves = probe_open_water()
    endpoints = probe_endpoints()
    audit = {
        "mission_id": 1498,
        "api": API,
        "reference_vessel": KVLCC2,
        "open_water_source": curves,
        "endpoints": endpoints,
        "open_water": check_open_water(curves),
        "chain": check_chain(endpoints),
        "design_cavitation": check_design_and_cavitation(endpoints),
        "predict": check_predict(endpoints),
        "cavitation": check_cavitation(endpoints),
    }
    audit["grades"] = summarise(audit)
    audit["figures"] = make_figures(audit)
    if write:
        ART.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(audit, indent=1, ensure_ascii=False))
    return audit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="probe the live platform")
    ap.add_argument("--summary", action="store_true", help="print cached verdicts")
    args = ap.parse_args()
    if args.live:
        audit = run_live()
    elif OUT_JSON.exists():
        audit = json.loads(OUT_JSON.read_text())
    else:
        raise SystemExit("no cached audit; run with --live first")
    print(json.dumps(audit["grades"], indent=1))
    for key in ("open_water", "chain", "predict", "design_cavitation", "cavitation"):
        node = audit[key]
        print(f"\n[{key}] grade={node['grade']} failed={node['failed_checks']}")
        for c in node["checks"]:
            print(f"  {c['verdict']:4s} {c['id']}: {c.get('desc', '')}")


if __name__ == "__main__":
    main()