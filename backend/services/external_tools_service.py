"""Service layer for external tool-class onboarding (platform mechanism).

Keeps the HTTP face thin: the service owns *where* the spec and the ledger live
(``TENSOR_EXTERNAL_TOOLS_SPEC`` / ``TENSOR_EXTERNAL_TOOLS_DIR``, so a deployment
points them at its own configuration), and exposes three read/write operations:

* :meth:`catalog` -- declared classes + the *last observed* status of each, with a
  freshness flag.  Declared ≠ observed, so the two are reported separately;
* :meth:`coverage` -- the KPI-shaped answer: how many classes reached REAL against
  the declared target;
* :meth:`probe` -- run the harness now (optionally for a subset) and, when asked,
  persist the ledger through :func:`tools.external_tools.report.write_artifacts`.

Zero domain semantics: tool ids/categories/thresholds arrive from the spec file.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # pragma: no cover - import-path fallback for deploy
    sys.path.insert(0, str(_ROOT))

from tools.external_tools import benchmark as benchmark_mod  # noqa: E402
from tools.external_tools import harness, report, spec as spec_mod  # noqa: E402

DEFAULT_SPEC = _ROOT / "data" / "external_tools" / "classes.json"
DEFAULT_LEDGER_DIR = _ROOT / "artifacts" / "external_tools"


class ExternalToolsError(RuntimeError):
    """Spec or ledger unusable; the HTTP layer turns this into a 4xx/5xx."""


def spec_path() -> Path:
    return Path(os.environ.get("TENSOR_EXTERNAL_TOOLS_SPEC") or DEFAULT_SPEC)


def ledger_dir() -> Path:
    return Path(os.environ.get("TENSOR_EXTERNAL_TOOLS_DIR") or DEFAULT_LEDGER_DIR)


def load_spec() -> spec_mod.Suite:
    try:
        return spec_mod.load_suite(spec_path())
    except spec_mod.SpecError as exc:
        raise ExternalToolsError(str(exc)) from exc


def latest_ledger() -> Optional[Dict[str, Any]]:
    path = ledger_dir() / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # corrupt artifact -> degrade, do not guess
        raise ExternalToolsError(f"ledger not JSON: {path}: {exc}") from exc


def _age_seconds(stamp: Any) -> Optional[float]:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def catalog() -> Dict[str, Any]:
    suite = load_spec()
    ledger = latest_ledger()
    observed: Dict[str, Mapping[str, Any]] = {}
    for rec in (ledger or {}).get("classes", []):
        observed[str(rec.get("tool_id"))] = rec
    classes: List[Dict[str, Any]] = []
    for cls in suite.classes:
        rec = observed.get(cls.tool_id, {})
        classes.append({
            "tool_id": cls.tool_id,
            "category": cls.category,
            "adapter": cls.adapter,
            "required": cls.required,
            "probe": dict(cls.probe),
            "invariants": [inv.to_dict() for inv in cls.invariants],
            "fallback": cls.fallback,
            "last_status": rec.get("status") or "UNOBSERVED",
            "last_detail": rec.get("detail", ""),
            "last_metrics": rec.get("metrics", {}),
        })
    age = _age_seconds((ledger or {}).get("generated_at"))
    return {
        "suite_id": suite.suite_id,
        "spec_path": str(spec_path()),
        "spec_version": spec_mod.SPEC_VERSION,
        "target_classes": suite.target_classes,
        "n_classes": len(suite.classes),
        "classes": classes,
        "ledger": {
            "present": ledger is not None,
            "generated_at": (ledger or {}).get("generated_at"),
            "age_seconds": None if age is None else round(age, 1),
            "digest": (ledger or {}).get("digest"),
            "value_digest": (ledger or {}).get("value_digest"),
            "verdict": (ledger or {}).get("verdict"),
        },
    }


def coverage() -> Dict[str, Any]:
    """KPI answer, from the persisted ledger only (no silent live probing)."""

    suite = load_spec()
    ledger = latest_ledger()
    if ledger is None:
        raise ExternalToolsError(
            f"no ledger under {ledger_dir()}; run tools.external_tools.cli run first")
    cov = dict(ledger.get("coverage") or {})
    meas = dict(ledger.get("measured") or {})
    age = _age_seconds(ledger.get("generated_at"))
    return {
        "suite_id": ledger.get("suite_id"),
        "target_classes": cov.get("target_classes", suite.target_classes),
        "real_classes": cov.get("real_classes", 0),
        "shortfall": cov.get("shortfall"),
        "ok": bool(cov.get("ok")),
        "verdict": ledger.get("verdict"),
        "real_tool_ids": meas.get("real_tool_ids", []),
        "real_categories": meas.get("real_categories", []),
        "counts_by_status": meas.get("counts_by_status", {}),
        "generated_at": ledger.get("generated_at"),
        "age_seconds": None if age is None else round(age, 1),
        "digest": ledger.get("digest"),
        "value_digest": ledger.get("value_digest"),
    }


def probe(tool_ids: Optional[Sequence[str]] = None, persist: bool = False,
          repo: Optional[str] = None) -> Dict[str, Any]:
    suite = load_spec()
    only = list(tool_ids or []) or None
    if only:
        unknown = sorted(set(only) - {c.tool_id for c in suite.classes})
        if unknown:
            raise ExternalToolsError(f"unknown tool ids: {unknown}")
    ctx = {"repo": str(repo or _ROOT),
           "out_dir": str(ledger_dir()),
           "dotenv": os.environ.get("EXTERNAL_TOOLS_DOTENV", "")}
    ledger = harness.run_suite(suite, only=only, ctx=ctx)
    ledger["verdict"] = harness.verdict(ledger)
    ledger["partial"] = bool(only)
    artifacts: Dict[str, str] = {}
    if persist and not only:
        artifacts = report.write_artifacts(ledger, ledger_dir(), stem="external_tools")
    ledger["artifacts"] = artifacts
    return ledger

def benchmark() -> Dict[str, Any]:
    """ref-vs-got scoring table over the *persisted* ledger (no silent live run).

    Fail-closed: without a ledger there is nothing to score, so the caller gets an
    error instead of an empty table that could read as "all good".
    """

    suite = load_spec()
    ledger = latest_ledger()
    if ledger is None:
        raise ExternalToolsError(
            f"no ledger under {ledger_dir()}; run tools.external_tools.cli run first")
    table = benchmark_mod.build(ledger, suite)
    table["ledger_dir"] = str(ledger_dir())
    return table
