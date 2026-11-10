"""C3/C4/C5 — caliber reader: verdict accuracy, fail-closed corrupt case, real replay."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from tools.caliber_reader import lines as clines
from tools.caliber_reader import reader as creader
from tools.caliber_reader import synth as csynth

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "benchmarks/verified/data_assimilation")
RESULTS = os.path.join(BENCH, "results.json")


@pytest.fixture(scope="module")
def lines():
    return clines.load_lineset(os.path.join(BENCH, "config/lines.json"))


@pytest.fixture(scope="module")
def rcfg():
    with open(os.path.join(BENCH, "config/reader_config.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def results():
    if not os.path.isfile(RESULTS):
        pytest.skip("run tools.data_assimilation.run_benchmark first")
    with open(RESULTS, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- lines ----
def test_line_kinds_and_range_fail_closed():
    ls = clines.LineSet.from_mapping({
        "log": {"kind": "log_power", "a": 0.075, "b": 2.0, "c": 2.0,
                "valid_range": [1e4, 1e10]},
        "off": {"kind": "power_offset", "a": 0.006, "b": 100.0, "c": 0.16, "d": -0.00205,
                "valid_range": [50.0, 1000.0]},
        "const": {"kind": "constant", "value": 0.5},
    })
    assert ls.eval("log", 1e7).in_range
    outside = ls.eval("log", 10.0)
    assert outside.in_range is False and outside.note
    assert ls.eval("off", 200.0).value == pytest.approx(
        0.006 * 300.0 ** -0.16 - 0.00205, rel=1e-12)
    assert ls.eval("const", 1.0).value == 0.5
    with pytest.raises(clines.LineError):
        ls.eval("log", 0.0)
    with pytest.raises(clines.LineError):
        clines.LineSet.from_mapping({"bad": {"kind": "nope"}})


def test_lineset_fingerprint_tracks_config():
    a = clines.LineSet.from_mapping({"l": {"kind": "constant", "value": 1.0}})
    b = clines.LineSet.from_mapping({"l": {"kind": "constant", "value": 2.0}})
    assert a.source_fingerprint != b.source_fingerprint


# --------------------------------------------------------------- reader ----
def test_six_synthetic_cases_are_judged_correctly(lines, rcfg, tmp_path):
    """C3: 6/6 verdict accuracy against the recorded ground truth."""
    paths = csynth.write_cases(str(tmp_path), lines, rcfg)
    assert len(paths) == 6
    got = {}
    for path in paths:
        table = json.load(open(path, "r", encoding="utf-8"))
        verdict = creader.interpret(table, lines, rcfg)
        got[table["case_id"]] = verdict["status"]
        assert verdict["status"] == table["expected"]["status"], (table["case_id"],
                                                                  verdict["reason"])
    assert got["case_01_declared_consistent"] == creader.CONSISTENT
    assert got["case_02_undeclared_scale"] == creader.CALIBER_INFERRED
    assert got["case_03_allowance_omitted"] == creader.SUBSIDY_OMITTED
    assert got["case_04_extra_allowance"] == creader.ALLOWANCE_DETECTED
    assert got["case_05_degraded"] == creader.DEGRADED
    assert got["case_06_corrupt"] == creader.WITHHELD_CORRUPT


def test_undeclared_case_recovers_the_scale_assignment(lines, rcfg, tmp_path):
    paths = csynth.write_cases(str(tmp_path), lines, rcfg)
    path = [p for p in paths if "case_02" in p][0]
    table = json.load(open(path, "r", encoding="utf-8"))
    verdict = creader.interpret(table, lines, rcfg)
    assert verdict["scale_assignment"] == table["expected"]["scale_assignment"]
    assert verdict["form_factor"] == pytest.approx(1.18, rel=1e-6)
    assert verdict["residual_coefficient"] == pytest.approx(0.30, rel=1e-6)
    assert verdict["similarity"]["ok"] is True
    assert verdict["error_decision_allowed"] is True


def test_allowance_case_quantifies_the_hidden_allowance(lines, rcfg, tmp_path):
    paths = csynth.write_cases(str(tmp_path), lines, rcfg)
    path = [p for p in paths if "case_04" in p][0]
    table = json.load(open(path, "r", encoding="utf-8"))
    verdict = creader.interpret(table, lines, rcfg)
    assert verdict["allowance_fraction"] == pytest.approx(0.06, abs=0.005)
    assert verdict["error_decision_allowed"] is False


def test_single_scale_table_is_withheld(lines, rcfg):
    rows = [{"row_id": "a", "object_id": "o", "speed_m_s": 2.0, "length_m": 6.0,
             "density_kg_m3": 998.2, "viscosity_m2_s": 1.004e-6, "coefficient": 0.004},
            {"row_id": "b", "object_id": "o", "speed_m_s": 2.5, "length_m": 6.0,
             "density_kg_m3": 998.2, "viscosity_m2_s": 1.004e-6, "coefficient": 0.0045}]
    verdict = creader.interpret({"table_id": "t", "rows": rows}, lines, rcfg)
    assert verdict["status"] == creader.WITHHELD_INSUFFICIENT
    assert verdict["error_decision_allowed"] is False


def test_declared_caliber_contradiction_is_detected(lines, rcfg, tmp_path):
    """A table whose labels are swapped must be flagged, not silently accepted."""
    paths = csynth.write_cases(str(tmp_path), lines, rcfg)
    path = [p for p in paths if "case_01" in p][0]
    table = json.load(open(path, "r", encoding="utf-8"))
    for row in table["rows"]:
        row["scale"] = "full" if row["row_id"].startswith("R") else "reduced"
    verdict = creader.interpret(table, lines, rcfg)
    assert verdict["status"] == creader.CALIBER_CONTRADICTION
    assert verdict["error_decision_allowed"] is False


def test_force_coefficient_identity_violation_is_corrupt(lines, rcfg, tmp_path):
    paths = csynth.write_cases(str(tmp_path), lines, rcfg)
    path = [p for p in paths if "case_01" in p][0]
    table = json.load(open(path, "r", encoding="utf-8"))
    table["rows"][0]["force_N"] = table["rows"][0]["force_N"] * 1000.0
    verdict = creader.interpret(table, lines, rcfg)
    assert verdict["status"] == creader.WITHHELD_CORRUPT
    assert verdict["exit_code"] != 0


def test_corrupt_case_exits_non_zero_via_cli(tmp_path):
    """C4: the CLI must exit non-zero on the corrupt table."""
    case = os.path.join(BENCH, "cases/case_06_corrupt.json")
    proc = subprocess.run([sys.executable, "-m", "tools.caliber_reader", case],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "WITHHELD" in proc.stdout


# --------------------------------------------------------------- replay ----
def test_replay_recovers_form_factor_and_attributes_deviation(lines, rcfg, results):
    """C5: inversion recovers the quoted form factor; the deviation is a caliber artefact."""
    c5 = results["criteria"]["C5"]
    assert c5["pass"], c5
    assert abs(c5["recovered"]["form_factor_error_pct"]) < 0.5
    assert abs(c5["recovered"]["residual_term_error_pct"]) < 0.5
    assert c5["attribution"]["attribution"] == "CALIBER"
    assert abs(c5["post_conversion_deviation_pct"]) < c5["tolerance_pct"]
    assert abs(c5["naive_deviation_pct"]) > 10.0
    steps = {s["factor"]: s["delta_pct_of_reference"] for s in c5["attribution"]["steps"]}
    # the naive gap is a near-cancellation of two ~40-point caliber terms (scale vs
    # residual), which is exactly why a caliber-blind percentage is meaningless
    assert steps["scale_effect"] < -40.0
    assert steps["residual_term"] > 40.0
    assert abs(steps["scale_effect"] + steps["residual_term"]) < 5.0
    assert c5["attribution"]["dominant_factor"] in ("scale_effect", "residual_term")


def test_invert_pair_is_exact_on_constructed_data(lines, rcfg):
    """Two rows, two unknowns: the inversion must close to machine precision."""
    l_red, scale = 6.0, 30.0
    k1, c_res, fn = 1.18, 0.30, 0.26
    cf_r = lines.eval(rcfg["viscous_line"], fn * np.sqrt(9.80665 * l_red) * l_red /
                      1.004e-6).value
    l_full = l_red * scale
    cf_f = lines.eval(rcfg["viscous_line"], fn * np.sqrt(9.80665 * l_full) * l_full /
                      1.188e-6).value
    ca = lines.eval(rcfg["allowance_line"], l_full).value
    c_r = k1 * cf_r + c_res * fn ** rcfg["residual_exponent"]
    c_f = k1 * cf_f + c_res * fn ** rcfg["residual_exponent"] + ca
    red = creader.Row("r", "o", fn * np.sqrt(9.80665 * l_red), l_red, 998.2, 1.004e-6, c_r)
    full = creader.Row("f", "o", fn * np.sqrt(9.80665 * l_full), l_full, 1025.0, 1.188e-6, c_f)
    inv = creader.invert_pair(red, full, lines, rcfg)
    assert inv["form_factor"] == pytest.approx(k1, rel=1e-9)
    assert inv["residual_term"] == pytest.approx(
        c_res * fn ** rcfg["residual_exponent"], rel=1e-9)
    assert abs(inv["closure_pct"]["reduced"]) < 1e-9
    assert abs(inv["closure_pct"]["full"]) < 1e-9


def test_attribution_flags_unexplained_gaps():
    """If the factors do not explain the gap, the attribution must say so."""
    att = creader.attribute_deviation(1.0, 1.0, [("identity", 1.0)])
    assert att["attribution"] == "CALIBER" and att["confidence"] == "HIGH"
    bad = creader.attribute_deviation(1.5, 1.0, [("wrong", 1.0)])
    assert bad["attribution"] == "UNEXPLAINED" and bad["confidence"] == "LOW"
    assert bad["dominant_factor"] == "wrong"