#!/usr/bin/env python3
"""Archive the delivered Wave-8 node acceptance artefacts into the corpus repo.

The reference corpus must be able to *re-adjudicate* delivered nodes offline, so
the node-side evidence is copied byte-for-byte into
``backend/data/reference_corpus/wave8_nodes/<NODE>/`` together with a manifest
that records the origin worktree, branch, HEAD commit and sha256 of every
archived file (audit chain: node evidence -> manifest -> corpus entry).

Read-only with respect to the source worktrees.

Usage:  python3 tools/archive_wave8_nodes.py [--work-root /srv/tensor/agent/work]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEST_REL = Path("backend/data/reference_corpus/wave8_nodes")

#: node -> (mission, origin worktree name, [(source relative path, archived name)])
NODES = {
    "N01": (1212, "m1212", [("artifacts/ice_contact_m1212/results.json", "results.json")]),
    "N02": (
        1216,
        "m1216",
        [
            ("artifacts/lng_sloshing/acceptance.json", "acceptance.json"),
            ("artifacts/lng_sloshing/natural_frequencies.json", "natural_frequencies.json"),
            ("artifacts/lng_sloshing/vof_forced_resonance.json", "vof_forced_resonance.json"),
            ("artifacts/lng_sloshing/vof_impact_pressure.json", "vof_impact_pressure.json"),
            ("artifacts/lng_sloshing/vof_free_decay.json", "vof_free_decay.json"),
        ],
    ),
    "N03": (1217, "m1217", [("artifacts/w8_n03/acceptance.json", "acceptance.json")]),
    "N04": (
        1213,
        "m1213",
        [
            ("outputs/sir5_fracture_propagation/validation_report.json", "validation_report_sir5.json"),
            ("outputs/si4_full_chain/validation_report.json", "validation_report_si4.json"),
        ],
    ),
    "N05": (1214, "m1214", [("benchmarks/validation_report.json", "validation_report.json")]),
    "N06": (
        1215,
        "m1215",
        [
            ("artifacts/grease_ice_envelope/criteria.json", "criteria.json"),
            ("artifacts/grease_ice_envelope/anchors_ledger.json", "anchors_ledger.json"),
            ("artifacts/grease_ice_envelope/riska_continuity.json", "riska_continuity.json"),
        ],
    ),
    "N07": (
        1218,
        "m1218",
        [
            ("artifacts/ice_digital_twin/acceptance.json", "acceptance.json"),
            ("artifacts/ice_digital_twin/results.json", "results.json"),
        ],
    ),
    "N08": (1219, "m1219", [("artifacts/ice_integration/acceptance.json", "acceptance.json")]),
}

SLUGS = {
    "N01": "ice_contact_phase_change",
    "N02": "lng_sloshing",
    "N03": "cylinder_lbm_frequency",
    "N04": "dem3d_fracture_bending",
    "N05": "ice_intake_dem",
    "N06": "grease_ice_envelope",
    "N07": "ice_digital_twin",
    "N08": "cross_domain_ice_integration",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(worktree: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - defensive
        return f"<unavailable: {exc}>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-root", default="/srv/tensor/agent/work")
    ap.add_argument("--dest", default=str(DEST_REL))
    args = ap.parse_args()
    work_root = Path(args.work_root)
    dest_root = Path(args.dest)
    dest_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "generated_by": "tools/archive_wave8_nodes.py",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline archive of the Wave-8 node acceptance artefacts that the reference corpus re-adjudicates; enables reproduction without the sibling worktrees.",
        "nodes": [],
    }
    for node, (mission, wt_name, files) in NODES.items():
        wt = work_root / wt_name
        node_dir = dest_root / f"{node}_{SLUGS[node]}"
        node_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "node": node,
            "mission": mission,
            "slug": SLUGS[node],
            "origin_worktree": str(wt),
            "origin_branch": git(wt, "rev-parse", "--abbrev-ref", "HEAD"),
            "origin_head": git(wt, "rev-parse", "--short", "HEAD"),
            "files": [],
        }
        for src_rel, name in files:
            src = wt / src_rel
            if not src.exists():
                rec["files"].append({"archived": name, "origin": src_rel, "status": "MISSING"})
                continue
            dst = node_dir / name
            shutil.copyfile(src, dst)
            rec["files"].append(
                {
                    "archived": name,
                    "origin": src_rel,
                    "status": "COPIED",
                    "bytes": dst.stat().st_size,
                    "sha256": sha256(dst),
                }
            )
        manifest["nodes"].append(rec)
        copied = sum(1 for f in rec["files"] if f["status"] == "COPIED")
        print(f"{node} (m{mission}) -> {node_dir.name}: {copied}/{len(files)} files")

    (dest_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"manifest written: {dest_root / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())