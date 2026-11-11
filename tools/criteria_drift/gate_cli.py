"""Gate CLI: the automation entry point a campaign-DAG driver can call.

    python3 -m tools.criteria_drift.gate_cli \
        --proposal proposal.json --report acceptance_report.json \
        --plan-id 1 --node-id p474w1-n02 --sqlite campaign.db --requested pass

Exit codes (so a DAG driver can branch without parsing text):

* ``0`` - clean audit, requested verdict applied (``pass`` -> ``done``, ``fail`` -> ``failed``)
* ``3`` - drift over policy: node forced to ``blocked``, ``block_reason`` written
* ``2`` - usage / IO error
* ``4`` - node not found

``--api-base`` + ``--node-db-id`` + ``--token`` post the same transition through
``POST /campaigns/nodes/{id}/verdict`` instead of the local mirror.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from . import campaign_integration, compare, matching


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="criteria-drift gate")
    ap.add_argument("--proposal", required=True, help="proposal/report JSON with acceptance_criteria")
    ap.add_argument("--report", required=True)
    ap.add_argument("--plan-id", type=int, default=0)
    ap.add_argument("--node-id", default="")
    ap.add_argument("--sqlite", default="", help="local campaign.db (campaign_nodes mirror)")
    ap.add_argument("--api-base", default="")
    ap.add_argument("--node-db-id", type=int, default=0)
    ap.add_argument("--token", default="")
    ap.add_argument("--requested", choices=("pass", "fail"), default="pass")
    ap.add_argument("--out", default="", help="optional path to write the audit JSON")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read proposal/report: {exc}", file=sys.stderr)
        return 2

    audit = compare.audit(proposal, report,
                          proposal_ref=str(args.proposal), report_ref=str(args.report),
                          policy=matching.load_policy())
    payload = audit.as_dict()

    applied = None
    if args.api_base and args.node_db_id and args.token:
        applied = campaign_integration.apply_via_api(args.api_base, args.node_db_id, audit,
                                                     args.token, args.requested)
    elif args.sqlite and args.node_id:
        conn = sqlite3.connect(args.sqlite)
        try:
            applied = campaign_integration.apply_sqlite(conn, args.plan_id, args.node_id,
                                                        audit, args.requested)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 4
        finally:
            conn.close()

    if args.out:
        Path(args.out).write_text(json.dumps({"audit": payload, "applied": applied},
                                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps({
            "summary": payload["summary"],
            "block_reason": payload["block_reason"],
            "recommended_actions": payload["recommended_actions"],
            "applied": applied,
        }, ensure_ascii=False, indent=2))
    return 3 if audit.block else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())