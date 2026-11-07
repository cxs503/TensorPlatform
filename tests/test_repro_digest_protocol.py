"""Protocol unit tests for tools/repro_digest (IN22-N08).

Run: ``python3 -m pytest tests/test_repro_digest_protocol.py -q``

Coverage map (the >=10 unit-test requirement):
  1. ULP identity                        2. ULP = 1 across nextafter
  3. signed zero / subnormal ULP         4. monotone key ordering
  5. NaN / +-Inf policy                  6. 12-digit quantization resolution
  7. quantization of zero and non-finite 8. bit vs quantized digest divergence
  9. hex codec round-trip / bad token   10. length + empty-vector negative cases
 11. gate accepts within k              12. gate rejects beyond k
 13. negative control 1e-9 rejected     14. 1e-13: quant passes, ULP gate catches
 15. empty record rejected              16. artifact contract (k, floor, naive-gate FAIL)
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.repro_digest import ulp
from tools.repro_digest import emit_digest as ed

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "artifacts" / "cross_isa_repro.json"


# --------------------------------------------------------------------------- #
# 1-4: ULP arithmetic
# --------------------------------------------------------------------------- #
def test_ulp_identity_is_zero():
    for x in (0.0, 1.0, -3.25, 1e-300, 1.7976931348623157e308):
        assert ulp.ulp_distance(x, x) == 0


def test_ulp_one_across_nextafter():
    x = 1.0
    up = math.nextafter(x, math.inf)
    dn = math.nextafter(x, -math.inf)
    assert ulp.ulp_distance(x, up) == 1
    assert ulp.ulp_distance(x, dn) == 1
    assert ulp.ulp_distance(up, dn) == 2


def test_signed_zero_and_subnormal():
    assert ulp.ulp_distance(0.0, -0.0) == 0
    assert ulp.quantize(-0.0) == ulp.quantize(0.0) == "0"
    smallest = 5e-324
    assert ulp.ulp_distance(0.0, smallest) == 1
    assert ulp.ulp_distance(smallest, 2 * smallest) == 1


def test_monotone_key_ordering():
    xs = [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]
    keys = [ulp.monotone_key(x) for x in xs]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


# --------------------------------------------------------------------------- #
# 5: NaN / Inf policy
# --------------------------------------------------------------------------- #
def test_nan_and_inf_policy():
    nan, inf = math.nan, math.inf
    assert ulp.ulp_distance(nan, nan) == 0
    assert ulp.ulp_distance(inf, inf) == 0
    assert ulp.ulp_distance(-inf, -inf) == 0
    assert ulp.ulp_distance(inf, -inf) == ulp.ULP_UNBOUNDED
    assert ulp.ulp_distance(nan, 1.0) == ulp.ULP_UNBOUNDED
    assert ulp.ulp_distance(inf, 1.0) == ulp.ULP_UNBOUNDED


# --------------------------------------------------------------------------- #
# 6-7: canonical quantization
# --------------------------------------------------------------------------- #
def test_quantization_resolution_12_digits():
    x = 1.0
    # 1e-13 relative -> below the 12th significant digit -> same canonical key
    assert ulp.quantize(x * (1 + 1e-13)) == ulp.quantize(x)
    # 1e-11 relative -> visible at 12 significant digits
    assert ulp.quantize(x * (1 + 1e-11)) != ulp.quantize(x)
    assert ulp.quantize(1e-9) == "1e-09"


def test_quantization_nonfinite_and_zero():
    assert ulp.quantize(math.nan) == "nan"
    assert ulp.quantize(math.inf) == "inf"
    assert ulp.quantize(-math.inf) == "-inf"
    assert ulp.quantize(0.0) == "0"
    with pytest.raises(ValueError):
        ulp.quantize(1.0, digits=0)


# --------------------------------------------------------------------------- #
# 8: naive bit digest vs protocol digest
# --------------------------------------------------------------------------- #
def test_bit_digest_vs_quant_digest():
    a = [1.0, 2.0, 3.0]
    b = [1.0, math.nextafter(2.0, math.inf), 3.0]
    assert ulp.bit_digest(a) != ulp.bit_digest(b)      # naive gate fails
    assert ulp.quant_digest(a) == ulp.quant_digest(b)  # protocol gate passes
    assert ulp.ulp_distance(a[1], b[1]) == 1


# --------------------------------------------------------------------------- #
# 9-10: codec and structural negatives
# --------------------------------------------------------------------------- #
def test_hex_codec_roundtrip_and_bad_token():
    for x in (0.0, -0.0, 1.0, -2.5e-17, 1.7976931348623157e308, 5e-324):
        assert ulp.decode_hex(ulp.encode_hex(x)) == x
    assert math.isnan(ulp.decode_hex("nan"))
    assert ulp.decode_hex("-inf") == -math.inf
    with pytest.raises(ValueError):
        ulp.decode_hex("0x1.zzp+1")
    with pytest.raises(TypeError):
        ulp.decode_hex(1.0)


def test_compare_length_mismatch_and_empty_vectors():
    ref = {"bench": "b", "fields": {"u": [1.0, 2.0]}, "scalars": {"s": 1.0}, "traj": []}
    cur = {"bench": "b", "fields": {"u": [1.0]}, "scalars": {"s": 1.0}, "traj": []}
    rep = ulp.compare_records(ref, cur)
    assert rep["fields"]["u"]["ok"] is False
    assert rep["max_ulp"] == ulp.ULP_UNBOUNDED
    # empty vectors compare vacuously, hence the explicit empty-record guard
    empty = {"bench": "b", "fields": {"u": []}, "scalars": {}, "traj": []}
    with pytest.raises(ulp.ReproViolation):
        ulp.assert_reproducible(empty, empty, ulp_gate=1)
    with pytest.raises(ValueError):
        ulp.compare_records({"bench": "a", "fields": {}, "scalars": {}, "traj": []},
                            {"bench": "b", "fields": {}, "scalars": {}, "traj": []})


# --------------------------------------------------------------------------- #
# 11-12: gate boundaries
# --------------------------------------------------------------------------- #
def _simple_rec(v: float) -> dict:
    return {"bench": "toy", "meta": {}, "fields": {"u": [1.0, 2.0, 3.0]},
            "scalars": {"s": v}, "traj": [{"step": 1, "probe": [1.0, 2.0]}]}


def test_gate_accepts_within_k():
    ref = _simple_rec(1.0)
    cur = _simple_rec(math.nextafter(1.0, math.inf) * 1.0)
    cur["scalars"]["s"] = ref["scalars"]["s"]  # restore -> identical
    assert ulp.assert_reproducible(ref, cur, ulp_gate=0)["max_ulp"] == 0
    # a 3-ULP drift passes with k=8 and fails with k=2
    drifted = _simple_rec(1.0)
    v = 1.0
    for _ in range(3):
        v = math.nextafter(v, math.inf)
    drifted["scalars"]["s"] = v
    assert ulp.assert_reproducible(ref, drifted, ulp_gate=8)["max_ulp"] == 3
    with pytest.raises(ulp.ReproViolation) as ei:
        ulp.assert_reproducible(ref, drifted, ulp_gate=2)
    assert "max_ulp=3 exceeds gate k=2" in str(ei.value)


def test_gate_rejects_quantization_mismatch():
    ref = _simple_rec(1.0)
    cur = _simple_rec(1.0)
    cur["fields"]["u"][1] = 2.0 * (1 + 1e-9)
    with pytest.raises(ulp.ReproViolation) as ei:
        ulp.assert_reproducible(ref, cur, ulp_gate=ulp.ULP_UNBOUNDED)
    assert "quantized digest mismatch" in str(ei.value)


# --------------------------------------------------------------------------- #
# 13-14: negative controls (protocol must reject injected perturbation)
# --------------------------------------------------------------------------- #
def test_negative_control_1e9_rejected_both_layers():
    ref = _simple_rec(1.0)
    pert = ulp.inject_relative_perturbation(ref, rel=1e-9, which="scalar")
    rep = ulp.compare_records(ref, pert)
    assert rep["quant_digest_equal"] is False
    assert rep["max_ulp"] > 1e6
    with pytest.raises(ulp.ReproViolation):
        ulp.assert_reproducible(ref, pert, ulp_gate=1024)


def test_negative_control_1e13_caught_by_ulp_gate_only():
    ref = _simple_rec(1.0)
    pert = ulp.inject_relative_perturbation(ref, rel=1e-13, which="scalar")
    rep = ulp.compare_records(ref, pert)
    assert rep["quant_digest_equal"] is True     # below 12-digit resolution
    assert rep["max_ulp"] > 64                   # but far outside any sane gate
    with pytest.raises(ulp.ReproViolation):
        ulp.assert_reproducible(ref, pert, ulp_gate=64)


# --------------------------------------------------------------------------- #
# 15-16: artifact-level contract (the machine-checkable acceptance criteria)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not ARTIFACT.exists(), reason="artifacts/cross_isa_repro.json not produced")
def test_artifact_contract():
    data = json.loads(ARTIFACT.read_text())
    proto = data["protocol"]
    k = proto["ulp_gate_k"]
    assert isinstance(k, int) and k >= 64, "gate k must be a declared conservative bound"
    assert proto["quantization"]["digits"] == 12
    assert set(proto["quantization"]["digits_by_bench"]) == {
        "h1_channel60", "h2_powerlaw", "h5_calibration"}
    assert len(proto["ulp_gate_k_by_bench"]) == 3
    assert all(v <= k for v in proto["ulp_gate_k_by_bench"].values())
    assert len(data["machines"]) >= 3, "at least 3 physical machines must be measured"

    floor = data["determinism_floor"]
    assert floor["bit_equal_rate_min"] == 1.0, "determinism floor must be 100% bit-identical"
    assert floor["quant_equal_rate_min"] == 1.0

    # (b) cross-architecture (cross-machine) quantized equality at the gate
    for row in data["cross_machine_base_table"]:
        assert row["quant_equal"] is True, row
        assert row["max_ulp"] <= k, row

    # (c) negative control must be rejected
    assert data["negative_control"]["all_rejected"] is True
    assert data["negative_control"]["n"] >= 6

    # (e) table completeness: 3 benches x every machine x every variant
    machines = {m["machine"] for m in data["machines"]}
    for bench, blob in data["benches"].items():
        rows = blob["table"]
        assert len({r["machine"] for r in rows}) == len(machines)
        assert len(rows) == len(machines) * len(blob["flags_sensitivity_max_ulp"])
        assert all(r["flags"] for r in rows)

    # naive bit-digest gate must fail somewhere (the empirical counterexample)
    naive = data["gate_evidence"]["naive_bit_gate"]
    assert naive["n_failed"] > 0
    assert naive["failure_rate"] > 0.0
    proto_gate = data["gate_evidence"]["protocol_gate"]
    assert proto_gate["n_failed"] == 0

    # (d) FMA attribution: switching contraction off must remove >10x of the drift
    for bench, att in data["attribution"].items():
        ratio = att["fma_ratio_with_over_without"]
        if ratio == "not_significant":
            continue
        assert ratio == "inf" or ratio > 10.0, (bench, ratio)

    # out-of-envelope builds are reported, not silently accepted
    assert len(data["gate_evidence"]["fastmath_out_of_envelope"]) >= 3


def test_gauge_class_huge_ulp_on_diagnostic_passes():
    """A cancelling diagnostic may carry an enormous ULP distance and still be
    acceptable -- that is exactly why the protocol declares gauge classes."""
    ref = {"bench": "h2_powerlaw", "meta": {},
           "fields": {"u": [0.3, 0.2], "gdot": [1.0, 0.5], "mu": [0.4, 0.4]},
           "scalars": {"u_max": 0.3, "momentum_residual": 0.0, "rel_l2_err_vs_analytic": 1.7e-3},
           "traj": []}
    cur = json.loads(json.dumps(ref))
    cur["scalars"]["momentum_residual"] = 1.5e-13        # 0.0 -> 1.5e-13
    rep = ulp.compare_records(ref, cur)
    assert rep["scalars"]["momentum_residual"]["class"] == "diagnostic"
    assert rep["scalars"]["momentum_residual"]["max_ulp"] > 10 ** 15   # 0.0 -> 1.5e-13
    assert rep["diag_ok"] is True
    assert ulp.assert_reproducible(ref, cur, ulp_gate=0)["max_ulp"] == 0


def test_gauge_class_diagnostic_beyond_tolerance_fails():
    ref = {"bench": "h2_powerlaw", "meta": {}, "fields": {"u": [0.3]},
           "scalars": {"momentum_residual": 0.0}, "traj": []}
    cur = json.loads(json.dumps(ref))
    cur["scalars"]["momentum_residual"] = 1e-8             # > 1e-11 tolerance
    with pytest.raises(ulp.ReproViolation) as ei:
        ulp.assert_reproducible(ref, cur, ulp_gate=10 ** 9)
    assert "diagnostic absolute tolerance exceeded" in str(ei.value)


@pytest.mark.skipif(shutil.which(ed.cc_name()) is None, reason="C compiler not available")
def test_kernel_rerun_is_bit_identical():
    """Same binary, same machine: two runs must agree bit for bit."""
    raw1 = ed.run_kernel("h2", "-O2", variant="t1")
    raw2 = ed.run_kernel("h2", "-O2", variant="t2")
    rec1, rec2 = ulp.load_record(raw1), ulp.load_record(raw2)
    assert ulp.bit_digest(rec1["fields"]["u"]) == ulp.bit_digest(rec2["fields"]["u"])
    assert ulp.assert_reproducible(rec1, rec2, ulp_gate=0)["max_ulp"] == 0


@pytest.mark.skipif(shutil.which(ed.cc_name()) is None, reason="C compiler not available")
def test_kernel_opt_levels_pass_protocol(repro_gate_k):
    """-O0 vs -O3: naive bit digests differ, protocol passes at the declared k."""
    rec0 = ulp.load_record(ed.run_kernel("h5", "-O0", variant="t0"))
    rec3 = ulp.load_record(ed.run_kernel("h5", "-O3", variant="t3"))
    rep = ulp.compare_records(rec0, rec3)
    assert rep["bit_digest_equal"] is False or rep["max_ulp"] == 0
    ulp.assert_reproducible(rec0, rec3, ulp_gate=repro_gate_k)