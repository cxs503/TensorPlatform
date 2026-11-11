"""H6 成熟度门 · 报告与图示（论文级六要素，数字全部程序化注入零手抄）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .core import LEVELS, LEVEL_DEFINITIONS, LEVEL_RANK

FIGSIZE = (7.2, 4.0)


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def fig_maturity_profile(gate: Mapping[str, Any], out: Path) -> Path:
    """图 1：五级成熟度分布——自述（基线）vs 证据对账（当前）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    before = (gate["maturity"]["before"] or {}).get("histogram", {})
    after = (gate["maturity"]["after"] or {}).get("histogram", {})
    xs = list(range(len(LEVELS)))
    b = [before.get(k, 0) for k in LEVELS]
    a = [after.get(k, 0) for k in LEVELS]
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=130)
    w = 0.38
    ax.bar([x - w / 2 for x in xs], b, width=w, label="baseline (self-claimed)", color="#9aa7b8")
    ax.bar([x + w / 2 for x in xs], a, width=w, label="current (evidence-reconciled)", color="#2f6f9f")
    for x, y in zip(xs, b):
        ax.text(x - w / 2, y + 0.2, str(y), ha="center", fontsize=8)
    for x, y in zip(xs, a):
        ax.text(x + w / 2, y + 0.2, str(y), ha="center", fontsize=8)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{i}\n{k}" for i, k in enumerate(LEVELS)], fontsize=8)
    ax.set_ylabel("capabilities")
    bs = (gate["maturity"]["before"] or {}).get("maturity_score")
    as_ = (gate["maturity"]["after"] or {}).get("maturity_score")
    ax.set_title(f"five-level maturity distribution  (score {bs} -> {as_})", fontsize=9)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_axes_health(gate: Mapping[str, Any], out: Path) -> Path:
    """图 2：四轴交叉一致性——每轴的「已核对量」与「发现量」。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = gate["axes"]["cross_source"]
    qb = gate["axes"]["quote_binding"]
    rc = gate["axes"]["markdown_recount"]
    es = gate["axes"]["evidence_support"]
    labels = ["cross-source\n(levels)", "quote binding\n(citations)",
              "markdown recount\n(metrics)", "evidence support\n(levels)"]
    checked = [xs.get("total") or 0, qb.get("total") or 0,
               len(rc.get("metrics") or []), (es.get("supported") or 0) + (es.get("orphan") or 0)]
    found = [len(gate["findings"]["divergence_mismatch"]), len(gate["findings"]["quote_binding"]),
             sum(1 for m in (rc.get("metrics") or []) if not m["ok"]) + len(gate["findings"]["markdown_rows"]),
             len(gate["findings"]["evidence_support"])]
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=130)
    idx = range(len(labels))
    w = 0.38
    ax.bar([i - w / 2 for i in idx], checked, width=w, color="#2f6f9f", label="checked")
    ax.bar([i + w / 2 for i in idx], found, width=w, color="#c0392b", label="findings")
    for i, (c, f) in enumerate(zip(checked, found)):
        ax.text(i - w / 2, c + 0.2, str(c), ha="center", fontsize=8)
        ax.text(i + w / 2, f + 0.2, str(f), ha="center", fontsize=8)
    ax.set_xticks(list(idx))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("count")
    ax.set_title(f"four-axis cross-consistency (verdict: {gate['verdict']})", fontsize=9)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_increment_flow(gate: Mapping[str, Any], out: Path) -> Path:
    """图 3：增量账本——逐能力秩变化（瀑布式）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    entries = gate["increments"].get("entries", [])
    entries = [e for e in entries if e.get("rank_delta") is not None]
    entries.sort(key=lambda e: (e["rank_delta"], e["capability_id"]))
    fig, ax = plt.subplots(figsize=(7.6, 4.6), dpi=130)
    ys = list(range(len(entries)))
    deltas = [e["rank_delta"] for e in entries]
    colors = ["#c0392b" if d < 0 else ("#27ae60" if d > 0 else "#9aa7b8") for d in deltas]
    ax.barh(ys, deltas, color=colors)
    ax.set_yticks(ys)
    ax.set_yticklabels([e["capability_id"] for e in entries], fontsize=5.5)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("maturity rank delta (current - baseline)")
    ax.set_title(f"increment ledger: {gate['increments'].get('counts')}", fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_report(gate: Mapping[str, Any], *, mutation: Mapping[str, Any] | None,
                  repro_cmd: str, anchors: Sequence[Mapping[str, str]]) -> str:
    before = gate["maturity"]["before"] or {}
    after = gate["maturity"]["after"] or {}
    inc = gate["increments"]
    xs = gate["axes"]["cross_source"]
    qb = gate["axes"]["quote_binding"]
    rc = gate["axes"]["markdown_recount"]
    es = gate["axes"]["evidence_support"]

    lines: List[str] = []
    A = lines.append
    A("# H6-MATURITY-GATE：能力增量账本 + 五级成熟度 + 交叉一致性（G1 验收门报告）")
    A("")
    A(f"> 门禁裁定：**{gate['verdict']}**（判据通过率 {gate['criteria_pass_rate']:.3f}）"
      f" · 阈值指纹 `{gate['thresholds_sha256'][:16]}`"
      f" · 增量链头 `{str(inc.get('chain_head'))[:16]}`（{inc.get('chain_len')} 条事件）")
    A("")

    # ① 案例介绍
    A("## ① 案例介绍（物理/工程背景与对象）")
    A("")
    A("本门不针对某个算例，而针对**能力台账本身**：平台把「某个能力有多成熟」的自述与"
      "独立证据对账，输出可机器复核的等级。对象是平台能力清单，量纲为「等级秩」"
      f"（L0..L{len(LEVELS) - 1}）。")
    A("")
    srcs = gate.get("sources", [])
    A(_md_table(["源", "角色", "路径", "as_of", "能力数", "sha256(前16)"],
                [[s["source_id"], "可用" if s["available"] else "**不可用**", f"`{s['path']}`",
                  s.get("as_of") or "-", s.get("capability_count", "-"),
                  (s.get("sha256") or "-")[:16]] for s in srcs]))
    A("")
    A("五级成熟度阶梯（本门唯一权威定义，秩严格单调）：")
    A("")
    A(_md_table(["等级", "秩", "判据"],
                [[k, LEVEL_RANK[k], v] for k, v in
                 LEVEL_DEFINITIONS.items()]))
    A("")

    # ② 怎么做的
    A("## ② 怎么做的（方法 / 流程 / 关键设置）")
    A("")
    A("```")
    A("自述快照(baseline) ─┐")
    A("                     ├─► 轴A 逐能力秩比较 → 分歧归类(OK/OVERCLAIM/REVERIFY/UNVERIFIED)")
    A("证据台账(current) ───┘        │")
    A("        │                     ├─► 轴B 引文逐字绑定：重算 line_sha256 vs 记录值")
    A("        ├─ 原始来源语料 ──────┤")
    A("        │                     ├─► 轴C 人读表数字 vs 台账重算（防手改/生成漂移）")
    A("        │                     └─► 轴D 等级↔证据同向（非 WITHHELD 必有同向证据）")
    A("        ▼")
    A("增量账本（逐能力 direction + 前驱哈希链）→ 五级画像 → 判据 C1..C8 → 裁定")
    A("```")
    A("")
    A("关键设置（**运行前声明**，不得事后调参）：")
    A("")
    A(_md_table(["阈值键", "值", "含义"],
                [[f"`{k}`", v, ""] for k, v in gate["thresholds"].items()]))
    A("")
    A(f"阈值集合指纹 `thresholds_sha256 = {gate['thresholds_sha256']}`，"
      "写入判定体，事后改阈值必然导致 `--verify` 重算不符。")
    A("")

    # ③ 计算结果
    A("## ③ 计算结果（数字表，程序化注入）")
    A("")
    A("### 3.1 五级成熟度画像")
    A("")
    A(_md_table(["等级", "秩", "基线(自述)", "当前(对账)", "变化"],
                [[k, LEVEL_RANK[k], (before.get("histogram") or {}).get(k, 0),
                  (after.get("histogram") or {}).get(k, 0),
                  (after.get("histogram") or {}).get(k, 0) - (before.get("histogram") or {}).get(k, 0)]
                 for k in LEVELS]))
    A("")
    A(f"- 平均秩：基线 {before.get('mean_rank')} → 当前 {after.get('mean_rank')}")
    A(f"- 归一化成熟度得分（均值秩 / {after.get('max_rank')}）："
      f"**{before.get('maturity_score')} → {after.get('maturity_score')}**"
      f"（差 {gate['maturity']['score_delta']:+}）")
    A(f"- N = {after.get('n')} 个能力")
    A("")
    A("### 3.2 增量账本（基线 → 当前）")
    A("")
    A(_md_table(["方向", "数量"], [[k, v] for k, v in inc.get("counts", {}).items()]))
    A("")
    A("逐能力明细（含证据引用数）：")
    A("")
    A(_md_table(["#", "能力", "基线", "当前", "方向", "Δ秩", "证据数"],
                [[e["seq"], f"`{e['capability_id']}`", e.get("baseline_level"),
                  e.get("current_level"), e["direction"],
                  "" if e["rank_delta"] is None else f"{e['rank_delta']:+}",
                  e["evidence_count"]] for e in inc.get("entries", [])]))
    A("")
    A("### 3.3 交叉一致性四轴")
    A("")
    A(_md_table(["轴", "核对量", "通过", "发现", "比率"],
                [["A 自述↔对账", xs.get("total"), xs.get("agreement"),
                  len(gate["findings"]["divergence_mismatch"]),
                  f"{(xs.get('agreement') or 0) / max(1, xs.get('total') or 1):.3f}"],
                 ["B 引文绑定", qb.get("total"), qb.get("matched"), qb.get("drift"),
                  f"{qb.get('match_rate')}"],
                 ["C 表↔台账重算", len(rc.get("metrics") or []),
                  sum(1 for m in rc.get("metrics") or [] if m["ok"]),
                  len(gate["findings"]["markdown_rows"]), "-"],
                 ["D 等级↔证据", (es.get("supported") or 0) + (es.get("orphan") or 0),
                  es.get("supported"), es.get("orphan"), "-"]]))
    A("")
    A("轴 C 逐指标对照（ref = 已发布人读表数字，got = 从台账重算）：")
    A("")
    A(_md_table(["子轴", "指标", "ref", "got", "偏差", "一致"],
                [[m["axis"], m["metric"], m["ref"], m["got"], f"{m['delta']:+}",
                  "✅" if m["ok"] else "❌"] for m in rc.get("metrics") or []]))
    A("")
    A("### 3.4 判据明细（C1..C8）")
    A("")
    A(_md_table(["判据", "陈述", "值", "比较", "阈值", "结果"],
                [[c["criterion_id"], c["statement"], c["value"], c["comparator"],
                  c["threshold"], "PASS" if c["pass"] else "**FAIL**"] for c in gate["criteria"]]))
    A("")

    # ④ 图
    A("## ④ 结果图")
    A("")
    A("- 图 1 `fig1_maturity_profile.png`：五级成熟度分布（基线 vs 当前），标题内嵌成熟度得分")
    A("- 图 2 `fig2_axes_health.png`：四轴交叉一致性——每轴核对量与发现量")
    A("- 图 3 `fig3_increment_flow.png`：增量账本逐能力秩变化")
    A("")

    # benchmark
    A("## ⑤ 数值对照与检出基准（ref vs got + 误差）")
    A("")
    if mutation:
        A(_md_table(["量", "ref（已知注入）", "got（门检出）", "误差", "召回"],
                    [["注入缺陷总数", mutation.get("injected"), mutation.get("detected"),
                      (mutation.get("detected") or 0) - (mutation.get("injected") or 0),
                      f"{mutation.get('recall')}"],
                     ["干净副本上的假阳性", 0, mutation.get("false_positives"), 
                      mutation.get("false_positives"), "-"]]))
        A("")
        A("逐用例（每个用例 = 对台账副本施加一处确定性变异，独立跑门）：")
        A("")
        A(_md_table(["用例", "变异", "注入", "检出", "检出判据"],
                    [[c["case"], c["mutation"], c["injected"], c["detected"],
                      ", ".join(c.get("fired") or []) or "-"] for c in mutation.get("cases", [])]))
        A("")

    # ⑥ 误差判定
    A("## ⑥ 误差判定")
    A("")
    A("本门的关键量全部是**计数/等级**，基准为「独立重算值」或「已发布数字」，故误差定义为"
      "`got - ref`（计数差）或等级秩差，判据容差为 0（不允许任何漂移）。")
    A("")
    n_caps = max(1, (after.get("n") or 0))
    max_md = max((abs(m["delta"]) for m in rc.get("metrics") or []), default=0)
    rows_err = [
        ("人读表↔台账重算最大计数偏差", 0, max_md),
        ("引文逐字绑定失败数（逐字可查）", 0, qb.get("drift") or 0),
        ("分歧标签复算不一致数", 0, len(gate["findings"]["divergence_mismatch"])),
        ("等级↔证据不同向数", 0, es.get("orphan") or 0),
        ("悬空引文（来源不可解析）数", 0, qb.get("source_unresolved") or 0),
    ]
    A(_md_table(["关键量", "基准 ref", "本门 got", "误差 got-ref", "相对量级", "是否<3%"],
                [[k, ref, got, f"{got - ref:+d}", f"{(got - ref) / n_caps:.1%}（以 {n_caps} 个能力为基数）",
                  "✓" if abs((got - ref) / n_caps) < 0.03 else "✗"] for k, ref, got in rows_err]))
    A("")
    A(f"**门禁裁定：{gate['verdict']}**；判据通过率 {gate['criteria_pass_rate']:.3f}。"
      "未通过判据与对应 finding 逐条列于 `gate.json.findings`，不做任何粉饰。")
    A("")

    # ⑤ 注意点
    A("## ⑦ 注意点 / 局限 / 适用范围")
    A("")
    A("1. **本门不生产领域证据**：它只做「账面一致性」。等级是否真的 REAL 由证据链"
      "（基准 vs 实现误差）决定，本门只保证该等级与证据、与自述、与发布数字三方不矛盾。")
    A("2. **源缺失必须显式降级**：任何源不可用时在 `gate.json.sources` 记 `available=false`，"
      "相应判据值为 `null` 而非 0（宁拒不猜；`null` 不通过，不会静默成绿）。")
    A("3. **分歧率高不是缺陷**：`OVERCLAIM` 大量出现说明自述系统性虚高，这正是台账存在的理由；"
      "判据 C3/C4 只要求「分歧可归类、可复算」，不要求「分歧为零」。")
    A("4. **引文绑定依赖原始语料**：语料是快照；报告正文若在上游变更，本门会报 drift，"
      "此时应重新 curation 而不是放宽阈值。")
    A("5. **增量链不是防篡改链**：单条事件含 `prev_sha256` 可发现中间被改，但尾部截断需外部锚；"
      "本门把 `chain_head` 写进判定体，`--verify` 会重算比对。")
    n = 6
    for src in gate.get("sources", []):
        if not src.get("available"):
            A(f"{n}. **源 `{src['source_id']}` 不可用（显式缺口，非绿灯）**："
              f"{src.get('note') or '（无说明）'} —— 相关判据取 `null`（不通过），"
              "并在 `gate.json.sources` 留痕，绝不静默当作通过。")
            n += 1
    A("")
    A("## ⑧ 复现")
    A("")
    A("```bash")
    A(repro_cmd)
    A("```")
    A("")
    A("## ⑨ 外部锚点")
    A("")
    A(_md_table(["锚点", "出处"],
                [[a["name"], f"`{a['ref']}`"] for a in anchors]))
    A("")
    return "\n".join(lines) + "\n"