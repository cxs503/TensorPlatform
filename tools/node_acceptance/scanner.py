"""Artifact scanner: node artifact directory -> :class:`NodeEvidence`.

Scope discipline
----------------
A node directory is usually a full repository checkout, so the scanner never
walks the whole tree.  It looks at:

* the declared top-level globs (validation reports, mission reports, criterion
  files),
* the declared search roots (``artifacts/``, ``benchmarks/``, ``runs/``,
  ``docs/``) up to ``scan_limits.max_depth``,
* figure directories and ``verified/`` benchmark case directories, which are
  *counted* rather than parsed.

Everything parsed is hashed (sha256) so the resulting verdict has an audit
chain back to exact bytes.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

from .criteria import load_lexicon
from .model import ArtifactRef, MetricRecord, NodeEvidence

_IMAGE_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".pdf", ".eps"}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify(path: Path, root: Path, roles: dict[str, str]) -> str:
    rel = path.relative_to(root).as_posix()
    for role in ("criteria", "validation_report", "validation_report_variant", "report"):
        pat = roles.get(role)
        if pat and re.search(pat, rel):
            return role
    if any(seg in _IMAGE_SUFFIXES for seg in [path.suffix.lower()]):
        return "figure"
    return "other"


def _candidate_files(root: Path, lex: dict) -> tuple[list[Path], list[str]]:
    """Return (files, warnings) for the declared scan surface."""
    limits = lex.get("scan_limits", {})
    max_files = int(limits.get("max_files", 600))
    max_depth = int(limits.get("max_depth", 4))
    warnings: list[str] = []
    found: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        if p.is_file() and p not in seen:
            seen.add(p)
            found.append(p)

    # 1. declared top-level globs
    try:
        for p in sorted(root.iterdir()):
            if p.is_file():
                for pat in lex.get("artifact_top_level_globs", []):
                    if fnmatch.fnmatch(p.name, pat):
                        _add(p)
                        break
    except OSError as exc:  # pragma: no cover - defensive
        warnings.append(f"root_unreadable:{exc}")

    # 2. declared search roots
    for name in lex.get("artifact_search_roots", []):
        base = root / name
        if not base.is_dir():
            continue
        base_depth = len(base.relative_to(root).parts)
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if len(p.relative_to(root).parts) - base_depth > max_depth:
                continue
            _add(p)
            if len(found) >= max_files:
                warnings.append(f"file_cap_reached:{max_files}")
                return found, warnings

    # 3. figures anywhere shallow (counted, not parsed)
    for name in ("figures",):
        for p in sorted(root.glob(f"**/{name}/*")):
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES:
                _add(p)

    return found, warnings


# ---------------------------------------------------------------------------
# JSON flattening
# ---------------------------------------------------------------------------

def flatten_metrics(
    doc: Any,
    source: str,
    lex: dict,
    max_metrics: int = 20000,
) -> list[MetricRecord]:
    """Flatten a JSON document into numeric / boolean / status leaves."""
    metrics: list[MetricRecord] = []
    true_tokens = tuple(t.lower() for t in lex.get("status_true_tokens", []))
    false_tokens = tuple(t.lower() for t in lex.get("status_false_tokens", []))

    def _status_value(text: str) -> bool | None:
        t = text.strip().lower()
        if not t:
            return None
        soft = ("pass_with_note", "pass_with_notes")
        if any(s in t for s in soft):
            return True
        if any(s in t for s in false_tokens):
            return False
        if any(s in t for s in true_tokens):
            return True
        return None

    def walk(node: Any, path: str, scope: str) -> None:
        if len(metrics) >= max_metrics:
            return
        # scopes are namespaced by artifact so two files cannot collide on
        # "anchors[1]" and silently swap evidence
        namespaced = f"{source}::{scope}"
        if isinstance(node, dict):
            for k, v in node.items():
                child = f"{path}.{k}" if path else str(k)
                walk(v, child, path if path else "")
        elif isinstance(node, list):
            for i, v in enumerate(node[:200]):
                walk(v, f"{path}[{i}]", path)
        elif isinstance(node, bool):
            metrics.append(MetricRecord(key=path, value=node, kind="bool", source=source, scope=namespaced))
        elif isinstance(node, (int, float)):
            metrics.append(MetricRecord(key=path, value=float(node), kind="number", source=source, scope=namespaced))
        elif isinstance(node, str):
            v = _status_value(node)
            if v is not None:
                metrics.append(MetricRecord(key=path, value=v, kind="status", source=source, scope=namespaced))

    walk(doc, "", "")
    return metrics


# ---------------------------------------------------------------------------
# Derived aggregates
# ---------------------------------------------------------------------------

def derive(metrics: list[MetricRecord], lex: dict, figure_count: int, verified_cases: list[str]) -> dict:
    """Aggregate leaves into the named quantities the criterion hints need."""
    error_words = tuple(w.lower() for w in lex["metric_hints"]["error"])
    duration_words = tuple(w.lower() for w in lex["metric_hints"]["duration"])
    tolerance_keys = tuple(w.lower() for w in lex.get("tolerance_keys", []))
    count_words = tuple(w.lower() for w in lex["metric_hints"].get("count", []))

    errors = []
    durations = []
    for m in metrics:
        if m.kind != "number" or m.value is None:
            continue
        leaf = m.leaf.lower()
        if leaf in tolerance_keys:
            continue
        if any(w in leaf for w in error_words):
            errors.append(m)
        if any(w in leaf for w in duration_words):
            durations.append(m)

    # test suites: sibling "total" + "passed"/"failed" inside one scope
    by_scope: dict[str, dict[str, MetricRecord]] = {}
    for m in metrics:
        by_scope.setdefault(m.scope or "<root>", {})[m.leaf.lower()] = m
    suites = []
    for scope, leaves in by_scope.items():
        total = next((leaves[k] for k in ("total", "n_tests", "num_tests", "tests_total") if k in leaves), None)
        passed = next((leaves[k] for k in ("passed", "n_passed", "tests_passed", "n_pass") if k in leaves), None)
        failed = next((leaves[k] for k in ("failed", "n_failed", "tests_failed", "n_fail") if k in leaves), None)
        if total is None or (passed is None and failed is None):
            continue
        t = float(total.value or 0.0)
        p = float(passed.value) if passed is not None else max(t - float(failed.value or 0.0), 0.0)
        f = float(failed.value) if failed is not None else max(t - p, 0.0)
        suites.append({
            "scope": scope,
            "total": t,
            "passed": p,
            "failed": f,
            "source": total.source,
            "keys": [x.key for x in (total, passed, failed) if x is not None],
        })

    # pass-ish / fail-ish flags
    flags_true, flags_false, flag_keys = 0, 0, []
    for m in metrics:
        if m.kind not in ("bool", "status"):
            continue
        leaf = m.leaf.lower()
        is_flag = (
            leaf in ("passed", "pass", "ok", "green", "all_passed", "all_pass", "all_green")
            or leaf.endswith("_pass")
            or leaf.endswith("_passed")
            or leaf.startswith("pass_")
            or leaf == "status"
            or leaf.endswith("_status")
        )
        if not is_flag:
            continue
        flag_keys.append(m.key)
        if bool(m.value):
            flags_true += 1
        else:
            flags_false += 1

    counted = [
        m for m in metrics
        if m.kind == "number" and any(w in m.leaf.lower() for w in count_words)
    ]

    return {
        "max_error": _max_abs(errors),
        "error_keys": [m.key for m in errors[:50]],
        "error_count": len(errors),
        "max_duration_s": _max_abs(durations),
        "duration_keys": [m.key for m in durations[:50]],
        "test_suites": suites,
        "test_total": max((s["total"] for s in suites), default=None),
        "test_passed": max((s["passed"] for s in suites), default=None),
        "test_failed": max((s["failed"] for s in suites), default=None),
        "test_pass_rate": (
            (max((s["passed"] for s in suites), default=0.0) / max((s["total"] for s in suites), default=0.0))
            if suites else None
        ),
        "pass_flags_true": flags_true,
        "pass_flags_false": flags_false,
        "pass_flag_keys": flag_keys[:200],
        "all_flags_true": (flags_false == 0 and flags_true > 0),
        "count_metrics": [{"key": m.key, "value": m.value} for m in counted[:50]],
        "figure_count": figure_count,
        "verified_case_count": len(verified_cases),
        "verified_cases": verified_cases[:100],
    }


def _max_abs(metrics: list[MetricRecord]) -> dict | None:
    best = None
    for m in metrics:
        if m.value is None:
            continue
        v = abs(float(m.value))
        if best is None or v > abs(float(best["value"])):
            best = {"value": float(m.value), "abs": v, "key": m.key, "source": m.source, "scope": m.scope}
    return best


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_ROLE_RANK = {"validation_report": 0, "criteria": 1, "validation_report_variant": 2, "report": 3, "json": 4, "other": 5, "figure": 6}


def _role_rank(role: str, path: Path, root: Path) -> int:
    return _ROLE_RANK.get(role, 6)


def role_of(evidence: NodeEvidence, source: str) -> str:
    """Role of the artifact a document pointer came from."""
    for a in evidence.artifacts:
        if a.path == source:
            return a.role
    return "other"


def scan_node_dir(root: str | Path, node_id: str | None = None, lex: dict | None = None) -> NodeEvidence:
    """Scan one node artifact directory and return the extracted evidence."""
    lex = lex or load_lexicon()
    root = Path(root)
    started = time.perf_counter()
    if not root.is_dir():
        return NodeEvidence(node_id=node_id or root.name, root=str(root),
                            warnings=[f"node_dir_missing:{root}"], elapsed_s=0.0)

    limits = lex.get("scan_limits", {})
    files, warnings = _candidate_files(root, lex)
    roles = lex.get("artifact_roles", {})

    artifacts: list[ArtifactRef] = []
    metrics: list[MetricRecord] = []
    documents: list[tuple[str, Any]] = []
    verified_cases: list[str] = []
    json_budget = int(limits.get("max_json_files", 40))
    size_cap = int(limits.get("max_json_bytes", 4_000_000))
    metric_cap = int(limits.get("max_metrics", 20000))

    figure_count = 0
    ordered = sorted(files, key=lambda p: (_role_rank(_classify(p, root, roles), p, root), p.as_posix()))
    for p in ordered:
        rel = p.relative_to(root).as_posix()
        role = _classify(p, root, roles)
        if p.suffix.lower() in _IMAGE_SUFFIXES:
            role = "figure"
            figure_count += 1
        if "verified" in rel.split("/")[:-1]:
            verified_cases.append(rel)
        try:
            size = p.stat().st_size
            digest = _sha256(p)
        except OSError as exc:  # pragma: no cover - defensive
            warnings.append(f"unreadable:{rel}:{exc}")
            continue
        if p.suffix.lower() != ".json":
            artifacts.append(ArtifactRef(path=rel, role=role, sha256=digest, bytes=size))
            continue
        artifacts.append(ArtifactRef(path=rel, role=role if role != "other" else "json", sha256=digest, bytes=size))
        if json_budget <= 0:
            warnings.append("json_file_cap_reached")
            continue
        if size > size_cap:
            warnings.append(f"json_too_large:{rel}")
            continue
        json_budget -= 1
        try:
            doc = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(f"json_parse_error:{rel}:{exc}")
            continue
        documents.append((rel, doc))
        metrics.extend(flatten_metrics(doc, rel, lex, max_metrics=max(0, metric_cap - len(metrics))))

    derived = derive(metrics, lex, figure_count, verified_cases)
    elapsed = time.perf_counter() - started
    derived["artifact_count"] = len(artifacts)
    derived["json_count"] = sum(1 for a in artifacts if a.path.endswith(".json"))
    derived["scanner_elapsed_s"] = round(elapsed, 3)

    return NodeEvidence(
        node_id=node_id or root.name,
        root=str(root),
        artifacts=artifacts,
        metrics=metrics,
        derived=derived,
        warnings=warnings,
        elapsed_s=elapsed,
        documents=documents,
    )