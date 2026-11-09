"""命令行入口：``python3 -m tools.proposal_seeding.cli <command>``。

命令（全部输出规范 JSON，便于落工件与审计）
--------------------------------------------
* ``aggregate --inputs A B --out agg.json``   多提案者聚合（相似度矩阵/分簇/吸收明细）
* ``seed --agg agg.json --wave p423w2 --out plan.json``  播种计划
* ``gate --plan plan.json [--snapshot snap.json] --out gate.json``  机器门复核
* ``pipeline --inputs ... --wave ... --out-dir DIR``  聚合→播种→自回归→门 全链
* ``replay --inputs ... --wave ...``     同输入跑两遍，比 ext_id 序列（幂等证据）
* ``advance --plan plan.json --wave-next ...``  溢出项反哺下一波（闭环第二圈）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .aggregate import aggregate
from .autoregress import advance_wave, ensure_platform_node, pipeline_telemetry, platform_proposal
from .config import load_config
from .gate import check_plan
from .seed import seed_plan


def _dump(obj: Any, path: str | Path | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _ext_id_sequence(plan: dict[str, Any]) -> list[str]:
    return [n["ext_id"] for n in plan["nodes"]]


def _run_pipeline(inputs: Sequence[str], wave: str, plan_id: str, cfg_path: str | None,
                  global_ext_ids: Sequence[str] = (), exclude_ext_ids: Sequence[str] = (),
                  parent_plan: str | None = None) -> dict[str, Any]:
    cfg = load_config(cfg_path)
    agg = aggregate(inputs, cfg)
    plan = seed_plan(agg, cfg, wave=wave, plan_id=plan_id, parent_plan=parent_plan)
    gate_pre = check_plan(plan, cfg, global_ext_ids=global_ext_ids,
                          exclude_ext_ids=exclude_ext_ids)
    telemetry = pipeline_telemetry(agg, plan, gate_pre)
    plan, injection = ensure_platform_node(plan, cfg, telemetry=telemetry)
    gate = check_plan(plan, cfg, global_ext_ids=global_ext_ids,
                      exclude_ext_ids=exclude_ext_ids)
    return {"config": cfg, "aggregate": agg, "plan": plan, "gate_pre_autoregress": gate_pre,
            "injection": injection, "gate": gate, "telemetry": telemetry}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="proposal_seeding", description=__doc__)
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_agg = sub.add_parser("aggregate")
    p_agg.add_argument("--inputs", nargs="+", required=True)
    p_agg.add_argument("--out", default=None)

    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--agg", required=True)
    p_seed.add_argument("--wave", required=True)
    p_seed.add_argument("--plan-id", default="generated")
    p_seed.add_argument("--out", default=None)

    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--plan", required=True)
    p_gate.add_argument("--snapshot", default=None, help="含 global_ext_ids 的快照 JSON")
    p_gate.add_argument("--out", default=None)

    p_pipe = sub.add_parser("pipeline")
    p_pipe.add_argument("--inputs", nargs="+", required=True)
    p_pipe.add_argument("--wave", required=True)
    p_pipe.add_argument("--plan-id", default="generated")
    p_pipe.add_argument("--snapshot", default=None)
    p_pipe.add_argument("--out-dir", default=None)

    p_replay = sub.add_parser("replay")
    p_replay.add_argument("--inputs", nargs="+", required=True)
    p_replay.add_argument("--wave", required=True)
    p_replay.add_argument("--out", default=None)

    p_adv = sub.add_parser("advance")
    p_adv.add_argument("--plan", required=True)
    p_adv.add_argument("--wave-next", required=True)
    p_adv.add_argument("--out", default=None)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "aggregate":
        _dump(aggregate(args.inputs, cfg), args.out)
        return 0

    if args.cmd == "seed":
        agg = json.loads(Path(args.agg).read_text(encoding="utf-8"))
        _dump(seed_plan(agg, cfg, wave=args.wave, plan_id=args.plan_id), args.out)
        return 0

    if args.cmd == "gate":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8")) if args.snapshot else {}
        _dump(check_plan(plan, cfg, global_ext_ids=snap.get("global_ext_ids", []),
                         exclude_ext_ids=snap.get("exclude_ext_ids", [])), args.out)
        return 0

    if args.cmd == "pipeline":
        snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8")) if args.snapshot else {}
        res = _run_pipeline(args.inputs, args.wave, args.plan_id, args.config,
                            global_ext_ids=snap.get("global_ext_ids", []),
                            exclude_ext_ids=snap.get("exclude_ext_ids", []))
        payload = {
            "aggregate": res["aggregate"],
            "plan": res["plan"],
            "gate_pre_autoregress": res["gate_pre_autoregress"],
            "injection": res["injection"],
            "gate": res["gate"],
            "telemetry": res["telemetry"],
        }
        _dump(payload, (Path(args.out_dir) / "pipeline.json") if args.out_dir else None)
        return 0 if res["gate"]["all_passed"] else 1

    if args.cmd == "replay":
        first = _run_pipeline(args.inputs, args.wave, "replay", args.config)
        second = _run_pipeline(args.inputs, args.wave, "replay", args.config)
        seq1, seq2 = _ext_id_sequence(first["plan"]), _ext_id_sequence(second["plan"])
        payload = {
            "run1_ext_ids": seq1,
            "run2_ext_ids": seq2,
            "identical": seq1 == seq2,
            "node_count": len(seq1),
        }
        _dump(payload, args.out)
        return 0 if seq1 == seq2 else 2

    if args.cmd == "advance":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        _dump(advance_wave(plan, cfg, wave_next=args.wave_next), args.out)
        return 0

    return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())