# m1659 — 阻力端点内核接线：轴对称旧拟合 → Holtrop-Mennen 1982（受证回归）

**任务**: m1659（models 席，自找活 tp-mod-3）  **日期**: 2026-11-01  **分支**: agent/m1659
**类型**: 验证欠账清算 + 功能补全（把已验证但未接线的内核接入 API）

## 1. 案例介绍（物理背景 / 输入）

平台 `/api/v1/calculations/resistance` 的**默认**内核是 `ITTC-1957 摩擦线 + 手拟合轴对称残阻曲线`，
湿表面积用 `S = π·B·L·(0.5+0.25·C_B)`——这是**轴对称体**（SUBOFF 标定）的估算式。
`docs/resistance_benchmark_verification.md`（m1414）已量化：该式对商船船体偏高 43%+，
是整条阻力基准链 8 个 WITHHELD 判例的根因之一。

同时，平台早已有**未经接线**的 Holtrop-Mennen (1982/1984) 受证实现：
`backend/services/holtrop_mennen.py`（`backend/tests/test_benchmark_reference_integrity.py`
用 KVLCC2 公开湿面积 27,194 m² 做过 ±10% 带内断言）；但其模块文档自述要"replace the
hand-fitted residual curve that shipped in backend/api/calculations.py"，而端点从未调用它。

## 2. 怎么做的（方法 / 流程）

1. **最小改动接线** `backend/api/calculations.py`：`ResistanceRequest` 新增 `method`
   （`ittc_axisymmetric` 默认 = 完全向后兼容 / `holtrop_mennen`）、`cm`、`cwp`、`lcb_frac`；
   响应新增 `resistance_method`、`wave_resistance_n`、`correlation_allowance_n`、
   `appendage_resistance_n`、`model_notes`（披露派生输入与域外警告）。
2. **零再拟合**：Holtrop 分支直接调用 `services.holtrop_mennen.holtrop_resistance`，
   数值原样透传，保证正典规则 R1 `C_T = R_T/(½ρSV²)` 精确成立、可离线复算。
3. **几何闸门前置**：Cb 物理带 [0.3,1.0] 与尺寸正定校验在分派内核之前执行（平台铁律
   "几何必须先验证"）。
4. **程序化裁决** `tools/m1659/adjudicate_resistance_kernel.py`：读正典数据集 v3.0
   （`backend/data/benchmark_reference.json`，m1614 唯一裁决者），对**可复核**判例
   同时跑两种内核，出表出图，数字零手抄。

## 3. 计算结果

### 3.1 KVLCC2 湿面积裁决（公开参考 27,194 m²）

| 内核 | S [m²] | 相对公开值偏差 | 10% 带内 |
|---|---|---|---|
| 旧轴对称式 | 40,958.4 | +50.62% | 否 |
| Holtrop-Mennen 1982 | 26,717.3 | -1.75% | 是 |

### 3.2 正典可复核判例的总阻力系数误差

| case_id | Cb | froude | ct_recorded | ct_legacy | err_legacy_pct | ct_holtrop | err_holtrop_pct |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 0.6 | 0.459 | 0.0038 | 0.003495 | -8.02 | 0.004019 | 5.77 |
| SUBOFF-02 | 0.6 | 0.61201 | 0.004 | 0.004021 | 0.53 | 0.003868 | -3.31 |
| SUBOFF-03 | 0.6 | 0.76501 | 0.0042 | 0.004886 | 16.33 | 0.003757 | -10.54 |
| SUBOFF-04 | 0.6 | 0.91801 | 0.0044 | 0.00611 | 38.87 | 0.003672 | -16.55 |

| 汇总 | 值 |
|---|---|
| 正典判例总数 / 可复核 / WITHHELD | 12 / 4 / 8 |
| 平均绝对误差（旧内核） | 15.94% |
| 平均绝对误差（Holtrop） | 9.04% |
| 15% 判据通过数（旧 → Holtrop） | 2 → 3 |
| 旧式/Holtrop 湿面积比区间 | 1.4658 – 1.8952（即旧式系统性偏高 47%–90%） |

### 3.3 图

* `artifacts/m1659/fig1_wetted_surface.png` — KVLCC2 湿面积：公开参考值 / 旧式 / Holtrop
* `artifacts/m1659/fig2_ct_error.png` — 可复核判例的 C_T 误差条形图 + ±15% 判据带

## 4. 注意点（坑 / 局限 / 适用范围）

* **Holtrop 不是潜艇方法**：4 个 SUBOFF（轴对称）判例上 Holtrop 误差 +5.8%…-16.6%，
  平均绝对误差 9.04% 优于旧式的 15.94% 是**尺度/工况巧合**，
  并不表示 Holtrop 适用于回转体；因此默认内核保持旧式，Holtrop 需显式选择（域分离）。
* 8 个 WITHHELD 判例（DTMB-5415 的 Fr/航速互不自洽、KVLCC2 缺 S）**不参与**裁决——
  数据侧问题不是换内核能解决的，仍需数据修复单。
* Cm/Cwp 未给时按 `0.92+0.13·Cb`（裁剪 0.999）与 Schneekluth `(1+2C_P)/3` 派生，
  在 `model_notes` 中逐条披露；对 S 的敏感度约 ±2%（B/T 项主导），不构成 3% 级风险。
* 只做**接线**，没有修改 `services/holtrop_mennen.py` 的任何物理；旧内核逐位不变。

## 5. 误差判定（显式回答"是否 <3%"）

| 关键量 | 基准 | 误差 | <3% ? |
|---|---|---|---|
| KVLCC2 湿面积 S（Holtrop） | 公开值 27,194 m² | -1.75% | **是** |
| KVLCC2 湿面积 S（旧式） | 同上 | +50.62% | **否**（50.6%，即 m1414 缺陷） |
| 可复核判例 C_T（Holtrop，均值） | 记录试验值 | 9.04% | **否**（判据为 15%，见下） |
| 可复核判例 C_T（旧式，均值） | 记录试验值 | 15.94% | **否** |

C_T 层面 3% 未达成的**原因如实声明**：可复核判例只有 4 个、且全是 SUBOFF 轴对称体，
正典数据集对这些行未给出湿面积（S 只能由内核自己估），故 C_T 误差里混入了 S 估计误差
与残阻模型差异；商船侧目前**没有一个**可复核判例（KVLCC2 四行均因缺记录湿面积判 WITHHELD）。
因此本单只断言"S 估计与公开值一致（<3%）"与"接线忠实、可离线复算"，
**不**宣称 R_T/C_T 达到 3%——这是数据缺口，不是内核缺口。

## 6. 复现

```
cd backend && python3 -m pytest tests/test_resistance_holtrop_routing.py -q   # 10 passed
python3 tools/m1659/adjudicate_resistance_kernel.py                            # 重出本节所有数字与图
```
