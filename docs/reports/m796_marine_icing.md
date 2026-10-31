# W3-N09 · 上层建筑海水飞沫积冰热平衡模型与 GM 损失

**Superstructure Marine Icing: Heat-Balance Model & GM Loss**
节点 `W3-N09`（物理域 / 风险中 / 战役 DAG）· 证据等级 **REAL** ·
机器验收 **全部通过**

产物路径（全部在 worktree 内，数字零手抄，报告由 `tools/m796_report_gen.py`
从工件注入）：

| 类型 | 路径 |
|---|---|
| 领域内核（新模块） | `backend/services/ship_performance/icing/` |
| API | `backend/api/domain/ship_icing.py`（`/api/v1/ship/icing/*`） |
| pytest | `tests/physics/test_marine_icing.py`（30 passed） |
| 验收记录 | `artifacts/m796/validation.json` |
| 案例全量输出 | `artifacts/m796/case_fishing_severe.json`, `case_fishing_calib.json`, `case_bulker_calib.json` |
| 图 | `artifacts/m796/fig1_heat_balance.png` … `fig4_benchmark.png` |
| 复现脚本 | `tools/m796_marine_icing_validation.py`, `tools/m796_report_gen.py` |

---

## 1 案例介绍

### 1.1 物理背景

船舶上层建筑/甲板的海水飞沫积冰（sea-spray icing）是极区与近极区航行的主要
稳性风险之一：船体运动与波浪破碎把海水抛向迎风面，飞沫在低于冰点的空气中
于结构表面冻结。积冰是**顶置附加质量**——它抬高重心 KG、吃掉复原力臂、并因
迎风面与背风面结冰量不等而产生**结冰横倾力矩**。小船（渔船、科考船）在
−15 ℃、18 m/s 条件下数小时内即可损失一半的稳性储备，是 IMO Polar Code
Part I-A 6.3 强制计入的失效模式。

本节点实现的是**机理模型**而非查表：以逐面板表面热平衡（Mákiranta/Lozowski
型，即 Mákiranta, Lozowski & George 2013 / Lozowski, Szilder & Makkonen 2000
的方程结构）判定干/湿结冰相态与冻结分数，时间积分得到逐面板冰厚与形心，
再作为附加质量顶置项重算 KG、GM、横倾角与复原力臂曲线 GZ(φ)，最后按
IS Code 2008 A.2.2 判据（含冰）与 Polar Code 6.3.1 设计冰载（迎风暴露面
30 cm、非暴露面 7.5 cm、ρ=900 kg/m³）给出稳性结论。

### 1.2 几何与参数

* 船体：**矩形横剖面直立舷（barge idealisation）**，剖面 (y,z) 多边形
  = B×D 矩形，棱柱长度 `L_prismatic = L·Cb`，因此 ∇ = L·B·T·Cb（Cb 只在纵向上
  起作用，横剖面是矩形）；GZ 用**剖面精确积分**（半平面裁剪 + 形心）+ 等排水量
  求浸深，故越过甲板边缘浸水后仍有效（此时 wall-sided 公式 GZ=GM·sinφ+½BM·tan²φ·sinφ 失效）。
* 上层建筑：由 `mesh.Block`（origin/size，即 `geometry` 能力给出的舱室/上层建筑
  块定义）离散为平面四边形面板（panel_size 0.75 m / 1.0 m），每个面板记录
  面积、外法向、形心、特征长度（=面尺度，用于平板换热关联式）、表面分区。
  `VesselSpec` 的 `deck_area/deck_height` 追加未占用露天甲板。
* 气象/海水：`Metocean`（T_a、U₁₀、RH、云量、太阳辐射、T_w、盐度、风向量、
  航速）。**飞沫与对流由相对风 `u_wind − u_ship` 驱动**——船速使艏部/前壁成为
  强撞击面（这是积冰的主导机制，静风中不会出现）。

两型船算例（均在 `PRESETS` 内，可复现）：

| 船型 | L×B×D×T m | Cb | KG m | ∇ m³ | 上层建筑 | 航速 m/s |
|---|---|---|---|---|---|---|
| `polar_fishing_vessel_28m` | 28.0×7.5×4.0×2.8 | 0.75 | 2.55 | 452 t | 驾驶室 7×5.5×3.5 + 烟囱 2×1.2×2.2 @z=4.0 | 6.0 |
| `ice_class_bulker_90m` | 90.0×18.0×9.0×6.0 | 0.85 | 5.90 | 8 442 t | 居住区 14×16×12 + 艏楼 8×12×2.5 @z=9.0 | 7.0 |

水面以上暴露包络面积（面板网格统计）：渔船 311.6 m²
（383 面板），散货船 2,440.0 m²
（1481 面板）。

---

## 2 方法

### 2.1 逐面板表面热平衡（Mákiranta/Lozowski 型）

对每个面板（单位面积，正号=热量离开表面）：

```
q_conv = h_c (T_s − T_a)                       对流（强制平板 + 自然对流 Churchill 合成）
q_evap = h_m (ρ_v,s − ρ_v,a) L_e               蒸发（液膜）/ 升华（干冰面）
q_lw   = ε σ [w(T_s⁴ − T_sky⁴) + (1−w)(T_s⁴ − T_sea⁴)]   长波（天空/海面视角因子 w=0.5）
q_sw   = α_s I                                 太阳短波
q_spray= ṁ_w c_w (T_w − T_f)                   飞沫显热
q_lat  = ṁ_f L_f                               冻结潜热
```

* `h_c`：强制 `Nu=0.037Re⁰·⁸Pr^{1/3} − 871`（混合流平均，Re>5e5）/ `0.664Re⁰·⁵Pr^{1/3}`
  （层流），自然 `0.15Ra^{1/3}`，合成 `(|h_f|³+|h_n|³)^{1/3}`；物性取**膜温**。
* 传质用 Chilton–Colburn（Lewis）类比 `h_m = h_c /(ρ_a c_p Le^{2/3})`，`Le=Sc/Pr`。
* 饱和蒸汽压 Buck(1981)，液面/冰面分开 → 干湿两态蒸发/升华自动区分。
* 海水冰点 UNESCO(1978) 多项式（S=33 psu → T_f = −1.81 ℃），L_f、c_w 随温度变化。
* 冰层导热阻力：湿态下薄膜温度为 T_f，外层表面温度由
  `q_loss(T_s) = k_ice (T_f − T_s)/t` 联立解出（k_ice=2.2 W/m·K）——
  **冰层增厚会自动抑制结冰率**，这是模型自限的关键。

### 2.2 干/湿分区（按表面热平衡符号判定）

令 `Q_sink` 为在 T_f 时环境可带走的热流（含冰层导热修正），
`ṁ_w` 为撞击水通量，则

* **干态（dry）**：`Q_sink ≥ ṁ_w (L_f + c_w(T_w−T_f))` —— 来水可全部冻结，
  多余冷量把冰面降到 T_f 以下（干冰面，升华，λ=1）；
* **湿态（wet）**：否则表面维持 T_f 液膜，仅冻结 **冻结分数**
  `λ = (Q_sink − ṁ_w c_w (T_w−T_f)) / (ṁ_w L_f) ∈ [0,1]`，其余成为溢流（runoff）；
* **无飞沫（dry_nospray）**：ṁ_w=0，冰面纯辐射/对流传热，T_s 由 `Σq=0` 解出
  —— 判据 (a) 即在此状态下检验。

### 2.3 飞沫通量经验关系

`ṁ_w = E_zone · W_eff(U_rel) · U_rel · (−n·û_wind)_+`，
`H_s = 0.21 U₁₀²/g`（Pierson–Moskowitz 充分成长），
`W_eff = W_ref √(H_s/H_ref)`（W_ref = 2.0e-4 kg/m³ @ H_ref=2 m，含飞沫收集效率）。
表面分区遮挡因子 E_zone：迎风 1.0 / 侧向 0.75 / 背风 0.15 / 上水平 0.45 / 下水平 0.05。
**W_ref 是整个模型唯一的标定旋钮**（`spray.CALIBRATION`），热平衡子模型没有可调参数，
该披露见 5.2 与 6.1。

### 2.4 积冰 → 稳性链路

1. 逐面板时间积分（准定常热平衡 + 显式质量累积，dt=600 s，冰层形心沿外法向偏移 t/2）；
2. 逐面累加质量与形心 → `IceLoad(mass, Σm·z, Σm·y)`；
3. 附加质量引起的**下沉量** `δT = m/(ρ A_w)`、`KG' = (Δ·KG + Σmz)/(Δ+m)`、
   `y_G' = Σmy/(Δ+m)`、`BM' = B²/(12T')`，故 `GM' = KB' + BM' − KG'`；
4. `GZ(φ)`：剖面多边形裁剪 + 等排水求浸深 + 形心 → `GZ = (y_B−y_G)cosφ + (z_B−z_G)sinφ`；
5. 结冰横倾力矩 `M = g Σm·y`（kN·m），静水横倾角由 `GZ(φ)=0` 求得（向加载侧倾）；
6. 判据：IS Code 2008 A.2.2（GM≥0.15 m、A(0–30°)≥0.055 m·rad、GZ(30°)≥0.20 m、
   GZ_max 出现在 ≥25°、A(30–40°)≥0.03 m·rad）在**结冰后**复算；并以
   Polar Code Part I-A 6.3.1 的 30 cm/7.5 cm 设计冰载另算一次。

### 2.5 工具链

纯 `math`（无 numpy/scipy 依赖）的领域内核 → pytest（30 例）→
`tools/m796_marine_icing_validation.py`（跑 3 个案例、4 项判据、4 张图、写 `validation.json`）
→ `tools/m796_report_gen.py`（从工件注入数字生成本报告）→ FastAPI 路由
（`/api/v1/ship/icing/{capability,heat_balance,spray,accumulate,benchmark}`）。

---

## 3 计算结果

### 3.1 机器可算判据（全部满足）

| 编号 | 判据 | 容差 | 计算值 | 判定 |
|---|---|---|---|---|
| (a) | 干态静止热平衡残差 \|Σq\| | < 1e-6 W/m² | 1.947e-12 W/m² (15 组 (T_a,U) 采样最差) | ✅ |
| (b) | 结冰率 vs Mákiranta 型解析基准 相对误差 | < 15 % | 11.5 % (4.42 vs 3.96 mm/h) | ✅ |
| (c) | GM 损失 vs 分层叠加解析 相对误差 | < 3 % | 1.25 % (18 个 (案例×层数) 组合最差) | ✅ |
| (d) | pytest 通过数 | ≥ 15 | 30 passed | ✅ |

判据 (a) 的 15 组采样覆盖 T_a ∈ {−5,−10,−15,−20,−30} ℃ ×
U ∈ {2,10,20} m/s，最差残差 1.947e-12 W/m²（比容差小 6 个数量级，
是求解器真实收敛精度而非宽容通过）。判据 (b) 的标定点见 3.4。

### 3.2 三个算例（气象与积冰总量）

| 案例 | 船舶 | T_a °C | U₁₀ m/s | T_w °C | 相对风速 m/s | 时长 h | 积冰质量 t | 形心 z m | 形心 y m |
|---|---|---|---|---|---|---|---|---|---|
| severe | polar_fishing_vessel_28m | -15.0 | 18.0 | 0.5 | 18.97 | 24 | 20.42 | 4.99 | 0.288 |
| calib | polar_fishing_vessel_28m | -10.0 | 10.0 | 1.0 | 11.66 | 24 | 13.88 | 5.01 | 0.304 |
| bulker | ice_class_bulker_90m | -10.0 | 10.0 | 1.0 | 12.21 | 24 | 108.57 | 11.90 | 0.799 |

### 3.3 逐分区积冰场（案例 `fishing_severe`，T_a=−15 ℃, U₁₀=18 m/s, 相对风
18.97 m/s, 24 h）

| 表面分区 | 面板数 | 面积 m² | 平均冰厚 mm | 最大冰厚 mm | 冰质量 t | 典型相态 |
|---|---|---|---|---|---|---|
| horizontal_down | 86 | 40.9 | 35.1 | 35.1 | 1.32 | dry |
| horizontal_up | 87 | 169.1 | 95.9 | 98.8 | 14.74 | wet |
| leeward | 105 | 50.8 | 0.0 | 0.0 | 0.00 | dry_nospray |
| side | 46 | 21.9 | 98.4 | 99.8 | 1.98 | wet |
| windward | 59 | 28.9 | 90.1 | 92.0 | 2.39 | wet |

要点：**迎风面、前壁（相对风撞击面）与上部水平面处于湿态（热汇受限）**，
逐面板冰厚最大 99.8 mm；背风面因
`(−n·û)_+ = 0` 在本模型下完全不结冰（见 5.3 局限 2）。冰层导热阻力使增长率随
冰厚显著衰减：清洁表面瞬时结冰率
10.6 mm/h，
而 24 h 内迎风面平均冰厚
90.1 mm 对应平均速率仅 3.75 mm/h
（衰减因子 0.35）。
这是物理上的自限行为，若无导热阻力，24 h 将给出
255 mm 的线性外推，明显高估。

### 3.4 标定点（−10 ℃、U=10 m/s）与解析基准对比

| 量 | 主模型（逐面板热平衡） | 解析基准（Mákiranta 型独立闭合） | 相对误差 |
|---|---|---|---|
| 结冰率 | 4.420 mm/h | 3.963 mm/h | **11.5 %** |
| 冻结分数 λ | 0.544 | 0.488 | 11.5 % |
| 对流 h_c W/(m²·K) | 25.98 | 23.48 | 10.6 % |
| 热汇 Q_sink W/m² | 394.5 | 356.0 | 10.8 % |
| 相态 | wet | wet | 一致 |

两个实现的差别是**闭合选择**而非数值：主模型用混合流平均 Nu + 膜温物性 +
Lewis 类比 Le^{2/3} + 蒸汽密度差；基准用 ISO 12494 局部湍流 Nu + 空气物性 +
Le=1 + 混合比（mixing ratio）差 + 冻结分数**解析式**（无根求解）。因此二者之差
度量的是"配方敏感性"，把辐射/蒸发分解换一种同样合法的写法，结冰率就会移动
~20 %（下表），这正是判据 (b) 取 15 % 文献级带宽、而不是 3 % 的原因。

### 3.5 闭合敏感性（为何不能声称 <3 %）

| 对流闭合选择 | h_c W/(m²·K) | 结冰率 mm/h |
|---|---|---|
| baseline (mixed-flow avg, film props) | 25.98 | 4.42 |
| pure turbulent avg (no laminar part) | 32.47 | 5.42 |
| local turbulent coefficient | 23.90 | 4.10 |
| free convection ignored | 25.20 | 4.30 |

### 3.6 结冰后的稳性

| 案例 | GM 空船 m | GM 结冰后 m | GM 损失 m | GM 损失 % | 结冰横倾力矩 kN·m | 结冰横倾角 ° | 结冰后 IS Code 全部满足 | 不满足项 |
|---|---|---|---|---|---|---|---|---|
| polar_fishing_vessel_28m<br>-15 °C / 18 m/s | 0.524 | 0.410 | -0.1146 | -21.9 | 57.6 | 1.74 | ❌ | area_0_30, gz_at_30, heel_at_max_gz, area_30_40 |
| polar_fishing_vessel_28m<br>-10 °C / 10 m/s | 0.524 | 0.444 | -0.0802 | -15.3 | 41.4 | 1.17 | ❌ | gz_at_30, heel_at_max_gz, area_30_40 |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | 1.600 | 1.506 | -0.0945 | -5.9 | 850.8 | 0.38 | ✅ | — |

### 3.7 IS Code 2008 A.2.2 逐项判据（空船 / 结冰后 / Polar Code 设计冰载）

| 案例 | 判据 | 限制 | 空船 | 结冰后 | Polar Code 30/7.5 cm 设计冰载 |
|---|---|---|---|---|---|
| polar_fishing_vessel_2<br>-15 °C / 18 m/s | GM ≥ 0.15 m | 0.150 | 0.5241 | 0.4095 ✅ | 0.1523 ✅ |
| polar_fishing_vessel_2<br>-15 °C / 18 m/s | A(0–30°) ≥ 0.055 m·rad | 0.055 | 0.0745 | 0.0466 ❌ | -0.0066 ❌ |
| polar_fishing_vessel_2<br>-15 °C / 18 m/s | GZ(30°) ≥ 0.20 m | 0.200 | 0.2277 | 0.1132 ❌ | -0.1434 ❌ |
| polar_fishing_vessel_2<br>-15 °C / 18 m/s | GZ_max 出现在 ≥ 25° | 25.000 | 26.5000 | 21.5000 ❌ | 12.5000 ❌ |
| polar_fishing_vessel_2<br>-15 °C / 18 m/s | A(30–40°) ≥ 0.03 m·rad | 0.030 | 0.0358 | 0.0129 ❌ | -0.0382 ❌ |
| polar_fishing_vessel_2<br>-10 °C / 10 m/s | GM ≥ 0.15 m | 0.150 | 0.5241 | 0.4439 ✅ | 0.1523 ✅ |
| polar_fishing_vessel_2<br>-10 °C / 10 m/s | A(0–30°) ≥ 0.055 m·rad | 0.055 | 0.0745 | 0.0551 ✅ | -0.0066 ❌ |
| polar_fishing_vessel_2<br>-10 °C / 10 m/s | GZ(30°) ≥ 0.20 m | 0.200 | 0.2277 | 0.1484 ❌ | -0.1434 ❌ |
| polar_fishing_vessel_2<br>-10 °C / 10 m/s | GZ_max 出现在 ≥ 25° | 25.000 | 26.5000 | 23.0000 ❌ | 12.5000 ❌ |
| polar_fishing_vessel_2<br>-10 °C / 10 m/s | A(30–40°) ≥ 0.03 m·rad | 0.030 | 0.0358 | 0.0199 ❌ | -0.0382 ❌ |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | GM ≥ 0.15 m | 0.150 | 1.6000 | 1.5055 ✅ | 1.0926 ✅ |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | A(0–30°) ≥ 0.055 m·rad | 0.055 | 0.2315 | 0.2101 ✅ | 0.1412 ✅ |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | GZ(30°) ≥ 0.20 m | 0.200 | 0.7560 | 0.6703 ✅ | 0.3353 ✅ |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | GZ_max 出现在 ≥ 25° | 25.000 | 29.0000 | 27.5000 ✅ | 21.5000 ❌ |
| ice_class_bulker_90m<br>-10 °C / 10 m/s | A(30–40°) ≥ 0.03 m·rad | 0.030 | 0.1261 | 0.1089 ✅ | 0.0405 ✅ |

### 3.8 GM 损失 vs 分层叠加解析

| 案例 | 分层数 | 分层叠加 ΔGM m | 逐面板 ΔGM m | 相对偏差 % |
|---|---|---|---|---|
| fishing_severe | 2 | -0.11316 | -0.11457 | 1.232 |
| fishing_severe | 3 | -0.11314 | -0.11457 | 1.253 |
| fishing_severe | 4 | -0.11314 | -0.11457 | 1.250 |
| fishing_severe | 8 | -0.11314 | -0.11457 | 1.253 |
| fishing_severe | 16 | -0.11314 | -0.11457 | 1.253 |
| fishing_severe | 64 | -0.11314 | -0.11457 | 1.252 |
| fishing_calib | 2 | -0.07952 | -0.08024 | 0.896 |
| fishing_calib | 3 | -0.07951 | -0.08024 | 0.908 |
| fishing_calib | 4 | -0.07951 | -0.08024 | 0.906 |
| fishing_calib | 8 | -0.07951 | -0.08024 | 0.908 |
| fishing_calib | 16 | -0.07951 | -0.08024 | 0.908 |
| fishing_calib | 64 | -0.07951 | -0.08024 | 0.907 |
| bulker_calib | 2 | -0.09418 | -0.09449 | 0.320 |
| bulker_calib | 3 | -0.09418 | -0.09449 | 0.323 |
| bulker_calib | 4 | -0.09418 | -0.09449 | 0.325 |
| bulker_calib | 8 | -0.09418 | -0.09449 | 0.325 |
| bulker_calib | 16 | -0.09418 | -0.09449 | 0.325 |
| bulker_calib | 64 | -0.09418 | -0.09449 | 0.325 |

分层叠加（把冰按高度分箱、每箱用**面积加权**形心高度代表）与逐面板积分
（冰层形心沿法向偏移 t/2）在 2–64 层范围内的偏差稳定在 ≤
1.25 %，偏差不随层数单调下降说明它主要来自
"法向偏移"这一物理差异而非分箱误差——两者都被 < 3 % 的判据覆盖。

### 3.9 图

| 图 | 内容 |
|---|---|
| `fig1_heat_balance.png` | (a) 标定面板热流分解（对流/蒸发/长波 vs 冻结潜热+飞沫显热）；(b) 迎风面板结冰率随 T_a（U=5/10/15/20 m/s）；(c) 干/湿相态图 + 冻结分数等值线 |
| `fig2_accretion_field.png` | (a) 逐面板冰厚"场"（横剖面投影着色）；(b) 冰厚随高度/表面分区分布；(c) 三个算例累积冰质量时间历程 |
| `fig3_stability.png` | (a) GZ 曲线（空船 / 结冰后 / Polar Code 设计冰载）叠加 IS Code GZ(30°)≥0.20 m 线；(b) GM 与结冰横倾角时间历程；(c) IS Code 逐项 值/限值 比值条形图（绿=通过，红=不满足） |
| `fig4_benchmark.png` | (a) 标定点 vs 解析基准（含观测"中度过冰"带 0.7–2.0 cm/h 参照）；(b) 闭合敏感性；(c) 分层叠加收敛性 |

---

## 4 结果判读

1. **渔船（28 m）是积冰敏感体**：空船 GM 0.524 m，
   GZ(30°) 仅 0.228 m（本就贴近 0.20 m 限值）；
   ∇ = 452 t；−10 ℃/10 m/s 24 h 积冰 13.88 t（占排水量
   3.1 %）即已使
   gz_at_30, heel_at_max_gz, area_30_40 不满足；
   更冷的 −15 ℃/18 m/s 24 h 积冰 20.42 t，GM 掉
   0.115 m，GZ(30°) 从 0.228 降到
   0.113 m，复原力臂曲线被"压平"。
   **这不是数值噪声，而是小船过冬积冰失效的经典机制**。
2. **Polar Code 30 cm 设计冰载对 28 m 渔船是不可承受的**：
   设计冰载 75.8 t（占排水量
   16.8 %）下 GM 掉到
   0.152 m（仍 >0.15 m），但 A(0–30°) 变为负值、
   GZ(30°) = -0.143 m
   ——**稳性丧失**。这与 Polar Code 要求小船配置更大的稳性储备/结冰管理方案的
   工程结论一致。
3. **90 m 冰级散货船（∇ = 8,469 t）24 h 积冰 108.6 t
   （占排水量 1.3 %）仍满足 IS Code**
   （GM 1.506 m，GZ(30°) 0.670 m），
   但 Polar Code 设计冰载 594 t 下
   heel_at_max_gz
   不满足——大船由"复原力臂形状/最大力臂角"而非 GM 主导。
4. **结冰横倾力矩数值上是二阶效应**：本算例横倾力矩
   58 kN·m → 横倾角 1.74°，
   远小于 GM 损失的影响；但对非对称上层建筑（如单舷吊机、艏楼偏心）横倾项可达
   数度，模型已把该项独立输出，不与 GM 损失混算。

---

## 5 注意点 / 局限 / 适用范围

### 5.1 适用范围
极区/近极区航行的**水面船舶上层建筑**，T_a ∈ [−40, 0] ℃、U₁₀ ∈ [0, 40] m/s、
T_w ∈ [−1.9, 5] ℃、航速 ≤ 15 m/s；给出**短时（≤ 72 h）**积冰与稳性变化。
不适用于：叶片/索缆等小尺度构件（走 ISO 12494 更好）、结构振动/海冰撞击、
冰层融化的长期热惯性（见 5.3）。

### 5.2 唯一的标定旋钮与它的影响（实测，非估计）

飞沫有效含水量 `W_ref = 2.0e-4 kg/m³` 是整个模型中**唯一**的自由参数
（热平衡子模型零可调参数）。标定点（−10 ℃、U=10 m/s）上把 W_ref 缩放 0.1/1/10 倍的
实测响应（`artifacts/m796/validation.json` →
`criteria.b_icing_rate_vs_reference.spray_calibration_sensitivity`）：

| W_ref 倍数 | W_ref kg/m³ | 飞沫通量 kg/(m²·s) | 相态 | λ | 结冰率 mm/h |
|---|---|---|---|---|---|
| ×0.1 | 2.000e-05 | 2.069e-04 | dry | 1.000 | 0.81 |
| ×1.0 | 2.000e-04 | 0.002 | wet | 0.544 | 4.42 |
| ×10.0 | 0.002 | 0.021 | wet | 0.024 | 1.95 |

解读：W_ref 决定的是**相态与限制机制**而不是单调放大结冰率。降低 10 倍
（×0.1）进入 **dry 供给受限**，结冰率降到
0.81 mm/h（等于飞沫通量本身，λ=1）；
提高 10 倍（×10）仍是 **wet**，但巨量来水的显热
项把冻结分数压到 λ=0.024，结冰率反而降到
1.95 mm/h（"暖水量大则不易结冰"的真实物理：
海水温度 1 ℃、需先冷却 2.8 K 才冻结）。基准取 4.42 mm/h
（λ=0.544），处于热汇受限的湿态中部，是对
两类失效模式都不偏置的选择。

### 5.3 已知简化（诚实清单）
1. **无脱冰（shedding）与融化**：模型中冰只增不减，故长时间（>72 h）会高估积冰
   与 GM 损失（保守侧）。
2. **背风面零飞沫**：`(−n·û)_+ = 0` 时为 0，未建模湍流扩散/尾流夹带。
   Polar Code 设计冰载仍给背风/非暴露面 7.5 cm 覆盖。
3. **无船体运动（6-DOF）**：飞沫由相对风驱动，未计入横摇/纵摇引起的甲板抨击
   （green water）通量增强。
4. **barge 剖面近似**：GZ 用矩形横剖面 + 棱柱长度，无舭部圆角/外飘；
   真实船型需由 `geometry` 能力的型线剖面替换（接口已按剖面多边形设计，
   `clip_polygon_halfplane` 对任意简单多边形有效）。
5. **未计冰对风压侧面积/受风面积的放大**、也未计冰对初稳性以外的横摇惯性矩影响。
6. **未做网格无关性收敛到极细**：面板 0.75/1.0 m，时间步 300 s vs 1800 s
   的积冰总量偏差 < 2 %（pytest `test_integration_timestep_convergence`）。

### 5.4 平台能力依赖
节点的原始设计拟调用平台 `geometry` 能力生成上层建筑网格；本工作环境内
`:8001`（ship-performance API）不可达（`audit_capabilities` 与 `/health` 探测均
超时/失败），因此几何以 `mesh.Block`（舱室/上层建筑块）在 worktree 内自建并
离散，接口形状与 `geometry.generate` 的块定义一致；接入真实平台时只需把
`VesselSpec.blocks` 换成 `geometry` 返回的块列表，物理内核无需改动。

---

## 6 误差判定（逐项，显式回答"是否 <3%"）

| 关键量 | 基准 | 相对误差 | 是否 <3 % | 是否满足本节点判据 |
|---|---|---|---|---|
| 干态静止热平衡残差 | 解析零点（Σq=0） | 1.947e-12 W/m² 绝对残差 | 是（远优） | ✅ (a) < 1e-6 W/m² |
| 结冰率（−10 ℃, U=10 m/s） | Mákiranta 型解析基准 3.96 mm/h | 11.5 % | **否**（>3 %，见 6.1） | ✅ (b) < 15 % |
| GM 损失 | 分层叠加解析 | 1.25 % | **是** | ✅ (c) < 3 % |
| GZ（小角度 ≤10°） | wall-sided 解析式 | ≤ 3 %（pytest 逐角断言，10° 处两法一致到 2 位有效数字） | 是 | — |
| 积冰总量（时间步收敛） | dt=300 s vs 1800 s | < 2 %（pytest） | 是 | — |
| 设计冰载质量 | 解析 ΣρtA（900 kg/m³×0.30/0.075 m） | 0（机器精度，pytest 断言 rel<1e-9） | 是 | — |

### 6.1 为什么结冰率只能是"文献级 15 %"而不是 <3 %

* **锚点不是实测点**：离线环境无外网（`curl https://…` 超时，HTTP 000），无法
  复核 Mákiranta et al. (2013) 原始数值表，因此基准取"同一物理、不同闭合"的
  解析实现（`icing/reference.py`，与主模型代码路径零共享）。判据 (b) 因此是
  **配方一致性检验**，不是实验验证。
* **配方本身的不确定度 ≥ 15 %**：3.5 节显示，仅把对流闭合从混合流平均换成
  纯湍流平均，结冰率就从 4.42 变到
  5.42 mm/h（+23 %）；
  辐射/蒸发的视角因子、Le 指数、物性取膜温还是气温，各自贡献 5–15 %。
  声称 < 3 % 只能是调参结果，不是证据。
* **绝对量级的旁证**：本模型 4.42 mm/h 落在海洋
  积冰文献常报的 1–5 mm/h（中度）与 Overland 结冰等级"轻度(<0.7 cm/h)/中度
  (0.7–2.0 cm/h)"分界附近，量级正确；但**绝对值的实验不确定度 ~±30 %**，
  报告与 API 均按"工程量级 + 相对趋势可信"使用，不作精确预报承诺。

### 6.2 本节点未完成 / 明确不做的部分
* 未接真实 `geometry` 能力（原因见 5.4，为环境不可达，非设计缺陷）；
* 未做与实测船积冰观测的直接对比（离线无数据集）；
* 未建模脱冰/融化与船体运动耦合（5.3 第 1、3 条）。

---

## 7 审计链

```
# 领域内核 + API + 测试（全部在 worktree 内）
backend/services/ship_performance/icing/{properties,heat_balance,spray,mesh,
    accumulation,stability,reference,model}.py
backend/api/domain/ship_icing.py            # /api/v1/ship/icing/*
tests/physics/test_marine_icing.py          # 30 passed

# 复现（约 149 s）
python3 tools/m796_marine_icing_validation.py   # -> artifacts/m796/validation.json + 4 figs + 3 cases
python3 tools/m796_report_gen.py                # -> docs/reports/m796_marine_icing.md
python3 -m pytest tests/physics/test_marine_icing.py -q
```

验收环境：Python 3.11.6，numpy 2.4.6，
matplotlib 3.10.8，`Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38`；
复现总耗时 149 s。证据等级 **REAL**：所有数字由逐面板
热平衡 + 剖面精确积分的实算产生，无硬编码结果、无随机数、无桩函数
（`audit_capabilities` 中新增能力路径 `ship.icing.superstructure`）。

---

## 8 参考文献

1. Mákiranta, R., Lozowski, E. P., George, T. (2013) *Numerical modelling of
   sea-water freezing on ship superstructures*. POAC'13.
2. Lozowski, E. P., Szilder, K., Makkonen, L. (2000) *Computer simulation of
   marine icing*. Marine Technology 37(3), 155–161.
3. ISO 12494:2017 *Atmospheric icing of structures*（对流闭合与冻结分数框架）。
4. Buck, A. L. (1981) *New equations for computing vapor pressure and
   enhancement factor*. J. Appl. Meteorol. 20, 1527–1532.
5. UNESCO (1978) *Eighth report of the joint panel on oceanographic tables and
   standards*（海水冰点多项式）。
6. IMO Res. MSC.267(85) *IS Code 2008*, Part A 2.2（完整稳性判据）。
7. IMO Res. MSC.385(94) *Polar Code*, Part I-A 6.3（积冰允量 30 cm / 7.5 cm）。
8. Overland, J. E. et al. (1986) / Overland (1990) 船舶积冰等级
   （轻度/中度/重度结冰率分级，用于量级旁证）。
9. Incropera & DeWitt, *Fundamentals of Heat and Mass Transfer*, ch. 7
   （平板混合流平均 Nu、Churchill 合成、Lewis 类比）。
