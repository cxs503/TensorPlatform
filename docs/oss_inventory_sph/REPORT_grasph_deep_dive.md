# GraSPH 深盘报告（OSS-SPH 深盘波 2 · tier3 快速盘）

> **scenario_key**: oss-sph-w2 ｜ **mission_id**: 8682 ｜ **campaign**: osssph-w2
> **仓库**: hschwane/GraSPH ｜ **版本固定**: commit `ae8c6ff94bf8d041fc3ed81e2ddb355bcd45c9ed`（2020-03-10，git clone 实测 HEAD）
> **盘点日期**: 2026-11-09 ｜ **盘点人**: agent/m8682
> **机器证据表**: `artifacts/oss-sph/grasph_evidence.py`（23 条 claim→file:line + 独立复算）
> **元数据快照**: `artifacts/oss-sph/grasph_api.json`（GitHub API 实查）

---

## 一、案例介绍（物理背景 / 仓库定位）

GraSPH 是德国科布伦茨大学（University of Koblenz）本科毕业论文代码（README 首段，opus 链接），面向**自引力天体流体**（分子云→恒星形成）的 GPU SPH 实时可视化模拟器。物理域=天体 SPH（非工程 WCSPH）：单相理想气体 + 软化牛顿引力 + 人工黏度，无边界、无多相、无自由面。

**GitHub API 实查**（`artifacts/oss-sph/grasph_api.json`，2026-11-09 本单实拉；后续 languages/commits 端点遇未认证限流 403，如实标注）：

| 字段 | 值 | 来源 |
|---|---|---|
| 语言 | C++（+GLSL 计算着色器） | API + 源码 |
| License | **LGPL-3.0** | API `license.spdx_id` + 仓内 LICENSE 文件头 |
| 星数 | 23 | API `stargazers_count`（与 m8638 普查一致） |
| Forks / open issues | 2 / 0 | API |
| 默认分支 | master | API |
| 创建 / 最后 push | 2018-05-22 / 2021-05-30 | API |
| 最近提交 | ae8c6ff 2020-03-10 "some bugfixed" | git log（63 commits，2017-11-12 起） |
| 仓大小 | 414 KB | API `size` |
| 目录结构 | `src/`=mpUtils 图形框架（内嵌）；`exec/GraSPH/`=仿真本体；`shader/`=GLSL | clone |
| 测试布局 | **无**（无 test*、无 benchmark、无 .github/CI） | find 全仓 |

## 二、源码级方法清单（逐条 file:line，全部 @ae8c6ff）

### 2.1 SPH 变体：WCSPH（弱可压）+ 自引力

| 能力 | 定位 | 说明 |
|---|---|---|
| 密度（poly6 核） | `calculateDensity.comp:90-91` | `density += m_j·Wpoly6`；同时算 ∂ρ/∂h（:97）与 Balsara 用 div/curl（:100-105） |
| 压力（spiky 梯度，对称 P/ρ²） | `calculateAcceleration.comp:63,100,102-105` | `acc -= m_j(pod2i·∇W_i + pod2j·∇W_j)` |
| 核函数库 | `kernel.glsl:12-21,72-92,101-104,113-131` | poly6/spiky-grad 在用；**WviscLap、Wspline 定义但零调用**（grep 证实） |
| 人工黏度（Monaghan α + Balsara 开关） | `calculateAcceleration.comp:107-123` | `II=-0.5·fij·α·vij·vsig/ρij`，`vsig=ci+cj-3vij` |
| Balsara 开关 | `densityAccumulator.comp:76-82` | `div/(div+vort+0.0001·ci/h)` |
| 自适应 Balsara（密度 smoothstep 渐关） | `calculateAcceleration.comp:113-118` | 阈值 8192/32768（`Settings.h:50-51`） |
| dh/dρ 修正（Springel & Hernquist 2002） | `densityAccumulator.comp:68-73` | `1/(1+h·(∂ρ/∂h)/(3ρ))` |
| 状态方程 | `densityAccumulator.comp:53-62` | `P=a·ρ^ac`；a=0.06，ac=1（等温）或 7/5（ARTIFICIAL_HEATING，FRAG_LIMIT=2048） |
| 引力（软化牛顿，G=1 码元） | `calculateAcceleration.comp:97` | `ε²=hi·hj·eps_factor²`，EPS_FACTOR=0.2（`Settings.h:44`） |

### 2.2 时间步控制

| 能力 | 定位 | 说明 |
|---|---|---|
| leapfrog（KDK）积分 | `integrateLeapfrog.comp:55-82` | 首步/后续步双路径（`not_first_step` 开关） |
| 每粒子自适应 dt 判据 | `integrateLeapfrog.comp:81` | `min(C·h/maxVsig, sqrt(2·εg·h·eps/|a|))`；C=0.3、εg=0.04（`Settings.h:35-36`） |
| 全局 dt = min(粒子 dt) + clamp | `main.cpp:366-384` | clamp 到 [5e-6, 0.04]（`Settings.h:32-34`） |
| h 自适应 | `calculateH.comp:27` | `h=(3·Nn·m/(4πρ))^{1/3}`，clamp[HMIN,HMAX]=[0.025,2]，Nn=50；启动迭代 20 次（`main.cpp:249`） |
| 已知缺陷（作者自述） | `future_work_and_known_issues.md:4` | "finding the new timestep has probably issues" |

### 2.3 边界处理

- `simpleBoxBoundary.comp:28-77` 实现了轴对齐盒反射（reflectiveness 系数），**但全仓无任何 dispatch 调用**（grep `simpleBoxBoundary` 于 main.cpp/ParticleSpawner.cpp 零命中）→ **死代码；实际运行=无边界自由空间**。

### 2.4 多相 / 自由面

- **无**。单相气体；无表面张力、无颜色函数、无第二相（`calculateAcceleration.comp` 全文 135 行无相关项）。

### 2.5 GPU / 并行策略

| 项 | 定位 | 说明 |
|---|---|---|
| OpenGL 4.5 compute + SSBO(std430) | 全部 `.comp` | 无 CUDA/OpenCL/Vulkan/MPI（grep 零命中） |
| shared-memory tiling | `calculateDensity.comp:33,44-110` | WGSIZE=256，TILES_PER_THREAD 分块，barrier 同步 |
| 每粒子多线程 + 累加器归约 | `Settings.h:72-75`；`densityAccumulator.comp:44-51` | 密度/加速度各 16 线程/粒子，HYDROS_PER_PARTICLE 归约 |
| 邻居搜索 | 无（O(N²) 全对） | 作者自列 "add datastructure / tree code"（`future_work:17`） |
| 精度 | 仅 float32 | 64bit 在 future_work（:8） |
| 唯一 atomicAdd | `measureMass.comp:29` | GL_NV_shader_atomic_float，仅质量诊断用 |

### 2.6 初始条件与诊断

- IC：Hammersley 拟随机球（`sphereSpawn.comp:52-72`）+ 4 频 curl 噪声初速（`main.cpp:127-132`，散度自由投影 `doSPHCurl.comp:63-84`，dispatch 于 `ParticleSpawner.cpp:220`）+ 角速度（`angVel.comp:24`）。
- 单位制：L=pc/48、M=M☉、G=6.674e-11（`Settings.h:28-29`）；默认 16384 粒子、20 M☉、半径 6 内部单位（`Settings.h:39-41`）。
- 诊断：参考立方体质量（`measureMass.comp:27-30`，`main.cpp:348-362`）；FPS/模拟速率打印（`main.cpp:400-417`）。

## 三、验证面盘点

| 维度 | 状态 | 证据 |
|---|---|---|
| 自带算例/基准 | **无**（唯一"算例"=README 的 YouTube 星形成视频，定性） | README 第 5-7 行 |
| 测试框架 | **无** | find 无 test*/pytest/ctest |
| CI | **无** | 无 .github/ |
| 量化验证数据 | **无**（无守恒量检查、无 Sedov/Sod/KH、无收敛研究） | future_work:9 "fix energy/entropy conservation" 自认未做 |
| 可复算锚点 | 核归一化 / 时间单位 / h 公式 | 本单 `grasph_evidence.py` 独立复算（见 §五） |

**结论：验证面=零。该仓价值在 GPU 管线架构与天体 SPH 工程细节，不在精度。**

## 四、可迁移方案（license 合规显式判定）

**License 判定**：LGPL-3.0（弱传染，动态链接可用）。按 playbook K2-3 纪律：GPL 家族**不搬代码**——`fit`（直接移植）排除；`adapt` 仅限净室重写公式/思路；`reference` 只当参照。

| ID | kind | 内容 | GAP 锚 |
|---|---|---|---|
| SPHSOL-grasph-01 | **reference** | GPU SPH 管线架构参照：SSBO+shared-memory tiling+每粒子 16 线程+累加器归约+每粒子自适应 dt 的 OpenGL 4.5 实现范式（注意：仓内无扩展性数据、O(N²) 无树码，只借架构不借性能结论） | GAP-SPH-GPU-SCALING |
| SPHSOL-grasph-02 | **adapt** | 每粒子自适应 dt 判据（Courant+加速度双判据取 min + 全局 min + clamp）净室重写进 TensorSPH 时间步模块；判据为 Springel(2005) 标准公式，重写无 license 风险；GraSPH 的 clamp 边界与"首步 dt 不变"处理（`integrateLeapfrog.comp:35-53`）可作实现细节参照 | GAP-SPH-WCSPH-VALIDATION |
| SPHSOL-grasph-03 | **reference** | 天体 SPH 标准测试参照：Hammersley 球+多频 curl+角速度的 IC 生成器与 pc/M☉ 单位制，用于 GAP-SPH-ASTRO-SEDOV 测试面（自引力坍缩/Sedov 类）的 IC 与量纲设计参照 | GAP-SPH-ASTRO-SEDOV |

## 五、计算结果（程序化注入，零手抄）

`python3 artifacts/oss-sph/grasph_evidence.py` 输出：

| 复算量 | 值 | 期望 | 误差 |
|---|---|---|---|
| poly6 3D 归一化 ∫4πr²W dr | 1.000000 | 1.0 | 0.000% |
| spiky 3D 归一化 | 1.000000 | 1.0 | 0.000% |
| spline 3D 归一化 | 1.000000 | 1.0 | 0.000% |
| 1 内部时间单位 | 44834.9 年 | —（复算 `main.cpp:15-18` 公式） | — |
| h 公式球内邻居数 | 50.00 | 50（NUM_NEIGHBOURS） | 0.00% |

注：spiky 核本体未在该仓定义（只有梯度 `WspikyGrad`），复算用与仓内梯度常量 `-45/(πh⁶)` 自洽的标准 spiky 核 `15/(πh⁶)(h-r)³`。

## 六、结果图

`artifacts/oss-sph/grasph_kernels.png`：左=三核 W(r)（h=1，按 kernel.glsl 常量）；右=径向质量密度 4πr²W(r)（积分=1 的可视化）。本单为文档/审计任务，无流场云图；该图为核函数归一化验证图。

## 七、注意点（坑 / 局限 / 适用范围）

1. **死代码陷阱**：simpleBoxBoundary 存在但从未 dispatch——读 README/目录会误判"有边界处理"（K2-1 活例）。
2. **文档-代码落差**：README 称 "self gravitating fluid" 仿真，但无守恒量输出，能量/熵守恒作者自认未修。
3. **API 限流**：未认证 60 req/h 共享 IP，首查成功后续 403；star 数等以首查+普查双源为准。
4. **适用边界**：float32 + O(N²) + 单卡 → 仅 ≤10⁴ 粒子实时可视化级；不可作为精度基准源。
5. **tier3 快速盘声明**：全量方法清单已在一次深读内完成（全仓 1208 行仿真代码全读），无抽样。

## 八、误差判定

本单为审计/盘点（非精度验证），无物理量对标基准；独立复算 5 项全部 0.000% 误差（§五）。**是否 <3%：是（复算项）；物理精度对标不适用（仓内无基准数据，原因=仓无验证面）。**

---

[OSS-INV]{"campaign":"osssph-w2","repo":"hschwane/GraSPH","tier":3,"methods":["WCSPH poly6 density (calculateDensity.comp:90-91)","spiky-grad symmetric pressure P/rho^2 (calculateAcceleration.comp:63,102-105)","Monaghan viscosity + Balsara switch (calculateAcceleration.comp:107-123; densityAccumulator.comp:76-82)","adaptive Balsara density smoothstep (calculateAcceleration.comp:113-118)","dh/drho correction Springel-Hernquist 2002 (densityAccumulator.comp:68-73)","EOS P=a*rho^ac isothermal/adiabatic (densityAccumulator.comp:53-62)","softened Newtonian gravity G=1 (calculateAcceleration.comp:97)","leapfrog KDK + per-particle adaptive dt (integrateLeapfrog.comp:55-82)","h adaptation (3*Nn*m/4pi*rho)^(1/3) (calculateH.comp:27)","Hammersley sphere IC + curl-noise + angular velocity (main.cpp:125-133)"],"boundary":"simpleBoxBoundary.comp:28-77 exists but never dispatched (dead code); effectively unbounded free space","gpu":"OpenGL 4.5 compute, SSBO std430, shared-memory tiling WGSIZE=256, 16 threads/particle + accumulator reduction (Settings.h:72-75); O(N^2) no tree; no MPI/CUDA/OpenCL; float32 only","validation":"none: no tests, no CI, no benchmarks, no conservation checks; only qualitative YouTube demo; author self-reports energy/entropy conservation unfixed","license":"LGPL-3.0","transferable":["SPHSOL-grasph-01 reference GPU pipeline architecture -> GAP-SPH-GPU-SCALING","SPHSOL-grasph-02 adapt per-particle adaptive dt criterion (clean-room) -> GAP-SPH-WCSPH-VALIDATION","SPHSOL-grasph-03 reference astro IC generator + pc/Msun units -> GAP-SPH-ASTRO-SEDOV"]}[/OSS-INV]

[OSS-SOL]{"repo":"hschwane/GraSPH","id":"SPHSOL-grasph-01","kind":"reference","summary":"OpenGL 4.5 compute 管线架构参照：SSBO+shared-memory tiling(WGSIZE=256)+每粒子16线程+累加器归约+每粒子自适应dt；仓内无扩展性数据且O(N^2)无树码，只借架构不借性能结论；LGPL-3.0 不搬码","gap":"GAP-SPH-GPU-SCALING"}[/OSS-SOL]

[OSS-SOL]{"repo":"hschwane/GraSPH","id":"SPHSOL-grasph-02","kind":"adapt","summary":"每粒子自适应时间步判据 min(C*h/maxVsig, sqrt(2*eps_g*h*eps/|a|)) + 全局min + clamp[MIN_DT,MAX_DT] + 首步dt不变（integrateLeapfrog.comp:35-82, main.cpp:366-384）净室重写进 TensorSPH 时间步模块；判据为 Springel(2005) 标准公式，LGPL-3.0 下重写公式安全","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"hschwane/GraSPH","id":"SPHSOL-grasph-03","kind":"reference","summary":"天体 SPH 测试面 IC 参照：Hammersley 拟随机球+多频 curl 散度自由初速+角速度（main.cpp:125-133, doSPHCurl.comp:63-84）与 pc/Msun 单位制（Settings.h:28-29，1 时间单位=44834.9 年，本单复算），用于 Sedov/自引力坍缩测试面的 IC 与量纲设计；LGPL-3.0 只参照","gap":"GAP-SPH-ASTRO-SEDOV"}[/OSS-SOL]
