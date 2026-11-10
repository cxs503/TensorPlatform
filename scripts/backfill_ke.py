#!/usr/bin/env python3
"""Backfill knowledge from historical reports.

Scans report.json files across the workspace and extracts knowledge entries.
Supports batch processing with progress tracking.

Usage:
    python backfill_ke.py --reports-dir /path/to/work --store knowledge.db
    python backfill_ke.py --report-file report.json --store knowledge.db
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.modules.knowledge_engine import (
    KnowledgeStore,
    FourContractExtractor,
    validate_entry,
)


class KnowledgeBackfill:
    """Backfill knowledge from historical reports."""

    def __init__(self, store_path: str = "knowledge_engine.db"):
        self.store = KnowledgeStore(store_path)
        self.extractor = FourContractExtractor()
        self.stats = {
            "total_reports": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "total_time_ms": 0,
        }

    def backfill_directory(
        self,
        reports_dir: str | Path,
        pattern: str = "report.json",
        max_reports: Optional[int] = None,
        verbose: bool = False,
    ) -> dict:
        """Backfill from a directory of reports.

        Args:
            reports_dir: Directory to scan
            pattern: File pattern to match
            max_reports: Maximum reports to process
            verbose: Print progress

        Returns:
            Statistics dict
        """
        reports_dir = Path(reports_dir)
        if not reports_dir.exists():
            return {"error": f"Directory not found: {reports_dir}"}

        # Find all report files
        report_files = sorted(reports_dir.rglob(pattern))
        if max_reports:
            report_files = report_files[:max_reports]

        self.stats["total_reports"] = len(report_files)

        if verbose:
            print(f"Found {len(report_files)} reports to process")

        for i, report_path in enumerate(report_files):
            if verbose and (i + 1) % 10 == 0:
                print(f"Processing {i + 1}/{len(report_files)}...")

            self._process_report(report_path)

        return self.get_stats()

    def backfill_single(self, report_path: str | Path) -> dict:
        """Backfill from a single report file."""
        return self._process_report(Path(report_path))

    def _process_report(self, report_path: Path) -> dict:
        """Process a single report file."""
        start_time = time.time()

        try:
            # Read report
            with open(report_path) as f:
                report = json.load(f)

            # Extract knowledge entry
            entry = self.extractor.extract_from_report(
                report,
                report_path=str(report_path),
            )

            # Validate
            entry_dict = entry.to_dict()
            is_valid, errors = validate_entry(entry_dict)

            if not is_valid:
                self.stats["failed"] += 1
                return {
                    "ok": False,
                    "error": "validation_failed",
                    "errors": errors,
                    "path": str(report_path),
                }

            # Store (upsert)
            entry_id = self.store.upsert(entry)

            elapsed_ms = (time.time() - start_time) * 1000
            self.stats["successful"] += 1
            self.stats["total_time_ms"] += elapsed_ms

            return {
                "ok": True,
                "entry_id": entry_id,
                "path": str(report_path),
                "elapsed_ms": round(elapsed_ms, 2),
            }

        except json.JSONDecodeError as e:
            self.stats["failed"] += 1
            return {
                "ok": False,
                "error": "json_parse_error",
                "message": str(e),
                "path": str(report_path),
            }
        except Exception as e:
            self.stats["failed"] += 1
            return {
                "ok": False,
                "error": str(type(e).__name__),
                "message": str(e),
                "path": str(report_path),
            }

    def get_stats(self) -> dict:
        """Get backfill statistics."""
        avg_time = (
            self.stats["total_time_ms"] / self.stats["successful"]
            if self.stats["successful"] > 0
            else 0
        )
        return {
            **self.stats,
            "avg_time_ms": round(avg_time, 2),
            "store_count": self.store.count(),
            "success_rate": (
                self.stats["successful"] / self.stats["total_reports"]
                if self.stats["total_reports"] > 0
                else 0
            ),
        }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Backfill knowledge from historical reports")
    ap.add_argument("--reports-dir", help="Directory to scan for reports")
    ap.add_argument("--report-file", help="Single report file to process")
    ap.add_argument("--store", default="knowledge_engine.db", help="Knowledge store path")
    ap.add_argument("--pattern", default="report.json", help="File pattern to match")
    ap.add_argument("--max-reports", type=int, help="Maximum reports to process")
    ap.add_argument("--verbose", "-v", action="store_true", help="Print progress")

    args = ap.parse_args(argv)

    backfill = KnowledgeBackfill(args.store)

    if args.report_file:
        result = backfill.backfill_single(args.report_file)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.reports_dir:
        stats = backfill.backfill_directory(
            args.reports_dir,
            pattern=args.pattern,
            max_reports=args.max_reports,
            verbose=args.verbose,
        )
        print(json.dumps(stats, indent=2))
        return 0 if stats.get("successful", 0) > 0 else 1

    print("Error: --reports-dir or --report-file required", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
