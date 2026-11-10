# 派发前可行性先验校准器：判据特征 → 验收概率的校准与背测

**节点** P475W1-N03 · **仓库** TensorPlatform · **口径** 零领域语义（纯平台机制）

## ① 案例介绍：研究对象与数据几何

战役播种（campaign seeding）在把节点派发给执行者之前，只能拿到**计划侧信息**：判据怎么写、依赖多深、要改哪个仓、提案有多新。本节点把这四类信息做成特征，估计该节点**通过验收的概率**，并且——这是关键——把它**校准**：报出的数字要是「频率意义上真的对」的概率，而不是一个排序分数。

数据：线上 ``campaign_nodes`` ⋈ ``campaign_plans``（只读，单一 SQL）3118 个节点 / 328 个计划，时间窗 2026-10-31T03:02:15+00:00 … 2026-12-14T04:02:42+00:00。状态分布 blocked=250、done=1713、in-flight=761、pending=394。

标签（主口径 primary）：done=1 ; blocked|failed=0 ; pending|in-flight|orphan=剔除(右删失)；得到 n=1963（正 1713 / 负 250）。
敏感性口径（strict）：done=1 ; blocked 且 block_reason 属可行性失败=0 ; 其余剔除(含 infra 运维事故)；n=1747（正 1713 / 负 34）。

**参数表**（16 维特征，全部为平台结构量）

| 族 | 特征 | 含义 |
|---|---|---|
| criteria_complexity | `criteria_n_log` | 判据条数 log1p |
| criteria_complexity | `criteria_structured_share` | 字典型（可机读）判据占比 |
| criteria_complexity | `criteria_numeric_share` | 带数值阈值+比较运算符的硬指标占比 |
| criteria_complexity | `criteria_bool_share` | 布尔/PASS 型门占比 |
| criteria_complexity | `has_criteria` | 是否写了判据 |
| dependency_topology | `dep_depth` | 同计划依赖链最长深度 |
| dependency_topology | `dep_count` | 直接依赖数 |
| dependency_topology | `dep_fanout` | 被依赖数（关键路径压力） |
| repo_maturity | `repo_prior_n_log` | 同仓既往有结局节点数 log1p |
| repo_maturity | `repo_prior_success` | 同仓既往验收率（Laplace 平滑，仅用更早样本） |
| proposal_novelty | `novelty` | 与更早节点的文本最大余弦距离（1=史上首见） |
| context | `plan_progress` | 同计划更早节点的 done 占比 |
| context | `risk_ord` | risk 序数 low/medium/high → 0/0.5/1 |
| context | `is_gate` | gate/checkpoint 节点 |
| context | `is_maturity` | 成熟化节点标记 |
| context | `budget_hours_log` | 预算小时 log1p |

## ② 怎么做的：方法 / 流程 / 工具链

1. **特征（因果）**：一次按 ``(created_at, node_id)`` 因果序扫描，所有时序量（同仓成功率、新颖度、同计划进度）只用**严格更早**的样本；新颖度用稀疏矩阵分块算最大余弦（与在线单点实现数学同义）。
2. **基学习器**：L2 逻辑回归（Newton/IRLS，截距不正则），16 维特征 z-score 标准化（统计量只来自训练窗）→ 原始分数（logit 尺度）。
3. **校准**：Platt scaling（2 参仿射）与 isotonic regression（PAV 单调回归）两条路线，**拟合在训练窗内按计划分组 K 折 out-of-fold 分数上**（每条分数都由没见过它的模型产生）。
4. **先验漂移修正**：``Δ = logit(π_cal) − logit(π_train)`` 平移到对数几率上（``π_cal`` 取最近一个已完整结局窗口的验收率，派发时刻可得）。
5. **背测**：按**计划的创建时刻**三切分（train 早 / calib 中 / test 晚），计划互不相交；主指标落在最晚计划组成的测试窗。
6. **负控**：标签在样本内随机置换，整套管线（含 OOF、校准、先验平移）重跑 200 次，得 AUC/BSS 零分布与经验 p 值。
7. **工具链**：纯 numpy（无 sklearn）；``pytest`` 20 条用例；matplotlib(Agg) 出图；``python3 -m tools.pre_dispatch_prior {run|predict|ablate}``。

切分尺寸 train/calib/test = 1182/394/387，计划数 {'train': 126, 'calib': 41, 'test': 49}；各类别数 {'train': (996, 186), 'calib': (364, 30), 'test': (353, 34)}；测试窗实测验收率 0.9121。

## ③ 计算结果（程序化注入，零手抄）

| 变体 | Brier | Brier skill vs 训练窗气候 | AUC | log loss | ECE(等宽10) | ECE(等频10) | 预测均值 |
|---|---|---|---|---|---|---|---|
| `climatology` | 0.0850 | 0.0000 | 0.5000 | 0.3186 | 0.0695 | — | 0.8426 |
| `raw` | 0.1133 | -0.3336 | 0.6628 | 0.3932 | 0.1905 | 0.1843 | 0.7279 |
| `platt` | 0.0998 | -0.1742 | 0.6628 | 0.3620 | 0.1548 | 0.1499 | 0.7623 |
| `isotonic` | 0.1024 | -0.2055 | 0.6436 | 0.3690 | 0.1516 | 0.1516 | 0.7606 |
| `platt_prior` | 0.0784 | 0.0772 | 0.6628 | 0.2905 | 0.0486 | 0.0504 | 0.8773 |
| `isotonic_prior` | 0.0801 | 0.0571 | 0.6436 | 0.2965 | 0.0354 | 0.0354 | 0.8768 |

诊断变体（只在中段校准窗上拟合，用来记录失败模式）：

| 变体 | Brier | Brier skill | AUC | ECE(等宽10) |
|---|---|---|---|---|
| `platt_window` | 0.0800 | 0.0583 | 0.6628 | 0.0091 |
| `isotonic_window` | 0.0764 | 0.1014 | 0.6626 | 0.0102 |

OOF 诊断：n=1182，5 折（plan（同计划节点同折，避免同波次泄漏）），OOF AUC 0.6772；OOF 校准斜率 0.6763；中段单窗**无约束**斜率 -0.2204（OOF 校准斜率为正且保序；中段单窗无约束拟合斜率 -0.220 → 退化/反号（会把预测排名翻转），故校准数据必须走 OOF）；中段窗 raw AUC 0.5546。

shuffle 负控：null AUC 0.5005 ± 0.0344，null BSS 最大 0.0210；观测 AUC 0.6628（p=0.0050）、观测 BSS 0.0772（p=0.0050）。

判据裁决：

| 判据 | 观测值 | 阈值 | 结果 |
|---|---|---|---|
| auc_test_min | 0.6628 | >= 0.6 | PASS |
| brier_skill_platt_min | 0.0772 | >= 0.0 | PASS |
| ece_reduction_min | 0.1419 | >= 0.0 | PASS |
| null_auc_within_tolerance | 0.5005 | |x-0.5|<= 0.05 | PASS |
| null_bss_max | 0.0210 | <= 0.05 | PASS |
| p_value_bss | 0.0050 | <= 0.05 | PASS |
| platt_slope_positive | 0.6763 | > 0.0 | PASS |
| min_test_samples | 387 | >= 100 | PASS |

合计 8/8 PASS，ALL=PASS。

对照实验（校准数据来源，同一测试窗）：

| 变体 | 斜率 | Brier | Brier skill | AUC | ECE | 排名是否被翻转 |
|---|---|---|---|---|---|---|
| `window_unregularized` | -0.220 | 0.0812 | 0.0441 | 0.3372 | 0.0152 | 是 |
| `window_unregularized_prior_shift` | -0.220 | 0.0834 | 0.0185 | 0.3372 | 0.0533 | 是 |
| `window_regularized` | +0.050 | 0.0800 | 0.0583 | 0.6628 | 0.0091 | 否 |
| `window_regularized_prior_shift` | +0.050 | 0.0827 | 0.0268 | 0.6628 | 0.0515 | 否 |
| `oof` | +0.676 | 0.0998 | -0.1742 | 0.6628 | 0.1548 | 否 |
| `oof_prior_shift` | +0.676 | 0.0784 | 0.0772 | 0.6628 | 0.0486 | 否 |

结论：单窗无正则拟合斜率反号（AUC 被翻转）：校准数据来源必须用 OOF；单窗正则化只稳住排名，水平拟合靠先验平移补齐


敏感性（strict 口径，剔除 infra 运维事故）：

尺寸 {'train': 1034, 'calib': 419, 'test': 294}，类别数 {'train': (1006, 28), 'calib': (416, 3), 'test': (291, 3)}。

| 变体 | Brier | Brier skill | AUC |
|---|---|---|---|
| `climatology` | 0.0104 | 0.0000 | 0.5000 |
| `raw` | 0.0109 | -0.0504 | 0.5590 |
| `platt` | 0.0108 | -0.0357 | 0.5590 |
| `isotonic` | 0.0107 | -0.0343 | 0.5590 |
| `platt_prior` | 0.0101 | 0.0271 | 0.5590 |
| `isotonic_prior` | 0.0101 | 0.0264 | 0.5590 |

strict 口径剔除 infra 运维事故（agent 丢失/LLM 报错），只剩「任务本身没过验收」，负例极少、方差大——只作敏感性对照，不作主判据

### 派发前实盘（当前 pending 节点的先验）

对当前 **394** 个 `pending` 节点按因果序算先验（每个节点只看比它更早的历史）：p 均值 0.852，P10/P50/P90 = 0.787 / 0.853 / 0.914。

| 最低先验 5 个节点 | plan | 判据条数 | 依赖数 | p_accept |
|---|---|---|---|---|
| 1258 | 211 | 0 | 11 | 0.601 |
| 2388 | 429 | 4 | 9 | 0.645 |
| 1105 | 179 | 7 | 10 | 0.695 |
| 2416 | 434 | 0 | 0 | 0.754 |
| 1941 | 359 | 5 | 0 | 0.755 |

| 最高先验 5 个节点 | plan | 判据条数 | 依赖数 | p_accept |
|---|---|---|---|---|
| 611 | 93 | 8 | 2 | 0.969 |
| 487 | 74 | 1 | 2 | 0.961 |
| 271 | 34 | 5 | 1 | 0.960 |
| 486 | 74 | 1 | 1 | 0.959 |
| 628 | 95 | 7 | 1 | 0.954 |

## ④ 结果图

五张图（同目录 PNG）：`fig_reliability.png`（可靠性图，等宽/等频双视角 + 每点样本数）、`fig_roc.png`（测试窗 ROC）、`fig_null_control.png`（shuffle 零分布 vs 观测值）、`fig_variants.png`（Brier/BSS/ECE 四变体对比）、`fig_coefficients.png`（标准化系数）。图内文字用英文（宿主无 CJK 字体，中文会渲染成方框——踩过的坑）。

## ⑤ 注意点：坑、局限、适用范围

1. **单窗校准会翻转排名**（本节点最大的一课）：中段窗口的负例只有 20~30 个且局部判别力≈0，无约束 Platt 把斜率学成负数（见对照实验 `window_unregularized`：斜率 -0.2204，测试窗 AUC 掉到 0.33 = 1−0.67）。校准数据来源改为 OOF 后斜率回到正、排名保住。
2. **类别极不平衡**：主口径正例占 ~87%，Brier 几乎由水平（先验）决定，判别力只值几个百分点；因此必须做先验漂移修正，否则「校准后反而输给气候基准」。
3. **右删失**：``pending``/``in-flight`` 节点无结局，一律剔除而不是当负例；这会让最新一波节点无法进入训练，模型对「当下派发」的先验依赖 ``π_cal`` 的估计。
4. **blocked 的语义混杂**：250 个 blocked/failed 里 208 个（83%）是 ``infra x*``（agent 丢失/LLM 报错）这类运维事故，不是可行性失败；主口径把它们算负例（口径=「发出去但没拿到验收」），strict 口径只剩 34 个真失败负例，AUC 方差极大——只作敏感性对照。
5. **不适用于**：单节点绝对可行性判定（模型给的是同分布下的频率先验，不是保证）；跨平台迁移（特征里含 repo 成熟度、计划进度等本平台结构量）。
6. **派发前路径只吃历史**：``predict`` 用 ``HistoryState`` 从头喂历史，不看任何结局列（pytest `test_predict_path_uses_history_only` 钉死）；特征因果性另有前缀一致性测试。

## ⑥ 误差判定（逐项列出，显式回答）

| 关键量 | 观测 | 基准 | 相对差 | 是否满足判据 |
|---|---|---|---|---|
| 测试窗 Brier（Platt+先验） | 0.0784 | 气候基准 0.0850 | skill 0.0772 | PASS（BSS>0） |
| 测试窗 ECE | 0.0486 | 未校准 0.1905 | 降 74.5% | PASS |
| 测试窗 AUC | 0.6628 | 随机 0.5 | +0.1628 | PASS（≥0.60） |
| 置换零分布 AUC 均值 | 0.5005 | 0.5 | 0.0005 | PASS（≤0.05） |
| 观测 BSS 的经验 p 值 | 0.0050 | 0.05 | — | PASS |

**「是否 <3%」的显式回答**：本任务是平台机制（概率校准），**没有物理实验/解析解基准**，所以 3% 误差门不适用；替代基准是三选一且已全部落地——①气候基准（先验）②置换零分布（200 次）③未校准原始分数。对三个替代基准，观测量的表现分别是 Brier skill 0.0772（>0，PASS）、null AUC 偏离 0.5 0.0005（≤0.05，PASS）、ECE 降 74.5%（>0，PASS）。**需要如实标注的弱项**：AUC 只有 0.6628（判据下限 0.60，PASS，但远非强预测器）——这是数据本身的性质（负例多数是运维事故，判据信息预测不了），不是管线缺陷；对外只能说「排序能力弱而有统计显著性」，不能说「能预测节点成败」。

## 审计链

- 数据源：``campaign_nodes`` ⋈ ``campaign_plans``（只读 SQL，见 `dataset.NODE_SQL`）
- 数据集指纹：`75072c527283c750a92e57be5c6e35d1e95778be5d331bda087ad315979291f8`
- 泄漏自检（前缀一致）：cut=0.7，k=2182，max_abs_dev=0.0，PASS
- 超参：{'l2': 1.0, 'train_frac': 0.6, 'calib_frac': 0.2, 'bins': 10, 'perm': 200, 'seed': 20261110}
- 产物：`artifacts/pre_dispatch_prior/{model.json, backtest.json, features.json.gz, ablation_window_platt.json, REPORT.md, PAPER.md, fig_*.png}`（特征矩阵 gzip，mtime=0 → 逐字节可复算）；产物幂等（同输入两次逐字节相同）
- 判据样本窗（primary）：2026-10-31T03:02:15+00:00 … 2026-12-07T11:48:49+00:00（未删失节点比全量窗短）
- 负例来源：{'infra': 208, 'feasibility': 34, 'other': 8}
- 复现：`python3 -m tools.pre_dispatch_prior run --source pg --perm 200 --sensitivity --docs-dir docs/pre_dispatch_prior`；`python3 -m pytest tests/test_pre_dispatch_prior.py -q`（20 passed）
