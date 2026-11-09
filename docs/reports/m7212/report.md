# IN63-N08 多物理场耦合接口契约标准化 — 验收报告 (m7212)

**节点**: IN63-N08（成熟化 / 跨求解器接口契约）  
**仓**: TensorPlatform  
**分支**: agent/m7212  
**产物**: `backend/services/coupling_contracts/`(机制) + `contracts/coupling/`(数据) + `backend/tests/test_coupling_contracts.py`(42 用例) + `tools/coupling_contracts/`(验收跑批)  
**机器门**: a=PASS, b=PASS, c=PASS, d=PASS, e=PASS, positive_control=PASS

## 1. 案例介绍（目标 / 参数 / 边界）

多求解器耦合（流体-结构-振动-航行性能）中，真正让结果失效的往往不是离散格式，而是**交换面上的契约不一致**：单位制混用（kN 当 N）、坐标系手性/朝向不同（z-up 与 y-up）、量纲不匹配（力接到压力端口上）、以及契约版本静默演进（下游未跟上单位改名）。本节点把这三类隐性错误变成机器可算的判据，落成一个**域中立**的标准化机制。

| 参数 | 取值 |
|---|---|
| 登记单位 | 113 个（SI / 英制 / 混合 / 航海四类制式） |
| 坐标系 | 4 个（blade_local, earth, hull_fixed, tank_local） |
| 学科契约 | 9 个（九大学科各一） |
| 耦合边 | 10 条（跨求解器数据流） |
| 契约整包违规 | 0 条 |
| 判据容差 | 1e-12（相对 / 绝对，按判据定义） |

**为什么这里没有场量云图**：本节点交付的是接口契约机制（单位/坐标系/量纲/版本），不是某个几何的流场/结构场求解，故无云图；结果图给的是判据曲线（往返误差、正交性、π 群基与拦截矩阵），这与节点物理目标一一对应，未以装饰图替代证据。

## 2. 怎么做的（方法 / 工具链）

### 2.1 分层设计（机制与领域数据分离）

| 模块 | 职责 | 关键机制 |
|---|---|---|
| `dimension.py` | 量纲代数 | SI 七基本量整数指数；`Fraction` 精确高斯消元求秩/零空间（无 eps 阈值，跨 ISA 可复现） |
| `units.py` | 单位制 | 显式白名单（拒绝前缀猜测）；递归下降解析 `kg/(m*s^2)`；仿射温标（degC/degF）；换算记录 SI 枢轴 |
| `frames.py` | 坐标系 | 刚体变换 (R,t)；`det=+1` 与 `RᵀR=I` 双判据（容差 1e-12）；帧图 BFS 最短路 + 逐跳追踪 |
| `buckingham.py` | 量纲一致性 | 无量纲群基 `k = n − rank`；声明群逐条无量纲判定、冗余/超定检测 |
| `contract.py` | 契约 | 端口（单位/坐标系/张量阶/required/declared_dim）+ 边表 + 整包校验，违规带字段定位 |
| `versioning.py` | 版本 | 变更分类（7 类破坏性 / 4 类兼容）；破坏性变更逐条要求迁移覆盖；迁移**可执行**并回校新契约 |
| `quantity.py` | 交换载荷 | 值+单位+坐标系封装；批量归一化到 SI / 目标制式；全零载荷显式降级 |

### 2.2 领域数据如何注入（零硬编码）

九大学科的端口字段全部以 JSON 数据落在 `contracts/coupling/v1/`、`v2/`；坐标系在 `frames.v1.json`；耦合边在 `coupling-map.v{1,2}.json`；迁移路径在 `migrations.v1_to_v2.json`。平台代码（`backend/services/coupling_contracts/`）与测试**不含任何学科词条**，测试夹具使用合成契约（`k.a`/`k.b`/`injected.*`），符合「域数据经配置/工件注入」的平台铁律。

### 2.3 工具链与可复现命令

```bash
# 单元/判据测试（42 用例）
cd backend && python -m pytest tests/test_coupling_contracts.py -q
# 机器验收跑批 + 图（写 artifacts/coupling_contracts/）
python3 tools/coupling_contracts/acceptance.py --figures
# 报告生成（本文件，数字程序化注入）
python3 tools/coupling_contracts/report.py
```

## 3. 计算结果（判据数字）

### 判据 (a) 单位制转换 — 100 组随机物理量往返

| 指标 | 数值 | 判据 | 结论 |
|---|---|---|---|
| 工况数 | 100 | =100 | PASS |
| 最坏相对误差 | 2.548e-16 | ≤ 1.000e-12 | PASS（余量 3924.7 倍） |
| 超差工况 | 0 | =0 | PASS |
| 登记表自检问题 | 0 | =0 | PASS |

换算覆盖 31 组 SI↔英制/航海/混合单位对（力/力矩/压力/长度/速度/质量/密度/功率/能量/粘度/频率/加速度），取值跨 12 个数量级；误差恒在 1 ulp 量级，与量级无关（图 1 右）。

### 判据 (b) 坐标系变换 — 正交性 / det / 逆变换

| 指标 | 数值 | 判据 | 结论 |
|---|---|---|---|
| 旋转工况 | 24（roll/pitch/yaw ∈ [-180,180]°） | ≥24 | PASS |
| max\|RᵀR−I\| | 2.220e-16 | ≤ 1e-12 | PASS |
| max\|det(R)−1\| | 2.220e-16 | ≤ 1e-12 | PASS |
| 点往返最大绝对误差 | 2.842e-14 m | ≤ 1e-12 | PASS |
| 帧图路径示例 | hull_fixed→blade_local | 最短路 | PASS |

点往返经过 40 组随机帧对（含随机位姿探针帧，仍有 2.84e-14 级误差），且矢量变换不含平移分量。

### 判据 (c) 量纲一致性校验 — 注入 10 组不匹配

| # | 字段 | 单位 | 错声明量纲 | 单位推导量纲 | 拦截 code | 报告字段 |
|---|---|---|---|---|---|---|
| 1 | `force_x` | kN | `M·L^2·T^-2` | `M·L·T^-2` | DIM_DECLARED_MISMATCH | `force_x` |
| 2 | `time_step` | s | `L^-1` | `T` | DIM_DECLARED_MISMATCH | `time_step` |
| 3 | `pressure` | kPa | `M·L^-3` | `M·L^-1·T^-2` | DIM_DECLARED_MISMATCH | `pressure` |
| 4 | `velocity` | m/s | `L` | `L·T^-1` | DIM_DECLARED_MISMATCH | `velocity` |
| 5 | `density` | kg/m^3 | `L^-3` | `M·L^-3` | DIM_DECLARED_MISMATCH | `density` |
| 6 | `moment` | kN*m | `M·L·T^-2` | `M·L^2·T^-2` | DIM_DECLARED_MISMATCH | `moment` |
| 7 | `power` | kW | `M·L^2·T^-2` | `M·L^2·T^-3` | DIM_DECLARED_MISMATCH | `power` |
| 8 | `viscosity` | Pa*s | `M·L^-1` | `M·L^-1·T^-1` | DIM_DECLARED_MISMATCH | `viscosity` |
| 9 | `area` | m^2 | `L^3` | `L^2` | DIM_DECLARED_MISMATCH | `area` |
| 10 | `mass` | kg | `kg` | `M` | BAD_DECLARED_DIM | `mass` |

拦截率 **10/10**，且每条都定位到具体字段名。正向对照：同批字段用正确量纲声明时 10/10 全部放行（排除「一律拒绝」的假绿）。

### 判据 (d) 契约版本管理 — v1→v2

| 契约 | 版本 | 破坏性变更 | 无迁移时 | 有迁移时 | 迁移执行 |
|---|---|---|---|---|---|
| `solver.resistance` | v1→v2 | UNIT_CHANGED(`water_density`), REMOVED_PORT(`wake_speed`), ADDED_REQUIRED(`advance_speed_out`), ADDED_REQUIRED(`gravity`) | FAIL（未覆盖 4 条） | PASS | PASS |
| `solver.seakeeping` | v1→v2 | UNIT_CHANGED(`heading_angle`), REMOVED_PORT(`added_resistance`), ADDED_REQUIRED(`added_resistance_force`) | FAIL（未覆盖 3 条） | PASS | PASS |
| `solver.propulsion` | v1→v2 | 无（纯扩展） | PASS | PASS | PASS |

迁移执行的具体数值变化（证明「有迁移」不是纸面声明）：

* `solver.resistance`: applied = rename:wake_speed->advance_speed_out, unit_rescale:water_density:kg/m^3->t/m^3, default_fill:gravity；迁移后字段 = advance_speed_in, advance_speed_out, friction_coefficient, gravity, kinematic_viscosity, resistance_force, water_density, waterline_length, wetted_area
* `solver.seakeeping`: applied = unit_rescale:heading_angle:deg->rad, rename:added_resistance->added_resistance_force；迁移后字段 = added_resistance_force, heading_angle, heave_rao, pitch_angle, roll_restoring_moment, wave_amplitude, wave_length, wave_period
* `solver.propulsion`: applied = （本契约无破坏性变更，无需迁移）；迁移后字段 = advance_ratio, diameter, inflow_speed, rotation_rate, shaft_power, thrust, torque, water_density

结论：**所有破坏性变更都被显式迁移覆盖**（True），**全部迁移可在样本载荷上真实跑通**（True），且无迁移声明时明确判 FAIL（逐条列出未覆盖字段）。

### 判据 (e) 负控 — 全零物理量输入

| 载荷 | SI 值 | degenerate | 单位方向矢量 | 物理意义 |
|---|---|---|---|---|
| #1 | 0.0 | True | None | undefined: all-zero magnitude, ratios/angles not computable |
| #2 | [0.0, 0.0, 0.0] | True | None | undefined: all-zero magnitude, ratios/angles not computable |
| #3 | 0.0 | False | None | defined |

崩溃：**False**（3 条全零载荷全部接受并显式标注无物理意义）；无量纲 0（比值型）不标 degenerate —— 0 的比值合法，0 的力没有方向。

### 正向对照 — 九大学科契约整包

契约 9 个 + 耦合边 10 条，违规 **0** 条（解析期 0 条）。

| 契约 | 端口数 n | 量纲矩阵秩 r | 独立 π 群 n−r | 声明 π 群 | 群基（示例前两条） |
|---|---|---|---|---|---|
| `solver.green_navigation` | 7 | 2 | 5 | 1 | fuel_mass_flow^-1·fuel_lhv^-1·shaft_power; fuel_lhv^-1/2·service_speed |
| `solver.ice_navigation` | 7 | 3 | 4 | 2 | ice_compressive_strength^-1/2·ice_density^1/2·ship_speed; ice_thickness^-2·ice_compressive_strength^-1·ice_resistance_force |
| `solver.manoeuvring` | 8 | 3 | 5 | 1 | rudder_angle; forward_speed^-2·water_density^-1·length_overall^-2·hydrodynamic_force |
| `solver.propulsion` | 8 | 3 | 5 | 2 | inflow_speed^-1·rotation_rate·diameter; inflow_speed^-4·rotation_rate^2·water_density^-1·thrust |
| `solver.resistance` | 8 | 3 | 5 | 5 | advance_speed_in·kinematic_viscosity^-1·waterline_length; water_density^-1·kinematic_viscosity^-2·resistance_force |
| `solver.seakeeping` | 8 | 3 | 5 | 1 | wave_amplitude^-1·wave_length; heading_angle |
| `solver.stability` | 7 | 3 | 4 | 1 | centre_of_gravity_height^-1·metacentric_height; centre_of_gravity_height^-1·beam |
| `solver.structure` | 8 | 3 | 5 | 3 | youngs_modulus^-1·external_pressure; plate_thickness^-1·span |
| `solver.vibration_noise` | 8 | 3 | 5 | 0 | damping_ratio; excitation_force^-1·structural_mass·acceleration_level |

## 4. 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 | `artifacts/coupling_contracts/fig_unit_roundtrip.png` | 判据(a)：100 组往返相对误差（log）与 1e-12 判据线；右图为误差-量级散点（跨 12 数量级无趋势） |
| 图 2 | `artifacts/coupling_contracts/fig_frame_validation.png` | 判据(b)：24 组欧拉角的 max\|RᵀR−I\| 与 \|det R−1\|；右图为 40 组随机点逆变换回原坐标误差 |
| 图 3 | `artifacts/coupling_contracts/fig_pi_and_intercepts.png` | 上：九大学科契约的端口数/秩/独立 π 群/声明群；下：10 组注入量纲不匹配全部拦截 |

## 5. 注意点（踩坑 / 局限 / 适用范围）

1. **仿射单位不可复合**：degC/degF 带偏置，参与 `degC/s` 这类复合会静默产生错误系数，已硬判负；这也是 `convert` 对纯比例单位走一次乘除对、对仿射单位走偏置路径的原因。
2. **`rpm` 的物理语义**：登记为 `rpm = 2π/60 rad/s`（转速→角速度）。若下游把 rpm 当 1/min 用，换算系数会有 2π 差异——契约里 `unit` 字段是唯一事实源，禁止下游自行再乘系数。
3. **π 群幂次**：零空间基向量本身可以是有理指数（如 `fuel_lhv^-1/2`，见 π 群快照），因为量纲矩阵的零空间在有理数域上闭合且用 `Fraction` 精确求解；但**契约里声明**的 π 群只接受整数幂（`v²/(gL)` 而不是 `v/√(gL)`），避免浮点指数进入门禁判定。
4. **版本兼容是结构性判定**：改名靠 `rename` 迁移显式声明，未声明时表现为「删一个+加一个必填」两条破坏性变更（保守但不会漏）；语义漂移（同单位同量纲、含义变了）只能靠 `semantic` 字段变更 + 人工评审，本机制无法自动判语义。
5. **与旧模块的关系**：`backend/services/ice_integration/dimensions.py` 是学科侧的旧单位表（145 行、无英制/复合表达式/π 定理）。本机制是通用层，未改动旧模块以避免影响在跑的冰区流水线；后续应由冰区模块改为调用本包（迁移项已记录，未在本单执行）。
6. **尚未接入运行时 API**：本单交付库 + 数据 + 测试 + 验收跑批，未新增 HTTP 端点；求解器调用方当前通过 `import services.coupling_contracts` 使用。API/网关层接入是后续节点。
7. **契约与代码双源风险**：JSON Schema 与 dataclass 各自描述字段；测试里的 schema 一致性用例（jsonschema 可用时）是这两者的连接件，若 CI 环境缺 jsonschema 会 skip —— 已按被测压力写明，不把 skip 当通过。

## 6. 误差判定（逐项对基准）

本节点的判据是**数学恒等式**而非实验数据，故基准是解析真值（往返可逆性、正交性、det=+1、零空间精确性），没有实验/文献误差可对标；下表如实列出每一项的基准与达成度：

| 判据 | 基准（解析真值） | 实测 | 相对判据容差 | 是否 <3% |
|---|---|---|---|---|
| (a) 单位往返 | 相对误差 = 0 | 2.548e-16 | 占判据容差 0.0255% | 是 |
| (b) 正交性 | max\|RᵀR−I\| = 0 | 2.220e-16 | 占判据容差 0.0222% | 是 |
| (b) 行列式 | \|det R−1\| = 0 | 2.220e-16 | 占判据容差 0.0222% | 是 |
| (b) 逆变换 | 绝对误差 = 0 | 2.842e-14 m | 占判据容差 2.84% | 是 |
| (c) 量纲拦截 | 拦截 10/10 | 10/10 | 漏检 0 | 是（误报/漏报均为 0） |
| (d) 版本兼容 | 破坏性变更 100% 有迁移 | 有迁移 PASS / 无迁移 FAIL | 覆盖 7 条破坏性变更 | 是 |
| (e) 负控 | 不崩溃且显式声明 | 崩溃 False | — | 是 |

**局限声明**：以上全部为机制层正确性证据（单元 + 集成 + 数据一致性），**不构成**任何学科求解精度证据；没有真实耦合算例（如流体-结构双向耦合）的端到端误差数据，跨求解器实装接入后的数值影响需后续节点用实际算例验证。

## 7. 审计链

* 机制代码: `backend/services/coupling_contracts/{dimension,units,frames,buckingham,contract,versioning,quantity}.py`
* 领域数据: `contracts/coupling/{frames.v1.json,coupling-map.v1.json,coupling-map.v2.json,migrations.v1_to_v2.json,v1/*.json,v2/*.json}`
* JSON Schema: `contracts/coupling/schema/*.schema.json`
* 测试: `backend/tests/test_coupling_contracts.py`（42 用例，覆盖 a–e + 正向对照 + schema 一致性）
* 验收跑批: `tools/coupling_contracts/acceptance.py` → `artifacts/coupling_contracts/acceptance_m7212.json`
* 图: `tools/coupling_contracts/figures.py` → `artifacts/coupling_contracts/fig_*.png`
* 报告生成: `tools/coupling_contracts/report.py`（数字由 JSON 程序化注入，零手抄）

