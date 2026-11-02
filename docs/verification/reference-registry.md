# 跨仓实验参考数据登记册与闸门（m1863）

## 1. 为什么做这件事

生态各仓的"物理正确"结论只能靠数字化实验表支撑，但这些表此前是**散落的一等公民**：格式各异、
出处写在报告散文里、误用后无人能发现。TensorLBM 就发生过一次真实事故——`dam_break_sc` 里
两张错表（把 M&M 的 T=3 值 2.7 映射到 T=2；另一张粗数字化表）产出 +50~82% 的假误差，
该仓报告已 retract。**没有登记册，这类静默替换会重演。**

本增量把参考数据做成可校验对象（`experimental-reference/v1`），判定口径固定为平台正典：
PASS(0) < WITHHELD(1) < WARN(2) < FAIL(3)，行级取最差；**精度判决只用 PASS/FAIL 两态**
（>15% 即 FAIL，不设 WARN 缓冲带，避免把超差结果降级成"警告"）。

## 2. 方法与工具链

| 组件 | 路径 | 作用 |
|---|---|---|
| 登记册 schema | `schemas/experimental-reference.schema.json` | 点表 + 出处 + 容差 + y_scale |
| 核心库 | `tools/vv/reference_registry.py` | validate / gate / checkpoint_errors / harvest |
| 驱动 | `tools/vv/run_reference_gate_m1863.py` | 台账 + 图 + 已合并数字复核 |
| 判据测试 | `tests/vv/test_reference_registry.py` | 14 项，含 2 项集成复核 |
| 登记册条目 | `references/registry/martin_moyce_1952_dambreak_front.json` | M&M 1952，14 点 |
| 仿真历史 | `references/sim/tensorlbm_dam_break_sc_{a40,a80}.csv` | 从 TensorLBM 历史抽两列（MANIFEST 溯源） |

闸门规则：参考点线性插值 → 逐点 `|sim-ref| / max(|ref|, 0.05·y_scale)` → 覆盖率 ≥ 60% 且可比点 ≥ 5 才给结论，**不外推**。

## 3. 结果

### 3.1 登记册条目自检

| 项 | 值 |
|---|---|
| 数据集 | `martin_moyce_1952_dambreak_front` |
| 校验判决 | **PASS**（usable_as_gate=True） |
| 出处 | Martin & Moyce 1952, Phil. Trans. R. Soc. A 244, 312, doi:10.1098/rsta.1952.0006 |
| 数字化 | Lethe 基准套件数字化表，TensorLBM `result.json#reference_digitisation_lethe` 逐点转录 |
| 源钉住 | commit `0d417663…af6`，sha256 `cdff3318…e6fa` |

### 3.2 闸门（TensorLBM SCMP 前沿 vs M&M）

| 网格 | 覆盖率 | 可比点 | 最差相对误差 | 最差刻度 | 判决 |
|---|---|---|---|---|---|
| a=40 | 92.9% | 13 | 19.89% | T=2.96 | **FAIL** |
| a=80 | 92.9% | 13 | 19.09% | T=2.96 | **FAIL** |

### 3.3 已合并数字的独立复核（claim recheck）

参考值与仿真值**都**插值到目标刻度，分母取参考值，与 TensorLBM `result.json#official_rerun_correct_convention` 逐项比对（容差 0.05 pp）：

| 网格 | 刻度 | 记录值 (%) | 本次重算 (%) | 差 (pp) | 一致 |
|---|---|---|---|---|---|
| a=40 | T1 | 10.526 | 10.5256 | -0.0004 | Y |
| a=40 | T2 | -7.579 | -7.5789 | 0.0001 | Y |
| a=40 | T2p96 | -19.889 | -19.8887 | 0.0003 | Y |
| a=80 | T1 | 2.49 | 2.49 | -0.0 | Y |
| a=80 | T2 | -9.723 | -9.7231 | -0.0001 | Y |
| a=80 | T2p96 | -19.086 | -19.0862 | -0.0002 | Y |

两网格共 6 个数字全部复现（最大偏差 0.0004 pp，纯四舍五入）。这说明 TensorLBM 的误差口径
与登记册口径一致、该仓记录可信——但**不代表结论达标**：

### 3.4 结论判定（≤15% 是否成立）

* T=1.0：a40 +10.53%、a80 +2.49% → 达标；
* T=2.0：a40 −7.58%、a80 −9.72% → 达标；
* T=2.96：a40 −19.89%、a80 −19.09% → **超 15% 线，不达标** ⇒ 闸门整体 **FAIL**。
  与 TensorLBM 自身 `checks.accuracy_le_3pct` / `pass_T2p96: false` 的结论一致；本报告不含
  "已达标"表述。误差随 G/τ 反向漂移是该仓已知未解项，本单不掩盖。

## 4. 注意点 / 局限

1. **不确定度是图读精度量级（±1% 满量程）**，不是原文献误差棒；M&M 原文只给图，无数值表。
2. 本单**没有重新数字化原始图版**：出处链是「M&M 1952 图 → Lethe 数字化 → TensorLBM JSON → 本登记册」，
   每一环可追溯到 sha256 / commit。
3. 闸门不外推：参考刻度必须在仿真覆盖内，否则记 WITHHELD——外推出来的"误差"不是误差。
4. `WARN` 只用于数据集自检的非阻断瑕疵（点少 / 无不确定度 / 含负值），**不进入精度判决**。
5. 采集器（harvest）扫描全生态时，多数表会因缺 `digitised_by` 被拒收——这是设计行为：
   机器发现的表默认不可当判据，必须人工补出处后入册。

## 5. 复现

```bash
python3 tools/vv/run_reference_gate_m1863.py              # 台账 + 图 + 复核
python3 -m pytest tests/vv/test_reference_registry.py -q  # 14 项判据
python3 tools/vv/reference_registry.py --harvest \
    --roots /root/tensor-ecosystem --registry references/registry \
    --ledger artifacts/reference_registry/ledger.json --validate
```

图：`docs/verification/figs/reference_registry_gate.png`（左：前沿轨迹 vs 参考表；右：三刻度误差 vs ±15% 线）。

## 6. 给下一单的线索

* TensorSPH `benchmarks/dam_break` 的 `experimental_reference` 闸门仍是 `not_evaluated`——本登记册已给出
  可直接消费的 M&M 表，接上即可把 SPH 的 B2 从"无数"变成"有判据"。
* TensorLBM `benchmarks/pending/dam_break_2d_multimodel/metrics_vs_MM.json` 的 `sc/scmp` 全 0 曲线会被本闸门
  判 FAIL（退化曲线检测已入测试）；建议在那边装同一道闸。
