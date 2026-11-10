"""CLI: replay metric sequences through the gate + sentinel.

Usage:
    python -m tools.metric_gate.replay --input snapshots.json [--config gate_config.json]

Input format (JSON array):
    [
        {"metric_name": "repro_rate", "value": 0.95, "evidence_count": 20,
         "row": {"reproduced": 19, "total": 20}, "timestamp": "2026-11-10T10:00:00Z"},
        ...
    ]

Output: gate results + sentinel alerts as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.metric_gate.expr_registry import ExprRegistry
from tools.metric_gate.gate import MetricGate
from tools.metric_gate.sentinel import RegressionSentinel, MetricSnapshot
from tools.metric_gate.models import MetricSnapshotStore


def _parse_timestamp(ts: str | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def load_config(config_path: str | None) -> Dict[str, Any]:
    """Load gate configuration from JSON file."""
    if config_path is None:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def build_registry(config: Dict[str, Any]) -> ExprRegistry:
    """Build expression registry from config."""
    registry = ExprRegistry()
    expressions = config.get("expressions", {})
    for name, expr in expressions.items():
        allowed_vars = config.get("allowed_vars", {}).get(name)
        if allowed_vars:
            registry.register(name, expr, set(allowed_vars))
        else:
            registry.register(name, expr)
    return registry


def build_gate(registry: ExprRegistry, config: Dict[str, Any]) -> MetricGate:
    """Build metric gate from config."""
    return MetricGate(
        registry=registry,
        default_tol=config.get("default_tol", 1e-6),
        per_metric_tol=config.get("per_metric_tol", {}),
    )


def build_sentinel(config: Dict[str, Any]) -> RegressionSentinel:
    """Build regression sentinel from config."""
    return RegressionSentinel(
        regression_tol=config.get("regression_tol", 5.0),
        base_tol=config.get("base_tol", 1e-6),
        higher_is_better=config.get("higher_is_better", True),
        min_evidence=config.get("min_evidence", 1),
        per_metric_config=config.get("per_metric_sentinel", {}),
    )


def replay(
    snapshots: List[Dict[str, Any]],
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Replay a sequence of metric snapshots through gate + sentinel.

    Returns:
        {
            "gate_results": [...],
            "sentinel_alerts": [...],
            "summary": {
                "total": N,
                "accepted": N,
                "rejected": N,
                "suspect": N,
            }
        }
    """
    config = config or {}
    registry = build_registry(config)
    gate = build_gate(registry, config)
    sentinel = build_sentinel(config)

    gate_results = []
    sentinel_alerts = []

    # Track last snapshot per metric for sentinel
    last_snapshots: Dict[str, MetricSnapshot] = {}

    for snap in snapshots:
        metric_name = snap["metric_name"]
        value = float(snap["value"])
        evidence_count = int(snap.get("evidence_count", 0))
        row = snap.get("row", {})
        ts = _parse_timestamp(snap.get("timestamp"))

        # Gate check
        result = gate.check(
            metric_name,
            value,
            row,
            evidence_count=evidence_count,
        )
        gate_results.append(result.as_dict())

        # Sentinel check
        curr_snapshot = MetricSnapshot(
            metric_name=metric_name,
            value=value,
            evidence_count=evidence_count,
            timestamp=ts,
        )

        if metric_name in last_snapshots:
            alert = sentinel.check_transition(
                metric_name,
                last_snapshots[metric_name],
                curr_snapshot,
            )
            if alert:
                sentinel_alerts.append(alert.as_dict())

        last_snapshots[metric_name] = curr_snapshot

    accepted = sum(1 for r in gate_results if r["accepted"])
    rejected = sum(1 for r in gate_results if not r["accepted"])

    return {
        "gate_results": gate_results,
        "sentinel_alerts": sentinel_alerts,
        "summary": {
            "total": len(snapshots),
            "accepted": accepted,
            "rejected": rejected,
            "suspect": len(sentinel_alerts),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Replay metric snapshots through gate + sentinel"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input JSON file with metric snapshots",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Gate configuration JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite DB URL for persistence (e.g., sqlite:///metrics.db)",
    )

    args = parser.parse_args()

    # Load input
    with open(args.input) as f:
        snapshots = json.load(f)

    # Load config
    config = load_config(args.config)

    # Run replay
    result = replay(snapshots, config)

    # Optional persistence
    if args.db:
        store = MetricSnapshotStore.from_url(args.db)
        for gr in result["gate_results"]:
            store.insert(
                metric_name=gr["metric_name"],
                value=gr["submitted_value"],
                evidence_count=gr.get("evidence_count", 0),
                recompute_expr=config.get("expressions", {}).get(gr["metric_name"]),
                recomputed_value=gr.get("recomputed_value"),
                gate_diff=gr.get("diff"),
                gate_accepted=gr["accepted"],
            )

    # Output
    output_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Summary to stderr
    summary = result["summary"]
    print(
        f"\nSummary: {summary['total']} total, "
        f"{summary['accepted']} accepted, "
        f"{summary['rejected']} rejected, "
        f"{summary['suspect']} suspect",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
