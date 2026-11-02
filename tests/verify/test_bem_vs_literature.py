"""Verification-debt tests for the first-principles (BEM) open-water model.

Mission 1393 certified ``backend/services/propeller_open_water.py`` with analytic
bounds only.  Mission 1697 cross-checked it against the canonical Wageningen
B-series regression and against the KP505 open-water experiment and found a
systematic thrust bias that the analytic bounds cannot see: ``K_T`` is up to
+56 % high while ``eta_0 < eta_ideal`` is violated **zero** times.

These tests therefore do three things:

1. **Drift gate** — recompute a few operating points live and compare with the
   numbers recorded in ``artifacts/m1697/bem_literature_audit/audit.json``, so a
   silent change of the model (or of the reference table) is caught.
2. **Characterisation bands** — assert the *measured* deviation bands.  A fix
   (post-stall section behaviour, solidity-consistent induction) will push the
   deviations below these bands and break the tests loudly, which is the intended
   forcing function: the bands must then be tightened and the evidence grade
   re-assessed, not silently left behind.
3. **Physics invariants** — monotone ``K_Q`` decay with ``J``, correct ``J_0``
   ordering with pitch ratio, and ``eta_0 < eta_ideal`` everywhere.

Run:  pytest tests/verify/test_bem_vs_literature.py -q
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

AUDIT_JSON = REPO_ROOT / "artifacts" / "m1697" / "bem_literature_audit" / "audit.json"
DIAMETER = 5.0
RHO = 1025.0

#: Deviations measured by mission 1697, expressed as (low, high) admissible bands
#: on ``max |K_T - K_T_ref| / K_T_ref``.  Tighten (never widen) after a model fix.
KT_BIAS_BANDS = {
    "B4-70": (0.20, 0.45),
    "B4-55": (0.40, 0.75),
    "B5-80": (0.30, 0.60),
    "B3-50": (0.02, 0.12),
}
#: ``J_0`` must stay *below* the regression root by this much (all four cases).
J0_BIAS_BAND = (-0.13, -0.03)


def _audit() -> dict:
    if not AUDIT_JSON.exists():
        pytest.skip(
            "audit artifact missing — run "
            "`python3 -m tools.verify.propeller_open_water.audit_bem_vs_literature`"
        )
    return json.loads(AUDIT_JSON.read_text(encoding="utf-8"))


def _geom(P_D: float, Ae_Ao: float, Z: int) -> PropellerGeometry:
    return PropellerGeometry(
        diameter_m=DIAMETER, pitch_ratio=P_D, blade_area_ratio=Ae_Ao, blade_count=Z
    )


def _case(name: str) -> dict:
    data = _audit()
    for c in data["vs_bseries"]["cases"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"case {name} not in audit artifact")


# ---------------------------------------------------------------------------
# 1. drift gate: recorded numbers must be reproducible
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,J", [("B4-70", 0.40), ("B5-80", 0.20), ("B3-50", 0.60)])
def test_recorded_points_are_reproducible(name: str, J: float) -> None:
    """Recompute a spot point and match the recorded audit values to 1e-9."""
    case = _case(name)
    recorded = min(case["points"], key=lambda r: abs(r["J"] - J))
    assert abs(recorded["J"] - J) < 1e-9, "artifact does not contain the expected J"
    bem = open_water_point(_geom(case["P_D"], case["Ae_Ao"], case["Z"]), J, rho=RHO)
    assert bem["KT"] == pytest.approx(recorded["KT_bem"], rel=1e-9, abs=1e-12)
    assert bem["KQ"] == pytest.approx(recorded["KQ_bem"], rel=1e-9, abs=1e-12)


def test_reference_table_still_matches_platform_data_file() -> None:
    """The clean-room B-series table must stay identical to the platform table."""
    candidates = [
        REPO_ROOT / "backend" / "data" / "official" / "Wageningen_Bseries_coeffs.dat",
        # mission 1403 audited the incoming platform tree; reuse its copy if the
        # table is not vendored in this repository
        Path("/srv/tensor/ship-workspace/ship-performance-platform")
        / "backend" / "data" / "official" / "Wageningen_Bseries_coeffs.dat",
    ]
    dat = next((c for c in candidates if c.exists()), None)
    if dat is None:
        pytest.skip("platform coefficient table absent from both repo and incoming tree")
    parsed = []
    for line in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) != 5:
            continue
        try:
            parsed.append((float(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])))
        except ValueError:
            continue
    if not parsed:
        pytest.skip("unexpected coefficient-file layout")
    n_kt = len(ref.KT_COEFFICIENTS)
    assert {t[:5] for t in parsed[:n_kt]} == set(ref.KT_COEFFICIENTS)


# ---------------------------------------------------------------------------
# 2. characterisation of the recorded literature deviation (the debt itself)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(KT_BIAS_BANDS))
def test_kt_bias_against_bseries_stays_in_documented_band(name: str) -> None:
    """``K_T`` deviation from the canonical regression is inside the measured band.

    Breaking this test means the model moved: if the deviation *fell* below the
    band the model was fixed and the band (and the evidence grade, see
    ``docs/verifier/BEM_OPEN_WATER_LITERATURE_AUDIT.md``) must be tightened.
    """
    case = _case(name)
    low, high = KT_BIAS_BANDS[name]
    assert low <= case["max_abs_KT_rel"] <= high, (
        f"{name}: max |dKT| = {case['max_abs_KT_rel']:.3f} outside documented band "
        f"[{low}, {high}] — re-run the audit harness and re-grade the evidence"
    )


def test_zero_thrust_advance_ratio_is_systematically_low() -> None:
    """All four geometries cross zero thrust early — a thrust-curve slope error."""
    data = _audit()
    for case in data["vs_bseries"]["cases"]:
        lo, hi = J0_BIAS_BAND
        assert lo <= case["J0_rel"] <= hi, (
            f"{case['name']}: J0 deviation {case['J0_rel']:+.3f} outside {J0_BIAS_BAND}"
        )


def test_sign_disagreement_is_confined_to_the_overloaded_pitch_case() -> None:
    """Only B4-55 (P/D=0.8) inverts the sign of ``K_T`` near J0, at most twice."""
    data = _audit()
    per_case = {c["name"]: c["sign_disagreements"] for c in data["vs_bseries"]["cases"]}
    assert per_case["B4-55"] <= 2
    assert sum(v for k, v in per_case.items() if k != "B4-55") == 0


def test_analytic_bound_cannot_see_the_bias() -> None:
    """The methodological finding of mission 1697, locked as an assertion.

    A ``K_T`` bias well above the 15 % acceptance band coexists with **zero**
    actuator-disc-bound violations, i.e. m1393's acceptance criteria were blind to
    an amplitude error of that size.
    """
    summary = _audit()["vs_bseries"]["summary"]
    assert summary["eta_bound_violations"] == 0
    assert summary["max_abs_KT_rel"] > 0.40
    assert summary["frac_KT_within_15pct"] < 0.60


def test_kp505_experiment_comparison_is_recorded() -> None:
    """Live check against the measured KP505 open-water test."""
    data = _audit()["vs_kp505_experiment"]
    case = data["case"]
    geom = _geom(case["P_D"], case["Ae_Ao"], case["Z"])
    max_rel = 0.0
    for row in data["points"]:
        bem = open_water_point(geom, row["J"], rho=RHO)
        assert bem["KT"] == pytest.approx(row["KT_bem"], rel=1e-9, abs=1e-12)
        if abs(row["KT_exp"]) >= 0.05:              # relative error defined
            max_rel = max(max_rel, abs((bem["KT"] - row["KT_exp"]) / row["KT_exp"]))
    # Recorded debt: the model misses the measured thrust by >40 % somewhere.
    assert max_rel == pytest.approx(data["summary"]["max_abs_KT_bem_rel"], abs=5e-3)
    assert max_rel > 0.40


# ---------------------------------------------------------------------------
# 3. physics invariants must keep holding (do not regress while fixing the bias)
# ---------------------------------------------------------------------------
def test_torque_decreases_monotonically_with_advance_ratio() -> None:
    geom = _geom(1.0, 0.70, 4)
    kq = [open_water_point(geom, J / 100.0, rho=RHO)["KQ"] for J in range(5, 86, 5)]
    assert all(b < a for a, b in zip(kq, kq[1:])), "K_Q must fall with J"


def test_efficiency_stays_below_the_actuator_disc_bound() -> None:
    for P_D, Ae, Z in ((1.0, 0.70, 4), (0.95, 0.80, 5), (1.2, 0.50, 3)):
        geom = _geom(P_D, Ae, Z)
        for J in (0.2, 0.4, 0.6):
            p = open_water_point(geom, J, rho=RHO)
            assert 0.0 <= p["eta0"] < p["eta_ideal"] <= 1.0


def test_zero_thrust_ratio_grows_with_pitch_ratio() -> None:
    J0 = [solve_zero_thrust_advance_ratio(_geom(pd, 0.55, 4), rho=RHO) for pd in (0.8, 1.0, 1.2)]
    assert all(b > a for a, b in zip(J0, J0[1:])), f"J0 must increase with P/D, got {J0}"
    assert all(0.5 < v < 1.6 for v in J0)


def test_froude_scaling_invariance_is_exact() -> None:
    k = 3.7
    base = PropellerGeometry(diameter_m=DIAMETER, pitch_ratio=1.0, blade_area_ratio=0.70, blade_count=4)
    scaled = PropellerGeometry(diameter_m=k * DIAMETER, pitch_ratio=1.0, blade_area_ratio=0.70, blade_count=4)
    a = open_water_point(base, 0.4, rho=RHO, n_rps=1.0)
    b = open_water_point(scaled, 0.4, rho=RHO, n_rps=1.0 / k)
    assert b["KT"] == pytest.approx(a["KT"], rel=1e-9)
    assert b["KQ"] == pytest.approx(a["KQ"], rel=1e-9)

# ---------------------------------------------------------------------------
# 4. batch-scan wrapper wiring (MOCK surrogate was still in the serving path)
# ---------------------------------------------------------------------------
def _wrapper():
    import importlib.util

    path = REPO_ROOT / "tools" / "batch_scan" / "batch_executor.py"
    spec = importlib.util.spec_from_file_location("_batch_executor_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.ShipCalculationEngine


def test_batch_wrapper_executes_no_hardcoded_surrogate() -> None:
    """The removed MOCK formula must not reappear as *code* (docstrings may cite it)."""
    import io
    import tokenize

    src = (REPO_ROOT / "tools" / "batch_scan" / "batch_executor.py").read_text(encoding="utf-8")
    code = " ".join(
        tok.string
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    for marker in ("0.5 - 0.3", "0.05 + 0.02", "min(0.85, max(0.3"):
        assert marker not in code, f"hard-coded open-water surrogate marker {marker!r} is back"


def test_batch_wrapper_output_equals_direct_service_call() -> None:
    """The wrapper must serve the BEM service itself, not an approximation."""
    engine = _wrapper()
    params = {
        "diameter_m": 5.0,
        "pitch_ratio": 0.95,
        "blade_area_ratio": 0.80,
        "blade_count": 5,
        "rpm": 120.0,
        "advance_speed_kn": 10.0,
    }
    out = engine.calc_propeller(params)
    n = params["rpm"] / 60.0
    J = params["advance_speed_kn"] * 0.5144 / (n * params["diameter_m"])
    direct = open_water_point(_geom(0.95, 0.80, 5), J, rho=RHO, n_rps=n)
    assert out["model"] == "bem-blade-element-momentum"
    assert out["thrust_coefficient"] == pytest.approx(direct["KT"], abs=1e-4)
    assert out["torque_coefficient"] == pytest.approx(direct["KQ"], abs=1e-4)
    assert out["thrust_kn"] == pytest.approx(direct["thrust_n"] / 1000.0, abs=0.01)


def test_batch_wrapper_keeps_physical_trends() -> None:
    engine = _wrapper()
    base = {"diameter_m": 5.0, "pitch_ratio": 1.0, "blade_area_ratio": 0.70,
            "blade_count": 4, "rpm": 120.0}
    kq = [engine.calc_propeller(dict(base, advance_speed_kn=kn))["torque_coefficient"]
          for kn in (2.0, 6.0, 10.0, 14.0)]
    assert all(b < a for a, b in zip(kq, kq[1:])), f"K_Q must fall with advance ratio, got {kq}"
