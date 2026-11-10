"""pytest surface for the cross-architecture reproduction gate.

Each test pins one of the six machine-checkable criteria (or one of the invariants
the criteria rely on), so a regression in the judge turns into a red test rather
than a quietly worse verdict:

``c1``  integer fields are bit-exact across every arm
``c2``  double-precision fields stay inside the relative band
``c3``  an injected out-of-band difference is always flagged, nothing else is
``c4``  a one-sided NaN is always flagged
``c5``  the whole pipeline finishes inside the latency budget
``c6``  the gate package contains zero domain vocabulary
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.cross_arch_gate import (audit, classify, fixtures, gate, numerics,
                                   pipeline, record as record_mod, selftest)


@pytest.fixture(scope="module")
def policy():
    return gate.load_policy()


@pytest.fixture(scope="module")
def ref_record():
    return record_mod.build_record("ref-native", arith="native", sum_variant="naive", libm="stdlib")


@pytest.fixture(scope="module")
def arms(ref_record):
    return {
        "rerun": record_mod.build_record("rerun", arith="native", sum_variant="naive", libm="stdlib"),
        "oracle": record_mod.build_record("oracle", arith="exact", sum_variant="fsum", libm="stdlib"),
        "fma": record_mod.build_record("fma", arith="fma", sum_variant="naive", libm="stdlib"),
        "assoc": record_mod.build_record("assoc", arith="native", sum_variant="reverse", libm="stdlib"),
        "libm": record_mod.build_record("libm-alt", arith="native", sum_variant="naive", libm="alt"),
    }


# --------------------------------------------------------------------------- #
# numerics
# --------------------------------------------------------------------------- #
def test_ulp_distance_matches_repo_protocol():
    from tools.repro_digest.ulp import ulp_distance as legacy_ulp
    samples = [0.0, -0.0, 1.0, 1.0000000000000002, 1e-300, -3.25, 4.5e17]
    for a in samples:
        for b in samples:
            assert numerics.ulp_distance(a, b) == legacy_ulp(a, b)
    assert numerics.ulp_distance(float("nan"), 1.0) == numerics.ULP_UNBOUNDED
    assert numerics.ulp_distance(1.0, 1.0) == 0


def test_portable_oracle_is_correctly_rounded():
    from fractions import Fraction
    # exact ties and the subnormal / overflow boundaries
    assert numerics.to_binary64(Fraction(1, 3)) == 1.0 / 3.0
    assert numerics.to_binary64(Fraction(2 ** 53 + 1)) == float(2 ** 53)
    assert numerics.to_binary64(Fraction(2 ** 53 + 3)) == float(2 ** 53 + 4)
    assert numerics.to_binary64(Fraction(1, 2 ** 1075)) == 0.0          # below half a subnormal
    assert numerics.to_binary64(Fraction(1, 2 ** 1076)) == 0.0          # quarter subnormal
    assert numerics.to_binary64(Fraction(3, 2 ** 1075)) == 1e-323       # 1.5 ulp -> even
    assert numerics.to_binary64(Fraction(5, 2 ** 1076)) == 5e-324       # 1.25 ulp -> down
    assert numerics.to_binary64(Fraction(2 ** 1024)) == math.inf
    assert numerics.exact_sqrt(2.0) == math.sqrt(2.0)
    assert numerics.exact_sqrt(9.0) == 3.0
    assert numerics.exact_fma(1.0 + 2 ** -52, 1.0 - 2 ** -52, -(1.0)) == -(2.0 ** -104)


def test_exact_palette_equals_hardware_for_single_rounding_ops():
    """``+ - * / sqrt`` must be bit-identical to the exact oracle on any conforming CPU.

    These four operations are specified to be correctly rounded, so a difference
    here would mean the hardware (or the compiler's code generation) violates
    IEEE-754 — that is precisely the class of defect the gate exists to catch.
    ``fma`` is deliberately excluded: it is a *different* operation (one rounding
    instead of two), which is why contraction must show up as a within-band
    difference rather than as a bit-identity failure.
    """
    inputs = fixtures.make_inputs()
    for op in ("add", "mul", "div", "sqrt"):
        for ia in range(len(inputs)):
            for ib in range(len(inputs)):
                a, b = inputs[ia], inputs[ib]
                reference = fixtures._eval_op(op, a, b, b, "exact")
                hardware = fixtures._eval_op(op, a, b, b, "native")
                assert numerics.bits(reference) == numerics.bits(hardware), f"{op} {ia},{ib}"


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def _fp_row(value):
    return {"kind": fixtures.FP, "hex": numerics.hex_token(value),
            "bits": format(numerics.bits(value), "016x")}


def _int_row(value):
    return {"kind": fixtures.INT, "int": value}


def test_classify_labels(policy):
    one = _fp_row(1.0)
    assert classify.classify_field("f", one, _fp_row(1.0), policy)["class"] == classify.BIT_IDENTICAL
    near = classify.classify_field("f", one, _fp_row(1.0 + 1e-15), policy)
    assert near["class"] == classify.WITHIN_TOLERANCE and near["ok"]
    far = classify.classify_field("f", one, _fp_row(1.0 + 1e-6), policy)
    assert far["class"] == classify.OVER_TOLERANCE and not far["ok"]
    nan_same = classify.classify_field("f", _fp_row(float("nan")), _fp_row(float("nan")), policy)
    assert nan_same["class"] == classify.NAN and nan_same["ok"]
    nan_one = classify.classify_field("f", one, _fp_row(float("nan")), policy)
    assert nan_one["class"] == classify.NAN and not nan_one["ok"]
    assert nan_one["detail"] == "single_sided_nan"
    assert classify.classify_field("f", _int_row(7), _int_row(7), policy)["class"] == classify.BIT_IDENTICAL
    mismatch = classify.classify_field("f", _int_row(7), _int_row(8), policy)
    assert mismatch["class"] == classify.OVER_TOLERANCE and mismatch["detail"] == "int_mismatch"
    missing = classify.classify_field("f", one, None, policy)
    assert missing["class"] == classify.OVER_TOLERANCE and missing["detail"] == "missing_field"


def test_gate_is_pure_and_deterministic(ref_record, arms, policy):
    before = json.dumps(ref_record, sort_keys=True)
    first = gate.compare(ref_record, arms["oracle"], policy)
    second = gate.compare(ref_record, arms["oracle"], policy)
    assert json.dumps(ref_record, sort_keys=True) == before
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --------------------------------------------------------------------------- #
# c1 / c2 — the measured arms
# --------------------------------------------------------------------------- #
def test_c1_integer_fields_bit_exact(ref_record, arms, policy):
    for label, cand in arms.items():
        report = gate.compare(ref_record, cand, policy)
        assert report["int_bit_exact_rate"] == 1.0, label
        assert report["class_counts"][classify.NAN] == 0, label
        assert report["verdict"] == "PASS", (label, report["verdict_reasons"])


def test_c2_double_precision_inside_band(ref_record, arms, policy):
    for label, cand in arms.items():
        report = gate.compare(ref_record, cand, policy)
        assert report["fp_within_band_fraction"] >= 0.95, label
        assert report["max_rel_observable"] <= policy["fp64_rel_tol"], label


def test_rerun_digest_identity(ref_record, arms, policy):
    report = gate.compare(ref_record, arms["rerun"], policy)
    assert report["same_digest"] is True
    assert report["max_ulp_observable"] == 0


# --------------------------------------------------------------------------- #
# c3 / c4 — the judge under mutation
# --------------------------------------------------------------------------- #
def test_c3_c4_mutation_matrix(ref_record, arms, policy):
    result = selftest.run_matrix(ref_record, arms, policy)
    metrics = result["metrics"]
    assert metrics["over_tolerance_detection_rate"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["nan_detection_rate"] == 1.0
    assert metrics["injected_fields"] > 0 and metrics["benign_fields"] > 0


def test_relative_mutation_of_exact_zero_is_a_no_op(ref_record):
    """Documented trap: scaling 0.0 cannot change it, so zero fields need absolute shifts."""
    zero_names = [n for n, row in ref_record["fields"].items()
                  if row["kind"] == fixtures.FP and numerics.decode_hex(row["hex"]) == 0.0]
    assert zero_names, "fixture should contain at least one exact-zero field"
    mutated = selftest.mutate_scale(ref_record, 1.0 + 1e-9, zero_names)
    for name in zero_names:
        assert mutated["fields"][name]["hex"] == ref_record["fields"][name]["hex"]
    shifted = selftest.mutate_shift(ref_record, 1e-3, zero_names)
    assert all(shifted["fields"][n]["hex"] != ref_record["fields"][n]["hex"] for n in zero_names)


def test_over_tolerance_verdict_fails(ref_record, policy):
    names = [n for n, row in ref_record["fields"].items()
             if row["kind"] == fixtures.FP and numerics.decode_hex(row["hex"]) != 0.0][:2]
    report = gate.compare(ref_record, selftest.mutate_scale(ref_record, 1.0 + 1e-6, names), policy)
    assert report["verdict"] == "FAIL"
    assert set(report["over_tolerance_fields"]) == set(names)


def test_missing_field_fails(ref_record, policy):
    name = sorted(ref_record["fields"])[0]
    report = gate.compare(ref_record, selftest.mutate_drop(ref_record, [name]), policy)
    assert report["verdict"] == "FAIL"
    assert any("structural" in reason for reason in report["verdict_reasons"])


# --------------------------------------------------------------------------- #
# c5 / c6 — pipeline budget and domain neutrality
# --------------------------------------------------------------------------- #
def test_c5_c6_pipeline_budget_and_audit(tmp_path):
    result = pipeline.run(tmp_path / "run", make_figures=False)
    assert result["all_pass"] is True, json.loads(Path(result["criteria"]).read_text())
    payload = json.loads(Path(result["criteria"]).read_text(encoding="utf-8"))
    assert payload["criteria"]["c5_e2e_latency_s"]["value"] < 300.0
    assert payload["criteria"]["c6_domain_semantics_hits"]["value"] == 0
    assert set(payload["criteria"]) == {
        "c1_int_bit_exact_rate", "c2_fp_within_band_fraction",
        "c3_over_tolerance_detection_rate", "c3_false_positive_rate",
        "c4_nan_detection_rate", "c5_e2e_latency_s", "c6_domain_semantics_hits"}
    for item in payload["criteria"].values():
        assert {"value", "threshold", "comparator", "pass", "source"} <= set(item)


def test_c6_domain_audit_clean():
    report = audit.scan()
    assert report["pass"] is True, report["hits"]
    assert report["hit_count"] == 0


def test_record_digest_detects_tampering(ref_record, tmp_path):
    tampered = json.loads(json.dumps(ref_record))
    name = next(n for n, row in tampered["fields"].items() if row["kind"] == fixtures.INT)
    tampered["fields"][name]["int"] = int(tampered["fields"][name]["int"]) + 1
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        record_mod.load_record(path)


def test_record_from_fields_is_the_integration_point(ref_record, policy):
    """Any benchmark that emits named numbers can be gated without touching the gate."""
    fields = {"metric_a": (record_mod.FP, 1.5), "metric_b": (record_mod.INT, 42)}
    rec = record_mod.record_from_fields("external-bench", fields)
    assert rec["field_count"] == 2 and rec["digest"]
    assert record_mod.field_digest(rec["fields"]) == rec["digest"]
    same = record_mod.record_from_fields("external-bench", fields)
    report = gate.compare(rec, same, policy)
    assert report["verdict"] == "PASS" and report["class_counts"][classify.BIT_IDENTICAL] == 2
    moved = record_mod.record_from_fields("external-bench", {"metric_a": (record_mod.FP, 1.5 * (1 + 1e-6)),
                                                            "metric_b": (record_mod.INT, 43)})
    assert gate.compare(rec, moved, policy)["verdict"] == "FAIL"


def test_pipeline_bites_on_a_divergent_external_arm(tmp_path, ref_record, policy):
    """Negative control at pipeline level: a bad foreign arm must turn all_pass False."""
    fields = {n: (row["kind"], numerics.decode_hex(row["hex"]) if "hex" in row else row["int"])
              for n, row in ref_record["fields"].items()}
    broken = dict(fields)
    victim = next(n for n, (kind, value) in fields.items()
                  if kind == fixtures.FP and value != 0.0)   # 0.0 cannot be scaled (see trap test)
    broken[victim] = (fixtures.FP, fields[victim][1] * (1 + 1e-6))
    path = record_mod.save_record(record_mod.record_from_fields("foreign-arm", broken),
                                  tmp_path / "foreign.json")
    result = pipeline.run(tmp_path / "run", external_arms={"foreign-arm": path}, make_figures=False)
    assert result["all_pass"] is False
    verdicts = {label: json.loads(Path(p).read_text(encoding="utf-8"))["verdict"]
                for label, p in result["pairs"].items()}
    assert verdicts["foreign-arm"] == "FAIL"


def test_validate_record_rejects_broken_payloads(ref_record):
    assert record_mod.validate_record(ref_record) is None
    broken = json.loads(json.dumps(ref_record))
    name = next(n for n, row in broken["fields"].items() if row["kind"] == fixtures.INT)
    broken["fields"][name] = {"kind": fixtures.INT}
    assert "integer payload" in record_mod.validate_record(broken)
    name = next(n for n, row in broken["fields"].items() if "hex" in row)
    broken["fields"][name] = {"kind": fixtures.FP, "hex": "not-a-float"}
    assert "unparsable" in record_mod.validate_record(broken)


def test_criteria_assembly_is_artifact_driven(tmp_path):
    result = pipeline.run(tmp_path / "run", make_figures=False)
    payload = json.loads(Path(result["criteria"]).read_text(encoding="utf-8"))
    for item in payload["criteria"].values():
        assert item["source"], "every criterion must point at the artifact it came from"
    assert payload["arms"], "arm table must be populated"