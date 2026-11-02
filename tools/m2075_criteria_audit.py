#!/usr/bin/env python3
"""Manual + mechanical audit of the two error-prone triage judgements (m2075).

Quantifies, from the frozen queue artifacts (never from hand-copied numbers):

1. structural split of the queue: "自找活" self-start template missions vs
   specific engineering orders, and how many of each carry a machine-decidable
   acceptance criterion;
2. a manual review of a 20-mission sample (seed 7, reproducible) against the
   tool's ``criteria_computable`` verdict -> miss rate;
3. a full review of every mission the tool marked ``criteria_computable``
   -> verdict correctness + whether the *matching fragment* is really a
   criterion (rationale precision);
4. sensitivity of the commit-sha decision to the cue-word window.

Output: artifacts/m2075_criteria_audit.json
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acceptance_triage import mission_text, parse_commit_shas  # noqa: E402

ARTIFACTS = ROOT / "artifacts"
QUEUE = ARTIFACTS / "acceptance_queue.json"
SNAPSHOT = ARTIFACTS / "acceptance_input_snapshot.json"
OUT = ARTIFACTS / "m2075_criteria_audit.json"

TEMPLATE_MARKER = "自找活"
SAMPLE_SEED = 7
SAMPLE_SIZE = 20

# Manual labels, read goal-by-goal on 2026-11-02 (owner audit):
#   "agree" = tool verdict matches the manual verdict
#   "miss"  = machine-decidable criterion present in the goal but not detected
# The 19 "agree" rows are instances of the single 自找活 self-start template,
# whose full body was read once (no numeric acceptance criterion anywhere).
MANUAL_LABELS = {
    1997: "agree", 1937: "agree", 2016: "agree", 1909: "agree", 1915: "agree",
    2053: "agree", 1921: "agree", 2008: "agree", 2066: "agree", 1911: "agree",
    2045: "agree", 1959: "agree", 1906: "agree", 1919: "agree", 2027: "miss",
    2023: "agree", 1914: "agree", 1971: "agree", 1920: "agree", 2057: "agree",
}

# Full review of the missions the tool flagged as criteria_computable.
CRITERIA_TRUE_REVIEW = {
    2071: {"verdict": "ok", "rationale": "imprecise",
           "note": "matched 'ΔA=0' which is the DEFECT statement, not a threshold; "
                   "the real criterion is '三组负向用例全绿 + 端点实跑返回 p_source'"},
    1940: {"verdict": "ok", "rationale": "ok",
           "note": "matched '误差 <=15%' which is the stated acceptance threshold"},
    1941: {"verdict": "ok", "rationale": "ok",
           "note": "matched 'pytest >=8 全绿' which is the stated acceptance threshold"},
    2028: {"verdict": "ok", "rationale": "imprecise",
           "note": "matched the sweep range 'Cb(0.60->0.82)' instead of the "
                   "criterion '敏感度 >5%' / '7/7 PASS'"},
}


def main() -> int:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    missions = {int(m["id"]): m for m in snap["missions"]}
    entries = {e["mission_id"]: e for e in queue["items"]}

    template = sorted(mid for mid in missions if TEMPLATE_MARKER in (missions[mid].get("goal") or ""))
    engineering = sorted(mid for mid in missions if mid not in set(template))

    def crit(mid: int) -> bool:
        return bool(entries[mid]["criteria_computable"])

    def act(mid: int, action: str) -> bool:
        return entries[mid]["suggested_action"] == action

    sample = sorted(random.Random(SAMPLE_SEED).sample(sorted(missions), SAMPLE_SIZE))
    labels = {mid: MANUAL_LABELS[mid] for mid in sample if mid in MANUAL_LABELS}
    agree = sum(1 for v in labels.values() if v == "agree")
    miss = sum(1 for v in labels.values() if v == "miss")

    flagged = sorted(mid for mid in entries if crit(mid))
    reviewed = {mid: CRITERIA_TRUE_REVIEW[mid] for mid in flagged if mid in CRITERIA_TRUE_REVIEW}

    # commit-sha decision sensitivity to the arbitrary cue-word window
    sensitivity = []
    for window in (12, 24, 40, 60):
        import tools.acceptance_triage as triage

        triage.SHA_CONTEXT_CHARS = window
        found = [mid for mid in sorted(missions)
                 if parse_commit_shas(mission_text(missions[mid]))]
        sensitivity.append({"window_chars": window, "missions_with_sha": len(found),
                            "distinct_shas": len({s for mid in found
                                                  for s in parse_commit_shas(mission_text(missions[mid]))})})
    import tools.acceptance_triage as triage

    triage.SHA_CONTEXT_CHARS = 24

    audit = {
        "source_artifacts": {
            "queue": "artifacts/acceptance_queue.json",
            "snapshot": "artifacts/acceptance_input_snapshot.json",
        },
        "queue_total": len(entries),
        "template_missions": {
            "n": len(template),
            "share": round(len(template) / max(len(entries), 1), 4),
            "with_criteria": sum(1 for mid in template if crit(mid)),
            "actions": {a: sum(1 for mid in template if act(mid, a))
                        for a in ("accept", "verify", "rerelease", "revoke")},
        },
        "engineering_missions": {
            "n": len(engineering),
            "ids": engineering,
            "with_criteria": sum(1 for mid in engineering if crit(mid)),
            "actions": {a: sum(1 for mid in engineering if act(mid, a))
                        for a in ("accept", "verify", "rerelease", "revoke")},
        },
        "criteria_manual_sample": {
            "seed": SAMPLE_SEED,
            "sample_size": len(sample),
            "ids": sample,
            "labels": {str(k): v for k, v in sorted(labels.items())},
            "agree": agree,
            "false_negative": miss,
            "miss_rate": round(miss / max(len(labels), 1), 4),
            "membership_check": "sample reproduced from random.Random(7).sample on mission ids",
        },
        "criteria_true_review": {
            "n_flagged": len(flagged),
            "ids": flagged,
            "reviewed": {str(k): v for k, v in sorted(reviewed.items())},
            "verdict_correct": sum(1 for v in reviewed.values() if v["verdict"] == "ok"),
            "rationale_precise": sum(1 for v in reviewed.values() if v["rationale"] == "ok"),
        },
        "sha_window_sensitivity": sensitivity,
        "limitations": [
            "manual labels come from reading the sampled goals' acceptance text "
            "(template missions share one body read once)",
            "sha tokens are parsed from evidence text, never resolved against git objects",
            "report bodies are fetched only when cheaper sources carry no sha",
        ],
    }
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({k: audit[k] for k in
                      ("queue_total", "template_missions", "engineering_missions",
                       "criteria_manual_sample", "criteria_true_review",
                       "sha_window_sensitivity")},
                     ensure_ascii=False, indent=1)[:2500])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())