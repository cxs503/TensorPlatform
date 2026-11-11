#!/usr/bin/env python3
"""``python3 -m tools.blind_calibration <cmd>``：计划 → 承诺 → 观测 → 计分 → 回填 → 报告。

子命令（每一步都可单独重跑，产物逐字节可复算）
==============================================
``plan``          按注入配置出题并声明概率（**不**跑检查器）→ program.json
``commit``        把承诺哈希写进盲账本（先承诺）
``observe``       跑确定性检查器并写观测（后观测）→ observations.json
``score-blind``   复算盲性 + 计分 + 按问句类型拆解 → blind_score.json
``external``      采集真实外部预测源（平台派发前先验）并计分 → external_score.json
``backfill``      run-manifest → E1–E5 五元证据链回填审计 → backfill.json
``figures``       由上述 JSON 生成可靠性曲线/分解图（matplotlib，Agg）
``audit``         生成论文级审计表 markdown

退出码：0 全部判据通过；2 有判据未通过（honest FAIL，不阻断产物落盘）；3 致命。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from tools.abm.ledger import TraceLedger

from . import episode, evidence, ledger as ledger_mod, metrics
from .harvest import DEFAULT_VARIANT, harvest as harvest_fn

EXIT_OK = 0
EXIT_JUDGEMENT_FAIL = 2
EXIT_FATAL = 3

ART_DIR = "artifacts/blind_calibration"


def _dump(path: str | os.PathLike[str], obj: Any) -> str:
    p = Path(str(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return str(p)


def _load(path: str | os.PathLike[str]) -> Any:
    return json.loads(Path(str(path)).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    program = episode.plan(cfg, repo=args.repo)
    _dump(args.out, program)
    print(f"plan: {program['n_items']} 个问句，git_clean={program['git_clean_at_declaration']} "
          f"→ {args.out}")
    return EXIT_OK


def cmd_commit(args: argparse.Namespace) -> int:
    program = _load(args.program)
    led = TraceLedger(args.ledger, reset=args.reset)
    n = episode.commit(led, program)
    print(f"commit: {n} 条承诺入账 → {args.ledger}")
    return EXIT_OK


def cmd_observe(args: argparse.Namespace) -> int:
    program = _load(args.program)
    led = TraceLedger(args.ledger, reset=False)
    n = episode.observe(led, program, repo=args.repo)
    integ = episode.integrity(led)
    obs = {
        "schema": "blind_calibration/observations/v1",
        "n_observed": n,
        "integrity": integ,
        "ledger": led.verify(),
        "program_digest": program.get("program_digest"),
    }
    _dump(args.out, obs)
    print(f"observe: {n} 条观测；盲性 ok={integ['ok']} → {args.out}")
    return EXIT_OK if integ["ok"] else EXIT_JUDGEMENT_FAIL


def cmd_score_blind(args: argparse.Namespace) -> int:
    program = _load(args.program)
    led = TraceLedger(args.ledger, reset=False)
    integ = episode.integrity(led)
    records = episode.forecasts(led)
    ledg = ledger_mod.CalibrationLedger(args.calibration_ledger, reset=True)
    ledg.append_many(records)
    result = ledg.score(n_bins=args.bins)
    result["datasets"] = {
        "name": "blind_program",
        "n": len(records),
        "program_digest": program.get("program_digest"),
        "declared_at": program.get("declared_at"),
        "git_clean_at_declaration": program.get("git_clean_at_declaration"),
        "prior_knowledge_disclosure": program.get("prior_knowledge_disclosure"),
    }
    result["blind_integrity"] = integ
    result["by_question"] = episode.group_summary(records, n_bins=args.bins)
    result["bootstrap"] = {
        "brier": metrics.bootstrap_ci([r.prob for r in records], [r.outcome for r in records],
                                      statistic="brier", n_boot=args.n_boot, seed=args.seed),
        "max_deviation": metrics.bootstrap_ci(
            [r.prob for r in records], [r.outcome for r in records],
            statistic="max_deviation", n_boot=args.n_boot, seed=args.seed),
    }
    result["curve_with_intervals"] = metrics.curve_with_intervals(
        [r.prob for r in records], [r.outcome for r in records], args.bins)
    _dump(args.out, result)
    ok = result["all_pass"] and integ["ok"]
    print(f"score-blind: n={result['n']} brier={result['brier']['raw']:.4f} "
          f"max_dev={result['checks']['reliability_max_deviation_lt']['value']} "
          f"all_pass={result['all_pass']} integrity={integ['ok']}")
    return EXIT_OK if ok else EXIT_JUDGEMENT_FAIL


def cmd_external(args: argparse.Namespace) -> int:
    out = harvest_fn(source=args.source, fixture=args.fixture, db=args.db,
                              variant=args.variant, train_frac=args.train_frac,
                              calib_frac=args.calib_frac, l2=args.l2)
    forecasts = out["forecasts"]
    ledg = ledger_mod.CalibrationLedger(args.calibration_ledger, reset=True)
    ledg.append_many(forecasts)
    result = ledg.score(n_bins=args.bins)
    result["datasets"] = {"name": "external_prior_holdout", "n": len(forecasts),
                          **out["meta"]}
    result["bootstrap"] = {
        "brier": metrics.bootstrap_ci([r.prob for r in forecasts],
                                      [r.outcome for r in forecasts],
                                      statistic="brier", n_boot=args.n_boot, seed=args.seed),
    }
    result["curve_with_intervals"] = metrics.curve_with_intervals(
        [r.prob for r in forecasts], [r.outcome for r in forecasts], args.bins)
    result["by_group"] = metrics.groupwise([r.prob for r in forecasts],
                                           [r.outcome for r in forecasts],
                                           [r.group for r in forecasts], args.bins)
    _dump(args.out, result)
    print(f"external: n={result['n']} brier={result['brier']['raw']:.4f} "
          f"max_dev={result['checks']['reliability_max_deviation_lt']['value']} "
          f"all_pass={result['all_pass']}")
    return EXIT_OK if result["all_pass"] else EXIT_JUDGEMENT_FAIL


def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    nodes = [{"node_id": n["node_id"], "run_dir": n["run_dir"],
              "declaration": n.get("declaration") or {}} for n in cfg["nodes"]]
    result = evidence.backfill(nodes, repo=args.repo)
    _dump(args.out, result)
    table = evidence.audit_table(result)
    _dump(str(Path(args.out).with_suffix(".table.json")), table)
    print(f"backfill: nodes={result['n_nodes']} slot_coverage={result['slot_coverage']:.3f} "
          f"grades={result['grade_distribution']} artifacts_ok="
          f"{result['artifacts_verified']}/{result['artifacts_total']}")
    ok = result["slot_coverage"] == 1.0 and result["artifact_verify_rate"] == 1.0
    return EXIT_OK if ok else EXIT_JUDGEMENT_FAIL


def cmd_audit(args: argparse.Namespace) -> int:
    from . import report as report_mod
    blind = _load(args.blind) if args.blind else None
    external = _load(args.external) if args.external else None
    backfill = _load(args.backfill)
    tables = report_mod.write_audit_markdown(blind=blind, external=external,
                                             backfill=backfill, out_path=args.out)
    figures = sorted(str(p) for p in Path(ART_DIR).glob("fig*.png"))
    doc = report_mod.write_report_markdown(blind=blind, external=external,
                                           backfill=backfill,
                                           out_path=args.report_out,
                                           figure_paths=figures)
    print(f"audit: 表 → {tables}; 报告 → {doc}")
    return EXIT_OK


def cmd_figures(args: argparse.Namespace) -> int:
    from .report import write_figures
    paths = write_figures(blind=_load(args.blind) if args.blind else None,
                          external=_load(args.external) if args.external else None,
                          backfill=_load(args.backfill) if args.backfill else None,
                          out_dir=args.out_dir)
    print(f"figures: {paths}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m tools.blind_calibration",
                                description="盲预测校准账本与 run-manifest 回填器")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--repo", default=".")
        sp.add_argument("--bins", type=int, default=metrics.DEFAULT_N_BINS)
        sp.add_argument("--n-boot", type=int, default=2000)
        sp.add_argument("--seed", type=int, default=0)

    sp = sub.add_parser("plan"); common(sp)
    sp.add_argument("--config", default="config/blind_calibration_nodes.v1.json")
    sp.add_argument("--out", default=f"{ART_DIR}/program.json")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("commit"); common(sp)
    sp.add_argument("--program", default=f"{ART_DIR}/program.json")
    sp.add_argument("--ledger", default=f"{ART_DIR}/blind_ledger.jsonl")
    sp.add_argument("--reset", action="store_true")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("observe"); common(sp)
    sp.add_argument("--program", default=f"{ART_DIR}/program.json")
    sp.add_argument("--ledger", default=f"{ART_DIR}/blind_ledger.jsonl")
    sp.add_argument("--out", default=f"{ART_DIR}/observations.json")
    sp.set_defaults(func=cmd_observe)

    sp = sub.add_parser("score-blind"); common(sp)
    sp.add_argument("--program", default=f"{ART_DIR}/program.json")
    sp.add_argument("--ledger", default=f"{ART_DIR}/blind_ledger.jsonl")
    sp.add_argument("--calibration-ledger", default=f"{ART_DIR}/calibration_ledger.jsonl")
    sp.add_argument("--out", default=f"{ART_DIR}/blind_score.json")
    sp.set_defaults(func=cmd_score_blind)

    sp = sub.add_parser("external"); common(sp)
    sp.add_argument("--source", default="pg")
    sp.add_argument("--fixture", default=None)
    sp.add_argument("--db", default="")
    sp.add_argument("--variant", default=DEFAULT_VARIANT)
    sp.add_argument("--train-frac", type=float, default=0.6)
    sp.add_argument("--calib-frac", type=float, default=0.2)
    sp.add_argument("--l2", type=float, default=1.0)
    sp.add_argument("--calibration-ledger", default=f"{ART_DIR}/external_ledger.jsonl")
    sp.add_argument("--out", default=f"{ART_DIR}/external_score.json")
    sp.set_defaults(func=cmd_external)

    sp = sub.add_parser("backfill"); common(sp)
    sp.add_argument("--config", default="config/blind_calibration_nodes.v1.json")
    sp.add_argument("--out", default=f"{ART_DIR}/backfill.json")
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("audit"); common(sp)
    sp.add_argument("--blind", default=f"{ART_DIR}/blind_score.json")
    sp.add_argument("--external", default=f"{ART_DIR}/external_score.json")
    sp.add_argument("--backfill", default=f"{ART_DIR}/backfill.json")
    sp.add_argument("--out", default="docs/blind_calibration_P143W3_N7_audit.md")
    sp.add_argument("--report-out", default="docs/blind_calibration_P143W3_N7.md")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("figures"); common(sp)
    sp.add_argument("--blind", default=f"{ART_DIR}/blind_score.json")
    sp.add_argument("--external", default=f"{ART_DIR}/external_score.json")
    sp.add_argument("--backfill", default=f"{ART_DIR}/backfill.json")
    sp.add_argument("--out-dir", default=ART_DIR)
    sp.set_defaults(func=cmd_figures)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return EXIT_FATAL
    except Exception as exc:  # noqa: BLE001 —— CLI 边界：致命错误给非零码 + 短消息
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FATAL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())