# 操纵性验证欠账核查与修复（m1651）

**范围**：`backend/services/digital_twin/models/manoeuvring.py`（数字孪生 3-DOF MMG，
回转/之字操纵），对标实船试验与 IMO 判据的证据链复核。
**方法**：文献无关锚点（定义恒等式 / 对称性 / 自模不变性 / 数值收敛 / 法规阈值）
+ 独立积分器复算 + 仓内第二套 MMG 实现的 code-to-code 比对。
**结论**：模型 docstring 自认"回转直径未对标实船"，此前唯一测试只断言 2.5–4.5 L
带宽（**80 % 误差也能通过**）。本单补上 **13 条机器可裁锚点**，抓出 **3 处缺陷**，
修复其中 **2 处 + 1 处结构性问题**（重复力模型分叉），并把判据固化为 pytest 门。

---

## 1. 为什么这是"验证欠账"

| 证据链环节 | 修复前状态 |
|---|---|
| 模块自身声明 | `"the absolute turning-circle diameter is *not* validated on a full-scale trial"` |
| 唯一测试 `test_manoeuvring_turning_band` | 仅带宽 `2.0 < D/L < 5.0`、`1.5 < advance/L < 4.0`、`30 < 速度损失% < 85` |
| 下游使用 | `digital_twin/surrogate_registry.py` 用 `turning_diameter_L` 训练 PCE 代理模型（误差会原样传下去） |
| 全仓 benchmark | `backend/data/benchmark_reference.json` = 12 个**阻力**算例，**零操纵算例** |

一条 2.5–4.5 L 的带宽对 320 m 船是 640 m 的不确定区间，不能算判据。

## 2. 案例与工具链

- **船型**：KVLCC2 主尺度（L=320 m，B=58 m，T=20.8 m，Cb=0.8098，ρ=1025 kg/m³，
  L/B=5.52，T/B=0.359，均在 `HullForm` 可容许域内）。
- **工况**：设计航速 15.5 kn = 7.974 m/s；舵角 5/10/20/35°，左右舷各一次；
  10°/10° 与 20°/20° 之字。
- **工具链**：`tools/benchmarks/manoeuvring_anchors.py`（锚点套件，离线纯计算，
  ~19 s，输出 `artifacts/m1651/manoeuvring_anchors.json`）→
  `tools/m1651_manoeuvring_figures.py`（图）→ pytest 门
  `backend/tests/feat/test_manoeuvring_anchors.py`（8 项，~25 s）。

## 3. 锚点结果（`manoeuvring_anchors.json`，程序化产出）

| 编号 | 判据 | 类型 | 修复前 | 修复后 | 数值 |
|---|---|---|---|---|---|
| M-A1 | δ=0 直线航行不得偏航（max\|y\|=0、ψ 摆幅=0） | 恒等 | PASS | PASS | 0 / 0 |
| M-A2 | 左右舷回转轨迹镜像（D、advance 相对差 <1e-9） | 对称 | **FAIL** | PASS | 0（见 §4.1） |
| M-A3 | 附加质量/惯性矩 vs 细长体闭式 my=ρπT²L、Jz=ρπT²L³/12 | 闭式 | FAIL(容差) | PASS | 1.5e-15 / 2.8e-4 |
| M-A4 | 稳态回转运动学恒等式 R=\|U\|/\|r\|，\|U\|=hypot(u,v) | 恒等 | **DEFECT** | PASS | 偏差 2e-16，旧式偏 5.96 % |
| M-A5 | IMO A.751(18)：D≤5L、advance≤4.5L（35°） | 法规 | PASS | PASS | D=4.192L，advance=2.718L |
| M-A6 | 稳态偏航率残差 <1 % | 收敛 | PASS | PASS | 0 |
| M-A7 | RK4 步长收敛（观测阶 >3） | 数值 | PASS | PASS | 1.2e-4 相对差（dt 0.4 vs 0.2） |
| M-A8 | 独立积分器复算 advance/D（<1 %） | 重复实现 | PASS | PASS | 1.4e-12 |
| M-A9 | Froude 缩放（λ=4）下 D/L、advance/L 不变 | 自模（诊断） | PASS | PASS | 0.69 % / 0.08 % |
| M-A10a/b | 20/20 超调 ≥ 10/10；10/10≤10°、20/20≤25° | 定义式/法规 | PASS | PASS | 9.78° / 14.48° |
| M-A10c | 之字积分器步长误差 <1 % | 数值 | PASS | PASS | 5e-7（改用 RK4 后） |
| M-A11 | code-to-code vs `physics.ship_env_maneuvering`（W3-N08） | 跨模型 | — | **WITHHELD** | 30–32 % 差异（见 §5） |

`verdict_counts = {PASS: 12, WITHHELD: 1}`。

![锚点图](../artifacts/m1651/fig_manoeuvring_anchors.png)

## 4. 抓出的缺陷与修复

### 4.1 左舷回转全部指标为 `nan`（M-A2，已修复）

`turning_circle` 内以 `psi >= pi/2`、`psi >= pi` 判定 90°/180°，而左舷回转 ψ 为负值，
故 `advance_m`、`tactical_diameter_m` 恒为 `nan`。这正是"只测右舷"的典型盲区：
带宽测试永远抓不到它，因为 `nan` 也不满足 `2.0 < x < 5.0`——但没有人跑过左舷。
修复：以 `|ψ|` 比较；新增回归断言 `stb[key] == port[key]`（rel 1e-9）。

### 4.2 回转直径用了 u 而非路径速度（M-A4，已修复，量级 6 %）

原式 `D = 2u/r`。稳态回转时船以漂角 β 前进，路径速度 `|U| = hypot(u,v)`，
故旧式把直径**低估 (1−cos β)**：本例 β=19.88° → 低估 **5.96 %**
（3.942L vs 真值 4.192L）。修复：`turning_diameter_m` 改为 `2|U|/|r|`；
旧值保留为 `turning_diameter_surge_m` 以便追溯；新增 `steady_drift_angle_deg`、
`extreme_transverse_diameter_m = 2 max|y|`（试验报告口径的直接几何量）。
下游 PCE 代理模型重训后 `turning_diameter_L` 抬升 6 %，带宽测试仍通过（3.94→4.19，
在 2–5 内），说明该修复未破坏既有契约。

### 4.3 同一个模块里有两套力模型（M-A11 相关，已结构性消除）

修复前 `zigzag()` 是 `turning_circle()` 的手抄副本，且抄漏/抄错三处：

| 项 | `turning_circle` | 旧 `zigzag` | 影响 |
|---|---|---|---|
| 船体升力项 `Y_lift` | 有（`C_Lα=πAR/2`，x_L=−0.05L） | **无** | 丢掉线性小漂角侧向力，正是"横流阻力抓不到"的那一项 |
| 舵 inflow `U_R` | 0.90 u | **0.70 u** | 初始偏航角加速度差 **(0.9/0.7)² = 1.65 倍** |
| 积分器 | RK4 | 前向 Euler（dt=0.1） | 步长误差 0.2 %（可接受但无必要） |

即同一艘船的两个已发布操纵指标不属于同一个水动力模型。修复：抽出共享
`_MMGCore`（力模型唯一实现）+ 共享 `rk4_step`，两者只差舵序与结束条件；
判据 `test_zigzag_shares_the_turning_circle_force_model` 断言 t=0 时两者偏航
角加速度逐位相同，且 `U_R` 改动带来的 35 % 缩水被显式记录。
之字结果随之变化：10/10 超调 9.64°→9.78°，20/20 12.49°→14.48°（仍在 IMO 限内）。

## 5. 关键量 vs 基准：误差逐项与"是否 <3 %"

| 关键量 | 基准（基准性质） | 相对误差 | <3 % ? |
|---|---|---|---|
| 稳态回转半径 R vs `\|U\|/\|r\|` 恒等式 | 运动学恒等式（精确） | 2.2e-16 | ✅ 是 |
| 回转直径（旧式 2u/r）vs 恒等式 | 同上 | **5.96 %** | ❌ 否（已修复为恒等式口径） |
| 附加质量 my vs ρπT²L | 细长体闭式 | 1.5e-15 | ✅ 是 |
| Jz vs ρπT²L³/12 | 闭式（61 段中点法则截断） | 2.8e-4 | ✅ 是 |
| advance/D vs 独立积分器复算 | 重复实现（dt=0.05） | 1.4e-12 | ✅ 是 |
| D/L 在 dt=0.4 vs 0.2 | 步长收敛 | 1.2e-4 | ✅ 是 |
| D/L 在 λ=4 Froude 缩放 | 自模不变性 | 0.69 % | ✅ 是 |
| **D/L vs 仓内第二套 MMG（W3-N08）** | **code-to-code** | **30.1 %**（4.192 vs 3.223） | ❌ **否，未解决** |
| **advance/L vs 同上** | code-to-code | **18.0 %**（2.718 vs 3.315） | ❌ 否，未解决 |
| **稳态漂角 vs 同上** | code-to-code | 19.88° vs 13.26°（+50 %） | ❌ 否，未解决 |
| **稳态航速比 vs 同上** | code-to-code | 0.293 vs 0.326（−10 %） | ❌ 否，未解决 |
| 回转直径 vs **实船试验** | **无可用试验数据** | — | **WITHHELD** |

**显式回答**：自洽类、数值类判据全部 **<3 %**（多为机器精度）；
**绝对操纵性预报误差无法判定**——本工作树内没有任何 KVLCC2 实船/水池操纵试验
数据（`benchmark_reference.json` 零操纵算例），故对"模型 vs 真值"的误差
一律记 **WITHHELD**，不做粉饰。仓内两套 MMG 在同一船型、同一舵角下相差
18–32 %，说明**至少一套**的绝对量级有问题，但两条锚点都无法判定是哪一套。

## 6. 注意点 / 局限

1. **几何是理想化船体**（矩形横剖面 + 抛物线水线），`Cwp = Cb`，真实 KVLCC2
   的 `Cwp ≈ 0.87 > Cb = 0.81`；本审计的所有判据都对这一点不敏感（恒等式/对称性），
   但**绝对**回转直径会受横剖面形状影响，不能当 KVLCC2 的预报值使用。
2. **IMO A.751(18) 阈值是"定义"不是"测量"**：`D≤5L`、`advance≤4.5L`、
   10/10≤10° 只说明"这条船按本模型算满足衡准"，不说明与实船一致。
3. **Froude 缩放判据是诊断量不是恒等量**：模型借用了 Holtrop 阻力（含 Re），
   0.69 % 的偏差来自尺度效应，属预期，不判 FAIL。
4. **M-A9 用 λ=4**：320 m 船无法 1:1 复现，缩尺比只影响诊断解读不影响结论。
5. **未做的事**：没有改 W3-N08 那套模型（其自带验收框架属别的战役面），
   也没有追查 30 % 差异的根源（需要实船数据或 SIMMAN 正式报告，本地无副本）；
   没有引入任何"记忆里的文献表值"——这正是 m1413 定下的纪律。
6. **给下次的线索**：`surrogate_registry.py` 的 PCE 代理模型用 m1651 前的数据
   训练，重训后 `turning_diameter_L` 抬升 6 %；`docs/digital_twin.md` 中若引用
   旧值需同步。另：`backend/api/digital_twin.py` 暴露的操纵端点尚无输入校验
   （非法舵角/负航速路径未测）。

## 7. 复现

```bash
cd /srv/tensor/agent/work/m1651
python3 tools/benchmarks/manoeuvring_anchors.py --t-end 900     # ~19 s → artifacts/m1651/*.json
python3 tools/m1651_manoeuvring_figures.py                      # → artifacts/m1651/fig_*.png
cd backend && python3 -m pytest tests/feat/test_manoeuvring_anchors.py -q   # 8 passed
```