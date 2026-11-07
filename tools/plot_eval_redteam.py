"""评价真实性红队抽样复算（IN24-E5）图：分层覆盖 / 判定与灵敏度 / 申报-重算对照。

输入 `artifacts/m5182_eval_redteam/recheck_run.json`（程序化读取，零手抄数字），
输出 `artifacts/m5182_eval_redteam/redteam_summary.png`。
"""
from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts" / "m5182_eval_redteam"
E_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}


def main() -> int:
    data = json.loads((ART / "recheck_run.json").read_text(encoding="utf-8"))
    population = data["population_by_stratum"]
    sample = data["sample_by_stratum"]
    strata = sorted(set(population) | set(sample))

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    # (1) 分层覆盖：总体 vs 抽样
    ax = axes[0]
    xs = range(len(strata))
    ax.bar([x - 0.2 for x in xs], [population.get(s, 0) for s in strata], width=0.4,
           label="confirmed population", color="#7b8fa6")
    ax.bar([x + 0.2 for x in xs], [sample.get(s, 0) for s in strata], width=0.4,
           label="red-team sample", color="#c8553d")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(strata)
    ax.set_ylabel("rows")
    ax.set_title(f"Stratified coverage (n={data['sample']}/{data['population']})")
    for x, s in zip(xs, strata):
        ax.text(x + 0.2, sample.get(s, 0) + 0.4, str(sample.get(s, 0)), ha="center", fontsize=9)
    ax.legend(frameon=False, fontsize=9)

    # (2) 判定分布 + 变异灵敏度
    ax = axes[1]
    verdicts = {k: v for k, v in data["verdicts"].items() if v}
    labels = list(verdicts) or ["(none)"]
    values = [verdicts[k] for k in labels] if verdicts else [0]
    ax.bar(labels, values, color="#3d6b8c")
    for i, v in enumerate(values):
        ax.text(i, v + 0.1, str(v), ha="center", fontsize=9)
    ax.set_ylabel("sampled rows")
    mut = data["controls"]["mutations"]
    ax.set_title(f"Verdicts on sample · injected-fault detection {mut['detected']}/{mut['total']}")
    ax.tick_params(axis="x", labelsize=8)

    # (3) 申报 vs 重算（E 级），按盲序排列
    ax = axes[2]
    rows = data["rechecks"]
    order = [r["blind_order"] for r in rows]
    declared = [E_ORDER.get(r["declared_e"], -1) for r in rows]
    recomputed = [E_ORDER.get(r["recomputed_e"], -1) for r in rows]
    ax.plot(order, declared, "o", label="declared E", color="#c8553d", markersize=7)
    ax.plot(order, recomputed, "x", label="recomputed E (blind)", color="#2f6f4e", markersize=8)
    for o, d, r in zip(order, declared, recomputed):
        if d != r:
            ax.annotate("mismatch", (o, max(d, r)), fontsize=7, color="black")
    ax.set_xticks(order)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.set_yticklabels(["E0", "E1", "E2", "E3", "E4"])
    ax.set_xlabel("blind order")
    ax.set_title("Declared vs blindly recomputed evidence level")
    ax.legend(frameon=False, fontsize=9)

    fig.suptitle("IN24-E5 red-team recheck of confirmed S×E evaluations", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = ART / "redteam_summary.png"
    fig.savefig(out, dpi=110)
    print(out, out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())