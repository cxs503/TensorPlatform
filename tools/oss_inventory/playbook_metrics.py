#!/usr/bin/env python3
"""Five-step health metrics for the OSS -> knowledge -> improvement loop.

The playbook (``docs/oss-to-tk-playbook.md``) defines five steps and three
headline indicators.  This tool turns them into numbers plus pass/fail gates so
"is the loop healthy?" stops being an opinion.

Indicators (as required by FEAT-OSS-PLAYBOOK)
---------------------------------------------
* **知识条数** — how many repos were registered, how many were actually deep
  dived, and how many knowledge rows came out (and how many rows per repo).
* **按锚点消费次数** — for every ``GAP-*`` anchor: how many downstream units
  (missions) reference it by name.
* **改进实证数** — how many downstream units demand an explicit consumption
  chain (their goal cites concrete ``oss_solutions`` rows), i.e. improvements
  that are traceable back to a specific upstream entry.

Gates are intentionally conservative and printed as PASS/FAIL; ``--strict``
makes a failing gate exit 1 so a wave cannot be declared green on vibes.

Read-only: the tool only runs ``SELECT`` statements.  Domain vocabulary never
appears here — the anchor prefix comes from the command line.

CLI
---
    python -m tools.oss_inventory.playbook_metrics --json-out artifacts/x.json
    python -m tools.oss_inventory.playbook_metrics --anchor-prefix GAP- --strict
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter

SEP = "\x1f"
DEFAULT_PSQL = "psql -h /var/run/postgresql -U foundation -d tensorplatform -X -q -tA"
POINTER_RE = re.compile(r"[\w./\-]+\.[A-Za-z0-9_]+:\d+(?:-\d+)?")
REPORT_ONLY_RE = re.compile(r"^report \d+$")

#: Gate thresholds.  Rationale documented in the playbook §6.
GATES = {
    "deep_dive_ratio": 0.70,      # >=70% of registered repos must be deep dived
    "anchored_ratio": 0.50,       # >=50% of knowledge rows carry an anchor slug
    "pointer_ratio": 0.80,        # >=80% of rows must have file:line pointers
    "consumed_anchor_ratio": 0.30,  # >=30% of anchors must be consumed downstream
}


def classify_slug(slug: str, anchor_prefix: str = "GAP-") -> str:
    s = (slug or "").strip()
    if not s:
        return "missing"
    if anchor_prefix and s.startswith(anchor_prefix) and len(s) > len(anchor_prefix):
        return "gap"
    if s.isascii() and re.match(r"^[A-Za-z0-9][\w:.\-/]*$", s):
        return "namespaced"
    return "free-label"


def compute_metrics(state: dict, anchor_prefix: str = "GAP-") -> dict:
    """Pure metric computation.

    ``state`` = {"inventory": [{repo,tier,status}], "solutions":
    [{repo,slug,files,evidence}], "goals": [{id,goal}]}
    """
    inv = state.get("inventory", [])
    sols = state.get("solutions", [])
    goals = state.get("goals", [])

    sol_by_repo = Counter(s.get("repo", "") for s in sols)
    tier_of = {i["repo"]: i.get("tier") for i in inv}
    deep = [i["repo"] for i in inv if sol_by_repo.get(i["repo"], 0) > 0]
    zero = sorted(i["repo"] for i in inv if sol_by_repo.get(i["repo"], 0) == 0)

    slug_classes = Counter(classify_slug(s.get("slug", ""), anchor_prefix) for s in sols)
    pointer_ok = sum(1 for s in sols if POINTER_RE.search(s.get("files", "") or ""))
    evidence_line = sum(1 for s in sols
                        if (s.get("evidence") or "") and not REPORT_ONLY_RE.match(
                            (s.get("evidence") or "").strip()))

    anchors = sorted({s.get("slug", "") for s in sols
                      if classify_slug(s.get("slug", ""), anchor_prefix) == "gap"})
    # consumption: a downstream unit "consumes" an anchor when its goal text
    # names the anchor verbatim (this is the machine-checkable part of §5).
    consumption = {}
    for a in anchors:
        hits = [g.get("id") for g in goals if a in (g.get("goal") or "")]
        if hits:
            consumption[a] = len(hits)
    chain_units = [g.get("id") for g in goals
                   if "oss_solutions" in (g.get("goal") or "")]

    n_inv, n_sol = len(inv), len(sols)
    metrics = {
        "step1_inventory": {
            "repos_registered": n_inv,
            "by_tier": dict(sorted(Counter(i.get("tier") for i in inv).items(),
                                   key=lambda kv: (kv[0] is None, kv[0]))),
            "repos_deep_dived": len(deep),
            "deep_dive_ratio": round(len(deep) / n_inv, 4) if n_inv else 0.0,
            "repos_zero_solution": zero,
        },
        "step3_knowledge": {
            "rows": n_sol,
            "rows_per_repo": round(n_sol / n_inv, 2) if n_inv else 0.0,
            "distinct_slugs": len({s.get("slug", "") for s in sols}),
            "slug_class": dict(slug_classes),
            "anchored_ratio": round(slug_classes.get("gap", 0) / n_sol, 4) if n_sol else 0.0,
            "pointer_ratio": round(pointer_ok / n_sol, 4) if n_sol else 0.0,
            "evidence_direct_ratio": round(evidence_line / n_sol, 4) if n_sol else 0.0,
        },
        "step5_consumption": {
            "anchors": anchors,
            "consumption_by_anchor": consumption,
            "consumed_anchor_ratio": round(len(consumption) / len(anchors), 4)
            if anchors else 0.0,
            "consumption_units": chain_units,
            "improvement_evidence_count": len(chain_units),
        },
    }
    metrics["gates"] = evaluate_gates(metrics)
    return metrics


def evaluate_gates(metrics: dict) -> dict:
    s1, s3, s5 = (metrics["step1_inventory"], metrics["step3_knowledge"],
                  metrics["step5_consumption"])
    checks = {
        "deep_dive_ratio": s1["deep_dive_ratio"],
        "anchored_ratio": s3["anchored_ratio"],
        "pointer_ratio": s3["pointer_ratio"],
        "consumed_anchor_ratio": s5["consumed_anchor_ratio"],
    }
    return {k: {"value": v, "threshold": GATES[k], "pass": v >= GATES[k]}
            for k, v in checks.items()}


def gates_ok(metrics: dict) -> bool:
    return all(g["pass"] for g in metrics["gates"].values())


def fetch_state(psql: str = DEFAULT_PSQL) -> dict:
    """Read-only pull of the three inputs the metrics need."""
    def q(sql: str) -> list[list[str]]:
        out = subprocess.run(f"{psql} -F {SEP} -c {json.dumps(sql)}", shell=True,
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"psql failed: {out.stderr.strip()[:400]}")
        rows = []
        for line in out.stdout.splitlines():
            if line == "":
                continue
            rows.append(line.split(SEP))
        return rows

    def flat(expr: str) -> str:
        """Collapse newlines so psql's line-oriented -tA output stays one row."""
        return f"regexp_replace(coalesce({expr}::text, ''), E'[\\n\\r\\t]+', ' ', 'g')"

    def sql_rows(sql: str, ncols: int) -> list[list[str]]:
        return [r for r in q(sql) if len(r) == ncols]

    inventory = [{"repo": r[0], "tier": int(r[1]) if r[1].strip().isdigit() else None,
                  "status": r[2]}
                 for r in sql_rows(
                     f"select {flat('repo')}, {flat('tier')}, {flat('status')} "
                     "from oss_inventory order by repo", 3)]
    solutions = [{"repo": r[0], "slug": r[1], "files": r[2], "evidence": r[3]}
                 for r in sql_rows(
                     f"select {flat('repo')}, {flat('feature_slug')}, "
                     f"{flat('files')}, {flat('evidence')} "
                     "from oss_solutions order by id", 4)]
    goals = [{"id": int(r[0]), "goal": (r[1] or "").replace(SEP, " ")}
             for r in sql_rows(
                 f"select {flat('id')}, left({flat('goal')}, 4000) "
                 "from tensor_dev_missions where goal is not null order by id", 2)]
    return {"inventory": inventory, "solutions": solutions, "goals": goals}


def render(metrics: dict) -> str:
    s1, s3, s5 = (metrics["step1_inventory"], metrics["step3_knowledge"],
                  metrics["step5_consumption"])
    lines = [
        f"step1 知识条数      repos={s1['repos_registered']} deep={s1['repos_deep_dived']}"
        f" ({s1['deep_dive_ratio']:.0%}) zero={len(s1['repos_zero_solution'])}"
        f" by_tier={s1['by_tier']}",
        f"step3 知识行        rows={s3['rows']} per_repo={s3['rows_per_repo']}"
        f" slugs={s3['distinct_slugs']} class={s3['slug_class']}",
        f"      证据强度      pointer={s3['pointer_ratio']:.0%}"
        f" evidence_direct={s3['evidence_direct_ratio']:.0%}",
        f"step5 按锚点消费     anchors={len(s5['anchors'])}"
        f" consumed={len(s5['consumption_by_anchor'])}"
        f" ratio={s5['consumed_anchor_ratio']:.0%}",
        f"      改进实证数    units={s5['improvement_evidence_count']}"
        f" ids={s5['consumption_units'][:8]}",
    ]
    for k, g in metrics["gates"].items():
        lines.append(f"gate {k:24s} {g['value']:.2f} >= {g['threshold']:.2f} "
                     f"{'PASS' if g['pass'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--psql", default=DEFAULT_PSQL)
    ap.add_argument("--anchor-prefix", default="GAP-")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--from-json", default=None,
                    help="compute from a saved state file instead of the DB")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    state = (json.load(open(args.from_json)) if args.from_json
             else fetch_state(args.psql))
    metrics = compute_metrics(state, args.anchor_prefix)
    print(render(metrics))
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(metrics, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json_out}")
    return 1 if (args.strict and not gates_ok(metrics)) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())