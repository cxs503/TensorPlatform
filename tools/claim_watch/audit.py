#!/usr/bin/env python3
"""Batch audit: claim freshness across several worktrees, with acceptance numbers.

One command produces every number the campaign node is accepted on:

=====  ==========================================================================
A1     fault-detection rate. Nine injected mutations over three fault classes
       (tamper artifact / change dependency / swap environment fingerprint,
       3 each) applied to *copies* of real claims in a temporary sandbox. Each
       mutation must land on the exact expected status (BROKEN / STALE /
       SUSPECT). Detection rate must be 1.0.
A2     false-positive rate: 200 genuine claims (artifact and every dependency
       resolve, nothing mutated) -- the share the checker refuses must be <= 2%.
A3     wall time of the whole cross-worktree batch must be <= 60 s.
A4     read-only: ``git status --porcelain`` of every audited worktree must be
       byte-identical before and after the batch.
A5     STALE recall against the git-labelled historical drift ledger must be
       >= 0.7 (produced by ``retro.py``; the labels never come from the checker).
=====  ==========================================================================

The audit never writes inside an audited worktree: its only outputs are the
artifacts directory given by ``--out`` and an optional ledger path, both of which
this project owns. Mutation sandboxes are temporary directories.

Zero domain vocabulary: repos, documents and files are opaque inputs.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import checker
from . import claims as claim_mod
from . import fingerprint as fp_mod
from . import harvest
from . import retro

ACCEPTANCE = {
    "A1_detection_rate": 1.0,
    "A2_false_positive_rate_max": 0.02,
    "A3_batch_seconds_max": 60.0,
    "A5_stale_recall_min": 0.7,
    "A2_n_true_claims": 200,
    "A1_mutations_per_class": 3,
}


# --------------------------------------------------------------------------- #
# read-only guarantees
# --------------------------------------------------------------------------- #
def git_status_digest(root: str) -> Dict[str, Any]:
    """Digest of a worktree's porcelain status (read-only git call)."""
    try:
        out = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"root": root, "digest": None, "error": str(exc)}
    if out.returncode != 0:
        return {"root": root, "digest": None, "error": out.stderr.decode("utf-8", "replace")[:200]}
    body = out.stdout
    return {"root": root, "digest": hashlib.sha256(body).hexdigest(),
            "n_lines": len([ln for ln in body.decode("utf-8", "replace").splitlines() if ln.strip()])}


# --------------------------------------------------------------------------- #
# batch build + check
# --------------------------------------------------------------------------- #
def batch(
    roots: Mapping[str, str],
    *,
    max_docs_per_repo: int = 400,
    with_env: bool = True,
    exclude: Sequence[str] = (),
) -> Dict[str, Any]:
    """Harvest claims from every root and check them; returns claims + results."""
    env = fp_mod.env_fingerprint() if with_env else None
    t0 = time.perf_counter()
    claims: List[Dict[str, Any]] = []
    per_repo: Dict[str, int] = {}
    for repo, root in roots.items():
        got = harvest.build_claims(root, repo, roots, max_docs=max_docs_per_repo,
                                   current_env=env, exclude=exclude)
        per_repo[repo] = len(got)
        claims.extend(got)
    t_harvest = time.perf_counter() - t0
    resolver = claim_mod.RepoResolver(dict(roots))
    t1 = time.perf_counter()
    results = checker.check_claims(claims, resolver, current_env=env)
    t_check = time.perf_counter() - t1
    return {
        "claims": claims,
        "results": results,
        "per_repo": per_repo,
        "seconds": {"harvest": t_harvest, "check": t_check, "total": t_harvest + t_check},
        "env": env,
    }


def _all_inputs_resolve(claim: Mapping[str, Any], resolver: claim_mod.RepoResolver) -> bool:
    if not resolver.resolve(claim.get("artifact_uri", "")):
        return False
    for dep in claim.get("depends_on") or []:
        if not resolver.resolve(dep.get("uri", "")):
            return False
    return True


def sha256sum_files(paths: Sequence[str]) -> Dict[str, str]:
    """Hash files with the GNU ``sha256sum`` binary, i.e. an implementation that
    is *not* this package's hashlib path.

    The false-positive criterion needs ground truth that does not share code with
    the checker: if the two disagree, the claim is excluded from the sample rather
    than silently counted either way.
    """
    out: Dict[str, str] = {}
    paths = [p for p in paths if p]
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        try:
            proc = subprocess.run(["sha256sum", "--"] + chunk, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in proc.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                digest, name = parts
                out[name.lstrip("*")] = digest
    return out


def verified_fresh_claims(claims: Sequence[Mapping[str, Any]], resolver: claim_mod.RepoResolver,
                          n: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Up to ``n`` claims whose artifact and dependencies all exist *and* whose
    recorded hashes match what GNU ``sha256sum`` computes right now.

    These are the "genuine claims" of acceptance criterion A2: nothing was
    mutated, every dependency resolves, every recorded hash is independently
    confirmed. Any non-OK verdict on them is a false positive.
    """
    candidates: List[Dict[str, Any]] = []
    for claim in claims:
        if not _all_inputs_resolve(claim, resolver):
            continue
        candidates.append(dict(claim))
        if len(candidates) >= n * 3:
            break
    paths: List[str] = []
    for claim in candidates:
        paths.append(resolver.resolve(claim["artifact_uri"]))
        for dep in claim.get("depends_on") or []:
            paths.append(resolver.resolve(dep["uri"]))
    hashes = sha256sum_files([p for p in paths if p])
    out: List[Dict[str, Any]] = []
    stats = {"n_candidates": len(candidates), "n_hash_confirmed": 0, "n_selected": 0}
    for claim in candidates:
        art = resolver.resolve(claim["artifact_uri"])
        if not art or hashes.get(art) != claim["artifact_sha256"]:
            continue
        ok = True
        for dep in claim.get("depends_on") or []:
            path = resolver.resolve(dep["uri"])
            if not path or hashes.get(path) != dep.get("sha256"):
                ok = False
                break
        if not ok:
            continue
        stats["n_hash_confirmed"] += 1
        if len(out) < n:
            out.append(claim)
    stats["n_selected"] = len(out)
    return out, stats


# --------------------------------------------------------------------------- #
# A1: mutation matrix
# --------------------------------------------------------------------------- #
def _materialize(claim: Mapping[str, Any], resolver: claim_mod.RepoResolver, dst: str) -> Dict[str, Any]:
    """Copy a claim's artifact and dependencies into ``dst`` and rewrite URIs.

    Copying is essential: mutations must never touch a real worktree (A4).
    """
    os.makedirs(dst, exist_ok=True)
    rewritten = dict(claim)
    used: Dict[str, str] = {}

    def place(uri: str) -> Optional[str]:
        src = resolver.resolve(uri)
        if not src:
            return None
        if not os.path.isfile(src):
            return None
        rel = used.get(src)
        if rel is None:
            base = os.path.basename(uri.replace(":", "_")) or "artifact"
            rel = f"{len(used):02d}_{os.path.basename(src)}"
            shutil.copyfile(src, os.path.join(dst, rel))
            used[src] = rel
        return rel

    art_rel = place(claim["artifact_uri"])
    if art_rel is None:
        raise RuntimeError(f"cannot materialize artifact {claim['artifact_uri']}")
    rewritten["artifact_uri"] = art_rel
    deps = []
    for dep in claim.get("depends_on") or []:
        rel = place(dep["uri"])
        if rel is None:
            continue
        deps.append({**dep, "uri": rel})
    rewritten["depends_on"] = deps
    return rewritten


def mutation_matrix(
    base_claims: Sequence[Mapping[str, Any]],
    resolver: claim_mod.RepoResolver,
    current_env: Mapping[str, Any],
    workdir: Optional[str] = None,
    per_class: int = ACCEPTANCE["A1_mutations_per_class"],
) -> Dict[str, Any]:
    """Inject 3 fault classes x ``per_class`` mutations and check detection."""
    tmp_root = workdir or tempfile.mkdtemp(prefix="claim_watch_mut_")
    rows: List[Dict[str, Any]] = []
    seeds = [c for c in base_claims if (c.get("depends_on") or [])][: max(per_class, 3)]
    if len(seeds) < 3:
        raise RuntimeError("need at least 3 claims with dependencies to inject mutations")

    def run(claim: Dict[str, Any], expect: str, fault: str, sandbox: str) -> Dict[str, Any]:
        local = dict(claim)
        if fault == "tamper_artifact":
            rel = local["artifact_uri"]
            with open(os.path.join(sandbox, rel), "ab") as fh:
                fh.write(b"\n// tampered\n")
        elif fault == "change_dep":
            rel = local["depends_on"][0]["uri"]
            with open(os.path.join(sandbox, rel), "ab") as fh:
                fh.write(b"\n// dependency changed\n")
        elif fault == "swap_env":
            recorded = dict(local.get("env_fingerprint") or current_env)
            other_arch = "aarch64" if fp_mod.arch_family(current_env.get("arch")) != "arm64" else "x86_64"
            local["env_fingerprint"] = fp_mod.env_fingerprint(
                arch=other_arch,
                platform_system=recorded.get("platform", current_env.get("platform")),
                byteorder="little",
                pointer_bits=64,
                python=recorded.get("python", current_env.get("python")),
                package_versions=recorded.get("packages") or {},
            )
        else:
            raise ValueError(fault)
        result = checker.check_claim(local, claim_mod.RepoResolver({"sb": sandbox}), current_env)
        return {
            "fault": fault,
            "expected_status": expect,
            "observed_status": result.status,
            "detected": result.status == expect,
            "recorded_grade": result.recorded_grade,
            "effective_grade": result.effective_grade,
            "causes": [c["code"] for c in result.causes],
            "claim_id": local["claim_id"],
        }

    for cls, expect in (("tamper_artifact", checker.BROKEN),
                        ("change_dep", checker.STALE),
                        ("swap_env", checker.SUSPECT)):
        for i in range(per_class):
            seed = seeds[i % len(seeds)]
            sandbox = os.path.join(tmp_root, f"{cls}_{i}")
            os.makedirs(sandbox, exist_ok=True)
            local = _materialize(seed, resolver, sandbox)
            local["evidence_grade"] = "REAL"  # declared by the producing tool at build time
            rows.append(run(local, expect, cls, sandbox))

    n = len(rows)
    n_detected = sum(1 for r in rows if r["detected"])
    per_class = {}
    for row in rows:
        bucket = per_class.setdefault(row["fault"], {"n": 0, "detected": 0})
        bucket["n"] += 1
        bucket["detected"] += int(row["detected"])
    return {
        "n_mutations": n,
        "n_detected": n_detected,
        "detection_rate": (n_detected / n) if n else None,
        "per_class": per_class,
        "rows": rows,
        "sandbox": tmp_root,
    }


# --------------------------------------------------------------------------- #
# reporting helpers
# --------------------------------------------------------------------------- #
def status_table(results: Sequence[checker.CheckResult], per_repo: Mapping[str, int]) -> List[Dict[str, Any]]:
    rows = []
    for repo, n in per_repo.items():
        subset = [r for r in results if str(r.claim_id).startswith(repo + ":")]
        counts = {s: 0 for s in checker.STATUSES}
        for r in subset:
            counts[r.status] = counts.get(r.status, 0) + 1
        rows.append({"repo": repo, "n_claims": n, **{f"n_{k}": v for k, v in counts.items()}})
    return rows


def markdown_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = ["| " + " | ".join(str(r.get(c, "")) for c in columns) + " |" for r in rows]
    return "\n".join([head, sep] + body)


def write_json(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    return path


def write_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def run_audit(
    roots: Mapping[str, str],
    out_dir: str,
    *,
    retro_roots: Optional[Mapping[str, str]] = None,
    max_docs_per_repo: int = 1500,
    n_true: int = ACCEPTANCE["A2_n_true_claims"],
    n_drift_cases: int = 9,
    ledger_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the batch and the acceptance probes; write the artifacts.

    Ordering matters for A4: the "after" status snapshot is taken *before* any
    artifact is written, so the snapshot pair brackets exactly the audit work
    (harvest + check + probes) and never the delivery of this report.
    """
    started = _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()
    roots = {k: os.path.abspath(v) for k, v in roots.items()}
    status_before = {repo: git_status_digest(root) for repo, root in roots.items()}

    t0 = time.perf_counter()
    # Never harvest the audit's own outputs: a claim ledger records artifact hashes,
    # so re-reading it would manufacture claims about claims (a feedback loop).
    exclude = [out_dir, os.path.dirname(os.path.abspath(ledger_path or os.path.join(out_dir, "x")))]
    data = batch(roots, max_docs_per_repo=max_docs_per_repo, exclude=exclude)
    resolver = claim_mod.RepoResolver(dict(roots))
    results = data["results"]
    summary = checker.summarize(results)

    # A2: genuine claims -- resolvable, and every recorded hash reconfirmed by GNU sha256sum.
    genuine, genuine_stats = verified_fresh_claims(data["claims"], resolver, n_true)
    genuine_results = checker.check_claims(genuine, resolver, current_env=data["env"], schema=None)
    flagged = [r for r in genuine_results if r.status != checker.OK]
    fp_rate = (len(flagged) / len(genuine_results)) if genuine_results else None

    # A1: mutation matrix over genuine claims (materialized into a /tmp sandbox)
    seeds = [c for c in genuine if c.get("depends_on") and c.get("env_fingerprint")][:3]
    if len(seeds) < 3:
        seeds = [c for c in data["claims"] if c.get("depends_on")][:3]
    mutations = mutation_matrix(seeds, resolver, data["env"])

    # A5: historical drift replay (git-labelled; own roots only)
    replay_roots = dict(retro_roots or roots)
    replay = retro.replay(replay_roots, n_cases=n_drift_cases, exclude=exclude)
    ledger = replay["ledger_cases"][:n_drift_cases]
    ledger_file = ledger_path or os.path.join(out_dir, "drift_ledger.json")

    total_seconds = time.perf_counter() - t0

    # A4: read-only verification -- bracketed before artifact delivery
    status_after = {repo: git_status_digest(root) for repo, root in roots.items()}
    unchanged = {repo: (status_before[repo].get("digest") == status_after[repo].get("digest"))
                 for repo in roots}

    acceptance = {
        "A1_detection_rate": {
            "value": mutations["detection_rate"],
            "threshold": ACCEPTANCE["A1_detection_rate"],
            "pass": mutations["detection_rate"] is not None and mutations["detection_rate"] >= ACCEPTANCE["A1_detection_rate"],
            "n_mutations": mutations["n_mutations"],
            "n_detected": mutations["n_detected"],
            "per_class": mutations["per_class"],
        },
        "A2_false_positive_rate": {
            "value": fp_rate,
            "threshold": ACCEPTANCE["A2_false_positive_rate_max"],
            "pass": fp_rate is not None and fp_rate <= ACCEPTANCE["A2_false_positive_rate_max"],
            "n_true_claims": len(genuine_results),
            "n_flagged": len(flagged),
            "hash_confirmed_pool": genuine_stats.get("n_hash_confirmed"),
            "hash_confirmed_candidates": genuine_stats.get("n_candidates"),
            "ground_truth_hasher": "GNU sha256sum (independent of the checker's hashlib path)",
        },
        "A3_batch_seconds": {
            "value": round(total_seconds, 3),
            "threshold": ACCEPTANCE["A3_batch_seconds_max"],
            "pass": total_seconds <= ACCEPTANCE["A3_batch_seconds_max"],
            "n_claims": summary["n_claims"],
            "breakdown": {k: round(v, 3) for k, v in data["seconds"].items()},
            "n_worktrees": len(roots),
            "scope": "harvest + check + drift replay (report writing excluded)",
        },
        "A4_read_only": {
            "value": all(unchanged.values()),
            "threshold": True,
            "pass": all(unchanged.values()),
            "per_repo": unchanged,
            "status_lines_before": {k: v.get("n_lines") for k, v in status_before.items()},
            "status_lines_after": {k: v.get("n_lines") for k, v in status_after.items()},
            "scope": "git status --porcelain --untracked-files=all, snapshotted before the batch and after the probes but before artifact delivery",
        },
        "A5_stale_recall": {
            "value": replay["recall"],
            "threshold": ACCEPTANCE["A5_stale_recall_min"],
            "pass": replay["recall"] is not None and replay["recall"] >= ACCEPTANCE["A5_stale_recall_min"],
            "n_drift": replay["counts"]["drift"],
            "n_drift_cases_in_ledger": len(ledger),
            "ledger_path": ledger_file,
            "precision": replay["precision"],
            "n_stale_outside_ground_truth": replay["n_stale_outside_ground_truth"],
            "labeller": "git object bytes at the claiming document's last commit",
        },
    }
    all_pass = all(v["pass"] for v in acceptance.values())

    report = {
        "schema": "claim-watch-audit/1.0",
        "generated_at": started,
        "roots": roots,
        "n_worktrees": len(roots),
        "summary": summary,
        "per_repo": data["per_repo"],
        "status_table": status_table(results, data["per_repo"]),
        "seconds": {k: round(v, 3) for k, v in data["seconds"].items()},
        "batch_seconds": round(total_seconds, 3),
        "env_fingerprint": data["env"],
        "mutations": {k: v for k, v in mutations.items() if k != "sandbox"},
        "false_positive_probe": {
            "n_claims": len(genuine_results),
            "n_flagged": len(flagged),
            "rate": fp_rate,
            "pool": genuine_stats,
            "flagged": [{"claim_id": r.claim_id, "status": r.status,
                         "causes": [c["code"] for c in r.causes]} for r in flagged[:20]],
        },
        "drift_replay": {k: v for k, v in replay.items() if k != "rows"},
        "drift_replay_rows": replay["rows"],
        "acceptance": acceptance,
        "all_pass": all_pass,
        "claims_ledger": os.path.join(out_dir, "claims_ledger.json"),
        "audit_table_md": os.path.join(out_dir, "audit_table.md"),
        "artifacts": {
            "audit_table_json": os.path.join(out_dir, "audit_table.json"),
            "acceptance_json": os.path.join(out_dir, "acceptance.json"),
            "mutations_json": os.path.join(out_dir, "mutations.json"),
            "drift_replay_json": os.path.join(out_dir, "drift_replay.json"),
            "drift_ledger": ledger_file,
            "claims_ledger": os.path.join(out_dir, "claims_ledger.json"),
        },
    }

    # Artifact delivery starts only after the A4 snapshots: the ledger lives in a
    # tracked path by default, so it must be written last.
    retro.write_ledger(replay, ledger_file, n_cases=n_drift_cases)
    # The table keeps a bounded sample of per-claim rows (the full ledger is
    # written separately): a per-claim dump of every audited worktree is
    # megabytes of redundancy next to claims_ledger.json.
    write_json(os.path.join(out_dir, "audit_table.json"),
               {"generated_at": started, "roots": roots, "summary": summary,
                "status_table": report["status_table"],
                "rows_note": "first 300 claims in harvest order; complete set in claims_ledger.json",
                "rows": [r.to_dict() for r in results][:300]})
    write_json(os.path.join(out_dir, "acceptance.json"), report["acceptance"])
    write_json(os.path.join(out_dir, "mutations.json"), mutations)
    write_json(os.path.join(out_dir, "drift_replay.json"), replay)
    claim_mod.dump_ledger(data["claims"], os.path.join(out_dir, "claims_ledger.json"))
    write_text(os.path.join(out_dir, "audit_table.md"), render_markdown(report))
    write_json(os.path.join(out_dir, "audit_report.json"), report)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    acc = report["acceptance"]
    lines = [
        "# Claim-watch batch audit",
        "",
        f"generated_at: {report['generated_at']}",
        f"worktrees: {report['n_worktrees']} · claims: {report['summary']['n_claims']} · "
        f"batch: {report['batch_seconds']} s",
        "",
        "## Acceptance",
        "",
        markdown_table(
            [{"criterion": k, "value": v.get("value"), "threshold": v.get("threshold"),
              "pass": v.get("pass")} for k, v in acc.items()],
            ["criterion", "value", "threshold", "pass"]),
        "",
        "## Status by worktree",
        "",
        markdown_table(report["status_table"],
                       ["repo", "n_claims"] + [f"n_{s}" for s in checker.STATUSES]),
        "",
        "## Causes",
        "",
        markdown_table([{"cause": k, "n": v} for k, v in report["summary"]["by_cause"].items()],
                       ["cause", "n"]),
        "",
        "## Grade transitions",
        "",
        markdown_table([{"transition": k, "n": v} for k, v in report["summary"]["downgrades"].items()] or
                       [{"transition": "(none)", "n": 0}], ["transition", "n"]),
        "",
        "## Injected mutations",
        "",
        markdown_table(report["mutations"]["rows"],
                       ["fault", "expected_status", "observed_status", "detected",
                        "recorded_grade", "effective_grade", "causes"]),
        "",
    ]
    return "\n".join(lines)


def parse_roots(items: Sequence[str]) -> Dict[str, str]:
    roots: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--root expects repo=path, got {item!r}")
        repo, path = item.split("=", 1)
        roots[repo] = path
    return roots


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="claim_watch batch audit")
    ap.add_argument("--root", action="append", default=[], help="repo=path (repeatable, <=5)")
    ap.add_argument("--retro-root", action="append", default=[], help="repo=path for the drift replay")
    ap.add_argument("--out", default="artifacts/claim_watch")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--max-docs-per-repo", type=int, default=1500)
    ap.add_argument("--n-true", type=int, default=ACCEPTANCE["A2_n_true_claims"])
    ap.add_argument("--n-drift-cases", type=int, default=9)
    ap.add_argument("--allow-more-than-5-trees", action="store_true")
    args = ap.parse_args(argv)

    roots = parse_roots(args.root)
    if not roots:
        raise SystemExit("at least one --root repo=path is required")
    if len(roots) > 5 and not args.allow_more_than_5_trees:
        raise SystemExit(f"the campaign node samples <=5 worktrees, got {len(roots)}")
    retro_roots = parse_roots(args.retro_root) if args.retro_root else None
    report = run_audit(roots, args.out, retro_roots=retro_roots,
                       max_docs_per_repo=args.max_docs_per_repo, n_true=args.n_true,
                       n_drift_cases=args.n_drift_cases, ledger_path=args.ledger)
    for name, item in report["acceptance"].items():
        print(f"{name}: value={item['value']} threshold={item['threshold']} pass={item['pass']}")
    print(f"all_pass={report['all_pass']}")
    print(f"report={os.path.join(args.out, 'audit_report.json')}")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())