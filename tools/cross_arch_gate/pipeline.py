"""End-to-end pipeline: build arms -> compare -> mutate -> audit -> criteria.

Ordering matters: the criterion block is assembled *last*, from artifacts that
already exist on disk, so every number in the verdict file has a file to point at.
Timing is taken around the whole compute path (arm construction, comparisons,
mutation matrix, audit) and reported as the latency criterion; nothing sleeps and
nothing touches the network, so the number is reproducible.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import audit, criteria, gate, record as record_mod, selftest

#: Arm matrix built on the node that runs the pipeline.  ``ref`` is the plain
#: native build; the rest are controlled variants, each isolating one mechanism
#: that is known to differ between CPU implementations (association order,
#: contraction, libm route) plus the portable IEEE oracle.
ARM_MATRIX: Tuple[Tuple[str, Dict[str, str]], ...] = (
    ("native-reference", {"arith": "native", "sum": "naive", "libm": "stdlib"}),
    ("native-rerun", {"arith": "native", "sum": "naive", "libm": "stdlib"}),
    ("ieee-oracle", {"arith": "exact", "sum": "fsum", "libm": "stdlib"}),
    ("fma-contract", {"arith": "fma", "sum": "naive", "libm": "stdlib"}),
    ("assoc-reverse", {"arith": "native", "sum": "reverse", "libm": "stdlib"}),
    ("assoc-fsum", {"arith": "native", "sum": "fsum", "libm": "stdlib"}),
    ("libm-altroute", {"arith": "native", "sum": "naive", "libm": "alt"}),
)


def build_arms(outdir: Path, with_rerun: bool = True) -> Dict[str, Path]:
    arms_dir = Path(outdir) / "arms"
    arms_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for label, variant in ARM_MATRIX:
        if label == "native-rerun" and not with_rerun:
            continue
        rec = record_mod.build_record(label, arith=variant["arith"],
                                      sum_variant=variant["sum"], libm=variant["libm"])
        paths[label] = record_mod.save_record(rec, arms_dir / f"{label}.json")
    return paths


def run(outdir: Path, policy_path: Optional[Path] = None,
        external_arms: Optional[Mapping[str, Path]] = None,
        make_figures: bool = True) -> Dict[str, Any]:
    started = time.perf_counter()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    policy = gate.load_policy(policy_path)

    arms = build_arms(outdir)
    external_arms = dict(external_arms or {})
    records = {label: record_mod.load_record(path) for label, path in arms.items()}
    for label, path in external_arms.items():
        records[label] = record_mod.load_record(Path(path))

    ref_label = "native-reference"
    ref = records[ref_label]

    pairs: List[Dict[str, Any]] = []
    per_pair_paths: Dict[str, str] = {}
    for label, rec in records.items():
        if label == ref_label:
            continue
        report = gate.compare(ref, rec, policy)
        path = gate.save_report(report, outdir / "pairs" / f"{label}.json")
        per_pair_paths[label] = str(path)
        pairs.append(report)

    mutation = selftest.run_matrix(ref, {k: v for k, v in records.items() if k != ref_label}, policy)
    mutation_path = outdir / "mutation_matrix.json"
    mutation_path.write_text(json.dumps(mutation, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit_report = audit.scan()
    audit_path = audit.save(audit_report, outdir / "domain_audit.json")

    latency = time.perf_counter() - started          # compute path, before figures

    criteria_report = criteria.assemble(
        policy, pairs, mutation, latency, audit_report,
        extra={"gate_report": str(outdir / "pairs"), "selftest_report": str(mutation_path),
               "audit_report": str(audit_path), "pipeline": "tools.cross_arch_gate.pipeline"})
    criteria_path = criteria.save(criteria_report, outdir / "criteria.json")

    figure_paths: List[str] = []
    if make_figures:
        from . import figures
        figure_paths = [str(p) for p in figures.make_all(outdir, records, pairs, mutation, criteria_report)]

    return {"outdir": str(outdir), "arms": {k: str(v) for k, v in arms.items()},
            "external_arms": {k: str(v) for k, v in external_arms.items()},
            "pairs": per_pair_paths, "mutation": str(mutation_path),
            "audit": str(audit_path), "criteria": str(criteria_path),
            "figures": figure_paths, "all_pass": criteria_report["all_pass"],
            "latency_s": round(latency, 3)}