# P143-W2-N04 六维统计功效门（Six-Dimension Statistical Power Gate）

> 节点：P143W2-N04（PR#870 重开整合版，mission 8716）
> 实现：`tools/abm/six_dim_power.py`（纯函数，零领域语义；stdlib 实现，scipy 仅作可选的精确区间）
> 验收：`tests/abm/test_six_dim_power.py`（74 条）+ `tests/abm/test_abm_protocol.py`（acceptance E 环 3 条）
> 配套：`tools/abm/acceptance.py`（验收聚合，含可选 E 环统计功效）
> 复现：`python runs/m8716/build_validation.py` → `artifacts/m8716/validation.json`

## 1. 问题的形状

在 ABM（Agent-Based Modeling）攻关编排协议中，六维评价表（正确性/精度/稳定性/效率/可复现性/适用范围）
长期存在一个**结构性缺陷**：「优于对照」的结论靠叙事而非统计判决。

| 旧做法 | 风险 |
|---|---|
| 肉眼比较两组均值 | 无第一类错误控制，样本量小时假阳性率可远超 5% |
| 单维 t 检验 | 六维同时检验 → 族系错误率（FWER）膨胀至 ~26%（1-0.95^6） |
| 无功效分析 | 不知道当前样本量能否检测到有意义的效应量 |
| 无 A/A 验证 | 无法确认检验程序本身的误报率是否受控 |
| 无方向语义 | accuracy/efficiency/reproducibility 是「越小越好」，不编码方向则「优于」的效应符号整体反号 |

本节点把「优于对照」从叙事升级为**带第一类错误控制的统计判决**，同时保持零领域语义——
维度名/效应量定义/阈值全部由调用方注入，本模块只做纯数学运算。

## 2. 功能清单

### 2.1 核心引擎（PR#870 重开前已在 main，m8646 缺陷修复后保留）

| 函数 | 定义 |
|---|---|
| `cohens_d(control, treatment)` | 配对设计 Cohen's d = mean(diff)/std(diff) |
| `paired_median_diff(control, treatment)` | 配对差中位数（非参数、抗异常值） |
| `permutation_test_six_dim(...)` | 六维逐一配对置换检验 + Bonferroni-Holm / Bonferroni / none |
| `mde_curve(...)` / `solve_n_for_mde(...)` | MDE(n) 曲线与样本量反解（含解析对照 `mde_analytic`） |
| `aa_false_positive_rate(...)` | A/A 空对照误报率（检验程序自身校准） |
| `retrospective_power(...)` | 既有实验数据的回溯性功效 |
| `six_dim_power_gate(...)` | 顶层判决：`pass` / `fail` / `inconclusive`（A/A 校准失控即作废） |

### 2.2 六维优势族判决 G25（PR#870 的独有能力，本次整合新增）

| 函数 | 定义 |
|---|---|
| `DIMENSION_DIRECTION` / `DIMENSION_NOTE` | 方向语义：accuracy/efficiency/reproducibility = -1（越小越好），其余 +1 |
| `build_pairs(records, ...)` | 把测量记录折成配对：键 = `(lineage, seat)`；**缺 `source` 不入账**、单位不一致不成对、同键重复报错 |
| `oriented_deltas(pairs, dim)` | `delta = direction(dim) * (v_treatment - v_control)`，正 = 实验组更好 |
| `paired_effect(deltas)` | Cohen's d_z、配对差均值/中位数、均值 t 区间、中位数序统计量区间 |
| `sign_flip_permutation_test(deltas, ...)` | 配对符号翻转置换检验（统计量 = 均值；`two-sided/greater/less`） |
| `holm_adjust(pvalues, alpha)` | Bonferroni-Holm 逐步下降校正（含逐步审计表） |
| `six_dim_advantage(records, ...)` | **族判决**：≥`min_dims` 维在 Holm 校正后显著更好 → `decision_pass` |
| `min_perm_for_holm(alpha, m)` | 置换 p 值的**分辨率下限** `ceil(m/alpha)-1`；不足时族判决数学上不可达 |

### 2.3 设计期功效（本次整合新增）

| 函数 | 用途 |
|---|---|
| `null_family_sim(...)` | 零效应族模拟：逐维误报率、`family_any`（FWER）、**判决规则本身**的误报率、六维全显著率 |
| `aa_null_control(...)` | A/A 空对照：族判决误报率 ≤ α，多随机种子泊松、逐维率对照 |
| `power_positive_control(d, n_pairs, rho, ...)` | 功效正控：已知合成效应下的实测检出率（附 CI 与解析对照） |
| `power_vs_rho(...)` | 配对收益：检出率作为臂间相关 rho 的函数（rho=0 为最保守行） |
| `family_decision_power_curve(...)` | 族判决功效随 n 的曲线（前 k 维有真实效应，其余为 0） |
| `required_n_for_family_decision(...)` | 从曲线反解「判出 ≥k 维优势所需配对对数」（网格分辨率如实报告） |
| `binom_ci(k, n)` | Clopper-Pearson 精确区间（无 scipy 退化为 Wilson，`method` 字段写明） |
| `mde_coefficient(alpha, power, two_sided)` | 解析 MDE 系数（2.8016 双侧 / 2.4865 单侧） |

### 2.4 验收聚合 E 环（`tools/abm/acceptance.py`）

`evaluate(..., power_gate=six_dim_advantage(...))` 新增第 E 条判据 `E1_six_dim_advantage_statistical`：
C 环只裁决「结构可比」，E 环才把「优于」做成可裁命题（每维带 `p_perm` / `p_holm` / `cohen_dz`）。
不传 `power_gate` 时既有行为逐字不变（回归测试覆盖）。

## 3. 判决规则 G25（预声明，不看结果后改）

> 六维中**至少 k 维（默认 2）**在 Holm(α=0.05) 校正后仍显著为实验组更好（方向性备择 `greater`）。

方向性备择是预声明的——命题本身是「优于」而非「不同」；双侧变体（`alternative="two-sided"`）
在 `aa_null_control` / `power_vs_rho` 中一并报告作为稳健性行，不改主判据。

```python
from tools.abm.six_dim_power import six_dim_advantage

records = [{"lineage": "L1", "seat": "S1", "arm": "control",   "dimension": "accuracy",
            "value": 0.030, "unit": "rel_err", "source": "runs/x/res.json#accuracy/control"},
           {"lineage": "L1", "seat": "S1", "arm": "treatment", "dimension": "accuracy",
            "value": 0.012, "unit": "rel_err", "source": "runs/x/res.json#accuracy/treatment"},
           # ... 其余维/席位
           ]
res = six_dim_advantage(records, min_dims=2, alpha=0.05, n_perm=10000, seed=20261109)
res["decision_pass"]          # True / False
res["n_significant_better"]   # 通过 Holm 的维数
res["dimensions"]["accuracy"]["p_holm"], res["dimensions"]["accuracy"]["effect"]["cohen_dz"]
```

## 4. 实测结果（全部来自 `artifacts/m8716/validation.json`）

### 4.1 G25 判决行为

| 场景 | 结果 |
|---|---|
| 2 维真实改进（12 对/维，d_z=1.94 / 1.65），其余 4 维无差异 | `n_significant_better=2`，`decision_pass=True`；accuracy `p_holm=0.0024`，efficiency `p_holm=0.0025`；无差异维 `p_perm=1.0`、`direction=indistinguishable` |
| 全零对照（六维均无差异） | `n_significant_better=0`，`decision_pass=False`，`max p_perm=1.0` |
| 实验组更差（correctness 负向） | 该维 `significant_better=False`、`direction=treatment_worse`，`decision_pass=False` |

### 4.2 检验自身校准（A/A 与零效应族）

| 量 | 实测 | 名义 / 判据 |
|---|---|---|
| 族级 FWER：≥1 维显著（200 族 × 20 对 × 499 置换） | 0.060，Clopper-Pearson 95% CI [0.031, 0.102] | α=0.05（CI 覆盖，置换 p 保守） |
| **判决规则本身**误报率：≥2 维显著（同上） | 0.000（0/200） | ≤ α=0.05 |
| 逐维误报率最大值（同上） | 0.020 | ≤ α=0.05 |
| 六维全显著族比例 | 0.000 | ≈0 |
| A/A 空对照（60 族 × 3 seeds，族判决 ≥2 维） | `pass=True`，族误报率 0.000，CI 上界 0.060 | ≤ α=0.05 |

### 4.3 功效正控与配对收益

| 量 | 实测 | 解析（正态 + t 修正） | 相对误差 |
|---|---|---|---|
| d=0.5, n=20, rho=0.7, 单侧检出率（400 次模拟 × 999 置换） | 0.880 | 0.877 | **0.31%** |
| d=0.5, n=20, rho=0（最保守） | 0.4325 | 0.443 | 2.4% |

rho 扫描（d=0.5, n=20, 300 次模拟/行，单侧）：0.0 → 0.457；0.3 → 0.560；0.5 → 0.707；
0.7 → 0.863；0.85 → 0.993（配对收益单调；rho 是**设计假设**，rho=0 行必须同报）。

### 4.4 族判决功效与所需 n（d_z=0.8，2/6 维有真实效应，120 次模拟/行，499 置换）

| n | 9 | 12 | 16 | 20 | 26 | 32 | 40 | 60 |
|---|---|---|---|---|---|---|---|---|
| P(≥2 维显著) | 0.142 | 0.242 | 0.458 | 0.617 | 0.825 | 0.975 | 0.967 | 1.000 |

- `required_n_for_family_decision(target_power=0.8)` → 网格命中 **n=26**（n=26 处 0.825）。
- 内部一致性：同一置换机制下单维功效（Holm 阈值 α/6）实测 0.803 → 独立维预测 0.803²=0.644，
  实测“两维同时显著”0.608，绝对差 0.036。
- n=40 的 0.967 vs n=32 的 0.975 差 0.008，在 120 次模拟的 Monte-Carlo 噪声内（SE≈0.014），
  不是效应回退。

### 4.5 既有 MDE 引擎抽查（`mde_curve`, 1000 置换）

| n | 数值 MDE | 解析 MDE | 偏差 |
|---|---|---|---|
| 20 | 0.6937 | 0.6544 | +6.0% |
| 40 | 0.4100 | 0.4526 | −9.4% |

## 5. 误差判定（逐项回答「是否 <3%」）

| 关键量 vs 基准 | 误差 | <3%？ |
|---|---|---|
| 功效正控检出率 0.880 vs 解析 0.877（d=0.5, n=20, rho=0.7） | 0.31% | **是** |
| 功效正控检出率 0.4325 vs 解析 0.443（rho=0，最保守行） | 2.4% | **是** |
| 族判决功效内部一致性：0.608 vs 独立维预测 0.644 | 绝对 0.036（相对 5.5%） | **否**（Monte-Carlo + 独立维近似；已如实列出） |
| A/A 族误报率 0.000 vs α=0.05 | 0/200，CI 上界 0.060 | 估计量本身在 CI 内；不予“<3%”声明 |
| 零效应族族级 FWER 0.060 vs α=0.05 | 点估计 +0.010，CI [0.031, 0.102] 覆盖 0.05 | **否**（200 族分辨率 0.05/族，只能声明 CI 覆盖） |
| MDE(n) n=20 / n=40 vs 解析 2.802σ/√n 口径 | +6.0% / −9.4% | **否**（在 15% 精度标准内；小 n 由 t 分位修正主导） |

## 6. 注意点与局限

1. **置换 p 值分辨率下限**：Holm 校正下最小可达 p = 1/(1+B) 必须 ≤ α/m；α=0.05、m=6 时
   B ≥ 119。`min_perm_for_holm()` 给出该值，`six_dim_advantage` / `null_family_sim` /
   `family_decision_power_curve` 都回写 `n_perm_resolution_ok`——不足时结论是「分辨率不够」，
   不是「没效应」。
2. **n 小时 p 值离散**：等幅配对下双侧最小 p = 2/2^n（n=5 → 6.25% 拒绝不了）；`MIN_TESTABLE_PAIRS=9`
   是设计下限（保证 0.05 以下有多档可分辨）。
3. **配对设计要求**：control 与 treatment 必须一一对应（同一 (lineage, seat) 的两臂测量）；
   独立双样本需改独立置换检验（本模块未实现，明确不支持）。
4. **零方差**：配对差全同号且等幅时 d_z = inf，`paired_effect` 如实标 inf；置换检验对常数偏移
   仍可判显著（符号翻转精确检验）。
5. **rho 是设计假设**：配对收益取决于两臂相关；rho=0 行是保守下界，必须与主结果同报。
6. **计算开销**：纯 stdlib 实现的置换循环，默认参数（族模拟 200 族 × 499 置换 ≈ 8 s；
   族功效曲线 8 点 × 120 模拟 × 499 置换 ≈ 20 s）。大规模扫描应走集群分片。
7. **不替代物理判据**：统计显著 ≠ 物理重要。本模块只回答「差异是否可靠」，物理意义由领域判据决定。

## 7. 与既有机制的关系

| 机制 | 管什么 | 边界 |
|---|---|---|
| `dimensions.py` | 六维表结构 + 多臂对照门（结构可比） | 本模块 **消费**其数值，做统计推断；方向字典与本模块 `DIMENSION_DIRECTION` 口径一致 |
| `acceptance.py` | 验收聚合（留痕/预测盲/多臂/三腿/**统计功效 E 环**） | E 环可选注入，不传则行为不变 |
| `blind.py` | 预测盲登记器 | 独立机制，无直接依赖 |
| `gates.py` | 七环有序过门状态机 | E 环可嵌入任一环 |
| `six_dim_power_gate` | 三态总判决（pass/fail/inconclusive） | 与 G25 互补：前者按维显著性+功效+效应量判总态，后者按族规则判「≥k 维优于」 |

## 8. 文件清单与复现

| 文件 | 说明 |
|---|---|
| `tools/abm/six_dim_power.py` | 核心实现（引擎 + G25 + 设计期功效，纯 stdlib） |
| `tools/abm/acceptance.py` | 验收聚合 + 可选 E 环 `E1_six_dim_advantage_statistical` |
| `tools/abm/__init__.py` | 包级导出同步 |
| `tests/abm/test_six_dim_power.py` | 74 条测试（原 45 + 新增 29） |
| `tests/abm/test_abm_protocol.py` | acceptance E 环 3 条 |
| `runs/m8716/build_validation.py` | 全部测量 + 两联图，程序化写 `artifacts/m8716/validation.json` |
| `artifacts/m8716/validation.json` | 原始测量（本节所有数字的唯一来源） |
| `artifacts/m8716/fig_g25_power.png` | 族判决功效 vs n；检出率 vs rho |

```bash
python -m pytest tests/abm -q                     # 89 passed
python runs/m8716/build_validation.py             # 重新生成测量与图
```