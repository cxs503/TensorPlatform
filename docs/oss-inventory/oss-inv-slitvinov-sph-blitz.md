# 【OSS-SPH 深盘波2】slitvinov/sph-blitz 源码级盘点与独立验证

**scenario_key**: oss-inventory
**mission_id**: 8685
**campaign**: osssph-w2（tier3 → 小仓快速盘=一次深读覆盖全量）
**快照**: `master` @ `1398d38ec2`（2026-08-01，"rm scripts/dep"），git tree 55 entries
**盘点范围**: 全仓逐文件读完（src/lib.c 1079 行 + src/sph.h 157 行 + example/sdpd.c + cases 4 例 + test/ + Makefile + README + gnuplot/scripts）
**方法**: GitHub API 实查（repos / contents / branches / commits / languages / actions / community）+ 本地 x86_64 与集群 loong64 双架构实跑
**日期**: 2026-11-09

---

## 一、案例介绍：这是什么仓

`slitvinov/sph-blitz` 是 Sergey Litvinov 自 2010 年维护的**二维多相 SPH/SDPD 求解器**（API description 原文 "2D multiphase SPH/SDPD code"），是配套论文 Hu & Adams（2006, JCP 213:844）、Español & Revenga（2003, PRE 67:026705）、Litvinov et al.（2008, PRE 77:066703）的参考实现。

### 1.1 API 实查（禁凭记忆；2026-11-09 取数）

| 字段 | 实测值 |
|---|---|
| full_name | slitvinov/sph-blitz |
| language | **C** |
| license | **GPL-3.0**（SPDX gpl-3.0，COPYING 在根目录） |
| stars / forks / watchers | 13 / 5 / 13 |
| created_at / pushed_at | 2010-05-07 / 2026-08-12 |
| repo size | 153393 KB（大头是 img/thermal.gif 与 docs/*.pdf；源码仅 ~35 KB） |
| default_branch / branches | master / 20（compressibleSPH{,2,3}、falling_block{,2}、rising_buble、polymer、spdp_solvent、PorositiesExperimental、tcl、add_glog、no_dim、separate_cpptcl、split_interaction、conduction_aer、commented_code、bug_on_megumi、gh-pages、master） |
| commits / contributors | 758 commits / 单作者 slitvinov（contributions=758） |
| tags / releases | 2 tags（SimuExample、ThermalBC_test）/ 0 releases |
| issues | 0（open+closed 均为 0） |
| CI | **无**（actions/workflows total_count = 0） |
| community profile | health 42%（无 CONTRIBUTING / CODE_OF_CONDUCT / issue、PR 模板） |
| languages | C 35225 B / Shell 2559 / Awk 2189 / Gnuplot 723 / Makefile 637 |

### 1.2 目录结构与体量

| 路径 | 内容 |
|---|---|
| `src/lib.c`（1079 行） | **全部物理内核**：核函数、EOS、配对耗散、表面张力、边界、时间积分、I/O |
| `src/sph.h`（157 行） | 数据结构（Particle / Material / Cell / Edge / Corner / Trip / Ini）+ 公开 API |
| `example/sdpd.c`（33 行） | 唯一 CLI 驱动：iniread → mkparts → bndbuild → volmass → 时间推进 |
| `src/Makefile`、`Makefile` | 静态库 libsph.a + example；`test:` 目标跑 test/regression.sh |
| `cases/*.cfg + *.txt` | 4 个算例（couette / poiseuille / ellipse / thermal）；cfg=参数，txt=粒子初值/重启动 |
| `test/regression.sh` | 回归脚本：构建 → 跑 2 例 → 与 test_data/ 金色文件**逐字节** diff |
| `test_data/` | 2 个金色输出（p.ref.ellipse.dat、p.ref.poiseuille.dat） |
| `gnuplot/`、`scripts/` | 绘图模板 + awk/shell 小工具（punto 格式转换），无计算逻辑 |
| `docs/hu.pdf`、`docs/sdpd.pdf` | 两篇论文 PDF（随仓分发，版权属 APS/Elsevier） |
| `img/` | README 用图 |

### 1.3 与 m8638 初判的偏差（必须纠正）

m8638 普查把本仓标为「Fortran BLITZ SPH」。**源码实查两条都否定**：

1. **不是 Fortran**：tree 中 0 个 `.f/.f90/.F` 文件；语言统计 C 占绝对多数；构建是 POSIX Makefile + gcc。仓名里的 "blitz" 与 BLITZ++/数组库无关（源码内 `blitz` 0 命中），只是项目名。
2. **不只是 SPH**：是 SPH 与 **SDPD**（smoothed dissipative particle dynamics）合体——耗散按 Español & Revenga 的**配对形式**离散并带 FDT 一致热噪声（src/lib.c:843-889），属含热涨落的介观求解器，不是宏观 WCSPH。

这条偏差是「凭仓名/凭记忆推断语言」的典型坑。

---

## 二、怎么做的：源码级方法清单（逐条 文件:行）

tier3 小仓，1079 行主文件全部读完（非抽样），清单即全量。

| # | 能力 | 实现 | 定位 |
|---|---|---|---|
| 1 | SPH 变体 | SPH(宏观)+SDPD(介观热涨落) 合体；动量通量按 Hu&Adams 2006 "bulk+interface" 离散 | `README:53`、`src/lib.c:1-2,223-278` |
| 2 | 核函数 | 二维三次样条 W(r)、dW(r)，归一化 63/478，支撑 r<h（d=3r/h 分段） | `src/lib.c:38-62,376-382` |
| 3 | 状态方程 | p = b0·(ρ/ρ0)^γ，b0 = a0·c_sound/γ，γ 逐材料 | `src/lib.c:64-66,403-406` |
| 4 | 密度/质量 | ρ_i=Σ_j m_j W_ij（求和式）；m_i=ρ_i/Σ_j W_ij（volmass 初始化） | `src/lib.c:736-747,714-735` |
| 5 | 动量离散 | 压力梯度用 (p_i V_i²+p_j V_j²) 权重；黏性拆横向 (5η/3−ζ)、纵向 (10η/3+4ζ)；滑移修正配对调和平均 sr/br | `src/lib.c:223-278,205-218` |
| 6 | 人工黏性 | Neumann-Richtmyer，θ=u_ij·e_ij·r_ij·δ/(r_ij²+0.01δ²) 正则；声速取 √(γ|p|/ρ) 使拉伸态(p<0)有界 | `src/lib.c:249-254` |
| 7 | 多相/自由面 | MAXMAT=3 编译期上限；nmat×nmat FORCES 表(σ、shear_slip、bulk_slip)；phi[MAXMAT²] 界面张量；色梯度 dphi + CSF 表面张力 | `src/sph.h:26`、`src/lib.c:350-367,629-651,748-765` |
| 8 | 热涨落 | FDT 一致噪声，方差 2k_B·T_eff·λ·F_ij·V_iV_j（λ=横向/纵向本征值）；k_B 按「长度单位=0.02 m」硬编码 | `src/lib.c:157,843-889` |
| 9 | 时间积分 | 速度 Verlet：两个 halfstep + 0.5 平均校正；AV/XSPH 冲量走 dU_av 单独通道 | `src/lib.c:783-831` |
| 10 | 时间步控制 | 自适应 dt=0.25·min(dt_g_vis, dt_surf, δ/(c_s+v_max))，dt_g_vis=min(√(δ/|g|), δ²/(2ν_max))，dt_surf=0.4√(δ³/σ) | `src/lib.c:400-406,796-807` |
| 11 | 边界处理 | 镜像 ghost（wall/free-slip/symmetry）+ 周期镜像；4×4 类型表(flip_n/flip_t/flip_dphi/periodic)；角点 ghost；每步重建 | `src/lib.c:923-928,930-961,963-1079` |
| 12 | 邻居搜索 | 均匀 cell 网格 (nx+2)×(ny+2)、3×3 模板、r<h 截断；配对表每步重建 **2 次**（两个 halfstep 各一次） | `src/lib.c:176-222,444-458,736/838` |
| 13 | 并行策略 | **无**：单线程 C；OpenMP/MPI/CUDA/OpenCL/pthread 在 src/lib.c、src/sph.h 中 **0 命中** | grep 实测 |
| 14 | 续算/I/O | rstout 写 project.txt（8 列 ASCII）→ INITIAL_CONDITION=1 读回续算；场输出 tecplot ASCII，**真实粒子与 ghost 混在同一 zone** | `src/lib.c:652-713,493-596` |
| 15 | 缺失物理 | 无热传导、无相变、无 particle shifting/δ-SPH 密度扩散、无 FSI、无 3D；上游 19 个实验分支（如 comprSPHinclHeatCond）**未并入 master** | branches API；grep conduction/heat=0 |

### 2.1 结构性观察（读码得出、直接影响移植的 5 件事）

1. **边界 ghost 的材料身份硬写死成 materials[0]**：`bndbuild()` 对所有镜像 ghost 调 `prtmirror(prtl_old, mtl)`，mtl 来自调用方 `q->materials`（`example/sdpd.c:23`）；`prtghost()` 覆盖 mtl、`prtcopy()` 只复制 ρ/p/U/φ 不复制 mtl（`src/lib.c:963-984,101-155`）。后果：Water 粒子的 ghost 变成 Wall 材料，边界层多相材料身份丢失，边界交互靠 FORCES 表项而非真实材料决定。
2. **MAXMAT=3 是编译期常量**（`src/sph.h:26`），φ 按 nmat² 索引，扩相须改头重编。
3. **cfg 的 FORCES 行 5 个数只用 4 个**：`i j eps sigma shear_slip bulk_slip`，`eps` 读入即丢弃（`src/lib.c:361-365`），是遗留列，会误导使用者以为能调 pair 截断。
4. **运行会就地覆盖算例文件**：`rstout()` 写 `project.txt`，README 教的 `example/sdpd cases/poiseuille` 会直接覆写受版本控制的 `cases/poiseuille.txt`（本审计实测 diff 非空）。回归脚本靠复制到 mktemp 目录规避，文档用法却会弄脏工作树。
5. **热噪声 k_B 绑定「长度单位=0.02 m」**（`src/lib.c:157`），其他量纲算例（如 cases/thermal.cfg 的 T0=5e14）必须自行换算，代码不校验。

---

## 三、验证面盘点

### 3.1 现有验证面（实测）

| 项 | 实测 | 判级 |
|---|---|---|
| 算例数 | 4 个 cfg，入回归的只有 2 个 | 偏少 |
| 测试框架 | `test/regression.sh`：make → 跑 2 例 → `diff` 逐字节比对金色文件 | STUB（无容差） |
| 容差 / 误差指标 | **无**（要求全等） | 缺 |
| 解析解/文献比对 | **无**（README 仅 gnuplot 目测） | 缺 |
| 收敛性研究 | **无** | 缺 |
| CI | **无**（0 workflows） | 缺 |
| 辅助脚本 | `test/ellipse`、`test/poiseuille`（只跑不判、无断言）、`gnuplot/*.gp` 目测 | STUB |

### 3.2 实测：干净树构建即失败

```
$ make -s all
Makefile:17: dep.mk: No such file or directory
make[1]: *** No rule to make target 'dep.mk'.  Stop.
make: *** [Makefile:2: lib] Error 2
```

`src/Makefile:17` 有 `include dep.mk`，而生成它的 `dep:` 目标依赖 `../scripts/dep` —— 该脚本已被 HEAD 提交 `1398d38ec2 "rm scripts/dep"` 删除，dep.mk 不在 tree 内也无生成规则。**结论：master 干净检出无法构建**，必须先 `touch src/dep.mk`。本审计全部实测均在 `touch src/dep.mk && make -s all` 之后进行（exit 0）。

### 3.3 实测：自带回归在本机失败（且失败原因本身说明问题）

```
$ sh test/regression.sh
ok   ellipse
< -0.208135 -0.208333 -0.00699232 9.30857e-08
---
> -0.208135 -0.208333 -0.00699232 9.30858e-08
< 1.6252 -0.208333 -0.00699232 9.30857e-08
```

退出码 = 1，差异行数 = 7，全部落在最后一位有效数字（`9.30857e-08` vs `9.30858e-08`）。说明：金色文件是某台机器 libm/编译器的产物，**逐字节回归跨平台不可移植**；又因为没有任何物理容差，「diff 绿」既不保证物理正确，也挡不住换机器就红。另有脆弱设计：金色文件名把**步数**编码进文件名（`p.0001171350.dat`、`p.0000050347.dat`），任何改动 dt 的补丁都会以「内容全不同」的方式失败。

### 3.4 本审计补建的量化验证面（MIT，脚本可复用）

`artifacts/oss_audit/sph_blitz/run_validation.py`：把上游二进制当黑盒，对**同一份 shipped 算例**（cases/poiseuille.cfg：g=0.2 沿 x、y 向双壁、x 向周期）建立三类解析比对：

1. **稳态** u(y)=g/(2ν)·y(H−y)，ν=η/ρ0=1.0、H=2、u_max=0.1000
2. **启动瞬态**：恒体力驱动的 Fourier 级数精确解（用于上游自己的结束时刻 t=0.05）
3. **间距扫描**：CELL_RATIO cr=2/3/4/5/6，并反演 ν_eff=gH²/(8·u_center)

---

## 四、计算结果（数字表；由脚本从 results.json / 集群日志程序化注入，零手抄）

### 4.1 稳态平面 Poiseuille：间距扫描

| cr | δ | 真实粒子数 | t | u_center 数值 | 中心误差 | 剖面 L2(占 u_max) | ν_eff | ν_eff/ν | 平台 |
|---|---|---|---|---|---|---|---|---|---|
| 2 | 0.1250 | 256 | 4.000 | -0.00079 | **100.8%（失效）** | 71.0% | -127.09 | -127.09 | x86_64 |
| 3 (t=4.00087) | 0.0833 | 576 | 4.001 | 0.09774 | 2.09% | 2.81% | 1.0231 | 1.0231 | x86_64 |
| 4 (t=4) | 0.0625 | 1024 | 4.000 | 0.08840 | 11.45% | 7.88% | 1.1313 | 1.1313 | x86_64 |
| 3 (t=8.00087, 已稳态) | 0.0833 | 576 | 8.001 | 0.09732 | 2.51% | 2.10% | 1.0275 | 1.0275 | x86_64 local |
| 4 (t=6, 已稳态) | 0.0625 | 1024 | 6.000 | 0.09175 | 8.09% | 5.17% | 1.0899 | 1.0899 | loong64 cluster |
| 5 (t=6, 已稳态) | 0.0500 | 1600 | 6.000 | 0.09109 | 8.75% | 6.51% | 1.0978 | 1.0978 | loong64 cluster |
| 6 (t=6, 已稳态) | 0.0417 | 2304 | 6.000 | 0.09080 | 9.04% | 6.58% | 1.1013 | 1.1013 | loong64 cluster |

三条硬结论：

1. **分辨率悬崖**：h/δ=2（cr=2）时流动根本建立不起来（u_center=-0.00079，ν_eff 反演为 -127.09），该 SDPD 离散至少要 h/δ≥3 —— 对我们自研 SPH 是明确的采样下限规格。
2. **cr=3（仓里 shipped 的算例设置）确实 <3%**：剖面 L2=2.81%（t=4）/2.10%（t=8），中心速度误差 2.09%/2.51%。但它**不是靠收敛**得到的。
3. **加密反而变差，ν_eff 收敛到 ≈1.101·ν 而非 1.0**：cr=3→4→5→6 的 ν_eff = 1.0275→1.0899→1.0978→1.1013（单调上升，趋于 1.101），即离散耗散在连续极限下比名义 η 高 **10.1%**；cr=3 的 2.8% 是两个同量级误差（支撑域截断偏低 + 极限黏性偏高）互相抵消的巧合，不是精度。这解释了为什么加密不收敛。

机制判定：**ν_eff 偏差 ~10% 是实测事实，机制未证实**。候选①配对耗散系数 (5η/3−ζ)/(10η/3+4ζ) 与二维 63/478 核归一化不匹配；候选②近壁 ghost 层影响。黑盒测量无法分离，需专门标定算例（Poiseuille 扫描 + 壁面滑移单独测量；Couette 线性剖面与 η 无关，标不出黏性），已登记为 GAP-SPH-WCSPH-VALIDATION 后续项。

### 4.2 启动瞬态（上游自己算例的结束时刻）

| 量 | 数值解 | Fourier 精确解 | 误差 |
|---|---|---|---|
| u_center | 0.01006 | 0.01006 | **0.026%** |
| 剖面 L2（占 u_max） | — | — | 0.56% |

注意：t=0.050347 时流动只到 u_max≈0.01006（稳态的 10.1%），此比对验证**脉冲启动响应与速度 Verlet 积分**的一致性，不代表稳态精度；上游算例 TIMING 结束时刻太早，其金色算例同样无法暴露稳态误差。

### 4.3 跨架构可复现性（x86_64 99 节点 vs loong64 n330）

同一 cr=4/t=6 算例：x86_64 剖面 L2=5.1729%、u_center=0.09175；loong64 L2=5.1729%、u_center=0.09175；两者 L2 相差 0.0e+00 个百分点，结论不依赖架构。

对照意义：同一份源码/二进制重编在两架构给出同一答案（结论不依赖架构），而上游金色文件在换 libm 时 ULP 级失败（§3.3）——正好是「逐字节回归 vs 容差回归」的对照实验。

### 4.4 图（本地生成，见 figures/）

- `f1_poiseuille_profiles.png`：三分辨率剖面 vs 解析抛物线 + 逐点偏差
- `f2_convergence.png`：L2 误差随 δ 变化（含 3% 验收线，展示非单调/不收敛）
- `f3_transient.png`：启动瞬态 vs Fourier 精确解
- `f4_field_poiseuille.png`：Poiseuille 速度场云图（t=1）
- `f5_field_drop.png`：shipped ellipse 算例的多相分布（zone=材料，含 ghost 层被写成 Wall 的现象）

---

## 五、注意点（踩过的坑 / 局限 / 适用范围）

1. **网络坑**：`raw.githubusercontent.com` 直连被策略 reset（Errno 104）；`git clone` 因仓 153 MB 超 120 s 被收割；匿名 api.github.com 403 rate-limit。可行路径=带 token 的 contents API 逐文件 base64 取（本审计按 sha256 固化 5 个关键文件）。
2. **构建坑**：干净树 `make` 必失败（dep.mk 无生成规则、scripts/dep 已删），不 `touch src/dep.mk` 会误判「仓不可构建」。
3. **输出坑**：tecplot 输出把真实粒子与 ghost 粒子写进同一 zone 且无标记；后处理必须几何裁剪（本审计用 0≤x,y≤box，ghost 必为域外镜像）。不裁剪时近壁速度统计被镜像速度污染（ghost 的 u_x 是反射值，符号相反）。
4. **就地覆盖坑**：`example/sdpd cases/<name>` 会覆写受版本控制的 `cases/<name>.txt`（rstout 与初值文件同名）。
5. **材料身份坑**：ghost 层材料固定为 materials[0]，多相算例的边界耗散由 FORCES[0][*] 决定，改材料参数时容易漏改边界行（ellipse.cfg 里 FORCES(1,2)/(2,1) 就带 shear_slip=0.5）。
6. **量纲坑**：k_B 硬编码 0.02 m 长度单位；thermal.cfg 的 T0=5e14 只有在该单位制下才有物理意义。
7. **本次比对的范围**：只验证了单相、层流、无表面张力的 Poiseuille 一条路径（σ=0、T0=0），**没有**验证多相界面（ellipse 无解析基准）、热涨落（thermal 无基准）、表面张力 Laplace 定律——这三条只能判 WITHHELD/无量化基准，如实登记。

---

## 六、误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 数值 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 稳态 u_center（cr=3，shipped） | 解析 g/(2ν)y(H−y) = 0.1000 | 0.09774（t=4）/ 0.09732（t=8） | 2.09% / 2.51% | **是（两时刻均 <3%）** |
| 稳态剖面 L2（cr=3） | 同上抛物线 | — | 2.81% / 2.10% | **是** |
| 稳态 u_center（cr=4，加密） | 同上 | 0.08840（t=4）/ 0.09175（t=6） | 11.45% / 8.09% | **否（>3%，且加密未改善）** |
| 稳态剖面 L2（cr=6，最密） | 同上 | — | 6.58% | **否** |
| ν_eff/ν（cr=6 连续极限） | 1.0 | 1.101 | 10.1%（偏高） | **否** |
| 启动瞬态 u_center（t=0.050347） | Fourier 级数精确解 0.01006 | 0.01006 | 0.026% | **是（但幅度仅稳态的 10.1%，只验证启动响应）** |
| 自带回归（2 例） | 上游金色文件 | — | 逐字节 diff 7 行（ULP 级） | **无法判定物理正确性**（无容差、无解析基准） |
| 多相界面 / 表面张力 / 热涨落 | 无基准（仓内、文献均无量化数据） | — | — | **WITHHELD（无基准，不许含糊通过）** |

**总判定**：本仓在**其自带设置（cr=3）**下对平面 Poiseuille 满足 <3% 的精度标准；但该精度不随加密保持（cr=4/6 为 5–11%），且反演出 ~10% 的黏性系统偏差，说明**精度靠误差抵消而非靠收敛**。作为我们平台的参考实现，**可用作算法行为规格与验证协议来源，不可作为精度基准来源**。验证面本身（2 个逐字节金色文件、无 CI、无解析比对、干净树不可构建）判 **STUB** 级，是本次可迁移的第一优先级资产（补验证面比迁算法更值）。

---

## 七、可迁移方案（≥2；kind 分级 + license 合规判定显式）

license 判定：上游 **GPL-3.0-only**，本平台仓 **MIT** → **代码级 adapt（复制/改写源文件）不合规**；全部可迁移项按 **reference**（依论文方程自行实现）+ **fit**（用其行为/算例协议自建工具）处理，实现代码全部自写，只引用论文与行为规格。

| id | kind | 内容 | GAP 锚 |
|---|---|---|---|
| SPHSOL-sphblitz-01 | reference | Hu&Adams(2006) 多相动量通量离散（压力梯度 (p_iV²+p_jV²) 加权 + 速度梯度修正、黏性横/纵向拆分 + 滑移调和平均、色梯度 dphi 的 CSF 表面张力） | GAP-SPH-MULTIPHASE |
| SPHSOL-sphblitz-02 | fit | 可移植的 WCSPH 验证协议（稳态抛物线 + Fourier 启动瞬态 + 间距扫描 + ν_eff 反演），一套 cfg、单次 ~10 s，能把幅值(黏性)误差与形状(滑移)误差分离 | GAP-SPH-WCSPH-VALIDATION |
| SPHSOL-sphblitz-03 | reference | 「不用 δ-SPH/粒子移动也能稳」的配方：√(γ|p|) 声速的 NR 人工黏性 + δ 正则 θ + 四重 dt 约束，作为 δ-SPH 消融实验的基线 | GAP-SPH-DELTA-SPH |
| SPHSOL-sphblitz-04 | reference | GPU 移植的成本结构（每步 2 次重建 cell list + ghost + 全配对表）与分辨率悬崖（h/δ=2 失效）量化 | GAP-SPH-GPU-SCALING |

明确**不可吸收**清单：3D（无）、热传导/相变（未并入 master）、并行（无）、δ-SPH（无）、FSI（无）、可微（无 AD 痕迹）。

---

## 八、机器块

[OSS-INV]{"campaign": "osssph-w2", "repo": "slitvinov/sph-blitz", "tier": 3, "methods": ["SPH+SDPD (Hu&Adams 2006 bulk/interface flux + Espanol&Revenga 2003 pair dissipation with FDT noise) src/lib.c:1-2,223-278,843-889", "kernel: 2D cubic spline, norm 63/478, support r<h src/lib.c:38-62,376-382", "eos: p=b0*(rho/rho0)^gamma src/lib.c:64-66,403-406", "density: summation rho_i=sum m_j W_ij src/lib.c:736-747; mass m_i=rho_i/sum W_ij src/lib.c:714-735", "momentum: (p_i V_i^2+p_j V_j^2) pressure gradient; viscosity split 5eta/3-zeta (transverse) / 10eta/3+4zeta (longitudinal) with slip-corrected harmonic pair means src/lib.c:223-278,205-218", "artificial viscosity: Neumann-Richtmyer, delta-regularised theta, c_s=sqrt(gamma|p|/rho) src/lib.c:249-254", "interface: colour-gradient dphi + CSF surface tension, per-pair sigma src/lib.c:629-651,748-765,350-367", "thermal: FDT noise, k_B hard-coded for 0.02 m length unit src/lib.c:157,843-889", "time integration: velocity Verlet, two half steps src/lib.c:783-831", "time step: adaptive dt=0.25*min(dt_g_vis,dt_surf,delta/(c_s+v_max)) src/lib.c:400-406,796-807", "boundary: mirror ghost (wall/free-slip/symmetry) + periodic images, 4x4 type table, corner ghosts, rebuilt every step src/lib.c:923-928,963-1079", "multiphase: MAXMAT=3 compile-time, nmat x nmat FORCES (sigma/shear_slip/bulk_slip), phi[MAXMAT^2] tensor src/sph.h:26,src/lib.c:350-367", "neighbour search: uniform cell grid 3x3 stencil, pair list rebuilt twice per step src/lib.c:176-222,444-458", "parallel: NONE (0 hits OpenMP/MPI/CUDA/OpenCL/pthread) - serial C", "missing: 3D, heat conduction, phase change, delta-SPH/particle shifting, FSI, differentiable"], "boundary": "mirror ghost + periodic image particles, 4 types (wall/free-slip/periodic/symmetry) + corner ghosts, rebuilt every step; ghost material hard-wired to materials[0] (src/lib.c:963-984, example/sdpd.c:23) so multiphase material identity is lost in the boundary layer", "gpu": "none - serial single-threaded C (0 hits OpenMP/MPI/CUDA/OpenCL/pthread); the cell list + ghost layer + full pair list are rebuilt twice per step, which is the GPU-porting hot spot", "validation": "2 bit-exact golden cases (ellipse, poiseuille), no CI (0 workflows), no tolerance/analytic reference/convergence study; clean tree does not even build (src/Makefile:17 include dep.mk, scripts/dep deleted); reproduced FAIL at ULP level on a different libm (7 diff lines); independent harness: steady Poiseuille L2 2.10% (cr=3 shipped) vs 5.17% (cr=4) vs 6.58% (cr=6), nu_eff/nu -> 1.101", "license": "GPL-3.0-only (COPYING, SPDX gpl-3.0)", "transferable": ["SPHSOL-sphblitz-01", "SPHSOL-sphblitz-02", "SPHSOL-sphblitz-03", "SPHSOL-sphblitz-04"]}[/OSS-INV]

[OSS-SOL]{"repo": "slitvinov/sph-blitz", "id": "SPHSOL-sphblitz-01", "kind": "reference", "summary": "Hu&Adams(2006) multiphase momentum-flux discretisation as a behavioural spec for TensorSPH: pressure gradient with (p_i V_i^2 + p_j V_j^2) weighting, viscosity split into transverse (5eta/3-zeta) and longitudinal (10eta/3+4zeta) parts with slip-corrected harmonic pair means, colour-gradient (dphi) CSF surface tension (src/lib.c:223-278,629-651). Re-implement from the paper equations - GPL code must not be copied into an MIT repo.", "gap": "GAP-SPH-MULTIPHASE"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/sph-blitz", "id": "SPHSOL-sphblitz-02", "kind": "fit", "summary": "Transportable WCSPH verification protocol (artifacts/oss_audit/sph_blitz/run_validation.py, MIT, written from textbook formulas): body-force plane Poiseuille with (a) steady parabola, (b) exact Fourier start-up transient, (c) particle-spacing sweep, (d) implied-viscosity inversion nu_eff=gH^2/(8 u_center); separates amplitude (nu) from shape (slip) error, ~10 s per run, one cfg file.", "gap": "GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/sph-blitz", "id": "SPHSOL-sphblitz-03", "kind": "reference", "summary": "Stability recipe WITHOUT delta-SPH / particle shifting: sqrt(gamma|p|) sound speed inside the Neumann-Richtmyer AV (bounded under tensile p<0), delta-regularised theta=u_ij.e_ij r_ij delta/(r_ij^2+0.01 delta^2) and a four-bound adaptive dt; useful as the no-density-diffusion ablation baseline when quantifying what delta-SPH buys us.", "gap": "GAP-SPH-DELTA-SPH"}[/OSS-SOL]
[OSS-SOL]{"repo": "slitvinov/sph-blitz", "id": "SPHSOL-sphblitz-04", "kind": "reference", "summary": "Negative evidence + cost structure for GPU work: the reference implementation rebuilds cell list, ghost layer and full pair list twice per time step with per-pair realloc (exactly what a GPU port must invert: persistent Verlet list + neighbour-sorted arrays); also quantifies the resolution cliff (h/delta=2 breaks the flow, h/delta>=5 reaches the asymptotic nu_eff).", "gap": "GAP-SPH-GPU-SCALING"}[/OSS-SOL]

---

## 九、产物清单

- `artifacts/oss_audit/sph_blitz/run_validation.py`：独立验证 harness（MIT，黑盒调用上游二进制）
- `artifacts/oss_audit/sph_blitz/visc_sweep.py`：集群端黏性扫描探针（在节点内编译后运行）
- `artifacts/oss_audit/sph_blitz/field_map.py`：场量云图生成
- `artifacts/oss_audit/sph_blitz/results.json`：全部数值（含 provenance sha256）
- `artifacts/oss_audit/sph_blitz/logs/cluster_probe.log`：集群作业 c1794237642_285 原始输出
- `artifacts/oss_audit/sph_blitz/figures/*.png`：5 张图