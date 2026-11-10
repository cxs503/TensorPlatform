#!/usr/bin/env python3
"""Anchor study: exercises the harness end-to-end and writes auditable numbers.

Three experiments, all with synthetic fixtures:

``A``  run the shipped case package (6 cases / 10 quantities) -> error table
``B``  quadrature sweep on the synthetic parabolic form -> measured order of
       accuracy versus the theoretical ``p = 2`` of the composite midpoint rule
``C``  tamper experiment -> detection rate per tamper class and over random
       single-byte mutations (criterion A3)

Outputs (deterministic, no wall clock)::

    artifacts/verification_harness/bundle/           # evidence bundle of A
    artifacts/verification_harness/integrity.md      # verifier adjudication of that bundle
    artifacts/verification_harness/study.json        # every number used by the report/figures
    artifacts/verification_harness/study.md          # human-readable mirror

Usage::

    python3 -m tools.verification_harness.study --out artifacts/verification_harness
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

from .anchors import synthetic
from .evidence import (
    MANIFEST_NAME,
    environment_digest,
    environment_fingerprint,
    sha256_file,
    verify_bundle,
    verification_markdown,
    write_bundle,
)
from .model import SCHEMA_VERSION
from .registry import CaseRegistry
from .runner import run_registry, write_run_report

PACKAGE_DIR = Path(__file__).resolve().parent
CASE_DIR = PACKAGE_DIR / "cases"
DEFAULT_OUT = "artifacts/verification_harness"


def _copy_tree(source: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    return target


def _tamper_trials(bundle: Path, workdir: Path, trials: int, seed: int) -> Dict[str, Any]:
    """Count detections for random single-byte mutations and for structural tampering."""
    rng = random.Random(seed)
    artifacts = sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != MANIFEST_NAME)
    detected = 0
    misses: List[Dict[str, Any]] = []
    for index in range(trials):
        source = artifacts[index % len(artifacts)]
        copy = _copy_tree(bundle, workdir / f"mut{index}")
        target = copy / source.relative_to(bundle)
        payload = bytearray(target.read_bytes())
        position = rng.randrange(len(payload))
        payload[position] ^= 1 << rng.randrange(8)
        target.write_bytes(bytes(payload))
        verdict = verify_bundle(copy)
        hit = (not verdict.ok) and target.relative_to(copy).as_posix() in verdict.content_tampered
        if hit:
            detected += 1
        else:
            misses.append({"artifact": source.relative_to(bundle).as_posix(), "offset": position})
        shutil.rmtree(copy, ignore_errors=True)

    classes: Dict[str, Any] = {}

    copy = _copy_tree(bundle, workdir / "missing")
    victim = sorted((copy / "cases").glob("*.json"))[0]
    victim.unlink()
    verdict = verify_bundle(copy)
    classes["missing_artifact"] = {"detected": bool(not verdict.ok and verdict.missing),
                                   "detail": list(verdict.missing)}
    shutil.rmtree(copy, ignore_errors=True)

    copy = _copy_tree(bundle, workdir / "extra")
    (copy / "cases" / "injected_record.json").write_text("{}\n", encoding="utf-8")
    verdict = verify_bundle(copy)
    classes["injected_artifact"] = {"detected": bool(not verdict.ok and verdict.extra),
                                    "detail": list(verdict.extra)}
    shutil.rmtree(copy, ignore_errors=True)

    copy = _copy_tree(bundle, workdir / "chain")
    manifest = json.loads((copy / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["entries"][0]["chain_hash"] = "0" * 64
    (copy / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(copy)
    classes["chain_tamper"] = {"detected": bool(not verdict.ok and verdict.chain_broken),
                               "detail": list(verdict.chain_broken)}
    shutil.rmtree(copy, ignore_errors=True)

    copy = _copy_tree(bundle, workdir / "rehash")
    victim = sorted((copy / "cases").glob("*.json"))[0]
    payload = bytearray(victim.read_bytes())
    payload[0] ^= 0xFF
    victim.write_bytes(bytes(payload))
    manifest = json.loads((copy / MANIFEST_NAME).read_text(encoding="utf-8"))
    rel = victim.relative_to(copy).as_posix()
    for entry in manifest["entries"]:
        if entry["path"] == rel:
            entry["sha256"] = sha256_file(victim)
            entry["size"] = victim.stat().st_size
    (copy / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = verify_bundle(copy)
    classes["forged_recorded_hash"] = {"detected": bool(not verdict.ok and "CHAIN" in verdict.tamper_classes),
                                       "detail": verdict.tamper_classes}
    shutil.rmtree(copy, ignore_errors=True)

    class_hits = sum(1 for value in classes.values() if value["detected"])
    total = trials + len(classes)
    hits = detected + class_hits
    return {
        "trials": trials,
        "byte_flip_detected": detected,
        "byte_flip_rate": detected / trials if trials else 0.0,
        "misses": misses,
        "classes": classes,
        "class_detected": class_hits,
        "class_total": len(classes),
        "detected_total": hits,
        "trials_total": total,
        "detection_rate": hits / total if total else 0.0,
    }


def _convergence_study() -> Dict[str, Any]:
    """Composite midpoint rule on the synthetic parabolic form -> O(h^2)."""
    length, beam, draft = 60.0, 9.0, 3.2
    exact = synthetic.parabolic_form_closed_form(length, beam, draft)
    rows: List[Dict[str, float]] = []
    for nx in (25, 50, 100, 200, 400):
        numeric = synthetic.parabolic_form_quadrature(length, beam, draft, nx=nx, nz=nx)
        rows.append({
            "nx": nx,
            "h": length / nx,
            "volume": numeric["volume"],
            "rel_error_volume": abs(numeric["volume"] - exact["volume"]) / exact["volume"],
            "rel_error_waterplane": abs(numeric["waterplane_area"] - exact["waterplane_area"])
            / exact["waterplane_area"],
        })
    # Least-squares slope of log(rel_error) vs log(h): the observed order.
    def slope(key: str) -> float:
        xs = [math.log(row["h"]) for row in rows]
        ys = [math.log(row[key]) for row in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        return num / den

    return {
        "inputs": {"length": length, "beam": beam, "draft": draft},
        "closed_form": exact,
        "rows": rows,
        "observed_order_volume": slope("rel_error_volume"),
        "observed_order_waterplane": slope("rel_error_waterplane"),
        "theoretical_order": 2.0,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verification-harness anchor study")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20261110)
    parser.add_argument("--bundle-dependencies", default="pytest")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dependencies = [name.strip() for name in args.bundle_dependencies.split(",") if name.strip()]

    # --- A: run the shipped case package -----------------------------------
    registry = CaseRegistry()
    registry.discover(CASE_DIR)
    report = run_registry(registry)
    write_run_report(report, out_dir)
    bundle_dir = out_dir / "bundle"
    manifest = write_bundle(registry, report, bundle_dir, dependencies=dependencies)
    verdict = verify_bundle(bundle_dir)
    (out_dir / "integrity.md").write_text(verification_markdown(verdict), encoding="utf-8")

    # --- B: convergence sweep ----------------------------------------------
    convergence = _convergence_study()

    # --- C: tamper experiment ----------------------------------------------
    workdir = out_dir / "_tamper_work"
    workdir.mkdir(parents=True, exist_ok=True)
    tamper = _tamper_trials(bundle_dir, workdir, trials=args.trials, seed=args.seed)
    shutil.rmtree(workdir, ignore_errors=True)

    fingerprint = environment_fingerprint(dependencies)
    study = {
        "schema_version": SCHEMA_VERSION,
        "cases": {"count": len(registry), "domains": registry.domains(),
                  "ids": list(registry.case_ids),
                  "case_domains": {cid: registry.get(cid).domain for cid in registry.case_ids}},
        "run": report.to_dict(),
        "bundle": {
            "path": str(bundle_dir),
            "entries": len(manifest["entries"]),
            "root_digest": manifest["root_digest"],
            "chain_head": manifest["chain_head"],
            "environment_digest": manifest["environment_digest"],
            "environment": fingerprint,
            "environment_matches_this_host": environment_digest(fingerprint) == manifest["environment_digest"],
            "verified_ok": verdict.ok,
            "verified_entries": verdict.checked_entries,
        },
        "convergence": convergence,
        "tamper": tamper,
    }
    (out_dir / "study.json").write_text(json.dumps(study, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "study.md").write_text(_study_markdown(study), encoding="utf-8")

    print(json.dumps({
        "cases": study["cases"]["count"],
        "pass": report.counts["PASS"],
        "fail": report.counts["FAIL"],
        "error": report.counts["ERROR"],
        "observed_order_volume": round(convergence["observed_order_volume"], 4),
        "detection_rate": round(tamper["detection_rate"], 4),
        "bundle_ok": verdict.ok,
        "out": str(out_dir),
    }, sort_keys=True))
    return 0 if report.ok and verdict.ok and tamper["detection_rate"] == 1.0 else 1


def _study_markdown(study: Dict[str, Any]) -> str:
    run = study["run"]
    bundle = study["bundle"]
    convergence = study["convergence"]
    tamper = study["tamper"]
    lines = [
        "# Verification harness — anchor study",
        "",
        f"schema: `{study['schema_version']}` | cases: {study['cases']['count']} | "
        f"PASS {run['counts']['PASS']} / FAIL {run['counts']['FAIL']} / ERROR {run['counts']['ERROR']}",
        "",
        "## A. Case package",
        "",
        "| case | domain | verdict | worst rel_error |",
        "|---|---|---|---|",
    ]
    for result in run["cases"]:
        rel = [m["rel_error"] for m in result["metrics"] if m["rel_error"] is not None]
        worst = "—" if not rel else f"{max(rel):.3e}"
        domain = study["cases"]["case_domains"].get(result["case_id"], "")
        lines.append(f"| {result['case_id']} | {domain} | {result['verdict']} | {worst} |")
    lines += [
        "",
        "## B. Convergence of the synthetic parabolic form (composite midpoint)",
        "",
        "| nx | h | volume | rel_error(volume) | rel_error(waterplane) |",
        "|---|---|---|---|---|",
    ]
    for row in convergence["rows"]:
        lines.append(f"| {row['nx']} | {row['h']:.4f} | {row['volume']!r} | "
                     f"{row['rel_error_volume']:.3e} | {row['rel_error_waterplane']:.3e} |")
    lines += [
        "",
        f"observed order (volume): {convergence['observed_order_volume']:.4f} "
        f"(theory {convergence['theoretical_order']})",
        f"observed order (waterplane area): {convergence['observed_order_waterplane']:.4f} "
        f"(theory {convergence['theoretical_order']})",
        "",
        "## C. Tamper experiment",
        "",
        f"random single-byte mutations: {tamper['byte_flip_detected']}/{tamper['trials']} detected",
        "",
        "| tamper class | detected |",
        "|---|---|",
    ]
    for name, value in sorted(tamper["classes"].items()):
        lines.append(f"| {name} | {value['detected']} |")
    lines += [
        "",
        f"aggregate detection rate: {tamper['detected_total']}/{tamper['trials_total']} "
        f"= {tamper['detection_rate']:.4f}",
        "",
        "## Audit chain",
        "",
        f"- bundle: `{bundle['path']}` ({bundle['entries']} hashed entries)",
        f"- root digest: `{bundle['root_digest']}`",
        f"- chain head: `{bundle['chain_head']}`",
        f"- environment digest: `{bundle['environment_digest']}`",
        f"- verifier verdict: {'OK' if bundle['verified_ok'] else 'FAILED'}",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())