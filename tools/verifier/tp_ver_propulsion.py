#!/usr/bin/env python3
"""
tp-ver-propulsion — independent verifier for open-water propeller performance.

Role separation (same contract as tp_ver_resistance.py):
  * the candidate is whatever the platform serves over HTTP; its own tests and its
    own "method" label are informational only;
  * the verdict comes from the analytic invariant battery in
    tools/verifier/pilots/propulsion_openwater.py, which the candidate cannot see.

Usage:
  python tools/verifier/tp_ver_propulsion.py \
      --api-base http://127.0.0.1:8001 \
      --candidate-id platform-propeller-wageningen-b \
      --mission-id 1524 \
      --output artifacts/verifier/propulsion_pilot_report.json

Exit code: 0 = candidate passed every invariant, 1 = at least one failed,
           2 = candidate unreachable / protocol error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../tools
sys.path.insert(0, _TOOLS)

from verifier.credit_ledger import CreditLedger              # noqa: E402
from verifier.pilots import propulsion_openwater as ow      # noqa: E402

DEFAULT_ENDPOINT = "/api/v1/propeller-design/wageningen-b"


def http_sampler(api_base: str, endpoint: str, timeout: float = 20.0, retries: int = 2):
    """Build a sampler that POSTs to the platform open-water endpoint."""
    url = api_base.rstrip("/") + endpoint

    def _sample(J: float, P_D: float, Z: int, Ae_A0: float) -> dict[str, float]:
        body = json.dumps({"J": J, "P_D": P_D, "Z": int(Z), "Ae_Ao": Ae_A0}).encode()
        last = None
        for _ in range(retries + 1):
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.load(resp)
                return {
                    "KT": float(payload["KT"]),
                    "KQ": float(payload["KQ"]),
                    "eta0": float(payload["eta0"]),
                }
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                last = exc
                time.sleep(0.2)
        raise RuntimeError(f"candidate unreachable at {url}: {last}")

    return _sample


def run(api_base: str, endpoint: str, candidate_id: str, candidate_version: str,
        mission_id: int | None, output: str | None, ledger: bool,
        sampler=None) -> dict:
    started = time.time()
    sampler = sampler or http_sampler(api_base, endpoint)
    result = ow.evaluate_candidate(sampler, candidate_id=candidate_id)
    finished = time.time()

    report = dict(result)
    report.update({
        "verifier_id": "tp-ver-propulsion",
        "candidate_version": candidate_version,
        "candidate_endpoint": api_base.rstrip("/") + endpoint,
        "started_at": started,
        "finished_at": finished,
        "duration_s": round(finished - started, 3),
        # ledger-compatible aliases
        "total_questions": result["total"],
        "pass_count": result["passed"],
        "fail_count": result["total"] - result["passed"],
        "overall_verdict": result["verdict"],
        "mission_id": mission_id,
    })

    if output:
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        with open(output, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[verifier] report written to {output}")

    if ledger:
        entry = CreditLedger().record_from_report(report, mission_id=mission_id)
        print(f"[verifier] credit ledger entry {entry.id} ({entry.verdict})")
    return report


def print_table(report: dict) -> None:
    print(f"\n{'invariant':28s} {'verdict':8s} {'measured':>12s}  detail")
    print("-" * 110)
    for inv in report["invariants"]:
        v = "PASS" if inv["passed"] else "FAIL"
        print(f"{inv['name']:28s} {v:8s} {inv['measured']:12.4f}  {inv['detail'][:60]}")
    print("-" * 110)
    print(f"overall: {report['verdict'].upper()} "
          f"({report['passed']}/{report['total']} invariants)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Independent verifier for propeller open-water performance")
    p.add_argument("--api-base", default=os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001"))
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--candidate-id", default="platform-propeller-wageningen-b")
    p.add_argument("--candidate-version", default="deployed")
    p.add_argument("--mission-id", type=int, default=None)
    p.add_argument("--output", default="artifacts/verifier/propulsion_pilot_report.json")
    p.add_argument("--no-ledger", action="store_true", help="do not append a credit ledger entry")
    args = p.parse_args(argv)

    try:
        report = run(args.api_base, args.endpoint, args.candidate_id,
                     args.candidate_version, args.mission_id, args.output,
                     not args.no_ledger)
    except RuntimeError as exc:
        print(f"[verifier] ERROR: {exc}", file=sys.stderr)
        return 2

    print_table(report)
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())