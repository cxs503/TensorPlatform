"""生成能力审计报告的图与指标 JSON（零领域语义：只读 report/criteria/golden 结果）。

产出（``artifacts/capability_audit_gate/``）::

    figures/level_tally.png        五级分布（当前审计 vs 基线）
    figures/agreement_vs_baseline.png  一致率 vs 多数类基线（金标准语料 / 真实台账）
    figures/runtime_cache.png      冷/热缓存运行耗时
    figures/gate_matrix.png        5×5 等级变化判定矩阵（回归防护检出面）
    metrics.json                   图中所有数字（程序化注入，零手抄）
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.capability_audit_gate import engine, gate as gate_mod, spec as spec_mod, taxonomy  # noqa: E402

OUT = REPO / "artifacts" / "capability_audit_gate"
GOLDEN = REPO / "config" / "capability_audit_gate" / "golden"
SPEC = REPO / "config" / "capability_audit_gate" / "spec.json"
CONTRAST = REPO / "artifacts" / "capability_ledger" / "capability_ledger_resolved.json"
LEVELS = list(reversed(taxonomy.LEVELS))


def _load_report() -> dict:
    return json.loads((OUT / "report.json").read_text(encoding="utf-8"))


def fig_levels(report: dict, baseline: dict, metrics: dict) -> Path:
    cur = report["summary"]["level_tally"]
    base = taxonomy.tally(baseline["levels"].values())
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = range(len(LEVELS))
    w = 0.38
    ax.bar([i - w / 2 for i in x], [base[lv] for lv in LEVELS], w, label="baseline (frozen)")
    ax.bar([i + w / 2 for i in x], [cur[lv] for lv in LEVELS], w, label="current audit")
    ax.set_xticks(list(x))
    ax.set_xticklabels(LEVELS, rotation=15)
    ax.set_ylabel("capabilities")
    ax.set_title("Evidence-level distribution (21 capabilities)")
    ax.legend()
    for i, lv in enumerate(LEVELS):
        ax.text(i - w / 2, base[lv] + 0.2, str(base[lv]), ha="center", fontsize=8)
        ax.text(i + w / 2, cur[lv] + 0.2, str(cur[lv]), ha="center", fontsize=8)
    ax.set_ylim(0, max(max(base.values()), max(cur.values())) + 3)
    fig.tight_layout()
    path = OUT / "figures" / "level_tally.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    metrics["level_tally_current"] = cur
    metrics["level_tally_baseline"] = base
    return path


def fig_agreement(report: dict, metrics: dict) -> Path:
    golden = json.loads((OUT / "golden_validation.json").read_text(encoding="utf-8"))
    real = report.get("contrast") or {}
    groups = ["golden corpus\n(10 labelled)", "real ledger\n(21 vs contrast)"]
    agree = [golden["agreement"], real.get("agreement") or 0.0]
    base = [golden["majority_baseline"], real.get("majority_baseline") or 0.0]
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    x = range(len(groups))
    w = 0.36
    ax.bar([i - w / 2 for i in x], base, w, label="majority-class baseline", color="#999999")
    ax.bar([i + w / 2 for i in x], agree, w, label="auto-audit agreement", color="#2b6cb0")
    ax.axhline(0.9, ls="--", lw=1.2, color="#c53030", label="predeclared 90% gate")
    ax.set_xticks(list(x))
    ax.set_xticklabels(groups)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("agreement")
    ax.set_title("Auto-audit agreement vs majority-class baseline")
    ax.legend(loc="lower right", fontsize=8)
    for i in x:
        ax.text(i - w / 2, base[i] + 0.02, f"{base[i]:.1%}", ha="center", fontsize=8)
        ax.text(i + w / 2, agree[i] + 0.02, f"{agree[i]:.1%}", ha="center", fontsize=8)
    fig.tight_layout()
    path = OUT / "figures" / "agreement_vs_baseline.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    metrics["agreement"] = {"golden": agree[0], "golden_baseline": base[0],
                            "real": agree[1], "real_baseline": base[1]}
    return path


def fig_runtime(metrics: dict) -> Path:
    spec = spec_mod.load_spec(SPEC)
    tmp = Path(tempfile.mkdtemp())
    try:
        cache = tmp / "cache.json"
        t0 = time.perf_counter()
        engine.run_audit(spec, root=REPO, cache_path=cache)
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        warm_report = engine.run_audit(spec, root=REPO, cache_path=cache)
        warm = time.perf_counter() - t0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fig, ax = plt.subplots(figsize=(5.4, 3.4))
    ax.bar(["cold cache", "warm cache"], [cold, warm], color=["#2b6cb0", "#38a169"])
    ax.axhline(60.0, ls="--", lw=1.2, color="#c53030", label="60 s budget")
    ax.set_ylabel("seconds")
    ax.set_title("Audit runtime (21 capabilities, incremental scan cache)")
    for i, v in enumerate([cold, warm]):
        ax.text(i, v + 0.02, f"{v:.2f}s", ha="center", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUT / "figures" / "runtime_cache.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    metrics["runtime_sec"] = {"cold": round(cold, 3), "warm": round(warm, 3),
                              "warm_cache_hit_rate": warm_report["summary"]["cache"]["hit_rate"]}
    return path


def fig_gate_matrix(metrics: dict) -> Path:
    labels = {"STABLE": 0, "UPGRADE": 1, "DOWNGRADE": 2, "REFUTATION": 3}
    grid = [[labels[taxonomy.classify_change(o, n)] for n in LEVELS] for o in LEVELS]
    fig, ax = plt.subplots(figsize=(5.8, 4.6))
    im = ax.imshow(grid, cmap="RdYlGn_r", vmin=0, vmax=3)
    ax.set_xticks(range(len(LEVELS)))
    ax.set_xticklabels(LEVELS, rotation=20, fontsize=8)
    ax.set_yticks(range(len(LEVELS)))
    ax.set_yticklabels(LEVELS, fontsize=8)
    ax.set_xlabel("current level")
    ax.set_ylabel("baseline level")
    for i in range(len(LEVELS)):
        for j in range(len(LEVELS)):
            name = [k for k, v in labels.items() if v == grid[i][j]][0]
            ax.text(j, i, name[:4], ha="center", va="center", fontsize=7)
    ax.set_title("Regression gate verdict per level transition")
    fig.colorbar(im, ax=ax, ticks=[0, 1, 2, 3], label="severity")
    fig.tight_layout()
    path = OUT / "figures" / "gate_matrix.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    detections = sum(1 for o in LEVELS for n in LEVELS if taxonomy.rank(n) < taxonomy.rank(o))
    metrics["gate_matrix"] = {"downgrade_pairs": detections,
                              "refutation_pairs": sum(1 for o in LEVELS for n in LEVELS
                                                      if taxonomy.classify_change(o, n) == "REFUTATION")}
    return path


def main() -> int:
    payload = _load_report()
    report = payload["report"]
    baseline = gate_mod.load_baseline(OUT / "baseline.json")
    metrics: dict = {"generated_at": report["generated_at"],
                     "capabilities": report["summary"]["capabilities"],
                     "coverage": report["summary"]["coverage"],
                     "chain_sufficient": report["summary"]["chain_sufficient"],
                     "chain_full": report["summary"]["chain_full"],
                     "checks": payload.get("checks")}
    paths = [fig_levels(report, baseline, metrics), fig_agreement(report, metrics),
             fig_runtime(metrics), fig_gate_matrix(metrics)]
    (OUT / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2,
                                                 sort_keys=True) + "\n", encoding="utf-8")
    for p in paths:
        print(f"figure: {p.relative_to(REPO)}")
    print(json.dumps(metrics["runtime_sec"], ensure_ascii=False))
    print(json.dumps(metrics["agreement"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())