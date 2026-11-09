# 【盘点】vivsim — JAX 浸入边界 IB-LBM 流固耦合（tier 2）

> 盘点锚点：`FEAT-OSS-INVENTORY-LBM` / mission 8293
> 对象：`haimingz/vivsim`，commit `fb84b12f02e18f3b9dfdf738d6a1a318d81539f9`（2026-06-28，"Fix shifted LBM equilibrium (#46)"）
> 读码方式：`git clone https://github.com/haimingz/vivsim.git` 成功，`git fetch --unshallow` 拿全 93 个 commit；**结论全部来自代码本体**，文档仅用于交叉核对（并发现一处文档超发，见 §8 风险 R4）。
> 证据级别声明：方法学结论全部为 **REAL / 源码已确认（source-confirmed）**；**没有一条是运行 vivsim 本体得到的**——审计沙箱内无 JAX 且无可访问的 PyPI（`pip install jax` → "no matching distribution"，集群 loong64 节点同样无 jax），而该仓**不带 test suite**，所以"跑它的代码"这条路走不通。为拿到可判定的数字，另做了两件事（产物在 `artifacts/oss_inventory/vivsim/`）：
> 1. `verify_ops.py`：把算子公式逐式转写为 NumPy，验证该仓**隐式依赖**的代数恒等式（48 项，48/48 通过，最大残差 2.2e-16）；
> 2. `poiseuille_recipes.py`：把该仓四种 collision/forcing recipe 逐式转写为 NumPy，按 `examples/2d/poiseuille_channel.py` 同一设置驱动到稳态，与解析解对比给出误差与收敛阶。
> 这两项是**公式级交叉验证（formula-level）**，不是对 vivsim 本体的执行；下文所有数字都标明来源。

---

## 一、概况

| 项 | 值 | 指针 |
|---|---|---|
| 语言 / 依赖 | Python ≥3.11 + JAX（`jax>=0.8`，cpu/cuda12/cuda13/tpu 四档 extras） | `pyproject.toml:1-24` |
| 许可证 | **MIT**（Copyright 2024 Haiming Zhu） | `LICENSE:1-3` |
| 规模 | 库 3275 行，examples 2783 行（examples ≈ Python 代码的 46%） | `find vivsim -name '*.py' \| xargs cat \| wc -l` |
| 版本 | 2.0.0 | `pyproject.toml:8` |
| 活跃度 | 93 commits，2024-12-12 起，末次 2026-06-28 | `git rev-list --count HEAD`；作者 `pyproject.toml:12` |
| 测试/CI | **无 tests 目录、无测试 CI**；`.github/workflows/` 只有 `clone.yml`/`view.yml` 两个刷流量徽章的定时任务 | `.github/workflows/clone.yml:1-10` |
| 定位 | 纯函数式 JAX **IB-LBM 流固耦合（VIV）**求解器，2D+3D，含多重直接力 IBM、四种碰撞模型、两族力项 | `README.md:14`、`pyproject.toml:6` |

**许可证对我们的可用性判断（MIT）**：可用。MIT 属最宽松一类——允许商用、修改、闭源再分发，含专利默示许可但**无明示专利条款**（常规风险）；唯一义务是**在源码或二进制中保留版权声明与本许可全文**，且不得用作者名义为我们的衍生品背书。对我们的具体含义：

- TensorLBM 若**移植/改写**其代码（例如把 `multi_direct_forcing` 的 MDF 迭代搬到我们的张量后端），只需在新文件头保留 "Portions derived from vivsim (c) 2024 Haiming Zhu, MIT"，并在仓库第三方声明清单登记，**不触发 copyleft**。
- 若只是**借鉴方法/公式**（我们绝大多数吸收点属此类），法务上更无负担，但仍应给出处（学术诚信）。
- 注意：MIT 不覆盖 `assets/*.gif|png`（论文配图性质素材），复用需单独确认。

---

## 二、方法谱系

### 2.1 格子（velocity set）

| 格子 | 位置 | 说明 |
|---|---|---|
| D2Q9 | `vivsim/lbm/lattice.py:39-110` | `Lattice` NamedTuple（`:23-37`）承载 `w`(`:43`)、`c`(`:44-57`)、`opp_dirs`(`:63`)、四向 `*_dirs`(`:58-62`)、以及**逐边界 `BoundarySpec`**(`:64-109`) |
| D3Q19 | `vivsim/lbm3d/lattice.py:39-126` | 权重 `:43-49`、速度集 `:50-60`、`opp_dirs` `:61-64`、`BoundarySpec`（含 `zero_dirs` 分组）`:65+` |

没有 D2Q5/D3Q15/D3Q27（全仓 `grep "D3Q27\|D3Q15"` 零命中）。**格子与边界索引分组是"数据驱动"的**：`BoundarySpec` 把 wall 切片 / 相邻流体线 / in_dirs / out_dirs / tan_dirs / 法向轴 / 法向符号显式表列化，所有边界算子只按 spec 索引，不硬编码方向号——这是值得抄的组织方式（§8 Top1）。

### 2.2 分布函数表示：**零中心（shifted）约定**（本仓最需警惕的一点）

f 里存的不是 `f_i`，而是 `f_i - w_i`：

- 宏观密度 `rho = 1 + Σ f`（`vivsim/lbm/basic.py:107`），速度按方向分组差商（`:109-110`）；
- 平衡态末尾减 1：`feq = w*(rho*(1+3uc+4.5uc²-1.5u²) - 1)`（`basic.py:134-136`）；
- 边界/力项常数项因此被"吸收进 w 的和"（例：`_helpers.py:117-127` 的 `1 + (...)`）。

NumPy 逐式验证其自洽（`out/verify_ops.json`，48/48）：`Σfeq = rho-1`（1.1e-16）、**`Σ c·feq = rho·u` 是恒等而非近似**（4.5e-17；因 D2Q9 三阶矩为零，u² 项对动量无贡献）、BGK 与 w-位移可交换（7.5e-17）、NEBB 的 shifted 形式 == 全量形式 − w（2.5e-17）。
**陷阱**：同一份 NEBB 代码在标准（非 shifted）约定下会因常数项错位而给出错的壁面密度，反向亦成立。只抄公式不抄约定会静默出错。附带发现：**shifted 表示下直接由 f 算应力会缺 `cs²·I`**（已验证 `Π = ρ(uu+cs²I) − cs²I`），这对"由 f 直接积分壁面力"的代码是硬坑。

### 2.3 碰撞

| 模型 | 位置 | 实现要点 |
|---|---|---|
| BGK / SRT | `vivsim/lbm/basic.py:140-158` | `(1-ω)f + ω feq`；`ω = 1/(3ν+0.5)`（`:161-174`） |
| MRT | `vivsim/lbm/collision/mrt.py:66-88` | **预计算矩阵**路线：`M`(`:10-22`)、`M_INV = inv(M)`(`:25`)、`S(ω)=diag(0,1.4,1.4,0,1.2,0,1.2,ω,ω)`(`:44`)、算子 `M_INV@S@M`(`:63`)、应用 `f + einsum(op, feq-f)`(`:88`) |
| KBC | `vivsim/lbm/collision/kbc.py:12-64` | 熵稳定器显式实现：`fneq` 拆 shear/high-order（`:39-49`），`full_feq = feq + w` 把 shifted 还原成完整 f 再算熵内积（`:51-56`），`half_gamma` 解析式（`:59`） |
| Regularized | `vivsim/lbm/collision/reg.py:4-49` | D2Q9 闭式二阶矩投影：`π_xx/π_yy/π_xy` 由 `fneq` 组合（`:22-24`），按 Hermite 基重构 `fneq_reg`（`:34-46`） |

无 TRT、无 recursive-regularized、无 entropic MRT。3D 全部平铺复制一套（`vivsim/lbm3d/collision/{kbc,mrt,reg}.py`），其中 3D reg 额外抽出 `get_second_order_projection`（`lbm3d/collision/reg.py:36`）而 2D 未抽——实现不对称，是后续漂移的种子（R3）。

### 2.4 力项

| 方案 | 位置 | 要点 |
|---|---|---|
| Guo 力项谱 | `vivsim/lbm/forcing/guo.py:10-33` | `S_i = w_i[3(c-u)+9(uc)c]·g`；已验证 `ΣS=0`、`ΣcS=g`（残差 2e-18） |
| Guo + BGK | `forcing/guo.py:38-57` | 乘 `(1-0.5ω)`（`:57`） |
| Guo + MRT | `forcing/guo.py:62-79,82-107` | 预计算 `M⁻¹(I-0.5S)M`（`:79`），施加为 tensordot（`:107`）——**forcing 也做成常数矩阵**，可抄的性能技巧 |
| EDM（位移速度） | `forcing/edm.py:4-31` | 注释明说等价于 `feq(u+g/2ρ)-feq(u-g/2ρ)`，实现复用 Guo 谱（`:27-31`） |
| 速度修正 | `basic.py:177-196` | `g·0.5/ρ`，供 Guo 路线在碰撞前修正 u |

`examples/2d/poiseuille_channel.py:77-148` 把四族组合写成四个 `update_step_*`（`:81,:97,:113,:129`），并给出**何时修正速度**的规则（EDM：碰撞后用未修正 u；Guo：碰撞前修正，见 `:83-85`、`:99-101` 注释）——这段"组合配方说明"比代码本身更值钱。

---

## 三、边界处理

| 类型 | 位置 | 实现要点 |
|---|---|---|
| Bounce-back（含运动壁） | `lbm/boundary/bb.py:15-53` | 用**迁移前**的 `f_before_stream` 取壁面 slice（`:44-46`），只重建三条 in-dir（`:48-50`）：`f_out+2/3 un`、`f_out+1/6(un±ut)`；常数已是 shifted 形式 |
| Specular reflection（滑移） | `bb.py:56-95` | 与 BB 同构，仅把两条对角 in-dir 交换（`:92-94`） |
| **Mask 局部 bounce-back** | `bb.py:98-110` | `f.at[:,mask].set(f[:,mask][OPP_DIRS])` 一行，支持任意布尔几何；3D 同（`lbm3d/boundary/bb.py:56`） |
| NEBB（Zou-He 型） | `lbm/boundary/nebb.py:20-58` | 壁密度由 `_helpers.py:117-127` 反算；三个 wrapper（velocity/pressure/**force-corrected**）在 `_helpers.py:184-240` 组合；运动壁力修正 `get_corrected_wall_velocity`（`_helpers.py:156-181`） |
| NEE（非平衡外推） | `lbm/boundary/nee.py:19-60` | 壁面 = 壁平衡态 + 邻点非平衡外推（`:50-60`） |
| 纯平衡态壁 | `lbm/boundary/eq.py:25-56` | 丢非平衡部分（`:55-56`）；作者自述精度最低（`:7`） |
| 特征（无反射出口） | `lbm/boundary/cbc.py:14-53`；3D `lbm3d/boundary/cbc.py:16-51` | 取边界内 3 线（`:8-11`），按 `cs=1/√3` 做 LODI 型推进（`:33-53`） |
| 周期 | streaming 自带 wrap | `basic.py:60-85` |
| **曲率/贴体曲面边界** | **无** | 曲面几何全部交给 IBM 的 delta 函数扩散界面，无 sharp-interface 曲面 BC、无曲线壁插值格式 |

**IBM（本仓主菜）**

- 核：Peskin 3pt、4pt、cosine 4pt（`ib/kernels.py:4-14, 25-35, 46-57`）。已验证三者单位分解 `Σφ=1`（残差 ≤1.1e-16）且非负。
- 模板：**张量积 4×4 = 16 点/标记**，一次性预计算 `(weights, flat_indices)`（`ib/stencil.py:7-51`，`stencil_radius=2`）；3D 为 4×4×4=64 点（`ib3d/stencil.py:7+`）。
- 插值/散布：`einsum("ms,cms->cm")` 与 `add.at` 等价的扁平散播（`stencil.py:54-78, 81-110`）。已验证二者**互为伴随**（`<Iu,v>=<u,I^T v>`，残差 2e-16）且保常数。
- MDF：`ib/mdf.py:10-64`，`lax.fori_loop` 迭代 `n_iter`（默认 5），每次 `ΔF = 2·ds·(u_target-u_interp)`（`:47`）、散布到网格（`:49`）、把网格速度增量插回标记（`:51`），末了 `grid_force = spread(total)`、`marker_reaction = -total`（`:60-64`）。`ds` 由几何模块给（2D 多边形面积/弧长 `ib/geometry.py:6-43`；3D 三角面片面积/体积 `ib3d/geometry.py:6-49`）。
- **工程关键点**：只在包围盒内做 IBM——`jax.lax.dynamic_slice` 取 `IB_SIZE×IB_SIZE` 局部区域，算完 `dynamic_update_slice` 写回（`examples/2d/vortex_induced_vibration.py:107-108,140-141`）；`examples/2d/flow_pass_cylinder.py:59-70` 进一步**把 stencil 预计算到该局部坐标系**，运行期不再重算。3D 同样（`examples/3d/flow_past_sphere.py:110-111`）。

---

## 四、多相 / 多组元（重点）

**结论：无。本仓是单相、等温、弱可压 LBM，不含任何多相/多组元机制。**

检索证据（`grep -rn -iE "multiphase|shan|chen|phase.field|free.surface|color.grad|pseudo.potential|allen|cahn|vof" --include=*.py`）：**零命中**（唯二形似命中是 `descriptor`/`discrete` 里的 "scr" 子串，人工核对后排除）。

| 手法 | 本仓 | 说明 |
|---|---|---|
| SCMP / Shan-Chen 伪势 | 无 | 无伪势 ψ、无互作用力、无有效质量 |
| MCMP 两组元 | 无 | 分布函数单一套 `(q,NX,NY)`，无第二组分容器 |
| free-energy / 相场 Allen-Cahn / Cahn-Hilliard | 无 | 无序参量场、无界面宽度梯度项 |
| 颜色梯度 | 无 | 无 recoloring / 界面张力算子 |
| free-surface（VOF/质量跟踪） | 无 | 无 fill fraction、无界面重构 |
| 密度比能力 | **N/A** | ρ 只是弱可压声学扰动：稳态 Poiseuille 实测 `ρ∈[0.999986,1.000062]`（`out/poiseuille_recipes.json`），量级 1e-4 |
| spurious current 处理 | 无 | 无界面即无 spurious current；无可报告数值 |

因此 §7 中 `GAP-LBM-MCMP-POISEUILLE`、MCMP 高密度比、自由面、相场 Allen-Cahn 四个功能点**不能从本仓取"实现方式"**，只能取"骨架"（§8 Top2 与 OSS-SOL 第 1 条的 transfer=adapt 及其 caveat）。任何声称本仓有 SC/相场实现的写法都是编造。---

## 五、GPU / 并行

- **数据布局：AA-pattern（结构体维放 leading axis）**——`f` 形状 `(9,NX,NY)` / `(19,NX,NY,NZ)`，分量维在前、空间维连续（`basic.py:68`、`lbm3d/basic.py` 同）。与 XLB/JAX-CFD 一致，便于对空间维做 contiguous 融合。
- **访存/融合**：靠"纯函数 + 静态 slice"喂 XLA。streaming 明确**不用 `jnp.roll`**，而是 9 条静态 concatenate（`basic.py:60-85`，注释 `:63-66` 说明"让工作对 XLA 可见、成为简单 concat 而非通用 roll"）；3D 同理 19 条（`lbm3d/basic.py:52-88`）。全程无手写 CUDA/OpenMP。
- **时间循环融合**：`@partial(jax.jit, static_argnums=1, donate_argnums=0)` + `jax.lax.scan`（`examples/2d/poiseuille_channel.py:150-170`；`examples/2d/vortex_induced_vibration.py:151-157`），`donate_argnums` 消除每步 f 拷贝。
- **分布式：有，但只到 halo 交换**。`vivsim/multidevice.py:13-38`（全文 39 行）用 `jax.lax.ppermute` 沿设备 mesh 轴做**周期性**边界粒子输运：y 向 `:31-32`、x 向 `:35-36`，域按 x/y 均分（`:4-6` 注释）。**没有** MPI、没有 `shard_map`/`NamedSharding`、没有计算-通信重叠（ppermute 阻塞 exchange）。属"最小可用分布式"，不是可扩展多卡方案。
- **网格细化（不是多重网格）**：`vivsim/multigrid.py` 实为**块结构 2:1 局部加密**——`init_grid` 建块+buffer 层（`:21-55`）、`fine_to_coarse` 四点平均（`:58-98`）、`coarse_to_fine` 直接复制（`:100-130`）、按 level 重算 ω 的公式 `ω = 2ω₀/(2^{l+1}+(1-2^l)ω₀)`（`:133-147`）、坐标→局部索引（`:150-171`）。只有 2D，且 **examples 无任何案例调用它**（`grep multigrid examples/` 零命中）——属未验证代码。
- **性能测量面**：`examples/benchmark.py:61-102` 的 `measure()` 做了 warmup(10)+repeat(200)+`block_until_ready`（`:63,74,79`），逐算子计时；`examples/benchmark3d.py` 同构。README 给了 RTX4090 上 1024² 与 128³ 的算子耗时表（`README.md:163-215`）——**我们无法复核这些数字**（无 GPU、无 JAX）。

---

## 六、可微 / 代码生成

- **autograd 友好度：结构上友好，实践上未用**。全库纯函数、无模块级可变状态、`jit` 之外无副作用；状态以 `carry` 传递（`poiseuille_channel.py:152-156`），因此 `jax.grad` 原则上可穿透整个时间循环（README `:14` 声称支持 AD）。但**仓内 `grep "jax.grad\|jacfwd\|jvp\|vjp"` 零命中**——没有一例求导/伴随用例，也无梯度验证。判定：**能力可及、未兑现**。
- **符号推导 / kernel 生成：无**。无 sympy、无 devito/loopy/firedrake 类 DSL；"代码生成"完全由 XLA 承担。唯一的"预计算"是**常数算子矩阵**：`M_INV@S@M`（`collision/mrt.py:63`）、`M⁻¹(I-0.5S)M`（`forcing/guo.py:79`）在 setup 期算好，运行期只做一次 tensordot——可直接照搬的"算子不重算"模式。
- **FSI 耦合可微性**：耦合是**固定点迭代**（`FSI_ITER` 子迭代，`examples/2d/vortex_induced_vibration.py:70,114`；cylinder `IB_ITER=3`，`flow_pass_cylinder.py:60`），未做 Newton，也无隐式伴随。对"可微 FSI / 形状优化"目标，本仓不提供现成路径。

---

## 七、与我们功能点的映射

对照表（`feature_ledger` 实时查询 2026-11-09：LBM 域现存仅 4 条——`GAP-LBM-MCMP-POISEUILLE`、`GAP-LBM-SPHERE`、`FEAT-OSS-INVENTORY-LBM`、`H2-FRAZIL-RHEOLOGY`；**性能类/平台类 slug 不存在**，故按规则提 NEW）。

| 功能点（slug） | 本仓有无 | 实现方式（指针） | transfer |
|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | **部分**：单相分层通道驱动 + 力修正壁 + 解析对标骨架齐全；**两组分/界面条件完全没有** | 四 recipe 驱动 `examples/2d/poiseuille_channel.py:77-148`；力修正 NEBB `_helpers.py:156-181`；解析对比 `:176-186` | **adapt**：抄"驱动-力项-壁面-解析对标"骨架，界面/两组分自建 |
| MCMP 高密度比 | **无** | — | 不可映射（勿编造） |
| 自由面 | **无** | — | 不可映射 |
| 相场 Allen-Cahn | **无** | — | 不可映射 |
| `GAP-LBM-SPHERE`（曲面边界力法+球绕流） | **部分**：3D 扩散界面 IBM + 球绕流算例；无 sharp 曲面力法、**无 Cd 定量验证** | 3D stencil `ib3d/stencil.py:7+`；三角面片几何 `ib3d/geometry.py:6-49`；球算例 `examples/3d/flow_past_sphere.py:108-169`（Re=2000，仅出图，全文无 `CD_REF`） | **adapt**：IBM 力法可作"力法"候选实现之一；定量对标要我们补 |
| `NEW:LBM-GPU-JAX-FUSION`（GPU 融合 kernel） | 有 | 纯函数+`jit(donate)`+`scan`：`poiseuille_channel.py:150`、`viv.py:151-157`；静态 slice streaming `basic.py:60-85`；AA-pattern `(q,NX,NY)` | **fits** |
| `NEW:LBM-MULTIDEVICE-PPERMUTE`（分布式多卡） | 有（最小版） | `multidevice.py:13-38` | **reference** |
| `NEW:LBM-POROUS-MASK-BB`（稀疏/多孔/复杂几何） | 有 | mask BB `bb.py:98-110`；文字光栅化成 mask 的用法 `examples/2d/flow_through_text.py:78` | **fits** |
| `NEW:LBM-BLOCK-REFINEMENT-2TO1` | 有（未验证） | `multigrid.py:21-171` | **reference** |
| `NEW:LBM-IBM-MDF-STENCIL`（IBM 多重直接力） | 有（2D+3D） | `ib/mdf.py:10-64`、`ib/stencil.py:7-110`、`ib/kernels.py:4-57` | **adapt** |
| `NEW:LBM-CASE-ORG-EXAMPLES`（案例组织） | 有 | 一文件=一案例；`step(f)` 纯函数 + `update_chunk` scan；参数块集中顶部（`poiseuille_channel.py:33-70`） | **fits** |
| `NEW:LBM-VERIFICATION-SURFACE`（测试与验证面） | **弱** | 有解析/文献对标**但只 print 不 assert**：`flow_pass_cylinder.py:197-213`（Cd/Cl 对 3 篇 DOI 文献的 `CD_REF=1.389`/`CL_REF=0.718`，`:80-86`）；`poiseuille_channel.py:176-186`（解析剖面，仅画图）；`couette_flow.py:96`、`taylor_green_vortex.py:142` 打印 rel-L2。**无 pytest、无容差门、无 CI** | **reference（反面样例为主）** |
| `NEW:LBM-CI-CORRECTNESS`（CI） | **无** | `.github/workflows/` 仅流量徽章 | 不可映射 |
| `NEW:LBM-AUTODIFF-READY-DESIGN` | 结构有、用例无 | 纯函数 + carry；仓内无 AD 用例 | **adapt** |

---

## 八、可吸收 Top3 + 风险

### Top1 —— `BoundarySpec` 表驱动边界 + 常数算子预计算（→ TensorLBM 求解器骨架）

把「每条边界的 wall 切片 / 邻线 / in_dirs / out_dirs / tan_dirs / 法向轴与符号」写成声明式数据（`lbm/lattice.py:64-109`、`lbm3d/lattice.py:65-126`），边界算子只按 spec 索引；配合 MRT 的 `M⁻¹SM` 与 MRT-forcing 的 `M⁻¹(I−0.5S)M` 预计算（`collision/mrt.py:63`、`forcing/guo.py:79`）。吸收后：新增 3D 边界/新碰撞模型只加数据与矩阵、不改内核；每步只做 tensordot。**验收方式**：与现有硬编码实现对同一算例逐位比对（1e-14 容差），并跑我们的守恒量测试。

### Top2 —— 局部包围盒 IBM（MDF + 预计算 stencil）（→ VIV/球绕流能力）

三重组合值得整体吸收：(a) 张量积 4×4（3D 4×4×4）stencil **一次性预计算** weights+flat indices（`ib/stencil.py:7-51`）；(b) MDF 用 `fori_loop` 迭代、`ΔF=2·ds·(u_t−u)`、伴随一致的 spread/interpolate（`ib/mdf.py:47-64`，伴随性已验证 2e-16）；(c) **只更新包围盒**——`dynamic_slice`/`dynamic_update_slice`（`viv.py:107-108,140-141`），把 IBM 代价从 O(全网格) 降到 O(包围盒)。这是把 IBM 从"能算"变成"能长跑"的关键工程点；`ds` 归一化（`ib/geometry.py:6-43`、`ib3d/geometry.py:6-49`）直接可用。

### Top3 —— 案例即纯函数 + 连续误差打印的验证习惯（→ 案例组织/验证面）

每个 `examples/*.py` 只做三件事：参数块、`step(f)` 纯函数、`scan` 跑；验证段把误差**算成数字打印**（`flow_pass_cylinder.py:197-213` 连参考文献 DOI 一起写在源码里 `:80-86`）——"零手抄参考值"的好习惯。**但必须补上缺的那半**：把 print 改成 assert+容差门（R1），否则验证面不可机器裁决。

### 风险 / 陷阱（吸收时逐条防）

- **R1 验证不可裁决**：全仓无 test、无 assert、无 CI 正确性门；对标值只 print（`flow_pass_cylinder.py:199-213`）。照搬其"验证风格"会让我们的门禁形同虚设 → 吸收时一律落成 pytest + 容差并进 CI。
- **R2 零中心约定是隐式契约**：`rho=1+Σf`（`basic.py:107`）、`feq−1`（`:135`）、边界常数 `1+`（`_helpers.py:117`）。抄任一公式到标准约定会静默出错；由 f 直接算应力还缺 `cs²I`（已数值验证）→ 移植必须成组迁移 + 约定断言测试（`verify_ops.py` 可作模板）。
- **R3 3D 是 2D 的复制粘贴**（`lbm/` 1277 行 vs `lbm3d/` 1000 行，边界/碰撞/力项文件一一对应），二者已开始漂移（3D 抽了 `get_second_order_projection`、2D 没有）→ 我们不要这种双份维护，用维度泛化 spec + 单套算子。
- **R4 文档超发**：`README.md:159` 声称实现 "Peskin's 2-, 3- and 4-point kernels"，代码只有 3pt/4pt/cosine-4pt（`ib/kernels.py:4,25,46`），**没有 2 点核** → 以代码为准，勿以 README 为清单。
- **R5 未验证模块**：`multigrid.py` 无算例调用；`multidevice.py` 仅 39 行、无重叠通信；3D IBM 只有出图无 Cd 对标 → 只能当思路参考，不可当可用实现。
- **R6 "可微"是宣传而非能力**：无一处 `jax.grad`。若战役目标是可微 FSI，本仓只能提供结构友好性。
- **R7 JAX 版本门槛**：`jax>=0.8`（`pyproject.toml:17`）——与我们现有后端版本不一致会撞 API（如 `precision='highest'` 的 einsum 用法，`collision/mrt.py:88`、`ib/stencil.py:78`）。

---

## 附：本次可执行验证的结果（零手抄，全部程序化产出）

**产物路径**（worktree 内，可复跑）：
- `artifacts/oss_inventory/vivsim/verify_ops.py` → `out/verify_ops.json`（48 项代数检查）
- `artifacts/oss_inventory/vivsim/poiseuille_recipes.py` → `out/poiseuille_recipes.json`（四 recipe + 网格收敛）
- `artifacts/oss_inventory/vivsim/mkfigs.py` → `figs/fig1_profiles.png`、`figs/fig2_convergence.png`、`figs/fig3_coverage.png`

**表 1 · 算子代数恒等式（NumPy 逐式转写，48/48 通过）**

| 检查项 | 残差 | 判据 |
|---|---|---|
| D2Q9/D3Q19 `Σw=1`、`c[opp]=−c`、二阶矩 `=cs²δ`、D3Q19 四阶矩各向同性 | ≤1.1e-16 | 1e-15 |
| `Σfeq = ρ−1`（shifted 自洽） | 1.1e-16 | 1e-14 |
| `Σc·feq = ρu`（**恒等**，非近似） | 4.5e-17 | 1e-14 |
| `shifted Π = ρ(uu+cs²I) − cs²I`（**缺 cs²I 的坑**） | 8.2e-17 | 2e-4 |
| BGK 与 w-位移可交换 | 7.5e-17 | 1e-15 |
| Guo 力项 `ΣS=0` / `ΣcS=g` | 2.0e-18 / 3.5e-18 | 1e-17 / 2e-3 |
| MRT `M` 行正交、行 0/3/5 = 质量与动量泛函、`S` 只把 ω 给剪切矩 | 0 | 1e-12 |
| MRT（全同速率）在非平衡子空间退化为 BGK | 3.3e-16 | 1e-12 |
| NEBB shifted 形式 == 全量形式 − w（四边）、壁密度 `1+dev` == 全量和 | 2.5e-17 / 0 | 1e-12 / 1e-13 |
| Peskin 3pt/4pt/cosine 单位分解与非负性；IB stencil 权重和 1 | ≤1.1e-16 | 1e-14 |
| interpolate/spread 互为伴随、保常数 | 2.0e-16 | 1e-12 |

**表 2 · 四种 collision/forcing recipe 的 Poiseuille 精度（NY=10, ν=0.2, Gx=1e-3，与仓内 example 同设置；基准=解析抛物剖面 `u=Gx/(2ν)y(H−y)`）**

| recipe | 迭代步（至 max\|Δu\|<1e-13） | 相对 L2（内点） | max\|err\|/u_max | ρ 范围 |
|---|---|---|---|---|
| BGK+EDM | 1300 | **7.365%** | 7.798% | [1.000000, 1.000000] |
| BGK+Guo | 1300 | **7.365%** | 7.798% | [1.000000, 1.000000] |
| MRT+Guo | 1200 | **6.026%** | 6.745% | [0.999986, 1.000062] |
| KBC+EDM | 1300 | **6.917%** | 7.464% | [0.999993, 1.000021] |

BGK+EDM 与 BGK+Guo 在四位有效数字上完全相同——与该仓 `forcing/edm.py:12-15` 的"二者等价"注释一致（这是等价的**独立数值佐证**）。

**表 3 · 网格收敛（BGK+EDM，保持 u_max=0.05，即 `Gx ∝ H⁻²`）**

| NY | Gx | u_max | 相对 L2 | 步数 |
|---|---|---|---|---|
| 10 | 9.877e-4 | 0.0523 | 7.362% | 1300 |
| 20 | 2.216e-4 | 0.0505 | 1.708% | 4900 |
| 40 | 5.260e-5 | 0.0501 | 0.411% | 19300 |

**观测收敛阶 2.11 / 2.05**，与 NEBB + 力修正壁的**二阶**预期一致。

> 方法学自纠：首次收敛试验保持 Gx 不变，得到"阶 0.33"的假结论——因为 `u_max = Gx·H²/(8ν)` 随 H² 增长，NY=80 时 u_max≈3.9 早已越出 LBM 有效域。改为固定 u_max 后阶恢复 2.0 附近。这个坑本身值得记（我们做网格收敛研究时同样须知）。

**误差判定（逐项回答"是否 <3%"）**

| 关键量 | 基准 | 本次值 | 误差 | <3%？ |
|---|---|---|---|---|
| Poiseuille 剖面 L2（NY=10，仓内 example 默认分辨率） | 解析解 | 7.365% | 7.365% | **否**（格式本身二阶正确，但默认分辨率只有 H=9，太粗；要 <3% 需 NY≥20） |
| Poiseuille 剖面 L2（NY=20） | 解析解 | 1.708% | 1.708% | **是** |
| Poiseuille 剖面 L2（NY=40） | 解析解 | 0.411% | 0.411% | **是** |
| 收敛阶 | 理论 2 | 2.11 / 2.05 | 5.5% / 2.5% | **是** |
| 算子代数恒等式 48 项 | 精确恒等 | 最大残差 2.2e-16 | — | **是**（判据 1e-12） |
| 3D IBM 球绕流 Cd | 无基准（仓内未给 `CD_REF`） | 未运行（无 JAX） | — | **无法判定**（WITHHELD：无基准也无执行能力，不编数） |
| 2D 圆柱 Re=100 Cd/Cl | 仓内引用值 1.389 / 0.718（3 篇 DOI） | 未运行（无 JAX） | — | **无法判定**（WITHHELD） |

**必须声明的局限**：①未运行 vivsim 本体（无 JAX），表 2/3 是对其**公式**的 NumPy 转写，存在转写误差风险（已用二阶收敛与"EDM≡Guo"两条独立性质交叉校验来降低该风险）；②`multigrid.py`/`multidevice.py`/3D IBM 三块**未做任何验证**；③结论只覆盖 commit `fb84b12`。

---

[OSS-INV]
repo: haimingz/vivsim
lang: Python (JAX)
license: MIT
methods: D2Q9;D3Q19;BGK;MRT(矩阵预计算);KBC(熵稳定);Regularized(闭式二阶矩投影);Guo-forcing(BGK/MRT);EDM(=Guo等价);Peskin-3pt/4pt/cosine4pt;MDF多直接力;NEBB;NEE;bounce-back(含运动壁);specular滑移;mask-bounce-back;特征出口;周期
multiphase: 无（全仓无 SC/MCMP/自由面/相场/颜色梯度，多相关键词 grep 零命中；单相弱可压，ρ扰动~1e-4）
boundary: bounce-back(bb.py:15-53)/specular(bb.py:56-95)/mask-BB(bb.py:98-110)/NEBB(nebb.py:20-58)/NEE(nee.py:19-60)/equilibrium(eq.py:25-56)/characteristic(cbc.py:14-53)/periodic(streaming wrap, basic.py:60-85)/IBM 扩散界面(ib/mdf.py:10-64);无曲率贴体曲面BC
gpu: JAX纯函数+XLA；AA-pattern (q,NX,NY)；静态slice streaming 代 jnp.roll(basic.py:60-85)；jit(donate_argnums)+lax.scan 融合时间循环(poiseuille_channel.py:150-170)；多设备仅 jax.lax.ppermute 周期 halo 交换(multidevice.py:13-38, 39行)；块结构 2:1 网格细化未验证(multigrid.py)
summary: MIT 的 JAX 纯函数式 IB-LBM 流固耦合库（2D D2Q9 / 3D D3Q19，BGK/MRT/KBC/Regularized × Guo/EDM，完整边界族 + Peskin+MDF 扩散界面 IBM），工程上最有价值的是 BoundarySpec 表驱动边界、常数算子矩阵预计算与包围盒内 IBM。它完全没有多相/多组元能力，也没有可执行的验证面（无 test、无 assert、无 CI），且采用零中心分布约定，公式不可单独摘抄。
[/OSS-INV]

[OSS-SOL]
repo: haimingz/vivsim
slug: GAP-LBM-MCMP-POISEUILLE
tag: force-corrected-nebb+body-force-driver-scaffold
method: 通道 Poiseuille 驱动骨架——常体力 g 加在 Guo/EDM 力项上，壁面用「力修正半格」NEBB（把壁面格点速度先减去 g/(2ρ) 再代入 NEBB），四组 collision/forcing recipe 并列可切换，末段与解析剖面 u=Gx/(2ν)y(H−y) 对比。不含任何两组分/界面力，分层 MCMP 需自加伪势互作用与界面条件。
files: examples/2d/poiseuille_channel.py:77, examples/2d/poiseuille_channel.py:81, examples/2d/poiseuille_channel.py:97, examples/2d/poiseuille_channel.py:129, examples/2d/poiseuille_channel.py:176, vivsim/lbm/boundary/_helpers.py:156, vivsim/lbm/forcing/edm.py:4, vivsim/lbm/forcing/guo.py:38
conventions: 格子单位；ω=1/(3ν+0.5)；零中心表示 rho=1+Σf；力修正壁速 u_wall^lattice=u_wall−g/(2ρ) 且 ρ_wall 沿用 1.0；H=NY−1（壁在首末格点）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: GAP-LBM-SPHERE
tag: ib3d-diffuse-interface-mdf
method: 3D 扩散界面 IBM 曲面力法——三角面片几何求面积/体积/ds，标记点用张量积 4×4×4 Peskin-4pt 模板插值/散布，多重直接力（MDF）迭代得到壁面力与反作用力，只在包围盒内 dynamic_slice/update。给出了曲面力法的一种实现方式，但仓内球绕流只出图、无 Cd 定量对标。
files: vivsim/ib3d/stencil.py:7, vivsim/ib3d/geometry.py:6, vivsim/ib/mdf.py:10, vivsim/ib/kernels.py:25, examples/3d/flow_past_sphere.py:108, examples/3d/flow_past_sphere.py:134
conventions: ds 归一化后 MDF 力 = 2·ds·(u_target−u_interp)；n_iter 默认 5（球算例取 3）；标记目标速度即壁面速度；包围盒 pad=4（球）/2（圆柱算例）
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-IBM-MDF-STENCIL
tag: peskin-tensor-product-stencil-mdf
method: 把 IBM stencil 一次性预计算成 (weights, flat_indices) 常数，运行期只做一次 einsum 插值与一次散播；插值/散布严格互为伴随（本次验证残差 2e-16），MDF 用 lax.fori_loop 迭代且可整体 jit。可直接搬到任何张量后端。
files: vivsim/ib/stencil.py:7, vivsim/ib/stencil.py:54, vivsim/ib/stencil.py:81, vivsim/ib/mdf.py:47, vivsim/ib/mdf.py:60
conventions: 模板半径 stencil_radius=2 → 2D 4×4=16 点、3D 4×4×4=64 点；索引扁平化 index=x*NY+y；权重和为 1，保常数场
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-GPU-JAX-FUSION
tag: jax-pure-function-aa-pattern-static-slice
method: 用「纯函数 + 静态 slice 拼接」代替 jnp.roll 实现 streaming（每方向一条 concatenate），配 jit(donate_argnums=0) 与 lax.scan 把时间循环整体融合；分布函数按 AA-pattern (q,NX,NY) 布局使空间维连续、便于 XLA 合并访存；benchmark 用 warmup+repeat+block_until_ready 逐算子计时。
files: vivsim/lbm/basic.py:60, vivsim/lbm/basic.py:44, examples/2d/poiseuille_channel.py:150, examples/2d/vortex_induced_vibration.py:151, examples/benchmark.py:61
conventions: f 形状 (q,NX,NY[,NZ])；donate_argnums=0 要求每步返回新 f；benchmark N_WARMUP=10 / N_REPEAT=200
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-MULTIDEVICE-PPERMUTE
tag: lax-ppermute-periodic-halo
method: 域沿 x/y 均分到设备 mesh，每步用 jax.lax.ppermute 在设备轴上交换相邻子域边界分布函数（周期环形拓扑）实现跨设备迁移。最小可用但阻塞、无计算通信重叠。
files: vivsim/multidevice.py:13, vivsim/multidevice.py:27, vivsim/multidevice.py:31, vivsim/multidevice.py:35
conventions: device_pairs=(i,(i+1)%n) 环形；只交换一层边界线；要求设备数整除域
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-POROUS-MASK-BB
tag: mask-indexed-bounceback
method: 用布尔 mask 标记固体格点，一行 f.at[:,mask].set(f[:,mask][OPP_DIRS]) 完成局部 bounce-back，几何任意（其算例把文字光栅化成 mask）。JAX 下无动态索引开销，天然适配多孔介质/复杂几何。
files: vivsim/lbm/boundary/bb.py:98, examples/2d/flow_through_text.py:78, vivsim/lbm3d/boundary/bb.py:56
conventions: mask 形状为空间维 (NX,NY)；opp_dirs 表须与 lattice 速度集顺序一致；多孔介质需自行加体积/渗透率统计（本仓无）
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-BLOCK-REFINEMENT-2TO1
tag: buffer-layer-2to1-refinement
method: 块结构 2:1 局部加密——细/粗块之间加一层 buffer，迁移后按方向做 fine→coarse 四点平均、coarse→fine 复制，并按 level 重算松弛参数以保持黏度一致。
files: vivsim/multigrid.py:21, vivsim/multigrid.py:58, vivsim/multigrid.py:100, vivsim/multigrid.py:133
conventions: 相邻层只许差 1（2 倍）；ω_l=2ω₀/(2^{l+1}+(1−2^l)ω₀)；只有 2D，且仓内无算例调用
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-CASE-ORG-EXAMPLES
tag: one-file-per-case-pure-step
method: 案例组织范式——每个算例文件顶部集中参数块（几何/物理/LBM 三节），中部一个纯函数 step(f)（碰撞→力项→迁移→边界，顺序与所用 forcing 严格配套），底部 update_chunk+scan 跑；可视化/后处理只读 f。可作为我们案例目录模板。
files: examples/2d/poiseuille_channel.py:33, examples/2d/poiseuille_channel.py:77, examples/2d/poiseuille_channel.py:148, examples/2d/vortex_induced_vibration.py:99
conventions: step 内 EDM 用未修正 u、Guo 用修正后 u（顺序不可调换）；参数块三节固定；chunk 化以便 tqdm 与 block_until_ready 同步
transfer: fits
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-VERIFICATION-SURFACE
tag: continuous-error-print-vs-doi-refs
method: 每个算例末尾把误差算成数字打印：圆柱绕流与 3 篇 DOI 文献的 Cd=1.389/Cl=0.718 比百分比误差，Couette/Taylor-Green/ABC 打印 rel-L2，Poiseuille 叠解析剖面。优点是参考值连出处写进源码，缺点是全仓无 assert、无 pytest、无 CI 门。
files: examples/2d/flow_pass_cylinder.py:80, examples/2d/flow_pass_cylinder.py:197, examples/2d/flow_pass_cylinder.py:199, examples/2d/poiseuille_channel.py:176, examples/2d/couette_flow.py:96
conventions: 参考值集中在 "REFERENCE DATA" 段并附 DOI；无量纲化 Cd=2h/(ρU²D)
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: haimingz/vivsim
slug: NEW:LBM-AUTODIFF-READY-DESIGN
tag: pure-function-fsi-fixed-point
method: 全库纯函数 + 状态作 scan carry 传递，FSI 用固定点子迭代（FSI_ITER/IB_ITER）耦合，使整个时间循环结构上可被 jax.grad 穿透；但仓内无一处求导用例，属"结构就绪、能力未兑现"。
files: examples/2d/vortex_induced_vibration.py:70, examples/2d/vortex_induced_vibration.py:114, examples/2d/vortex_induced_vibration.py:151, examples/2d/flow_pass_cylinder.py:60
conventions: FSI_ITER=1（VIV 算例）、IB_ITER=3（圆柱/球算例）；耦合为显式固定点，无 Newton/伴随
transfer: adapt
[/OSS-SOL]