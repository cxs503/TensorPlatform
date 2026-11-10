#!/usr/bin/env python3
"""Acceptance runner for SolverProtocol v1 (node p337w1-n01-solver-adapter-protocol).

Everything the acceptance criteria claim is *measured here* and written to
artifacts, so the numbers in the report can be traced to a JSON file produced by
a command in this repository:

    C1  format coverage            -> artifacts/solver_adapter/format_coverage.json
    C2  mypy + abstract/concrete   -> artifacts/solver_adapter/mypy.txt + acceptance.json
    C3  contract test count/status -> acceptance.json (collected via pytest --collect-only)
    C4  conservation residuals     -> artifacts/solver_adapter/conservation_matrix.json
    C5  zero domain vocabulary     -> acceptance.json (hits list, must be empty)
    C6  backward compatibility     -> acceptance.json (files touched outside the new package)
    C7  documents                  -> acceptance.json (paths + byte sizes)

Usage::

    python3 tools/solver_adapter/acceptance.py            # write artifacts
    python3 tools/solver_adapter/acceptance.py --dry-run  # print only
    python3 tools/solver_adapter/acceptance.py --figures  # also render the PNG
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:        # allow `python3 tools/solver_adapter/acceptance.py`
    sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "tools" / "solver_adapter"
TESTS = ROOT / "tests" / "test_solver_adapter"
DOCS = ROOT / "docs" / "solver_adapter"
ARTIFACTS = ROOT / "artifacts" / "solver_adapter"
SCHEMA = ROOT / "schemas" / "solver_protocol_v1.json"

TOL = 1e-3


def _run(cmd: List[str]) -> Tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# --------------------------------------------------------------------- C1
def coverage_artifact(dry_run: bool) -> Dict[str, Any]:
    from tools.solver_adapter import coverage_json, scan_coverage

    report = scan_coverage()
    if not dry_run:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "format_coverage.json").write_text(coverage_json(report), encoding="utf-8")
    return dict(report)


# --------------------------------------------------------------------- C2
def mypy_artifact(dry_run: bool) -> Dict[str, Any]:
    code, output = _run([sys.executable, "-m", "mypy", "tools/solver_adapter",
                         "--ignore-missing-imports", "--no-error-summary"])
    own_errors = [line for line in output.splitlines() if line.startswith("tools/solver_adapter")]
    if not dry_run:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "mypy.txt").write_text(
            f"$ python3 -m mypy tools/solver_adapter --ignore-missing-imports\n"
            f"exit={code}\n"
            f"errors in tools/solver_adapter: {len(own_errors)}\n\n{output}\n",
            encoding="utf-8",
        )
    from tools.solver_adapter import SolverAdapter

    abstract = sorted(SolverAdapter.__abstractmethods__)
    concrete = sorted(
        name
        for name, value in vars(SolverAdapter).items()
        if not name.startswith("_") and callable(value)
        and not getattr(value, "__isabstractmethod__", False)
    )
    return {
        "mypy_exit_code": code,
        "errors_in_package": len(own_errors),
        "abstract_methods": abstract,
        "abstract_count": len(abstract),
        "concrete_methods": concrete,
        "concrete_count": len(concrete),
    }


# --------------------------------------------------------------------- C3
def test_artifact() -> Dict[str, Any]:
    code, output = _run([sys.executable, "-m", "pytest", "tests/test_solver_adapter",
                         "--collect-only", "-q", "--tb=no"])
    match = re.search(r"(\d+)\s+tests?\s+collected", output)
    collected = int(match.group(1)) if match else 0
    return {"collect_exit_code": code, "collected_cases": collected,
            "collect_tail": output.splitlines()[-1] if output.splitlines() else ""}


def run_tests() -> Dict[str, Any]:
    code, output = _run([sys.executable, "-m", "pytest", "tests/test_solver_adapter", "-q",
                         "--tb=no"])
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for label in counts:
        match = re.search(r"(\d+)\s+" + label, output)
        if match:
            counts[label] = int(match.group(1))
    return {"exit_code": code, **counts,
            "tail": output.splitlines()[-1] if output.splitlines() else ""}


# --------------------------------------------------------------------- C4
def conservation_artifact(dry_run: bool) -> Dict[str, Any]:
    from tools.solver_adapter import (
        SOLVER_IDS,
        MappingMode,
        MappingSpec,
        ExchangeRequest,
        build_adapter,
        map_field,
    )
    from tools.coupling.mesh import BoxMesh

    rows: List[Dict[str, Any]] = []
    for solver_id in SOLVER_IDS:
        source = build_adapter(solver_id, n_cells=4)
        source.init()
        spec = source._spec("density")
        for n_dst in (4, 5, 6, 8, 12):
            target_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (n_dst, n_dst, n_dst))
            request = ExchangeRequest(
                solver_id=solver_id,
                version="1.0.0",
                target_solver="dem" if solver_id != "dem" else "lbm",
                field_name="density",
                mapping=MappingSpec(mode=MappingMode.CONSERVATIVE, operator="overlap_l2",
                                    sum_tol=TOL),
            )
            result = map_field(source.export_field("density"), spec, spec, target_mesh, request)
            rows.append({
                "solver_id": solver_id,
                "field": "density",
                "kind": spec.kind,
                "src_cells": source.mesh.n_cells,
                "dst_cells": target_mesh.n_cells,
                "sum_rel_residual": result.metrics.sum_rel_residual,
                "moment_rel_residual": result.metrics.moment_rel_residual,
                "passed": result.passed,
            })

    # flux quantities: refine tangentially so the surface measure is preserved
    flux_rows: List[Dict[str, Any]] = []
    for solver_id in ("fvm", "dg", "bem"):
        source = build_adapter(solver_id, n_cells=4)
        source.init()
        spec = source._spec("flux_x")
        for factor in (2, 3):
            target_mesh = source.mesh.refined((1, factor, factor))
            request = ExchangeRequest(
                solver_id=solver_id,
                version="1.0.0",
                target_solver="dg" if solver_id != "dg" else "fvm",
                field_name="flux_x",
                mapping=MappingSpec(mode=MappingMode.CONSERVATIVE, operator="overlap_l2"),
            )
            result = map_field(source.export_field("flux_x"), spec, spec, target_mesh, request)
            flux_rows.append({
                "solver_id": solver_id,
                "field": "flux_x",
                "kind": spec.kind,
                "tangential_factor": factor,
                "sum_rel_residual": result.metrics.sum_rel_residual,
                "passed": result.passed,
            })

    # precision / conservation trade-off: refine source *and* destination
    tradeoff: List[Dict[str, Any]] = []
    lossy: List[Dict[str, Any]] = []
    for n_src in (4, 6, 8, 12):
        source_n = build_adapter("lbm", n_cells=n_src)
        source_n.init()
        spec_n = source_n._spec("density")
        source_n._fields["density"] = 1.0 + 2.0 * source_n.mesh.centers[:, 0]
        target_mesh = source_n.mesh.refined(2)
        exact = 1.0 + 2.0 * target_mesh.centers[:, 0]
        for mode, operator in ((MappingMode.CONSERVATIVE, "overlap_l2"),
                               (MappingMode.STANDARD, "overlap_p1")):
            request = ExchangeRequest(solver_id="lbm", version="1.0.0", target_solver="dem",
                                      field_name="density",
                                      mapping=MappingSpec(mode=mode, operator=operator))
            result = map_field(source_n.export_field("density"), spec_n, spec_n, target_mesh,
                               request)
            tradeoff.append({
                "mode": mode.value,
                "operator": operator,
                "src_cells": source_n.mesh.n_cells,
                "dst_cells": target_mesh.n_cells,
                "source_cell_size": float(source_n.mesh.length_scale),
                "max_abs_error_vs_linear": float(np.max(np.abs(result.field.values - exact))),
                "sum_rel_residual": result.metrics.sum_rel_residual,
            })
        # negative control: a *non-nested* destination (odd resolution), where the
        # cell volumes do not match, so nearest transfer without the correction
        # must lose (or gain) mass well beyond the tolerance
        odd = 2 * n_src + 1
        lossy_mesh = BoxMesh.uniform((0, 0, 0), (1, 1, 1), (odd, odd, odd))
        request = ExchangeRequest(solver_id="lbm", version="1.0.0", target_solver="dem",
                                  field_name="density",
                                  mapping=MappingSpec(mode=MappingMode.STANDARD,
                                                      operator="nearest_scaled"))
        result = map_field(source_n.export_field("density"), spec_n, spec_n, lossy_mesh, request,
                           apply_conservation_correction=False)
        lossy.append({
            "operator": "nearest_scaled",
            "conservation_correction": False,
            "src_cells": source_n.mesh.n_cells,
            "dst_cells": lossy_mesh.n_cells,
            "sum_rel_residual": result.metrics.sum_rel_residual,
            "passed": result.passed,
        })

    payload = {
        "protocol": "solver-protocol/v1",
        "tolerance": TOL,
        "density_conservative": rows,
        "flux_conservative": flux_rows,
        "worst_density_residual": max(r["sum_rel_residual"] for r in rows),
        "worst_flux_residual": max(r["sum_rel_residual"] for r in flux_rows),
        "tradeoff": tradeoff,
        "negative_control": lossy,
        "worst_negative_control_residual": max(r["sum_rel_residual"] for r in lossy),
        "all_density_passed": all(r["passed"] for r in rows),
        "all_flux_passed": all(r["passed"] for r in flux_rows),
        "negative_control_detected": any(not r["passed"] for r in lossy),
    }
    if not dry_run:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "conservation_matrix.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return payload


# --------------------------------------------------------------------- C5
def vocabulary_artifact() -> Dict[str, Any]:
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    targets = [str(p.relative_to(ROOT)) for p in sorted(PACKAGE.rglob("*.py"))]
    targets += [str(p.relative_to(ROOT)) for p in sorted(TESTS.glob("*.py"))]
    targets += [str(p.relative_to(ROOT)) for p in sorted(DOCS.glob("*.md"))]
    targets += [str(SCHEMA.relative_to(ROOT))]
    result = scan_paths(targets, lexicon=load_lexicon(), root=ROOT)
    return {"scanned_files": result["scanned_files"], "hits": result["hits"],
            "targets": targets}


# --------------------------------------------------------------------- C6
def compatibility_artifact() -> Dict[str, Any]:
    code, output = _run(["git", "status", "--porcelain"])
    mine = ("tools/solver_adapter/", "tests/test_solver_adapter/", "docs/solver_adapter/",
            "artifacts/solver_adapter/", "schemas/solver_protocol_v1.json")
    entries = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status, path = line[:2].strip(), line[3:].strip()
        entries.append((status, path))
    new_only = [p for _, p in entries if p.startswith(mine)]
    modified_existing = [p for status, p in entries
                         if not p.startswith(mine) and status != "??"]
    unrelated_untracked = [p for status, p in entries
                           if not p.startswith(mine) and status == "??"]
    return {
        "files_touched": [p for _, p in entries],
        "new_only": new_only,
        "modified_existing": modified_existing,
        "unrelated_untracked_present": unrelated_untracked,
        "existing_coupling_suite_exit_code": _run(
            [sys.executable, "-m", "pytest", "tests/test_coupling_remap.py",
             "tests/test_coupling_exchange_gate.py", "tests/test_multiphysics_coupling.py",
             "-q"])[0],
        "note": (
            "the eight solver repositories are separate checkouts and are not present in "
            "this worktree, so their test suites cannot be run here; backward compatibility "
            "is argued structurally (no tracked file outside the new package is modified) "
            "and by re-running the pre-existing coupling suite"
        ),
    }


# --------------------------------------------------------------------- C7
def documents_artifact() -> Dict[str, Any]:
    docs = {}
    for name in ("PROTOCOL_V1.md", "ADAPTER_GUIDE.md", "CONTRACT_TEST_REPORT.md"):
        path = DOCS / name
        docs[name] = {"path": str(path.relative_to(ROOT)),
                      "exists": path.is_file(),
                      "bytes": path.stat().st_size if path.is_file() else 0}
    return docs


# ------------------------------------------------------------------ figure
def render_figure(conservation: Dict[str, Any], outdir: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(11.0, 4.2), dpi=150)

    # (a) conservation residual vs destination cell count
    density = conservation["density_conservative"]
    xs = sorted({row["dst_cells"] for row in density})
    for solver_id in sorted({row["solver_id"] for row in density}):
        series = [row for row in density if row["solver_id"] == solver_id]
        series.sort(key=lambda r: r["dst_cells"])
        axes[0].semilogy([r["dst_cells"] for r in series],
                         [max(r["sum_rel_residual"], 1e-18) for r in series],
                         marker="o", markersize=3, linewidth=1.0, label=solver_id)
    lossy = sorted(conservation["negative_control"], key=lambda r: r["dst_cells"])
    axes[0].semilogy([r["dst_cells"] for r in lossy],
                     [max(r["sum_rel_residual"], 1e-18) for r in lossy],
                     marker="x", linestyle="--", color="crimson",
                     label="lossy control (nearest, no fix)")
    axes[0].axhline(conservation["tolerance"], color="black", linestyle=":",
                    label="protocol tolerance 1e-3")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("destination cells (source 4^3)")
    axes[0].set_ylabel("relative total residual  |dS| / S")
    axes[0].set_title("(a) conservative mapping vs lossy control")
    axes[0].grid(alpha=0.3, which="both")
    axes[0].legend(fontsize=7, ncol=2)

    # (b) accuracy on a linear field: conservative vs standard
    for mode in ("conservative", "standard"):
        series = [r for r in conservation["tradeoff"] if r["mode"] == mode]
        series.sort(key=lambda r: r["dst_cells"])
        axes[1].loglog([r["dst_cells"] for r in series],
                       [max(r["max_abs_error_vs_linear"], 1e-18) for r in series],
                       marker="s", linewidth=1.2, label=f"{mode} ({series[0]['operator']})")
    axes[1].set_xlabel("destination cells (source 4^3)")
    axes[1].set_ylabel("max |value - linear exact|")
    axes[1].set_title("(b) accuracy on a linear field")
    axes[1].grid(alpha=0.3, which="both")
    axes[1].legend(fontsize=8)

    figure.suptitle("SolverProtocol v1 - conservation and accuracy of field exchange",
                    fontsize=10)
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "conservation_vs_accuracy.png"
    figure.savefig(target)
    plt.close(figure)
    return target


# --------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--figures", action="store_true")
    parser.add_argument("--json-out", default=str(ARTIFACTS / "acceptance.json"))
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip the pytest run (use when the caller already ran it)")
    args = parser.parse_args()

    coverage = coverage_artifact(args.dry_run)
    mypy = mypy_artifact(args.dry_run)
    collected = test_artifact()
    tests = {"skipped": True} if args.skip_tests else run_tests()
    conservation = conservation_artifact(args.dry_run)
    vocabulary = vocabulary_artifact()
    compatibility = compatibility_artifact()
    documents = documents_artifact()

    figure_path = None
    if args.figures:
        figure_path = render_figure(conservation, ARTIFACTS / "figures")
    elif (ARTIFACTS / "figures" / "conservation_vs_accuracy.png").is_file():
        figure_path = ARTIFACTS / "figures" / "conservation_vs_accuracy.png"

    criteria = {
        "C1_format_coverage": {
            "pass": bool(coverage["coverage"] == 1.0 and coverage["ok"]),
            "coverage": coverage["coverage"],
            "records": f"{coverage['records_mapped']}/{coverage['records_total']}",
            "verified_against_repo": coverage["verified_against_repo"],
            "artifact": "artifacts/solver_adapter/format_coverage.json",
        },
        "C2_abstract_base_class": {
            "pass": bool(mypy["errors_in_package"] == 0
                         and mypy["abstract_count"] == 4 and mypy["concrete_count"] == 8),
            "mypy_errors_in_package": mypy["errors_in_package"],
            "abstract_count": mypy["abstract_count"],
            "concrete_count": mypy["concrete_count"],
            "artifact": "artifacts/solver_adapter/mypy.txt",
        },
        "C3_contract_tests": {
            "pass": bool(collected.get("collected_cases", 0) >= 100
                         and tests.get("failed", 0) == 0
                         and tests.get("exit_code", 1) == 0),
            "collected_cases": collected.get("collected_cases"),
            "passed": tests.get("passed"),
            "failed": tests.get("failed"),
            "skipped": tests.get("skipped"),
            "artifact": "artifacts/solver_adapter/acceptance.json",
        },
        "C4_conservation": {
            "pass": bool(conservation["all_density_passed"] and conservation["all_flux_passed"]
                         and conservation["worst_density_residual"] < TOL
                         and conservation["worst_flux_residual"] < TOL
                         and conservation["negative_control_detected"]),
            "worst_density_residual": conservation["worst_density_residual"],
            "worst_flux_residual": conservation["worst_flux_residual"],
            "worst_negative_control_residual": conservation["worst_negative_control_residual"],
            "tolerance": TOL,
            "artifact": "artifacts/solver_adapter/conservation_matrix.json",
        },
        "C5_zero_domain_vocabulary": {
            "pass": bool(vocabulary["hits"] == []),
            "hits": len(vocabulary["hits"]),
            "files_scanned": vocabulary["scanned_files"],
        },
        "C6_backward_compatibility": {
            "pass": bool(compatibility["modified_existing"] == []
                         and compatibility["existing_coupling_suite_exit_code"] == 0),
            "modified_existing_files": compatibility["modified_existing"],
            "existing_coupling_suite_exit_code": compatibility["existing_coupling_suite_exit_code"],
            "limitation": compatibility["note"],
        },
        "C7_documents": {
            "pass": bool(all(d["exists"] and d["bytes"] > 500 for d in documents.values())),
            "documents": documents,
        },
    }

    payload = {
        "node": "p337w1-n01-solver-adapter-protocol",
        "protocol": "solver-protocol/v1",
        "criteria": criteria,
        "all_pass": all(item["pass"] for item in criteria.values()),
        "figure": str(figure_path.relative_to(ROOT)) if figure_path else None,
        "honesty": {
            "native_impl": False,
            "note": (
                "the eight adapters implement the protocol against a synthetic test kernel; "
                "no real library was executed and no library repository was inspected"
            ),
        },
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.dry_run:
        print(text)
        return 0
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(textwrap.indent(f"wrote {args.json_out}", ""))
    print(json.dumps({k: v["pass"] for k, v in criteria.items()}, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())