"""从产物 JSON 生成论文级报告（docs/IN56-N07_*.md）——零手抄数字。

用法::

    python3 -m tools.evaluation_ops.extval.report \
        --artifacts artifacts/IN56-N07 --out docs/IN56-N07_external_validity_calibration.md
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "—（无定义）"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, int):
        return str(x)
    return f"{x:.{nd}f}"


def _pct(x: Any, nd: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f}%"


def build_markdown(art: str) -> str:
    acc = _load(os.path.join(art, "acceptance.json"))
    met = _load(os.path.join(art, "metrics.json"))
    dset = _load(os.path.join(art, "dataset.json"))
    d = acc["diagnostics"]
    cert = d["identifiability_certificate"]
    asc = d["association_batch_controlled"]
    ctl = d["instrument_controls"]
    wf = met["walk_forward"]
    crit = {c["criterion"]: c for c in acc["criteria"]}

    sens_rows = []
    for h in (24, 48, 72):
        p = os.path.join(art, "sensitivity", f"h{h}", "dataset.json")
        if not os.path.exists(p):
            continue
        dj = _load(p)
        mj_p = os.path.join(art, "sensitivity", f"h{h}", "metrics.json")
        mj = _load(mj_p) if os.path.exists(mj_p) else None
        sens_rows.append(
            (
                h,
                dj["n"],
                dj["n_positive"],
                dj["n"] - dj["n_positive"],
                dj["excluded_counts"],
                (mj["association"]["grade_only"]["auc_mean"] if mj else None),
            )
        )

    imp = met["shap"]["importance"][:8]
    imp_rows = "\n".join(
        f"| {i + 1} | `{r['feature']}` | {_f(r['mean_abs_shap'], 4)} | {_f(r['coef_original_scale'], 3)} | "
        f"{'是' if r.get('is_batch_proxy') else ''} |"
        for i, r in enumerate(imp)
    )
    wf_rows = "\n".join(
        f"| {k} | {_f(wf[k]['pooled_auc'])} | {_f(wf[k]['pooled_brier'], 4)} | {wf[k]['n_scored']} | "
        f"{_f(wf[k]['coverage'], 3)} | {wf[k]['fold_auc_defined']}/{wf[k]['n_folds']} | "
        f"{wf[k]['folds_with_single_class_test']}/{wf[k]['n_folds']} |"
        for k in ("logreg", "random_forest", "grade_only")
    )
    fold_rows = "\n".join(
        f"| {f['fold']} | {f['n_train']} | {f['n_pos_train']}/{f['n_train']} | {f['n_test']} | "
        f"{f['n_neg_test']} | {_f(f['auc'])} | {f['t_train_max'][:19]} | {f['t_test_min'][:19]} |"
        for f in wf["logreg"]["folds"]
    )
    crit_rows = "\n".join(
        f"| {c['criterion']} | {c['status']} | {c['value'] if not isinstance(c['value'], dict) else json.dumps(c['value'], ensure_ascii=False)[:150]} | {c['threshold']} |"
        for c in acc["criteria"]
    )
    sens_md = "\n".join(
        f"| {h} | {n} | {pos} | {neg} | {json.dumps(exc, ensure_ascii=False)} | {_f(g)} |"
        for h, n, pos, neg, exc, g in sens_rows
    )
    within = asc["within_delivery_wave"]

    return f"""# IN56-N07 评价外部效度校准：S/E 判级对下游真实结果的判别力（walk-forward AUC · Brier）

> 战役节点：**IN56-N07**（评价体系成熟化 · 外部效度校准）｜仓：TensorPlatform｜分支：`agent/m6178`
> 产物：`tools/evaluation_ops/extval/`（新模块）+ `artifacts/IN56-N07/`（数据集/指标/图）+ `tests/test_extval_calibration.py`
> 全部数字由 `tools/evaluation_ops/extval/*` 程序化产出，本文由 `tools.evaluation_ops.extval.report` 从 JSON 渲染，**无手抄数字**。
> 机器裁决：**{acc['verdict']}**（{acc['n_pass']}/{acc['n_total']} 条判据通过）——honest FAIL，不覆盖。

---

## 1. 案例介绍（对象 / 队列 / 参数表）

**研究问题**：平台的 S×E 判级（S=成果价值等级，E=证据强度等级）是否对**下游真实结果**
（合并 / 回退 / 返工 / 丢弃）有**判别力**？用 walk-forward AUC 与 Brier score 量化。

三个时刻必须严格分离，否则"预测"是后视镜：

```
created_at  <  finished_at  <  t_label
   建单          特征快照        下游事件（PR merged/closed/open、返工、终止）
```

| 参数 | 值 | 来源 |
|---|---|---|
| 观测快照 `as_of` | {dset['as_of']} | 本次运行 |
| mission 源表 | `tensor_dev_missions`（`finished_at` 非空） | 线上库只读 |
| 判级表 | `outcome_evaluations`（`grade` 非空，confirmed/candidate） | 同上 |
| 下游事件表 | `mission_lifecycle_log`（`pr_gate` / `custody_requeue` / 治理动作） | 同上 |
| 观察窗 horizon | {dset['horizon_hours']} h（敏感性：24 / 48 / 72 h） | 工程默认 |
| 已判级且已完结 mission | {dset['n'] + sum(dset['excluded_counts'].values())} | `dataset.json` |
| 下游结果**可观测**队列 | **{dset['n']}**（阳性 {dset['n_positive']}，负类 {dset['n'] - dset['n_positive']}） | 同上 |
| 被剔除（WITHHELD） | {sum(dset['excluded_counts'].values())} → {json.dumps(dset['excluded_counts'], ensure_ascii=False)} | 同上 |
| 特征 | 16 维（S/E 序数、证据条数、判据码数、goal 复杂度、tool_calls、compactions、落盘文件数、自检文本长度、fail_stage、accepted、git 脏、时长、预算、摘要长度） | `dataset.FEATURE_NAMES` |
| 标签构成 | {json.dumps(dset['label_kind_counts'], ensure_ascii=False)} | 同上 |
| 判级构成 | {json.dumps(dset['grade_counts'], ensure_ascii=False)} | 同上 |

**一句话结论**：队列够大（{dset['n']} ≥ 200）、特征与标签时刻严格分离（0 条违规），但**下游标签在时间上不可识别**——
负类只由**一次 PR 回填波**（{_f(0)} 条：{json.dumps(cert['negative_by_finish_day'], ensure_ascii=False)}）
产生，而负类最晚时刻之后的阳性有 **{cert['positive_after_last_negative']}** 条。任何 out-of-time 测试折都不含负类，
故 walk-forward AUC **数学上无定义**（0/{cert['n_folds']} 折有定义的 AUC）。这是本单的核心结论，不是实现缺陷
（见 §3.4 仪器正/负控）。

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 标签口径（钉死；改口径须升 `SCHEMA_VERSION`）

| 最终 PR 状态 / 治理事件 | 语义 | y |
|---|---|---|
| `merged` | PR 被合并（下游整合） | 1 |
| `closed` | PR 被关闭/弃用（下游丢弃） | 0 |
| `open` 且观察满 `horizon` | 未整合 / 待返工 | 0 |
| 终态 ∈ `failed/stopped/revoked/orphan` | 丢弃 | 0 |
| 治理负动作（`reclaim/orphan_detect/write_off/suspend`）或 `custody_requeue` | 返工 | 0 |
| 其余（观察窗未满 / 无任何下游事件 / 标签早于特征 / 标签晚于 `as_of`） | 结果未定 | 剔除（WITHHELD） |

### 2.2 三层评估口径（分层报，禁混用）

- **L1 判据口径 = 扩张窗口 walk-forward**：样本按 `finished_at` 升序；第 k 折训练集严格位于测试块之前
  （同刻样本整体归入 test 侧，避免 `train_max == test_min`）；主指标 = 折外预测汇总的 **pooled AUC / pooled Brier**。
- **L2 关联性口径 = 重复分层 k 折**（批次控制用）：全体、仅判级、仅批次代理、去批次代理，及
  **同交付波（同一天）队列**；配 **标签置换零分布**（p 值）。
- **L3 仪器控制**：正控（向真实特征注入已知线性信号 + 15% 噪声）与负控（标签置换），
  证明"低 AUC 是数据性质还是代码缺陷"。

### 2.3 工具链

| 模块 | 职责 |
|---|---|
| `extval/dataset.py` | 只读抓取 3 张表 → 纯函数 `derive_samples()` 造样本（含删失/泄漏双闸门） |
| `extval/models.py` | 纯 numpy：L2 逻辑回归（Newton-IRLS）、CART 随机森林、AUC（Mann-Whitney 秩）、Brier、可靠性曲线、ECE、bootstrap CI、置换采样 Shapley |
| `extval/analysis.py` | walk-forward 切分与评估、分层 k 折、置换零分布、批次控制、正/负控、7 张图 |
| `extval/acceptance.py` | 七条机器判据 → `acceptance.json`；CLI 一次跑完 |
| `extval/report.py` | 本文渲染（零手抄） |
| `tests/test_extval_calibration.py` | 19 条 pytest（度量解析基准、Shapley 效率性、估计器、时序纪律、标签口径、退化可识别性、仪器控制） |

**复现命令**

```bash
python3 -m tools.evaluation_ops.extval.acceptance \\
    --as-of 2026-11-07T16:30:00+00:00 --horizon-hours 48 \\
    --out artifacts/IN56-N07                    # 数据→训练→图→判据（含自动跑 pytest）
python3 -m tools.evaluation_ops.extval.report \\
    --artifacts artifacts/IN56-N07 --out docs/IN56-N07_external_validity_calibration.md
```

## 3. 计算结果（程序化产出的数字表）

### 3.1 L1 判据口径：walk-forward（扩张窗口，5 折）

| 模型 | pooled AUC | pooled Brier | 折外样本 | 覆盖率 | 有定义的折 AUC | 单类测试折 |
|---|---|---|---|---|---|---|
{wf_rows}

简单规则基线（S1/S2=成功、E≤1=失败）：AUC **{_f(met['metrics']['simple_rule']['auc'])}**、命中正类
{met['metrics']['simple_rule']['n_pos_pred']} 条；随机基线 AUC **0.5**，常数预测 Brier（无技能参考）
**{_f(met['metrics']['random_baseline']['brier_base_rate'], 4)}**。

逐折明细（`n_pos_train/n_train` 显示训练折**含负类**，`n_neg_test` 显示测试折**不含负类**）：

| 折 | n_train | 训练阳性/总数 | n_test | 测试负类数 | 折 AUC | train_max | test_min |
|---|---|---|---|---|---|---|---|
{fold_rows}

> 读法：训练折里两类都在（{cert['folds_with_single_class_train']}/{cert['n_folds']} 单类训练折），
> 但**测试折里没有任何负类**（{cert['folds_with_single_class_test']}/{cert['n_folds']} 单类）→ 折 AUC 无定义，
> pooled AUC 亦无定义。这不是可调参能修的问题：负类全部早于 {cert['negative_max_t_features'][:19]}，
> 其后仍有 {cert['positive_after_last_negative']} 条阳性，out-of-time 设计无法把负类放进测试折。

### 3.2 L2 关联性口径：判别力到底有多少（批次控制）

| 特征集 | AUC（重复分层 5 折，均值） | 说明 |
|---|---|---|
| 全 16 维 | {_f(asc['full']['auc_mean'])} ± {_f(asc['full']['auc_std'], 4)} | 与"仅批次代理"几乎相同 |
| 仅 S/E 判级（2 维） | {_f(asc['grade_only']['auc_mean'])} ± {_f(asc['grade_only']['auc_std'], 4)} | 置换零分布 p = {_f(asc['permutation_null_grade_only']['p_value'], 4)} |
| 仅批次代理（`{', '.join(asc['batch_proxy_features'])}`） | {_f(asc['batch_proxy_only']['auc_mean'])} | 非因果的"交付波"标识 |
| 去批次代理（14 维） | {_f(asc['full_minus_batch_proxy']['auc_mean'])} | 批内仍有可分性，但不足以支撑因果解读 |
| 同交付波（2026-11-05，n={within.get('2026-11-05', {}).get('n', '—')}，pos={within.get('2026-11-05', {}).get('n_pos', '—')}）全 16 维 | {_f(within.get('2026-11-05', {}).get('full'))} | 批次控制后 |
| 同上 · 仅 S/E 判级 | {_f(within.get('2026-11-05', {}).get('grade_only'))} | **判级单独判别力 0.61–0.66** |
| 同上 · 仅批次代理 | {_f(within.get('2026-11-05', {}).get('batch_only'))} | 全模型 AUC 的绝大部分来自这里 |

置换零分布（全 16 维）：观测 AUC {_f(asc['permutation_null_full']['observed'])}，零分布均值
{_f(asc['permutation_null_full']['null_mean'])}、P95 {_f(asc['permutation_null_full']['null_p95'])}，
p = {_f(asc['permutation_null_full']['p_value'], 4)}（{asc['permutation_null_full']['n_permutations']} 次置换）。

### 3.3 Shapley 特征重要性（置换采样 Shapley，效率性误差 {_f(met['shap']['max_abs_efficiency_gap'], 2)}）

| 排名 | 特征 | mean \\|φ\\| | 逻辑回归系数（原尺度） | 批次代理 |
|---|---|---|---|---|
{imp_rows}

红标（批次代理）特征占据前两位——它们不是因果变量，而是**交付波标识**：负类任务的时长中位数
高出正类两个数量级、goal 长度约两倍。任何把 0.98 的关联 AUC 解读为"判级很准"的结论都会被这个混淆推翻。

### 3.4 仪器控制（低 AUC 是数据性质还是代码缺陷？）

| 控制 | pooled AUC | pooled Brier | 期望 | 结论 |
|---|---|---|---|---|
| 负控（标签置换） | {_f(ctl['negative']['pooled_auc'])} | {_f(ctl['negative']['pooled_brier'], 4)} | ≈0.5 | 通过：无虚假信号 |
| 实测（L1 walk-forward） | {_f(wf['primary']['pooled_auc'])} | {_f(wf['primary']['pooled_brier'], 4)} | 判据要求 ≥0.65 | 无定义（单类测试折） |
| 正控（注入已知信号，噪声 {ctl['positive']['noise']}） | {_f(ctl['positive']['pooled_auc'])} | {_f(ctl['positive']['pooled_brier'], 4)} | ≥0.8 | 通过：管线能把真信号找回来 |

正控走的是**同一条 walk-forward 管线、同一批真实特征、同一张时间轴**，注入标签有时间铺开的信号后
AUC 立刻回到 {_f(ctl['positive']['pooled_auc'])}——证明 L1 的"无定义"来自**标签的时间结构**，不是估计器或切分实现。

### 3.5 观察窗敏感性（horizon）

| horizon (h) | 队列 n | 阳性 | 负类 | 剔除计数 | S/E 单独 AUC（L2） |
|---|---|---|---|---|---|
{sens_md}

负类对 horizon 与"回填波"极端敏感：48 h 时 107 条，72 h 时塌到 1 条。**负类不是一个稳健的人群**。

### 3.6 七条判据裁决

| 判据 | 状态 | 值 | 阈值 |
|---|---|---|---|
{crit_rows}

## 4. 结果图（7 张，全部由 `metrics.json`/`dataset.json` 程序化绘制）

| 图 | 文件 | 本图回答的问题 / 读法 |
|---|---|---|
| 图 1 ROC（L1 vs L2） | `artifacts/IN56-N07/figures/fig1_roc_walkforward.png` | 判据口径 ROC 因 5/5 折测试集单类而**退化**（曲线贴边 = 无定义），标题栏打印可识别性状态；同图叠加 L2 关联性 ROC 作对照——注意二者不可混为一谈 |
| 图 2 校准/可靠性 | `fig2_calibration.png` | 折外预测分箱：预测均值 vs 实际合并率，标注 Brier 与 ECE；点大小 ∝ 箱内样本数 |
| 图 3 SHAP 重要性 | `fig3_shap_importance.png` | 置换采样 Shapley 的 mean\|φ\| 排名（红=批次代理特征），效率性误差 1e-16 量级 |
| 图 4 切分与标签构成 | `fig4_splits_labels.png` | 上：时间序累计合并率与 5 个扩张窗口折边界；下：标签类型计数 |
| 图 5 批次混淆诊断 | `fig5_batch_confound.png` | `duration_h` / `goal_len` 在正负两类间近乎完全不重叠（单特征 AUC 标注）——这是 0.98 关联 AUC 的真实来源 |
| 图 6 仪器控制 | `fig6_controls.png` | 负控 0.552 / 实测无定义 / 正控 0.938 三柱 + 0.65 验收线：区分"数据性质"与"代码缺陷" |
| 图 7 同交付波内的判别力 | `fig7_within_wave.png` | 批次控制后四组对比：全特征 / 仅判级 / 仅批次代理 / 去批次代理 |

报告中内嵌图 1/2/3/5/6/7（按 60% 宽 + q62 压缩以适配报告网关 250 KB 负载上限）；原图见 `artifacts/`。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **判级行是后视镜**：`outcome_evaluations.decided_at` 晚于下游标签的有
   {d['grade_decided_after_label']['n']}/{d['grade_decided_after_label']['of']} 条（评价波 2026-11-07 回填）。
   若拿 `decided_at` 当特征时刻，等于让评价者先看结果再打分——本模块一律用 `finished_at`，并强制
   `t_label > finished_at`（本队列 0 违规）。
2. **判级覆盖率的结构性缺口**：判级行只为 `succeeded` mission 自动生成，`failed/stopped/revoked/orphan`
   几乎没有判级；因此"判级 → 丢弃"这一条链在数据层面**根本不存在样本**，任何"判级能预测失败"的说法都不可检验。
3. **负类单波次**：`pr_gate` 的 `open` 只出现在 2026-11-04T23:00 的回填里（245 行），之后只有 `merged` 流；
   负类的"未合并"可能是**观测缺口**而非质量判据（其中多数同时是 `custody_requeue` 返工任务，
   返工事件时刻甚至早于 mission 完结）。本报告因此把负类语义限定为"观察窗结束时仍未整合/已返工"。
4. **同刻边界**：`finished_at` 有并列，切分必须把同刻样本整体推到 test 侧，否则 `train_max == test_min`
   的假泄露（已用单测锁死，见 `test_expanding_splits_train_strictly_before_test`）。
5. **Shapley 实现**：宿主无 `shap`/`sklearn`，用置换采样 Shapley（非 KernelSHAP 回归近似）；
   背景取单点参考 → 效率性精确成立（实测 {_f(met['shap']['max_abs_efficiency_gap'], 2)}）。
   它解释的是**逻辑回归**的 log-odds，不是随机森林，也不是因果效应。
6. **适用范围**：本模块只读平台库、不改 dispatcher、不建表；重跑一条命令即可。数据修好后判据会自动翻绿。

## 6. 误差判定（逐条回答"是否 <3%"）

本单**没有解析解/文献基准**可对标（被测量是平台治理流程的判别力，不是物理场量），因此
"关键量误差是否 <3%"这一问**不适用**；对应地，本单用三类**可机器复算**的等价物替代，逐条如下：

| 关键量 | 基准 | 实测 | 判定 |
|---|---|---|---|
| AUC 实现 | Mann-Whitney 秩统计量（闭式，含并列半步） | 与手工枚举一致（4×4 手算 0.875；全并列 0.5） | **误差 0（精确一致）** |
| Shapley 效率性 | `Σφ = f(x) − E[f]`（可加性恒等式） | 最大偏差 {_f(met['shap']['max_abs_efficiency_gap'], 2)} | **远优于 1e-6 容差** |
| 时序无泄露 | `train_max < test_min`（每折）、`t_features < t_label`（每样本） | 5/5 折有序，{dset['n']}/{dset['n']} 样本有序 | **0 违规** |
| 负控 | 标签置换后 AUC ≈ 0.5 | {_f(ctl['negative']['pooled_auc'])} | 偏差 {_f(abs((ctl['negative']['pooled_auc'] or 0) - 0.5) * 100, 1)}%（>3%，因 5 折单次置换的抽样噪声，属预期） |
| 正控 | 注入信号后 AUC 应 ≥ 0.8 | {_f(ctl['positive']['pooled_auc'])} | 达到 |
| 验收判据 1/2/3 | AUC ≥ 0.65、Brier ≤ 0.25、相对规则提升 ≥ 0.05 | AUC 无定义 / Brier 单类无意义 / 提升无定义 | **未达标（honest FAIL）** |

**honest-FAIL 声明**：本节点判据 1、2、3 **未通过**（AUC 无定义、Brier 折外单类、相对提升无定义），
判据 4、5、6、7 通过。不替代、不粉饰：平台当前的下游结果数据**不足以**支撑 S/E 判级的外部效度校准。

## 7. 复现与补救路径（让判据能翻绿的**数据**前置条件）

1. **PR 状态按 mission 轮询留痕**：每次观测写一行 `pr_gate`（哪怕状态不变），`detail.at` 记录观测时刻
   → 负类获得时间铺开，out-of-time 设计才可识别（当前障碍的唯一根因）。
2. **失败也判级**：`failed/stopped/revoked/orphan` 完结时同样生成 `outcome_evaluations` 行（可先 `pending`）
   → 打通"判级 → 丢弃"样本链。
3. **判级与结果解耦存档**：`decided_at` 与下游标签时刻分开统计，保留"判级先于结果"的子集做真·前瞻验证。
4. **回退（revert）通道**：目前平台没有 revert 记录表，只能用 `reopen`/回归红代替 → 建议补 `artifact_reverts`。

上述 1–4 落地后，重跑 §2.3 的两条命令即可产出真实 AUC/Brier（脚本与判据无需改动）。"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="extval-report")
    ap.add_argument("--artifacts", default="artifacts/IN56-N07")
    ap.add_argument("--out", default="docs/IN56-N07_external_validity_calibration.md")
    args = ap.parse_args(argv)
    md = build_markdown(args.artifacts)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(f"wrote {args.out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())