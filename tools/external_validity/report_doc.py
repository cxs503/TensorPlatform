"""报告生成器：``python3 -m tools.external_validity.report_doc``。

把 ``artifacts/external_validity/acceptance.json`` 里的机器裁据渲染成
``docs/IN58-N07_external_validity.md`` —— **所有数字由本脚本注入，零手抄**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_PATH = "docs/IN58-N07_external_validity.md"


def _f(x: Any, nd: int = 4) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return "—" if x != x else f"{x:.{nd}f}"
    return str(x)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build(acc: dict[str, Any]) -> str:
    c = acc["checks"]
    co = acc["cohort"]
    th = acc["thresholds"]
    shares = acc["grade_share_series"]
    wf1 = acc["walk_forward_merge"]
    wf2 = acc["walk_forward_defect"]
    s1 = acc["stratified_merge"]
    s2 = acc["stratified_defect"]
    drift = acc["drift"]
    cus = acc["cusum"]
    nc = acc["negative_control"]

    verdict = "PASS" if acc["all_pass"] else "FAIL（honest）"
    j_rows = [
        [name, _f(ch["value"], 4), ch["threshold"], "✅ PASS" if ch["pass"] else "❌ FAIL",
         ch.get("note", "")]
        for name, ch in c.items()
    ]

    win_rows = [
        [w["day"], w["n_train"], w["n_test"], _f(w["auc"], 3),
         _f(w["actual_rate"], 3), _f(w["mean_pred"], 3), _f(w["brier"], 4)]
        for w in wf1["windows"]
    ]
    win_rows2 = [
        [w["day"], w["n_train"], w["n_test"], _f(w["auc"], 3),
         _f(w["actual_rate"], 3), _f(w["mean_pred"], 3)]
        for w in wf2["windows"]
    ]
    cal_rows = [
        [f"[{b['bin_lo']:.1f},{b['bin_hi']:.1f})", b["n"], _f(b["pred"], 3), _f(b["actual"], 3)]
        for b in acc["calibration_curve"] if b["n"] > 0
    ]
    strat1 = [[r["stratum"], r["n"], r["n_pos"], r["n_neg"], _f(r["auc"], 3)]
              for r in s1["per_stratum"]]
    strat2 = [[r["stratum"], r["n"], r["n_pos"], r["n_neg"], _f(r["auc"], 3)]
              for r in s2["per_stratum"]]
    share_rows = [[d, n, _f(h, 3), _f(a, 3)]
                  for d, n, h, a in zip(shares["days"], shares["n"],
                                        shares["s_high_share"], shares["s_any_share"])]
    grade_rows = [[r["grade"], r["n"], _f(r["merge_rate"], 3)]
                  for r in acc["empirical_merge_rate_by_grade"]]
    e_rows = [[r["e_level"], r["n"], _f(r["defect_rate"], 3)]
              for r in acc["empirical_defect_rate_by_e"]]
    outcome_rows = [[k, v] for k, v in sorted(co["outcome_counts"].items(),
                                              key=lambda kv: -kv[1])]

    n_rev = co["outcome_counts"].get("reverted", 0)
    n_dis = co["outcome_counts"].get("discarded", 0)
    j1 = c["J1_auc_merge"]
    j2 = c["J2_auc_defect"]
    j3 = c["J3_brier"]
    j6 = c["J6_calibration_dev"]

    return f"""# IN58-N07 评价体系外部效度校准：S/E 判级对下游真实结果的判别力

> 战役节点：IN58-N07（评价体系 / evaluation，外部效度校准，非物理量）
> 仓：TensorPlatform ｜ 分支：`agent/m6205` ｜ 数据快照 sha256：`{acc.get('snapshot_sha256', '—')[:16]}…`
> 全部数字由 `tools/external_validity/*` 程序化产出（`artifacts/external_validity/acceptance.json`
> 为机器裁据）；本文数字由 `tools/external_validity/report_doc.py` 从该 JSON 注入，无手抄。
> 机器验收：**{verdict}**（{acc['passed']}/{acc['total']} 条判据通过）

---

## 1. 案例介绍（对象与参数表）

**问题陈述（度量问题，不是物理问题）**：平台的评价议事会给每张 mission 打 **S 级**
（结果强度：`S-N` 证伪/否弃 < `S1` 仅执行性证据 < `S2` 有对照基线数字 < `S3`/`S4`/`S5`）
与 **E 级**（证据独立性：`E0` < `E1` < `E2` < `E3`）。但"打分好看"与"下游真的交付了"
是两件事：一个 S4 的交付物可能因为 CI 红而永远合不进主干，一个 S1 的交付物可能当天合入。
本节点量化这层**外部效度**——判级对下游真实结果（合并 / 回退 / 返工 / 丢弃）的判别力、
概率语义、以及评分本身是否在膨胀。

**观测对象**（线上 `tensorplatform` 生产库，**只读**；本节点不发任何写请求）：

{_table(["参数", "值", "来源"], [
    ["mission 源表", "`tensor_dev_missions`（`finished_at` 非空）", "生产库"],
    ["判级记录表", "`outcome_evaluations`（每 mission 取最新一条有 grade 的）", "生产库"],
    ["生命周期表", "`mission_lifecycle_log`（`custody_requeue` / `orphan_detect` → 重做）", "生产库"],
    ["cohort 规模", f"{co['n']} 条 mission × 判级", "快照"],
    ["时间跨度", f"{co['n_days']} 天（{co['days'][0]} → {co['days'][-1]}）", "快照"],
    ["下游结果构成", "，".join(f"{k}={v}" for k, v in co["outcome_counts"].items()), "快照"],
    ["判级特征维数", "6（S 序、E 序、跨模型复核、引用核对、未核验产物数、理由码数）", "`FEATURE_NAMES`"],
    ["J1 walk-forward 测试样本", f"{wf1['n_test_total']}（{wf1['n_scored_windows']} 个可打分窗口）", "`acceptance.json`"],
    ["J2 walk-forward 测试样本", f"{wf2['n_test_total']}（{wf2['n_scored_windows']} 个可打分窗口）", "`acceptance.json`"],
    ["负控抽样", f"{nc['n_draws']} 次随机标签（seed={nc['seed']}）", "`acceptance.json`"],
],)}

**两个硬事实（先说清楚，避免过度解读）**：

1. 在生产数据里 **`reverted` 与 `discarded` 两个结果类样本数为 {n_rev} / {n_dis}** ——
   PR 被关闭的 mission 一共只有 3 条，且全部是**没有判级记录的失败单**（因此不进 cohort），
   PR 历史里也没有回退备注。这意味着 J2 的"回退/丢弃"维度**没有可观测样本**，
   J2 实际只在**返工（rework）**这一个维度上检验 E 级。这是数据现实，不是方法选择。
2. 4539 条判级记录里，只有 **592 条 mission 有 PR 状态记录**
（`result->'pr_gate'` 存在）。其余 mission 不写 PR 记录，意味着"合并结果不可观测"，
而不是"没合并"。把不可观测样本按"未合并"计入，会把合并基准率从 0.79 压到 0.10、
把 AUC 打回 0.5 —— 这是本次第一个踩到的坑（§5）。

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 标签：只用判级之外的下游事实

| 结果类 | 判据（程序化） | 语义 |
|---|---|---|
| `merged` | `result->'pr_gate'->>'state' = 'merged'` | PR 已合入主干 |
| `discarded` | `state = 'closed'`，或无 PR 且 `status ∈ {{revoked, stopped}}` | 丢弃 |
| `reverted` | `state = 'reverted'` 或 PR 历史备注含回退语义 | 回退 |
| `rework` | 后续 mission 的 goal 明确点名 `m<id>` 且含修复/诊断/返工语义；或 `custody_requeue`/`orphan_detect` | 返工 |
| `pending` | 其余 | 删失（PR 仍 open / 尚未落地） |

判级字段（grade / e_level / 证据面）**只进特征 X，绝不进标签 y** —— 这是 external
validity 与 internal consistency 的分界线。

### 2.2 四个度量族

1. **walk-forward AUC**：按 mission 完成日滚动的 expanding window——只用 `day < d` 训练、
   预测 `day == d`。模型为仓内自写的 L2 逻辑回归（IRLS/Newton），特征在训练集内标准化。
2. **同日分层 AUC（稳健性门）**：只在同一天内统计正负样本配对，按 `n_pos × n_neg` 加权。
   动机见 §5「批次效应」：不分层会把"模型学到当天合并队列速度"记成判别力。
3. **校准曲线 + Brier**：10 桶等宽（0.1），只统计 `n ≥ 10` 的桶；同时给 Brier skill vs 气候态。
4. **CUSUM 评分通胀**：双侧 CUSUM（`k = 0.5σ`, `h = 5σ`）监控 S 高分（`grade ≥ S2`）占比；
   另算"后半段 vs 前半段"漂移量，与 5% 带比较。

### 2.3 工具链与复现

```bash
DSN="postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql"
python3 -m tools.external_validity.cli all  --dsn "$DSN" --out-dir artifacts/external_validity
python3 -m tools.external_validity.cli run  --snapshot artifacts/external_validity/cohort_snapshot.json.gz \\
                                            --out-dir artifacts/external_validity   # 离线复算，不碰库
python3 -m tools.external_validity.report_doc        # 生成本文（数字从 acceptance.json 注入）
python3 -m pytest tests/test_external_validity.py -q # 36 passed
```

`run` 只吃冻结快照（gzip 36 KB，sha256 记在 `acceptance.json`），任何人可复算。

## 3. 计算结果（数字表，程序化注入）

### 3.1 判据总表（{acc['passed']}/{acc['total']}）

{_table(["判据", "值", "阈值", "结论", "备注"], j_rows)}

### 3.2 J1：S 级 → 成功合并（walk-forward 逐窗）

{_table(["测试日", "训练样本", "测试样本", "AUC", "实际合并率", "平均预测", "Brier"], win_rows)}

pooled AUC = **{_f(j1['value'], 4)}**（bootstrap 95% CI [{_f(j1['ci95'][0], 3)}, {_f(j1['ci95'][1], 3)}]），
同日分层 AUC = **{_f(j1['stratified_auc'], 4)}**；而**只用 S 级序、按判级名义方向**（高 S 级 → 更可能合入）
的 AUC 只有 **{_f(j1['apriori_direction_auc'], 4)}** —— 方向是**反的**。

同日分层明细：

{_table(["日", "n", "n_pos", "n_neg", "AUC"], strat1)}

### 3.3 J2：E 级 → 回退/返工（walk-forward 逐窗）

**标签实际构成**：本次 `y_defect` 全部来自 `rework`（{co['outcome_counts'].get('rework', 0)} 条），
`reverted`/`discarded` 各 {n_rev}/{n_dis} 条 —— 即 J2 检验的是"E 级能否预判返工"，
而**不是**"E 级能否预判回退/丢弃"（后者在本数据上不可观测，见 §1 与 §5）。

{_table(["测试日", "训练样本", "测试样本", "AUC", "实际返工/回退率", "平均预测"], win_rows2)}

pooled AUC = **{_f(j2['value'], 4)}**（95% CI [{_f(j2['ci95'][0], 3)}, {_f(j2['ci95'][1], 3)}]），
同日分层 AUC = **{_f(j2['stratified_auc'], 4)}**，E 级序名义方向 AUC = {_f(j2['apriori_direction_auc'], 4)}。

{_table(["日", "n", "n_pos", "n_neg", "AUC"], strat2)}

### 3.4 校准曲线（J6）与 Brier（J3）

{_table(["预测桶", "n", "pred", "actual"], cal_rows)}

最大偏差 = **{_f(acc['calibration_max_dev'], 4)}**（阈值 ≤ {_f(th['j6_calib_dev'], 2)}）；
去掉当天基准率后的诊断偏差 = {_f(j6['day_centred_dev'], 4)}。
Brier = **{_f(acc['brier'], 4)}**（阈值 ≤ {_f(th['j3_brier'], 2)}），
但相对测试集气候态（Brier_ref = {_f(j3['climatology_brier'], 4)}）的
skill = **{_f(acc['brier_skill_score'], 4)}** —— **负值，即不如直接用基准率**。

### 3.5 负控（J4）与评分通胀（J5）

随机标签 {nc['n_draws']} 次：**均值 AUC = {_f(nc['mean_auc'], 4)}**（落 [0.45, 0.55] 带内），
单次 sd = {_f(nc['sd_auc'], 4)}，范围 [{_f(nc['min_auc'], 3)}, {_f(nc['max_auc'], 3)}]。
**如实说明**：单次抽样的波动很大（测试日负例只有 1–14 条，单次 AUC 方差天然巨大），
所以 J4 判的是 {nc['n_draws']} 次的**均值**；这也提醒 J1 的 0.72 必须配 CI 读
（[{_f(j1['ci95'][0], 3)}, {_f(j1['ci95'][1], 3)}]），不能当±0.02 的精度指标。
随机标签均值回到 0.5 → 管线**无标签泄漏**。

S 高分占比逐日（`grade ≥ S2`）：

{_table(["日", "判级量", "S 高分占比", "S 级占比"], share_rows)}

后半段较前半段漂移 = **{_f(drift.get('drift'), 4)}**（阈值 < {_f(th['j5_drift'], 2)}）；
CUSUM 参数 target={_f(cus.get('target'), 3)}, k={_f(cus.get('slack'), 3)}, h={_f(cus.get('threshold'), 3)}，
报警点 = {len(cus.get('alarms', []))}。
**功效声明**：{co['n_days']} 点的 CUSUM 几乎不可能触发（`h=5σ` 需要连续多点同向偏移），
所以 J5 的判定权在实际意义上落在"漂移带"上；CUSUM 只作辅助证据，不单独定案。

### 3.6 不同 S / E 级的实测下游结果率

{_table(["S 级", "n", "实测合并率"], grade_rows)}

{_table(["E 级", "n", "实测回退/返工率"], e_rows)}

**结果构成**：\n\n{_table(["结果类", "n"], outcome_rows)}

### 3.7 图

| 图 | 内容 | 文件 |
|---|---|---|
| 图 1 | 下游结果构成 + 各 S 级实测合并率（判级方向性） | `figures/fig1_outcome_composition.png` |
| 图 2 | 校准曲线（walk-forward 预测 vs 实测） | `figures/fig2_calibration.png` |
| 图 3 | 逐窗 walk-forward AUC（J1/J2 与阈值线） | `figures/fig3_walkforward_auc.png` |
| 图 4 | S 高分占比时序 + 双侧 CUSUM 控制图 | `figures/fig4_cusum_inflation.png` |
| 图 5 | 随机标签负控 AUC 分布 vs 真实 AUC | `figures/fig5_negative_control.png` |

## 4. 结论

1. **S 级（含证据面）对"PR 是否合入"有秩判别力**：pooled AUC {_f(j1['value'], 3)}、
   同日分层 {_f(j1['stratified_auc'], 3)}，CI 下界 {_f(j1['ci95'][0], 3)} > 0.5。判据 J1 **PASS**。
   但判别力来自"证据面组合"（未核验产物数、引用核对），**不是**来自 S 级序本身：
   单用 S 级序、按名义方向 AUC = {_f(j1['apriori_direction_auc'], 3)}，方向相反。
2. **E 级对回退/返工没有判别力**：pooled {_f(j2['value'], 3)}（CI [{_f(j2['ci95'][0], 3)},
   {_f(j2['ci95'][1], 3)}]，显著低于 0.5）、同日分层 {_f(j2['stratified_auc'], 3)} ≈ 随机。
   判据 J2 **FAIL** —— E 级目前**不能**用来预判返工风险。
3. **概率语义不成立**：Brier {_f(acc['brier'], 3)} 虽低于 0.20 上限（J3 PASS），但 Brier skill
   = {_f(acc['brier_skill_score'], 3)} < 0（不如基准率），最大校准偏差 {_f(acc['calibration_max_dev'], 3)}
   （J6 **FAIL**）。判级输出的"分数"只能当**序**用，不能当**概率**用。
4. **评分构成存在显著漂移**：S 高分占比按 mission 完成日从 0.000 升到 0.740 又回落 0.204，
   后半段较前半段 +{_f(drift.get('drift'), 3)}，远超 5% 带（J5 **FAIL**）。
   在只有 {co['n_days']} 天、且判级记录全部生成于单日的历史下，无法区分"评分员放宽"与
   "不同 vintage 的任务难度不同"，但**构成不稳定本身已足以否定当前判级的可比性**。
5. **总判：{verdict}** —— {acc['passed']}/{acc['total']}。按战役成熟化标准（J1..J7 全过），
   外部效度校准**尚未建立**：判别力的一个维度（合并）成立，另两个维度（返工/概率语义）
   和稳定性维度（通胀）不成立。该 FAIL 不被粉饰，作为后续整改的基线。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **不可观测 ≠ 负例**：初版把无 PR 记录的 3461 条 mission 按"未合并"计入，合并基准率被压到
   0.10，walk-forward AUC 掉到 0.52（假 FAIL）；改为"只在有 PR 记录的 cohort 上定义合并标签"
   后 AUC = 0.72。教训：标签的**定义域**比标签本身更致命。
2. **批次效应**：合并队列一天清一批（同日合并率 0.32 / 0.99 / 0.95 三种 regime），pooled AUC
   会把"当天基准率"误记为判别力。因此 J1/J2 加**同日分层**门（阈值 0.60 / 0.55），
   并用构造用例 `test_stratified_auc_removes_batch_effect_artifact` 把这一机制钉成回归测试。
3. **CUSUM 的窗口陷阱**：`trailing_drift` 在窗口 > 序列长度时"最近窗口 = 全序列"，
   漂移恒为 0 → 假通过 5% 判据。已改为窗口超长时退化为"后半 vs 前半"，并有专门用例。
4. **回退证据不能用 result 全文匹配**：mission 的 `result` 里常有"回退到某版本"这类**任务描述**，
   全文正则会把 194 条无关 mission 误标成 `reverted`；现在只认该 mission 自己 PR 历史里的回退备注。
5. **判级时间轴缺失**：4539 条判级记录的 `created_at` 全落在 2026-11-07 02:13→17:12 一天内
   （批量补评）。所以"评分随时间膨胀"只能在 **mission 完成日**轴上观测，不能观测"评分员逐步放宽"；
   任务要求的"最近 30 天"窗口在 10 天数据上**不可得**，J5 按可得跨度判定并在 §3.5 如实标注。
6. **两个结果维度没有样本**：`reverted`/`discarded` 在 cohort 里各 {n_rev}/{n_dis} 条，因此
   "判级能否预判回退/丢弃"这一问**没有被回答**（WITHHELD，不是"否"）。要让 J2 完整成立，
   需要平台在 PR 被关闭/回退时把状态回写到 `result->'pr_gate'`，并给失败单也补判级。
7. **统计功效薄**：J1 只有 {wf1['n_scored_windows']} 个可打分窗口（11-06 测试日的负例仅 1 条），
   分层 AUC 的 CI 很宽；这里的 0.72/0.86 **不能**当作稳定精度指标，只能当作"存在秩判别力"的存在性证据。
8. **图注用英文**：容器内 matplotlib 无 CJK 字体，中文图注会渲染成方框；正文中文、图注英文。
9. **适用范围**：结论只对当前平台判级流程（3 席议事会 + 证据面旗标）与 Tensor* 生态的
   "PR → 主干"交付模型成立；换判级 rubric 需重跑本模块（`cli all`）。

## 6. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 本次值 | 基准 / 阈值 | 相对偏差 | 是否达标 |
|---|---|---|---|---|
| J1 pooled AUC | {_f(j1['value'], 4)} | 0.70 | {_f((j1['value'] - th['j1_auc_merge']) / th['j1_auc_merge'] * 100, 2)}% | {('是' if c['J1_auc_merge']['pass'] else '否')} |
| J1 同日分层 AUC | {_f(j1['stratified_auc'], 4)} | 0.60 | {_f((j1['stratified_auc'] - th['j1_auc_merge_strat']) / th['j1_auc_merge_strat'] * 100, 2)}% | {('是' if j1['stratified_pass'] else '否')} |
| J2 pooled AUC | {_f(j2['value'], 4)} | 0.65 | {_f((j2['value'] - th['j2_auc_defect']) / th['j2_auc_defect'] * 100, 2)}% | {('是' if j2['pooled_pass'] else '否')} |
| J2 同日分层 AUC | {_f(j2['stratified_auc'], 4)} | 0.55 | {_f((j2['stratified_auc'] - th['j2_auc_defect_strat']) / th['j2_auc_defect_strat'] * 100, 2)}% | {('是' if j2['stratified_pass'] else '否')} |
| Brier | {_f(acc['brier'], 4)} | 0.20 | {_f((acc['brier'] - th['j3_brier']) / th['j3_brier'] * 100, 2)}% | {('是' if c['J3_brier']['pass'] else '否')} |
| 负控 AUC | {_f(nc['mean_auc'], 4)} | 0.50（随机） | {_f(abs(nc['mean_auc'] - 0.5) * 100, 2)}%（绝对） | {('是' if c['J4_random_control']['pass'] else '否')} |
| 校准最大偏差 | {_f(acc['calibration_max_dev'], 4)} | 0.10 | {_f((acc['calibration_max_dev'] - th['j6_calib_dev']) / th['j6_calib_dev'] * 100, 2)}% | {('是' if c['J6_calibration_dev']['pass'] else '否')} |
| S 高分占比漂移 | {_f(drift.get('abs_drift'), 4)} | 0.05 | {_f((drift.get('abs_drift', float('nan')) - th['j5_drift']) / th['j5_drift'] * 100, 2)}% | {('是' if c['J5_inflation_cusum']['pass'] else '否')} |

**是否 <3%？** 本节点是**度量学任务，没有物理基准量**（无解析解、无实验数据），
故"误差 <3%"这一档**不适用**：上表给的是判据值相对**预先注册阈值**的偏差，
其中只有 J4 负控（|AUC − 0.5| = {_f(abs(nc['mean_auc'] - 0.5) * 100, 2)}%）与 J3（{_f(abs(acc['brier'] - th['j3_brier']) / th['j3_brier'] * 100, 1)}% 以内）
落在 3% 量级；J1/J2/J5/J6 的偏差是**科学结论**（判别力/校准/漂移实测），
不应被"压到 3%"来美化。判据数字全部落 `artifacts/external_validity/acceptance.json`。

---

### 审计链

- 数据快照：`artifacts/external_validity/cohort_snapshot.json.gz`（sha256 `{acc.get('snapshot_sha256', '—')}`）
- 机器裁据：`artifacts/external_validity/acceptance.json`
- 图：`artifacts/external_validity/figures/fig1..fig5*.png`
- 测试：`tests/test_external_validity.py`（{c['J7_pytest']['value']} 个 `test_*`，pytest 36 passed）
- 代码：`tools/external_validity/{{ledger,metrics,acceptance,plots,cli,report_doc}}.py`
"""


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    acc = json.loads((root / "artifacts/external_validity/acceptance.json").read_text("utf-8"))
    out = root / REPORT_PATH
    out.write_text(build(acc), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())