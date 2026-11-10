"""Generate attribution examples + a machine-readable summary for the report.

Outputs (all inside ``artifacts/dispatch_readiness``):

``attribution_examples.md``  human-readable verdicts for a sample of historical
                             nodes, including the four blocked nodes of plan 233
``summary.json``             the numbers quoted in the report, together with the
                             artefact paths and the acceptance verdicts
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from tools.dispatch_readiness.backtest import (  # noqa: E402
    build_views,
    label_views,
    load_label_config,
    predict_views,
    replay_interception,
    risk_score,
)
from tools.dispatch_readiness.gate import verdict_line  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "dispatch_readiness")
TARGET_PLAN = 233


def _load(name):
    with open(os.path.join(ART, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    snapshot = _load("history_snapshot.json")
    body = _load("backtest_report.json")
    rules = load_label_config()

    views = build_views(snapshot)
    preds = predict_views(views, moment="consult")
    rows = {r["node"]: r for r in label_views(views, rules)}

    by_plan = [v for v in views if str(v.plan_id) == str(TARGET_PLAN)]
    plan233 = []
    for view in by_plan:
        result = preds[view.key]
        plan233.append(
            {
                "node": view.key,
                "ext_id": view.ext_id,
                "final_status": view.status,
                "block_reason": view.block_reason,
                "gate_verdict": result.status,
                "risk": round(risk_score(result), 3),
                "reasons": result.blocking_reasons[:3],
                "line": verdict_line(
                    {
                        "node": f"{view.ext_id} ({view.key})",
                        "status": result.status,
                        "decision": "dispatch" if result.dispatchable else "defer",
                        "blocking_reasons": result.blocking_reasons,
                    }
                ),
            }
        )

    blocked_non_infra = [
        {"node": r["node"], "ext_id": r["ext_id"], "plan": r["plan_id"], "reason": r["block_reason"],
         "gap_at_stop": r["gap_at_stop"], "verdict": preds[r["node"]].status,
         "blocking": preds[r["node"]].blocking_reasons[:2]}
        for r in rows.values()
        if r["label"] is not None and r["label"] == 1
    ]

    replay = replay_interception(views, preds)

    lines = []
    lines.append("# Attribution examples — pre-dispatch interface readiness gate\n")
    lines.append("Every verdict below is produced by `DispatchGate.preflight` on the reconstructed")
    lines.append("delivery registry of the corresponding moment; the text is what a dispatcher log")
    lines.append("would carry. Node keys are database ids, `ext_id` the campaign label.\n")

    lines.append(f"## Plan {TARGET_PLAN} (the campaign this node matures)\n")
    lines.append("| node | final | recorded block reason | gate verdict | risk |")
    lines.append("|---|---|---|---|---|")
    for item in plan233:
        reason = (item["block_reason"] or "-").replace("|", "/")
        lines.append(f"| {item['ext_id']} ({item['node']}) | {item['final_status']} | {reason} | {item['gate_verdict']} | {item['risk']} |")
    fired = sum(1 for item in plan233 if item["gate_verdict"] != "READY")
    lines.append("")
    lines.append(f"Gate would have deferred **{fired} of {sum(1 for i in plan233 if i['final_status']=='blocked')}** "
                 f"blocked nodes in plan {TARGET_PLAN}: all four historical blocks were recorded as")
    lines.append("`infra x5 (agent_lost)`, i.e. worker loss, not an upstream delivery gap. This is the")
    lines.append("central honest finding of the backtest: the premise that those blocks were caused by")
    lines.append("missing upstream delivery is **not supported by the recorded cause**.\n")

    lines.append("## Upstream-gap blocks the gate intercepts\n")
    for item in blocked_non_infra[:12]:
        lines.append(f"* `{item['ext_id']}` (node {item['node']}, plan {item['plan']}) — recorded: “{item['reason']}”")
        lines.append(f"  * verdict: **{item['verdict']}**")
        for reason in item["blocking"]:
            lines.append(f"  * attribution: {reason}")
    lines.append("")

    lines.append("## Interception replay (AC5)\n")
    lines.append(f"* nodes the gate would have deferred: {replay['intercepted']}")
    lines.append(f"* of those, released after the upstream gap was closed: {replay['released_after_upstream_fix']}")
    lines.append(f"* released on the first pass after the fix: {replay['released_first_pass']} "
                 f"(rate {replay['first_pass_rate_after_fix']})")
    lines.append(f"* still blocked with the gap open: {replay['still_blocked_after_interception']}")
    lines.append("")
    for example in replay["attribution_examples"][:6]:
        lines.append(f"* `{example['ext_id']}` (node {example['node']}): " + "; ".join(example["reasons"]))

    md_path = os.path.join(ART, "attribution_examples.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    summary = {
        "schema": "dispatch-readiness-summary/1.0",
        "artifacts": {
            "history_snapshot": "artifacts/dispatch_readiness/history_snapshot.json",
            "backtest_report": "artifacts/dispatch_readiness/backtest_report.json",
            "attribution_examples": "artifacts/dispatch_readiness/attribution_examples.md",
            "figures": sorted(
                f"artifacts/dispatch_readiness/figures/{name}"
                for name in os.listdir(os.path.join(ART, "figures"))
                if name.endswith(".png")
            ),
            "doc_report": "docs/reports/P233W2-N08_interface_readiness_gate.md",
        },
        "provenance": {
            "sql_sha256": snapshot.get("provenance", {}).get("sql_sha256"),
            "snapshot_sha256": snapshot.get("provenance", {}).get("snapshot_sha256"),
            "report_sha256": body.get("report_sha256"),
        },
        "universe": {
            "terminal_nodes": snapshot.get("counts", {}).get("nodes"),
            "labeled": body["targets"]["delivery_gap_consult"]["n_labeled"],
            "positives": body["targets"]["delivery_gap_consult"]["n_positive"],
            "excluded_infra": body["out_of_scope"]["by_stratum"],
        },
        "metrics": {
            key: {
                "n_positive": target["n_positive"],
                "n_negative": target["n_negative"],
                "confusion_matrix": target["confusion_matrix"],
                "recall": target["recall"],
                "recall_ci95": target["recall_ci95"],
                "fpr": target["fpr"],
                "fpr_ci95": target["fpr_ci95"],
                "auc": target["auc"],
            }
            for key, target in body["targets"].items()
        },
        "permutation_null_primary": body["permutation_null_primary"],
        "mode_sensitivity": body["mode_sensitivity"],
        "interception_replay": {k: v for k, v in replay.items() if k != "attribution_examples"},
        "ac": body["ac"],
        "ac_extra": {
            "AC4_zero_domain": {
                "scanned": [
                    "backend/tools/dispatch_readiness",
                    "backend/tests/test_dispatch_readiness.py",
                    "backend/tests/test_dispatch_readiness_zero_domain.py",
                ],
                "terms_config": "config/dispatch_readiness_domain_terms.json",
                "degrade_on_missing_config": "test_missing_terms_config_degrades_to_identity",
                "passed": None,
            },
            "AC5_attribution": {
                "examples": "artifacts/dispatch_readiness/attribution_examples.md",
                "first_pass_rate_after_fix": replay["first_pass_rate_after_fix"],
                "passed": replay["first_pass_rate_after_fix"] is not None,
            },
            "AC6_report": {"doc": "docs/reports/P233W2-N08_interface_readiness_gate.md", "passed": None},
        },
        "plan_233_check": {
            "blocked_nodes": sum(1 for item in plan233 if item["final_status"] == "blocked"),
            "gate_deferred": fired,
            "note": "all plan-233 blocks are recorded as infra (agent_lost); the contract gate cannot and should not catch them",
        },
    }
    with open(os.path.join(ART, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(md_path)
    print(os.path.join(ART, "summary.json"))


if __name__ == "__main__":
    main()