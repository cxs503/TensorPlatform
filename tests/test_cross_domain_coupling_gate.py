"""Cross-domain coupling consistency gate — mechanism tests.

Fixture policy: every fixture uses *synthetic* domain names (``alpha``/``beta``
fields, units ``uu``/``ku``) so that no real discipline vocabulary leaks into the
platform mechanism layer.  One test asserts that property on the source itself.

Coverage matrix
---------------
spec layer      : unknown key, missing field, duplicate id, bad grade, unknown case
units           : dimension compatibility, conversion factor, mismatch -> SpecError
registry        : sandbox allow-root, provenance sha256, missing evaluator
coupling check  : PASS under tolerance, FAIL over tolerance, WITHHELD on
                  missing evaluator / length mismatch / non-numeric output
coverage check  : 100% wired, incomplete contract -> FAIL, dimension clash -> FAIL
end-to-end      : regression under/over tolerance, missing baseline -> WITHHELD
anti-fraud      : tampered baseline must FAIL (gate is not vacuous)
determinism     : two runs -> identical report_sha256
report layer    : six sections + explicit "<3%" verdict column + figure slots
real pack       : the shipped ship case pack passes with documented numbers
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cross_domain_coupling import report as report_mod  # noqa: E402
from src.cross_domain_coupling.gate import (  # noqa: E402
    EvaluatorRegistry,
    coverage_report,
    coupling_check,
    relative_error,
    run_gate,
)
from src.cross_domain_coupling.spec import SpecError, load_spec  # noqa: E402

PACK_SOURCE = '''
"""Synthetic pack: alpha_* produce beta_* quantities."""


def alpha_value(case):
    scale = case.get("scale", 1.0)
    return [10.0 * scale, 20.0 * scale, 40.0 * scale]


def beta_value(case):
    scale = case.get("scale", 1.0)
    return [10.0 * scale, 20.0 * scale, 40.0 * scale * 1.01]


def alpha_runs(case_set):
    return {c["id"]: {"flux": c["v"] * 2.0, "load": c["v"] * 1.5} for c in case_set["items"]}


def beta_runs(case_set):
    return {c["id"]: {"flux": c["v"] * 2.0, "load": c["v"] * 1.5} for c in case_set["items"]}


def alpha_bad_text(case):
    return ["not-a-number"]


def alpha_short(case):
    return [1.0]
'''


def base_spec(**over):
    spec = {
        "name": "synth_pack",
        "version": "0.1.0",
        "evaluator_module": "pack_mod.py",
        "units": {
            "uu": {"dimension": "length", "to_canonical": 1.0},
            "ku": {"dimension": "length", "to_canonical": 1000.0},
            "zz": {"dimension": "mass", "to_canonical": 1.0},
        },
        "cases": {"case_1": {"scale": 1.0, "a": 2.0, "b": 1.0}},
        "couplings": [
            {
                "id": "k1",
                "title": "alpha -> beta",
                "producer": "alpha.solver",
                "consumer": "beta.solver",
                "quantity": "field_strength",
                "unit": "uu",
                "consumer_unit": "ku",
                "owner": "fixture",
                "version": "1.0.0",
                "evidence_grade": "REAL",
                "frame": "canonical",
                "tolerance_pct": 2.0,
                "primary": "alpha_value",
                "independent": "beta_value",
                "case_key": "case_1",
            }
        ],
        "end_to_end": {
            "evaluator": "alpha_runs",
            "baseline_path": "baseline.json",
            "tolerance_pct": 5.0,
            "case_set_key": "set_1",
        },
        "end_to_end_cases": {"set_1": {"items": [{"id": "item_a", "v": 3.0}, {"id": "item_b", "v": 5.0}]}},
    }
    for key, value in over.items():
        spec[key] = value
    return spec


def write_pack(tmp_path: Path, spec: dict, baseline: dict | None = None) -> Path:
    (tmp_path / "pack_mod.py").write_text(PACK_SOURCE)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    if baseline is not None:
        (tmp_path / "baseline.json").write_text(json.dumps(baseline))
    return spec_path


BASELINE = {"item_a": {"flux": 6.0, "load": 4.5}, "item_b": {"flux": 10.0, "load": 7.5}}


# ------------------------------------------------------------------ spec layer
def test_spec_rejects_unknown_key(tmp_path):
    spec = base_spec(); spec["surprise"] = 1
    with pytest.raises(SpecError, match="unknown key"):
        load_spec(write_pack(tmp_path, spec))


def test_spec_rejects_missing_contract_field(tmp_path):
    spec = base_spec(); del spec["couplings"][0]["owner"]
    with pytest.raises(SpecError, match="missing key"):
        load_spec(write_pack(tmp_path, spec))


def test_spec_rejects_duplicate_coupling_id(tmp_path):
    spec = base_spec(); spec["couplings"].append(dict(spec["couplings"][0]))
    with pytest.raises(SpecError, match="duplicate coupling id"):
        load_spec(write_pack(tmp_path, spec))


def test_spec_rejects_bad_evidence_grade(tmp_path):
    spec = base_spec(); spec["couplings"][0]["evidence_grade"] = "GUESS"
    with pytest.raises(SpecError, match="evidence grade"):
        load_spec(write_pack(tmp_path, spec))


def test_spec_rejects_unknown_case_reference(tmp_path):
    spec = base_spec(); spec["couplings"][0]["case_key"] = "nope"
    with pytest.raises(SpecError, match="unknown case"):
        load_spec(write_pack(tmp_path, spec))


def test_unit_conversion_and_dimension_mismatch(tmp_path):
    spec = load_spec(write_pack(tmp_path, base_spec()))
    assert spec.conversion_factor("ku", "uu") == pytest.approx(1000.0)
    with pytest.raises(SpecError, match="dimension mismatch"):
        spec.conversion_factor("uu", "zz")


# -------------------------------------------------------------- registry layer
def test_registry_sandbox_and_provenance(tmp_path):
    spec_path = write_pack(tmp_path, base_spec())
    spec = load_spec(spec_path)
    mod = (tmp_path / spec.evaluator_module).resolve()
    reg = EvaluatorRegistry(mod, allow_root=tmp_path)
    assert reg.has("alpha_value") and not reg.has("missing_one")
    assert len(reg.sha256) == 64
    with pytest.raises(SpecError, match="escapes allow-root"):
        EvaluatorRegistry(mod, allow_root=tmp_path / "elsewhere")


def test_registry_missing_evaluator_is_withheld(tmp_path):
    spec = load_spec(write_pack(tmp_path, base_spec()))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    rec = coupling_check(spec, spec.couplings[0].__class__(**{**spec.couplings[0].__dict__, "primary": "nope"}), reg)
    assert rec["verdict"] == "WITHHELD"
    assert "not registered" in rec["reason"]


# --------------------------------------------------------------- coupling check
def test_relative_error_floored():
    assert relative_error(1.0, 0.0) < 1e-6 or relative_error(1.0, 0.0) > 1e3  # finite, never ZeroDivision
    assert relative_error(1.01, 1.0) == pytest.approx(1.0)


def test_coupling_pass_within_tolerance(tmp_path):
    spec = load_spec(write_pack(tmp_path, base_spec()))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    rec = coupling_check(spec, spec.couplings[0], reg)
    assert rec["verdict"] == "PASS"
    assert rec["max_error_pct"] == pytest.approx(100 * 0.01 / 1.01, rel=1e-6)
    assert rec["margin_pct"] > 0


def test_coupling_fail_over_tolerance(tmp_path):
    spec = base_spec(); spec["couplings"][0]["tolerance_pct"] = 0.5
    pack = load_spec(write_pack(tmp_path, spec))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    rec = coupling_check(pack, pack.couplings[0], reg)
    assert rec["verdict"] == "FAIL" and rec["margin_pct"] < 0


def test_coupling_non_numeric_output_withheld(tmp_path):
    spec = base_spec(); spec["couplings"][0]["primary"] = "alpha_bad_text"
    pack = load_spec(write_pack(tmp_path, spec))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    assert coupling_check(pack, pack.couplings[0], reg)["verdict"] == "WITHHELD"


def test_coupling_length_mismatch_withheld(tmp_path):
    spec = base_spec(); spec["couplings"][0]["primary"] = "alpha_short"
    pack = load_spec(write_pack(tmp_path, spec))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    rec = coupling_check(pack, pack.couplings[0], reg)
    assert rec["verdict"] == "WITHHELD" and "length mismatch" in rec["reason"]


# ---------------------------------------------------------------- coverage check
def test_coverage_complete(tmp_path):
    spec = load_spec(write_pack(tmp_path, base_spec()))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    cov = coverage_report(spec, reg)
    assert cov["coverage_pct"] == 100.0 and cov["verdict"] == "PASS"
    # 1 uu = 0.001 ku  ->  value_in_uu * factor == value_in_ku
    assert cov["items"][0]["unit_conversion_factor"] == pytest.approx(0.001)


def test_coverage_blank_field_blocks(tmp_path):
    spec = base_spec(); spec["couplings"][0]["owner"] = "   "
    pack = load_spec(write_pack(tmp_path, spec))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    cov = coverage_report(pack, reg)
    assert cov["coverage_pct"] < 100.0 and cov["verdict"] == "FAIL"
    assert "owner" in cov["items"][0]["missing_contract_fields"]


def test_coverage_dimension_clash_blocks(tmp_path):
    spec = base_spec(); spec["couplings"][0]["consumer_unit"] = "zz"
    pack = load_spec(write_pack(tmp_path, spec))
    reg = EvaluatorRegistry((tmp_path / "pack_mod.py").resolve(), allow_root=tmp_path)
    cov = coverage_report(pack, reg)
    assert cov["verdict"] == "FAIL" and cov["items"][0]["dimension_compatible"] is False


# --------------------------------------------------------------- end-to-end + gate
def test_end_to_end_and_overall_pass(tmp_path):
    p = write_pack(tmp_path, base_spec(), BASELINE)
    rep = run_gate(p, allow_root=tmp_path)
    assert rep["overall_verdict"] == "PASS", rep["verdicts"]
    assert rep["verdicts"]["end_to_end_regression"] == "PASS"
    assert rep["summary"]["end_to_end_max_error_pct"] == pytest.approx(0.0, abs=1e-9)


def test_tampered_baseline_fails(tmp_path):
    bad = {"item_a": {"flux": 6.6, "load": 4.5}, "item_b": {"flux": 10.0, "load": 7.5}}
    rep = run_gate(write_pack(tmp_path, base_spec(), bad), allow_root=tmp_path)
    assert rep["verdicts"]["end_to_end_regression"] == "FAIL"
    assert rep["overall_verdict"] == "FAIL"


def test_missing_baseline_withheld(tmp_path):
    rep = run_gate(write_pack(tmp_path, base_spec()), allow_root=tmp_path)
    assert rep["verdicts"]["end_to_end_regression"] == "WITHHELD"
    assert rep["overall_verdict"] != "PASS"


def test_metric_set_mismatch_withheld(tmp_path):
    bad = {"item_a": {"flux": 6.0}}
    rep = run_gate(write_pack(tmp_path, base_spec(), bad), allow_root=tmp_path)
    assert rep["verdicts"]["end_to_end_regression"] == "WITHHELD"


def test_determinism(tmp_path):
    p = write_pack(tmp_path, base_spec(), BASELINE)
    a = run_gate(p, allow_root=tmp_path)
    b = run_gate(p, allow_root=tmp_path)
    assert a["report_sha256"] == b["report_sha256"]
    assert json.dumps(a["criteria"], sort_keys=True) == json.dumps(b["criteria"], sort_keys=True)


# ---------------------------------------------------------------- report layer
def test_report_six_sections_and_verdict_column(tmp_path):
    p = write_pack(tmp_path, base_spec(), BASELINE)
    rep = run_gate(p, allow_root=tmp_path)
    md = report_mod.render_markdown(rep, {"title": "synthetic", "background": "b", "method": "m",
                                          "caveats": "c", "verdict": "v"})
    for _, heading in report_mod.SECTION_TITLES:
        assert heading in md
    assert "< 3% ?" in md and "YES" in md
    assert rep["report_sha256"] in md


# ------------------------------------------------------- zero-domain-semantics
FORBIDDEN_TOKENS = (
    "ship", "hull", "stability", "seakeeping", "resistance", "propuls", "fouling",
    "cii", "gz_", "wave", "tanker", "bulker", "ferry", "kvlcc", "ittc", "plate",
    "panel_stress", "green_navigation", "ice",
)


def test_mechanism_layer_has_no_domain_semantics():
    offenders = {}
    for path in sorted((ROOT / "src" / "cross_domain_coupling").glob("*.py")):
        text = path.read_text().lower()
        # word-boundary match: substring hits such as "cii" inside an unrelated
        # word must not be reported as domain leakage
        hits = [t for t in FORBIDDEN_TOKENS if re.search(r"\b" + re.escape(t), text)]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"domain vocabulary leaked into mechanism layer: {offenders}"


def test_mechanism_layer_has_no_numeric_physics_constants():
    """No physical constants / tolerances may be hardcoded in the engine."""
    text = "".join(p.read_text() for p in (ROOT / "src" / "cross_domain_coupling").glob("*.py"))
    for token in ("9.80665", "1025", "1.19e-6", "0.075", "3.114", "0.242"):
        assert token not in text, f"physical constant {token} hardcoded in mechanism layer"


# ------------------------------------------------------------------- real pack
PACK = ROOT / "domains" / "ship_coupling_gate_v1" / "spec.json"


@pytest.mark.skipif(not PACK.exists(), reason="shipped pack missing")
def test_shipped_pack_passes_with_documented_numbers():
    rep = run_gate(PACK, allow_root=ROOT)
    assert rep["pack"]["name"] == "ship_coupling_gate_v1"
    assert rep["overall_verdict"] == "PASS", rep["verdicts"]
    assert rep["summary"]["coverage_pct"] == 100.0
    tolerances = rep["summary"]["coupling_tolerances_pct"]
    errors = rep["summary"]["coupling_max_errors_pct"]
    assert tolerances == {"C1": 2.0, "C2": 3.0, "C3": 1.0}
    for cid, err in errors.items():
        assert err <= tolerances[cid], (cid, err, tolerances[cid])
    assert rep["summary"]["end_to_end_max_error_pct"] <= 5.0


@pytest.mark.skipif(not PACK.exists(), reason="shipped pack missing")
def test_shipped_pack_baseline_is_pinned():
    rep = run_gate(PACK, allow_root=ROOT)
    e2e = next(c for c in rep["criteria"] if c["criterion"] == "end_to_end_regression")
    frozen = json.loads((PACK.parent / "baseline.json").read_text())
    import hashlib
    assert e2e["baseline_sha256"] == hashlib.sha256(
        (PACK.parent / "baseline.json").read_bytes()).hexdigest()
    assert len(frozen) == 5
