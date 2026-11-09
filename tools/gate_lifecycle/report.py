"""Report + figure rendering for the gate-lifecycle study (matplotlib only).

Figures (platform-mechanism task, so no physical field plots are expected):

fig1  schedule comparison -- baseline dispatch offsets (measured) vs the
      automated critical-path list schedule;
fig2  gate outcome distribution -- verdicts, statuses, honest-FAIL protection
      and the failure rate with / without the protection;
fig3  critical path and total slack of the automated schedule;
fig4  maturity comparison -- time (replay and workload-equalised) and manual
      interventions per node, with the C5 target lines drawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _save(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def fig_schedule_gantt(sched: Mapping[str, Any], baseline_nodes: Sequence[Mapping[str, Any]],
                       out: Path) -> str:
    entries = sched["entries"]
    auto_ids = [e["node_id"] for e in entries]
    fig, axes = plt.subplots(1, 2, figsize=(13, 0.5 * max(len(auto_ids), 8) + 2), sharey=False)

    ax = axes[0]
    for i, rec in enumerate(baseline_nodes):
        ax.barh(i, rec["duration_s"], left=rec["offset_s"], height=0.6,
                color="#b0b0b0" if rec.get("status") == "done" else "#e08080",
                edgecolor="k", linewidth=0.4)
    ax.set_yticks(range(len(baseline_nodes)))
    ax.set_yticklabels([f"n{i + 1:02d}" for i in range(len(baseline_nodes))])
    ax.set_xlabel("time since first dispatch [s]")
    ax.set_title("(a) baseline plan 165: measured dispatch (no schedule)")
    ax.grid(axis="x", alpha=0.3)

    ax = axes[1]
    for i, e in enumerate(entries):
        ax.barh(i, e["duration"], left=e["start"], height=0.6,
                color=f"C{e['lane'] % 10}", edgecolor="k", linewidth=0.4)
    ax.set_yticks(range(len(entries)))
    ax.set_yticklabels(auto_ids)
    cp = set(sched.get("critical_path", []))
    for i, e in enumerate(entries):
        if e["node_id"] in cp:
            ax.barh(i, e["duration"], left=e["start"], height=0.6, fill=False,
                    edgecolor="crimson", linewidth=2.0)
    ax.set_xlabel("time [s]")
    ax.set_title(f"(b) automated CPM list schedule (k={sched['capacity']}, "
                 f"makespan={sched['makespan']:.0f}s, red=critical)")
    ax.grid(axis="x", alpha=0.3)
    return _save(fig, out)


def fig_gate_outcomes(payload: Mapping[str, Any], out: Path) -> str:
    verdicts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for r in payload["node_results"]:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    ax = axes[0]
    keys = sorted(verdicts)
    ax.bar(keys, [verdicts[k] for k in keys], color=["#5aa469" if k == "PASS" else
                                                     "#c0504d" if k == "FAIL" else "#e0a24a"
                                                     for k in keys], edgecolor="k")
    ax.set_title("(a) automated verdicts")
    ax.set_ylabel("nodes")

    ax = axes[1]
    keys = sorted(statuses)
    ax.barh(keys, [statuses[k] for k in keys], color="#7aa6c2", edgecolor="k")
    ax.set_title("(b) node status (honest_fail = S-N track)")
    ax.set_xlabel("nodes")

    ax = axes[2]
    gaps = payload["gaps"]["counts"]
    keys = sorted(gaps)
    ax.barh(keys, [gaps[k] for k in keys], color="#9a7fb8", edgecolor="k")
    ax.set_title("(c) gap_analysis findings -> proposals")
    ax.set_xlabel("count")
    return _save(fig, out)


def fig_critical_path(payload: Mapping[str, Any], out: Path) -> str:
    inv = payload["invariants"]
    ids = sorted(inv["slacks"])
    slack = [inv["slacks"][i] for i in ids]
    cp = set(inv["critical_path"])
    fig, ax = plt.subplots(figsize=(9, 0.35 * len(ids) + 1.8))
    ax.barh(ids, slack, color=["#c0504d" if i in cp else "#8fa8bf" for i in ids],
            edgecolor="k")
    ax.set_xlabel("total slack [s] (0 = on the critical path)")
    ax.set_title(f"critical path length {inv['critical_length']:.0f}s, "
                 f"makespan {inv['makespan']:.0f}s, peak parallelism "
                 f"{inv['max_parallelism']}")
    ax.grid(axis="x", alpha=0.3)
    return _save(fig, out)


def fig_maturity(cmp: Mapping[str, Any], out: Path) -> str:
    c = cmp["comparison"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    ax = axes[0]
    labels = ["replay\n(deps enforced)", "workload-equalised\n(serial model)"]
    base = [c["time_replay"]["baseline_s"], c["time_serial"]["baseline_s"]]
    auto = [c["time_replay"]["automated_s"], c["time_serial"]["automated_s"]]
    x = range(2)
    ax.bar([i - 0.18 for i in x], base, width=0.36, label="baseline", color="#b0b0b0",
           edgecolor="k")
    ax.bar([i + 0.18 for i in x], auto, width=0.36, label="automated", color="#5aa469",
           edgecolor="k")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("end-to-end time [s]")
    ax.set_title("(a) time: both comparisons reported")
    for i, (b, a) in enumerate(zip(base, auto)):
        red = (b - a) / b
        ax.text(i, max(b, a) * 1.02, f"{red * 100:+.1f}%", ha="center", fontsize=9)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    iv = c["interventions"]
    ax.bar(["baseline", "automated"],
           [iv["baseline_per_node"], iv["automated_per_node"]],
           color=["#b0b0b0", "#5aa469"], edgecolor="k")
    ax.axhline(iv["target"], color="crimson", ls="--",
               label=f"target reduction {iv['target']:.0%} -> <= "
                     f"{iv['baseline_per_node'] * (1 - iv['target']):.2f}/node")
    ax.set_ylabel("manual interventions per node")
    ax.set_title(f"(b) interventions: {iv['reduction'] * 100:.1f}% reduction")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, out)


def render_markdown(report: Mapping[str, Any]) -> str:
    c = report["criteria"]
    lines: list[str] = []
    add = lines.append
    add(f"# {report['title']}")
    add("")
    add(report["abstract"])
    add("")
    add("## 1. 案例介绍（背景/对象/参数）")
    add("")
    add(report["intro"])
    add("")
    add("## 2. 方法（流程/关键设置/工具链）")
    add("")
    for step in report["method"]:
        add(f"- {step}")
    add("")
    add("## 3. 计算结果（判据数字，程序化注入）")
    add("")
    add("| 判据 | 指标 | 实测 | 阈值 | 结论 | 产物 |")
    add("|---|---|---|---|---|---|")
    for k in sorted(c):
        item = c[k]
        add(f"| {k} | {item['metric']} | {item['value']} | {item['target']} | "
            f"{item['status']} | `{item['artifact']}` |")
    add("")
    add("### 3.1 调度层")
    add("")
    sch = report["schedule"]
    add(f"- 计划 {report['plan']['plan_id']}：{len(report['plan']['nodes'])} 节点，"
        f"容量 k={sch['capacity']}，峰值并行 {report['invariants']['max_parallelism']}，"
        f"makespan {sch['makespan']:.0f}s")
    add(f"- 关键路径：{' → '.join(sch['critical_path'])}（长度 "
        f"{sch['critical_length']:.0f}s）")
    add(f"- 依赖波次：{report['invariants']['waves']}")
    add(f"- 无环检测 + 关键路径单次耗时 {report['timing']['dag_ms']:.3f} ms"
        f"（阈值 1000 ms）")
    add("")
    add("### 3.2 验收门与能力台账")
    add("")
    acc = report["accuracy"]
    add(f"- 人工标注金标集 {acc['cases']} 例，自动裁决一致 {acc['agree']} 例，"
        f"准确率 {acc['accuracy'] * 100:.2f}%（阈值 95%）")
    add(f"- 混淆矩阵：`{acc['confusion']}`")
    add(f"- 能力台账登记完整率 {report['completeness']['completeness'] * 100:.1f}%"
        f"（PASS 节点 {report['completeness']['passed_nodes']}，缺失 "
        f"{report['completeness']['missing']}）")
    add(f"- S 级分布：{report['ledger']['by_grade']}；类别分布："
        f"{report['ledger']['by_category']}")
    add("")
    add("### 3.3 失败保护与自回归")
    add("")
    fa = report["failures"]
    add(f"- 失败率（honest-FAIL 剔除）= {fa['failed']}/{fa['denominator']} = "
        f"{fa['failure_rate']:.4f}；S-N 轨节点 {fa['negative_track']}")
    add(f"- gap_analysis 结论：{report['gaps']['counts']}")
    add(f"- 提案注入：新生成 {report['proposals']['generated']} 条，去重 "
        f"{report['proposals']['deduped']} 条，池内 {report['injection']['pool_size']} 条")
    add(f"- 下一波播种：{report['seeded_plan']['plan_id']} 共 "
        f"{len(report['seeded_plan']['nodes'])} 节点，来源提案 "
        f"{report['seeded_plan']['meta']['seeded_from']}")
    add(f"- 闭环验证：播种波次重跑后 PASS "
        f"{report['wave_b']['passed']} 节点、台账新增 {report['wave_b']['appended']} 条")
    add("")
    add("### 3.4 全链路幂等")
    add("")
    add(f"- 两次运行的逐节点结果摘要 {report['idempotency']['digest_a']} vs "
        f"{report['idempotency']['digest_b']}："
        f"{'一致' if report['idempotency']['equal'] else '不一致'}")
    add(f"- 台账去重：第二次 register 追加 {report['idempotency']['ledger_appended_second']} 条，"
        f"判重 {report['idempotency']['ledger_duplicates_second']} 条")
    add("")
    add("## 4. 结果图")
    add("")
    for fig in report["figures"]:
        add(f"![{fig['name']}]({fig['path']})")
        add("")
        add(f"*{fig['caption']}*")
        add("")
    add("## 5. 注意点（坑/局限/适用范围）")
    add("")
    for note in report["notes"]:
        add(f"- {note}")
    add("")
    add("## 6. 误差判定")
    add("")
    add(report["error_verdict"])
    add("")
    add("### 审计链")
    add("")
    for item in report["audit"]:
        add(f"- {item}")
    add("")
    return "\n".join(lines)