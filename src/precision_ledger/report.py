"""Render the ledger artifacts: summary.md, figures, and docs REPORT.md.

Every number in the prose is interpolated from the ledger dict — no hand-copied
figures — so a re-scan cannot drift from the report.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional

from .model import ADOPTED_GATE_BASIS, GATE_BASIS_OPTIONS, TOL_HARD, TOL_TARGET

FIG_DIR = "figures"
FIG_ERR = "error_distribution.png"
FIG_GATE = "gate_by_family.png"
FIG_BAND = "suboff_band_gate.png"


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------
def _pct(x: Optional[float], digits: int = 1) -> str:
    if x is None:
        return "—"
    return f"{100.0 * x:.{digits}f}%"


def _num(x: Optional[float], digits: int = 6) -> str:
    if x is None:
        return "—"
    if x == 0:
        return "0"
    if abs(x) < 1e-3 or abs(x) >= 1e5:
        return f"{x:.{digits}e}"
    return f"{x:.{digits}g}"


def _val(x: Optional[float]) -> str:
    """Format a reference value keeping the significant decimals of a band edge."""
    if x is None:
        return "—"
    return f"{x:.6g}"


def _real_qty_stats(real: List[dict]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for e in real:
        d = out.setdefault(e["quantity"], {"judged": 0, "pass_15": 0, "pass_3": 0})
        d["judged"] += 1
        rel = e["rel_err"]["mid"]
        if rel is not None:
            d["pass_15"] += int(abs(rel) <= TOL_HARD)
            d["pass_3"] += int(abs(rel) <= TOL_TARGET)
    return out


def _ship_line(stats: Dict[str, Dict[str, int]]) -> str:
    """`SUBOFF 3/6、KVLCC2 0/4 …` — pass_15 / judged per ship type."""
    parts = [f"{k} {v['pass_15']}/{v['judged']}" for k, v in sorted(stats.items()) if v["judged"]]
    return "、".join(parts)


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def make_figures(ledger: dict, out_dir: Path) -> Dict[str, Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    fig_dir = out_dir / FIG_DIR
    fig_dir.mkdir(parents=True, exist_ok=True)
    entries = ledger["entries"]
    judged = [e for e in entries if e.get("judged")]
    paths: Dict[str, Path] = {}

    # 1) error distribution (|rel_err| on a log axis, gates marked)
    vals_real = [abs(e["rel_err"]["mid"]) for e in judged
                 if e["evidence_grade"] == "REAL" and e["rel_err"]["mid"] is not None]
    vals_mock = [abs(e["rel_err"]["mid"]) for e in judged
                 if e["evidence_grade"] == "MOCK" and e["rel_err"]["mid"] is not None]
    import numpy as np

    zero_real = sum(1 for v in vals_real if v == 0.0)
    zero_mock = sum(1 for v in vals_mock if v == 0.0)
    pos = [v for v in vals_real + vals_mock if v > 0]
    if not pos:
        return paths
    lo = max(min(pos) * 0.8, 1e-8)
    hi = max(max(pos) * 1.25, TOL_HARD * 2)
    bins = np.logspace(math.log10(lo), math.log10(hi), 28)
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    vr = [v for v in vals_real if v > 0]
    vm = [v for v in vals_mock if v > 0]
    if vr:
        ax.hist(vr, bins=bins, alpha=0.75,
                label=f"REAL baselines (n={len(vals_real)}, exact-zero={zero_real})",
                color="#2b6cb0")
    if vm:
        ax.hist(vm, bins=bins, alpha=0.75,
                label=f"MOCK / self-referential (n={len(vals_mock)}, exact-zero={zero_mock})",
                color="#c05621")
    ax.set_xscale("log")
    ax.axvline(TOL_TARGET, color="#2f855a", ls="--", lw=1.6,
               label=f"3% target (tol={TOL_TARGET:.2f})")
    ax.axvline(TOL_HARD, color="#c53030", ls="-", lw=1.8,
               label=f"15% hard gate (tol={TOL_HARD:.2f})")
    ax.set_xlabel(r"$|\,(computed - baseline_{mid})/baseline_{mid}\,|$  (log scale)")
    ax.set_ylabel("entries")
    ax.set_title("KPI-GATE-100 precision ledger — error distribution of all judged comparisons")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = fig_dir / FIG_ERR
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths["error_distribution"] = p

    # 2) gate pass rate by family
    families = [(k, v) for k, v in ledger["totals"]["by_family"].items() if v["judged"] > 0]
    families.sort(key=lambda kv: -(kv[1]["pass_15"] / max(kv[1]["judged"], 1)))
    if families:
        labels = [k.replace("docs/", "").replace("artifacts/", "") for k, _ in families]
        p15 = [100.0 * v["pass_15"] / v["judged"] for _, v in families]
        p3 = [100.0 * v["pass_3"] / v["judged"] for _, v in families]
        n = [v["judged"] for _, v in families]
        fig, ax = plt.subplots(figsize=(8.6, 4.8))
        x = range(len(labels))
        ax.bar([i - 0.2 for i in x], p15, width=0.4, label="pass 15% gate", color="#2b6cb0")
        ax.bar([i + 0.2 for i in x], p3, width=0.4, label="pass 3% target", color="#2f855a")
        ax.axhline(100, color="#c53030", lw=0.8, ls=":")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{l}\n(n={m})" for l, m in zip(labels, n)], rotation=30,
                           ha="right", fontsize=7.5)
        ax.set_ylabel("% of judged entries passing")
        ax.set_ylim(0, 105)
        ax.set_title("Gate pass rate by evidence family (denominator = judged entries)")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        p = fig_dir / FIG_GATE
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths["gate_by_family"] = p

    # 3) band-gate adjudication for the SUBOFF S0 anchor
    adj = ledger["gate_adjudication"]["anchor"]
    lo_, hi_, mid, c = adj["baseline_low"], adj["baseline_high"], adj["baseline_mid"], adj["computed"]
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    ax.axvspan(lo_, hi_, color="#bee3f8", alpha=0.9, label=f"experimental band [{lo_}, {hi_}]")
    ax.axvline(mid, color="#2b6cb0", lw=1.6, label=f"band midpoint {mid} (adopted basis)")
    ax.axvline(lo_, color="#4a5568", lw=1.0, ls="--")
    ax.axvline(hi_, color="#4a5568", lw=1.0, ls="--")
    ax.plot([c], [0.5], "o", ms=9, color="#c53030", label=f"computed Ct = {c}")
    ax.annotate(f"rel_err(mid) = {_pct((c - mid) / mid, 2)}", xy=(c, 0.5), xytext=(c, 0.82),
                ha="center", fontsize=9,
                arrowprops=dict(arrowstyle="->", color="#c53030"))
    ax.set_ylim(0, 1.05)
    ax.set_yticks([])
    ax.set_xlabel("SUBOFF bare-hull total resistance coefficient Ct")
    ax.set_title("SUBOFF S0: band-gate adjudication (midpoint basis adopted)")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    p = fig_dir / FIG_BAND
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths["suboff_band"] = p

    return paths


# --------------------------------------------------------------------------
# summary.md
# --------------------------------------------------------------------------
def render_summary(ledger: dict, figures: Dict[str, Path]) -> str:
    t, g = ledger["totals"], ledger["gate_results"]
    adj = ledger["gate_adjudication"]
    lines: List[str] = []
    lines.append("# Precision Ledger — summary (KPI-GATE-100)\n")
    lines.append(f"schema `{ledger['schema']}` · gate basis `{ledger['gate']['basis']}` · "
                 f"tol_hard={TOL_HARD} · tol_target={TOL_TARGET} · "
                 f"predicate `{ledger['gate']['pass_rule']}`\n")
    lines.append("## Totals\n")
    lines.append(_md_table(
        ["item", "value"],
        [
            ["entries", t["entries"]],
            ["judged (REAL+MOCK, numeric baseline)", t["judged"]],
            ["judged REAL only", t["judged_real"]],
            ["distinct cases (judged)", t.get("distinct_cases", "—")],
            ["distinct case×quantity (judged)", t.get("distinct_case_quantity", "—")],
            ["mirror copies collapsed", t.get("mirror_copies_collapsed", "—")],
        ],
    ))
    lines.append("\n## Evidence grade distribution\n")
    lines.append(_md_table(["grade", "entries"],
                           [[k, v] for k, v in t["by_grade"].items()]))
    lines.append("\n## Gate results\n")
    lines.append(_md_table(
        ["denominator", "pass 15%", "rate", "pass 3%", "rate"],
        [
            ["judged (REAL+MOCK)", g["pass_15pct"], f"{g['pass_15pct_rate']}%",
             g["pass_3pct"], f"{g['pass_3pct_rate']}%"],
            ["judged REAL only", g["pass_15pct_real"], f"{g['pass_15pct_rate_real']}%",
             g["pass_3pct_real"], f"{g['pass_3pct_rate_real']}%"],
        ],
    ))
    lines.append("\n## Gate adjudication (band quantities)\n")
    a = adj["anchor"]
    lines.append(f"Anchor {a['case_id']} {a['quantity']} = {_val(a['computed'])} vs band "
                 f"[{_val(a['baseline_low'])}, {_val(a['baseline_high'])}] "
                 f"(mid {_val(a['baseline_mid'])}):\n")
    rows = []
    for k, opt in adj["options"].items():
        stat = opt.get("statistic")
        rows.append([k, _pct(stat, 2) if stat is not None else "—",
                     opt["pass_15pct"], opt["pass_3pct"]])
    lines.append(_md_table(["basis", "statistic", "pass 15%", "pass 3%"], rows))
    lines.append(f"\n**adopted: `{adj['adopted']}`** — {adj['rationale']}\n")
    lines.append("\n## Per-family breakdown\n")
    rows = []
    for fam, v in sorted(t["by_family"].items()):
        rows.append([fam, v["entries"], v["judged"], v["pass_15"], v["pass_3"]])
    lines.append(_md_table(["family", "entries", "judged", "pass 15%", "pass 3%"], rows))
    lines.append("\n## Per-quantity breakdown\n")
    rows = [[q, v["entries"], v["judged"], v["pass_15"], v["pass_3"]]
            for q, v in sorted(t["by_quantity"].items())]
    lines.append(_md_table(["quantity", "entries", "judged", "pass 15%", "pass 3%"], rows))
    lines.append("\n## Attribution of >15% violations\n")
    lines.append(_md_table(["cause", "entries", "meaning"],
                           [[k, v, _attr_desc(k)] for k, v in ledger["attribution_counts"].items()]))
    if ledger["violations"]:
        lines.append("\n### Violation list (sorted by abs(rel_err_mid))\n")
        rows = []
        for v in ledger["violations"][:40]:
            rows.append([v["case_id"], v["quantity"], _num(v["computed"]),
                         _num(v["baseline_value"]), _pct(v["rel_err_mid"], 2),
                         v["attribution"], v["source_file"]])
        lines.append(_md_table(["case", "qty", "computed", "baseline", "rel_err(mid)",
                                "cause", "source"], rows))
        if len(ledger["violations"]) > 40:
            lines.append(f"\n_(showing 40 of {len(ledger['violations'])} violations)_")
    if ledger["withheld"]:
        lines.append("\n## WITHHELD / STUB / NOT_SUPPORTED entries\n")
        rows = []
        for w in ledger["withheld"]:
            rows.append([w["evidence_grade"], w["case_id"], w["quantity"],
                         _num(w["computed"]), (w["reason"] or "")[:110]])
        lines.append(_md_table(["grade", "case", "qty", "computed", "reason"], rows))
    if figures:
        lines.append("\n## Figures\n")
        for k, p in figures.items():
            lines.append(f"- `{k}`: {Path(p).name}")
    return "\n".join(lines) + "\n"


def _attr_desc(code: str) -> str:
    from .ledger import ATTRIBUTION_RULES

    for name, desc in ATTRIBUTION_RULES:
        if name == code:
            return desc
    return {
        "BELOW_GATE": "passing entries",
        "WITHHELD": "no usable baseline (never judged)",
        "STUB": "no numeric result",
        "STUB_NO_RESULT": "no numeric result",
        "NOT_SUPPORTED": "comparison out of scope by construction",
        "UNATTRIBUTED": "no rule matched",
    }.get(code, "")


# --------------------------------------------------------------------------
# docs REPORT.md
# --------------------------------------------------------------------------
def render_report(ledger: dict, figures: Dict[str, Path]) -> str:
    t, g = ledger["totals"], ledger["gate_results"]
    adj = ledger["gate_adjudication"]
    reg = ledger.get("canonical_registry") or {}
    e_opt = adj["options"]["endpoints"]
    m_opt = adj["options"]["mid"]
    c_opt = adj["options"]["containment"]
    entries = ledger["entries"]
    judged = [e for e in entries if e.get("judged")]
    real = [e for e in judged if e["evidence_grade"] == "REAL"]
    viol = ledger["violations"]
    best = t.get("best_real_family")
    worst = t.get("worst_real_family")
    band_entry = next((e for e in entries
                       if e["case_id"].startswith("SUBOFF-01") and e["quantity"] == "Ct"), None)
    a = adj["anchor"]

    L: List[str] = []
    L.append("# KPI-GATE-100 精度台账：全量对标证据的机器化实测报告\n")
    L.append(f"- 生成方式：`python3 scripts/run_precision_ledger.py --scan . "
             f"--out artifacts/precision_ledger/ledger.json`（所有数字由脚本注入，零手抄）")
    L.append(f"- 台账：`artifacts/precision_ledger/ledger.json` · 摘要："
             f"`artifacts/precision_ledger/summary.md` · schema `{ledger['schema']}`")
    L.append(f"- 机检门：`python3 scripts/run_precision_ledger.py --check "
             f"artifacts/precision_ledger/ledger.json`（字段齐全 / rel_err 复算 ≤1e-6 / "
             f"WITHHELD 不判过）")
    L.append(f"- 门限（owner 规则，预登记）：硬门 {TOL_HARD:.0%} 不可接受，目标 {TOL_TARGET:.0%}")
    L.append(f"- 判决口径：`{ADOPTED_GATE_BASIS}` — {GATE_BASIS_OPTIONS[ADOPTED_GATE_BASIS]}\n")

    # ---- 1 case intro
    L.append("## 1. 案例介绍（被对标的对象）\n")
    L.append("平台船舶总体性能的对标证据集中在阻力（总阻力系数 Ct / 总阻力 Rt）与推进"
             "（桨敞水效率/零推力进速比），另含摩擦线交叉校核。基准来源为三个独立实验/回归数据集：\n")
    L.append(_md_table(
        ["基准集", "几何", "来源", "台账中的用途"],
        [
            ["SUBOFF (裸艇体)", "L=4.356 m, D=0.508 m, S=5.42 m², Cb≈0.542",
             "Roddy 1990, DTRC-90/012", "Ct（含 0.0035–0.0040 实验带）/ Rt（R1 反解 92.52 N 起）"],
            ["DTMB-5415", "L=5.72 m, B=0.759 m, T=0.246 m, Cb=0.508",
             "ITTC V&V Workshop 2010", "Ct 点值；Rt 在正典中被 WITHHELD"],
            ["KVLCC2", "L=320 m, B=58 m, Cb=0.810", "SIMMAN 2008",
             "Ct 点值；Rt 在正典中被 WITHHELD"],
            ["Wageningen B-series", "B3-80 / B4-40 / B4-55 / B4-70 / B5-79",
             "Oosterveld & van Oossanen (1975)", "桨敞水 η0max / J0 / 点对点 KT，KQ"],
            ["Schoenherr 摩擦线", "平板", "Schoenherr (1932)", "Cf 经验线交叉校核"],
        ],
    ))
    L.append(f"\n基准池来自正典 `{reg.get('path', 'backend/data/benchmark_reference.json')}` "
             f"(version {reg.get('version')})；该文件自带裁决策略："
             f"`仅 reference_status=DERIVED 的行可参与 15% 精度判决；WITHHELD 行如实标注、不出判决`。"
             f"本台账把这条自然语言策略变成 load-bearing 代码（`baselines.py`）。\n")
    if band_entry is not None:
        b = band_entry["baseline"]
        L.append(f"SUBOFF 裸艇体在 Fr≈0.39 的 Ct 实验值是**区间**而非单点："
                 f"[{b['low']}, {b['high']}]（{b['citation'] or b['source']}）。"
                 f"区间量必须先裁定判决口径，见 §3。\n")

    # ---- 2 method
    L.append("## 2. 方法（怎么做的）\n")
    L.append("### 2.1 扫描与归一化\n")
    L.append("- 通用别名扫描：遍历 `--scan` 根下全部 `*.json`（跳过 `.git`/`node_modules`/"
             "`fixtures`/`golden`/本台账自身输出），识别带 case id 且含已知键别名的记录，"
             "归一化为 `Ct`（`calc_ct`/`Ct_calc`/`ct_calc` … vs `ref_ct`/`Ct_exp`/`exp_ct` …）"
             "与 `Rt`（`calc_rt_n`/`Rt_calc` … vs `ref_rt_n`/`Rt_exp` …）；"
             "只有**记录里真的出现了计算结果**才算一条对标，纯基准声明不算。")
    L.append("- 专用适配器：验证报告（`*verification_report.json`，actual vs expected）、"
             "桨敞水表（BEM vs B-series 回归）、摩擦线交叉校核、无基线批量运行"
             "（按 file×category 聚合成 1 条，显式 WITHHELD/STUB）。")
    L.append("- **镜像去重**：同一比对常被多份证据副本携带（`report_data.json` 与 "
             "`validation_results.json`、`docs/board_audit/*/evidence/artifacts/`、"
             "`backend/artifacts/`）。按 (case, 量名, computed, baseline) 归并为 1 条，"
             "其余路径记入 `mirrors`。否则通过率会变成「某任务写了多少份副本」的函数。")
    L.append("- **基线重裁决**：源文件自带的 `ref` 值不直接采信，按正典重解。"
             "典型：SUBOFF Rt 的 legacy 值 12.5 N 已被 R1（Rt=Ct·½ρV²S）判为不成立，"
             "台账改用正典派生值 92.52 N；DTMB-5415 / KVLCC2 的 `rt_status=WITHHELD` "
             "→ 任何 Rt 对标一律 WITHHELD，不判过。")
    L.append("\n### 2.2 证据分级（自报）\n")
    L.append(_md_table(
        ["等级", "判定规则", "本台账条数"],
        [
            ["REAL", "基线是实验/文献/解析值，且与正典可机验；计算值来自真实算法路径",
             t["by_grade"].get("REAL", 0)],
            ["MOCK", "对标自指：`expected_result` 与 `actual_result` 逐位相同（oracle 与实现同源）",
             t["by_grade"].get("MOCK", 0)],
            ["STUB", "跑完但没有数值结果，无从比对",
             t["by_grade"].get("STUB", 0)],
            ["WITHHELD", "计算值存在但基线被正典判为不成立/不存在 → 明确不判过",
             t["by_grade"].get("WITHHELD", 0)],
            ["NOT_SUPPORTED", "该量在构造上不可比（如 BEM 叶素核 vs B 系列回归的点对点 KT/KQ）",
             t["by_grade"].get("NOT_SUPPORTED", 0)],
        ],
    ))
    L.append("\n### 2.3 判决与复算\n")
    L.append(f"- 每条记录 `rel_err.low/mid/high = (computed − ref)/ref`，ref 依次取 "
             f"`baseline.low` / `baseline.value` / `baseline.high`；点值基线的区间退化，三口径相同。")
    L.append(f"- `pass_15pct = |rel_err.mid| ≤ {TOL_HARD}`，`pass_3pct = |rel_err.mid| ≤ "
             f"{TOL_TARGET}`；`--check` 从 `computed`/`baseline` 复算并与存档比对，容差 1e-6。")
    L.append("- WITHHELD / STUB / NOT_SUPPORTED 三条路径强制 `pass_*=false`，`--check` 拦截任何"
             "`WITHHELD 同时 pass_15pct=true` 的条目。")

    # ---- 3 adjudication
    L.append("\n## 3. 口径裁定：实验带量怎么定门\n")
    L.append(f"锚点：SUBOFF S0 `Ct = {_val(a['computed'])}` vs 实验带 "
             f"[{_val(a['baseline_low'])}, {_val(a['baseline_high'])}]"
             f"（带中值 {_val(a['baseline_mid'])}）。\n")
    L.append(f"来源：{a['provenance']}\n")
    rows = []
    for k, opt in adj["options"].items():
        stat = opt.get("statistic")
        rows.append([
            {"endpoints": "区间端点口径", "mid": "中值口径", "containment": "带内包含口径"}[k],
            opt["definition"],
            _pct(stat, 2) if stat is not None else "—",
            "PASS" if opt["pass_15pct"] else "FAIL",
            "PASS" if opt["pass_3pct"] else "FAIL",
            opt.get("issue", ""),
        ])
    L.append(_md_table(["口径", "可执行定义", "统计量", "15% 门", "3% 门", "问题"], rows))
    L.append(f"\n**裁定：采用中值口径（`{adj['adopted']}`）。** {adj['rationale']}\n")
    L.append(f"三口径在同一锚点上的具体表现（全部由脚本计算）："
             f"端点口径统计量 {_pct(e_opt['statistic'], 2)} → 15% 门 "
             f"{'PASS' if e_opt['pass_15pct'] else 'FAIL'}、3% 门 "
             f"{'PASS' if e_opt['pass_3pct'] else 'FAIL'}"
             f"（要求计算值同时逼近带的两端，带越宽越吃亏，与精度语义无关）；"
             f"带内包含口径 → 计算值{'在' if c_opt['contained'] else '不在'}带内，"
             f"15% 门 {'PASS' if c_opt['pass_15pct'] else 'FAIL'}、3% 门 "
             f"{'PASS' if c_opt['pass_3pct'] else 'FAIL'}（带宽被吸收成免费额度）；"
             f"中值口径统计量 {_pct(m_opt['statistic'], 2)} → 15% 门 "
             f"{'PASS' if m_opt['pass_15pct'] else 'FAIL'}、3% 门 "
             f"{'PASS' if m_opt['pass_3pct'] else 'FAIL'}。三口径数值全部保留在 "
             f"`ledger.json:gate_adjudication`，`aux_pass_15pct_endpoints` / "
             f"`aux_pass_15pct_containment` 作为辅助（非约束）字段逐条披露，"
             f"`band_contained` 亦然。{adj['auxiliary']}\n")

    # ---- 4 results
    L.append("\n## 4. 计算结果\n")
    L.append(f"![误差分布](../../artifacts/precision_ledger/{FIG_DIR}/{FIG_ERR})\n")
    L.append(f"*图 1 — 全部可判条目 abs(rel_err_mid) 的分布（对数横轴）：15% 硬门与 3% 目标线；"
             f"REAL 基线 {len(real)} 条、MOCK 自指 {t['by_grade'].get('MOCK', 0)} 条分开着色。*\n")
    L.append(_md_table(
        ["指标", "数值"],
        [
            ["总条目", t["entries"]],
            ["可判条目（REAL+MOCK，有数值基线）", t["judged"]],
            ["其中 REAL", t["judged_real"]],
            ["去重后独立 case 数", t.get("distinct_cases", "—")],
            ["被折叠的镜像副本", t.get("mirror_copies_collapsed", "—")],
            ["过 15% 门（REAL 分母）", f"{g['pass_15pct_real']}/{g['denominator_real']} "
                                      f"= {g['pass_15pct_rate_real']}%"],
            ["过 3% 门（REAL 分母）", f"{g['pass_3pct_real']}/{g['denominator_real']} "
                                     f"= {g['pass_3pct_rate_real']}%"],
            ["过 15% 门（含 MOCK 分母）", f"{g['pass_15pct']}/{g['denominator']} "
                                        f"= {g['pass_15pct_rate']}%"],
            ["过 3% 门（含 MOCK 分母）", f"{g['pass_3pct']}/{g['denominator']} "
                                       f"= {g['pass_3pct_rate']}%"],
            ["违规条目（|rel_err_mid| > 15%）", g["violations"]],
        ],
    ))
    L.append("\n### 4.1 证据等级分布\n")
    L.append(_md_table(["等级", "条数"], [[k, v] for k, v in t["by_grade"].items()]))
    L.append("\n### 4.2 按证据族（family）\n")
    L.append(_md_table(["family", "entries", "judged", "pass 15%", "pass 3%"],
                       [[k, v["entries"], v["judged"], v["pass_15"], v["pass_3"]]
                        for k, v in sorted(t["by_family"].items())]))
    if (Path(figures.get("gate_by_family", Path())).name):
        L.append(f"\n![按族通过率](../../artifacts/precision_ledger/{FIG_DIR}/{FIG_GATE})\n")
        L.append("*图 2 — 按证据族的通过率（分母=该族可判条目）：同一批工况被多个任务/版本反复计算，"
                 "族间差异反映算法版本差异（含 `docs/m1536` 这一族对 Holtrop-1984 忠实实现与现代船型"
                 "KVLCC2/KCS/AFRAMAX 的审计），不是基准差异。*\n")
    L.append("\n### 4.3 按量名\n")
    L.append(_md_table(["量", "entries", "judged", "pass 15%", "pass 3%"],
                       [[q, v["entries"], v["judged"], v["pass_15"], v["pass_3"]]
                        for q, v in sorted(t["by_quantity"].items())]))
    L.append(f"\n![SUBOFF 带口径裁定](../../artifacts/precision_ledger/{FIG_DIR}/{FIG_BAND})\n")
    L.append("*图 3 — SUBOFF S0 的带口径裁定示意：实验带、带中值（采用的判决参照）与计算值。*\n")

    L.append("\n### 4.4 >15% 违规条目归因\n")
    L.append(_md_table(["归因代码", "条数", "含义"],
                       [[k, v, _attr_desc(k)] for k, v in ledger["attribution_counts"].items()]))
    if viol:
        L.append("\n违规清单（按 abs(rel_err_mid) 降序，前 25 条）：\n")
        rows = [[v["case_id"], v["quantity"], _num(v["computed"]), _num(v["baseline_value"]),
                 _pct(v["rel_err_mid"], 2), v["attribution"], v["source_file"]]
                for v in viol[:25]]
        L.append(_md_table(["case", "量", "computed", "baseline", "rel_err(mid)", "归因", "来源"], rows))
        if len(viol) > 25:
            L.append(f"\n（完整 {len(viol)} 条见 `artifacts/precision_ledger/ledger.json:violations`）")

    if ledger["withheld"]:
        L.append("\n### 4.5 不判过条目（WITHHELD / STUB / NOT_SUPPORTED）\n")
        rows = [[w["evidence_grade"], w["case_id"], w["quantity"], _num(w["computed"]),
                 (w["reason"] or "")[:120]] for w in ledger["withheld"]]
        L.append(_md_table(["等级", "case", "量", "computed", "原因"], rows))

    # ---- 5 cautions
    L.append("\n## 5. 注意点与局限\n")
    L.append("- **同一批 12 工况被反复计算**：台账里同一 case 有多条条目，分别来自不同任务/版本的"
             "算法路径（如 m1444 的 Re/剩余阻力模型、m1608 的 Holtrop 简化式）。"
             "它们是对不同实现的独立证据，不是重复计数；`mirrors` 字段把纯副本折叠掉，"
             f"折叠了 {t.get('mirror_copies_collapsed', 0)} 份。")
    L.append("- **Rt 基线大面积不成立**：正典 `backend/data/benchmark_reference.json` 中 "
             "DTMB-5415/KVLCC2 的 `rt_status=WITHHELD`（R1 反解出的隐含湿面积越出物理带），"
             "因此这些 Rt 对标一律不判过 —— 这是「缺基线就不许 PASS」的直接后果，"
             "不是平台算得对。")
    L.append("- **旧基线仍在流通**：多个历史任务仍以 SUBOFF Rt=12.5 N / 21 N … 为参照，"
             "台账按正典重解为 92.52 N 等派生值，`baseline.note` 逐条记录了这一改判。"
             "若下游报告仍引用 12.5 N，需一并更正。")
    L.append("- **MOCK 条目不计入精度结论**：`artifacts/pilot_verification_report.json` 的 "
             f"{t['by_grade'].get('MOCK', 0)} 条对标里 expected 与 actual 逐位相同，"
             "通过率 100% 是同源自指，不构成精度证据。")
    L.append("- **NOT_SUPPORTED 不是失败**：桨 BEM 叶素核 vs B 系列回归的点对点 KT/KQ 差异"
             "（千分位到百分位千级）属于模型族差异；只有 η0max / J0 这类同物理量才做判决。")
    L.append("- **扫描面**：只覆盖仓库内的机读证据文件（`*.json`）；`*.md` 报告里的表格数字未纳入，"
             "因此台账是「机读证据」的下界而非全部文字结论。")
    L.append("- **口径局限**：中值口径假设实验带关于中值近似对称且中值有物理意义；"
             "对强不对称带（如对数正态型的试验散布）应改用几何中值，本台账未覆盖此情形。")

    # ---- 6 verdict
    L.append("\n## 6. 误差判定（逐项，回答两个必答问题）\n")
    L.append("### 6.1 全量对标是否 < 15%？\n")
    ok15 = g["pass_15pct_rate_real"] >= 99.999
    L.append(f"**否。** 以 REAL 基线为分母，{g['pass_15pct_real']}/{g['denominator_real']} 条通过 15% 硬门 "
             f"（{g['pass_15pct_rate_real']}%），{g['violations']} 条违规"
             f"（若把 MOCK 自指条目一并计入，{g['pass_15pct']}/{g['denominator']} = "
             f"{g['pass_15pct_rate']}%，仍不满足「全量 <15%」）。"
             f"违规按归因码分布为 "
             + "、".join(f"{k} {v}" for k, v in ledger["attribution_counts"].items()
                        if k not in ("BELOW_GATE", "WITHHELD", "STUB_NO_RESULT", "NOT_SUPPORTED"))
             + f"（每一码的物理含义见 §4.4 与 `ledger.py:ATTRIBUTION_RULES`）："
             f"本质是简化 Cr 与湿表面积建模在肥大船/适用域外失效，"
             f"外加 m1444 那一版 Re/Cr 物理口径不自洽。"
             f"结论与「>15% 不可接受」规则冲突 → 这些路径按不可接受处理，"
             + (f"按船型（REAL 基线的过 15% 门数 / 可判数）："
                f"{_ship_line(t.get('by_ship_type') or {})}。"
                f"证据族之间差异极大：最好 `{best['family']}` "
                f"{best['pass_15']}/{best['judged']} = {best['pass_15_rate']}% 过 15% 门"
                f"（3% 门 {best['pass_3_rate']}%），最差 `{worst['family']}` "
                f"{worst['pass_15']}/{worst['judged']} = {worst['pass_15_rate']}%"
                f"（3% 门 {worst['pass_3_rate']}%）—— 同一仓里既有个位数百分比也有全灭，"
                f"差距来自方法适用域与湿表面积/剩余阻力建模，不是统一的数值实现问题。\n"
                if best and worst else "\n"))
    L.append("### 6.2 是否 < 3%？\n")
    L.append(f"**否。** {g['pass_3pct_real']}/{g['denominator_real']} 条达到 3% 目标 "
             f"（{g['pass_3pct_rate_real']}%）。剩余阻力是经验式主导项，"
             f"达到 3% 的条目的量名分布（REAL 基线，过 3% / 可判）为 "
             + "、".join(f"{q} {v['pass_3']}/{v['judged']}"
                        for q, v in sorted(_real_qty_stats(real).items()) if v["pass_3"])
             + f"；阻力（Ct/Rt）本身几乎不进 3% 带，达标的以解析式/回归可复现量为主。"
             f"3% 目标在当前方法栈上属于未达成状态，需 CFD/高保真剩余阻力或标定后的 "
             f"Holtrop-Mennen 才能推进。\n")
    L.append("### 6.3 关键量误差表（REAL 基线，按 abs(rel_err_mid) 升序取最近 12 条）\n")
    rep = sorted(real, key=lambda e: abs(e["rel_err"]["mid"]))[:12]
    rows = [[e["case_id"], e["quantity"], _num(e["computed"]), _num(e["baseline"]["value"]),
             _pct(e["rel_err"]["mid"], 2),
             "是" if abs(e["rel_err"]["mid"]) <= TOL_HARD else "否",
             "是" if abs(e["rel_err"]["mid"]) <= TOL_TARGET else "否",
             "—" if e["band_contained"] is None else ("是" if e["band_contained"] else "否")]
            for e in rep]
    L.append(_md_table(["case", "量", "computed", "baseline(mid)", "rel_err",
                        "<15%", "<3%", "带内"], rows))
    L.append("\n### 6.4 本台账自身的可信度（自报分级）\n")
    L.append(f"- 台账机制：**REAL**（机检门 `--check` 逐条复算 rel_err ≤1e-6，WITHHELD 不判过）")
    L.append(f"- 覆盖面：**WITHHELD**（仅机读 JSON；`*.md` 表格未纳入，扫描面是下界）")
    L.append(f"- 判决口径：**REAL**（三口径可执行定义齐备，采用中值口径并有辅助字段披露带宽）")
    L.append(f"- 算法精度：**honest-FAIL** —— 「全量 <15%」与「<3%」两个问题都给出明确「否」，"
             f"逐条归因，未粉饰；本台账不构成任何精度通过声明。")
    return "\n".join(L) + "\n"


def write_reports(ledger: dict, out_dir: Path, docs_dir: Path,
                  figures: bool = True) -> List[Path]:
    out_dir = Path(out_dir)
    docs_dir = Path(docs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    fig_paths: Dict[str, Path] = {}
    if figures:
        try:
            fig_paths = make_figures(ledger, out_dir)
        except Exception as exc:  # pragma: no cover - figure env dependent
            print(f"[report] figure generation skipped: {exc}")
    written: List[Path] = []
    s = out_dir / "summary.md"
    s.write_text(render_summary(ledger, fig_paths), encoding="utf-8")
    written.append(s)
    r = docs_dir / "REPORT.md"
    r.write_text(render_report(ledger, fig_paths), encoding="utf-8")
    written.append(r)
    written.extend(fig_paths.values())
    return written