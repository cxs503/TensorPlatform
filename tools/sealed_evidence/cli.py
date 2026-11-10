#!/usr/bin/env python3
"""sealed_evidence CLI —— 证据封存与回收闸命令行入口。

用法::

    # 封存任务证据
    python3 -m tools.sealed_evidence.cli seal --mission m10144 --workdir /path/to/work --output artifacts/sealed_evidence.json

    # 运行回收闸
    python3 -m tools.sealed_evidence.cli gate --workdir /path/to/work --mission m10144

    # 处理 drift ledger
    python3 -m tools.sealed_evidence.cli drift --ledger data/claim_watch/drift_ledger.json --output artifacts/drift_cause.json

    # 重放沙箱
    python3 -m tools.sealed_evidence.cli replay --cmd "pytest tests/" --cwd /path/to/work --timeout 300
"""
from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.sealed_evidence.sealer import seal_evidence
from tools.sealed_evidence.recovery_gate import run_recovery_gate, GateVerdict
from tools.sealed_evidence.replay_sandbox import run_replay_sandbox
from tools.sealed_evidence.drift_attributor import process_drift_ledger


def cmd_seal(args):
    """封存任务证据。"""
    contract = seal_evidence(
        mission_id=args.mission,
        workdir=args.workdir,
        replay_cmd=args.cmd,
        replay_cwd=args.cwd,
        replay_timeout_s=args.timeout,
        output_path=args.output,
    )
    print(json.dumps(contract, indent=2, sort_keys=True))
    return 0


def cmd_gate(args):
    """运行回收闸。"""
    result = run_recovery_gate(
        workdir=args.workdir,
        mission_id=args.mission,
        replay_timeout_s=args.timeout,
        skip_replay=args.skip_replay,
    )
    output = {
        "verdict": result.verdict.value,
        "reasons": result.reasons,
        "missing_artifacts": result.missing_artifacts,
        "sha256_mismatches": result.sha256_mismatches,
        "replay_result": result.replay_result,
        "event": result.event,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.verdict == GateVerdict.PASS else 1


def cmd_drift(args):
    """处理 drift ledger。"""
    result = process_drift_ledger(
        ledger_path=args.ledger,
        workdir=args.workdir,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["summary"]["unknown"] == 0 else 1


def cmd_replay(args):
    """运行重放沙箱。"""
    result = run_replay_sandbox(
        cmd=args.cmd,
        cwd=args.cwd,
        timeout_s=args.timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["exit_code"] == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="sealed_evidence CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seal
    seal_parser = subparsers.add_parser("seal", help="Seal task evidence")
    seal_parser.add_argument("--mission", required=True, help="Mission ID")
    seal_parser.add_argument("--workdir", required=True, help="Work directory")
    seal_parser.add_argument("--cmd", default="echo test", help="Replay command")
    seal_parser.add_argument("--cwd", default=".", help="Replay working directory")
    seal_parser.add_argument("--timeout", type=int, default=300, help="Replay timeout (s)")
    seal_parser.add_argument("--output", help="Output contract file path")
    seal_parser.set_defaults(func=cmd_seal)

    # gate
    gate_parser = subparsers.add_parser("gate", help="Run recovery gate")
    gate_parser.add_argument("--workdir", required=True, help="Work directory")
    gate_parser.add_argument("--mission", required=True, help="Mission ID")
    gate_parser.add_argument("--timeout", type=int, default=300, help="Replay timeout (s)")
    gate_parser.add_argument("--skip-replay", action="store_true", help="Skip replay check")
    gate_parser.set_defaults(func=cmd_gate)

    # drift
    drift_parser = subparsers.add_parser("drift", help="Process drift ledger")
    drift_parser.add_argument("--ledger", required=True, help="Drift ledger path")
    drift_parser.add_argument("--workdir", default=".", help="Work directory")
    drift_parser.add_argument("--output", help="Output drift_cause.json path")
    drift_parser.set_defaults(func=cmd_drift)

    # replay
    replay_parser = subparsers.add_parser("replay", help="Run replay sandbox")
    replay_parser.add_argument("--cmd", required=True, help="Command to run")
    replay_parser.add_argument("--cwd", default=".", help="Working directory")
    replay_parser.add_argument("--timeout", type=int, default=300, help="Timeout (s)")
    replay_parser.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
