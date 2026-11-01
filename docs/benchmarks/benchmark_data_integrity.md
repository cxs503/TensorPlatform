# 对标参考库一致性审计 + 阻力引擎解析账本（m1406 / data 席）

> 产物：`backend/services/benchmark_integrity.py`（一致性门）、`backend/tests/test_benchmark_integrity.py`（17 项 pytest）、
> `scripts/benchmark_audit.py`（账本生成器）、`runs/m1406/benchmark_audit.json`（本页全部数字的来源）。
> 本页数值由 `scripts/gen_benchmark_doc.py` 从该 JSON 注入，无手抄。

## 1. 案例介绍（物理背景与对象）

平台唯一的实验参考库 `backend/data/benchmark_reference.json` 支撑 `/api/v1/calculations/benchmark_validate`
的全部对标结论（12 行算例、3 型船：SUBOFF 轴对体、DTMB-5415 水面舰、KVLCC2 油轮），也是历次
"Holtrop 对 SUBOFF 误差 >300% → honest-FAIL" 判词的唯一依据。参考行本身必须先满足它所引用的定义：

- 总阻力系数 `C_T = R_T / (0.5·ρ·S·V²)`
- 傅汝德数 `Fn = V / sqrt(g·L)`
- 方形系数几何恒等式 `Δ = ρ·L·B·T·Cb`

任一行违反上述任一恒等式，坏掉的是**参考值**而不是数值模型；据此算出的误差百分比不构成验证结论。

## 2. 怎么做的（方法/工具链）

1. `services/benchmark_integrity.py`：纯函数一致性门（仅 stdlib，可脱离 DB/API 测试）。逐行校验
   ①速度单位 ②Fn 恒等式（默认 5%）③C_T/R_T/S 恒等式（20%）④几何恒等式（10%）
   ⑤同型船内 `0.5ρS` 漂移（10%）⑥可闭合性（是否声明 S）⑦来源引用；按 REAL / WITHHELD 分级。
   另给出无量纲软信号：隐含湿表面积 / 方块包络 `L·B + 2LT + 2BT`（合理带 [0.4, 2.0]）。
2. `api/domain/ship_benchmark.py` 接线：结果携带 `reference_evidence` / `reference_issues`；
   **参考值为 WITHHELD 时不再输出 pass 判决**；新增 `GET /calculations/benchmark_audit` 账本端点；
   载荷键改为 `speed_ms`、C_T 由 `R_T/(0.5ρSV²)` 反推、base URL 支持 8001/8002/8010/env 探测。
3. 阻力引擎解析账本：对 `POST /calculations/resistance` 的返回逐项用闭式解复算
   （Re、Fn、ITTC-1957 摩擦线、湿表面积公式、R_f、R_t = R_f + R_r、残阻律）。

## 3. 计算结果（数字表）

### 3.1 参考库分级（12/12 行 WITHHELD，判定 `REFERENCE_BASE_UNUSABLE`）

REAL=0 · WITHHELD=12 · 合计=12

| case | 证据级 | 隐含 S [m²] | 声明 S [m²] | 隐含/包络 | Fn 相对偏差 | 阻断项 |
|---|---|---|---|---|---|---|
| SUBOFF-01 | WITHHELD | 0.7325 | 5.42 | 0.1024 | 0.02% | C_T/R_T/S identity violated: declared S=5.42 m^2 but (C_T,R_T) implies S=0.73 m^2 (factor 7.40x)<br>within-hull 0.5*rho*S drifts 16.4% (worst SUBOFF-01) |
| SUBOFF-02 | WITHHELD | 0.6576 | 5.42 | 0.0919 | 0.02% | C_T/R_T/S identity violated: declared S=5.42 m^2 but (C_T,R_T) implies S=0.66 m^2 (factor 8.24x)<br>within-hull 0.5*rho*S drifts 16.4% (worst SUBOFF-01) |
| SUBOFF-03 | WITHHELD | 0.6012 | 5.42 | 0.084 | 0.02% | C_T/R_T/S identity violated: declared S=5.42 m^2 but (C_T,R_T) implies S=0.60 m^2 (factor 9.02x)<br>within-hull 0.5*rho*S drifts 16.4% (worst SUBOFF-01) |
| SUBOFF-04 | WITHHELD | 0.5693 | 5.42 | 0.0796 | 0.02% | C_T/R_T/S identity violated: declared S=5.42 m^2 but (C_T,R_T) implies S=0.57 m^2 (factor 9.52x)<br>within-hull 0.5*rho*S drifts 16.4% (worst SUBOFF-01) |
| DTMB5415-01 | WITHHELD | 0.552 | — | 0.0733 | 82.23% | Froude inconsistent: declared=0.061 vs V/sqrt(gL)=0.3434 (rel=82.2%, L=5.72 m)<br>geometry inconsistent: rho*L*B*T*Cb=541 kg vs declared 466 kg (rel=13.9%)<br>wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable)<br>within-hull 0.5*rho*S drifts 12.8% (worst DTMB5415-04) |
| DTMB5415-02 | WITHHELD | 0.568 | — | 0.0754 | 82.23% | Froude inconsistent: declared=0.122 vs V/sqrt(gL)=0.6867 (rel=82.2%, L=5.72 m)<br>geometry inconsistent: rho*L*B*T*Cb=541 kg vs declared 466 kg (rel=13.9%)<br>wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable)<br>within-hull 0.5*rho*S drifts 12.8% (worst DTMB5415-04) |
| DTMB5415-03 | WITHHELD | 0.5138 | — | 0.0682 | 82.24% | Froude inconsistent: declared=0.244 vs V/sqrt(gL)=1.3735 (rel=82.2%, L=5.72 m)<br>geometry inconsistent: rho*L*B*T*Cb=541 kg vs declared 466 kg (rel=13.9%)<br>wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable)<br>within-hull 0.5*rho*S drifts 12.8% (worst DTMB5415-04) |
| DTMB5415-04 | WITHHELD | 0.465 | — | 0.0618 | 82.24% | Froude inconsistent: declared=0.366 vs V/sqrt(gL)=2.0602 (rel=82.2%, L=5.72 m)<br>geometry inconsistent: rho*L*B*T*Cb=541 kg vs declared 466 kg (rel=13.9%)<br>wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable)<br>within-hull 0.5*rho*S drifts 12.8% (worst DTMB5415-04) |
| KVLCC2-01 | WITHHELD | 7389.3214 | — | 0.2155 | 0.36% | wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable) |
| KVLCC2-02 | WITHHELD | 7099.0663 | — | 0.2071 | 0.19% | wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable) |
| KVLCC2-03 | WITHHELD | 6929.3104 | — | 0.2021 | 0.48% | wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable) |
| KVLCC2-04 | WITHHELD | 6840.1329 | — | 0.1995 | 0.06% | wetted surface not declared: C_T/R_T identity cannot be closed (reference value unverifiable) |

同型船内一致性（同一船体、同一流体，`0.5ρS` 必须恒定）：
SUBOFF 最大漂移 16.4%（最差 SUBOFF-01）、
DTMB-5415 12.8%（最差 DTMB5415-04）、
KVLCC2 5.3%（最差 KVLCC2-01）。

### 3.2 阻力引擎解析账本（探针 SUBOFF-01，V = 3.0 m/s，L = 4.356 m）

| 校验项 | 引擎输出 | 闭式解 | 误差 | <3% |
|---|---|---|---|---|
| reynolds | 13015936.25 | 13015936.25 | 3.826e-10 | ✅ |
| froude | 0.458926 | 0.458926 | 3.513e-07 | ✅ |
| ittc1957_cf | 0.00286721 | 0.00286721 | 7.962e-10 | ✅ |
| wetted_surface_formula | 4.417911 | 4.417911 | 0.000e+00 | ✅ |
| frictional_resistance | 56.8878 | 56.8878 | 1.249e-16 | ✅ |
| total_is_sum | 68.6868 | 68.6868 | 0.000e+00 | ✅ |
| residual_law | 0.00059468 | 0.00059468 | 8.890e-07 | ✅ |

### 3.3 对标链路的历史缺陷（实测，非推测）

- 旧载荷键 `speed`：引擎按 **1.0 m/s**（默认值）计算，而请求为
  3.0 m/s —— 静默偏低 3.0 倍且不报错。
- 旧代码读 `calc_data["ct"]`，引擎只返回 `total_resistance_n` → `calc_ct` 恒 0 → 每行误差恒 100%、恒 FAIL。
- 旧代码只连 8002 端口（本机已死）→ 每行降级 `Resistance API call failed`（error_pct = 999）。
- 参考表 R_T 与 C_T 两列互不自洽：SUBOFF 隐含 S 与声明 5.42 m² 相差 7.4 倍；
  DTMB-5415 的 Fn 列整体偏低 82.2%；几何 Δ 与 `ρLBT·Cb` 差 13.9%。

## 4. 结果图

![benchmark audit](figs/benchmark_audit.png)

(a) 每行由 (C_T, R_T) 反推的湿表面积与方块包络之比：全部落在合理带 [0.4, 2.0] 之外；
(b) Fn 列与 `V/sqrt(gL)` 的相对偏差：DTMB-5415 四行 82.2%，SUBOFF/KVLCC2 <1%；
(c) 引擎账本各项相对误差（对数轴），全部 ≈1e-6 量级。

## 5. 注意点（踩过的坑与适用范围）

- **只判级、不改数**：参考值必须回原文（Roddy 1990 / ITTC 2010 / SIMMAN 2008）才能修，本单只建门禁，不臆造新参考值。
- 隐含/包络比值是**软信号**（方块包络不等于真实船体表面），只提示不阻断；阻断规则是"是否声明 S / 是否违反恒等式"。
- 本机部署的引擎尚未含本次新增透明字段（`wetted_surface_m2`/`cf`/`cr`），账本中这两项由
  `R_f/(0.5ρSV²)`、`R_r/(0.5ρSV²)` 反推，JSON 的 `source` 字段已标注；部署后自动改用引擎直报值。
- 引擎湿表面积用轴对体近似 `S = πBL(0.5 + 0.25Cb)`，对水面舰不适用；本次不改模型，只把它暴露为可审计字段。
- 未做：12 行参考值逐条重标（需原文数据）、CFD 交叉验证、`/calculations/stability` 的同门禁接线。

## 6. 误差判定（逐项，显式回答是否 <3%）

引擎 vs 闭式解（7 项）：Re 3.8e-10、Fn 3.5e-07、
ITTC-1957 Cf 8.0e-10、S 公式 0.0e+00、
R_f 1.2e-16、`R_t − R_f − R_r` = 0.0e+00 N、
残阻律 8.9e-07 → **全部 <3%（实测 ≤1.3e-6，仅受 JSON 四舍五入限制）**。

参考库 vs 自身定义：SUBOFF C_T/R_T/S 恒等式偏差 86.5%（因子 7.4）、DTMB-5415 Fn 偏差 82.2%、
DTMB-5415 几何偏差 13.9% → **远超 15% 不可接受阈值**，故 12 行全部降级 WITHHELD：
此前以该库为基的任何"<15% 通过"或"honest-FAIL"判词均须重新裁定。
