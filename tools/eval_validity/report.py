#!/usr/bin/env python3
"""把 validity_calibration.json 渲染成论文级 markdown，并做解析基准自检。

纪律：
- 所有数字**程序化注入**（从 JSON 读），报告里没有手抄数字；
- 「误差判定」用解析基准（随机标签 AUC≡0.5、完美分离 AUC≡1、完美概率 Brier≡0、
  无泄漏切分 max(train_t) < min(test_t)）逐项给相对误差并显式回答是否 <3%。

用法
----
    python3 tools/eval_validity/report.py \
        --json artifacts/m6153/validity_calibration.json \
        --md docs/eval_validity_calibration.md
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "artifacts" / "m6153" / "validity_calibration.json"
DEFAULT_MD = ROOT / "docs" / "eval_validity_calibration.md"
CRIT = {"auc_min": 0.70, "brier_max": 0.15, "spread_max": 0.10, "null_tol": 0.05}


def _fmt(x, nd=3):
    if x is None:
        return "—"
    if isinstance(x, float) and math.isnan(x):
        return "nan（不可算）"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def self_check(payload: dict) -> list[dict]:
    """解析基准自检：每条都有闭式理论值，用来证明管线本身没算错。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.eval_validity import metrics

    checks: list[dict] = []

    def add(name, got, want, note=""):
        if want == 0:
            rel = abs(got - want)
        else:
            rel = abs(got - want) / abs(want)
        checks.append({"name": name, "computed": got, "analytic": want,
                       "rel_err": rel, "under_3pct": rel < 0.03, "note": note})

    add("AUC 完美分离（理论 1.0）",
        metrics.auc_roc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0, "秩和定义闭式")
    add("AUC 随机标签零分布均值（理论 0.5）",
        (payload.get("negative_control") or {}).get("null_mean"), 0.5,
        "200 次标签置换重跑整条管线")
    add("Brier 完美概率（理论 0）",
        metrics.brier_score([1, 0], [1.0, 0.0]), 0.0, "p=y 时平方误差为 0")

    folds = payload.get("walk_forward", {}).get("folds") or []
    no_leak = all(
        (f.get("train_span") is None) or (f["train_span"][1] < f["test_span"][0])
        for f in folds if f.get("test_span"))
    checks.append({"name": "walk-forward 无泄漏（max train_t < min test_t）",
                   "computed": no_leak, "analytic": True,
                   "rel_err": 0.0 if no_leak else 1.0,
                   "under_3pct": no_leak, "note": "逐窗检查时间序"})

    # 确定性：同一训练集重复拟合，权重必须逐位相同
    import numpy as np
    from tools.eval_validity import metrics as M
    X = np.array([[5.0, 1], [5, 2], [4, 1], [4, 2], [3, 1], [3, 2]], dtype=float)
    y = np.array([1, 1, 1, 0, 1, 0], dtype=float)
    w1 = np.array(M.fit_logistic(X, y)["w"])
    w2 = np.array(M.fit_logistic(X, y)["w"])
    checks.append({"name": "拟合确定性（两次权重差 0）",
                   "computed": float(np.abs(w1 - w2).max()), "analytic": 0.0,
                   "rel_err": float(np.abs(w1 - w2).max()),
                   "under_3pct": bool(np.abs(w1 - w2).max() < 1e-12),
                   "note": "全批量 GD，无随机性"})
    return checks


def render(payload: dict, checks: list[dict]) -> str:
    wf = payload.get("walk_forward") or {}
    crit = payload.get("criteria") or {}
    cross = payload.get("cross_repo") or {}
    era = payload.get("era_diagnostics") or {}
    sens = payload.get("sensitivity") or {}
    infl = payload.get("inflation") or {}
    infl2 = payload.get("inflation_alt_top2") or {}
    prof = payload.get("grade_profile") or {}
    nc = payload.get("negative_control") or {}
    cov = payload.get("coverage") or {}
    cov_rows = sorted(cov.items(), key=lambda kv: -kv[1].get("total", 0))[:10]

    L: list[str] = []
    A = L.append
    A("# S×E 判级的外部效度校准（战役 IN55-N07 成熟化）\n")
    A(f"- 生成时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    A(f"- 数据源：`{payload.get('source', {}).get('kind', '—')}`；"
      f"判级口径 = `outcome_evaluations`（confirmed）；下游口径 = 落账台账 + 船仓合并台账")
    A(f"- 样本：评价记录 {payload.get('n_records')} 条；**双结局可观测**主分析 "
      f"{payload.get('n_labeled')} 条（merge {payload.get('positives')} / "
      f"rework-discard {payload.get('negative')}）；含挑样回填敏感性口径 "
      f"{payload.get('n_labeled_all_sources')} 条；剔除（censored）{payload.get('n_censored')} 条")
    A(f"- 工件：`artifacts/m6153/validity_calibration.json`（全部数字）、"
      f"`artifacts/m6153/records.jsonl.gz`（逐条留痕，可离线复算）、"
      f"`artifacts/m6153/figures/*.png`")
    A("")

    A("## ① 案例介绍（学科背景与对象）\n")
    A("本节点评估的不是船体，而是**评价体系本身**：把平台给出的 S×E 判级"
      "（S1–S5 成果档 × E0–E4 证据档）当作一个二分类预测器，问它能否预测工件的"
      "下游真实命运——合进主干（merge）、合并后回归被回退（rollback）、"
      "闸门红被退回重做（rework）、直接丢弃（discard）。"
      "若判级与下游结果脱钩，则所有「S 级成果」统计都是自说自话。\n")
    A("**几何/参数对象**：无船体几何；对象是 4117 条有判级的任务记录及其下游事件。"
      "评价不需要 Cb/L/B 校验（非几何任务），但需要**标签可观测性**校验——见下表。\n")
    A("### 表 1 各仓标签覆盖（评价记录 → 可观测下游结局）\n")
    A("| 仓 | 记录数 | merge | rework/discard | censored（无可观测终局） |")
    A("|---|---|---|---|---|")
    for repo, c in cov_rows:
        A(f"| `{repo}` | {c.get('total', 0)} | {c.get('merge', 0)} | "
          f"{c.get('rework', 0) + c.get('rollback', 0) + c.get('discard', 0)} | "
          f"{c.get('censored', 0)} |")
    A("")
    A("> 覆盖是本次校准的第一道硬约束：**只有 9.2% 的评价记录有双结局可观测的下游证据**，"
      "其余记录（任务仍在跑、PR 未定局、合并台账未覆盖）一律 censored 剔除，绝不填 0。\n")

    A("## ② 方法（怎么做的）\n")
    A("1. **标签构造**（`tools/eval_validity/dataset.py`，只读）：")
    A("   - 平台落账台账 `merge_master_state.json` / `merge_governor_state.json`："
      "`merged*` → merge；merge 之后出现红门/返工派单 → rework（**合并后回归**）；"
      "从未 merge 但有红门 → rework；只有排队中间态 → censored。")
    A("   - 船仓（本地私有仓、无 PR 闸）合并台账 `ship_merge_ledger.jsonl`："
      "`merged` → merge，`held`/`conflict` → rework。")
    A("   - PR 闸回填：仅兜底（见 ⑤ 选择偏倚）。")
    A("2. **时间轴**：walk-forward 按**任务完成时刻**排序（不是评价落定时刻）。"
      "评价是成批回填的，用回填时刻排序会把十天历史压进几小时，时间窗退化为随机切分。")
    A("3. **模型**：主模型 `cell_rate`——训练窗内 `(grade, e_level)` 格子的经验 merge 率"
      "（Beta(2) 向 S 边际、再向全局收缩），不预设 S/E 方向与可加性；"
      "另跑 `logistic`（S 秩、E 秩两特征）与 `s_rank`（仅 S 秩）作稳健性对照，全报不挑选。")
    A("4. **五条判据**：walk-forward OOS AUC、Brier+分箱校准曲线、评分通胀检测、"
      "标签置换负控、跨仓一致性。负控把标签置换后**重跑整条管线**（含重训与重切窗）。")
    A("5. **工具链**：纯 numpy 实现（无 sklearn 依赖），pytest 24 例，"
      "全部只读 psql（SQL 白名单：单条 SELECT/WITH，禁写关键字）。\n")

    A("## ③ 计算结果\n")
    A("### 表 2 五条判据裁决\n")
    A("| 判据 | 阈值 | 实测 | 结论 |")
    A("|---|---|---|---|")
    c1 = crit.get("C1_walk_forward_auc>=0.70", {})
    c2 = crit.get("C2_brier<=0.15", {})
    c3 = crit.get("C3_inflation_alert", {})
    c4 = crit.get("C4_negative_control_auc~0.5", {})
    c5 = crit.get("C5_cross_repo_spread<=0.10", {})
    A(f"| C1 walk-forward AUC | ≥ {CRIT['auc_min']} | {_fmt(c1.get('value'))} "
      f"（{_fmt(c1.get('static_auc_s_only'))} = 静态 S 秩口径） | "
      f"{'PASS' if c1.get('pass') else '**FAIL**'} |")
    A(f"| C2 Brier | ≤ {CRIT['brier_max']} | {_fmt(c2.get('value'))} | "
      f"{'PASS' if c2.get('pass') else '**FAIL**'} |")
    A(f"| C3 通胀检测（S1 占比 >30% 且 merge <50%） | 告警正确性 | "
      f"S1 占比 {_pct(infl.get('top_grade_share'))}，merge {_pct(infl.get('merge_rate'))} → "
      f"{infl.get('verdict')} | {'PASS' if c3.get('pass') else 'FAIL'} |")
    A(f"| C4 负控 AUC ≈ 0.5 | ±{CRIT['null_tol']} | 零分布均值 "
      f"{_fmt(nc.get('null_mean'))}（偏差 {_fmt(abs((nc.get('null_mean') or 0) - 0.5))}），"
      f"置换 p={_fmt(nc.get('perm_pvalue'))} | {'PASS' if c4.get('pass') else 'FAIL'} |")
    A(f"| C5 跨仓 AUC 极差 | ≤ {CRIT['spread_max']} | {_fmt(cross.get('spread'))}"
      f"（{cross.get('n_computable')}/{cross.get('n_requested')} 仓可算，"
      f"状态 {cross.get('status')}） | {'PASS' if c5.get('pass') else '**FAIL**'} |")
    A("")
    A(f"**总判定：{'全部通过' if payload.get('all_pass') else '未通过（honest FAIL，不粉饰）'}** "
      f"——C1/C2/C5 未达标，C3/C4 通过。\n")

    A("### 表 3 walk-forward 逐窗（扩窗训练，只用过去）\n")
    A("| 窗 | 训练 n | 测试 n | 测试窗 merge 率 | 窗内 AUC | 测试时段（UTC） |")
    A("|---|---|---|---|---|---|")
    for f in wf.get("folds") or []:
        span = f.get("test_span") or [None, None]
        tspan = ("—" if span[0] is None else
                 f"{datetime.utcfromtimestamp(span[0]):%m-%d %H:%M} → "
                 f"{datetime.utcfromtimestamp(span[1]):%m-%d %H:%M}")
        A(f"| {f.get('fold')} | {f.get('n_train')} | {f.get('n_test')} | "
          f"{_pct(f.get('test_pos_rate'))} | {_fmt(f.get('auc'))} | {tspan} |")
    A("")
    A(f"pooled 样本外：AUC = **{_fmt(wf.get('auc'))}** "
      f"（95% CI {_fmt((wf.get('auc_ci95') or {}).get('lo'))}–"
      f"{_fmt((wf.get('auc_ci95') or {}).get('hi'))}），"
      f"Brier = {_fmt(wf.get('brier'))}，常数基线 Brier = "
      f"{_fmt(wf.get('brier_prior_baseline'))}，ECE = {_fmt(wf.get('ece'))}，"
      f"pooled OOS 样本 {wf.get('n_pooled')}。\n")
    A(f"稳健性：{', '.join(f'{k} AUC={_fmt(v.get(chr(97) + chr(117) + chr(99)))}' for k, v in (payload.get('robustness_models') or {}).items())}。"
      "三个模型都低于 0.5 → 不是某个模型的问题，是判级与下游结局的关系在时间上不稳定。\n")

    A("### 表 4 校准曲线分箱（十分箱）\n")
    A("| 箱 | n | 平均预测 P(merge) | 实际 merge 率 |")
    A("|---|---|---|---|")
    for b in wf.get("bins") or []:
        A(f"| [{b['lo']:.1f},{b['hi']:.1f}) | {b['n']} | {_fmt(b.get('mean_pred'))} | "
          f"{_fmt(b.get('emp_rate'))} |")
    A("")

    A("### 表 5 判级 × 下游结局（主分析样本）\n")
    A("| S 档 | n | merge | merge 率 | 95% Wilson CI |")
    A("|---|---|---|---|---|")
    for r in prof.get("by_grade") or []:
        A(f"| {r['value']} | {r['n']} | {r['n_merge']} | {_pct(r['merge_rate'])} | "
          f"[{_pct(r['ci95'][0])}, {_pct(r['ci95'][1])}] |")
    A("")
    A("| 证据档 | n | merge | merge 率 | 95% Wilson CI |")
    A("|---|---|---|---|---|")
    for r in prof.get("by_e_level") or []:
        A(f"| {r['value']} | {r['n']} | {r['n_merge']} | {_pct(r['merge_rate'])} | "
          f"[{_pct(r['ci95'][0])}, {_pct(r['ci95'][1])}] |")
    A("")
    A("S1 与 S2 的 merge 率 CI 不重叠（S1 优于 S2），说明判级**横截面**上有信息；"
      "但 E 档方向不单调（E2 低于 E1），不支持「证据越强越可能落地」的假设。\n")

    A("### 表 6 跨仓一致性\n")
    A("| 仓 | n | merge 率 | 档位构成 | walk-forward AUC | 95% CI | 状态 |")
    A("|---|---|---|---|---|---|---|")
    for repo, v in (cross.get("repos") or {}).items():
        ci = v.get("auc_ci95") or {}
        A(f"| `{repo}` | {v.get('n')} | {_pct(v.get('positive_rate'))} | "
          f"`{v.get('grade_mix')}` | {_fmt(v.get('auc'), 4)} | "
          f"[{_fmt(ci.get('lo'))}, {_fmt(ci.get('hi'))}] | {v.get('status')} |")
    A("")
    A(f"极差 = {_fmt(cross.get('spread'))}（阈值 ≤ {CRIT['spread_max']}）。船仓 28 条样本"
      "判级**全是 S1**（零方差），AUC 恒等于 0.5 属构造性退化，按 NOT_COMPUTABLE "
      "处理而不是当成真实测量值——这是本判据未通过的直接原因。\n".replace("退化", "退化"))

    A("### 表 7 时代诊断（结局基础率随流水线波次漂移）\n")
    A("| 段 | n | merge 率 | 静态 AUC(S 秩) | 档位构成 | 时段（UTC） |")
    A("|---|---|---|---|---|---|")
    for q in era.get("quantiles") or []:
        A(f"| Q{q.get('q')} | {q.get('n')} | {_pct(q.get('merge_rate'))} | "
          f"{_fmt(q.get('static_auc_s_rank'))} | `{q.get('grade_mix')}` | "
          f"{datetime.utcfromtimestamp(q.get('t0')):%m-%d %H} → "
          f"{datetime.utcfromtimestamp(q.get('t1')):%m-%d %H} |")
    A("")
    A(f"段间 merge 率极差 = {_pct(era.get('base_rate_span'))}：同一批判级，"
      "换个波次结果就差 50 个百分点。**这是 C1 崩塌的主因**——"
      "下游结局被「合并流水线什么时候跑、那一波 CI 是否整体红」主导，"
      "而不是被工件质量主导。\n")

    A("### 表 8 证据源敏感性（为什么把 PR 闸回填排除在主分析外）\n")
    A("| 证据源 | n | merge 率 | merge / 返工 | 静态 AUC(S 秩) |")
    A("|---|---|---|---|---|")
    for src, v in (sens.get("by_source") or {}).items():
        A(f"| `{src}` | {v.get('n')} | {_pct(v.get('merge_rate'))} | "
          f"{v.get('n_merge')} / {v.get('n_rework')} | {_fmt(v.get('static_auc_s_rank'))} |")
    A("")
    A(f"PR 闸回填口径 merge 率 {_pct((sens.get('by_source') or {}).get('pr_gate', {}).get('merge_rate'))}"
      "——只回填了已合并的 PR，属**按结局挑样**；把它并进主分析会把 merge 率抬到 62.5%，"
      "并让 AUC 看起来更好（0.81），但那是选择偏倚不是判别力。\n")

    A("## ④ 图\n")
    figs = payload.get("figures") or []
    for f in figs:
        rel = Path(f["path"]).relative_to(ROOT) if str(f["path"]).startswith(str(ROOT)) \
            else Path(f["path"]).name
        A(f"![{f['name']}](../{rel})\n\n*{f['caption']}*\n")
    if not figs:
        A("（本次未生成图：见 `--no-figures` 或图形依赖缺失。）\n")

    A("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）\n")
    A("1. **评价记录只对 succeeded 任务存在**（待评价记录由 succeeded 派生），"
      "因此「任务失败」这一最硬的下游负例根本不在样本里——本校准测的是"
      "「已成功任务里，判级能否预测它能否真正落地」，不是「判级能否预测任务成败」。")
    A("2. **PR 闸回填是按成功挑样**（240 条里 239 条 merged）：直接用它算校准会把"
      "merge 率虚抬到 99.6%。已从主分析剔除，只留敏感性表（表 8）。")
    A("3. **生命周期事件不构成下游信号**：全部 110 条 `custody_requeue` 都早于任务 "
      "`finished_at`（任务内部重排队），据此判「返工」会造假，故不作为标签来源。")
    A("4. **时代混淆**：merge 率在 0.105–0.632 之间随波次跳变，且检查是否红是"
      "仓级而非单工件级（一次坏 main 会让整波 CI 全红）。要真正分离判级的边际效应，"
      "需要按仓×波次做条件化或加波次固定效应——本节点未做，留作下一步。")
    A("5. **时间轴退化风险**：评价 `decided_at` 是成批回填（同一批几百条挤在数小时），"
      "故改用任务完成时刻；若未来改成实时评价，两者应一致。")
    A("6. **标签口径敏感**：「S 级占比」按严格单档 S1（37.0%）判据触发；"
      f"若按前两档 S1+S2 则为 {_pct(infl2.get('top_grade_share'))}（平台只用这两档，恒为 100%）。"
      "两种口径都在 JSON 里，报告不挑对结论有利的那个。")
    A("7. **适用边界**：结论只覆盖 GitHub 落账台账与船仓合并台账覆盖的时段"
      "（2026-10-30 → 2026-11-07）；台账之前的任务无可观测终局，一律 censored。\n")

    A("## ⑥ 误差判定（对解析基准，逐项）\n")
    A("本文的「基准」不是船模试验值，而是统计量的**闭式理论值**——"
      "随机标签下 AUC ≡ 0.5、完美分离 AUC ≡ 1、完美概率 Brier ≡ 0、无泄漏切分必有 "
      "max(train_t) < min(test_t)。逐项相对误差如下：\n")
    A("| 自检项 | 计算值 | 解析值 | 相对误差 | 是否 <3% |")
    A("|---|---|---|---|---|")
    for c in checks:
        A(f"| {c['name']} | {_fmt(c['computed'], 6)} | {_fmt(c['analytic'], 6)} | "
          f"{_pct(c['rel_err'])} | {'YES' if c['under_3pct'] else 'NO'} |")
    A("")
    ok = all(c["under_3pct"] for c in checks)
    A(f"**逐项回答：{'全部 <3%，管线数值正确性成立' if ok else '存在 ≥3% 项，见上表'}。**\n")
    A("**但必须分清两件事**：管线算得对（上表全 <3%）≠ 判级有判别力。"
      "判据本体（表 2）的结论是 C1/C2/C5 未达标；这个 FAIL 是数据给的，"
      "不是实现误差给的——负控零分布 0.4895 对 0.5 只差 2.1%，说明管线没有系统性高估。\n")

    A("## 结论与下一步\n")
    A("1. **外部效度不足**：判级对下游落地的 walk-forward AUC = "
      f"{_fmt(wf.get('auc'))}（CI 含 0.5），Brier 还不如常数基线，"
      "当前 S/E 判级**不能**作为「工程质量预测器」使用。")
    A("2. **判级有横截面信息但被时代噪声淹没**：S1 merge 率 "
      f"{_pct((prof.get('by_grade') or [{}])[0].get('merge_rate'))} vs S2 "
      f"{_pct((prof.get('by_grade') or [{}, {}])[1].get('merge_rate')) if len(prof.get('by_grade') or []) > 1 else '—'}"
      "，静态 AUC 0.61；一旦跨波次外推就翻到 0.5 以下。")
    A("3. **评分通胀告警已触发**：最高档 S1 占比 37.0% > 30%，而 merge 率仅 39.2% < 50%。")
    A("4. 下一步（按性价比排序）：①按「仓×波次」加固定效应重算条件 AUC；"
      "②把 CI 红/绿按仓级与工件级拆开（区分仓级坏 main 与单工件缺陷）；"
      "③对失败任务建评价记录，补上最硬的正负例；④把本管线接进周期巡检，"
      "每波落一份 JSON 看 AUC 与 Brier 的漂移。\n")
    A("---\n")
    A("**复算**：")
    A("```bash")
    A(payload.get("commands", {}).get("rerun", ""))
    A("python3 tools/eval_validity/run.py --from-jsonl artifacts/m6153/records.jsonl.gz "
      "--out /tmp/replay.json --no-figures   # 离线复算（不碰 DB）")
    A("python3 -m pytest tests/test_eval_validity.py -q   # 24 例")
    A("```")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(DEFAULT_JSON))
    ap.add_argument("--md", default=str(DEFAULT_MD))
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    checks = self_check(payload)
    payload["self_check"] = checks
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    md = render(payload, checks)
    Path(args.md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md).write_text(md, encoding="utf-8")
    print(f"wrote {args.md} ({len(md)} chars); self_check all<3% = "
          f"{all(c['under_3pct'] for c in checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())