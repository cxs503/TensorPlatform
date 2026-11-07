#!/usr/bin/env python3
"""Autonomy exit dashboard CLI (IN20-R7).

Usage
-----
    python3 -m tools.autonomy.dashboard baseline \
        --window-hours 24 \
        --out artifacts/autonomy/autonomy_baseline.json \
        --markdown docs/reports/autonomy_baseline_IN20_R7.md \
        --figure artifacts/autonomy/autonomy_baseline.png \
        --chronicle

``baseline`` computes the three autonomy metrics, writes the JSON artifact
(with the reproducible SQL index), renders a markdown scoreboard, optionally
draws the figure and appends the snapshot to ``chronicle_snapshots`` so the
trend becomes queryable.

``trend`` reads the chronicle history back:

    python3 -m tools.autonomy.dashboard trend --limit 20
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .metrics import (
    PgRunner,
    build_baseline,
    chronicle_payload,
    default_daemon_roots,
)


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
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


CHRONICLE_INSERT_SQL = (
    "INSERT INTO chronicle_snapshots (ts, payload) VALUES (%(ts)s, %(payload)s::jsonb)"
)


def write_chronicle(runner, baseline: Mapping[str, Any], ts: datetime | None = None) -> dict[str, Any]:
    """Append the autonomy snapshot to ``chronicle_snapshots`` (trend source)."""
    payload = chronicle_payload(baseline)
    stamp = ts or datetime.now(timezone.utc)
    params = {"ts": stamp, "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True)}
    try:
        execute = getattr(runner, "execute", None)
        if callable(execute):
            execute(CHRONICLE_INSERT_SQL, params)
        else:  # fallback for runners that only expose a query API
            runner.fetchall(CHRONICLE_INSERT_SQL, params)
        return {"status": "written", "ts": stamp.isoformat(), "table": "chronicle_snapshots"}
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


CHRONICLE_TREND_SQL = (
    "SELECT ts, payload->'autonomy' AS autonomy FROM chronicle_snapshots "
    "WHERE payload ? 'autonomy' ORDER BY ts DESC LIMIT %(limit)s"
)


def read_trend(runner, limit: int = 20) -> list[dict[str, Any]]:
    rows = runner.fetchall(CHRONICLE_TREND_SQL, {"limit": int(limit)})
    out: list[dict[str, Any]] = []
    for ts, autonomy in rows:
        if isinstance(autonomy, str):
            autonomy = json.loads(autonomy)
        out.append({"ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts), "autonomy": autonomy})
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_markdown(baseline: Mapping[str, Any], trend: Sequence[Mapping[str, Any]] = ()) -> str:
    m1 = baseline["metrics"]["M1"]
    m2 = baseline["metrics"]["M2"]
    m3 = baseline["metrics"]["M3"]
    owner = m2["by_class"]["owner"]
    coord = m2["by_class"]["coordinator"]

    def fmt(value, unit=""):
        return "WITHHELD" if value is None else f"{value}{unit}"

    lines = [
        "# 自主度退出仪表盘 · 基线（IN20-R7）",
        "",
        f"- 生成时刻：`{baseline['generated_at']}`",
        f"- 统计窗口：`{baseline['window_start']}` → `{baseline['window_end']}`（{baseline['window_hours']} h）",
        f"- 交互通道身份（人类侧）：`{','.join(str(i) for i in baseline['interactive_user_ids'])}`",
        f"- 产出器：`{baseline['provenance']['producer']}` · commit `{baseline['provenance'].get('commit')}`",
        "",
        "## 指标一 无人类输入续航时长",
        "",
        "| 项 | 值 | 证据级 |",
        "|---|---|---|",
        f"| 续航时长 | **{fmt(m1['value_hours'], ' h')}** = {fmt(None if m1['value_hours'] is None else round(m1['value_hours'] / 24.0, 2), ' d')} | {m1['status']} |",
        f"| 最近一次人类动作 | `{m1['last_human_action_at']}` | {m1['status']} |",
        "",
        "## 指标二 人类干预次数/日（owner 与协调者分列）",
        "",
        "| 角色类 | 窗口内次数 | 折算/日 | 最近一次 | 证据级 |",
        "|---|---|---|---|---|",
        f"| owner（裁决/派单） | {fmt(owner['count_in_window'])} | {round((owner['count_in_window'] or 0) * 24.0 / max(m2['window_hours'], 1e-9), 2)} | `{owner['last_action_at']}` | {owner['status']} |",
        f"| coordinator（会话动作） | {fmt(coord['count_in_window'])} | {round((coord['count_in_window'] or 0) * 24.0 / max(m2['window_hours'], 1e-9), 2)} | `{coord['last_action_at']}` | {coord['status']} |",
        f"| 合计 | {fmt(m2['total'])} | {fmt(m2['per_day'])} | — | {m2['status']} |",
        "",
        "## 指标三 机制覆盖率（分母 = 补给/调节/放门/合并/收割/记录/自愈/文档 八项）",
        "",
        f"**覆盖率 = {m3['covered']}/{m3['denominator']} = {fmt(m3['percent'], '%')}**（证据级 {m3['status']}）",
        "",
        "| 职能 | daemon/替身 | 窗口内活动 | 判定 |",
        "|---|---|---|---|",
    ]
    for mech in baseline["detail"]["mechanisms"]:
        daemon = mech["daemon_artifact"] or "—"
        lines.append(
            f"| {mech['label']} | `{os.path.basename(daemon) if mech['daemon_artifact'] else daemon}` "
            f"| {fmt(mech['activity_count'])} | {mech['grade']}{' ✅' if mech['covered'] else ''} |"
        )

    lines += [
        "",
        "> 判定规则：`covered = daemon 产物存在 AND 窗口内活动证据 > 0`；只有 daemon 无活动 = `STUB`，只有人做 = `MOCK`。",
        "",
        "## SQL 可复算索引",
        "",
        "| 指标 | 源 | 语句 |",
        "|---|---|---|",
    ]
    for entry in baseline["sql"]:
        lines.append(f"| {entry['metric']} | `{entry['key']}` | `{entry['sql']}` |")

    if trend:
        lines += ["", "### 编年史趋势（chronicle_snapshots）", "", "| ts | 续航h | owner | coord | 覆盖率% |", "|---|---|---|---|---|"]
        for row in trend:
            a = row.get("autonomy") or {}
            lines.append(
                f"| {row['ts']} | {a.get('M1_unbroken_autonomy_hours')} | "
                f"{a.get('M2_owner_interventions')} | {a.get('M2_coordinator_interventions')} | "
                f"{a.get('M3_percent')} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_figure(baseline: Mapping[str, Any], path: str) -> str | None:
    """Two-panel scoreboard figure; returns the path or None if matplotlib is absent."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover - matplotlib optional
        return None

    _ensure_dir(path)
    m1 = baseline["metrics"]["M1"]
    m2 = baseline["metrics"]["M2"]
    m3 = baseline["metrics"]["M3"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), gridspec_kw={"width_ratios": [1.0, 1.05, 1.6]})

    # panel 1: unbroken autonomy (hours + reference markers)
    ax = axes[0]
    hours = m1["value_hours"]
    ax.bar(["unbroken\nautonomy"], [hours or 0.0], color="#2b6cb0", width=0.5)
    for ref, label in ((24.0, "1 d"), (72.0, "3 d"), (168.0, "7 d")):
        ax.axhline(ref, ls="--", lw=0.9, color="#a0aec0")
        ax.text(0.42, ref, label, fontsize=8, color="#718096", va="bottom", ha="right")
    ax.set_ylabel("hours since last human action")
    ax.set_title(f"M1  no-human-input endurance\n{m1['status']}", fontsize=10)
    if hours:
        ax.text(0, hours / 2, f"{hours:.1f} h\n({hours/24:.2f} d)", ha="center", va="center",
                color="white", fontsize=10, fontweight="bold")

    # panel 2: interventions per day by actor class
    ax = axes[1]
    classes = ["owner", "coordinator"]
    counts = [m2["by_class"][c]["count_in_window"] or 0 for c in classes]
    bars = ax.bar(classes, counts, color=["#c05621", "#2f855a"], width=0.55)
    ax.set_ylabel(f"actions in {baseline['window_hours']:.0f} h window")
    ax.set_title(f"M2  human interventions/day\nper_day total = {m2['per_day']}", fontsize=10)
    for bar, value in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, value, str(value), ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, max(counts + [1]) * 1.35)

    # panel 3: mechanism coverage
    ax = axes[2]
    mechs = baseline["detail"]["mechanisms"]
    labels = [m["key"] for m in mechs]
    values = [1 if m["covered"] else 0.5 if m["daemon_present"] else 0 for m in mechs]
    colors = ["#2f855a" if m["covered"] else ("#d69e2e" if m["daemon_present"] else "#c53030") for m in mechs]
    ax.barh(labels, values, color=colors)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("0 = manual (MOCK) · 0.5 = daemon w/o evidence (STUB) · 1 = covered")
    ax.set_title(f"M3  mechanism coverage  {m3['covered']}/{m3['denominator']} = {m3['percent']}%", fontsize=10)
    for i, m in enumerate(mechs):
        ax.text(1.02, i, str(m["activity_count"]), va="center", fontsize=8, color="#4a5568")

    fig.suptitle(
        "Autonomy exit dashboard — baseline (IN20-R7), zero domain semantics", fontsize=11
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Autonomy exit dashboard (IN20-R7)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="compute the three autonomy metrics")
    b.add_argument("--window-hours", type=float, default=24.0)
    b.add_argument("--out", default="artifacts/autonomy/autonomy_baseline.json")
    b.add_argument("--markdown", default=None)
    b.add_argument("--figure", default=None)
    b.add_argument("--chronicle", action="store_true", help="append snapshot to chronicle_snapshots")
    b.add_argument("--interactive-user-ids", default=os.environ.get("TP_INTERACTIVE_USER_IDS", "2"))
    b.add_argument("--daemon-roots", default=None, help="os.pathsep separated; default repo ops/ + tools/")
    b.add_argument("--dsn", default=None)
    b.add_argument("--now", default=None, help="ISO instant, for reproducible replays")

    t = sub.add_parser("trend", help="read the chronicle trend back")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--dsn", default=None)
    t.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = PgRunner(args.dsn)

    if args.cmd == "trend":
        rows = read_trend(runner, args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0

    ids = [int(x) for x in str(args.interactive_user_ids).replace(";", ",").split(",") if x.strip()]
    if args.daemon_roots is None:
        roots = default_daemon_roots(_repo_root())
    else:
        roots = tuple(x for x in args.daemon_roots.split(os.pathsep) if x)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)

    baseline = build_baseline(
        runner,
        now=now,
        window_hours=args.window_hours,
        interactive_user_ids=ids,
        daemon_roots=roots,
        repo_commit=_git_commit(),
    )

    if args.chronicle:
        baseline["chronicle"] = write_chronicle(runner, baseline)
    trend: list[dict[str, Any]] = []
    try:
        trend = read_trend(runner, 10)
    except Exception:  # pragma: no cover - chronicle table optional
        trend = []

    _ensure_dir(args.out)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2, default=str)
    print(f"artifact: {args.out}")

    if args.markdown:
        _ensure_dir(args.markdown)
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(baseline, [r for r in trend if (r.get("autonomy") or {}).get("M1_unbroken_autonomy_hours") is not None]))
        print(f"markdown: {args.markdown}")

    if args.figure:
        made = render_figure(baseline, args.figure)
        print(f"figure: {made}")

    m1 = baseline["metrics"]["M1"]
    m2 = baseline["metrics"]["M2"]
    m3 = baseline["metrics"]["M3"]
    print(
        "M1 unbroken_autonomy_hours={} ({}) | M2 owner={} coordinator={} ({}) | M3 coverage={}/{}={}% ({})".format(
            m1["value_hours"], m1["status"],
            m2["by_class"]["owner"]["count_in_window"],
            m2["by_class"]["coordinator"]["count_in_window"], m2["status"],
            m3["covered"], m3["denominator"], m3["percent"], m3["status"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())