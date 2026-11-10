#!/usr/bin/env python3
"""Finish hook — intercept finish() calls and auto-extract knowledge entries.

This hook is called by the harness after every finish() invocation.
It extracts four-contract knowledge and stores it in the Knowledge Engine.

Integration points:
1. Harness calls this script after finish()
2. Script reads finish payload from stdin or file
3. Extracts knowledge entry
4. Validates against schema
5. Stores in Knowledge Engine
6. Returns extraction status

Usage:
    python finish_hook_ke.py --payload <path> [--store <db_path>]
    echo '{"summary": "..."}' | python finish_hook_ke.py --stdin
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.modules.knowledge_engine import (
    KnowledgeStore,
    FourContractExtractor,
    validate_entry,
)


class FinishHookKE:
    """Finish hook for knowledge extraction."""

    def __init__(self, store_path: str = "knowledge_engine.db"):
        self.store = KnowledgeStore(store_path)
        self.extractor = FourContractExtractor()
        self.stats = {
            "total_processed": 0,
            "successful_extractions": 0,
            "failed_extractions": 0,
            "total_time_ms": 0,
        }

    def process_finish(
        self,
        finish_payload: dict,
        mission_id: Optional[int] = None,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict:
        """Process a finish() call and extract knowledge.

        Args:
            finish_payload: The finish() call payload
            mission_id: Mission identifier
            repo: Repository name
            branch: Git branch
            commit: Git commit hash
            agent_id: Agent identifier

        Returns:
            Dict with extraction status and entry_id
        """
        start_time = time.time()
        self.stats["total_processed"] += 1

        try:
            # Extract knowledge entry
            entry = self.extractor.extract_from_finish(
                finish_payload,
                mission_id=mission_id,
                repo=repo,
                branch=branch,
                commit=commit,
                agent_id=agent_id,
            )

            # Validate entry
            entry_dict = entry.to_dict()
            is_valid, errors = validate_entry(entry_dict)

            if not is_valid:
                self.stats["failed_extractions"] += 1
                return {
                    "ok": False,
                    "error": "validation_failed",
                    "errors": errors,
                    "entry_id": None,
                }

            # Store entry (upsert to handle duplicates)
            entry_id = self.store.upsert(entry)

            elapsed_ms = (time.time() - start_time) * 1000
            self.stats["successful_extractions"] += 1
            self.stats["total_time_ms"] += elapsed_ms

            return {
                "ok": True,
                "entry_id": entry_id,
                "elapsed_ms": round(elapsed_ms, 2),
                "store_count": self.store.count(),
            }

        except Exception as e:
            self.stats["failed_extractions"] += 1
            return {
                "ok": False,
                "error": str(type(e).__name__),
                "message": str(e),
                "entry_id": None,
            }

    def get_stats(self) -> dict:
        """Get hook statistics."""
        avg_time = (
            self.stats["total_time_ms"] / self.stats["successful_extractions"]
            if self.stats["successful_extractions"] > 0
            else 0
        )
        return {
            **self.stats,
            "avg_time_ms": round(avg_time, 2),
            "store_count": self.store.count(),
        }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Finish hook for knowledge extraction")
    ap.add_argument("--payload", help="Path to finish payload JSON file")
    ap.add_argument("--stdin", action="store_true", help="Read payload from stdin")
    ap.add_argument("--store", default="knowledge_engine.db", help="Knowledge store path")
    ap.add_argument("--mission-id", type=int, help="Mission ID")
    ap.add_argument("--repo", help="Repository name")
    ap.add_argument("--branch", help="Git branch")
    ap.add_argument("--commit", help="Git commit")
    ap.add_argument("--agent-id", help="Agent ID")
    ap.add_argument("--stats", action="store_true", help="Print statistics")

    args = ap.parse_args(argv)

    hook = FinishHookKE(args.store)

    if args.stats:
        print(json.dumps(hook.get_stats(), indent=2))
        return 0

    # Read payload
    if args.stdin:
        payload = json.load(sys.stdin)
    elif args.payload:
        with open(args.payload) as f:
            payload = json.load(f)
    else:
        print("Error: --payload or --stdin required", file=sys.stderr)
        return 1

    # Process finish
    result = hook.process_finish(
        payload,
        mission_id=args.mission_id,
        repo=args.repo,
        branch=args.branch,
        commit=args.commit,
        agent_id=args.agent_id,
    )

    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
