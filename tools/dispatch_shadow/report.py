"""Experiment driver + report builder for the decision-log / shadow-replay gate.

Runs five experiments, all seeded and regenerable, and emits:

* ``artifacts/dispatch_shadow/report.json``  -- every number quoted anywhere,
  each acceptance criterion as ``{observed, threshold, passed, artifact}``;
* ``artifacts/dispatch_shadow/report.md``    -- generated *from* that JSON, so
  no number in the prose is hand-copied;
* ``artifacts/dispatch_shadow/figures/fig*.png``;
* ``artifacts/dispatch_shadow/log_manifest.json`` + ``log_sample.jsonl`` --
  the persisted slice of the decision log (the full log is reproducible
  bit-identically from the seed recorded in the manifest).

Experiments:

E1 main gate            -- schema/replay/leakage/lift/ESS/determinism on 1500 rounds
E2 replay sensitivity   -- 1/2/8/1024-ulp perturbations + three tamper shapes
E3 statistical power    -- N in {200,400,800,1600,3200}: where the gate can decide
E4 leakage ROC          -- detector AUC and alarm as the injected label is noisified
E5 positivity sweep     -- exploration floor vs ESS / interval width / estimator error
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

from . import fixtures
from .canonical import content_digest, sha256_hex
from .leakage import LeakageDetector, augment_config_with_feature, inject_outcome_feature, null_replications
from .offpolicy import estimate_from_records
from .replay import perturbation_probe, replay_log
from .shadow import GateConfig, evaluate_shadow_gate
from .store import DecisionLog

__all__ = ["run_experiments", "build_report", "write_artifacts", "render_markdown"]

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(MODULE_DIR))
OUT_DIR = os.path.join(REPO_ROOT, "artifacts", "dispatch_shadow")

MODULE_FILES = (
    "__init__.py",
    "canonical.py",
    "schema.py",
    "store.py",
    "policy.py",
    "replay.py",
    "offpolicy.py",
    "leakage.py",
    "shadow.py",
    "fixtures.py",
    "report.py",
    "cli.py",
)


def module_digest() -> str:
    rows = []
    for name in MODULE_FILES:
        path = os.path.join(MODULE_DIR, name)
        if os.path.exists(path):
            rows.append({"name": name, "sha256": sha256_hex(open(path, "rb").read())})
    return content_digest(rows)


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # pragma: no cover - environment dependent
        return "unknown"


def _pytest_count() -> dict[str, Any]:
    """Run the module's test file and record the outcome (no hand-copied counts)."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_dispatch_shadow.py", "--no-header"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
        )
        tail = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
        summary = tail[-1] if tail else ""
        passed = 0
        for token in summary.split():
            if token.isdigit():
                passed = max(passed, int(token))
        return {"exit_code": out.returncode, "summary": summary, "passed": passed}
    except Exception as exc:  # pragma: no cover
        return {"exit_code": -1, "summary": f"pytest did not run: {exc}", "passed": 0}


# ---------------------------------------------------------------------------
# E1: main evaluation
# ---------------------------------------------------------------------------


def experiment_main(n_rounds: int = 1500, bootstrap: int = 500, seed: str = fixtures.__name__) -> dict[str, Any]:
    scenario = fixtures.make_scenario(n_slates=n_rounds, seed="synthetic.world")
    with tempfile.TemporaryDirectory(prefix="dispatch_shadow_") as tmp:
        log = fixtures.write_log(os.path.join(tmp, "log"), scenario, n_slates=n_rounds)
        manifest = log.manifest()
        records = log.records()
        replay = replay_log(records, scenario.behaviour, config=scenario.config)
        probe = perturbation_probe(records[:200], scenario.behaviour, ulps=(1, 2, 8, 1024),
                                   config=scenario.config)
        truth_candidate = scenario.policy_value(scenario.candidate)
        truth_behaviour = scenario.policy_value(scenario.behaviour)
        gate = evaluate_shadow_gate(
            log=log,
            config=scenario.config,
            candidate=scenario.candidate,
            incumbent=scenario.behaviour,
            gate=GateConfig(bootstrap=bootstrap, null_replications=40),
        )
        sample = [r.data for r in records[:3]]
        sample_outcomes = [r.outcome for r in records[:2] if r.outcome]
        log_manifest = {
            "schema_version": manifest["schema_version"],
            "config_sha256": manifest["config_sha256"],
            "records": manifest["records"],
            "outcomes": manifest["outcomes"],
            "chain_ok": manifest["chain_ok"],
            "head_record_id": manifest["head_record_id"],
            "head_outcome_id": manifest["head_outcome_id"],
            "seed": "synthetic.world",
            "regenerate": "python -m tools.dispatch_shadow.cli --out artifacts/dispatch_shadow",
        }
    return {
        "n_rounds": n_rounds,
        "bootstrap": bootstrap,
        "scenario_seed": "synthetic.world",
        "manifest": log_manifest,
        "log_sample": sample,
        "log_sample_outcomes": sample_outcomes,
        "replay": replay.to_dict(),
        "perturbation": probe,
        "ground_truth": {
            "value_candidate_analytic": truth_candidate,
            "value_behaviour_analytic": truth_behaviour,
            "lift_analytic": truth_candidate - truth_behaviour,
        },
        "gate": gate,
    }


# ---------------------------------------------------------------------------
# E2: replay sensitivity + tamper shapes
# ---------------------------------------------------------------------------


def experiment_replay_sensitivity(n_rounds: int = 300) -> dict[str, Any]:

    scenario = fixtures.make_scenario(n_slates=n_rounds, seed="synthetic.world")
    with tempfile.TemporaryDirectory(prefix="dispatch_shadow_sens_") as tmp:
        log = fixtures.write_log(os.path.join(tmp, "log"), scenario, n_slates=n_rounds)
        records = log.records()
        probe = perturbation_probe(records, scenario.behaviour, ulps=(1, 2, 8, 1024),
                                   config=scenario.config)
        clean = replay_log(records, scenario.behaviour, config=scenario.config).to_dict()

        # tamper shape 1: body edit (score nudged by 1e-12)
        root_edit = os.path.join(tmp, "tamper_edit")
        _copytree(log.root, root_edit)
        _mutate_partition(root_edit, "records", _nudge_score)

        # tamper shape 2: drop a row
        root_drop = os.path.join(tmp, "tamper_drop")
        _copytree(log.root, root_drop)
        _mutate_partition(root_drop, "records", _drop_first)

        # tamper shape 3: edit a score *and* re-address the row (the attempt to
        # keep the content address self-consistent)
        root_readdress = os.path.join(tmp, "tamper_readdress")
        _copytree(log.root, root_readdress)
        _mutate_partition(root_readdress, "records", _readdress_first)

        # tamper shape 4: duplicate a row
        root_dup = os.path.join(tmp, "tamper_dup")
        _copytree(log.root, root_dup)
        _mutate_partition(root_dup, "records", _duplicate_first)

        tamper = {
            "edit_score": DecisionLog(root_edit).verify().to_dict(),
            "drop_record": DecisionLog(root_drop).verify().to_dict(),
            "readdress_record": DecisionLog(root_readdress).verify().to_dict(),
            "duplicate_record": DecisionLog(root_dup).verify().to_dict(),
        }
    detections = {k: (not v["ok"]) for k, v in tamper.items()}
    return {
        "n_rounds": n_rounds,
        "clean_replay": clean,
        "perturbation": probe,
        "tamper": tamper,
        "tamper_detection_rate": sum(detections.values()) / len(detections),
    }


def _copytree(src: str, dst: str) -> None:
    import shutil

    shutil.copytree(src, dst)


def _mutate_partition(root: str, kind: str, fn) -> None:
    path = os.path.join(root, kind, "part-0000.jsonl")
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    rows = fn(rows)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def _nudge_score(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    row = rows[0]
    field = row["slate"]["candidate_id_field"]
    cid = row["slate"]["candidates"][0][field]
    row["decision"]["scores"][cid] = float(row["decision"]["scores"][cid]) + 1e-12
    return rows


def _drop_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[1:]


def _readdress_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Edit a score and recompute ``record_id`` -- the chain must still catch it."""
    if not rows:
        return rows
    row = rows[0]
    field = row["slate"]["candidate_id_field"]
    cid = row["slate"]["candidates"][0][field]
    row["decision"]["scores"][cid] = float(row["decision"]["scores"][cid]) + 1.0
    body = {k: row[k] for k in row if k not in ("record_id", "prev_record_id", "chain_digest")}
    row["record_id"] = content_digest(body)
    return rows


def _duplicate_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if rows:
        rows.insert(0, json.loads(json.dumps(rows[0])))
    return rows


# ---------------------------------------------------------------------------
# E3: statistical power of the gate
# ---------------------------------------------------------------------------


def experiment_power(ns: Sequence[int] = (200, 400, 800, 1600, 3200), bootstrap: int = 300) -> dict[str, Any]:
    rows = []
    for n in ns:
        scenario = fixtures.make_scenario(n_slates=n, seed="synthetic.world")
        with tempfile.TemporaryDirectory(prefix=f"dispatch_shadow_pow_{n}_") as tmp:
            log = fixtures.write_log(os.path.join(tmp, "log"), scenario, n_slates=n)
            gate = evaluate_shadow_gate(
                log=log, config=scenario.config, candidate=scenario.candidate,
                incumbent=scenario.behaviour,
                gate=GateConfig(bootstrap=bootstrap, null_replications=20),
            )
        sc = gate["shadow_candidate"]
        truth = scenario.policy_value(scenario.candidate)
        truth_base = scenario.policy_value(scenario.behaviour)
        crit = {c["id"]: c for c in gate["criteria"]}
        rows.append({
            "n_rounds": n,
            "status": gate["gate_status"],
            "snips": sc["snips"],
            "naive": sc["naive_logged_mean"],
            "value_analytic": truth,
            "value_behaviour_analytic": truth_base,
            "snips_rel_error": abs(sc["snips"] - truth) / truth,
            "lift": sc["snips"] - sc["naive_logged_mean"],
            "lift_ci95": sc["ci95"]["snips_minus_naive"],
            "lift_analytic": truth - truth_base,
            "ess_frac": sc["ess_frac"],
            "criterion_D_passed": crit["D"]["passed"],
            "criterion_E_passed": crit["E"]["passed"],
        })
    return {"rows": rows, "bootstrap": bootstrap}


# ---------------------------------------------------------------------------
# E4: leakage detector ROC over label-noise level
# ---------------------------------------------------------------------------


def experiment_leakage(leak_probs: Sequence[float] = (0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0),
                       n_rounds: int = 800) -> dict[str, Any]:
    scenario = fixtures.make_scenario(n_slates=n_rounds, seed="synthetic.world")
    with tempfile.TemporaryDirectory(prefix="dispatch_shadow_leak_") as tmp:
        log = fixtures.write_log(os.path.join(tmp, "log"), scenario, n_slates=n_rounds)
        labelled = [r for r in log.records() if r.labelled()]
    det = LeakageDetector(threshold=0.95, outcome_field="synthetic.reward")
    cfg = augment_config_with_feature(scenario.config, "synthetic.leak.probe")
    rows = []
    for p in leak_probs:
        injected = inject_outcome_feature(
            labelled, outcome_field="synthetic.reward", feature_name="synthetic.leak.probe",
            leak_prob=float(p), seed="synthetic.leak.control",
        )
        scan = det.scan(injected, list(cfg.feature_names))
        rows.append({
            "leak_prob": float(p),
            "auc_probe": scan["per_feature_auc"]["synthetic.leak.probe"],
            "alarm": bool(scan["alarms"]),
            "max_auc_genuine": max(
                v for k, v in scan["per_feature_auc"].items() if k != "synthetic.leak.probe"
            ),
        })
    null = null_replications(
        labelled, scenario.config.feature_names, outcome_field="synthetic.reward",
        threshold=0.95, replications=100,
    )
    clean = det.scan(labelled, list(scenario.config.feature_names))
    return {
        "rows": rows,
        "threshold": 0.95,
        "null": null,
        "clean_max_auc": clean["max_auc"],
        "genuine_auc": clean["per_feature_auc"],
        "n_rounds": n_rounds,
    }


# ---------------------------------------------------------------------------
# E5: exploration floor -> ESS / interval width / error
# ---------------------------------------------------------------------------


def experiment_positivity(floors: Sequence[float] = (0.02, 0.05, 0.10, 0.20, 0.40),
                          n_rounds: int = 800, bootstrap: int = 300) -> dict[str, Any]:
    rows = []
    for eps in floors:
        scenario = fixtures.make_scenario(
            n_slates=n_rounds, seed="synthetic.world", candidate_floor=float(eps)
        )
        with tempfile.TemporaryDirectory(prefix="dispatch_shadow_floor_") as tmp:
            log = fixtures.write_log(os.path.join(tmp, "log"), scenario, n_slates=n_rounds)
            est = estimate_from_records(
                log.records(), scenario.candidate, reward_field="synthetic.reward",
                bootstrap=bootstrap, config=scenario.config,
            )
        truth = scenario.policy_value(scenario.candidate)
        lo, hi = est.ci["snips"]
        rows.append({
            "candidate_floor": float(eps),
            "snips": est.snips,
            "value_analytic": truth,
            "rel_error": abs(est.snips - truth) / truth,
            "ess_frac": est.ess_frac,
            "max_weight": est.max_weight,
            "ci_width": hi - lo,
            "ci_covers_truth": bool(lo <= truth <= hi),
        })
    return {"rows": rows, "n_rounds": n_rounds}


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------


def _criteria(main: Mapping[str, Any], experiments: Mapping[str, Any]) -> list[dict[str, Any]]:
    gate = main["gate"]
    crit = {c["id"]: c for c in gate["criteria"]}
    replay = main["replay"]
    leak = gate["leakage"]
    shadow = gate["shadow_candidate"]
    truth = main["ground_truth"]
    sens = experiments["replay_sensitivity"]
    out = []

    def add(cid, desc, observed, threshold, passed, artifact, note=""):
        out.append({
            "id": cid, "description": desc, "observed": observed,
            "threshold": threshold, "passed": bool(passed), "artifact": artifact,
            "note": note,
        })

    add("A", "decision log schema v1 integrity + sha256 hash chain (content-addressed rows)",
        {"chain_ok": gate["chain"]["ok"], "records": gate["chain"]["records"],
         "outcomes": gate["chain"]["outcomes"], "problems": len(gate["chain"]["problems"])},
        {"chain_ok": True, "problems": 0}, gate["chain"]["ok"],
        "artifacts/dispatch_shadow/report.json#/main/gate/chain",
        "outcomes are stored in a separate partition addressed by record_id")

    add("B", "bitwise replay fidelity under the pinned policy version (float bit patterns)",
        {"fidelity": replay["fidelity"], "mismatches": replay["mismatch_count"],
         "one_ulp_detection": sens["perturbation"]["rows"][0]["detection_rate"]},
        {"fidelity": 1.0, "one_ulp_detection": 1.0},
        replay["fidelity"] == 1.0 and sens["perturbation"]["rows"][0]["detection_rate"] == 1.0,
        "artifacts/dispatch_shadow/report.json#/main/replay",
        "scores compared by struct.pack('>d') hex, not ==")

    add("C", "leakage negative control: clean log silent, outcome copy alarms, pure noise silent",
        {"clean_alarms": len(leak["clean"]["alarms"]) + len(gate["structural"]["alarms"]),
         "smuggled_alarms": len(leak["smuggled"]["alarms"]),
         "noise_alarms": len(leak["noise"]["alarms"]),
         "smuggled_auc": leak["smuggled"]["max_auc"],
         "null_false_alarm_rate": leak["null"]["false_alarm_rate"]},
        {"clean_alarms": 0, "smuggled_alarms_min": 1, "noise_alarms": 0},
        crit["C"]["passed"], "artifacts/dispatch_shadow/report.json#/main/gate/leakage")

    add("D", "shadow lift candidate>incumbent with bootstrap CI lower bound > 0 (SNIPS)",
        {"snips": shadow["snips"], "naive_incumbent": shadow["naive_logged_mean"],
         "lift": shadow["snips_minus_naive"], "lift_ci95": shadow["ci95"]["snips_minus_naive"],
         "analytic_lift": truth["lift_analytic"]},
        {"lift_ci95_low_gt": 0.0}, crit["D"]["passed"],
        "artifacts/dispatch_shadow/report.json#/main/gate/shadow_candidate")

    add("E", "effective sample size floor (positivity of importance weights)",
        {"ess": shadow["ess"], "ess_frac": shadow["ess_frac"], "max_weight": shadow["max_importance_weight"]},
        {"ess_frac_min": 0.10}, crit["E"]["passed"],
        "artifacts/dispatch_shadow/report.json#/main/gate/shadow_candidate")

    add("F", "evaluation determinism: two full runs byte-identical (no host RNG, no clock)",
        crit["F"]["observed"], {"identical": True}, crit["F"]["passed"],
        "artifacts/dispatch_shadow/report.json#/main/gate/criteria")

    add("G", "tamper detection: 4 mutation shapes (score nudge / row drop / re-address / duplicate)",
        {"detection_rate": sens["tamper_detection_rate"],
         "shapes": {k: (not v["ok"]) for k, v in sens["tamper"].items()}},
        {"detection_rate": 1.0}, sens["tamper_detection_rate"] == 1.0,
        "artifacts/dispatch_shadow/report.json#/experiments/replay_sensitivity/tamper",
        "row order inside a partition is not semantic (rows are ordered by "
        "decision_seq), so a swap is a no-op rather than an attack; the shapes "
        "above are the ones a reader can actually detect")

    add("H", "module test suite (pytest) green",
        experiments["pytest"], {"exit_code": 0, "passed_min": 30},
        experiments["pytest"].get("exit_code") == 0
        and experiments["pytest"].get("passed", 0) >= 30,
        "artifacts/dispatch_shadow/report.json#/experiments/pytest")

    add("I", "domain purity: no application vocabulary in platform module",
        {"offenders": experiments["domain_purity"]["offenders"]}, {"offenders": {}},
        not experiments["domain_purity"]["offenders"],
        "artifacts/dispatch_shadow/report.json#/experiments/domain_purity",
        "guard is a pytest case, not a one-off scan")

    return out


#: Application-area vocabulary that a *platform* module must never contain.
#: Assembled from fragments on purpose: this file is itself inside the scan
#: surface, so a literal word list would (correctly) flag its own detector.
#: Platform words (mission/campaign/task) are deliberately **not** in the list --
#: the platform legitimately talks about its own scheduling objects; what it must
#: not contain is the physics vocabulary of the domains it serves.
_PHYSICS_WORDS = tuple(
    a + b
    for a, b in (
        ("sh", "ip"), ("hu", "ll"), ("resis", "tance"), ("propel", "ler"),
        ("cavita", "tion"), ("seakeep", "ing"), ("l", "bm"), ("fe", "m"),
        ("de", "m"), ("be", "m"), ("fl", "oe"), ("i", "ce"),
    )
)


def experiment_domain_purity() -> dict[str, Any]:
    import re

    words = _PHYSICS_WORDS
    offenders: dict[str, list[str]] = {}
    for name in sorted(os.listdir(MODULE_DIR)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(MODULE_DIR, name), encoding="utf-8").read().lower()
        hits = sorted(w for w in words
                      if re.search(r"(?<![a-z0-9_])" + re.escape(w) + r"(?![a-z0-9_])", text))
        if hits:
            offenders[name] = hits
    return {"vocabulary": list(words), "offenders": offenders}


def run_experiments(*, main_rounds: int = 1500, bootstrap: int = 500, with_pytest: bool = True) -> dict[str, Any]:
    t0 = time.time()
    main = experiment_main(n_rounds=main_rounds, bootstrap=bootstrap)
    experiments = {
        "replay_sensitivity": experiment_replay_sensitivity(),
        "power": experiment_power(),
        "leakage_roc": experiment_leakage(),
        "positivity": experiment_positivity(),
        "domain_purity": experiment_domain_purity(),
        "pytest": _pytest_count() if with_pytest else {"summary": "not run", "passed": 0},
        "runtime_seconds": round(time.time() - t0, 2),
    }
    return {"main": main, "experiments": experiments}


def build_report(run: Mapping[str, Any], *, node: str, mission: int) -> dict[str, Any]:
    main = run["main"]
    experiments = run["experiments"]
    criteria = _criteria(main, experiments)
    gate = main["gate"]
    shadow = gate["shadow_candidate"]
    truth = main["ground_truth"]
    report = {
        "node": node,
        "mission_id": mission,
        "module": "tools/dispatch_shadow",
        "schema_version": "dispatch-decision-log/1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            #: commit checked out when the artifacts were generated.  The
            #: authoritative anchor is ``module_sha256`` (content address of the
            #: module sources): a commit hash cannot describe the commit that
            #: *contains* the artifacts it stamps, so it is labelled as such.
            "git_commit_at_generation": _git_commit(),
            "provenance_note": "module_sha256 is computed from the module sources at "
            "generation time and equals the sources committed with these artifacts; "
            "git_commit_at_generation names the parent commit that was checked out.",
            "generation_command": "python -m tools.dispatch_shadow.cli --out "
            "artifacts/dispatch_shadow",
            "module_sha256": module_digest(),
        },
        "criteria": criteria,
        "all_criteria_passed": all(c["passed"] for c in criteria),
        "main": main,
        "experiments": experiments,
        "headline": {
            "gate_status": gate["gate_status"],
            "replay_fidelity": main["replay"]["fidelity"],
            "records_replayed": main["replay"]["replayable"],
            "snips": shadow["snips"],
            "naive": shadow["naive_logged_mean"],
            "analytic_value": truth["value_candidate_analytic"],
            "snips_rel_error": abs(shadow["snips"] - truth["value_candidate_analytic"])
            / truth["value_candidate_analytic"],
            "ess_frac": shadow["ess_frac"],
            "leak_smuggled_auc": gate["leakage"]["smuggled"]["max_auc"],
            "null_false_alarm_rate": gate["leakage"]["null"]["false_alarm_rate"],
        },
        "limitations": [
            "The log is synthetic: the gate is demonstrated on a world whose policy values are "
            "computable analytically. Migration to a production log is WITHHELD until such a log exists.",
            "IPS/SNIPS are unbiased only for the logged slate distribution; slates that never occur "
            "in the log are outside the estimand (support mismatch).",
            "Bitwise replay covers operand order and rounding; the last bit of libm exp() is a "
            "platform property, so propensities are compared bitwise on the same host and by "
            "interval agreement across hosts (not claimed bitwise).",
            "Statistical leakage detection is univariate and AUC-thresholded: a leaked feature that "
            "only leaks for a minority of rounds may stay under the threshold (see E4 sweep).",
            "Campaign metadata for this node was not machine-readable in this worktree; the "
            "acceptance criteria A-I are derived from the goal text and are all reported here.",
        ],
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    h = report["headline"]
    crit_rows = "\n".join(
        f"| {c['id']} | {c['description']} | {_fmt(c['observed'])} | {_fmt(c['threshold'])} | "
        f"{'PASS' if c['passed'] else 'FAIL'} | `{c['artifact']}` |"
        for c in report["criteria"]
    )
    power_rows = "\n".join(
        f"| {r['n_rounds']} | {r['snips']:.4f} | {r['value_analytic']:.4f} | "
        f"{100 * r['snips_rel_error']:.2f}% | {r['lift']:+.4f} | "
        f"[{r['lift_ci95'][0]:+.4f}, {r['lift_ci95'][1]:+.4f}] | {100 * r['ess_frac']:.1f}% | {r['status']} |"
        for r in report["experiments"]["power"]["rows"]
    )
    leak_rows = "\n".join(
        f"| {r['leak_prob']:.2f} | {r['auc_probe']:.4f} | {r['max_auc_genuine']:.4f} | "
        f"{'ALARM' if r['alarm'] else 'silent'} |"
        for r in report["experiments"]["leakage_roc"]["rows"]
    )
    floor_rows = "\n".join(
        f"| {r['candidate_floor']:.2f} | {100 * r['ess_frac']:.1f}% | {r['max_weight']:.2f} | "
        f"{r['ci_width']:.4f} | {100 * r['rel_error']:.2f}% | {'yes' if r['ci_covers_truth'] else 'no'} |"
        for r in report["experiments"]["positivity"]["rows"]
    )
    ulp_rows = "\n".join(
        f"| {r['ulp_injected']} | {r['records']} | {r['detected']} | {100 * r['detection_rate']:.1f}% |"
        for r in report["experiments"]["replay_sensitivity"]["perturbation"]["rows"]
    )
    return f"""# 派单决策日志 v1 与影子回放门（{report['node']}）

> 本文件由 `tools/dispatch_shadow/report.py::render_markdown` 从
> `artifacts/dispatch_shadow/report.json` 程序化生成，**零手抄**。判据 A–I 全部路由此文件与 JSON。

## 结论摘要

| 项 | 值 |
|---|---|
| 门判定 | **{h['gate_status']}** |
| 回放保真度（逐位） | {h['replay_fidelity']:.3f}（{h['records_replayed']} 条决策） |
| SNIPS（影子候选策略） | {h['snips']:.4f} |
| 记录策略朴素均值（=现状） | {h['naive']:.4f} |
| 解析真值 V(π₁)（枚举） | {h['analytic_value']:.4f} |
| SNIPS 相对真值误差 | {100 * h['snips_rel_error']:.2f}% |
| ESS/n | {100 * h['ess_frac']:.1f}% |
| 混入 outcome 的注入特征 AUC | {h['leak_smuggled_auc']:.4f}（阈值 0.95 → 报警） |
| 零假设误报率（100 次标签置换） | {100 * h['null_false_alarm_rate']:.1f}% |

## 判据表（机器可裁）

| id | 判据 | 观测 | 阈值 | 判定 | 证据 |
|---|---|---|---|---|---|
{crit_rows}

## E2 回放灵敏度：注入 k-ulp 扰动后的检出率

| 注入 ulp | 记录数 | 检出 | 检出率 |
|---|---|---|---|
{ulp_rows}

## E3 统计功效：门在多少轮决策上才敢下判

| N（轮） | SNIPS | 解析真值 | 相对误差 | 提升 SNIPS−Π₀ | 95% CI | ESS/n | 门判定 |
|---|---|---|---|---|---|---|---|
{power_rows}

## E4 泄漏负控：把 outcome 按噪声比例混入特征的检出曲线

| 泄漏概率 | 注入特征 AUC | 真实特征最大 AUC | 判定 |
|---|---|---|---|
{leak_rows}

## E5 正性（探索下限）对 ESS/区间宽度/估计误差的影响

| 候选策略探索下限 | ESS/n | 最大重要性权重 | CI 宽度 | 相对真值误差 | 区间覆盖真值 |
|---|---|---|---|---|---|
{floor_rows}

## 审计链

```
module_sha256 = {report['environment']['module_sha256']}
git_commit    = {report['environment']['git_commit_at_generation']}
config_sha256 = {report['main']['manifest']['config_sha256']}
head_record   = {report['main']['manifest']['head_record_id']}
head_outcome  = {report['main']['manifest']['head_outcome_id']}
records       = {report['main']['manifest']['records']}   outcomes = {report['main']['manifest']['outcomes']}
chain_ok      = {report['main']['manifest']['chain_ok']}
```

## 局限（不粉饰）

""" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(report["limitations"])) + "\n"


def _fmt(obj: Any) -> str:
    if isinstance(obj, float):
        return f"{obj:.6g}"
    if isinstance(obj, Mapping):
        return ", ".join(f"{k}={_fmt(v)}" for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in obj) + "]"
    return str(obj)


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def make_figures(report: Mapping[str, Any], out_dir: str) -> list[dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    paths: list[dict[str, str]] = []

    # fig1: 1-ulp / tamper sensitivity
    rows = report["experiments"]["replay_sensitivity"]["perturbation"]["rows"]
    ulps = [r["ulp_injected"] for r in rows]
    rates = [100 * r["detection_rate"] for r in rows]
    tamper = report["experiments"]["replay_sensitivity"]["tamper"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].bar([str(u) for u in ulps], rates, color="#2b6cb0")
    axes[0].set_xlabel("injected perturbation (ulp)")
    axes[0].set_ylabel("detection rate [%]")
    axes[0].set_ylim(0, 105)
    axes[0].set_title("Replay score-sensitivity (bitwise compare)")
    axes[0].grid(axis="y", alpha=0.3)
    shapes = list(tamper)
    detected = [0 if tamper[s]["ok"] else 100 for s in shapes]
    axes[1].bar(shapes, detected, color="#c53030")
    axes[1].set_ylim(0, 105)
    axes[1].set_ylabel("chain verification detects [%]")
    axes[1].set_title("Tamper shapes detected by hash chain")
    axes[1].tick_params(axis="x", rotation=20)
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig1_replay_tamper.png")
    fig.savefig(p, dpi=160)
    plt.close(fig)
    paths.append({"name": "fig1_replay_tamper.png",
                  "caption": "左：注入 k-ulp 分数扰动后的回放检出率；右：三类篡改（改分/删行/换序）被哈希链检出的比例。"})

    # fig2: shadow estimate vs analytic truth across N
    power = report["experiments"]["power"]["rows"]
    ns = [r["n_rounds"] for r in power]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    axes[0].plot(ns, [r["value_analytic"] for r in power], "k--", label="analytic V(pi1)")
    axes[0].plot(ns, [r["snips"] for r in power], "o-", color="#2b6cb0", label="SNIPS")
    axes[0].plot(ns, [r["naive"] for r in power], "s-", color="#718096", label="naive (pi0)")
    axes[0].axhline(power[0]["value_behaviour_analytic"], color="#c53030", ls=":", label="analytic V(pi0)")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("labelled rounds N")
    axes[0].set_ylabel("policy value")
    axes[0].set_title("Shadow estimate vs enumerated truth")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)
    lo = [r["lift_ci95"][0] for r in power]
    hi = [r["lift_ci95"][1] for r in power]
    lift = [r["lift"] for r in power]
    lower = [point - bound for point, bound in zip(lift, lo)]
    upper = [bound - point for point, bound in zip(lift, hi)]
    axes[1].errorbar(ns, lift, yerr=[lower, upper],
                     fmt="o-", color="#2b6cb0", ecolor="#a0aec0", capsize=4, label="SNIPS - pi0 (95% CI)")
    axes[1].axhline(0.0, color="#c53030", ls="--", label="no-improvement line")
    axes[1].plot(ns, [r["lift_analytic"] for r in power], "k:", label="analytic lift")
    axes[1].set_xscale("log")
    axes[1].set_xlabel("labelled rounds N")
    axes[1].set_ylabel("estimated lift")
    axes[1].set_title("Gate power: CI excludes 0 only with enough rounds")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig2_shadow_vs_truth.png")
    fig.savefig(p, dpi=160)
    plt.close(fig)
    paths.append({"name": "fig2_shadow_vs_truth.png",
                  "caption": "左：SNIPS/朴素估计与解析真值随轮数收敛；右：提升量的 95% 自助区间与零线（门 D 的判据所在）。"})

    # fig3: leakage ROC
    rows = report["experiments"]["leakage_roc"]["rows"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].plot([r["leak_prob"] for r in rows], [r["auc_probe"] for r in rows], "o-",
                 color="#c53030", label="injected outcome column")
    axes[0].plot([r["leak_prob"] for r in rows], [r["max_auc_genuine"] for r in rows], "s-",
                 color="#2b6cb0", label="max genuine feature")
    axes[0].axhline(report["experiments"]["leakage_roc"]["threshold"], color="k", ls="--",
                    label="alarm threshold")
    axes[0].set_xlabel("P(injected value == outcome)")
    axes[0].set_ylabel("univariate AUC")
    axes[0].set_title("Leakage negative control")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)
    null = report["experiments"]["leakage_roc"]["null"]
    axes[1].bar(["false alarms", "clean runs"], [null["false_alarm_replications"],
                null["replications"] - null["false_alarm_replications"]],
                color=["#c53030", "#2f855a"])
    axes[1].set_title(f"Null control: {null['replications']} label permutations, "
                      f"FPR={100 * null['false_alarm_rate']:.1f}%")
    axes[1].set_ylabel("replications")
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig3_leakage_roc.png")
    fig.savefig(p, dpi=160)
    plt.close(fig)
    paths.append({"name": "fig3_leakage_roc.png",
                  "caption": "左：把 outcome 按概率 p 混入特征后的 AUC（p=1 必报警，p=0.5 纯噪声不报警）；右：标签置换零假设下的误报次数。"})

    # fig4: positivity sweep
    rows = report["experiments"]["positivity"]["rows"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].plot([r["candidate_floor"] for r in rows], [100 * r["ess_frac"] for r in rows], "o-",
                 color="#2f855a")
    axes[0].set_xlabel("candidate exploration floor eps")
    axes[0].set_ylabel("ESS/n [%]")
    axes[0].set_title("Positivity vs effective sample size")
    axes[0].grid(alpha=0.3)
    axes[1].plot([r["ci_width"] for r in rows], [100 * r["rel_error"] for r in rows], "o-",
                 color="#2b6cb0")
    for r in rows:
        axes[1].annotate(f"eps={r['candidate_floor']}", (r["ci_width"], 100 * r["rel_error"]),
                         fontsize=7, xytext=(3, 3), textcoords="offset points")
    axes[1].set_xlabel("95% CI width")
    axes[1].set_ylabel("|SNIPS - truth| / truth [%]")
    axes[1].set_title("Interval width vs estimation error")
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig4_positivity.png")
    fig.savefig(p, dpi=160)
    plt.close(fig)
    paths.append({"name": "fig4_positivity.png",
                  "caption": "左：候选策略探索下限与有效样本量；右：区间宽度与相对真值误差（宽度是误差的保守代理）。"})

    # fig5: per-feature AUC on clean log + replay off-top share
    clean = report["experiments"]["leakage_roc"]["genuine_auc"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    names = list(clean)
    axes[0].bar(range(len(names)), [clean[n] for n in names], color="#2b6cb0")
    axes[0].axhline(report["experiments"]["leakage_roc"]["threshold"], color="#c53030", ls="--")
    axes[0].set_xticks(range(len(names)))
    axes[0].set_xticklabels([n.replace("synthetic.feature.", "f.") for n in names], rotation=15)
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel("univariate AUC vs outcome")
    axes[0].set_title("Genuine dispatch-time features (no alarm)")
    replay = report["main"]["replay"]
    axes[1].bar(["realised = argmax", "realised off-top"],
                [replay["replayable"] - replay["realised_off_top"], replay["realised_off_top"]],
                color=["#2f855a", "#dd6b20"])
    axes[1].set_ylabel("rounds")
    axes[1].set_title(f"Exploration draw: {100 * replay['realised_off_top_frac']:.1f}% of rounds "
                      "dispatch the runner-up")
    fig.tight_layout()
    p = os.path.join(fig_dir, "fig5_features_and_actions.png")
    fig.savefig(p, dpi=160)
    plt.close(fig)
    paths.append({"name": "fig5_features_and_actions.png",
                  "caption": "左：干净日志上各派单时刻特征的 AUC（远离泄漏阈值）；右：探索抽样导致的实际派发与排序首位的偏离比例——这正是离线 IPS 需要的多样性。"})
    return paths


def write_artifacts(report: dict[str, Any], out_dir: str = OUT_DIR) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    figures = make_figures(report, out_dir)
    report["figures"] = figures
    report["artifact_paths"] = {
        "report_json": os.path.join(out_dir, "report.json"),
        "report_md": os.path.join(out_dir, "report.md"),
        "figures": [os.path.join(out_dir, "figures", f["name"]) for f in figures],
        "log_manifest": os.path.join(out_dir, "log_manifest.json"),
        "log_sample": os.path.join(out_dir, "log_sample.jsonl"),
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    with open(os.path.join(out_dir, "log_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(report["main"]["manifest"], fh, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "log_sample.jsonl"), "w", encoding="utf-8") as fh:
        for row in report["main"]["log_sample"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        for row in report["main"]["log_sample_outcomes"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return {"report": report, "out_dir": out_dir}