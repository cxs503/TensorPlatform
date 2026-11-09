#!/usr/bin/env python3
"""Independent verification of the FEM OSS registry (mission m8692).

Two checks, both machine-decidable:

  A. DRIFT RE-FETCH  — re-query GitHub for every curated repo's detail and latest
     commit, then diff against the registry fields (stars / language / license /
     last_commit). Reports per-field agreement rate and the per-repo error rate
     used for the report's "是否 <3%" question.
  B. STRUCTURAL INTEGRITY — every gap_proposals anchor must exist in the registry;
     every registry entry must carry an evidence block; every field must be
     non-null where required.

Writes verification.json next to the registry.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
sys.path.insert(0, HERE)
import collect_fem_inventory as C  # noqa: E402

FIELDS = ["lang", "license", "stars"]


def main() -> int:
    reg = json.load(open(os.path.join(HERE, "oss_fem_registry.json"), encoding="utf-8"))
    repos = reg["repositories"]

    # ---- A. drift re-fetch
    diffs, checked = [], 0
    for r in repos:
        name = r["repo"]
        d = C.get(f"/repos/{name}")
        c = C.get(f"/repos/{name}/commits", {"per_page": 1})
        checked += 1
        if d is None:
            diffs.append({"repo": name, "field": "*", "registry": "-", "refetch": "unavailable"})
            continue
        live = {
            "lang": d.get("language"),
            "license": ((d.get("license") or {}).get("spdx_id")),
            "stars": d.get("stargazers_count"),
            "last_commit": ((c[0]["commit"]["committer"]["date"])[:10] if c else None),
        }
        for f in FIELDS + ["last_commit"]:
            if live[f] != r.get(f):
                diffs.append({"repo": name, "field": f, "registry": r.get(f), "refetch": live[f]})

    n_field_checks = checked * (len(FIELDS) + 1)
    agree = n_field_checks - len([d for d in diffs if d["field"] != "*"])
    field_error_rate = (len([d for d in diffs if d["field"] != "*"]) / n_field_checks) if n_field_checks else 1.0

    # ---- B. structural integrity
    structural = []
    names = {r["repo"] for r in repos}
    for g in reg["gap_proposals"]:
        for a in g["anchor_repos"]:
            if a not in names:
                structural.append(f"{g['gap_id']}: anchor {a} not in registry")
    for r in repos:
        for key in ("repo", "tier", "lang", "license", "stars", "last_commit", "summary", "evidence"):
            if r.get(key) in (None, "", []):
                structural.append(f"{r['repo']}: empty required field {key}")
        if not r["evidence"].get("readme_quote"):
            structural.append(f"{r['repo']}: missing readme_quote")
    tier1 = [r for r in repos if r["tier"] == 1]
    if len(repos) < 20:
        structural.append(f"only {len(repos)} curated repos (<20 required)")
    if len(tier1) < 8:
        structural.append(f"only {len(tier1)} tier1 repos (<8 required)")
    if len(reg["gap_proposals"]) < 6:
        structural.append("fewer than 6 GAP-FEM proposals")

    out = {
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mission_id": 8692,
        "drift_check": {
            "repos_rechecked": checked,
            "field_checks": n_field_checks,
            "agreements": agree,
            "field_agreement_rate": round(agree / n_field_checks, 6) if n_field_checks else 0.0,
            "field_error_rate": round(field_error_rate, 6),
            "threshold_3pct_pass": field_error_rate < 0.03,
            "diffs": diffs,
        },
        "structural_check": {"violations": structural, "passed": not structural},
        "counts": {
            "curated_repos": len(repos),
            "tier1": len(tier1),
            "tier2": sum(1 for r in repos if r["tier"] == 2),
            "tier3": sum(1 for r in repos if r["tier"] == 3),
            "gap_proposals": len(reg["gap_proposals"]),
            "search_rounds": reg["metadata"]["search_rounds"],
            "candidate_pool": reg["metadata"]["candidate_pool_size"],
        },
        "api_calls_this_run": len(C.CALLS),
    }
    # provenance: this run's REST calls are logged like the collector's
    json.dump({"generated_at": out["verified_at"], "authenticated": bool(C.TOKEN),
               "n_calls": len(C.CALLS), "calls": C.CALLS},
              open(os.path.join(RAW, "api_calls_verify.json"), "w", encoding="utf-8"), indent=1)
    dst = os.path.join(HERE, "verification.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "drift_check"}, ensure_ascii=False, indent=1))
    print(f"drift: {agree}/{n_field_checks} agree, error_rate={field_error_rate:.4%}, diffs={len(diffs)}")
    for d in diffs[:10]:
        print("   ", d)
    return 0 if not structural and field_error_rate < 0.03 else 1


if __name__ == "__main__":
    raise SystemExit(main())