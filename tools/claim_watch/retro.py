#!/usr/bin/env python3
"""Historical drift replay: measure STALE recall against an independently labelled ledger.

The acceptance criterion for this campaign node is not "our checker is
self-consistent"; it is *recall against drift that already happened*:

    take N recorded (document -> artifact) assertions from real repositories,
    label each one with git — the artifact's bytes as of the commit that last
    touched the claiming document vs its bytes now — and ask how many of the
    genuinely drifted ones the checker flags STALE.

Why git is the right labeller: it is an independent implementation of content
identity (git object hashing over a different code path) and an independent
source of truth (the committed state, not the working tree). If the two agree,
the result is not a tautology of one hashing routine.

Three-way labelling (all reported, none hidden)
-----------------------------------------------
``drift``            bytes of the dependency at the claiming document's last
                     commit differ from the bytes now: the claim was true when
                     written and is not true now.
``stale_at_birth``   bytes now equal bytes at the claiming commit, but the hash
                     the document recorded never matched them: a pre-existing
                     defect, not drift. The checker flags STALE here too, and
                     that is correct behaviour — but it must not be counted as a
                     recall hit, so the two categories are kept apart.
``consistent``       recorded hash equals current bytes.

Recall is therefore ``|STALE ∩ drift| / |drift|``; the ledger is written next to
the report so anyone can recompute it. The ledger is built by this module and the
checker never reads its labels: the only shared input is the claim record.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import checker
from . import claims as claim_mod
from . import harvest


def _git(root: str, *args: str, timeout: int = 30) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def last_commit(root: str, relpath: str) -> Optional[Tuple[str, int]]:
    """(commit sha, unix commit time) of the last commit touching ``relpath``."""
    out = _git(root, "log", "-1", "--format=%H\t%ct", "--", relpath)
    if not out:
        return None
    line = out.strip().splitlines()[0] if out.strip() else ""
    if "\t" not in line:
        return None
    sha, ts = line.split("\t", 1)
    try:
        return sha, int(ts)
    except ValueError:
        return None


def blob_at(root: str, commit: str, relpath: str) -> Optional[bytes]:
    """Bytes of ``relpath`` as committed in ``commit`` (None if absent)."""
    try:
        out = subprocess.run(["git", "-C", root, "show", f"{commit}:{relpath}"],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _rel_in_root(root: str, path: str) -> Optional[str]:
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    if not path.startswith(root + os.sep):
        return None
    return os.path.relpath(path, root)


def collect_candidates(
    roots: Mapping[str, str],
    *,
    max_docs_per_repo: int = 60,
    max_candidates: int = 200,
    exclude: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Recorded assertions whose dependency lives inside the same git worktree.

    Repositories are interleaved round-robin so the replay is not dominated by
    whichever root happens to be listed first.
    """
    per_repo: Dict[str, List[Dict[str, Any]]] = {}
    for repo, root in roots.items():
        if not os.path.exists(os.path.join(root, ".git")):
            # .git is a directory in a normal clone and a file in a linked worktree
            continue
        resolver = claim_mod.RepoResolver({repo: root})
        rows: List[Dict[str, Any]] = []
        for pair in harvest.scan_pairs(root, repo, roots, max_docs=max_docs_per_repo,
                                       exclude=exclude):
            path = resolver.resolve(pair["uri"])
            if not path:
                continue
            dep_rel = _rel_in_root(root, path)
            if dep_rel is None:
                continue
            rows.append({**pair, "root": root, "dep_rel": dep_rel})
            if len(rows) >= max_candidates:
                break
        per_repo[repo] = rows
    out: List[Dict[str, Any]] = []
    queues = [list(rows) for rows in per_repo.values() if rows]
    while queues and len(out) < max_candidates:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            out.append(queue.pop(0))
            if len(out) >= max_candidates:
                break
    return out


def label_case(case: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach git-based ground truth to one candidate case."""
    root = case["root"]
    doc_rel = case["document"]
    doc_commit = last_commit(root, doc_rel)
    out = dict(case)
    out["doc_commit"] = doc_commit[0] if doc_commit else None
    out["doc_commit_time"] = (
        _dt.datetime.fromtimestamp(doc_commit[1], tz=_dt.timezone.utc).isoformat() if doc_commit else None
    )
    dep_now = case.get("dep_rel")
    current_bytes = None
    current_path = os.path.join(root, dep_now) if dep_now else None
    if current_path and os.path.exists(current_path):
        try:
            with open(current_path, "rb") as fh:
                current_bytes = fh.read()
        except OSError:
            current_bytes = None
    if current_bytes is None:
        out.update(label="missing_dep", current_sha256=None, at_doc_commit_sha256=None)
        return out
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    at_commit_sha = None
    if doc_commit and dep_now:
        blob = blob_at(root, doc_commit[0], dep_now)
        if blob is not None:
            at_commit_sha = hashlib.sha256(blob).hexdigest()
    out["current_sha256"] = current_sha
    out["at_doc_commit_sha256"] = at_commit_sha

    if at_commit_sha is None:
        # Dependency did not exist at the claiming commit: it appeared later.
        label = "drift" if case["sha256"] != current_sha else "consistent"
    elif at_commit_sha != current_sha:
        label = "drift"
    elif case["sha256"] != current_sha:
        label = "stale_at_birth"
    else:
        label = "consistent"
    out["label"] = label
    return out


def case_claim(case: Mapping[str, Any]) -> Dict[str, Any]:
    """The claim a checking engine would have stored for this assertion."""
    return claim_mod.make_claim(
        claim_id=f"{case['repo']}:{case['document']}#{os.path.basename(str(case['uri']))}",
        value=f"sha256:{case['sha256'][:16]}",
        unit="digest",
        artifact_uri=f"{case['repo']}:{case['document']}",
        artifact_sha256=claim_mod.sha256_file(os.path.join(case["root"], case["document"])) or "",
        verified_at=case.get("verified_at") or _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        evidence_grade="REAL",
        env_fingerprint=None,
        depends_on=[{"uri": case["uri"], "sha256": case["sha256"], "kind": "artifact",
                     "role": "recorded-by-document"}],
        producer=case["document"],
        notes="historical replay case (git-labelled)",
    )


def replay(
    roots: Mapping[str, str],
    *,
    n_cases: int = 9,
    max_docs_per_repo: int = 400,
    max_candidates: int = 200,
    min_labelled: Optional[int] = None,
    exclude: Sequence[str] = (),
) -> Dict[str, Any]:
    """Run the checker over git-labelled historical assertions and score it.

    Labelling stops as soon as ``n_cases`` genuine drift cases are found *and* at
    least ``min_labelled`` (default ``2 * n_cases``) cases have been labelled, so
    the sample keeps a healthy share of non-drift controls without spending git
    calls on the whole corpus.
    """
    candidates = collect_candidates(roots, max_docs_per_repo=max_docs_per_repo,
                                    max_candidates=max_candidates, exclude=exclude)
    need = min_labelled if min_labelled is not None else max(2 * n_cases, n_cases)
    labelled: List[Dict[str, Any]] = []
    n_drift = 0
    for case in candidates:
        row = label_case(case)
        labelled.append(row)
        n_drift += int(row["label"] == "drift")
        if n_drift >= n_cases and len(labelled) >= need:
            break

    resolver = claim_mod.RepoResolver(dict(roots))
    rows: List[Dict[str, Any]] = []
    for case in labelled:
        claim = case_claim(case)
        result = checker.check_claim(claim, resolver, current_env=None)
        rows.append({
            "claim_id": claim["claim_id"],
            "repo": case["repo"],
            "document": case["document"],
            "dependency": case["uri"],
            "label": case["label"],
            "doc_commit": case.get("doc_commit"),
            "doc_commit_time": case.get("doc_commit_time"),
            "recorded_sha256": case["sha256"],
            "current_sha256": case.get("current_sha256"),
            "at_doc_commit_sha256": case.get("at_doc_commit_sha256"),
            "status": result.status,
            "recorded_grade": result.recorded_grade,
            "effective_grade": result.effective_grade,
            "causes": [c["code"] for c in result.causes],
        })

    drift = [r for r in rows if r["label"] == "drift"]
    stale = [r for r in rows if r["status"] == checker.STALE]
    hits = [r for r in drift if r["status"] == checker.STALE]
    birth = [r for r in rows if r["label"] == "stale_at_birth"]
    consistent = [r for r in rows if r["label"] == "consistent"]
    missing = [r for r in rows if r["label"] == "missing_dep"]

    recall = (len(hits) / len(drift)) if drift else None
    not_true_now = set(id(r) for r in drift) | set(id(r) for r in birth) | set(id(r) for r in missing)
    stale_outside = [r for r in stale if id(r) not in not_true_now]
    precision = (len(stale) - len(stale_outside)) / len(stale) if stale else None

    ordered = drift + birth + consistent + missing
    selected = ordered[:max(n_cases, len(drift))]

    return {
        "schema": "claim-watch-drift-replay/1.0",
        "generated_at": _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "n_candidates_available": len(candidates),
        "n_candidates": len(rows),
        "counts": {
            "drift": len(drift),
            "stale_at_birth": len(birth),
            "consistent": len(consistent),
            "missing_dep": len(missing),
            "stale_flagged": len(stale),
        },
        "recall": recall,
        "precision": precision,
        "n_stale_outside_ground_truth": len(stale_outside),
        "rows": rows,
        "ledger_cases": [
            {k: r[k] for k in ("claim_id", "repo", "document", "dependency", "label",
                               "doc_commit", "doc_commit_time", "recorded_sha256",
                               "current_sha256", "at_doc_commit_sha256", "status",
                               "recorded_grade", "effective_grade")}
            for r in selected
        ],
        "env_note": "env fingerprints are unasserted for historical records; SUSPECT is not exercised here",
    }


def write_ledger(report: Mapping[str, Any], path: str, n_cases: int = 9) -> int:
    cases = report.get("ledger_cases") or []
    cases = cases[:n_cases] if len(cases) >= n_cases else cases
    payload = {
        "schema": "claim-watch-drift-ledger/1.0",
        "purpose": "Git-labelled drift cases used for the STALE-recall acceptance criterion (A5). "
                   "Labels come from git object bytes at the claiming document's last commit, "
                   "not from the checker under test.",
        "generated_at": report.get("generated_at"),
        "n_cases": len(cases),
        "recall": report.get("recall"),
        "counts": report.get("counts"),
        "cases": cases,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return len(cases)