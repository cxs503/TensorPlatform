"""Capability registry: what is audited, and with which probes.

Each :class:`CapabilitySpec` binds one platform capability (API endpoint) to
four independent families of evidence:

``invariance``    semantically equivalent inputs must give the same answer
                  (determinism, serialization order, unit choice, similitude).
``sensitivity``   causally relevant inputs must move the answer by a physically
                  plausible amount — this is the anti-hardcoded-constant knife.
``analytic``      the answer must match a closed-form law transcribed in
                  :mod:`tools.capability_audit.oracle`.
``monotonic``     "three cuts": sweeps along three independent parameter axes
                  where the sign of dQ/dp is fixed by physics.

The claim under test (``claimed_level``) is taken from the platform's own
capability snapshot (``tests/fixtures/capability_ledger/capabilities_snapshot.json``)
so that the audit is literally a falsification of a recorded claim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import benchmark, oracle

KNOT = oracle.KNOT

#: Reference hull — a KCS-like 100 m single-screw tanker, chosen so that every
#: endpoint in the registry accepts it without geometry-closure warnings.
BASE_HULL: Dict[str, float] = {
    "L": 100.0,
    "B": 16.0,
    "T": 6.0,
    "Cb": 0.7,
    "displacement": 0.7 * 100.0 * 16.0 * 6.0,   # 6720 m^3, closed on L*B*T*Cb
    "speed_kn": 12.0,
    "rho": 1025.0,
    "nu": 1.19e-6,
    "KG": 7.0,
    "D": 9.0,
    "Dp": 3.0,
    "n_rps": 3.0,
    "pitch_ratio": 1.0,
    "blades": 4,
    "ae_ao": 0.55,
    "Hs": 3.0,
    "Tp": 8.0,
}


@dataclass
class InvarianceTest:
    name: str
    transform: Callable[[Dict[str, Any]], Dict[str, Any]]
    tol: float
    compare_fields: Optional[List[str]] = None
    expected_scale: float = 1.0   # divide the variant by this before comparing
    note: str = ""


@dataclass
class SensitivityTest:
    name: str
    field: str
    transform: Callable[[Dict[str, Any]], Dict[str, Any]]
    min_rel_change: float
    direction: int = 0          # +1 increase, -1 decrease, 0 either way
    note: str = ""


@dataclass
class AnalyticTest:
    name: str
    check: Callable[[Dict[str, Any], Any], Dict[str, Any]]
    tol: float
    fatal: bool = True
    note: str = ""


@dataclass
class MonotonicTest:
    name: str
    field: str
    values: List[float]
    transform: Callable[[float], Dict[str, Any]]
    direction: int               # +1 non-decreasing, -1 non-increasing
    min_rel_step: float = 0.0    # required |dQ|/Q between consecutive points
    note: str = ""


@dataclass
class CapabilitySpec:
    cap_id: str
    endpoint: str
    domain: str
    claimed_level: str
    claim_source: str
    base_input: Dict[str, Any]
    invariances: List[InvarianceTest] = field(default_factory=list)
    sensitivities: List[SensitivityTest] = field(default_factory=list)
    analytics: List[AnalyticTest] = field(default_factory=list)
    monotonics: List[MonotonicTest] = field(default_factory=list)
    benchmarks: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# payload builders
# --------------------------------------------------------------------------
def _resistance_hull(cb: float = BASE_HULL["Cb"], speed_kn: float = BASE_HULL["speed_kn"],
                     scale: float = 1.0, speed_scale: Optional[float] = None) -> Dict[str, Any]:
    ss = speed_scale if speed_scale is not None else scale ** 0.5
    return {
        "length": BASE_HULL["L"] * scale,
        "beam": BASE_HULL["B"] * scale,
        "draft": BASE_HULL["T"] * scale,
        "displacement": cb * (BASE_HULL["L"] * BASE_HULL["B"] * BASE_HULL["T"]) * scale ** 3,
        "speed": speed_kn * ss,
        "speed_unit": "kn",
        "cb": cb,
        "water_density": BASE_HULL["rho"],
        "water_viscosity": BASE_HULL["nu"],
        "body_type": "ship",
    }


def _holtrop(speeds: List[float], scale: float = 1.0) -> Dict[str, Any]:
    return {
        "Lwl": BASE_HULL["L"] * scale,
        "Lpp": (BASE_HULL["L"] - 1.5) * scale,
        "B": BASE_HULL["B"] * scale,
        "T": BASE_HULL["T"] * scale,
        "displacement": BASE_HULL["displacement"] * scale ** 3,
        "Cb": BASE_HULL["Cb"],
        "Cm": 0.98,
        "Cwp": 0.75,
        "speeds": speeds,
        "body_type": "ship",
    }


def _stability(kg: float = BASE_HULL["KG"], scale: float = 1.0) -> Dict[str, Any]:
    return {
        "L": BASE_HULL["L"] * scale,
        "B": BASE_HULL["B"] * scale,
        "T": BASE_HULL["T"] * scale,
        "D": BASE_HULL["D"] * scale,
        "displacement": 0.7 * 100.0 * 16.0 * 6.0 * scale ** 3,
        "Cb": BASE_HULL["Cb"],
        "KG": kg * scale,
    }


def _propulsion(speed_kn: float = BASE_HULL["speed_kn"], pitch: Optional[float] = None,
                n_rps: Optional[float] = None) -> Dict[str, Any]:
    return {
        "L": BASE_HULL["L"],
        "B": BASE_HULL["B"],
        "T": BASE_HULL["T"],
        "displacement": BASE_HULL["displacement"],
        "Cb": BASE_HULL["Cb"],
        "speed": speed_kn,
        "Dp": BASE_HULL["Dp"],
        "n": n_rps if n_rps is not None else BASE_HULL["n_rps"],
        "pitch_ratio": pitch if pitch is not None else BASE_HULL["pitch_ratio"],
        "blades": BASE_HULL["blades"],
        "ae_ao": BASE_HULL["ae_ao"],
        "water_density": BASE_HULL["rho"],
    }


def _added_resistance(hs: float = BASE_HULL["Hs"], tp: float = BASE_HULL["Tp"],
                      speed_kn: float = BASE_HULL["speed_kn"]) -> Dict[str, Any]:
    return {
        "displacement": BASE_HULL["displacement"],
        "L": BASE_HULL["L"],
        "B": BASE_HULL["B"],
        "T": BASE_HULL["T"],
        "Cb": BASE_HULL["Cb"],
        "speed": speed_kn,
        "Hs": hs,
        "Tp": tp,
    }


def _turning(rudder: float = 35.0, speed_kn: float = BASE_HULL["speed_kn"]) -> Dict[str, Any]:
    return {
        "L": BASE_HULL["L"],
        "B": BASE_HULL["B"],
        "T": BASE_HULL["T"],
        "displacement": BASE_HULL["displacement"],
        "Cb": BASE_HULL["Cb"],
        "KG": BASE_HULL["KG"],
        "Dp": BASE_HULL["Dp"],
        "Ar": 12.0,
        "speed": speed_kn,
        "rudder_angle": rudder,
    }


def _reversed_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: payload[k] for k in reversed(list(payload))}


# --------------------------------------------------------------------------
# analytic check adapters: (payload, response) -> {'observed','expected','detail'}
# --------------------------------------------------------------------------
def _ck_ittc57(p, r):
    re, cf = float(r["reynolds_number"]), float(r["cf"])
    ref = oracle.ittc57_cf(re)
    return {"observed": cf, "expected": ref, "detail": f"Cf(Re={re:.4e})"}


def _ck_cf_total(p, r):
    obs = float(r["cf_total"])
    exp = float(r["cf"]) + float(r["dcf"])
    return {"observed": obs, "expected": exp, "detail": "Cf_total = Cf + dCf"}


def _ck_froude(p, r):
    obs = float(r["froude_number"])
    exp = oracle.froude_number(float(r["speed_ms"]), float(p["length"]))
    return {"observed": obs, "expected": exp, "detail": "Fr = V/sqrt(gL)"}


def _ck_reynolds(p, r):
    obs = float(r["reynolds_number"])
    exp = oracle.reynolds_number(float(r["speed_ms"]), float(p["length"]), float(p["water_viscosity"]))
    return {"observed": obs, "expected": exp, "detail": "Re = VL/nu"}


def _ck_ct_closure(p, r):
    obs = float(r["rt"])
    exp = oracle.total_resistance_kn(float(r["ct"]), float(p["water_density"]),
                                     float(r["speed_ms"]), float(r["wetted_surface"]))
    return {"observed": obs, "expected": exp, "detail": "R_T = C_T .5 rho V^2 S"}


def _ck_pe(p, r):
    obs = float(r["pe"])
    exp = oracle.effective_power_kw(float(r["rt"]), float(r["speed_ms"]))
    return {"observed": obs, "expected": exp, "detail": "P_E = R_T V"}


def _first_point(r):
    if isinstance(r, list):
        return r[0]
    return r


def _ck_froude_holtrop(p, r):
    pt = _first_point(r)
    v = pt["speed"] * KNOT
    obs = float(pt["froude_number"])
    exp = oracle.froude_number(v, float(p["Lwl"]))
    return {"observed": obs, "expected": exp, "detail": "Fr at the first speed"}


def _ck_pe_holtrop(p, r):
    pt = _first_point(r)
    obs = float(pt["effective_power"])
    exp = oracle.effective_power_kw(float(pt["total_resistance"]), pt["speed"] * KNOT)
    return {"observed": obs, "expected": exp, "detail": "P_E = R_T V at the first speed"}


def _ck_gm(p, r):
    obs = float(r["GM"])
    exp = oracle.gm_identity(float(r["KB"]), float(r["BM"]), float(p["KG"]))
    return {"observed": obs, "expected": exp, "detail": "GM = KB + BM - KG"}


def _ck_kb(p, r):
    obs = float(r["KB"])
    exp = oracle.kb_wall_sided(float(p["T"]), float(p["Cb"]), float(r["Cw"]))
    return {"observed": obs, "expected": exp, "detail": "KB = T(5/6 - Cb/(3Cw))"}


def _ck_bm(p, r):
    obs = float(r["BM"])
    exp = oracle.bm_wall_sided(float(r["Cw"]), float(p["B"]), float(p["Cb"]), float(p["T"]))
    return {"observed": obs, "expected": exp, "detail": "BM = Cw B^2/(12 Cb T)"}


def _ck_advance(p, r):
    obs = float(r["advance_ratio"])
    exp = oracle.advance_ratio(p["speed"] * KNOT, float(r["wake_fraction"]),
                               float(r["n_rps"]), float(r["propeller_diameter"]))
    return {"observed": obs, "expected": exp, "detail": "J = V(1-w)/(nD)"}


def _ck_eta0(p, r):
    obs = float(r["eta_open_water"])
    exp = oracle.eta_open_water(float(r["advance_ratio"]), float(r["kt"]), float(r["kq"]))
    return {"observed": obs, "expected": exp, "detail": "eta0 = J KT/(2 pi KQ)"}


def _ck_eta_ideal(p, r):
    obs = float(r["eta_ideal"])
    exp = oracle.eta_ideal_from_ct(float(r["ct_loading"]))
    return {"observed": obs, "expected": exp, "detail": "eta_i = 2/(1+sqrt(1+Ct))"}


def _ck_eta_bound(p, r):
    eta0, eta_i = float(r["eta_open_water"]), float(r["eta_ideal"])
    margin = eta_i - eta0
    return {"observed": eta0, "expected": eta_i,
            "detail": f"eta0 must not exceed the actuator-disk limit (margin {margin:+.4f})"}


def _ck_wavelength(p, r):
    obs = float(r["lambda_p_m"])
    exp = oracle.deep_water_wavelength(float(p["Tp"]))
    return {"observed": obs, "expected": exp, "detail": "lambda = gT^2/(2 pi)"}


def _ck_turning_closure(p, r):
    """Kinematic closure: rate = V / R with R = steady diameter / 2."""
    v = float(r["final_speed"])
    radius = float(r["steady_diameter"]) / 2.0
    exp_rad_s = v / radius if radius else 0.0
    exp_deg_s = math.degrees(exp_rad_s)
    obs = float(r["turning_rate"])
    # The endpoint does not declare the unit of turning_rate (rad/s vs deg/s);
    # report against deg/s (the larger of the two candidates) so the check is
    # the *lenient* one — if even the lenient reading is off, the finding holds.
    return {"observed": obs, "expected": exp_deg_s,
            "detail": f"V/R = {exp_rad_s:.4f} rad/s = {exp_deg_s:.3f} deg/s "
                      f"(endpoint does not declare units)"}


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------
_SNAPSHOT = "capabilities_snapshot.json"

SPECS: List[CapabilitySpec] = [
    CapabilitySpec(
        cap_id="calc.resistance",
        endpoint="/calculations/resistance",
        domain="阻力",
        claimed_level="REAL",
        claim_source=_SNAPSHOT,
        base_input=_resistance_hull(),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12,
                           note="same payload twice -> bit-identical numbers"),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12,
                           note="JSON key order must not matter"),
            InvarianceTest("unit_equivalence",
                           lambda p: dict(p, speed=p["speed"] * KNOT, speed_unit="m/s"),
                           1e-5,
                           compare_fields=["rt", "cf", "ct", "pe", "reynolds_number"],
                           note="12 kn == 6.1733 m/s"),
            InvarianceTest("froude_similitude",
                           lambda p: _resistance_hull(scale=2.0),
                           1e-9,
                           compare_fields=["froude_number", "cb_from_dims"],
                           note="lambda=2, V*sqrt(2): Fr and Cb are dimensionless invariants"),
        ],
        sensitivities=[
            SensitivityTest("rt_responds_to_speed",
                            field="rt",
                            transform=lambda p: dict(p, speed=p["speed"] * 1.1),
                            min_rel_change=0.05, direction=+1,
                            note="+10% speed must move R_T by more than 5%"),
        ],
        analytics=[
            AnalyticTest("ittc57_friction_line", _ck_ittc57, 5e-3),
            AnalyticTest("cf_total_decomposition", _ck_cf_total, 5e-3),
            AnalyticTest("froude_identity", _ck_froude, 1e-4),
            AnalyticTest("reynolds_identity", _ck_reynolds, 1e-3),
            AnalyticTest("ct_resistance_closure", _ck_ct_closure, 1e-2),
            AnalyticTest("effective_power_identity", _ck_pe, 1e-2),
        ],
        monotonics=[
            MonotonicTest("rt_increases_with_speed", field="rt",
                          values=[8, 10, 12, 14, 16, 18, 20],
                          transform=lambda v: dict(_resistance_hull(), speed=v),
                          direction=+1, min_rel_step=0.02),
            MonotonicTest("rt_increases_with_cb", field="rt",
                          values=[0.60, 0.65, 0.70, 0.75],
                          transform=lambda v: _resistance_hull(cb=v),
                          direction=+1, min_rel_step=0.005),
        ],
        benchmarks=benchmark.load_cases(),
        notes=["R_T is reported in kN; P_E in kW.",
               "benchmark family reuses backend/data/benchmark_reference.json; only rows whose "
               "own integrity gate marks reference_status=DERIVED are adjudicated (15% tolerance), "
               "WITHHELD rows are reported as withheld and never judged."],
    ),
    CapabilitySpec(
        cap_id="calc.resistance.holtrop",
        endpoint="/resistance/predict",
        domain="阻力",
        claimed_level="REAL",
        claim_source=_SNAPSHOT + " (calc.resistance; endpoint-level projection)",
        base_input=_holtrop([10.0, 12.0, 14.0]),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12),
            InvarianceTest("batch_slice_consistency",
                           lambda p: dict(p, speeds=[10.0]),
                           1e-9,
                           compare_fields=["[0].total_resistance", "[0].friction_resistance",
                                           "[0].effective_power", "[0].froude_number"],
                           note="a single-speed request must equal the same row of a batch"),
            InvarianceTest("froude_similitude",
                           lambda p: _holtrop([s * math.sqrt(2.0) for s in p["speeds"]], scale=2.0),
                           1e-9,
                           compare_fields=["[0].froude_number", "[1].froude_number", "[2].froude_number"],
                           note="geometric similitude with V ~ sqrt(lambda) keeps Fr"),
        ],
        sensitivities=[
            SensitivityTest("rt_responds_to_speed", field="[1].total_resistance",
                            transform=lambda p: dict(p, speeds=[s * 1.1 for s in p["speeds"]]),
                            min_rel_change=0.05, direction=+1),
        ],
        analytics=[
            AnalyticTest("froude_identity", _ck_froude_holtrop, 1e-3),
            AnalyticTest("effective_power_identity", _ck_pe_holtrop, 2e-2),
        ],
        monotonics=[
            MonotonicTest("rt_increases_with_speed", field="total_resistance",
                          values=[8, 10, 12, 14, 16, 18],
                          transform=lambda v: _holtrop([float(v)]),
                          direction=+1, min_rel_step=0.02),
        ],
        notes=["Effective power uses the same V as the reported speed; tolerance 2% covers "
               "the endpoint's 0.01 kW rounding."],
    ),
    CapabilitySpec(
        cap_id="calc.stability",
        endpoint="/stability/evaluate",
        domain="稳性",
        claimed_level="REAL",
        claim_source=_SNAPSHOT,
        base_input=_stability(),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12),
            InvarianceTest("geometric_similitude",
                           lambda p: _stability(scale=2.0),
                           1e-4,
                           compare_fields=["GM", "KB", "BM", "KM"],
                           expected_scale=2.0,
                           note="lambda=2 (all lengths x2, displacement x8): KB/BM/GM/KM must "
                                "scale exactly by lambda"),
        ],
        sensitivities=[
            SensitivityTest("gm_responds_to_kg", field="GM",
                            transform=lambda p: dict(p, KG=p["KG"] + 0.5),
                            min_rel_change=0.5, direction=-1,
                            note="+0.5 m in KG must cut GM by ~0.5 m"),
        ],
        analytics=[
            AnalyticTest("gm_identity", _ck_gm, 5e-4,
                         note="tolerance set by the 4-decimal print precision of KB/BM/GM"),
            AnalyticTest("kb_wall_sided", _ck_kb, 5e-4),
            AnalyticTest("bm_wall_sided", _ck_bm, 5e-4),
        ],
        monotonics=[
            MonotonicTest("gm_decreases_with_kg", field="GM",
                          values=[6.0, 6.5, 7.0, 7.5],
                          transform=lambda v: _stability(kg=float(v)),
                          direction=-1, min_rel_step=0.05,
                          note="sweep kept inside the positive-GM band; beyond it GZ_max "
                               "saturates at 0 and the monotonicity statement degenerates"),
            MonotonicTest("gzmax_decreases_with_kg", field="GZ_max",
                          values=[6.0, 6.5, 7.0, 7.5],
                          transform=lambda v: _stability(kg=float(v)),
                          direction=-1, min_rel_step=0.02),
        ],
        notes=["The endpoint echoes Cw, so KB/BM are checked against the wall-sided law using "
               "the endpoint's own Cw — a weaker claim than an external Cw, stated explicitly."],
    ),
    CapabilitySpec(
        cap_id="propeller.open_water",
        endpoint="/propulsion/predict",
        domain="推进",
        claimed_level="REAL",
        claim_source=_SNAPSHOT,
        base_input=_propulsion(),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12),
        ],
        sensitivities=[
            SensitivityTest("kt_responds_to_pitch", field="kt",
                            transform=lambda p: dict(p, pitch_ratio=p["pitch_ratio"] * 0.9),
                            min_rel_change=0.01, direction=-1,
                            note="-10% P/D must reduce K_T"),
            SensitivityTest("kt_responds_to_expanded_area", field="kt",
                            transform=lambda p: dict(p, ae_ao=p["ae_ao"] * 1.36),
                            min_rel_change=0.01, direction=+1,
                            note="+36% A_E/A_0 must raise K_T (blade-area effect)"),
            SensitivityTest("kq_responds_to_advance", field="kq",
                            transform=lambda p: dict(p, speed=p["speed"] * 1.15),
                            min_rel_change=0.01, direction=-1,
                            note="+15% speed (higher J) must reduce K_Q — this is the "
                                 "knife the 2026-11-01 ledger failed propeller on"),
        ],
        analytics=[
            AnalyticTest("advance_ratio_identity", _ck_advance, 1e-3),
            AnalyticTest("open_water_efficiency_identity", _ck_eta0, 1e-3),
            AnalyticTest("actuator_disk_ideal_efficiency", _ck_eta_ideal, 5e-3),
            AnalyticTest("eta0_below_actuator_disk_limit", _ck_eta_bound, 0.0, fatal=False,
                         note="information-only: eta0 <= eta_i must hold with positive margin"),
        ],
        monotonics=[
            MonotonicTest("kt_decreases_with_j", field="kt",
                          values=[8.0, 10.0, 12.0, 14.0, 16.0],
                          transform=lambda v: _propulsion(speed_kn=float(v)),
                          direction=-1, min_rel_step=0.01),
            MonotonicTest("kq_decreases_with_j", field="kq",
                          values=[8.0, 10.0, 12.0, 14.0, 16.0],
                          transform=lambda v: _propulsion(speed_kn=float(v)),
                          direction=-1, min_rel_step=0.005,
                          note="torque coefficient must fall with advance ratio"),
        ],
        notes=["K_T/K_Q come from the endpoint's own BEMT solve; the probe tests the "
               "definitional identities, not the blade-element physics."],
    ),
    CapabilitySpec(
        cap_id="calc.added_resistance",
        endpoint="/added-resistance/calculate",
        domain="阻力",
        claimed_level="UNREGISTERED",
        claim_source="not present in capabilities_snapshot.json (audit surfaces it)",
        base_input=_added_resistance(),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12),
            InvarianceTest("declared_speed_independence",
                           lambda p: dict(p, speed=p["speed"] * 1.15),
                           1e-9,
                           compare_fields=["R_aw_kN", "lambda_p_m", "heading_factor"],
                           note="STAWAVE-1 declares speed_dependent=false; a speed change "
                                "must not move the raw added resistance"),
        ],
        sensitivities=[
            SensitivityTest("raw_responds_to_hs", field="R_aw_kN",
                            transform=lambda p: dict(p, Hs=p["Hs"] * 1.2),
                            min_rel_change=0.2, direction=+1,
                            note="STAWAVE-1 scales ~Hs^2"),
        ],
        analytics=[
            AnalyticTest("deep_water_dispersion", _ck_wavelength, 1e-6,
                         note="lambda_p = g T_p^2/(2 pi) with standard gravity g=9.80665; "
                              "the Fr identity elsewhere uses 9.81 (mixed constants, 0.03%)"),
        ],
        monotonics=[
            MonotonicTest("raw_increases_with_hs", field="R_aw_kN",
                          values=[1.0, 2.0, 3.0, 4.0, 5.0],
                          transform=lambda v: _added_resistance(hs=float(v)),
                          direction=+1, min_rel_step=0.5),
        ],
        notes=["Claim is UNREGISTERED: the platform exposes the endpoint but the capability "
               "snapshot never lists it, so 'REAL' was never claimed — reported for the record."],
    ),
    CapabilitySpec(
        cap_id="calc.maneuvering",
        endpoint="/maneuvering/turning",
        domain="操纵",
        claimed_level="UNREGISTERED",
        claim_source="not present in capabilities_snapshot.json (audit surfaces it)",
        base_input=_turning(),
        invariances=[
            InvarianceTest("determinism", lambda p: dict(p), 1e-12),
            InvarianceTest("serialization_order", _reversed_keys, 1e-12),
        ],
        sensitivities=[
            SensitivityTest("diameter_responds_to_rudder", field="tactical_diameter",
                            transform=lambda p: dict(p, rudder_angle=20.0),
                            min_rel_change=0.05, direction=+1,
                            note="35 deg -> 20 deg rudder must widen the turning circle"),
            SensitivityTest("advance_responds_to_speed", field="advance",
                            transform=lambda p: dict(p, speed=p["speed"] * 0.75),
                            min_rel_change=0.05, direction=+1,
                            note="advance scales with approach speed"),
        ],
        analytics=[
            AnalyticTest("turning_rate_kinematic_closure", _ck_turning_closure, 0.5,
                         fatal=False,
                         note="information-only: rate vs V/R, endpoint does not declare units"),
        ],
        monotonics=[
            MonotonicTest("diameter_widens_as_rudder_reduces", field="tactical_diameter",
                          values=[35.0, 30.0, 25.0, 20.0, 15.0],
                          transform=lambda v: _turning(rudder=float(v)),
                          direction=+1, min_rel_step=0.02,
                          note="smaller rudder angle must not shrink the circle"),
        ],
        notes=["Tactical diameter, transfer and advance are the classic IMO 3.3 style "
               "figures; a constant-valued answer here is a hardcoded surrogate."],
    ),
]


def spec_by_id(cap_id: str) -> CapabilitySpec:
    for spec in SPECS:
        if spec.cap_id == cap_id:
            return spec
    raise KeyError(cap_id)


def load_claims(snapshot_path: Optional[str] = None) -> Dict[str, str]:
    """Read the platform capability snapshot (claimed levels) if available."""
    import json
    from pathlib import Path

    path = Path(snapshot_path) if snapshot_path else (
        Path(__file__).resolve().parents[2]
        / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json")
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("capabilities", {}))