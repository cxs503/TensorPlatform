"""Tests for the dispatch decision log, deterministic replay and shadow gate.

Fixtures are ``synthetic.*`` by contract: the platform must not learn domain
vocabulary from its tests.  Every numeric assertion below is either an exact
identity (bit patterns, hashes, schema invariants) or a comparison against a
ground truth the synthetic world computes analytically, never a hard-coded
"expected" number copied from a run.
"""

from __future__ import annotations

import json
import math
import os
import shutil

import pytest

from tools.dispatch_shadow import (
    DecisionLog,
    GateConfig,
    LogConfig,
    Policy,
    UnpinnedPolicyError,
    content_digest,
    evaluate_shadow_gate,
    estimate_from_records,
    inject_outcome_feature,
    make_outcome,
    make_record,
    perturbation_probe,
    replay_log,
    replay_record,
    scan_structural,
)
from tools.dispatch_shadow import fixtures, leakage, policy as policy_mod
from tools.dispatch_shadow.leakage import LeakageDetector, auc, augment_config_with_feature
from tools.dispatch_shadow.offpolicy import bootstrap_ci, ips_snips

SEED = "synthetic.world"
N_SMALL = 60


@pytest.fixture(scope="module")
def small_world(tmp_path_factory):
    root = tmp_path_factory.mktemp("small_log")
    scenario = fixtures.make_scenario(n_slates=N_SMALL, seed=SEED)
    log = fixtures.write_log(str(root / "log"), scenario, n_slates=N_SMALL)
    return scenario, log


@pytest.fixture(scope="module")
def big_world(tmp_path_factory):
    root = tmp_path_factory.mktemp("big_log")
    scenario = fixtures.make_scenario(n_slates=1200, seed=SEED)
    log = fixtures.write_log(str(root / "log"), scenario, n_slates=1200)
    return scenario, log


# ---------------------------------------------------------------------------
# schema v1 + content addressing
# ---------------------------------------------------------------------------


def test_config_rejects_outcome_field_as_feature():
    with pytest.raises(Exception):
        LogConfig(
            feature_names=("synthetic.feature.alpha", "synthetic.reward"),
            outcome_fields=("synthetic.reward",),
        )


def test_record_content_address_is_key_order_independent(small_world):
    scenario, _ = small_world
    cands = scenario.slates[0]["candidates"]
    decision = scenario.behaviour.decide(cands)
    kwargs = dict(
        config=scenario.config,
        log_id="synthetic.log",
        decision_seq=0,
        dispatch_ts=1.7e9,
        policy=scenario.behaviour.to_record_policy(),
        scores=decision["scores"],
        ranking=decision["ranking"],
        chosen=decision["chosen"],
        propensities=decision["propensities"],
        exploration_floor=0.1,
    )
    r1 = make_record(candidates=cands, **kwargs)
    shuffled = [dict(reversed(list(c.items()))) for c in cands]
    r2 = make_record(candidates=shuffled, **kwargs)
    assert r1["record_id"] == r2["record_id"]
    assert content_digest({k: r1[k] for k in r1 if k in
                           ("schema_version", "log_id", "decision_seq", "dispatch_ts",
                            "policy", "config_sha256", "slate", "decision", "writer")}) == r1["record_id"]


def test_record_id_changes_when_a_feature_changes(small_world):
    scenario, _ = small_world
    cands = [dict(c, features=dict(c["features"])) for c in scenario.slates[0]["candidates"]]
    decision = scenario.behaviour.decide(cands)
    common = dict(
        config=scenario.config, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
        policy=scenario.behaviour.to_record_policy(), scores=decision["scores"],
        ranking=decision["ranking"], chosen=decision["chosen"],
        propensities=decision["propensities"], exploration_floor=0.1,
    )
    a = make_record(candidates=cands, **common)
    cands[0]["features"]["synthetic.feature.alpha"] = 0.123456
    scores = dict(decision["scores"])
    scores[cands[0]["synthetic.candidate_id"]] = scenario.behaviour.score(cands[0]["features"])
    ranking = sorted(scores, key=lambda c: (-scores[c], c))
    props = policy_mod.softmax_propensities(
        [scores[c] for c in scores], scenario.behaviour.temperature, 0.1
    )
    b = make_record(
        candidates=cands, scores=scores, ranking=ranking,
        propensities={c: p for c, p in zip(scores, props)},
        **{k: v for k, v in common.items() if k not in ("scores", "ranking", "propensities")},
    )
    assert a["record_id"] != b["record_id"]


def test_record_rejects_unregistered_feature(small_world):
    scenario, _ = small_world
    cands = [dict(c, features=dict(c["features"])) for c in scenario.slates[0]["candidates"]]
    cands[0]["features"]["synthetic.feature.unknown"] = 0.5
    decision = scenario.behaviour.decide([{**c, "features": {k: v for k, v in c["features"].items()
                                                             if k in scenario.config.feature_names}}
                                          for c in cands])
    with pytest.raises(Exception):
        make_record(
            config=scenario.config, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
            policy=scenario.behaviour.to_record_policy(), candidates=cands,
            scores=decision["scores"], ranking=decision["ranking"],
            chosen=decision["chosen"], propensities=decision["propensities"],
            exploration_floor=0.1,
        )


def test_post_hoc_timestamp_is_rejected_as_leak(small_world):
    scenario, _ = small_world
    cands = [dict(c, features=dict(c["features"]), field_ts=dict(c["field_ts"]))
             for c in scenario.slates[0]["candidates"]]
    decision = scenario.behaviour.decide(cands)
    cands[0]["field_ts"]["synthetic.feature.alpha"] = 1.7e9 + 10.0  # refreshed after dispatch
    with pytest.raises(Exception) as excinfo:
        make_record(
            config=scenario.config, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
            policy=scenario.behaviour.to_record_policy(), candidates=cands,
            scores=decision["scores"], ranking=decision["ranking"],
            chosen=decision["chosen"], propensities=decision["propensities"],
            exploration_floor=0.1,
        )
    assert "dispatch_ts" in str(excinfo.value)


def test_propensity_positivity_and_normalisation_enforced(small_world):
    scenario, _ = small_world
    cands = scenario.slates[0]["candidates"]
    decision = scenario.behaviour.decide(cands)
    ids = list(decision["propensities"])
    bad = {cid: (0.0 if i == 0 else decision["propensities"][cid]) for i, cid in enumerate(ids)}
    with pytest.raises(Exception):
        make_record(
            config=scenario.config, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
            policy=scenario.behaviour.to_record_policy(), candidates=cands,
            scores=decision["scores"], ranking=decision["ranking"],
            chosen=decision["chosen"], propensities=bad, exploration_floor=0.1,
        )
    flat = {cid: 0.3 for cid in ids}  # sum != 1
    with pytest.raises(Exception):
        make_record(
            config=scenario.config, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
            policy=scenario.behaviour.to_record_policy(), candidates=cands,
            scores=decision["scores"], ranking=decision["ranking"],
            chosen=decision["chosen"], propensities=flat, exploration_floor=0.1,
        )


def test_schema_version_is_pinned(small_world):
    scenario, _ = small_world
    cfg = LogConfig(
        feature_names=scenario.config.feature_names,
        outcome_fields=scenario.config.outcome_fields,
        schema_version="dispatch-decision-log/0",
    )
    with pytest.raises(Exception):
        make_record(
            config=cfg, log_id="synthetic.log", decision_seq=0, dispatch_ts=1.7e9,
            policy=scenario.behaviour.to_record_policy(),
            candidates=scenario.slates[0]["candidates"],
            scores=scenario.behaviour.decide(scenario.slates[0]["candidates"])["scores"],
            ranking=scenario.behaviour.decide(scenario.slates[0]["candidates"])["ranking"],
            chosen=scenario.behaviour.decide(scenario.slates[0]["candidates"])["chosen"],
            propensities=scenario.behaviour.decide(scenario.slates[0]["candidates"])["propensities"],
            exploration_floor=0.1,
        )


# ---------------------------------------------------------------------------
# store: chain integrity, partitions, late outcomes
# ---------------------------------------------------------------------------


def test_chain_verifies_and_partitions_join(small_world):
    _, log = small_world
    report = log.verify()
    assert report.ok, report.problems[:3]
    assert report.records == N_SMALL
    assert report.outcomes == N_SMALL
    recs = log.records()
    assert len(recs) == N_SMALL
    assert all(r.labelled() for r in recs)
    ids = [r.record_id for r in recs]
    assert len(set(ids)) == len(ids)
    # slate order is recovered from decision_seq, not from file order
    assert [r.decision_seq for r in recs] == list(range(N_SMALL))


def test_tampered_record_body_is_detected(small_world, tmp_path):
    _, log = small_world
    root = shutil.copytree(log.root, tmp_path / "copy")
    path = os.path.join(root, "records", "part-0000.jsonl")
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    cid = rows[0]["slate"]["candidate_id_field"]
    rows[0]["decision"]["scores"][rows[0]["slate"]["candidates"][0][cid]] += 1.0
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    report = DecisionLog(str(root)).verify()
    assert not report.ok
    codes = {p["code"] for p in report.problems}
    assert "RECORD_INVALID" in codes or "CHAIN_DIGEST_MISMATCH" in codes


def test_deleted_record_is_detected_as_sequence_gap(small_world, tmp_path):
    _, log = small_world
    root = shutil.copytree(log.root, tmp_path / "copy2")
    path = os.path.join(root, "records", "part-0000.jsonl")
    lines = open(path, encoding="utf-8").read().splitlines()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines[1:]) + "\n")
    report = DecisionLog(str(root)).verify()
    assert not report.ok
    assert any(p["code"] == "SEQ_GAP" for p in report.problems)


def test_late_outcome_keeps_record_identity_and_is_addressable(small_world, tmp_path):
    scenario, log = small_world
    root = shutil.copytree(log.root, tmp_path / "copy3")
    copy = DecisionLog(str(root))
    recs = copy.records(with_outcomes=False)
    target = recs[-1].record_id
    before = copy.head_digest()
    copy.append_outcome(
        record_id=target,
        outcome={"synthetic.reward": 1.0, "synthetic.accepted": 1.0, "synthetic.cost": 1.0},
        outcome_ts=1.7e9 + 10 * N_SMALL + 7200,
        source="synthetic.labeler.late",
    )
    assert copy.verify().ok
    joined = {r.record_id: r for r in copy.records()}
    assert joined[target].outcome is not None
    assert joined[target].reward("synthetic.reward") == 1.0
    assert copy.head_digest() == before  # outcome partition does not touch the decision chain


def test_orphan_outcome_is_detected(small_world, tmp_path):
    scenario, log = small_world
    root = shutil.copytree(log.root, tmp_path / "copy4")
    copy = DecisionLog(str(root))
    bogus = "0" * 64
    row = make_outcome(
        outcome_index=999,
        record_id=bogus,
        outcome={"synthetic.reward": 1.0},
        outcome_ts=1.7e9,
        source="synthetic.labeler",
        prev_outcome_id=copy._outcome_rows()[-1]["outcome_id"],  # noqa: SLF001
        prev_outcome_chain=copy._outcome_rows()[-1]["chain_digest"],  # noqa: SLF001
        config=copy.config,
    )
    copy._append("outcomes", 999, row)  # noqa: SLF001 - bypasses index bookkeeping
    report = copy.verify()
    assert any(p["code"] == "ORPHAN_OUTCOME" for p in report.problems)


# ---------------------------------------------------------------------------
# replay engine
# ---------------------------------------------------------------------------


def test_replay_is_bitwise_identical_on_clean_log(big_world):
    scenario, log = big_world
    report = replay_log(log.records(), scenario.behaviour, config=scenario.config)
    assert report.ok, report.mismatches[:3]
    assert report.fidelity == 1.0
    assert report.mismatches == []
    # the realised action is a draw, so some rounds legitimately dispatch the
    # runner-up; that is recorded, not treated as a replay failure
    assert 0.0 < report.realised_off_top < report.replayable


def test_replay_detects_one_ulp_perturbation(big_world):
    scenario, log = big_world
    probe = perturbation_probe(log.records()[:200], scenario.behaviour, ulps=(1,), config=scenario.config)
    row = probe["rows"][0]
    assert row["records"] == 200
    assert row["detection_rate"] == 1.0


def test_replay_refuses_unpinned_policy_version(big_world):
    scenario, log = big_world
    outsider = Policy(
        policy_id="synthetic.policy.unregistered.v1",
        weights={n: 0.1 for n in scenario.config.feature_names},
        feature_names=scenario.config.feature_names,
    )
    report = replay_log(log.records(), outsider, config=scenario.config)
    assert not report.ok
    assert all(r["code"] == "POLICY_VERSION_MISMATCH" for r in report.refused)


def test_replay_refuses_tampered_policy_spec(big_world):
    scenario, log = big_world
    rec = log.records()[0]
    assert replay_record(rec, scenario.behaviour, config=scenario.config) == []
    tampered = json.loads(json.dumps(rec.data))
    tampered["policy"]["params"]["weights"]["synthetic.feature.alpha"] += 0.1
    from tools.dispatch_shadow.schema import DecisionRecord

    with pytest.raises(UnpinnedPolicyError):
        replay_record(DecisionRecord(data=tampered), scenario.behaviour, config=scenario.config)


def test_replay_refuses_changed_kernel(monkeypatch, big_world):
    scenario, log = big_world
    rec = log.records()[0]
    original = policy_mod._kernel_digest_cached()  # noqa: SLF001
    monkeypatch.setattr(policy_mod, "_kernel_digest_cached", lambda: "f" * 64)
    try:
        with pytest.raises(UnpinnedPolicyError):
            replay_record(rec, scenario.behaviour, config=scenario.config)
    finally:
        monkeypatch.undo()
    assert policy_mod.kernel_digest() == original


def test_feature_order_does_not_change_scores_but_changes_version(small_world):
    """``math.fsum`` makes the score order-invariant; the version hash still pins order."""
    scenario, _ = small_world
    feats = scenario.slates[0]["candidates"][0]["features"]
    w = {n: 0.3 for n in scenario.config.feature_names}
    a = Policy(policy_id="synthetic.policy.order.a", weights=w,
               feature_names=scenario.config.feature_names)
    b = Policy(policy_id="synthetic.policy.order.a", weights=w,
               feature_names=tuple(reversed(scenario.config.feature_names)))
    assert a.score(feats) == b.score(feats)
    assert a.sha256 != b.sha256


def test_linear_score_matches_exact_fsum():
    feats = {"synthetic.feature.alpha": 0.1, "synthetic.feature.beta": 0.7,
             "synthetic.feature.gamma": 0.3}
    names = tuple(feats)
    p = Policy(policy_id="synthetic.policy.known", weights={n: 0.4 for n in names},
               bias=0.25, feature_names=names)
    expected = math.fsum([0.25] + [0.4 * feats[n] for n in names])
    assert p.score(feats) == expected


def test_softmax_floor_guarantees_positivity():
    props = policy_mod.softmax_propensities([10.0, -10.0, 0.0], 0.1, 0.05)
    assert pytest.approx(sum(props), rel=0, abs=1e-12) == 1.0
    assert min(props) >= 0.05 / 3 - 1e-15


# ---------------------------------------------------------------------------
# off-policy estimation
# ---------------------------------------------------------------------------


def test_ips_equals_naive_when_target_is_behaviour(big_world):
    scenario, log = big_world
    est = estimate_from_records(
        log.records(), scenario.behaviour, reward_field="synthetic.reward", bootstrap=0,
        config=scenario.config,
    )
    assert est.n == len(log.records())
    assert est.snips == pytest.approx(est.naive, rel=0, abs=1e-12)
    assert est.ips == pytest.approx(est.naive, rel=0, abs=1e-12)
    assert est.ess_frac == pytest.approx(1.0, abs=1e-12)


def test_snips_is_scale_invariant_and_ess_formula():
    w = [1.0, 2.0, 0.5, 3.0]
    r = [0.0, 1.0, 1.0, 0.0]
    a = ips_snips(w, r)
    b = ips_snips(w, [10 * x for x in r])
    assert b.snips == pytest.approx(10 * a.snips, rel=1e-12)
    expected_ess = (sum(w) ** 2) / sum(x * x for x in w)
    assert a.ess == pytest.approx(expected_ess, rel=1e-12)
    assert a.ips == pytest.approx(sum(wi * ri for wi, ri in zip(w, r)) / len(w), rel=1e-12)


def test_ips_known_answer_hand_computed():
    # two rounds: weights 2 and 0.5, rewards 1 and 0 -> IPS = (2*1 + 0)/2 = 1
    est = ips_snips([2.0, 0.5], [1.0, 0.0])
    assert est.ips == pytest.approx(1.0, rel=0, abs=1e-15)
    assert est.snips == pytest.approx(2.0 / 2.5, rel=0, abs=1e-15)
    assert est.naive == pytest.approx(0.5, rel=0, abs=1e-15)


def test_bootstrap_is_seeded_and_replayable():
    w = [1.0, 1.5, 0.8, 2.0, 1.1, 0.9]
    r = [0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    a = bootstrap_ci(w, r, replicates=200, seed="synthetic.seed")
    b = bootstrap_ci(w, r, replicates=200, seed="synthetic.seed")
    c = bootstrap_ci(w, r, replicates=200, seed="synthetic.other")
    assert a == b
    assert a != c
    assert a["snips"][0] <= a["snips"][1]


def test_shadow_recovers_the_analytic_policy_value(big_world):
    scenario, log = big_world
    truth = scenario.policy_value(scenario.candidate)
    est = estimate_from_records(
        log.records(), scenario.candidate, reward_field="synthetic.reward", bootstrap=200,
        config=scenario.config,
    )
    rel_err = abs(est.snips - truth) / truth
    assert rel_err < 0.05, (est.snips, truth, rel_err)
    lo, hi = est.ci["snips"]
    assert lo <= truth <= hi  # the interval must cover the enumerated truth
    assert est.snips > est.naive  # the better policy is detected as better


# ---------------------------------------------------------------------------
# leakage: structural guards + statistical negative control
# ---------------------------------------------------------------------------


def test_clean_log_has_no_structural_alarms(big_world):
    scenario, log = big_world
    scan = scan_structural(log.records(), scenario.config)
    assert scan["ok"]
    assert scan["alarms"] == []


def test_outcome_field_as_feature_name_is_refused(small_world):
    """Registering an outcome field as a feature must fail at config time."""
    scenario, _ = small_world
    with pytest.raises(Exception):
        LogConfig(
            feature_names=tuple(list(scenario.config.feature_names) + ["synthetic.reward"]),
            outcome_fields=scenario.config.outcome_fields,
        )


def test_statistical_detector_alarms_on_smuggled_outcome_and_silent_on_noise(big_world):
    scenario, log = big_world
    labelled = [r for r in log.records() if r.labelled()]
    det = LeakageDetector(threshold=0.95, outcome_field="synthetic.reward")
    cfg = augment_config_with_feature(scenario.config, "synthetic.leak.probe")
    smuggled = inject_outcome_feature(
        labelled, outcome_field="synthetic.reward", feature_name="synthetic.leak.probe", leak_prob=1.0
    )
    noise = inject_outcome_feature(
        labelled, outcome_field="synthetic.reward", feature_name="synthetic.leak.probe", leak_prob=0.5
    )
    scan_s = det.scan(smuggled, list(cfg.feature_names))
    scan_n = det.scan(noise, list(cfg.feature_names))
    assert scan_s["per_feature_auc"]["synthetic.leak.probe"] == pytest.approx(1.0, abs=1e-12)
    assert len(scan_s["alarms"]) == 1
    assert scan_n["alarms"] == []
    assert scan_n["per_feature_auc"]["synthetic.leak.probe"] == pytest.approx(0.5, abs=0.15)


def test_auc_known_answers():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == pytest.approx(1.0)
    assert auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == pytest.approx(0.0)
    assert auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == pytest.approx(0.5)


def test_null_control_false_alarm_rate_is_low(big_world):
    scenario, log = big_world
    labelled = [r for r in log.records() if r.labelled()]
    res = leakage.null_replications(
        labelled[:400], scenario.config.feature_names,
        outcome_field="synthetic.reward", threshold=0.95, replications=40,
    )
    assert res["false_alarm_rate"] <= 0.05
    assert res["max_auc_quantiles"]["median"] < 0.95


# ---------------------------------------------------------------------------
# shadow gate
# ---------------------------------------------------------------------------


def test_gate_passes_when_the_log_supports_the_claim(big_world):
    scenario, log = big_world
    res = evaluate_shadow_gate(
        log=log, config=scenario.config, candidate=scenario.candidate,
        incumbent=scenario.behaviour, gate=GateConfig(bootstrap=300, null_replications=20),
    )
    crit = {c["id"]: c for c in res["criteria"]}
    assert crit["A"]["passed"] and crit["B"]["passed"] and crit["C"]["passed"]
    assert crit["F"]["passed"]
    assert res["replay_fidelity"] == 1.0
    assert res["gate_status"] in {"PASS", "FAIL"}
    if res["gate_status"] == "PASS":
        assert crit["D"]["passed"] and crit["E"]["passed"]
    else:  # an honest FAIL must say which criterion failed and by how much
        failed = [c for c in res["criteria"] if not c["passed"]]
        assert failed


def test_gate_withholds_without_labels(tmp_path):
    scenario = fixtures.make_scenario(n_slates=20, seed="synthetic.world")
    root = tmp_path / "unlabelled"
    log = DecisionLog(str(root), config=scenario.config)
    rec = scenario.behaviour.decide(scenario.slates[0]["candidates"])
    log.append_decision(
        dispatch_ts=1.7e9, policy=scenario.behaviour.to_record_policy(),
        candidates=scenario.slates[0]["candidates"], scores=rec["scores"],
        ranking=rec["ranking"], chosen=rec["chosen"], propensities=rec["propensities"],
        exploration_floor=rec["exploration_floor"],
    )
    res = evaluate_shadow_gate(log=log, config=scenario.config, candidate=scenario.candidate,
                               incumbent=scenario.behaviour)
    assert res["gate_status"] == "WITHHELD"


def test_gate_evaluation_is_deterministic(big_world):
    scenario, log = big_world
    kw = dict(log=log, config=scenario.config, candidate=scenario.candidate,
              incumbent=scenario.behaviour, gate=GateConfig(bootstrap=100, null_replications=10))
    a = evaluate_shadow_gate(**kw)
    b = evaluate_shadow_gate(**kw)
    assert content_digest(a) == content_digest(b)


# ---------------------------------------------------------------------------
# domain purity
# ---------------------------------------------------------------------------

#: Application-area vocabulary a platform module must not contain.  Platform
#: words ("mission", "campaign", "task") are deliberately absent: the platform
#: legitimately names its own scheduling objects; what it must not learn is the
#: physics vocabulary of the domains it serves.
DOMAIN_WORDS = (
    "ship", "hull", "resistance", "propeller", "cavitation", "seakeeping",
    "lbm", "fem", "dem", "bem", "floe", "ice",
)


def test_module_is_domain_free():
    """The platform module must not learn domain vocabulary from a task.

    Whole-token matching only: ``ice`` inside ``service`` or ``choice`` is not a
    domain word, ``ice`` as an identifier is.
    """
    import re

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_dir = os.path.join(here, "tools", "dispatch_shadow")
    offenders = {}
    for name in sorted(os.listdir(mod_dir)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(mod_dir, name), encoding="utf-8").read().lower()
        hits = sorted(
            w for w in DOMAIN_WORDS
            if re.search(r"(?<![a-z0-9_])" + re.escape(w) + r"(?![a-z0-9_])", text)
        )
        if hits:
            offenders[name] = hits
    assert offenders == {}, offenders


def test_tests_use_only_synthetic_names():
    here = os.path.dirname(os.path.abspath(__file__))
    text = open(os.path.join(here, "test_dispatch_shadow.py"), encoding="utf-8").read().lower()
    # the domain-word list itself is exempt (it is the detector's vocabulary)
    parts = text.split("domain_words = (")
    text = parts[0] + (parts[2] if len(parts) > 2 else "")
    forbidden_domain = ("tensor" + "lbm", "tensor" + "dem", "lind" + "qvist",
                        "wig" + "ley", "hul" + "l")
    for forbidden in forbidden_domain:
        assert forbidden not in text
    assert "synthetic." in text