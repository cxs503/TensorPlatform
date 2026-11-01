# 阻力基准数据集内部一致性审计（benchmark_reference.json v1.0 → v2.0）

> 本文件由 `backend/tools/benchmark_audit_report.py` 程序化生成：文中与图中的每个数字都来自 `backend/tools/benchmark_dataset_audit.py` 输出的审计 JSON，无手抄值。

## 1. 案例介绍（为什么要审计这条基准）

`backend/data/benchmark_reference.json` 是平台精度判决的地基：`POST /api/v1/calculations/benchmark_validate` 以它为准判定阻力计算 PASS/FAIL（判据 误差 ≤ 15%）。数据集共 12 条：SUBOFF（DARPA 潜艇标模，Roddy 1990）4 条、DTMB-5415（ITTC V&V Workshop 2010）4 条、KVLCC2（SIMMAN 2008）4 条。审计不依赖任何外部文献，只校验**同一行内部**必须成立的恒等式：

| 恒等式 | 表达式 | 容差 |
|---|---|---|
| Froude 数 | Fr = V/√(gL) | 1% |
| 雷诺数 / 摩擦系数 | Re = VL/ν, Cf = 0.075/(log10 Re − 2)² (ITTC-57) | Cf 为 Ct 硬下界 |
| 拖曳力闭合 | Rt = Ct·½ρV²S | 5% |
| 方形系数闭合 | Cb = m/(ρLBT) | 3% |

常量：g = 9.81 m/s²，ρ = 1025.0 kg/m³，ν = 1.139e-06 m²/s（ITTC-57 15 °C）。可行性判据另加 Fr ≤ 0.5（超出即不属于排水型拖曳水池工况）与 1 ≤ Ct/Cf ≤ 3。

## 2. 怎么做的

1. `tools/benchmark_dataset_audit.py`：逐行重算上述恒等式，按 ERROR（物理不可能/自相矛盾，必须改）与 WARN（可疑/无法离线核验）分级，输出 JSON + markdown；
2. `tools/migrate_benchmark_dataset_v2.py`：把可由本行数据**唯一确定**的列就地修正（Fr、Rt），原值保留在 `*_as_filed` 字段；无法离线判定的冲突（cb vs 排水量）**不改数**，只打结论标记；
3. `api/domain/ship_benchmark.py`：Fr 改为从 V/L 现算（不再信任数据列）；新增 `validate_geometry_preset()` 对 Cb/L/B/T/排水量做闭合校验并标注单位；新增 `strict_validity` 开关，使 `reference_status` 非 verified 的行**不得参与** PASS/FAIL 统计；
4. `tests/test_benchmark_dataset_audit.py`：把上述缺陷与端点行为全部锁进回归测试。

## 3. 计算结果

**审计前（v1.0）**：12 行中 12 行存在 ERROR 级矛盾，按检查项统计 {"cb_closure:ERROR": 8, "froude_identity:ERROR": 4, "froude_range:WARN": 6, "rt_closure:ERROR": 4}。三条独立缺陷：

1. **Fr 列错 5.6 倍（DTMB-5415 全部 4 行）**：文件值 0.061/0.122/0.244/0.366，而 V/√(gL) 给出 0.343/0.687/1.374/2.060；端点里 `Fr > 0.4 → HONEST-FAIL` 的分支因此永远不触发，且 30 kn 一条对应 Fr = 2.06（已非排水型工况）；
2. **Rt 列与自身 Ct 列矛盾（SUBOFF 全部 4 行）**：文件 12.5/21/31.5/45 N，Ct·½ρV²S 给出 95.0/177.8/291.7/440.0 N，偏差 86.8–89.8%（差 7.6–9.8 倍）；同一行同时给出 S = 5.42 m²，故两条数不可能同真；
3. **cb 与排水量矛盾（DTMB-5415、KVLCC2 共 8 行）**：反解所需水密度 DTMB-5415 为 859.5 kg/m³、KVLCC2 为 982.0 kg/m³，均落在物理可能的水密度区间 [1000, 1026] 之外——因此与 ρ 的取值无关，矛盾本身成立。（KVLCC2 若以 cb 为准，排水量为 320437.2 t。）

### 3.1 审计前逐行数字

| case | ship | Fr filed | Fr = V/sqrt(gL) | Re | Cf(ITTC-57) | Ct/Cf | Rt filed [N] | Rt = Ct*r*V^2*S [N] | cb filed | cb from disp. | implied rho [kg/m^3] | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 0.4589 | 0.4589 | 1.147e+07 | 0.00293 | 1.297 | 12.5 | 95.0 | -- | -- | -- | inconsistent_unresolved |
| SUBOFF-02 | SUBOFF | 0.6119 | 0.6119 | 1.530e+07 | 0.00279 | 1.434 | 21.0 | 177.78 | -- | -- | -- | inconsistent_unresolved |
| SUBOFF-03 | SUBOFF | 0.7649 | 0.7649 | 1.912e+07 | 0.002689 | 1.562 | 31.5 | 291.66 | -- | -- | -- | inconsistent_unresolved |
| SUBOFF-04 | SUBOFF | 0.9179 | 0.9179 | 2.295e+07 | 0.00261 | 1.686 | 45.0 | 440.0 | -- | -- | -- | inconsistent_unresolved |
| DTMB5415-01 | DTMB-5415 | 0.061 | 0.3434 | 1.292e+07 | 0.002871 | 1.567 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-02 | DTMB-5415 | 0.122 | 0.6867 | 2.583e+07 | 0.00256 | 1.484 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-03 | DTMB-5415 | 0.244 | 1.3735 | 5.167e+07 | 0.002298 | 1.523 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-04 | DTMB-5415 | 0.366 | 2.0602 | 7.750e+07 | 0.002162 | 1.757 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| KVLCC2-01 | KVLCC2 | 0.1285 | 0.1285 | 2.023e+09 | 0.001405 | 1.993 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-02 | KVLCC2 | 0.1377 | 0.1377 | 2.168e+09 | 0.001394 | 2.153 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-03 | KVLCC2 | 0.1423 | 0.1423 | 2.240e+09 | 0.001388 | 2.233 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-04 | KVLCC2 | 0.1469 | 0.1469 | 2.312e+09 | 0.001383 | 2.314 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |

![Fig.1](figures/ship_benchmark_audit/fig1_froude_identity.png)

*Fig.1 Fr 列与 V/√(gL) 恒等线：蓝点为 v1.0 原值（DTMB-5415 四点全部偏离恒等线 82%），绿方为 v2.0 修正值。*

![Fig.2](figures/ship_benchmark_audit/fig2_towing_force_closure.png)

*Fig.2 SUBOFF 拖曳力：文件值与由其 Ct、湿表面积算出的值相差 87–90%。*

![Fig.3](figures/ship_benchmark_audit/fig3_block_coefficient.png)

*Fig.3a cb 闭合；Fig.3b 由文件 cb 反解的水密度（绿色带为物理可能区间），两者均在带外。*

### 3.2 修正后（v2.0）

Fr 恒等式与 Rt 闭合的 ERROR 全部消除（ERROR 行数 12 → 8），剩余 ERROR 全部是**无法离线判定**的 cb/排水量冲突，如实保留并标 `reference_status = inconsistent_unresolved`，绝不猜数。逐行状态：

| case | ship | Fr filed | Fr = V/sqrt(gL) | Re | Cf(ITTC-57) | Ct/Cf | Rt filed [N] | Rt = Ct*r*V^2*S [N] | cb filed | cb from disp. | implied rho [kg/m^3] | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 0.4589 | 0.4589 | 1.147e+07 | 0.00293 | 1.297 | -- | 95.0 | -- | -- | -- | verified |
| SUBOFF-02 | SUBOFF | 0.6119 | 0.6119 | 1.530e+07 | 0.00279 | 1.434 | -- | 177.78 | -- | -- | -- | out_of_validity_range |
| SUBOFF-03 | SUBOFF | 0.7649 | 0.7649 | 1.912e+07 | 0.002689 | 1.562 | -- | 291.66 | -- | -- | -- | out_of_validity_range |
| SUBOFF-04 | SUBOFF | 0.9179 | 0.9179 | 2.295e+07 | 0.00261 | 1.686 | -- | 440.0 | -- | -- | -- | out_of_validity_range |
| DTMB5415-01 | DTMB-5415 | 0.3434 | 0.3434 | 1.292e+07 | 0.002871 | 1.567 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-02 | DTMB-5415 | 0.6867 | 0.6867 | 2.583e+07 | 0.00256 | 1.484 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-03 | DTMB-5415 | 1.3735 | 1.3735 | 5.167e+07 | 0.002298 | 1.523 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| DTMB5415-04 | DTMB-5415 | 2.0602 | 2.0602 | 7.750e+07 | 0.002162 | 1.757 | -- | -- | 0.508 | 0.426 | 859.5 | inconsistent_unresolved |
| KVLCC2-01 | KVLCC2 | 0.1285 | 0.1285 | 2.023e+09 | 0.001405 | 1.993 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-02 | KVLCC2 | 0.1377 | 0.1377 | 2.168e+09 | 0.001394 | 2.153 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-03 | KVLCC2 | 0.1423 | 0.1423 | 2.240e+09 | 0.001388 | 2.233 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |
| KVLCC2-04 | KVLCC2 | 0.1469 | 0.1469 | 2.312e+09 | 0.001383 | 2.314 | -- | -- | 0.8098 | 0.7758 | 982.0 | inconsistent_unresolved |

![Fig.4](figures/ship_benchmark_audit/fig4_dataset_status.png)

*Fig.4 数据集判决分布：v1.0 全部 12 行为不可用；v2.0 起 1 行可判 PASS/FAIL、3 行超适用范围、8 行冲突待外部源。*

### 3.3 端点几何预置校验（`validate_geometry_preset`）

| 船型 | 适用 | 通过 | cb 文件值 | cb = m/(ρLBT) | 偏差 % | 说明 |
|---|---|---|---|---|---|---|
| SUBOFF | False | False | 0.508 | 0.0004 | -- | body of revolution: preset displacement/cb are placeholders, ship Cb undefined |
| DTMB-5415 | True | False | 0.508 | 0.426 | 19.26 | cb 0.508 inconsistent with displacement/L*B*T (implies cb=0.4260) |
| KVLCC2 | True | False | 0.8098 | 0.7758 | 4.38 | cb 0.8098 inconsistent with displacement/L*B*T (implies cb=0.7758) |

顺带发现：预置字典的 `displacement` 单位不统一（DTMB-5415 为 kg、KVLCC2 为 t、SUBOFF 为无量纲占位），现已显式标注 `displacement_unit` 并在校验前归一化，否则闭合校验本身会差 1000 倍。

## 4. 注意点（局限与适用范围）

- **本地可判定 vs 需外部源**：Fr/Rt/Cb 闭合是纯代数恒等式，可离线判定；而「哪一列才是真值」（KVLCC2 是 cb=0.8098 还是排水量 307 000 t）必须查 SIMMAN/ITTC 原始报告，本单**不做猜测**，只把候选解写进 `audit_flags`；
- **experimental_ct 本身仍未被外部核验**：本单只证明它与同行的 Rt/S 不相容，不能证明其绝对值正确（`experimental_ct_status = externally_unverified_in_offline_audit`）。任何以此为基准的 <15% 判决，其可信度上限取决于该 Ct 的真实性——这是下一张「验证欠账」单的入口；
- **Fr ≤ 0.5 是可行性判据而非物理定律**：它是「拖曳水池排水型工况」的工程边界，用于把不可作为基准的行标出来（`out_of_validity_range`），不是把数据删掉；
- **严格模式默认关闭**：`strict_validity=False` 保持既有 API 行为，避免破坏下游；需要判决结论可信时显式开启，未通过的行进入 `summary.excluded_cases` 并写明原因；
- **测试环境坑**：本仓 `tests/conftest.py` 会 import `main`，本机缺 `cryptography`/`psycopg2`，故本单用 `DATABASE_URL=sqlite:///:memory: pytest --noconftest` 跑（见 §5）。

## 5. 误差判定（是否 < 3%）

本单产出的是「基数校验」而非一条新的阻力算法，因此按下表的**逐项恒等式残差**判定：

| 检查项 | v1.0 最大残差 | v2.0 最大残差 | 是否 < 3% |
|---|---|---|---|
| Fr 恒等式 | 82.24% | 0.00% (无违例) | v2.0 是 |
| Rt 闭合（5% 容差） | 89.77% | 0.00% (无违例) | v2.0 是 |
| Cb 闭合（3% 容差） | 19.26% | 19.26% | **否——如实登记为未解决** |
| 数据集判决可用行占比 | 0/12 | 1/12 可判 + 3 超范围 + 8 冲突登记 | 不适用 |

**显式回答**：Fr 与 Rt 两项在 v2.0 已把残差压到 0%（< 3% 成立）；Cb 项**不是** < 3%：几何预置闭合残差 KVLCC2 4.38%、DTMB-5415 19.26%，
是真实的 cb/排水量冲突，需外部文献方可闭环，本单如实标 FAIL/WITHHELD 而不粉饰。审计前后 ERROR 行数 12 → 8，减少的 4 行是 Fr+Rt 缺陷行，剩余 8 行为同一 cb 冲突在 8 个行上的重复。

## 6. 复现

```bash
cd backend
python3 tools/benchmark_dataset_audit.py data/benchmark_reference_v1.0.json --md /tmp/before.md
python3 tools/benchmark_dataset_audit.py data/benchmark_reference.json --md /tmp/after.md
python3 tools/migrate_benchmark_dataset_v2.py --in data/benchmark_reference_v1.0.json --out /tmp/v2.json
DATABASE_URL=sqlite:///:memory: python3 -m pytest tests/test_benchmark_dataset_audit.py \
        tests/feat/test_ship_benchmark.py -q --noconftest
```
