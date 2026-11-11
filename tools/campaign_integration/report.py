"""论文级六要素报告渲染 + 图（数字全部由机器产物注入，零手抄）。

六要素（owner 2026-10-29 发表级契约）：① 案例介绍 ② 怎么做的 ③ 计算结果 ④ 结果图
⑤ 注意点 ⑥ 误差判定；六要素自检复用 :func:`tools.maturity_gate.report.check_six_elements`
（同一套判据，免得「同一份报告两种判定」）。

图标签一律 ASCII —— 容器内 matplotlib 缺 CJK 字体，中文会渲染成方框（歷次踩过的坑）。
本模块不含任何领域词条：节点名/判据文字/接口名全部来自工件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"
_COLORS = {PASS: "#2e9e5b", FAIL: "#d64545", WITHHELD: "#c9c9c9"}


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        if x == 0:
            return "0"
        if abs(x) < 1e-4 or abs(x) >= 1e5:
            return f"{x:.3e}"
        return f"{x:.6g}"
    return str(x)


# ---------------------------------------------------------------- 图
def build_figures(out_dir: str | Path, ctx: Mapping[str, Any]) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    figures: List[Dict[str, str]] = []
    gate = ctx["gate"]
    summaries = gate["node_summaries"]
    keys = [s["node_key"] for s in summaries]
    short = [k.split("-")[-1] if "-" in k else k for k in keys]
    idx = list(range(len(keys)))

    # fig1: 每节点判据三态 + artifact 数（右侧注记）
    fig, ax = plt.subplots(figsize=(10, 4.6))
    passed = [s["passed"] for s in summaries]
    failed = [s["failed"] for s in summaries]
    withheld = [s["withheld"] for s in summaries]
    ax.bar(idx, passed, color=_COLORS[PASS], label="PASS")
    ax.bar(idx, failed, bottom=passed, color=_COLORS[FAIL], label="FAIL")
    ax.bar(idx, withheld, bottom=[p + f for p, f in zip(passed, failed)],
           color=_COLORS[WITHHELD], label="WITHHELD")
    for i, s in enumerate(summaries):
        art = (ctx["presence"].get(s["node_key"], {}).get("delivery") or {}).get(
            "artifact_count", 0)
        ax.text(i, -0.55, f"art={art}", ha="center", fontsize=7, color="#444444")
    ax.set_xticks(idx)
    ax.set_xticklabels(short, rotation=0, fontsize=8)
    ax.set_ylim(-0.9, max(3.2, max(passed + failed + withheld or [3]) + 0.6))
    ax.set_yticks(range(0, 4))
    ax.set_ylabel("criteria count")
    ax.set_title("Per-node criteria verdicts (grey = withheld: no reproducible evidence)")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    p1 = out / "fig1_node_evidence.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig1_node_evidence.png", "path": str(p1),
                    "caption": "每节点三态判据数（蓝=PASS，红=FAIL，灰=WITHHELD）+ 该节点实测 "
                               "artifact 数（art=）。灰柱意味『无从判断』，不是『不过』。"})

    # fig2: 交付判据 a–f 看板
    crit = ctx["delivery"]
    names = [k for k in sorted(crit)]
    fig, ax = plt.subplots(figsize=(9.6, 3.0))
    colors = [_COLORS[crit[k]["status"]] for k in names]
    ax.barh([f"{k} {crit[k]['name'][:34]}" for k in names], [1 for _ in names], color=colors)
    for i, k in enumerate(names):
        ax.text(0.01, i, crit[k]["detail"][:110], va="center", fontsize=7, color="white")
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.set_title("Campaign delivery criteria (green=pass, red=fail, grey=withheld)")
    fig.tight_layout()
    p2 = out / "fig2_delivery_criteria.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig2_delivery_criteria.png", "path": str(p2),
                    "caption": "战役交付判据 a–f 判决看板；条内文字为程序化注入的实测/阈值。"})

    # fig3: 契约检查看板
    checks = ctx["contracts"]["checks"]
    fig, ax = plt.subplots(figsize=(9.6, 3.2))
    labels = [f"{c['id']}" for c in checks]
    colors = [_COLORS[c["status"]] for c in checks]
    ax.barh(labels, [max(1, c["checks"]) for c in checks], color=colors)
    for i, c in enumerate(checks):
        txt = (f"{c['kind']}  checks={c['checks']}  violations={len(c['violations'])}"
               + (f"  ({c['reason']})" if c.get("reason") else ""))
        ax.text(0.05, i, txt, va="center", fontsize=7, color="white")
    ax.set_xlabel("declared checks")
    ax.set_title("Interface / registry contracts (grey = withheld, anti-vacuity)")
    fig.tight_layout()
    p3 = out / "fig3_contracts.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig3_contracts.png", "path": str(p3),
                    "caption": "契约逐条：注册表自洽（当期可判）与跨节点接口契约（缺证据即灰）。"
                               "『0 违反』只在真被检查过时才算通过。"})
    return figures


# ---------------------------------------------------------------- 报告
def render(ctx: Mapping[str, Any]) -> str:
    gate = ctx["gate"]
    camp = gate["campaign"]
    plan = ctx["plan"]
    summary = ctx["presence_summary"]
    contracts = ctx["contracts"]
    maturity = ctx["maturity"]
    delivery = ctx["delivery"]
    lines: List[str] = []
    add = lines.append

    add(f"# 战役集成与验收门（{gate['spec_id']}）")
    add("")
    add(f"- `gate_schema_version`: `{gate['gate_schema_version']}`")
    add(f"- `input_digest`: `{gate['input_digest']}`（证据规格 + 全部被读证据文档 sha256 的 sha256）")
    add(f"- `snapshot_digest`: `{ctx['provenance']['snapshot_digest']}`"
        f"（注册表快照，fetched_at={ctx['snapshot'].get('fetched_at')}）")
    add(f"- `sources_config_digest`: `{ctx['provenance']['sources_config_digest']}`")
    add(f"- 战役计划：`{plan.get('name')}`（plan_id={plan.get('plan_id')}，"
        f"状态 {plan.get('status')}）")
    add(f"- **两层裁定**：证据层（物理判据可复算性）= **{camp['verdict']}**；"
        f"交付层（战役验收门 a–f）= **{ctx['delivery_verdict']}**")
    add("")
    add("## ① 案例介绍")
    add("")
    add("本节点的物理目标是把上游 N01–N09 **各自验证过**的多域成果集成成一份战役级交付，"
        "并在战役验收门上逐项裁决。难点不在算一个数，而在「凭什么相信上游的数」："
        "报告里的形容词、绿色的测试、并进去的 PR 都不能直接当证据。故本文的案例对象是"
        "**集成链本身**——它的输入、它的证据门、它的裁定与它的成熟化单：")
    add("")
    add("| 环节 | 输入 | 输出 | 机器判据 |")
    add("|---|---|---|---|")
    add("| 注册表 | 战役 DAG 快照（API → 落盘） | 节点×mission×验收清单 | 快照 sha256 入审计链 |")
    add("| 收集 | 节点工作树规范交付位 + 报告索引 | presence 文档（实测交付态） | 缺就写缺，不补值 |")
    add("| 证据层裁定 | 证据文档 + 证据规格 | PASS/FAIL/WITHHELD 三态 | 无证据不 PASS |")
    add("| 契约层 | 跨节点接口契约 + 注册表自洽 | 0 违反 / 违反明细 / 未检 | 任一侧无证据即 WITHHELD |")
    add("| 成熟化 | 判定体 + presence | feature_ledger INSERT + rollback | 四道准入才 REAL |")
    add("")
    add("参与本战役的上游节点（全部数字来自快照与 presence 文档，非手抄）：")
    add("")
    add("| 节点 | 仓库 | mission | 任务状态 | 实测 artifact | 报告 | 证据等级 | 判据通过率 |")
    add("|---|---|---|---|---|---|---|---|")
    levels = {e["entry_id"]: e["evidence_level"] for e in maturity["entries"]}
    for s in gate["node_summaries"]:
        p = ctx["presence"].get(s["node_key"], {})
        add(f"| `{s['node_key']}` | {s['repo']} | {s['mission_id']} | "
            f"{(p.get('mission') or {}).get('status')} | "
            f"{(p.get('delivery') or {}).get('artifact_count')} | "
            f"{(p.get('reports') or {}).get('count')} | {levels.get(s['node_key'])} | "
            f"{_fmt(s['pass_ratio'])} |")
    add("")
    add(f"战役规模：节点 {summary['nodes_total']} 个，实测交付 "
        f"{summary['nodes_delivered']} 个（覆盖率 {_fmt(summary['artifact_coverage'])}），"
        f"artifact 总数 {summary['artifact_total']}（其中可解析 JSON "
        f"{summary.get('artifact_with_json', 0)} 份），报告条目 {summary['report_total']}；"
        f"任务状态分布 {summary['mission_status_counts']}，其中基础设施阻塞 "
        f"{len(summary['infra_blocked_keys'])} 个。")
    add("")
    add(f"**证据协议符合性**：{len(summary.get('protocol_conformant_keys', []))}/"
        f"{summary['nodes_total']} 个节点交付了协议要求的证据文档"
        f"（{summary.get('protocol_conformant_keys') or '无'}）。"
        "不符合协议的 artifact 只能证明『有东西』，不能证明『数可复算』——"
        "故它们的判据仍停在 WITHHELD，这是刻意的不给分。")
    add("")
    add("## ② 怎么做的")
    add("")
    add(f"命令：`{ctx['run_command']}`。工具链：`tools/campaign_integration/`"
        "（sources → contracts → maturity → report 四模块）+ 复用 `tools/maturity_gate/`"
        "判据引擎（spec/evidence/gate/ledger/contracts/report）+ pytest。")
    add("")
    add("关键设置：")
    add("")
    add("1. **缺就写缺**：节点零 artifact 也照落 presence 文档（`delivery.delivered=false`），"
        "让「没交付」变成可复算事实；证据文档不存在则判据 WITHHELD——『在飞』绝不写成『通过』。")
    add("2. **判据是分母**：每节点 3 条判据（交付 / 测试 / 验收覆盖），通过率按判据算；"
        "少声明一条不会让通过率虚高。")
    add("3. **反空真**：跨节点接口契约任一侧无证据即 WITHHELD——「两边都没交付所以没矛盾」"
        "不算 0 违反；总体契约判定只在全部契约真被检查过时才可能 PASS。")
    add("4. **互裁**：证据文档可声明上游自述（`claim.*`）；自述为真而本引擎复算为假 → "
        "`claim_mismatch`，一票入账且不改判（造假嫌疑必须显形）。")
    add("5. **成熟化 fail-closed**：只有『有 artifact + 判据过绿线 + 无 FAIL + 无 claim_mismatch』"
        "四道全过才写 feature_ledger，且必附 evidence 路径与 sha256；WITHHELD 不写 INSERT。")
    add("6. **零领域语义**：本包代码内无学科词条；节点名/仓库/判据文字/接口名/单位全由"
        "`data/campaign_integration/*.json` 工件注入（域数据经配置注入，不进代码）。")
    add("")
    add("## ③ 计算结果")
    add("")
    add("### 3.1 证据层（判据可复算性）")
    add("")
    add("| 量 | 值 |")
    add("|---|---|")
    for k in ("nodes_total", "nodes_delivered", "nodes_absent", "nodes_green", "nodes_red",
              "min_pass_ratio", "mean_pass_ratio", "criteria_pass_threshold",
              "claim_mismatch_total", "verdict"):
        add(f"| campaign.{k} | {_fmt(camp.get(k))} |")
    add("")
    add("### 3.2 交付层（战役验收判据 a–f）")
    add("")
    add("| 判据 | 判决 | 实测/阈值 |")
    add("|---|---|---|")
    for k in sorted(delivery):
        add(f"| {k} {delivery[k]['name']} | {delivery[k]['status']} | {delivery[k]['detail']} |")
    add("")
    add(f"交付层裁定：**{ctx['delivery_verdict']}**；"
        f"硬失败项 {ctx['delivery_hard_fail'] or '无'}。")
    add("")
    add("### 3.3 契约层")
    add("")
    add(f"契约总数 {contracts['contracts_total']}（接口契约 "
        f"{contracts['interface_contracts_total']} 条，其中真被检查 "
        f"{contracts['interface_contracts_checked']} 条），WITHHELD "
        f"{contracts['withheld_total']} 条，违反 {contracts['violations_total']} 条，"
        f"裁定 **{contracts['verdict']}**。")
    add("")
    add("| 契约 | 类型 | 接口 | 检查数 | 违反 | 状态 | 原因 |")
    add("|---|---|---|---|---|---|---|")
    for c in contracts["checks"]:
        add(f"| `{c['id']}` | {c['kind']} | {c.get('interface') or '—'} | {c['checks']} | "
            f"{len(c['violations'])} | {c['status']} | {c.get('reason') or '—'} |")
    add("")
    if contracts["violations_total"]:
        add("违反明细（逐条，含节点/规则/证据）：")
        add("")
        for c in contracts["checks"]:
            for v in c["violations"]:
                add(f"- `{c['id']}` → `{v.get('rule')}` → "
                    f"{ {k: val for k, val in v.items() if k != 'rule'} }")
        add("")
    add("### 3.4 成熟化单（feature_ledger）")
    add("")
    add(f"条目 {maturity['entries_total']}，REAL {maturity['real_total']}"
        f"（其中上游 {maturity.get('real_upstream_total', maturity['real_total'])}，"
        f"集成节点自身机制正控 {len(maturity.get('self_registered') or [])}），"
        f"deferred {maturity['deferred_total']}，等级分布 {maturity['levels']}。")
    real_entries = [e for e in maturity["entries"] if e["register"]]
    if real_entries:
        add("")
        add("REAL 条目（已生成 INSERT，证据路径逐条可溯源）：")
        add("")
        add("| 条目 | 等级 | 证据 |")
        add("|---|---|---|")
        for e in real_entries:
            ev = e["evidence"].get("artifacts") or []
            ev_txt = ", ".join(f"`{a.get('path')}`@{(a.get('sha256') or '')[:12]}" for a in ev)
            tests = e["evidence"].get("tests")
            if tests:
                ev_txt += f"；pytest rc={tests.get('returncode')} green={tests.get('green')}"
            add(f"| `{e['slug']}` | {e['evidence_level']} | {ev_txt} |")
    if maturity["deferred"]:
        add("")
        add("deferred 明细（不写 INSERT 的具名原因）：")
        add("")
        add("| 条目 | 原因 |")
        add("|---|---|")
        for d in maturity["deferred"]:
            add(f"| `{d['slug']}` | {', '.join(d['reasons'])} |")
    add("")
    add("## ④ 结果图")
    add("")
    for fig in ctx["figures"]:
        add(f"**{fig['name']}** — {fig['caption']}")
        add("")
    add("## ⑤ 注意点")
    add("")
    for i, item in enumerate(ctx["limitations"], 1):
        add(f"{i}. {item}")
    add("")
    add("## ⑥ 误差判定")
    add("")
    add(f"**是否<3%**：{ctx['error_headline']}")
    add("")
    add("| 量 | 实测 | 判据 | 余量口径 |")
    add("|---|---|---|---|")
    for row in ctx["error_rows"]:
        add(f"| {row['quantity']} | {_fmt(row['measured'])} | {row['predicate']} | "
            f"{row['note']} |")
    add("")
    add("## 审计链")
    add("")
    add(f"- 证据规格：`{ctx['spec_path']}`（由注册表快照程序化生成，零手抄）")
    add(f"- 判定体：`{ctx['out_dir']}/gate.json`（input_digest 见上）")
    add(f"- 契约明细：`{ctx['out_dir']}/contracts.json`；成熟化单："
        f"`{ctx['out_dir']}/maturity_plan.json`")
    add(f"- SQL：`{ctx['out_dir']}/feature_ledger.sql` + "
        f"`{ctx['out_dir']}/feature_ledger_rollback.sql`（回滚覆盖校验 "
        f"{ctx['rollback_covers']}）")
    add("- presence 文档（逐节点 sha256）：")
    for key in sorted(ctx["provenance"]["presence_digests"]):
        add(f"  - `{key}` → `{ctx['provenance']['presence_digests'][key]}`")
    add("")
    return "\n".join(lines)