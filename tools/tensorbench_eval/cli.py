"""``python -m tools.tensorbench_eval.cli`` —— 递交 / 取证 / L4 复核（命令行面）。

DSN 优先级：``--dsn`` > ``$TP_TENSORBENCH_DSN`` > ``$DATABASE_URL`` >
``postgresql+psycopg2:///tensorplatform``（本机 peer 认证）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import contract, store
from tools.maturity_gate import benchmark_source


def _engine(dsn: str | None):
    from sqlalchemy import create_engine
    url = (dsn or os.environ.get("TP_TENSORBENCH_DSN") or os.environ.get("DATABASE_URL")
           or "postgresql+psycopg2:///tensorplatform")
    return create_engine(url, future=True)


def _mission(conn, mission_id: int):
    from sqlalchemy import text
    row = conn.execute(text("SELECT id, goal, repo, result FROM tensor_dev_missions "
                            "WHERE id = :mid"), {"mid": int(mission_id)}).mappings().first()
    return None if row is None else dict(row)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tensorbench-eval")
    ap.add_argument("--dsn", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sub = sub.add_parser("submit", help="按 mission 绑定的规格递交集群评价作业")
    p_sub.add_argument("--mission-id", type=int, required=True)
    p_sub.add_argument("--spec", help="规格 JSON 路径（覆盖 mission.result.tensorbench）")
    p_sub.add_argument("--ref", default="main")
    p_sub.add_argument("--timeout", type=int, default=1800)

    p_ing = sub.add_parser("ingest", help="用集群作业的原始结果取证落库")
    p_ing.add_argument("--mission-id", type=int, required=True)
    p_ing.add_argument("--cluster-job-id", type=int, required=True)
    p_ing.add_argument("--spec", required=True, help="规格 JSON 路径")
    p_ing.add_argument("--result", required=True, help="TensorBench result JSON 路径")
    p_ing.add_argument("--stdout", required=True, help="workload stdout 日志路径")

    p_l4 = sub.add_parser("l4", help="L4 基准判据复核（有实证行=PASS；无行=claimed_not_proven）")
    p_l4.add_argument("--mission-id", type=int, required=True)
    p_l4.add_argument("--tolerance-rel", type=float, default=None,
                      help="判据容差下限（缺省用行内 tolerance_rel）")

    args = ap.parse_args(argv)
    engine = _engine(args.dsn)
    with engine.connect() as conn:
        if args.cmd == "submit":
            mission = _mission(conn, args.mission_id)
            if mission is None:
                print(f"mission {args.mission_id} not found", file=sys.stderr)
                return 2
            spec = (json.loads(Path(args.spec).read_text(encoding="utf-8"))
                    if args.spec else (store.spec_from_mission(mission) or {}).get("spec"))
            if not spec:
                print("no benchmark spec bound to mission (use --spec)", file=sys.stderr)
                return 2
            out = store.submit_evaluation(conn, mission, spec=spec, ref=args.ref,
                                          timeout=args.timeout)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0
        if args.cmd == "ingest":
            out = store.ingest_result(
                conn, mission_id=args.mission_id, cluster_job_id=args.cluster_job_id,
                spec=json.loads(Path(args.spec).read_text(encoding="utf-8")),
                result=json.loads(Path(args.result).read_text(encoding="utf-8")),
                workload_stdout=Path(args.stdout).read_text(encoding="utf-8"))
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0 if out["verified"] else 1
        rows = store.rows_for_mission(conn, args.mission_id)
        out = benchmark_source.verdict(rows, tolerance_rel=args.tolerance_rel,
                                       claimed_level=store.declared_level(
                                           _mission(conn, args.mission_id) or {}))
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["status"] == benchmark_source.VERIFIED else 1


if __name__ == "__main__":
    raise SystemExit(main())
