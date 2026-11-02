# 船舶阻力基准数据集一致性审计（benchmark_reference.json）

> 生成工件：`tools/benchmark_audit_report.py`（本文件与 `artifacts/benchmark_audit/*` 全部由脚本产出，
> 数字零手抄）。审计器：`backend/services/benchmark_audit.py`。回归测试：`tests/test_benchmark_audit.py`。
> 审计时间 2026-11-01（m1589）。

## 1. 为什么审

`backend/api/domain/ship_benchmark.py` 对外发布一个**精度判决门**：Holtrop/ITTC-57 计算 Ct 与
`backend/data/benchmark_reference.json` 中 `experimental_ct` 的相对误差 ≤15 % 即判 pass。
门本身的合法性依赖参考值**内部自洽**——若参考表自相矛盾，pass 只是把配方误差藏在数据缺陷后面。
因此把 3 条定义性恒等式作为审计判据（Δ 为相对偏差）：

| 判据 | 恒等式 | 容差 | 含义 |
|---|---|---|---|
| C1 FROUDE_IDENTITY | Fn = V/√(gL) | 1 % | 比较点（速度）与长度同一参考系 |
| C2 CT_RT_RECIPROCITY | Ct = Rt/(½ρSV²) | 5 % | 无量纲 Ct 与有力纲 Rt 互洽 |
| C3 BLOCK_COEFF_IDENTITY | cb·L·B·T = ∇ | 5 % | 方形系数与排水量互洽 |
| C4 SPEED_REFERENCE_FRAME | V/√(gL) ≤ 1 | — | 排水型基准算例不存在跨声速/滑行 Fn |
| C5 CT_PLAUSIBILITY | Ct/Cf_ITTC57 ∈ [0.9, 3.5] | — | Ct 不得低于摩擦线（物理下界） |

ρ：模型尺度淡水 999 kg/m³，KVLCC2 实船海水 1025 kg/m³；ν：模型 1.0e-6、实船 1.19e-6 m²/s。

## 2. 审计结果（程序化表格）

| case | ship | Fn stated | Fn derived | Ct stated | Ct from Rt | blocking | warnings |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | SUBOFF | 0.459 | 0.4590 | 0.00380 | 0.000513 | - | CT_RT_RECIPROCITY |
| SUBOFF-02 | SUBOFF | 0.612 | 0.6120 | 0.00400 | 0.000485 | - | CT_RT_RECIPROCITY |
| SUBOFF-03 | SUBOFF | 0.765 | 0.7650 | 0.00420 | 0.000465 | - | CT_RT_RECIPROCITY |
| SUBOFF-04 | SUBOFF | 0.918 | 0.9180 | 0.00440 | 0.000462 | - | CT_RT_RECIPROCITY |
| DTMB5415-01 | DTMB-5415 | 0.061 | 0.3434 | 0.00450 | - | FROUDE_IDENTITY | CT_RT_RECIPROCITY, BLOCK_COEFF_IDENTITY |
| DTMB5415-02 | DTMB-5415 | 0.122 | 0.6868 | 0.00380 | - | FROUDE_IDENTITY | CT_RT_RECIPROCITY, BLOCK_COEFF_IDENTITY |
| DTMB5415-03 | DTMB-5415 | 0.244 | 1.3738 | 0.00350 | - | FROUDE_IDENTITY, SPEED_REFERENCE_FRAME | CT_RT_RECIPROCITY, BLOCK_COEFF_IDENTITY |
| DTMB5415-04 | DTMB-5415 | 0.366 | 2.0606 | 0.00380 | - | FROUDE_IDENTITY, SPEED_REFERENCE_FRAME | CT_RT_RECIPROCITY, BLOCK_COEFF_IDENTITY |
| KVLCC2-01 | KVLCC2 | 0.129 | 0.1286 | 0.00280 | - | - | CT_RT_RECIPROCITY |
| KVLCC2-02 | KVLCC2 | 0.138 | 0.1378 | 0.00300 | - | - | CT_RT_RECIPROCITY |
| KVLCC2-03 | KVLCC2 | 0.143 | 0.1423 | 0.00310 | - | - | CT_RT_RECIPROCITY |
| KVLCC2-04 | KVLCC2 | 0.147 | 0.1469 | 0.00320 | - | - | CT_RT_RECIPROCITY |

cases=12 gate_eligible=8 blocked=4

**缺陷清册（n = 12 例）**：`{"CT_RT_RECIPROCITY": 12, "FROUDE_IDENTITY": 4, "BLOCK_COEFF_IDENTITY": 4, "SPEED_REFERENCE_FRAME": 2}`；
判据被阻断（WITHHELD）的算例：`DTMB5415-01, DTMB5415-02, DTMB5415-03, DTMB5415-04`。

## 3. 四类实测缺陷

1. **C1 弗劳德恒等式破裂（4/12，DTMB-5415）**：表列 Fn=0.061 而 V/√(gL)=0.343，偏差 82 %；
   末两例反解 Fn=1.37、2.06 > 1 —— 说明 `speed_ms` 是**实船速度**却配了**模型长度** 5.72 m，
   两列参考系不一致。任何"DTMB-5415 通过 15 % 门"的结论都不可复核。
2. **C2 Ct↔Rt 恒等式破裂（12/12）**：SUBOFF 由 Rt 反解 Ct=0.00051 而表列 0.0038（偏 86–90 %）；
   KVLCC2 由 Rt 反解 Ct=0.00076 而表列 0.0028 —— 且 KVLCC2/DTMB 缺 `wetted_surface_m2`，
   根本无法反算（数据完整性欠账）。
3. **C3 方形系数恒等式（4/12，DTMB-5415）**：cb·L·B·T = 0.5425 m³ vs 表列 0.4663 t（16 %）。
4. **几何预设与数据集打架（API 层）**：旧的硬编码 `SHIP_GEOMETRY` 里 SUBOFF 的 `cb` 写成了
   0.508（＝艇体直径），DTMB-5415 的排水量写成 466.3 kg（其自身 cb·L·B·T 应有 542.5 kg）。
   现改为**从数据集派生**，只对数据集缺失的键保留显式 `fallback` 并回传 `preset_sources` 溯源。

## 4. 门硬化：fail-closed（WITHHELD）

`POST /api/v1/calculations/benchmark_validate` 现在先跑审计：

* C1/C4/C5 破裂 → 该算例判 `pass=False`，`method="WITHHELD"`，
  `note="REFERENCE_DATA_INCONSISTENT[...]: ..."`，并计入 `summary.withheld_cases`；
* C2/C3 破裂（Ct 本身仍合物理）→ 记 warning，不阻断（数据卫生债，登记待修）；
* `summary.reference_data_audit` 回传缺陷计数，`summary.preset_sources` 回传几何溯源。

即：**不许用坏参考值点亮绿灯**。审计后 12 例中 8 例可判（SUBOFF、KVLCC2），4 例 WITHHELD（DTMB-5415）。

## 5. 结果图

![fig1](fig1_froude_identity.png)：表列 Fn 与 V/√(gL) 的对角线图，DTMB-5415 四点显著偏离，两点越过 Fn=1。
![fig2](fig2_identity_residuals.png)：逐算例三条恒等式残差（对数轴），红线为容差；可见 C2 残差 80–90 % 量级。

## 6. 注意点与局限

* 本轮**只修可判定项**（派生预设、fail-closed），**不重写文献数值**：DTMB-5415 正确速度/尺度、
  KVLCC2 湿表面积需回原始文献（ITTC V&V Workshop、MOERI 官方 particular、Roddy 1990）核对后由
  负责人确认，禁止凭记忆填数。
* C2/C3 具体是 Ct 对还是 Rt 对，本审计不作裁决（只能证明二者矛盾）。C5 显示 SUBOFF Ct/Cf≈1.33、
  KVLCC2 Ct/Cf≈2.0（均在物理包线内），提示**力纲列**更可能是笔误来源，仍待文献核实。
* 场景外推：Holtrop 对 Fr>0.4（SUBOFF）本就超适用域，本审计不改善该方法的物理适用范围。
