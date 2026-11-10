"""报告配图：直接从台账 / 差异报告 JSON 读数作图（零手抄）。

图 1 等级分布：自动登记后能力的证据等级分布（证据强度是否被高估，一眼可见）。
图 2 逐能力结局：每个能力的测试结局构成（passed/failed/skipped/xfailed/not_run）。
图 3 marker 格式覆盖：闭环实际覆盖的 marker 书写格式数量（C1 的图形证据）。

用法::

    PYTHONPATH=. python -m tools.capability_discovery.figures
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

LEVEL_ORDER = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
STACK_ORDER = ["passed", "failed", "error", "xfailed", "skipped", "xpassed", "not_run"]
STACK_COLORS = {
    "passed": "#2a9d8f",
    "failed": "#e76f51",
    "error": "#8b2f2f",
    "xfailed": "#e9c46a",
    "skipped": "#b0b0b0",
    "xpassed": "#f4a261",
    "not_run": "#5d5d5d",
}
LEVEL_COLORS = {
    "REAL": "#2a9d8f",
    "WITHHELD": "#e9c46a",
    "MOCK": "#e76f51",
    "STUB": "#8d99ae",
    "NOT_SUPPORTED": "#5d5d5d",
}


def load_artifacts(out_dir: Path | str) -> Dict[str, object]:
    out = Path(out_dir)
    report = json.loads((out / "discovery_report.json").read_text(encoding="utf-8"))
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    return {"report": report, "ledger": ledger}


def make_figures(out_dir: Path | str = "artifacts/capability_discovery") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    arts = load_artifacts(out)
    report = arts["report"]
    ledger = arts["ledger"]
    entries = ledger["entries"]
    caps: List[str] = sorted(entries)
    levels = {cid: entries[cid]["level"] for cid in caps}
    outcomes = {cid: entries[cid]["evidence"]["outcomes"] for cid in caps}
    formats: Dict[str, int] = report["summary"]["marker_formats"]

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.2))

    # ── 图 1：等级分布 ────────────────────────────────────────────────────
    ax = axes[0]
    counts = [sum(1 for v in levels.values() if v == lv) for lv in LEVEL_ORDER]
    bars = ax.bar(LEVEL_ORDER, counts, color=[LEVEL_COLORS[lv] for lv in LEVEL_ORDER])
    for b, c in zip(bars, counts):
        if c:
            ax.text(b.get_x() + b.get_width() / 2, c + 0.08, str(c), ha="center", fontsize=11)
    ax.set_title("Evidence level after auto-registration\n(conservative default policy)", fontsize=11)
    ax.set_ylabel("capabilities")
    ax.set_ylim(0, max(counts) + 1 if max(counts) else 1)
    ax.grid(axis="y", alpha=0.3)

    # ── 图 2：逐能力结局构成 ─────────────────────────────────────────────
    ax = axes[1]
    labels = [c.replace("derived.test_synthetic_core.", "derived.") for c in caps]
    left = [0] * len(caps)
    for state in STACK_ORDER:
        vals = [outcomes.get(c, {}).get(state, 0) for c in caps]
        if not any(vals):
            continue
        ax.barh(labels, vals, left=left, color=STACK_COLORS[state], label=state)
        left = [a + b for a, b in zip(left, vals)]
    ax.set_title("Test outcomes per discovered capability\n(pytest, live run)", fontsize=11)
    ax.set_xlabel("test nodes")
    ax.legend(fontsize=8, ncol=3, loc="lower right")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.3)

    # ── 图 3：marker 格式覆盖 ────────────────────────────────────────────
    ax = axes[2]
    fmts = sorted(formats)
    vals = [formats[f] for f in fmts]
    bars = ax.barh(fmts, vals, color="#457b9d")
    for b, v in zip(bars, vals):
        ax.text(v + 0.05, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=10)
    ax.set_title(f"Marker formats covered: {len(fmts)} (criterion C1 needs >=5)", fontsize=11)
    ax.set_xlabel("capabilities using the format")
    ax.set_xlim(0, max(vals) + 1.2 if vals else 1)
    ax.grid(axis="x", alpha=0.3)

    fig.suptitle(
        "capability_discovery: pytest markers -> capability ledger (zero domain semantics)", fontsize=13
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    path = fig_dir / "discovery_summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> int:
    path = make_figures()
    print(f"figure written: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())