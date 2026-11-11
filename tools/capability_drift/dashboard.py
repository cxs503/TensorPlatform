"""能力健康度仪表板：绿/黄/红 + 历史趋势 + 图（matplotlib，ASCII 标注避免缺字形）。

历史以 jsonl 追加（每次检测一行），仪表板与趋势图都从历史渲染，报告数字零手抄。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .detector import DriftReport, GREEN, RED, YELLOW
from .runner import CaseRun


# --------------------------------------------------------------------------- #
# 健康度
# --------------------------------------------------------------------------- #
def health_summary(report: DriftReport) -> Dict[str, Any]:
    h = report.health()
    h["dashboard"] = "RED" if h["status"] == RED else ("YELLOW" if h["status"] == YELLOW else "GREEN")
    return h


def verdict_table_markdown(report: DriftReport, *, digits: int = 4) -> str:
    rows = [
        "| 能力 | 求解器 | 状态 | 指标 | 基线 | 实测 | 相对误差 | 阈值 | 说明 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in report.verdicts:
        if not v.comparisons:
            rows.append(f"| `{v.capability}` | {v.solver} | {v.status} | - | - | - | - | - | 无判据 |")
            continue
        for c in v.comparisons:
            base = "-" if c.baseline is None else f"{c.baseline:.{digits}g}"
            obs = "-" if c.observed is None else f"{c.observed:.{digits}g}"
            rel = "-" if c.rel_err is None else f"{c.rel_err * 100:.2f} %"
            note = c.note or (c.waiver or {}).get("reason", "")
            rows.append(
                f"| `{c.capability}` | {v.solver} | {c.status} | `{c.metric}` | {base} | {obs} | "
                f"{rel} | {c.threshold * 100:.2f} % | {note} |"
            )
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# 历史
# --------------------------------------------------------------------------- #
def history_record(
    report: DriftReport,
    *,
    commit: str = "",
    changed: Sequence[str] = (),
    duration_s: Optional[float] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    h = report.health()
    rec: Dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "changed_files": list(changed),
        "health": h,
        "red_capabilities": [v.capability for v in report.red],
        "yellow_capabilities": [v.capability for v in report.yellow],
        "max_rel_err": max(
            (c.rel_err or 0.0) for v in report.verdicts for c in v.comparisons
        )
        if report.verdicts
        else 0.0,
        "duration_s": duration_s,
    }
    if extra:
        rec.update(dict(extra))
    return rec


def history_append(path: str | Path, record: Mapping[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return p


def history_load(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    return plt


def figure_drift_bars(report: DriftReport, path: str | Path, *, title: str = "") -> Optional[str]:
    """每个指标的相对误差 vs 阈值线（红=超阈，灰=未超阈）。"""
    plt = _plt()
    labels: List[str] = []
    rel: List[float] = []
    thresh: List[float] = []
    colors: List[str] = []
    for v in report.verdicts:
        for c in v.comparisons:
            if c.rel_err is None:
                continue
            labels.append(f"{c.capability}:{c.metric}".replace("synth.", ""))
            rel.append(c.rel_err * 100.0)
            thresh.append(c.threshold * 100.0)
            colors.append("#c0392b" if c.status == "red" else ("#f39c12" if c.status == "waived" else "#7f8c8d"))
    if not labels:
        return None
    fig_h = max(3.0, 0.28 * len(labels) + 1.6)
    fig, ax = plt.subplots(figsize=(8.4, fig_h), dpi=150)
    y = list(range(len(labels)))
    ax.barh(y, rel, color=colors, height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.axvline(thresh[0] if thresh else 5.0, color="#2980b9", linestyle="--", linewidth=1.2,
               label=f"gate rel_tol = {thresh[0]:.2f} %" if thresh else "gate")
    ax.set_xlabel("relative error vs baseline [%]")
    ax.set_title(title or "Capability drift: observed vs baseline")
    ax.legend(loc="lower right", fontsize=8)
    ax.invert_yaxis()
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return str(out)


def figure_health_history(history: Sequence[Mapping[str, Any]], path: str | Path) -> Optional[str]:
    """历史健康度：红/黄/绿能力数堆叠柱。"""
    if not history:
        return None
    plt = _plt()
    labels = [str(i + 1) for i in range(len(history))]
    red = [int((h.get("health") or {}).get("red", 0)) for h in history]
    yellow = [int((h.get("health") or {}).get("yellow", 0)) for h in history]
    green = [int((h.get("health") or {}).get("green", 0)) for h in history]
    fig, ax = plt.subplots(figsize=(8.4, 3.2), dpi=150)
    ax.bar(labels, green, color="#27ae60", label="green")
    ax.bar(labels, yellow, bottom=green, color="#f39c12", label="yellow")
    ax.bar(labels, red, bottom=[g + y for g, y in zip(green, yellow)], color="#c0392b", label="red")
    ax.set_xlabel("monitor run #")
    ax.set_ylabel("capabilities")
    ax.set_title("Capability health history")
    ax.legend(fontsize=8)
    if len(labels) > 30:
        ax.set_xticks(labels[:: max(1, len(labels) // 20)])
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return str(out)


def figure_case_timings(runs: Iterable[CaseRun], path: str | Path, *, budget_s: float = 600.0) -> Optional[str]:
    """案例重跑耗时（A3 判据：整轮重跑 < budget）。"""
    runs = [r for r in runs if r is not None]
    if not runs:
        return None
    plt = _plt()
    names = [r.case_id for r in runs]
    durs = [r.duration_s for r in runs]
    fig, ax = plt.subplots(figsize=(8.4, 3.0), dpi=150)
    ax.bar(names, durs, color="#2c7fb8")
    ax.axhline(budget_s, color="#c0392b", linestyle="--", linewidth=1.2,
               label=f"budget {budget_s:.0f} s")
    ax.set_ylabel("wall clock [s]")
    ax.set_title("Verification case rerun time")
    ax.tick_params(axis="x", labelrotation=60, labelsize=7)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return str(out)