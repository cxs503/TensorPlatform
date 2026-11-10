"""Command line entry point of the change-impact test selector.

Usage examples
--------------
::

    python -m tools.test_impact_selector.cli build-graph --out artifacts/.../graph.json
    python -m tools.test_impact_selector.cli select --changed tools/x/y.py --mode shadow --ledger runs/tis.jsonl
    python -m tools.test_impact_selector.cli select --staged --mode enforcing --json /tmp/sel.json
    python -m tools.test_impact_selector.cli matrix  --out artifacts/.../failclosed_matrix.json
    python -m tools.test_impact_selector.cli replay  --commits 40 --out artifacts/.../replay.json
    python -m tools.test_impact_selector.cli injections --discover --out artifacts/.../injection_proof.json
    python -m tools.test_impact_selector.cli determinism --changed a.py --repeat 10
    python -m tools.test_impact_selector.cli ledger-stats --ledger runs/tis.jsonl
    python -m tools.test_impact_selector.cli reproduce --run-id tis-xxxx --ledger runs/tis.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from .config import SelectorConfig
from .graph import ImportGraph
from .ledger import Ledger
from .selector import ImpactSelector

DEFAULT_CACHE = ".cache/test_impact_selector/scans.json"
DEFAULT_ARTIFACTS = "artifacts/test_impact_selector"


def _root(args) -> Path:
    return Path(args.root).resolve()


def _build(args):
    root = _root(args)
    t0 = time.time()
    graph = ImportGraph.build(
        root,
        use_history=not args.no_history,
        history_commits=args.history_commits,
        jobs=args.jobs,
        cache_path=(root / args.cache) if args.cache else None,
    )
    out = Path(args.out) if args.out else root / DEFAULT_ARTIFACTS / "import_graph.json"
    graph.save(out)
    payload = {
        "graph": str(out),
        "fingerprint": graph.fingerprint,
        "stats": graph.stats(),
        "build": graph.build_report.as_dict(),
        "wall_s": round(time.time() - t0, 3),
    }
    print(json.dumps(payload, indent=1, sort_keys=True))
    return payload


def _changed_from_git(root: Path, staged: bool, diff_from: str | None) -> List[str]:
    if diff_from:
        cmd = ["git", "diff", "--name-only", diff_from]
    elif staged:
        cmd = ["git", "diff", "--cached", "--name-only"]
    else:
        cmd = ["git", "status", "--porcelain"]
    out = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=False).stdout
    files: List[str] = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if staged or diff_from:
            files.append(line.strip())
        else:  # porcelain "XY path"
            files.append(line[3:].strip())
    return sorted(set(files))


def _selector(args, graph: Optional[ImportGraph] = None) -> tuple[ImportGraph, ImpactSelector]:
    root = _root(args)
    if graph is None:
        graph = ImportGraph.build(
            root,
            use_history=not args.no_history,
            jobs=args.jobs,
            cache_path=(root / args.cache) if args.cache else None,
        )
    cfg = SelectorConfig(mode=args.mode)
    return graph, ImpactSelector(graph, cfg, root=root)


def _select(args):
    root = _root(args)
    graph, selector = _selector(args)
    changed = list(args.changed or [])
    if args.changed_file:
        changed += [ln.strip() for ln in Path(args.changed_file).read_text().splitlines() if ln.strip()]
    if args.staged or args.diff_from or args.git_status:
        changed += _changed_from_git(root, args.staged, args.diff_from)
    t0 = time.time()
    selection = selector.select(changed, mode=args.mode)
    dur_ms = (time.time() - t0) * 1000.0
    plan = selector.effective_plan(selection)
    row = None
    if args.ledger:
        row = Ledger(root / args.ledger).record(selection, plan, dur_ms, extra={"command": args.command})
    payload = selection.as_dict()
    payload["plan"] = {k: v for k, v in plan.items() if k != "tests"}
    payload["selector_ms"] = round(dur_ms, 3)
    payload["ledger_record"] = row
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "tests"}, indent=1, sort_keys=True))
    if args.print_tests:
        print("\n".join(selection.tests))
    return payload


def _matrix(args):
    from .matrix import run_matrix

    res = run_matrix()
    out = Path(args.out) if args.out else _root(args) / DEFAULT_ARTIFACTS / "failclosed_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, sort_keys=True))
    return res


def _replay(args):
    from .replay import replay, save

    graph, selector = _selector(args)
    res = replay(_root(args), graph, selector, commits=args.commits)
    out = Path(args.out) if args.out else _root(args) / DEFAULT_ARTIFACTS / "replay.json"
    save(res, out)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, sort_keys=True))
    return res


def _injections(args):
    from .proof_injection import DEFAULT_SLICE, discover_slice, run_proof, save

    root = _root(args)
    graph, selector = _selector(args)
    slice_dirs = list(args.slice or DEFAULT_SLICE)
    discovery = None
    if args.discover:
        candidates = list(args.candidates or [])
        if not candidates:
            candidates = sorted(
                {
                    t.rsplit("/", 1)[0]
                    for t in graph.tests()
                    if "/" in t and t.startswith(("tools/", "tests/"))
                }
            )[: args.max_candidates]
        discovery = discover_slice(root, candidates)
        slice_dirs = discovery["kept"] or slice_dirs
    module_range = None
    if args.module_range:
        lo, _, hi = args.module_range.partition(":")
        module_range = (int(lo or 0), int(hi) if hi else args.modules)
    res = run_proof(
        root,
        graph,
        selector,
        slice_dirs=slice_dirs,
        top_modules=args.modules,
        timeout=args.timeout,
        module_range=module_range,
    )
    if discovery:
        res["slice_discovery"] = discovery
    out = Path(args.out) if args.out else root / DEFAULT_ARTIFACTS / "injection_proof.json"
    save(res, out)
    print(
        json.dumps(
            {k: v for k, v in res.items() if k not in ("rows", "slice_discovery", "baseline")}, indent=1, sort_keys=True
        )
    )
    return res


def _aggregate(args):
    from .proof_injection import aggregate, save

    rows: List[dict] = []
    slice_dirs: Sequence[str] = ()
    slice_tests = None
    baseline = None
    for p in args.inputs:
        chunk = json.loads(Path(p).read_text(encoding="utf-8"))
        rows.extend(chunk.get("rows", []))
        slice_dirs = chunk.get("slice_dirs") or slice_dirs
        slice_tests = chunk.get("slice_tests", slice_tests)
        baseline = chunk.get("baseline") or baseline
    res = aggregate(rows, slice_dirs=slice_dirs, slice_tests=slice_tests, baseline=baseline)
    save(res, args.out)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, sort_keys=True))
    return res


def _shadow_rollout(args):
    """Shadow rollout: real changed-file sets from git history, ledger rows only.

    Every row is a real write path: the decision is taken in ``shadow`` mode (the
    pipeline would run the *full* suite, the ledger stores what the selector
    would have run), which is the pre-condition for flipping to ``enforcing``.
    """
    from .replay import git_commits

    root = _root(args)
    graph, selector = _selector(args)
    ledger = Ledger(root / args.ledger)
    commits = git_commits(root, limit=args.commits * 2)[: args.commits]
    rows = []
    for c in commits:
        changed = [f for f in c.files if (root / f).exists() or f.endswith(".py")]
        t0 = time.time()
        selection = selector.select(changed, mode="shadow")
        dur = (time.time() - t0) * 1000.0
        rows.append(ledger.record(selection, selector.effective_plan(selection), dur, extra={"sha": c.sha}))
    stats = ledger.stats()
    res = {
        "schema": "tp.test_impact_selector.shadow_rollout/v1",
        "commits": len(rows),
        "ledger": args.ledger,
        "stats": stats,
        "mean_selector_ms": round(sum(r["selector_ms"] for r in rows) / len(rows), 3) if rows else None,
        "enforcing_ready": stats["shadow_records"] >= selector.config.shadow_min_samples,
    }
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(res, indent=1, sort_keys=True))
    return res


def _determinism(args):
    root = _root(args)
    graph, selector = _selector(args)
    hashes = []
    for _ in range(args.repeat):
        sel = selector.select(list(args.changed), mode=args.mode)
        hashes.append(sel.selection_hash)
    res = {
        "repeat": args.repeat,
        "unique_hashes": len(set(hashes)),
        "hash": hashes[0],
        "deterministic": len(set(hashes)) == 1,
        "graph_fingerprint": graph.fingerprint,
    }
    print(json.dumps(res, indent=1, sort_keys=True))
    return res


def _ledger_stats(args):
    root = _root(args)
    res = Ledger(root / args.ledger).stats()
    print(json.dumps(res, indent=1, sort_keys=True))
    return res


def _reproduce(args):
    root = _root(args)
    graph, selector = _selector(args)
    res = Ledger(root / args.ledger).reproduce(args.run_id, graph, selector)
    print(json.dumps(res, indent=1, sort_keys=True))
    return res


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.test_impact_selector.cli", description=__doc__)
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="scan cache path (empty string disables)")
    ap.add_argument("--jobs", type=int, default=8, help="parallel parse workers")
    ap.add_argument("--no-history", action="store_true", help="disable git co-change edges")
    ap.add_argument("--history-commits", type=int, default=400)
    ap.add_argument("--mode", default="shadow", choices=("shadow", "enforcing", "off"))
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-graph")
    b.add_argument("--out", default=None)
    b.set_defaults(func=_build)

    s = sub.add_parser("select")
    s.add_argument("--changed", nargs="*", default=[])
    s.add_argument("--changed-file", default=None)
    s.add_argument("--staged", action="store_true")
    s.add_argument("--diff-from", default=None)
    s.add_argument("--git-status", action="store_true")
    s.add_argument("--ledger", default=None)
    s.add_argument("--json", default=None)
    s.add_argument("--print-tests", action="store_true")
    s.set_defaults(func=_select)

    m = sub.add_parser("matrix")
    m.add_argument("--out", default=None)
    m.set_defaults(func=_matrix)

    r = sub.add_parser("replay")
    r.add_argument("--commits", type=int, default=40)
    r.add_argument("--out", default=None)
    r.set_defaults(func=_replay)

    i = sub.add_parser("injections")
    i.add_argument("--discover", action="store_true")
    i.add_argument("--candidates", nargs="*", default=[])
    i.add_argument("--max-candidates", type=int, default=24)
    i.add_argument("--slice", nargs="*", default=None)
    i.add_argument("--modules", type=int, default=15)
    i.add_argument("--module-range", default=None, help="chunk selection, e.g. 0:5")
    i.add_argument("--timeout", type=int, default=900)
    i.add_argument("--out", default=None)
    i.set_defaults(func=_injections)

    sh = sub.add_parser("shadow-rollout")
    sh.add_argument("--commits", type=int, default=220)
    sh.add_argument("--ledger", default="runs/test_impact_selector/ledger.jsonl")
    sh.add_argument("--out", default=None)
    sh.set_defaults(func=_shadow_rollout)

    d = sub.add_parser("determinism")
    d.add_argument("--changed", nargs="*", default=[])
    d.add_argument("--repeat", type=int, default=10)
    d.set_defaults(func=_determinism)

    ag = sub.add_parser("aggregate-injections")
    ag.add_argument("--inputs", nargs="+", required=True)
    ag.add_argument("--out", required=True)
    ag.set_defaults(func=_aggregate)

    ls = sub.add_parser("ledger-stats")
    ls.add_argument("--ledger", default="runs/test_impact_selector/ledger.jsonl")
    ls.set_defaults(func=_ledger_stats)

    rp = sub.add_parser("reproduce")
    rp.add_argument("--ledger", default="runs/test_impact_selector/ledger.jsonl")
    rp.add_argument("--run-id", required=True)
    rp.set_defaults(func=_reproduce)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())