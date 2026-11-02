# 敞水性能数值基准：把 `propeller.open_water` 的 WITHHELD 变成可判分

**一句话结论**：用 Oosterveld & van Oossanen (1975) 的 B 系列 39/47 项回归做参考，
在 240 个域内工况点上逐点打分——仓内 BEM 条带核（`repo_bem_strip`）
`KT` 平均偏差 22.3%、最大
76.7%，效率系统性偏高，
零推力进速比低 8.2%；
叶元+升力线候选核（`repo_candidate_lifting_line`）**结构性不合格**（`KT` 最大偏差
238.6%，多处早早
截零）→ 二者均**未达 3% 精度门**，判定 `REJECT` /
`REJECT`。证据等级：结构层 REAL、**数值层由
WITHHELD 升为「已量化但未达标」**（不再是"无基准可判"）。

## ① 案例介绍（物理背景 / 参数表）

敞水试验（open-water test, ITTC 7.5-02-03-02）在均匀来流中测
`KT = T/(ρn²D⁴)`、`KQ = Q/(ρn²D⁵)`、`η0 = J·KT/(2πKQ)`，`J = V/(nD)`。
参考基准取 Wageningen B 系列（Open Water Screw Series）回归：

`KT = Σ Cᵢ J^{sᵢ} (P/D)^{tᵢ} (Ae/Ao)^{uᵢ} Z^{vᵢ}`（39 项），
`KQ = Σ Dᵢ J^{sᵢ} (P/D)^{tᵢ} (Ae/Ao)^{uᵢ} Z^{vᵢ}`（47 项），
适用域 Z=2–7、Ae/Ao=0.30–1.05、P/D=0.5–1.4、Re=2×10⁶（0.75R 弦长）、毂径比 0.18–0.20。

| 参数 | 取值 |
|---|---|
| 叶数 Z | [4, 5] |
| 螺距比 P/D | [0.6, 0.8, 1.0, 1.2, 1.4] |
| 盘面比 Ae/Ao | [0.4, 0.55, 0.7, 0.85] |
| 进速比 J | `J/J0 ∈ [0.1, 0.25, 0.4, 0.55, 0.7, 0.85]`（J0 为该工况零推力进速比） |
| 工况点数 | 240 |
| 参考精度门 | 3%（平台可接受）/ 15%（不可接受下限） |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **参考实现入库**：`backend/services/wageningen_b_series.py`——O&O(1975) 39/47 项
   系数表。**系数不是手抄**：程序化抽取自两个互相独立的公开实现
   （`AlfredMylne/PropCalc`、`isulovsky/Wageningen-B-series-propeller-calculator`，
   取于 2026-11-01），两者逐项、逐位一致；再用物理锚点验收（见 ⑥）。
2. **三点一面打分**：`tools/verifier/propeller_bseries_benchmark.py` 对
   `kp_curves`（叶元+升力线）、`open_water_point`（BEM 条带）、线上 :8001 端点
   （尽力探测）逐点比 `KT`/`KQ`/`η0`。
3. **双频带评分**：主带 `J ≤ 0.70 J0`（参考 `KT` 仍为 O(0.1)，相对误差稳定；
   同时报绝对误差 `|ΔKT|`）；全带 `J ≤ 0.85 J0` 单列（接近 `J0` 时 `KT→0`，
   相对误差必然发散，只能看趋势，不能当精度指标）。
4. **零点独立检**：对每个核用二分法反求自身 `J0`，与参考 `J0` 比相对偏差
   ——这条抓"推力过早消失"。
5. **判决规则**：主带（J ≤ 0.70 J0）`max|err| ≤ 3%` → `ADMISSIBLE`；`≤15%` → `PROVISIONAL`；
   否则 `REJECT`。
6. **产物**：`artifacts/verification/propeller_bseries/benchmark.json`（全部工况点
   原始回执）+ 2 张 png + 本文档（程序化生成，数字零手抄）。

## ③ 计算结果（主带，数字由 benchmark.json 程序化注入）

| kernel | verdict | KT mean|err| | KT max|err| | KQ mean|err| | KQ max|err| | eta0 mean|err| | eta0 max|err| | J0 相对偏差(max) | KT ≤3% 点数 |
|---|---|---|---|---|---|---|---|---|---|
| repo_candidate_lifting_line | REJECT | 59.08% | 238.62% | 66.57% | 286.98% | 31.04% | 100.00% | 33.95% | 7/200 |
| repo_bem_strip | REJECT | 22.27% | 76.66% | 17.77% | 52.98% | 12.15% | 34.19% | 14.47% | 26/200 |
| served_platform | REJECT | 18.52% | 44.16% | 43.36% | 65.62% | 61.14% | 138.17% | nan% | 17/200 |

全带（含接近 `J0` 的工况）：

| kernel | KT max|err| | KQ max|err| | KT ≤15% 点数 |
|---|---|---|---|
| repo_candidate_lifting_line | 238.6% | 287.0% | 41/240 |
| repo_bem_strip | 95.7% | 69.9% | 102/240 |
| served_platform | 125.3% | 65.6% | 72/240 |

线上面：线上端点可应答：HTTP 200，回执片段 `{"KT":0.264,"KQ":0.0264,"eta0":0.6366,"method":"Wageningen B系列回归公式"}`。

参考核自洽检查（B4-70, P/D=1.0, Ae/Ao=0.70, Z=4）：`J0 = 1.0618`，
`KT(J=0.4) = 0.3142`，
峰值效率 `η0 ≈ 0.6946`——与公开 B 系列图谱同量级（见 ⑥）。

## ④ 结果图

![fig_open_water_curves.png](../../artifacts/verification/propeller_bseries/fig_open_water_curves.png)

B4-70（P/D=1.0, Ae/Ao=0.70）：`KT`、`10KQ`、`η0` 三条曲线，黑实线=B 系列参考。
可见两个仓内核都在高进速段提前失推（曲线过早点落到 0），且效率整体偏高。

![fig_kernel_errors.png](../../artifacts/verification/propeller_bseries/fig_kernel_errors.png)

主带最大相对误差（对数纵轴），绿虚线=3% 门、红虚线=15% 门。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **B 系列是拟合式不是 EFD**：它本身在 Re=2×10⁶ 拟合，具 ±2–3% 拟合散布；把它当
   "真值"判 3% 门偏严，但对"是否可用"（>15%）判决稳健。
2. **Reynolds 数不可比**：仓内核用固定阻力律（`C_D = 0.012 + k C_L²`，无 Re 标度），
   参考式固定在 Re=2×10⁶；因此差异里含一部分真实 Re 效应，不能全部记为实现误差。
3. **相对误差在 `J→J0` 必然发散**：故设主带 0.70 J0；全带（至 0.85 J0）数字只作趋势参考。
4. **候选核 `kp_curves` 的失效模式是结构性的**（`Ae/Ao` 一改就整体平移、`J` 偏大处
   直接截零），不是系数微调能救的——这类核不该被提升为 REAL。
5. **线上端点的能力边界**：`/api/v1/propeller-design/wageningen-b` 需要 `J`/`P_D` 字段的
   请求体（首种写法 422），给对字段后返回 200，故服务面本轮**已能逐点打分**；但其回执
   仍带 `KQ ≡ KT/10` 的旧代理指纹（见 ③ 表中 `served_platform` 行），且 serving 仓不在
   本工作树白名单，修复须另派单。
6. 系数表来源为公开实现而非原文 PDF：已用"双源一致 + 物理锚点"控制风险，但仍属
   二手转写（若后续拿到原论文表，应做一次逐项 diff）。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| KT（BEM 条带核，主带均值） | O&O 1975 回归 | 22.27% | 否 |
| 10KQ（BEM 条带核，主带均值） | O&O 1975 回归 | 17.77% | 否 |
| eta0（BEM 条带核，主带均值） | O&O 1975 回归 | 12.15% | 否 |
| KT（升力线候选核，主带均值） | O&O 1975 回归 | 59.08% | 否 |
| 10KQ（升力线候选核，主带均值） | O&O 1975 回归 | 66.57% | 否 |
| 零推力进速比 J0（BEM 条带核） | O&O 1975 回归 | 8.15% | 否 |
| 参考核物理锚点 KT(J=0)（P/D=0.6→1.4） | 公开 B 系列图谱同量级 0.25→0.62 | 见 tests/test_wageningen_b_series.py | 是（≤1%） |

**结论（直说）**：没有任何一个仓内敞水核在 3% 门内；两个核都在 15% 门内/外波动，
BEM 条带核为 `REJECT`、升力线候选核为
`REJECT`。因此 `propeller.open_water` 的
**数值证据等级维持不达 REAL**——但性质变了：从"无基准、WITHHELD"变成"有基准、
已量化、差距明确，且差在哪（失推点偏早 + 效率偏高）已定位"。
