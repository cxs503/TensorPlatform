"""
Tests for the hull-geometry consistency gate (Cb / L / B / T / displacement).

Platform rule under test: **no resistance comparison is trustworthy until the
geometry behind it is self-consistent.**  These tests pin

1. the analytical identity itself (cube, half-ellipsoid, unit conversions);
2. the three historical defects found in the benchmark harness
   (DTMB-5415 displacement, KVLCC2 displacement, SUBOFF placeholder preset);
3. the API contract that made every benchmark case run at 1.0 m/s
   (``speed`` sent to a ``speed_ms`` field, plus an unsupported ``displacement``);
4. the audit artefact and the "no silent unverified geometry" rule for presets.

Modules are loaded from file paths because ``backend/api/__init__`` pulls in the
database layer (psycopg2), which is not needed - and not available - for a pure
geometry gate.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
DOMAIN = BACKEND / "api" / "domain"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


GC = _load("geometry_consistency", DOMAIN / "geometry_consistency.py")
SB = _load("ship_benchmark", DOMAIN / "ship_benchmark.py")


@pytest.fixture(scope="module")
def dataset():
    return json.loads((BACKEND / "data" / "benchmark_reference.json").read_text())


# ---------------------------------------------------------------------------
# 1. the identity itself
# ---------------------------------------------------------------------------

def test_identity_cube():
    """A box-shaped hull has Cb == 1 exactly."""
    c = GC.check_merchant_hull("cube", 10.0, 4.0, 2.0, cb=1.0,
                               displacement=80.0, displacement_unit="m3")
    assert c.verdict == GC.PASS
    assert c.implied_cb == pytest.approx(1.0, rel=1e-12)


def test_identity_half_ellipsoid():
    """Submerged half-ellipsoid with semi-axes L/2, B/2, T.

    V = (2/3)*pi*(L/2)*(B/2)*T = (pi/6)*L*B*T  ->  Cb = pi/6 = 0.5236.
    """
    L, B, T = 100.0, 16.0, 6.0
    vol = 3.141592653589793 / 6.0 * L * B * T
    c = GC.check_merchant_hull("half-ellipsoid", L, B, T, cb=3.141592653589793 / 6.0,
                               displacement=vol, displacement_unit="m3")
    assert c.verdict == GC.PASS
    assert c.implied_cb == pytest.approx(3.141592653589793 / 6.0, rel=1e-12)


def test_unit_equivalence_t_kg_m3():
    """307000 t, 307e6 kg and the corresponding volume are the same geometry."""
    vol = GC.displacement_to_volume(307000.0, "t")
    assert vol == pytest.approx(307000.0 * 1000.0 / 1025.0, rel=1e-12)
    assert GC.displacement_to_volume(307000.0 * 1000.0, "kg") == pytest.approx(vol)
    assert GC.displacement_to_volume(vol, "m3") == pytest.approx(vol)


def test_invalid_displacement_rejected():
    with pytest.raises(ValueError):
        GC.displacement_to_volume(-5.0, "m3")
    with pytest.raises(ValueError):
        GC.displacement_to_volume(5.0, "furlongs")


# ---------------------------------------------------------------------------
# 2. the three historical defects
# ---------------------------------------------------------------------------

def test_dtmb5415_old_displacement_fails_gate():
    """The shipped 466.3 kg record implied Cb=0.426, i.e. -16.15% vs stated 0.508."""
    c = GC.check_merchant_hull("DTMB-5415:old", 5.72, 0.759, 0.246, cb=0.508,
                               displacement=466.3, displacement_unit="kg")
    assert c.verdict == GC.FAIL
    assert c.rel_dev_pct == pytest.approx(-16.15, abs=0.05)
    assert c.implied_cb == pytest.approx(0.4260, abs=5e-4)


def test_kvlcc2_old_displacement_warns():
    """307000 t is -4.19% inconsistent with Cb=0.8098 (should be 320474 t)."""
    c = GC.check_merchant_hull("KVLCC2:old", 320.0, 58.0, 20.8, cb=0.8098,
                               displacement=307000.0, displacement_unit="t")
    assert c.verdict in (GC.WARN, GC.FAIL)
    assert c.rel_dev_pct == pytest.approx(-4.19, abs=0.02)


def test_kvlcc2_corrected_passes():
    """SIMMAN 2008 / MOERI value 320474 t closes the identity to <0.02%."""
    c = GC.check_merchant_hull("KVLCC2", 320.0, 58.0, 20.8, cb=0.8098,
                               displacement=320474.0, displacement_unit="t")
    assert c.verdict == GC.PASS
    assert abs(c.rel_dev_pct) < 0.02


def test_suboff_placeholder_preset_is_invalid_not_a_percentage():
    """beam == draft == displacement == cb == 0.508 is a placeholder, not a hull."""
    c = GC.check_preset("SUBOFF", {
        "length": 4.356, "beam": 0.508, "draft": 0.508,
        "displacement": 0.508, "cb": 0.508,
    })
    assert c.verdict == GC.INVALID
    assert "degenerate" in c.note


# ---------------------------------------------------------------------------
# 3. bodies of revolution (SUBOFF-style: no beam, no draft, no Cb)
# ---------------------------------------------------------------------------

def test_body_of_revolution_surface_only():
    """SUBOFF: volume unpublished, S=5.42 m2 -> S/(pi*D*L) = 0.780, inside band."""
    c = GC.check_body_of_revolution("SUBOFF", length=4.356, diameter=0.508,
                                    wetted_surface_m2=5.42)
    assert c.verdict == GC.PASS
    assert c.values["S_over_piDL"] == pytest.approx(0.780, abs=1e-3)


def test_body_of_revolution_surface_out_of_band():
    c = GC.check_body_of_revolution("bad", length=4.356, diameter=0.508,
                                    wetted_surface_m2=5.42 * 4)
    assert c.verdict == GC.FAIL


def test_body_of_revolution_volumetric_coefficient():
    """A spheroid has Cv = 2/3 of the enclosing cylinder."""
    L, D = 10.0, 1.0
    vol = 2.0 / 3.0 * (3.141592653589793 / 4.0) * D ** 2 * L
    c = GC.check_body_of_revolution("spheroid", L, D, volume_m3=vol,
                                    expected_volumetric_coef=2.0 / 3.0)
    assert c.verdict == GC.PASS
    assert c.implied_cb == pytest.approx(2.0 / 3.0, rel=1e-12)


def test_body_of_revolution_missing_evidence_is_invalid():
    c = GC.check_body_of_revolution("empty", 4.356, 0.508)
    assert c.verdict == GC.INVALID


# ---------------------------------------------------------------------------
# 4. dataset-level gate on the *current* repository state
# ---------------------------------------------------------------------------

def test_dataset_geometry_gate_passes(dataset):
    rep = GC.audit_dataset(dataset)
    assert rep["dataset_summary"]["gate_passed"] is True
    assert rep["dataset_summary"]["counts"]["FAIL"] == 0
    assert rep["dataset_summary"]["max_abs_dev_pct"] < 0.1


def test_dataset_records_declare_displacement_consistently(dataset):
    """Every merchant-hull record must satisfy its own stated Cb after the fix."""
    for case in dataset["cases"]:
        g = case["geometry"]
        if case["ship_type"] == "SUBOFF":
            continue
        unit = "t" if "displacement_t" in g else "kg"
        disp = g.get("displacement_t", g.get("displacement_kg"))
        c = GC.check_merchant_hull(case["case_id"], g["length_m"], g["beam_m"],
                                   g["draft_m"], g["cb"], disp,
                                   displacement_unit=unit)
        assert c.verdict == GC.PASS, (case["case_id"], c.note)


def test_dataset_record_without_cb_is_invalid_not_silent():
    c = GC.check_dataset_geometry("X-01", "X", {"length_m": 10.0, "beam_m": 2.0,
                                                "draft_m": 1.0, "displacement_m3": 12.0})
    assert c.verdict == GC.INVALID
    assert "unverified" in c.note


def test_presets_are_all_gated(dataset):
    """No preset may pass unnoticed: each is PASS/WARN or declares its status."""
    rep = GC.audit_dataset(dataset, SB.SHIP_GEOMETRY)
    for row in rep["presets"]:
        if row["verdict"] == GC.INVALID:
            assert row["values"].get("geometry_status"), (
                f"{row['label']} is INVALID without declaring geometry_status")
        else:
            assert abs(row["rel_dev_pct"]) <= GC.DEFAULT_WARN_PCT
    assert rep["preset_summary"]["counts"]["FAIL"] == 0


def test_summarise_tracks_worst_deviation():
    good = GC.check_merchant_hull("a", 100.0, 16.0, 6.0, 0.7, 6720.0)
    bad = GC.check_merchant_hull("b", 100.0, 16.0, 6.0, 0.7, 6000.0)
    s = GC.summarise([good, bad])
    assert s["max_abs_dev_label"] == "b"
    assert s["gate_passed"] is False


# ---------------------------------------------------------------------------
# 5. API contract: the parameter that never reached the solver
# ---------------------------------------------------------------------------

def test_resistance_payload_matches_request_model():
    """``speed`` used to be sent to a ``speed_ms`` field -> 1.0 m/s default for
    every benchmark case; ``displacement`` is not a request field at all."""
    audit = json.loads((REPO / "artifacts" / "m1404" / "geometry_audit.json").read_text())
    contract = audit["api_contract"]
    assert contract["silently_dropped_keys"] == []
    assert "speed_ms" in contract["request_fields"]
    assert "speed_ms" in contract["harness_payload_keys"]
    assert "displacement" not in contract["request_fields"]


def test_harness_forwards_case_speed(monkeypatch):
    """The harness must hand the case speed to the API under the right key."""
    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ct": 0.004, "method": "Holtrop"}

    def _fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return _Resp()

    monkeypatch.setattr("httpx.post", _fake_post)
    SB._call_resistance_api("KVLCC2", 7.202, SB.SHIP_GEOMETRY["KVLCC2"])
    assert captured["speed_ms"] == pytest.approx(7.202)
    assert "speed" not in captured
    assert "displacement" not in captured


def test_benchmark_validate_surfaces_geometry_gate(monkeypatch):
    """The response summary carries the gate, so a bad geometry is visible."""
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ct": 0.004, "method": "Holtrop"}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    out = SB.benchmark_validate(SB.BenchmarkValidateRequest(
        ship_type="KVLCC2", benchmark_id="KVLCC2-01"))
    gate = out.summary["geometry_check"]
    assert gate["available"] is True
    assert gate["preset"]["verdict"] == GC.PASS
    assert gate["dataset"]


def test_benchmark_validate_suboff_reports_unverified_geometry(monkeypatch):
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"ct": 0.0055, "method": "Holtrop"}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    out = SB.benchmark_validate(SB.BenchmarkValidateRequest(
        ship_type="SUBOFF", benchmark_id="SUBOFF-01"))
    assert out.summary["geometry_check"]["preset"]["verdict"] == GC.INVALID


# ---------------------------------------------------------------------------
# 6. audit artefact reproducibility
# ---------------------------------------------------------------------------

def test_audit_artefact_is_up_to_date(tmp_path):
    """Regenerating the audit must reproduce the committed artefact."""
    sys.path.insert(0, str(REPO / "tools" / "ship_perf"))
    try:
        mod = _load("audit_benchmark_geometry",
                    REPO / "tools" / "ship_perf" / "audit_benchmark_geometry.py")
        fresh = mod.build_audit()
    finally:
        sys.path.pop(0)
    stored = json.loads((REPO / "artifacts" / "m1404" / "geometry_audit.json").read_text())
    assert fresh["dataset_summary"] == stored["dataset_summary"]
    assert fresh["preset_summary"] == stored["preset_summary"]
    assert fresh["api_contract"] == stored["api_contract"]
    assert [r["label"] for r in fresh["dataset_geometry"]] == \
           [r["label"] for r in stored["dataset_geometry"]]