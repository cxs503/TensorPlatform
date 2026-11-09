# 【盘点】sailfish-team/sailfish —— CUDA/OpenCL GPU LBM 先行者（历史架构参照）

> 任务 FEAT-OSS-INVENTORY-LBM / 单项目深盘（tier 2）。读码途径：`git clone https://github.com/sailfish-team/sailfish.git` 成功（本地 `/tmp/sf`，未进入我方工作树）。HEAD = `b7140c58`（2019-11-06），工作树 clean。
> 全文方法结论均附 `文件:行`。第七/八节的定量结论由 `artifacts/oss_inventory/sailfish/probe_sailfish.py` 从 sailfish **源码本体**解析后独立复核（结果 `probe_results.json`，图 1–3），表格数字程序化注入，无手抄。

## 一、概况

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | sailfish-team/sailfish | — |
| HEAD | `b7140c58`（2019-11-06，"Merge pull request #50 from sailfish-team/devel"） | `git log -1` |
| 主语言 | Python（元编程）+ OpenCL C / CUDA C（mako 模板生成的内核） | `README:1-40`；`sailfish/backend_opencl.py:15`；`sailfish/backend_cuda.py:34` |
| 规模 | 154 `.py` + 27 `.cc`/23 `.h`（多数属 `utils/voxelizer` 体素化工具）+ 28 `.mako` 模板 | `find` 统计 |
| 许可证 | **LGPL v3**（声明式：`README:3-4`；`setup.py:51` `license='LGPLv3'`）。仓根**无 LICENSE 文件**，GitHub API `license: null` | `README:3-4`, `setup.py:51` |
| 活跃度 | 205 commits；created 2011-11-13；最后**代码**提交 2019-11-06；`pushed_at` 2022-02-03（仅元数据）；256★ / 89 forks / 14 open issues → **休眠 6.7 年** | GitHub REST；`git rev-list --count` |
| 定位（一句话） | 2011–2019 年"sympy 符号推导 → mako 模板 → OpenCL/CUDA 内核"的 Python 元编程 LBM 框架；**多相（Shan-Chen 单/双/三组分 + 自由能二元）实现完整**，是"符号→内核生成"范式的早期标本与 GPU 数据布局（SoA + AA/AB）的教科书级参照 | `sailfish/sym.py`、`sailfish/templates/*.mako` |

**许可证对我们的可用性判断（关键）**

- LGPLv3 = 弱 copyleft，约束**链接/派生库**：若把我方代码与 sailfish 代码结合为库再分发，须以 LGPL 开放该库并允许重链接。直接搬运 `.mako`/`.py` 片段进 TensorLBM 等于把 LGPL 义务引入自有求解器 → **禁止拷贝代码**。
- 方法/公式/数据布局/验证判据属**思想**，不受版权约束。本报告界定的用途 = **reference（只读参照）+ 自家框架内 clean-room 重写表达式**，注释保留"对照 sailfish `<file>:<line>`"溯源。
- 无专利/商标障碍，无 GPL 式强传染 → "读码—重写"路径合规。故 `transfer` 一律 ≤ `adapt`，**不给 `fits`（直接取用）**。

## 二、方法谱系

**格子**（权重/basis 均为 sympy `Rational` 精确值）

| lattice | Q | dim | |Σw−1| | |Σ w·e| | |Σ w e_ae_b − δ/3| | |m40−3m22| | class | weights |
|---|---|---|---|---|---|---|---|---|
| D2Q9 | 9 | 2D | 0.0e+00 | 0.0e+00 | 5.6e-17 | 0.0e+00 | sym.py:61 | sym.py:74 |
| D3Q15 | 15 | 3D | 0.0e+00 | 0.0e+00 | 5.6e-17 | 0.0e+00 | sym.py:228 | sym.py:236 |
| D3Q19 | 19 | 3D | 0.0e+00 | 0.0e+00 | 1.1e-16 | 0.0e+00 | sym.py:312 | sym.py:323 |
| D3Q27 | 27 | 3D | 0.0e+00 | 0.0e+00 | 1.7e-16 | 1.1e-16 | sym.py:409 | sym.py:421 |

（`D3Q13` 亦在 `sym.py:151`；`KNOWN_GRIDS` 声明 5 个格子 `sym.py:1151`。D3Q27 仅用于符号/矩测试 `tests/sym_equilibrium.py:118`，无专用碰撞模型。）

**碰撞谱系**（统一在 mako 模板里分支，sim 类只装配参数）

| 碰撞 | 实现位置 | 说明 |
|---|---|---|
| BGK | `sailfish/templates/relaxation.mako:183-202` | `relaxate()`/`_relaxate()`，力项经 `bgk_args` 注入 |
| MRT（矩空间） | `relaxation_mrt.mako:30-97` | `MS_relaxate()`；变换矩阵与逐矩松弛率按格子定义（D2Q9 `sym.py:78-130`，D3Q19 `sym.py:330+`） |
| TRT | **无独立实现** | 仅把 MRT 的 `pxx`/`pxy` 设为同一 `inv_tau`（`sym.py:110-113`），无 `Λ±` 双松弛率 API |
| entropic（H 定理） | `templates/entropic.mako:39-70` | entropic mirror state `f^α=f+α f^neq`；权重 `grid.entropic_weights`（`sym.py:326`） |
| KBC / RR | 无（`NTRegularized*` 是边界条件，非碰撞模型：`node_type.py:208,269`） | — |

**力项**（`--force_implementation {guo,edm,velocity_shift}`，`lb_base.py:325-326`）

| 方案 | 位置 | 数学 |
|---|---|---|
| Guo | `relaxation_common.mako:56-64` | 源项 `(1−1/(2τ))w_i[(e−v)/c_s²+(e·v)e/c_s⁴]·F`，速度含 `F/(2ρ)` 修正（`:110-118`） |
| EDM（exact difference / shifted velocity） | `relaxation_common.mako:66-82` + `sym_force.py:184-193` | 平衡态里的 `v` 换成 `v+a`，一阶自动含源项 |
| velocity shift | `relaxation_common.mako:121-123` | `v0 = iv0 + τ a` |
| 自由能外力离散形式 | `sym_force.py:162-182` | 依 Kusumaatmaja 论文 Eq. 2.13 构造 `−Σ_i w_i[(a·e)(1+3e·v) − a·v]` 型源项 |

**组织方式**：`lb_*.py` 只含"内核名 + 参数表 + 缓冲区拼装"（范例 `lb_binary.py:423-517`），算术全在 `.mako` 由 sympy 表达式生成 ⇒ 一套模板 × N 模型/格子。这既是它 2011 年覆盖 5 格子 × 3 碰撞 × 多边界的原因，也是腐化后无法运行的原因（第六节）。

## 三、边界处理

| 边界类型 | 位置 | 备注 |
|---|---|---|
| 半程反弹（`NTHalfBBWall`） | `node_type.py:115`；`boundary.mako:322-343` | 壁面位于节点间；多相模型要求壁后留一层 unused 层（`binary_shan_chen.mako:132-133` FIXME） |
| 全程反弹（`NTFullBBWall`） | `node_type.py:144` | 2 步时滞回注，多相里需 INFINITY 特判（`lb_binary_fluid.mako:103-111`） |
| Zou–He（密度/速度） | `node_type.py:229` / `:256`；`boundary.mako:344` `zouhe_fixup` | 逐方向补缺失分布 |
| Guo 密度外推 | `node_type.py:222`；`boundary.mako:201-235` | 含 `guo_density_node_index_shift_*` 三段式索引位移技巧 |
| 正则化 | `node_type.py:208`（密度）、`:269`（速度） | — |
| TMS 移动壁 | `node_type.py:171`；动量交换力 `lb_base.py:422`（引 Ladd 1994） | 用于粒子/壁面受力 |
| 其他 | 滑移 `node_type.py:402`、Neumann `:353`、Yu 出流 `:336`、DoNothing `:296`、Copy `:310`、梯度自由出流 `:286` | — |
| **曲率/插值反弹**（Bouzidi、Filippova–Hänel） | **无** | `grep -i "curved\|bouzidi\|interpolated.*bounce"` 仅命中 Ladd 注释 `lb_base.py:422` ⇒ 曲面壁只能体素化 + 阶梯近似 |
| IBM | `templates/ibm.mako:1-98`；挂载 `lb_single.py:354-364`；核调度 `subdomain_runner.py:1784` | φ₂ 核 `(1−|dx|)(1−|dy|)(1−|dz|)`，支持域 2×2(×2) 节点（`ibm.mako:27-38`）；力分摊 `atomicAdd`（`:87-91`）；Hooke 回复力 `:78-89` |

## 四、多相/多组元（重点）

> 结论先行：**本仓有多相，且是两种独立范式（伪势 Shan-Chen / 自由能 Cahn-Hilliard 型）；但没有自由面、没有相场 Allen-Cahn、没有颜色梯度/CSF，也没有任何密度比或 spurious current 的量化证据。**

**(1) 单组分 Shan-Chen（伪势）** — `lb_single.py:242-270`
- 耦合注册 `add_force_coupling(0, 0, 'SCG')`（`lb_single.py:250`），耦合表结构 `lb_base.py:386-394`。
- 伪势 `sym.py:896-903`：`linear: ψ=ρ`；`classic: ψ=ρ₀(1−e^{−ρ/ρ₀}), ρ₀=1`；注册表 `:905-908`。**只有这两种**（无 Carnahan–Starling / P–R / Li-Luo 改进势）。
- 力核 `shan_chen.mako:45-84`：`F = −G ψ(x) Σ_i w_i e_i ψ(x+e_i)`（逐方向累加 `:57-75`，局部 ψ `:78`）。
- 力→加速度 `shan_chen.mako:9-25`：`sca0[i] /= g{grid}m0`（即 `a=F/ρ`），再以"加速度进平衡态"方式入碰撞，不另加源项。

**(2) 双组分 Shan-Chen** — `lb_binary.py:375-517`
- 参数 `G11/G12/G22` + `sc_potential`（`lb_binary.py:406-414`），两套分布各自 `tau`（`:420`）。
- **关键约定**：宏量准备 `binary_shan_chen.mako:49-87` 先各自求 ρ_σ、ρ_σu_σ，再用松弛时间加权合成同一速度场 `u = Σ_σ(ρ_σ/τ_σ)u_σ / Σ_σ(ρ_σ/τ_σ)`（`:78-82`，`total_rho = tau0_inv*rho0 + tau1_inv*rho1`）——SC 双组分黏性耦合的标准做法，也是我方 MCMP 分层 Poiseuille 必须复刻的约定。
- 力在碰撞前算（`binary_shan_chen.mako:109-110`），两套力各自由 `force_couplings[(a,b)]='G12'` 驱动（三组分表见 `lb_ternary.py:165-170`）。
- 主循环 `ShanChenPrepareMacroFields` → `ShanChenCollideAndPropagate0/1`（一次核内碰撞+传播），AB 双缓冲对（`lb_binary.py:482-515`）。

**(3) 三组分 Shan-Chen** — `lb_ternary.py:154-333`（`G12/G13/G23`，`:165-170`，`:174-186`；伪势仍只有两种 `:209-213`）。

**(4) 自由能二元流体（Cahn–Hilliard 型）** — `lb_binary.py:139-373`
- 两套分布（流体 + 序参量）的平衡态由 sympy 符号推导：`sym_equilibrium.py:15-48`、`:50-71`。
  - 体自由能压强 `pb = ρ/3 + A(−φ²/2 + 3φ⁴/4)`（`sym_equilibrium.py:29`）
  - 化学势 `μ = A(−φ + φ³) − κ∇²φ`（`:55`）
  - κ 各向异性（把 `κ∂ₐφ∂_bφ` 塞进二阶矩）：`sym_equilibrium.py:38-41` + 稀疏系数 `wxx/wyy/wzz/wxy/wyz/wxz`（`lb_binary.py:198-268`）
  - 含 `λ=τ(1−3c_s²)` 非标准格子修正（`sym_equilibrium.py:26,35-36`），D2Q9/D3Q19（c_s²=1/3）自动为 0
- 参数 `Gamma`（迁移率）、`kappa`（界面张力）、`A`（势阱深度）、`tau_a/tau_b`、`model{bgk,mrt}`（`lb_binary.py:168-176`）。
- 润湿/接触角：`bc_wall_grad_phase` + 一阶/二阶 stencil（`lb_binary.py:161-167`），实现 `lb_binary_fluid.mako:170-255`（注释指 Kusumaatmaja 学位论文 p.18 "option 2"；半程反弹下仅支持一阶 `:219-220`）。
- 弛豫可切 MRT：`relaxation.mako:17` `FE_MRT_relaxate`。

**(5) 无的部分（明写，勿臆造）**

| 手法 | 状态 | 证据 |
|---|---|---|
| 自由面（VOF / 填充率 / 界面重构） | **无（STUB）** | `lb_single.py:221-239` 的 `LBFreeSurface` 只把平衡态换成浅水 `shallow_water_equilibrium`（`sym_equilibrium.py:73-88`），无填充率、无界面/gas 单元标记、无 `free_surface` 内核 |
| 相场 Allen-Cahn | **无** | `grep -i "allen\|phase.?field"` 空；序参量方程是 CH 型（`sym_equilibrium.py:55` 的 `−κ∇²φ`），非 AC 的 `f'(φ)` 双势阱驱动 |
| 颜色梯度 / CSF 表面张力 | **无** | 表面张力只通过自由能 `κ`（二阶矩）与 SC 的 `G12` 隐式给出 |
| 互作用势形式 | 2 种（linear / classic） | `sym.py:896-908` |
| 密度比能力 | **WITHHELD（无证据）** | 仓内无密度比验证；示例用 `1e-6` 当"空相"初值（`examples/binary_fluid/sc_poiseuille_2d.py:22-27`、`sc_laplace_2d.py:31-35`），但**未测稳态密度比**；Laplace 校验只打印 `Δp`（`sc_laplace_2d.py:64-73`），其 EOS 取 `p=(ρ_a+ρ_b)+Gρ_aρ_b`（`:70-71`，c_s=1 单位下 ψ=ρ 的 SC 状态方程） |
| spurious current 处理/度量 | **无** | `grep -i "spurious"` 空；`sc_laplace_2d.py` 只做 `Δp` 与 `σ/r` 的定性打印，不测界面最大寄生速度 |
| 力项加法差异 | 有（两范式） | SC：`a=F/ρ` 进速度并可切 `guo/edm/velocity_shift`（`shan_chen.mako:20-23`+`lb_base.py:325`）；自由能：压力张量并入平衡态二阶矩（`sym_equilibrium.py:32-41`） |

## 五、GPU / 并行

- **数据布局 = SoA（按方向分平面）**：每 lattice 一个平坦 buffer，长 `Q × N_active × 4 B`（`subdomain_runner.py:564-567`、`:904-910`）；索引 `dist_out[gi + dist_size*idir + neighbor_offset]`（`boundary.mako:34-39`）⇒ 同方向节点连续，**传播退化为整片平移、访存天然合并**。与我方按节点存 Q 分布的 AoS 相反，是本仓最值得吸收的"结构性知识"。
- **访问模式** `--access_pattern {AB, AA}`（默认 AB，`lb_base.py:61`）：AB = 双缓冲乒乓（`subdomain_runner.py:908-910`）；AA = 原地 + 读时传播/opposite slot（`propagation.mako:169-179`）。
- **优化开关**：`--propagate_on_read`（`lb_base.py:77`）、`--propagate_with_shuffle`（`:82`）；块内共享内存传播 `kernel_common.mako:169-189` + `propagation.mako:292`（`prop_{dir}` 共享数组 = `BLOCK_SIZE` 或 `(block+warp−1)/warp` 条）。
- **间接寻址（稀疏/多孔/任意几何）**：`--node_addressing indirect` 用 `nodes[dense_gi+offset]`（`boundary.mako:36`；`lb_binary.py:50-56,57-92` 的 `PPi` 签名；`subdomain_runner.py:898-901`）⇒ 只存活跃流体节点。
- **后端双实现**：OpenCL（`backend_opencl.py:15-241`：`cl.Buffer`、`enqueue_nd_range_kernel`、`ALLOC_HOST_PTR` 异步主机缓冲 `:95-115`）；CUDA/pycuda（`backend_cuda.py:34-332`：nvcc 选项、`EIGHT_BYTE_BANK_SIZE` `:98`、L1 开关 `:62`）。
- **分布式**：几何子域切分（`geo.py`、`subdomain.py`）+ 连接器抽象 `MPSubdomainConnector`（`connector.py:22`）、`ZMQSubdomainConnector`（`:73`）、`ZMQRemoteSubdomainConnector`（`:120`）、`CompressedZMQRemoteSubdomainConnector`（`:162`）；收发 `subdomain_runner.py:1065-1101`（`np.hstack` + `connector.send`）。
- **通信隐藏：弱**。`subdomain_runner.py:1088` 留着 TODO "Use non-blocking sends here?"，`_send_dists()` 紧随计算同步拷贝；无 CUDA-aware MPI、无 GPU 直连（节点间 TCP/execnet，`doc/distributed_simulations.rst:1-25`）⇒ 我方"分布式多卡"只能借架构，不能借性能。

## 六、可微 / 代码生成

- **代码生成**：sympy → C 打印器（`sym_codegen.py:206` `cexpr()`，`:173-205` 的 `_print_Pow/_print_Rational/_print_Mul` 规则），并有**多项式截断**（`:21-41` `_truncate_add`/`truncate_velocity`）与**因式化**（`:89` `poly_factorize`，减少 GPU 乘法）。"符号→截断→因式化→内核"是它当年性能不落后手写 CUDA 的核心。
- **可微性：无**。无 autograd/伴随/梯度接口；方程是"符号平衡态 + 显式 stencil"，要做可微必须在框架层重写。
- **现代环境不可运行（风险）**：`sym_codegen.py:11` `from sympy.printing.ccode import C99CodePrinter` 在现版 sympy 已移除 → 本机 `sympy 1.14` 下 `import sailfish.sym` 直接 `ModuleNotFoundError`（已复现）。加之 pycuda/OpenCL 2011 期 API 与 Python 2 期写法，**运行时复现成本高，只能读码**；这也是本报告用"独立重算其公式"替代"跑它的算例"的原因。

## 七、与我们功能点的映射（feature_ledger slug）

| slug | 本仓对应实现 | 映射结论 | 证据 |
|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | `examples/binary_fluid/sc_poiseuille_2d.py` | **adapt**：构型可直接照抄（H=256、黏度比 5、`G12=1.2`、`force_implementation='edm'`），但该例**不含解析剖面对比代码**，需我方自建解析解 | `sc_poiseuille_2d.py:14-55` |
| MCMP 高密度比 | 无直接实现（仅 2 种伪势、无 EOS 变体、密度比未验证） | **reference only** + NEW 提案 | `sym.py:896-908`；`sc_laplace_2d.py:64-73` |
| 自由面 | STUB（浅水壳） | **不可复用** → NEW 提案 | `lb_single.py:221-239` |
| 相场 Allen-Cahn | 无 | NEW 提案 | `grep` 空 |
| GPU 融合 kernel | 单核内完成碰撞+传播（`*CollideAndPropagate*`） | **reference**（我方是张量算子融合，不同层次） | `binary_shan_chen.mako:89-140` |
| 分布式多卡 | 子域 + ZMQ/execnet 连接器 | **reference**（架构借鉴，代码不搬；无通信隐藏） | `connector.py:22-161`；`subdomain_runner.py:1088` |
| 稀疏/多孔 | 间接寻址 `nodes[]` + 稀疏节点表 | **adapt 思路** | `boundary.mako:36` |
| 案例组织 | `examples/binary_fluid/` 17 个例（SC 8 + 自由能 6 + 波/分离） | **adapt**（例-验证一一对应的组织法） | `examples/binary_fluid/` |
| 测试与验证面 | `regtest/poiseuille.py`（解析解最大速度误差随黏度曲线）、`regtest/ldc_golden/`（Ghia 数据）、`regtest/sc_phase_sep.py`（G 扫描定 spinodal）、`tests/test_goldens.sh`（npz golden 比对） | **fits**（纯判据/流程，无代码耦合） | `regtest/poiseuille.py:64-90`；`regtest/sc_phase_sep.py:39-50`；`tests/test_goldens.sh:1-20` |
| CI | **无 CI 配置**（无 `.github/`、无 `.travis.yml`）；仅 `Makefile` `presubmit` 目标串起 test/regtest/goldens | **reference**（借"golden npz 对齐"思路） | `Makefile:6` |
| NEW: 润湿/接触角边界 | `bc_wall_grad_phase` 壁面 φ 梯度润湿 BC（一/二阶） | **adapt**（冰区/油水润湿可借） | `lb_binary.py:161-167`；`lb_binary_fluid.mako:170-255` |

## 八、可吸收 Top3 + 风险

**Top1｜自由能二元平衡态 + 压力张量闭包（最有价值）**
`sym_equilibrium.py:15-48` + `lb_binary.py:198-268`。我用独立实现复核（表⑦、图 3）：0/1/2 阶矩残差 4.44e-16（机械精度），证明闭包为
`P_ab = [p_b − κφ∇²φ]δ_ab + ρu_a u_b + κ(∂_aφ∂_bφ − ½|∇φ|²δ_ab)`，
其中各向异性系数满足 `Σw_xy e_x e_y = 1.0`、`Σw_xx e_x² = 0.5`、`Σw_yy e_x² = −0.5`（表⑥）——这组**精确有理数**可直接作为我方 GAP-LBM-MCMP 第二条路线（自由能/CH 型）的验收基准。
风险：LGPL ⇒ 只能 clean-room 重写（我方是 PyTorch 张量写法，重写成本可控）。

**Top2｜分层两组分 Poiseuille 验证夹具**
`sc_poiseuille_2d.py:14-55` + 速度加权约定 `binary_shan_chen.mako:78-82`。可直接作为我们 `GAP-LBM-MCMP-POISEUILLE` 的机器验收门构型（黏度比 5、`G12=1.2`、edm 力、半程反弹 + 壁后 unused 层）。
风险：该例只打印场量，无解析剖面对比 → 需我方自建分层解析解做 <3% 判定。

**Top3｜验证面范式：解析解误差曲线 + golden 场 + 参数扫描定相变点**
`regtest/poiseuille.py:70-78`（`max(v_sim)/max(v_th) − 1` 随黏度扫）、`regtest/ldc_golden/*_ghia`、`regtest/sc_phase_sep.py:39-50`（G∈[3,5.5] 扫描判 spinodal 位置）。三者都是**纯判据**，可零风险移植进 TensorLBM 的 pytest 验收门。

**风险总表**
1. **休眠 6.7 年 + 工具链腐化**（`sym_codegen.py:11` 与 sympy 1.14 不兼容已复现）⇒ 任何"它跑出来是多少"的结论都不可复现，只能读码 + 独立重算。
2. **LGPLv3 传染** ⇒ 一律 reference/adapt，禁止代码搬运；本报告的所有复用建议都带 clean-room 约束。
3. **无密度比 / 无 spurious current 证据** ⇒ 不得据此宣称"SC 能打高密度比"；高密度比功能点仍需自研（CS/P–R 势 / 稳定化力项）。
4. **无曲率反弹 / 自由面 / 相场** ⇒ 船舶结构类几何（真实曲面）与自由液面问题**不可借**，需自研或转向其他项目（Palabos/openlb 路线）。
5. 示例层的物理结论（如 Laplace 定律）只有"打印"，没有断言 ⇒ 不能当验收门用，只能当参考构型。

## 九、复核方法与误差判定

**复核手段**（`artifacts/oss_inventory/sailfish/probe_sailfish.py`，纯 stdlib + numpy/matplotlib，可重跑）：
1. 解析 `sailfish/sym.py` 拿到 D2Q9/D3Q13/D3Q15/D3Q19/D3Q27 的 basis 与有理权重（表④），做**精确**格子一致性检验。
2. 复刻 `lb_binary.py:198-268` 的各向异性系数生成过程，检查 κ 张量项的归一化（表⑥）。
3. 按 `sym_equilibrium.py:15-71` 逐项实现自由能两套平衡态，在 64 组随机宏量下检验 0/1/2 阶矩（表⑦，图 3）。
4. 按 `shan_chen.mako:45-84` 实现 SC stencil 力，与连续极限 `−G c_s²ψ∇ψ` 对比（表⑧，图 1），并检验**均匀场力恒为 0**（测得 0.00e+00）。

（表④原始值见 `probe_results.json` 的 `lattice` 字段；表⑥⑦⑧如下。）

**表⑥** κ 各向异性系数归一化（复刻 `lb_binary.py:198-268`）

| lattice | Σw_xy e_x e_y | Σw_xy e_x² | Σw_xx e_x² | Σw_yy e_x² | Σw_yz e_y e_z | Σw_xz e_x e_z |
|---|---|---|---|---|---|---|
| D2Q9 | 1.0 | 0.0 | 0.5 | -0.5 | 0.0 | 0.0 |
| D3Q19 | 1.0 | 0.0 | 0.5 | -0.5 | 1.0 | 1.0 |

**表⑦** 自由能平衡态矩复核（64 组随机宏量，`sym_equilibrium.py:15-71`）

| lattice | trials | |Σf−ρ| | |Σf e−ρu| | |Σf e e − P_ab| | |Σf_ord−φ| | |Σf_ord e−φu| |
|---|---|---|---|---|---|---|
| D2Q9 | 64 | 2.22e-16 | 6.94e-17 | 1.11e-16 | 1.11e-16 | 6.94e-18 |
| D3Q19 | 64 | 4.44e-16 | 5.55e-17 | 2.22e-16 | 1.11e-16 | 6.94e-18 |

**表⑧** SC stencil 力 vs 连续极限（`shan_chen.mako:45-84`）

| interface width W [a] | max|ΔF_y| | L2 rel. err [%] | peak rel. err [%] |
|---|---|---|---|
| 1.5 | 8.302e-03 | 9.57 | 9.71 |
| 2 | 3.278e-03 | 6.00 | 5.41 |
| 4 | 4.732e-04 | 1.56 | 1.50 |
| 8 | 5.965e-05 | 0.39 | 0.37 |

**误差判定（逐项，显式回答是否 <3%）**

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| 5 个格子的 `Σw−1`、`Σw·e`、`Σw e_ae_b−δ/3`、4 阶矩各向异性 | 精确有理数恒等式 | ≤ 1.67e-16 | **是**（机械精度） |
| 自由能流体/序参量平衡态 0/1/2 阶矩 | 解析闭包 `P_ab`（见 Top1） | ≤ 4.44e-16 | **是**（机械精度） |
| SC stencil 力 vs 连续极限（界面宽 W=8 a） | `−G c_s²ψ∇ψ` | 0.39% | **是** |
| SC stencil 力（W=4 a） | 同上 | 1.56% | **是** |
| SC stencil 力（W=2 a，≈多数 SC 模拟的界面厚度） | 同上 | 6.00% | **否**（收敛阶测为 a^-1.91） |
| 真实 CFD 结果 vs 实验/文献 | — | **未测** | **无结论**：工具链腐化（第六节），无法运行其算例；本报告不对其物理精度做任何声明 |

**局限**：①只做了"公式级复核"，未做"求解器级复现"；②D3Q15/D3Q27 的碰撞/边界路径未逐一深读（仅确认格子在 `sym.py` 与测试中出现）；③自由能模型的 MRT 版本（`relaxation.mako:17`）只定位未验证；④`utils/voxelizer`（183 文件、CGAL 体素化）未深盘，只登记其存在。

## 十、图

- **图 1** `fig1_shan_chen_force.png`：SC stencil 力 vs 连续极限（W=2 剖面）+ 晶格截断误差随界面厚的收敛（测得阶 -1.91）。
- **图 2** `fig2_coverage_matrix.png`：方法族覆盖矩阵（0=无/1=痕迹/2=部分/3=完整，格内标 `file:line` 证据），一眼看出"多相完整、自由面/相场/曲率反弹/可微为空"。
- **图 3** `fig3_formula_residuals.png`：自由能平衡态矩残差 + SC 均匀场力残差（对数轴），全部落到机器精度地板。

（说明：本任务为**代码级技术盘点**，物理算例不可运行，故不提供场量云图；图 1–3 是对本仓公式与晶格数据的可复现复核结果，属"计算结果"而非示意。）

[OSS-INV]
repo: sailfish-team/sailfish
lang: Python + OpenCL C / CUDA C (mako codegen)
license: LGPL-3.0 (declared README:3-4 / setup.py:51; no LICENSE file in repo root)
methods: D2Q9;D3Q13;D3Q15;D3Q19;D3Q27 | BGK;MRT(moment space);entropic(mirror-state);TRT only as MRT degenerate pxx/pxy | forces: guo;edm(shifted-velocity);velocity_shift;free-energy-pressure-tensor
multiphase: Shan-Chen pseudopotential single/binary/ternary (linear+classic psi) sailfish/templates/shan_chen.mako:45-84, sailfish/lb_binary.py:375-517, sailfish/lb_ternary.py:154-333; free-energy binary (Cahn-Hilliard) sailfish/sym_equilibrium.py:15-71 + sailfish/lb_binary.py:139-373; NO free-surface (stub shell sailfish/lb_single.py:221-239), NO Allen-Cahn, NO color-gradient/CSF, density-ratio + spurious-current unmeasured (WITHHELD)
boundary: half-way BB (node_type.py:115); full-way BB (:144); Zou-He (:229,:256); Guo density extrapolation (:222); regularized (:208,:269); TMS moving wall (:171); slip/Neumann/Yu-outflow (:402,:353,:336); immersed boundary (templates/ibm.mako:1-98); NO curved/interpolated BB
gpu: SoA per-direction contiguous slabs in flat Q*N buffer (subdomain_runner.py:564-567, boundary.mako:34-39); AA/AB access patterns (lb_base.py:61, propagation.mako:169-179); shared-memory in-block propagation (kernel_common.mako:169-189); indirect addressing for sparse domains (boundary.mako:36); OpenCL + CUDA(pycuda) backends; subdomain ZMQ/execnet exchange, no CUDA-aware MPI, no comm hiding (subdomain_runner.py:1088 TODO)
summary: 2011-2019 年 sympy→mako→GPU 内核生成型 LBM 框架，多相（SC 单/双/三组分 + 自由能二元）实现完整且公式经我方独立复核（矩残差 1e-16），是"符号推导→内核"与 SoA 数据布局的历史参照。仓已休眠 6.7 年、工具链腐化不可运行、LGPLv3 限制直接取用，且无自由面/相场/曲率反弹/密度比证据，故定位为 reference(读码+clean-room 重写)。
[/OSS-INV]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: GAP-LBM-MCMP-POISEUILLE
tag: two-component-sc-layered-poiseuille-edm
method: 两组分 SC 分层 Poiseuille：各自分布求 ρ_σ 与 ρ_σu_σ，用松弛时间加权合成速度场 u=Σ(ρ_σ/τ_σ)u_σ/Σ(ρ_σ/τ_σ)；SC 力 F=−G12ψ_a(x)Σw_i e_i ψ_b(x+e_i) 转为加速度后经 EDM(shifted velocity) 进平衡态；半程反弹壁 + 壁后一层 unused 节点；两套黏度比 5 的解析剖面即验证基准。
files: examples/binary_fluid/sc_poiseuille_2d.py:14-55, sailfish/templates/models/binary_shan_chen.mako:78-82, sailfish/templates/shan_chen.mako:45-84, sailfish/lb_binary.py:406-414
conventions: G12=1.2; visc1=visc2/5; force_implementation='edm'; grid='D2Q9'; H=256 通道; 密度以 1.0/1e-6 初始化（近似空相，非真实密度比）; u 的 τ 加权平均是 SC 双组分标准约定
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: GAP-LBM-MCMP-POISEUILLE
tag: free-energy-binary-equilibrium-pressure-tensor
method: 自由能二元模型的平衡态由符号推导：流体分布含体自由能压强 pb=ρ/3+A(−φ²/2+3φ⁴/4) 与 κ 各向异性修正（wxx/wyy/wzz/wxy/wyz/wxz），序参量分布含化学势 μ=A(−φ+φ³)−κ∇²φ；二阶矩闭包为 P_ab=[pb−κφ∇²φ]δ+ρu_a u_b+κ(∂_aφ∂_bφ−½|∇φ|²δ)，我方独立复核残差 1e-16。
files: sailfish/sym_equilibrium.py:15-48, sailfish/sym_equilibrium.py:50-71, sailfish/sym_equilibrium.py:55, sailfish/lb_binary.py:198-268
conventions: Σw_xy e_x e_y=1.0, Σw_xx e_x²=0.5, Σw_yy e_x²=−0.5（精确有理数）; λ=τ(1−3c_s²) 对 D2Q9/D3Q19 为 0; κ 项在 S.wi 乘法之外单独相加; Gamma=迁移率, kappa=界面张力, A=势阱深度
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: NEW:sc-pseudopotential-stencil-force-and-potentials
tag: shan-chen-explicit-stencil-forcing
method: 伪势 SC 力以显式 stencil 计算 F(x)=−Gψ(x)Σ_i w_i e_i ψ(x+e_i)（ψ 只取 linear 或 classic 指数形式），再除以 ρ 转成加速度进入碰撞；实测均匀场上该力恒为 0（无虚假体力），相对连续极限 −Gc_s²ψ∇ψ 的 L2 误差随界面厚以 a^-1.9 收敛（W=8a 时 0.39%，W=2a 时 5.99%）。
files: sailfish/templates/shan_chen.mako:45-84, sailfish/templates/shan_chen.mako:9-25, sailfish/sym.py:896-908
conventions: 耦合常数 G11/G12/G22（双组分）、SCG（单组分）; sc_potential ∈ {linear, classic}; classic 取 ρ0=1.0; 代码里未乘 c_s²，故等效耦合 G_eff=G/3，与文献 G 标度差 3 倍（迁移时须换算）
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: NEW:wetting-contact-angle-bc
tag: wall-phase-gradient-wetting-bc
method: 润湿边界通过壁面 φ 梯度参数 bc_wall_grad_phase 实现：半程反弹时用一阶 stencil、全程反弹时用一/二阶 stencil 把壁外虚拟 φ 设为 φ_wall−order*grad_phase，从而控制接触角；注释指 Kusumaatmaja 学位论文 p.18 option 2。
files: sailfish/lb_binary.py:161-167, sailfish/templates/models/lb_binary_fluid.mako:170-255, sailfish/templates/models/lb_binary_fluid.mako:219-220
conventions: bc_wall_grad_phase 默认 0.0（中性润湿）; bc_wall_grad_order ∈ {1,2}，半程反弹仅支持 1; 需壁后一层 unused 节点
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: 测试与验证面
tag: analytic-profile-error-curve-plus-golden-fields
method: 验证面三件套：(a) Poiseuille 解析剖面对比，对每档黏度算 max(v_sim)/max(v_th)−1 并画随黏度曲线；(b) Ghia 等 Re=100/400/1000 的 LDC golden 场按分量存 npz，逐例比对；(c) 扫描 G∈[3,5.5] 用密度场极值/序参量定位 spinodal 分解阈值（G≈4.0）。
files: regtest/poiseuille.py:64-90, regtest/sc_phase_sep.py:27-50, tests/test_goldens.sh:1-20, regtest/ldc_golden/re1000_x_ghia
conventions: 误差指标为"最大速度相对误差"而非全域 L2; golden 比对用 utils/compare_results.py 的 npz 逐字段容差; 奇数节点数使最大速度恰好落在节点上（lat_nx=127）
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: 稀疏/多孔
tag: indirect-node-addressing
method: 间接寻址：维护 nodes[] 稠密→稀疏映射，内核以 nodes[dense_gi+offset] 访问活跃流体节点，未激活节点不占存储也不参与计算；配套 subdomain 端只需给出 encoded_map 的稀疏节点表。
files: sailfish/templates/boundary.mako:34-39, sailfish/lb_binary.py:50-56, sailfish/subdomain_runner.py:898-901
conventions: 仅 node_addressing='indirect' 生效，内核参数签名多一个 PPi; 场量同样按稠密索引存; 与 AA/AB 访问模式正交
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: GPU 融合 kernel
tag: soa-per-direction-fused-collide-propagate
method: 每个 lattice 一个平坦 buffer 长 Q*N_active，按方向分片连续（idx=gi+q*N+offset），使传播成为整片平移；碰撞与传播融合在同一内核（*CollideAndPropagate*），跨方向部分用块内共享内存传播或 warp shuffle，另提供 AB 双缓冲与 AA 原地读时传播两种访问模式。
files: sailfish/subdomain_runner.py:564-567, sailfish/templates/boundary.mako:34-39, sailfish/templates/kernel_common.mako:169-189, sailfish/templates/propagation.mako:169-179, sailfish/templates/models/binary_shan_chen.mako:89-140
conventions: 默认 access_pattern='AB'（双缓冲）; propagate_with_shuffle 需支持 shuffle 的设备; 共享数组长度 BLOCK_SIZE 或 (block+warp−1)/warp
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: 分布式多卡
tag: subdomain-zmq-connector-exchange
method: 子域切分 + 连接器抽象（multiprocessing 共享数组 / ZMQ PAIR / 跨主机 ZMQ / blosc 压缩版）交换边界分布层；主从进程用 socket 交换端口与统计；集群通过 execnet(SSH) 或 PBS 启动每节点每 GPU 一个 runner。
files: sailfish/connector.py:22-161, sailfish/subdomain_runner.py:1065-1101, sailfish/subdomain_runner.py:1088
conventions: 边界交换层厚度=1（stencil 半径）; 无 CUDA-aware MPI，收发为同步阻塞（代码内 TODO）; 主机端 np.hstack 打包
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: sailfish-team/sailfish
slug: 案例组织
tag: example-per-physics-case-with-regtest-hook
method: 每个物理案例一个可执行脚本（binary_fluid/ 下 SC 与 free-energy 各成系列），脚本只声明几何/初值/边界/参数，物理模型由 sim 类装配；对应的 regtest 用同名参数覆盖（minimal PBT），使"例—验证"一一对应。
files: examples/binary_fluid/sc_poiseuille_2d.py:19-55, examples/binary_fluid/fe_poiseuille_2d.py:1-40, regtest/poiseuille.py:16-30
conventions: 参数集中在 update_defaults 的 defaults dict; 网格尺寸/黏度由例子给出，验证脚本只覆盖所需项
transfer: adapt
[/OSS-SOL]