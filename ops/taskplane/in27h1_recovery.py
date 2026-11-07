"""IN27-H1 证据/图生成：daemon_warns.error_total 的越界轨迹与修复后衰减投影。

口径与器官一致（``registry.METRICS['error_total']``）：**滚动 60 分钟窗口内的 ERROR 行数**
（ERROR/CRITICAL/FATAL）。本脚本不复用脉冲解析器，独立按时间戳复算，用于交叉核对
（避免「用被测代码证明被测代码」）。输出：

  artifacts/in27h1/error_total_trajectory.json   复算数据（窗口曲线 + 实测点 + 交叉核对）
  artifacts/in27h1/in27h1_error_recovery.png     两张图（窗口计数轨迹 / 5 分钟 ERROR 桶）

用法::

    python3 ops/taskplane/in27h1_recovery.py [--repair-at ISO8601] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

LOGS = ["/var/log/tp-fleet/campaign_dispatcher.log", "/var/log/tp-fleet/autoheal.log"]
TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
HIGH = re.compile(r"\b(ERROR|CRITICAL|FATAL)\b")
WINDOW = timedelta(minutes=60)
WARN_TH, CRIT_TH = 3, 10          # registry.METRICS['error_total'] 的 warn/crit（只读引用，不改）


def load_error_timestamps(paths=LOGS) -> list[datetime]:
    out = []
    for p in paths:
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                m = TS.match(line)
                if m and HIGH.search(line):
                    out.append(datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc))
    return sorted(out)


def window_count(times: list[datetime], at: datetime, window: timedelta = WINDOW) -> int:
    lo = at - window
    return sum(1 for t in times if lo < t <= at)


def trajectory(times, start, end, step=timedelta(minutes=1)):
    xs, ys = [], []
    t = start
    while t <= end:
        xs.append(t)
        ys.append(window_count(times, t))
        t += step
    return xs, ys


def crossing(xs, ys, th=CRIT_TH, after=None):
    """``after`` 之后首个窗口计数 ≤ th 的时刻（= 回阈内的预计时刻）。"""
    for x, y in zip(xs, ys):
        if after is not None and x < after:
            continue
        if y <= th:
            return x
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair-at", default=None, help="修复生效时刻（ISO，默认读 artifacts 记录）")
    ap.add_argument("--out", default="artifacts/in27h1")
    ap.add_argument("--now", default=None)
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    now = datetime.fromisoformat(args.now).replace(tzinfo=timezone.utc) if args.now else \
        datetime.now(timezone.utc)
    repair_at = None
    if args.repair_at:
        repair_at = datetime.fromisoformat(args.repair_at).replace(tzinfo=timezone.utc)
    else:
        rec = out / "repair_applied_at.txt"
        if rec.exists():
            repair_at = datetime.fromisoformat(rec.read_text().strip()).replace(tzinfo=timezone.utc)

    times = load_error_timestamps()
    measured = window_count(times, now)
    anchor = repair_at or (times[-1] if times else now)
    lo = anchor - timedelta(hours=6)                     # 只看缺陷期附近，避免史前空窗
    if times:
        lo = max(lo, min(times) - timedelta(minutes=1))
    xs, ys = trajectory(times, lo.replace(second=0, microsecond=0), now + timedelta(minutes=75))
    cross = crossing(xs, ys, after=anchor)

    data = {
        "metric": "daemon_warns.error_total",
        "unit": "lines / rolling 60 min",
        "thresholds": {"warn": WARN_TH, "crit": CRIT_TH, "note": "阈值只读引用，未改动"},
        "as_of": now.isoformat(timespec="seconds"),
        "repair_applied_at": repair_at.isoformat(timespec="seconds") if repair_at else None,
        "error_lines_total_in_log": len(times),
        "error_total_measured": measured,
        "last_error_at": times[-1].isoformat(timespec="seconds") if times else None,
        "projected_back_in_bounds_at": cross.isoformat(timespec="seconds") if cross else None,
        "back_in_bounds_rule": f"error_total <= {CRIT_TH} (crit 即影子立单触发线) 首次成立的时刻",
        "window_minutes": int(WINDOW.total_seconds() // 60),
        "errors_last_30min": sum(1 for t in times if t > now - timedelta(minutes=30)),
        "trajectory": [{"t": x.isoformat(timespec="minutes"), "error_total": y}
                       for x, y in zip(xs, ys)],
    }
    (out / "error_total_trajectory.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 图 ────────────────────────────────────────────────────────────
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.0, 6.6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    ax1.step(xs, ys, where="post", color="#c0392b", lw=1.8,
             label="error_total (rolling 60 min, recomputed)")
    ax1.axhline(CRIT_TH, color="#e74c3c", ls="--", lw=1.2, label=f"crit threshold = {CRIT_TH}")
    ax1.axhline(WARN_TH, color="#f39c12", ls=":", lw=1.2, label=f"warn threshold = {WARN_TH}")
    if repair_at:
        ax1.axvline(repair_at, color="#27ae60", lw=1.8,
                    label="root-cause fix applied (state backfill)")
    if cross:
        ax1.axvline(cross, color="#2c3e50", lw=1.2, ls="-.",
                    label=f"projected back in bounds {cross:%H:%M}Z")
        ys_cross = window_count(times, cross)
        ax1.plot([cross], [ys_cross], "o", color="#2c3e50", ms=5)
    ax1.set_ylabel("ERROR lines in window")
    ax1.set_title("IN27-H1  daemon_warns.error_total — defect flood, repair and window decay\n"
                  "(KeyError 'arbiter_attempts' from a legacy autonomy state; ~45 s cadence)")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(alpha=0.3)

    # 5 分钟桶
    bucket = timedelta(minutes=5)
    b_start = min(times).replace(second=0, microsecond=0) - bucket if times else now
    bx, by = [], []
    t = b_start
    end = now
    while t < end:
        bx.append(t + bucket / 2)
        by.append(sum(1 for x in times if t <= x < t + bucket))
        t += bucket
    ax2.bar(bx, by, width=bucket * 0.8, color="#7f8c8d", label="ERROR lines / 5 min")
    if repair_at:
        ax2.axvline(repair_at, color="#27ae60", lw=1.8)
    ax2.set_ylabel("lines / 5 min")
    ax2.set_xlabel("2026-11-07 (UTC)")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "in27h1_error_recovery.png", dpi=150)
    print(json.dumps({k: v for k, v in data.items() if k != "trajectory"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())