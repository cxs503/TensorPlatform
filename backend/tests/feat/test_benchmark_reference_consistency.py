"""Verification-debt guards for the ship-resistance validation chain (mission 1452).

Three failure modes found in the platform are pinned here:

1. the reference dataset behind ``POST /calculations/benchmark_validate`` carried
   Froude numbers and total-resistance values that contradicted their own
   dimensionless coefficients -- every "pass" or "fail" it produced was
   meaningless;
2. the validator read ``response["ct"]`` although no producer published a ``ct``,
   so every comparison was ``0.0`` versus the reference and scored a universal
   100 % error;
3. the resistance core itself was untested against the ITTC-57 line and against
   the definition of the coefficient it reports.

Run with:  cd backend && python3 -m pytest tests/feat/test_benchmark_reference_consistency.py -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

G = 9.81
RHO = 998.0
NU = 1.004e-6


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # postponed annotations need this for resolution
    spec.loader.exec_module(module)
    return module


def _load_ship_benchmark():
    """Load the router module without executing api.domain's package __init__,
    which imports every domain and therefore the database layer."""
    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")
    return _load_module("ship_benchmark_under_test",
                        BACKEND / "api" / "domain" / "ship_benchmark.py")


def _load_audit_module():
    path = REPO / "tools" / "audit_resistance_validation.py"
    spec = importlib.util.spec_from_file_location("audit_resistance_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_audit_module()
from services.ship_resistance_core import compute_resistance  # noqa: E402  (needs BACKEND on sys.path)
DATASET = json.loads((BACKEND / "data" / "benchmark_reference.json").read_text())


# ---------------------------------------------------------------------------
# 1. Reference-dataset physical consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda c: c["case_id"])
def test_reference_case_is_physically_consistent(case):
    """Every reference case must satisfy I1-I5 of the audit tool."""
    result = audit.audit_case(case)
    assert result["verdict"] == "CONSISTENT", (
        f"{case['case_id']} violates {result['failed_invariants']}: "
        f"{json.dumps([c for c in result['checks'] if not c['ok']], indent=2)}"
    )


def test_reference_dataset_declares_its_corrections():
    """The repaired dataset must carry provenance, not silently changed numbers."""
    corrections = DATASET.get("corrections")
    assert corrections, "dataset corrections block missing"
    assert corrections["audit"].endswith("audit_resistance_validation.py")
    assert corrections["n_fixes_applied"] > 0
    for case in DATASET["cases"]:
        # any case whose Rt was nulled must say why
        if case.get("experimental_rt_n") is None:
            assert case["rt_n_provenance"] == "unavailable_no_wetted_surface"
        else:
            assert case["rt_n_provenance"] == "derived_from_ct"


def test_no_reference_case_contradicts_its_own_coefficient():
    """Rt must equal 0.5*rho*S*V^2*Ct wherever both are tabulated."""
    for case in DATASET["cases"]:
        rt, ct = case.get("experimental_rt_n"), case["experimental_ct"]
        s = case["geometry"].get("wetted_surface_m2")
        if rt is None:
            continue
        assert s, f"{case['case_id']}: Rt tabulated without a documented wetted surface"
        rho = audit.RHO_SEA if case["geometry"]["length_m"] > 50 else RHO
        expected = 0.5 * rho * s * case["speed_ms"] ** 2 * ct
        assert abs(rt - expected) / expected < 0.02, (
            f"{case['case_id']}: reported Rt={rt} N vs {expected:.1f} N implied by Ct"
        )


# ---------------------------------------------------------------------------
# 2. Resistance core against the ITTC-1957 line and the Ct definition
# ---------------------------------------------------------------------------

def test_ittc57_against_closed_form():
    """Cf must reproduce the ITTC-1957 correlation line to machine precision."""
    assert audit.ittc57_cf(1e6) == pytest.approx(4.6875e-3, rel=1e-12)
    assert audit.ittc57_cf(1e7) == pytest.approx(3.0e-3, rel=1e-12)
    assert audit.ittc57_cf(1e8) == pytest.approx(2.083333e-3, rel=1e-6)
    assert audit.ittc57_cf(1e9) == pytest.approx(1.530612e-3, rel=1e-6)


def test_resistance_core_is_force_and_coefficient_consistent():
    res = compute_resistance(
        speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.542,
    )
    s = res["wetted_surface_m2"]
    re_expected = 3.0 * 4.356 / NU
    fn_expected = 3.0 / math.sqrt(G * 4.356)

    assert res["reynolds_number"] == pytest.approx(re_expected, rel=1e-9)
    assert res["froude_number"] == pytest.approx(fn_expected, rel=1e-9)
    assert res["cf"] == pytest.approx(audit.ittc57_cf(re_expected), rel=1e-12)

    # force closure against the coefficient the function publishes
    assert res["total_resistance_n"] == pytest.approx(
        0.5 * RHO * s * 3.0 ** 2 * res["ct"], rel=1e-12
    )
    # and the published ct must be the sum of its parts
    assert res["ct"] == pytest.approx(res["cf"] + res["cr"], rel=1e-12)
    # Ct must clear the ITTC-57 floor (there is always form + wave on top)
    assert res["ct"] > res["cf"]


def test_resistance_scales_with_speed_squared_at_fixed_coefficient():
    """Doubling speed at Froude-similar Ct raises Rt by ~4x (sanity on units)."""
    lo = compute_resistance(speed_ms=1.5, length=4.356, beam=0.508, draft=0.508, cb=0.542)
    hi = compute_resistance(speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.542)
    assert hi["total_resistance_n"] / lo["total_resistance_n"] == pytest.approx(4.0, rel=0.35)


def test_resistance_rejects_out_of_range_block_coefficient():
    with pytest.raises(ValueError):
        compute_resistance(speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.1)


# ---------------------------------------------------------------------------
# 3. Validator contract
# ---------------------------------------------------------------------------

def _legacy_force_response(case, rho=RHO):
    """A service response shaped like the deployed one: force, no 'ct'."""
    geom = case["geometry"]
    s = geom["wetted_surface_m2"] or 4.0
    rt = 0.5 * rho * s * case["speed_ms"] ** 2 * case["experimental_ct"]
    return {"rt": rt, "wetted_surface": s, "method": "ITTC-57 + empirical Cr"}


def test_extract_ct_reconstructs_coefficient_from_force():
    sb = _load_ship_benchmark()

    case = next(c for c in DATASET["cases"] if c["case_id"] == "SUBOFF-01")
    geometry = sb.geometry_from_case(case)
    ct = sb.extract_ct(_legacy_force_response(case), geometry, case["speed_ms"])
    assert ct == pytest.approx(case["experimental_ct"], rel=1e-9)


def test_benchmark_validate_scores_against_reference(monkeypatch):
    """End-to-end: a service that reproduces the reference must pass."""
    sb = _load_ship_benchmark()

    case = next(c for c in DATASET["cases"] if c["case_id"] == "SUBOFF-01")
    monkeypatch.setattr(sb, "_call_resistance_api",
                        lambda ship_type, speed_ms, geometry: _legacy_force_response(case))

    resp = sb.benchmark_validate(sb.BenchmarkValidateRequest(
        ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
    result = resp.results[0]
    assert result.calc_ct == pytest.approx(case["experimental_ct"], rel=1e-9)
    assert result.error_pct < 1.0
    assert result.pass_ is True
    assert resp.summary["passed"] == 1


def test_benchmark_validate_flags_instead_of_scoring_ineligible_cases(monkeypatch):
    """Out-of-envelope cases are excluded, not counted as algorithm failures."""
    sb = _load_ship_benchmark()

    monkeypatch.setattr(sb, "_call_resistance_api",
                        lambda ship_type, speed_ms, geometry: {"rt": 1.0, "wetted_surface": 4.0})
    resp = sb.benchmark_validate(sb.BenchmarkValidateRequest(
        ship_type="DTMB-5415", benchmark_id="all"))

    assert resp.summary["excluded_cases"], "no case was excluded despite Fn>envelope"
    scored = {r.case_id for r in resp.results}
    for excluded in resp.summary["excluded_cases"]:
        assert excluded["case_id"] not in scored
        assert "excluded" in json.dumps(resp.summary)


def test_benchmark_validate_geometry_comes_from_the_case_not_the_preset():
    sb = _load_ship_benchmark()

    case = next(c for c in DATASET["cases"] if c["case_id"] == "KVLCC2-01")
    geometry = sb.geometry_from_case(case)
    assert geometry["length"] == case["geometry"]["length_m"]
    assert geometry["beam"] == case["geometry"]["beam_m"]
    assert geometry["draft"] == case["geometry"]["draft_m"]
    assert geometry["cb"] == case["geometry"]["cb"]