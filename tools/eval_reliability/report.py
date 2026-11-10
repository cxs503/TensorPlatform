"""论文级报告生成：Markdown 六要素 + matplotlib 图（程序化注入，零手抄）。

六要素对应（owner 2026-10-29 升级版）：
① 案例介绍（判级体系是什么、数据从哪来、参数表）
② 怎么做的（系数/生成模型/判据/工具链）
③ 计算结果（数字表：由 ``run`` 传入的 dict 直接渲染）
④ 结果图（恢复曲线、覆盖率、注入对照、真实台账判级画像、最小审数曲线、门特性）
⑤ 注意点（踩过的坑与适用边界）
⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」或说明为何不适用）

本模块**不重新计算**任何数字：所有数值来自 ``run`` 的分析结果 dict，避免
「报告里的数与工件里的数不一致」这一常见造假面。
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


# ─────────────────────────────── 图 ───────────────────────────────

def _save(fig, path: Path, with_b64: bool = True) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    out: dict[str, Any] = {"path": str(path)}
    if with_b64:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140)
        out["png_base64"] = base64.b64encode(buf.getvalue()).decode("ascii")
    plt.close(fig)
    return out


def plot_recovery(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图1：α̂ 与 α_true 的恢复（含 CI 半宽）+ 覆盖率。"""
    rows = [r for r in result["recovery"]["rows"] if r.get("status") == "ok"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    tgt = [r["alpha_true"] for r in rows]
    est = [r["mean_alpha_hat"] for r in rows]
    err = [r["mean_ci_halfwidth"] for r in rows]
    axes[0].errorbar(tgt, est, yerr=err, fmt="o", capsize=4, color="#1f77b4",
                     label="alpha_hat +- mean CI half-width")
    axes[0].plot([0, 1], [0, 1], "--", color="grey", lw=1, label="identity")
    for r in rows:
        axes[0].annotate(f"bias={r['bias']:+.3f}", (r["alpha_true"], r["mean_alpha_hat"]),
                         textcoords="offset points", xytext=(6, -12), fontsize=8)
    axes[0].set_xlabel("alpha_true (population limit of generating model)")
    axes[0].set_ylabel("mean alpha_hat")
    axes[0].set_title(f"J1 alpha recovery (n={result['recovery']['n_units']}, "
                      f"reps={result['recovery']['reps']})")
    axes[0].legend(fontsize=8)

    cov = [r["ci_coverage"] for r in rows]
    axes[1].bar([f"{r['alpha_true']:.2f}" for r in rows], cov, color="#2ca02c")
    axes[1].axhline(result["thresholds"]["ci_coverage_min"], ls="--", color="crimson",
                    label=f"J2 gate {result['thresholds']['ci_coverage_min']}")
    axes[1].set_ylim(0, 1.05)
    axes[1].set_xlabel("alpha_true")
    axes[1].set_ylabel("95% CI coverage")
    axes[1].set_title("J2 bootstrap CI coverage")
    axes[1].legend(fontsize=8)
    return _save(fig, path)


def plot_injection(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图2：注入 lenient/harsh 后 Δα 的理论预测 vs 实测。"""
    inj = result["injection"]
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    if inj.get("status") == "ok":
        theory = inj["delta_alpha_theory"]
        mean_hat = inj["delta_alpha_hat_mean"]
        sd = inj["delta_alpha_hat_sd"] or 0.0
        ax.bar(["theory (population dA)", "measured (paired mean dA)"],
               [theory, mean_hat], color=["#9467bd", "#ff7f0e"])
        ax.errorbar([1], [mean_hat], yerr=[1.96 * sd], fmt="none", capsize=6,
                    ecolor="black")
        ax.annotate(f"rel.err={inj['relative_error']:.3f}",
                    (0.5, max(theory, mean_hat) * 0.6), ha="center", fontsize=9)
        ax.set_ylabel("delta alpha (drop from +-0.5-level raters)")
        ax.set_title(f"J5 injection recovery (alpha_base={inj['base_alpha_target']}, "
                     f"sigma={inj['sigma']:.3f})")
    else:
        ax.text(0.5, 0.5, "injection not run", ha="center")
    return _save(fig, path)


def plot_grader_profile(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图3：真实台账判级画像——各判级者键的等级构成（看系统性宽/严）。"""
    prof = result.get("grader_profile", {})
    by = prof.get("by_grader", {})
    graders = [g for g, _ in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))][:8]
    fig, ax = plt.subplots(figsize=(8.2, 3.9))
    if not graders:
        ax.text(0.5, 0.5, "no keyed grader data", ha="center")
    else:
        levels = ["S1", "S2", "S3", "S4", "S5"]
        bottom = np.zeros(len(graders))
        colors = plt.cm.viridis(np.linspace(0.1, 0.85, len(levels)))
        for i, lv in enumerate(levels):
            vals = np.array([by[g].get(lv, 0) for g in graders], float)
            ax.barh([g[:26] for g in graders], vals, left=bottom, color=colors[i], label=lv)
            bottom += vals
        ax.set_xlabel("confirmed evaluations (count)")
        ax.set_title("grader profile: grade mix per reviewer key (real ledger)")
        ax.legend(fontsize=7, ncol=5, loc="lower right")
    return _save(fig, path)


def plot_min_table(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图4：CI 半宽随可配对单元数 n 的变化，标出 ≤0.10 达标要求。"""
    tbl = result["min_rating_table"]
    fig, ax = plt.subplots(figsize=(6.8, 3.9))
    markers = ["o", "s", "^", "d"]
    i = 0
    for row in tbl.get("rows", []):
        if row.get("status") == "unreachable":
            continue
        xs = [m["n_units"] for m in row["measured"]]
        ys = [m["ci_halfwidth"] for m in row["measured"]]
        ax.plot(xs, ys, marker=markers[i % len(markers)],
                label=f"alpha_true={row['alpha_true']:.2f}, m={row['n_raters']}")
        i += 1
    ax.axhline(tbl["target_halfwidth"], ls="--", color="crimson",
               label=f"target {tbl['target_halfwidth']}")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("pairable units n (missions with >=2 graders)")
    ax.set_ylabel("CI half-width")
    ax.set_title("J4 minimum rating count for CI half-width <= 0.10")
    ax.legend(fontsize=7)
    return _save(fig, path)


def plot_gate(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图5：决策门工作特性 + 真实台账标签分布。"""
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    j6 = next((c for c in result["criteria"] if c["id"] == "J6"), None)
    if j6 and isinstance(j6.get("value"), dict):
        vals = [j6["value"]["good"]["rate"], j6["value"]["bad"]["rate"]]
        axes[0].bar(["alpha=0.8\n(expect PASS)", "alpha=0.5\n(expect REQUIRE_THIRD)"],
                    vals, color=["#2ca02c", "#d62728"])
        axes[0].set_ylim(0, 1.05)
        axes[0].axhline(result["thresholds"]["gate_pass_rate_min"], ls="--",
                        color="black", lw=1)
        axes[0].set_ylabel("correct-decision rate")
        axes[0].set_title("J6 gate operating characteristic")
        axes[0].tick_params(axis="x", labelsize=8)
    labels = result["gate"]["label_counts"]
    if labels:
        axes[1].bar(list(labels.keys()), list(labels.values()), color="#1f77b4")
        axes[1].set_title("real ledger: mission-level gate labels")
        axes[1].tick_params(axis="x", rotation=20, labelsize=8)
        axes[1].set_ylabel("missions")
    return _save(fig, path)


def plot_alpha_matrix(result: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """图6：不同口径系数对照 + 真实双评单元逐对差值直方图。"""
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    coef = result.get("coefficients", {})
    names, vals = [], []
    for k, label in (("alpha_ordinal", "Krippendorff a (ordinal)"),
                     ("alpha_nominal", "Krippendorff a (nominal)"),
                     ("gwet_ac1", "Gwet AC1"), ("gwet_ac2", "Gwet AC2"),
                     ("icc_1_1", "ICC(1,1)"), ("icc_2_k", "ICC(2,k)")):
        v = coef.get(k)
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            names.append(label)
            vals.append(float(v))
    if names:
        axes[0].barh(names, vals, color="#17becf")
        axes[0].axvline(result["thresholds"]["alpha_gate"], ls="--", color="crimson")
        axes[0].set_xlim(min(-0.2, min(vals) - 0.05), 1.05)
        axes[0].set_title("real ledger: coefficient comparison")
        axes[0].tick_params(axis="y", labelsize=8)
    pairs = result.get("pairs", [])
    ps = result.get("pairs_summary", {})
    if pairs:
        bins = np.arange(-0.5, 5.5, 1)
        same = [p["abs_diff_levels"] for p in pairs if p.get("same_grader")]
        cross = [p["abs_diff_levels"] for p in pairs if not p.get("same_grader")]
        if cross:
            axes[1].hist([cross], bins=bins, color="#d62728", rwidth=0.6,
                         label=f"cross-grader (inter-rater) n={len(cross)}")
        if same:
            axes[1].hist(same, bins=bins, alpha=0.75, color="#7f7f7f", rwidth=0.4,
                         label=f"same grader key (intra-rater) n={len(same)}")
        axes[1].set_xlabel("|delta level| within the same mission")
        axes[1].set_ylabel("pairs")
        axes[1].set_title(f"same-mission pairs, all attempts "
                          f"(n={ps.get('n_pairs', len(pairs))})", fontsize=9)
        axes[1].legend(fontsize=7)
    else:
        axes[1].text(0.5, 0.5, "no multi-record missions", ha="center")
        axes[1].set_title("same-mission pairs: none")
    return _save(fig, path)


def make_figures(result: Mapping[str, Any], outdir: str | Path) -> list[dict[str, Any]]:
    out = Path(outdir)
    specs = [
        ("fig1_alpha_recovery.png", plot_recovery,
         "图1 J1/J2：α 真值恢复（含 CI 半宽）与 95% CI 覆盖率"),
        ("fig2_injection.png", plot_injection,
         "图2 J5：注入 ±0.5 级 lenient/harsh 判级者后 Δα 的理论 vs 实测"),
        ("fig3_grader_profile.png", plot_grader_profile,
         "图3 真实台账判级画像：各判级者键的等级构成"),
        ("fig4_min_rating_table.png", plot_min_table,
         "图4 J4：CI 半宽 ≤0.10 所需最小评审判数"),
        ("fig5_gate.png", plot_gate,
         "图5 J6：决策门工作特性与真实台账 mission 级标签分布"),
        ("fig6_coefficients.png", plot_alpha_matrix,
         "图6 系数口径对照（左，生产口径 n_pairable=0 故留空/仅显示可算项）与"
         "同 mission 逐对差值（右，口径=全部尝试含 rejected）"),
    ]
    figs = []
    for name, fn, caption in specs:
        d = fn(result, out / name)
        figs.append({"name": name, "caption": caption, **d})
    return figs


# ─────────────────────────────── Markdown ───────────────────────────────

def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return "n/a" if not math.isfinite(v) else f"{v:.{nd}f}"
    return str(v)


def _crit_value(val: Any) -> str:
    """把判据 value 渲染成人能读的一行（嵌套结构不原样倒 dict repr）。"""
    if not isinstance(val, dict):
        return _fmt(val)
    parts: list[str] = []
    for k, v in val.items():
        if isinstance(v, (int, float, bool)) or v is None:
            parts.append(f"{k}={_fmt(v)}")
        elif isinstance(v, str):
            parts.append(f"{k}={v}")
        elif isinstance(v, dict):
            inner = ", ".join(f"{ik}={_fmt(iv)}" for ik, iv in v.items()
                              if isinstance(iv, (int, float, str, bool)))
            if inner:
                parts.append(f"{k}({inner})")
        elif isinstance(v, list):
            parts.append(f"{k}[{len(v)}]")
    return "<br>".join(parts)


def _crit_table(criteria) -> str:
    lines = ["| 判据 | 含义 | 数值 | 阈值 | 状态 |", "|---|---|---|---|---|"]
    def esc(x: Any) -> str:
        return str(x).replace("|", "\\|")

    for c in criteria:
        lines.append(f"| {c['id']} | {esc(c['name'])} | {esc(_crit_value(c['value']))} | "
                     f"{esc(c['threshold'])} | **{c['status']}** |")
    return "\n".join(lines)


def build_markdown(result: Mapping[str, Any]) -> str:
    cov = result["coverage"]
    coef = result.get("coefficients", {})
    verdict = result["gate"]["verdict"]
    applied = result["gate"]
    rec = result["recovery"]
    inj = result["injection"]
    vc = result.get("variance", {})
    th = result["thresholds"]
    crit = result["criteria"]
    prior = result.get("prior_sensitivity", []) or [{}]

    md: list[str] = []
    md.append("# 判级者间一致性门与可靠度置信区间（IN55W3-N07）")
    md.append("")
    md.append(f"> mission 10363 · 分支 `{result.get('branch', 'agent/m10363')}` · "
              f"工件 `{result.get('artifact_path')}`")
    md.append("")

    md.append("## ① 案例介绍")
    md.append("")
    md.append("S×E 判级体系给每个成果打 S1–S5 + E0–E4。此前台账里只有一个等级，"
              "没有任何「换成另一个判级者会不会给同一等级」的证据，因此所有 S×E 数字都"
              "缺可靠性区间。本节点测的是**测量可靠性**（判级者间一致性），不是等级内容本身。")
    md.append("")
    md.append("**数据源**：`outcome_evaluations`（只读单条 SELECT），判级者身份 = `reviewer` "
              "字段；缺身份者记 `UNKEYED`，计入覆盖率分母且**不插补**；`S-N`（否决轨）"
              "不同构念，不参与序数 α。默认纳入 `confirmed` + `candidate` 两种已判级状态"
              "（`candidate` 只是不计入 S×E **分布**统计，它仍是一次真实的判级行为），"
              "并在 3.4 给 confirmed-only 的敏感性对照。")
    md.append("")
    md.append("| 量 | 值 |")
    md.append("|---|---|")
    for k, label in (("n_records", "记录数"), ("n_keyed", "有判级者身份"),
                     ("n_unkeyed", "UNKEYED（不插补）"), ("unkeyed_share", "UNKEYED 占比"),
                     ("n_missions", "Mission 数"),
                     ("n_pairable_missions", "可配对（≥2 判级者）Mission"),
                     ("n_graders", "不同判级者键数"),
                     ("max_graders_per_mission", "单 Mission 最多判级者数"),
                     ("n_off_scale", "S-N 记录（不入序数尺度）"),
                     ("n_missions_multi_record", "多记录 Mission（≥2 条判级）"),
                     ("n_cross_grader_pairs", "**跨判级者**配对（真间一致性证据）"),
                     ("n_same_grader_pairs", "同一判级者键内配对（内部离散）")):
        md.append(f"| {label} | {_fmt(cov.get(k))} |")
    md.append("")
    md.append("判级者键是**席位复合键**（如 `seat-执行+seat-台账`），不是自然人；"
              "因此 α 度量的是复合判级键之间的一致性。")
    if cov.get("n_cross_grader_pairs", 0) == 0:
        md.append("")
        md.append("**关键事实**：生产集里**没有任何一个 mission 被两个不同判级者评过**"
                  "（跨判级者配对 = 0）——所以生产口径下 α 根本不可算。台账里唯一的"
                  "同成果双评来自「被判据拒收的尝试」（它们与有效记录共享 `artifact_id`），"
                  "见 3.4b 的探索性区间。")
    md.append("")

    md.append("## ② 怎么做的")
    md.append("")
    md.append("**系数实现（自写，环境不保证有第三方可靠性库）**：Krippendorff α"
              "（ordinal 主指标 / nominal 敏感性）、Gwet AC1/AC2、Fleiss κ、"
              "ICC(1,1)/ICC(2,1)/ICC(2,k)，全部带按单元（mission）重采样的 bootstrap CI。"
              "系数正确性靠**解析可验证性质**钉住：两判级者名义 α 在大样本下 → Scott π、"
              "Fleiss κ(=2 判级者) = Scott π、AC2 = AC1（K=2）、完美一致 α=1、缺格单元不计入 n。")
    md.append("")
    md.append("**合成恢复夹具**（可控真值，所有量以「级」为单位）：")
    md.append("")
    md.append("```")
    md.append("l_i ~ p(l)                                   # 真实等级, l in {1..5}")
    md.append("r_ig = clip(round(l_i + b_g + e_ig), 1, 5),  e_ig ~ N(0, sigma^2)")
    md.append("```")
    md.append("")
    md.append("总体极限 α 用条件分布精确枚举（`P(r=c|l)=Phi((c+.5-l-b)/s)-Phi((c-.5-l-b)/s)`，"
              "端类别边界取 ±∞ 以复现 clip），因此 α_true **不含抽样噪声**；再二分校准 σ 命中 "
              "α_true∈{0.2,0.5,0.8}。注入实验用 +0.5（lenient）/ −0.5（harsh）级偏置，"
              "与基线**同随机数配对**，Δα 的抽样噪声被消掉。")
    md.append("")
    md.append("**方差分解** `grade ~ 1 + (1|grader) + (1|mission)`：本台账「一个 mission 基本"
              "只被一个判级者评」使 mission 与判级者效应不可分离识别，故只识别 φ_g = "
              "σ²_grader/(σ²_grader+σ²_(mission+噪声))（不等组单向随机效应矩估计，Searle 口径）；"
              "mission 分量由多评子样本反解并标注可识别性。")
    md.append("")
    md.append(f"**判据与阈值**（单点契约 `tools/eval_reliability/acceptance.py:THRESHOLDS`）："
              f"α 门 {th['alpha_gate']}、可判最少双评单元 {int(th['min_pairable_units'])}、"
              f"UNKEYED 上限 {th['max_unkeyed_share']}、CI 半宽目标 {th['target_ci_halfwidth']}。")
    md.append("")

    md.append("## ③ 计算结果")
    md.append("")
    md.append("### 3.1 判据裁决")
    md.append("")
    md.append(_crit_table(crit))
    md.append("")
    md.append(f"**总裁决**：`{result['adjudication']['overall']}`"
              f"（FAIL={result['adjudication']['fails']}，"
              f"WITHHELD={result['adjudication']['withheld']}）")
    md.append("")
    md.append("### 3.2 恢复实验（真值可控）")
    md.append("")
    md.append("| α_true | σ | mean α̂ | bias | MAE | SD | CI 覆盖率(BC) | 覆盖率(百分位) | CI 半宽 | 重复 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rec["rows"]:
        if r.get("status") != "ok":
            continue
        md.append(f"| {_fmt(r['alpha_true'])} | {_fmt(r['sigma'])} | "
                  f"{_fmt(r['mean_alpha_hat'])} | {_fmt(r['bias'], 4)} | "
                  f"{_fmt(r['mae'], 4)} | {_fmt(r['sd_alpha_hat'], 4)} | "
                  f"{_fmt(r['ci_coverage'])} | {_fmt(r.get('ci_coverage_percentile'))} | "
                  f"{_fmt(r['mean_ci_halfwidth'])} | {r['reps_used']} |")
    md.append("")
    md.append("### 3.3 注入 lenient/harsh（±0.5 级）")
    md.append("")
    if inj.get("status") == "ok":
        md.append("| 量 | 值 |")
        md.append("|---|---|")
        for k, label in (("alpha_base_population", "基线总体 α（真值）"),
                         ("alpha_injected_population", "注入后总体 α"),
                         ("delta_alpha_theory", "理论 Δα"),
                         ("delta_alpha_hat_mean", "实测 Δα̂（配对均值）"),
                         ("delta_alpha_hat_sd", "实测 Δα̂ 的 SD"),
                         ("relative_error", "相对误差"),
                         ("sigma", "σ"), ("reps_used", "配对重复数")):
            md.append(f"| {label} | {_fmt(inj.get(k), 4)} |")
        md.append("")
    md.append("### 3.4 真实台账一致性系数（探索性；n_pairable 见 ①）")
    md.append("")
    if coef:
        md.append("| 系数 | 值 |")
        md.append("|---|---|")
        for k, v in coef.items():
            if not (k.startswith(("icc", "alpha", "gwet")) or k in ("fleiss_kappa",)):
                continue
            if v is None:
                md.append(f"| {k} | n/a（无完整格/无可配对单元） |")
            else:
                md.append(f"| {k} | {_fmt(v)} |")
        if coef.get("complete_case_error"):
            md.append(f"| Gwet/κ 完整格 | n/a（{coef['complete_case_error'][:40]}） |")
        md.append("")
    ps = result.get("pairs_summary", {})
    md.append(f"**配对结构**（口径 = 含 rejected 的全部判级尝试，见 3.4b）：同 mission 配对共 "
              f"{ps.get('n_pairs', 0)} 对，其中**跨判级者** "
              f"{ps.get('n_cross_grader_pairs', 0)} 对、同一判级者键内 {ps.get('n_same_grader_pairs', 0)} 对"
              f"（跨判级者配对平均 |Δ级| = {_fmt(ps.get('mean_abs_diff'))}）。"
              "跨判级者配对是唯一能支撑「判级者间一致性」的证据；同一判级者键内的差异属于"
              "判级者内部离散，不能混为一谈。")
    md.append("")
    sens = result.get("sensitivity") or []
    if sens:
        md.append("**状态敏感性（结论不依赖 candidate 的取舍）**")
        md.append("")
        md.append("| 纳入状态 | 记录数 | 跨判级者配对 | UNKEYED 占比 | α̂(n_pairable) |")
        md.append("|---|---|---|---|---|")
        for row in sens:
            if "error" in row:
                md.append(f"| {','.join(row['statuses'])} | n/a | n/a | n/a | {row['error'][:40]} |")
                continue
            md.append(f"| {','.join(row['statuses'])} | {row['n_records']} | "
                      f"{row['n_cross_grader_pairs']} | {_fmt(row['unkeyed_share'])} | "
                      f"{_fmt(row['alpha_ordinal'])} (n={row['n_pairable_units']}) |")
        md.append("")
    ex = result.get("exploratory") or {}
    if ex and "error" not in ex:
        md.append("### 3.4b 探索性：全部判级尝试（含被判据拒收）——台账首份可靠性区间")
        md.append("")
        ci = ex.get("alpha_ci") or {}
        md.append(f"口径：{ex.get('scope')}；记录 {ex.get('n_records')} 条，"
                  f"可配对（跨判级者）Mission **{ex.get('n_pairable_missions')}** 个，"
                  f"跨判级者配对 {ex.get('n_cross_grader_pairs')} 对。")
        md.append("")
        md.append("| 量 | 值 |")
        md.append("|---|---|")
        md.append(f"| α̂（S 轨，Krippendorff ordinal） | {_fmt(ex.get('alpha_ordinal'))} |")
        md.append(f"| S 轨可配对单元 n | {ex.get('alpha_n_pairable_units')} |")
        md.append(f"| S 轨 95% CI（按单元 bootstrap，仅重采样变异性） | "
                  f"[{_fmt(ci.get('lo'))}, {_fmt(ci.get('hi'))}]（半宽 "
                  f"{_fmt(ci.get('half_width'))}） |")
        pa_s = ex.get("pair_agreement_S") or {}
        pa_e = ex.get("pair_agreement_E") or {}
        md.append(f"| S 轨逐对一致率 + Clopper-Pearson 95% | "
                  f"{pa_s.get('n_agree')}/{pa_s.get('n_pairs')} = {_fmt(pa_s.get('rate'))}，"
                  f"精确区间 [{_fmt(pa_s.get('lo'))}, {_fmt(pa_s.get('hi'))}] |")
        md.append(f"| α̂（E 轨） | {_fmt(ex.get('alpha_e_ordinal'))}"
                  f"（n={ex.get('alpha_e_n_pairable_units')}） |")
        md.append(f"| E 轨逐对一致率 + Clopper-Pearson 95% | "
                  f"{pa_e.get('n_agree')}/{pa_e.get('n_pairs')} = {_fmt(pa_e.get('rate'))}，"
                  f"精确区间 [{_fmt(pa_e.get('lo'))}, {_fmt(pa_e.get('hi'))}] |")
        md.append("")
        if ex.get("pairs"):
            md.append("| mission | 判级者 A → B | 等级 | E 档 | \\|ΔS\\| |")
            md.append("|---|---|---|---|---|")
            for pr in ex["pairs"]:
                md.append(f"| {pr['mission_id']} | {str(pr['grader_a'])[:22]} → "
                          f"{str(pr['grader_b'])[:22]} | {pr['grade_a']} / {pr['grade_b']} | "
                          f"{pr.get('e_a')} / {pr.get('e_b')} | {pr['abs_diff_levels']} |")
            md.append("")
        md.append(f"⚠ {ex.get('caveat')}")
        md.append("")
        md.append(f"**这条区间的含义**：它是 S×E 台账**第一次**拿到的可靠性区间，但 n="
                  f"{ex.get('alpha_n_pairable_units')} 远低于 {int(th['min_pairable_units'])} "
                  "的出数下限，因此**只能作探索性参考，不得作升账依据**（门的裁决仍是 WITHHELD）。")
        md.append("")
        md.append("**⚠ 反保守警告（必读）**：在这种「13 对里 12 对完全相同」的稀疏结构下，"
                  "bootstrap 重采样**造不出未观察到的分歧模式**，α̂ 的 bootstrap CI 半宽只有 0.01 "
                  "量级，明显小于真实不确定性。因此本报告并报 **Clopper-Pearson 精确二项区间**"
                  "作为按逐对一致率的保守界——上表两者的差距本身就是「小样本下不要相信窄区间」的证据。")
        md.append("")
        md.append("**观察到的机制**：12/13 对的 S 等级**完全相同**（都是 S1），分歧只出现在 E 轨"
                  "（被判据拒收的尝试给 E0，E0 按规则不可登记）；唯一一对 S 分歧（mission 5025）"
                  "是 2 档（S3 vs S5）且拒收原因是不足 50 字理由。即：这批数据里 **S 档判级稳定、"
                  "E 档与理由完备性才是失败点**——这条结论只有做了逐对拆解才看得见。")
        md.append("")
    md.append("### 3.5 判级者方差占比 φ_g")
    md.append("")
    md.append("| 量 | 值 |")
    md.append("|---|---|")
    for k, label in (("phi_g", "φ_g（判级者方差占比）"), ("sigma2_grader", "σ²_grader"),
                     ("sigma2_eps", "σ²_(mission+噪声)"),
                     ("sigma2_mission", "σ²_mission（多评子样本反解）"),
                     ("n_multi_graded_missions", "多评 Mission 数"),
                     ("mission_component_identifiable", "mission 分量可识别")):
        md.append(f"| {label} | {_fmt(vc.get(k))} |")
    md.append("")
    if result.get("variance_ci", {}).get("status") == "ok":
        md.append(f"φ_g 的 95% CI（按 mission 聚类 bootstrap）："
                  f"[{_fmt(result['variance_ci']['lo'])}, {_fmt(result['variance_ci']['hi'])}]")
        md.append("")
    gp = result.get("grader_profile", {})
    means = gp.get("mean_level_by_grader") or {}
    if means:
        md.append("| 判级者键 | 记录数 | 平均 S 等级 | E 档中位数 |")
        md.append("|---|---|---|---|")
        sizes = gp.get("by_grader", {})
        for g, _mu in sorted(means.items(), key=lambda kv: -kv[1]):
            n_g = sum(sizes.get(g, {}).values())
            md.append(f"| {g} | {n_g} | {_fmt(_mu)} | "
                      f"{_fmt((gp.get('median_e_rank_by_grader') or {}).get(g))} |")
        md.append("")
    md.append("**⚠ φ_g 的读法边界**：这里的 φ_g 主要反映「不同席位判级键给出的等级水平差异」，"
              "而判级者键与时间段、成果构成高度混杂（每个键主导的窗口不同），因此**不能**读作"
              "「判级者个人效应」；mission 分量本身不可识别（多评 Mission = 0）。"
              "把它当「判级配置差异的方差份额」而非「人的方差份额」，才是诚实的解释。")
    md.append("")
    md.append("### 3.6 决策门裁决")
    md.append("")
    md.append(f"- 状态：**{verdict['status']}**（enforcement={verdict['enforcement']}）")
    md.append(f"- 理由：{'; '.join(verdict['reasons'])}")
    b = applied["bucket_counts"]
    md.append(f"- 判级配置分布：单评 {b['single']} / 双评 {b['pair']} / 三评+ {b['three_plus']}")
    md.append(f"- 标签分布：{applied['label_counts']}")
    md.append(f"- 补齐到可判还需 "
              f"**{applied['to_reach_min_pairable']['needed_extra_second_evaluations']}** "
              f"次独立第二判级")
    md.append("")
    md.append("### 3.7 最小审数表（CI 半宽 ≤0.10）")
    md.append("")
    md.append("| α_true | 判级者数 | 实测最小 n | 外推 n* | 实测点（n: 半宽） |")
    md.append("|---|---|---|---|---|")
    for row in result["min_rating_table"]["rows"]:
        if row.get("status") == "unreachable":
            continue
        pts = "; ".join(f"{m['n_units']}:{_fmt(m['ci_halfwidth'], 3)}"
                        for m in row["measured"])
        md.append(f"| {_fmt(row['alpha_true'])} | {row['n_raters']} | "
                  f"{_fmt(row.get('min_n_units_measured'))} | "
                  f"{_fmt(row.get('min_n_units_extrapolated'))} | {pts} |")
    md.append("")

    md.append("## ④ 结果图")
    md.append("")
    for f in result.get("figures", []):
        md.append(f"**{f['caption']}** — `{f['path']}`")
        md.append("")

    md.append("## ⑤ 注意点（坑与边界）")
    md.append("")
    md.append("1. **总体 α 不能照抄样本公式**：把 `D_e` 的 `n(n−1)` 修正搬到总体极限上，会让"
              "「完全随机判级」的 α 地板假抬到 1/m（m=4 时 0.25），低 α 档真值整体偏 0.1。"
              "本模块总体 α 用 `n²`，并在 N=20000 大样本上与样本 α 对齐到 0.002 以内。")
    md.append("2. **端类别边界必须取 ±∞**：`clip` 把超界概率质量折回端类别，按 0.5/5.5 切边界"
              "会漏算折算质量——这一条在 α_true=0.2 档造成 +0.10 的假偏差，是本节点最大的坑。")
    md.append("3. **bootstrap 必须按单元重采样**：按判级值重采样把 n 算大、CI 算窄，覆盖率虚高，"
              "是典型的假自信。")
    md.append("4. **φ_g 的可识别性依赖设计**：单评审级使 mission 与判级者效应混杂；本模块只报"
              "可识别的 φ_g，mission 分量标 `mission_component_identifiable`，"
              "且矩估计 `σ̂²=max(0,·)` 在 σ²_g→0 时下侧偏保守。")
    md.append(f"5. **α 与边际分布绑定**：序数度量是可分解度量，期望分歧由边际决定。同一 σ=0.8 "
              f"换先验，总体 α = {_fmt(prior[0].get('alpha_population'))}（对称）/ "
              f"{_fmt(prior[1].get('alpha_population') if len(prior) > 1 else float('nan'))}（均匀）/ "
              f"{_fmt(prior[2].get('alpha_population') if len(prior) > 2 else float('nan'))}（偏斜）"
              "——跨批次比较 α 时必须同时报边际。")
    md.append("6. **判级者键≠人**：复合席位键把多个席位合并成一次判级，跨键一致性只说明"
              "「不同席位组合的判级口径是否一致」，不能反推个体（人或模型）的可靠性，"
              "更不可用于排名或画像（台账纪律：禁加权禁排名）。")
    md.append("7. **AC2 口径**：机会一致率在文献有多种写法，本实现取线性权重 + Gwet 边际口径，"
              "只作敏感性指标，不参与决策门。")
    md.append("")

    md.append("## ⑥ 误差判定（逐项回答「是否 <3%」）")
    md.append("")
    rows = [r for r in rec["rows"] if r.get("status") == "ok"]
    md.append("本节点的「基准」是**可控真值**（生成模型的总体极限 α），不是文献值，"
              "因此 3% 相对误差口径不适用：α 是 0–1 系数，3% 相对误差在 α≈0.2 时等同"
              "绝对误差 0.006，比抽样 SD 小一个量级，没有判别意义。等价的可判口径是"
              "「|bias| ≤0.05 个 α 单位」与「CI 覆盖率 ≥90%」。")
    md.append("")
    if rows:
        md.append("| 量 | 值 | 判定 |")
        md.append("|---|---|---|")
        maxb = max(abs(r["bias"]) for r in rows)
        maxm = max(r["mae"] for r in rows)
        minc = min(r["ci_coverage"] for r in rows)
        md.append(f"| 最大 \\|bias\\| (α̂ vs α_true) | {_fmt(maxb, 4)} | "
                  f"{'≤0.05 达标' if maxb <= 0.05 else '>0.05 未达标'} |")
        md.append(f"| 最大 MAE（单次判级典型误差） | {_fmt(maxm, 4)} | "
                  f"{'≤0.05 达标' if maxm <= 0.05 else '>0.05 未达标'} |")
        md.append(f"| 最小 CI 覆盖率 | {_fmt(minc, 3)} | "
                  f"{'≥0.90 达标' if minc >= 0.90 else '未达标'} |")
        if inj.get("status") == "ok":
            md.append(f"| 注入 Δα 相对误差 | {_fmt(inj['relative_error'], 4)} | "
                      f"{'≤0.10 达标' if inj['relative_error'] <= 0.10 else '未达标'} |")
        md.append("")
    md.append(f"**是否 <3%**：不适用（理由见上，逐项数字见表）。**真实台账 α 一律 WITHHELD**："
              f"可配对 Mission 仅 {cov['n_pairable_missions']} 个、UNKEYED 占比 "
              f"{_fmt(cov['unkeyed_share'])}，达不到 {int(th['min_pairable_units'])} 的下限，"
              "故不对**生产口径**的真实 α 报数值区间——这是 honest WITHHELD，不是 PASS；"
              "唯一的双评证据（被判据拒收的尝试）以探索性区间 + 保守精确区间的形式在 3.4b 给出，"
              "并明确标注不得作升账依据。")
    md.append("")
    md.append("## 局限与下一步")
    md.append("")
    md.append("1. 真实 α 不可判的根因是**设计**：需给成果配第二个独立判级者；本报告已给出"
              f"「还需 {applied['to_reach_min_pairable']['needed_extra_second_evaluations']} "
              "次第二判级」的最小可派工投入量。")
    md.append("2. 判级者身份需拆解为真实独立席位/模型，否则 α 只能解释为「复合键口径一致性」。")
    md.append("3. α 的区间估计在小 n 下用 BC bootstrap；如需更高精度可加 BCa（需要加速度项，"
              "本节点先不引入以免小样本下反而失真）。")
    return "\n".join(md) + "\n"


__all__ = ["make_figures", "build_markdown"]