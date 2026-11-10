"""CLI for the sustainment operation matrix (``python3 -m tools.sustainment``).

Subcommands
-----------
``lineage``   build the artifact lineage DAG (+lock / verify / figure / traces)
``evidence``  audit a report's evidence chain (figures / numbers / criteria)
``backup``    run the backup-restore drill (sqlite reference engine or postgres)
``gc``        classify worktrees, archive tar+zstd, honour the disk pressure gate
``matrix``    run the four above in one pass into a single capability matrix JSON

Exit codes
----------
``0`` all requested checks pass · ``1`` a check failed · ``4`` GC lock busy
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import SCHEMA_VERSION
from . import backup_drill as bd
from . import evidence_audit as ea
from . import lineage as lg
from . import worktree_gc as wg


def _dump(obj: dict, out: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    print(text)


# ---------------------------------------------------------------------------
# subcommand handlers
# ---------------------------------------------------------------------------


def cmd_lineage(args: argparse.Namespace) -> int:
    if args.op == "verify":
        if not args.lock:
            print("--lock required for verify", file=sys.stderr)
            return 1
        inv = lg.scan(args.root, max_files=args.max_files)
        lin = lg.Lineage(lg.build_graph(inv), inv)
        drift = lg.verify_lock(lin, args.lock)
        payload = {"schema_version": SCHEMA_VERSION, "ok": drift.ok, "drift": drift.to_json()}
        _dump(payload, args.out)
        return 0 if drift.ok else 1
    roots = args.root or ["artifacts", "runs", "docs"]
    report = lg.build_report(
        roots, out_json=args.out, lock_path=args.lock, rules=args.rules,
        max_files=args.max_files, out_png=args.figure, trace=args.trace,
    )
    _dump({k: v for k, v in report.items() if k != "traces"} | {"traces": report.get("traces", {})},
          None)
    return 0 if report["summary"]["is_dag"] else 1


def cmd_evidence(args: argparse.Namespace) -> int:
    res = ea.audit(
        args.report,
        figures_manifest=args.figures,
        claims=args.claims,
        criteria=args.criteria,
        min_numeric_coverage=args.min_numeric_coverage,
        require_sections=not args.no_sections,
        base_dir=args.base_dir,
    )
    payload = res.to_json()
    _dump(payload, args.out)
    return 0 if res.ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    # precedence: explicit --tables > fixture tables > config file
    tables = [t for t in (args.tables.split(",") if args.tables else []) if t]
    if args.fixture:
        created = bd.build_fixture_db(args.source, n_tables=args.fixture,
                                      rows=args.fixture_rows)
        tables = tables or created
    elif not tables and args.tables_config:
        tables = bd.load_tables_config(args.tables_config)
    res = bd.run_drill(
        source=args.source, tables=tables, workdir=args.workdir, engine_kind=args.engine,
        ledger_path=args.ledger, rpo_budget_s=args.rpo_budget, rto_budget_s=args.rto_budget,
        negative_controls=not args.no_negative_controls,
    )
    _dump(res, args.out)
    return 0 if res.get("verdict") == "PASS" else 1


def cmd_gc(args: argparse.Namespace) -> int:
    try:
        res = wg.run_gc(
            repo=args.repo, worktrees_root=args.worktrees_root,
            archive_root=args.archive_root, base=args.base, ttl_hours=args.ttl_hours,
            apply=args.apply, limit=args.limit, collect_size=not args.no_size,
            lock_path=args.lock, lock_timeout=args.lock_timeout,
            disk_path=args.disk_path, warn_pct=args.warn_pct, force_pct=args.force_pct,
            workers=args.workers, stray_sample=args.stray_sample, out_json=args.out,
        )
    except wg.LockBusy as exc:
        print(json.dumps({"ok": False, "error": "lock_busy", "detail": str(exc)}))
        return 4
    _dump(res, None if args.out else None)
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    """One-pass capability matrix: all four mechanisms, one verdict document."""
    t0 = time.time()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix: dict = {"schema_version": SCHEMA_VERSION, "generated_at": lg._now(),
                    "components": {}}

    # 1 lineage
    roots = args.root or ["artifacts", "runs", "docs"]
    lin = lg.build_report(
        roots, out_json=str(out_dir / "lineage.json"),
        lock_path=str(out_dir / "lineage_lock.json"),
        max_files=args.max_files, out_png=str(out_dir / "lineage_dag.png"),
    )
    matrix["components"]["lineage"] = {
        "ok": lin["summary"]["is_dag"],
        "nodes": lin["summary"]["nodes"], "edges": lin["summary"]["edges"],
        "sha256_coverage": lin["summary"]["sha256_coverage"],
        "artifact": str(out_dir / "lineage.json"),
        "figure": str(out_dir / "lineage_dag.png"),
    }

    # 2 evidence chain
    if args.report_md:
        res = ea.audit(args.report_md, figures_manifest=args.figures, claims=args.claims,
                       criteria=args.criteria,
                       min_numeric_coverage=args.min_numeric_coverage)
        (out_dir / "evidence_audit.json").write_text(
            json.dumps(res.to_json(), indent=2, ensure_ascii=False))
        matrix["components"]["evidence"] = {
            "ok": res.ok,
            "numbers_coverage": res.numbers.get("coverage"),
            "figures_problems": len(res.figures.get("problems", [])),
            "criteria_passed": f"{res.criteria.get('passed')}/{res.criteria.get('total')}",
            "artifact": str(out_dir / "evidence_audit.json"),
        }
    else:
        matrix["components"]["evidence"] = {"ok": None, "reason": "no_report_supplied"}

    # 3 backup drill
    src = args.backup_source
    tables: list[str] = []
    if args.backup_fixture:
        tables = bd.build_fixture_db(src, n_tables=args.backup_fixture)
    elif args.tables_config:
        tables = bd.load_tables_config(args.tables_config)
    drill = bd.run_drill(
        source=src, tables=tables, workdir=str(out_dir / "drill_work"),
        engine_kind=args.backup_engine, ledger_path=str(out_dir / "backup_ledger.json"),
        negative_controls=True,
    )
    (out_dir / "backup_drill.json").write_text(json.dumps(drill, indent=2, ensure_ascii=False))
    matrix["components"]["backup"] = {
        "ok": drill.get("verdict") == "PASS", "verdict": drill.get("verdict"),
        "rto_s": drill.get("rto_s"), "rpo_s": drill.get("rpo_s"),
        "negative_controls": len(drill.get("negative_controls", [])),
        "negative_controls_detected": sum(
            1 for n in drill.get("negative_controls", []) if n.get("detected")),
        "artifact": str(out_dir / "backup_drill.json"),
    }

    # 4 worktree GC
    try:
        gc = wg.run_gc(
            repo=args.repo, worktrees_root=args.worktrees_root,
            archive_root=str(out_dir / "worktree_archive"), base=args.base,
            ttl_hours=args.ttl_hours, apply=False, limit=args.gc_limit,
            collect_size=not args.no_size, workers=args.workers,
            out_json=str(out_dir / "worktree_gc.json"),
        )
        matrix["components"]["gc"] = {
            "ok": True, "worktrees": gc["summary"]["worktrees"],
            "by_class": gc["summary"]["by_class"],
            "removable": gc["summary"]["removable"],
            "gate_level": gc["gate_verdict"]["level"],
            "disk_used_pct": gc["disk"]["used_pct"],
            "artifact": str(out_dir / "worktree_gc.json"),
        }
    except Exception as exc:  # noqa: BLE001
        matrix["components"]["gc"] = {"ok": False, "reason": str(exc)[:300]}

    matrix["ok"] = all(c.get("ok") in (True, None) for c in matrix["components"].values())
    matrix["elapsed_s"] = round(time.time() - t0, 3)
    (out_dir / "matrix.json").write_text(json.dumps(matrix, indent=2, ensure_ascii=False))
    _dump(matrix, None)
    return 0 if matrix["ok"] else 1


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python3 -m tools.sustainment",
                                 description="platform self-sustainment operation matrix")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lineage", help="artifact lineage DAG")
    p.add_argument("op", nargs="?", default="build", choices=["build", "verify"])
    p.add_argument("--root", action="append", default=None)
    p.add_argument("--out")
    p.add_argument("--lock")
    p.add_argument("--figure")
    p.add_argument("--rules", nargs="+", default=["R1", "R2", "R3"])
    p.add_argument("--max-files", type=int, default=20000)
    p.add_argument("--trace", action="append", default=[])
    p.set_defaults(func=cmd_lineage)

    p = sub.add_parser("evidence", help="report evidence-chain audit")
    p.add_argument("--report", required=True)
    p.add_argument("--figures")
    p.add_argument("--claims")
    p.add_argument("--criteria")
    p.add_argument("--base-dir")
    p.add_argument("--out")
    p.add_argument("--min-numeric-coverage", type=float, default=0.9)
    p.add_argument("--no-sections", action="store_true")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("backup", help="backup / restore drill")
    p.add_argument("--engine", default="sqlite", choices=["sqlite", "postgres"])
    p.add_argument("--source", required=True)
    p.add_argument("--tables")
    p.add_argument("--tables-config", default=bd.DEFAULT_TABLES_CONFIG)
    p.add_argument("--workdir", default="/tmp/sustain_drill")
    p.add_argument("--ledger")
    p.add_argument("--rpo-budget", type=float, default=bd.DEFAULT_RPO_BUDGET_S)
    p.add_argument("--rto-budget", type=float, default=bd.DEFAULT_RTO_BUDGET_S)
    p.add_argument("--no-negative-controls", action="store_true")
    p.add_argument("--fixture", type=int, default=0,
                   help="create N deterministic tables at --source first")
    p.add_argument("--fixture-rows", type=int, default=40)
    p.add_argument("--out")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("gc", help="worktree GC")
    p.add_argument("--repo", required=True)
    p.add_argument("--worktrees-root")
    p.add_argument("--archive-root", default="artifacts/sustainment/worktree_archive")
    p.add_argument("--base", default="main")
    p.add_argument("--ttl-hours", type=float, default=wg.DEFAULT_TTL_HOURS)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-size", action="store_true")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--stray-sample", type=int, default=30)
    p.add_argument("--lock")
    p.add_argument("--lock-timeout", type=float, default=0.0)
    p.add_argument("--disk-path")
    p.add_argument("--warn-pct", type=float, default=wg.DEFAULT_WARN_PCT)
    p.add_argument("--force-pct", type=float, default=wg.DEFAULT_FORCE_PCT)
    p.add_argument("--out")
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("matrix", help="all four mechanisms in one pass")
    p.add_argument("--out-dir", default="artifacts/sustainment")
    p.add_argument("--root", action="append")
    p.add_argument("--max-files", type=int, default=20000)
    p.add_argument("--report-md")
    p.add_argument("--figures")
    p.add_argument("--claims")
    p.add_argument("--criteria")
    p.add_argument("--min-numeric-coverage", type=float, default=0.9)
    p.add_argument("--backup-engine", default="sqlite")
    p.add_argument("--backup-source", default="/tmp/sustain_matrix/platform.db")
    p.add_argument("--backup-fixture", type=int, default=5)
    p.add_argument("--tables-config", default=bd.DEFAULT_TABLES_CONFIG)
    p.add_argument("--repo", default=".")
    p.add_argument("--worktrees-root")
    p.add_argument("--base", default="main")
    p.add_argument("--ttl-hours", type=float, default=wg.DEFAULT_TTL_HOURS)
    p.add_argument("--gc-limit", type=int, default=50)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--no-size", action="store_true")
    p.set_defaults(func=cmd_matrix)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())