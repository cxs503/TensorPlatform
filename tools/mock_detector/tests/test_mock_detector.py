"""pytest suite for the structural MOCK detector.

All fixtures are synthetic (``x_in``/``k_gain``/``y_out``) — no domain vocabulary
— because the whole claim of the module is that it reaches its verdicts without
domain knowledge.  The suite asserts both directions: degenerate behaviours must
be caught, and an honest endpoint must **not** be flagged.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tools.mock_detector import autospec, classify, ledger_link, probes, report as report_mod
from tools.mock_detector.canonical import (compare_vectors, flatten_numeric,
                                          numeric_sha256, shape_of_series, vector_span)
from tools.mock_detector.fixtures import EXPECTED, make_server, run_selfcheck
from tools.mock_detector.httpc import JsonClient
from tools.mock_detector.model import LEVELS, LEVEL_RANK, ProbeReport, Target
from tools.mock_detector.probes import ProbeConfig, run_probes


# --------------------------------------------------------------------------
# canonical layer
# --------------------------------------------------------------------------
def test_flatten_ignores_booleans_and_nonfinite():
    vec = flatten_numeric({"a": 1, "b": True, "c": {"d": 2.5}, "e": [1.0, 2.0],
                           "f": float("nan"), "g": "text"})
    assert vec == {"a": 1.0, "c.d": 2.5, "e[0]": 1.0, "e[1]": 2.0}


def test_numeric_hash_is_order_and_precision_stable():
    a = {"x.out": 1.0000000000001, "y.out": 2.0}
    b = {"y.out": 2.0, "x.out": 1.0}            # same to 12 significant digits
    assert numeric_sha256(a) == numeric_sha256(b)
    assert numeric_sha256(a) != numeric_sha256({"x.out": 1.1, "y.out": 2.0})


def test_compare_vectors_empty_comparison_is_not_a_pass():
    worst, path, pair, n = compare_vectors({"a": 1.0}, {"b": 1.0})
    assert n == 0 and path is None and worst == 0.0


@pytest.mark.parametrize("series,expected", [
    ([1.0, 1.0, 1.0], "constant"),
    ([1.0, 2.0, 3.0], "monotone"),
    ([3.0, 2.0, 1.0], "monotone"),
    ([1.0, 3.0, 2.0], "non_monotone"),
    ([1.0], "undefined"),
])
def test_shape_of_series(series, expected):
    assert shape_of_series(series) == expected


def test_vector_span_is_relative():
    assert vector_span([100.0, 101.0]) == pytest.approx(1.0 / 101.0, rel=1e-9)
    assert vector_span([]) == 0.0


# --------------------------------------------------------------------------
# probes against the synthetic server
# --------------------------------------------------------------------------
@pytest.fixture()
def synthetic_server():
    server = make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _target(path: str) -> Target:
    return Target(cap_id=path.rsplit("/", 1)[-1], method="POST", path=path,
                  base_payload={"x_in": 10.0, "k_gain": 1.0},
                  sweep_fields=["x_in", "k_gain"], timeout_s=5.0)


def test_determinism_probe_detects_randomness(synthetic_server):
    client = JsonClient(synthetic_server)
    cfg = ProbeConfig()
    rand = classify.classify(run_probes(client, _target("/api/v1/synthetic/random"), cfg))
    assert rand.det_ok is False
    assert rand.level == "MOCK"
    assert "NONDETERMINISTIC" in rand.reason_codes

    lin = classify.classify(run_probes(client, _target("/api/v1/synthetic/linear"), cfg))
    assert lin.det_ok is True and lin.det_worst_rel == pytest.approx(0.0)


def test_idempotency_probe_detects_stateful_counter(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/counter"),
                                       ProbeConfig()))
    assert rep.idem_ok is False
    assert rep.level == "MOCK"
    assert "NON_IDEMPOTENT" in rep.reason_codes


def test_isolation_probe_detects_order_dependence(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/order_state"),
                                       ProbeConfig()))
    assert rep.idem_ok is True                 # immediate repeats are stable ...
    assert rep.idem_isolation_ok is False      # ... but A→B→A drifts
    assert "ORDER_DEPENDENT_STATE" in rep.reason_codes


def test_monotonicity_probe_detects_hardcoded_output(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/hardcoded"),
                                       ProbeConfig()))
    assert rep.axes_probed >= 2
    assert rep.axes_respond == 0
    assert rep.axes_invariant == rep.axes_probed
    assert rep.level == "MOCK" and "INPUT_INVARIANT" in rep.reason_codes


def test_monotonicity_probe_keeps_honest_endpoint_real(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/linear"),
                                       ProbeConfig()))
    assert rep.axes_respond >= 1
    assert rep.level == "REAL"
    assert rep.level_scope == "STRUCTURAL"
    assert rep.accuracy_verified is False      # never claims accuracy


def test_metadata_only_endpoint_is_stub(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/metadata"),
                                       ProbeConfig()))
    assert rep.level == "STUB" and "NO_NUMERIC_OUTPUT" in rep.reason_codes


def test_missing_and_broken_routes(synthetic_server):
    client = JsonClient(synthetic_server)
    absent = classify.classify(run_probes(client, _target("/api/v1/synthetic/missing"),
                                          ProbeConfig()))
    assert absent.level == "NOT_SUPPORTED"
    broken = classify.classify(run_probes(client, _target("/api/v1/synthetic/boom"),
                                          ProbeConfig()))
    assert broken.level == "WITHHELD"


def test_unreachable_endpoint_is_withheld_not_pass():
    client = JsonClient("http://127.0.0.1:9", default_timeout=1.0)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/linear"),
                                       ProbeConfig()))
    assert rep.level == "WITHHELD"
    assert "UNREACHABLE" in rep.flags


def test_quantized_lookup_is_caught(synthetic_server):
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/quantized"),
                                       ProbeConfig()))
    assert rep.level == "MOCK"
    assert ("QUANTIZED_OUTPUT" in rep.reason_codes
            or "INPUT_INVARIANT" in rep.reason_codes)


# --------------------------------------------------------------------------
# fixture self-check: the non-vacuity criterion C1
# --------------------------------------------------------------------------
def test_global_perturbation_prevents_false_hardcoded_verdict(synthetic_server):
    """A driver outside the swept set must yield WITHHELD, not a MOCK accusation."""
    client = JsonClient(synthetic_server)
    rep = classify.classify(run_probes(client, _target("/api/v1/synthetic/global_only"),
                                       ProbeConfig()))
    assert rep.axes_respond == 0
    assert rep.global_scale_ok is False
    assert rep.level == "WITHHELD" and rep.fired_rule == "GLOBAL_RESPONSE_ONLY"


def test_determinism_may_be_inferred_from_repeats():
    rep = ProbeReport(cap_id="x", method="POST", path="/x", status=200, http_ok=True,
                      det_ok=None, idem_ok=True, idem_isolation_ok=True,
                      n_samples=10, n_distinct_vectors=10, axes_probed=2, axes_respond=2)
    classify.classify(rep)
    assert rep.level == "REAL"
    assert "DETERMINISM_INFERRED_FROM_REPEAT" in rep.flags


def test_uniform_sweep_rescues_closure_constrained_endpoint(synthetic_server):
    """When every single-axis sweep breaks a closure constraint, a common-scale sweep
    must still be able to prove the endpoint computes (flag UNIFORM_SWEEP_FALLBACK)."""
    client = JsonClient(synthetic_server)
    target = Target(cap_id="closure", method="POST", path="/api/v1/synthetic/closure",
                    base_payload={"x_in": 10.0, "k_gain": 1.0, "y_out_in": 20.0},
                    sweep_fields=["x_in", "k_gain", "y_out_in"], timeout_s=5.0)
    rep = classify.classify(run_probes(client, target, ProbeConfig()))
    assert "UNIFORM_SWEEP_FALLBACK" in rep.flags
    assert any(a.get("field") == "*uniform*" and a.get("status") == "ok" for a in rep.axes)
    assert rep.axes_respond >= 1
    assert rep.level == "REAL"


def test_fixture_selftest_all_expected(tmp_path):
    out = run_selfcheck(str(tmp_path / "fixture_selftest.json"))
    assert out["n_fixtures"] == len(EXPECTED) >= 8
    assert out["n_correct"] == out["n_fixtures"], out["mismatched"]
    assert out["false_negatives"] == 0
    assert out["false_positive_list"] == []


# --------------------------------------------------------------------------
# rule engine invariants
# --------------------------------------------------------------------------
def test_level_ladder_order():
    assert LEVEL_RANK["REAL"] > LEVEL_RANK["MOCK"] > LEVEL_RANK["STUB"]
    assert LEVEL_RANK["REAL"] > LEVEL_RANK["WITHHELD"] > LEVEL_RANK["NOT_SUPPORTED"]


def test_every_rule_maps_to_a_declared_level():
    for rule in classify.RULE_TABLE:
        assert rule["level"] in LEVELS
        assert rule["code"] in classify.PREDICATES


def test_classify_is_pure_function_of_recorded_numbers():
    rep = ProbeReport(cap_id="x", method="POST", path="/x", status=200, http_ok=True,
                      det_ok=True, idem_ok=True, idem_isolation_ok=True,
                      n_samples=10, n_distinct_vectors=9, axes_probed=2, axes_respond=2)
    assert classify.classify(rep).level == "REAL"
    rep2 = ProbeReport(cap_id="x", method="POST", path="/x", status=200, http_ok=True,
                       det_ok=True, idem_ok=True, idem_isolation_ok=True,
                       n_samples=10, n_distinct_vectors=1, axes_probed=2, axes_respond=0)
    assert classify.classify(rep2).level == "MOCK"


# --------------------------------------------------------------------------
# autospec: schema-only payload synthesis
# --------------------------------------------------------------------------
def _spec(path: str, props: dict, required=None) -> dict:
    return {"openapi": "3.0.0",
            "paths": {path: {"post": {"summary": "synthetic",
                                      "requestBody": {"content": {"application/json": {
                                          "schema": {"type": "object",
                                                     "properties": props,
                                                     "required": required or []}}}}}}}}


def test_autospec_prefers_declared_defaults_and_ranges():
    spec = _spec("/api/v1/synthetic/alpha", {
        "x_in": {"type": "number", "default": 7.5},
        "k_gain": {"type": "number", "minimum": 1.0, "maximum": 3.0},
        "n_step": {"type": "integer", "minimum": 2},
        "flag": {"type": "boolean", "default": False},
        "label": {"type": "string", "default": "probe"},
    }, required=["x_in"])
    targets = autospec.build_targets(spec)
    assert len(targets) == 1
    t = targets[0]
    assert t.base_payload["x_in"] == 7.5
    assert 1.0 <= t.base_payload["k_gain"] <= 3.0
    assert isinstance(t.base_payload["n_step"], int) and t.base_payload["n_step"] >= 2
    assert set(t.sweep_fields) == {"x_in", "k_gain", "n_step"}


def test_autospec_is_deterministic_and_skips_nested_required():
    spec = _spec("/api/v1/synthetic/beta", {"x_in": {"type": "number"}},
                 required=["x_in"])
    a = autospec.build_targets(spec)[0].base_payload
    b = autospec.build_targets(spec)[0].base_payload
    assert a == b
    nested = _spec("/api/v1/synthetic/gamma", {"obj": {"type": "object",
                                                       "properties": {"z": {"type": "number"}}}},
                   required=["obj"])
    assert autospec.build_targets(nested) == []


def test_autospec_excludes_asynchronous_job_paths():
    spec = _spec("/api/v1/synthetic/simulate", {"x_in": {"type": "number", "default": 1.0}})
    spec["paths"]["/api/v1/synthetic/plain"] = spec["paths"]["/api/v1/synthetic/simulate"]
    paths = [t.path for t in autospec.build_targets(spec)]
    assert paths == ["/api/v1/synthetic/plain"]


def test_alternate_payload_changes_numbers_only():
    t = Target(cap_id="a", method="POST", path="/x",
               base_payload={"x_in": 5.0, "label": "keep"})
    alt = autospec.alternate_payload(t, 1)
    assert alt is not None and alt["label"] == "keep"
    assert alt["x_in"] != t.base_payload["x_in"]


# --------------------------------------------------------------------------
# ledger linkage
# --------------------------------------------------------------------------
def _probe(cap_id: str, path: str, level: str) -> ProbeReport:
    rep = ProbeReport(cap_id=cap_id, method="POST", path=path)
    rep.level = level
    return rep


def test_overlay_flags_overclaims_and_keeps_ledger_untouched():
    probes = [_probe("calc.alpha", "/api/v1/alpha/calc", "MOCK"),
              _probe("calc.beta", "/api/v1/beta/calc", "REAL")]
    ledger = {"status": "OK", "path": "ledger.json", "sha256": "deadbeef",
              "payload": {"capabilities": [
                  {"id": "calc.alpha", "claimed_level": "REAL", "domain": "d1"},
                  {"id": "calc.beta", "claimed_level": "REAL", "domain": "d1"},
                  {"id": "calc.gamma", "claimed_level": "REAL", "domain": "d1"}]}}
    overlay = ledger_link.build_overlay(probes, ledger, {})
    by_id = {e["cap_id"]: e for e in overlay["entries"]}
    assert by_id["calc.alpha"]["flag"] == "MOCK_OVERCLAIM"
    assert by_id["calc.beta"]["flag"] == "AGREE"
    assert by_id["calc.gamma"]["flag"] in ("UNMAPPED", "AGREE")
    assert overlay["mocked_capability_ids"] == ["calc.alpha"]
    assert overlay["ledger"]["mutated"] is False


def test_apply_overlay_writes_derived_copy(tmp_path):
    probes = [_probe("calc.alpha", "/api/v1/alpha/calc", "MOCK")]
    ledger = {"status": "OK", "path": "l.json", "sha256": "cafe",
              "payload": {"capabilities": [{"id": "calc.alpha", "claimed_level": "REAL"}]}}
    overlay = ledger_link.build_overlay(probes, ledger, {})
    out = tmp_path / "derived.json"
    res = ledger_link.apply_overlay(overlay, ledger, str(out))
    derived = json.loads(out.read_text())
    assert res["n_mock_flags"] == 1
    assert derived["derived_from_sha256"] == "cafe"
    assert derived["capabilities"][0]["mock_flag"] == "MOCK_OVERCLAIM"
    assert derived["capabilities"][0]["reconciled_level_proposed"] == "MOCK"


def test_token_match_finds_endpoint_for_ledger_id():
    # synthetic ids only: the detector must match on tokens, not on semantics
    assert ledger_link.match_score("calc.sigma", "/api/v1/figures/sigma") > 0.4
    assert ledger_link.match_score("calc.sigma", "/api/v1/other/thing") == 0.0


# --------------------------------------------------------------------------
# report layer
# --------------------------------------------------------------------------
def test_domain_leak_scan_is_clean_for_the_module():
    root = Path(__file__).resolve().parents[3]
    leak = report_mod.domain_leak_scan(root)
    assert leak["n_hits"] == 0, leak["hits"]


def test_report_and_criteria_generation(tmp_path):
    scan = {"generated_at": "2026-01-01T00:00:00+00:00", "base_url": "http://x",
            "code_commit": "abc", "limits": ["limit"],
            "probes": [
                {"cap_id": "a", "path": "/a", "method": "POST", "level": "REAL",
                 "axes_probed": 2, "axes_respond": 2, "n_samples": 10,
                 "n_distinct_vectors": 10, "reason_codes": ["STRUCTURAL_PASS"],
                 "flags": [], "detail": "", "axes": []},
                {"cap_id": "b", "path": "/b", "method": "POST", "level": "MOCK",
                 "axes_probed": 2, "axes_respond": 0, "n_samples": 10,
                 "n_distinct_vectors": 1, "reason_codes": ["INPUT_INVARIANT"],
                 "flags": [], "detail": "",
                 "axes": [{"field": "x_in", "status": "ok",
                           "accepted_values": [1.0, 2.0, 3.0],
                           "series_sample": {"y_out": [5.0, 5.0, 5.0]},
                           "shape_counts": {"constant": 1}, "max_jump_ratio": 1.0}]},
            ],
            "summary": {"n_targets": 2, "n_decided": 2, "decision_rate": 1.0,
                        "level_counts": {"REAL": 1, "MOCK": 1, "STUB": 0,
                                         "WITHHELD": 0, "NOT_SUPPORTED": 0},
                        "reason_counts": {"INPUT_INVARIANT": 1},
                        "flag_counts": {}, "mock_ids": ["b"], "stub_ids": [],
                        "withheld_ids": [], "not_supported_ids": [], "real_ids": ["a"],
                        "rule_table": classify.rule_table_json()}}
    overlay = {"ledger": {"path": "l", "sha256": "x" * 64, "status": "OK",
                          "n_capabilities": 1, "mutated": False},
               "flag_counts": {"MOCK_OVERCLAIM": 1}, "mocked_capability_ids": ["b"],
               "entries": [{"cap_id": "b", "domain": "d", "claimed_level": "REAL",
                            "observed_level": "MOCK", "flag": "MOCK_OVERCLAIM",
                            "action": "demote"}]}
    figs = report_mod.make_figures(scan, overlay, tmp_path / "figures")
    assert len(figs) >= 3
    for fig in figs:
        assert Path(fig["path"]).is_file() and Path(fig["path"]).stat().st_size > 1000
    criteria = report_mod.build_criteria(
        scan, overlay,
        {"n_fixtures": 9, "n_correct": 9, "false_negatives": 0, "false_positives": 0},
        {"n_compared": 4, "n_agree": 4, "agreement": 1.0, "disagreements": []},
        {"n_hits": 0, "hits": []}, {"scan_json": "s", "overlay_json": "o",
                                    "repeat_json": "r", "fixture_json": "f",
                                    "leak_json": "l"})
    assert criteria["n_criteria"] == 6 and criteria["all_pass"]
    md = report_mod.render_markdown(scan, overlay, criteria, figs)
    assert "## 3 计算结果" in md and "误差判定" in md
    assert "MOCK_OVERCLAIM" in md


# --------------------------------------------------------------------------
# scanner plumbing
# --------------------------------------------------------------------------
def test_scanner_end_to_end_on_synthetic_server(synthetic_server, tmp_path):
    from tools.mock_detector.scanner import scan_targets
    client = JsonClient(synthetic_server)
    targets = [_target(p) for p in list(EXPECTED)[:6]]
    scan = scan_targets(client, targets, concurrency=2, repo_root=str(tmp_path))
    assert scan.summary["n_targets"] == 6
    for probe in scan.probes:
        assert probe.level in LEVELS
    d = scan.to_dict()
    assert json.loads(json.dumps(d))["schema"] == "mock-detector-scan/1.0"