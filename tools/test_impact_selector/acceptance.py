"""Acceptance report builder for the test-subset maturity node (p299w4-n05).

Everything here is *computed*: no number is hand copied into the report.  The
builder reads the artefacts produced by the individual proofs (matrix, injection
chunks, replay, ledger) and re-measures the cheap things (determinism, warm graph
load, decision latency, ledger replay) so that the acceptance JSON can be
re-generated end to end at any time::

    python -m tools.test_impact_selector.acceptance --out artifacts/.../acceptance.json
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import SelectorConfig
from .graph import ImportGraph
from .ledger import Ledger
from .matrix import run_matrix
from .proof_injection import aggregate as aggregate_injections
from .replay import git_commits, replay
from .selector import ImpactSelector

SCHEMA = "tp.test_impact_selector.acceptance/v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_injection_chunks(artifacts: Path) -> Optional[dict]:
    rows: List[dict] = []
    slice_dirs: Sequence[str] = ()
    slice_tests = None
    baseline = None
    files = sorted(artifacts.glob("inj_c*.json")) + [p for p in [artifacts / "injection_proof.json"] if p.is_file()]
    seen = set()
    for p in files:
        chunk = _load(p) or {}
        for row in chunk.get("rows", []):
            key = (row.get("module"), row.get("fault"))
            if key in seen:  # chunk files win over the merged aggregate
                continue
            seen.add(key)
            rows.append(row)
        slice_dirs = chunk.get("slice_dirs") or slice_dirs
        slice_tests = chunk.get("slice_tests", slice_tests)
        baseline = chunk.get("baseline") or baseline
    if not rows:
        return None
    merged = aggregate_injections(rows, slice_dirs=slice_dirs, slice_tests=slice_tests, baseline=baseline)
    merged["sources"] = [str(p.relative_to(artifacts.parent.parent)) if artifacts.parent.parent in p.parents else str(p) for p in files]
    return merged


def _determinism(root: Path, changed: Sequence[str], cli_cwd: Path) -> dict:
    """Same-process repeats + two independent processes must agree (C6)."""
    graph = ImportGraph.build(root, use_history=True, jobs=8, cache_path=root / ".cache/test_impact_selector/scans.json")
    selector = ImpactSelector(graph, SelectorConfig(mode="enforcing"), root=root)
    hashes = [selector.select(list(changed)).selection_hash for _ in range(10)]
    proc_hashes = []
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-m", "tools.test_impact_selector.cli", "--jobs", "8", "determinism",
             "--changed", *changed, "--repeat", "1"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        m = re.search(r'"hash": "([0-9a-f]{64})"', out)
        proc_hashes.append(m.group(1) if m else None)
    return {
        "same_process_unique": len(set(hashes)),
        "repeat": len(hashes),
        "hash": hashes[0],
        "process_hashes": proc_hashes,
        "processes_agree": len(set(proc_hashes)) == 1 and proc_hashes[0] == hashes[0],
        "deterministic": len(set(hashes)) == 1 and len(set(proc_hashes)) == 1,
    }


def _latency(root: Path, changed: Sequence[str]) -> dict:
    """Warm path cost: cached graph load + one decision."""
    cache = root / ".cache/test_impact_selector/scans.json"
    t0 = time.time()
    graph = ImportGraph.build(root, use_history=True, jobs=8, cache_path=cache)
    warm_s = time.time() - t0
    selector = ImpactSelector(graph, SelectorConfig(mode="enforcing"), root=root)
    times = []
    for _ in range(5):
        t = time.time()
        selector.select(list(changed))
        times.append((time.time() - t) * 1000.0)
    return {
        "warm_graph_load_s": round(warm_s, 4),
        "graph_cache_hit": graph.build_report.graph_cache_hit,
        "decision_ms_mean": round(sum(times) / len(times), 3),
        "decision_ms_max": round(max(times), 3),
        "files": graph.stats()["files"],
        "tests": graph.stats()["tests"],
    }


def build(
    root: Path | str,
    artifacts: Path | str = "artifacts/test_impact_selector",
    ledger_path: Path | str = "runs/test_impact_selector/ledger.jsonl",
    replay_commits: int = 60,
    full_suite_seconds: Optional[float] = None,
) -> Dict[str, object]:
    root = Path(root).resolve()
    artifacts = (root / artifacts).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    # ---- C1: real-failure recall (injection replay) + historical obligations
    injections = _merge_injection_chunks(artifacts)
    graph = ImportGraph.build(root, use_history=True, jobs=8, cache_path=root / ".cache/test_impact_selector/scans.json")
    selector = ImpactSelector(graph, SelectorConfig(mode="enforcing"), root=root)
    rp = replay(root, graph, selector, commits=replay_commits, log=lambda *a: None)

    # ---- C2: size + overhead
    rows = rp["rows"]
    mean_ratio = sum(r["ratio"] for r in rows) / len(rows) if rows else None
    lat = _latency(root, ["tools/test_impact_selector/graph.py", "tools/failure_minimizer/ddmin.py"])
    base_seconds = full_suite_seconds
    base_source = "measured_full_suite"
    if base_seconds is None and injections and injections.get("baseline", {}).get("duration_s"):
        base_seconds = float(injections["baseline"]["duration_s"])
        base_source = "measured_slice_baseline"
    overhead = None
    if base_seconds:
        overhead = {
            "full_suite_seconds": round(base_seconds, 3),
            "source": base_source,
            "warm_overhead_ratio": round(lat["warm_graph_load_s"] / base_seconds, 6),
            "decision_overhead_ratio": round((lat["decision_ms_mean"] / 1000.0) / base_seconds, 6),
            "note": "warm path = cached graph load + one decision; the decision alone is the per-change cost",
        }

    # ---- C3: fail-closed matrix
    matrix = run_matrix()

    # ---- C4: docs only never empty (matrix control + replay empties)
    docs_row = next((r for r in matrix["rows"] if r["name"] == "control_docs_only"), None)

    # ---- C5: zero domain semantics
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent.parent))
    from tools.test_impact_selector.tests.test_zero_domain import (  # local import: test helper reuse
        _lexicon,
        _package_files,
        _scan,
    )

    domain_hits = _scan(_package_files(), list(_lexicon()["tokens"]))

    # ---- C6: determinism
    det = _determinism(root, ["tools/test_impact_selector/graph.py", "tools/failure_minimizer/ddmin.py"], root)

    # ---- C7: ledger auditability
    ledger = Ledger(root / ledger_path)
    stats = ledger.stats()
    sample_row = None
    repro = None
    for candidate in reversed(ledger.rows()):
        rep = ledger.reproduce(candidate["run_id"], graph, selector)
        if rep.get("found"):
            sample_row = candidate
            repro = rep
            if rep.get("match"):
                break
    ledger_audit = {
        "records": stats["records"],
        "shadow_records": stats["shadow_records"],
        "ledger_sha256": stats["ledger_sha256"],
        "sample_run_id": sample_row["run_id"] if sample_row else None,
        "sample_reproduced": bool(repro and repro.get("match")),
        "sample_graph_unchanged": bool(repro and repro.get("graph_unchanged")),
        "reproduce": repro,
        "ledger_file_sha256": _sha(root / ledger_path) if (root / ledger_path).is_file() else None,
    }

    # ---- aggregate decisions from the shadow ledger (C2 population)
    all_rows = ledger.rows()
    ratio_all = [r["ratio"] for r in all_rows] if all_rows else []
    mean_ratio_all = sum(ratio_all) / len(ratio_all) if ratio_all else None

    criteria = {
        "C1_recall": {
            "pass": bool(injections and injections["recall_100pct"] and injections["scenarios_with_real_failure"] >= 5
                         and injections["scenarios"] >= 30 and rp["recall_100pct"]),
            "injection_scenarios": injections["scenarios"] if injections else None,
            "injection_real_failure_scenarios": injections["scenarios_with_real_failure"] if injections else None,
            "injection_recall": injections["recall"] if injections else None,
            "missing_total": injections["missed_total"] if injections else None,
            "subset_reproduced_all": injections["subset_reproduced_all"] if injections else None,
            "replay_commits": rp["commits"],
            "replay_obligations": rp["obligations_total"],
            "replay_obligation_recall": rp["obligation_recall"],
        },
        "C2_size_and_overhead": {
            "pass": bool(mean_ratio is not None and mean_ratio <= 0.40 and overhead is not None
                         and overhead["decision_overhead_ratio"] <= 0.05),
            "replay_mean_ratio": round(mean_ratio, 6) if mean_ratio is not None else None,
            "ledger_mean_ratio": round(mean_ratio_all, 6) if mean_ratio_all is not None else None,
            "overhead": overhead,
        },
        "C3_failclosed": {
            "pass": bool(matrix["criterion_C3"]),
            "verdict": matrix["verdict"],
            "classes": [r["name"] for r in matrix["rows"] if r["name"] not in ("control_subset", "control_docs_only")],
        },
        "C4_nonempty": {
            "pass": bool(docs_row and docs_row["ok"] and rp["empty_selections"] == 0),
            "docs_only_selected": docs_row["selected"] if docs_row else None,
            "docs_only_tests": docs_row["tests"] if docs_row else None,
            "replay_empty_selections": rp["empty_selections"],
        },
        "C5_zero_domain": {"pass": domain_hits == [], "hits": domain_hits, "lexicon_size": len(_lexicon()["tokens"])},
        "C6_determinism": {
            "pass": bool(det["deterministic"] and det["processes_agree"]),
            **det,
        },
        "C7_ledger": {
            "pass": bool(ledger_audit["records"] >= 200 and ledger_audit["sample_reproduced"]
                         and ledger_audit["sample_graph_unchanged"]),
            **{k: v for k, v in ledger_audit.items() if k != "reproduce"},
        },
    }
    verdict = {k: v["pass"] for k, v in criteria.items()}
    result = {
        "schema": SCHEMA,
        "node": "p299w4-n05-test-subset-maturity",
        "repo": "TensorPlatform",
        "graph": {
            "fingerprint": graph.fingerprint,
            "stats": graph.stats(),
            "build": graph.build_report.as_dict(),
        },
        "criteria": criteria,
        "verdict": verdict,
        "all_pass": all(verdict.values()),
        "artifacts": {
            "import_graph": str((artifacts / "import_graph.json").relative_to(root))
            if (artifacts / "import_graph.json").is_file()
            else None,
            "failclosed_matrix": str((artifacts / "failclosed_matrix.json").relative_to(root)),
            "injection_proof": str((artifacts / "injection_proof.json").relative_to(root)),
            "replay": str((artifacts / "replay.json").relative_to(root)),
            "ledger": str(ledger_path),
        },
        "replay": {k: v for k, v in rp.items() if k not in ("rows",)},
        "replay_rows": rp["rows"],
        "injection": {k: v for k, v in (injections or {}).items() if k != "rows"},
        "injection_rows": (injections or {}).get("rows", []),
        "matrix_rows": matrix["rows"],
    }
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="build the acceptance JSON for the test subset selector")
    ap.add_argument("--root", default=".")
    ap.add_argument("--artifacts", default="artifacts/test_impact_selector")
    ap.add_argument("--ledger", default="runs/test_impact_selector/ledger.jsonl")
    ap.add_argument("--replay-commits", type=int, default=60)
    ap.add_argument("--full-suite-seconds", type=float, default=None)
    ap.add_argument("--out", default="artifacts/test_impact_selector/acceptance.json")
    args = ap.parse_args(argv)
    result = build(
        Path(args.root),
        artifacts=args.artifacts,
        ledger_path=args.ledger,
        replay_commits=args.replay_commits,
        full_suite_seconds=args.full_suite_seconds,
    )
    out = Path(args.root) / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "all_pass": result["all_pass"], "out": str(out)}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())