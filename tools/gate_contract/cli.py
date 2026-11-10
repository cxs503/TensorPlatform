#!/usr/bin/env python3
"""gate_contract CLI：verify / verify-all / backfill / negcontrol / crossarch / ledger-trace。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 python tools/gate_contract/cli.py 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gate_contract.acceptance import evaluate_acceptance, precheck_mission
from tools.gate_contract.backfill import backfill
from tools.gate_contract.contract import canonical_json
from tools.gate_contract.ledger import append_verdict, connect, trace_back
from tools.gate_contract.negcontrol import build_cases, measure_cases
from tools.gate_contract.verify import verify_dir, verify_gate


def _load_registry(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(obj, json_out: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate_contract")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="复算一份 gate.json")
    v.add_argument("--gate", required=True)
    v.add_argument("--registry")
    v.add_argument("--evidence-root")
    v.add_argument("--ledger")
    v.add_argument("--json")

    va = sub.add_parser("verify-all", help="复算门目录下全部 */gate.json")
    va.add_argument("--gate-root", required=True)
    va.add_argument("--registry")
    va.add_argument("--json")

    bf = sub.add_parser("backfill", help="由 claims 数据回填 gate.json")
    bf.add_argument("--claims", required=True)
    bf.add_argument("--out", required=True)
    bf.add_argument("--work-root")
    bf.add_argument("--issued-utc")
    bf.add_argument("--json")

    nc = sub.add_parser("negcontrol", help="生成并计量注入式负控")
    nc.add_argument("--gate-root", required=True)
    nc.add_argument("--registry", required=True)
    nc.add_argument("--out", required=True)
    nc.add_argument("--per-class", type=int, default=12)
    nc.add_argument("--json")

    ca = sub.add_parser("crossarch", help="跨宿主判定一致性（异构指纹 + libc 往返 + 远端件）")
    ca.add_argument("--gate-root", required=True)
    ca.add_argument("--registry", required=True)
    ca.add_argument("--claims", required=True)
    ca.add_argument("--out", required=True)
    ca.add_argument("--remote")
    ca.add_argument("--json")

    cc = sub.add_parser("crossarch-compare", help="两份 verify-all 判定件的跨机一致性")
    cc.add_argument("--reference", required=True)
    cc.add_argument("--candidate", required=True)
    cc.add_argument("--json")

    lt = sub.add_parser("ledger-trace", help="台账抽查回溯")
    lt.add_argument("--db", required=True)
    lt.add_argument("--limit", type=int, default=20)
    lt.add_argument("--json")

    ac = sub.add_parser("acceptance", help="dev_acceptance 前门（按 mission_id）")
    ac.add_argument("--mission", type=int, required=True)
    ac.add_argument("--policy")
    ac.add_argument("--json")

    args = ap.parse_args(argv)

    if args.cmd == "verify":
        res = verify_gate(args.gate, registry=_load_registry(args.registry),
                          evidence_root=args.evidence_root)
        if args.ledger:
            con = connect(args.ledger)
            append_verdict(con, res, run_cmd=f"cli verify --gate {args.gate}")
            con.close()
        _emit(res, args.json)
        return 0 if res["grade"] == "PASS" else 2
    if args.cmd == "verify-all":
        res = verify_dir(args.gate_root, registry=_load_registry(args.registry))
        summary = {"n": len(res),
                   "grades": {g: sum(1 for r in res if r["grade"] == g)
                              for g in ("PASS", "FAIL", "WITHHELD")},
                   "digests": {r["gate_id"]: r["verdict_digest"] for r in res},
                   "gates": res}
        _emit(summary, args.json)
        return 0 if summary["grades"]["WITHHELD"] == 0 and summary["grades"]["FAIL"] == 0 else 2
    if args.cmd == "backfill":
        res = backfill(args.claims, args.out, work_root=args.work_root,
                       issued_utc=args.issued_utc)
        _emit(res, args.json)
        return 0
    if args.cmd == "negcontrol":
        root = Path(args.gate_root)
        cases = build_cases([p.parent for p in sorted(root.glob("*/gate.json"))],
                            args.out, per_class=args.per_class)
        res = measure_cases(cases, _load_registry(args.registry),
                            positive_dirs=[p.parent for p in sorted(root.glob("*/gate.json"))])
        _emit(res, args.json)
        return 0 if res["false_pass"] == 0 else 3
    if args.cmd == "crossarch":
        from tools.gate_contract.crossarch import report
        res = report(args.gate_root, args.registry, args.claims, args.out,
                     remote_artifact=args.remote)
        _emit(res, args.json)
        return 0 if res["consistency_min"] == 1.0 else 4
    if args.cmd == "crossarch-compare":
        from tools.gate_contract.crossarch import compare
        res = compare(json.loads(Path(args.reference).read_text(encoding="utf-8")),
                      json.loads(Path(args.candidate).read_text(encoding="utf-8")))
        _emit(res, args.json)
        return 0 if res["consistency"] == 1.0 and res["n_compared"] else 5
    if args.cmd == "ledger-trace":
        con = connect(args.db)
        _emit(trace_back(con, limit=args.limit), args.json)
        con.close()
        return 0
    if args.cmd == "acceptance":
        policy = None
        if args.policy:
            from tools.gate_contract.acceptance import load_policy
            policy = load_policy(path=args.policy)
        res = precheck_mission(args.mission, policy=policy)
        _emit({"mission_id": args.mission, "precheck": res,
               "summary": canonical_json({"decision": (res or {}).get("decision")})}, args.json)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
