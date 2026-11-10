"""cycle_baseline.__main__ —— CLI：真实库取数 → 基线报告 → 工件 / 文档 / 图。

用法
----
    python -m tools.cycle_baseline --dry-run                       # 只打印判据
    python -m tools.cycle_baseline --source pg --repos-root /root/tensor-ecosystem \
        --out-json artifacts/cycle_baseline/baseline.json \
        --out-md docs/IN55W3-N08_cycle_baseline.md \
        --figure-dir artifacts/cycle_baseline
    python -m tools.cycle_baseline --source fixture --fixture <json> --dry-run

纪律：只读库/只读 git；唯一写盘目标为上面三个参数；``--dry-run`` 不落任何文件。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from . import figures, render, runner, segments, sources, spec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_fixture(path: pathlib.Path) -> tuple[list[dict], dict, Optional[dict], Optional[list]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw, {}, None, None
    return (raw.get("missions", []), raw.get("merge_index", {}),
            raw.get("sql_ledger"), raw.get("sql_attribution"))


def build(args: argparse.Namespace) -> tuple[dict, list[dict], dict]:
    asof = args.asof or datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.asof = asof
    if args.source == "fixture":
        missions, merge_idx, sql_ledger, sql_attr = _load_fixture(pathlib.Path(args.fixture))
        git_stats = {"source": "fixture", "missions_matched": len(merge_idx)}
        if merge_idx:
            sources.attach_merge_times(missions, {int(k): v for k, v in merge_idx.items()})
        report = runner.build_report(missions, sql_ledger=sql_ledger,
                                     sql_attribution=sql_attr, source="fixture",
                                     window_days=args.window_days, seed=args.seed,
                                     git_stats=git_stats, asof=asof)
        return report, missions, {"sql_ledger": sql_ledger or {}}

    snap = sources.fetch_snapshot(asof, args.window_days, args.db)
    missions = snap["cohort"]
    sql_ledger = snap["ledger_sql"]
    sql_attr = snap["attribution_sql"]
    git_stats: dict[str, Any] = {"source": "git", "repos_root": args.repos_root}
    if args.repos_root:
        gi = sources.git_merge_index(args.repos_root, since=args.git_since)
        git_stats.update(gi["stats"])
        git_stats.update(sources.attach_merge_times(missions, gi["index"]))
    else:
        git_stats["note"] = "no --repos-root: t_merge 全部缺失（覆盖率 0 → WITHHELD）"
    report = runner.build_report(missions, sql_ledger=sql_ledger, sql_attribution=sql_attr,
                                 source="pg", window_days=args.window_days,
                                 seed=args.seed, git_stats=git_stats, asof=asof)
    return report, missions, {"sql_ledger": sql_ledger}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="cycle baseline: four-segment + fairness")
    ap.add_argument("--source", default="pg", choices=("pg", "fixture"))
    ap.add_argument("--fixture", default="")
    ap.add_argument("--db", default="")
    ap.add_argument("--repos-root", default="")
    ap.add_argument("--git-since", default="60 days ago")
    ap.add_argument("--window-days", type=int, default=spec.DEFAULT_WINDOW_DAYS)
    ap.add_argument("--asof", default="",
                    help="窗口锚点 ISO-8601 UTC；缺省=运行时刻。固化它才能复现同一 cohort")
    ap.add_argument("--seed", type=int, default=spec.DEFAULT_SEED)
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--figure-dir", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    report, missions, extra = build(args)
    records = segments.build_records(missions)

    print(json.dumps({
        "asof": report["asof"],
        "cohort_n": report["cohort"]["n"],
        "coverage": {s: report["coverage_gate"]["per_segment"][s]["coverage"]
                     for s in spec.SEGMENTS},
        "withheld": report["coverage_gate"]["withheld_segments"],
        "criteria": {k: v["pass"] for k, v in report["criteria"].items()
                     if k != "overall"},
        "detector_f1": report["criteria"]["b_detector_f1"]["detail"],
        "jain": {p: report["policy_simulator"]["policies"][p]["measured"]["jain_count"]
                 for p in report["policy_simulator"]["policies"]},
        "provenance_rows": report["provenance"]["verify"]["n_rows"],
    }, indent=2, ensure_ascii=False))

    if args.dry_run:
        return 0

    if args.out_json:
        path = pathlib.Path(args.out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[ok] json → {path}")
    figs: list[dict] = []
    if args.figure_dir:
        figs = figures.render_all(report, records, pathlib.Path(args.figure_dir))
        for f in figs:
            print(f"[ok] fig  → {f['path']}")
    if args.out_md:
        path = pathlib.Path(args.out_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render.render_markdown(report, figures=figs), encoding="utf-8")
        print(f"[ok] md   → {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())