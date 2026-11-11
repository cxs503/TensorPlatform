"""Command line interface for the capability evidence chain."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline

__all__ = ["main"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capability_evidence_chain",
        description="Scan solver repositories, bind verification artefacts to "
                    "capability entries, decide equivalence with TOST and "
                    "reclassify historical PASS declarations.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the full evidence chain")
    run.add_argument("--config", required=True)
    run.add_argument("--root", default=None, help="override the ecosystem root")
    run.add_argument("--out", required=True)
    run.add_argument("--seed", type=int, default=pipeline.DEFAULT_SEED)
    run.add_argument("--ks-boot", type=int, default=2000)
    run.add_argument("--package-dir", default=None,
                     help="directory scanned by the zero-domain-semantics check")
    run.add_argument("--lexicon", default=None, help="domain lexicon JSON")

    summ = sub.add_parser("summary", help="print a compact summary of an artefact")
    summ.add_argument("--artifact", required=True)
    return p


def _summary(artifact: dict) -> dict:
    return {
        "association": {
            k: artifact["association"][k]
            for k in ("repos_scanned", "files_scanned", "recognized_files",
                      "bound_files", "association_rate")
        },
        "capabilities": [
            {
                "capability": c["capability"],
                "scenario": c["scenario"],
                "decision": c.get("decision"),
                "n": c.get("n"),
                "power": c.get("power"),
                "mde": c.get("mde"),
            }
            for c in artifact["capabilities"]
        ],
        "reclassification": artifact.get("reclassification", {}).get("summary", {}),
        "criteria": {
            k: (v.get("pass") if isinstance(v, dict) else v)
            for k, v in artifact.get("criteria", {}).items()
        },
    }


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        artifact = pipeline.run(
            args.config,
            root=args.root,
            seed=args.seed,
            ks_boot=args.ks_boot,
            package_dir=args.package_dir,
            lexicon_path=args.lexicon,
        )
        out = pipeline.write_artifact(artifact, args.out)
        print(json.dumps(_summary(artifact), indent=2))
        print(f"artifact written to {out}")
        return 0 if artifact["criteria"].get("all_pass", False) else 1
    if args.command == "summary":
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        print(json.dumps(_summary(artifact), indent=2))
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())