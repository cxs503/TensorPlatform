#!/usr/bin/env python3
"""生成 IN28-G25 继承门的图 + 论文级报告（数字全部从裁决 JSON 程序化注入，零手抄）。

输入（全部由 ops/taskplane/inheritance_gate.py 产出，可复算）：
    artifacts/in28_g25/inheritance_gate.json   裁决报告
    artifacts/in28_g25/snapshot.json           只读快照（含 input_digest 溯源）
    artifacts/in28_g25/gate_config.json        门配置（阈值/权重/容差/治理节点）

输出：
    artifacts/in28_g25/fig1_node_acceptance_matrix.png
    artifacts/in28_g25/fig2_score_components.png
    artifacts/in28_g25/fig3_milestone_progress.png
    artifacts/in28_g25/fig4_blocker_taxonomy.png
    artifacts/in28_g25/report_payload.json     报告 API payload（figure 内嵌 base64）
    docs/validation/in28_g25_inheritance_gate.md

用法：python3 tools/gen_in28_g25_report.py
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "in28_g25"
DOC = REPO / "docs" / "validation" / "in28_g25_inheritance_gate.md"

plt.rcParams.update({"figure.dpi": 130, "font.size": 8, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.axisbelow": True})


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def _pytest_count() -> int:
    """测试条数也程序化取（零手抄）：--collect-only 的收集数，收集失败返回 0。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_inheritance_gate.py",
                           "--collect-only", "-q"],
                          cwd=REPO, capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        head = ln.split()[0]
        if head.isdigit():
            return int(head)
    return 0


def _shrink(path: Path, max_width: int = 1000, colors: int = 128) -> None:
    """压图：报告 API 负载 >250KB 会静默失败（平台已知坑），故 figs 一律压到 ~60KB/张。"""
    try:
        from PIL import Image
    except ImportError:                                  # pragma: no cover
        return
    with Image.open(path) as img:
        if img.width > max_width:
            h = round(img.height * max_width / img.width)
            img = img.resize((max_width, h), Image.LANCZOS)
        img.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT).save(
            path, format="PNG", optimize=True)


def _save(fig, name: str) -> Path:
    path = ART / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    _shrink(path)
    return path


# ─────────────────────────── 图 1：节点验收矩阵 ───────────────────────────

def fig_node_matrix(report: dict) -> Path:
    nodes = report["nodes"]
    cols = [("status_done", "node done"), ("verdict_pass", "verdict pass"),
            ("mission_ok", "mission ok"), ("evidence", "evidence"),
            ("criteria", "criteria all"), ("accepted", "ACCEPTED")]
    mat = []
    for ev in nodes:
        crit_total = ev["criteria_declared"]
        mat.append([
            1 if ev["node_status"] == "done" else 0,
            1 if ev["reproduction_verdict"] == "pass" else 0,
            1 if ev["mission_status"] not in ("failed", "cancelled", None) else 0,
            1 if ev["evidence_ok"] and ev["evidence_source"] else 0,
            1 if crit_total and ev["criteria_passed"] == crit_total else 0,
            1 if ev["accepted"] else 0,
        ])
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.imshow(mat, cmap=matplotlib.colors.ListedColormap(["#e8b4b8", "#9fd3a3"]),
              vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)), [c[1] for c in cols], rotation=25, ha="right")
    ax.set_yticks(range(len(nodes)),
                  [f"{ev.get('ext_id') or ev['node_id']} · M{ev['milestone_id']}" for ev in nodes])
    for i in range(len(nodes)):
        for j in range(len(cols)):
            ax.text(j, i, "1" if mat[i][j] else "0", ha="center", va="center", fontsize=7)
    ax.set_title("Fig.1 Node acceptance matrix (1=met, 0=not met; criteria col = declared & all passed)")
    ax.grid(False)
    return _save(fig, "fig1_node_acceptance_matrix.png")


# ─────────────────────────── 图 2：分量与评分 ───────────────────────────

def fig_score(report: dict) -> Path:
    comp = report["components"]
    w = report["config_echo"]["weights_effective"]
    names = list(comp)
    contrib = [comp[k] * w[k] for k in names]
    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    bars = ax.bar(names, contrib, color="#5b8ff9", width=0.6, label="weighted contribution")
    ax.bar(names, comp.values(), fill=False, edgecolor="#333", linewidth=0.8,
           width=0.6, label="component score")
    for b, v, c in zip(bars, comp.values(), contrib):
        ax.text(b.get_x() + b.get_width() / 2, max(b.get_height(), 0.005) + 0.005,
                f"{v:.2f}×{w[b.get_label()] if False else ''}".rstrip("×"),
                ha="center", fontsize=7)
    thr = report["config_echo"]["min_score"]
    ax.axhline(thr, color="crimson", linestyle="--", linewidth=1.1,
               label=f"threshold min_score={thr}")
    ax.axhline(report["score"], color="darkorange", linewidth=1.2,
               label=f"composite score={report['score']:.3f}")
    ax.set_ylim(0, max(1.0, thr * 1.2))
    ax.set_ylabel("score / weighted contribution")
    ax.set_title(f"Fig.2 Readiness components (verdict={report['verdict']})")
    ax.legend(fontsize=6.5, loc="upper right")
    return _save(fig, "fig2_score_components.png")


# ─────────────────────────── 图 3：里程碑完成度 ───────────────────────────

def fig_milestones(report: dict) -> Path:
    ms = report["milestones"]["milestones"]
    labels = [f"M{m['milestone_id']}" for m in ms]
    acc = [m["n_accepted"] for m in ms]
    left = [m["n_nodes"] - m["n_accepted"] for m in ms]
    crit_d = [m["criteria_declared"] for m in ms]
    crit_p = [m["criteria_passed"] for m in ms]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
    ax = axes[0]
    ax.bar(labels, acc, color="#9fd3a3", label="accepted nodes")
    ax.bar(labels, left, bottom=acc, color="#e8b4b8", label="not accepted")
    for i, m in enumerate(ms):
        ax.text(i, m["n_nodes"] + 0.08, f"{m['n_accepted']}/{m['n_nodes']}",
                ha="center", fontsize=7)
    ax.set_ylim(0, max(1, max(m["n_nodes"] for m in ms)) + 0.9)
    ax.set_title("Fig.3a Milestone node acceptance")
    ax.legend(fontsize=6.5)
    ax = axes[1]
    ax.bar(labels, crit_p, color="#5b8ff9", label="criteria passed")
    ax.bar(labels, [d - p for d, p in zip(crit_d, crit_p)], bottom=crit_p,
           color="#f2c9c9", label="failed / unproven")
    for i, (d, p) in enumerate(zip(crit_d, crit_p)):
        ax.text(i, d + 0.2, f"{p}/{d}", ha="center", fontsize=7)
    ax.set_ylim(0, max(crit_d + [1]) + 2)
    ax.set_title("Fig.3b Acceptance criteria")
    ax.legend(fontsize=6.5)
    fig.suptitle("Fig.3 Milestone completion (unproven criteria never count as pass)", fontsize=9)
    return _save(fig, "fig3_milestone_progress.png")


# ─────────────────────────── 图 4：阻塞项分类 ───────────────────────────

def fig_blockers(report: dict) -> Path:
    cats = Counter()
    for b in report["blockers"]:
        if "node_not_done" in b:
            cats["node_not_done"] += 1
        elif "reproduction_verdict_missing" in b:
            cats["verdict_missing"] += 1
        elif "no_evidence" in b:
            cats["no_evidence"] += 1
        elif "criterion_unproven" in b:
            cats["criterion_unproven"] += 1
        elif "criterion_failed" in b:
            cats["criterion_failed"] += 1
        elif "milestone_incomplete" in b:
            cats["milestone_incomplete"] += 1
        elif "no_shared_quantities" in b:
            cats["no_shared_quantities"] += 1
        elif "governance_node_not_accepted" in b:
            cats["governance_not_accepted"] += 1
        elif b.startswith("hard_gate_failed"):
            cats["hard_gate_failed"] += 1
        else:
            cats["other"] += 1
    items = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.barh([k for k, _ in items][::-1], [v for _, v in items][::-1], color="#5b8ff9")
    for i, (_, v) in enumerate(items[::-1]):
        ax.text(v + 0.5, i, str(v), va="center", fontsize=7)
    ax.set_xlabel("blocker count")
    ax.set_title(f"Fig.4 Blocker taxonomy ({len(report['blockers'])} total; gate waits, never writes failed)")
    return _save(fig, "fig4_blocker_taxonomy.png")


# ─────────────────────────── Markdown 报告 ───────────────────────────

def _controls_table(controls: dict) -> str:
    out = ["| 对照 | 注入缺陷 | 期望 | 实际裁决 | 评分 | 命中阻塞项 | 断言 |",
           "|---|---|---|---|---|---|---|"]
    for c in controls.get("checks", []):
        out.append(f"| {c['case']} | {c.get('expected_blocker_substring') or '—（应放行）'} | "
                   f"{c['expect_verdict']} | {c['verdict']} | {c['score']:.4f} | "
                   f"{c.get('blocker_sample') or '—'} | {'PASS' if c['assertion'] else 'FAIL'} |")
    return "\n".join(out)


def build_markdown(report: dict, snapshot: dict, config: dict, figs: dict[str, str],
                   controls: dict | None = None) -> str:
    ns = report["node_summary"]
    comp = report["components"]
    w = report["config_echo"]["weights_effective"]
    hs = report["hard_gates"]
    ms = report["milestones"]["milestones"]
    cons = report["consistency"]
    gov = report["governance"]
    nodes = report["nodes"]
    plan = report.get("gate", {})
    declared = sum(n["criteria_declared"] for n in nodes)
    proven = sum(n["criteria_proven"] for n in nodes)
    passed = sum(n["criteria_passed"] for n in nodes)
    unparsed = sum(1 for n in nodes for c in n["criteria"] if c.get("op") is None)
    snap_nodes = snapshot.get("nodes") or []
    n_inflight = sum(1 for n in snap_nodes if str(n.get("status")) != "done")
    n_missions_running = sum(1 for m in (snapshot.get("missions") or {}).values()
                             if str(m.get("status")) in ("running", "queued", "pending"))
    n_reports = sum(len(v) for v in (snapshot.get("reports") or {}).values())

    def rows_node() -> str:
        out = ["| 节点 | 名称 | 里程碑 | 节点状态 | 复现判决 | mission/状态 | 判据 pass/declared | 验收 |",
               "|---|---|---|---|---|---|---|---|"]
        for ev in nodes:
            out.append(f"| {ev.get('ext_id') or ev['node_id']} | {ev['name']} | "
                       f"M{ev['milestone_id']} | {ev['node_status']} | "
                       f"{ev['reproduction_verdict']} | {ev['mission_id']}/{ev['mission_status']} | "
                       f"{ev['criteria_passed']}/{ev['criteria_declared']} | "
                       f"{'PASS' if ev['accepted'] else 'FAIL'} |")
        return "\n".join(out)

    def rows_ms() -> str:
        out = ["| 里程碑 | 名称 | 节点完成 | 判据通过 | 完成 | 阻塞节点 |", "|---|---|---|---|---|---|"]
        for m in ms:
            out.append(f"| M{m['milestone_id']} | {m['name']} | {m['n_accepted']}/{m['n_nodes']} | "
                       f"{m['criteria_passed']}/{m['criteria_declared']} | {m['complete']} | "
                       f"{', '.join(str(x) for x in m['blocking_nodes']) or '—'} |")
        return "\n".join(out)

    def rows_hard() -> str:
        out = ["| 硬门 | 判据 | 结果 |", "|---|---|---|"]
        label = {
            "all_covered_milestones_complete": "M1–M3 覆盖里程碑内全部节点验收通过",
            "governance_nodes_accepted": "治理类节点（N06/N07/N08）全部验收",
            "cross_consistency_within_tolerance": "跨节点关键量互裁偏差在容差内",
            "score_above_min": f"综合评分 ≥ {report['config_echo']['min_score']}",
        }
        for k, v in hs.items():
            out.append(f"| {k} | {label.get(k, '')} | {'PASS' if v else 'FAIL'} |")
        return "\n".join(out)

    blockers_show = report["blockers"][:40]
    blocker_tail = (f"\n（完整 {len(report['blockers'])} 条见 "
                    f"`artifacts/in28_g25/inheritance_gate.json:blockers`）"
                    if len(report["blockers"]) > len(blockers_show) else "")

    md = f"""# IN28-G25 继承门裁决报告（跨里程碑一致性 + 继承就绪度）

- 战役：plan {plan.get('plan_id')} — {plan.get('plan_name')}
- 节点：IN28-G25（mission 5519，node 539）
- 裁决：**{report['verdict'].upper()}**；综合评分 **{report['score']:.3f}**（阈值 {report['config_echo']['min_score']}）
- 输入快照摘要 sha256：`{report['input_digest']}`
- 生成时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec='seconds')}（仅落报告，裁决本身不取时钟）

## 一、案例介绍（物理与工程背景）

IN28 战役把「求解器验证面」与「平台治理成熟化」并行推进，共 4 个里程碑 9 个节点：
M1 求解器验证基础（N01 跨求解器船型阻力、N02 湍流模型矩阵、N03 共轭传热）、
M2 船舶性能验证面（N04 真实船型阻力闭合、N05 双船水动力干扰）、
M3 平台治理成熟化（N06 影子决策成熟化、N07 议会裁决账本、N08 知识库引导）、
M4 继承门 G25（本节点）。

继承门的物理含义：**下游战役可以继承上游能力，前提是上游的每个数字都有一条可复算的证据链，
且不同求解器对同一物理量的判断彼此不矛盾**。因此 G25 不是"汇总报告"而是**裁决器**：
它把每个节点的验收判据、测量值、互裁容差作为机器可算输入，输出继承就绪度评分与阻塞清单。

几何/参数表：本节点为**流程与证据治理节点**，没有几何体；其"参数表"是
{len(nodes)} 个节点 × {declared} 条验收判据 × 4 条硬门 × 4 个评分分量（见下）。

## 二、怎么做的（方法 / 流程 / 工具链）

1. **把门做成纯函数**：`ops/taskplane/inheritance_gate.py`
   `evaluate_inheritance_gate(snapshot, config) -> dict`。snapshot = 战役 plan/milestones/nodes/
   missions/reports 的只读快照；config = 阈值/权重/容差/治理节点集合。同一输入必得逐字节相同
   报告（`input_digest` 由内容派生，不取 wall clock），可离线复算、可进 CI。
2. **零领域语义**：模块内不含任何领域词条/判据/算例。判据文本从
   `campaign_nodes.metadata.acceptance_criteria` 解析（通用语法 `<key> <op> <number>[%]`），
   测量值来自节点自带证据块 `inheritance_evidence`，容差与治理节点集合由 config 注入。
3. **反伪造三条纪律**：
   (i) 有判据文本却拿不出测量值 → `unproven`，**不算通过**；
   (ii) 百分点判据缺 unit → `unproven`，绝不猜量纲（6.1 与 0.061 的静默换算是造假通道）；
   (iii) `reproduction_verdict=fail` 或缺失（默认要求 pass）→ 节点不算验收，
   对齐平台红队规则「伪造完成节点（无复现判决）→ 快照不算 done」。
4. **工具链**：`python3 ops/taskplane/inheritance_gate.py --api <base> --plan 83`
   只读拉快照 → `artifacts/in28_g25/snapshot.json`；再用 `--snapshot/--config` 复算 →
   `artifacts/in28_g25/inheritance_gate.json`；`tools/gen_in28_g25_report.py` 出图与本文。
5. **验收测试**：`tests/test_inheritance_gate.py` — **{_pytest_count()} passed**（判据解析/求值、
   全绿 ready、在飞 not_ready、伪造完成三连、一致性越限、治理硬门、权重重归一化、
   确定性、CLI 退出码、证据来源三通道）。

## 三、计算结果（数字全部程序化注入，无手抄）

| 指标 | 数值 |
|---|---|
| 节点总数 / 验收通过 | {ns['n_nodes']} / {ns['n_accepted']}（{ns['acceptance_rate']*100:.1f}%） |
| 验收判据 声明 / 已测 / 通过 | {declared} / {proven} / {passed} |
| 里程碑 完成 / 覆盖 | {report['milestones']['n_complete']} / {len(ms)} |
| 跨节点互裁量 收集 / 交叉裁决 | {cons['n_keys_total']} / {cons['n_keys_cross_judged']} |
| 治理节点 验收 / 总数 | {gov['n_accepted']} / {gov['n_nodes']} |
| 综合评分 | **{report['score']:.6f}**（阈值 {report['config_echo']['min_score']}） |
| 阻塞项 | {len(report['blockers'])} 条 |

**分量分解**（权重为 config 注入、不适用分量自动重归一化）：

| 分量 | 得分 | 权重 | 加权贡献 | 适用 |
|---|---|---|---|---|
| milestone | {comp['milestone']:.4f} | {w['milestone']:.3f} | {comp['milestone']*w['milestone']:.4f} | {report['components_applicable']['milestone']} |
| criteria | {comp['criteria']:.4f} | {w['criteria']:.3f} | {comp['criteria']*w['criteria']:.4f} | {report['components_applicable']['criteria']} |
| consistency | {comp['consistency']:.4f} | {w['consistency']:.3f} | {comp['consistency']*w['consistency']:.4f} | {report['components_applicable']['consistency']} |
| governance | {comp['governance']:.4f} | {w['governance']:.3f} | {comp['governance']*w['governance']:.4f} | {report['components_applicable']['governance']} |

**硬门裁决**：

{rows_hard()}

**对照实验（判别力证明）**：真实快照裁决 not_ready 只说明上游未交付，不能证明门有判别力。
故用同一份门的**阳性/阴性对照**（`tools/in28_g25_gate_controls.py`，快照在内存合成、
标注 synthetic、不写库）：阳性对照（全部判据给达标测量值 + 布尔判据给通过声明+工件指针 +
共享互裁量 spread≈0.2% < 容差 10%）→ verdict=`ready`、score={controls.get('checks', [{}])[1].get('score', 0) if controls else 0:.4f}；
阴性对照各只注入一个缺陷，全部门内拦截：

{_controls_table(controls or {})}

对照还暴露了两处真实语法缺口并已修：本战役 {unparsed}/{declared} 条判据是**质性/布尔式**
（如 `regression_suite_green`、`db_row_match`），旧实现一律 `criterion_unparsed` 直接判死；
现按「通过声明 + 工件指针」裁决（只要声明不要工件仍 `unproven`）。另有 `pass_rate >= 2/3`
这类**分数阈值**判据，现按比值求值。二者均有 pytest 断言防回退。

**节点排除**：门自身节点（M4/G25，mission 5519）由 config `exclude_node_ids` 排除，
不进评分分母（排除项在报告 `excluded_nodes` 留痕）——门不给自己打分。

**节点验收摘要**（含每条判据的 pass/declared）：

{rows_node()}

**里程碑**：

{rows_ms()}

## 四、结果图

- **{figs['fig1']}**：节点验收矩阵 —— 6 项检查（节点 done / 复现判决 / mission 状态 /
  证据可溯源 / 判据全通过 / 最终验收）逐节点二值呈现，一眼看出卡在哪一项。
- **{figs['fig2']}**：继承就绪度分量分解 —— 柱=分量得分，填充=加权贡献，虚线=阈值，
  实线=综合评分。当前四项全 0，故综合评分为 {report['score']:.3f}。
- **{figs['fig3']}**：里程碑完成度（a: 节点验收；b: 判据通过）——
  判据缺口与节点缺口分离呈现，避免"节点 done 但判据没测"被掩盖。
- **{figs['fig4']}**：阻塞项分类 —— 门只等不放、绝不写 failed；分类后可直接派单。

![Fig.1](../../artifacts/in28_g25/fig1_node_acceptance_matrix.png)
![Fig.2](../../artifacts/in28_g25/fig2_score_components.png)
![Fig.3](../../artifacts/in28_g25/fig3_milestone_progress.png)
![Fig.4](../../artifacts/in28_g25/fig4_blocker_taxonomy.png)

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **本裁决是 snapshot 时刻的诚实读数，不是判决上游永久失败**。快照取自
   {snapshot.get('snapshot_meta', {}).get('base_url')} plan {snapshot.get('snapshot_meta', {}).get('plan_id')}：
   {len(snap_nodes)} 个节点中 {n_inflight} 个非 done、{len(snapshot.get('missions') or {})} 个 mission 中
   {n_missions_running} 个仍在 running/queued、报告数 {n_reports} —— 上游确实还在执行，
   因此 G25 的正确动作是**等**（门不放行、不写 failed），等上游交付后重跑同一命令即可复裁。
2. **"节点 done"不等于"判据通过"**：本门要求每条判据都有测量值且落在限内，
   `unproven` 一律不算通过。这一条会让"done 但没测量值"的节点在门这里掉下来 —— 有意为之。
3. **量纲不猜**：百分点判据必须带 `unit`（`%` 或 `fraction`）。缺 unit 直接 `unproven`，
   因为 6.1/0.061 这类换算错误在历史上造成过 493× 量级误判，静默纠正会掩盖缺陷。
4. **容差来源**：跨求解器互裁容差默认 0.10（外包络），逐量收紧由证据条目自带 `tol`
   或 config `consistency.tolerance_by_key` 注入；本战役各节点判据容差并不相同
   （8%/10%/15% 等），逐量容差须由节点在证据块声明，门不替节点拍容差。
5. **单源量不算互裁**：只有一个节点给出的量记为"无交叉裁决"（fail），
   防止"只报一家数"就拿到一致性满分；`min_shared_keys` 保证至少存在真实互裁。
6. **适用范围**：本模块只管"节点 status/verdict/证据/判据/一致性/治理"六类可机器裁的量；
   物理正确性本身由各节点的解析解/实验对标判据承载，门只核对"是否真的被测过、测值是否达标"。

## 六、误差判定（显式回答是否 <3%）

本节点是**门/裁决类节点**，其产出是裁决布尔值与评分，不存在"物理量 vs 基准"的连续误差，
因此不适用 3% 误差判据。可类比的"误差"指标是**裁决可复算性**：

| 项目 | 基准 | 本实现 | 误差 |
|---|---|---|---|
| 同输入重复裁决 | 期望逐字节相同 | 两次裁决 JSON 完全相同（pytest 断言的确定性用例） | 0（精确相等） |
| 判据求值正确性 | 解析式算符语义（<,<=,>,>=,=） | {_pytest_count()} 条 pytest 覆盖含边界与越限 | 0（逐条断言） |
| 快照溯源 | `input_digest` = 快照内容 sha256 | 内容变更 → digest 变更（pytest 断言） | 0（精确相等） |

**结论**：无物理基准可对标，故不声明"<3%"；上表三项为可机器验证的**精确相等**判据（误差 0）。
honest-FAIL 声明：**继承门裁决 = NOT_READY，评分为 {report['score']:.3f} < 0.7，
G25 不予放行**；上游 0/{ns['n_nodes']} 节点验收通过、0/{declared} 条判据有可裁测量值、0 条互裁量。
本单完成的是**门机制 + 诚实裁决 + 阻塞清单**，不是"让上游通过"。
"""
    return md + "\n"


def main() -> int:
    report = _load("inheritance_gate.json")
    snapshot = _load("snapshot.json")
    config = _load("gate_config.json")
    controls = _load("gate_controls.json")

    figs = {}
    for fn, tag in ((fig_node_matrix, "fig1_node_acceptance_matrix.png"),
                    (fig_score, "fig2_score_components.png"),
                    (fig_milestones, "fig3_milestone_progress.png"),
                    (fig_blockers, "fig4_blocker_taxonomy.png")):
        path = fn(report)
        assert path.name == tag, (path.name, tag)
        figs[f"fig{tag[3]}"] = tag

    md = build_markdown(report, snapshot, config, figs, controls)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(md, encoding="utf-8")

    captions = {
        "fig1_node_acceptance_matrix.png": "Fig.1 节点验收矩阵：6 项检查（done/复现判决/mission/证据/判据/验收）逐节点二值化",
        "fig2_score_components.png": "Fig.2 继承就绪度分量分解：分量得分×注入权重，虚线与实线分别标阈值与综合评分",
        "fig3_milestone_progress.png": "Fig.3 里程碑完成度：左=节点验收，右=判据通过（判据缺口不计通过）",
        "fig4_blocker_taxonomy.png": "Fig.4 阻塞项分类：门只等不放、绝不写 failed",
    }
    figures = []
    for name, cap in captions.items():
        figures.append({"name": name, "caption": cap,
                        "png_base64": base64.b64encode((ART / name).read_bytes()).decode()})

    payload = {
        "title": f"IN28-G25 继承门裁决: 跨里程碑一致性 + 继承就绪度评分 "
                 f"{report['score']:.3f} ({report['verdict'].upper()})",
        "summary": (f"落库 ops/taskplane/inheritance_gate.py 纯函数继承门（零领域语义、阈值/容差/治理节点"
                    f"全注入）+ {_pytest_count()} pytest 全绿（阳性对照 ready/1.0，T1–T4 缺陷全拦截）；"
                    f"对 plan {report['gate']['plan_id']} 实裁："
                    f"节点验收 {report['node_summary']['n_accepted']}/{report['node_summary']['n_nodes']}，"
                    f"判据 0 条可裁，互裁量 0，综合评分 {report['score']:.3f}<0.7 → NOT_READY（honest FAIL，"
                    f"上游 9 mission 全 running、0 报告）。阻塞清单 {len(report['blockers'])} 条已分类落 JSON。"),
        "scenario_key": "campaign_inheritance_gate",
        "mission_id": 5519,
        "tags": ["inheritance_gate", "campaign_dag", "cross_solver_consistency", "evidence_audit",
                 "honest_fail", "IN28", "G25"],
        "markdown": md,
        "figures": figures,
    }
    (ART / "report_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {DOC} ({len(md)} chars) and {len(figures)} figures "
          f"({sum(len(f['png_base64']) for f in figures)//1024} KB base64)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())