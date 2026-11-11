"""CLI entry point for failure signature miner.

Usage:
    python -m tools.failure_miner mine --days 30 --limit 200
    python -m tools.failure_miner classify --input failed_missions.json
    python -m tools.failure_miner cluster --input classified.json
    python -m tools.failure_miner export --output artifacts/traps_candidates.json
    python -m tools.failure_miner full-pipeline --days 30 --output artifacts/traps_candidates.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def cmd_mine(args: argparse.Namespace) -> int:
    """Collect failed missions from database."""
    from database.config import get_db
    from .signature_miner import collect_failed_missions_sqlite, collect_failed_missions

    # Try PostgreSQL first, fall back to SQLite
    try:
        db = next(get_db())
        missions = collect_failed_missions(db, days=args.days, limit=args.limit)
    except Exception:
        # SQLite fallback for testing
        import sqlite3
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = args.db_path or "backend/foundation-ai.db"
        engine = create_engine(f"sqlite:///{db_path}")
        with Session(engine) as db:
            missions = collect_failed_missions_sqlite(db, days=args.days, limit=args.limit)

    # Serialize
    output = []
    for m in missions:
        output.append({
            "mission_id": m.mission_id,
            "goal": m.goal[:200],
            "repo": m.repo,
            "status": m.status,
            "fail_stage": m.fail_stage,
            "has_report": m.has_report,
            "created_at": m.created_at,
            "finished_at": m.finished_at,
        })

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(output)} missions to {args.output}")
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Classify failed missions into signature types."""
    from .signature_miner import FailedMissionView
    from .classifier import classify_batch, classification_report

    with open(args.input, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Reconstruct FailedMissionView from raw data
    missions = []
    for item in raw:
        m = FailedMissionView(
            mission_id=item["mission_id"],
            goal=item.get("goal", ""),
            repo=item.get("repo", ""),
            status=item.get("status", "failed"),
            fail_stage=item.get("fail_stage"),
            has_report=item.get("has_report", False),
            created_at=item.get("created_at"),
            finished_at=item.get("finished_at"),
        )
        missions.append(m)

    domain_blacklist = []
    if args.domain_blacklist:
        from .domain_guard import load_domain_blacklist
        domain_blacklist = load_domain_blacklist(args.domain_blacklist)

    classified = classify_batch(missions, domain_blacklist=domain_blacklist)

    # Output
    output = []
    for m, sig_type in classified:
        output.append({
            "mission_id": m.mission_id,
            "signature_type": sig_type,
        })

    report = classification_report(classified)

    result = {"classified": output, "report": report}

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Wrote classification to {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    """Cluster classified missions into failure signatures."""
    from .signature_miner import FailedMissionView
    from .classifier import classify_batch
    from .cluster import cluster_signatures, deduplicate_against_existing

    with open(args.input, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Reconstruct missions
    missions = []
    for item in raw:
        m = FailedMissionView(
            mission_id=item["mission_id"],
            goal=item.get("goal", ""),
            repo=item.get("repo", ""),
            status=item.get("status", "failed"),
            fail_stage=item.get("fail_stage"),
            has_report=item.get("has_report", False),
        )
        missions.append(m)

    domain_blacklist = []
    if args.domain_blacklist:
        from .domain_guard import load_domain_blacklist
        domain_blacklist = load_domain_blacklist(args.domain_blacklist)

    classified = classify_batch(missions, domain_blacklist=domain_blacklist)
    signatures = cluster_signatures(classified)

    # Deduplicate against existing traps
    existing = []
    if args.existing_traps:
        with open(args.existing_traps, "r", encoding="utf-8") as f:
            existing = json.load(f)

    new_sigs, marked = deduplicate_against_existing(signatures, existing)

    output = {
        "signatures": [s.to_dict() for s in new_sigs],
        "marked_existing": marked,
        "total_clusters": len(signatures),
        "new_candidates": len(new_sigs),
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(new_sigs)} new candidates to {args.output}")
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export traps_candidates.json for append to KNOWN_TRAPS."""
    from .cluster import compute_similarity

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    signatures = data.get("signatures", [])

    # Format as TRAPS candidates (≤120 chars each)
    candidates = []
    for sig in signatures:
        desc = (
            f"[{sig['signature_type']}] error={sig['error_code']} "
            f"freq={sig['frequency']} impact={sig['impact_score']:.1f} "
            f"missions={sig['mission_ids'][:5]}"
        )
        # Truncate to 120 chars
        if len(desc) > 120:
            desc = desc[:117] + "..."

        candidates.append({
            "signature_hash": sig["signature_hash"],
            "description": desc,
            "signature_type": sig["signature_type"],
            "error_code": sig["error_code"],
            "frequency": sig["frequency"],
            "impact_score": sig["impact_score"],
            "mission_ids": sig["mission_ids"],
            "evidence_sql": sig["evidence_sql"],
        })

    output = {
        "generated_at": _now(),
        "total_candidates": len(candidates),
        "candidates": candidates,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Exported {len(candidates)} TRAPS candidates to {args.output}")
    return 0


def cmd_full_pipeline(args: argparse.Namespace) -> int:
    """Run full pipeline: mine → classify → cluster → export."""
    from database.config import get_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from .signature_miner import collect_failed_missions_sqlite, FailedMissionView
    from .classifier import classify_batch, classification_report
    from .cluster import cluster_signatures, deduplicate_against_existing
    from .domain_guard import load_domain_blacklist, verify_zero_domain_semantics

    # Load domain blacklist
    domain_blacklist = []
    if args.domain_blacklist:
        domain_blacklist = load_domain_blacklist(args.domain_blacklist)

    # Collect missions
    try:
        db = next(get_db())
        from .signature_miner import collect_failed_missions
        missions = collect_failed_missions(db, days=args.days, limit=args.limit)
    except Exception:
        db_path = args.db_path or "backend/foundation-ai.db"
        engine = create_engine(f"sqlite:///{db_path}")
        with Session(engine) as db:
            missions = collect_failed_missions_sqlite(db, days=args.days, limit=args.limit)

    if not missions:
        print("No failed missions found in the lookback window.")
        return 0

    print(f"Collected {len(missions)} failed missions")

    # Classify
    classified = classify_batch(missions, domain_blacklist=domain_blacklist)
    report = classification_report(classified)
    print(f"Classification report: {json.dumps(report, indent=2)}")

    # Cluster
    signatures = cluster_signatures(classified)
    print(f"Clustered into {len(signatures)} signature groups")

    # Deduplicate against existing
    existing = []
    if args.existing_traps:
        with open(args.existing_traps, "r", encoding="utf-8") as f:
            existing = json.load(f)

    new_sigs, marked = deduplicate_against_existing(signatures, existing)
    print(f"New candidates: {len(new_sigs)}, marked existing: {len(marked)}")

    # Verify zero domain semantics
    trap_dicts = [s.to_dict() for s in new_sigs]
    domain_check = verify_zero_domain_semantics(trap_dicts, domain_blacklist)
    print(f"Domain semantics check: clean={domain_check['clean']}, hits={len(domain_check['hits'])}")

    # Export
    candidates = []
    for sig in new_sigs:
        desc = (
            f"[{sig.signature_type}] error={sig.error_code} "
            f"freq={sig.frequency} impact={sig.impact_score:.1f} "
            f"missions={sig.mission_ids[:5]}"
        )
        if len(desc) > 120:
            desc = desc[:117] + "..."

        candidates.append({
            "signature_hash": sig.signature_hash,
            "description": desc,
            "signature_type": sig.signature_type,
            "error_code": sig.error_code,
            "frequency": sig.frequency,
            "impact_score": round(sig.impact_score, 4),
            "mission_ids": sig.mission_ids,
            "evidence_sql": sig.evidence_sql,
        })

    output = {
        "generated_at": _now(),
        "total_candidates": len(candidates),
        "classification_report": report,
        "domain_check": domain_check,
        "marked_existing": marked,
        "candidates": candidates,
    }

    output_path = args.output or "artifacts/traps_candidates.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Exported {len(candidates)} TRAPS candidates to {output_path}")
    return 0


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description="Failure signature miner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # mine
    p_mine = subparsers.add_parser("mine", help="Collect failed missions")
    p_mine.add_argument("--days", type=int, default=30)
    p_mine.add_argument("--limit", type=int, default=500)
    p_mine.add_argument("--output", type=str)
    p_mine.add_argument("--db-path", type=str)
    p_mine.set_defaults(func=cmd_mine)

    # classify
    p_classify = subparsers.add_parser("classify", help="Classify missions")
    p_classify.add_argument("--input", type=str, required=True)
    p_classify.add_argument("--output", type=str)
    p_classify.add_argument("--domain-blacklist", type=str)
    p_classify.set_defaults(func=cmd_classify)

    # cluster
    p_cluster = subparsers.add_parser("cluster", help="Cluster signatures")
    p_cluster.add_argument("--input", type=str, required=True)
    p_cluster.add_argument("--output", type=str)
    p_cluster.add_argument("--existing-traps", type=str)
    p_cluster.add_argument("--domain-blacklist", type=str)
    p_cluster.set_defaults(func=cmd_cluster)

    # export
    p_export = subparsers.add_parser("export", help="Export TRAPS candidates")
    p_export.add_argument("--input", type=str, required=True)
    p_export.add_argument("--output", type=str, required=True)
    p_export.set_defaults(func=cmd_export)

    # full-pipeline
    p_full = subparsers.add_parser("full-pipeline", help="Run full pipeline")
    p_full.add_argument("--days", type=int, default=30)
    p_full.add_argument("--limit", type=int, default=500)
    p_full.add_argument("--output", type=str, default="artifacts/traps_candidates.json")
    p_full.add_argument("--db-path", type=str)
    p_full.add_argument("--existing-traps", type=str)
    p_full.add_argument("--domain-blacklist", type=str)
    p_full.set_defaults(func=cmd_full_pipeline)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
