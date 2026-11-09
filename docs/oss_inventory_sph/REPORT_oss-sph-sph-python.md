# OSS-SPH 深盘波2 · pmocz/sph-python 技术盘点报告

> **scenario_key**: oss-sph-w2
> **mission_id**: 8680
> **盘点日期**: 2026-11-09
> **盘点人**: agent/m8680
> **目标仓**: pmocz/sph-python（tier3，m8638 普查初判：极简 numpy SPH，3b1b 风格教学名仓）
> **产物**: 本报告 + `artifacts/oss-sph/sph-python/`（源码快照 + 复跑验证工件）
> **方法**: git clone 全量深读（tier3 小仓一次深读完成全量清单）+ 无头复跑量化验证

---

## 一、案例介绍（物理背景 / 盘点范围）

### 1.1 仓库定位

pmocz/sph-python 是 Philip Mocz（Princeton，3Blue1Brown 视频作者）2020 年发布的**教学级 SPH 实现**：用 ~250 行 Python 模拟一颗 "toy star"（玩具星）的静水平衡结构。配套 Medium 算法长文（README.md 链接）。m8638 普查（wave-0）将其定为 tier3 教学名仓，本单为波2深盘。

### 1.2 物理问题

- **问题**: 自引力多正压星（polytrope）的静水平衡密度剖面
- **关键简化**: 引力**不是**牛顿自引力，而是线性回复力 `a -= lmbda·pos`（谐振势，sph.py:146）——lmbda 经 gamma 函数解析调参（sph.py:173-181，实测 lmbda=2.0120，与代码注释 "~2.01" 一致），使平衡态密度剖面为**精确抛物线** ρ(r)=lmbda/(4k)(R²−r²)（sph.py:199）
- **EOS**: 多正压 P=k·ρ^(1+1/n)，n=1（sph.py:106）
- **参数**: N=400 粒子，M=2，R=0.75，h=0.1，k=0.1，dt=0.04，tEnd=12（sph.py:158-167）

### 1.3 盘点范围

| 维度 | 覆盖 |
|---|---|
| GitHub 元数据 | 语言/license/提交史/目录/测试/CI（clone 实查；API 限流项如实标注） |
| 方法清单 | 核函数/密度/EOS/力/积分器/边界/时间步/多相/GPU——逐条 file:line |
| 验证面 | 自带算例、测试框架、量化数据（含本单复跑实测） |
| 可迁移 | ≥2 条，adapt/fit/reference 分级 + license 合规显式判定 |

---

## 二、GitHub 实查元数据

| 项 | 值 | 证据 |
|---|---|---|
| 默认分支 | master | clone 后 `git branch -a` |
| 提交数 | 8（2020-09-19 初版 → 2025-05-08 "ruff format"） | `git log` |
| 最近提交 | e4d8ab6 2025-05-08 "ruff format" | `git log --format` |
| 目录结构 | 4 文件：`sph.py`(6.7KB) · `README.md` · `LICENSE`(35KB) · `sph.png` | clone 目录清单 |
| 语言 | Python（唯一代码文件 sph.py） | 目录清单 |
| License | **GPL-3.0**（LICENSE 全文，头部 "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007" 实读确认） | LICENSE 头部实读 |
| 测试布局 | **无**（无 tests/、无 test_*.py、无 pytest 配置） | 目录清单 |
| CI | **无**（无 .github/ 目录） | 目录清单 |
| 打包 | 无 setup.py/pyproject.toml/requirements.txt | 目录清单 |
| 星数 | **insufficient**（api.github.com 本 IP 小时配额耗尽，HTML 页超时；wave-1 注册表 m8638 2026-11-09 记 66★，作次级来源） | API 返回 rate limit exceeded |

**活跃度判读**: 2020 初版后仅 2023 两次 typo 修正 + 2025 一次 ruff 格式化——**维护已停**，属"冻结的教学参考"，无演进风险也无演进红利。

---

## 三、源码级方法清单（逐条 file:line）

### 3.1 SPH 变体与核心算子

| # | 能力 | 定位 | 判读 |
|---|---|---|---|
| 1 | 3D 高斯核 W=(1/(h√π))³·exp(−r²/h²) | sph.py:13-25（式在 L25） | 归一化解析验证 ∫W d³r=1 ✓；非紧支集核（全空间作用，无截断） |
| 2 | 核梯度 gradW | sph.py:30-46（式在 L42-45） | 与解析导数 −2x/h²·W 逐项核对一致 ✓ |
| 3 | SPH 密度 ρ_i=Σ_j m·W_ij | sph.py:79-93（L92） | 标准形式 |
| 4 | 多正压 EOS P=k·ρ^(1+1/n) | sph.py:97-106（L106） | 标准 |
| 5 | 对称压力力 a_i=−Σ_j m(P_i/ρ_i²+P_j/ρ_j²)∇W_ij | sph.py:137-140（L138-140） | Monaghan 动量守恒对称形式 ✓ |
| 6 | "引力"=线性回复力 a−=lmbda·pos | sph.py:145-146 | **非牛顿自引力**，谐振势近似（教学简化）；lmbda 解析调参 sph.py:173-181 |
| 7 | "黏度"=线性阻尼 a−=nu·vel | sph.py:148-149 | **非 SPH 人工黏度**（无 Monaghan α 项、无速度差形式） |

### 3.2 时间步控制

- **固定 dt=0.04**（sph.py:161），Nt=300 步（sph.py:189）
- 积分器：KDK leapfrog（kick-drift-kick），sph.py:201-215
- **无 CFL 判据、无自适应 dt、无子循环**——dt 靠作者手调

### 3.3 边界处理

- **无任何边界处理**：无边界粒子/幽灵粒子/罚函数/反射；粒子靠谐振势自然约束（实测终态最大半径 0.61 < R=0.75）

### 3.4 多相 / 自由面

- **无**：单相、无界面追踪、无表面张力、无相变

### 3.5 GPU / MPI 并行

- **无**。纯 numpy 向量化；**无邻居搜索**——全对 O(N²) 矩阵（getPairwiseSeparations，sph.py:50-73），N=400 时 400×400 矩阵无压力，N~10⁴ 即不可用（内存 O(N²)）

### 3.6 初始条件与诊断

- IC：高斯随机位置（**未截断到球内**）+ 零速度，seed=42（sph.py:171,182-183）→ 初态非平衡，星体先弛豫
- 诊断：固定 100 点径向网格采样密度（sph.py:195-199）与解析抛物线对比（sph.py:240-242）——**仅目视，仓内无任何量化误差指标**

---

## 四、验证面盘点

| 项 | 状态 |
|---|---|
| 自带算例 | toy star 本身（`python sph.py` → sph.png），本单无头复跑成功（300 步，秒级） |
| 测试框架 | 无 |
| CI | 无 |
| 量化验证数据 | 仓内无；**本单复跑实测**（工件 `artifacts/oss-sph/sph-python/run_validation/`）：中心密度误差 −1.27%、星内平均绝对相对误差 3.63%、RMS 7.75%、最大 29.1%（边缘 r→R 处解析解→0 所致） |

**结论**: 该仓的"验证"= 一张目视对比图。量化后：N=400/h=0.1 的朴素设置**未收敛**（均值 3.63% > 3%），中心区尚可（−1.27%）。这是 GAP-SPH-ASTRO-SEDOV 的一个现成**负基线**。

---

## 五、可迁移方案（license 合规显式判定）

**License 总判定**: GPL-3.0 强 copyleft。TensorSPH 为独立许可仓 → **禁止逐字代码复用（adapt 出局）**；算法思想/数学形式不受版权约束，可 reference/fit（重实现）。

| ID | 分级 | 内容 | GAP 锚 |
|---|---|---|---|
| SPHSOL-sphpython-01 | reference | toy-star 静水平衡（谐振势+多正压 EOS→精确抛物线剖面）作为 TensorSPH 解析基准：移植设置、补 L2/max 量化判据；本单实测 N=400 基线 mean 3.63%/max 29.1% 直接入账为负基线 | GAP-SPH-ASTRO-SEDOV |
| SPHSOL-sphpython-02 | reference | 对称压力力形式（sph.py:138）+ 归一化高斯核/梯度对（sph.py:13-46）作为 TensorSPH 最小参考：核归一化单测、总动量漂移≈0 守恒单测、力反对称单测 | GAP-SPH-WCSPH-VALIDATION |
| SPHSOL-sphpython-03 | fit | 网格采样 SPH 密度 vs 解析剖面诊断模式（sph.py:195-199,241）泛化为 TensorSPH 验证 harness：任意解析剖面 + L2/max 误差自动报告 | GAP-SPH-WCSPH-VALIDATION |

---

## 六、注意点（踩坑 / 局限）

1. **API 限流**: 本 IP 未认证配额耗尽，星数/HTML 页不可达 → 星数标 insufficient，其余元数据全部来自 clone 实查（git 协议不受 API 限流）。
2. **"引力"是弹簧**: 教学简化，勿把本仓当自引力 SPH 参考；其 lmbda 调参公式（sph.py:173-181）只对 n=1 谐振势成立。
3. **高斯核无截断**: 全空间求和，物理上等价于无截断核；TensorSPH 若用紧支集核（M6/Wendland）不可直接对照其数值。
4. **复跑可复现**: seed=42 固定，本单复跑与仓内 sph.png 行为一致（终态半径 0.61）。
5. **维护冻结**: 最近实质改动 2023，2025 仅格式化——引用其数值时注明"冻结快照 e4d8ab6"。

---

## 七、误差判定（关键量 vs 基准，逐项）

基准 = 仓内解析剖面 ρ(r)=lmbda/(4k)(R²−r²)（sph.py:199），本单复跑 t=12 终态：

| 关键量 | 实测 | 是否 <3% |
|---|---|---|
| 中心密度 | −1.27%（2.7936 vs 2.8294） | ✅ 是 |
| 星内平均绝对相对误差 | 3.63% | ❌ 否 |
| 星内 RMS 相对误差 | 7.75% | ❌ 否 |
| 星内最大相对误差 | 29.1%（边缘区） | ❌ 否 |

**显式回答**: 关键量（中心密度）<3%；整体剖面**不满足** <3%（honest：该仓无量化验证，此为本单首次量化，N=400/h=0.1 未收敛）。

---

## 八、机器块

[OSS-INV]{"campaign":"osssph-w2","repo":"pmocz/sph-python","tier":3,"methods":["3D Gaussian kernel normalized (sph.py:13-25)","analytic gradW (sph.py:30-46)","SPH density sum (sph.py:79-93)","polytropic EOS P=k*rho^(1+1/n) (sph.py:97-106)","symmetric momentum-conserving pressure force (sph.py:137-140)","linear restoring force a-=lmbda*pos, NOT Newtonian gravity (sph.py:145-146)","linear damping a-=nu*vel, NOT SPH artificial viscosity (sph.py:148-149)","KDK leapfrog fixed dt=0.04, no CFL (sph.py:161,201-215)","O(N^2) full pairwise, no neighbor search (sph.py:50-73)","grid-sampled radial density diagnostic vs analytic parabola (sph.py:195-199,240-242)"],"boundary":"none (no boundary/ghost/penalty; harmonic potential confines, final max radius 0.61)","gpu":"none (pure numpy vectorization, O(N^2) memory)","validation":"in-repo visual-only (no tests/CI/quantitative data); re-run 2026-11-09 seed=42: center -1.27%, mean 3.63%, RMS 7.75%, max 29.1% inside star","license":"GPL-3.0 (LICENSE full text, header verified)"}[/OSS-INV]

[OSS-SOL]{"repo":"pmocz/sph-python","id":"SPHSOL-sphpython-01","kind":"reference","summary":"toy-star hydrostatic equilibrium (harmonic potential + polytropic EOS -> exact parabolic profile rho=lmbda/(4k)(R^2-r^2)) ported as quantitative analytic benchmark for TensorSPH; measured baseline N=400/h=0.1: mean 3.63%/max 29.1% = ready negative baseline; GPL-3.0 => reference-only, re-implement","gap":"GAP-SPH-ASTRO-SEDOV"}[/OSS-SOL]

[OSS-SOL]{"repo":"pmocz/sph-python","id":"SPHSOL-sphpython-02","kind":"reference","summary":"symmetric pressure force form a_i=-sum m(Pi/rhoi^2+Pj/rhoj^2)gradW (sph.py:138) + normalized Gaussian kernel/gradient pair (sph.py:13-46) as minimal reference for TensorSPH unit tests: kernel normalization, total-momentum drift ~0, force antisymmetry; GPL-3.0 => reference-only","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

[OSS-SOL]{"repo":"pmocz/sph-python","id":"SPHSOL-sphpython-03","kind":"fit","summary":"grid-sampled SPH density vs analytic profile diagnostic pattern (sph.py:195-199,241) generalized into TensorSPH validation harness: arbitrary analytic profile + L2/max error auto-report; pattern reuse, no code copy (GPL-3.0)","gap":"GAP-SPH-WCSPH-VALIDATION"}[/OSS-SOL]

---

**产物清单**:
- `docs/oss_inventory_sph/REPORT_oss-sph-sph-python.md` — 本报告
- `artifacts/oss-sph/sph-python/` — 源码快照（e4d8ab6 冻结）
- `artifacts/oss-sph/sph-python/run_validation/` — 复跑工件：profile.npz（剖面数据）· profile_compare.png（对比图）· error.json（误差溯源）
