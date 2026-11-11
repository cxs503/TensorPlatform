"""Shadow replay gate: turn "the new policy is better" into a machine verdict.

The gate answers one question with evidence that can be re-checked later:

    *Could the candidate policy version beat the incumbent on the rounds we
    already logged -- and is the log itself trustworthy enough to answer?*

Six criteria, each a number plus a threshold (nothing is asserted in prose):

===== ============================================== =========================
id    what it checks                                 fail mode it blocks
===== ============================================== =========================
A     schema v1 integrity + hash chain verify        hand-edited / reordered log
B     bitwise replay fidelity (0 mismatches)         "replay" that is a re-run
      + 1-ulp perturbation sensitivity                with different code/state
C     leakage: clean log 0 alarms, injected leak     outcome smuggled into
      alarms, pure-noise column stays silent          features
D     shadow lift: CI lower bound of SNIPS-Pi0 > 0   a "better" claim that is
                                                      selection bias
E     effective sample size >= floor                 positivity collapse
      (importance weights concentrated)
F     determinism: two evaluations byte-identical    host RNG / dict order
===== ============================================== =========================

``WITHHELD`` is a first-class outcome: if the log has no labelled rounds, or if
ESS is too low to support any comparison, the gate refuses to answer instead of
returning a weak "pass".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .canonical import content_digest
from .leakage import (
    LeakageDetector,
    augment_config_with_feature,
    inject_outcome_feature,
    null_replications,
    scan_structural,
)
from .offpolicy import estimate_from_records
from .policy import Policy, PolicyRegistry
from .replay import perturbation_probe, replay_log
from .schema import DecisionRecord, LogConfig
from .store import DecisionLog

__all__ = ["GateConfig", "GateCriterion", "evaluate_shadow_gate"]


@dataclass
class GateConfig:
    reward_field: str = "synthetic.reward"
    leak_threshold: float = 0.95
    ess_min: float = 0.10
    min_lift: float = 0.0
    bootstrap: int = 500
    alpha: float = 0.05
    null_replications: int = 40
    leakage_feature: str = "synthetic.leak.probe"
    leak_injection_prob: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward_field": self.reward_field,
            "leak_threshold": self.leak_threshold,
            "ess_min": self.ess_min,
            "min_lift": self.min_lift,
            "bootstrap": self.bootstrap,
            "alpha": self.alpha,
            "null_replications": self.null_replications,
            "leakage_feature": self.leakage_feature,
            "leak_injection_prob": self.leak_injection_prob,
        }


@dataclass
class GateCriterion:
    id: str
    description: str
    observed: Any
    threshold: Any
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "observed": self.observed,
            "threshold": self.threshold,
            "passed": bool(self.passed),
            "detail": self.detail,
        }


def _evaluate_once(
    *,
    log: DecisionLog,
    records: Sequence[DecisionRecord],
    config: LogConfig,
    candidate: Policy,
    incumbent: Policy,
    gate: GateConfig,
) -> dict[str, Any]:
    chain = log.verify()
    structural = scan_structural(records, config)
    replay = replay_log(records, incumbent, config=config)
    probe = perturbation_probe(records, incumbent, ulps=(1, 2, 8, 1024), config=config)

    labelled = [r for r in records if r.labelled()]
    if not labelled:
        return {
            "status": "WITHHELD",
            "reason": "no labelled rounds in the log",
            "chain": chain.to_dict(),
            "structural": structural,
            "replay": replay.to_dict(),
            "perturbation": probe,
        }

    shadow = estimate_from_records(
        records,
        candidate,
        reward_field=gate.reward_field,
        bootstrap=gate.bootstrap,
        alpha=gate.alpha,
        config=config,
    )
    incumbent_est = estimate_from_records(
        records,
        incumbent,
        reward_field=gate.reward_field,
        bootstrap=0,
        alpha=gate.alpha,
        config=config,
    )

    # ---- leakage: clean / injected-smuggled / pure-noise -----------------
    detector = LeakageDetector(threshold=gate.leak_threshold, outcome_field=gate.reward_field)
    clean_scan = detector.scan(labelled, config.feature_names)
    leak_cfg = augment_config_with_feature(config, gate.leakage_feature)
    smuggled = inject_outcome_feature(
        labelled,
        outcome_field=gate.reward_field,
        feature_name=gate.leakage_feature,
        leak_prob=gate.leak_injection_prob,
        seed="synthetic.leak.control",
    )
    smuggled_scan = detector.scan(smuggled, list(leak_cfg.feature_names))
    noise = inject_outcome_feature(
        labelled,
        outcome_field=gate.reward_field,
        feature_name=gate.leakage_feature,
        leak_prob=0.5,
        seed="synthetic.leak.control",
    )
    noise_scan = detector.scan(noise, list(leak_cfg.feature_names))
    null = null_replications(
        labelled,
        config.feature_names,
        outcome_field=gate.reward_field,
        threshold=gate.leak_threshold,
        replications=gate.null_replications,
    )

    # ---- criteria --------------------------------------------------------
    ci_low = shadow.ci.get("snips_minus_naive", [float("nan"), float("nan")])[0]
    lift = shadow.snips - incumbent_est.snips
    criteria = [
        GateCriterion(
            "A",
            "schema v1 integrity and hash-chain verification",
            {"chain_ok": chain.ok, "records": chain.records, "problems": len(chain.problems)},
            {"chain_ok": True},
            bool(chain.ok),
            f"{len(chain.problems)} chain problems",
        ),
        GateCriterion(
            "B",
            "bitwise replay fidelity under the pinned incumbent version",
            {"fidelity": replay.fidelity, "mismatches": len(replay.mismatches),
             "one_ulp_detection": probe["rows"][0]["detection_rate"] if probe["rows"] else 0.0},
            {"fidelity": 1.0, "one_ulp_detection": 1.0},
            replay.ok and replay.fidelity == 1.0
            and (probe["rows"][0]["detection_rate"] == 1.0 if probe["rows"] else False),
            f"{replay.replayable} records replayed",
        ),
        GateCriterion(
            "C",
            "leakage negative control (clean silent / outcome-copy alarms / noise silent)",
            {"clean_alarms": len(clean_scan["alarms"]) + len(structural["alarms"]),
             "smuggled_alarms": len(smuggled_scan["alarms"]),
             "noise_alarms": len(noise_scan["alarms"]),
             "smuggled_auc": smuggled_scan["per_feature_auc"].get(gate.leakage_feature),
             "null_false_alarm_rate": null["false_alarm_rate"]},
            {"clean_alarms": 0, "smuggled_alarms_min": 1, "noise_alarms": 0},
            (len(clean_scan["alarms"]) + len(structural["alarms"])) == 0
            and len(smuggled_scan["alarms"]) >= 1
            and len(noise_scan["alarms"]) == 0,
            f"threshold AUC >= {gate.leak_threshold}",
        ),
        GateCriterion(
            "D",
            "shadow lift of candidate over incumbent (SNIPS, bootstrap CI lower bound)",
            {"snips_candidate": shadow.snips, "snips_incumbent": incumbent_est.snips,
             "lift": lift, "ci95_low": ci_low, "ci95_high": shadow.ci.get("snips_minus_naive", [None, None])[1]},
            {"ci95_low_gt": gate.min_lift},
            math.isfinite(ci_low) and ci_low > gate.min_lift,
            f"n={shadow.n} labelled rounds",
        ),
        GateCriterion(
            "E",
            "effective sample size floor (positivity / weight concentration)",
            {"ess": shadow.ess, "ess_frac": shadow.ess_frac, "max_weight": shadow.max_weight},
            {"ess_frac_min": gate.ess_min},
            shadow.ess_frac >= gate.ess_min,
            "ESS/n = (sum w)^2 / (n sum w^2)",
        ),
    ]
    passed = all(c.passed for c in criteria)
    status = "PASS" if passed else "FAIL"
    if not criteria[4].passed and criteria[3].passed:
        # the estimate exists but cannot carry a comparison -> refuse, don't fail
        status = "WITHHELD"

    return {
        "status": status,
        "chain": chain.to_dict(),
        "structural": structural,
        "replay": replay.to_dict(),
        "perturbation": probe,
        "shadow_candidate": shadow.to_dict(),
        "shadow_incumbent": incumbent_est.to_dict(),
        "leakage": {
            "clean": clean_scan,
            "smuggled": {"alarms": smuggled_scan["alarms"],
                          "max_auc": smuggled_scan["max_auc"]},
            "noise": {"alarms": noise_scan["alarms"], "max_auc": noise_scan["max_auc"]},
            "null": null,
        },
        "criteria": [c.to_dict() for c in criteria],
        "config_sha256": config.sha256,
        "policy_versions": {
            "candidate": {"policy_id": candidate.policy_id, "sha256": candidate.sha256},
            "incumbent": {"policy_id": incumbent.policy_id, "sha256": incumbent.sha256},
        },
    }


def evaluate_shadow_gate(
    *,
    log: DecisionLog,
    config: LogConfig,
    candidate: Policy,
    incumbent: Policy,
    gate: GateConfig | None = None,
    registry: PolicyRegistry | None = None,
) -> dict[str, Any]:
    """Run the shadow gate twice (determinism criterion F) and return the report."""
    gate = gate or GateConfig()
    registry = registry or PolicyRegistry()
    registry.register(incumbent)
    registry.register(candidate)
    records = log.records()
    first = _evaluate_once(
        log=log, records=records, config=config,
        candidate=candidate, incumbent=incumbent, gate=gate,
    )
    second = _evaluate_once(
        log=log, records=records, config=config,
        candidate=candidate, incumbent=incumbent, gate=gate,
    )
    deterministic = content_digest(first) == content_digest(second)
    crit_f = GateCriterion(
        "F",
        "determinism of the whole evaluation (two runs, byte-identical report)",
        {"identical": deterministic},
        {"identical": True},
        deterministic,
        "counter-mode SHA-256 RNG + canonical JSON: no host RNG, no clock",
    )
    report = dict(first)
    report.setdefault("criteria", []).append(crit_f.to_dict())
    if report.get("status") != "WITHHELD":
        if not deterministic and report["status"] == "PASS":
            report["status"] = "FAIL"
    report["gate_config"] = gate.to_dict()
    report["gate_status"] = report.get("status")
    report["replay_fidelity"] = report.get("replay", {}).get("fidelity")
    return report