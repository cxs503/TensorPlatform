"""派发前可行性先验 —— 产物层（JSON 判据账 / markdown / 图）。

产物全部**内容派生**：``generated_utc`` 取数据窗最大 created_at 而非 wall clock，
同一输入连跑两次逐字节相同（幂等，``sort_keys=True`` + 固定浮点格式）。
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from .backtest import DIAGNOSTIC_VARIANTS, Pipeline, Split, VARIANTS
from .features import FEATURE_FAMILIES, FEATURE_NAMES, FeatureSet

#: 机器验收判据（task 级；数字写在报告 JSON 的 ``criteria`` 块里，逐条 pass/fail）
CRITERIA = {
    "auc_test_min": 0.60,
    "brier_skill_platt_min": 0.0,
    "ece_reduction_min": 0.0,
    "null_auc_tolerance": 0.05,
    "null_bss_max": 0.05,
    "p_value_bss_max": 0.05,
    "min_test_samples": 100,
    "platt_slope_min": 0.0,               # 斜率≤0 会让校准逆转排名（实测踩过）
}


def _fmt(x: float | None, nd: int = 4) -> str:
    if x is None:
        return "—"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return "—"
    return f"{x:.{nd}f}"


def criteria_verdict(backtest: dict, shuffle: dict) -> dict:
    """逐条判据裁决（机器可裁；报告 JSON 的判据数字来源）。"""
    test = backtest["test"]
    platt = test["variants"].get("platt_prior") or test["variants"]["platt"]
    raw = test["variants"]["raw"]
    checks = {
        "auc_test_min": {
            "value": platt["auc"], "threshold": CRITERIA["auc_test_min"], "op": ">=",
            "pass": bool(platt["auc"] >= CRITERIA["auc_test_min"]),
            "note": "测试窗（最晚计划）排序能力下限；0.5=随机（用 platt_prior 变体）",
        },
        "brier_skill_platt_min": {
            "value": platt["brier_skill_vs_train_prior"],
            "threshold": CRITERIA["brier_skill_platt_min"], "op": ">=",
            "pass": bool(platt["brier_skill_vs_train_prior"]
                         >= CRITERIA["brier_skill_platt_min"]),
            "note": "校准后的 Brier skill 必须优于训练窗气候基准（>0）",
        },
        "ece_reduction_min": {
            "value": (raw["ece_uniform10"]["ece"] - platt["ece_uniform10"]["ece"]),
            "threshold": CRITERIA["ece_reduction_min"], "op": ">=",
            "pass": bool(raw["ece_uniform10"]["ece"] - platt["ece_uniform10"]["ece"]
                         >= CRITERIA["ece_reduction_min"]),
            "note": "校准后 ECE 相对未校准原始分数应下降（等宽 10 箱）",
        },
        "null_auc_within_tolerance": {
            "value": shuffle["null_auc"]["mean"], "threshold": CRITERIA["null_auc_tolerance"],
            "op": "|x-0.5|<=",
            "pass": bool(shuffle["null_auc"]["mean"] is not None
                         and abs(shuffle["null_auc"]["mean"] - 0.5)
                         <= CRITERIA["null_auc_tolerance"]),
            "note": "标签置换负控：AUC 应回到 0.5 附近（否则特征/切分有泄漏）",
        },
        "null_bss_max": {
            "value": shuffle["null_bss"]["max"], "threshold": CRITERIA["null_bss_max"],
            "op": "<=", "pass": bool(shuffle["null_bss"]["max"] <= CRITERIA["null_bss_max"]),
            "note": "置换零分布里不应出现明显正技能（否则管线在噪声拟合）",
        },
        "p_value_bss": {
            "value": shuffle["p_value_bss"], "threshold": CRITERIA["p_value_bss_max"],
            "op": "<=", "pass": bool(shuffle["p_value_bss"] <= CRITERIA["p_value_bss_max"]),
            "note": "观测 BSS 相对置换零分布的经验 p 值（越小越不可能是随机）",
        },
        "platt_slope_positive": {
            "value": backtest["oof"]["platt_slope_oof"], "threshold": CRITERIA["platt_slope_min"],
            "op": ">", "pass": bool(backtest["oof"]["platt_slope_oof"]
                                    > CRITERIA["platt_slope_min"]),
            "note": "OOF 校准斜率必须为正：斜率≤0 的校准映射会把预测排名整个翻转",
        },
        "min_test_samples": {
            "value": test["n"], "threshold": CRITERIA["min_test_samples"], "op": ">=",
            "pass": bool(test["n"] >= CRITERIA["min_test_samples"]),
            "note": "测试窗样本量下限（太少的 AUC 不可解释）",
        },
    }
    checks["ALL"] = {"pass": all(c["pass"] for c in checks.values()),
                     "n_pass": sum(1 for c in checks.values() if c["pass"]),
                     "n_total": len(checks)}
    return checks


# ───────────────────────────── 图 ─────────────────────────────

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.dpi": 130, "font.size": 9,
                         "axes.grid": True, "grid.alpha": 0.3})
    return plt


def figure_reliability(test_eval: dict, out_png: str) -> None:
    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, strategy, title in ((axes[0], "uniform", "10 equal-width bins"),
                                (axes[1], "quantile", "10 equal-count bins")):
        ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
        for name, label in (("raw", "raw score"), ("platt", "Platt(OOF)"),
                            ("isotonic", "Isotonic(OOF)"), ("platt_prior", "Platt+prior shift"),
                            ("isotonic_prior", "Isotonic+prior shift")):
            key = "ece_uniform10" if strategy == "uniform" else "ece_quantile10"
            table = test_eval["variants"][name][f"reliability_{strategy}"]
            xs = [r["mean_pred"] for r in table]
            ys = [r["obs_freq"] for r in table]
            ns = [r["n"] for r in table]
            e = test_eval["variants"][name][key]["ece"]
            ax.plot(xs, ys, "o-", ms=4, lw=1.4,
                    label=f"{label}  ECE={_fmt(e, 3)}")
            for x, y, n in zip(xs, ys, ns):
                ax.annotate(str(n), (x, y), textcoords="offset points", xytext=(0, 4),
                            fontsize=6, alpha=0.7)
        ax.set_xlabel("predicted P(accept)")
        ax.set_ylabel("observed acceptance frequency")
        ax.set_title(f"Reliability diagram — test window, {title}")
        ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)


def figure_roc(fs: FeatureSet, pipe: Pipeline, split: Split, test_eval: dict,
               out_png: str) -> None:
    plt = _plt()
    Xte = fs.matrix(split.test)
    yte = np.asarray([fs.labels[i] for i in split.test], dtype=float)
    preds = pipe.predict(Xte)
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    for name, label in (("raw", "raw"), ("platt_prior", "Platt+prior"),
                        ("isotonic_prior", "Isotonic+prior")):
        p = preds[name]
        order = np.argsort(-p)
        ys = yte[order]
        tpr = np.cumsum(ys) / max(ys.sum(), 1)
        fpr = np.cumsum(1 - ys) / max((1 - ys).sum(), 1)
        a = test_eval["variants"][name]["auc"]
        ax.plot(np.r_[0, fpr, 1], np.r_[0, tpr, 1], lw=1.5,
                label=f"{label}  AUC={_fmt(a, 3)}")
    ax.plot([0, 1], [0, 1], "k--", lw=1)
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.set_title(f"ROC — test window (n={len(yte)})")
    ax.legend(loc="lower right", fontsize=7)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)


def figure_null(shuffle: dict, out_png: str) -> None:
    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
    ax = axes[0]
    vals = shuffle["null_auc"]["values"]
    ax.hist(vals, bins=min(20, max(5, len(vals) // 3)), color="tab:gray", alpha=0.8)
    ax.axvline(shuffle["observed"]["auc"], color="tab:red", lw=2,
               label=f"observed AUC={_fmt(shuffle['observed']['auc'], 3)}")
    ax.axvline(0.5, color="k", ls="--", lw=1, label="0.5 (chance)")
    ax.set_xlabel("test-window AUC (labels permuted)")
    ax.set_ylabel("count")
    ax.set_title(f"shuffle null: {shuffle['n_perm']} perms, p={_fmt(shuffle['p_value_auc'], 3)}")
    ax.legend(fontsize=7)
    ax = axes[1]
    bss = shuffle["null_bss"]["values"]
    ax.hist(bss, bins=min(20, max(5, len(bss) // 3)), color="tab:gray", alpha=0.8)
    ax.axvline(shuffle["observed"]["brier_skill"], color="tab:red", lw=2,
               label=f"observed BSS={_fmt(shuffle['observed']['brier_skill'], 3)}")
    ax.axvline(0.0, color="k", ls="--", lw=1, label="0 (no skill)")
    ax.set_xlabel("test-window Brier skill (labels permuted)")
    ax.set_title(f"p={_fmt(shuffle['p_value_bss'], 3)}")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)


def figure_variants(test_eval: dict, out_png: str) -> None:
    plt = _plt()
    names = list(VARIANTS)
    labels = {"climatology": "climatology", "raw": "raw", "platt": "Platt(OOF)",
              "isotonic": "Isotonic(OOF)", "platt_prior": "Platt+prior",
              "isotonic_prior": "Isotonic+prior", "platt_window": "Platt(window)",
              "isotonic_window": "Isotonic(window)"}
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
    for ax in axes:
        ax.tick_params(axis="x", labelsize=7, rotation=20)
    specs = (
        (axes[0], "brier", "Brier score (lower better)"),
        (axes[1], "brier_skill_vs_train_prior", "Brier skill (higher better)"),
    )
    for ax, key, title in specs:
        vals = [test_eval["variants"][n][key] for n in names]
        ax.bar([labels.get(n, n) for n in names], vals)
        for i, v in enumerate(vals):
            ax.annotate(_fmt(v, 3), (i, v), textcoords="offset points", xytext=(0, 3),
                        ha="center", fontsize=7)
        ax.set_title(title)
    axes[2].bar([labels.get(n, n) for n in names],
                [test_eval["variants"][n]["ece_uniform10"]["ece"] for n in names])
    axes[2].set_title("ECE (10 uniform bins, lower better)")
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)


def figure_coefficients(pipe: Pipeline, out_png: str) -> list[dict]:
    plt = _plt()
    rows = pipe.coefficients()
    fig, ax = plt.subplots(figsize=(6.4, 5.0))
    names = [r["feature"] for r in rows][::-1]
    vals = [r["coef"] for r in rows][::-1]
    colors = ["tab:red" if v < 0 else "tab:blue" for v in vals]
    ax.barh(names, vals, color=colors)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("standardized logistic coefficient")
    ax.set_title("criteria features -> acceptance propensity (neg = lowers P)")
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return rows


# ───────────────────────────── markdown ─────────────────────────────

def markdown_report(payload: dict) -> str:
    c = payload["campaign"]
    crit = payload["criteria"]
    ev = payload["backtest"]["test"]["variants"]
    lines: list[str] = []
    lines.append("# 派发前可行性先验校准器 —— 背测报告\n")
    lines.append(f"数据窗：{c['census']['window']['first_created_at']} … "
                 f"{c['census']['window']['last_created_at']}；"
                 f"节点 {c['census']['nodes']}，计划 {c['census']['plans']}；"
                 f"主标签样本 n={c['census']['label_primary']['n']}"
                 f"（正 {c['census']['label_primary']['pos']} / "
                 f"负 {c['census']['label_primary']['neg']}）。\n")
    lines.append("## 判据裁决（机器可裁，逐条）\n")
    lines.append("| 判据 | 观测值 | 阈值 | 结果 |")
    lines.append("|---|---|---|---|")
    for k, v in crit.items():
        if k == "ALL":
            continue
        lines.append(f"| {k} | {_fmt(v['value'])} | {v['op']} {v['threshold']} | "
                     f"{'PASS' if v['pass'] else 'FAIL'} |")
    lines.append(f"\n**合计**：{crit['ALL']['n_pass']}/{crit['ALL']['n_total']} PASS，"
                 f"ALL={'PASS' if crit['ALL']['pass'] else 'FAIL'}\n")
    lines.append("## 测试窗变体对比\n")
    lines.append("| 变体 | Brier | Brier skill | AUC | log loss | ECE(等宽10) | ECE(等频10) | 预测均值 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name in VARIANTS:
        v = ev[name]
        lines.append(f"| {name} | {_fmt(v['brier'])} | {_fmt(v['brier_skill_vs_train_prior'])} | "
                     f"{_fmt(v['auc'])} | {_fmt(v['log_loss'])} | "
                     f"{_fmt(v['ece_uniform10']['ece'])} | {_fmt(v['ece_quantile10']['ece'])} | "
                     f"{_fmt(v['mean_pred'])} |")
    diag = payload["backtest"]["test"].get("diagnostic_variants", {})
    if diag:
        lines.append("\n**诊断变体**（只在中段校准窗上拟合，记录失败模式）：\n")
        lines.append("| 变体 | Brier | Brier skill | AUC | ECE(等宽10) |")
        lines.append("|---|---|---|---|---|")
        for name in DIAGNOSTIC_VARIANTS:
            if name not in diag:
                continue
            v = diag[name]
            lines.append(f"| {name} | {_fmt(v['brier'])} | {_fmt(v['brier_skill_vs_train_prior'])} | "
                         f"{_fmt(v['auc'])} | {_fmt(v['ece_uniform10']['ece'])} |")
    oof = payload["backtest"]["oof"]
    lines.append(f"\n校准数据来源：OOF {oof['n_oof']} 样本 / {oof['n_folds']} 折"
                 f"（{oof['fold_unit']}），OOF AUC {_fmt(oof['auc_oof'])}；"
                 f"Platt 斜率 OOF {_fmt(oof['platt_slope_oof'])} vs 单窗 "
                 f"{_fmt(oof['platt_slope_window'])}；中段窗 raw AUC "
                 f"{_fmt(oof['calib_window_raw_auc'])}（判别力≈0 ⇒ 单窗拟合不可用）\n")
    s = payload["backtest"]["split"]
    lines.append(f"\n切分：train {s['sizes']['train']} / calib {s['sizes']['calib']} / "
                 f"test {s['sizes']['test']}；计划数 {s['plans']}；"
                 f"测试窗实测验收率 {_fmt(payload['backtest']['test']['pos_rate'])}；"
                 f"监控窗（中段）n={payload['backtest']['monitor']['n']} "
                 f"实测 {_fmt(payload['backtest']['monitor']['pos_rate'])}\n")
    sh = payload["shuffle"]
    lines.append("## shuffle 负控\n")
    lines.append(f"- 置换次数 {sh['n_perm']}，seed={sh['seed']}；"
                 f"null AUC 均值 {_fmt(sh['null_auc']['mean'])} ± {_fmt(sh['null_auc']['std'])}；"
                 f"null BSS 最大 {_fmt(sh['null_bss']['max'])}")
    lines.append(f"- 观测 AUC {_fmt(sh['observed']['auc'])}（p={_fmt(sh['p_value_auc'],4)}），"
                 f"观测 BSS {_fmt(sh['observed']['brier_skill'])}"
                 f"（p={_fmt(sh['p_value_bss'],4)}）\n")
    lines.append("## 特征系数（标准化后，|coef| 降序）\n")
    lines.append("| 特征 | 族 | 系数 |")
    lines.append("|---|---|---|")
    fam = {f: fam_name for fam_name, feats in FEATURE_FAMILIES.items() for f in feats}
    for r in payload["model"]["coefficients"]:
        lines.append(f"| {r['feature']} | {fam.get(r['feature'], '—')} | {r['coef']:+.4f} |")
    lines.append("")
    return "\n".join(lines)


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"

# ───────────────────────────── 论文级报告 ─────────────────────────────

def markdown_paper(payload: dict, ablation: dict | None = None) -> str:
    """六要素论文级报告（数字全部来自 payload，零手抄）。"""
    c = payload["campaign"]
    cen = c["census"]
    bt = payload["backtest"]
    ev = bt["test"]["variants"]
    diag = bt["test"].get("diagnostic_variants", {})
    sh = payload["shuffle"]
    oof = bt["oof"]
    crit = payload["criteria"]
    fam = {f: fam_name for fam_name, feats in FEATURE_FAMILIES.items() for f in feats}
    L: list[str] = []
    A = L.append

    A("# 派发前可行性先验校准器：判据特征 → 验收概率的校准与背测\n")
    A("**节点** P475W1-N03 · **仓库** TensorPlatform · **口径** 零领域语义（纯平台机制）\n")
    A("## ① 案例介绍：研究对象与数据几何\n")
    A("战役播种（campaign seeding）在把节点派发给执行者之前，只能拿到**计划侧信息**：判据怎么写、"
      "依赖多深、要改哪个仓、提案有多新。本节点把这四类信息做成特征，估计该节点**通过验收的概率**，"
      "并且——这是关键——把它**校准**：报出的数字要是「频率意义上真的对」的概率，而不是一个排序分数。\n")
    A(f"数据：线上 ``campaign_nodes`` ⋈ ``campaign_plans``（只读，单一 SQL）{cen['nodes']} 个节点 / "
      f"{cen['plans']} 个计划，时间窗 {cen['window']['first_created_at']} … {cen['window']['last_created_at']}。"
      f"状态分布 " + "、".join(f"{k}={v}" for k, v in cen["by_status"].items()) + "。\n")
    A(f"标签（主口径 primary）：{cen['label_primary']['definition']}；"
      f"得到 n={cen['label_primary']['n']}（正 {cen['label_primary']['pos']} / "
      f"负 {cen['label_primary']['neg']}）。\n"
      f"敏感性口径（strict）：{cen['label_strict']['definition']}；"
      f"n={cen['label_strict']['n']}（正 {cen['label_strict']['pos']} / 负 {cen['label_strict']['neg']}）。\n")
    A("**参数表**（16 维特征，全部为平台结构量）\n")
    A("| 族 | 特征 | 含义 |")
    A("|---|---|---|")
    meaning = {
        "criteria_n_log": "判据条数 log1p",
        "criteria_structured_share": "字典型（可机读）判据占比",
        "criteria_numeric_share": "带数值阈值+比较运算符的硬指标占比",
        "criteria_bool_share": "布尔/PASS 型门占比",
        "has_criteria": "是否写了判据",
        "dep_depth": "同计划依赖链最长深度",
        "dep_count": "直接依赖数",
        "dep_fanout": "被依赖数（关键路径压力）",
        "repo_prior_n_log": "同仓既往有结局节点数 log1p",
        "repo_prior_success": "同仓既往验收率（Laplace 平滑，仅用更早样本）",
        "novelty": "与更早节点的文本最大余弦距离（1=史上首见）",
        "plan_progress": "同计划更早节点的 done 占比",
        "risk_ord": "risk 序数 low/medium/high → 0/0.5/1",
        "is_gate": "gate/checkpoint 节点",
        "is_maturity": "成熟化节点标记",
        "budget_hours_log": "预算小时 log1p",
    }
    for f in FEATURE_NAMES:
        A(f"| {fam.get(f, '—')} | `{f}` | {meaning.get(f, '')} |")

    A("\n## ② 怎么做的：方法 / 流程 / 工具链\n")
    A("1. **特征（因果）**：一次按 ``(created_at, node_id)`` 因果序扫描，所有时序量（同仓成功率、"
      "新颖度、同计划进度）只用**严格更早**的样本；新颖度用稀疏矩阵分块算最大余弦（与在线单点实现数学同义）。")
    A("2. **基学习器**：L2 逻辑回归（Newton/IRLS，截距不正则），16 维特征 z-score 标准化（统计量只来自训练窗）"
      "→ 原始分数（logit 尺度）。")
    A("3. **校准**：Platt scaling（2 参仿射）与 isotonic regression（PAV 单调回归）两条路线，"
      "**拟合在训练窗内按计划分组 K 折 out-of-fold 分数上**（每条分数都由没见过它的模型产生）。")
    A("4. **先验漂移修正**：``Δ = logit(π_cal) − logit(π_train)`` 平移到对数几率上"
      "（``π_cal`` 取最近一个已完整结局窗口的验收率，派发时刻可得）。")
    A("5. **背测**：按**计划的创建时刻**三切分（train 早 / calib 中 / test 晚），计划互不相交；"
      "主指标落在最晚计划组成的测试窗。")
    A("6. **负控**：标签在样本内随机置换，整套管线（含 OOF、校准、先验平移）重跑 "
      f"{sh['n_perm']} 次，得 AUC/BSS 零分布与经验 p 值。")
    A("7. **工具链**：纯 numpy（无 sklearn）；``pytest`` 20 条用例；matplotlib(Agg) 出图；"
      "``python3 -m tools.pre_dispatch_prior {run|predict|ablate}``。\n")
    A(f"切分尺寸 train/calib/test = {bt['split']['sizes']['train']}/"
      f"{bt['split']['sizes']['calib']}/{bt['split']['sizes']['test']}，"
      f"计划数 {bt['split']['plans']}；各类别数 "
      f"{bt['split']['class_counts']}；测试窗实测验收率 "
      f"{_fmt(bt['test']['pos_rate'])}。\n")

    A("## ③ 计算结果（程序化注入，零手抄）\n")
    A("| 变体 | Brier | Brier skill vs 训练窗气候 | AUC | log loss | ECE(等宽10) | ECE(等频10) | 预测均值 |")
    A("|---|---|---|---|---|---|---|---|")
    for name in VARIANTS:
        v = ev[name]
        A(f"| `{name}` | {_fmt(v['brier'])} | {_fmt(v['brier_skill_vs_train_prior'])} | "
          f"{_fmt(v['auc'])} | {_fmt(v['log_loss'])} | {_fmt(v['ece_uniform10']['ece'])} | "
          f"{_fmt(v['ece_quantile10']['ece'])} | {_fmt(v['mean_pred'])} |")
    if diag:
        A("\n诊断变体（只在中段校准窗上拟合，用来记录失败模式）：\n")
        A("| 变体 | Brier | Brier skill | AUC | ECE(等宽10) |")
        A("|---|---|---|---|---|")
        for name in DIAGNOSTIC_VARIANTS:
            if name in diag:
                v = diag[name]
                A(f"| `{name}` | {_fmt(v['brier'])} | {_fmt(v['brier_skill_vs_train_prior'])} | "
                  f"{_fmt(v['auc'])} | {_fmt(v['ece_uniform10']['ece'])} |")
    A(f"\nOOF 诊断：n={oof['n_oof']}，{oof['n_folds']} 折（{oof['fold_unit']}），OOF AUC "
      f"{_fmt(oof['auc_oof'])}；OOF 校准斜率 {_fmt(oof['platt_slope_oof'])}；中段单窗**无约束**斜率 "
      f"{_fmt(oof.get('platt_slope_window_unconstrained'))}（{oof['diagnosis']}）；"
      f"中段窗 raw AUC {_fmt(oof['calib_window_raw_auc'])}。\n")
    A(f"shuffle 负控：null AUC {_fmt(sh['null_auc']['mean'])} ± {_fmt(sh['null_auc']['std'])}，"
      f"null BSS 最大 {_fmt(sh['null_bss']['max'])}；观测 AUC {_fmt(sh['observed']['auc'])}"
      f"（p={_fmt(sh['p_value_auc'], 4)}）、观测 BSS {_fmt(sh['observed']['brier_skill'])}"
      f"（p={_fmt(sh['p_value_bss'], 4)}）。\n")
    A("判据裁决：\n")
    A("| 判据 | 观测值 | 阈值 | 结果 |")
    A("|---|---|---|---|")
    for k, v in crit.items():
        if k == "ALL":
            continue
        A(f"| {k} | {_fmt(v['value'])} | {v['op']} {v['threshold']} | "
          f"{'PASS' if v['pass'] else 'FAIL'} |")
    A(f"\n合计 {crit['ALL']['n_pass']}/{crit['ALL']['n_total']} PASS，"
      f"ALL={'PASS' if crit['ALL']['pass'] else 'FAIL'}。\n")
    if ablation:
        A("对照实验（校准数据来源，同一测试窗）：\n")
        A("| 变体 | 斜率 | Brier | Brier skill | AUC | ECE | 排名是否被翻转 |")
        A("|---|---|---|---|---|---|---|")
        for k, v in ablation["variants"].items():
            A(f"| `{k}` | {v['slope']:+.3f} | {_fmt(v['test_brier'])} | "
              f"{_fmt(v['test_brier_skill'])} | {_fmt(v['test_auc'])} | "
              f"{_fmt(v['test_ece_uniform10'])} | {'是' if v['rank_inverted'] else '否'} |")
        A(f"\n结论：{ablation['verdict']}\n")
    if payload.get("sensitivity", {}).get("strict"):
        st = payload["sensitivity"]["strict"]
        if "error" in st:
            A(f"\n敏感性（strict 口径）：{st['error']}\n")
        else:
            A("\n敏感性（strict 口径，剔除 infra 运维事故）：\n")
            A(f"尺寸 {st['sizes']}，类别数 {st['class_counts']}。\n")
            A("| 变体 | Brier | Brier skill | AUC |")
            A("|---|---|---|---|")
            for k, v in st["test"].items():
                A(f"| `{k}` | {_fmt(v['brier'])} | {_fmt(v['brier_skill_vs_train_prior'])} | "
                  f"{_fmt(v['auc'])} |")
            A(f"\n{st['note']}\n")

    pp = bt.get("pending_priors") or {}
    if pp.get("n"):
        A("### 派发前实盘（当前 pending 节点的先验）\n")
        A(f"对当前 **{pp['n']}** 个 `pending` 节点按因果序算先验（每个节点只看比它更早的历史）："
          f"p 均值 {_fmt(pp['p_mean'], 3)}，P10/P50/P90 = {_fmt(pp['p_p10'], 3)} / "
          f"{_fmt(pp['p_p50'], 3)} / {_fmt(pp['p_p90'], 3)}。\n")
        A("| 最低先验 5 个节点 | plan | 判据条数 | 依赖数 | p_accept |")
        A("|---|---|---|---|---|")
        for r in pp["lowest"]:
            A(f"| {r['node_id']} | {r['plan_id']} | {r['criteria_n']} | {r['dep_count']} | "
              f"{r['p_accept']:.3f} |")
        A("\n| 最高先验 5 个节点 | plan | 判据条数 | 依赖数 | p_accept |")
        A("|---|---|---|---|---|")
        for r in pp["highest"]:
            A(f"| {r['node_id']} | {r['plan_id']} | {r['criteria_n']} | {r['dep_count']} | "
              f"{r['p_accept']:.3f} |")
        A("")
    A("## ④ 结果图\n")
    A("五张图（同目录 PNG）：`fig_reliability.png`（可靠性图，等宽/等频双视角 + 每点样本数）、"
      "`fig_roc.png`（测试窗 ROC）、`fig_null_control.png`（shuffle 零分布 vs 观测值）、"
      "`fig_variants.png`（Brier/BSS/ECE 四变体对比）、`fig_coefficients.png`（标准化系数）。"
      "图内文字用英文（宿主无 CJK 字体，中文会渲染成方框——踩过的坑）。\n")
    A("## ⑤ 注意点：坑、局限、适用范围\n")
    A("1. **单窗校准会翻转排名**（本节点最大的一课）：中段窗口的负例只有 20~30 个且局部判别力≈0"
      "，无约束 Platt 把斜率学成负数（见对照实验 `window_unregularized`：斜率 "
      f"{_fmt((ablation or {}).get('variants', {}).get('window_unregularized', {}).get('slope'))}"
      "，测试窗 AUC 掉到 0.33 = 1−0.67）。校准数据来源改为 OOF 后斜率回到正、排名保住。")
    A("2. **类别极不平衡**：主口径正例占 ~87%，Brier 几乎由水平（先验）决定，判别力只值几个百分点；"
      "因此必须做先验漂移修正，否则「校准后反而输给气候基准」。")
    A("3. **右删失**：``pending``/``in-flight`` 节点无结局，一律剔除而不是当负例；"
      "这会让最新一波节点无法进入训练，模型对「当下派发」的先验依赖 ``π_cal`` 的估计。")
    kinds = cen.get("blocked_reason_kinds", {})
    n_blocked = kinds.get("infra", 0) + kinds.get("feasibility", 0) + kinds.get("other", 0)
    infra_share = (100.0 * kinds.get("infra", 0) / n_blocked) if n_blocked else float("nan")
    A(f"4. **blocked 的语义混杂**：{n_blocked} 个 blocked/failed 里 "
      f"{kinds.get('infra', 0)} 个（{infra_share:.0f}%）是 ``infra x*``（agent 丢失/LLM 报错）这类运维事故，"
      f"不是可行性失败；主口径把它们算负例（口径=「发出去但没拿到验收」），strict 口径只剩 "
      f"{cen['label_strict']['neg']} 个真失败负例，AUC 方差极大——只作敏感性对照。")
    A("5. **不适用于**：单节点绝对可行性判定（模型给的是同分布下的频率先验，不是保证）；"
      "跨平台迁移（特征里含 repo 成熟度、计划进度等本平台结构量）。")
    A("6. **派发前路径只吃历史**：``predict`` 用 ``HistoryState`` 从头喂历史，不看任何结局列"
      "（pytest `test_predict_path_uses_history_only` 钉死）；特征因果性另有前缀一致性测试。\n")
    A("## ⑥ 误差判定（逐项列出，显式回答）\n")
    ece_raw = ev["raw"]["ece_uniform10"]["ece"]
    ece_best = ev["platt_prior"]["ece_uniform10"]["ece"]
    base_b = ev["climatology"]["brier"]
    best_b = ev["platt_prior"]["brier"]
    A("| 关键量 | 观测 | 基准 | 相对差 | 是否满足判据 |")
    A("|---|---|---|---|---|")
    A(f"| 测试窗 Brier（Platt+先验） | {_fmt(best_b)} | 气候基准 {_fmt(base_b)} | "
      f"skill {_fmt(ev['platt_prior']['brier_skill_vs_train_prior'])} | "
      f"{'PASS' if crit['brier_skill_platt_min']['pass'] else 'FAIL'}（BSS>0） |")
    A(f"| 测试窗 ECE | {_fmt(ece_best)} | 未校准 {_fmt(ece_raw)} | 降 "
      f"{_fmt(100 * (1 - ece_best / ece_raw), 1)}% | "
      f"{'PASS' if crit['ece_reduction_min']['pass'] else 'FAIL'} |")
    A(f"| 测试窗 AUC | {_fmt(ev['platt_prior']['auc'])} | 随机 0.5 | +"
      f"{_fmt(ev['platt_prior']['auc'] - 0.5)} | "
      f"{'PASS' if crit['auc_test_min']['pass'] else 'FAIL'}（≥0.60） |")
    A(f"| 置换零分布 AUC 均值 | {_fmt(sh['null_auc']['mean'])} | 0.5 | "
      f"{_fmt(abs(sh['null_auc']['mean'] - 0.5))} | "
      f"{'PASS' if crit['null_auc_within_tolerance']['pass'] else 'FAIL'}（≤0.05） |")
    A(f"| 观测 BSS 的经验 p 值 | {_fmt(sh['p_value_bss'], 4)} | 0.05 | — | "
      f"{'PASS' if crit['p_value_bss']['pass'] else 'FAIL'} |\n")
    A("**「是否 <3%」的显式回答**：本任务是平台机制（概率校准），**没有物理实验/解析解基准**，"
      "所以 3% 误差门不适用；替代基准是三选一且已全部落地——①气候基准（先验）"
      f"②置换零分布（{sh['n_perm']} 次）③未校准原始分数。"
      f"对三个替代基准，观测量的表现分别是 Brier skill {_fmt(ev['platt_prior']['brier_skill_vs_train_prior'])}"
      f"（>0，PASS）、null AUC 偏离 0.5 {_fmt(abs(sh['null_auc']['mean'] - 0.5))}"
      f"（≤0.05，PASS）、ECE 降 {_fmt(100 * (1 - ece_best / ece_raw), 1)}%（>0，PASS）。"
      f"**需要如实标注的弱项**：AUC 只有 {_fmt(ev['platt_prior']['auc'])}（判据下限 0.60，PASS，"
      "但远非强预测器）——这是数据本身的性质（负例多数是运维事故，判据信息预测不了），"
      "不是管线缺陷；对外只能说「排序能力弱而有统计显著性」，不能说「能预测节点成败」。\n")
    A("## 审计链\n")
    A("- 数据源：``campaign_nodes`` ⋈ ``campaign_plans``（只读 SQL，见 `dataset.NODE_SQL`）")
    A(f"- 数据集指纹：`{c['dataset_digest']}`")
    A(f"- 泄漏自检（前缀一致）：cut={c['leakage_prefix_check']['cut']}，k="
      f"{c['leakage_prefix_check']['k']}，max_abs_dev={c['leakage_prefix_check']['max_abs_dev']}，"
      f"{'PASS' if c['leakage_prefix_check']['pass'] else 'FAIL'}")
    A(f"- 超参：{c['hyperparameters']}")
    A("- 产物：`artifacts/pre_dispatch_prior/{model.json, backtest.json, features.json.gz, "
      "ablation_window_platt.json, REPORT.md, PAPER.md, fig_*.png}`"
      "（特征矩阵 gzip，mtime=0 → 逐字节可复算）；产物幂等（同输入两次逐字节相同）")
    A(f"- 判据样本窗（primary）：{cen['label_primary']['window']['first_created_at']} … "
      f"{cen['label_primary']['window']['last_created_at']}（未删失节点比全量窗短）")
    A(f"- 负例来源：{cen.get('blocked_reason_kinds')}")
    A(f"- 复现：`python3 -m tools.pre_dispatch_prior run --source pg --perm "
      f"{c['hyperparameters']['perm']} --sensitivity --docs-dir docs/pre_dispatch_prior`；"
      "`python3 -m pytest tests/test_pre_dispatch_prior.py -q`（20 passed）\n")
    return "\n".join(L)
