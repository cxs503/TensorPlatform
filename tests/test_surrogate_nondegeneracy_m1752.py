"""Offline regression tests for the m1752 surrogate non-degeneracy audit.

Two things are being proven here:

1. **The verdict layer is replayable.**  ``judge_all`` is a pure function of the
   probe snapshot, so every verdict in
   ``artifacts/m1752/surrogate_verdicts.json`` can be recomputed here with no
   network access.  A verdict that cannot be replayed cannot be trusted.
2. **The detector is not vacuous.**  Synthetic records are injected on purpose:
   a constant answer, a noisy answer, a plausible-but-wrong scaling law, a
   missing endpoint.  Each must land on its own classification, so a green test
   run means the criteria actually discriminate rather than always passing.

The positive/negative controls from the live battery (a documented ``[MOCK]``
endpoint that injects noise, and the REAL Holtrop engine) are asserted too:
if the detector can no longer catch the known mock, the audit is worthless.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "tools", "audit"))

import surrogate_nondegeneracy_m1752 as sn  # noqa: E402

SNAPSHOT = os.path.join(_ROOT, sn.SNAPSHOT)
VERDICTS = os.path.join(_ROOT, sn.VERDICTS)

ALLOWED_CLASSES = {
    "MOCK_STOCHASTIC", "MOCK_DEGENERATE", "SUSPECT_SCALING",
    "BEHAVES_AS_MODEL", "HEURISTIC_FORMULA_DECLARED_MOCK",
    "UNREACHABLE", "NOT_JUDGED",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def snapshot():
    if not os.path.exists(SNAPSHOT):
        pytest.skip("probe snapshot absent (run --probe first)")
    with open(SNAPSHOT) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def verdicts(snapshot):
    return sn.judge_all(snapshot)


def make_record(rid="syn", base_body=None, repeat_body=None, up_body=None,
                declared=None, speed_key="speed", output_key="rt",
                role="subject", status=200, extra_probes=None):
    """Build a synthetic probe record with the same shape as the snapshot."""
    base_body = {"rt": 100.0} if base_body is None else base_body
    repeat_body = base_body if repeat_body is None else repeat_body
    up_body = {"rt": 400.0} if up_body is None else up_body
    probes = {
        "base": {"payload": {speed_key: 7.0} if speed_key else {},
                 "status": status, "body": base_body},
        "base_repeat": {"payload": {speed_key: 7.0} if speed_key else {},
                        "status": status, "body": repeat_body},
        "speed_up": {"payload": {speed_key: 9.9} if speed_key else {},
                     "status": status, "body": up_body},
    }
    for name, body in (extra_probes or {}).items():
        probes[name] = {"payload": {"__contrast__": 1}, "status": status, "body": body}
    return {"id": rid, "path": f"syn/{rid}", "role": role,
            "label_expected": None, "speed_key": speed_key,
            "output_key": output_key, "openapi_tags": declared,
            "probes": probes}


# ---------------------------------------------------------------------------
# 1. replay integrity
# ---------------------------------------------------------------------------
def test_snapshot_has_records(snapshot):
    assert snapshot["schema"] == "surrogate_nondegeneracy/1"
    assert snapshot["mission"] == 1752
    assert len(snapshot["records"]) >= 9


def test_verdict_layer_replays_bit_for_bit(snapshot):
    """The stored verdicts must equal a fresh offline recomputation."""
    stored = json.load(open(VERDICTS))
    fresh = sn.judge_all(snapshot)
    assert fresh["counts"] == stored["counts"]
    assert fresh["controls"] == stored["controls"]
    pairs = [(a["id"], a["classification"]) for a in fresh["verdicts"]]
    pairs_stored = [(a["id"], a["classification"]) for a in stored["verdicts"]]
    assert pairs == pairs_stored
    for a, b in zip(fresh["verdicts"], stored["verdicts"]):
        assert a["criteria"].keys() == b["criteria"].keys()
        for crit in ("D1", "D2", "D3", "L1"):
            assert (a["criteria"][crit] or {}).get("pass") == \
                   (b["criteria"][crit] or {}).get("pass")


def test_every_endpoint_gets_a_known_classification(verdicts):
    for v in verdicts["verdicts"]:
        assert v["classification"] in ALLOWED_CLASSES, v


def test_unprobed_interface_gaps_are_documented(snapshot):
    """Endpoints the battery cannot drive are recorded, never silently dropped."""
    notes = {n["path"]: n["reason"] for n in snapshot["interface_notes"]}
    assert "performance/predict" in notes and "multipart" in notes["performance/predict"]
    assert "vibration/predict" in notes and "ship_id" in notes["vibration/predict"]
    probed = {r["path"] for r in snapshot["records"]}
    assert not (set(notes) & probed)


# ---------------------------------------------------------------------------
# 2. controls: the detector must catch a known mock and pass a known REAL engine
# ---------------------------------------------------------------------------
def test_positive_control_stochastic_mock_is_caught(verdicts):
    v = next(v for v in verdicts["verdicts"] if v["id"] == "ctl_mock_ai_resistance")
    assert v["classification"] == "MOCK_STOCHASTIC"
    assert v["criteria"]["D1"]["pass"] is False
    assert v["criteria"]["D1"]["max_rel"] > sn.TOL_DETERMINISM


def test_negative_control_real_engine_passes(verdicts):
    v = next(v for v in verdicts["verdicts"] if v["id"] == "ctl_real_resistance_engine")
    assert v["criteria"]["D1"]["pass"] is True
    assert v["criteria"]["D2"]["pass"] is True
    assert v["criteria"]["D3"]["pass"] is True
    lo, hi = sn.EXPONENT_BAND
    assert lo <= v["criteria"]["D3"]["exponent"] <= hi


def test_detector_is_non_vacuous(verdicts):
    assert verdicts["controls"]["stochastic_mock_caught"] is True
    assert verdicts["controls"]["real_engine_passes"] is True
    assert verdicts["controls"]["detector_non_vacuous"] is True


def test_declared_mock_is_never_reported_as_a_model(verdicts):
    """Label honesty both ways: a [MOCK] endpoint may not be called a model."""
    for v in verdicts["verdicts"]:
        declared = (v["criteria"]["L1"] or {}).get("declared")
        if declared == "MOCK":
            assert v["classification"] != "BEHAVES_AS_MODEL", v["id"]


# ---------------------------------------------------------------------------
# 3. mutation tests: each criterion must fire on its own defect
# ---------------------------------------------------------------------------
def test_constant_output_is_degenerate():
    rec = make_record("const", base_body={"rt": 500.0}, repeat_body={"rt": 500.0},
                      up_body={"rt": 500.0})
    v = sn.judge_record(rec)
    assert v["criteria"]["D1"]["pass"] is True
    assert v["criteria"]["D2"]["pass"] is False
    assert v["classification"] == "MOCK_DEGENERATE"


def test_noise_is_caught_as_stochastic():
    rec = make_record("noisy", base_body={"rt": 500.0},
                      repeat_body={"rt": 505.0}, up_body={"rt": 2000.0})
    v = sn.judge_record(rec)
    assert v["criteria"]["D1"]["pass"] is False
    assert v["classification"] == "MOCK_STOCHASTIC"


def test_wrong_scaling_law_is_flagged():
    """Sensitive and deterministic, but R ~ V^0.2 is not ship resistance."""
    rec = make_record("scaling", base_body={"rt": 500.0}, repeat_body={"rt": 500.0},
                      up_body={"rt": 500.0 * (9.9 / 7.0) ** 0.2})
    v = sn.judge_record(rec)
    assert v["criteria"]["D2"]["pass"] is True
    assert v["criteria"]["D3"]["pass"] is False
    assert v["classification"] == "SUSPECT_SCALING"


def test_physical_scaling_passes():
    rec = make_record("ok", base_body={"rt": 100.0}, repeat_body={"rt": 100.0},
                      up_body={"rt": 100.0 * (9.9 / 7.0) ** 2.0})
    v = sn.judge_record(rec)
    assert v["criteria"]["D3"]["pass"] is True
    assert v["classification"] == "BEHAVES_AS_MODEL"


def test_undeclared_degenerate_fails_label_criterion():
    rec = make_record("undecl", base_body={"rt": 1.0}, repeat_body={"rt": 1.0},
                      up_body={"rt": 1.0}, declared=None)
    v = sn.judge_record(rec)
    assert v["criteria"]["L1"]["pass"] is False
    assert v["criteria"]["L1"]["finding"] == "UNDECLARED_DEGENERATE"


def test_declared_mock_label_criterion_passes():
    rec = make_record("decl", base_body={"rt": 1.0}, repeat_body={"rt": 1.0},
                      up_body={"rt": 1.0}, declared="[MOCK] whatever")
    v = sn.judge_record(rec)
    assert v["criteria"]["L1"]["pass"] is True
    assert v["classification"] == "MOCK_DEGENERATE"


def test_contrast_probe_rescues_a_single_lever_artifact():
    """If the physical lever is flat but a contrast probe moves the answer, D2 passes."""
    rec = make_record("resc", base_body={"rt": 1.0}, repeat_body={"rt": 1.0},
                      up_body={"rt": 1.0},
                      extra_probes={"contrast_x": {"rt": 7.0}})
    v = sn.judge_record(rec)
    assert v["criteria"]["D2"]["pass"] is True
    assert v["criteria"]["D2"]["best_changed"] > 0


def test_unreachable_endpoint_is_not_silently_scored():
    rec = make_record("dead", status=500)
    v = sn.judge_record(rec)
    assert v["classification"] == "UNREACHABLE"
    assert v["criteria"]["D2"] is None or v["criteria"]["D2"] == {}


# ---------------------------------------------------------------------------
# 4. helpers: output resolution must not depend on field-name luck
# ---------------------------------------------------------------------------
def test_resolve_output_dotted_and_suffix():
    body = {"output": {"Rt": 12.5}, "predictions": [{"total_resistance": 3.0},
                                                    {"total_resistance": 9.0}]}
    assert sn.resolve_output(body, "output.Rt") == 12.5
    assert sn.resolve_output(body, "Rt") == 12.5
    assert sn.resolve_output(body, "total_resistance") == 9.0   # largest wins
    assert sn.resolve_output(body, "not_there") is None
    assert sn.resolve_output(body, None) is None


def test_numeric_leaves_ignore_bools_and_strings():
    leaves = sn.numeric_leaves({"a": True, "b": "x", "c": 1.5, "d": [0, 2]})
    assert leaves == {"c": 1.5, "d[0]": 0.0, "d[1]": 2.0}


def test_criteria_constants_are_sane():
    lo, hi = sn.EXPONENT_BAND
    assert lo >= 1.0 and hi <= 10.0 and lo < hi
    assert 0 < sn.CROSS_ENGINE_BAND < 1
    assert sn.TOL_DETERMINISM < sn.TOL_SENSITIVITY


# ---------------------------------------------------------------------------
# 5. cross-engine diagnostic present and self-consistent
# ---------------------------------------------------------------------------
def test_cross_engine_ratios_are_recomputable(verdicts, snapshot):
    engine_rec = next(r for r in snapshot["records"]
                      if r["id"] == "ctl_real_resistance_engine")
    eng = sn.resolve_output(engine_rec["probes"]["base"]["body"],
                            engine_rec["output_key"])
    assert eng and eng > 0
    assert verdicts["engine_rt"] == eng
    for row in verdicts["cross_engine"]:
        assert row["ratio"] == pytest.approx(row["value"] / eng)
        assert "units" in row["note"]