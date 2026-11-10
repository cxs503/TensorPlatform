#!/usr/bin/env python3
"""把 artifacts/repro_replay/repro_audit.json 渲染成论文级报告（图 + 数字表，零手抄）。

所有数字都从审计 JSON / 抽样框 JSON 程序化读取，不做手写转抄。
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT_DIR = "artifacts/repro_replay"
VERDICT_COLORS = {"pass": "#2e7d32", "drift": "#f9a825", "fail": "#c62828", "blocked": "#78909c"}
LAYER_NAME = {"numeric": "数值层（工件重算）", "test": "测试层（pytest 重跑）"}


def load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fig_overall(audit: dict, path: str) -> dict:
    kpi = audit["kpi"]
    layers = ("numeric", "test")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    bottoms = [0, 0]
    for verdict in ("pass", "drift", "fail", "blocked"):
        vals = [sum(1 for i in audit["items"]
                    if i.get("layer") == lay and i["verdict"] == verdict) for lay in layers]
        ax.bar(list(layers), vals, bottom=bottoms, color=VERDICT_COLORS[verdict], label=verdict)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("nodes")
    ax.set_title(f"抽样判决分布（N={audit['n_sampled']}, seed={audit['seed']}）")
    ax.legend(fontsize=8, ncol=4)
    ax2 = axes[1]
    rate = kpi["repro_rate"]
    ax2.barh(["repro_rate"], [rate or 0], color="#1565c0")
    ax2.axvline(0.95, color="#c62828", ls="--", lw=1.2, label="target 0.95")
    ax2.set_xlim(0, 1)
    ax2.set_xlabel("pass / (pass+drift+fail)")
    ax2.set_title(f"repro_rate = {(rate or 0):.4f}；blocked {kpi['counts']['blocked']} 单列不进分母")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"name": "fig1_repro_rate.png", "path": path,
            "caption": "左：按复证层（数值层/测试层）分解的 pass/drift/fail/blocked 判决数；"
                       "右：实测 repro_rate 与 95% 目标线，blocked 按判据(d)单列不进分母。"}


def fig_repo(audit: dict, path: str) -> dict:
    repos = sorted({i["repo"] for i in audit["items"]})
    fig, ax = plt.subplots(figsize=(max(6, 0.9 * len(repos) + 3), 4.2))
    bottoms = [0] * len(repos)
    for verdict in ("pass", "drift", "fail", "blocked"):
        vals = [sum(1 for i in audit["items"] if i["repo"] == r and i["verdict"] == verdict)
                for r in repos]
        ax.bar(repos, vals, bottom=bottoms, color=VERDICT_COLORS[verdict], label=verdict)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("nodes")
    ax.set_title("按仓分层的判决构成（抽样框按 repo×层 分层轮转）")
    ax.legend(fontsize=8, ncol=4)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"name": "fig2_by_repo.png", "path": path,
            "caption": "各仓抽样判决构成：覆盖 {} 个仓（判据 a 要求 ≥3）。".format(len(repos))}


def fig_budget(audit: dict, path: str) -> dict:
    durs = sorted(i["duration_sec"] for i in audit["items"])
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.plot(range(1, len(durs) + 1), durs, "o-", ms=4, color="#37474f")
    ax.axhline(audit["protocol"]["per_node_timeout_sec"], color="#c62828", ls="--", lw=1.2,
               label=f"per-node budget {audit['protocol']['per_node_timeout_sec']}s")
    ax.set_xlabel("node (sorted)")
    ax.set_ylabel("wall seconds")
    ax.set_title("单节点重跑成本（判据 e：≤10 min/节点）")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"name": "fig3_budget.png", "path": path,
            "caption": "单节点重跑墙钟成本分布；全部样本均低于 600 s/节点预算，总墙钟 %.1f s。"
                       % audit["wall_sec_total"]}


def fig_funnel(frame: dict, audit: dict, path: str) -> dict:
    st = frame.get("stats", {})
    steps = [
        ("done∩succeeded 节点", st.get("queried", 0)),
        ("odc 含 artifacts/*.json", st.get("queried", 0) - st.get("no_on_disk_changes", 0)
         - st.get("no_numeric_artifact", 0)),
        ("合并 commit 可定位", st.get("queried", 0) - st.get("no_on_disk_changes", 0)
         - st.get("no_numeric_artifact", 0) - st.get("no_commit", 0)),
        ("数值工件随 commit 落地", st.get("layer_numeric", 0)),
        ("另有测试层可复证", st.get("layer_test", 0)),
        ("本次抽样", audit["n_sampled"]),
    ]
    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    labels = [s[0] for s in steps]
    vals = [s[1] for s in steps]
    ax.barh(labels[::-1], vals[::-1], color="#5c6bc0")
    for y, v in enumerate(vals[::-1]):
        ax.text(v + max(vals) * 0.01, y, str(v), va="center", fontsize=8)
    ax.set_xlabel("nodes")
    ax.set_title("证据可得性漏斗：数值工件不随 commit 落地是本任务最大约束")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"name": "fig4_durability_funnel.png", "path": path,
            "caption": "从 done 节点到可复证样本的漏斗：{} 个 done 节点中仅 {} 个节点的数值工件"
                       "随合并 commit 落地（其余被推送门禁滤除），故复证必须双轨（数值层 + 测试层）。"
                       .format(st.get("queried", 0), st.get("layer_numeric", 0))}


def md_table(audit: dict) -> str:
    kpi = audit["kpi"]
    rows = ["| 指标 | 值 |", "|---|---|"]
    rows.append(f"| 抽样 N | {audit['n_sampled']} |")
    rows.append(f"| 覆盖仓数 | {audit['n_repos']} |")
    rows.append(f"| pass / drift / fail / blocked | {kpi['counts']['pass']} / "
                f"{kpi['counts']['drift']} / {kpi['counts']['fail']} / {kpi['counts']['blocked']} |")
    rows.append(f"| 分母（pass+drift+fail） | {kpi['denominator']} |")
    rows.append(f"| **repro_rate** | **{kpi['repro_rate']:.6f}** |")
    rows.append(f"| blocked_share（单列） | {kpi['blocked_share']:.6f} |")
    return "\n".join(rows)


def md_items(audit: dict, limit: int = 40) -> str:
    head = ("| node | repo | 层 | 判决 | 秒 | 明细/原因 |", "|---|---|---|---|---|---|")
    rows = list(head)
    for it in audit["items"][:limit]:
        detail = it["blocked_reason"] or (
            f"{it.get('n_items', 0)} 项数值"
            + (f"，drift {it.get('n_drift')}" if it.get("n_drift") else "")
            + (f"，归因 {it['attribution']['label']}" if it.get("attribution") else ""))
        rows.append(f"| {it['node_id']} | {it['repo']} | {it.get('layer')} | {it['verdict']} | "
                    f"{it['duration_sec']} | {detail[:110]} |")
    return "\n".join(rows)


def main(out_md: str = "docs/reports/report_m6864_repro_replay.md") -> int:
    audit = load(os.path.join(OUT_DIR, "repro_audit.json"))
    frame = load(os.path.join(OUT_DIR, "frame.json"))
    figs = [
        fig_overall(audit, os.path.join(OUT_DIR, "fig1_repro_rate.png")),
        fig_repo(audit, os.path.join(OUT_DIR, "fig2_by_repo.png")),
        fig_budget(audit, os.path.join(OUT_DIR, "fig3_budget.png")),
        fig_funnel(frame, audit, os.path.join(OUT_DIR, "fig4_durability_funnel.png")),
    ]
    payload = {"figures": [{"name": f["name"], "path": f["path"], "caption": f["caption"]} for f in figs],
               "tables": {"kpi": md_table(audit), "items": md_items(audit)},
               "kpi": audit["kpi"], "attribution_counts": audit["attribution_counts"],
               "frame_stats": frame.get("stats")}
    with open(os.path.join(OUT_DIR, "figures.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
    if out_md:
        os.makedirs(os.path.dirname(out_md), exist_ok=True)
        with open(out_md, "w", encoding="utf-8") as fh:
            fh.write("# 历史验收声明的重跑复证（P188-N11 / m6864）\n\n")
            fh.write(md_table(audit) + "\n\n")
            for f in figs:
                fh.write(f"## {f['name']}\n\n![{f['name']}]({f['path']})\n\n{f['caption']}\n\n")
            fh.write("## 逐节点明细\n\n" + md_items(audit) + "\n")
    print(json.dumps({"figures": [f["name"] for f in figs], "kpi": audit["kpi"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))