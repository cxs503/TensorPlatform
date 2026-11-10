"""Fault-injection replay: real full-suite failures vs. the selected subset.

Why this exists
---------------
Static graph recall cannot be *proven* by inspecting the graph: the only honest
ground truth for "which tests break when X changes" is a real run.  This module
therefore replays scenarios on **real repository modules**:

1. take a fast, clean, real test slice (auto discovered, see ``discover_slice``),
2. inject a real defect into one real source module (import break / behaviour
   corruption),
3. run the *full* slice -> ground truth failure set (real execution),
4. ask the selector for the subset for ``[module]`` -> run only that subset,
5. compare: every failing test file must be inside the selected subset.

Both runs are real pytest runs; the module file is restored byte-exactly after
each scenario and the restore is verified with ``git status``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .graph import ImportGraph
from .selector import ImpactSelector

DEFAULT_SLICE: Tuple[str, ...] = (
    "tools/failure_minimizer",
    "tools/dispatch_contract",
    "tools/delivery_hygiene",
)

FAULT_KINDS: Tuple[str, ...] = ("import_break", "behavior_corrupt")


# ---------------------------------------------------------------------------
# pytest plumbing
# ---------------------------------------------------------------------------


def run_pytest(root: Path, targets: Sequence[str], timeout: int = 900) -> Dict[str, object]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    # keep pytest temp dirs inside the tree: the shared /tmp is often full and
    # an ENOSPC mid-run would look like a test failure
    tmp = root / ".cache" / "pytest-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmp)
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", "--tb=no", "-rfE"]
    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    dur = time.time() - t0
    failed: List[str] = []
    errored: List[str] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        line = line.strip()
        for prefix, bucket in (("FAILED ", failed), ("ERROR ", errored)):
            if line.startswith(prefix):
                loc = line[len(prefix) :].split(" ")[0]
                path = loc.split("::")[0]
                if path.endswith(".py") and path not in bucket:
                    bucket.append(path)
    summary = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    return {
        "rc": proc.returncode,
        "duration_s": round(dur, 3),
        "failed_files": sorted(failed),
        "errored_files": sorted(errored),
        "summary": summary[-1] if summary else "",
        "tail": (proc.stdout or proc.stderr)[-800:],
    }


def _clean_pycache(root: Path, rel: str) -> None:
    d = (root / rel).parent / "__pycache__"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# slice discovery
# ---------------------------------------------------------------------------


def discover_slice(root: Path, candidates: Sequence[str], timeout: int = 120) -> Dict[str, object]:
    """Keep only candidate dirs that collect *and* pass cleanly at HEAD."""
    kept: List[str] = []
    dropped: List[Dict[str, object]] = []
    for rel in candidates:
        if not (root / rel).exists():
            dropped.append({"dir": rel, "reason": "missing"})
            continue
        try:
            res = run_pytest(root, [rel], timeout=timeout)
        except subprocess.TimeoutExpired:
            dropped.append({"dir": rel, "reason": "timeout"})
            continue
        if res["rc"] == 0:
            kept.append(rel)
        else:
            dropped.append(
                {"dir": rel, "reason": "not clean", "rc": res["rc"], "summary": res["summary"]}
            )
    return {"kept": kept, "dropped": dropped}


# ---------------------------------------------------------------------------
# fault injection
# ---------------------------------------------------------------------------


def inject_import_break(source: str, tag: str) -> str:
    return source + f'\n\nraise RuntimeError("INJECTED_FAULT_{tag}")\n'


def inject_behavior_corrupt(source: str, tag: str) -> Tuple[str, str]:
    """Wrap the first public top level function so it returns ``None``."""
    import ast

    tree = ast.parse(source)
    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            target = node.name
            break
    if target is None:
        return inject_import_break(source, tag), "<import_break_fallback>"
    patch = (
        f"\n\n_TIS_ORIG = {target}\n"
        f"def {target}(*a, **k):  # INJECTED_FAULT_{tag}\n"
        f"    _TIS_ORIG(*a, **k)\n"
        f"    return None\n"
    )
    return source + patch, target


def apply_and_revert(root: Path, rel: str, mutate) -> Iterable[object]:
    """Context manager: write mutated content, restore byte exact, verify."""
    path = root / rel
    original = path.read_text(encoding="utf-8")
    original_sha = hashlib.sha256(original.encode()).hexdigest()

    class _Ctx:
        def __enter__(self_inner):
            new_source, detail = mutate(original)
            path.write_text(new_source, encoding="utf-8")
            _clean_pycache(root, rel)
            self_inner.detail = detail
            return self_inner

        def __exit__(self_inner, *exc):
            path.write_text(original, encoding="utf-8")
            _clean_pycache(root, rel)
            restored = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
            status = subprocess.run(
                ["git", "status", "--porcelain", "--", rel],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            self_inner.restored_clean = restored == original_sha
            self_inner.git_status_clean = status == ""
            return False

    return _Ctx()


# ---------------------------------------------------------------------------
# scenario selection
# ---------------------------------------------------------------------------


def candidate_modules(graph: ImportGraph, slice_dirs: Sequence[str], top: int = 15) -> List[str]:
    """Source modules used by the slice, ranked by how many slice tests need them."""
    slice_tests = [t for t in graph.tests() if any(t.startswith(d + "/") for d in slice_dirs)]
    counter: Dict[str, int] = {}
    for t in slice_tests:
        for dep in graph.test_closure(t):
            node = graph.nodes.get(dep)
            if node is None or node.is_test or dep.endswith("__init__.py"):
                continue
            counter[dep] = counter.get(dep, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [rel for rel, n in ranked if n >= 1][:top]


def run_proof(
    root: Path | str,
    graph: ImportGraph,
    selector: ImpactSelector,
    slice_dirs: Sequence[str] = DEFAULT_SLICE,
    top_modules: int = 15,
    timeout: int = 900,
    log=print,
    module_range: Tuple[int, int] | None = None,
) -> Dict[str, object]:
    root = Path(root).resolve()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *slice_dirs],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    # untracked ("??") files are fine: only *modified* files could hide a
    # half restored injection
    dirty = "\n".join(
        line for line in dirty.splitlines() if line.strip() and not line.startswith("??")
    )
    if dirty:
        return {
            "schema": "tp.test_impact_selector.injection_proof/v1",
            "ok": False,
            "reason": "slice tree is dirty; refusing to inject (restore first)",
            "dirty": dirty.splitlines()[:20],
        }
    baseline = run_pytest(root, list(slice_dirs), timeout=timeout)
    log(f"[proof] baseline rc={baseline['rc']} {baseline['summary']}")
    if baseline["rc"] != 0:
        return {
            "schema": "tp.test_impact_selector.injection_proof/v1",
            "ok": False,
            "reason": "slice baseline is not green",
            "baseline": baseline,
        }
    all_slice_tests = [t for t in graph.tests() if any(t.startswith(d + "/") for d in slice_dirs)]
    modules = candidate_modules(graph, slice_dirs, top=top_modules)
    if module_range:
        modules = modules[module_range[0] : module_range[1]]
    log(f"[proof] {len(modules)} candidate modules, {len(all_slice_tests)} slice tests")

    rows: List[dict] = []
    seq = 0
    for rel in modules:
        for kind in FAULT_KINDS:
            seq += 1
            tag = f"s{seq:02d}"
            if kind == "import_break":
                mutate = lambda src, tag=tag: (inject_import_break(src, tag), "import_break")  # noqa: E731
            else:
                mutate = lambda src, tag=tag: inject_behavior_corrupt(src, tag)  # noqa: E731
            with apply_and_revert(root, rel, mutate) as ctx:
                full = run_pytest(root, list(slice_dirs), timeout=timeout)
                gt = sorted(set(full["failed_files"]) | set(full["errored_files"]))
                sel = selector.select([rel], mode="enforcing")
                selected = set(sel.tests)
                caught = [g for g in gt if g in selected]
                missed = [g for g in gt if g not in selected]
                subset_targets = sorted(selected & set(all_slice_tests))
                sub_res = (
                    run_pytest(root, subset_targets, timeout=timeout) if subset_targets else {"failed_files": [], "errored_files": []}
                )
                sub_fail = sorted(set(sub_res.get("failed_files", [])) | set(sub_res.get("errored_files", [])))
            rows.append(
                {
                    "seq": seq,
                    "module": rel,
                    "fault": kind,
                    "fault_detail": getattr(ctx, "detail", kind),
                    "restored_clean": getattr(ctx, "restored_clean", None),
                    "git_status_clean": getattr(ctx, "git_status_clean", None),
                    "full_rc": full["rc"],
                    "full_duration_s": full["duration_s"],
                    "ground_truth_failures": gt,
                    "has_real_failure": bool(gt),
                    "selected": len(sel.tests),
                    "selection_mode": sel.mode,
                    "trigger": sel.trigger,
                    "selected_ratio": round(sel.ratio, 4),
                    "recall_static": (len(missed) == 0),
                    "missed": missed,
                    "subset_targets": len(subset_targets),
                    "subset_failing_files": sub_fail,
                    "subset_reproduced_gt": (set(gt) <= set(sub_fail)) if gt else None,
                    "selector_ms": None,
                }
            )
            log(
                f"[proof] {tag} {rel} {kind}: gt={len(gt)} selected={len(sel.tests)} "
                f"missed={len(missed)} restored={getattr(ctx, 'restored_clean', None)}"
            )

    payload = aggregate(rows, slice_dirs=slice_dirs, slice_tests=len(all_slice_tests), baseline=baseline)
    payload["modules"] = modules
    payload["module_range"] = list(module_range) if module_range else None
    return payload


def _renumber(rows: Sequence[dict]) -> List[dict]:
    ordered = sorted(rows, key=lambda r: (r["module"], r["fault"]))
    out = []
    for i, r in enumerate(ordered, start=1):
        row = dict(r)
        row["seq"] = i
        out.append(row)
    return out


def aggregate(
    rows: Sequence[dict],
    slice_dirs: Sequence[str] = DEFAULT_SLICE,
    slice_tests: int | None = None,
    baseline: Optional[dict] = None,
) -> Dict[str, object]:
    """Recompute the proof verdict from scenario rows (chunk merge safe)."""
    real = [r for r in rows if r["has_real_failure"]]
    recall_ok = [r for r in real if r["recall_static"]]
    repro = [r for r in real if r.get("subset_reproduced_gt")]
    return {
        "schema": "tp.test_impact_selector.injection_proof/v1",
        "ok": len(rows) >= 30 and len(real) >= 5 and len(recall_ok) == len(real),
        "slice_dirs": list(slice_dirs),
        "slice_tests": slice_tests,
        "baseline": baseline,
        "scenarios": len(rows),
        "scenarios_with_real_failure": len(real),
        "recall_100pct": len(recall_ok) == len(real) and len(real) > 0,
        "recall": round(len(recall_ok) / len(real), 6) if real else None,
        "missed_total": sum(len(r["missed"]) for r in rows),
        "subset_reproduced": len(repro),
        "subset_reproduced_all": len(repro) == len(real),
        "all_restored_clean": all(r.get("restored_clean") for r in rows),
        "mean_selected_ratio": round(sum(r["selected_ratio"] for r in rows) / len(rows), 6) if rows else None,
        "full_suite_mean_duration_s": round(sum(r["full_duration_s"] for r in rows) / len(rows), 3) if rows else None,
        "rows": _renumber(rows),
    }


def save(result: Dict[str, object], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    return p