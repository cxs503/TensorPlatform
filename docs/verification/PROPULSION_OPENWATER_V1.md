# 推进域验证欠账清偿 v1 — 螺旋桨敞水（open water）审计与门禁

EDIT_WHITELIST: backend/methods/, backend/tests/, tools/verification/, docs/verification/, artifacts/propulsion/

任务 m1595（models 席自找活）。产出：`backend/methods/propulsion/`（解析锚+门禁+参考曲线）、`tools/verification/propulsion_baseline.py`（跑批）、`artifacts/propulsion/`（results.json+2 图）、`backend/tests/feat/test_propulsion_openwater.py`（20 项测试）。

## 1. 案例与对象

被审对象：serving 船体性能平台（:8001）`platforms/shared/methods/wageningen_b.py`（commit 38841cf），其 `METHOD_METADATA` 自述 `validation_status = "verified"`、参考 "Troost, Wageningen B-series open-water propeller data"，输入域 `0 <= J <= 1.2, 0.6 <= P/D <= 1.4, 0.4 <= Ae/Ao <= 0.8, Z in {3,4,5}`。同平台"官方基准集"为 `backend/data/test_data/propulsion_wageningen.json`（B4-70，P/D=1.0，Ae/A0=0.7，4 叶），已只读留档至 `backend/methods/propulsion/data/b4_70_reference_dataset.json`（附 `_provenance`）。

## 2. 方法

1. **解析锚（REAL，闭式，无经验常数）**：敞水效率恒等式 `eta_0 = J K_T/(2 pi K_Q)`；推力/扭矩/功率恒等式 `T = K_T rho n^2 D^4`、`Q = K_Q rho n^2 D^5`、`P_D = 2 pi n Q`；推力载荷 `C_T = 8 K_T/(pi J^2)`；作动盘理想效率 `eta_i = 2/(1+sqrt(1+C_T)) = 1/(1+a)`，`C_T = 4a(1+a)`，必要判据 `eta_0 <= eta_i`；Froude 相似（等 J 下 K_T,K_Q 与 n,D 无关）。
2. **门禁套件** `audit_open_water_curve`：G1 效率恒等式、G2 系柱点正性、G3 严格单调（允许零推力后截断零尾）、G4 零推力交点在域内且该处 eta_0→0、G5 效率单峰、G6 动量理论上界、G7 Froude 相似（独立测试）。
3. **参考曲线 RBC-1**：对声明数据集做 Fritsch–Carlson 单调三次 Hermite 插值（残差 0、构造上严格单调、域外截断不外推），eta_0 只由恒等式给。弃三参数幂律拟合：该数据集非光滑，幂律最优拟合在 J=1.0 残差 >60%，用拟合等同于替换数据。
4. **被审模型适配器** `serving_wageningen_b`：逐行转写 upstream `open_water_coefficients`（含 `max(0,·)`、`max(1e-6,·)`、`clip(.,0,0.95)` 三个 clamp），使其可复现、可门禁化。

## 3. 结果

### 3.1 逐点残差（serving vs 其自述基准集）
| J | K_T 基准 | K_T serving | ΔK_T | K_Q 基准 | K_Q serving | ΔK_Q | Δeta_0 | 基准集 eta 恒等式偏差 |
|---|---|---|---|---|---|---|---|---|
| 0.0 | 0.4000 | 0.4190 | 4.7% | 0.0400 | 0.0648 | 62.0% | — | 0.0000 |
| 0.2 | 0.3500 | 0.3626 | 3.6% | 0.0380 | 0.0572 | 50.6% | -31.4% | 0.0008 |
| 0.4 | 0.2800 | 0.3054 | 9.1% | 0.0340 | 0.0498 | 46.4% | -25.4% | 0.0003 |
| 0.6 | 0.1800 | 0.2474 | 37.4% | 0.0270 | 0.0424 | 56.9% | -12.4% | 0.0004 |
| 0.8 | 0.1000 | 0.1886 | 88.6% | 0.0180 | 0.0350 | 94.7% | 21.1% | 0.1414 |
| 1.0 | 0.0300 | 0.1290 | 330.0% | 0.0080 | 0.0278 | 247.5% | 23.7% | 0.0002 |
| 1.2 | 0.0000 | 0.0686 | — | 0.0000 | 0.0206 | — | — | — |

最大绝对相对偏差：**K_T 330.0%，K_Q 247.5%，eta_0 31.4%**（验收阈值 15%，目标 3%）。

### 3.2 门禁判定
| 门 | 声明的基准集 | serving wageningen_b | 参考曲线 RBC-1 |
|---|---|---|---|
| `G1_eta_identity` | False | True | True |
| `G2_bollard` | True | True | True |
| `G3_monotone` | True | True | True |
| `G4_zero_thrust` | True | False | True |
| `G5_eta_single_peak` | True | True | True |
| `G6_momentum_bound` | True | True | True |
| `G7_froude_similarity` | None | None | None |

- **基准集 G1 失败**：`J=0.8` 处 `eta_0=0.566`，恒等式给 0.7069（偏差 25%），该行自相矛盾；其余 6 行自洽。数据集 notes 亦自述 "Values are approximate"，无法溯源到 Oosterveld & van Oossanen (1975) 表值。
- **serving G4 失败**：其回归解析零推力点 `J=1.424`（P/D=1.0, Ae/A0=0.7, Z=4）落在自述有效域 `J<=1.2` 之外；`J=1.2` 处仍给 `K_T=0.1290`，基准集此点为 0 —— 声明域内不存在零推力点。
- serving 其余门（恒等式、单调、动量上界）通过，因为其 eta_0 由恒等式构造且被 clamp 保护：**门禁通过 ≠ 精度合格**。

### 3.3 解析锚（动量理论）
| 工况 | C_T | eta_i | 基准集 eta_0 | eta_0/eta_i | 理想伴流 w |
|---|---|---|---|---|---|
| J=0.4 | 4.4563 | 0.5995 | 0.5243 | 0.874 | 0.4005 |
| J=0.6 | 1.2732 | 0.7975 | 0.6366 | 0.798 | 0.2025 |
| J=0.8 | 0.3979 | 0.9165 | 0.7074 | 0.772 | 0.0835 |

基准集效率为作动盘上界的 0.55–0.70 倍，量级合理；但其形状与 serving 回归互相矛盾，说明至少一者错，而以自述 verified 出现的那个偏差更大。

## 4. 判定与建议

| 项 | 判定 | 依据 |
|---|---|---|
| serving `validation_status="verified"` | **不成立（overclaim）** | 与自述基准集偏差 K_T 330.0% / K_Q 247.5%，远超 15% |
| 绝对精度 vs Wageningen B 系列 | **WITHHELD** | Oosterveld & van Oossanen (1975) 39/47 项系数无离线可溯源副本，拒绝凭记忆转录 |
| 基准集可用性 | **需修** | `J=0.8` 行违反 eta 恒等式；末点 K_T=K_Q=0 同时归零非物理 |
| 门禁/参考曲线 | **可用（REAL 判据）** | 20 项测试全绿，含反例检测（非单调、越界效率、恒等式违反） |

建议：(a) serving 元数据由 `verified` 降级为 `approximate (regression, unvalidated)`；(b) 由可溯源 PDF 转录 B 系列系数填入 `B_SERIES_TERMS`（结构就绪，`evaluate_b_series` 对空表抛错）；(c) 修正基准集 `J=0.8` 行与末点。

## 5. 局限

- 单干轻量（load15≈125 不起重算力）：无 CFD/实验对标，仅解析与恒等式锚。
- 被审模型为逐行转写副本，upstream 改动需重转写（`SERVING_PROVENANCE` 记 commit）。
- `kt_zero_J = 1.2000`（RBC-1，插值得）。未覆盖船-桨相互作用（伴流/推力减额）与空泡，属下一单增量。

## 6. 复现

```
cd backend && python3 -m pytest --noconftest tests/feat/test_propulsion_openwater.py -q   # 20 passed
MPLBACKEND=Agg python3 tools/verification/propulsion_baseline.py                          # 生成 artifacts/propulsion/
```
