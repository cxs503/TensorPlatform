#!/usr/bin/env python3
"""SR inheritance-readiness instrument CLI (IN27-H7).

Usage
-----
    # produce the artifact + weekly report + figure and append the s8 clock slot
    python3 -m tools.autonomy.sr_dashboard snapshot \
        --window-days 30 --sample-size 200 \
        --out artifacts/sr/sr_baseline.json \
        --markdown docs/reports/IN27_H7_sr_inheritance_readiness.md \
        --figure artifacts/sr/sr_baseline.png \
        --chronicle

    # read the trend back out of the chronicle clock
    python3 -m tools.autonomy.sr_dashboard trend --limit 30

    # prove the instrument runs every day (仪表连续产出)
    python3 -m tools.autonomy.sr_dashboard continuity --days 14

``snapshot`` is idempotent with respect to its inputs: the same database state
and the same ``--now`` produce byte-identical JSON (sorted keys, ISO stamps).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from . import sr_readiness as SR
from .metrics import PgRunner


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", _repo_root(), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # pragma: no cover - git not available
        return None


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


def _fmt(value: Any, unit: str = "", digits: int = 2) -> str:
    if value is None:
        return "WITHHELD"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def _pct(value: Any) -> str:
    if value is None:
        return "WITHHELD"
    return f"{100.0 * float(value):.1f}%"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_markdown(
    report: Mapping[str, Any],
    continuity: Mapping[str, Any] | None = None,
    trend: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Render the weekly SR report (numbers injected programmatically)."""
    m = report["metrics"]
    sr1, sr2, sr3, sr4 = m["SR1"], m["SR2"], m["SR3"], m["SR4"]
    lines: list[str] = []
    lines.append("# IN27-H7 SR 继承就绪度量 —— 周报")
    lines.append("")
    lines.append(
        f"> 生成时钟 `{report['generated_at']}` · schema `{report['schema']}` · "
        f"chronicle 时钟槽 `{report['clock']['slot']}`（{report['clock']['name']}）· "
        f"窗口 {report['window_days']:g} d / 周窗 {report['week_days']:g} d · "
        f"域语义 `{report['domain_semantics']}`"
    )
    lines.append("")
    lines.append("## 1. 记分板")
    lines.append("")
    lines.append("| 指标 | 当前值 | 目标 | 达标 | 证据等级 | 口径 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(
        f"| SR1 影子分歧率 | {_pct(sr1['value'])} | < 20% | {_fmt(sr1['meets_target'])} | "
        f"`{sr1['status']}` | {sr1.get('reason', '')} |"
    )
    lines.append(
        f"| SR2 协调官自主天数 | {_fmt(sr2['value'], ' d')} | ≥ 7 d | {_fmt(sr2['meets_target'])} | "
        f"`{sr2['status']}` | {sr2.get('reason', '')} |"
    )
    lines.append(
        f"| SR3 红线决策包完整正确率 | {_pct(sr3['value'])} | = 100%（抽检） | {_fmt(sr3['meets_target'])} | "
        f"`{sr3['status']}` | {sr3.get('reason', '')} |"
    )
    lines.append(
        f"| SR4 owner 非红线干预/周 | {_fmt(sr4['value'])} | = 0 | {_fmt(sr4['meets_target'])} | "
        f"`{sr4['status']}` | {sr4.get('reason', '')} |"
    )
    lines.append("")

    lines.append("## 2. SR1 影子分歧率（首月基线）")
    lines.append("")
    counts = sr1.get("counts") or {}
    lines.append(
        f"- 窗口内影子回合：**{sr1.get('rounds')}**（ids={sr1.get('round_ids')}），提议动作 "
        f"**{sr1.get('items')}** 条；可判定（matured/early）**{counts.get('matched', 0) + counts.get('diverged', 0)}** 条，"
        f"其中 matched={counts.get('matched', 0)}、diverged={counts.get('diverged', 0)}，"
        f"pending={counts.get('pending', 0)}、unobservable={counts.get('unobservable', 0)}。"
    )
    lines.append(
        f"- 判读窗口：look-ahead {sr1.get('lookahead_hours')} h（未到期记 `pending`，绝不进分母）；"
        f"早期读数窗口 {sr1.get('provisional_hours')} h（仅在已出现处置证据时提前判定）。"
    )
    if sr1.get("horizon_reads"):
        lines.append("")
        lines.append("| look-ahead | 分歧率 | 可判定/条目 | 已到期 |")
        lines.append("|---|---|---|---|")
        for read in sr1["horizon_reads"]:
            lines.append(
                f"| {read['horizon_hours']:g} h | {_pct(read['value'])} | "
                f"{read['decided']}/{sum(read['counts'].values())} | {_fmt(read.get('settled'))} |"
            )
        if sr1.get("baseline_value") is not None:
            base = next(
                (r for r in sr1["horizon_reads"] if r["horizon_hours"] == sr1.get("baseline_horizon_hours")), {}
            )
            bc = base.get("counts") or {}
            lines.append("")
            lines.append(
                f"- **首月基线读数**：{sr1['baseline_horizon_hours']:g} h look-ahead 上 {_pct(sr1['baseline_value'])}"
                f"——该读数的分母是 {base.get('decided')}/{sr1.get('items')} 条已到期条目"
                f"（matched={bc.get('matched', 0)}、diverged={bc.get('diverged', 0)}），"
                f"另有 {bc.get('pending', 0)} 条 pending 不计入；主读数仍取 {sr1.get('lookahead_hours'):g} h。"
            )
        else:
            lines.append("")
            lines.append(
                "- **首月基线读数**：尚无任一 horizon 全部到期——基线数字随影子官逐拍积累自动落定"
                "（本仪表按小时连续产出，最早 1 h horizon 见上表）。"
            )
    if sr1.get("by_action"):
        lines.append("")
        lines.append("| 动作 | matched | diverged | pending | unobservable |")
        lines.append("|---|---|---|---|---|")
        for action, cell in sorted(sr1["by_action"].items()):
            lines.append(
                f"| `{action}` | {cell['matched']} | {cell['diverged']} | {cell['pending']} | {cell['unobservable']} |"
            )
    if sr1.get("entries"):
        lines.append("")
        lines.append("| # | 回合 | 动作 | 目标器官 | 判定 | 依据 | 命中证据 |")
        lines.append("|---|---|---|---|---|---|---|")
        for idx, entry in enumerate(sr1["entries"], 1):
            hits = entry.get("hits")
            lines.append(
                f"| {idx} | {entry.get('round_id')} | `{entry.get('action')}` | `{entry.get('target')}` | "
                f"**{entry.get('state')}** | {entry.get('basis')} | {hits if hits is not None else '-'} |"
            )
    lines.append("")

    lines.append("## 3. SR2 协调官自主天数")
    lines.append("")
    lines.append(f"- 续航锚点：`{sr2.get('anchor_at')}`（{sr2.get('anchor')}）→ **{_fmt(sr2.get('value'), ' d')}**")
    lines.append(
        f"- 窗口内手打决策 {sr2.get('hand_typed_decisions_in_window')} 次"
        f"（≈{_fmt(sr2.get('hand_typed_per_week'))}/周）；无手打决策的洁净天数 "
        f"{sr2.get('clean_days_in_window')}/{int(report['window_days'])}。"
    )
    lines.append("")

    lines.append("## 4. SR3 红线决策包抽检")
    lines.append("")
    lines.append(
        f"- 总体（decided）：**{sr3.get('population')}** 包；抽检 **{sr3.get('sample_size')}** 包"
        f"（md5 定序，seed `{sr3.get('seed')}`，可复算）；未决包 {sr3.get('undecided')} 个被排除。"
    )
    if sr3.get("per_check_pass"):
        lines.append("")
        lines.append("| 检查项 | 通过 | 失败 |")
        lines.append("|---|---|---|")
        label = dict(SR.PACK_CHECKS)
        for key, passed in sr3["per_check_pass"].items():
            lines.append(f"| {label.get(key, key)} | {passed} | {sr3['per_check_fail'][key]} |")
    lines.append("")

    lines.append("## 5. SR4 owner 非红线干预")
    lines.append("")
    lines.append(
        f"- 周窗内非红线手打决策 **{sr4.get('non_redline_decisions')}** 次"
        f"（kinds={sr4.get('non_redline_kinds')}）→ **{_fmt(sr4.get('value'))}/周**（目标 0）。"
    )
    lines.append(
        f"- 红线内手打决策 {sr4.get('redline_decisions')} 次（kinds={sr4.get('redline_kinds')}），不计入本指标。"
    )
    lines.append(
        f"- 红线生命周期动作（write_off/suspend/…）在周窗内 {len(sr4.get('redline_lifecycle_actions') or [])} 条。"
    )
    lines.append("")

    if continuity:
        lines.append("## 6. 仪表连续产出")
        lines.append("")
        lines.append(
            f"- 近 {continuity.get('days')} d：有快照 {len(continuity.get('days_with_snapshot') or [])} 天，"
            f"覆盖率 {_pct(continuity.get('coverage'))}，当前连续产出 {continuity.get('current_run_streak')} 天；"
            f"缺口 {continuity.get('missing_days') or '无'}。"
        )
        lines.append("")
    if trend:
        lines.append("## 7. 趋势（chronicle s8 槽）")
        lines.append("")
        lines.append("| ts | SR1 | SR2 (d) | SR3 | SR4 (/wk) |")
        lines.append("|---|---|---|---|---|")
        for row in trend[:12]:
            block = row.get("sr") or {}
            if not isinstance(block, Mapping):
                continue
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    row.get("ts"),
                    _pct((block.get("SR1") or {}).get("value")),
                    _fmt((block.get("SR2") or {}).get("value")),
                    _pct((block.get("SR3") or {}).get("value")),
                    _fmt((block.get("SR4") or {}).get("value")),
                )
            )
        lines.append("")

    lines.append("## 8. 复算入口")
    lines.append("")
    lines.append("每个数字都对应下方一条 SQL（参数已代入，可直接粘进 `psql`）：")
    lines.append("")
    for item in report["sql"]:
        lines.append(f"- `{item['metric']}` / `{item['key']}`: `{item['sql']}`")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# figure
# ---------------------------------------------------------------------------


def plot_figure(report: Mapping[str, Any], path: str, trend: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Four-panel scoreboard: value-vs-target, per-check audit, SR4 bars, trend."""
    try:  # pragma: no cover - plotting is optional in CI
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

    m = report["metrics"]
    _ensure_dir(path)
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.0))
    fig.suptitle(
        "IN27-H7 SR inheritance readiness — s8 harness clock",
        fontsize=13,
        fontweight="bold",
    )

    # panel 1: SR values normalised against their targets
    ax = axes[0][0]
    keys = ["SR1", "SR3", "SR4"]
    labels = ["SR1 divergence", "SR3 pack quality", "SR4 owner non-redline/wk"]
    values = [
        m["SR1"]["value"],
        m["SR3"]["value"],
        (m["SR4"]["value"] / 7.0) if m["SR4"]["value"] is not None else None,
    ]
    bars = ax.bar(
        labels,
        [0 if v is None else min(v, 1.5) for v in values],
        color=["#c0392b" if (v is None or not ok) else "#27ae60" for v, ok in zip(
            values,
            [m["SR1"]["meets_target"], m["SR3"]["meets_target"], m["SR4"]["meets_target"]],
        )],
    )
    ax.axhline(0.20, color="#e67e22", ls="--", lw=1.2, label="SR1 target < 20%")
    ax.axhline(1.00, color="#2c3e50", ls=":", lw=1.2, label="SR3 target = 100%")
    ax.set_ylim(0, 1.35)
    ax.set_ylabel("share / normalised")
    ax.set_title("value vs target (grey text = value)")
    for bar, value, key in zip(bars, values, keys):
        shown = "WITHHELD" if value is None else (
            f"{100 * value:.1f}%" if key != "SR4" else f"{m['SR4']['value']:.2f}/wk"
        )
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            shown,
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.legend(fontsize=7, loc="upper right")

    # panel 2: SR3 per-check pass/fail
    ax = axes[0][1]
    per_pass = m["SR3"].get("per_check_pass") or {}
    if per_pass:
        names = list(per_pass)
        passes = [per_pass[n] for n in names]
        fails = [m["SR3"]["per_check_fail"][n] for n in names]
        ax.barh(names, passes, color="#27ae60", label="pass")
        ax.barh(names, fails, left=passes, color="#c0392b", label="fail")
        ax.set_xlabel("audited packs")
        ax.set_title(f"SR3 pack audit (n={m['SR3'].get('sample_size')})")
        ax.legend(fontsize=7)
    else:
        ax.text(0.5, 0.5, "SR3 WITHHELD", ha="center", va="center")

    # panel 3: SR1 disposition states
    ax = axes[1][0]
    counts = m["SR1"].get("counts") or {}
    states = ["matched", "diverged", "pending", "unobservable"]
    ax.bar(
        states,
        [counts.get(s, 0) for s in states],
        color=["#27ae60", "#c0392b", "#95a5a6", "#7f8c8d"],
    )
    ax.set_ylabel("shadow items")
    divergence = "WITHHELD" if m["SR1"]["value"] is None else f"{100 * m['SR1']['value']:.1f}%"
    ax.set_title(
        f"SR1 shadow items by state (rounds={m['SR1'].get('rounds')}, divergence={divergence})"
    )
    for idx, state in enumerate(states):
        ax.text(idx, counts.get(state, 0), str(counts.get(state, 0)), ha="center", va="bottom", fontsize=9)

    # panel 4: trend of SR1/SR2 from the chronicle slot (single point at birth)
    ax = axes[1][1]
    points = [row for row in trend if isinstance(row.get("sr"), Mapping)]
    if points:
        xs = list(range(len(points)))[::-1]
        sr1_series = [(points[i]["sr"].get("SR1") or {}).get("value") for i in range(len(points))]
        sr2_series = [(points[i]["sr"].get("SR2") or {}).get("value") for i in range(len(points))]
        ax.plot(xs, [float("nan") if v is None else 100 * v for v in sr1_series], "o-", color="#c0392b", label="SR1 %")
        ax.axhline(20, color="#e67e22", ls="--", lw=1.0, label="SR1 target")
        ax2 = ax.twinx()
        ax2.plot(xs, [float("nan") if v is None else v for v in sr2_series], "s-", color="#2980b9", label="SR2 days")
        ax2.set_ylabel("SR2 days")
        ax.set_xlabel("snapshot index (oldest → newest)")
        ax.set_ylabel("SR1 %")
        ax.set_title("chronicle s8 trend")
        ax.legend(fontsize=7, loc="upper right")
    else:
        ax.text(0.5, 0.5, "no chronicle history yet", ha="center", va="center")

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return {"status": "written", "path": path}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _runner_from_env() -> SR.Runner:
    return PgRunner()


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def cmd_snapshot(args: argparse.Namespace) -> int:
    now = _parse_now(args.now)
    runner = _runner_from_env()
    report = SR.build_report(
        runner,
        now=now,
        window_days=args.window_days,
        week_days=args.week_days,
        lookahead_hours=args.lookahead_hours,
        provisional_hours=args.provisional_hours,
        sample_size=args.sample_size,
        seed=args.seed,
        repo_commit=_git_commit(),
    )
    continuity = SR.continuity(runner, now or datetime.now(timezone.utc), args.continuity_days)
    trend = SR.read_trend(runner, limit=args.trend_limit)
    report["continuity"] = continuity
    report["trend_points"] = len(trend)

    if args.out:
        _ensure_dir(args.out)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    if args.markdown:
        _ensure_dir(args.markdown)
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report, continuity, trend))
    if args.figure:
        report["figure"] = plot_figure(report, args.figure, trend)
    if args.chronicle:
        report["chronicle"] = SR.write_chronicle(runner, report, now)

    deliverables = {
        "module": "tools/autonomy/sr_readiness.py",
        "cli": "tools/autonomy/sr_dashboard.py",
        "tests": "tests/test_sr_readiness.py",
        "artifact": args.out,
        "weekly_report": args.markdown,
        "figure": report.get("figure", {}).get("path") if isinstance(report.get("figure"), dict) else args.figure,
        "verifier": "scripts/verify_sr_metrics.py",
        "loop": "ops/sr_instrument_loop.sh",
        "operating_manual": "docs/IN27_H7_sr_instrument.md",
        "chronicle_table": "chronicle_snapshots.payload->'s8_harness'->'sr_inheritance'",
    }
    report["acceptance"] = SR.acceptance_summary(report, deliverables, report.get("chronicle"), continuity)

    if args.out:  # persist the acceptance block + write receipt next to the artifact
        _ensure_dir(args.out)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report, continuity, trend))

    print(
        json.dumps(
            {
                "generated_at": report["generated_at"],
                "grades": report["grades"],
                "values": {k: report["metrics"][k]["value"] for k in ("SR1", "SR2", "SR3", "SR4")},
                "chronicle": report.get("chronicle"),
                "out": args.out,
                "markdown": args.markdown,
                "figure": report.get("figure"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    runner = _runner_from_env()
    print(json.dumps(SR.read_trend(runner, limit=args.limit), ensure_ascii=False, indent=2))
    return 0


def cmd_continuity(args: argparse.Namespace) -> int:
    runner = _runner_from_env()
    now = _parse_now(args.now) or datetime.now(timezone.utc)
    print(json.dumps(SR.continuity(runner, now, args.days), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SR inheritance-readiness instrument (IN27-H7)")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="compute SR1..SR4 and emit artifact/report/figure")
    snap.add_argument("--window-days", type=float, default=30.0)
    snap.add_argument("--week-days", type=float, default=7.0)
    snap.add_argument("--lookahead-hours", type=float, default=24.0)
    snap.add_argument("--provisional-hours", type=float, default=1.0)
    snap.add_argument("--sample-size", type=int, default=200)
    snap.add_argument("--seed", default="IN27-H7")
    snap.add_argument("--continuity-days", type=int, default=14)
    snap.add_argument("--trend-limit", type=int, default=60)
    snap.add_argument("--now", default=None, help="override the clock (ISO-8601, UTC)")
    snap.add_argument("--out", default=None)
    snap.add_argument("--markdown", default=None)
    snap.add_argument("--figure", default=None)
    snap.add_argument("--chronicle", action="store_true", help="append the s8 clock slot")
    snap.set_defaults(func=cmd_snapshot)

    trend = sub.add_parser("trend", help="read the s8 SR history back")
    trend.add_argument("--limit", type=int, default=30)
    trend.set_defaults(func=cmd_trend)

    cont = sub.add_parser("continuity", help="daily production continuity of the instrument")
    cont.add_argument("--days", type=int, default=14)
    cont.add_argument("--now", default=None)
    cont.set_defaults(func=cmd_continuity)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())