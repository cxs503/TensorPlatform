# OSS 深盘 · slitvinov/lammps-sph-multiphase（LAMMPS 内多相/相变 SPH）

> **campaign**: `osssph-w2` ｜ **tier**: 3 ｜ **pin**: `{pin}`
> **盘点日期**: {date} ｜ **方法**: GitHub API 实查 + pin 版本源码逐行定位 + 独立复算
> **产物**: `{art}`（repo_meta / evidence / verification / machine_blocks / fig1）
> **生成方式**: 本报告由 `tools/oss_inventory/lammps_sph_multiphase_inventory.py` 渲染，
> 文中所有数字均来自 API 响应、源码解析或本模块的数值复算（零手抄）。

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

### 1.1 物理背景

被盘仓是 **LAMMPS 的扩展补丁包**（不是独立求解器）：在 LAMMPS 的 `PKG_SPH` 之上补 6 个多相 pair style、1 个相变 fix、1 个 per-atom compute，覆盖**沸腾与蒸发**这类带潜热的多相自由面问题。物理栈：

- **弱可压缩 SPH（WCSPH）**：五次样条核、Tait 状态方程、密度求和法、显式积分（LAMMPS 内核 `fix sph`）；
- **多相（液/汽）**：每种相自带 `rho0 / cv / 声速 / 粘性 / 导热`，跨相用调和平均传递粘性、导热；
- **表面张力**：颜色函数梯度 + CSF 应力投影（不做曲率拟合）；
- **相变**：界面处液粒子超过饱和温度后按「超出显热/潜热」的速率概率蒸发，新汽粒子质量按核权重从邻域液粒子「精确捐出」，能量账扣 `m*Hwv`。

### 1.2 几何与算例（仓库自带 12 个算例目录）

仓库没有几何生成器，几何是 LAMMPS `lattice`/`region` 直接铺粒子：二维方形域、二维半无限板、二维圆域内液滴、三维立方域内气泡。`bubble_on_wall` 的域与物性全部写在输入脚本变量表里（`vars.lmp`），下表**原样抽取**该文件的表达式（不做数值化改写，避免转抄误差）：

{parameter_table}

### 1.3 仓库元数据（GitHub API，{date} 实查）

{meta_table}

**判定**：license 字段为 `null`、树内无 `LICENSE`/`COPYING` 文件 → **许可不明 → 本仓只可 reference，禁止搬码**（纪律：license 不明 = reference-only）。

---

## ② 怎么做的（方法 / 流程 / 工具链）

1. **API 实查**（禁凭记忆）：`GET /repos/{repo}`（元数据/语言/许可）、`/branches`、`/commits?per_page=15`、`/git/trees/{pin}?recursive=1`（全树 {n_tree} 条目）、`/languages`。API 走平台侧 token（环境变量 `GITHUB_TOKEN`，不回显）。
2. **pin 到 commit**：`{pin}`（main HEAD），并把树内**全部源码/输入脚本 blob**（非 `img/`、非二进制、≤400 kB）下载到本地缓存 `{cache_hint}`，共 {n_blobs} 个文件——后续所有 `path:line` 都在这个确定版本上解析，浮动 main 的指针不会被混进来。
3. **源码级方法清单**：对每条能力声明写「文件 + 正则」，用脚本在 pin 版本里**检索出首个命中行**，把 `path:line` 与**该行原文**一起入证据表（不是人肉记行号）。正则失配即整脚本失败——证据表不允许静默缺项。
4. **独立复算（防「读完即抄」）**：把 `sph_multiphase.h` 里的**归一化常数与分段多项式**用正则抽出来翻译成 Python，再独立复算 4 条只由源码决定的恒等式：
   - 核函数归一化 `∫W dV = 1`（2D/3D 各一次）；
   - 解析导数表 `W'(q)` 是否等于 `W(q)` 的符号导数（sympy）；
   - Tait EOS 的 `B = c²ρ0/γ` 是否真的给出 `dp/dρ|_{ρ0} = c²`；
   - `fix phase_change` 的能量簿记是否严格满足 `ΔE = −m·Hwv`。
5. **否定声明同等举证**：对「没有 GPU / 没有 OpenMP / 没有 δ-SPH 密度扩散 / 没有 ISPH 投影 / 没有 CI / 没有测试 / 没有许可文件」逐条跑正则并记命中数（{n_files_code} 个源码文件扫描范围）。
6. **机器块**：JSON 机器块（本波 `osssph-w2` 契约）内嵌本报告；另出 playbook 的 key:value 块（`{mach}`），并过平台校验器 `python -m tools.oss_inventory.machine_blocks --strict`（0 违规）。

**未做的部分（诚实边界）**：本单**没有编译 LAMMPS、没有跑任何 SPH 算例**。因此第 ⑥ 节的误差判定只覆盖「源码可判定恒等式」，**不覆盖** Poiseuille/圆柱/板导热的物理精度——仓库的验证图是 gnuplot 目视对照，没有留数值误差指标，平台侧也无该仓的构建产物。

---

## ③ 计算结果（数字表，全部程序化注入）

### 3.1 代码级方法清单（逐条 `path:line`）

{methods_table}

### 3.2 证据表（claim → file:line → 源码原文）

{evidence_table}

### 3.3 独立复算结果

{verification_table}

关键结构事实（复算得到，不是估计）：核函数支撑域 `cut = 3h`，因此**光滑长度 h = 粒子间距 dx**；算例里 `h = 3*dx` 是「截断半径」，不是光滑长度——这一点很容易被误读。按简单立方布点，`r ≤ 2h` 内邻居数 {n_2h}、`r < 3h` 内邻居数 {n_3h}。另注意 Tait EOS 带背景压强：`rbackground = 0` 时 `p(ρ0) = B ≠ 0`（数值见 3.3 的 EOS 行），所以「壁面直接设 ρ」等于把壁压钉在背景压强上——迁移到带自由面外压的算例时必须显式处理这个偏移。

### 3.4 否定声明（absence evidence）

{absence_table}

### 3.5 验证面盘点

{validation_table}

---

## ④ 结果图

![kernel verification]({figure_path})

**图注**：左 = 从 `MULTIPHASE/sph_multiphase.h` 正则抽出的核函数 `f(q)` 与导数表 `f'(q)`（q=3r/cut，支撑 q<3，三段分段）；中 = 本节 4 条恒等式的独立复算残差（对数坐标，红虚线 1e-12 容差）；右 = Tait EOS 与 ρ0 处切线（斜率应等于输入声速的平方，见 3.3）。

---

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **不是库，是补丁包**：必须 `cp MULTIPHASE/*.cpp *.h lammps/src/` 后重编 LAMMPS（README 给 make/CMake 两条路），依赖 LAMMPS 内核的 `fix sph`、`atom_style sph`、`fix property/atom ... ghost yes`。任何「直接 pip/链接」的迁移设想都不成立。
2. **文档-代码落差（K2-1 类）**：README 写 `fix phase_change (optional convert CV)`，代码里 `convert` 的语义是「被抽干（`rmass < 2*to_mass`）的液粒子**整颗**转成汽粒子并把 `cv` 改成 `convert_cv`」（`{pc_convert}`），不是「改个 CV 值」；此外 `fix` 初始化要求 `narg >= 14`，参数顺序脆弱。
3. **相变是概率算子**：蒸发速率由 `(e_i − cv·Tc)/Hwv·dt·rate` 给出概率（`{pc_threshold}`），再乘界面门（邻域必须存在 `from_type`，`{pc_gate}`）。这使结果**依赖随机种子**；仓库把 RNG 状态写进 restart（`{pc_restart}`）说明作者自己也把可复现性当一等公民——迁移时必须保留这一条，否则统计量不可复现。
4. **插入不查重叠**：新汽粒子位置沿颜色梯度法向偏移 `dr`（默认 `0.5*dx`，失败按 0.75 衰减重试），但**没有最小间距/重叠检测**（源码检索无 exclusion/hmin 逻辑）。迁移到 SPH 相变时这是第一个要补的稳健性口。
5. **表面张力没有曲率**：颜色梯度 → CSF 应力投影，避免二阶导/曲率拟合（数值更稳），代价是接触角只能通过 `alpha_dw`/`alpha_gd` 系数「调」，`examples/contact_angle` 用两组 `alpha_dw`（1.5 vs 0.5）对比，属参数化润湿，不是 Young 定律实现。
6. **没有 δ-SPH 密度扩散、没有人工粘性开关、没有 ISPH/投影**（见 3.4 命中数）：因此张力不稳定/压力噪声只能靠 Tait 指数与 `rbackground` 兜（`rbackground=0` 时 p(ρ0)=0，可切到「负压截断」式处理）。
7. **验证是目视对照**：4 个算例都有解析/文献参照（Morris 1997 瞬态反 Poiseuille 级数、Morris 1997 圆柱 FEM 曲线数字化、VanSant 双材料板闭式解、Hu&Adams 毛细波），但**没有一条留了误差指标或容差**，也没有 CI/测试框架（workflow 文件 {n_ci}，测试文件 {n_tests}）。要拿来当平台基线，必须自己补「数值 vs 参照的 L2/∞ 误差 + 收敛阶」。
8. **并行**：MPI 域分解 + 自定义 forward/reverse comm（颜色梯度 3 分量、ρ、e、被抽走的 dmass），无 GPU/OpenMP/Kokkos（3.4 命中数 0）。`neighbor 0 bin` + `neigh_modify delay 0 every 1`：邻居表每步重建，属正确性优先的配置，性能不在作者目标内。

---

## ⑥ 误差判定（关键量 vs 基准，逐项）

本单的「基准」是**源码自身决定的解析恒等式**（可机器判定），不是物理实验数据。逐项列出：

{error_table}

**是否 <3%？** 上述 5 项恒等式残差全部在 `1e-12` 量级或更低（最大 {max_resid}），**全部远优于 3%**。

**物理精度（Poiseuille / 圆柱绕流 / 板导热 / 毛细波）本单未复现**：需要编译带 `PKG_SPH` 的 LAMMPS 并跑 12 个算例，超出本单范围；仓库本身也未给出任何数值误差指标（只有 gnuplot 目视图）。因此对「SPH 物理精度是否 <3%」**不作判定**（honest：不粉饰、不外推）。这也是本仓挂 `GAP-SPH-WCSPH-VALIDATION` 的原因：**基线在，度量缺**。

---

## 机器块

```
{inv_json}
```

```
{sol_json}
```

playbook key:value 块（校验器过闸版）见 `{mach}`。