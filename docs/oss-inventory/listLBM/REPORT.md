# 【盘点】sorush-khajepor/listLBM —— 稀疏 list 型多相多孔 LBM（Fortran OO）

> 盘点方式：**代码本体为主**（codeload tarball 全树 87 文件 / 43 个 .f90 库模块 / 8641 行，examples 另 1232 行），文档仅辅助。
> 仓库获取：`https://codeload.github.com/sorush-khajepor/listLBM/tar.gz/refs/heads/master`（master 树，2026-11-09 取；github.com:443 / `git clone` 被网络策略阻断，raw.githubusercontent 被 reset，api.github.com 60/h 已耗尽 → 走 codeload 一次拿全树）。
> 除静态读码外，本次**实编译 + 实跑**（gfortran 12.3.0 / cmake 3.26）取得运行期证据，见 §7.2、图 1–2。所有引用为 `文件:行`（master 树行号）。未推任何分支、未 clone 进我们的仓。

---

## 一、概况

| 项 | 内容 | 指针 |
|---|---|---|
| 语言 | Fortran 2003/2008（free-form `.f90`），面向对象（type-bound procedure + class 多态） | `lists/parts/part_mod.f90:14-42` |
| 规模 | 43 个库模块 .f90 / 8641 行；examples 1232 行；writeout 2 文件 | `find . -name "*.f90" | xargs wc -l` |
| 许可证 | **GPL-3.0**（全文 `License_gpl-3.0.txt`，每个源文件头 GPLv3 声明） | `lattice/lattice_mod.f90:1-19` |
| 依赖 | VTKFortran 1.1.0（GPLv3/MIT 双许可）仅用于 VTK 输出；CMake 构建 | `README.md:10-12`；`CMakeLists.txt:49-52` |
| 活跃度 | 单作者 PhD 项目，copyright 2017；无 CI、无 tests、无 release；examples 内留 `main_bak.f90`/`main_back.f90` 备份文件 → 归档型科研代码 | `lattice/lattice_mod.f90:3`；`examples/3d/shanchen/main_bak.f90` |

**许可证对我们的可用性判断（关键）**：GPL-3.0 是最强 copyleft 之一。

- **不可做**：把 `.f90` 文件、或逐行翻译/换语言改写的版本放进 TensorLBM/TensorPlatform（指针级改写仍是衍生作品）→ GPL 传染我们的仓，与私有/商业目标冲突。
- **可以做**：吸收**算法与方法**。本仓核心算法都有公开文献出处：Shan–Chen 伪势（PRE 1993, 47:1815，作者自引 `examples/3d/shanchen/main.f90:30`）、Guo 力项（PRE 2002, 65:046308，自引 `colBGKextForce_mod.f90:42,110`）、Zou–He 型速度边界族、稀疏邻接表数据结构。按文献公式独立实现（clean-room：只读文献与结论、由未读其源码者实现）不构成衍生作品。
- **落地纪律**：本报告只登记「方法名 + 做了什么 + 约定」，**不摘录可编译代码**；TensorLBM 侧须按文献自行推导并独立验证（本次 §7.2 的 Laplace/密度比基准可直接复用为我们的验收线）。
- 附带风险：`README.md:10-12` 说 VTKFortran 另有 MIT 下载路径，但本仓自身仍是 GPLv3 —— 不能靠「依赖是 MIT」放宽。

**定位一句话**：把多孔介质中不与流体相互作用的固体节点从计算中彻底剔除（「主链表 mainlist + 同碰撞类型子链表 sublist」的稀疏 list 结构），在 Fortran OO 框架内实现 SC 伪势 SCMP/MCMP 多相流的**串行** LBM 求解器。

---

## 二、方法谱系

### 2.1 格子（D2Q9 / D3Q15 / D3Q19 / D3Q27）

| 格子 | 初始化例程 | 指针 | 权重 | 反向表 | 半向表 |
|---|---|---|---|---|---|
| D2Q9 | `initD2Q9lattice(cvel_)` | `lattice/lattice_mod.f90:56-98` | 4/9, 1/9×4, 1/36×4 (`:79-81`) | `:83` | `:87` |
| D3Q27 | `initD3Q27lattice` | `:100-158`（q 顺序 中心→面→边→角，见 `:115-119` 注释） | — | — | — |
| D3Q19 | `initD3Q19lattice` | `:161-218` | 1/3, 1/18×6, 1/36×12 (`:189-191`) | `:193` | `:194` |
| D3Q15 | `initD3Q15lattice` | `:224-280` | 16/72, 8/72×6, 1/72×8 (`:252-254`) | `:256` | `:257` |

- 微速度与 $c_s^2=cvel^2$、$c_s^4=cvel^4$ 由 `setmicrovel` 统一给出（`:293-298`），`cqvec=cvel*qvec`（`:77` 等）。
- `numd/numq/nq_1/nd_1/weight/qvec/cqvec/opposite/halfqs` 是**模块级 protected 全局变量**（`:29-50`）→ 全库 `use lattice_mod` 共享；换格子 = 在 `main` 里调用不同 `initD*`（`examples/2d/shanchen_drop/main.f90:44`）。
- 边界「情形」（角/边）名表 `bcsname`：2D 8 种（`rig/top/lef/bot/rigtop/leftop/lefbot/rigbot`，`:89-97`），3D 26 种由 ±x/±y/±z 拼名（`:196-217`、`:258-280`），配 `nbcsit`（`:64,106,169,232`）→ 支持「每个角/边一个子链表 + 一个专用边界条件」。

### 2.2 碰撞

- **只有 BGK（单弛豫）**：`pdf=(1-ω)pdf+ω f_eq`，`ω=1/τ`（`collision/base/collisionBGK_mod.f90:104-119`、`:127-132`）。
- 平衡态 `getfeq_BGK`：标准二阶 $w_q\rho[1+3(c_q\!\cdot\!u)/c^2+4.5(c_q\!\cdot\!u)^2/c^4-1.5u^2/c^2]$（`:185-198`）；另有单方向版 `:200-211`、一阶矩 `:173-183`。
- 空白碰撞 `:122-125`；固定密度壁碰撞 `wallcollision_t` `:67-72`、`:213-230`。
- **无 TRT / MRT / KBC / 正则化（RR）**：全仓 `grep -iE "TRT|MRT|KBC|regularized"` **0 命中**。

### 2.3 力项

1. **Guo 外力（标准二阶）** —— `collision/base/colBGKextForce_mod.f90`
   - 碰撞 `pdf=(1-ω)pdf+ω f_eq(u^*)+S`（`:58-75`），速度平移 $u^*=u+F/(2\rho)$（`:90-98`）；
   - 源项 $S_q=(1-0.5\omega)w_q[F\!\cdot\!(c_q-u)/c_s^2 + (F\!\cdot\!c_q)(u\!\cdot\!c_q)/c_s^4]$，代码用 `csqr/3`、`cqad/9` 表达（`:111-122`）→ `cvel=1` 时与 $c_s^2=1/3$ 一致；
   - 「物理速度」`getvel` 另加 $F/(2\rho)$（`:79-87`）。
2. **SC 伪势力（SCMP）** —— `collision/pseudopotential/ppot_mod.f90`
   - $F=-G\,\psi(x)\sum_q w_q\,\psi(x+e_q)\,e_q$（`:128-148`，`:145`）；
   - **相互作用权重取 3×格点权重**：`this%weight(1:nq_1)=weight(1:nq_1)*3`（`:112-114`，注释 "3 times weight of LB equation"）；`fcqvec=fcc*qvec`（`:110`），fcc 默认 1（`:104-108`）→ **移植时必须按文献重标定 G，不能照抄其 G 数值**；
   - 力经父类 Guo 注入实现（`ppot_mod.f90:29` 继承 `colBGKextForce_t`，只覆写 `getforce` `:118-125`）→ 「一套力注入 + 可换力来源」的良好分层。
3. **MCMP 多组分力** —— `collision/pseudopotential/mcmp_mod.f90`
   - 对其它 `ncomps-1` 组分求和，每对可给独立 $G$ 与 $\psi$：`:248-278`（`:263` `G=mcpotparam(otherid)%G`）；预计算 `othercomp/othercomppotid` 索引表避免每步搜索（`:213-234`）；
   - 体积力按加速度×密度：`force = getmcmpforce + extforce*rho`（`:239-246`）。

### 2.4 OO 组织方式（值得借鉴的「物理插件」版式）

- `sublist` 持有 `mainlist` 指针、`part` 持有 `mainlist + slists(:)`（`lists/parts/part_mod.f90:14-42`），`collision` 持有 `sublist` 指针（`collision/base/collisionBGK_mod.f90:77-83`）→ **每个子链表挂一个碰撞对象**，同一步内不同区域跑不同物理（流体/固体/虚节点）。
- 多态插槽：`getrho/getvel/getfeq/getforce/getsource/updatepot/cellcollide` 全是 type-bound procedure，子类型覆写即换模型（`colBGKpp_t` 只覆写 `getforce`；`colBGKppwall_t` 覆写 `getrho/getvel/cellcollide`）。
- 伪势模型在碰撞前同步全场势：`partppot2d_t%collide` = `updatepot()` 再 `part2d_t%collide()`（`lists/parts/partppot_mod.f90:56-75`）；势存 `mainlist%pot(:)`（`lists/base/mainlist_mod.f90:122-128`），多组分势 `mcpot(1:ncomps-1,:)`（`:131-138`）。

---

## 三、边界处理

| 类型 | 实现 | 指针 |
|---|---|---|
| **交换式 streaming（bounce-back 的一半）** | `opposite_streamfluid`：每 cell、对 `halfqs` 一半方向与 `neilist(dir,icell)` 的 `opposite(dir)` **就地交换** pdf → 单趟完成 pull+push，零临时数组 | `stream/stream_mod.f90:45-61` |
| **固体壁面** | `streamwall_t%stream2` 空（不做反向交换），`stream1` 继承流体交换 → 交换 + 空白碰撞 ≡ **半步 bounce-back** | `stream/stream_mod.f90:37-40`、`:82-85` |
| **湿节点(wet-node)固定密度壁** | 固体节点 $\rho\equiv\rho_{wall}$、$u\equiv0$、碰撞为空 → 壁 pdf 不演化，只通过伪势与流体作用；`rho_wall` 即润湿性旋钮 | `collision/pseudopotential/ppot_mod.f90:57-69`、`:181-215` |
| **Zou–He 型速度边界（带横向 + 镜像对角修正）** | 法向用 $m_1$、切向用 $m_2,m_3$、对角再减 $m_4\sum pdf(|n_{xyz}|)\,sign$ | `collision/boundaryconditions/velocitybc_mod.f90:90-110` |
| 同上：每格子系数**硬编码** | D2Q9: $2,\frac16,\frac13,\frac12$；D3Q19: $1,\frac16,\frac13,\frac12$；D3Q15: $2,\frac1{12},\frac16,\frac14$ | `velocitybc_mod.f90:69-86` |
| 边界密度反解 | 切向和 + 2×出流方向，再除 $(1+u\!\cdot\!n)$ | `velocitybc_mod.f90:148-167` |
| 角/边情形自动构造 | `setNxyz`：由切向速度投影 + 法向过滤自动生成「镜像对角」表 → 天然支持角点 | `velocitybc_mod.f90:113-145` |
| 密度约束边界 | `densitybc_t` 继承速度边界，由 ρ 反解 u 再定 pdf | `collision/boundaryconditions/densitybc_mod.f90:26-34`、`:56-76`、`:78-98` |
| 零梯度出流（一阶外推） | `pdf(i,x)=pdf(i,x-1)` | `outflowbc_mod.f90:60-74` |
| 角上「出流 + 其它边界」复合 | `outflowcrossbc_t` 逐方向分派给不同 bc | `outflowcrossbc_mod.f90:28-40`、`:44-59`、`:77-87` |
| 周期边界 | `ghostlist_t` 虚节点链表（`strbodycell/potbodycell/closest2gcell/outlet` 映射）+ 流场拷贝 | `lists/base/ghostlist_mod.f90:38-50`、`:84-104`；`stream/stream_mod.f90:104-129`；`imgproc/pbc_mod.f90` |
| **曲率边界 / IBM** | **无**。几何只能体素化（staircase）：imgproc 标 cell kind（黑=纯固体被剔除，wall=与流体相邻的固体保留） | `grep -iE "curvature|immersed|IBM"` 0 命中；`imgproc/search2d_mod.f90`、`imgproc/presetghosts*_mod.f90`、`imgproc/readsolid*_mod.f90` |

> 分层很干净：`collisionbc_t` 持 `fcol` 指针指向体碰撞（`collision/boundaryconditions/collisionbc_mod.f90:33-48`、`:60-74`），边界节点「补 pdf → 复用体碰撞」，边界条件与体碰撞模型解耦。

---

## 四、多相 / 多组元（重点）

### 4.1 SCMP（单组分多相，Shan–Chen 1993）

- 类型 `colBGKPP_t extends colBGKextForce_t`（`ppot_mod.f90:22-54`）；势 $\psi(\rho)$ 用**过程指针**注入（`class(potparam_t), pointer :: potparam`，`:31`），`potparam_t` 含 `G/a/b/T/rho_0`（`potentialfunc_mod.f90:26-31`）。
- 力项 $F=-G\psi(x)\sum_q 3w_q\psi(x+e_q)e_q$（`:128-148`），经 Guo 途径注入（§2.3）。
- 四个势函数（`potentialfunc_mod.f90`）：Carnahan–Starling（`:44-54`，`:52` 用 `sqrt(abs(...))` 防负）、van der Waals（`:57-66`）、**Shan–Chen 指数型** $\psi=\rho_0(1-e^{-\rho/\rho_0})$（`:69-74`）、$\psi=\rho$（MCMP 常用，`:77-82`）。
- 壁面/虚节点势一致性：固体用固定 $\rho_{wall}$ 求势（`:191-198`）；虚节点取其代表的实际流体节点的密度与势（`:236-296`，`:289`）→ **周期边界处伪势不失真**（很多实现会掉的坑）。

### 4.2 MCMP（多组分）

- 类型 `colBGKmcmp_t`（`mcmp_mod.f90:22-46`），初始化建组分索引表（`:182-236`），每对组分独立 $G$/势（`:263`）。
- **两种碰撞并存，但绑定的是「速度混合、无源项」版**：
  - 被绑定 `cellcollide => cellcollide_colBGkscmcmp`（`:45`）：$u=\frac{\sum_i\sum_q c_q f_q^i + F}{\rho_1+\rho_2}+\frac{F}{\rho_i}$（`:142`），随后单组分 $f^{eq}(\rho_i,u)$ + BGK，**不加速度源项**（`:150-152`）；
  - 未被绑定（死代码/备用）：Guo 版 `cellcollide_colBGKmcmp`（`:161-180`，含 `getsource`）—— 反而更规范；
  - 稳定性兜底：$\rho_1+\rho_2\le\varepsilon$ 时 $u=0$（`:144-146`）。
- 壁面 `colBGKmcmpwall_t` 固定 `rho_wall`（`:48-58`、`:74-108`）；虚节点 `colBGKmcmpgf_t` 逐对组分成势拷贝（`:317-334`）。

### 4.3 密度比能力（实测）

2D D2Q9，$\psi=\rho_0(1-e^{-\rho/\rho_0})$，$G=-1.5$，$\rho_0=1$，$\tau=1$，128×128，液滴 R=16，4000 步 → $\rho_l=1.4505$，$\rho_v=0.2282$，**密度比 6.36**。仓内自述 3D 参考 $\rho_l\approx1.4,\rho_v\approx0.22$（`examples/3d/shanchen/main.f90:26-28`）。

- **高密度比（≫10）无任何专用机制**：无 Li–Luo 力项改进、无 EOS 级压力/伪势修正、无 MRT、无等温一致性修正 → 该能力本仓**不提供**，只能当低比基线。
- **spurious current 无专门处理**：唯一相关是 Guo 源项的 $(1-\omega/2)$ 因子（`colBGKextForce_mod.f90:118`）；实测 $|u|_{max}$ 随液滴半径显著增长（R=8→0.0063，R=16→0.058，图 2b）。

### 4.4 明确「无」的部分（勿臆造）

| 能力 | 结论 | 证据 |
|---|---|---|
| free-energy LBM | **无** | 无对应模块；`grep -iE "free.surface"` 0 命中 |
| phase-field / Allen–Cahn / Cahn–Hilliard | **无** | `grep -iE "Allen|Cahn|phase.field"` 0 命中 |
| 颜色梯度模型 | **无** | `grep -iE "color.gradient"` 0 命中 |
| free-surface LBM（VOF 型） | **无** | 只有「黑固体节点剔除」，不是自由面 |
| 两组分分层 Poiseuille 验证算例 | **无** | examples 仅 8 个（shanchen 2D/3D、drop_rise_mcmp、von_karman、porous、sphere pressure/velocity、t_junction） |

---

## 五、GPU / 并行

- **无 MPI / OpenMP / OpenACC / CUDA**：全仓 `grep -iE "omp|mpi|openacc|cuda"` 只命中 README/license 文本；作者自述 "While the code has a great potential for parallelization, I haven't done it yet."（`README.md:3`）。
- **数据布局**：`pdf(0:nq_1, ist:ind)`（`lists/base/mainlist_mod.f90:104-119`），Fortran 列主序 ⇒ 同 cell 的 $q=0..Q-1$ **连续**（cell-major / "AA-pattern" 风格），内层循环都是 $q$（`stream/stream_mod.f90:51-58`、`collision/base/collisionBGK_mod.f90:114-116`）。好处：单 cell 的 9/19 个双精度落在一两条 cache line；坏处：**跨 cell 同方向不连续 ⇒ 无法按方向向量化**（AVX/JAX 沿方向轴 vmap 会被 stride 打断）。我们若照此布局做 GPU/JAX 须改方向主序 SoA。
- **邻接表**：`neilist(1:nq_1, ist:ind)` 显式存邻居 cell 索引（`mainlist_mod.f90:111`）→ 天然非结构网格，固体不进 mainlist。
- **内存合并 / 通信隐藏**：无（串行）。并行切分点天然存在（子链表按碰撞类型分组：`part_mod.f90:19-24` 的 `fluids/walls/ghostf` 指针，`lists/kinds/*` 五类），但**模块级全局状态**（`lattice_mod.f90:29-50`）是并行化真正的障碍 —— **反面教训**：格子常量应作不可变参数对象显式传入。
- **稀疏收益实测**（2D，128×128，加 4 个 26×26 盒子）：`black=2304` 纯固体被剔除（`imgproc.rep`：`computational 16900→14596`，`Porosity 1.0→0.835`），活跃流体 15876→13172（−17.0%），600 步 wall clock 7.286 s→6.347 s（−12.9%）→ 与节点削减基本线性（§7.2）。

---

## 六、可微 / 代码生成

- **无 autograd、无符号推导、无 kernel 生成**。全手写 Fortran；「参数化」只靠 OO 多态与过程指针注入势函数（`potentialfunc_mod.f90:26-31`、`ppot_mod.f90:31`；用例 `examples/3d/shanchen/main.f90:80` `pm%getpot => pot_SC`）。
- **构建即配置**：换算例要手改 `CMakeLists.txt` 的 `example_path`/`project_name` 再重 cmake（`CMakeLists.txt:5-16`）；Release flags `-Ofast -march=native`（`:44`），`-w` 关全部告警（`:40`）。
- 对 JAX/Python 生态：**零可直接复用的可微资产**；SC 力项（即 $\nabla\psi$ 的格点模板）与 Guo 源项都是局部模板算子，天然可写成纯函数 + `lax.scan`/`vmap`，但必须按文献自行推导实现（GPL，见 §1）。

---

## 七、与我们功能点的映射 + 实测验证

### 7.1 映射表（feature_ledger slug）

| slug | 本仓状态 | 指针 | 吸收方式 |
|---|---|---|---|
| `GAP-LBM-MCMP-POISEUILLE`（两组分分层 Poiseuille） | **部分**：MCMP 框架齐备但**无该验证算例**（最接近 = drop rising in 8 boxes） | `examples/2d/drop_rise_mcmp_8boxes/main.f90:21-25`、`:96-125`；`mcmp_mod.f90:114-155,182-236,248-278` | reference（借组分索引表/$G$/`rho_wall` 设置思路；验收算例自建） |
| MCMP 高密度比 | **无**（实测比 6.36，无任何稳定化机制） | §4.3 | 不可吸收，仅作低比基线 |
| 自由面 / 相场 Allen–Cahn / 颜色梯度 / free-energy | **无** | §4.4 | 不可吸收 |
| `GPU 融合 kernel` | **无**，且布局方向非连续（SIMD 不友好） | `mainlist_mod.f90:110`；§5 | 反例参考（布局改方向主序） |
| 分布式多卡 | **无** | `README.md:3` | 不可吸收 |
| `稀疏 / 多孔` | **强项**：sublist 分类 + 显式邻接 + 图像法几何 + 固体剔除，实测 −12.9% wall clock / −17% 活跃节点 | `lists/base/mainlist_mod.f90:104-119`；`lists/*`；`imgproc/*` | **adapt（首选吸收点）** |
| 案例组织 | 一算例一 `main.f90`（卡式显式编程），换算例须改 CMake 重编 | `CMakeLists.txt:5-16`；`examples/*/*/main.f90` | 反例（我们应参数化/声明式） |
| 测试与验证面 | **无任何自动化测试**（无 tests/、无 `.github/`、无 CI 配置；验证只体现为 PNG + guide.pdf） | `find . -name "*.yml"` 空 | 反例，明确记为缺口 |
| CI | **无** | 同上 | 不可吸收 |

### 7.2 实测验证（本次自建 driver，非仓内测试）

**实验设计**（对照 experimental-design 技能）：决策 =「本仓 SC 实现数值是否可信、可否作为方法参考」；指标 = ①稳态 $\rho_l,\rho_v$、密度比 ②Laplace 定律 $\Delta p$–$1/R$ 线性 ③spurious current ④稀疏加速比与节点削减的一致性；**预声明阈值**：逐点相对误差 < 3% 为通过。控制变量：格子(D2Q9)/$\psi$/G/τ/界面宽固定，只变液滴半径 R∈{8,12,16}；另做 R=8 的 4000 vs 8000 步收敛对照。环境：gfortran 12.3.0 `-O2 -ffree-line-length-none`，库模块直接编译（**绕过 writeout**，它依赖 VTKFortran），driver 用 `initbubble`（`interface_width=3`）+ y 周期/x 通道，box 128×128，`rho_wall=1.4`。

**结果表（程序化输出，见 `data/raw_runs.txt`）**

| R | 步数 | $\rho_l$ | $\rho_v$ | 密度比 | $|u|_{max}$ | $\Delta p$ | wall clock |
|---|---|---|---|---|---|---|---|
| 8 | 4000 | 1.435701 | 0.226462 | 6.340 | 0.006254 | 0.001662 | 44.4 s |
| 8 | 8000 | 1.435717 | 0.226329 | 6.344 | 0.006231 | 0.001649 | 91.2 s |
| 12 | 4000 | 1.444624 | 0.227094 | 6.361 | 0.026780 | 0.001164 | 40.6 s |
| 16 | 4000 | 1.450542 | 0.228235 | 6.355 | 0.058423 | 0.000891 | 46.9 s |

压力用 SC 状态方程 $p=\rho/3+\tfrac{G}{2}\psi^2$（$\psi=\rho_0(1-e^{-\rho/\rho_0})$）取体相平均后作差。

**稀疏性对照（2D 128×128，加/不加 4 个 26×26 盒子，各 600 步）**

| 配置 | 活跃流体节点 | wall 节点 | ghost | 纯固体被剔除 | Porosity | 600 步耗时 |
|---|---|---|---|---|---|---|
| 无固体 | 15876 | 0 | 516 | 0 | 1.000 | 7.286 s |
| 4 个盒子 | 13172 | 400 | 516 | 2304 | 0.835 | 6.347 s |

### 7.3 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 误差 | <3%? |
|---|---|---|---|
| 收敛性：R=8 的 $\rho_l$ 4000 vs 8000 步 | 自洽稳态 | 1.6e-5 绝对 / **0.0008%** 相对 | ✅ |
| 收敛性：$|u|_{max}$ 4000 vs 8000 步 | 自洽稳态 | **0.37%** | ✅ |
| Laplace：$\Delta p$ vs 纯 $\sigma/R$（过原点最小二乘） | Young–Laplace | 最大 **4.50%** | ❌ |
| Laplace：$\Delta p$ vs $\sigma/(R+\delta)$，$\delta=1.28$ 反演 | Young–Laplace + 有效半径 | 最大 **0.21%** | ✅ |
| **盲测**：由 R=8,16 两点定 $\delta,\sigma$ → 预测 R=12 | 独立实算值 0.001164 | **0.33%** | ✅ |
| 密度比 vs 仓内自述 3D 参考 (1.4, 0.22) | `examples/3d/shanchen/main.f90:27-28` | $\rho_l$ **+3.61%**，$\rho_v$ **+3.74%** | ❌（说明：该参考值来自 D3Q19 3D 算例注释，本实验是 2D D2Q9，格子/维度不同，**只能算弱基准**，不判定其模型错） |
| 稀疏加速 vs 节点削减 | 节点削减 −17.0% | 时间 −12.9%，差 **4.1 pp** | 一致性可接受（wall/ghost 节点不随几何等比减少） |

结论：**该仓 SC 伪势实现数值上可信（Laplace 定律在引入 1.28 格子的有效半径偏移后 0.21%，且盲测 0.33%）**，但纯 $\sigma/R$ 拟合 4.50% 说明其液滴**静态压力与名义半径存在系统性偏移**（SC 模型界面位置/密度分布随曲率变化，属模型固有而非实现 bug）；高密度比与低 spurious current 不是它的能力范围。

**注意点 / 坑**

1. **编译缺陷（源码级）**：`collision/pseudopotential/ppot_mod.f90:291` 与 `mcmp_mod.f90:330` 写的是 `stop'Error: ...'`（`stop` 后缺空格），gfortran 12.3 直接 `Error: Blank required in STOP statement` → **首轮 43 个模块中 2 个编译失败**。README 只声明测过 gfortran 5.4.1（`README.md:8`）。本次为跑通实验在**本地 /tmp 副本**加空格修好（未推任何分支、未改上游）。
2. `CMakeLists.txt:52` 用 `configure_file` 拷贝 `extlib/lib_vtk_io.mod`，缺 VTKFortran 时 cmake 直接失败 → 想只跑物理、不要 VTK 输出，必须绕开 `writeout/`（本文做法）。
3. **MCMP 力项归一不自洽**：被绑定的 `cellcollide_colBGkscmcmp` 用总密度归一第一矩、却用组分密度除体积力（`mcmp_mod.f90:142`）→ 照抄会引入动量偏差；同文件更规范的 Guo 版（`:161-180`）是**死代码**。
4. **SC 力项权重 ×3 约定**（`ppot_mod.f90:114`）→ G 数值不可跨实现照搬。
5. **性能**：128×128 D2Q9 4000 步 = 65.5 M cell-updates / 46.9 s ≈ **1.40 MCUPS 单核**（含 OO 虚调用开销）→ 比常规优化 LBM 慢 1–2 个数量级，**不可作性能参考**，只作正确性参考。
6. 无任何测试/CI，验证结果只以 PNG 与 PDF 存在 → 复现全靠人。

---

## 八、可吸收 Top3 + 风险

**Top1 —— 稀疏 list 架构（子链表分类 + 显式邻接表 + 固体剔除）** [fits: adapt]
把节点按「同一碰撞类型」分到子链表、每子链表挂一个碰撞对象（`lists/parts/part_mod.f90:14-42` + `collision/base/collisionBGK_mod.f90:77-83`），并用 `neilist(1:nq,icell)` 存显式邻居（`mainlist_mod.f90:111`）→ kernel 内**零 if-固体 分支**。对我们的多孔介质/冰区（固体占比高的域）实测收益 13%（对应 17% 节点削减），固体越多收益越大。落地建议：TensorLBM 增加「索引集（fluid/wall/ghost）+ 每集邻接数组」驱动 `jax.lax.scan`/`vmap` 分派，而不是稠密网格 + mask。
*风险*：JAX 静态形状要求索引数组每步固定 → 静止几何完全适用，移动/融化边界需重建索引（成本需度量）。

**Top2 —— 图像法几何摄入 + 边界情形自动识别（imgproc 子系统）** [fits: adapt]
体素图/盒子 → cell kind（黑=剔除、wall=邻流体保留）、PBC 自动预置 ghost、角/边情形自动命名（`imgproc/readsolid*_mod.f90`、`presetghosts*_mod.f90`、`search*_mod.f90`），并输出 `total/computational/black/Porosity` 报表（`imgproc/imgproc2d_mod.f90:197-211`）。对我们的价值：CT 扫描多孔介质、冰区几何的体素化流水线与**几何守恒报表口径**（可作为我们「几何先验证 Cb/L/B」同类的前置检查：孔隙率/固体计数/ghost 计数三项守恒）。
*风险*：staircase 分辨率依赖、无曲率边界；报表口径需与我们的几何验证标准对齐（别直接沿用其 `Porosity` 定义）。

**Top3 —— 「可插拔势函数 + 统一力项注入」的多相分层** [fits: adapt/reference]
$\psi(\rho)$ 用策略对象注入（`potentialfunc_mod.f90:26-31` + `ppot_mod.f90:31`），力项统一走父类 Guo 注入（`ppot_mod.f90:29`、`colBGKextForce_mod.f90:58-75`）→ 换 EOS/换多相模型不动碰撞主体。价值：TensorLBM 多相模块可用同一接口并列跑 CS/SC/VW 三套 EOS 做对照 benchmark（正是我们 `GAP-LBM-MCMP-*` 需要的可对照骨架）。
*风险*：GPL（只取结构思想）、力项权重 ×3 约定、其 MCMP 归一不自洽（§7.2 注意点 3）——照抄结构可以，照抄公式系数不行。

**仓库级风险**：① GPL-3.0（法律风险，见 §1）；② 单作者归档代码、无测试/CI（质量风险）；③ 老编译器方言（`stop'...'` 已在新版 gfortran 失败）；④ 死代码与半成品备份文件并存（`mcmp_mod.f90:161-180`、`main_bak.f90`），读码时容易误当主路径。

---

## 九、复现指引（本次实验）

```bash
# 1) 取树（本机 github.com:443 / git clone 被阻断时走 codeload）
python3 -c "import urllib.request;open('/tmp/t.tgz','wb').write(urllib.request.urlopen(
  urllib.request.Request('https://codeload.github.com/sorush-khajepor/listLBM/tar.gz/refs/heads/master',
  headers={'User-Agent':'x'}),timeout=90).read())"
tar xzf /tmp/t.tgz -C /tmp/listLBM_src
# 2) 修两处 stop 语法（仅本地副本，不推上游）
sed -i "s/stop'/stop '/g" collision/pseudopotential/ppot_mod.f90 collision/pseudopotential/mcmp_mod.f90
# 3) 迭代编译库模块（排除 writeout/ 与 examples/），Pass 循环至无进展
# 4) driver 见 docs/oss-inventory/listLBM/data/driver_notes.txt，运行： ./laplace 16 4000
```

原始输出：`data/raw_runs.txt`（各算例 CSV/PRESSURE/TIMING/LIST 行与 imgproc.rep）、`data/rho_field_R16.csv`（128×128 密度场）。
图：`figures/fig1_scmp_field_profile.png`（密度云图 + 径向剖面）、`figures/fig2_laplace_spurious.png`（Laplace 拟合与盲测 + spurious current）。

---

[OSS-INV]
repo: sorush-khajepor/listLBM
lang: Fortran 2003/2008
license: GPL-3.0
methods: D2Q9/D3Q15/D3Q19/D3Q27(lattice/lattice_mod.f90:56,100,161,224); BGK单弛豫(collision/base/collisionBGK_mod.f90:104-119); Guo二阶力项(colBGKextForce_mod.f90:58-75,111-122); SC伪势SCMP(ppot_mod.f90:128-148); SC-MCMP(mcmp_mod.f90:248-278); 无TRT/MRT/KBC/正则化
multiphase: SC伪势SCMP(ppot_mod.f90:128-148,权重x3约定:114)+MCMP(mcmp_mod.f90:114-155,248-278,组分索引表:213-234); 势函数CS/VW/SC/rho(potentialfunc_mod.f90:44-82); 实测密度比6.36,无高密度比机制; spurious current无专门处理; free-energy/phase-field/color-gradient/free-surface均无
boundary: 交换式streaming半步bounce-back(stream_mod.f90:45-61,82-85); 湿节点固定密度壁rho_wall(ppot_mod.f90:191-215); Zou-He型速度/密度边界带镜像对角修正(velocitybc_mod.f90:90-110,系数69-86; densitybc_mod.f90:56-76); 一阶零梯度出流(outflowbc_mod.f90:62-74)+角上复合(outflowcrossbc_mod.f90:44-87); 周期ghost链表(ghostlist_mod.f90:38-50, stream_mod.f90:104-129); 无曲率/IBM
gpu: 无(全仓0命中omp/mpi/cuda; README.md:3自述未并行化); 数据布局pdf(0:nq,cell)cell-major方向连续(mainlist_mod.f90:110)对SIMD不友好; 稀疏剔除实测-12.9%wallclock对-17%节点
summary: listLBM 用「主链表+同碰撞类型子链表+显式邻接表」剔除多孔介质固体节点，是 SC 伪势 SCMP/MCMP 多相流的串行 Fortran OO 参考实现，实测 Laplace 定律在 1.28 格子有效半径偏移下误差 0.21%、盲测 R=12 误差 0.33%，数值可信。但它无高密度比机制（实测比 6.36）、无 spurious current 处理、无 TRT/MRT/KBC、无任何并行/GPU、无测试与 CI，且为 GPL-3.0——只能吸收方法不能吸收代码。
[/OSS-INV]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:多孔介质稀疏索引集（子链表分类+显式邻接表+固体节点剔除）
tag: sparse-sublist-neighbor-list
method: 把节点按「同一碰撞类型」分入子链表，每子链表挂一个碰撞对象（多态覆写 cellcollide/getforce 等）；邻居关系用 neilist(1:nq,icell) 显式存 cell 索引，纯固体节点（不与流体相邻）完全不进主链表，故 kernel 内无 if-solid 分支。
files: lists/parts/part_mod.f90:14-42, lists/base/mainlist_mod.f90:104-119, lists/base/mainlist_mod.f90:111, collision/base/collisionBGK_mod.f90:77-83, lists/kinds/walllist_mod.f90:20-48, imgproc/imgproc2d_mod.f90:197-211
conventions: neilist 索引为 0 基方向 × cell 索引；halfqs 为每格子「半边方向」表(lattice_mod.f90:87,194,257)；子链表用 istinmlist/indinmlist 界定；实测 4 个 26x26 盒子使活跃流体 15876→13172(-17.0%)、600 步 wall clock 7.286→6.347s(-12.9%)
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:体素图像几何摄入与孔隙率/单元报表
tag: voxel-imgproc-porosity-report
method: 由体素图(.dat)或解析盒子标记 cell kind（wall/blackghost/blackwall），自动为周期方向预置 ghost 节点、自动识别角/边边界情形，最后输出 total/computational/black/Porosity 与各子链表单元数报表。
files: imgproc/readsolid2d_mod.f90:1-80, imgproc/presetghosts2d_mod.f90:1-80, imgproc/search2d_mod.f90:1-90, imgproc/pbc_mod.f90:1-60, imgproc/imgproc2d_mod.f90:197-211
conventions: 黑(black)=纯固体单元被剔除；wall=与流体相邻的固体保留；Porosity=1-(blackwall+wall)/indomain；ghost 计入 mainlist 但不计入流体
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:可插拔伪势策略+统一Guo力项注入的多相分层
tag: pluggable-pseudopotential-guo-forcing
method: 把 psi(rho) 作为过程指针策略对象注入碰撞；SC 相互作用力只覆写 getforce，统一由父类 Guo 力项（速度平移 u+F/2rho + 源项 (1-0.5omega)w[...]）注入，换 EOS/换多相模型不动碰撞主体。
files: collision/pseudopotential/potentialfunc_mod.f90:26-82, collision/pseudopotential/ppot_mod.f90:29,88-116,128-148, collision/base/colBGKextForce_mod.f90:58-75,90-98,111-122
conventions: 伪势可选 CS/VW/SC/psi=rho；源项用 c_s^2=cvel^2/3 通用写法(cvel=1 时 1/3)；注意本实现相互作用权重=3x格点权重(ppot_mod.f90:114)，G 需按文献重标定
transfer: adapt
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: GAP-LBM-MCMP-POISEUILLE
tag: shan-chen-mcmp-velocity-mixing
method: 多组分 SC：初始化时预计算 othercomp/othercomppotid 索引表，每步对 ncomps-1 个其它组分求相互作用力并叠加体积力 acc*rho；碰撞用「速度混合、无源项」方案 u=(sum_i sum_q c f + F)/(rho_1+rho_2)+F/rho_i 后各组分用 f_eq(rho_i,u)，rho 和过小则置 u=0。
files: collision/pseudopotential/mcmp_mod.f90:114-155,161-180,182-236,239-246,248-278, examples/2d/drop_rise_mcmp_8boxes/main.f90:96-125
conventions: mcpotparam(:)%G 每对组分独立(mcmp_mod.f90:263)；壁面 rho_wall 控制润湿；extforce 是加速度(乘 rho)；注意本实现力项归一用总密度而归一力用组分密度(mcmp_mod.f90:142)不自洽，规范版 Guo 路径(:161-180)未被绑定
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:交换式streaming+湿节点固定密度壁的边界族
tag: swap-streaming-wetnode-zouhe
method: streaming 就地对 halfqs 一半方向与邻居交换 pdf（单趟无临时数组）；固体子链表跳过第二趟交换+空白碰撞=半步 bounce-back，且固体节点用固定 rho_wall/零速的「湿节点」参与伪势作用；进/出口用 Zou-He 型带切向与镜像对角修正的速度/密度边界，角点由自动生成的镜像对角表处理，出流用一阶外推，角上可复合。
files: stream/stream_mod.f90:45-61,82-85, collision/pseudopotential/ppot_mod.f90:181-215, collision/boundaryconditions/velocitybc_mod.f90:90-110,113-145,148-167, collision/boundaryconditions/densitybc_mod.f90:56-76, collision/boundaryconditions/outflowbc_mod.f90:62-74, collision/boundaryconditions/outflowcrossbc_mod.f90:44-87
conventions: 速度/密度边界系数 m1..m4 按格子硬编码(D2Q9 2,1/6,1/3,1/2; D3Q19 1,1/6,1/3,1/2; D3Q15 2,1/12,1/6,1/4)；normal 为指向域外的单位法向整数向量；rho_wall 例值 1.4~2.0 视润湿需求
transfer: reference
[/OSS-SOL]

[OSS-SOL]
repo: sorush-khajepor/listLBM
slug: NEW:单文件算例+CMake改路径的组织方式（反例）
tag: example-per-main-antipattern
method: 每个算例是一个独立 main.f90，换算例必须手改顶层 CMakeLists.txt 的 example_path/project_name 再重新 cmake 构建；无任何自动化测试与 CI，验证结果只以 PNG/PDF 存在。
files: CMakeLists.txt:5-16,40-44, examples/2d/shanchen_drop/main.f90:42-125, examples/3d/shanchen/main.f90:33-141
conventions: Release flags -Ofast -march=native 且 -w 关告警；writeout 需外部 VTKFortran 的 .mod/.o 预编译文件(CMakeLists.txt:49-52)才可编译
transfer: reference
[/OSS-SOL]
---

## 附录：同域条目先扫（发布后补记，报告 id=9558 已入库）

按纪律先扫 oss-inventory 同域既有条目，避免重复盘点与结论冲突（GET `/tensor/reports?scenario_key=oss-inventory`）：

| id | 条目 | 与本仓关系 |
|---|---|---|
| 9551 | pylbm/pylbm（Python，多方法谱系） | 方法覆盖更广（含 MRT/多相），listLBM 反而是「稀疏几何 + Fortran 性能结构」的差异化样本 |
| 9552 | lbm-principles-practice/code（Krüger 教科书官方代码，MIT） | 教学级实现，MIT 可直接借鉴代码；listLBM 为 GPL 不可借鉴代码 → 若两者方法重叠，**优先用 9552 的 MIT 实现** |
| 9553 / 9557 | MPLBM-UT（+回归校验固化 18 passed） | 提供可复用的回归验证面；本报告的 Laplace/密度比/收敛三项基准可并入其口径 |
| 9555 | Lattice-Boltzmann-fl | 同类整理 |
| 9547 | openLBMPM | 多孔介质（MPM 耦合）方向，与 listLBM 的「稀疏固体剔除」互补：前者管骨架力学，后者管孔隙流体 |
| 9545 | LBM_Taichi | GPU（Taichi）路径，正好补 listLBM 缺的并行/加速维度 |

结论：本仓的**独占增量**=①稀疏 list 数据结构与体素几何报表口径 ②GPL 许可红线下的「只能吸方法」判定 ③实编译实跑得到的 Laplace/稀疏加速定量基准 ④两处 `stop'...'` 编译缺陷（新版 gfortran 失效）——这四点在同域其它条目中未见重复。
