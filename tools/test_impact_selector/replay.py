"""Historical replay over real commits of the working tree.

What this can and cannot prove (honesty matters here)
----------------------------------------------------
A historical commit at ``HEAD`` cannot be *re-executed* cheaply: the full suite
of this repository is minutes long and partly uncollectable in the CI sandbox.
So the replay produces two kinds of evidence, clearly separated:

* **real-failure recall** -- comes from :mod:`proof_injection`, which performs
  actual pytest runs with actual injected defects (>=30 scenarios);
* **historical replay** (here) -- re-derives the selector's decision for >=30
  real commits and checks *non-tautological obligations* that a sound selector
  must satisfy, plus the size / fail-closed statistics:

  - ``direct``     : a test file modified by the commit must be selected,
  - ``literal``    : a test whose source mentions the changed module (subprocess
                     style usage) must be selected,
  - ``cocchange``  : a test that history says always moves with the module must
                     be selected,
  - ``nonempty``   : every commit selects >= 1 test file (criterion C4),
  - ``ratio``      : mean selected / full over the replay (criterion C2).

The ``closure`` obligation (tests that import a changed module) is *auto
satisfied* by construction and is therefore reported as a self consistency count
only, never as "recall".
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import is_test_path
from .graph import ImportGraph
from .selector import ImpactSelector


def git_text(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else ""


@dataclass
class Commit:
    sha: str
    subject: str
    files: List[str]


def git_commits(root: Path, limit: int = 60, max_files: int = 40, since: str | None = None) -> List[Commit]:
    args = ["log", f"-{limit}", "--no-merges", "--name-only", "--pretty=format:__C__%h|%s"]
    if since:
        args.insert(1, f"--since={since}")
    raw = git_text(root, *args)
    commits: List[Commit] = []
    cur: Optional[Commit] = None
    for line in raw.splitlines():
        if line.startswith("__C__"):
            if cur and cur.files:
                commits.append(cur)
            sha, _, subject = line[5:].partition("|")
            cur = Commit(sha=sha.strip(), subject=subject.strip(), files=[])
        elif line.strip() and cur is not None:
            cur.files.append(line.strip())
    if cur and cur.files:
        commits.append(cur)
    return [c for c in commits if 0 < len(c.files) <= max_files]


def replay(
    root: Path | str,
    graph: ImportGraph,
    selector: ImpactSelector,
    commits: int = 40,
    log=print,
) -> Dict[str, object]:
    root = Path(root).resolve()
    history = git_commits(root, limit=commits * 3)
    history = history[: max(commits, 1)]
    rows: List[dict] = []
    must_total = must_covered = 0
    ratios: List[float] = []
    fail_closed = 0
    empty = 0

    for commit in history:
        changed = [f for f in commit.files if (root / f).is_file() or f.endswith(".py")]
        selection = selector.select(changed, mode="enforcing")
        selected = set(selection.tests)
        direct = sorted(f for f in changed if is_test_path(f))
        literal: List[str] = []
        cocchange: List[str] = []
        for f in changed:
            if f in graph.nodes:
                for t in sorted(graph.impact_references(f)):
                    if graph.nodes.get(t) and graph.nodes[t].is_test:
                        literal.append(t)
                for t in sorted(graph.cocchange(f)):
                    cocchange.append(t)
        obligations = sorted(set(direct) | set(literal) | set(cocchange))
        missed = [o for o in obligations if o not in selected]
        must_total += len(obligations)
        must_covered += len(obligations) - len(missed)
        ratios.append(selection.ratio)
        if selection.mode == "full":
            fail_closed += 1
        if not selection.tests:
            empty += 1
        rows.append(
            {
                "sha": commit.sha,
                "subject": commit.subject[:80],
                "changed": len(changed),
                "decision": selection.mode,
                "trigger": selection.trigger,
                "selected": len(selection.tests),
                "ratio": round(selection.ratio, 6),
                "direct_tests": len(direct),
                "literal_tests": len(set(literal)),
                "cocchange_tests": len(set(cocchange)),
                "obligations": len(obligations),
                "missed": missed,
                "ok": not missed,
                "selection_hash": selection.selection_hash,
            }
        )
        log(
            f"[replay] {commit.sha} {selection.mode} {len(selection.tests)}/{selection.all_tests} "
            f"obl={len(obligations)} missed={len(missed)}"
        )

    return {
        "schema": "tp.test_impact_selector.replay/v1",
        "commits": len(rows),
        "obligations_total": must_total,
        "obligations_covered": must_covered,
        "obligation_recall": round(must_covered / must_total, 6) if must_total else None,
        "recall_100pct": (must_covered == must_total) if must_total else False,
        "commits_with_direct_tests": sum(1 for r in rows if r["direct_tests"]),
        "mean_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
        "max_ratio": round(max(ratios), 6) if ratios else None,
        "fail_closed_commits": fail_closed,
        "fail_closed_ratio": round(fail_closed / len(rows), 6) if rows else None,
        "empty_selections": empty,
        "graph_fingerprint": graph.fingerprint,
        "rows": rows,
    }


def save(result: Dict[str, object], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    return p