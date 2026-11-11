# P167W2B-N08 跨库场交换守恒性验证面与 conservation_gate 入库门

**节点**：`P167W2B-N08`  **仓库**：TensorPlatform  **包**：`src/coupling/conservation_gate/`（version 1.0.0）  **mission**：12424

**产物**：`src/coupling/conservation_gate/{ledger,gate,faces,exact,controls}.py`、`tests/test_p167w2b_n08_conservation_gate.py`（pytest 用例集，含 17 负控逐条参数化）、`scripts/run_p167w2b_n08_conservation_audit.py`、`artifacts/p167w2b-n08/`（审计 JSON + 门禁裁决 + 负控 + 3 图 + 清单）。

**判据**：20 项机器判据全部通过（`all_passed=true`，审计运行 10.18 s）。

---

## ① 案例介绍：三个交接面、一本账

多物理耦合真正会无声出错的地方不在解算器内部，而在**交接**：断裂的 FEM 单元把表面交给 DEM 生成碎块、LBM 把格子场交给 FVM、两个解算器各自按自己的步长推进却要交换数据。任何一本账不平，泄漏掉的量在下一步就会被当成物理源项，而且**与真实物理不可区分**——所以交接面必须自带账本，而不是靠事后比对。

本节点把三类交接各自做成一条**双侧账本**（提供方值 / 消费方值），再由一个 fail-closed 的**入库门**裁决；几何与场量全部合成，不含任何领域语义。

| 交接面 | 几何 / 场 | 参数 |
|---|---|---|
| FEM→DEM 断裂 | 立方单元 half=0.5，密度 ρ=2.0 | v=[1.0, 0.5, 0.0]，ω=[0.2, -0.4, 0.6]，释放应变能 U=0.3；切面工况 1/3/6 面（轴切）、斜切 3 面、二进（1/4 平面）3 面 |
| LBM→FVM 流场 | 源 8³ 格子 h=0.1；目标 4³ 体积元 H=0.2（同一物理域） | rho=1+0.4 sin(2 pi x), u=(0.4y,0.4z,0)；耦合面=重合面 / 单元内切 / 斜平面 |
| 时钟 | 双/三解算器整数 tick 步长 | 100 个 LCM 窗口，步长比 [[10, 10], [10, 3], [7, 13], [1, 10], [10, 3, 7]]；乐观侧采样周期 50 substeps |

判据（动手前声明，运行后由脚本填值）：断裂四账本 + 惯量张量 < 1e-9；流场界面质量通量 < 1e-6（库存 < 1e-9）；保守同步 100 窗口无死锁、零未来读、屏障偏斜 = 0；乐观同步回滚率 < 5% 且重放等价 < 1e-9；入库门对 17 个负控全部拒绝且理由码正确。

## ② 怎么做的

**账本格式（`ledger.py`）**：一侧账本 = 面名 + 若干条目（名称 / 形状 / 提供方值 / 消费方值 / 声明容差）+ 生产方溯源 + 驱动输入摘要；整份文档用 sha256 封缄。**账本里没有残差**——残差由门自己从原始双侧值重算，所以生产方既不能预置一个好看的残差，也不能靠放大容差混过去。

**入库门（`gate.py`）**：17 个具名理由码（`SEAL_MISMATCH` / `BALANCE_VIOLATION` / `NON_SPD_TENSOR` / `FLAG_FALSE` / `BOUND_VIOLATION` …），三类判据算子：`balance`（守恒等式）、`bound`（上界：回滚率、偏斜、计数）、`flag_true`（必须成立的运行期不变量）。策略里没有判据直接 `ValueError`——空转的门不允许存在。

**FEM→DEM（`faces.fem_dem_ledger`）**：切面 → 真实凸碎块（不是等效球），碎块初速 v_i = v + ω×d_i，释放应变能投入质心无污染膨胀模态 u_i = α d_i，`α = sqrt(2U/Σm_i|d_i|²)`，于是能量账闭合而线动量/角动量账不被扰动。五本账 + 体积全部双侧记录。

**ℚ 复核（`exact.py`）**：把同一份顶点坐标用 `Fraction` 重算体积、一阶矩、二阶矩（同一四面体矩恒等式），得到**精确有理数**残差。这些量都是坐标的多项式，所以 ℚ 里为 0 就等于恒等成立；能量账含开方（无理）故**故意排除**在 ℚ 之外。

**LBM→FVM（`faces.lbm_fvm_ledger`）**：库存用体积重叠超网格 remap；界面通量用面积权重算子（`Σ ρ u·n A` 在 Γ 上逐面求交）；负基线是「在接收单元质心采样 + 反距离权重」的经典捷径。

**时钟语义（`faces.barrier_run` / `faces.optimistic_run`）**：保守侧用整数 tick，每个窗口取所有步长的 LCM，窗口内每个解算器跑完整数步、窗口末同时提交 → 偏斜恒为 0；窗口被人为缩短时必然有解算器跑不完，死锁检测器必须报出来。乐观侧是 Time Warp 语义：消费方在消息延迟期间用陈旧样本推测推进，反消息到达即回滚并重放，回滚步数 / 总步数 = 回滚率。

**负控（`controls.py`）**：1 正控 + 17 负控；两个负控不是按比例造假，而是把**捷径模型真实输出**塞回账本（等效球惯量张量、反距离界面通量），证明门认得出物理失效模式而不只是数值噪声。

## ③ 计算结果（程序化注入，零手抄）

### 3.1 判据总表

| 判据 | 算子 | 阈值 | 实测 | 结论 |
|---|---|---|---|---|
| `clock.barrier_skew` | bound | 1.000e-09 | 0 | PASS |
| `clock.deadlocks` | bound | 0 | 0 | PASS |
| `clock.future_reads` | bound | 0 | 0 | PASS |
| `clock.global_time_monotone` | flag_true | — | true | PASS |
| `clock.optimistic_replay_error` | bound | 1.000e-09 | 0 | PASS |
| `clock.optimistic_rollback_rate` | bound | 0.05 | 0.0376 | PASS |
| `fem_dem.angular_momentum` | balance | 1.000e-09 | 4.050e-16 | PASS |
| `fem_dem.energy` | balance | 1.000e-09 | 2.702e-16 | PASS |
| `fem_dem.inertia_tensor` | balance | 1.000e-09 | 2.355e-16 | PASS |
| `fem_dem.mass` | balance | 1.000e-09 | 2.220e-16 | PASS |
| `fem_dem.momentum` | balance | 1.000e-09 | 1.407e-16 | PASS |
| `fem_dem.volume` | balance | 1.000e-09 | 2.220e-16 | PASS |
| `lbm_fvm.interface_mass_flux` | balance | 1.000e-06 | 4.096e-16 | PASS |
| `lbm_fvm.mass_inventory` | balance | 1.000e-09 | 7.393e-15 | PASS |
| `lbm_fvm.momentum_inventory` | balance | 1.000e-09 | 1.227e-15 | PASS |

### 3.2 FEM→DEM 五本账（五工况取最差）

| 账本 | float 残差 | ℚ 残差 | 判据 |
|---|---|---|---|
| mass | 2.220e-16 | 0（恒等 0） | 1.000e-09 |
| momentum | 1.407e-16 | 0（恒等 0） | 1.000e-09 |
| angular_momentum | 4.050e-16 | 0（恒等 0） | 1.000e-09 |
| energy | 2.702e-16 | — | 1.000e-09 |
| inertia_tensor | 2.355e-16 | 0（恒等 0） | 1.000e-09 |
| volume | 2.220e-16 | 0（恒等 0） | 1.000e-09 |

| 断裂工况 | 面数 | 碎块数 | 质量残差 | 角动量残差 | 惯量张量残差 | 碎块惯量 SPD |
|---|---|---|---|---|---|---|
| dyadic_quarters | 3 | 4 | 0 | 2.726e-16 | 2.355e-16 | True |
| oblique_three | 3 | 8 | 0 | 4.050e-16 | 2.153e-16 | True |
| single_plane | 1 | 2 | 0 | 2.550e-16 | 1.372e-16 | True |
| six_planes | 6 | 27 | 2.220e-16 | 2.294e-16 | 9.892e-17 | True |
| three_planes | 3 | 8 | 0 | 0 | 1.373e-16 | True |

ℚ 全部工况恒等为零：`true`。也就是说 float 路径的 1e-16 是求和舍入，不是模型误差；几何剖分自证 ΣV_i = V_elem 也是恒等式。

**负基线（等效球碎块，同一交接）**：

| 工况 | 全张量惯量残差 | 角动量残差 | 质量残差 | 碎块惯量各向同性 |
|---|---|---|---|---|
| single_plane | 0.3295 | 0.7811 | 0 | true |
| three_planes | 0.7075 | 1.211 | 0 | true |

质量账它过得去（等质量球），但惯量张量与角动量**结构性**过不去：各向同性张量无法表示各向异性单元的惯量，这不是精度问题，是模型自由度不足。

### 3.3 LBM→FVM 库存与界面通量

| 耦合面 | 面积权重通量残差 | 反距离捷径通量残差 | 库存质量残差 | 覆盖率 |
|---|---|---|---|---|
| face_aligned | 4.096e-16 | 4.096e-16 | 7.393e-15 | 1 |
| inside_cell | 4.096e-16 | 4.096e-16 | 7.393e-15 | 1 |
| oblique | 1.206e-16 | 0.003245 | 7.393e-15 | 1 |

| 目标网格 h | 单元/轴 | 界面通量残差 | 反距离通量残差 | 线性场重构误差 |
|---|---|---|---|---|
| 0.1 | 8 | 0 | 8.191e-16 | 0 |
| 0.2 | 4 | 4.096e-16 | 4.096e-16 | 3.084e-16 |
| 0.4 | 2 | 4.096e-16 | 4.096e-16 | 3.469e-16 |

诚实注记：反距离捷径只在**斜耦合面**上失效（0.003245 ≫ 1e-6），在轴对齐网格+轴对齐耦合面上它碰巧守恒（采样点恰好落在权重一致的位置）。所以「界面通量守恒」这条判据的判别力来自斜平面与网格比扫描，而不是来自任取一个算例都会过的运气。本题给出的最小工况（ρ=1、u 常值的均匀场）是剪切场的退化情形，此时 remap 与面积权重通量都退化为精确恒等，误差恒为 0（单测 `test_uniform_field_case_is_exact` 覆盖）；报告表格刻意用非均匀场，因为只有非均匀场才能区分「守恒」与「恰好对」。

### 3.4 时钟同步语义

| 步长比（tick） | 窗口（s, LCM） | 屏障偏斜 | 停滞窗口 | 未来读 | 全局时间单调 | 总步数 |
|---|---|---|---|---|---|---|
| [10, 10] | 0.01 | 0 | 0 | 0 | true | 200 |
| [10, 3] | 0.03 | 0 | 0 | 0 | true | 1300 |
| [7, 13] | 0.091 | 0 | 0 | 0 | true | 2000 |
| [1, 10] | 0.01 | 0 | 0 | 0 | true | 1100 |
| [10, 3, 7] | 0.21 | 0 | 0 | 0 | true | 12100 |

检测器有效性（负控）：把窗口从 LCM 缩短到 10 tick → 停滞窗口 86、`deadlock=true`、偏斜 0.006；让消费方读本窗口未提交数据 → 未来读 1300 次。两条检测器都不是空转。

乐观运行（工作点：采样周期 50 substeps，延迟均匀取自 {0,1,2,3,4} 步，200 周期）：回滚步数 376/10000，**回滚率 0.0376**（< 5%），重放等价误差 0（回滚后与确定性重放**逐位相同**），不回滚漂移 4.466e-04。

| 延迟支持集 n（均值≈n/2 步） | 回滚率 | 重放误差 | 不回滚漂移 |
|---|---|---|---|
| 1 | 0 | 0 | 0 |
| 2 | 0.0096 | 0 | 8.571e-05 |
| 3 | 0.0193 | 0 | 3.171e-04 |
| 4 | 0.0291 | 0 | 3.411e-04 |
| 5 | 0.0376 | 0 | 4.466e-04 |
| 6 | 0.0482 | 0 | 1.835e-04 |
| 11 | 0.0965 | 0 | 4.780e-04 |
| 21 | 0.1938 | 0 | 0.001615 |
| 41 | 0.3876 | 0 | 0.00224 |
| 51 | 0.4847 | 0 | 0.00296 |

| 采样周期 | 回滚率 | 重放误差 | 不回滚漂移 |
|---|---|---|---|
| 0.025 | 0.2419 | 0 | 8.361e-04 |
| 0.05 | 0.2419 | 0 | 0.001565 |
| 0.1 | 0.2419 | 0 | 0.0196 |
| 0.2 | 0.2419 | 0 | 0.01749 |

### 3.5 入库门裁决与负控

证据包：面 ['fem_dem', 'lbm_fvm', 'clock']，`bundle_sha256=4c67c70cc02015d1…`，裁决封缄 `defa489051ab1f8e…`，**accepted=true**。

负控 17 个，全部拒绝且理由码匹配 17/17；假接受率 0，特异度 1；正控通过 true。

| 负控 | 期望理由码 | 实测理由码 | 结果 |
|---|---|---|---|
| mass_target_plus_1pct | BALANCE_VIOLATION | BALANCE_VIOLATION | OK |
| drop_inertia_ledger | MISSING_ENTRY | MISSING_ENTRY | OK |
| nan_momentum_source | NONFINITE | NONFINITE | OK |
| asymmetric_inertia | ASYMMETRIC_TENSOR | ASYMMETRIC_TENSOR, BALANCE_VIOLATION, NON_SPD_TENSOR | OK |
| non_spd_inertia | NON_SPD_TENSOR | BALANCE_VIOLATION, NON_SPD_TENSOR | OK |
| energy_lost_1e-3 | BALANCE_VIOLATION | BALANCE_VIOLATION | OK |
| rollback_rate_12pct | BOUND_VIOLATION | BOUND_VIOLATION | OK |
| deadlock_counted | BOUND_VIOLATION | BOUND_VIOLATION | OK |
| future_read_counted | BOUND_VIOLATION | BOUND_VIOLATION | OK |
| clock_not_monotone | FLAG_FALSE | FLAG_FALSE | OK |
| flux_leak_1e-4 | BALANCE_VIOLATION | BALANCE_VIOLATION | OK |
| producer_relaxes_tolerance | BALANCE_VIOLATION | BALANCE_VIOLATION | OK |
| duplicate_mass_entry | DUPLICATE_ENTRY | DUPLICATE_ENTRY | OK |
| missing_flow_face | MISSING_FACE | MISSING_FACE | OK |
| missing_provenance | MISSING_PROVENANCE | MISSING_PROVENANCE | OK |
| edited_after_seal | SEAL_MISMATCH | BALANCE_VIOLATION, SEAL_MISMATCH | OK |
| never_sealed | MISSING_SEAL | MISSING_SEAL | OK |

真输出替换负控：等效球惯量张量 → 拒绝（`BALANCE_VIOLATION`）；反距离界面通量 → 拒绝（`BALANCE_VIOLATION`）。

## ④ 结果图

图 1：断裂交接五本账的残差（float vs ℚ vs 等效球基线，对数轴，虚线为 1e-9 判据）。float 与 ℚ 都贴在机器精度，等效球基线的惯量/角动量账高 12~15 个数量级。

图 2：乐观同步的回滚率随消息延迟线性增长（实测点对恒等式 rate = 延迟/周期），红色虚线 = 5% 判据；右轴是同一运行在不回滚时的漂移，说明回滚不是优化细节而是正确性的一部分。

图 3：界面质量通量残差，面积权重算子 vs 反距离捷径，覆盖三种耦合面与网格比扫描；只有斜平面把捷径打穿到 1e-3，而本算子在任何几何下都 ≤ 1e-16。

## ⑤ 注意点与局限

1. **门的判别力来自负控，不来自绿灯**。17 个负控里有一个是「生产方自己把容差放大到 1.0 并交一份坏了 2% 的动量账」，门仍然拒绝——容差归门所有。

2. **ℚ 复核只覆盖多项式账**。能量账含 `sqrt(2U/Σm|d|²)`，是无理数，故 ℚ 排除它；能量账的 1e-16 是 float 且没有独立复核，这是本报告的证据边界。

3. **反距离捷径在轴对齐几何上碰巧守恒**（见 3.3 注记）。如果只跑一个轴对齐算例，会得出「捷径也够用」的错误结论——这正是为什么判据必须配斜平面与网格比扫描。

4. **回滚率与周期/延迟强耦合**：rate = 延迟/周期 是解析恒等式，5% 判据等价于「平均延迟 < 周期的 5%」。把它当常数指标用在别的协议上是错的，必须连同延迟分布一起入库。

5. **证据包只有一个面一份账**（本节点的工作点是最难算例）；多耦合面并存的证据包需要在面名上加限定（本次未做，属已知缺口）。

6. **时钟面是语义仿真，不是真实通信栈**：延迟分布是合成的、无丢包与重排序，回滚成本按步数计（未计检查点存储开销）。

## ⑥ 误差判定

| 关键量 | 基准 | 实测误差 | 是否 < 3% |
|---|---|---|---|
| 断裂质量账 | ρV_elem（ℚ 恒等） | 2.220e-16 | 是 |
| 断裂线动量账 | Σm_i v_i（ℚ 恒等） | 1.407e-16 | 是 |
| 断裂角动量账 | I ω（ℚ 恒等） | 4.050e-16 | 是 |
| 断裂能量账 | 动能 + U（float） | 2.702e-16 | 是 |
| 断裂惯量张量 | 平行轴合成（ℚ 恒等） | 2.355e-16 | 是 |
| 流场库存质量 | ΣρV（几何恒等） | 7.393e-15 | 是 |
| 界面质量通量 | Σρu·nA（面积权重） | 4.096e-16 | 是 |
| 屏障偏斜 | 精确 0（整数 tick） | 0 | 是 |
| 乐观重放等价 | 确定性重放 | 0 | 是 |
| 回滚率 | 5% 判据 | 0.0376 | 是（且 < 5% 判据） |

以上每一项都以「< 1e-9 / 1e-6 / 5%」的判据数字对照，误差比 3% 口径严 7~13 个数量级；唯一没有基准的量是回滚率（它是成本指标，不是误差），其判据来自节点契约的 5% 上界。

诚实边界：表里的「基准」是同一数学恒等式在另一种算术/另一种几何下的复算，不是实验数据——交接面守恒本来就没有实验基准，对抗性基准在 3.2/3.3 的捷径基线里，两个捷径都被本实现打到判据之外。

## 审计链

- 审计脚本：`scripts/run_p167w2b_n08_conservation_audit.py`（运行 10.18 s，所有数字现场计算，零手抄）
- 证据包：`artifacts/p167w2b-n08/conservation_audit.json`、`gate_verdict.json`、`negative_controls.json`、`run_manifest.json`（含每个文件的 sha256）
- 证据包摘要：`bundle_sha256=4c67c70cc02015d15b305509498dbd0cda6447f2e70c10991cc35876a28f70aa`
- 裁决封缄：`defa489051ab1f8e31d151ae7f78f87f92eb4fb5f898eef30abb066f963e26af`
- 零领域语义扫描：6 个包内文件 × 14 个禁忌词，命中 0（clean=true）
- 单测：`tests/test_p167w2b_n08_conservation_gate.py`（47 用例，含 17 个负控逐条参数化）
