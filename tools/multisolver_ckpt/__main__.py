"""Command line entry point: ``python -m tools.multisolver_ckpt <command>``.

Commands
--------
``digest``          run a scenario uninterrupted, print the final state digest
``restart-digest``  restore the newest checkpoint under ``--root`` and continue
``selfcheck``       store round-trip + zstd losslessness + 1-bit flip detection
``bench``           save/load throughput at the requested payload sizes
``report``          run the full acceptance suite and write artifacts + figures
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import bench_io
from .codec import get_codec, zstd_available
from .errors import CheckpointError
from .models import build_harness
from .rollback import RestartSupervisor
from .store import CheckpointStore


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def cmd_digest(args: argparse.Namespace) -> int:
    harness, ledger = build_harness(args.scenario, damped=args.damped)
    run = harness.run(args.steps, ledger=ledger)
    _emit({
        "scenario": run.scenario, "steps": run.master_steps,
        "final_digest": run.final_digest,
        "energy_first": run.first_energy.values if run.first_energy else None,
        "energy_last": run.last_energy.values if run.last_energy else None,
    })
    return 0


def cmd_restart_digest(args: argparse.Namespace) -> int:
    from .determinism import make_store
    harness, ledger = build_harness(args.scenario, damped=args.damped)
    store = make_store(args.root, ledger, grid=harness.grid)
    outcome = RestartSupervisor(store).resume_into(harness)
    remaining = int(args.steps) - harness.master_index
    if remaining > 0:
        harness.run(remaining, ledger=ledger)
    _emit({
        "scenario": harness.name, "restored_step": outcome.step,
        "restored_digest": outcome.digest, "steps": harness.master_index,
        "final_digest": harness.snapshot().digest,
        "rollbacks": [r.to_dict() for r in outcome.rollbacks],
    })
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ckpt-selfcheck-"))
    try:
        codec = get_codec("zstd")
        probe = bytes(range(256)) * 4096
        roundtrip = codec.decompress(codec.compress(probe)) == probe
        harness, ledger = build_harness(3)
        store = CheckpointStore(tmp / "store", codec="zstd", ledger=ledger, grid=harness.grid)
        run = harness.run(8, ledger=ledger)
        saved = store.save(run.master_steps, harness.snapshot())
        loaded = store.load()
        bit_identical = loaded.digest == harness.snapshot().digest
        # 1-bit flip in the payload file must be detected
        payload = store.dir_of(loaded.step) / "kinetic.binzst"
        if not payload.is_file():
            payload = sorted(p for p in store.dir_of(loaded.step).iterdir() if p.suffix == ".zst")[0]
        raw = bytearray(payload.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        payload.write_bytes(bytes(raw))
        detected = False
        try:
            store.load()
        except CheckpointError:
            # a flipped bit either breaks the zstd frame checksum (unreadable) or
            # the digest/sha256 verification (corruption) -- both refuse the load
            detected = True
        _emit({
            "zstd_available": zstd_available(),
            "codec_roundtrip": roundtrip,
            "state_digest": saved.state_digest,
            "bit_identical_after_restore": bit_identical,
            "one_bit_flip_detected": detected,
            "compression_ratio": saved.compression_ratio,
        })
        return 0 if (roundtrip and bit_identical and detected) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_bench(args: argparse.Namespace) -> int:
    root = Path(args.root)
    results = bench_io.run_size_sweep(
        root, [int(g * bench_io.GIB) for g in args.sizes],
        n_solvers=args.solvers, codec=args.codec, level=args.level,
        max_workers=args.workers,
    )
    _emit({"results": results})
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import build_artifacts

    payload = build_artifacts(
        out_dir=Path(args.out), figures=not args.no_figures,
        bench_sizes=[float(s) for s in args.sizes] if args.sizes else None,
        stages=tuple(args.stages),
    )
    _emit({"artifacts": payload})
    return 0


def cmd_document(args: argparse.Namespace) -> int:
    import json as _json

    from .report import write_documents

    data = _json.loads(Path(args.results).read_text(encoding="utf-8"))
    summary = None
    if args.pytest_summary and Path(args.pytest_summary).is_file():
        summary = _json.loads(Path(args.pytest_summary).read_text(encoding="utf-8"))
    out = write_documents(
        data,
        acceptance_path=Path(args.acceptance or Path(args.results).parent / "acceptance.json"),
        docs_path=Path(args.docs),
        test_summary=summary,
    )
    _emit(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.multisolver_ckpt", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("digest", help="uninterrupted run digest")
    p.add_argument("--scenario", default="3")
    p.add_argument("--steps", type=int, default=1000)
    p.add_argument("--damped", action="store_true")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("restart-digest", help="restore newest checkpoint and continue")
    p.add_argument("--scenario", default="3")
    p.add_argument("--steps", type=int, default=1000)
    p.add_argument("--root", required=True)
    p.add_argument("--damped", action="store_true")
    p.set_defaults(func=cmd_restart_digest)

    p = sub.add_parser("selfcheck", help="round-trip + corruption-detection smoke test")
    p.set_defaults(func=cmd_selfcheck)

    p = sub.add_parser("bench", help="save/load throughput")
    p.add_argument("--root", required=True)
    p.add_argument("--sizes", nargs="+", type=float, default=[0.25, 0.5, 1.0])
    p.add_argument("--solvers", type=int, default=3)
    p.add_argument("--codec", default="zstd")
    p.add_argument("--level", type=int, default=3)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("report", help="full acceptance suite -> artifacts")
    p.add_argument("--out", default="artifacts/m9723")
    p.add_argument("--sizes", nargs="*", type=float, default=None)
    p.add_argument("--no-figures", action="store_true")
    p.add_argument("--stages", nargs="+", default=["repro", "energy", "sync", "corruption", "bench"],
                   help="which acceptance stages to (re)run")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("document", help="write acceptance.json + markdown report from results.json")
    p.add_argument("--results", default="artifacts/m9723/results.json")
    p.add_argument("--acceptance", default=None)
    p.add_argument("--docs", default="docs/MULTISOLVER_COUPLING_CHECKPOINT_REPORT.md")
    p.add_argument("--pytest-summary", default=None)
    p.set_defaults(func=cmd_document)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())