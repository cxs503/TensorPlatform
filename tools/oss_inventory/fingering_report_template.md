# 【盘点】Fingering_dynamics —— 相场/自由能两相 LBM + Zou-He + 半程反弹润湿（tier 1）

- 盘点主体：TensorPlatform 建设公民（mission {MISSION}，锚点 FEAT-OSS-INVENTORY-LBM，波 2）
- 仓：`{REPO}`（`master`）；**无 LICENSE 文件（GitHub `license=None` → 全版权保留）**
- 读码途径：`git clone` 在本网络 120 s 超时被收割，改用 **api.github.com contents API（base64）** 取回
  **全部 14 个文本文件（13 个 .py/.md + .gitignore）**（`git/trees/HEAD?recursive=1` 共 20 条目，其余 4 个是 ~9 MB 的 gif/png 二进制）；
  blob SHA 与行数见 `runs/oss_fingering/source_manifest.json`（可复核）。
- 证据门：`tools/oss_inventory/fingering_evidence.py` 的 **{N_POINTERS} 条（路径，逐字子串）指针** +
  **{N_ABSENT} 条缺席断言** + **{N_COUNTS} 条出现次数断言**，全部由 `fingering_inventory.py`
  在真实源码树上定位成 `path:line`；任何一条对不上即硬失败（反臆造门，不允许手写行号）。
- 复算：`tools/oss_inventory/fingering_physics.py`（独立重写的公开公式，**不含被盘点仓的代码文本**）
  → `runs/oss_fingering/analysis_results.json`；图 `runs/oss_fingering/figures/*.png`。
- 回归：`tests/test_oss_inventory_fingering.py`（把本报告的定量结论固定成断言）。
- **未读到的东西（不参与任何结论）**：4 个二进制资产
  （`3phase_precipitationVF.gif` 1.35 MB、`ezgif.com-video-to-gif-compressor.gif` 3.27 MB、
  `fingering_periodic.gif` 4.09 MB、`wettability_validation.png` 39 kB）。它们只承载"作者宣称的结果"，
  没有可复核数字，本报告不引用其中任何图像结论。

---

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | `{REPO}` | `GET /repos/{REPO}` |
| 语言 | Python（numpy / cupy / sympy / scipy / opencv / numba），无编译内核 | GitHub `language=Python` |
| 许可证 | **无（`license=None`）→ 对我们 = reference-only** | `GET /repos/...` `license` 为空 |
| 活跃度 | created 2018-10-09，最后 push **2019-02-28**；26★ / 5 fork，2 open issues，未归档 | `GET /repos/...` |
| 论文锚 | README 指向 *J. Petrol. Sci. Eng.* (2015) 一篇（两相 LBM + 指进） | {P_readme_tagline} |
| 规模 | 13 个 .py/.md 文本文件、**{LOC_TOTAL} 行** | `runs/oss_fingering/source_manifest.json` |
| 定位一句话 | 二维**相场/自由能两相 LBM**（f 承载 ρ/p、g 承载序参量 ψ）模拟多孔介质中的黏性指进；配 Zou-He 速度进出口、半程反弹障碍/壁面、扩散界面润湿；另有一个**独立的谱方法指进**求解器 | {P_mu_formula}、{P_zh_in}、{P_bb_rect}、{P_sp_ref} |

LOC 实测（文本文件，空行计入）：

{LOC_TABLE}

---

## 二、方法谱系

### 2.1 格子与数据结构

| 项 | 实现 | 证据 |
|---|---|---|
| 格子 | **D2Q9**，速度/权重表在 `Compute.__init__` 里逐点 `if/elif` 硬编码（非查表/非工厂） | {P_d2q9_table} |
| 布局 | 9 个 `(H,W)` 全网格数组的列表 `f[i]`、`g[i]`（方向优先，整场为临时量） | {P_rho_from_f} |
| 规模 | 380×380 / 1000 步（主算例）；400×400 / 4000 步（周期版）；400×420（GPU 版）；200×250（验证版） | {P_grid_380}、{P_max_t}、{P_per_psi_wall}、{P_val_grid} |

### 2.2 双分布 + 变松弛

- **f 分布**承载质量/动量，用**相依赖**松弛时间 `τ_f = 3ν(ψ) + 1/2`：{P_relax_f}、{P_mix_viscosity}、{P_mix_tau}
- **g 分布**承载序参量 ψ（守恒型相场），用**固定** τ：{P_relax_g}
- 两相黏度调和插值 `ν(ψ)=2ν₁ν₂/(ν₁(1−ψ)+ν₂(1+ψ))`，端点 ν₁=η_n（ψ=+1）、ν₂=η_n·M（ψ=−1）——
  **M=20 实为黏度比**，尽管注释写作「非牛顿/牛顿」：{P_M_ratio}
- 宏观量：`ρ=Σf`（{P_rho_from_f}）、`ψ=Σg`（{P_psi_from_g}）

### 2.3 多相模型 = 自由能 / 相场（Cahn–Hilliard 型），**不是** Shan-Chen

| 项 | 内容 | 证据 |
|---|---|---|
| 化学势 | `μ = a·ψ(1−ψ²) − κ∇²ψ`（双阱自由能 + 梯度项） | {P_mu_formula} |
| 压力 | `p = ρ/3 + ψ·μ`（含界面贡献） | {P_pressure_law} |
| 界面参数 | `κ = 0.75·ξ·σ`、`a = −2κ/ξ²`、`ξ = 2` 格子 | {P_kappa_relation}、{P_a_relation} |
| 迁移率 | `γ = u₀H/((−a·Pe)(τ−0.5))` | {P_gamma_mobility} |
| 力的注入 | 由 ∇ψ·μ 构造 Guo 型源项，带 `(1−1/(2τ_f))` 半隐式因子（同文件 `getLarge_F`） | {P_relax_f} |
| 缺席 | 无 Shan-Chen 伪势、无重力/浮力、无自由面 VOF、无颜色梯度 | 缺席断言 `ShanChen` / `gravity` 全绿 |

**密度比**：ψ∈[−1,1]，ρ 恒 ≈1（不可压单密度）→ 本仓**不做高密度比**，对应我们台账
`GAP-LBM-MCMP-HIGHDENSITY` 的是**另一条技术路线**（能量型相场），不是可迁移的高密度比方案。

### 2.4 边界条件

| 类型 | 实现 | 证据 |
|---|---|---|
| 入口 | **Zou-He 速度入口**：`ρ_in=1/(1−u_x)(...)`，非平衡反弹额外带 **∇ψ·μ/2 化学势通量修正**；ψ 用 `1−Σg_known` 反推并按 `w₁+w₅+w₈` 归一分配 | {P_zh_in}、{P_zh_in_rho}、{P_zh_in_flux}、{P_zh_in_psi}、{P_zh_in_psi_split} |
| 出口 | **也是速度边界**（`u_x=u₀`；`ρ_out=1/(1+u_x)(...)`）——**不是压力出口** | {P_zh_out}、{P_zh_out_rho} |
| 出口角点 | 只显式处理 2 个角点（入口处理 4 个）→ 出入口不对称 | {P_zh_out_corner} |
| 上下壁 | 半程反弹（无滑移；ψ 由虚拟节点 ψ_wall 提供） | {P_top_bottom_bb} |
| 矩形障碍 | 8 类壁面/角点掩码的**半程反弹**，反弹表按方向 1..8 手写 | {P_bb_rect}、{P_bb_rec_def} |
| 圆/椭圆障碍 | 凹/凸角点分类的另一套反弹表 | {P_bb_circle_def} |
| 带孔左壁 | 左边界中部开孔，其余仍反弹 | {P_bb_hole} |
| 迁移 | `np.roll` 全周期环绕后由上述边界函数覆写（依赖"先迁移再赋边界"的顺序） | {P_stream} |

### 2.5 润湿

润湿**不是**接触角标定，而是"壁面给定虚拟序参量 ψ_wall"的扩散界面壁面条件：壁面格点的 ψ 被强制为
`psi_wall`，并作为差分虚拟节点参与 ∇ψ、∇²ψ（{P_psi_wall_halo}）。四个脚本取四种值：
`−1.0`（主算例 {P_psi_wall_main}）、`0.0`（验证 {P_val_psi_wall}）、`−0.5`（周期 {P_per_psi_wall}）、
`+1.0`（GPU {P_gpu_psi_wall}）——足以说明"润湿参数"只是人工扫描的超参，没有定量标定链。

### 2.6 几何

`create_block.py` 把矩形/圆/椭圆（含旋转）障碍栅格化、用 `binary_fill_holes` 补洞，
`setblock()` 叠加成全局布尔掩码并输出**角点表**供半程反弹使用：
{P_blk_rect}、{P_blk_circle}、{P_blk_ellipse}、{P_blk_fill}、{P_blk_set}、{P_blk_call}。
主算例是**交替 4/5 块交错阵列**（{P_blk_call} 前 20 行）：本报告用独立重写的循环复算得
**{V_NBLOCKS} 块 20×20 障碍 → 固相占比 {V_SOLID}、孔隙率 {V_POROSITY}**。

### 2.7 第二条技术线：谱方法指进（`spectrum_method/`）

与 LBM 无关的另一套实现：涡量-流函数 + 谱方法解 Saffman–Taylor 型界面失稳。

| 项 | 实现 | 证据 |
|---|---|---|
| 文献 | Tryggvason & Aref, *Phys. Fluids* 1988（DOI 10.1063/1.866726）；README 另称三相版对应 PRE 93, 023103，但三相脚本本身只引 1988 文献 | {P_sp_ref}、{P_readme_pre_ref}、{P_sp_3phase_ref} |
| 网格 | 256×128（三相版 512×128）配置点，周期谱离散 | {P_sp_grid} |
| 时间推进 | **积分因子 + 二阶 Adams–Bashforth**；启动步 RK4 | {P_sp_if}、{P_sp_rk4}、{P_sp_ab2} |
| 非线性项 | `J = φ_y c_x − φ_x c_y`；流函数谱解 `φ̂ = −iRk_yĉ/(k²+iR(k_xc_x+k_yc_y))` | {P_sp_jacobian}、{P_sp_phi} |
| 后处理 | 从浓度场 FFT 提取特征波数（**无与线性稳定性理论对比**） | {P_sp_wavenum} |

---

## 三、数值实现质量（本报告的独立复核）

### 3.1 差分模板：**正确**（REAL）

仓内 `getNabla_psix/y`、`getNabla_psi2` 用的是 D2Q9 各向同性模板（梯度 `(1/12)[4·轴向+对角]`、
拉普拉斯 `(1/6)[4·轴向+对角−20·中心]`，{P_lap_center}、{P_lap_norm}）。我把它们独立重写后做两项机器检验：

{T_STENCIL}

两处**无害异常**（如实登记，避免误判为 bug）：

1. 拉普拉斯函数里 `i=1` 用了 `axis=0`（在梯度函数里 `axis=0` 是 y）——轴向权全为 4、对角权全为 1，
   四个轴向/对角邻居仍被穷举，**数值等价**（二次场 0 误差 + 图 3 二阶收敛即为证据）。
2. `a = −2κ/ξ²` 与 `κ = 0.75ξσ` 联立后，平衡解**恰好**是 `ψ = tanh(x/ξ)`、界面张力**恰好**是
   `σ = 4κ/(3ξ)`（第六节给出解析 + 数值复核）——说明系数抄自自洽的自由能公式，而非拟合值。

### 3.2 工程面：脚本级，不是库

| 观察 | 证据 |
|---|---|
| 顶层全局常量 + 模块级 `print`；无 CLI、无配置文件 | {P_grid_380}；缺席断言 `argparse` |
| 无断言/无自检 | 缺席断言 `assert` |
| `np.seterr(all='raise')` 只在 `__main__`；`except Warning` 捕不到 `FloatingPointError` → 发散直接崩而不被按设计吞掉 | {P_randint_bug} 同文件末尾 |
| 引入 `sympy` 仅为符号占位（运行期无用） | {P_sympy_import} |
| `n_non`（幂律指数）、`rho0` 定义后主流程不用 | 次数断言 `n_non=1`、`rho0=2` |
| 每步 2 次 `copy.deepcopy`（18 个 H×W 场）+ `stream` 里 ~28 次 `np.roll`；380² 每场 1.16 MB → 每步约 40 MB 量级的纯拷贝/临时量流量（**推断**：静态计数，未做性能剖析） | {P_stream} |
| 无 tests/、无 `.github/`、无 `requirements.txt`；README 目录树把 GPU 脚本写成 `fingering_per_gpu.py`（实际 `fingering_periodic_gpu.py`）、验证脚本写成 `valitation.py`（实际 `validation.py`） | {P_readme_gpu_name}、{P_readme_typo_validation} |

### 3.3 GPU 版：CuPy 直替换，**无融合 kernel**（关键负面结论）

`fingering_periodic_gpu.py` 把 numpy 换成 cupy（{P_gpu_cupy}、{P_gpu_device_array}，78 处 `cp.`），
但**没有自研 kernel**：`RawKernel` / `ElementwiseKernel` / 直接 CUDA / MPI 全部 0 命中（4 条缺席断言全绿）。
即：**逐算子 kernel 启动 + 全网格临时量**（每步 ~20 个算子，每个算子一个 kernel），瓶颈是 HBM 带宽与启动开销，
不是"融合 kernel"。于是我们台账里 `NEW:CAP-LBM-GPU-FUSED-KERNEL` **不能**从本仓继承，只能参考其算子求值顺序。
另：GPU 版仍 `import cv2`（{P_gpu_cv2}）——CPU 侧依赖未解耦。

---

## 四、验证面（本仓最薄的一环）

| 宣称 | 实际 | 证据 |
|---|---|---|
| "wettability validation" | `validation.py` 自述「润湿性确认代码」，但**只跑液滴 + 画 ψ 图**；`Theta = π/4` 定义了却**全文件仅 1 次出现**（从未参与计算） | {P_val_doc}、{P_val_theta}、{P_val_plot} |
| 液滴初值 | 半径 36 的圆，中性壁 ψ_wall=0 | {P_val_droplet}、{P_val_psi_wall} |
| 定量指标 | **无 Young–Laplace 压差、无接触角反演、无解析解对比、无质量守恒监控** | 缺席断言 `contact_angle`；同文件无 `assert` |
| 指进形态测量 | `branch_width.py` 用 OpenCV 做视频帧差分+轮廓，量"分支宽度"——是**测量工具**，但仓内没有基准值回填 | {P_bw_width}、{P_bw_cv2} |
| 谱方法 | `getwavenum()` 从场 FFT 提取特征波数，本可与线性稳定性理论对比——仓内没有对比 | {P_sp_wavenum} |
| 复现门槛 | 无 `requirements.txt`；依赖 numpy/cupy/sympy/scipy/opencv/pandas/numba/matplotlib；`plt.show()` 需交互后端 | 树中无 requirements |

**证据等级判定**：物理*实现*是 **REAL**（公式自洽、模板正确，第六节复核），但**验证面 = WITHHELD**：
仓内没有任何可复核的定量误差，作者的结果只能以 ~9 MB 的 gif/png 字节流"看"，无法核对。
因此指进形态、接触角、界面张力精度一律标 **WITHHELD（不得作为我们的基准）**。

---

## 五、与我们 feature_ledger 的映射

| feature_ledger slug | 本仓对应实现（指针） | 结论 |
|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE` | 双分布相场 + 相依赖黏度 + 驱替入口（{P_mu_formula}、{P_zh_in}、{P_mix_tau}） | **adapt**：把分层两相 Poiseuille 的验收算例接到相场求解器（我们现有 MCMP 是伪势/颜色梯度） |
| `GAP-LBM-MCMP-HIGHDENSITY` | 无高密度比机制（ρ≈1，ψ∈[−1,1]） | **不适用**：本仓给的是能量型相场路线，密度比恒 1 |
| NEW: 相场/自由能内核 | `μ=aψ(1−ψ²)−κ∇²ψ`、`p=ρ/3+ψμ`、`σ=4κ/(3ξ)`（{P_mu_formula}、{P_pressure_law}、{P_kappa_relation}） | **fits**：TensorLBM 缺"守恒型 Cahn–Hilliard 相场"内核，这是最小可移植闭环（含 κ/σ/ξ 一致定标） |
| NEW: 两级 Zou-He（含化学势通量） | 入口 `ρ_in=1/(1−u_x)(...)−ψ_xμ/2`；ψ 侧 `1−Σg` 按权重分配（{P_zh_in_flux}、{P_zh_in_psi_split}） | **adapt**：多相 Zou-He 需同时补"质量 + 序参量"两条守恒式，本仓给出一种权重分配约定 |
| NEW: 扩散界面润湿壁 | 壁面虚拟 ψ_wall 作为差分虚拟节点（{P_psi_wall_halo}） | **fits**：比 Shan-Chen 的 `G_ads` 少一层标定，但"接触角 vs ψ_wall"标定表本仓没做（见第四节） |
| NEW: 障碍几何 + 角点反弹 | `setblock()` → 掩码 + 角点表；8 类/12 类角点反弹表（{P_blk_set}、{P_bb_rec_def}、{P_bb_circle_def}） | **adapt**："把任意栅格几何变成无滑移壁"的角点分类表值得作为我们几何管线的验收项 |
| NEW: 谱方法 Saffman–Taylor | 积分因子 + AB2/RK4，涡量-流函数谱解（{P_sp_if}、{P_sp_phi}） | **adapt**：指进的**线性稳定性**验证最好用谱方法（可解析对标），LBM 只做非线性阶段；可作第二实现交叉验证 |
| NEW: GPU 融合 kernel | CuPy 直替换、无 kernel（{P_gpu_cupy}；4 条缺席断言） | **reject（as-is）**：仅可参考算子顺序，性能路径不可继承 |
| `GAP-LBM-CI` / 验证面 | 无测试、无 CI、无断言 | **reject**：反面教材（本报告因此把精度结论全部降级为 WITHHELD） |

---

## 六、独立复算（本报告新增的可机器复核结果）

### 6.1 自由能相场：解析解与仓内常数的一致性

模型 `μ = aψ(1−ψ²) − κ∇²ψ`；平衡（μ=0）一维界面满足 `κψ'' = aψ(1−ψ²)`。

解析：取 `a = −2κ/ξ²`（正是仓内 {P_a_relation}），方程化为 `ψ'' = −(2/ξ²)ψ(1−ψ²)`；
首积分得 `ψ = tanh(x/ξ)`（ψ(±∞)=±1 的唯一单调解），界面张力
`σ = κ∫ψ'²dx = κ·(4/3)/ξ = 4κ/(3ξ)`——与仓内 `κ = 0.75·ξ·σ`（{P_kappa_relation}）**代数等价**。

数值复核（Newton + 步长回溯，Dirichlet ψ(∓L)=∓1，κ 已约去）：

{T_KINK}

⇒ **`κ=0.75ξσ` + `a=−2κ/ξ²` + `ξ=2` 三常数互相自洽**，不是拟合系数。这三个关系式（而非代码文本）
可以直接搬进 TensorLBM 的相场内核。

### 6.2 黏度插值与 BGK 松弛时间

{T_VISC}

`ν(ψ=+1) = η_n = v0 = (τ−0.5)/3 = {V_V0}` 是**代数恒等式**：因为仓内 `R_Nu = Eta/1000`
使 `η_n = Eta·C_t/(C_ρC_W²) ≡ v0`（{P_eta_n}）。也就是说这一层换算**不含独立物理信息**
（`{V_ETA_N}` 只是 `v0` 的循环定义）——这也解释了本仓**为何无法宣称"物理标定"**。

### 6.3 无量纲换算链（按 `fingering.py:16-50` 的公式重算）

{T_CHAIN}

⇒ Ca 闭合误差为 0 属**代数恒等（不是验证）**；Ma≈{V_MA} 说明不可压假设成立（可直接作为我们算例的
参数合法性判据）；Re≈{V_RE} 说明确实落在低速指进（Stokes）区。

### 6.4 谱方法积分因子（扩散）精确性

`c̄ = [ĉ + Δt(−3/2·Ĵⁿ + 1/2·Ĵⁿ⁻¹)]·exp(−k²Δt)`（{P_sp_if}、{P_sp_ab2}）。纯扩散（J≡0）时该格式对
解析解 `exp(−k²t)` 精确（浮点级）：

{T_SPECTRAL}

⇒ 这是本仓**唯一**能给出"零误差"结论的格式性质；它验证的是**时间积分器**，不是指进物理。

### 6.5 图件

- 图 1 `runs/oss_fingering/figures/fig1_interface_profile.png`：离散 kink 解 vs `tanh(x/ξ)` + 网格收敛。
- 图 2 `runs/oss_fingering/figures/fig2_viscosity_map.png`：`ν(ψ)`、`τ_f(ψ)`（M=20）。
- 图 3 `runs/oss_fingering/figures/fig3_stencil_convergence.png`：差分算子二阶收敛。

---

## 七、注意点 / 陷阱 / 适用边界

1. **`np.random.randint(0,1,...)` 恒为 0 → 初始扰动退化**（{P_randint_bug}）：
   `my_number = randint(0,1)*2−1 ≡ −1`，ρ 初值扰动恒为**负**且幅度恒定。指进靠扰动触发，
   这条"随机"注入实际是单边偏置 → 照抄会得到虚假的确定性对称性。
2. **出口是速度边界而非压力边界**（{P_zh_out}）：驱替问题通常给压力出口/回压，速度出口会抑制下游压力波动，
   可能低估界面失稳（**推断**，未运行验证；影响 JD 判据）。
3. **出口角点处理不对称**（{P_zh_out_corner} 仅 2 行）：质量守恒可能有 O(1) 边界泄漏（**推断**），
   4000 步长时程会累积；本仓没有质量守恒诊断可查。
4. **M 的语义漂移**：注释「非牛顿/牛顿」（{P_M_ratio}）vs 实际"两相黏度比"（{P_mix_viscosity}）；
   真正的幂律 `n_non` 定义后未用（次数断言 `n_non=1`）。抄参数前先对齐语义。
5. **τ_f 极端值**：ψ=−1 端 `τ_f≈{V_TAUMAX}` 远离 BGK 精度区间（0.5~2）→ 该相剪切波衰减极快，
   相界面附近存在**非物理的黏性偏置**（既非物理也非二阶精度）。
6. **无许可证**：`license=None` ⇒ 代码文本**不可**并入 TensorLBM/TensorPlatform（无授权即保留全部权利）。
   可用的是**公式与算法思想**——第五节所有 `transfer` 均按"重写实现"标注。
7. **README 与实现不一致**（{P_readme_gpu_name} vs {P_readme_typo_validation}）；仓名 `Fingering_dynamics`
   但主算例是"障碍阵列中的两相驱替"，与 gif 形态不能互相印证（gif 未展开，不计入证据）。
8. **验证面全空**：唯一含 `Theta` 的行从未被使用（{P_val_theta}）→ 本仓**不能**作为接触角/指进精度基准。

---

## 八、结论与最小下一步

**判定**：`REAL-公式 / WITHHELD-精度 / 无 CI / 无许可证`。作者自用、可读性好、物理自洽但**完全没有验证**
的 tier-1 参考实现；对我们最有价值的是三条自洽定标关系 + 两套边界条件约定，而不是代码。

最小下一步（建议排进 LBM 多相战役，按性价比排序）：

1. **S2 相场内核**：在 TensorLBM 新建"守恒型 Cahn–Hilliard 相场"多相内核，以本报告 6.1 的
   kink + `σ=4κ/(3ξ)` 作为**机器可裁的验收门**（离散解 vs tanh 的 h² 收敛 + σ 数值积分相对误差 <1e-6）；
   首个算例用 `GAP-LBM-MCMP-POISEUILLE` 的分层两相 Poiseuille（相场版）+ Young–Laplace 液滴压差
   （补上本仓缺的那一步）。
2. **S2 两级 Zou-He**：实现"ρ + ψ 双守恒式"的入口/出口，验收判据用**质量守恒残差**（仓内缺；出口角点要补全，见陷阱 3）。
3. **S3 对照台**：谱方法 Saffman–Taylor 作为指进线性稳定性的解析对标台，LBM 只跑非线性阶段与润湿壁面，
   两者在"最不稳定波数/增长串"上做交叉验证（这是本仓两条技术线本该做但没做的对比）。
4. **不做的**：不搬 GPU 路径（无融合 kernel）、不搬高密度比叙事（ρ≈1）、不引用任何精度数字（WITHHELD）。

---

{MACHINE_BLOCKS}