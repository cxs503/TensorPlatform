# 开源 LBM 技术盘点 · 单项目深盘：`Maarten-vd-Sande/lbm`

> 锚点 FEAT-OSS-INVENTORY-LBM（mission 8288）· tier 1 · 自由面 LBM Python 实现
> 盘点对象：`https://github.com/Maarten-vd-Sande/lbm` @ `573a01c4d2e6321f3634ff271adca4ce91872e6f`（`master` tip）
> 盘点方式：**读代码本体**（README 仅辅助定位），逐条结论带 `文件:行` 指针；另加运行时探针（§九）验证静态判断。

---

## 零、取证与复现（provenance）

| 项 | 值 |
|---|---|
| 仓库 / 分支 / commit | `Maarten-vd-Sande/lbm` / `master` / `573a01c4d2e6321f3634ff271adca4ce91872e6f`（`git ls-remote` 直读） |
| 取证通道 | `git clone` 在本机 SNI 窗内挂死（120 s 超时）；改用 `https://codeload.github.com/Maarten-vd-Sande/lbm/tar.gz/refs/heads/master`（8,399,371 B，gzip 完整）解包到 `/tmp/lbm_src`（**未进入我们的仓**） |
| 树规模 | 16 个文件；代码 664 行（`README.md` 18 + `requirements.yaml` 7 + `lbm/*.py` 500 + `examples/*.py` 139），另 2 个动图（约 8 MB）与误提交的 `lbm/__pycache__/*.cpython-36.pyc` |
| GitHub REST | 共享出口 IP 上 unauthenticated rate limit 耗尽（`{"message":"API rate limit exceeded..."}`）→ stars/commits/issues 元数据 **WITHHELD**；活跃度改由树内容 + 提交时间戳判定 |
| 运行探针 | `runs/m8288/{micro_props,probe_lbm_repo,ab_variants,run_v2,diag_nan,make_figure}.py`；因 loong64 镜像无 numba（`pip install numba` → *No matching distribution*），用**语义等价**的 pass-through `numba.jit` shim 纯 Python 执行（`runs/m8288/probe_lbm_repo.py:32-47`），故探针**不代表性能**，只验证语义 |

---

## 一、概况

- **语言/依赖**：Python ≥3.6，仅 `numpy`+`numba`（`requirements.yaml:1-7`）；`matplotlib`/`ffmpeg` 只服务 examples 出 GIF/MP4。
- **许可证：仓内无 LICENSE 文件**（全树 `grep -ril licen` 零命中；根目录只有 `README.md`、`requirements.yaml`、`lbm/`、`examples/`）。按默认版权法即 **all rights reserved**。
  **对我们的可用性判断：代码不可复制/移植进 TensorLBM**（无授权，连"署名即可用"都没有）；只有**算法思想**（Thuerey 博士论文体系的自由面 VoF-ε 记账 + Körner/waLBerla 公开算法）可以阅读后自行实现。任何逐行搬运都属侵权风险，必须走"读思想 → 我们自己的实现 + 单测"路线；如需代码级复用，须先取得作者书面授权。
- **活跃度**：单一历史快照，全树文件 mtime 均为 `2020-08-08 18:39`（= 最后一次提交时间）；无 `.github/`、无 CI、无测试、无 `setup.py`/`pyproject.toml`（不可 `pip install`，只能靠 `sys.path` hack 导入，`examples/dambreak.py:9`）。判为**停滞的教学仓**。
- **定位一句话**：一个 2D 的、串行的、单相液体 + 气体空腔的 **D2Q9 自由面 LBM 最小可读实现**（Thuerey 论文的低层接口），面向 dam break / 圆柱绕流两个动画 demo，不面向工程计算。

---

## 二、方法谱系

| 维度 | 本仓实现 | 指针 |
|---|---|---|
| 格子 | **仅 D2Q9**（无 D3Q19/D3Q27、无多块、无自适应） | 速度集 `lbm/util.py:6-14`；权重 `lbm/util.py:17-25`（`w0=4/9, w1-4=1/9, w5-8=1/36`，与速度排序一致，无错位）；`cs²=1/3` 出现在 `lbm/streamcollide.py:141-144` 的二阶平衡态里 |
| 碰撞 | **仅单松弛 BGK**：`fout = fin + ω(feq − fin)`，ω 以长度 1 数组传入（`np.array([1.0])` / `[1.8]`） | `lbm/streamcollide.py:146-157`；ω 由调用方给：`examples/dambreak.py:19`、`examples/cylinder_flow.py:19` |
| TRT / MRT / KBC / RR / regularized | **无**（全仓无 `omega` 第二松弛、无矩空间变换、无熵/正则化） | 全树无匹配 |
| 力项 | **只有常体力（重力）一种**：`fout[n] -= ρ·w_n·(e_n·g)`；无 Guo(1998) 速度修正、无 shifted-velocity/EDM、无半力修正（速度就是 `u=Σf e/ρ`） | `lbm/streamcollide.py:154-157`；`u` 计算 `lbm/streamcollide.py:118-127` |
| 力项量纲一致性 | 系数缺 `1/cs² = 3`：由 `Σ_n w_n e_nα e_nβ = (1/3)δ_αβ` 得每步 `Δu = −(1/3)g`，而一阶正确源项是 `Δu = −g`。**实测** `Δu_y = 0.0333333 = |g|/3`（`g_y=−0.1`），与解析一致到 7 位（`runs/m8288/micro_props.json` D5、图 (d)） | — |
| 实现组织 | 3 个内核文件：`streamcollide.py`（宏观量+平衡态+碰撞+流动+边界）、`update_types.py`（相态转换+质量重分配）、`time_evolve.py`（时间循环）；**无算子抽象、无 kernel 生成、逐格标量循环** | `lbm/time_evolve.py:11-30` |
| 状态数组 | `fin/fout/equi/fdist:(9,nx,ny) float32`、`inlet/u:(2,nx,ny)`、`rho/mass:(nx,ny)`、`cell_type:(nx,ny) int` | `lbm/init.py:10-18` |

---

## 三、边界处理（逐一定位）

1. **无滑移 bounce-back（节点式，流体侧执行）**：若上游邻格不属于 `FLUID|INTERFACE|INLET|OUTLET`，则 `fin[n] = fout[v_inv[n]]`。
   `lbm/streamcollide.py:59-66`（流体格分支）、`:92-94`（界面格的 obstacle 邻格）。
   性质：**节点型（node-based）反弹，不是 half-way**，无壁面半格偏移 → 壁面几何位置有 `O(½Δx)` 系统偏差；无 Bouzidi/曲率插值 BB、无移动壁、无部分反弹系数。
2. **自由面（气相侧）**：反 bounce-back 重构 `fin[n] = feq[n] + feq[opp] − fout[opp]`，随后用局部法向把"指向液体外"的方向一律按同式覆盖。
   `lbm/streamcollide.py:89-91`（gas 邻格）、`:96-100`（法向投影修正 `normal·e_opp > 0` 的方向被 feq+feq_opp−f_opp 覆盖）。
3. **表面法向**：`n = ½∇ε` 中心差分，`ε = mass/ρ` 截断到 `[0,1]`。`lbm/streamcollide.py:181-187`、`159-179`。
4. **入口（速度入口 + 密度闭合）**：给定 `u_in`，已知方向的密度闭合 `ρ = (Σ_{n=0..2} f_n + 2Σ_{n=3..5} f_n)/(1−u_x)`，未知方向用平衡态填充。
   `lbm/streamcollide.py:218-233`（`set_inlet`）+ `:31-33`（`fin[6..8] = equi`）。
   **不是 Zou-He**（没有非平衡反弹的显式构造，只用 feq 填充 + 质量守恒闭合），且**硬编码"入口必须在左侧、流向 +x"**（docstring `lbm/streamcollide.py:218-222` 自述）。
5. **出口**：零梯度外推 `fin[3:6](ix) = fin[3:6](ix−1)`，硬编码"出口必须在右侧"。`lbm/streamcollide.py:210-216`。
6. **曲率 / 接触角 / IBM**：**全无**（无壁面润湿、无 CSF、无 immersed boundary、无 ghost-fluid）。
7. **越界守卫缺陷**：`lbm/streamcollide.py:56` 写成 `if 0 < ix_next >= fin.shape[1] or 0 < iy_next >= fin.shape[2]: continue`，Python 链式比较语义等价于 `0 < x and x ≥ nx` → **只挡高端**；`ix_next ∈ {0, −1}` 不被挡 → 低端 `−1` 索引静默变成周期镜像（与 DOMAIN 内其它边界语义冲突），高端则 `continue` 留下上一步旧值。实测语义表：`ix_next=−1/0/1/9 → 不跳过；10 → 跳过`（`runs/m8288/micro_props.json` D3）。

---

## 四、多相 / 多组元（重点：本仓**没有**真正的多相模型）

**明确结论：无 SCMP、无 MCMP、无 free-energy、无 phase-field/Allen-Cahn、无颜色梯度、无相互作用势（`ψ`）、无 recoloring、无表面张力（CSF/Laplace）。**
证据：只有**一套**分布函数 `fin/fout`（`lbm/init.py:10-11`）；`cell_type` 只有 `FLUID=1, INTERFACE=2, GAS=4, OBSTACLE=8, INLET=16, OUTLET=32` 六个基本类型 + 邻域/意图标志（`lbm/util.py:31-49`）；全树无 `psi|potential|recolor|surface_tension|chemical_potential` 符号。

本仓有的是**一套自由面（free-surface / VoF 型）方案**，属 Thuerey–Körner(2005)–waLBerla 家族，机制如下（全部定位到行）：

1. **单元类型位标志 + 邻域缓存**：`FLUID/INTERFACE/GAS/OBSTACLE/INLET/OUTLET` 与 `NO_FLUID_NEIGH/NO_EMPTY_NEIGH/NO_IFACE_NEIGH`、`TO_FLUID/TO_GAS` 打包进一个 int（`lbm/util.py:31-49`，因 numba 不支持 `IntFlag` 用 namedtuple 兜底 `:51-56`）；每步末重算邻域标志（`lbm/update_types.py:115-132`），供内层循环 O(1) 跳过空单元（`lbm/streamcollide.py:15-16`、`:46-47`）。
2. **填充率 ε = mass/ρ**（`lbm/streamcollide.py:159-179`）。注意 **`mass` 与 `rho` 是两个独立场**（`lbm/init.py:16-17`）；流动后对流体格强制 `rho := mass`（`lbm/streamcollide.py:104-105`）→ 密度被当作体积质量使用。
3. **交错质量通量记账**：
   - F→I（界面格从液体邻格取分布）：`fin[n]=fout[n](上游)`，`mass += fout[n](上游) − fout[opp](本格)`（`lbm/streamcollide.py:77-81`）。
   - I→I：`mass += interface_mass_exchange(...) · (ε_self+ε_nei)/2`（`lbm/streamcollide.py:82-88`），交换方向由邻居标志决定（`lbm/streamcollide.py:190-208`）。
4. **相变判据**（阈值硬编码 `fill_offset=0.003`、`lonely_tresh=0.1`，`lbm/update_types.py:12-13`，判据 `:20-27`）：
   - I→F：`mass > (1+δ)ρ` 或（`mass ≥ (1−δ_l)ρ` 且有液体邻格）；
   - I→G：`mass < −δρ` 或（`mass ≤ δ_l ρ` 且无液体邻格）或孤立界面格。
   - I→F 时把邻接的 gas 格升为 interface 并用 **average_surround**（邻格 ρ,u 平均）初始化（`lbm/update_types.py:32-43` + `:134-156`）；I→G 时把邻接 fluid 格降为 interface（`:45-54`）。
5. **超额质量重分配**：转 F 时 `mex = mass−ρ`；转 G 时 `mex = mass`、法向取反、`mass=0`（`lbm/update_types.py:64-71`）；随后按 `η_n = max(0, e_n·n)` 加权投给 interface 邻格的 `fdist`（`:80-97`），若权重全 0 则对 interface 邻格均匀分摊（`:98-101`）；下一次进入 `update_types` 时收集回 `mass`（`:107-109`）。
6. **密度比能力**：**气相是"空"（无气体密度/粘性/压力），密度比 ≡ ∞（真空近似）**，不支持有限密度比两相，也没有气体状态方程。
7. **spurious current**：**无**——因为**没有表面张力模型**（无相互作用力、无 CSF 源项），所以既没有经典 spurious current，也完全失去毛细/表面张力/接触角能力（Weber/Capillary 数不可控）。
8. **质量守恒实测（关键负面结果）**：该记账**不闭合**。
   - V0（逐行照抄）：**第 7 步即 NaN**（新填充格 `Σf=0` → `u=Σfe/ρ` 除零，`lbm/streamcollide.py:127` 无守卫；NaN 源格为 interface，`runs/m8288/diag_nan.json`）。
   - V1（仅补 `ρ≤1e-6` 守卫）：跑满 200 步，200 步末**总液量 +1.48%**，但过程**非单调**——中途峰值 **+14.2%**（约第 60 步）。
   - V2（再补"每步清零 `fdist`"）：跑满 200 步，末值 **−2.90%**，过程峰值 **+14.4%**。
   → `fdist` 是跨步"暂存"数组，**全程无重置**（只写于 `lbm/update_types.py:94-101`，只读于 `:107-109`），V1 里旧值被重复收集（质量虚增），V2 里写入非 interface 邻格的份额永不被收集（质量丢失）。这条是我们最该记住的反面结论：**结构可以借鉴，记账必须重写并配守恒单测**。
9. **`get_epsilon` 死分支**：`lbm/streamcollide.py:166` 写 `elif cell_type & CT.GAS == 2:`，Python 解析为 `cell_type & (4 == 2)` = `cell_type & False` = 0 → **GAS 分支永不可达**，气体格落到 `else` 返回 `0.5`（实测 `get_epsilon(GAS,0,0)=0.5`；应为 0，`runs/m8288/micro_props.json` D1）。后果：界面格"一侧液体、一侧气体"时法向被低估 2×（真值 `½(1−0)=0.5`，实现 `½(1−0.5)=0.25`，由 `lbm/streamcollide.py:181-187` 的公式直接推出），而该法向正是 §三.2 的表面法与 §四.5 质量重分配的输入。
10. **速度钳位（隐藏限幅器）**：`|u| > √(2/3) = 0.8165` 时整矢量缩放（`lbm/streamcollide.py:129-133`）——阈值对应 **Ma = 1.414**（`cs=0.577`），远超 LBM 有效区（Ma≲0.1–0.3），等于**没有稳定性保护**；且实测 dam break **第 20 步即触发，200 步中有 181 步 `max|u|` 贴死 0.8165**（图 (b)）→ 动力学被静默截断，守恒性进一步破坏。

---

## 五、GPU / 并行

- **GPU：无**（全树无 `cuda|torch|pyopencl|cupy|kernel`）。
- **MPI/多进程/多线程：无**（单文件单进程；无分区、无 halo、无通信隐藏、无 domain decomposition）。
- **numba 使用**：`@jit(nopython=True)` 于 `lbm_streaming`(`streamcollide.py:39`)、`get_epsilon`(`:159`)、`get_normal`(`:181`)、`update_types`(`update_types.py:7`)；而 `update_rho_u`(`:108`)、`set_equi`(`:136`)、`collide`(`:146`)、`interface_mass_exchange`(`:190`)、`set_outlet`(`:210`)、`set_inlet`(`:218`)、`average_surround`(`update_types.py:134`) 只写 `@jit()`——**装饰器风格不一致，语义随 numba 版本漂移**（numba<0.59 允许 object-mode 回退；≥0.59 默认 nopython）。
- **真正的性能瓶颈（结构性，非实测）**：最外层的**时间步驱动 `lbm_collision` 的 `@jit(nopython=True)` 被注释掉**（`lbm/streamcollide.py:6`）→ 每个时间步要在解释器里跑 `nx·ny` 次 Python 循环，且每格还要 3–5 次 jit 内核调度（`lbm/streamcollide.py:25,28,36`）；再叠加每步 4 次全场 `np.copy`（`lbm/time_evolve.py:18,24`）与**每步一次 `print`**（`lbm/time_evolve.py:12`）。
- **数据布局**：`(9, nx, ny)` —— 方向维在最外层（per-direction planes），单元内 `iy` 连续；循环 `ix` 外、`iy` 内（`lbm/streamcollide.py:44-45`）→ 仅 `iy` 方向访存连续，`ix` 方向跨 stride；**无 AA-pattern、无融合 stream-collide（两套分布数组交换、显式 fout 阶段）、无向量化/SIMD 指令、无 SoA 结构体**。
- **内存**：`time_evolve` 把**全历史** `u` 与 `cell_type` 存下来供动画（`lbm/time_evolve.py:8-9, 29-30`）——自带 cylinder 例（300×130、7000 步，`examples/cylinder_flow.py:17-22`）即 `7000×2×300×130×4 B ≈ 2.18 GB`；且 `dtype=type(cell_type)` 实为 `np.ndarray` → 得到 **object dtype**（实测 `runs/m8288/micro_props.json` D2），既费内存又断向量化。

---

## 六、可微 / 代码生成

- **不可微**：纯 numpy + numba，无 autograd/torch/jax，无伴随/灵敏度接口。
- **无符号推导**：平衡态、力项、边界全部手写标量式（`lbm/streamcollide.py:136-144, 146-157, 210-233`）。
- **无 kernel 生成**：无 CSE、无 DSL/嵌入语言、无 C/Fortran 代码生成；唯一"编译"是 numba 即时编译。
- **无工程化配套**：无 CLI/配置文件（参数直接写在脚本顶层，`examples/dambreak.py:17-21`、`examples/cylinder_flow.py:17-22`）、无 checkpoint/restart、无日志（只有 `print(time)`）。

---

## 七、与我们功能点的映射

| feature_ledger slug | 本仓提供？ | 定位 | transfer | 说明 |
|---|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | **否** | — | — | 无第二组元分布、无组分间作用力、无体力驱动的两相分层设置；本仓对 GAP **零贡献**（GAP 仍未闭合）。附带信息：我们自己的 `TensorLBM/src/tensorlbm/multiphase.py` 已含 SCMP/MCMP/颜色梯度/自由能四族，此仓不构成对标基线 |
| MCMP 高密度比 | **否** | — | — | 气相为真空，无有限密度比机制 |
| 自由面 | **是（VoF-ε 记账式）** | `streamcollide.py:69-105`、`update_types.py:7-132` | **adapt** | 与我们 `TensorLBM/src/tensorlbm/free_surface_lbm(_2d).py`（fill=ρ/ρ_liquid）是**同族不同记账路线**：本仓用独立 `mass` 场 + 显式通量记账 + 法向加权超额质量重分配。结构可借鉴，记账不可照抄（§四.8 实测不守恒） |
| 相场 Allen-Cahn | **否** | — | — | 无 ψ/化学势/界面厚度模型 |
| GPU 融合 kernel | **否** | — | — | numba CPU；我们已 torch 化，此仓反向不可用 |
| 分布式多卡 | **否** | — | — | 单进程；无分区/halo |
| 稀疏 / 多孔 | **弱（仅静态掩码）** | `streamcollide.py:64-66`（obstacle 掩码 + 反弹） | reference | 只有 `OBSTACLE` 位标志，无多孔介质阻力源项（无 Darcy/Ergun）、无稀疏数据结构 |
| 案例组织 | **有（教学级）** | `examples/dambreak.py:17-49`、`examples/cylinder_flow.py:17-57` | reference | 参数硬编码在模块顶层、`sys.path` hack（`:9`）、ffmpeg 绝对路径硬编码（`dambreak.py:61`、`cylinder_flow.py:74`）、随机障碍无 `seed`（`cylinder_flow.py:35-39`）→ **反面教材** |
| 测试与验证面 | **无** | — | — | **0 个测试文件、0 个解析解对照、0 条误差指标**（README 只有两张 GIF）。结论：此类仓**不能**作为我们 dam-break 定量验证的基线来源（我们的 `benchmarks/bench_dam_break_quant.py` 必须自建解析/文献基准） |
| CI | **无** | — | — | 无 `.github/`，无 lint/类型/单测门 |

---

## 八、可吸收 Top3（对 TensorLBM / TensorPlatform）+ 风险

**Top1 — 自由面的"显式质量通量记账 + 法向加权超额质量重分配"骨架**（`streamcollide.py:77-88`、`:190-208`、`update_types.py:56-113`）
可吸收三点：①每格保留独立 `mass` 场作为**可审计的守恒账本**（便于在平台侧加"总液量漂移"守卫指标）；②超额质量按 `max(0, e_n·n)` 加权而非均匀分摊（`update_types.py:80-97`），避免在薄水膜/飞溅处人为堆积；③新界面格用邻格 ρ,u 的**平均初始化**（`update_types.py:134-156`）而非直接置零，抑制新格注入的伪压力。
**风险**：本仓两种记账变体实测分别 +1.48% / −2.90%（200 步），**照抄必错**；`fdist` 跨步不复位是根因。若采纳，必须（a）暂存数组每步清零或改为局部（b）加守恒审计单测（总液量漂移 <1e−3/千步）（c）重新推导法向（本仓 GAS 分支死代码使法向低估 2×）。

**Top2 — 单元类型位标志 + 邻域缓存的"单整数打包"**（`util.py:31-49` + `update_types.py:115-132`）
一个 int 同时装"类型 / 邻域存在性 / 本步转换意图"，内层循环 O(1) 跳过空单元（`streamcollide.py:15-16,46-47`）。对我们 torch 版的价值：用**单个 int16 flags 张量**替代 4–6 个 bool mask，可减少 kernel 数量与显存带宽；**风险**：torch.compile 下位运算链会拆成多个小 kernel 反而更慢，且我们现有实现是 `torch.where` 函数式路线（`free_surface_lbm.py:1-10` 注释自述），in-place 位运算与之冲突 → 只能作思路参考，须用我们的 benchmark 实测再定。

**Top3 — 极简时间循环骨架作为"参考实现 oracle"**（`time_evolve.py:11-30`，500 行核心）
价值不在性能而在**可读性**：可作为一个独立的 2D 自由面参考实现，用于对我们三维实现做**形貌级**交叉验证（dam break 前锋位置、空腔贯通时刻的定性一致）。**风险**：V0 在 7 步内 NaN（须先补零密度守卫）、不守恒、有 Ma=1.41 的静默速度钳位、2D only → **只能做形貌对照，不能做定量基准**。

**风险总表（吸收前必读）**：许可证缺失（法律上不可复制代码）｜0 测试 0 对照（无任何验证基线）｜确定性缺陷 9 处（见下附表）｜2D only｜串行无并行｜教学级代码质量（`.pyc` 入库、装饰器风格不一、参数硬编码）。

### 附表：静态+运行时确认的缺陷清单
| # | 缺陷 | 指针 | 证据 | 严重度 |
|---|---|---|---|---|
| 1 | `rho_prev = np.copy(mass)` 笔误（prev 场取自 mass） | `time_evolve.py:18` | 源码；且 `:24` 又覆盖一次 → `update_types` 收到的"前态"实为当前态 | 中 |
| 2 | `fdist` 跨步不复位 → 质量虚增/丢失 | `update_types.py:76-109` | 运行：+1.48% / −2.90%（200 步） | **高** |
| 3 | `Σf=0` 时 `u=Σfe/ρ` 除零无守卫 → NaN 传播 | `streamcollide.py:127` | 运行：V0 第 7 步 18 个非有限值 | **高** |
| 4 | `cell_type & CT.GAS == 2` 死分支（优先级） | `streamcollide.py:166` | 运行：`get_epsilon(GAS)=0.5` 应为 0 | **高**（污染法向） |
| 5 | 力项缺 `1/cs²`、无 Guo 速度修正 | `streamcollide.py:154-157` | 运行：`Δu=−g/3`（解析一致到 7 位） | 中（定量失真） |
| 6 | 越界守卫链式比较写错 | `streamcollide.py:56` | 运行语义表 D3：`−1/0` 不跳过 | 中 |
| 7 | 速度钳位在 Ma=1.41（等于无保护）且静默改动量 | `streamcollide.py:129-133` | 运行：`max|u|` 贴死 0.8165 | 中 |
| 8 | `dtype=type(cell_type)` → object 数组 | `time_evolve.py:9` | 运行：dtype=`object` | 低（性能） |
| 9 | 每步 `print` + 全历史存储 + 4 次全场 `np.copy` | `time_evolve.py:12,8-9,18,24` | 源码；cylinder 例 ≈2.18 GB | 低（工程） |

---

## 九、运行时探针（本报告新增证据，非仓库自带）

`runs/m8288/` 下 5 个脚本，全部在 `50×50 D2Q9、ω=1.0、g=(0,−0.1)、dam-break 水柱 20×35`（`examples/dambreak.py` 的等比缩放）上执行：

| 变体 | 唯一差异 | 存活步数 | 200 步总液量漂移 | 备注 |
|---|---|---|---|---|
| V0 逐行照抄 | — | **6（第 7 步 NaN）** | — | `diag_nan.json` 定位首个坏格 = interface 格 |
| V1 补零密度守卫 | `streamcollide.py:127` 加 `ρ≤1e-6 → ρ=1,u=0` | 200 | 末值 **+1.48%**（峰值 **+14.2%**） | `max|u|` 第 20 步起贴死钳位（181/200 步） |
| V2 守卫 + 每步清 `fdist` | V1 + `update_types` 入口 `fdist[:]=0` | 200 | 末值 **−2.90%**（峰值 **+14.4%**） | 末端漂移反号 → 证实双向记账错误 |

（漂移过程非单调、峰值 ~14% 但末端回落，说明记账既虚增又丢失、方向随相态活动变化。）

V0 另在 `100×100`（等比缩放）上逐行照抄跑满 400 步（`runs/m8288/probe_results.json`，含 `time_evolve.py:18` 的 `rho_prev=mass` 笔误）：**第 20 步内即出现非有限值，其后全程 NaN**；仓库内**无任何 NaN 检测/中止/断言**，交付的 examples 会安静地写出全 NaN 动画。

图：`docs/oss-inventory/figures/fig1_lbm_mvd_probes.png`（(a) 质量漂移；(b) 速度贴钳位；(c) 200 步速度场/自由面；(d) 力项标定 3× 偏小）。
**误差判定**：本报告不含"我们的求解器 vs 基准"的精度对标，故不存在 <3% 判据；这里唯一的定量对标是**力项标定**——实测 `Δu_y = 0.0333333` 对解析 `|g|/3 = 0.0333333`，相对误差 `<1e−6`（探针自校验通过）；以及"该实现相对正确一阶源项偏小 3×"（0.0333 vs 0.1，即 **−66.7%**，远超 15% 不可接受线）——这正说明本仓**不能**作为力驱动类算例的定量参考。质量守恒**无基准**（本仓无测试/无解析对照），仅给出三变体互相比较的相对漂移。

---

## 机器块

[OSS-INV]
repo: Maarten-vd-Sande/lbm
lang: Python
license: NONE (no LICENSE file in tree — default all-rights-reserved; code not reusable)
methods: D2Q9; BGK(single omega); body-force(gravity, w*rho*e.g, no Guo/velocity-shift, 1/cs^2 factor missing); free-surface(VoF fill=mass/rho + explicit mass-flux bookkeeping + normal-weighted excess-mass redistribution); boundary: node-based no-slip bounce-back + equilibrium-fill inlet + zero-gradient outlet + normal-projected anti-bounce-back at free surface; no TRT/MRT/KBC/RR/regularized; 2D only
multiphase: 无 SCMP/MCMP/free-energy/phase-field/color-gradient/surface-tension; only single-phase + gas-void free surface (lbm/util.py:36-49, lbm/streamcollide.py:69-105, lbm/update_types.py:56-113)
boundary: node-based bounce-back (lbm/streamcollide.py:59-66,92-94); free-surface anti-bounce-back + normal projection (lbm/streamcollide.py:89-91,96-100); equilibrium-fill velocity inlet with mass-conserving density closure (lbm/streamcollide.py:218-233,31-33); zero-gradient outlet (lbm/streamcollide.py:210-216); no Zou-He, no curvature/contact-angle BB, no IBM
gpu: 无 (numba CPU jit only, no CUDA/torch/MPI; outermost time-step driver lbm_collision not jitted — decorator commented out at lbm/streamcollide.py:6)
summary: 500 行教学级 2D D2Q9 自由面 LBM（Thuerey/Körner VoF-ε 家族），无多相/无并行/无测试，许可证缺失。代码级不可复用，仅有 3 处算法思想值得吸收；运行时实测揭示其质量记账不守恒（±1.5%~3%/200 步）、力项弱 3×、第 7 步即 NaN。
[/OSS-INV]

[OSS-SOL]
repo: Maarten-vd-Sande/lbm
slug: NEW:free-surface-mass-bookkeeping-variant
tag: thuerey-vof-mass-exchange
method: 每格独立 mass 场做体积质量账本，F→I 直接取上游分布并记账，I→I 交换按两侧填充率 ε 平均加权；转相时把超额质量按 max(0,e·n) 加权投给 interface 邻格，下一步收集回 mass。
files: lbm/streamcollide.py:77-88, lbm/streamcollide.py:190-208, lbm/update_types.py:56-113
conventions: ε=mass/rho∈[0,1]（rho 为格内密度，流体格每步强制 rho:=mass）；转换阈值 fill_offset=0.003、lonely_tresh=0.1；fdist 为跨步暂存数组（本仓从不复位，是漂移根因）；格型位标志 FLUID=1/INTERFACE=2/GAS=4/OBSTACLE=8/INLET=16/OUTLET=32
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: Maarten-vd-Sande/lbm
slug: NEW:new-interface-cell-init-average-surround
tag: average-surround-init
method: 空腔格升级为界面格时，用周围 FLUID/INTERFACE 邻格的 rho 与 u 的算术平均初始化该格宏观量并重建 feq，避免新格从零注入伪压力。
files: lbm/update_types.py:32-43, lbm/update_types.py:134-156
conventions: 邻域取 D2Q9 的 8 个非静止方向；计数 c>0 才平均，否则保留原值；调用后强制 cell_type=INTERFACE
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: Maarten-vd-Sande/lbm
slug: NEW:free-surface-surface-normal-from-fill
tag: fill-gradient-normal
method: 界面法向由填充率 ε 的相邻差分 n=0.5*(ε_{i-1}-ε_{i+1}, ε_{j-1}-ε_{j+1}) 给出，再用 n·e_opp>0 判据把"指向液体外"的方向用 feq+feq_opp-f_opp 重构覆盖。
files: lbm/streamcollide.py:181-187, lbm/streamcollide.py:96-100
conventions: 法向未归一化（只用符号）；ε 需 GAS=0、FLUID=1 的正确取值——本仓因 streamcollide.py:166 优先级 bug 使 GAS 返回 0.5，法向被低估 2×，不可照抄
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: Maarten-vd-Sande/lbm
slug: NEW:cell-type-bitflag-neighbour-cache
tag: bitflag-neighbour-mask
method: 用一个整数位域同时打包格型（FLUID/INTERFACE/GAS/OBSTACLE/INLET/OUTLET）、邻域存在性（NO_FLUID_NEIGH/NO_EMPTY_NEIGH/NO_IFACE_NEIGH）与本步转换意图（TO_FLUID/TO_GAS），内层循环用位与 O(1) 跳过空单元。
files: lbm/util.py:31-49, lbm/update_types.py:115-132, lbm/streamcollide.py:15-16
conventions: 位值 2^0..2^10；因 numba 不支持 enum.IntFlag 用 namedtuple 惰性展开（util.py:51-58）；每步末统一清除邻域位再重算
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: Maarten-vd-Sande/lbm
slug: NEW:reference-oracle-2d-dambreak
tag: oracle-minimal-d2q9-fs
method: 500 行、3 内核文件的极简 2D 自由面骨架（宏观量→平衡态→BGK 碰撞→带相态分支的流动→相态更新），可作为形貌级参考实现与教学材料。
files: lbm/time_evolve.py:11-30, lbm/streamcollide.py:7-106, examples/dambreak.py:17-49
conventions: 只用前需补零密度守卫（否则 7 步 NaN）；不守恒（±1.5%~3%/200 步）；有 Ma=1.41 静默速度钳位；仅 2D；无许可证
transfer: reference
[/OSS-SOL]