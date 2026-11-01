"""Integrity gate for the resistance benchmark reference dataset.

The benchmark harness judges the platform's resistance solver against
``backend/data/benchmark_reference.json``.  A reference row that is internally
inconsistent makes every pass/fail verdict downstream meaningless, so this
module turns the audit of ``scripts/audit_benchmark_dataset.py`` into a
regression gate.

The checks below are re-derived *independently* of the audit script (closed
form, no shared helper) so that a bug in one cannot mask the other:

K1 Froude           Fr == V / sqrt(g L)
K2 knot -> m/s      working-frame speed == knots * 0.514444 / sqrt(lambda)
K3 hydrostatics     Cb * L * B * T == m / rho
K4 friction bound   Ct / Cf(ITTC-57) >= 1.02   (hard physical bound)
K5 force closure    Rt == 0.5 rho S V^2 Ct     (where S is stored)
K6 provenance       every row cites a source and declares field provenance
K7 withheld forces  rows without a wetted surface must not carry a bare force

Regression pins record the v1.0 defects (mission 1509) so they cannot return.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.ship_benchmark import (  # noqa: E402
    _load_benchmark_data,
    list_benchmark_cases,
)

DATASET_PATH = BACKEND_DIR / "data" / "benchmark_reference.json"
G = 9.80665
KNOT = 0.514444
TOL_FR = 0.005
TOL_KNOT = 0.005
TOL_MASS = 0.03
TOL_RT = 0.02
CF_FLOOR = 1.02


def cf_ittc57(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


@pytest.fixture(scope="module")
def dataset():
    return json.loads(DATASET_PATH.read_text())


@pytest.fixture(scope="module")
def cases(dataset):
    return dataset["cases"]


def _mass_kg(case):
    geo = case["geometry"]
    if geo.get("displacement_t") is not None:
        return geo["displacement_t"] * 1000.0
    return geo.get("displacement_kg")


# ---------------------------------------------------------------------------
# K1-K3: kinematics and hydrostatics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_idx", range(12))
def test_k1_froude_closure(cases, case_idx):
    case = cases[case_idx]
    L = case["geometry"]["length_m"]
    fr_calc = case["speed_ms"] / math.sqrt(G * L)
    assert case["froude_number"] == pytest.approx(fr_calc, rel=TOL_FR), (
        f"{case['case_id']}: stored Fr={case['froude_number']} vs "
        f"V/sqrt(gL)={fr_calc:.4f}"
    )


@pytest.mark.parametrize("case_idx", range(12))
def test_k2_speed_frame_closure(cases, case_idx):
    case = cases[case_idx]
    if case.get("speed_kn") is None:
        pytest.skip("row has no knot entry")
    lam = case.get("scale_factor", 1.0)
    expected = case["speed_kn"] * KNOT / math.sqrt(lam)
    assert case["speed_ms"] == pytest.approx(expected, rel=TOL_KNOT)


@pytest.mark.parametrize("case_idx", range(12))
def test_k3_hydrostatic_closure(cases, case_idx):
    case = cases[case_idx]
    geo = case["geometry"]
    if geo.get("beam_m") is None:
        pytest.skip("axisymmetric body, no beam/draft")
    vol_geo = geo["length_m"] * geo["beam_m"] * geo["draft_m"] * geo["cb"]
    vol_mass = _mass_kg(case) / case["water_density_kg_m3"]
    assert vol_mass == pytest.approx(vol_geo, rel=TOL_MASS)


# ---------------------------------------------------------------------------
# K4: hard hydrodynamic bound
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_idx", range(12))
def test_k4_ct_above_friction(cases, case_idx):
    case = cases[case_idx]
    L = case["geometry"]["length_m"]
    re = case["speed_ms"] * L / case["kinematic_viscosity_m2_s"]
    ratio = case["experimental_ct"] / cf_ittc57(re)
    assert ratio >= CF_FLOOR, (
        f"{case['case_id']}: Ct/Cf={ratio:.3f} < {CF_FLOOR} "
        f"(Re={re:.2e}) - resistance below flat-plate friction is impossible"
    )
    assert ratio < 3.0, f"{case['case_id']}: Ct/Cf={ratio:.3f} implausibly high"


# ---------------------------------------------------------------------------
# K5/K7: force closure or documented withholding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_idx", range(12))
def test_k5_k7_force_closure_or_withheld(dataset, case_idx):
    case = dataset["cases"][case_idx]
    S = case["geometry"].get("wetted_surface_m2")
    rt = case["experimental_rt_n"]
    if S:
        expected = (0.5 * case["water_density_kg_m3"] * S
                    * case["speed_ms"] ** 2 * case["experimental_ct"])
        assert rt == pytest.approx(expected, rel=TOL_RT), (
            f"{case['case_id']}: Rt={rt} vs 0.5 rho S V^2 Ct={expected:.2f}"
        )
    else:
        withheld = {w["case_id"] for w in dataset["integrity"]["withheld"]}
        assert rt is None, (
            f"{case['case_id']}: force stored without a wetted surface "
            "(closure unverifiable) - withhold it and log the removal"
        )
        assert case["case_id"] in withheld, (
            f"{case['case_id']}: withheld force not documented in integrity.withheld"
        )


# ---------------------------------------------------------------------------
# K6: provenance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_idx", range(12))
def test_k6_provenance_declared(cases, case_idx):
    case = cases[case_idx]
    assert len(case["source_citation"].strip()) >= 20
    prov = case["provenance"]
    for field in ("experimental_ct", "froude_number", "speed_ms",
                  "displacement", "experimental_rt_n"):
        assert prov[field], f"{case['case_id']}: missing provenance for {field}"
    assert case["water_density_kg_m3"] > 0
    assert case["kinematic_viscosity_m2_s"] > 0


# ---------------------------------------------------------------------------
# Regression pins: the four v1.0 defect classes must stay repaired
# ---------------------------------------------------------------------------

def test_dataset_version_and_integrity_block(dataset):
    assert dataset["version"] >= "1.1"
    integ = dataset["integrity"]
    assert len(integ["v1_0_defects"]) == 4
    assert integ["change_log"], "repair must be auditable (before/after log)"
    assert len(integ["withheld"]) == 8, "8 rows lost their unverifiable Rt"


def test_pin_dtmb5415_speed_frame_restored(cases):
    """v1.0 paired model geometry with full-scale knots (Fr up to 2.06)."""
    rows = [c for c in cases if c["ship_type"] == "DTMB-5415"]
    assert len(rows) == 4
    for row in rows:
        # model length 5.72 m = 142 m Lpp / 24.825 -> speeds must be recovered
        assert row["speed_ms"] < 3.2
        assert row["froude_number"] < 0.45, (
            f"{row['case_id']}: Fr={row['froude_number']} - displacement hull "
            "model cannot reach this Froude number"
        )
        assert row["frame"] == "model"
        assert row["speed_ms_full_scale"] == pytest.approx(
            row["speed_kn"] * KNOT, rel=1e-3
        )
    assert rows[0]["speed_ms"] == pytest.approx(0.5162, abs=1e-3)


def test_pin_masses_close_with_cb(cases):
    kv = [c for c in cases if c["ship_type"] == "KVLCC2"][0]
    assert kv["geometry"]["displacement_t"] == pytest.approx(320437.2, rel=1e-3)
    dm = [c for c in cases if c["ship_type"] == "DTMB-5415"][0]
    assert dm["geometry"]["displacement_kg"] == pytest.approx(541.6, rel=1e-3)


def test_pin_suboff_force_recomputed(cases):
    row = cases[0]
    assert row["case_id"] == "SUBOFF-01"
    assert row["experimental_rt_n"] == pytest.approx(92.7, rel=0.01), (
        "v1.0 stored 12.5 N - 7.4x below 0.5 rho S V^2 Ct"
    )


# ---------------------------------------------------------------------------
# Consumer contract: the API exposes the repaired metadata
# ---------------------------------------------------------------------------

def test_list_endpoint_exposes_integrity(cases):
    out = list_benchmark_cases(ship_type="DTMB-5415")
    assert out["total"] == 4
    row = out["cases"][0]
    assert row["provenance"]["experimental_rt_n"].startswith("withheld")
    assert row["usable_for_validation"] is True
    assert "froude_number" in row and "water_density_kg_m3" in row


def test_loading_via_api_matches_disk():
    assert _load_benchmark_data()["version"] == json.loads(
        DATASET_PATH.read_text())["version"]

def test_unusable_rows_are_never_gated(monkeypatch):
    """A row flagged usable_for_validation=False must not reach the gate."""
    import api.domain.ship_benchmark as sb

    data = json.loads(DATASET_PATH.read_text())
    for row in data["cases"]:
        row["usable_for_validation"] = False
    monkeypatch.setattr(sb, "_load_benchmark_data", lambda: data)

    sb._call_resistance_api  # keep import warm
    with pytest.raises(Exception) as exc:
        sb.benchmark_validate(sb.BenchmarkValidateRequest(
            ship_type="SUBOFF", benchmark_id="all"))
    assert "No usable benchmark case" in str(exc.value)


def test_list_endpoint_summarises_integrity():
    out = list_benchmark_cases()
    assert out["dataset_version"] >= "1.1"
    integ = out["integrity"]
    assert integ["n_withheld_fields"] == 8
    assert integ["n_repairs"] == 24
    assert len(integ["defects_repaired"]) == 4
