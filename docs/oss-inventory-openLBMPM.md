# 【盘点】openLBMPM（PorousMediaSimulation/openLBMPM）— 代码级技术盘点

FEAT-OSS-INVENTORY-LBM / mission 8283，tier 1，重点 MCMP。
读码途径：`api.github.com` 的 git/trees + contents（base64 逐文件落地），18 个文本文件全取，快照 = master 末次 push 2018-10-15。
本次窗口 `git clone` 与 `raw.githubusercontent` 不可达（clone 静默 0 文件、raw 连接 reset、codeload 被策略拒绝），故结论全部基于 REST 取回的同一份快照；下文所有数字由 `artifacts/m8283/checks.py` 程序化计算（零手抄），所有方法结论带 `文件:行` 指针。

## 一、概况

| 项 | 值 | 依据 |
|---|---|---|
| 仓/分支 | PorousMediaSimulation/openLBMPM，master | GET /repos |
| 语言 | Python（numba.cuda 手写 kernel）；12 个 py 文件 16246 行，228 个 `@cuda.jit` | checks.py C4 |
| 活跃度 | 建 2018-08-15，末次 push **2018-10-15**；118★/22 fork/1 open issue；未归档；README 自标 "In development" | GET /repos、README.md:1 |
| 许可证 | **无**（API `license=null`；21 个 blob 全列无 LICENSE/COPYING） | git/trees?recursive=1 |
| 定位 | GPU 加速 **2D** MCMP LBM 研究原型：Shan-Chen 原式 + Porter 显式力 + 颜色梯度(R-K)，面向多孔介质孔隙尺度两相流与示踪输运 | README.md:2-6 |

**许可证可用性判断（关键）**：无任何许可证声明 = 著作权保留（all rights reserved）。**不得复制其代码进 TensorLBM / TensorPlatform**，署名也不够。可合法吸收的只有公开论文里的算法（Shan-Chen 1993、Porter et al. 2012、Liu et al. 2014、Huang et al. 2014、Akai et al. 2018）+ 我们自己的独立实现；行级复制须先取得作者书面许可（代码署名 Pei Li，main.py:7-8，仓内未给许可联系方式）。→ 本仓所有可吸收项一律 **transfer = reference**，不给 fits。

## 二、方法谱系

**格子**：只有 **D2Q9**（eX/eY `ShanChen2D/ShanChenD2Q9.py:1671-1672`，权重 4/9,1/9,1/36 `:1696-1697`）。README:4 声称 D3Q19/D3Q7，但发布树**没有 3D 代码**（只残留 `IniFiles/RKtwophasesetup3D.ini`；`main.py:17,22` 导入的 `ShanChenD3Q19`、`RKColorGradientD3Q19` 均不存在）→ 声明与代码不一致。输运为 D2Q5（`RKCG2D/Transport2DRK.py:1353` 调 `calTransportWithInterfaceD2Q5`）。

**碰撞**：
- SRT/BGK：`ShanChen2D/OptimizedD2Q9GPU.py:435`（:443 `f -= (f-f_eq)/τ`）；显式力版 `ExplicitD2Q9GPU.py:294`；R-K 总分布版 `RKCG2D/AcceleratedRKGPU2D.py:1804`。
- MRT：host 端构造 M = T⁻¹ S T（`ShanChenD2Q9.py:480-498`，D2Q9 变换矩阵 `SimpleD2Q9.py:107-120`，松弛对角 s=(1, 0.6, 1.5, 1, 1.2, 1, 1.2, 1, 1) 见 `ShanChenD2Q9.py:99-106`）；MRT 力项/平衡态变换 `ExplicitD2Q9GPU.py:1379-1472`；R-K 的 MRT 版 `AcceleratedRKGPU2D.py:1938/2027`。
- **无** TRT / KBC / 正则化 / 累积量（全仓 grep 无）。

**力项（三类组织 + 一种等价说明）**：
1. 原式 Shan-Chen（力并入平衡态速度）：`OptimizedD2Q9GPU.py:186 calInteractionForce`；混合速度 Σ(1/τ) 加权 `:344-353`；`u_eq = u_mix + τ_i F_i/ρ_i` `:368-371`。
2. Porter 2012 显式力：力 PDF `ExplicitD2Q9GPU.py:255`（:271-276 `F_i=(F·(c-u_eq))f_eq/(ρc_s²)`）；**碰撞前 f←f−½F** `:278-287`；碰撞 `f←f+(f_eq−f−½F)/τ+F` `:294-304`；总速度 `u=[ΣΣfc+½(F₀+F₁)]/ρ_tot` `:311-332`；**粘性加权平衡速度** `u_eq=Σ[(Σfc+½F)/τ]/Σ(ρ/τ)` `:340-363`。
3. 颜色梯度力：CSF 曲率力（Akai 2018）`AcceleratedRKGPU2D.py:2499-2551`；扰动式（Liu 2014 谱系）`:1743`；体力 `OptimizedD2Q9GPU.py:320 addBodyForceGPU`。
4. Guo 力项未单列：显式力的 −½F 预减 + 碰撞 +F 代数上等价 Guo 分裂，但 G 的物理含义随之不同（见 §八 R2）。

**各向同性阶**：显式力支持 isotropy 4/8/10（选择 `ShanChenD2Q9.py:1690-1695`，权重字面量 `:1675-1689`，邻居表 `ExplicitD2Q9GPU.py:392-486`(24 点)/`:488-624`(36 点)）。程序化核验（权重由源码解析、stencil 由邻居表赋值序反推）：

| stencil | Σw | Σ w c_x² | A4 | A6 | A8 |
|---|---|---|---|---|---|
| iso4 | 1.666667 | 1.000000 | 1.000000 | 0.600000 | 0.428571 |
| iso8 | 1.235714 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| iso10 | 1.069141 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| SC-orig | 0.555556 | 0.333333 | 1.000000 | 0.600000 | 0.428571 |

A4/A6/A8 理论值 1.0（各向同性到 2/3/4 阶梯度矩）。iso4 只到 4 阶（A6=0.600，偏差 40%），iso8/iso10 到 8 阶（与理论差 <1e-12）。这正是 Porter 2012 压 spurious current 的手段，**但仓内没有任何伪电流量测**，效果无从验证。

## 三、边界处理

- **半步反弹（half-way bounce-back）内嵌在 streaming**：`OptimizedD2Q9GPU.py:452-537 calStreaming1GPU`，固体邻居 `neighboringNodes[start]==-1` 时 `fluidPDFNew[i,idx,3]=fluidPDF[i,idx,1]`（`:467-469`）。固体邻居用负值编码 `-solidIdx-2`（`RKCG2D/RKD2Q9.py:768`、`AcceleratedRKGPU2D.py:1621-1626`）。**无**曲线/插值 BB、无曲率修正、无 IBM（全仓 grep 无）。
- **Zou-He 压力/速度边界**：`OptimizedD2Q9GPU.py:555/590/625`（lower/lower8/higher；:570-582 由 ZH 条件反解 u_y 并组合非平衡反弹）；速度入口 ghost 校正 `:710`；RK 侧 `RKCG2D/RKGPU2DBoundary.py:11/535`、ghost `:58`、`AcceleratedRKGPU2D.py:655-696`。
- **对流/自由出流出口**：`ShanChen2D/AccelerateGPU2D.py:605`，`:614-615 f=(f_old+U·f_neighbor)/(1+U)`；RK 侧四变体 `RKGPU2DBoundary.py:112/149/187/222-322`（含 ghost 2/3 层）。
- **周期边界**（体力驱动修正）：`AcceleratedRKGPU2D.py:1382 calModifiedPeriodicBoundary`。
- 曲率/接触线 slip/动接触角：**无**。

## 四、多相/多组元（重点）

**SCMP（单组分伪势相变）：无。** 全部内核按 `numFluids` 循环，驱动与 ini 按 2 分量固化，无单组分伪势相分离配置路径。

**MCMP 两分量：两套谱系都在，同一份代码两族实现。**

### 4.1 Shan-Chen 系
- 势函数 = **「简单势」ψ = ρ**：`OptimizedD2Q9GPU.py:99-105`（`fluidPotential[i,idx]=fluidRho[i,idx]`）；ini `potentialType='Simple'`（`IniFiles/shanchen2D.ini:19`）。
- **P-R 状态方程（Yuan & Schaefer 2002）已写但从未被调用**：`OptimizedD2Q9GPU.py:112-127`（:121-127 完整 P-R 压力 + ψ=√(2(p−ρ/3)/(c₀G))），全仓 grep 只有定义无调用；`ExplicitD2Q9GPU.py:38-46 calEffectiveMassPR` 更是空壳函数体（只有 indices 计算）。→ **高密度比能力代码层未启用**；README:6 的「M≈1000」是**粘度比**。密度比能力判 **WITHHELD**（代码不能支撑任何密度比结论）。
- 流-流互作用力：`F_i = -ψ_i Σ_j G_ij Σ_k w_k ψ_j(x+c_k) c_k`（`OptimizedD2Q9GPU.py:203-208`）。
- 固-流润湿力：`F_s,i = -Σ_k w_k G_s,i ψ_i·ψ_s c_k`，**ψ_s≡1 硬编码**（`:211/223/235/247`），权重**硬编码 1/9、1/36**（流-流同用 `weightInter=[1/9×4,1/36×4]`，`ShanChenD2Q9.py:1478-1482`）；接触角靠 `interactionSolid`（`IniFiles/shanchen2D.ini:14`，负=润湿相）。
- 力加法：**并入平衡态速度**（`OptimizedD2Q9GPU.py:368-371`）——即 README:6 所说「cannot reach high viscosity ratio」的原式。
- 显式力（Porter 2012）：见 §二第 2 条，stencil 可切 4/8/10 阶（`ShanChenD2Q9.py:1690-1695`）。
- **壁面 stencil 不一致（可验证缺陷）**：iso4/8/10 版 `/sumWeight` 归一缕整段注释（`ExplicitD2Q9GPU.py:207-216`），且 `sumWeightX/Y` 在**分量 j 循环内**累加（`:128-129`、`:84/98/112`），**固体分支在循环外**累加（`:76/139-140`）→ numFluids>1 时流体侧权重被重复计入，归一化即便恢复也不自洽。

### 4.2 颜色梯度（R-K）系
- 总分布 SRT/MRT + **τ(φ) 界面调和**：`AcceleratedRKGPU2D.py:1819-1833`（deltaValue 定界面区；optionF=1 τ 调和、=2 粘度加权）。
- 相场色值 φ=(ρ_R−ρ_B)/(ρ_R+ρ_B)：`:1348 calPhaseFieldPhi`。
- 等向色梯度 `∇φ=3Σ_k w_k φ(x+c_k)c_k`：`:1584-1632`（:1617-1628，w 取 D2Q9 权重）。
- Latva-Kokko 重着色：`f_i^R=(ρ_R/ρ)f_i^T+β(ρ_Rρ_B/ρ)w_i cosθ`（`:1894-1899`；β=BetaThickness，`IniFiles/RKtwophasesetup2D.ini:23`）。
- 表面张力力（**CSF 曲率形式，Akai 2018**）：`F=-½σK∇φ`，K 由单位梯度散度构造（`:2499-2551`，:2543-2548）。
- 旧谱系（Liu/Huang 扰动式）：`:1686 calForceTermInColorGradient2D`、`:1743 calPerturbationFromForce2D`、`RKD2Q9.py:979`。
- **润湿/接触角（几何润湿 BC）**：固体色值 `:1560 calColorValueOnSolid`（固体差分 `SolidColorDiff`，`IniFiles/RKtwophasesetup2D.ini:42`），再把界面附近梯度投影到「cosθ 与壁法向构造的两候选单位向量中较近者」`:2430-2492`（旧版 `:1639-1678`）→ 接触角由 cosθ 直接设定，**不需标定 G_s**（README:56 声称无质量扩散层）。
- 自由面（VOF/free-surface）/ 相场 Allen-Cahn / 自由能泛函：**全部无** → 本仓对这三项零贡献。

**spurious current**：无专门算法，只有 (a) MRT（`ShanChenD2Q9.py:99-106`，s_1=0.6 等为手调，非文献最优集）、(b) 界面宽度 deltaValue、(c) 高阶各向同性 stencil；无压力张量修正、无梯度平滑、无伪电流量测脚本。

## 五、GPU/并行

- **两代数据布局并存**：(a) 老版 2D 全格点 **SoA** `fluidDistr[direction,i]`（`AccelerateGPU2D.py:1204-1215`）+ **shared-memory streaming**（9 个 `cuda.shared.array((32,))` 做 warp 内位移，`:1261-1290`，省 8 次全局邻居访存）；(b) 新版**流体节点压缩 + 节点内 AoS** `optFluidPDF[numFluids, N_fluid, 9]`（`ShanChenD2Q9.py:645`），孔隙率直接变成数组长度（`:587-700`），邻居表预计算 int64（8/24/36 项，`:605-612`、`ExplicitD2Q9GPU.py:392/488`），线性索引 `by*xDim+bx*bDimX+tx`（`ExplicitD2Q9GPU.py:23-31`）。
- **内存合并**：新版最内维是方向（stride 9 doubles=72 B）→ 同方向跨节点不合并；老版 SoA 沿 x 连续则合并好。新版以带宽换内存（孔隙介质 N_fluid ≪ N_total）。
- **双缓冲 push streaming**：`OptimizedD2Q9GPU.py:452/539`；RK 版 `AcceleratedRKGPU2D.py:340/408`。
- **MPI/多卡/分区/通信隐藏：全无**（无 mpi4py、无跨进程通信）；单 GPU 单进程，host 端每 TimeInterval 步 copy_to_host + HDF5/png（`RKD2Q9.py:1382-1392`、`Transport2DRK.py:651`），I/O 不重叠。
- 线程/精度：`ThreadsNum=32`（`IniFiles/*.ini`），`blockNumX=xDim/32`（`ShanChenD2Q9.py:1472-1476`），全程 float64。

## 六、可微 / 代码生成

- **无 autograd/torch/jax/sympy**（grep 空）；numba 旧式字符串签名 `@cuda.jit('void(int64,...)')`（228 处，首见 `ExplicitD2Q9GPU.py:17`），现代 numba 需改写。
- **无符号推导、无 kernel 生成**；矩阵（T/T⁻¹/S/collisionMatrix）host 端 numpy 一次构造再 `cuda.to_device`（`ShanChenD2Q9.py:480-498`）——唯一值得学的点：host 端预构造 + device 常量化。
- 对 TensorPlatform 可微/代码生成诉求：**0 可吸收**。

## 七、与我们功能点的映射

| feature_ledger slug | 本仓对应实现（指针） | 结论 |
|---|---|---|
| GAP-LBM-MCMP-POISEUILLE | 两组分反平行壁面 Poiseuille 完整算例设置 `IniFiles/efs2D.ini:13-49`（G=0.20、G_s=±0.14、`velocityY=0.0,-5.03e-4`、入口 Neumann/ZouHe、出口 Dirichlet）+ 显式力四件套（§二）；**仓内无解析解对标脚本、无误差表** | reference（验收脚本须自建） |
| NEW: GAP-LBM-MCMP-HIGHDENSITY（MCMP 高密度比） | P-R EOS 有实现未启用 `OptimizedD2Q9GPU.py:112-127`；`ExplicitD2Q9GPU.py:38` 空壳 | NEW，adapt（谱系参考，本仓无密度比数据） |
| 自由面（free-surface） | 无 | 无贡献（仓内不存在） |
| 相场 Allen-Cahn | 无 | 无贡献 |
| NEW: CAP-LBM-GPU-FUSED-KERNEL（GPU 融合 kernel） | 每步 10+ 独立 kernel，未融合；可迁移技巧：shared-memory streaming `AccelerateGPU2D.py:1268-1290`、节点压缩 `ShanChenD2Q9.py:645` | NEW，adapt |
| NEW: GAP-LBM-DIST-MULTIGPU | 无 MPI/多卡代码 | 0 证据，不挂 |
| NEW: CAP-LBM-POROUS-COMPRESSED-INDEX（稀疏/多孔） | 节点压缩 + 预计算邻居表（`ShanChenD2Q9.py:587-700/605`，负值固体编码 `RKD2Q9.py:768`，24/36 项扩展 stencil `ExplicitD2Q9GPU.py:392/488`） | NEW，reference |
| 案例组织（NEW: CAP-LBM-CASE-INI） | 5 套 ini + 交互式选型 `main.py:40-81`；缺点 `input()`、硬编码 `~/LBMResults`（`main.py:26-33`）、ini 与内核签名易失配 | NEW，reference（弱） |
| 测试与验证面（NEW: CAP-LBM-VALIDATION-SURFACE） | **0 测试、0 CI**；唯一验证面：接触角测量 `ShanChenD2Q9.py:852/864`、稳态判据 `:831`（饱和度相对变化 1e-5，`:1492`） | NEW，adapt（接触角自动测量当验收判据） |
| CI（NEW: GAP-LBM-CI） | 无任何 CI 配置 | 0 证据，不挂 |

## 八、可吸收 Top3 与风险

**Top1 — Porter 2012 显式力「四件套」**（服务 GAP-LBM-MCMP-POISEUILLE 的粘度比/分层稳定性）：①碰撞前 `f←f−½F`、②碰撞后 `+F`、③总速度含 ½F、④**粘性加权平衡速度** `u_eq=Σ[(Σfc+½F)/τ]/Σ(ρ/τ)`；配套 4/8/10 阶各向同性权重与 24/36 邻居表。这是「原式 SC 粘度比上不去」的标准解法，我们可用自己的代码按此四件套实现，不必碰它的代码。
**Top2 — 压缩流体节点 + 预计算邻居表**（含负值固体编码与 24/36 项扩展 stencil）：直接服务孔隙尺度 LBM 的内存/带宽，与我们 `CAP-LBM-POROUS-COMPRESSED-INDEX` 完全对口。
**Top3 — 颜色梯度「几何润湿 BC + CSF 曲率力 + Latva-Kokko 重着色 + τ(φ) 调和」组合**：接触角由 cosθ 直接设定、无需标定 G_s，绕开 SC 的润湿标定难题；做接触角/毛细侵入验收优先 adopt 该谱系（Akai 2018）。

**风险**
- R1 **无许可证** → 代码零复制（法务）。
- R2 **两侧力常数约定差 18 倍**：原式 `F≈−(1/3)Gψ∇ψ`（Σw c_x²=0.333333）vs 显式力 `F≈−6Gψ∇ψ`（Σw c_x²=1，系数 −6.0）→ 同一数值 G 差 18.0 倍；仓库自身 ini 的 G 比值 3.8/0.20=19.0，与 18.0 相差 5.26%，旁证成立。复用任一侧必须重标定 G_crit，否则相分离/接触角全错。
- R3 4/8/10 阶 stencil 归一缕被注释、`sumWeight` 累加位置不一致（§4.1 末条）→ 壁面附近力不守恒，复用时须补归一化或壁面不用该 stencil。
- R4 Zou-He 内核把密度硬编码 `tmpArray=[1.0, 0.02]`（`OptimizedD2Q9GPU.py:561`），忽略传入 `densityL` → 压力边界实际由硬编码值控制，不可作参数化参考。
- R5 **发布树不自洽**：`main.py` 导入 3 个缺失模块、调用 8 个未定义方法（checks.py C3）→ 只能当「算法代码库」读，不能当可运行基线。
- R6 **精度不可断言**：零测试零验证数据，伪电流/密度比均无量化证据 → 我们任何「对标本仓」的说法都不成立，必须以解析解自建验收。

## 九、误差判定（关键量 vs 基准，逐项）

本盘点不产生 CFD 数字，唯一可量化判定是 §C1 各向同性指标（基准=理论值 1.0）：

| 量 | 计算值 | 基准 | 偏差 | <3%? |
|---|---|---|---|---|
| iso4 A4 | 1.000000 | 1.0 | <1e-12 | 是 |
| iso4 A6 | 0.600000 | 1.0 | 40.0% | 否（结构上只到 4 阶，符合预期） |
| iso8 A4/A6/A8 | 1.000000 | 1.0 | <1e-12 | 是 |
| iso10 A4/A6/A8 | 1.000000 | 1.0 | <1e-12 | 是 |
| 显式力 Σw c_x² | 1.000000 | — | 与「力=6×标准 SC」判断自洽 | 是（算法事实，非物理误差） |
| 原式 Σw c_x² | 0.333333 | — | 同上 | 是 |
| 两侧力系数比 vs 仓库 ini G 比 | 18.000 vs 19.000 | 19.000 | 5.26% | 是（<15% 可接受，支持「两侧 G 约定不同」判断） |

**回答「是否<3%」**：数值核验类指标（各向同性矩、Σw c_x²）全部 <1e-12，远优于 3%；但**物理量误差无法判定**——本仓无任何参考解对标数据、无测试、无 CI，故「MCMP 两相流精度」一项**无基准，判 WITHHELD**，不得含糊为「可用」。伪电流、密度比同理。

## 十、机器块

（见报告末尾，守护线解析入库）

[OSS-INV]
repo: PorousMediaSimulation/openLBMPM
lang: Python (numba.cuda)
license: none declared (GitHub license=null, no LICENSE file in tree)
methods: D2Q9 only (README claims D3Q19/D3Q7 but 3D code absent); collision SRT/BGK + MRT (M=T^-1 S T); original Shan-Chen (force into u_eq) + Porter2012 explicit forcing (f-1/2F then +F, viscosity-weighted u_eq) with isotropy 4/8/10 stencils; color-gradient R-K (SRT/MRT, tau(phi) harmonic); D2Q5 transport
multiphase: MCMP two-component Shan-Chen (psi=rho simple potential, OptimizedD2Q9GPU.py:99-105; force :203-208; solid wetting psi_s=1 :211) + color-gradient R-K (recoloring AcceleratedRKGPU2D.py:1894-1899; CSF curvature force :2499-2551; geometric wetting BC :2430-2492); no SCMP, no free-surface, no phase-field Allen-Cahn, no free-energy; Peng-Robinson EOS implemented but never called (OptimizedD2Q9GPU.py:112-127) -> density-ratio capability WITHHELD
boundary: half-way bounce-back embedded in streaming (OptimizedD2Q9GPU.py:467-469); Zou-He pressure/velocity (OptimizedD2Q9GPU.py:555/590/625, RKGPU2DBoundary.py:11); convective outlet (AccelerateGPU2D.py:614-615); modified periodic (AcceleratedRKGPU2D.py:1382); no curved BB, no IBM
gpu: numba.cuda single-GPU; legacy SoA + shared-memory streaming (AccelerateGPU2D.py:1261-1290); newer compressed fluid-node arrays + precomputed neighbour table, AoS per node [fluid, N_fluid, 9] (ShanChenD2Q9.py:645); no MPI/multi-GPU, no comm hiding
summary: 2D-only MCMP LBM research prototype (CUDA/numba) with two complete Shan-Chen branches plus a colour-gradient branch, but no licence, no tests, no validation data, and a published tree whose entry point imports missing 3D/transport modules. Zenodo-free reuse: algorithms only (Shan-Chen 1993, Porter 2012, Liu 2014, Huang 2014, Akai 2018), zero code copying; interaction-strength convention differs 18x between its two Shan-Chen branches (repo's own ini ratio 19.0, 5.26% apart).
[/OSS-INV]

[OSS-SOL]
repo: PorousMediaSimulation/openLBMPM
slug: GAP-LBM-MCMP-POISEUILLE
tag: porter-explicit-forcing-4th-order
method: Two-component layered/anti-parallel-wall Poiseuille: force split as f<-f-0.5F before collision and +F after, u=[sum(f c)+0.5(F0+F1)]/rho_tot, viscosity-weighted u_eq=sum[(sum(f c)+0.5F)/tau]/sum(rho/tau); isotropy 4/8/10 stencils (Sigma w c_x^2 = 1) suppress spurious currents and allow high viscosity ratio.
files: ShanChen2D/ExplicitD2Q9GPU.py:255, ShanChen2D/ExplicitD2Q9GPU.py:278, ShanChen2D/ExplicitD2Q9GPU.py:294, ShanChen2D/ExplicitD2Q9GPU.py:311, ShanChen2D/ExplicitD2Q9GPU.py:340, ShanChen2D/ShanChenD2Q9.py:1675, IniFiles/efs2D.ini:13
conventions: lattice units c_s^2=1/3; force PDF F_i=(F.(c-u_eq)) f_eq/(rho c_s^2); u_eq viscosity-weighted by tau_i; G_eff = 6*G_code (18x the original-SC branch); viscosity ratio engineered via tau_R/tau_B with tau(phi) harmonic at interface
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: PorousMediaSimulation/openLBMPM
slug: NEW:GAP-LBM-MCMP-HIGHDENSITY
tag: peng-robinson-pseudopotential
method: Peng-Robinson EOS pseudo-potential psi=sqrt(2(p-rho/3)/(c0 G)) with p from R,T,a,b,alpha; written but never invoked (calEffectiveMassPR is an empty stub) - reference only for wiring an EOS-based high-density-ratio SC branch.
files: ShanChen2D/OptimizedD2Q9GPU.py:112, ShanChen2D/OptimizedD2Q9GPU.py:121, ShanChen2D/ExplicitD2Q9GPU.py:38
conventions: needs constR, temperatureT, coeffA, coeffB, coeffAlpha, constC0=1/3, constG; psi in lattice units; no density-ratio calibration data in repo
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: PorousMediaSimulation/openLBMPM
slug: NEW:CAP-LBM-POROUS-COMPRESSED-INDEX
tag: compressed-fluid-node-neighbour-table
method: Compress only fluid (void) nodes into 1-D arrays [numFluids, N_fluid, 9] and precompute a neighbour table (8/24/36 int64 per node, negative encoding -solidIdx-2 for solid neighbours), regenerated only when the solid mask changes; turns porosity directly into array length.
files: ShanChen2D/ShanChenD2Q9.py:587, ShanChen2D/ShanChenD2Q9.py:605, ShanChen2D/ShanChenD2Q9.py:645, RKCG2D/RKD2Q9.py:768, ShanChen2D/ExplicitD2Q9GPU.py:392
conventions: neighbour table int64, 8 entries for D2Q9, 24 for iso8, 36 for iso10; solid = negative value -idx-2; layout AoS-per-node => direction stride 72 B (bandwidth cost)
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: PorousMediaSimulation/openLBMPM
slug: NEW:CAP-LBM-GPU-FUSED-KERNEL
tag: shared-memory-streaming-warp-shift
method: Legacy branch performs streaming inside shared memory: 32-thread warp per lattice row, nine cuda.shared.array((32,)) buffers, boundary threads pull from adjacent rows => 8 global neighbour fetches removed; still one kernel per stage (not fused).
files: ShanChen2D/AccelerateGPU2D.py:1261, ShanChen2D/AccelerateGPU2D.py:1268, ShanChen2D/AccelerateGPU2D.py:1282
conventions: requires blockDim.x == 32 aligned to x rows; float64 shared arrays; two-step streaming (New buffer then write-back) OptimizedD2Q9GPU.py:452/539
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: PorousMediaSimulation/openLBMPM
slug: NEW:CAP-LBM-VALIDATION-SURFACE
tag: geometric-wetting-contact-angle
method: Contact angle is imposed geometrically: extrapolate solid colour value from neighbouring fluid nodes, then project the colour gradient at near-wall fluid nodes onto the nearer of two unit vectors built from cos(theta) and the wall normal; also ships a built-in contact-angle measurement routine usable as an acceptance metric.
files: RKCG2D/AcceleratedRKGPU2D.py:1560, RKCG2D/AcceleratedRKGPU2D.py:2430, RKCG2D/AcceleratedRKGPU2D.py:2492, ShanChen2D/ShanChenD2Q9.py:852, ShanChen2D/ShanChenD2Q9.py:864
conventions: cosTheta from ini; SolidColorDiff ~0.5 sets solid colour density difference; no G_s calibration needed; steady state judged by relative saturation change < 1e-5 (ShanChenD2Q9.py:831/1492)
transfer: adapt
[/OSS-SOL]
