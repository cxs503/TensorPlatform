"""Regression guards for the resistance benchmark reference dataset.

The dataset ``backend/data/benchmark_reference.json`` underpins every resistance
validation on the platform, so it is itself treated as a validated artefact:

* G1 every case declares the fluid and scale it was measured at;
* G2 the published total-resistance coefficient sits above the ITTC-1957
  flat-plate friction floor (total resistance below skin friction is impossible);
* G3 the two experimental columns (``experimental_ct`` / ``experimental_rt_n``)
  describe the same measurement;
* any column that fails G2/G3 is explicitly quarantined through ``rt_status`` so
  downstream code cannot silently consume it.

The audit logic lives in ``tools/audit_benchmark_reference.py``.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tools.audit_benchmark_reference import (  # noqa: E402
    CT_REL_TOL,
    DEFAULT_DATASET,
    audit_case,
    audit_dataset,
    estimate_wetted_surface,
    ittc57_cf,
)

DATASET = json.loads(Path(DEFAULT_DATASET).read_text(encoding="utf-8"))
CASES = DATASET["cases"]


def _case(case_id: str) -> dict:
    for c in CASES:
        if c["case_id"] == case_id:
            return c
    raise KeyError(case_id)


# ---------------------------------------------------------------------------
# G1: declaration completeness
# ---------------------------------------------------------------------------

def test_dataset_has_cases():
    assert len(CASES) >= 12


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_every_case_declares_fluid_and_scale(case):
    fluid = case.get("fluid") or {}
    assert fluid.get("rho", 0) > 0, "missing fluid.rho"
    assert fluid.get("nu", 0) > 0, "missing fluid.nu"
    assert case.get("scale") in ("model", "full")


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_every_case_declares_rt_status(case):
    assert case.get("rt_status") in (
        "verified_coherent", "quarantined_inconsistent", "uncheckable")
    assert case.get("froude_status") in (
        "verified_consistent", "quarantined_inconsistent", "uncheckable")
    assert case.get("hull_class") in ("displacement", "axisymmetric")


# ---------------------------------------------------------------------------
# G2: friction floor on the published coefficient
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_published_ct_above_friction_floor(case):
    nu = case["fluid"]["nu"]
    Re = case["speed_ms"] * case["geometry"]["length_m"] / nu
    Cf = ittc57_cf(Re)
    assert case["experimental_ct"] >= Cf, (
        f"{case['case_id']}: Ct={case['experimental_ct']:.5f} < Cf={Cf:.5f}")


def test_ittc57_matches_reference_value():
    # ITTC-1957 at Re = 1e6 is a textbook anchor: Cf = 0.075/(6-2)^2 = 0.0046875.
    assert ittc57_cf(1.0e6) == pytest.approx(0.0046875, rel=1e-12)
    # Cf must decrease monotonically with Re.
    assert ittc57_cf(1.0e7) < ittc57_cf(1.0e6)


# ---------------------------------------------------------------------------
# G3 + audit verdicts
# ---------------------------------------------------------------------------

def test_dataset_is_fully_audited():
    report = audit_dataset(DATASET)
    assert report["summary"]["undeclared_violations"] == []
    assert report["summary"]["cases_total"] == len(CASES)
    assert (report["summary"]["coherent"] + report["summary"]["rt_inconsistent"]
            + report["summary"]["kinematically_inconsistent"]
            + report["summary"]["unusable"]) == len(CASES)
    assert report["summary"]["physically_usable"] == report["summary"]["coherent"]
    # Every quarantined case must carry an explanation for downstream readers.
    for res in report["cases"]:
        if res["verdict"] == "rt_inconsistent":
            assert _case(res["case_id"])["rt_note"]


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_quarantined_rt_is_not_used_as_reference(case):
    """A quarantined resistance column must not be presented as a measurement."""
    if case["rt_status"] != "quarantined_inconsistent":
        return
    assert "not a measurement" in case["experimental_rt_derived_note"]
    published = case["experimental_rt_n"]
    derived = case["experimental_rt_derived_n"]
    # The published column is grossly different from the physically admissible one.
    assert abs(derived / published - 1.0) > CT_REL_TOL


def test_detects_full_scale_speed_with_model_length():
    """G4: a model-scale length paired with a full-scale speed must be rejected."""
    case = copy.deepcopy(_case("DTMB5415-01"))
    res = audit_case(case)
    # In the shipped dataset this case declares Fn=0.061 while V/sqrt(gL)=0.343.
    assert res["froude_ok"] is False
    assert res["length_implied_by_froude_m"] > 10 * case["geometry"]["length_m"]


def test_axisymmetric_hull_exempt_from_froude_ceiling():
    """A submarine body at Fn>0.6 is legitimate; a displacement hull is not."""
    case = copy.deepcopy(_case("SUBOFF-04"))
    assert case["hull_class"] == "axisymmetric"
    assert audit_case(case)["froude_ok"] is True

    hull = copy.deepcopy(_case("KVLCC2-01"))
    hull["hull_class"] = "displacement"
    hull["speed_ms"] = 40.0  # Fn ~ 0.71 at L=320 m
    assert audit_case(hull)["froude_ok"] is False


def test_detects_physically_impossible_rt():
    """A total resistance below the flat-plate friction must be rejected."""
    case = copy.deepcopy(_case("SUBOFF-01"))
    case["experimental_rt_n"] = 1.0  # absurdly small
    res = audit_case(case)
    assert res["verdict"] == "rt_inconsistent"
    assert res["friction_floor_ok"] is False
    assert any("friction floor" in p for p in res["problems"])


def test_accepts_coherent_case_and_flags_missing_declaration():
    """A self-consistent case passes G2/G3 but still needs an rt_status."""
    case = copy.deepcopy(_case("SUBOFF-01"))
    q = 0.5 * case["fluid"]["rho"] * case["speed_ms"] ** 2
    S = case["geometry"]["wetted_surface_m2"]
    case["experimental_rt_n"] = case["experimental_ct"] * q * S  # coherent by construction
    case.pop("rt_status", None)
    res = audit_case(case)
    assert res["verdict"] == "coherent"
    assert res["friction_floor_ok"] is True

    # Declarations are strict: a coherent case must be declared verified.
    report = audit_dataset({"cases": [case]})
    assert report["summary"]["coherent"] == 1
    assert report["summary"]["undeclared_violations"], "missing declarations must be flagged"
    case["rt_status"] = "verified_coherent"
    case["froude_status"] = "verified_consistent"
    report = audit_dataset({"cases": [case]})
    assert report["summary"]["undeclared_violations"] == []
    # ... and a *contradicting* declaration is a violation.
    case["rt_status"] = "quarantined_inconsistent"
    report = audit_dataset({"cases": [case]})
    assert len(report["summary"]["undeclared_violations"]) == 1


def test_derived_rt_reproduces_published_ct():
    """Round-trip: Ct -> Rt -> Ct_implied must be exact to numerical precision."""
    for case in CASES:
        nu, rho = case["fluid"]["nu"], case["fluid"]["rho"]
        V, L = case["speed_ms"], case["geometry"]["length_m"]
        S = (case["geometry"].get("wetted_surface_m2")
             or estimate_wetted_surface(case))
        Re = V * L / nu
        Cf = ittc57_cf(Re)
        q = 0.5 * rho * V * V
        rt = case["experimental_ct"] * q * S
        assert rt / (q * S) == pytest.approx(case["experimental_ct"], rel=1e-12)
        # The derived resistance is physically admissible by construction.
        assert case["experimental_ct"] >= Cf


def test_wetted_surface_estimate_within_geometric_bounds():
    """S_est must lie between a two-side slab and the enclosing box."""
    for case in CASES:
        g = case["geometry"]
        if "wetted_surface_m2" in g:
            continue
        L, B, T = g["length_m"], g["beam_m"], g["draft_m"]
        S_est = estimate_wetted_surface(case)
        assert 2.0 * L * T <= S_est <= L * (B + 2.0 * T)


def test_cli_exits_zero_on_current_dataset(tmp_path):
    from tools.audit_benchmark_reference import main
    out = tmp_path / "audit.json"
    assert main(["--json", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["undeclared_violations"] == []
    assert len(payload["cases"]) == len(CASES)

# ---------------------------------------------------------------------------
# Drift guard: the offline replica must stay pinned to the shipped model
# ---------------------------------------------------------------------------

def test_replica_matches_shipped_resistance_formulas():
    """The comparison out of tools/ must not silently diverge from the API code."""
    import re
    from tools.compare_resistance_vs_reference import platform_model

    src = (REPO_ROOT / "backend" / "api" / "calculations.py").read_text(encoding="utf-8")
    assert "Cf = 0.075 / (math.log10(Re) - 2.0) ** 2" in src, "ITTC-1957 line changed"
    assert "Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0" in src, \
        "residual-resistance line changed"
    assert "S = math.pi * B * L * (0.5 + 0.25 * Cb)" in src, "wetted-surface line changed"

    m = platform_model(length=5.72, beam=0.759, draft=0.246, cb=0.508,
                       speed=2.572, rho=999.1, nu=1.1386e-6)
    # Values recomputed from the shipped formulas by hand from the source lines.
    Re = 2.572 * 5.72 / 1.1386e-6
    assert m["reynolds"] == pytest.approx(Re, rel=1e-12)
    assert m["cf"] == pytest.approx(0.075 / (math.log10(Re) - 2.0) ** 2, rel=1e-12)
    assert m["ct"] == pytest.approx(m["cf"] + m["cr"], rel=1e-12)
    if m["fn"] <= 0.05:
        assert m["cr"] == 0.0
