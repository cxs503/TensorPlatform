# 全科学计算模块盘点 + 参考数据溯源（audit2）

> 任务：整改战役·阶段一/B（mission 2087）。**只读代码**，本单不改任何生产代码。
> 产物：本文件 + `artifacts/audit2/audit2_scientific_inventory.json`、
> `audit2_reference_provenance.json`、`audit2_solver_interfaces.json`、`audit2_lineage_graph.json`、
> `audit2_tests_green.xml` / `audit2_tests_mixed.xml`、`fig1..fig3*.png`。
> 生成器：`artifacts/audit2/build_inventory.py`（扫描）→ `artifacts/audit2/gen_doc.py`（本文件）。
> **本页所有数字由脚本从仓内工件注入，无手抄**；定性判断逐条标 `【已证实】/【推测】/【未验证】`。

## 0. 结论速览

| 项 | 结果 | 态 |
|---|---|---|
| 盘点域 / 功能条目 | 10 域 / **33 条** | 【已证实】 |
| 有对应测试的条目 | 26 / 33（无测试：PRP-05, MAN-02, ICE-02, ICE-03, ICE-04, OPT-02, UQ-01） | 【已证实】 |
| 带 HTTP 路由的条目族 | 16，其中**未挂载**（线上 404）9 个 | 【已证实】 |
| 带路由但不在 main.py 挂载链上的科学 API 模块 | **11 个 / 51 条路由** | 【已证实】 |
| 与 TensorLBM/FEM/DEM/SPH/DG/BEM/FVM 的**可执行**耦合 | **0 处**（AST 判真实 import + 运行期调用） | 【已证实】 |
| 参考库正典 `backend/data/benchmark_reference.json`（v3.0） | 12 行：DERIVED 4 / WITHHELD 8 | 【已证实】 |
| 可作 15% 判决的阻力参考行 | **仅 SUBOFF 4 行**（DTMB-5415 ×4、KVLCC2 ×4 全 WITHHELD） | 【已证实】 |
| KCS 参考数据 | 仓内**只有几何与 Holtrop 计算值**，无阻力/试验算例 | 【已证实】 |
| 参考阻力性质 | 全部为 **由 Ct 经 R1 恒等式反推（DERIVED）**，不是独立登记的试验总阻力 | 【已证实】 |
| SUBOFF 参考 Ct 序列 | 精确等差（线性拟合残差 8.7e-19 = 机器零） | 【已证实】 |
| 证据等级分布（18 条纯 REAL / 3 条含 MOCK / 12 条含 WITHHELD / 1 条含 NOT_SUPPORTED） | 见 §2 逐条 | 【已证实】 |
| 单测现状 | 核心绿集 245 项全绿；几何/基准混合集 126 项中 5 红 | 【已证实】 |

**一句话**：算法层比预期扎实（18/33 条纯 REAL，多为闭式解/解析锚点且有独立交叉实现），
但**四处结构性风险**必须写进台账：
① 参考数据只有 4 行可用，且这 4 行是「由 Ct 反推」+ 精确等差，**不能当独立实验基准**；
② 9 个功能族的端点**根本没挂到 main.py**（含旗舰能力 `benchmark_validate`），
   精化实现（Holtrop 参考引擎、MMG 方法库版、振动/积冰/极地族）都躺在未接线的一侧；
③ 与 Tensor* 求解器**零可执行耦合**，"求解器覆盖"实为本仓 Python 重写；
④ 两个**端点层**仍是 MOCK 级简化（阻力剩余项经验律 RES-01、MMG 船体力恒零 MAN-01），却顶着高阶方法名（§2.1 D-01/D-05）。

## 1. 范围、方法与证据纪律

### 1.1 范围
`backend/services/**`（含 `polar/`、`vv/`、`digital_twin/`、`ice_integration/`、`ice_shm/`、
`ship_performance/`、`extreme_ice_load/`）、`backend/api/**`、`backend/methods/**`、
`backend/physics/**`、`optimizer/**`；参考数据 `backend/data/**`、`backend/migrations/**`。

### 1.2 方法（可复现，无手抄）
1. **静态解析**而非 import：`from main import app` 在本环境直接失败
   （`ModuleNotFoundError: psycopg2`，见 `backend/database/config.py:46` 建 engine），
   故路由可达性用正则解析 `backend/main.py` → `backend/api/domain/*.py` 的 include 链，
   `artifacts/audit2/build_inventory.py::mount_chain`。**这一步证明的是"接线"，不是"物理正确"**。
2. **参考值复算**：对正典 12 行逐行独立复算 R1（Rt=Ct·½ρV²S）、R3（Fr=V/√(gL)）、
   ITTC-1957 Cf 与摩擦下限，不复用 `benchmark_canon.py` 的代码路径（避免同源自证）。
3. **序列诊断**：对每型 Ct(V) 做最小二乘线性拟合，报最大残差；
   残差 < 1e-12 即"精确等差"，是合成长序列（而非独立测点散布）的机器可判特征。
4. **计数**：模块/路由/命中文件均由脚本枚举，清单见各 JSON。

### 1.3 与既有台账的关系（避免两套口径打架）
仓内已有 `docs/audit/ship_api_evidence_census.md` + `ship_api_evidence_ledger.json`
（`tools/audit/ship_api_evidence_census.py` 生成，口径：1427 条 API 路由中
44 条船舶域 / 21 项策展能力，
等级分布 ANCHORED=30, WITHHELD=14, MOCK_RISK=0）。
本单**不重算那份台账**，只补它没有的三件事：① 逐功能（不是逐路由）的算法/适用范围/缺口；
② 参考数据的逐条溯源；③ 求解器接口与数据血缘。两者冲突时以本单的**代码事实**为准并注明。

### 1.4 三态标注
- **【已证实】**：附 `文件:行号`、命令输出或脚本可复算的数值。
- **【推测】**：有代码/数据线索但缺一环（如原文献不在仓内）——写明缺什么。
- **【未验证】**：本单未做或无法做（离线无原文、无实测数据），不得当作结论引用。

> 纪律提醒：HTTP 200 / 流程跑通 / 单测通过 **都不等于物理正确**。本单 §6 专门列出
> "单测红"的地方，§4 专门讨论"参考值由公式生成再自证"。

## 2. 全科学计算模块盘点（10 域 / 33 条）

表 A 给入口/算法/等级/适用范围；表 B 给调用者/测试/缺口。ID 与 JSON 的 `items[].id` 一一对应。

### 阻力 resistance

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| RES-01 | `POST /calculations/resistance` | `backend/api/calculations.py` (879) | ITTC-1957 摩擦线 Cf=0.075/(log10Re-2)^2（Re<5e5 退 Blasius）+ 经验剩余 Cr=0.002(1+2Cb)Fn^2.5 + S=pi*B*L*(0.5+0.25Cb)（可被实测 S 覆盖） | REAL(摩擦线) / MOCK(剩余阻力经验律，无出版溯源) | 轴对称体与常规单体；Cr 律对非常规船型无依据（模块 docstring 自述） |
| RES-02 | （库模块，无路由） | `backend/services/resistance_holtrop.py` (295) | RT=(1+k)RF+RW+RB+RTR+RA，c7/iE/c1/c2/c5/m1/m4/lambda 全链按公开公式集 | REAL(公式实现)；单点绝对值仅作独立方法交叉参考 | 排水型单体 0.55<Cb<0.85 且 Fn<=0.4；潜艇/双体/滑行艇应判 NOT_SUPPORTED |
| RES-03 | （库模块，无路由） | `backend/api/holtrop1984.py` (467) | 与 RES-02 同方法集的单一实现；optimizer 经 canonical_bridge 委托（禁止复制系数链） | REAL(公式实现) | 缺 rhow(Fn>0.4) 修正、LR~=L，均显式 flag |
| RES-04 | （库模块，无路由） | `backend/services/resistance_core.py` (102) | 阻力分解 + Fn 适用域机器判词 | REAL(结构) / 数值继承 RES-01 | 超域返回 not_applicable |
| RES-05 | `POST /calculations/batch_sweep` **⚠未挂载** | `backend/api/domain/ship_batch.py` (172) | 参数网格 × 阻力核，逐点透出 method/evidence | REAL(编排) / 单点继承 RES-01 | 继承被调核 |
| RES-06 | `POST /calculations/benchmark_validate`<br>`GET /calculations/benchmark_cases`<br>`GET /calculations/benchmark_audit`<br>`GET /calculations/benchmark_cases_audit` **⚠未挂载** | `backend/api/domain/ship_benchmark.py` (436) | 算例逐点对比正典 Ct；数据集闸门只放行 CONSISTENT 行参与 15% 判决 | WITHHELD（12 行中 8 行不可复核，见正文 §4） | 仅 SUBOFF 4 行可判决；DTMB-5415/KVLCC2 整列 WITHHELD |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| RES-01 | `backend/api/calculations.py`, `backend/api/domain/ship_benchmark.py`, `backend/services/benchmark_dataset_audit.py`, `scripts/audit_benchmark_chain_m1388.py`, `tests/test_ship_benchmark_integrity.py` | `backend/tests/test_calculations.py`, `backend/tests/feat/test_resistance_verification.py` | — |
| RES-02 | （无仓内调用者） | `backend/tests/feat/test_resistance_verification.py` | — |
| RES-03 | `optimizer/interfaces/resistance_eval.py` | `tests/test_calc_verification_m1604.py`, `optimizer/audit/optimizer_objective_audit.py` | — |
| RES-04 | `backend/services/benchmark_audit.py` | `backend/tests/feat/test_resistance_verification.py` | — |
| RES-05 | （无仓内调用者） | `backend/tests/feat/test_ship_batch.py` | 路由未挂载（线上 404） |
| RES-06 | `backend/api/domain/ship_benchmark.py`, `backend/services/benchmark_dataset_audit.py`, `scripts/audit_benchmark_integrity.py` | `backend/tests/feat/test_ship_benchmark.py`, `backend/tests/feat/test_benchmark_integrity_audit.py`, `backend/tests/feat/test_benchmark_chain_m1388.py`, `tests/test_benchmark_canon.py` | 路由未挂载（线上 404） |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| RES-01 | 请求体（L/B/T/Cb/ρ/ν；可选实测湿面积 S） | backend/data/benchmark_reference.json SUBOFF 4 行（Rt 由 Ct 反推） |
| RES-02 | 调用方给船型参数（iE/lcb/Cstern/ABT 缺省时内部估计并落 assumptions） | Holtrop & Mennen 1982 / Holtrop 1984 公式集（原文不在仓） |
| RES-03 | 内置 4 型几何常量 + 调用方航速 | KVLCC2/KCS/AFRAMAX/DTMB-5415 公布主尺度（未回原表） |
| RES-04 | 调用方参数 | 无外部数据；判词自带物理带（Fn/Cb 域） |
| RES-05 | 请求网格（参数扫描范围） | 继承 RES-01（无独立参考） |
| RES-06 | backend/data/benchmark_reference.json + benchmark_reference_sources_v2.json | Roddy 1990 / ITTC V&V 2010 / SIMMAN 2008（citation 字符串；原文不在仓） |

### 静水力/稳性 hydrostatics & stability

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| HYD-01 | （库模块，无路由） | `backend/services/ship_hydrostatics.py` (469) | 站位×水线半宽积分 ∇/Cb/Aw/KB/BM；锚点=方箱与 Wigley 抛物型 | REAL（解析锚点） | 需真实型值表；锚点只覆盖方箱/Wigley |
| HYD-02 | （库模块，无路由） | `backend/services/box_hydrostatics.py` (258) | ∇=LBT、KB=T/2、BM=B^2/12T、GZ=(GM+0.5*BM*tan^2 phi)sin phi；出水后多边形裁切解析积分 | REAL（闭式，无离散误差） | 方箱；非方箱退化为裁切多边形 |
| HYD-03 | （库模块，无路由） | `backend/api/domain/hull_geometry.py` (500) | R4 排水量闭合、轴对称 Cb 有效值、Holtrop/Denny-Mumford 湿面积、形体系数带 | REAL（恒等式判据） | 平台『几何必须先验 Cb/L/B』的机器实现 |
| HYD-04 | `POST /calculations/stability` | `backend/api/calculations.py` (879) | 墙侧 GZ 恒等式 + 出水后 gz_basis=linear_fallback；90° 奇点 HTTP 400 | REAL(方箱锚点) / WITHHELD(实船经验式) | 甲板边缘浸没以下；GM 由调用方给定 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| HYD-01 | `backend/api/calculations.py`, `generate_wigley_figures.py`, `tests/test_wigley_hydrostatics_validation.py`, `tools/verify_ship_hydrostatics.py` | `tests/test_calc_verification_m1604.py` | — |
| HYD-02 | `tools/verification/stability_engine_audit_m1597.py` | `backend/tests/test_box_hydrostatics.py` | — |
| HYD-03 | （无仓内调用者） | `backend/tests/feat/test_hull_geometry_validation.py` | — |
| HYD-04 | `backend/api/calculations.py`, `scripts/verify_stability_analytic_benchmark.py` | `backend/tests/test_calculations.py`, `tests/test_analytic_stability_bench.py` | — |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| HYD-01 | 调用方型值表（stations × waterlines 半宽） | 方箱 / Wigley 闭式解析值 |
| HYD-02 | 无（纯解析） | 矩形箱体静水力与墙侧 GZ 恒等式 |
| HYD-03 | 请求几何；可选 offset 表 | R4 排水量恒等式、ITTC-57 摩擦线、Holtrop/Denny-Mumford 湿面积回归 |
| HYD-04 | 请求（GM/尺度/吃水） | 方箱墙侧恒等式；甲板浸没判据 |

### 推进 propulsion

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| PRP-01 | `POST /calculations/propulsion/self_propulsion` | `backend/api/calculations.py` (879) | 伴流 w / 推力减额 t / etaD 闭式链；attained EEDI MEPC.308(73) | REAL（闭式/根解 + 恒等式自校） | 输入驱动，无船型回归 |
| PRP-02 | `POST /calculations/propulsion/actuator_disk` | `backend/api/calculations.py` (879) | 动量理论 T=2 rho A v_i (v+v_i) | REAL | 稳态均匀来流 |
| PRP-03 | （库模块，无路由） | `backend/services/propeller_open_water.py` (386) | 叶元条带积分 + Prandtl 梢/毂损失 + 动量一致诱导速度，根解诱导因子 | REAL（替换旧硬编码 KT=0.5-0.3J 代理） | 无粘叶元，剖面升阻系数外给 |
| PRP-04 | （库模块，无路由） | `backend/services/wageningen_b_series.py` (287) | Oosterveld & van Oossanen (1975) KT(39 项)/KQ(47 项)，系数表双独立实现程序化互校 | REAL（文献回归 + 双源逐项一致） | B 系列几何族；非 B 系列桨存在模型形差 |
| PRP-05 | （库模块，无路由） | `backend/api/domain/propeller_open_water.py` (396) | kp_curves / check_invariants（J0 存在、KQ 随 J 单调降） | REAL(不变式)；绝对精度取决于核 | 库模块无 APIRouter → 无 HTTP 入口 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| PRP-01 | `backend/api/calculations.py`, `tools/verify/eedi_verification.py`, `tools/verify/propeller_open_water/audit_bem_vs_bseries.py` | `backend/tests/test_calculations.py` | — |
| PRP-02 | `backend/api/calculations.py`, `tools/verify/eedi_verification.py`, `tools/verify/propeller_open_water/audit_bem_vs_bseries.py` | `backend/tests/test_calculations.py` | — |
| PRP-03 | `tests/test_batch_engine_provenance.py`, `tests/test_m1662_propeller_wiring.py`, `tests/test_propeller_open_water.py`, `tests/verify/test_bem_vs_bseries_audit.py`, `tests/verify/test_bem_vs_literature.py` | `tests/test_bem_propeller_vs_oosterveld.py` | — |
| PRP-04 | （无仓内调用者） | `tests/test_bem_propeller_vs_oosterveld.py` | — |
| PRP-05 | （无仓内调用者） | **无对应测试** | 无机器判据测试 |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| PRP-01 | 请求（w/t/ηR/SFOC/装机功率/载重） | MEPC.308(73) EEDI 公式（原文不在仓） |
| PRP-02 | 请求（盘面积/来流/推力） | 致动盘动量理论闭式解 |
| PRP-03 | 剖面升阻系数由调用方给（无内置翼型库） | backend/data/propulsion_benchmark_reference.json 的 KP505 试验点 |
| PRP-04 | 内嵌 KT(39 项)/KQ(47 项) 系数表（双独立实现互校） | Oosterveld & van Oossanen (1975) 公布回归表 |
| PRP-05 | 调用方参数 | 同上（B 系列回归） |

### 耐波 seakeeping

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| SEA-01 | `POST /calculations/seakeeping_rao`<br>`POST /calculations/seakeeping_spectrum`<br>`POST /calculations/seakeeping_response` | `backend/api/domain/ship_seakeeping.py` (178) | 单自由度 (M+A)eta''+Beta'+C*eta=F；C33=rho g Aw / C55=rho g nabla GM_L / C44=m g GM_T；谱矩→有义值 | REAL(闭式锚点) / WITHHELD(绝对 RAO，辐射系数为参数化) | 线性频域小幅值；无剖面水动力库 |
| SEA-02 | （库模块，无路由） | `backend/services/ship_performance/seakeeping/broken_ice.py` (732) | 碎冰覆盖附加阻尼/周期修正 | WITHHELD（docs/audits/seakeeping_verification_m1433.md） | 经验修正，无实测基准 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| SEA-01 | `backend/api/domain/ship_seakeeping.py`, `tests/verify/test_seakeeping_crosspath_m1679.py`, `tools/audit_seakeeping_endpoints.py`, `tools/verify/seakeeping/crosspath_adjudication.py` | `backend/tests/feat/test_ship_seakeeping.py`, `backend/tests/feat/test_seakeeping_reference.py` | — |
| SEA-02 | `generate_figures_broken_ice.py`, `tests_broken_ice/test_broken_ice_seakeeping.py` | `backend/tests/test_broken_ice_seakeeping.py` | — |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| SEA-01 | 请求（船型水线/∇/GM/波浪谱） | 闭式锚点（色散、遭遇频率、长波极限、共振峰、谱矩）+ 谱定义；无试验 RAO |
| SEA-02 | 内嵌碎冰修正系数 | 无（WITHHELD） |

### 操纵 manoeuvring

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| MAN-01 | `POST /calculations/maneuvering/mmg` | `backend/api/calculations.py` (879) | 端点内联简化 MMG：舵力 F_N=0.5*rho*A_R*f_alpha*u_R^2*sin(delta)，u_R=0.8U、t_R=0.35、a_H=0.3 为硬编码典型值；船体力 X_H=-q*0.01、**Y_H=0、N_H=0**；x_R=-Hr/L | REAL(舵力闭式 & 无量纲化) / MOCK(船体横向力与力矩恒零、阻力常数化) | 只算单时刻水动力，不积分轨迹；船体力恒零使 Y/N 只由舵贡献 → 不能用于回转/Z 形试验预测（精化版在 backend/methods/manoeuvring/model.py，未接线） |
| MAN-02 | `GET /calculations/manoeuvring/limits`<br>`POST /calculations/manoeuvring` **⚠未挂载** | `backend/api/domain/manoeuvring.py` (144) | IMO 限值判词 + 风/流环境耦合轨迹积分 | REAL(判词/积分) / WITHHELD(力模型) | 模块未挂载 → 线上 404（见正文 §3） |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| MAN-01 | `backend/api/domain/misc.py`, `backend/api/holtrop1984.py`, `backend/services/ship_resistance.py` | `backend/tests/feat/test_manoeuvring.py`, `backend/tests/feat/test_manoeuvring_numerics.py`, `backend/tests/feat/test_manoeuvring_anchors.py` | — |
| MAN-02 | （无仓内调用者） | **无对应测试** | 无机器判据测试；路由未挂载（线上 404） |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| MAN-01 | 请求（L/B/T/Cb/Hr/Ar/舵角/航速） | 无：t_R=0.35 / a_H=0.3 / u_R=0.8U 为硬编码典型值且未标出处（方法库版用 Fujii 横流舵力） |
| MAN-02 | 请求 + 风/流环境场 | IMO 操纵性限值（决议原文不在仓） |

### 冰区 ice

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| ICE-01 | `POST /ice-uq/resistance`<br>`POST /ice-uq/uq`<br>`POST /ice-uq/uq/all`<br>`GET /ice-uq/ships` | `backend/api/domain/ice_uq.py` (220) | 校准 Lindqvist(1989) + PCE(Smolyak 稀疏网格) + Sobol 指数 | WITHHELD（就地拟合→in-sample 恒真；LOO 出样 -23.2%…+16.5%） | 三船单点标定，不可外推 |
| ICE-02 | （库模块，无路由） | `backend/services/ice_canon.py` (194) | 破碎+弯曲+下沉三分解，每个系数非派生即显式标为约定 | REAL(派生锚点) | 参考模型，非生产路径 |
| ICE-03 | `GET /polar/capabilities`<br>`GET /polar/ships`<br>`POST /polar/ships/validate`<br>`GET /polar/ice-field/scenario`<br>`POST /polar/ice-field/summary`<br>`POST /polar/resistance/level-ice`<br>`POST /polar/resistance/level-ice/grid`<br>`POST /polar/resistance/channel`<br>`POST /polar/convoy/spacing`<br>`POST /polar/convoy/curve`<br>`POST /polar/cii`<br>`POST /polar/route/optimize`<br>`POST /polar/voyage/optimize`<br>`GET /polar/acceptance`<br>`GET /polar/acceptance/route`<br>`GET /polar/acceptance/channel` **⚠未挂载** | `backend/api/domain/polar.py` (509) | 开阔水 Holtrop + 冰阻力三分解；网格冰场 track 积分；航线动态规划 | REAL(实现验证) / WITHHELD(绝对冰阻力) | 闭式 vs 网格场验证实现误差，非全尺度验证 |
| ICE-04 | `GET /ship/icing/capability`<br>`POST /ship/icing/heat_balance`<br>`POST /ship/icing/spray`<br>`POST /ship/icing/accumulate`<br>`POST /ship/icing/benchmark` **⚠未挂载** | `backend/api/domain/ship_icing.py` (245) | 热平衡 + 喷雾通量 + 累积 | WITHHELD（未挂载；无独立实测基准） | 见正文 §3 |
| ICE-05 | （库模块，无路由） | `backend/services/extreme_ice_load/extreme_ice_load.py` (756) | Hermite 三次 Euler-Bernoulli 梁 + Tikhonov(L 曲线) 反演 + 安全系数带 | REAL(结构/反演) / WITHHELD(冰载荷输入) | 梁模型（冰带纵桁），非全船有限元 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| ICE-01 | `backend/api/domain/ice_uq.py`, `docs/ice_uq/generate_figures.py`, `tests/test_m1720_ice_resistance_validation_audit.py`, `tests/test_pce_ice_resistance_uq.py`, `tools/ice_resistance_crosscheck.py` | `backend/tests/polar` | — |
| ICE-02 | `tests/test_ice_resistance_crosscheck.py` | **无对应测试** | 无机器判据测试 |
| ICE-03 | `backend/tools/audit/m1687_ice_three_way.py`, `tools/ice_resistance_crosscheck.py`, `backend/api/domain/polar.py`, `tools/polar/run_acceptance.py` | **无对应测试** | 无机器判据测试；路由未挂载（线上 404） |
| ICE-04 | （无仓内调用者） | **无对应测试** | 无机器判据测试；路由未挂载（线上 404） |
| ICE-05 | `scripts/generate_extreme_ice_figures.py`, `tests/test_extreme_ice_load.py` | `tests/test_extreme_ice_load.py` | — |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| ICE-01 | 三船试验均值（内嵌常量）+ 用户分布假设 | MV Arctic / Polarstern / Terry Fox 单点试验均值 |
| ICE-02 | 无（由几何/强度参数派生） | TP-ICE-ANCHOR 派生式；文献歧义系数显式标注 |
| ICE-03 | 冰场/环境输入 + backend/services/polar/ships.py 船型库 | 闭式 vs 网格场自校；无实测冰阻力 |
| ICE-04 | 环境参数（温度/风速/液态水含量） | 无独立实测 |
| ICE-05 | 应变测量流（接口）+ 屈服强度默认常量 | 梁单元解析解；无现场试验 |

### 规范环保 regulatory & green

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| GRN-01 | `GET /calculations/green/fuel_factors`<br>`GET /calculations/green/evidence`<br>`POST /calculations/green/eedi`<br>`POST /calculations/green/eexi`<br>`POST /calculations/green/cii`<br>`POST /calculations/green/voyage_co2` **⚠未挂载** | `backend/api/domain/ship_green.py` (211) | L1 C_F=w_C*44.009/12.011；L2 attained=(P*SFOC*C_F)/(f_i*f_c*Capacity*V_ref) | L1/L2 REAL · L3 WITHHELD · L4 CII 等级 NOT_SUPPORTED | MARPOL Annex VI；L3/L4 不静默默认 |
| GRN-02 | `GET /polar/capabilities`<br>`GET /polar/ships`<br>`POST /polar/ships/validate`<br>`GET /polar/ice-field/scenario`<br>`POST /polar/ice-field/summary`<br>`POST /polar/resistance/level-ice`<br>`POST /polar/resistance/level-ice/grid`<br>`POST /polar/resistance/channel`<br>`POST /polar/convoy/spacing`<br>`POST /polar/convoy/curve`<br>`POST /polar/cii`<br>`POST /polar/route/optimize`<br>`POST /polar/voyage/optimize`<br>`GET /polar/acceptance`<br>`GET /polar/acceptance/route`<br>`GET /polar/acceptance/channel` **⚠未挂载** | `backend/api/domain/polar.py` (509) | MEPC.353(78) G1 + MEPC.354(78) G4 + MEPC.364(79)；质量路/能量路双路径互校 | REAL(规则锚值)；无船队数据 | 与 GRN-01 双实现 → 漂移风险（见正文 §7 G-04） |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| GRN-01 | `tests/test_green_navigation.py` | `backend/tests/polar` | 路由未挂载（线上 404） |
| GRN-02 | （无仓内调用者） | `backend/tests/polar` | 路由未挂载（线上 404） |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| GRN-01 | 船型登记表 + 燃料含碳分数 | MARPOL Annex VI / MEPC.364(73) C_F 表（转录值，原文不在仓） |
| GRN-02 | 船队参数输入 | MEPC.353(78) G1 / 354(78) G4 / 364(79)（转录值） |

### 振动噪声 vibration & noise

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| VIB-01 | `POST /calculations/vibration/beam_natural`<br>`POST /calculations/vibration/plate_natural`<br>`POST /calculations/vibration/excitation`<br>`POST /calculations/vibration/isolation`<br>`POST /calculations/noise/panel_tl`<br>`POST /calculations/noise/levels` **⚠未挂载** | `backend/api/domain/ship_vibration.py` (713) | Euler-Bernoulli 梁闭式 + Hermite 一致质量 FEM 自校；Kirchhoff 板 Navier 解 + Rayleigh 商独立复算 | REAL（解析基准 <3%） | 线性小变形、均匀截面/等厚板；未挂载 → 404 |
| VIB-02 | `POST /calculations/vibration/beam_natural`<br>`POST /calculations/vibration/plate_natural`<br>`POST /calculations/vibration/excitation`<br>`POST /calculations/vibration/isolation`<br>`POST /calculations/noise/panel_tl`<br>`POST /calculations/noise/levels` **⚠未挂载** | `backend/api/domain/ship_vibration.py` (713) | 单自由度隔振传递率、质量定律隔声、IEC 61672 A 计权合成 | REAL(闭式) / WITHHELD(实船阻尼与源级) | 同上；端点未挂载 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| VIB-01 | （无仓内调用者） | `backend/tests/feat/test_ship_vibration.py` | 路由未挂载（线上 404） |
| VIB-02 | （无仓内调用者） | `backend/tests/feat/test_ship_vibration.py` | 路由未挂载（线上 404） |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| VIB-01 | 无（闭式 + FEM 自校） | Euler-Bernoulli 梁 / Kirchhoff 板解析特征值 |
| VIB-02 | 请求（隔振器刚度/阻尼、板厚、声源级） | IEC 61672 A 计权定义、质量定律 |

### 优化 optimization

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| OPT-01 | （库模块，无路由） | `optimizer/core/optimization_loop.py` (299) | CMA-ES + 阻力目标委托正典 holtrop1984；8 个艏部参数经真实水动力通道 | REAL(优化器/委托) / 目标精度继承 RES-03 | KCS 艏部参数化，无实测闭环 |
| OPT-02 | （库模块，无路由） | `optimizer/ice_class/objectives.py` (226) | 冰级规范约束 + Lindqvist 命名启发式目标 | MOCK-RISK（ice_canon 判其为启发式） | 设计筛选；不得引用为物理精度 |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| OPT-01 | （无仓内调用者） | `optimizer/audit/optimizer_objective_audit.py` | — |
| OPT-02 | （无仓内调用者） | **无对应测试** | 无机器判据测试 |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| OPT-01 | KCS 艏部参数化 + 正典 Holtrop 内核 | 继承 RES-03；无实测阻力闭环 |
| OPT-02 | 冰级规范约束参数 | 无（启发式目标，ice_canon 已判不可作验证） |

### UQ 与数值验证

| ID | 入口端点 | 实现（行数） | 核心算法 | 证据等级 | 适用范围声明 |
|---|---|---|---|---|---|
| UQ-01 | （库模块，无路由） | `backend/services/ice_resistance_uq.py` (1013) | Smolyak 稀疏网格 PCE + 系数法 Sobol | REAL(数学) / WITHHELD(物理模型) | 输入分布为假设，无实测协方差 |
| UQ-02 | （库模块，无路由） | `backend/services/vv/gci.py` (433) | Roache/Celik 三网格 GCI + 观测阶 + 外推值 | REAL（确定性算术，无物理可调参数） | 需三套网格解；平台科学端点尚未接入 |
| UQ-03 | （库模块，无路由） | `backend/services/digital_twin/pce.py` (165) | PCE 代理 + 在线状态估计 | REAL(数学) / 代理精度依赖训练数据 | 见 tests/test_digital_twin_surrogate_accuracy.py |

表 B：调用者 / 测试 / 缺口

| ID | 调用者（import 本模块的文件） | 测试位置 | 证据缺口 |
|---|---|---|---|
| UQ-01 | `backend/api/domain/ice_uq.py`, `docs/ice_uq/generate_figures.py`, `tests/test_m1720_ice_resistance_validation_audit.py`, `tests/test_pce_ice_resistance_uq.py`, `tools/ice_resistance_crosscheck.py` | **无对应测试** | 无机器判据测试 |
| UQ-02 | `backend/services/vv/__init__.py`, `tools/verification/vv_gci_certify.py` | `tests/test_gci_anchors.py`, `tests/test_gci_core.py`, `tests/test_gci_verification.py` | — |
| UQ-03 | （无仓内调用者） | `tests/test_digital_twin_surrogate_accuracy.py` | — |

表 C：数据来源 / 参考数据

| ID | 数据来源 | 参考数据（判据来源） |
|---|---|---|
| UQ-01 | 输入分布假设（内嵌） | 无实测协方差 |
| UQ-02 | 三套网格解（调用方给） | Roache 1994 / Celik 2008 程序 + 解析解锚点 |
| UQ-03 | 上游训练数据（数据集表） | 见 tests/test_digital_twin_surrogate_accuracy.py |

### 2.1 文档/命名与实现不一致（本单新发现，均已证实）

| # | 位置 | 声称 | 实际 | 影响 |
|---|---|---|---|---|
| D-01 | `backend/api/calculations.py:327` 端点 docstring | "Uses a simplified Holtrop-Mennen formulation calibrated for the SUBOFF axisymmetric body" | 实为 `ITTC-57 摩擦线 + Cr=0.002(1+2Cb)Fn^2.5`（`:358`, `:373`），**不是 Holtrop**；同文件模块头（`:15-17`）已自述 Cr "without a published provenance" | 外部读者会把 SUBOFF 误差归因于 Holtrop 不适用，得出错误整改方向（`docs/reports/m1388` 记录过这种误判） |
| D-02 | `backend/api/holtrop1984.py:464` | 字段 `s_published=4.6494`（"published"） | 等于 Holtrop 湿面积回归计算值（偏差 0.0004%，§4.5） | 参考几何被当成文献值使用 |
| D-03 | `backend/data/benchmark_reference_sources_v2.json` `description` | "Benchmark reference dataset ... Sources: Roddy 1990 ..." | 该版本 8/12 行数值互相矛盾，v3 正典已把 8 行判 WITHHELD | 若有人直接读 v2 当基准，会把不可复核的行当实验值 |
| D-04 | `backend/services/ice_resistance_uq.py:12-21` docstring | 自述 calibration_factor 为就地拟合、in-sample 误差恒为零 | 代码与自述一致（`calibration_factor` 由 R_ref/R_raw 反解，`:61`） | 自述诚实，但**端点默认输出**仍是"校准后吻合"，需在响应里带 out-of-sample 指标 |

| D-05 | `backend/api/calculations.py:763` MMG 端点 docstring | "基于日本 MMG 标准方法（Yasukawa & Yoshimura 2015）" | 船体力 `X_H=-q*0.01`、**`Y_H=0`、`N_H=0`**（`:814-816`），舵处流速 `u_R=0.8U`、`t_R=0.35`、`a_H=0.3` 硬编码无出处（`:800,805,806`） | 端点输出被读成"MMG 标准方法结果"；真正的 MMG 条带/横流实现在 `backend/methods/manoeuvring/model.py`，只被未挂载的 `api/domain/manoeuvring.py` 使用 |

| D-06 | `backend/tests/test_ship_domain_routers.py:27-38` | 测试名/文档称 "Router registration ... properly registered" | 只断言 `router.routes`（模块内路由表），从不断言 `app.routes`（应用挂载表） | 对 9 个未挂载端点族**该测试永远不会失败** → 假门禁；本单的可达性由 main.py include 链独立判定（§2 表 A ⚠） |

这套"命名/文档比实现更硬"的模式与 §4.7 的 s_published 同源：**平台的物理代码普遍比它的文案保守**，
风险在文案侧而非算法侧。

## 3. 求解器接口盘点：TensorLBM/FEM/DEM/SPH/DG/BEM/FVM

### 3.1 结论
**没有任何可执行耦合。** 用 AST 判真实 `import`（而非文案里的字符串）+ 运行期 HTTP/subprocess
调用模式扫描，全仓命中数 = **0**。
【已证实】命令：`python3 artifacts/audit2/build_inventory.py` → `audit2_solver_interfaces.json.executable_coupling_count`。

（初版正则扫描曾报 12 处 `from tensorlbm ...`，逐条核查全部是**文档字符串里的示例代码**
（如 `scripts/portability_census.py:43`），已改为 AST 判定，避免假阳性。这条本身也说明：
"仓库名出现在代码里"与"调用该求解器"是两件事。）

### 3.2 求解器名出现的位置（纯字符串/元数据）
| 求解器 | 代码文件命中数 | 主要命中点 | 真实耦合 |
|---|---|---|---|
| TensorLBM | 62 | `scripts/portability_census.py`, `artifacts/audit2/build_inventory.py`, `tools/ops/benchmark/build_backlog.py`, `artifacts/audit2/gen_doc.py` | **无** |
| TensorFEM | 13 | `artifacts/audit2/build_inventory.py`, `tools/agent/gc.py`, `tools/agent/tensor_agent.py`, `backend/api/tensor_missions.py` | **无** |
| TensorDEM | 24 | `tools/ail/probes.py`, `tools/ail/corpus.py`, `artifacts/audit2/build_inventory.py`, `tools/agent/tensor_agent.py` | **无** |
| TensorSPH | 9 | `artifacts/audit2/build_inventory.py`, `eval_set/scripts/run_eval.py`, `backend/api/tensor_missions.py`, `eval_set/scripts/generate_baseline.py` | **无** |
| TensorDG | 7 | `artifacts/audit2/build_inventory.py`, `backend/api/tensor_missions.py`, `operator_log/collect_2026-10-25.m299.py`, `operator_log/verify_2026-10-25.m299.py` | **无** |
| TensorBEM | 22 | `operator_log/collect_2026-10-25.m305.py`, `operator_log/collect_2026-10-25.m312.py`, `operator_log/collect_2026-10-25.m315.py`, `operator_log/collect_2026-10-25.m325.py` | **无** |
| TensorFVM | 14 | `artifacts/audit2/build_inventory.py`, `tests/factory/test_agent_control_plane.py`, `tools/agent/gc.py`, `tools/agent/tensor_agent.py` | **无** |

上游证据：`backend/api/tensor_missions.py:219` 只是把这些仓库名登记为可选
`repo` 清单（任务派发用）；`backend/services/ice_integration/contracts.py:117-123, 203-208`
把 `source_hint` 写成 `TensorLBM sloshing (worktree m1216)` / `TensorDEM:src/tensordem (worktree m1214)`。

### 3.3 "求解器覆盖"实为仓内重写（关键澄清）
| 平台内声明的求解器覆盖 | 声明的来源 | 仓内实际实现 | 性质 |
|---|---|---|---|
| N02 液舱晃荡（势流自由面 + 壁面冲击） | `TensorLBM sloshing (worktree m1216)` | `backend/services/ice_integration/reference_nodes.py（N02 参考实现）` | 本仓 Python 参考实现，不是调用 TensorLBM |
| N05 海水吸入系统冰块堵塞（Beverloo + Ergun） | `TensorDEM:src/tensordem (worktree m1214)` | `backend/services/ice_integration/nodes_downstream.py:573` | 本仓解析式实现 |
| 冰带纵桁 Euler-Bernoulli 梁刚度 | `TensorPhysics / TensorFEM hermite_cubic_euler_bernoulli` | `backend/services/ice_shm/frame.py（docstring: mirrors the beam element formulation）` | 公式镜像实现，不是调用 TensorFEM |

逐条判定：
- N02 液舱晃荡：`backend/services/ice_integration/reference_nodes.py` 的势流自由面 + 冲击压力参考实现，
  与 TensorLBM 的一行代码都没有共享 → 能力表述里的 TensorLBM 只能算**方法出处**，不是**执行依赖**。【已证实】
- N05 堵塞：`nodes_downstream.py:573` 是 Beverloo + Ergun 解析式，TensorDEM 的 DEM 颗粒求解未被调用。【已证实】
- 冰带梁：`backend/services/ice_shm/__init__.py:5-7` 自述 "mirrors the beam element formulation"，
  `ice_shm/frame.py:3` 同样；即公式镜像实现。【已证实】
- 结论：平台目前**不持有**任何外部求解器接口（无 factory/backend 适配层、无 solver registry、
  无 solver URL 配置）。若对外宣称"耦合 TensorLBM/FEM/DEM"，属**能力越界**，应降到 WITHHELD。【已证实】

## 4. 参考数据溯源专项（SUBOFF / KVLCC2 / KCS / DTMB-5415）

### 4.1 仓内每一份落点
（统计已排除本单自身产物 `artifacts/audit2/**` 与本文，避免自指膨胀。）
| 船型 | 仓内命中文件数 | 主要落点（按命中次数） |
|---|---|---|
| SUBOFF | 258 | `tools/verif/verify_resistance_m1775.py`(67), `tests/fixtures/capability_ledger/reports_corpus.json`(51), `artifacts/timing_baseline.json`(50), `tests/verification_m1604.py`(42), `backend/tests/test_calculations.py`(41), `tests/fixtures/capability_evidence/reports_snapshot.json`(41), `tests/test_resistance_m1775_verification.py`(38), `tests/test_suboff_resistance_validation.py`(38) |
| KVLCC2 | 295 | `tests/fixtures/capability_ledger/reports_corpus.json`(104), `tools/verifier/geometry/audit_geometry.py`(59), `tests/test_holtrop_mennen_verification.py`(58), `backend/tests/test_broken_ice_seakeeping.py`(52), `tests_broken_ice/test_broken_ice_seakeeping.py`(52), `tests/test_seakeeping_rao_verification.py`(41), `tools/gen_anchors_report.py`(32), `docs/verification/ship_benchmark_integrity.md`(30) |
| KCS | 134 | `tools/validation/validate_deployed_stability_formulas.py`(79), `tools/verifier/geometry/audit_geometry.py`(61), `docs/reports/m1456_manoeuvring_evidence_audit.md`(31), `tensor_reports/m597_report.json`(24), `tests/fixtures/capability_ledger/reports_corpus.json`(24), `agent_work/m1576/build_report.py`(22), `tools/gen_anchors_report.py`(21), `docs/verification/GEOMETRY_CAPABILITY_AUDIT.md`(20) |
| DTMB-5415 | 189 | `tests/fixtures/capability_ledger/reports_corpus.json`(104), `docs/reports/m1561_benchmark_canon_adjudication.md`(56), `docs/verification/audit_before.json`(54), `docs/verification/benchmark_audit.json`(46), `artifacts/batch_sweep_demo.json`(42), `docs/verification/ship_benchmark_integrity.md`(38), `backend/data/benchmark_reference.json`(36), `docs/reports/benchmark_reference_integrity_gate.md`(35) |

层级说明（按"能不能当判据"排序）：
1. **正典**：`backend/data/benchmark_reference.json` v3.0（12 行，唯一判决来源，
   规则 R1 Rt=Ct·½ρV²S, R2 流体溯源（模型=淡水/实船=海水）, R3 Fr=V/√(gL), R4 Δ=Cb·L·B·T·ρ, R5 Ct≥Cf_ITTC57, R6 形体带）。【已证实】
2. **冻结裁决输入**：`backend/data/benchmark_reference_sources_v2.json` v2.0
   （v2.0 记录值快照，供 `benchmark_canon.py:57` 确定性复算）。【已证实】
3. **已作废**：`backend/data/benchmark_reference_v1_legacy.json` v1.0（保留 legacy，禁止引用）。【已证实】
4. **螺旋桨锚**：`backend/data/propulsion_benchmark_reference.json`（2 个算例，KP505 试验 + B 系列回归）。【已证实】
5. 其余为派生产物：`docs/verification/*.json`、`artifacts/**/audit*.json`、`docs/reports/m1561/*` 等（审计留痕，不是新数据源）。

### 4.2 12 行逐条判定（原始单位/尺度/流体/湿面积/状态）
| case | 船型 | 尺度 | ρ [kg/m³] / ν [m²/s] | V [m/s] | Fn(复算) | S [m²] | Ct | Rt(正典)[N] | Ct/Cf | 判决 |
|---|---|---|---|---|---|---|---|---|---|---|
| DTMB5415-01 | DTMB-5415 | model | 998.2 / 1.004e-06 | 0.4569 | 0.060994 | **缺** | 0.0045 | **null** | 1.1698 | WITHHELD |
| DTMB5415-02 | DTMB-5415 | model | 998.2 / 1.004e-06 | 0.9139 | 0.122002 | **缺** | 0.0038 | **null** | 1.1271 | WITHHELD |
| DTMB5415-03 | DTMB-5415 | model | 998.2 / 1.004e-06 | 1.8278 | 0.244004 | **缺** | 0.0035 | **null** | 1.1749 | WITHHELD |
| DTMB5415-04 | DTMB-5415 | model | 998.2 / 1.004e-06 | 2.7417 | 0.366005 | **缺** | 0.0038 | **null** | 1.3667 | WITHHELD |
| KVLCC2-01 | KVLCC2 | full | 1025.0 / 1.188e-06 | 7.202 | 0.128542 | **缺** | 0.0028 | **null** | 1.9828 | WITHHELD |
| KVLCC2-02 | KVLCC2 | full | 1025.0 / 1.188e-06 | 7.717 | 0.137733 | **缺** | 0.003 | **null** | 2.142 | WITHHELD |
| KVLCC2-03 | KVLCC2 | full | 1025.0 / 1.188e-06 | 7.974 | 0.14232 | **缺** | 0.0031 | **null** | 2.222 | WITHHELD |
| KVLCC2-04 | KVLCC2 | full | 1025.0 / 1.188e-06 | 8.231 | 0.146907 | **缺** | 0.0032 | **null** | 2.3023 | WITHHELD |
| SUBOFF-01 | SUBOFF | model | 998.2 / 1.004e-06 | 3.0 | 0.458926 | 5.42 | 0.0038 | 92.52 | 1.3253 | DERIVED |
| SUBOFF-02 | SUBOFF | model | 998.2 / 1.004e-06 | 4.0 | 0.611902 | 5.42 | 0.004 | 173.13 | 1.4641 | DERIVED |
| SUBOFF-03 | SUBOFF | model | 998.2 / 1.004e-06 | 5.0 | 0.764877 | 5.42 | 0.0042 | 284.04 | 1.5947 | DERIVED |
| SUBOFF-04 | SUBOFF | model | 998.2 / 1.004e-06 | 6.0 | 0.917852 | 5.42 | 0.0044 | 428.49 | 1.7206 | DERIVED |

### 4.3 参考阻力到底是试验值、数值还是经验估计？
**三者都不是——是"由无量纲 Ct 反推的总阻力"。** 【已证实】
- 正典每行给出 `experimental_ct` 与 `rt_basis`；DERIVED 行的 `experimental_rt_n` 由
  R1 恒等式 `Rt=Ct·½ρV²S` 复算（`backend/services/benchmark_canon.py:110-118`）。闭合误差见下表（≈0，即**恒等式自洽**）。【已证实】
- 换言之：**Rt 不是独立登记的拖曳力**。若 Ct 本身有误，Rt 会忠实地跟着错，
  而"Rt 与 Ct 自洽"永远成立 —— 这正是"用公式生成参考值再自证"的**形式**。
  正典对此是自觉的（`rt_status=DERIVED`、`provenance.rules`、`WITHHELD` 策略），
  但**对外引用时极易被读成"实验总阻力"**，必须在报告里写明"Rt 为 Ct 的换算值"。 【已证实】

| case | v1 归档 Rt [N] | v2 归档 Rt [N] | 正典 Rt [N] | v1/正典 | v2 偏差 | R1 复算 Rt [N] | R1 闭合误差 |
|---|---|---|---|---|---|---|---|
| SUBOFF-01 | 12.5 | 95.0 | 92.52 | 0.135× | 2.681% | 92.515 | -0.0052% |
| SUBOFF-02 | 21.0 | 177.78 | 173.13 | 0.121× | 2.686% | 173.128 | -0.0013% |
| SUBOFF-03 | 31.5 | 291.66 | 284.04 | 0.111× | 2.683% | 284.038 | -0.0008% |
| SUBOFF-04 | 45.0 | 440.0 | 428.49 | 0.105× | 2.686% | 428.491 | 0.0003% |

### 4.4 Ct 序列的合成迹（本单新增证据）
| 船型 | 航速序列 [m/s] | Ct 序列 | 相邻差 | 线性拟合最大残差 | 精确等差？ | 随速单调升？ |
|---|---|---|---|---|---|---|
| SUBOFF | [3.0, 4.0, 5.0, 6.0] | [0.0038, 0.004, 0.0042, 0.0044] | [0.0002, 0.0002, 0.0002] | 8.674e-19 | **是** | 是 |
| DTMB-5415 | [0.4569, 0.9139, 1.8278, 2.7417] | [0.0045, 0.0038, 0.0035, 0.0038] | [-0.0007, -0.0003, 0.0003] | 3.254e-04 | 否 | **否（非单调）** |
| KVLCC2 | [7.202, 7.717, 7.974, 8.231] | [0.0028, 0.003, 0.0031, 0.0032] | [0.0002, 0.0001, 0.0001] | 1.222e-07 | 否 | 是 |

**判定**：
- **SUBOFF**【已证实】：Ct = 0.0038/0.0040/0.0042/0.0044 于 V=3/4/5/6 m/s，
  相邻差恒为 +0.0002，线性拟合最大残差 **8.7e-19（机器零）**。
  这不可能来自独立测点的实验散布 → **该 Ct 列是拟合/合成的，不能作为独立实验真值**（图 2）。
- **反证在同一棵树里**：`backend/tests/feat/test_benchmark_integrity_audit.py:242-249`
  （`test_suboff_ct_falls_with_speed`，docstring 明确"deeply submerged body's Ct must fall with Re"）
  要求 Ct 随速**不升**，而正典 Ct 严格递增 → **该测试在 HEAD 上是红的**（§6）。
  即：仓内自己的物理判据否证了这条参考序列。这比"外部文献无法回读"更硬。【已证实】
- **KVLCC2**【已证实】：Ct 单调升但非精确等差（残差 1.2e-7）；该列整列 WITHHELD，不参与判决。
- **DTMB-5415**【已证实】：Ct 非单调（0.0045→0.0038→0.0035→0.0038），且 Fr 列隐含船长 181 m
  （正典 `froude_basis` 已写明）→ 整列 WITHHELD。
- **KVLCC2 量级复核**【推测】：全尺度 Re≈1.9e9 时 Cf_ITTC57≈0.00141（本页表 4.4 由脚本算），
  而归档 Ct/Cf = 1.98–2.30。常规单体船 (1+k)+Ca 只到 1.1–1.4 量级，故该 Ct 列**偏大约 40%**。
  因缺 SIMMAN 原表，只能标【推测】；但它与 WITHHELD 判决方向一致（该列不可用）。

### 4.5 湿表面积有无真实出处？
| 船型 | 仓内湿面积 [m²] | 仓内声称 | 独立复算 | 溯源判定 |
|---|---|---|---|---|
| SUBOFF | 5.42 | benchmark_reference.json rt_basis 称『Roddy 1990 表列湿面积』 | pi*B*L*(0.5+0.25*Cb) = 4.4179 m²（相对归档值 -18.49%） | 未验证：仓内无 Roddy 1990 原表页/CSV，无法逐位核对 |
| DTMB-5415 | 4.6494 | holtrop1984.py 字段 s_published（注释 Holtrop correlation, Denny-Mumford 4.598） | Holtrop 回归 4.6494 / Denny-Mumford 4.5974；与 s_published 偏差 0.0004% | 已证实：该值等于回归式计算值而非 ITTC-2010 原表 → 字段名 s_published 过度声明 |
| KVLCC2 | 27194.0 | holtrop1984.py s_published（实船 L=320 m） | Holtrop 回归 26490.1894 / Denny-Mumford 26345.1038；与 s_published 偏差 2.6569% | 未验证：无 SIMMAN 2008 原表；正典参考库该列缺湿面积 |
| KCS | 9494.0 | holtrop1984.py 注释称 Tokyo 2015 公布值 | Holtrop 回归 8844.9914 / Denny-Mumford 9040.3926；与 s_published 偏差 7.3376% | 未验证：仓内无 Tokyo 2015 原表；正典参考库无 KCS 算例 |

- **SUBOFF 5.42 m²**【未验证】：正典自称"Roddy 1990 表列湿面积"，但**仓内无该原表页/CSV/扫描件**，
  无法逐位核对。可做的只有交叉一致性：轴对称近似 `pi*B*L*(0.5+0.25Cb)` 给
  4.4179 m²
  （-18.49%），
  与 `backend/services/ship_resistance.py` 文档里"该式对回转体偏低约 18%"的自述一致
  → 归档值比近似式大，**方向上自洽**，但**不等于已溯源**。
- **DTMB-5415 4.6494 m²**【已证实为过度声明】：`backend/api/holtrop1984.py:464` 字段名
  `s_published`，但数值与 Holtrop 湿面积回归计算值**逐位相同**（偏差
  0.0004%），
  注释也自认"Holtrop correlation"。即它是**回归计算值**，不是 ITTC-2010 公布值 → 字段名误导。
- **KVLCC2 27194 m² / KCS 9494 m²**【未验证】：无 SIMMAN 2008 / Tokyo 2015 原表；
  与 Holtrop 回归差 2.6569% /
  7.3376%，量级合理但无法判定来源。
- **正典参考库本身**：DTMB-5415 / KVLCC2 行的 `wetted_surface_m2` **缺失** → 这正是它们被判
  WITHHELD 的直接原因（`rt_basis` 反解出的表观 S 落在物理带外，见 §4.2）。【已证实】

### 4.6 KCS 专项
- 仓内 **没有 KCS 阻力参考算例**（正典 12 行无 KCS）。【已证实：`audit2_reference_provenance.json` 的 case_rows 无 KCS】
- 仅有几何：`backend/api/holtrop1984.py:435-446`（Lpp=230, B=32.2, T=10.8, Cb=0.6505, S=9494）与
  `optimizer/interfaces/kcs_bow_param.py`（艏部参数化）。【已证实】
- 因此 `optimizer` 的"KCS 船型优化"只能做**相对改进**，不能声明绝对阻力精度（目标函数绝对值继承 Holtrop）。【已证实】

### 4.7 "未经说明的公式生成参考值再自证"审计结论
| 疑点 | 判定 | 依据 |
|---|---|---|
| Rt 由 Ct 反推 | **存在**（形式性自证） | `benchmark_canon.py:110-118`；闭合误差≈0 是恒等式必然 |
| SUBOFF Ct 精确等差 | **存在**（合成迹） | §4.4，残差 8.7e-19 |
| DTMB-5415 `s_published` 实为回归值 | **存在**（命名过度声明） | `holtrop1984.py:464` + 回归复算偏差 0.0004% |
| 用参考库调参后宣布"通过 15%" | **不成立**，但需警惕 | `test_benchmark_integrity_audit.py:257-269` 断言 SUBOFF-01..03 在 15% 内、04 越限；这是"模型对 1 条合成基准"的拟合，不能外推 |
| 基准闸门是否自证 | **不自证，反而是最硬的一环** | `benchmark_canon.py:19-26` 六条恒等式 + WITHHELD 策略把 8/12 行判为不可用 |
| 阻力端点是否自证 | **部分** | 端点的 Cr 与基准 Ct 的同源性未被独立物理论文约束（RES-01 自述 Cr 无出版溯源） |

## 5. 数据-模型-实验-运行-报告 引用链

### 5.1 实际存在的表与字段
- ORM（SQLAlchemy）：`backend/models/core.py` — `Dataset`(:125)、`Model`(:171)、`ModelVersion`(:206)、
  `TrainingJob`(:235)、`PipelineRun`(:687)。【已证实】
- 裸 SQL（无 ORM）：`tensorphysics_runs`（`backend/migrations/999_tensorphysics_run_registry.sql:2`）、
  `tensor_reports`、`tensor_dev_missions`、`tensor_executions` 等（DDL 见 `audit2_lineage_graph.json.raw_sql_nodes`）。【已证实】

### 5.2 引用链（实测边）
| 起点 | 终点 | 关系 | 证据 |
|---|---|---|---|
| datasets | projects | **FK** | models/core.py Dataset.project_id -> projects.id |
| model_versions | models | **FK** | models/core.py ModelVersion.model_id |
| tensorphysics_runs | datasets | **NONE** | 999_tensorphysics_run_registry.sql 无 REFERENCES datasets |
| tensorphysics_runs | models | **NONE** | 同上：仅 manifest JSONB 自由文本 |
| tensor_reports | tensor_dev_missions | **SOFT** | tensor_reports.mission_id 无外键约束（CREATE TABLE 内无 REFERENCES） |
| tensor_reports | tensorphysics_runs | **NONE** | ReportCreate 字段无 run_id（api/tensor_reports.py） |
| tensorphysics_runs | artifact_store | **URI** | manifest.outputs[].uri = s3://bucket/key + checksum_sha256 |

```
 datasets ──FK──> projects
 models <──FK── model_versions
                     ▲
                     │  (无外键)
 training_jobs ──────┘
 tensor_dev_missions ──(mission_id, 无外键)──> tensor_reports
          ▲                                        ▲
          │ (无外键)                                │ (无 run_id 字段)
 tensor_executions                           tensorphysics_runs ──manifest.outputs[].uri(s3://…+sha256)──> 对象存储
```

### 5.3 结论
- **运行 → 数据/模型：无机器可查引用。** `tensorphysics_runs` 只有 `run_id + manifest(JSONB) + manifest_sha256`，
  DDL 中 `foreign_keys` 为空（见 JSON），数据集/模型只能以自由文本写在 manifest 里。【已证实】
- **报告 → 运行：无字段。** `tensor_reports` 的 `ReportCreate`（`backend/api/tensor_reports.py:42-49`）
  只有 `scenario_key / mission_id / stage / tags / markdown / figures`，**没有 run_id / dataset_id**。【已证实】
- **科学计算端点完全不落库**：`/calculations/*` 全族是无状态 HTTP，响应即走，
  不写 run/dataset/report → **当前"实验-运行"链在物理上不存在**，只有任务-报告链存在。【已证实】
- 因此本单把该关系图标为 **WITHHELD（链条不成立）**，而不是"松散实现"。

## 6. 单测与验证记录（证明的不是物理正确）

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=backend pytest <7 个核心科学测试文件> tests/test_benchmark_canon.py tests/test_gci_core.py --junitxml=artifacts/audit2/audit2_tests_green.xml` | 245 项，失败 0，错误 0 |
| `PYTHONPATH=backend pytest <操纵/基准/几何/polar 混合集> --junitxml=artifacts/audit2/audit2_tests_mixed.xml` | 126 项，失败 **5**，错误 0 |

混合集的红项（HEAD 上真实失败）：
- `tests.feat.test_benchmark_integrity_audit.TestDatasetIntegrityAudit::test_suboff_ct_falls_with_speed`
- `tests.feat.test_benchmark_integrity_audit.TestConsistencyBetweenPaths::test_endpoint_and_direct_physics_agree`
- `tests.feat.test_hull_geometry_validation::test_api_resistance_uses_geom_validated_wetted_surface`
- `tests.feat.test_hull_geometry_validation::test_api_resistance_rejects_geometry_inconsistent_cb`
- `tests.feat.test_hull_geometry_validation::test_api_stability_reports_validity_envelope`

逐条归因：
1. `test_suboff_ct_falls_with_speed`：**参考数据违反仓内物理判据**（§4.4）。这是本单最重要的红项——
   它说明"基准通过"与"物理正确"是两件事。【已证实】
2. `test_endpoint_and_direct_physics_agree`：`error_pct` 报 34.58，由返回字段复算 34.568，
   差 0.0118 > 容差 0.01 → 舍入/契约细节，不是物理错误。【已证实】
3. `test_hull_geometry_validation.py` 3 项：测试期望 `wetted_surface_model` / `hull_form` /
   `heel_limit_deg` 等字段（`test:168-207`），HEAD 的 `ResistanceRequest`/`StabilityResponse`
   已无这些字段 → **测试与实现对同一 API 的两代契约**（m1400 期能力未落地）。【已证实】

> 因此本单**不把任何"单测通过"当作物理正确证据**；也不会因为混合集红而改动生产代码（本阶段只读）。

## 7. 证据缺口清单（阶段二输入）

| # | 缺口 | 严重度 | 依据 | 阶段二建议动作 |
|---|---|---|---|---|
| G-01 | 参考库只有 SUBOFF 4 行可用，且为 Ct 反推 + 精确等差 | **A** | §4.3/§4.4 | 回 DTRC-90/012 原表取**拖曳力**与**测点 Ct**（至少 8 个 Fr），落 CSV + sha256；补 3 组实验散布检验（不允许等差） |
| G-02 | DTMB-5415 / KVLCC2 整列 WITHHELD（缺湿面积、Fr 与航速互斥） | **A** | §4.2 | 回 ITTC-2010 / SIMMAN 2008 试验矩阵取 S、V、Rt；补 `S_provenance` 字段（页码/表号） |
| G-03 | KCS 无参考算例 | B | §4.6 | 采 Tokyo 2015 KCS 阻力试验点入正典，`case_id=KCS-xx`，与几何交叉校验 Cb/L/B |
| G-04 | CII/EEDI 双实现（`green_navigation.py` 与 `polar/cii.py`） | B | §2 GRN-01/GRN-02 | 二选一为正典，另一路改为对其断言；加"同输入同输出"对拍测试 |
| G-05 | 11 个科学 API 模块 / 51 条路由未挂载（含 benchmark_validate） | **A** | §2 表 A ⚠ 标记 + `orphan_science_api_modules` | 要么接线进 `main.py`，要么从能力台账中删除；二者取其一，禁止"文档说有、线上 404" |
| G-06 | 与 Tensor* 求解器零可执行耦合，但能力表述引用其名 | **A** | §3 | 能力卡片加 `solver_dependency: none` 字段；若要真耦合，需建 solver adapter（factory/backend）+ 冒烟测试 |
| G-07 | 运行/报告/数据/模型之间无外键，科学端点不落库 | **A** | §5 | 给 `tensor_reports` 加 `run_id`；科学端点写 run manifest（含输入 hash + 模型版本 + 输出 sha256） |
| G-08 | RES-01 的 Cr 经验律无出版溯源，却被端点为默认模型 | **A** | §2 RES-01 | 端点默认切到 Holtrop 正典（RES-03）并保留 `method` 字段；或把 RES-01 降级为 legacy |
| G-09 | 3 个 `test_hull_geometry_validation` 红项（两代 API 契约） | B | §6 | 决定保留哪代 API，然后**同步**测试或实现（本单不动代码） |
| G-10 | 冰阻力 UQ 的 in-sample 标定 | B | §2 ICE-01 | 换 LOO/留船验证作默认判决口径，`calibration_factor` 不得就地拟合 |
| G-11 | 振动/积冰/操纵指标域有物理实现但无 HTTP 入口 | C | §2 VIB/ICE-04/MAN-02 | 随 G-05 一起决定去处 |
| G-14 | 缺"应用挂载"门禁测试（只测 router.routes） | **A** | §2.1 D-06 | 加一条 `test_app_mounts_promised_endpoints`：从能力台账读承诺端点，断言 `app.routes` 覆盖之；未挂载即红 |
| G-13 | **端点层两处 MOCK**：RES-01 剩余阻力经验律、MAN-01 船体力 `Y_H=N_H=0` | **A** | §2.1 D-01/D-05 | RES-01 默认切 Holtrop 正典；MAN-01 改调 `methods/manoeuvring` 方法库版（两者都已存在，缺的是接线）→ 与 G-05 合并施工 |
| G-12 | GCI（UQ-02）与平台科学端点未连通 | C | §2 UQ-02 | 对唯一有离散化的域（冰场 track 积分）接 GCI，产出网格收敛表 |

## 8. 复现命令

```bash
# 1) 清单（写 artifacts/audit2/*.json）
python3 artifacts/audit2/build_inventory.py
# 2) 图（fig1 摩擦下限 / fig2 SUBOFF 等差 / fig3 证据矩阵+未挂载路由）
python3 artifacts/audit2/make_figures.py
# 3) 本文件
python3 artifacts/audit2/gen_doc.py
# 4) 测试证据（junit 落 artifacts/audit2/）
PYTHONPATH=backend python3 -m pytest backend/tests/feat/test_resistance_verification.py \
  backend/tests/test_calculations.py backend/tests/test_box_hydrostatics.py \
  backend/tests/feat/test_ship_vibration.py backend/tests/feat/test_ship_seakeeping.py \
  tests/test_bem_propeller_vs_oosterveld.py tests/test_gci_anchors.py \
  tests/test_benchmark_canon.py tests/test_gci_core.py \
  -p no:cacheprovider --junitxml=artifacts/audit2/audit2_tests_green.xml -q
```

## 9. 局限（诚实声明）

- 【未验证】无法回读任何原始文献（Roddy 1990 / ITTC-2010 / SIMMAN 2008 / Tokyo 2015 / MEPC 决议原文）：
  仓内无 PDF/表页，故"归档值是否等于原文"一律标"未验证"，不做真伪断言。
- 【推测】参考库 12 行的历史成因（谁在何时用何种方式填入）只在 `docs/reports/m1561_*` 有叙事，
  本单未做 git 逐提交考古。
- 【未验证】`/calculations/*` 线上实例与本次工作树**不是同一份代码**：`:8002` 的 `ResistanceRequest`
  要求 `displacement/speed`（报错原文：`Field required: displacement`），而工作树用
  `speed_ms/wetted_surface_m2` → **部署漂移**，本单所有结论只对工作树 HEAD 成立。
- 【已证实】另有一个应用入口 `backend/factory_app.py`（agent edition），它只挂
  `missions/sessions/approvals/models/residents/strategy/soul/campaign_plans` 八个 tensor 路由
  （`factory_app.py:39-41`），**不含任何科学计算路由** → 本单的"未挂载"结论对两个入口都成立。
- 本单只读：`git show --name-only HEAD` 只含 `docs/audits/` 与 `artifacts/audit2/`，无生产代码改动。
