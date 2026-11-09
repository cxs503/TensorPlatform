# 【盘点】CudaPhaseField —— 三相相场 LBM 的 CUDA Fortran 求解器（单项目深盘·波2）

- scenario_key: `oss-inventory` ｜ feature 锚点: FEAT-OSS-INVENTORY-LBM ｜ 盘点日期 2026-11-09
- 仓库: `AminZar69/CudaPhaseField`，master，commit `d6181e677c413bbec443570010bca60d896022b5`（2026-08-16 合并 #20）
- 树指纹: `src/*.f90` 六文件拼接 sha256 = `97dc0e5d518de62a0b0d56089f408888ce3d2501445e7678d606172ab72cc1d6`
- 本仓结论前置：**这是三元（三相）相场 LBM 的“体相”实现：守恒 Allen–Cahn 界面捕捉 + 压力演化型流体分布；公开树里没有固壁/润湿/多孔实现，也没有任何测试或 CI。** GPL-3 决定其只能作算法参考（transfer=reference/adapt，零代码搬迁）。

所有数字均由 `docs/oss_inventory/cudaphasefield_evidence.py` 从源码树程序化提取/复算（脚本同时校验本报告全部 `文件:行` 指针，见第七节末）。

## 一、概况

| 项 | 值 | 依据 |
|---|---|---|
| 身份 | 三相（three-phase）LBM 相场求解器，CUDA Fortran（`nvfortran -cuda`） | `README.md:2`、`Makefile:64` |
| 语言/体量 | Fortran 90 + CUDA Fortran 扩展；`src/` 6 文件 **2170 行**（global_subroutines 1245 / host_subroutines 652 / device_var 106 / main 97 / host_var 63 / precision_m 7） | 脚本 `loc` 统计 |
| 许可证 | **GPL-3.0**（正文 + 每源文件头部 GPLv3 声明） | `LICENSE:1` |
| 活跃度 | 2021-02 首次提交（7 个 commit 后停滞）→ **2026-07-23 至 2026-08-16 复活**，20 个 PR 化 commit（#1 数组三维化、#3 .cuf→.f90、#5 共享内存分块、#7 `_fp_kind` 后缀、#9/#17 README 动画与精度基准、#11 寄存器溢出修复、#13 分布数组提出循环、#15 界面与流体分布传播合并、#19 CI 模板）；共 27 个 commit | `git log`（本地全量 clone） |
| 星/牌面 | 派发单标“星数低=未成熟”，本次**未能独立核实**（`api.github.com` 出网被安全策略拦截，改以 git clone 取证；见第八节）。可知的“排面”只有 README 里的 GPU 精度基准图表 | `README.md:7`、assets/`gpu_benchmark.png` |
| 工程面 | **零测试、零 CI、零验证数据、零输入文件**：`.github/` 只有 ISSUE_TEMPLATE 与 PR 模板（0 workflow）；全树**没有任何测试文件**（`find -name '*test*'` 0 命中），`CONTRIBUTING.md` 只要求人手“两种精度都 build and test”，无自动门禁；全部算例参数以 Fortran `parameter` 硬编码 | `src/host_var.f90:24`、`src/device_var.f90:23` |
| 定位一句话 | 用 D2Q9 + 三套分布（φ1、φ2 两条界面分布 + 压力分布）模拟“第三相包裹中的两滴不互溶液滴合并”，2D、周期边界、单 GPU | `README.md:2`、`src/main.f90:54` |

精度开关：`precision_m.f90` 单一 `fp_kind`，默认 **double**（`src/precision_m.f90:5`；README 明说单精度长跑会漂移，`README.md:67`）。

## 二、方法谱系（格子 / 分布 / 碰撞 / 力项）

**格子：只有 D2Q9。** 速度集与权重 `src/device_var.f90:26`、`:28`。程序化核验权重矩：Σw=1、Σw c_x²=Σw c_y²=1/3（c_s²=1/3）、Σw c_x⁴=1/3、Σw c_x²c_y²=1/9、Σw c_x³=0 → 各向同性到四阶，是标准 D2Q9。**无 D3Q19/27，无 3D**（`nxd/nyd` 二维，`src/device_var.f90:23`）。

**三套分布，两类方程（二元分布 LBM 的三元扩展）：**
- `h1`、`h2`：界面捕捉分布，各 9 方向（`src/device_var.f90:50`、`:52`）
- `g`：流体（压力演化型）分布，9 方向
- 第三相由**代数闭合**给出：φ3 = 1 − φ1 − φ2（`src/global_subroutines.f90:43`），因此只有两条界面分布 —— 这是本仓最重要的建模选择：三元被压成“两独立序参量 + 归一化”，省一条分布，也把三个相的化学计量绑死在单纯形上。

**每个时间步 12 个 kernel，纯算子分裂（无融合）：**
`phi_cal → periodic_phi → gradient_cal → interface_cal → chemical_potential → hydro_cal → collision_h → collision_g1 → collision_g2 → periodic_f → propagation_gather → propagation_scatter`（`src/main.f90:54`、`:60`、`:65`；顺序由 main 内时间循环体给出，脚本统计 launches/step=12）。

**界面捕捉 = 守恒 Allen–Cahn（带界面法向压缩项）：**
- 平衡态 `h_eq = φ_k Γ_i − ½ w_i (4/w)|φ_k(1−φ_k)| (e_i·n_k)/3` 结构（`src/global_subroutines.f90:688`），其中 `Γ_i = w_i + w_i(3+4.5 e·u −1.5u²)` 复用流体的二阶平衡形式；
- 松弛 `h ← h(1−w_c) + h_eq w_c + source`（`src/global_subroutines.f90:688`）；host 侧同构的平衡态装配见 `src/host_subroutines.f90:386`；
- **弛豫系数由迁移率给出**：`w_c = 1/(0.5 + 3m)`，m=0.01 → **w_c = 1.886792**（`src/device_var.f90:30`、`:31`）。这是过松弛（w_c>1），但放大因子 `|1−w_c| = 0.886792 < 1`，线性稳定；等价 τ_φ = 1/w_c = 0.53。
- 界面厚度 `w = 4` 格（`src/device_var.f90:29`），tanh 剖面 10–90 宽度 = 2·atanh(0.8)·w/2 = **4.394 格**（脚本复算）。

**化学势（三元双阱 + 梯度项，本仓的核心公式）：**
```
μ1 = (12/w)[ λ1 φ1(1−φ1)(1−2φ1) − 2λ_t φ1φ2(1−φ1−φ2) ] − (3/4) w λ1 ∇²φ1
```
`src/global_subroutines.f90:329`、`:331`；μ2、μ3 同构（φ3 项用 λ3）。λ 由表面张力矩阵按 `λ_k = σ_ij + σ_ki − σ_jk` 反解，`λ_t` 取三者的**调和平均**（`src/device_var.f90:35`）。程序化核验：
- 随包参数 σ 全为 0.1（`src/device_var.f90:35`）→ **λ = (0.1, 0.1, 0.1)、λ_t = 0.1**，三相表面张力完全退化相同；
- 双阱体项在三个纯相 (1,0,0)/(0,1,0)/(0,0,1) 处对三个组分**全部为 0**（脚本复算 max|·| = 0.0e+00）→ 纯相是自由能驻点，公式自洽；**但没有 δ 函数式的“纯相即为唯一极小”判据可查**，三相位形（哪相润湿哪相）由 σ 矩阵给定，仓内无任何相图/张力标定数据。

**离散算子（脚本逐项复算，用于精度判定）：**
- 梯度 `∂φ/∂x = (1/3)(φ(x+1)−φ(x−1)) + (1/12)(四角交叉项)`（`src/global_subroutines.f90:177`）：对线性、二次场**精确**（max err 0 ~ 1.4e-14），实测收敛阶 **1.998**，即**二阶**；对角修正不提升阶数，只把各向异性从 5 点差分级别降到 1.67e-3。
- 拉普拉斯 `(4·轴向四邻 + 1·四角 − 20·中心)/6`（`src/global_subroutines.f90:318`）：对二次场精确（3.6e-12），实测阶 **1.999**；**各向异性在 k=π/4 处 5.354e-4，是朴素 5 点格式（2.624e-2）的 1/49** —— 9 点各向同性模板的真实收益就在这里，属可用工程细节。
- 边界法向 `n_k = ∇φ_k / |∇φ_k|`，零梯度处以 `sqrt(|∇φ|²+1e-32)` 兜底（`src/global_subroutines.f90:191`）。

**流体部分（压力演化型 LBM + 三力叠加 + Guo 型分裂强制）：**
- 压力 `p = Σ_i g_i`，平衡态 `g_eq = p w_i + w_i(e·u)(3+4.5e·u) − 1.5u² w_i`（`src/global_subroutines.f90:907`）；
- 三个力源逐项写开：表面张力 `f_s = Σ_k μ_k ∇φ_k`（`src/global_subroutines.f90:462`）、压力梯度项 `f_p = −p∇ρ/3`（`src/global_subroutines.f90:378`）、以及一个应力-密度梯度耦合项 `f_m = ((0.5−τ)/τ)(S·∇ρ)`（`src/global_subroutines.f90:468` 三项求和）；
- 速度 `u = Σ g e + 0.5 f/ρ`，ρ 由组分质量密度加权 `ρ = Σφ_k ρ_k`（`src/global_subroutines.f90:474`，`src/device_var.f90:25`：ρ=(1000,500,100)）；
- 强制项按 ½ 分裂：`h_lpg = 3 w_i (e_i·F)/ρ` 先以 **−0.5·h_lpg** 进平衡态（`src/global_subroutines.f90:907` 附近写法）、碰撞后再加 **+h_lpg**（`src/global_subroutines.f90:922`）→ 等价 Guo 分裂强制，`src/global_subroutines.f90:896` 给出 h_lpg 定义。
- 粘性：`τ = (τ1−τ3)·H(φ1) + (τ2−τ3)·H(φ2) + τ3`，`H` 是 `0.5+sign(0.5,φ)` 的**阶跃**（`src/global_subroutines.f90:407`、`:410`）→ 三相粘性按“谁为正”分段常值；随包 τ1=τ2=τ3=0.9（`src/device_var.f90:32`~`:34`）→ **整套三相粘性切换在实际配置下恒为 τ≡0.9（ν=(τ−0.5)/3=0.1333），机制在场但不起作用**；且若真给不同 τ，界面处会出现**一节点宽的粘性跳变**（而非调和插值），属可复现的建模缺陷（见第八节 R3）。

## 三、边界处理（本仓最大的“名不副实”点）

- **只有周期边界**：φ 的 ghost 层由 `periodic_phi` 直接镜像写入（`src/global_subroutines.f90:54`）；分布 `h/g` 由 `periodic_f` 同步（`src/global_subroutines.f90:941`）；`propagation_gather/scatter` 用 `x−e_i` 直接取邻元、不做任何方向判断（`src/global_subroutines.f90:1159`、`:1177`、`:1203`）。
- **固壁/润湿：公开树里等于不存在。**
  - 装置上“有”：`is_solid_node` 掩膜已声明（`src/device_var.f90:76`），5 个 kernel（`phi_cal/hydro_cal/collision_h/collision_g1/collision_g2`）都对它做了 `==0` 的**流体侧**判定；
  - 但 `set_host` 把掩膜**整体置 0**（`src/host_subroutines.f90:519`），即没有几何注入路径；
  - 且**传播核从不引用掩膜**（脚本 `streaming_kernels_reference_mask=false`）→ 没有半步反弹、没有壁面 φ 的 Neumann/几何润湿条件、没有接触角；
  - 全树关键字计数：`bounce`=0、`angle|theta`=0、`wetting`=0；`git log -S anglehost` 只命中 2021 年的初始提交，且命中的是**被注释掉的分配语句** → **README 引用的两篇论文（`README.md:127` 静接触角、`README.md:129` 三相渗透）里的“改进阶梯润湿边界”从未进入公开树**。
- 结论：本仓的边界能力**不是“弱”，而是“无”**。任何“从本仓抄润湿/多孔”的计划都不成立；能拿的只有体相相场 + GPU 组织。自由面（VOF/front-tracking）同样零命中。

## 四、多相/多组元（重点：能补我们哪个缺，补不了哪个）

**算例 = 三相体系中两滴合并（droplet coalescence）。** 初值：两个半径 r=35 的圆形液滴，圆心分置 x=127∓35（**圆心距 2r=70 ⇒ 恰好相切**），剖面为 `φ_k = 0.5 + 0.5 tanh(2(r−r_i)/w)`（`src/host_subroutines.f90:412`、`src/host_var.f90:32`、`:33`；初始化子程序 `src/host_subroutines.f90:399`）。脚本复算 254×254 网格上：
- Σφ−1 的最大偏差 **1.11e-16**（归一化约束在初值即机器精度成立）；
- 面积分数 φ1 = φ2 = **0.059811**、φ3 = **0.880377**；
- ρ ∈ [100, 1000]，ρ 均值 **177.755**（`src/device_var.f90:25`）。

**密度比的口径必须说清**：ρ=(1000,500,100) 是**各组分自身的质量密度**（进 ρ=Σφ_kρ_k 用于惯性项），不是同一流体的液/气密度比，也不是伪势模型的“高密度比”。所以本仓对 `MCMP 高密度比` 这一缺口 **零贡献**（既无 Shan–Chen 伪势、也无 Peng–Robinson 状态方程、也无自由能泛函）。

**逐项能力判定（多相类）：**

| 目标能力 | 本仓 | 证据 |
|---|---|---|
| 相场 Allen–Cahn | **REAL（唯一一个真正可参考的实现）**：守恒 AC + 界面法向压缩 + 三元 μ | `src/global_subroutines.f90:329`、`:688` |
| 三元（N≥3）多相 | **REAL**：φ3 代数闭合、λ 由 σ 矩阵调和平均反解 | `src/global_subroutines.f90:43`、`src/device_var.f90:35` |
| MCMP 高密度比 | **NOT_SUPPORTED**：无伪势/无 EOS/无自由能泛函 | 全树 grep（脚本 keyword 计数） |
| 自由面（free surface） | **NOT_SUPPORTED**：无 VOF/无 fill fraction/无界面重构 | 同上 |
| 润湿/接触角/多孔介质 | **NOT_SUPPORTED**（掩膜在、几何与 BC 不在） | `src/host_subroutines.f90:519` |
| spurious current 处理 | **NOT_SUPPORTED**：无压力张量修正、无伪电流量测、无平衡态修正 | 全树 grep |

## 五、GPU / 并行（本仓最值得学的工程层）

**kernel 清单（行号 = kernel 起点，脚本统计 LOC 合计 1213 行）：**

| kernel | 起点 | 作用 |
|---|---|---|
| `phi_cal` | `src/global_subroutines.f90:22` | φ1/φ2 求和、φ3 闭合、ρ 加权 |
| `periodic_phi` | `src/global_subroutines.f90:54` | φ 周期 ghost |
| `gradient_cal` | `src/global_subroutines.f90:95` | 梯度（共享 tile） |
| `interface_cal` | `src/global_subroutines.f90:191` | 单位法向 |
| `chemical_potential` | `src/global_subroutines.f90:247` | μ（共享 tile + 9 点拉氏） |
| `hydro_cal` | `src/global_subroutines.f90:348` | p、三力、u |
| `collision_h` | `src/global_subroutines.f90:484` | 界面分布碰撞（最大 kernel，286 行） |
| `collision_g1` | `src/global_subroutines.f90:771` | 流体碰撞前半（geq + 强制项） |
| `collision_g2` | `src/global_subroutines.f90:852` | 流体碰撞后半（写回 g） |
| `periodic_f` | `src/global_subroutines.f90:941` | 分布周期 ghost |
| `propagation_gather` | `src/global_subroutines.f90:1159` | 拉取式传播（→ 24 临时） |
| `propagation_scatter` | `src/global_subroutines.f90:1203` | 回写分布 |

- **并行模型**：单 GPU、CUDA Fortran kernel（`attributes(global) subroutine`，12 个），线程映射 `x=(blockIdx%x−1)*blockDim%x+threadIdx%x`，`tblock=(32,32,1)`、`grid=ceil((nx+2)/32)`（`src/main.f90:54` 附近的 launch 配置）。**无 MPI、无多 GPU、无域分解、无 GPU Direct**（全树无 `mpi` 命中）。
- **共享内存分块（REAL，可复用模式）**：`gradient_cal` 与 `chemical_potential` 各把 φ1/φ2/φ3 装进 `s_phi(0:TILE+1,0:TILE+1)` 的**带 1 格 halo 环的 tile**，halo 由 `tx==1/TILE`、`ty==1/TILE` 与四个角线程单独装载，装完 `syncthreads()`，随后 8 邻域模板全走 shared（`src/global_subroutines.f90:104`、`:256`）。TILE 必须与 launch 的 blockDim 一致（源码注释明确写了这条耦合）。复算：单 block 的 shared 占用 = 3 相 × 34×34 × 8 B = 27.7 KB（双精度），254² 上 8×8 个 block，末列/末行的线程有一圈纯冗余。
- **传播 = gather + scatter 两趟 kernel，24 个全局临时数组**：`htemp11..28`、`gtemp1..8`（`src/device_var.f90:52`、`src/global_subroutines.f90:1177`、`:1203`）。commit #15 把 h1/h2/g 三套分布的传播**合并**到同一 pair kernel 里（这是真融合，但只是“合并同类项”）。
- **代价量化（脚本按声明数组推算，非实测）**：每节点每步传播流量 = 2×(3 pop × 8 dir × 8 B)×2 = **768 B**；若做成单 kernel pull-streaming（无临时数组）= **384 B**，比值 **2.00×** —— 即仅靠“合并 gather/scatter 为一个 kernel”就有一倍带宽的理论上界。设备内存：3 个分布数组各 4.5 MiB（共 13.5 MiB）、24 个传播临时 11.81 MiB、**173 个 `(nxd,nyd)` 显式场数组合计 85.15 MiB**（双精度）→ 真正吃内存的是“为可读性给每个物理量单开一个数组”，而不是分布本身。
- **寄存器压力是这套 kernel 的真实瓶颈**：`collision_h` 单核 286 行、要同时读 18 条 h 分布 + 18 条 heq + 18 条 hlph，历史上曾因寄存器溢出**直接 launch 失败**；修法是编译期 `maxregcount:64` 写死进 Makefile，并注明“不是诊断开关，是永久修复”（`Makefile:61`、`:64`；commit 4ccc99a）。→ 移植我们自己的融合 kernel 时，这条“先钉寄存器上限再调 tile”的经验可直接用。
- **精度切换**：`fp_kind` 单点切换（`src/precision_m.f90:5`），README 记录双精度的实测基准与单精度长跑漂移（`README.md:67`）；历史提交里还留过一段 `safe` 构建（双精度 + 该 GPU/工具链组合下必须 −O0 才正确的已确认 codegen 缺陷），后来在 Pascal→Hopper 迁移时删除 —— 属“工具链相关的数值正确性风险”，可作我们 GPU CI 的检查项。

## 六、输出与可复现性

- 输出：每 `step=8000` 步（`src/host_var.f90:25`）写一个 **ASCII `.vtr`（VTK RectilinearGrid）**，字段 Density/phi1/phi2/phi3/Velocity(3 分量)/Pressure（`src/host_subroutines.f90:582`、`:592`、`:622`、`:631`；写文件子程序 `src/host_subroutines.f90:522`）。`tf=1.5e6`（`src/host_var.f90:24`）→ 最多 188 帧、254² 网格（`src/host_var.f90:26`）。
- 复现性：**参数无输入文件**，全部硬编码在 host_var/device_var 两处且必须手工保持一致（README 明确要求“host_var 与 device_var 里的参数要设成一样”）；无 seed 需求（确定性）。计时只用 cudaEvent 包住时间循环（`src/main.f90` 主体）。
- 验证面：**仓内没有任何基准/参考数据**，README 的性能图是“单精度 vs 双精度、不同 GPU”的吞吐对比（`README.md:7`），**不是**物理精度验证。因此本仓的验收线只能靠论文（`README.md:127`、`README.md:129`）——而论文用的润湿代码不在树里（第三节）。这点决定了它的 `测试与验证面` / `CI` 只能判 **NOT_SUPPORTED**。

## 七、对我们的可用性判定（GPL-3 边界 + 移植建议）

**许可证结论**：GPL-3.0（`LICENSE:1` + 每文件头部声明）→ copyleft。**不得把本仓代码搬进 TensorLBM / TensorPlatform**；可合法吸收的是公式与算法（守恒 Allen–Cahn 三元形式、λ 由 σ 反解与调和平均、压力演化 LBM + Guo 分裂强制、9 点各向同性拉普拉斯与镜像 halo 分块、`maxregcount` 寄存器治理）。所有条目 **transfer ∈ {reference, adapt}**，给不出 `copy`。

**逐目标映射（feature_ledger）：**

| 映射目标 | 判定 | 可吸收物 | 证据等级 |
|---|---|---|---|
| 相场 Allen–Cahn | **REAL，唯一活跃实现** | 守恒 AC + 界面法向项 + w_c=1/(0.5+3m) 的迁移率接线 | 源码确认（含数值自洽核验） |
| 相场（三元 N=3 形式） | **REAL，但退化配置** | φ3 代数闭合的省分布技巧 + λ_k 由 σ 矩阵反解 | 源码确认 |
| GAP-LBM-MCMP-POISEUILLE | **不适用**（范类不同：本仓无伪势/无组分输运压力边界） | — | 源码确认（grep 0） |
| MCMP 高密度比 | **NOT_SUPPORTED** | — | grep 0（无 SC/EOS/自由能） |
| 自由面（free surface） | **NOT_SUPPORTED** | — | grep 0 |
| GPU 融合 kernel | **部分 REAL**：共享内存 halo 分块 ✔、h/g 传播合并 ✔、collide+stream 未融合 ✘（12 launch/step，768 vs 384 B/node 上界 2×） | 镜像 halo tile 装载 + 角点线程写法；maxregcount 治理 | 源码确认 + 模型推算（非实测） |
| 分布式多卡 | **NOT_SUPPORTED**（无 MPI/无域分解） | — | grep 0 |
| 稀疏/多孔 | **NOT_SUPPORTED**（无几何、无稀疏索引、无固壁 BC） | — | 源码确认（`src/host_subroutines.f90:519`） |
| 案例组织 | **STUB 级**：参数硬编码在 Fortran 模块、无输入文件、host/device 双份需手工同步；输出命名有 6 档格式化（可忽略） | 反例警示：**我们的算例必须走配置注入** | 源码确认 |
| 测试与验证面 | **NOT_SUPPORTED**（0 测试、0 参考数据、定量误差判据缺失） | — | 目录/grep 确认 |
| CI | **NOT_SUPPORTED**（`.github/` 只有 issue/PR 模板；#19 只加模板） | — | 目录确认 |

**建议吸收清单（按优先级）：**
1. **P0 — 相场能力补口**：把我们相场分支缺的“三元守恒 AC + φ3 代数闭合 + λ 由 σ 反解（调和平均）”按本仓公式独立重写，并配 **我们自己的** 验收：① Σφ−1 机器精度；② 三个纯相处体项归零；③ 双阱静止界面 cap 形变（Laplace 定律）压力差 vs σ/R 误差 <3%；④ 相体积守恒漂移 <1e-6/1e5 步。
2. **P1 — 算子基座**：9 点各向同性拉普拉斯 + 对角修正梯度（已复算：二阶、各向异性 1/49）用在我们 2D 相场/自由能分支的 μ 与曲率计算上，成本几乎为零。
3. **P1 — GPU 组织**：镜像 halo tile（含角点线程）与 `maxregcount` 治理照抄**模式**（不抄代码）；把“gather+scatter 合一”作为我们融合 kernel 的下一档优化目标（理论 2× 带宽上界）。
4. **P2 — 反例入库**：参数硬编码、无测试、论文功能未随树发布（润湿 BC）——作为“OSS 引用前必须验证功能在树”的案例，进我们的 `KNOWN_TRAPS` 语义（“README/论文引用的功能 ≠ 公开树里的功能”）。

## 八、注意点与误差判定（诚实记录）

**已确认（源码级）与推论（模型级）分列：**

| # | 结论 | 等级 |
|---|---|---|
| C1 | 公开树无固壁/润湿/多孔；论文所述阶梯润湿 BC 从未进入公开历史 | 源码确认（`git log -S` 全历史 + grep 三重） |
| C2 | 三相粘性切换机制在场，但随包 τ 全等 ⇒ 实际恒为 0.9（ν=0.1333） | 源码确认 + 脚本复算 |
| C3 | 9 点拉普拉斯各向异性比 5 点小 49×；两者阶数同为 2 | 脚本复算（收敛阶 1.9986 / 1.9986） |
| C4 | 双阱体项在三个纯相处严格为 0 ⇒ 公式自洽；但无相图/张力标定数据 | 脚本复算 |
| C5 | 传播为 gather+scatter 两趟、24 个全局临时数组；理论带宽上界 2× | 源码确认 + 模型推算（**未实测**） |
| C6 | 无测试/CI/参考数据；性能图不含物理精度 | 目录与 grep 确认 |
| I1 | 该实现属“Chiu–Lin 型三元相场 AC”谱系（结构相似），**但仓内没有引文**，此归属为**推断**，不作事实引用 | 推断 |
| I2 | `f_m`（应力-密度梯度项）应为界面附近质量/动量一致性修正，仓内无注释说明其来源与量级，**未验证** | 未检查 |

**误差判定（关键量 vs 基准，逐项）：**
1. D2Q9 权重各向同性误差：Σw c_x⁴−1/3 = 0，Σw c_x²c_y²−1/9 = 0 → **<3% 成立（实为机器精度 0）**。
2. 离散拉普拉斯对二次场的精确性：最大绝对误差 3.6e-12（相对 ~1e-13）→ **<3% 成立**。
3. 离散梯度对二次场：1.4e-14 → **<3% 成立**。
4. 梯度/拉普拉斯收敛阶：实测 1.9978 / 1.9986 vs 理论 2 → 偏差 <0.2% → **<3% 成立**。
5. 各向异性（k=π/4）：9 点 5.354e-4，5 点 2.624e-2 —— 这是**格式间比较**，非与解析解比较；9 点格式本身的相对符号误差 5.35e-4 → **<3% 成立**。
6. **物理精度：无法判定（WITHHELD）** —— 仓内无参考解、无实验对标、且本机无 `nvfortran`/无 NVIDIA GPU，**本次未能运行求解器**（`which nvfortran`、`which nvidia-smi` 均空）。任何“该求解器算得准”的陈述都缺证据，一律不写。
7. 双精度 vs 单精度：README 自述单精度长跑漂移（`README.md:67`），仓内无量化的漂移曲线 → **WITHHELD**。

**局限与踩坑（本单自身）：**
- `api.github.com` 出网被安全策略拦截（403/拦截），星数/fork/issue 数**未独立核实**；活跃度改用本地全量 clone 的 `git log` 取证（27 commit、2021 首发 + 2026 复活），这部分是硬证据。
- 4090 无 FP64 吞吐之类性能数字只作背景，未复测（本机无 GPU）。
- 无 `nvfortran` 亦无 GPU，故未做任何 runtime 复现；脚本复算的是**源码写死的数学**（权重、模板、常数、公式）而非求解结果。
- 本次未深入 `collision_g2`（`src/global_subroutines.f90:852`）与 `periodic_f`（`src/global_subroutines.f90:941`）的逐行语义（g1/g2 拆分点、ghost 写入范围），属**未检查**，若后续要移植压力演化 LBM 需补这一课。

## 九、产物与自检

- 证据脚本：`docs/oss_inventory/cudaphasefield_evidence.py`（静态提取 + 数值核验 + 指针校验 + 出图）
- 机器可读证据：`docs/oss_inventory/CudaPhaseField_evidence.json`
- 图：`docs/oss_inventory/figures/cpf_fig1_init_condition.png`（随包初值场 + tanh 剖面与 10–90 宽度）、`cpf_fig2_operator_symbols.png`（9 点 vs 5 点拉普拉斯符号保真度、梯度符号误差）、`cpf_fig3_traffic_model.png`（传播流量 768 vs 384 B/node、设备内存构成）
- 指针自检：报告内 **141 条** `文件:行` 由脚本 `--verify` 逐条校验，**bad=0**，且每条的 EXPECT token 全部命中（不只是“非空行”）；EXPECT 表 58 项全部被正文引用（unasserted=0）。
- 复跑命令：`CPF_SRC=/tmp/cpf python3 cudaphasefield_evidence.py --out CudaPhaseField_evidence.json --figdir figures --verify CudaPhaseField.md`（返回码 0）。

```text
[OSS-INV]
repo: AminZar69/CudaPhaseField
ref: master @ d6181e677c413bbec443570010bca60d896022b5 (2026-08-16); src_sha256=97dc0e5d518de62a0b0d56089f408888ce3d2501445e7678d606172ab72cc1d6
license: GPL-3.0 (copyleft) -> reuse = algorithms only, zero code copying
language: Fortran90 + CUDA Fortran (nvfortran -cuda), 6 files / 2170 lines, 12 device kernels
activity: 27 commits; 2021-02 initial burst then 5-year gap; revived 2026-07-23..2026-08-16 under PR-numbered issues (#1..#19)
positioning: 2D D2Q9 three-phase phase-field LBM: conservative Allen-Cahn for phi1/phi2 (+ algebraic phi3=1-phi1-phi2) coupled to a pressure-evolution hydrodynamic LBM; periodic box; two tangent r=35 droplets in a 254x254 lattice; single GPU
validation: no tests, no CI, no reference data, no input file (all parameters are Fortran parameters); README benchmarks are FP32-vs-FP64 throughput only
boundaries: periodic only; solid mask declared (device_var.f90:76) but set to 0 (host_subroutines.f90:519) and never read by the streaming kernels -> no bounce-back, no wetting/contact-angle, no porous media; the staircase-wetting BC of the cited papers is absent from the published tree (git log -S over full history)
verified-operators: D2Q9 weights isotropic to 4th order (Sigma w cx4=1/3, Sigma w cx2cy2=1/9); 9-point Laplacian exact on quadratics (3.6e-12), 2nd order (1.9986), anisotropy at k=pi/4 5.354e-4 vs 2.624e-2 for 5-point (49x); gradient exact on quadratics, 2nd order (1.9978), anisotropy 1.67e-3
verified-ternary: sigma=(0.1 x9) -> lambda=(0.1,0.1,0.1), lambda_t=0.1 (harmonic mean); bulk double-well term vanishes at all three pure phases (max|.|=0.0)
verified-AC: w_c=1/(0.5+3m)=1.886792 (m=0.01), |1-w_c|=0.886792<1, tau_phi=0.53, interface w=4 cells, 10-90 width 4.394 cells
verified-init: sum(phi)-1 max dev 1.11e-16; area fractions 0.059811/0.059811/0.880377; rho in [100,1000], mean 177.755; droplet centre distance = 2r (tangent)
gpu: CUDA Fortran single-GPU; shared-memory halo tiles (TILE=32, corners loaded by dedicated threads) in gradient_cal and chemical_potential; 12 kernel launches/step (no collide+stream fusion); h1/h2/g propagation merged into one gather + one scatter kernel with 24 global temporaries; maxregcount:64 pinned as permanent fix for collision_h register overshoot; no MPI/multi-GPU
memory-model (inference, not measured): streaming 768 B/node/step as coded vs 384 B/node/step if fused (=2.00x bound); 3 populations 4.5 MiB each, 24 temporaries 11.81 MiB, 173 (nxd,nyd) field arrays 85.15 MiB (DP)
physics-precision: WITHHELD -- no reference/experimental data in repo and no nvfortran/GPU available here, solver was not executed
summary: GPL-3 three-phase phase-field LBM (conservative Allen-Cahn + pressure-evolution hydro) is the only live OSS reference we have found for the Allen-Cahn/phase-field gap, with a verified 9-point isotropic Laplacian and a reusable shared-memory halo-tile GPU pattern; it contributes nothing to MCMP high-density-ratio, free-surface, wetting or porous-media gaps (no solid geometry at all in the published tree), and has zero test/CI/validation surface.
[/OSS-INV]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 相场 Allen-Cahn
tag: ternary-conservative-ac-two-order-parameters
method: Conservative Allen-Cahn for phi1/phi2 with an interface-normal compression source term, phi3 closed algebraically as 1-phi1-phi2, relaxation factor fixed by mobility as w_c=1/(0.5+3m) (over-relaxed but |1-w_c|<1); equilibrium follows h_eq = phi_k*(w_i+w_i*(3+4.5 e.u-1.5u^2)) - 0.5*w_i*(4/w)*|phi_k(1-phi_k)|*(e_i.n_k)/3.
files: src/global_subroutines.f90:688, src/global_subroutines.f90:43, src/device_var.f90:31, src/device_var.f90:30, src/device_var.f90:29
conventions: phi in [0,1] with sum_k phi_k = 1 (machine precision, 1.11e-16); interface thickness w=4 lattice cells (10-90 width 4.394); mobility m=0.01 -> tau_phi=0.53; lattice units c_s^2=1/3
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: NEW:CAP-LBM-TERNARY-CHEMPOT-SIGMA-TO-LAMBDA
tag: ternary-double-well-with-harmonic-lambda
method: Ternary chemical potential mu_k = (12/w)[lambda_k phi_k(1-phi_k)(1-2phi_k) - 2 lambda_t phi1 phi2 (1-phi1-phi2)] - (3/4) w lambda_k lap(phi_k), with lambda_k = sigma_jk + sigma_ki - sigma_ij recovered from the 3x3 surface-tension matrix and lambda_t = 3/(1/lambda_1+1/lambda_2+1/lambda_3) (harmonic mean). Verified: bulk term vanishes at all three pure phases; shipped sigma is degenerate (all 0.1) so all three interfacial tensions coincide.
files: src/global_subroutines.f90:329, src/global_subroutines.f90:331, src/global_subroutines.f90:318, src/device_var.f90:35
conventions: sigma(3,3) symmetric with zero diagonal in practice; mu enters the force as f_s = sum_k mu_k grad phi_k; lambda in lattice units; no tension calibration or phase diagram shipped
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: GAP-LBM-MCMP-POISEUILLE
tag: not-applicable-phase-field-not-pseudopotential
method: No contribution. The code has no Shan-Chen pseudo-potential, no interaction strength G, no colour-gradient, no component-resolved pressure boundary, no Poiseuille/layered-flow setup; its multiphase physics is a ternary Allen-Cahn phase field with algebraic third phase. Mapping it onto the MCMP Poiseuille gap would be a category error.
files: src/device_var.f90:25, src/global_subroutines.f90:462
conventions: rho = sum_k phi_k rho_k uses per-component mass densities (1000,500,100) for the inertial term only - this is NOT a thermodynamic density ratio
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: MCMP 高密度比
tag: not-supported
method: Not supported. No pseudo-potential/EOS (no Peng-Robinson, no Csanad), no free-energy functional, no van der Waals loop handling; the (1000,500,100) triple is a per-component mass density used only in rho=sum phi_k rho_k and in the pressure-gradient force, not a high-density-ratio mechanism.
files: src/device_var.f90:25, src/global_subroutines.f90:378
conventions: -
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 自由面（free surface）
tag: not-supported
method: Not supported. No VOF/fill fraction, no interface reconstruction, no empty-cell handling, no front tracking; the whole domain is filled with a three-phase mixture (sum phi = 1 everywhere).
files: src/global_subroutines.f90:43
conventions: -
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: GPU 融合 kernel
tag: shared-memory-halo-tile-plus-merged-propagation
method: (a) REAL pattern - gradient_cal and chemical_potential stage phi1/phi2/phi3 into shared tiles s_phi(0:TILE+1,0:TILE+1) with a one-cell halo ring loaded by the tx==1/TILE and ty==1/TILE threads plus four dedicated corner threads, then syncthreads and run the 8-neighbour stencil entirely from shared memory; TILE is a compile-time constant that must equal the launch blockDim. (b) REAL but partial fusion - the three population sets h1,h2,g are propagated together in one gather kernel and one scatter kernel through 24 global temporaries (commit #15). (c) NOT fused - collision and streaming are separate kernels, 12 launches per timestep.
files: src/global_subroutines.f90:104, src/global_subroutines.f90:256, src/global_subroutines.f90:1159, src/global_subroutines.f90:1203, src/main.f90:54, src/main.f90:65
conventions: tblock=(32,32,1); TILE must track blockDim; double precision default (8 B); streaming traffic 768 B/node/step as coded vs 384 B if gather+scatter fused (2.00x bound, model not measurement); 24 temporaries = 11.81 MiB, 173 field arrays = 85.15 MiB
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: NEW:CAP-LBM-GPU-REGISTER-PRESSURE-GUARD
tag: maxregcount-pinned-before-tuning
method: Register pressure, not bandwidth, is what breaks the biggest kernel: collision_h needs 18 populations + 18 equilibria + 18 source arrays live at once and overflowed the register budget, silently failing the launch. The fix is a compile-time cap applied to every build mode (maxregcount:64) documented as a permanent constraint rather than a diagnostic. A second, toolchain-level lesson is recorded in history: on one host-compiler/external-device-toolkit pairing, double precision silently produced NaN velocities under any optimisation level and only -O0 host + debug device fixed it.
files: Makefile:61, Makefile:64, src/precision_m.f90:5
conventions: cap applies to cudaflags and ldflags alike; release = -O3 -fast, debug = -O0 -g -Mbounds -Mchkptr -traceback; precision switched in one place (fp_kind)
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 分布式多卡
tag: not-supported
method: Not supported. No MPI, no domain decomposition, no halo exchange, no multi-GPU; execution model is one host + one GPU with module-level device arrays sized by compile-time nxd/nyd.
files: src/device_var.f90:23, src/global_subroutines.f90:22
conventions: -
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 稀疏/多孔
tag: not-supported-mask-without-geometry
method: Not supported. A solid mask exists (is_solid_node) and five kernels guard fluid updates with it, but set_host sets it to zero everywhere and the streaming kernels never consult it, so there is no bounce-back, no wall phi condition and no geometry generation; nothing in the tree can represent a porous medium.
files: src/device_var.f90:76, src/host_subroutines.f90:519, src/global_subroutines.f90:1159
conventions: -
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 案例组织
tag: hardcoded-parameter-modules
method: STUB-grade case organisation: every case parameter is a Fortran parameter duplicated between device_var (device) and host_var (host) and the README instructs the user to keep the two copies identical manually; there is no input file, no case directory, no restart. Output is a fixed-name ASCII .vtr every 8000 steps. Useful to us only as a counter-example: our cases must be config-injected.
files: src/host_var.f90:24, src/host_var.f90:32, src/device_var.f90:23, src/host_subroutines.f90:522, src/host_subroutines.f90:592
conventions: tf=1.5e6 steps, step=8000 frames, grid 254x254, r=35, w=4, density (1000,500,100), tau1..tau3=0.9
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: 测试与验证面
tag: not-supported
method: Not supported. Zero test files, zero reference datasets, no analytic-solution harness, no contact-angle or surface-tension measurement routine; the only quantitative artefact is a FP32-vs-FP64 throughput chart across GPUs, which says nothing about physical accuracy. Physical accuracy of this solver is therefore WITHHELD, not merely unverified by us.
files: README.md:7, README.md:127
conventions: -
transfer: none
[/OSS-SOL]

[OSS-SOL]
repo: AminZar69/CudaPhaseField
slug: CI
tag: not-supported
method: Not supported. .github contains only ISSUE_TEMPLATE (bug_report, feature_request, config) and PULL_REQUEST_TEMPLATE; there is no workflow, no build job, no GPU runner. The 2026 revival added templates but no automation, so nothing checks that the tree even compiles.
files: README.md:67, Makefile:64
conventions: -
transfer: none
[/OSS-SOL]
```