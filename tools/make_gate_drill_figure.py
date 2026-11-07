#!/usr/bin/env python3
"""放门演练结果图（IN20-R2）：判据矩阵 + 沙箱审计链。

纯后端/治理任务没有场量云图：这里画的是**判据矩阵**（等价于「状态场」逐门
取值）与**审计链计数**（等价于收敛曲线：释放→回滚的可逆性）。数字全部从
artifacts/gate_auto_release_drill.json 程序化读取，不手抄。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DRILL = REPO / "artifacts/gate_auto_release_drill.json"
OUT = REPO / "artifacts/gate_auto_release_drill.png"

# 图内文案用 ASCII：宿主只装了 Noto Sans（无 CJK 字形），中文会掉成豆腐块
SHORT = {129: "G13 systems-pierce", 144: "G14 gap-ledger", 153: "G15 autoregression",
         165: "G16 pool-close", 170: "G18 chronicler", 185: "G20 spec(SxE)"}


def main() -> int:
    data = json.loads(DRILL.read_text(encoding="utf-8"))
    gates = data["production"]["gates"]
    ids = [g["node_id"] for g in gates]
    labels = [SHORT.get(i, str(i)) for i in ids]

    # 列：上游就绪 / 工件可解析 / 非治理门（owner_only 取反）
    matrix = np.array([[
        1 if g["checks"]["upstream"]["ok"] else 0,
        1 if g["checks"]["artifacts"]["ok"] else 0,
        1 if not g["checks"]["owner_only"]["flag"] else 0,
    ] for g in gates], dtype=float)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 4.6),
                                   gridspec_kw={"width_ratios": [1.25, 1]})

    im = ax1.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax1.set_xticks(range(3))
    ax1.set_xticklabels(["upstream all done", "criteria artifacts\nresolvable", "not governance"], fontsize=8.5)
    ax1.set_yticks(range(len(labels)))
    ax1.set_yticklabels(labels, fontsize=9)
    ax1.set_title("G13-G20 gate criteria matrix (production, read-only drill)", fontsize=10.5)
    for r in range(matrix.shape[0]):
        for c in range(matrix.shape[1]):
            ax1.text(c, r, "✓" if matrix[r, c] else "✗", ha="center", va="center",
                     fontsize=13, color="black")
        reason = gates[r]["reason"]
        reason = reason.encode("ascii", "ignore").decode() or "hold_owner_only"
        color = {"satisfied": "#1b5e20", "artifact_missing": "#8d6e63",
                 "no_criteria_declared": "#8d6e63", "upstream_pending": "#8d6e63"}.get(
                     reason, "#b71c1c")
        ax1.text(2.62, r, reason, ha="left", va="center", fontsize=8, color=color)
    ax1.set_xlim(-0.5, 4.9)
    fig.colorbar(im, ax=ax1, ticks=[0, 1], fraction=0.03, pad=0.28)

    rehearse = data["rehearsal"]
    run_counts = rehearse["run"]["counts"]
    names = ["auto release", "wait", "owner_only", "rollback"]
    sandbox_vals = [run_counts.get("release", 0), run_counts.get("wait", 0),
                    run_counts.get("hold_owner_only", 0),
                    rehearse["audit_action_counts"].get("rollback", 0)]
    prod_vals = [data["production"]["counts"]["release"],
                 data["production"]["counts"]["wait"],
                 data["production"]["counts"]["hold_owner_only"], 0]
    x = np.arange(len(names))
    ax2.bar(x - 0.2, sandbox_vals, width=0.4, label="sandbox (real UPDATE)",
            color="#1565c0")
    ax2.bar(x + 0.2, prod_vals, width=0.4, label="production (read-only)",
            color="#ef6c00")
    for i, (s, p) in enumerate(zip(sandbox_vals, prod_vals)):
        ax2.text(i - 0.2, s + 0.08, str(s), ha="center", fontsize=9)
        ax2.text(i + 0.2, p + 0.08, str(p), ha="center", fontsize=9)
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, fontsize=9)
    ax2.set_ylabel("gates / audit rows")
    ax2.set_title("actions + audit chain (release<->rollback paired)", fontsize=10.5)
    ax2.legend(fontsize=8, loc="upper right")
    ax2.grid(axis="y", alpha=0.3)

    note = (f"upstream ready {sum(1 for g in gates if g['checks']['upstream']['ok'])}/{len(gates)} | "
            f"artifacts resolvable {sum(1 for g in gates if g['checks']['artifacts']['ok'])}/{len(gates)} | "
            f"owner_only protected {sum(1 for g in gates if g['checks']['owner_only']['flag'])} | "
            f"production snapshot unchanged = {data['production']['production_untouched']}")
    fig.text(0.01, 0.01, note, fontsize=8.5, color="#37474f")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT, dpi=84)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())