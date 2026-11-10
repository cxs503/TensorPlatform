"""CLI entry point for multi-repo capability discovery.

Usage:
    python -m capability_discovery_multirepo --config config.json
    python -m capability_discovery_multirepo --repos TensorLBM,TensorFEM --output artifacts/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import DiscoveryConfig, RepoDescriptor
from .pipeline import run_pipeline


def build_config_from_args(args: argparse.Namespace) -> DiscoveryConfig:
    """Build DiscoveryConfig from CLI arguments."""
    if args.config:
        return DiscoveryConfig.from_json(args.config)

    repos = []
    if args.repos:
        for repo_spec in args.repos.split(","):
            parts = repo_spec.strip().split("=")
            name = parts[0]
            root = parts[1] if len(parts) > 1 else name
            repos.append(RepoDescriptor(name=name, root=root))

    return DiscoveryConfig(
        repos=repos,
        central_registry=args.registry,
        ledger_path=args.ledger,
        output_dir=args.output or "artifacts/capability_discovery_multirepo",
        min_confidence=args.min_confidence,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Multi-repo capability auto-discovery and registration"
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to discovery config JSON file",
    )
    parser.add_argument(
        "--repos", "-r",
        help="Comma-separated repo specs (name=path or just name)",
    )
    parser.add_argument(
        "--registry",
        help="Path to central capability registry JSON",
    )
    parser.add_argument(
        "--ledger",
        help="Path to capability ledger JSON",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output directory for reports",
    )
    parser.add_argument(
        "--min-confidence",
        type=float, default=0.0,
        help="Minimum confidence threshold for auto-registration",
    )
    parser.add_argument(
        "--workspace", "-w",
        default=".",
        help="Workspace root directory",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "markdown", "both"],
        default="both",
        help="Report output format",
    )
    parser.add_argument(
        "--verify-chain",
        action="store_true",
        help="Verify ledger hash chain and exit",
    )

    args = parser.parse_args(argv)

    if args.verify_chain and args.ledger:
        from .auto_registrar import CapabilityLedger
        ledger = CapabilityLedger(Path(args.ledger))
        ok, msg = ledger.verify_chain()
        print(f"Chain verification: {'OK' if ok else 'BROKEN'} — {msg}")
        return 0 if ok else 1

    config = build_config_from_args(args)
    if not config.repos:
        print("Error: No repositories configured. Use --repos or --config.", file=sys.stderr)
        return 1

    result = run_pipeline(
        config=config,
        workspace_root=args.workspace,
    )

    # Print summary
    s = result.report.summary
    print(f"\n=== Capability Discovery Run: {result.run_id} ===")
    print(f"Records: {len(result.records)}")
    print(f"Features: {len(result.features)}")
    print(f"Added: {s.get('added', 0)}")
    print(f"Updated: {s.get('updated', 0)}")
    print(f"Unchanged: {s.get('unchanged', 0)}")
    print(f"Missing: {s.get('missing', 0)}")
    print(f"Chain: {'OK' if s.get('chain_ok') else 'BROKEN'}")

    if result.coverage:
        print(f"\nCoverage: {result.coverage.get('coverage_ratio', 0):.1%}")
        if result.coverage.get("missing"):
            print(f"Missing capabilities: {', '.join(result.coverage['missing'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
