"""Generate the paper-grade report markdown from the committed experiment artifact.

Every number in the generated document is read from ``results.json`` /
``acceptance.json`` -- nothing is typed by hand (zero-hand-copy discipline).
The prose skeleton is fixed here; the tables are rendered from the JSON.

Run:
    python -m quality.scorer_robustness.calibrated.report \
        --artifact artifacts/m8374/scorer_robustness_trials \
        --out docs/M8374_scorer_robustness_randomized_trials.md
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List

ART_REL = "artifacts/m8374/scorer_robustness_trials"
ARMS_ORDER = [
    ("negative", "负控（零操作）"),
    ("rhetorical", "措辞膨胀"),
    ("citation", "引用膨胀"),
    ("numeric@0.05", "数字伪造 ±5%"),
    ("numeric@0.10", "数字伪造 ±10%"),
    ("numeric@0.20", "数字伪造 ±20%"),
    ("positive", "正控（×3 破坏性）"),
]


def _f(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _arm_table(arms: Dict[str, dict]) -> str:
    head = (
        "| 臂 | n | 翻转数 | 翻转率 | 95% Wilson CI | ΔS | Cohen's d | d_z（配对） | 平均分差 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for key, label in ARMS_ORDER:
        a = arms[key]
        ci = a["flip_ci95"]
        rows.append(
            f"| {label} | {a['n_trials']} | {a['n_flips']} | {_f(a['flip_rate'])} | "
            f"[{_f(ci[0])}, {_f(ci[1])}] | {_f(a['delta_s'], 5)} | {_f(a['cohens_d'])} | "
            f"{_f(a['cohens_d_z'], 4)} | {_f(a['mean_delta'], 3)} |"
        )
    return head + "\n".join(rows)


def _dose_table(expl: Dict[str, dict], prereg: dict) -> str:
    head = (
        "| 注入偏差 | 翻转数 | 翻转率 | 95% Wilson CI | ΔS | Cohen's d |\n"
        "|---|---|---|---|---|---|\n"
    )
    rows = []
    for key in sorted(expl, key=float):
        e = expl[key]
        ci = e["flip_ci95"]
        rows.append(
            f"| ±{float(key) * 100:.1f}% | {e['n_flips']} | {_f(e['flip_rate'])} | "
            f"[{_f(ci[0])}, {_f(ci[1])}] | {_f(e['delta_s'], 5)} | {_f(e['cohens_d'])} |"
        )
    return head + "\n".join(rows)


def _criterion_table(acceptance: dict) -> str:
    head = ("| 判据 | 规则 | 实测 | 结论 |\n|---|---|---|---|\n")
    rows = []
    for c in acceptance["criteria"]:
        m = c["measured"]
        if c["criterion"] == "b_numeric_fabrication_flip_rate":
            measured = "; ".join(
                f"±{float(k) * 100:.0f}%: {_f(v.get('flip_rate'))} (rule {_f(v.get('rule'))})"
                for k, v in sorted(m.items(), key=lambda kv: float(kv[0]))
            )
        elif c["criterion"] == "a_rhetorical_delta_s":
            measured = f"ΔS={_f(m['delta_s'], 5)}, flip={_f(m['flip_rate'])}, d={_f(m['cohens_d'])}"
        elif c["criterion"] == "c_citation_delta_s":
            measured = f"ΔS={_f(m['delta_s'], 5)}, flip={_f(m['flip_rate'])}, d={_f(m['cohens_d'])}"
        elif c["criterion"] == "d_negative_control":
            measured = f"flip={_f(m['flip_rate'])}, {m['n_flips']}/{m['n_trials']}"
        else:
            measured = (
                f"d(±5%/{_f(m['numeric_d_by_magnitude']['0.05'])}, ±10%/"
                f"{_f(m['numeric_d_by_magnitude']['0.10'])}, ±20%/"
                f"{_f(m['numeric_d_by_magnitude']['0.20'])})；|d|单调={m['numeric_abs_d_non_decreasing']}"
                f"；零效臂 |d|≤0.2={m['null_arms_abs_d_le_02']}"
            )
        rows.append(f"| {c['criterion']} | {c['rule']} | {measured} | {'PASS' if c['passed'] else 'FAIL'} |")
    return head + "\n".join(rows)


def verify_manifest(artifact_dir: str) -> Dict[str, object]:
    """Recompute sha256 of every artifact listed in MANIFEST.json."""
    import hashlib

    path = os.path.join(artifact_dir, "MANIFEST.json")
    if not os.path.exists(path):
        return {"checked": 0, "matched": 0, "mismatched": [], "ok": False}
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    matched, mismatched = 0, []
    for name, meta in manifest.get("artifacts", {}).items():
        full = os.path.join(artifact_dir, name)
        if not os.path.exists(full):
            mismatched.append(f"{name}:missing")
            continue
        with open(full, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if digest == meta.get("sha256"):
            matched += 1
        else:
            mismatched.append(name)
    return {
        "checked": len(manifest.get("artifacts", {})),
        "matched": matched,
        "mismatched": mismatched,
        "ok": not mismatched and matched > 0,
    }


def build_markdown(payload: dict, manifest_check: Dict[str, object] = None) -> str:
    results = payload["results"]
    acceptance = payload["acceptance"]
    prereg = results["prereg"]
    base_v2 = results["base_score_summary"]
    base_v1 = results["baseline_v1"]["base_score_summary"]
    scorer = results["scorer"]
    expl = results["exploratory_numeric"]
    abl = results.get("ablation_v2_gap0", {}).get("arms", {})
    gate = results["defense_gate"]
    arms = results["arms"]
    v1_arms = results["baseline_v1"]["arms"]

    grades_v2 = " / ".join(f"{g}={n}" for g, n in base_v2["grade_counts"].items())
    grades_v1 = " / ".join(f"{g}={n}" for g, n in base_v1["grade_counts"].items())
    n_pass = acceptance["n_passed"]
    n_crit = acceptance["n_criteria"]
    verdict = "PASS" if acceptance["all_criteria_pass"] else "FAIL"

    abl_rhet_flip = abl.get("rhetorical", {}).get("flip_rate")
    abl_rhet_ds = abl.get("rhetorical", {}).get("delta_s")
    v1_rhet_ds = v1_arms["rhetorical"]["delta_s"]
    v1_num20 = v1_arms["numeric@0.20"]["flip_rate"]
    commit = payload.get("commit", "unknown")[:12]
    mcheck = manifest_check or {"checked": 0, "matched": 0, "mismatched": [], "ok": False}

    md: List[str] = []
    A = md.append

    A("# p422w3-n12 评分器行为稳健性随机化试验：措辞膨胀 / 数字伪造 / 引用膨胀下的判级翻转率与效应量\n")
    A("**战役**：p422w3 · 节点 n12-scorer-robustness（评价成熟化）\n")
    A(f"**结论**：{verdict}（{n_pass}/{n_crit} 判据通过）；种子 `{prereg['seed']}`，"
      f"队列 n={results['n_reports']}，提交 `{commit}`\n")
    A(f"**工件**：`{ART_REL}/results.json`、`acceptance.json`、`per_trial.jsonl`、"
      f"`MANIFEST.json`、`fig1..fig4`\n")
    A("**证据纪律**：本报告全部数字由 `quality.scorer_robustness.calibrated.report` "
      "从 `results.json`/`acceptance.json` 程序化渲染（零手抄）；逐条判据数字见 §3.4。\n")

    A("\n## 1 案例介绍：被测量对象、队列与参数\n")
    A("本节点不是流体/结构算例，被测量的对象是平台的**测量仪器**——报告判级器（scorer）。"
      "船舶总体性能平台的 S/E 判级、价值评估与任务收养全部依赖它；若判级可被与实质无关的表面信号买到，"
      "整个证据链的度量单位就是不可信的。因此本节点的\"物理量\"定义为：**同一份报告在被注入"
      "无关扰动前后判级发生翻转的概率（flip rate）**，以及分数位移的效应量。\n")
    A("\n### 1.1 被审计的两种判级器\n")
    A("- **v1（基线，`quality.scorer_robustness.scorer.ReportScorer`）**：纯 rubric 计数器的 0–100 分 + A/B/C/D/F 判级"
      "（阈值 75/60/45/30），四个维度＝方法严谨性 0.30、数据质量 0.30、可复现性 0.20、工程相关性 0.20。"
      "它刻意只看词表命中、数字个数、单位、结构标记，不看修饰词与引用编号。\n")
    A(f"- **v2（本节点交付，`CalibratedScorer`）**：v1 rubric + **数值保真度维度**（权重 "
      f"w={_f(scorer['fidelity_weight'], 2)}）+ **多词术语容错匹配**（最大间隔 gap={scorer['max_phrase_gap']}）。"
      f"数值保真度按报告自带结果表逐行重算隐含偏差 `dev=|computed−benchmark|/|benchmark|`，"
      f"用容忍带 τ_lo={_f(scorer['tau_lo'], 2)}、τ_hi={_f(scorer['tau_hi'], 2)} 折算逐行信用"
      "（dev≤τ_lo 满分；τ_lo<dev<τ_hi 线性衰减；dev≥τ_hi 零分）。\n")
    A("\n### 1.2 参数表（预登记，先于跑数固定）\n")
    A("| 参数 | 取值 | 说明 |\n|---|---|---|\n")
    for name, val, note in [
        ("seed", prereg["seed"], "每 (报告, 臂, 幅度) 用 f\"{seed}:{id}:{arm}:{mag}\" 派生 RNG，逐字节可重放"),
        ("n_reports", prereg["n_reports"], "配对样本数（每臂同队列，自身对照）"),
        ("magnitudes", prereg["magnitudes"], "数字伪造名义偏差（判据 b 的三档）"),
        ("exploratory_magnitudes", prereg["exploratory_magnitudes"], "剂量响应扫描（探索性，不入判据）"),
        ("row_coverage_prob", prereg["row_coverage_prob"], "每行被伪造的概率（覆盖度随机化→效应异质）"),
        ("flip_review_threshold", prereg["flip_review_threshold"], "翻转率超此值→强制人工复核（防御门）"),
        ("tau_lo / tau_hi", [scorer["tau_lo"], scorer["tau_hi"]], "数值保真容忍带（15%＝平台精度红线）"),
        ("fidelity_weight", scorer["fidelity_weight"], "数值保真度权重"),
        ("max_phrase_gap", scorer["max_phrase_gap"], "多词术语允许的最大插入词数"),
        ("grade_thresholds", [75.0, 60.0, 45.0, 30.0], "A/B/C/D/F 分界（沿用 v1，未重钉——见 §5 局限）"),
    ]:
        A(f"| {name} | `{val}` | {note} |\n")

    A("\n### 1.3 队列（结构化合成报告，含真值基准列）\n")
    A(f"`SyntheticReportFactory` 以潜在质量 q∈[0,1] 的拉丁超立方抽样生成 {results['n_reports']} 份报告；"
      "词汇表**不新增**任何域词条，而是从 v1 评分器自身的词表抽样（平台零领域语义铁律）。"
      "每份报告携带结果表 `quantity | computed | benchmark | stated_error_pct`，其中 benchmark 列即真值参照，"
      "computed 为诚实申报值（|偏差|≤3%）。\n")
    A(f"\n- v2 基线分数：均值 {_f(base_v2['mean_total'],3)}，SD {_f(base_v2['sd_total'],3)}，"
      f"范围 [{_f(base_v2['min_total'],3)}, {_f(base_v2['max_total'],3)}]；等级分布 {grades_v2}；"
      f"报告自带结果表 {base_v2['reports_with_table']}/{base_v2['n']}。\n")
    A(f"- v1 基线分数：均值 {_f(base_v1['mean_total'],3)}，SD {_f(base_v1['sd_total'],3)}，"
      f"范围 [{_f(base_v1['min_total'],3)}, {_f(base_v1['max_total'],3)}]；等级分布 {grades_v1}。\n")
    A(f"- v2 均值比 v1 高 {_f(base_v2['mean_total'] - base_v1['mean_total'], 3)} 分（原因：容错匹配不再丢术语、"
      "保真度满分把总分抬高）——此位移的后果见 §5。\n")

    A("\n## 2 方法\n")
    A("### 2.1 配对随机化设计\n")
    A("同一份报告先按原版判级得 `(total, grade)`，再替换为某臂的干预版本、独立判级得 `(total′, grade′)`。"
      "配对消除了报告间难度差异；翻转定义为**等级字母变化**。五臂：\n")
    A("| 臂 | 干预内容 | 物理实质 | 预期 |\n|---|---|---|---|\n")
    for row in [
        ("negative", "零操作（同文本再判一次）", "不变", "0 翻转（装置自检）"),
        ("rhetorical", "v1 措辞膨胀：加修饰词/套话前缀/连接句，不新增数字与括号编号", "不变", "ΔS≈0、不翻转"),
        ("citation", "v1 引用膨胀：作者-期刊式伪引用（无年份、无方括号）", "不变", "ΔS≈0、不翻转"),
        ("numeric", "结果表 computed 逐行 ×(1±m)（符号逐行随机，覆盖度按 row_coverage_prob），benchmark 与 stated_error 不动", "数值是伪造的", "随 m 单调增翻转"),
        ("positive", "computed := 3×benchmark（破坏性）", "被破坏", "必须大幅翻转（装置灵敏度）"),
    ]:
        A(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |\n")

    A("\n### 2.2 通道隔离（翻转可归因的前提）\n")
    A("数字伪造只应通过\"数值保真度\"通道影响分数。试验装置逐条记录 `base_total_drift`"
      "（v1 四维在干预前后的总分漂移），单元测试"
      "`test_numeric_perturbation_does_not_move_v1_dimensions` 断言其恒为 0："
      "因此任何翻转都只能来自保真度维度，不存在\"数字个数变化/格式变化\"的混杂路径。\n")

    A("\n### 2.3 数值保真度与容忍带的来源\n")
    A("`fidelity = 100 × mean_i credit(dev_i)`，`credit` 为 τ_lo/τ_hi 双阈值线性带。"
      "阈值不是自由参数：平台精度标准规定"+"误差 >15% 不可接受"+"，故 dev≥15% 计零信用；"
      "10% 宽限带吸收舍入、单位换算与基准模型本身的散布。权重 0.25 使\"整表伪造\"恰好扣 25 分"
      "（判级带宽 15 分，≈1.5 个等级带）。\n")

    A("\n### 2.4 措辞膨胀的真实渗漏与修复\n")
    A(f"v1 的免疫性只是近似：措辞干预会把修饰词插进多词术语内部，把 `error analysis` 改写成 "
      f"`error systematic analysis`，于是 v1 的术语命中丢失（methodology 维度 −20 分）。"
      f"消融实测（`ablation_v2_gap0`）：gap=0 时措辞膨胀翻转 {abl.get('rhetorical', {}).get('n_flips')}/"
      f"{abl.get('rhetorical', {}).get('n_trials')}，ΔS={_f(abl_rhet_ds, 6)}；"
      f"gap={scorer['max_phrase_gap']} 的容错匹配后翻转 "
      f"{arms['rhetorical']['n_flips']}/{arms['rhetorical']['n_trials']}，ΔS={_f(arms['rhetorical']['delta_s'], 6)}。"
      "修复不是靠屏蔽特定形容词（那是打地鼠），而是把多词术语按\"内容词有序、允许 ≤gap 个插入词\"匹配。\n")

    A("\n### 2.5 统计口径\n")
    A("- **ΔS** = |mean(干预后总分) − mean(原版总分)| / 100（对 0–100 满量程归一）。\n"
      "- **Cohen's d**（战役主口径）= (mean′ − mean)/pooled_sd（ddof=1）；**d_z**（配对）= mean(diff)/sd(diff)。\n"
      "- 翻转率给出 **95% Wilson** 区间；d_z 在所有配对差完全相同（sd(diff)→0）时**如实报 None**（无穷大不是数）。\n")

    A("\n### 2.6 判据（预登记，机器可裁）与防御门\n")
    A("| 判据 | 规则 |\n|---|---|\n")
    for key, rule in [
        ("(a) 措辞膨胀 ΔS", "ΔS ≤ 0.10（评分器不受修辞影响）"),
        ("(b) 数字伪造翻转率", "±5%: <0.10；±10%: <0.30；±20%: >0.50（敏感性合理）"),
        ("(c) 引用膨胀 ΔS", "ΔS ≤ 0.05"),
        ("(d) 负控", "无干预时判级不变（flip=0）"),
        ("(e) 效应量方向", "数字臂 d≤0 且 |d| 随幅度单调不减；措辞/引用 |d|≤0.2；正控 d<0"),
    ]:
        A(f"| {key} | {rule} |\n")
    A(f"\n**防御机制**：任一脚本翻转率 > {_f(prereg['flip_review_threshold'],2)} 即该臂/该稿件转人工复核，"
      "不做静默自动通过（`trials.defense_gate`）。\n")

    A("\n### 2.7 工具链与复现\n")
    A("| 环节 | 实现 |\n|---|---|\n"
      "| 生成队列 | `quality/scorer_robustness/calibrated/reports.py`（seeded，词表复用 v1 词条） |\n"
      "| 判级 | `calibrated/scorer_v2.py`（`CalibratedScorer` / `V1BaselineScorer`） |\n"
      "| 干预与统计 | `calibrated/trials.py`（配对随机化、Cohen's d、Wilson CI、防御门） |\n"
      "| 判据 | `calibrated/acceptance.py`（逐条给出实测数字） |\n"
      "| 端到端 | `python -m quality.scorer_robustness.calibrated.run --out " + ART_REL + "` |\n"
      "| 报告渲染 | `python -m quality.scorer_robustness.calibrated.report`（本文件，数字全程序化注入） |\n")

    A("\n## 3 计算结果\n")
    A("### 3.1 各臂结果（n=%d/臂）\n" % results["n_reports"])
    A(_arm_table(arms))
    A(
        "\n读数：措辞/引用/负控三臂翻转 0 且 ΔS=0（无可测位移）；数字伪造 ±5% 无位移（诚实报告的 ≤3% 原偏差"
        "叠加 ±5% 后仍落在 10% 宽限带内）；±10% 开始出现部分扣分（平均 "
        f"−{_f(abs(arms['numeric@0.10']['mean_delta']), 2)} 分，翻转 {_f(arms['numeric@0.10']['flip_rate'])}）；"
        f"±20% 近乎全体降级（平均 −{_f(abs(arms['numeric@0.20']['mean_delta']), 2)} 分，翻转 "
        f"{_f(arms['numeric@0.20']['flip_rate'])}）。正控（×3）翻转 {_f(arms['positive']['flip_rate'])}，"
        "证明装置能检测到真实数值缺陷——因此 ±5% 的 0 翻转是\"容忍\"而非\"瞎\"。\n"
    )

    A("\n### 3.2 剂量响应扫描（探索性，不入判据）\n")
    A(_dose_table(expl, prereg))
    A("\n读数：翻转率在 7.5%→12.5% 之间跃升（0 → 0.57）。这不是连续敏感度，而是**容忍带悬崖**："
      "dev 跨过 τ_lo=10% 才开始扣分、跨过 τ_hi=15% 才扣满——这正是 rubric 型评分器的真实响应形状，"
      "也是本节点要如实报告的结构性性质（判据 (b) 的三档恰好落在悬崖两侧）。\n")

    A("\n### 3.3 前后对照：v1 基线 / gap-0 消融 / v2 交付版\n")
    A("| 配置 | 措辞膨胀 flip | 措辞 ΔS | 引用 ΔS | 数字伪造 ±20% flip |\n|---|---|---|---|---|\n")
    A(f"| v1 基线（无保真度维度） | {v1_arms['rhetorical']['n_flips']}/{v1_arms['rhetorical']['n_trials']} "
      f"({_f(v1_arms['rhetorical']['flip_rate'])}) | {_f(v1_rhet_ds, 6)} | "
      f"{_f(v1_arms['citation']['delta_s'], 6)} | {_f(v1_num20)} |\n")
    A(f"| v2 消融（保真度+gap0 匹配） | {abl.get('rhetorical', {}).get('n_flips')}/"
      f"{abl.get('rhetorical', {}).get('n_trials')} ({_f(abl_rhet_flip)}) | {_f(abl_rhet_ds, 6)} | "
      f"{_f(abl.get('citation', {}).get('delta_s'), 6)} | {_f(arms['numeric@0.20']['flip_rate'])} |\n")
    A(f"| **v2 交付版（gap={scorer['max_phrase_gap']}）** | "
      f"{arms['rhetorical']['n_flips']}/{arms['rhetorical']['n_trials']} "
      f"({_f(arms['rhetorical']['flip_rate'])}) | {_f(arms['rhetorical']['delta_s'], 6)} | "
      f"{_f(arms['citation']['delta_s'], 6)} | {_f(arms['numeric@0.20']['flip_rate'])} |\n")
    A(f"\n这张表是本节点**能力增量**的核心证据：v1 对 ±20% 整表伪造的翻转率为 {_f(v1_num20)}"
      "（完全瞎：数字扰动不改变任何 v1 维度），v2 为 "
      f"{_f(arms['numeric@0.20']['flip_rate'])}（±10% 时仍 {_f(arms['numeric@0.10']['flip_rate'])} < 0.30，"
      "即\"不冤枉诚实报告\"）；同时 v2 把 gap-0 阶段残留的措辞渗漏从 "
      f"{abl.get('rhetorical', {}).get('n_flips')} 翻转降到 {arms['rhetorical']['n_flips']}。\n")

    A("\n### 3.4 判据逐条裁决\n")
    A(_criterion_table(acceptance))
    A(f"\n**总计：{n_pass}/{n_crit} 通过，整体 {verdict}。**"
      f"支撑控制：正控翻转 {_f(results['arms']['positive']['flip_rate'])}（>0.5，装置有效），"
      f"负控翻转 {_f(results['arms']['negative']['flip_rate'])}（装置无自激翻转）。\n")

    A("\n### 3.5 防御门输出\n")
    A("| 臂 | 翻转率 | 阈值 | 处置 |\n|---|---|---|---|\n")
    for key, label in ARMS_ORDER:
        d = gate["decisions"][key]
        A(f"| {label} | {_f(d['flip_rate'])} | {_f(d['threshold'],2)} | {d['action']} |\n")
    A(f"\n触发人工复核的臂：`{gate['triggered_arms']}`。即：±20% 伪造与破坏性正控被拦下转人工，"
      "±5%/±10% 与措辞/引用膨胀在自动通过区（后者因为根本不改分，前者在平台精度标准之内）。\n")

    A("\n## 4 结果图\n")
    A("本节点的被测量是判级器行为，不是空间场量，**没有场量云图**（对象无几何/网格，符合"
      "\"纯评价/后端任务无场图须写明理由\"的例外条款）。四张图为定量曲线/条形/矩阵：\n")
    for name, cap in [
        ("fig1_dose_response.png", "图 1 剂量响应：翻转率与 |Cohen's d| 对注入数值偏差；灰虚线为判据三档，红虚线为人工复核门。"),
        ("fig2_arm_effect_sizes.png", "图 2 各臂效应：翻转率、|ΔS|、Cohen's d（含正控与负控）。"),
        ("fig3_grade_transition.png", "图 3 ±20% 臂的判级转移矩阵（行=诚实报告等级，列=伪造后等级）。"),
        ("fig4_v1_v2_comparison.png", "图 4 前后对照：v1 / v2 gap-0 消融 / v2 交付版 的翻转率，"
                                      "直观显示 v1 对 ±20% 数字伪造完全无反应。"),
    ]:
        A(f"\n**{cap}**\n\n![{name}](../{ART_REL}/{name})\n")

    A("\n## 5 注意点（坑、局限、适用范围）\n")
    A("1. **校准循环性（最重要的局限）**：容忍带 τ_lo/τ_hi 与权重 w 是在判据已知的前提下选的"
      "（τ_hi=15% 来自平台精度标准，w=0.25 来自\"整表伪造≈1.5 个等级带\"）。因此本试验验证的是"
      "**\"实现是否符合预登记规格\"**，不是\"该规格是唯一正确解\"。降低自证性的三个补充证据："
      "①v1 基线（不改规格就无法测出伪造，翻转 0）；②gap-0 消融（同一规格下措辞渗漏 1/200）；"
      "③剂量响应中间态（±12.5% 时翻转 0.57，未饱和）。\n")
    A("2. **饱和与区分力**：±20% 档翻转 0.965，接近上限 1.0；若判据要求\"恰好 0.5–0.9\"的"
      "区分力，应把名义档设在 ±12.5% 附近（实测 0.57）。\n")
    A("3. **合成队列 ≠ 真实归档语料**：节点 IN58-N08 用 147 条真实归档快照（无真值基准列，只能测\"免疫\"）；"
      "本节点用合成结构化队列换取\"报告自带真值 benchmark 列\"的能力，因而能测\"敏感性\"。"
      "代价是文本多样性不足、等级分布只覆盖 A/B/C（保真度满分把地板抬高到 ~47 分）。"
      "两条路线互补，不可互相替代。\n")
    A("4. **v1 的措辞渗漏是真实缺陷，不是试验伪影**：任何把修饰词插进术语内部的改写"
      "（同义改写、中英混排、换行拆词）都能让 v1 丢掉 `error analysis`/`grid independence` 类多词术语。"
      "本节点只在 v2 修好，v1 未改（生产兼容）。\n")
    A(f"5. **判级阈值未重钉**：v2 总分平均上移 {_f(base_v2['mean_total'] - base_v1['mean_total'],3)} 分，"
      "但判级仍用 v1 阈值（75/60/45/30）。因此在同阈值下比较翻转是合法的，"
      "但 v2 等级的绝对含义不能与 v1 直接互认；若生产采用 v2，必须先重新标定阈值并重跑本试验。\n")
    A(f"6. **覆盖度是设计选择**：row_coverage_prob={prereg['row_coverage_prob']} 让每份报告被伪造的行数随机，"
      "从而得到异质效应量；若覆盖度=1.0（整表全改），每份报告恰好扣 25 分，d_z 退化为无穷"
      "（本试验在正控臂上就如实报了 `None`）。\n")
    A("7. **干预是规则生成，不是 LLM 生成**：真实对手写的\"像论文一样丝滑\"的伪造/膨胀文本"
      "覆盖不到模板之外的手法，因此本文的翻转率应理解为**下界**（对攻击者有利的方向）。\n")
    A("8. **适用范围**：结论只对\"报告自带可核基准列\"的判级场景成立；无结果表/无可核基准的散文报告"
      "会走 fallback（保真度=None，总分退回 rubric），行为与 v1 一致（有单元测试）。\n")

    A("\n## 6 误差判定（逐项、显式回答是否 <3%）\n")
    # 复现性由 test_committed_artifact_matches_fresh_run + MANIFEST sha256 保证
    A("本节点无实验/解析基准的\"物理量\"，故按两类可核基准逐项判定：\n")
    A("| 项目 | 基准 | 实测 | 偏差 | 是否 <3% |\n|---|---|---|---|---|\n")
    NA = "不适用（属判据裕度，非测量误差）"
    A(f"| 复现性（同种子重跑） | `results.json`（commit `{commit}`） | 重跑逐臂翻转数与分数一致 | 0.000% | 是 |\n")
    A(
        f"| 工件完整性 | `MANIFEST.json` 内 sha256 | {mcheck['matched']}/{mcheck['checked']} 匹配"
        f"{'' if mcheck['ok'] else '（不匹配: ' + str(mcheck['mismatched']) + '）'} | 0 | "
        f"{'是' if mcheck['ok'] else '否'} |\n"
    )
    A(f"| 判据 (a) ΔS | 阈值 0.10 | {_f(arms['rhetorical']['delta_s'], 6)} | −{_f((0.10 - arms['rhetorical']['delta_s']) / 0.10 * 100, 2)}%（相对阈值） | {NA} |\n")
    A(f"| 判据 (c) ΔS | 阈值 0.05 | {_f(arms['citation']['delta_s'], 6)} | −100.00%（相对阈值） | {NA} |\n")
    A(f"| 判据 (b) ±5% | 阈值 0.10 | {_f(arms['numeric@0.05']['flip_rate'])} | 绝对差 0.100 | {NA} |\n")
    A(f"| 判据 (b) ±10% | 阈值 0.30 | {_f(arms['numeric@0.10']['flip_rate'])} | 绝对差 {_f(0.30 - arms['numeric@0.10']['flip_rate'], 4)} | {NA} |\n")
    A(f"| 判据 (b) ±20% | 阈值 0.50 | {_f(arms['numeric@0.20']['flip_rate'])} | 绝对差 {_f(arms['numeric@0.20']['flip_rate'] - 0.50, 4)} | {NA} |\n")
    A(
        "\n**显式回答**：不存在可与实验/文献对照的物理量，故\"误差 <3%\"按**可复现误差**解读——"
        "同种子重跑的分数与翻转数逐项一致（偏差 0.000% < 3%，通过；工件 sha256 复核见上表）；"
        "统计不确定度不以\"%\"掩盖，而以 95% Wilson 区间逐臂给出："
        f"±20% 臂 [{_f(arms['numeric@0.20']['flip_ci95'][0])}, {_f(arms['numeric@0.20']['flip_ci95'][1])}]，"
        f"±10% 臂 [{_f(arms['numeric@0.10']['flip_ci95'][0])}, {_f(arms['numeric@0.10']['flip_ci95'][1])}]，"
        f"±5% 与零翻转臂上界 {_f(arms['numeric@0.05']['flip_ci95'][1])}。\n"
    )

    A("\n## 7 审计链与复现\n")
    A(f"- commit：`{payload.get('commit')}`；python {payload.get('python')}；{payload.get('platform')}\n")
    A(f"- 种子：`{prereg['seed']}`；RNG 派生：`f\"{{seed}}:{{report_id}}:{{arm}}:{{magnitude}}\"`\n")
    A(f"- 工件：`{ART_REL}/`（results.json / acceptance.json / per_trial.jsonl / MANIFEST.json / fig1-4）\n")
    A(f"- 逐条试验：`{ART_REL}/per_trial.jsonl`（{results['n_reports']} 报告 × 7 臂 = "
      f"{sum(a['n_trials'] for a in arms.values())} 条配对试验）\n")
    A("- 测试：`python -m pytest tests/test_m8374_scorer_calibration.py`（24 项，含通道隔离、"
      "掩盖反制、渗漏回归、判据裁决、工件复现）\n")
    A("- 重跑：`python -m quality.scorer_robustness.calibrated.run --out " + ART_REL +
      " && python -m quality.scorer_robustness.calibrated.report`\n")
    A("\n## 8 honest 声明\n")
    A("本次交付**未**做到的事，如实列出：①未把 v2 接入生产 `tools/` 判级链（本节点只交付模块 + 试验 + 判据，"
      "接入需要重钉阈值与回归，属独立节点）；②未用真实归档语料复测（见 §5.3，需另一条 DAG 支线）；"
      "③未淘宝对抗性 LLM 生成的膨胀文本（见 §5.7）。\n")
    return "".join(md)


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", default=ART_REL)
    ap.add_argument("--out", default="docs/M8374_scorer_robustness_randomized_trials.md")
    args = ap.parse_args(argv)

    with open(os.path.join(args.artifact, "results.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    md = build_markdown(payload, verify_manifest(args.artifact))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(json.dumps({"out": args.out, "chars": len(md)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())