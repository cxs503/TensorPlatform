# 湿面积子模型几何精确化：`/calculations/resistance` 支持回转体母线积分（m1683）

> 承接 m1666（`calc.resistance` 外部基准复核 · honest-FAIL 子模型级）「给下一张自找活单的建议」第 2 条：
> **对 `form_type=body_of_revolution` 用几何/实测 S**。本单把这条建议做成已合并、可回归的工程能力，
> 并对闭式几何基准逐项复核，不做任何表值转录。
>
> 证据等级：几何积分 **REAL**（闭式锚点机器精度级一致）；端点接线 **REAL**（默认路径零改动、可回归）。
> 算例出处：`artifacts/m1683/wetted_surface_ledger.json`（程序化生成，本文件所有数字零手抄）。

## ① 案例介绍

阻力链路里湿面积 S 同时进入摩擦阻力 `Rf = ½ρ·S·Cf·V²` 与总阻力系数 `Ct = Rt/(½ρSV²)`。
平台此前的 S 是一条形状盲经验拟合：

```
S_legacy = π · B · L · (0.5 + 0.25·Cb)
```

m1666 用仓内唯一带实测 S 的算例（DARPA SUBOFF，Roddy 1990，5.42 m²）与同 L/D 长椭球闭式解
（5.494 m²，自校 1.36%）双向对标，得 −18.49% / −19.58%，越 15% 线 → 子模型判 FAIL。
根因不是标定常数，而是**拟合式的形状**：对 B 线性、对 Cb 只有 0.25 权重，换 Cb 洗不白。

本单的物理立场：回转体（潜艇、鱼雷、SUBOFF 类细长体）的湿面积不是待回归量，而是**几何量**——
给定母线 `r(x)`，绕轴对称回转面的侧面积是精确积分。表 1 为算例参数。

| 参数 | 取值 | 来源 |
|---|---|---|
| L | 4.356 m | `benchmark_reference.json`（SUBOFF 母型） |
| D = B = T | 0.508 m | 同上（回转体正典约定 B=T=D） |
| Cb（几何反算） | 0.523594 | 本单由母线积分得到 |
| π/6 | 0.523599 | 长椭球闭式 Cb，相对差 8e-6 |
| 介质 | ρ=998 kg/m³, ν=1.004e-6 m²/s | 端点默认 |
| 工况 | V=2.0 m/s（端点测试用） | — |

## ② 怎么做的

新增 `backend/services/wetted_surface.py`，核心是把母线**折线**绕轴回转，逐段用**精确圆台侧面积**累加：

```
dS = π (r₁ + r₂) · √(h² + (r₁ − r₂)²)          （圆台侧面积，对直母线精确）
dV = π h (r₁² + r₁r₂ + r₂²)/3                   （圆台体积，用于几何 Cb 闸门）
Cb_geom = ∇ / (L · B · T)，B = T = 2·r_max       （回转体正典约定，长椭球 → π/6）
```

- 平滑母线上的误差为 `O(h²)`（§⑥ 用 51→3201 点加密实测拟合斜率 −1.76）；
- 同时给出**离散化指示量** `|S_frustum − S_弧长梯形| / S_frustum`，调用侧可见证据质量；
- **几何前置闸门**：把由母线积分的 `Cb_geom` 与请求声明的 `cb` 比对，相对偏差 >5% 直接 400，
  遵守「几何必须先验证 Cb/L/B」的平台精度标准（不再允许形状与 Cb 互相矛盾时静默出数）；
- 端点 `/calculations/resistance` 新增可选字段 `meridian_profile: [[x, r], ...]`
  （`x` 严格递增、`r ≥ 0`），响应新增 `wetted_surface_method`，`wetted_surface_source`
  取值扩为 `geometry_integrated`。优先级：**母线几何 > 显式 `wetted_surface_m2` > 经验拟合**，
  默认路径（不传新字段）逐位不变。

工具链：`python3 -m pytest tests/test_wetted_surface_geometry.py -q`；
证据脚本见本文件末「复现方式」。

## ③ 计算结果

**表 2 三类闭式几何锚点**（`artifacts/m1683/wetted_surface_ledger.json::shape_anchors`）

| 形状 | 闭式解 S [m²] | 几何积分 S [m²] | 误差 | 旧拟合 S [m²] | 旧拟合误差 |
|---|---|---|---|---|---|
| 圆柱 D=2, L=10 | 62.831853 | 62.831853 | **0.0 %** | 43.752932 | −30.37 % |
| 球 R=1 | 12.566371 | 12.566362 | **−7.2e−5 %** | 7.928119 | −36.91 % |
| 长椭球（SUBOFF 等效） | 5.493780 | 5.493755 | **−4.5e−4 %** | 4.385931 | −20.17 % |

**表 3 母线加密收敛**（长椭球，闭式解为基准）

| N（母线点数） | 51 | 101 | 201 | 401 | 801 | 1601 | 3201 |
|---|---|---|---|---|---|---|---|
| 相对误差 [%] | −0.273 | −0.089 | −0.028 | −0.008 | −0.002 | −0.001 | −0.000 |

对数斜率 −1.76，符合 `O(h²)` 的折线逼近行为（弦长系统性略短，故误差为负）。

**图**：`artifacts/m1683/fig_wetted_surface_anchors.png`
(a) 三形状上「旧拟合 vs 几何积分」误差柱状对照，15% 线为判据；(b) 母线加密双对数收敛。

## ④ 注意点（坑 / 局限 / 适用范围）

- **坑 1：批处理 `Ct` 口径看不见面积误差。** `Ct = Rt/(½ρSV²)` 里 S 同时出现在分子分母，
  用 Ct 对标等于把面积误差淡化；必须直接对 S 立判据（本单只对标 S 本身）。
- **坑 2：旧账本的「自洽性检查」是自证。** 用同一拟合式比对自身误差 1e-7，再多次也证伪不了模型；
  本单所有基准都是闭式几何（圆柱/球/椭球/箱体），不含任何记忆表值。
- **坑 3：回转体 `Cb` 约定。** 若按 `T = r_max` 计算，长椭球得 Cb=1.048 > 1（非物理）；
  平台正典对 `body_of_revolution` 用 `B = T = D`，长椭球恰为 π/6。闸门采用正典约定，5% 容差。
- **局限 1：** 只覆盖回转体（母线）与箱体约定，未覆盖一般船型的型值表三角网格面积
  （现有 `services.ship_hydrostatics._wetted_surface` 走「横剖面周长 × L」积分，对纵向斜率
  不可忽略的船型偏小，属另一条待复核路径）。
- **局限 2：** 母线本身的正确性是**调用方证据**；本单只保证「给定几何 → S」的积分正确，
  不追溯几何来源。SUBOFF 母型母线原文数据仓内没有，因此未重标正典 12 行参考值。
- **局限 3：** 未改引擎默认经验拟合与残阻律（m1666 已声明改动需独立评审单）；
  本单是**新增可选路径**，默认行为逐位不变（有回归测试守护）。
- **未做（如实列出）：** ①`Cr` 的 Fn 依赖未重标（缺 SUBOFF 原文数据，不臆造）；
  ②未把 `wetted_surface_status` 接进 `/calculations/benchmark_validate` 响应体；
  ③未做 CFD 交叉验证。

## ⑤ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%? | <15%? | 判定 |
|---|---|---|---|---|---|
| 圆柱 S（几何积分） | πDL 闭式 | 0.0 % | **是** | 是 | PASS |
| 球 S（几何积分） | 4πR² 闭式 | 7.2e−5 % | **是** | 是 | PASS |
| 长椭球 S（几何积分） | 椭球闭式 5.49378 m² | 4.5e−4 % | **是** | 是 | PASS |
| 端点 `S`（母线路径，2001 点） | 椭球闭式 | <1e−4 % | **是** | 是 | PASS |
| 几何 Cb（母线积分） | π/6 = 0.523599 | 8e−6 相对 | **是** | 是 | PASS |
| 圆柱 S（旧拟合） | πDL 闭式 | −30.37 % | 否 | **否** | FAIL（旧） |
| 球 S（旧拟合） | 4πR² 闭式 | −36.91 % | 否 | **否** | FAIL（旧） |
| 长椭球 S（旧拟合） | 椭球闭式 | −20.17 % | 否 | **否** | FAIL（旧） |

**显式结论**：几何积分路径在全部三个闭式锚点上 **满足 <3%**（实测 <1e−3 %）；
旧拟合式在同一批锚点上 **既不满足 3% 也不满足 15%**，与其在 SUBOFF 实测上的 −18.5 % 一致，
说明 m1666 的 FAIL 判决是公式性状问题，且本单的修复把该性状问题消除（在几何已知的前提下）。
端到端总阻力 Rt 的精度**不受本单承诺**：残阻律仍为经验式（m1666 判 −25.75%…+9.69%），
这是显式遗留欠账，见 ④「未做」。

## 复现方式

```bash
python3 -m pytest tests/test_wetted_surface_geometry.py -q          # 19 passed
python3 -m pytest tests/test_wetted_surface_geometry.py tests/test_ship_calc_verification.py -q  # 45 passed
# 账本 + 图（程序化，零手抄）
python3 - <<'PY'
# 见 artifacts/m1683/wetted_surface_ledger.json 与 fig_wetted_surface_anchors.png 的生成脚本注释
PY
```

产物：`backend/services/wetted_surface.py`、`tests/test_wetted_surface_geometry.py`、
`backend/api/calculations.py`（+`meridian_profile` / `wetted_surface_method`）、
`artifacts/m1683/wetted_surface_ledger.json`、`artifacts/m1683/fig_wetted_surface_anchors.png`。

## 给下一张自找活单的建议

1. **一般船型 S 复核**：`ship_hydrostatics._wetted_surface` 的「周长 × L」积分对吃水浅/纵向斜率大的
   船型（Wigley、KCS 首尾）偏小，建议用型值表三角网格面积作第二实现并给 GCI（`O(h²)`）。
2. **闸门接线**：把 `wetted_surface_source/method`、`cb_geom` 与 Rt 误差暴露进
   `/calculations/benchmark_validate` 响应，让 UI 直接看见面积证据等级。
3. **残阻律**：需要 SUBOFF 原文 Fn–Cr 数据入库（`backend/data/propulsion_benchmark_reference.json`
   的做法可复用），方可重标，禁止臆造。