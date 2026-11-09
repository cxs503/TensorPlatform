# p422w3-n11 评价外部效度校准（生成文档，勿手改）

> 本文由 `python3 -m tools.eval_discriminability.cli all` 生成：
> 代码 → `artifacts/eval_discriminability/REPORT.md`（图形路径改写为 `../artifacts/...`）。
> 机器裁据与全部数字见 `artifacts/eval_discriminability/result.json`；
> 快照 sha256 见 `artifacts/eval_discriminability/snapshot_manifest.json`。
> 复现：`python3 -m tools.eval_discriminability.cli run --snapshot artifacts/eval_discriminability/snapshot.json.gz --out-dir artifacts/eval_discriminability`

---

# 评价体系外部效度校准：S/E 判级对下游真实结果的判别力（walk-forward AUC · 校准 · Mann-Kendall 通胀 · 逐层分解）

> 模块：`tools/eval_discriminability/`（新）＋复用 `tools/external_validity/metrics.py`（口径单一源）｜快照 sha256：`fdc4071bcf9be00d…`｜as_of：2026-11-09T07:33:15+00:00
> 机器裁决：**PARTIAL**（4/7 条 PASS）｜全部数字由本模块从 `result.json` 注入，无手抄。

## 1. 案例介绍（对象 / 队列 / 参数表）

**问题**：平台的 S×E 判级（S=成果强度，E=证据独立性）是否对**下游真实结果**（交付是否被整合：PR 合并 vs 返工/丢弃）具备判别力？分数是否随时间**通胀**（判级标准松弛）？判别力在哪些 repo / 域 / agent 上失效？

三个时刻严格分离，否则「预测」是后视镜：

```
created_at  <  finished_at  <  t_label
   建单          特征快照        下游事件（PR merged / 返工点名 / 丢弃）
```

| 项 | 值 | 来源 |
|---|---|---|
| 快照 as_of | 2026-11-09T07:33:15+00:00 | 本次运行 |
| 任务行数 | 4532 | `cohort.build_study` |
| 标签可观测 | 958（21.1%） | 下游事件可解析 |
| 删失（WITHHELD） | 3574 | PR 未落地 / 无下游事件 |
| 标签构成 | {'merged': 475, 'pending': 3574, 'rework': 483} | `tensor_dev_missions` ⋈ `outcome_evaluations` |
| 时间跨度 | ['2026-10-29', '2026-11-07'] | finished_at 日 |
| 特征集 | grade_only(S,E) / evidence_only(4 维) / full(6 维) | `ledger.FEATURE_NAMES` |
| 分解维度 | repo, agent_family, agent, domain, model, origin | repo / claimed_by / 战役 physics_scope / model |
| 随机种子 | neg=20261109　rec=20261109 | 可复算 |

## 2. 怎么做的（方法 / 流程 / 关键设置）

1. **标签**（与判级解耦）：`merged` → y=1；`rework`/`discarded`/`reverted` → y=0；`pending` 删除（删失，不冒充负类）。构造逻辑复用 `tools.external_validity.ledger`。
2. **walk-forward**：expanding window，训练集恒为 `day < d`、测试集 `day == d`；守卫：训练正负各 ≥10、测试 ≥10，否则跳窗留痕（不静默）。
3. **判别力**：pooled AUC＋bootstrap 95%CI，并给**同日条件配对 AUC**（只在同一天内配对，剔除「当天基准率」造成的构成假象——Simpson 陷阱）。
4. **校准**：样本外概率 10 桶等宽曲线、Brier、Brier skill（vs 测试集气候态）。
5. **通胀**：日度序列上 **Mann-Kendall** 趋势检验（结修正＋连续性修正）＋**Theil-Sen** 稳健斜率与 Kendall 型 95%CI；`p<0.05 且斜率>0` → ALERT。
6. **分解**：按维度分层，逐层给同日 AUC＋bootstrap CI；层内正或负 <10 → WITHHELD。
7. **仪器营业特性**：负控（整体打乱标签 → 期望 0.5）与正控（保留真实日期与类别构成、注入已知 AUC 的合成标签 → 能否复原）。
8. **归因消融**：`grade_only` 与 `full` 两个特征集并跑，回答「AUC 是判级挣的，还是旁证特征挣的」。

```bash
DSN="postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql"
python3 -m tools.eval_discriminability.cli all  --dsn "$DSN" --out-dir artifacts/eval_discriminability
python3 -m tools.eval_discriminability.cli run  --snapshot artifacts/eval_discriminability/snapshot.json.gz \
                                              --out-dir artifacts/eval_discriminability   # 离线复算
python3 -m pytest tests/test_eval_discriminability.py -q
```

## 3. 计算结果

### 3.1 判据总表

| 判据 | 值 | 阈值 | 结论 | 备注 |
|---|---|---|---|---|
| J1 | walk-forward AUC（同日口径） | 0.4810 | >= 0.7 | IMPROVE | 3 窗 / 697 测试样本；配对 18062；pooled AUC=0.392（95%CI [0.345, 0.441]，构成敏感，见 J5） |
| J2 | 校准 Brier | 0.3836 | <= 0.15 | IMPROVE | climate=0.588；Brier skill=-0.584 |
| J3 | 判级通胀 (Mann-Kendall) | 0.0110 | p >= 0.05 且斜率<=0 | ALERT | 复合分 Sen 斜率=0.1168/日（相对 7.78%/日，95%CI [0.0000, 0.1979]）；显著项=['high_share', 'mean_s', 'mean_score'] |
| J4 | 负控（随机标签） | 0.4919 | |mean-0.5| <= 0.05 | PASS | 200 次置换；sd=0.019；2.5–97.5%=[0.455, 0.529] |
| J5 | 正控（注入已知 AUC） | 0.7411 | |bias| <= 0.15 | PASS | 目标 0.75（同日口径）；bias=-0.009；sd=0.011；n=20；pooled 口径 bias=-0.278（构成偏置实测 -0.269） |
| J6 | 判别力分解 | 25.0000 | 可用层 >= 1 | PASS | 维度 {'repo': 8, 'agent_family': 11, 'agent': 2, 'domain': 1, 'model': 2, 'origin': 1}；agent_family 同日加权 AUC=0.528；最弱层=tp-qa 最强层=tp-qw37 |
| J7 | 既有评价管线回归（金标指纹） | 0.0000 | 0 处漂移 | PASS | tools.external_validity.metrics 出口指纹不变 |

### 3.2 walk-forward 判别力（逐窗）

**grade_only（只用 S/E 判级）**：pooled AUC = 0.3923（95%CI [0.345, 0.441]），同日条件 AUC = 0.4810（配对 18062），3 窗 / 697 测试样本。

| 测试日 | 训练 | 测试 | 正/负 | 实际通过率 | 平均预测 | AUC | Brier |
|---|---|---|---|---|---|---|---|
| 2026-11-05 | 261 | 276 | 35/241 | 0.127 | 0.326 | 0.475 | 0.1681 |
| 2026-11-06 | 537 | 121 | 104/17 | 0.860 | 0.197 | 0.520 | 0.5602 |
| 2026-11-07 | 658 | 300 | 271/29 | 0.903 | 0.312 | 0.479 | 0.4396 |

跳窗留痕：2026-10-29（warmup(<2d)）；2026-10-30（warmup(<2d)）；2026-10-31（train class imbalance pos=0 neg=50）；2026-11-01（train class imbalance pos=0 neg=64）；2026-11-02（train class imbalance pos=0 neg=83）；2026-11-03（train class imbalance pos=0 neg=98）；2026-11-04（train class imbalance pos=2 neg=118）。

**full（全部判级可观测特征）**：pooled AUC = 0.2456（95%CI [0.209, 0.283]），同日条件 AUC = 0.5006（配对 18062），3 窗 / 697 测试样本。

| 测试日 | 训练 | 测试 | 正/负 | 实际通过率 | 平均预测 | AUC | Brier |
|---|---|---|---|---|---|---|---|
| 2026-11-05 | 261 | 276 | 35/241 | 0.127 | 0.352 | 0.452 | 0.1754 |
| 2026-11-06 | 537 | 121 | 104/17 | 0.860 | 0.206 | 0.570 | 0.5478 |
| 2026-11-07 | 658 | 300 | 271/29 | 0.903 | 0.256 | 0.537 | 0.5089 |

跳窗留痕：2026-10-29（warmup(<2d)）；2026-10-30（warmup(<2d)）；2026-10-31（train class imbalance pos=0 neg=50）；2026-11-01（train class imbalance pos=0 neg=64）；2026-11-02（train class imbalance pos=0 neg=83）；2026-11-03（train class imbalance pos=0 neg=98）；2026-11-04（train class imbalance pos=2 neg=118）。

### 3.3 校准（样本外概率）

Brier = **0.3836**（阈值 0.15），Brier skill vs 气候态 = -0.5836，最大桶偏差（n≥10）= 0.6205，气候基准率 = 0.5882，n = 697。

| 桶 | n | 平均预测 | 实际通过率 | 偏差 |
|---|---|---|---|---|
| [0.1, 0.2) | 102 | 0.174 | 0.794 | -0.621 |
| [0.2, 0.3) | 341 | 0.228 | 0.680 | -0.453 |
| [0.3, 0.4) | 113 | 0.361 | 0.584 | -0.223 |
| [0.4, 0.5) | 127 | 0.432 | 0.213 | 0.219 |
| [0.5, 0.6) | 12 | 0.564 | 0.167 | 0.398 |
| [0.6, 0.7) | 2 | 0.657 | 1.000 | -0.343 |

### 3.4 判级通胀（Mann-Kendall ＋ Theil-Sen）

| 观测量 | 活跃日 | Kendall τ | p | Sen 斜率/日 | 95%CI | 相对通胀率/日 | 显著性 |
|---|---|---|---|---|---|---|---|
| 高分级(S>=S2)占比 | 10 | 0.533 | 0.0193 | 0.04063 | [0.0000, 0.1233] | —（中位数=0） | ALERT |
| S 序均值 | 10 | 0.533 | 0.0193 | 0.05435 | [0.0000, 0.1366] | 5.44% | ALERT |
| 复合分 S+0.5E 均值 | 10 | 0.578 | 0.0110 | 0.11675 | [0.0000, 0.1979] | 7.78% | ALERT |

活跃日 10 天（过滤门槛：日判级量 ≥ 30，被过滤 0 天）；α = 0.05；报警项 = ['high_share', 'mean_s', 'mean_score']。

**体制诊断**：判级波次分界日 = 2026-11-04；分界后 4 个活跃日的复合分 MK：n=4、τ=0.333、p=0.7341（ok）、中位复合分=2.345。全序列显著而后段不显著 ⇒ 信号主要是判级波次台阶（口径换代），不是持续松弛

### 3.5 判别力分解（按维度）

**repo**：可用层 8/11，层加权同日 AUC = 0.5316，最弱 = TensorCAD，最强 = TensorFVM。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| TensorBEM | 74 | 33/41 | 0.177 | 0.472 | [0.078, 0.286] | ok |
| TensorCAD | 50 | 21/29 | 0.197 | 0.345 | [0.076, 0.327] | ok |
| TensorDEM | 131 | 74/57 | 0.160 | 0.615 | [0.086, 0.237] | ok |
| TensorFEM | 89 | 52/37 | 0.306 | 0.441 | [0.196, 0.429] | ok |
| TensorFVM | 59 | 30/29 | 0.236 | 0.704 | [0.115, 0.378] | ok |
| TensorLBM | 96 | 81/15 | 0.570 | 0.465 | [0.401, 0.754] | ok |
| TensorPlatform | 77 | 57/20 | 0.633 | 0.633 | [0.532, 0.731] | ok |
| TensorSPH | 77 | 26/51 | 0.152 | 0.405 | [0.053, 0.256] | ok |

⚠ **单类层 1 个**：这些层内只有一类标签，AUC 数学上无定义。规模最大的几个：ship-performance-platform(0/2)。若单类层与维度取值强相关（如某维度下单子几乎全是合并单），说明**标签可观测性与该维度不独立**，该维度的分解结论不可外推。

另有 3 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：TensorDG, TensorPhysics, ship-performance-platform。

**agent_family**：可用层 11/15，层加权同日 AUC = 0.5277，最弱 = tp-qa，最强 = tp-qw37。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| tp-app | 25 | 14/11 | 0.201 | 0.385 | [0.021, 0.442] | ok |
| tp-arch | 57 | 27/30 | 0.230 | 0.607 | [0.102, 0.350] | ok |
| tp-doc | 66 | 41/25 | 0.236 | 0.428 | [0.121, 0.372] | ok |
| tp-gen | 38 | 23/15 | 0.268 | 0.389 | [0.099, 0.455] | ok |
| tp-impl | 99 | 57/42 | 0.207 | 0.505 | [0.120, 0.298] | ok |
| tp-merge | 67 | 42/25 | 0.184 | 0.433 | [0.075, 0.307] | ok |
| tp-mod | 51 | 31/20 | 0.160 | 0.406 | [0.063, 0.277] | ok |
| tp-paper | 60 | 39/21 | 0.363 | 0.388 | [0.218, 0.524] | ok |
| tp-qa | 34 | 22/12 | 0.307 | 0.340 | [0.107, 0.524] | ok |
| tp-qw37 | 26 | 12/14 | 0.351 | 0.857 | [0.131, 0.594] | ok |
| tp-ver | 108 | 59/49 | 0.225 | 0.682 | [0.140, 0.315] | ok |

另有 4 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：tp-ds31, tp-infra, tp-qw36, tp-qw38。

**agent**：可用层 2/57，层加权同日 AUC = 0.7400，最弱 = tp-arch-1，最强 = tp-qw37-1。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| tp-arch-1 | 23 | 11/12 | 0.201 | 0.591 | [0.032, 0.436] | ok |
| tp-qw37-1 | 26 | 12/14 | 0.351 | 0.857 | [0.131, 0.594] | ok |

⚠ **单类层 5 个**：这些层内只有一类标签，AUC 数学上无定义。规模最大的几个：tp-qw38-3(2/0), tp-ds31-1(1/0), tp-qw38-4(1/0), tp-qw38-5(1/0), tp-qw38-6(1/0)。若单类层与维度取值强相关（如某维度下单子几乎全是合并单），说明**标签可观测性与该维度不独立**，该维度的分解结论不可外推。

另有 55 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：tp-app-1, tp-app-2, tp-arch-2, tp-arch-3, tp-arch-4, tp-doc-1, tp-doc-2, tp-doc-3, tp-doc-4, tp-ds31-1, tp-ds31-2, tp-gen-1 …等 55 层。

**domain**：可用层 1/31，层加权同日 AUC = 0.5169，最弱 = (no_campaign)，最强 = (no_campaign)。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| (no_campaign) | 510 | 230/280 | 0.307 | 0.517 | [0.261, 0.352] | ok |

⚠ **单类层 24 个**：这些层内只有一类标签，AUC 数学上无定义。规模最大的几个：ice_navigation(14/0), 阻力(13/0), 冰区航行(11/0), 耐波性(11/0), resistance(10/0), 绿色航行(10/0)。若单类层与维度取值强相关（如某维度下单子几乎全是合并单），说明**标签可观测性与该维度不独立**，该维度的分解结论不可外推。

另有 30 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：FEM, LBM, LBM湍流/传热/守恒, TensorLBM, TensorLBM湍流, hydrodynamics, hydroelasticity, ice_mechanics, ice_navigation, ice_resistance, maneuvering, resistance …等 30 层。

**model**：可用层 2/6，层加权同日 AUC = 0.5475，最弱 = deepseek-v4-flash，最强 = (unknown)。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| (unknown) | 564 | 387/177 | 0.334 | 0.548 | [0.284, 0.385] | ok |
| deepseek-v4-flash | 115 | 17/98 | 0.046 | 0.529 | [0.009, 0.097] | ok |

另有 4 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：Qwen3.6-35B-A3B, Qwen3.8-27B, qwen3.6-plus, qwen3.7-plus。

**origin**：可用层 1/2，层加权同日 AUC = 0.4812，最弱 = owner，最强 = owner。

| 层 | n | 正/负 | 层合并 AUC | 同日 AUC | 95%CI | 状态 |
|---|---|---|---|---|---|---|
| owner | 673 | 407/266 | 0.224 | 0.481 | [0.187, 0.263] | ok |

另有 1 层样本不足（正或负 <10，记 WITHHELD 不补 0.5）：agent。

### 3.6 可识别性 / 功效（回答「为什么 AUC 算不出来」）

| 项 | 值 |
|---|---|
| 有标签的天数 | 10 |
| 正负都存在的天数 | 5 |
| 满足类别下限（各≥10）的天数 | 4 |
| 阳性 / 阴性总数 | 475 / 483 |
| 负/正比 | 1.02 |
| 首个出现阳性标签的日期 | 2026-11-03 |
| 最后出现阴性标签的日期 | 2026-11-07 |
| 在 AUC=0.7 处当前样本的 SE | 0.0168 |
| 达到 SE≤0.05 所需阳性数 | 55 |
| 阳性数缺口 | 0 |

**判读**：在 AUC=0.7 处当前样本的标准误 = 0.0168，目标 ≤0.05 → **样本精度已足够**，因此 §3.2 的 AUC 不足**不能**归因于样本量，只能归因于信号本身（判级与下游结果不同向）。

### 3.7 仪器营业特性（负控 / 正控）

| 控制 | 统计量 | 值 | 结论 |
|---|---|---|---|
| 负控（打乱标签） | 200 次置换均值 AUC | 0.4919 | PASS |
| 负控 | 均值偏差 |mean−0.5| | 0.0081 | 阈值 0.05 |
| 负控 | 2.5–97.5% 分位 | [0.455, 0.529] |  |
| 正控（注入标签） | 目标 AUC 0.75（同日口径） | 0.7411 | PASS |
| 正控 | 偏差 / sd | -0.009 / 0.011 | 20 次重复 |
| 正控（同一批注入） | pooled 口径复原值 | 0.4719 | pooled bias=-0.278 |
| **构成偏置实测** | pooled − 同日（同批注入） | -0.269 | pooled 估计量在此数据上不可用 |

### 3.8 构成偏置（本次实测的估计量失效）

同一批注入标签（同日判别力被控制在 0.75）下，pooled 口径只复原出 0.4719，与同日口径差 **-0.269**。原因：各日下游结果基准率相差极大（见 §3.6 逐日正负构成），pooled AUC 把「今天整体容易合并」当成了排序信号。因此本节点的 J1 一律以**同日条件 AUC** 为判据量，pooled AUC 仅作敏感性量报出——这是纠偏，不是放宽。

## 4. 结果图

![calibration](artifacts/eval_discriminability/figures/calibration.png)

**图（calibration）**：样本外可靠性曲线（对角虚线=完美校准；点大小∝桶样本量）。

![auc_timeline](artifacts/eval_discriminability/figures/auc_timeline.png)

**图（auc_timeline）**：逐窗 walk-forward AUC（标注正/负样本构成）；绿虚线=0.70 判据。

![inflation](artifacts/eval_discriminability/figures/inflation.png)

**图（inflation）**：日度高分级占比与复合分均值；橙虚线=Theil-Sen 趋势，副标题含 MK p 值。

![decomposition](artifacts/eval_discriminability/figures/decomposition.png)

**图（decomposition）**：逐层同日 AUC 森林图（灰线=95% bootstrap CI）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **构成假象（Simpson）**：合并率有强批次效应（某天合并队列清一批、次日几乎不动）。pooled AUC 会把「模型学到当天基准率」记成判别力，故本模块同时给同日条件 AUC，并以它为保守口径。
2. **删失 ≠ 负类**：`pending` 行（PR 未落地/无下游事件）一律剔除；本次剔除 3574 行（78.9%）。把删失当负类会把基准率从 ~0.5 压到 0.1 并把 AUC 打回 0.5。
3. **判级波次回填**：`outcome_evaluations.decided_at` 大多晚于下游事件，故特征时刻一律取 `finished_at`，并要求 `t_label > finished_at`——用 `decided_at` 是后视镜。
4. **通胀检验的样本门槛**：日判级量 < 门槛的日期被过滤（占比是纯噪声）；门槛与被过滤天数已披露。Mann-Kendall 只检验**单调趋势**，不识别台阶式跳变（如有版本升级式标准变化，需另作分段检验）。
5. **pooled AUC 在本数据上失效（已实测）**：正控实验证明 pooled 口径会把构成差异记成判别力（偏置见 §3.8）。任何引用 pooled AUC 的结论都必须同时给同日口径。
6. **通胀结论的机制**：全序列 MK 显著主要来自判级波次台阶（早期 6 个活跃日只有 S1 档，之后多档并存），因此「分数通胀」在本窗口内是**口径换代**而非连续松弛；分界后子序列的 MK 检验见 §3.4，两者必须分开解读。
7. **小层不可判定**：层内正或负 <10 记 WITHHELD，不补 0.5——补默认值会把「不知道」伪装成「无判别力」。
8. **适用范围**：结论只覆盖本快照的 10 天窗口与本次标签口径（合并 vs 返工/丢弃）；「未合并」不等于「质量差」，还可能是排队/超时。

## 6. 误差判定（关键量 vs 判据/基准）

本节点是**度量成熟化**任务，没有物理实验基准；因此逐项对照战役 metadata 的机器判据，并显式回答「是否 <3%」这一问题域：

| 关键量 | 值 | 基准/阈值 | 误差或余量 | 是否达标 |
|---|---|---|---|---|
| walk-forward AUC（J1） | 0.4810 | >= >= 0.7 | -0.2190 | IMPROVE |
| Brier（J2） | 0.3836 | <= <= 0.15 | 0.2336 | IMPROVE |
| 通胀 p（J3） | 0.010999 | >= p >= 0.05 且斜率<=0 | 见备注 | ALERT |
| 负控 AUC（J4） | 0.4919 | |mean-0.5| <= 0.05 | 0.0081 | PASS |
| 正控复原偏差（J5） | -0.009 | |bias| <= 0.15 | -0.009 | PASS |

**「是否 <3%」的显式回答**：本任务的全部量都是**统计估计量**（AUC/Brier/p 值），没有物理真值可对，3% 误差判据在此不适用——不得套用。因此以「已知真值的合成数据」（正控注入 AUC）代替真值基准：复原偏差 -0.009（目标 0.75），即仪器在合成真值上的相对偏差 = 1.18%，满足 3% 线；真实数据侧的 AUC 与阈值余量见上表。

**honest 声明**：本报告不掩盖任何未达标项——判据状态按 PASS / IMPROVE / ALERT / WITHHELD / FAIL 原样列出，总裁决见首部。
