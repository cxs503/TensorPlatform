#!/usr/bin/env python3
"""Geometry validation-debt audit for the benchmark reference dataset.

Reads (never mutates) the benchmark dataset and the hand-typed ``SHIP_GEOMETRY``
presets, applies the Cb/L/B/T identity gate from
``backend/api/domain/geometry_consistency.py`` and writes a machine-generated
audit report.  Also records which parameters the resistance API actually
consumes (AST inspection of ``ResistanceRequest``) so that a parameter the
harness sends but the API silently drops cannot go unnoticed again.

Usage:
    python3 tools/ship_perf/audit_benchmark_geometry.py \
        --out artifacts/m1404/geometry_audit.json \
        --csv artifacts/m1404/geometry_audit.csv

No network, no database, no heavy compute.
"""
from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
DOMAIN = BACKEND / "api" / "domain"


def _load(name: str, path: Path):
    """Import a module straight from a file path (skips package __init__ / DB)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # required for dataclasses to resolve types
    spec.loader.exec_module(mod)
    return mod


def resistance_request_fields(calc_path: Path) -> List[str]:
    """Names of the fields of ``ResistanceRequest`` (AST only, no import)."""
    tree = ast.parse(calc_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ResistanceRequest":
            out = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    out.append(stmt.target.id)
            return out
    return []


def harness_payload_keys(bench_path: Path) -> List[str]:
    """Keys of the JSON payload built by ``_call_resistance_api`` (AST only)."""
    tree = ast.parse(bench_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_call_resistance_api":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "payload" for t in sub.targets
                ):
                    if isinstance(sub.value, ast.Dict):
                        return [k.value for k in sub.value.keys
                                if isinstance(k, ast.Constant)]
    return []


def build_audit() -> Dict[str, Any]:
    # NB: do NOT put backend/api/domain on sys.path -- it contains platform.py,
    # which shadows the stdlib ``platform`` module imported by uuid/pydantic.
    # Loading geometry_consistency under its real module name is enough for the
    # fallback ``from geometry_consistency import ...`` inside ship_benchmark.
    gc = _load("geometry_consistency", DOMAIN / "geometry_consistency.py")
    sb = _load("ship_benchmark", DOMAIN / "ship_benchmark.py")

    dataset = json.loads((BACKEND / "data" / "benchmark_reference.json").read_text())
    report = gc.audit_dataset(dataset, sb.SHIP_GEOMETRY)

    api_fields = resistance_request_fields(BACKEND / "api" / "calculations.py")
    payload_keys = harness_payload_keys(DOMAIN / "ship_benchmark.py")
    dead = [k for k in payload_keys if k not in api_fields]

    report["api_contract"] = {
        "endpoint": "POST /api/v1/calculations/resistance",
        "request_fields": api_fields,
        "harness_payload_keys": payload_keys,
        "silently_dropped_keys": dead,
        "note": ("Harness keys absent from ResistanceRequest are discarded by the "
                 "pydantic model, so the resistance number cannot depend on them. "
                 "Geometry fields that only exist in the harness therefore never "
                 "reach the solver and must not be treated as validated by it."),
    }
    report["gate_rule"] = (
        "Cb_stated must equal displacement_volume/(L*B*T) within "
        f"{gc.DEFAULT_TOL_PCT}% (WARN up to {gc.DEFAULT_WARN_PCT}%); "
        "bodies of revolution use Cv = V/(pi/4*D^2*L) or, when the volume is not "
        "published, the wetted-surface ratio S/(pi*D*L)."
    )
    return report


def write_csv(report: Dict[str, Any], path: Path) -> None:
    rows = report["dataset_geometry"] + report["presets"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["label", "scheme", "verdict", "implied_coef", "stated_coef",
                    "rel_dev_pct", "volume_m3", "note"])
        for r in rows:
            w.writerow([r["label"], r["scheme"], r["verdict"], r["implied_cb"],
                        r["stated_cb"], r["rel_dev_pct"], r["volume_m3"], r["note"]])


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "artifacts" / "m1404" / "geometry_audit.json"))
    ap.add_argument("--csv", default=str(REPO / "artifacts" / "m1404" / "geometry_audit.csv"))
    args = ap.parse_args(argv)

    report = build_audit()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    write_csv(report, Path(args.csv))

    ds, ps = report["dataset_summary"], report["preset_summary"]
    print(f"dataset  : {ds['counts']} max|dev|={ds['max_abs_dev_pct']}% ({ds['max_abs_dev_label']})")
    print(f"presets  : {ps['counts']} max|dev|={ps['max_abs_dev_pct']}% ({ps['max_abs_dev_label']})")
    print(f"dropped  : {report['api_contract']['silently_dropped_keys']}")
    print(f"written  : {out}")

    # Exit status separates a numerical inconsistency (FAIL, must be fixed) from
    # an acknowledged approximation (INVALID whose geometry_status is declared).
    rows = report["dataset_geometry"] + report["presets"]
    hard_fail = ds["counts"].get("FAIL", 0) + ps["counts"].get("FAIL", 0)
    undeclared = sum(1 for r in rows if r["verdict"] == "INVALID"
                     and not (r.get("values") or {}).get("geometry_status"))
    if hard_fail:
        print(f"FAIL     : {hard_fail} geometr(ies) inconsistent with their stated Cb")
        return 2
    if undeclared:
        print(f"UNDECLARED: {undeclared} unrecognised geometry record(s)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())