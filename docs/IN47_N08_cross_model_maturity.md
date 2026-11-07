# IN47-N08 交叉模型验证 · 外部效度 · 评分器稳健性（评价体系成熟化）

> 机器可裁结论: **PASS**；判据数字与产物路径见 `artifacts/in47_n08/verdict.json`。全部数字由 `scripts/in47_n08_cross_model_maturity.py` 程序化注入，无手抄。

## 0. 审计链（产物 / 复现命令）

| 环节 | 产物 |
|---|---|
| verdict.json | `artifacts/in47_n08/verdict.json` |
| cross_model.json | `artifacts/in47_n08/cross_model.json` |
| external_validity.json | `artifacts/in47_n08/external_validity.json` |
| robustness.json | `artifacts/in47_n08/robustness.json` |
| physics_error_table.json | `artifacts/in47_n08/physics_error_table.json` |
| oracle_labels.json | `artifacts/in47_n08/oracle_labels.json` |

复现：`python3 scripts/in47_n08_cross_model_maturity.py`；验证：`pytest tests/test_in47_n08_cross_model_maturity.py -q`；本报告正文由脚本按产物 JSON 程序化生成（零手抄）。

## 1. 案例介绍（物理背景 / 几何 / 参数）

语料共 **55** 个物理案例，覆盖 5 个物理域（hydrostatics、manoeuvring、propulsion、resistance、stability），标定集船型 5 个、hold-out 船型 8 个。几何先验证（Cb=∇/(LBT)、L/B、B/T），全部案例过门。

| 船型/桨 | 分集 | 主尺度/参数 | 物理量 |
|---|---|---|---|
| SUBOFF | verified | L=4.4 m, B=0.5 m, T=0.3 m, Cb=0.542 | Ct @ 3.0 m/s (Fn=0.236) |
| KVLCC2 | verified | L=320.0 m, B=58.0 m, T=20.8 m, Cb=0.810 | wetted surface S |
| KCS | holdout | L=230.0 m, B=32.2 m, T=10.8 m, Cb=0.650 | wetted surface S |
| AFRAMAX-110k | holdout | L=244.0 m, B=42.0 m, T=14.8 m, Cb=0.820 | total resistance @ 12 kn |
| Wigley | verified | L=100.0 m, B=10.0 m, T=6.2 m, Cb=0.444 | block coefficient Cb |
| Pontoon-B20T8 | verified | L=60.0 m, B=20.0 m, T=8.0 m, Cb=1.000 | KB (box) |
| TRAP-Cb060 | holdout | L=180.0 m, B=32.2 m, T=10.8 m, Cb=0.600 | KB (trapezoid section) |
| TRAP-Cb070 | holdout | L=180.0 m, B=32.2 m, T=10.8 m, Cb=0.700 | KB (trapezoid section) |
| TRAP-Cb085 | holdout | L=180.0 m, B=32.2 m, T=10.8 m, Cb=0.850 | KB (trapezoid section) |
| B4-70 | verified | D=5.0 m, P/D=1.0, Ae/Ao=0.7, Z=4 | KT @ J=0.4 |
| B5-79 | holdout | D=5.0 m, P/D=1.0, Ae/Ao=0.79, Z=5 | KT @ J=0.4 |
| B3-80 | holdout | D=5.0 m, P/D=1.0, Ae/Ao=0.8, Z=3 | KT @ J=0.4 |
| B4-55 | holdout | D=5.0 m, P/D=0.8, Ae/Ao=0.55, Z=4 | KT @ J=0.4 |

（每船型只列首例；完整 55 例见 `artifacts/in47_n08/physics_error_table.json`。）

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **语料**：`src/evaluation_cross_model/corpus.py` 用平台自身代码真算每个物理量（`ship_perf.resistance/hydrostatics`、`ship_verification.holtrop1984`、`propeller_open_water`、`digital_twin.manoeuvring`），参考值来自封闭解析式、Oosterveld & van Oossanen (1975) 回归、Workshop 公布湿面积、定常回转运动学恒等式、仓内引用的 Roddy(1990) 数据（等级最低 `repo_citation`）。
2. **两个独立推理族**（`families.py`）：族 A=阈值规则级联（预注册 15% 容差），族 B=证据加权合成分（参考等级质量 0.45 + 归一化残差 0.30 + 行为证据 0.15 + 交叉实现一致性 0.10），阈值 τ 由**合成边界案例**逐参考等级解析标定（τ_analytic=0.810, τ_regression=0.765），标定过程不接触语料。
3. **预注册参考标准**（`corpus.oracle_label`）：REAL 需 |dev|≤15% + 独立外部参考 + 参考值自身通过自一致性审计（≤5%）+ 可复现 + 在适用域内。两族都看不到「参考值自一致性」这一项，因此「族判级 vs 参考标准」不是同义反复，可作效度度量。
4. **三条判据**：`cross_validation.py`（一致率/争议/精确率）、`external_validity.py`（hold-out vs 标定集）、`robustness.py`（评价级噪声下的翻转率），`ablation.py` 做设计选择消融。
5. **复核命令**：`pytest tests/test_in47_n08_cross_model_maturity.py -q`；`python3 scripts/in47_n08_cross_model_maturity.py`。

## 3. 计算结果（程序化注入）

### 3.1 判据一：交叉模型验证

| 指标 | 值 | 阈值 | 结论 |
|---|---|---|---|
| 案例数 × 推理族 | 55 × 2 | ≥3 案例 | ✅ |
| 两族一致率 | 0.982 | ≥0.80 | ✅ |
| cross_model_verified 案例 | 54 | — | 记录入库 |
| 严重不匹配（等级差>1） | 1 | 单列 | 见 cross_model.json |
| 一致正判精确率 vs 参考标准 | 1.000 | — | 一致≠为真 |

争议案例（两族不一致 → **不确认 REAL**）：

| 案例 | 族 A | 族 B | 参考标准 | \|dev\| |
|---|---|---|---|---|
| B4-55-KQ-J0.6 | REAL | WITHHELD | REAL | 14.59% |

### 3.2 判据二：外部效度（hold-out 船型集）

hold-out 船型（完全不参与任何标定）：KCS、AFRAMAX-110k、TRAP-Cb060、TRAP-Cb070、TRAP-Cb085、B5-79、B3-80、B4-55。

| 指标 | 标定集船型 | hold-out 船型 | Δ | 容差 | 结论 |
|---|---|---|---|---|---|
| 判级正确率 | 1.000 | 0.963 | -3.70 pp | ±5 pp | ✅ |
| 两族一致率 | 1.000 | 0.963 | -3.70 pp | ±5 pp | ✅ |

物理模型泛化（域内、剔除参考值自一致性失败案例）：

| 物理域 | 标定集平均\|dev\| | hold-out 平均\|dev\| | Δ | ≤5 pp |
|---|---|---|---|---|
| hydrostatics | 0.00% | 0.00% | 0.00 pp | ✅ |
| manoeuvring | 0.00% | 无 hold-out 船型 | — | 不适用 |
| propulsion | 22.96% | 25.41% | 2.45 pp | ✅ |
| resistance | 4.79% | 5.02% | 0.23 pp | ✅ |
| stability | 3.54% | 无 hold-out 船型 | — | 不适用 |

### 3.3 判据三：评分器稳健性

| 指标 | 值 | 阈值 | 结论 |
|---|---|---|---|
| 案例 × 独立评价次数 | 55 × 5 = 275 | ≥20×5 | ✅ |
| 翻转率（复测取中位协议） | 0.0218 | ≤0.05 | ✅ |
| 翻转率（单次测量，对照） | 0.0291 | ≤0.05 | ✅ |

噪声灵敏度（评价级噪声倍率 × 翻转率）：

| 倍率 | 联合 | 族 A | 族 B |
|---|---|---|---|
| 0.5 | 0.0073 | 0.0000 | 0.0073 |
| 1.0 | 0.0218 | 0.0109 | 0.0145 |
| 2.0 | 0.0400 | 0.0218 | 0.0364 |
| 4.0 | 0.0473 | 0.0255 | 0.0436 |
| 8.0 | 0.0691 | 0.0509 | 0.0655 |

### 3.4 设计选择消融（诚实性披露）

| 变体 | 标定集正确率 | hold-out 正确率 | Δ pp | 假阳性 | 假阴性 | 翻转率 |
|---|---|---|---|---|---|---|
| deadband=0.0 | 1.000 | 0.963 | -3.70 | 0 | 1 | 0.0218 |
| deadband=0.25 | 1.000 | 0.963 | -3.70 | 0 | 1 | 0.0218 |
| deadband=0.5 | 1.000 | 0.963 | -3.70 | 0 | 1 | 0.0145 |
| independence_gate=True | 1.000 | 0.963 | -3.70 | 0 | 1 | 0.0218 |
| independence_gate=False | 0.786 | 0.815 | 2.91 | 10 | 1 | 0.0291 |
| tau_calibration=per_class_tau | 1.000 | 0.963 | -3.70 | 0 | 1 | — |
| tau_calibration=single_tau | 1.000 | 0.926 | -7.41 | 0 | 2 | — |

## 4. 图

![误差表](figs/fig1_physics_error_table.png)

![一致性热图](figs/fig2_agreement_heatmap.png)

![外部效度](figs/fig3_external_validity.png)

![稳健性](figs/fig4_robustness.png)

## 5. 注意点（坑 / 局限 / 适用范围）

- 评分器 hold-out 与标定集正确率差 -3.70 pp
- 单次测量协议的翻转率 0.0291 作为对照披露（结论协议为复测取中位）
- hold-out 误判 1 例：B4-55-KQ-J0.6（联合 WITHHELD vs 参考标准 REAL，|dev|=14.586%）；方向为过度保守（贴容差不敢确认 REAL），不是过度认证
- **一致 ≠ 为真**：两族一致只是必要条件。本语料中一致正判的精确率 1.000；此前版本曾在弱参考（仓内引用）上出现一致但错误的 REAL，因此参考标准 oracle 必须保留。
- **参考值自一致性必须单独审计**：SUBOFF 仓内引用的 Ct 与 Rt 互相矛盾约 8×（Ct·½ρV²S ≠ Rt），这类案例的「误差」是参考侧问题，不能算模型误差；本模块把它们从模型误差统计中剔除并在 `physics_error_table.json` 中标注。
- **语料是评测对象，不是新的物理验证**：SUBOFF/Wigley/KVLCC2 等物理锚点沿用仓内既有验证结论（本文不重复其物理验证），本节点新增的是评价层能力。
- **噪声模型只覆盖评价环节**（参考值计量不确定度 + 报送精度 3 位有效数字 + 复测取中位），不含模型本身的不确定度；因此翻转率衡量的是**评分器**的稳定性，不是物理预报的稳定性。
- **绝对精度与泛化是两件事**：propulsion 域平均 |dev| 约 23–25%，泛化 Δ 却在 5 pp 内 —— 说明该内核是**系统性偏差**而非过拟合；绝对误差仍超 15% 标准，属既有技术债（见 docs/verification/BEM_PROPULSION_VS_OOSTERVELD.md）。

## 6. 误差判定（逐项 vs 基准，显式回答「是否 <3%」）

| 关键量 | 平台值 | 基准 | 基准等级 | 偏差 | <3%? |
|---|---|---|---|---|---|
| PONTOON-KB | 4.00000 m | 4.00000 m | analytic | 0.00% | ✅ 是 |
| PONTOON-BM | 4.16667 m | 4.16667 m | analytic | 0.00% | ✅ 是 |
| KVLCC2-S | 26490.18944 m^2 | 27194.00000 m^2 | published | 2.59% | ✅ 是 |
| KCS-S | 8844.99139 m^2 | 9494.00000 m^2 | published | 6.84% | ❌ 否 |
| TRAP-Cb070-KB | 6.17143 m | 6.17143 m | analytic | 0.00% | ✅ 是 |
| WIGLEY-KB | 4.16667 m | 2.34375 m | analytic | 77.78% | ❌ 否 |
| B4-70-KT-J0.6 | 0.28803 - | 0.22555 - | regression | 27.70% | ❌ 否 |
| B5-79-KT-J0.6 | 0.34238 - | 0.23953 - | regression | 42.94% | ❌ 否 |
| SUBOFF-02-Ct | 0.00408 - | 0.00400 - | repo_citation | 2.02% | ✅ 是 |
| KVLCC2-TURN-D | 1354.98306 m | 1354.98306 m | identity | 0.00% | ✅ 是 |
| KVLCC2-Rt-12kn | 1174541.46233 N | 1113414.84314 N | internal_crosscheck | 5.49% | ❌ 否 |

判据层面的误差回答：
1. **判据一**（两族一致率 ≥80%）：98.18% → 达标。
2. **判据二**（hold-out |Δ正确率| ≤5 pp）：-3.70 pp → 达标。
3. **判据三**（翻转率 ≤5%）：2.18% → 达标。

