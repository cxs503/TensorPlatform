"""清偿判决 + 成熟化门 C1..C7（使用小型内联夹具，不依赖落库账本）。"""
import copy

from tools.repro_settlement.canonical import sha256_obj
from tools.repro_settlement.settle import GATES, settle, settle_case

RUNTIME = {"implementation": "CPython", "python": "3.11.8", "system": "linux",
           "machine": "x86_64", "packages": {"numpy": "1.24.0"}}


def case_pass(cid="p"):
    return {"case_id": cid, "status": "pass"}


def case_blocked(cid="b"):
    return {"case_id": cid, "status": "blocked", "fail_source": "dependency_missing",
            "blocked": {"reason": "dependency_missing", "pins": ["numpy"],
                        "solver_commit": "abc123"}}

def case_drift(cid="d", label="a", delta=0.01, tol=0.05, rebaselined=False):
    ev = {"rng": {"recorded_seed": 1, "observed_seed": 1, "rerun_spread": 0.0},
          "baseline": {"recorded_sha256": "aa", "observed_sha256": "aa"}}
    if label == "a":
        ev["resolution"] = {"recorded": {"n": 10}, "observed": {"n": 40}}
    elif label == "b":
        ev["solver"] = {"recorded": {"v": 1}, "observed": {"v": 2}}
    elif label == "c":
        ev["rng"] = {"recorded_seed": None, "observed_seed": 3, "rerun_spread": 1e-3}
    elif label == "d":
        ev["baseline"] = {"recorded_sha256": "aa", "observed_sha256": "bb"}
    return {"case_id": cid, "status": "drift", "fail_source": "numeric_drift",
            "drift": {"evidence": ev, "delta_rel": delta, "tolerance": tol,
                      "rebaselined": rebaselined}}


def corpus(cases, ci=None, sample=None):
    return {"corpus_id": "t", "cases": cases,
            "ci": ci or {"pytest_count": 30,
                         "architectures": {"x86_64": "pass", "loongarch64": "pass"}},
            "audit_sample": sample or []}


def test_pass_case_verdict_is_pass():
    assert settle_case(case_pass(), runtime=RUNTIME)["verdict"] == "pass"


def test_blocked_case_settles_with_lock():
    out = settle_case(case_blocked(), runtime=RUNTIME)
    assert out["verdict"] == "blocked_settled" and len(out["lock_sha256"]) == 64


def test_blocked_case_without_commit_stays_blocked():
    c = case_blocked()
    del c["blocked"]["solver_commit"]
    out = settle_case(c, runtime=RUNTIME)
    assert out["verdict"] == "blocked"
    assert "solver_commit_missing" in out["lock_completeness"]


def test_drift_case_gets_label_and_rebaseline_within_tolerance():
    out = settle_case(case_drift(delta=0.01, tol=0.05), runtime=RUNTIME)
    assert out["label"] == "a_resolution" and out["verdict"] == "drift_rebaselined"


def test_drift_beyond_tolerance_stays_open():
    out = settle_case(case_drift(delta=0.09, tol=0.05), runtime=RUNTIME)
    assert out["verdict"] == "drift_open"


def test_drift_without_evidence_is_unattributed():
    c = case_drift()
    c["drift"]["evidence"] = {}
    out = settle_case(c, runtime=RUNTIME)
    assert out["verdict"] == "drift_unattributed" and out["label"] == "unattributed"


def test_repro_rate_counts_settled_blocked_as_pass():
    rep = settle(corpus([case_pass(), case_blocked()]), runtime=RUNTIME)
    assert rep["repro_rate_before"] == 0.5 and rep["repro_rate_after"] == 1.0


def test_gates_tuple_is_complete():
    assert GATES == ("C1", "C2", "C3", "C4", "C5", "C6", "C7")


def test_c1_fails_below_target_line():
    rep = settle(corpus([case_pass(), case_drift(delta=0.9, tol=0.05)]), runtime=RUNTIME)
    assert rep["gates"]["C1"]["ok"] is False and rep["repro_rate_after"] < 0.95


def test_c2_fails_when_a_blocked_case_lacks_commit():
    c = case_blocked()
    del c["blocked"]["solver_commit"]
    rep = settle(corpus([case_blocked("b1"), c]), runtime=RUNTIME)
    assert rep["gates"]["C2"]["ok"] is False


def test_c2_fails_with_no_blocked_cases_at_all():
    rep = settle(corpus([case_pass()]), runtime=RUNTIME)
    assert rep["gates"]["C2"]["ok"] is False


def test_c3_reports_label_distribution():
    rep = settle(corpus([case_drift("d1", "a"), case_drift("d2", "d")]), runtime=RUNTIME)
    assert rep["gates"]["C3"]["ok"] is True
    assert rep["gates"]["C3"]["distribution"]["a_resolution"] == 1


def test_c4_requires_min_tests_and_both_arches():
    rep = settle(corpus([case_pass()],
                        ci={"pytest_count": 3,
                            "architectures": {"x86_64": "pass", "loongarch64": "absent"}}),
                 runtime=RUNTIME)
    assert rep["gates"]["C4"]["ok"] is False
    assert rep["gates"]["C4"]["arches"]["missing_or_failing"] == ["loongarch64"]


def test_c5_agreement_rate_and_mismatch_listing():
    sample = [{"case_id": "d1", "human_label": "a_resolution"},
              {"case_id": "d2", "human_label": "c_seed"}]
    rep = settle(corpus([case_drift("d1", "a"), case_drift("d2", "d")], sample=sample),
                 runtime=RUNTIME)
    assert rep["gates"]["C5"]["agreement"] == 0.5
    assert rep["gates"]["C5"]["mismatches"] == ["d2"]


def test_c6_detects_second_run_digest_match():
    c = corpus([case_pass(), case_blocked(), case_drift()])
    first = settle(c, runtime=RUNTIME)
    second = settle(c, runtime=RUNTIME, previous_digest=first["digest"])
    assert second["gates"]["C6"]["ok"] is True and second["digest"] == first["digest"]


def test_c6_fails_on_different_digest():
    first = settle(corpus([case_pass()]), runtime=RUNTIME)
    second = settle(corpus([case_pass(), case_pass("p2")]), runtime=RUNTIME,
                    previous_digest=first["digest"])
    assert second["gates"]["C6"]["ok"] is False


def test_c7_flags_open_case_without_fail_source():
    c = case_drift(delta=0.9, tol=0.05)
    del c["fail_source"]
    rep = settle(corpus([c]), runtime=RUNTIME)
    assert rep["gates"]["C7"]["ok"] is False
    assert rep["gates"]["C7"]["unsourced"] == ["d"]


def test_settle_is_pure_no_input_mutation():
    c = corpus([case_pass(), case_blocked(), case_drift()])
    snapshot = copy.deepcopy(c)
    settle(c, runtime=RUNTIME)
    assert sha256_obj(c) == sha256_obj(snapshot)


def test_all_gates_pass_on_matured_fixture():
    cases = [case_pass(f"p{i}") for i in range(4)] + [case_blocked("b1")] + \
            [case_drift("d1", "a", 0.01, 0.05), case_drift("d2", "b", 0.01, 0.05)]
    sample = [{"case_id": "d1", "human_label": "a_resolution"}]
    rep = settle(corpus(cases, sample=sample), runtime=RUNTIME)
    assert rep["gates"]["C1"]["ok"] and rep["gates"]["C2"]["ok"] and rep["gates"]["C3"]["ok"]
    assert rep["gates"]["C4"]["ok"] and rep["gates"]["C5"]["ok"] and rep["gates"]["C7"]["ok"]


def test_richardson_correction_settles_a_resolution_drift():
    c = case_drift("d1", "a", delta=0.06, tol=0.05)
    c["drift"]["correction"] = {"method": "richardson", "ratio": 2.0, "order": 2.0}
    out = settle_case(c, runtime=RUNTIME)
    assert out["verdict"] == "drift_rebaselined" and out["residual_rel"] < 0.05


def test_baseline_version_drift_settles_by_rebaseline():
    c = case_drift("d1", "d", delta=0.09, tol=0.05, rebaselined=True)
    c["drift"]["baseline_supersession"] = {"superseded": "aa", "superseded_by": "bb"}
    out = settle_case(c, runtime=RUNTIME)
    assert out["verdict"] == "drift_rebaselined"


def test_correction_without_declared_label_still_open():
    c = case_drift("d1", "a", delta=0.30, tol=0.05)
    c["drift"]["correction"] = {"method": "richardson", "ratio": 2.0, "order": 2.0}
    assert settle_case(c, runtime=RUNTIME)["verdict"] == "drift_open"
