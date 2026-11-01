# IS Code 2008 A.2.3 天气衡准（severe wind and rolling）——平台缺口补齐与解析基准验证（m1660）

**学科**：稳性 | **证据等级**：杠杆/面积链 **REAL**，`θ1` 系数表 **WITHHELD**（见 §5.1）
**机器验收**：`tests/test_m1660_weather_criterion.py` 24 passed
**工件**（数字全部由 `tools/m1660_weather_validation.py` 程序化注入，本文件零手抄）

| 类型 | 路径 |
|---|---|
| 领域内核 | `backend/services/ship_performance/stability/weather_criterion.py` |
| 包入口 | `backend/services/ship_performance/stability/__init__.py` |
| pytest | `tests/test_m1660_weather_criterion.py`（24 项判据） |
| 数值工件 | `artifacts/m1660/validation.json` |
| 图 | `artifacts/m1660/fig1_gz_and_levers.png`、`fig2_margin_vs_windage.png`、`fig3_quadrature_convergence.png` |
| 复现 | `python3 tools/m1660_weather_validation.py` |

---

## 1 案例介绍

### 1.1 为什么这是"验证欠账"而不是新玩具

`audit_capabilities` 把 `calc.stability` 判为 **REAL**，平台已有两条 GZ 证据链
（`m1384` 内联墙侧闭式 + `m1386` 服务化精确箱体基准，`m1564` 裁决合成）。但**稳性衡准只做到了
IS Code 2008 Part A §2.2 的通用项**：

- `backend/services/ship_performance/icing/stability.py:324-356` 实现了 `A0-30 ≥ 0.055`、
  `A30-40 ≥ 0.09`、`GZmax ≥ 0.20 m @ φ ≥ 25°`、`GM ≥ 0.15 m`；
- 同一处在 docstring 里**自己声明** weather-criteria 项不在范围内；
- 全仓 grep（`weather criterion` / `lw1` / `lw2` / `0.055`+风压）确认 **A.2.3 无任何实现**。

即：平台能回答"这条船的 GZ 面积够不够"，**回答不了"这股阵风它扛不扛得住"**——而后者正是
小船/大受风面积船（渔船、科考船、集装箱船）的真实失效模式，也是 Polar Code 与
IS Code 2008 里对受风面积最敏感的一条。

### 1.2 案例：全尺度箱形驳船（可解析验证的几何）

选箱形驳船不是偷懒，而是**只有这一几何存在精确解析复原力臂**（`services/stability_reference.py`
的 `exact_gz_box`：浸没剖面按常排水量求沉降 + 鞋带公式算形心，与墙侧闭式零共享代码路径）。
这正是本仓既有的"独立复算"范式。

| 参数 | 值 |
|---|---|
| L × B × T × D | 100.0 × 16.0 × 6.0 × 9.0 m |
| 排水量 Δ | 9840 t（ρ = 1.025 t/m³） |
| 受风面积 A | 300 m²（上层建筑侧投影） |
| 受风形心到水下侧向形心高度 Z | 7.5 m（10.5 m − T/2） |
| 风压 P | 504 Pa（A.2.3.1 规定值） |
| 墙侧闭式有效域上限 | 20.56°（`wall_sided_limit_deg`，甲板边缘入水） |

两工况：**loaded KG = 6.0 m（GM = 0.556 m，合格）**、**light KG = 6.5 m（GM = 0.056 m，GM 项已不合格但天气衡准仍须评）**。

---

## 2 怎么做的

1. **判据先于实现**：A.2.3 可写成三条纯几何/代数命题 —— 杠杆式、面积式、回摇角式。
   前两条不依赖任何查表值，因此可以在离线条件下自证；第三条依赖规范表值，必须**降级处理**而不是假装 REAL。
2. **实现**（`weather_criterion.py`，纯标准库、无 numpy 依赖）：
   - `wind_heeling_levers`：`lw1 = P·A·Z /(1000·g·Δ)`，`lw2 = 1.5·lw1`（量纲：Pa·m²·m = J，÷ N = m）；
   - `simpson_integrate`：复化 Simpson（n 强制偶数）；
   - `wall_sided_area_analytic`：墙侧复原力臂的**闭式原函数**
     `F(θ) = −GM·cosθ + ½·BM·(secθ + cosθ)`，因 `d/dθ(secθ+cosθ) = sinθ·tan²θ`；
   - `weather_criterion`：`b = ∫_{θ1}^{40°}(GZ − lw2)dθ` 与 `a = ∫_{θ1}^{θf} lw2 dθ` 比较，
     `θf` 默认由"消失角扫描"（0.05° 步长，首个 `GZ ≤ 0` 的角，封顶 40°）给出，也可由调用方给排水孔角；
   - 结果对象携带 `verdict ∈ {pass, fail, undefined}`、全部中间量、以及 provenance 标签。
3. **验证手段**（不含任何"记忆里的表值"）：
   - **闭式对拍**：数值积分 vs 解析原函数；
   - **收敛阶自证**：n 减半误差应按 2⁴ 下降；
   - **恒等式**：`lw1` 对 `A·Z` 线性（比值 2.000）、对 Δ 严格反比（比值 0.500）、`lw2/lw1 = 1.500`，偏差为 0；
   - **物理边界机器化**：墙侧闭式在自身有效域内与精确箱体一致的误差，及越界后的发散量，写进验收而非口述。
4. **工具链**：`pytest`（判据）+ `tools/m1660_weather_validation.py`（工件与图，matplotlib Agg）。

---

## 3 计算结果

### 3.1 判据锚点（`artifacts/m1660/validation.json → anchors`）

| 编号 | 判据 | 结果 | 结论 |
|---|---|---|---|
| A | 400 段 Simpson vs 闭式原函数（∫²¹°₄₀° GZ dθ） | 相对误差 **8.2e−13** | < 1e−9 ✔ |
| B | n = 40→320 逐次减半的误差比 | 观测阶 **3.9997**（4 次逐半 3.9991/3.9998/3.9999/4.0002） | Simpson 四阶 ✔ |
| C | `lw1` 对 A·Z / Δ 的恒等式 + `lw2/lw1` | 比值 2.000 / 0.500 / 1.500，最大偏差 **0.0** | 恒等 ✔ |
| G | 墙侧闭式 vs 精确箱体 | 限值 −2°（18.56°）处误差 **3.1e−16 m**；40° 处误差 **0.912 m**（精确值为负、闭式为正） | 域内精确、域外发散 ✔ |

### 3.2 设计点衡准结果（A = 300 m²，Δ = 9840 t ⇒ lw1 = 0.011752 m，lw2 = 0.017627 m）

| 工况 | 复原力臂基准 | θ1 | θf | a [m·rad] | b [m·rad] | b−a | b/a | 判定 |
|---|---|---|---|---|---|---|---|---|
| loaded KG6.0（GM 0.556） | 精确箱体 | 19.86° | 40.0° | 0.00620 | 0.10610 | 0.09991 | 17.12 | **pass** |
| loaded KG6.0 | 墙侧闭式 | 19.86° | 40.0° | 0.00620 | 0.21108 | 0.20488 | 34.07 | pass |
| light KG6.5（GM 0.056） | 精确箱体 | 20.53° | 36.38° | 0.00488 | 0.01787 | 0.01300 | 3.67 | **pass** |
| light KG6.5 | 墙侧闭式 | 20.53° | 40.0° | 0.00599 | 0.12285 | 0.11686 | 20.51 | pass |

精确箱体下 `GZmax` = 0.353 m @ 27.75°（loaded）/ 0.133 m @ 24.5°（light），与 A.2.2 的
「GZmax ≥ 0.20 m @ φ ≥ 25°」一致/不一致的分野也由同一曲线给出——两条衡准现在能接在同一条 GZ 上评。

### 3.3 图

- **`fig1_gz_and_levers.png`**：两工况的 GZ 曲线（精确箱体 vs 墙侧闭式）+ `lw1`/`lw2` 水平线 +
  `θ1`、40° 上限、墙侧有效域上限竖线。可直接读出"闭式在 20.56° 之后一路偏离，40° 处比真值高 0.91 m"。
- **`fig2_margin_vs_windage.png`**：裕度 `b − a` 随受风面积 A（0→2000 m²）的扫描。精确箱体基下
  **light 工况在 A ≈ 659 m² 处穿过零点**（fail），而墙侧基到 2000 m² 仍为正——同一条船、同一份风，
  两种 GZ 基给出相反结论。
- **`fig3_quadrature_convergence.png`**：误差-n 双对数图与斜率 −4 参考线重合，面积积分不是"假定对"。

---

## 4 误差判定（逐项对基准）

| 关键量 | 基准 | 本实现误差 | 是否 < 3% |
|---|---|---|---|
| ∫GZ dθ（面积） | 闭式原函数（解析） | 8.2e−13（相对） | **是** |
| lw1 / lw2 | 代数恒等式 | 0.0 | **是** |
| Simpson 收敛阶 | 理论 4 | 3.9997（偏差 0.008%） | **是** |
| 墙侧闭式 vs 精确箱体（域内 18.56°） | 独立积分解 | 3.1e−16 m | **是** |
| 墙侧闭式 vs 精确箱体（40°，域外） | 独立积分解 | 0.912 m（>100%） | **否（预期内，域外失效）** |
| θ1 表值（X1/X2/s） | IS Code 2008 表 2.3.4-1..3 | **无法离线裁定** | **不适用（WITHHELD）** |

没有基准的量不冒充有基准：`θ1` 的三个系数表是**转录值**，本单只用结构与单调性做验收，绝不把它们当证据。

---

## 5 注意点 / 局限 / 适用范围

### 5.1 证据分级是显式写进代码的

`COEFFICIENT_PROVENANCE = {lw1_lw2: REAL, b_area: REAL, theta1_tables: WITHHELD}`，
`theta1_is_code()` 的返回值里带 `provenance` 字段。**任何调用方都可以用 `theta1_deg=` 直接注入**
实测回摇角，从而完全绕开转录表去出判定——这是刻意的设计：规范表值应当由持标准者 diff，而不是由模型记忆。

### 5.2 本次抓到的两个真问题

1. **越界外推把裕度放大 2.0×（loaded）/ 5.6×（light）**：`b/a` 从精确基的 17.1/3.67 被闭式基抬到
   34.1/20.5。且 light 工况在 A ≈ 659 m² 以上两者判定相反。**结论：天气衡准的判定只能建立在
   有效域内的 GZ 上**；把 `calc.stability` 的墙侧闭式直接喂给天气衡准（不检查 `wall_sided_valid`）
   会系统性地放行不合格船。已在模块 docstring 与测试
   `test_verdict_differs_between_shipped_and_exact_righting_arm` 中固化为回归。
2. **`θ1 ≥ 40°` 时 b 面积为空**：初版直接 `max()` 空序列抛异常；现返回 `verdict="undefined"` 并写明理由
   （判据在规范里也是无定义的，不能悄悄返回 pass）。

### 5.3 适用范围

- 适用：水面船舶**完整稳性**的 A.2.3 判定；GZ 可来自任意源（闭式、精确积分、积冰工况、试验表）。
- 不适用：破舱稳性（SOLAS II-1 概率法）、参数横摇/纯稳性丧失（第二代衡准 Level 2）、
  结冰工况下的 Polar Code 6.3 附加项（走 `ship_performance/icing`）。
- 本单未做（诚实标注）：**未接 HTTP 端点**（`backend/main.py` 的 domain 路由注册面本次未动，
  避免与在飞的 API 改动冲突）；未做与商业稳性软件（如 NAPA/GHS）的比对（无授权数据）。

---

## 6 复现

```bash
python3 -m pytest tests/test_m1660_weather_criterion.py -q      # 24 passed
python3 tools/m1660_weather_validation.py                        # 生成 artifacts/m1660/*
```