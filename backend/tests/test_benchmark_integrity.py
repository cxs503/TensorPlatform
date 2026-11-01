"""
Tests for the benchmark reference-data integrity gate.

The gate protects the platform's only experimental reference base
(``backend/data/benchmark_reference.json``): a case may only be graded REAL -
and therefore only carry a pass verdict - when the reference row satisfies the
physics identities it is quoted under:

    C_T = R_T / (0.5 rho S V^2)      Fn = V / sqrt(g L)      Delta = rho L B T Cb

Only stdlib is required, so the suite runs without the API/database stack.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services.benchmark_integrity import (  # noqa: E402
    REAL,
    WITHHELD,
    audit_case,
    audit_dataset,
    froude_number,
    implied_wetted_area,
    ship_platform_bases,
    total_resistance_coefficient,
)

DATASET = BACKEND_DIR / "data" / "benchmark_reference.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET.read_text())


@pytest.fixture(scope="module")
def suboff_row(dataset):
    return next(c for c in dataset["cases"] if c["case_id"] == "SUBOFF-01")


@pytest.fixture(scope="module")
def dtmb_row(dataset):
    return next(c for c in dataset["cases"] if c["case_id"] == "DTMB5415-01")


@pytest.fixture(scope="module")
def kvlcc2_row(dataset):
    return next(c for c in dataset["cases"] if c["case_id"] == "KVLCC2-02")


# ---------------------------------------------------------------------------
# Primitive relations
# ---------------------------------------------------------------------------

def test_froude_number_matches_suboff_reference():
    """Fn of SUBOFF at 3 m/s, L = 4.356 m: declared 0.459, recomputed 0.45893."""
    fn = froude_number(3.0, 4.356)
    assert fn == pytest.approx(0.458926, abs=1e-6)
    assert abs(fn - 0.459) / 0.459 < 0.05


def test_implied_wetted_area_roundtrip():
    """C_T = R_T/(0.5 rho S V^2) must invert back to the same area."""
    area = implied_wetted_area(rt_n=1000.0, ct=0.004, speed_ms=5.0, rho=1000.0)
    assert area == pytest.approx(20.0, rel=1e-12)


# ---------------------------------------------------------------------------
# Per-case checks
# ---------------------------------------------------------------------------

def test_suboff_ct_rt_identity_violation_detected(suboff_row):
    """Declared S = 5.42 m^2 but (C_T, R_T) implies 0.73 m^2 -> factor ~7.4."""
    audit = audit_case(suboff_row)
    assert audit["evidence"] == WITHHELD
    assert audit["wetted_area_declared_m2"] == pytest.approx(5.42)
    assert audit["wetted_area_implied_m2"] == pytest.approx(0.7325, rel=1e-3)
    ratio = audit["wetted_area_declared_m2"] / audit["wetted_area_implied_m2"]
    assert ratio == pytest.approx(7.40, rel=0.02)
    assert any("identity violated" in i for i in audit["blocking_issues"])


def test_suboff_froude_declared_value_is_consistent(suboff_row):
    """SUBOFF Froude column is right (rel err 1.6e-4) - the R_T column is not."""
    audit = audit_case(suboff_row)
    assert audit["froude_rel_error"] < 0.01
    assert not any("Froude inconsistent" in i for i in audit["blocking_issues"])


def test_dtmb_froude_column_inconsistent(dtmb_row):
    """DTMB-5415 declares Fn = 0.061 at 2.572 m/s with L = 5.72 m; truth is 0.343."""
    audit = audit_case(dtmb_row)
    assert audit["froude_recomputed"] == pytest.approx(0.3434, rel=1e-3)
    assert audit["froude_rel_error"] > 0.5
    assert any("Froude inconsistent" in i for i in audit["blocking_issues"])


def test_dtmb_geometry_identity_inconsistent(dtmb_row):
    """rho L B T Cb = 541 kg vs declared 466 kg (>10% apart)."""
    audit = audit_case(dtmb_row)
    assert audit["displacement_from_geometry_kg"] == pytest.approx(541.0, rel=0.01)
    assert audit["displacement_rel_error"] > 0.10
    assert any("geometry inconsistent" in i for i in audit["blocking_issues"])


def test_kvlcc2_wetted_area_not_verifiable(kvlcc2_row):
    """No declared S -> the identity cannot be closed -> WITHHELD, not REAL."""
    audit = audit_case(kvlcc2_row)
    assert audit["evidence"] == WITHHELD
    assert any("wetted surface not declared" in i for i in audit["blocking_issues"])


def test_kvlcc2_implied_area_implausible_against_block_envelope(kvlcc2_row):
    """Block envelope L*B + 2LT + 2BT = 34285 m^2; implied area is only ~0.21 of it."""
    audit = audit_case(kvlcc2_row)
    assert audit["envelope_area_m2"] == pytest.approx(34285.0, rel=0.01)
    assert audit["implied_over_envelope"] < 0.4
    assert audit["plausible"] is False


def test_self_consistent_case_is_graded_real():
    """A row that satisfies every identity and carries a citation grades REAL."""
    case = {
        "case_id": "SYNTH-01",
        "ship_type": "SYNTH",
        "speed_ms": 3.0,
        "froude_number": froude_number(3.0, 4.356),
        "experimental_ct": 0.004,
        "experimental_rt_n": 0.004 * 0.5 * 998.0 * 5.42 * 9.0,
        "source_citation": "synthetic row with closed identities",
        "geometry": {"length_m": 4.356, "beam_m": 0.508, "draft_m": 0.508,
                     "wetted_surface_m2": 5.42},
    }
    audit = audit_case(case)
    assert audit["blocking_issues"] == []
    assert audit["evidence"] == REAL


def test_missing_citation_blocks_real():
    case = {
        "case_id": "NOCITE-01",
        "ship_type": "SYNTH",
        "speed_ms": 3.0,
        "froude_number": froude_number(3.0, 4.356),
        "experimental_ct": 0.004,
        "experimental_rt_n": 0.004 * 0.5 * 998.0 * 5.42 * 9.0,
        "geometry": {"length_m": 4.356, "beam_m": 0.508, "draft_m": 0.508,
                     "wetted_surface_m2": 5.42},
    }
    audit = audit_case(case)
    assert audit["evidence"] == WITHHELD
    assert any("citation" in i for i in audit["blocking_issues"])


# ---------------------------------------------------------------------------
# Dataset level
# ---------------------------------------------------------------------------

def test_dataset_audit_withholds_every_case(dataset):
    report = audit_dataset(dataset)
    summary = report["summary"]
    assert summary["total"] == 12
    assert summary["real"] == 0
    assert summary["withheld"] == 12
    assert summary["verdict"] == "REFERENCE_BASE_UNUSABLE"


def test_dataset_audit_detects_within_hull_drift(dataset):
    """SUBOFF rows drift 16.4% in 0.5 rho S, i.e. C_T and R_T were not produced together."""
    hulls = audit_dataset(dataset)["hulls"]
    assert hulls["SUBOFF"]["consistent"] is False
    assert hulls["SUBOFF"]["max_rel_deviation"] == pytest.approx(0.164, abs=0.005)
    assert hulls["DTMB-5415"]["consistent"] is False
    assert hulls["KVLCC2"]["consistent"] is True


def test_tolerance_is_configurable(dataset):
    """A 50% identity tolerance (2.5x the default) does not resurrect the R_T column."""
    report = audit_dataset(dataset, tol_identity=0.50, tol_within_hull=0.25,
                           tol_froude=0.50, tol_geometry=0.50)
    ids = [a["case_id"] for a in report["cases"] if a["evidence"] == WITHHELD]
    assert "SUBOFF-01" in ids  # still a factor-7 identity violation


# ---------------------------------------------------------------------------
# Calculation-API bridge
# ---------------------------------------------------------------------------

def test_ct_derived_from_total_resistance_n():
    """Live engine answers R_T only; C_T must be recovered through 0.5 rho S V^2."""
    response = {"total_resistance_n": 67.3882, "wetted_surface_m2": 2.574}
    case = {"geometry": {"wetted_surface_m2": 5.42}}
    ct = total_resistance_coefficient(response, case, rho=998.0, speed_ms=3.0)
    assert ct == pytest.approx(67.3882 / (0.5 * 998.0 * 5.42 * 9.0), rel=1e-12)
    assert 0.001 < ct < 0.005


def test_ct_none_when_wetted_area_unknown():
    assert total_resistance_coefficient({"total_resistance_n": 10.0}, {}, 998.0, 3.0) is None
    assert total_resistance_coefficient({"total_resistance_n": 0.0}, {}, 998.0, 3.0) is None


def test_platform_bases_env_wins(monkeypatch):
    monkeypatch.setenv("SHIP_PLATFORM_URL", "http://10.0.0.9:9000/")
    bases = ship_platform_bases()
    assert bases[0] == "http://10.0.0.9:9000"
    assert len(bases) == len(set(bases))


def test_platform_bases_fall_back_to_self_hosted_port(monkeypatch):
    monkeypatch.delenv("SHIP_PLATFORM_URL", raising=False)
    assert "http://127.0.0.1:8010" in ship_platform_bases()