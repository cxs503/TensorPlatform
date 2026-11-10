"""Synthetic-domain tests for the cross-solver interoperation gate.

The gate is a platform mechanism, so its tests use *synthetic* domain vocabulary
(``SYNTH-*``, ``amplitude``, ``stiffness``) and temporary artifacts only: the
discriminating power of every check kind has to be provable without any
ecosystem checkout.  The campaign instance is tested separately in
``test_xsolver_interop_campaign.py`` against committed snapshots.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.xsolver.interop import (  # noqa: E402
    FAIL, PASS, WITHHELD, UnitTable, evaluate, load_default_table, summarize,
)
from services.xsolver.interop.gate import GateError, digest_of, validate_manifest  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures: a synthetic two-arm "solver pair"
# ---------------------------------------------------------------------------
@pytest.fixture()
def units() -> UnitTable:
    return load_default_table()


@pytest.fixture()
def workspace(tmp_path: Path, units: UnitTable):
    """Two synthetic arms + a manifest writer."""
    (tmp_path / "artifacts").mkdir()
    left = {
        "frequency_ratio": [0.6, 0.8, 1.0, 1.2, 1.4],
        "amplitude_left": [1.00, 1.40, 1.80, 1.20, 0.70],
        "geometry": {"block_coefficient": 0.700, "length_m": 10.0},
        "stiffness_MPa": [12.0, 18.0, 24.0],
        "components": [0.4, 0.5],
        "total": 0.9,
        "orders": [3],
        "rpm_band": {"low": 80.0, "high": 140.0},
        "barred_band": {"low": 120.0, "high": 140.0},
        "scalars": {"band_lo": 5.0, "band_hi": 15.0, "rated_rpm": 100.0, "blades": 5},
    }
    right = {
        "frequency_ratio": [0.6, 0.8, 1.0, 1.2, 1.4],
        "amplitude_right": [1.03, 1.45, 1.86, 1.22, 0.69],   # <= 10 % of the left arm
        "geometry": {"block_coefficient": 0.701, "length_m": 10.02},
    }
    (tmp_path / "artifacts" / "left.json").write_text(json.dumps(left), encoding="utf-8")
    (tmp_path / "artifacts" / "right.json").write_text(json.dumps(right), encoding="utf-8")

    def run(items, artifacts=None, defaults=None):
        manifest = {
            "campaign": "SYNTH-CAMPAIGN",
            "defaults": defaults or {},
            "artifacts": artifacts or {
                "left": {"path": "artifacts/left.json"},
                "right": {"path": "artifacts/right.json"},
            },
            "items": items,
        }
        return evaluate(manifest, root=tmp_path, units=units)

    return run


def _check(verdict, check_id):
    for item in verdict["items"]:
        for check in item["checks"]:
            if check["id"] == check_id:
                return check
    raise AssertionError(f"check {check_id} not found")


# ---------------------------------------------------------------------------
# unit / dimension machinery
# ---------------------------------------------------------------------------
def test_unit_table_prefixes_and_dimensions(units: UnitTable):
    assert units.compatible("MPa", "Pa")
    assert units.compatible("Hz", "rpm")
    assert not units.compatible("MPa", "Hz")
    assert units.scale_ratio("MPa", "Pa") == pytest.approx(1e6)
    parsed = units.parse("kg_m2")
    assert parsed is not None and dict(parsed["dims"]) == {"L": 2, "M": 1}
    assert units.parse("N_per_m2") is not None  # composite ratio form
    assert units.parse("not_a_unit") is None


def test_field_suffix_unit_is_case_insensitive(units: UnitTable):
    assert units.field_suffix_unit("segment_stresses_MPa") == "MPa"
    assert units.field_suffix_unit("natural_frequencies_hz") == "Hz"
    assert units.field_suffix_unit("plain_name") is None


# ---------------------------------------------------------------------------
# check kinds: each one must both pass and fail on synthetic data
# ---------------------------------------------------------------------------
def test_series_compare_passes_within_tolerance(workspace):
    verdict = workspace([{
        "id": "SYNTH-compare",
        "checks": [{
            "id": "s.pass", "kind": "series_compare", "tolerance": 0.10, "min_points": 5,
            "window": {"min": 0.6, "max": 1.4},
            "left": {"x": {"artifact": "left", "pointer": "/frequency_ratio"},
                     "y": {"artifact": "left", "pointer": "/amplitude_left"}},
            "right": {"x": {"artifact": "right", "pointer": "/frequency_ratio"},
                      "y": {"artifact": "right", "pointer": "/amplitude_right"}},
        }],
    }])
    check = _check(verdict, "s.pass")
    assert check["status"] == PASS
    assert check["detail"]["n_points"] == 5
    assert check["detail"]["max_rel_diff"] < 0.10


def test_series_compare_fails_out_of_tolerance(workspace):
    verdict = workspace([{
        "id": "SYNTH-compare",
        "checks": [{
            "id": "s.fail", "kind": "series_compare", "tolerance": 0.02, "min_points": 5,
            "left": {"x": {"artifact": "left", "pointer": "/frequency_ratio"},
                     "y": {"artifact": "left", "pointer": "/amplitude_left"}},
            "right": {"x": {"artifact": "right", "pointer": "/frequency_ratio"},
                      "y": {"artifact": "right", "pointer": "/amplitude_right"}},
        }],
    }])
    check = _check(verdict, "s.fail")
    assert check["status"] == FAIL
    assert check["fractures"][0]["type"] == "tolerance_exceeded"


def test_series_compare_withholds_when_overlap_too_small(workspace):
    verdict = workspace([{
        "id": "SYNTH-compare",
        "checks": [{
            "id": "s.window", "kind": "series_compare", "tolerance": 0.10, "min_points": 5,
            "window": {"min": 2.0, "max": 3.0},
            "left": {"x": {"artifact": "left", "pointer": "/frequency_ratio"},
                     "y": {"artifact": "left", "pointer": "/amplitude_left"}},
            "right": {"x": {"artifact": "right", "pointer": "/frequency_ratio"},
                      "y": {"artifact": "right", "pointer": "/amplitude_right"}},
        }],
    }])
    assert _check(verdict, "s.window")["status"] == WITHHELD


def test_reference_band_and_trend(workspace):
    verdict = workspace([{
        "id": "SYNTH-band",
        "checks": [
            {"id": "b.inside", "kind": "reference_band", "band": {"lo": 10.0, "hi": 20.0},
             "points": [{"label": "p1", "ref": {"artifact": "left", "pointer": "/stiffness_MPa/0"}}]},
            {"id": "b.outside", "kind": "reference_band", "band": {"lo": 40.0, "hi": 60.0},
             "points": [{"label": "p1", "ref": {"artifact": "left", "pointer": "/stiffness_MPa/0"}}]},
            {"id": "b.trend_ok", "kind": "trend_agreement",
             "computed": {"artifact": "left", "pointer": "/stiffness_MPa"},
             "reference": {"artifact": "left", "pointer": "/stiffness_MPa"}},
            {"id": "b.trend_bad", "kind": "trend_agreement",
             "computed": {"artifact": "left", "pointer": "/stiffness_MPa"},
             "reference": {"artifact": "left", "pointer": "/total"}},
        ],
    }])
    assert _check(verdict, "b.inside")["status"] == PASS
    outside = _check(verdict, "b.outside")
    assert outside["status"] == FAIL
    assert outside["fractures"][0]["type"] == "outside_reference_band"
    assert _check(verdict, "b.trend_ok")["status"] == PASS
    assert _check(verdict, "b.trend_bad")["status"] in (FAIL, WITHHELD)


def test_field_contract_detects_dimension_mismatch(workspace):
    contract = {
        "fields": [
            {"name": "stiffness", "pointer": "/stiffness_MPa",
             "field_name": "stiffness_MPa", "quantity": "stress", "unit_from": "field_suffix"},
        ],
    }
    verdict = workspace([{
        "id": "SYNTH-contract",
        "checks": [
            {"id": "c.ok", "kind": "field_contract", "artifact": "left", "contract": contract},
            {"id": "c.dim", "kind": "field_contract", "artifact": "left",
             "contract": {"fields": [{"name": "stiffness", "pointer": "/stiffness_MPa",
                                      "field_name": "stiffness_MPa", "quantity": "frequency",
                                      "unit_from": "field_suffix"}]}},
            {"id": "c.decl", "kind": "field_contract", "artifact": "left",
             "contract": {"fields": [], "declarations": [
                 {"name": "amplitude_convention",
                  "any_of_pointers": ["/meta/amplitude_convention"]}]}},
        ],
    }])
    assert _check(verdict, "c.ok")["status"] == PASS
    dim = _check(verdict, "c.dim")
    assert dim["status"] == FAIL
    assert dim["detail"]["violations"][0]["type"] == "dimension_mismatch"
    decl = _check(verdict, "c.decl")
    assert decl["status"] == FAIL
    assert decl["detail"]["violations"][0]["type"] == "missing_declaration"


def test_shared_parameter_order_coverage_and_bands(workspace):
    verdict = workspace([{
        "id": "SYNTH-shared",
        "checks": [
            {"id": "p.agree", "kind": "shared_parameter", "parameter": "Cb", "tolerance": 0.02,
             "entries": [{"label": "left", "artifact": "left", "pointer": "/geometry/block_coefficient"},
                         {"label": "right", "artifact": "right", "pointer": "/geometry/block_coefficient"}]},
            {"id": "p.disagree", "kind": "shared_parameter", "parameter": "L", "tolerance": 0.001,
             "entries": [{"label": "left", "artifact": "left", "pointer": "/geometry/length_m"},
                         {"label": "right", "artifact": "right", "pointer": "/geometry/length_m"}]},
            {"id": "p.missing", "kind": "shared_parameter", "parameter": "X", "tolerance": 0.02,
             "entries": [{"label": "left", "artifact": "left", "pointer": "/geometry/length_m"},
                         {"label": "ghost", "artifact": "ghost", "pointer": "/x"}]},
            {"id": "o.covered", "kind": "order_coverage",
             "required": {"artifact": "left", "pointer": "/orders/0"},
             "provided": {"artifact": "left", "pointer": "/orders"}},
            {"id": "o.missing", "kind": "order_coverage",
             "required": {"artifact": "left", "pointer": "/scalars/blades"},
             "provided": {"expr": "hz * 60 / rpm",
                          "bindings": {"hz": {"artifact": "left", "pointer": "/orders/0"},
                                       "rpm": {"artifact": "left", "pointer": "/scalars/rated_rpm"}}}},
            {"id": "band.overlap", "kind": "band_overlap", "expect": "overlap", "min_fraction": 0.9,
             "band_a": {"low": {"artifact": "left", "pointer": "/rpm_band/low"},
                        "high": {"artifact": "left", "pointer": "/rpm_band/high"}},
             "band_b": {"low": {"artifact": "left", "pointer": "/barred_band/low"},
                        "high": {"artifact": "left", "pointer": "/barred_band/high"}}},
            {"id": "band.disjoint", "kind": "band_overlap", "expect": "disjoint",
             "band_a": {"low": {"artifact": "left", "pointer": "/rpm_band/low"},
                        "high": {"artifact": "left", "pointer": "/rpm_band/high"}},
             "band_b": {"low": {"artifact": "left", "pointer": "/barred_band/low"},
                        "high": {"artifact": "left", "pointer": "/barred_band/high"}}},
        ],
    }])
    assert _check(verdict, "p.agree")["status"] == PASS
    assert _check(verdict, "p.disagree")["status"] == FAIL
    assert _check(verdict, "p.missing")["status"] == WITHHELD
    assert _check(verdict, "o.covered")["status"] == PASS
    missing = _check(verdict, "o.missing")
    assert missing["status"] == FAIL and missing["detail"]["missing_orders"] == [5]
    assert _check(verdict, "band.overlap")["status"] == PASS
    assert _check(verdict, "band.disjoint")["status"] == FAIL


def test_additivity_and_aggregate(workspace):
    verdict = workspace([{
        "id": "SYNTH-add",
        "checks": [
            {"id": "a.ok", "kind": "additivity", "tolerance": 0.03,
             "components": {"artifact": "left", "pointer": "/components"},
             "total": {"artifact": "left", "pointer": "/total"}},
            {"id": "a.bad", "kind": "additivity", "tolerance": 0.01,
             "components": {"artifact": "left", "pointer": "/components"},
             "total": {"expr": "t * 1.5", "bindings": {"t": {"artifact": "left", "pointer": "/total"}}}},
            {"id": "a.sum_of_records", "kind": "shared_parameter", "parameter": "I", "tolerance": 0.02,
             "entries": [{"label": "sum", "artifact": "left", "pointer": "/components", "aggregate": "sum"},
                         {"label": "total", "artifact": "left", "pointer": "/total"}]},
        ],
    }])
    assert _check(verdict, "a.ok")["status"] == PASS
    bad = _check(verdict, "a.bad")
    assert bad["status"] == FAIL and bad["fractures"][0]["type"] == "additivity_residual"
    assert _check(verdict, "a.sum_of_records")["status"] == PASS


# ---------------------------------------------------------------------------
# gate-level guarantees
# ---------------------------------------------------------------------------
def test_absent_artifact_never_passes_and_gate_fails_honestly(workspace):
    verdict = workspace([{
        "id": "SYNTH-missing",
        "checks": [{"id": "m.present", "kind": "artifact_present", "artifact": "ghost"}],
    }], artifacts={"ghost": {"path": "artifacts/ghost.json", "missing_reason": "synthetic absence"}})
    assert _check(verdict, "m.present")["status"] == WITHHELD
    assert verdict["gate_status"] == WITHHELD
    assert verdict["fractures"][0]["type"] == "artifact_missing"


def test_fail_dominates_and_cannot_be_upgraded(workspace):
    verdict = workspace([{
        "id": "SYNTH-mixed",
        "checks": [
            {"id": "x.ok", "kind": "artifact_present", "artifact": "left"},
            {"id": "x.bad", "kind": "reference_band", "band": {"lo": 100.0, "hi": 200.0},
             "points": [{"label": "p", "ref": {"artifact": "left", "pointer": "/total"}}]},
        ],
    }])
    assert verdict["gate_status"] == FAIL
    assert verdict["honest_fail"] is True
    assert verdict["upgrade_forbidden"] is True
    # re-running with the manifest that *would* pass produces a different verdict;
    # the recorded FAIL verdict itself carries no upgrade path
    assert "upgrade" not in json.dumps(verdict["items"]).lower()


def test_sha256_pin_detects_tampering(workspace, tmp_path):
    path = tmp_path / "artifacts" / "left.json"
    pinned = json.loads(json.dumps({"path": "artifacts/left.json"}))
    from services.xsolver.interop import sha256_file
    pinned["sha256"] = sha256_file(path)
    verdict = workspace([{"id": "I", "checks": [
        {"id": "t.present", "kind": "artifact_present", "artifact": "left"}]}],
        artifacts={"left": pinned})
    assert _check(verdict, "t.present")["status"] == PASS

    payload = json.loads(path.read_text())
    payload["total"] = 0.1
    path.write_text(json.dumps(payload), encoding="utf-8")
    verdict2 = workspace([{"id": "I", "checks": [
        {"id": "t.present", "kind": "artifact_present", "artifact": "left"}]}],
        artifacts={"left": pinned})
    assert _check(verdict2, "t.present")["status"] == WITHHELD
    assert "sha256 mismatch" in _check(verdict2, "t.present")["detail"]["reason"]


def test_manifest_schema_is_fail_closed():
    with pytest.raises(GateError):
        validate_manifest({"campaign": "SYNTH"})
    with pytest.raises(GateError):
        validate_manifest({"campaign": "SYNTH", "items": []})
    with pytest.raises(GateError):
        validate_manifest({"campaign": "SYNTH", "items": [
            {"id": "I", "checks": [{"id": "c", "kind": "artifact_present"}]},
            {"id": "I", "checks": [{"id": "d", "kind": "artifact_present"}]}]})
    with pytest.raises(GateError):
        validate_manifest({"campaign": "SYNTH", "items": [
            {"id": "I", "checks": [{"id": "c", "kind": "artifact_present"},
                                   {"id": "c", "kind": "artifact_present"}]}]})


def test_unknown_check_kind_is_withheld(workspace):
    verdict = workspace([{"id": "I", "checks": [{"id": "u.kind", "kind": "does_not_exist"}]}])
    assert _check(verdict, "u.kind")["status"] == WITHHELD
    assert _check(verdict, "u.kind")["fractures"][0]["type"] == "unknown_kind"


def test_verdict_digest_is_reproducible(workspace):
    items = [{"id": "I", "checks": [{"id": "d.present", "kind": "artifact_present", "artifact": "left"}]}]
    first, second = workspace(items), workspace(items)
    def payload(verdict):
        return {k: v for k, v in verdict.items() if k not in ("generated_at", "verdict_digest")}

    assert digest_of(payload(first)) == digest_of(payload(second))
    assert first["manifest_digest"] == second["manifest_digest"]
    assert summarize(first).startswith(f"gate {first['gate_status']}")