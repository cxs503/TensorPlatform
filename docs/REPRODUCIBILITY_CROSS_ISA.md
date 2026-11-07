# 跨指令集浮点可复现性计量：碎冰 / 流变 / 校准三类物理工件的容差协议

**任务**：IN22-N08（战役 DAG 节点，物理域：数值方法 / 验证与确认计量）
**工件**：`tools/repro_digest/`（协议 + pytest fixture + 三基准内核）、`artifacts/cross_isa_repro.json`（机器可读判据）、`artifacts/repro_digest/`（原始逐次运行值、图）
**结论一句话**：在 loongarch64 三台物理机上，三个物理工件**逐 bit 可复现**（117 个机×变体×基准组合、351 次运行，相等率 100 %）；跨机分岔为零，**全部分岔来自代码生成（FMA 收缩）与 libm 路径**；朴素 bit-digest 门在 108 个受检配置中失败 12 个（11.1 %），本报告声明的「量化 + max-ULP」协议失败 0 个，同时仍能拒收 1e-9 相对扰动（18/18）。

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

浮点可复现性不是一个抽象的程序语言问题，它决定**数值证据能否跨机器互认**：一条「同一份代码在两台机器上给出同一份阻力 / 流变 / 校准结果」的声明，是数值结论能够被审计的前提。本任务挑选三类在实践中确有跨平台搬运需求的物理工件：

| 工件 | 物理问题 | 几何 / 离散 | 关键参数 | 复现性机制（有意植入） |
|---|---|---|---|---|
| **H1 通道 60 s**（碎冰通道） | 冰水通道强制流动，冰体积分数 $\phi(z)$ 使有效黏度随温度变化 $\nu_{\rm eff}=\nu_0 e^{-\beta(T-T_{\rm ref})}$；温度场与速度场耦合<br>$\partial_t u=\nu_{\rm eff}\nabla^2 u+f,\ \partial_t T=\alpha\nabla^2 T$ | 一维通道 $H=1$ m，$N=64$ 点，无滑移壁，$T(0)=263.15$ K（冰封壁）/ $T(H)=271.15$ K（开水面） | $\nu_0=4\times10^{-3}$，$\beta=0.05$ K$^{-1}$，$\alpha=5\times10^{-4}$，$f=0.02$ m/s$^2$，$\Delta t=0.01$ s，6000 步 = **精确 60 s** | 三点差分 stencil（可被 FMA 收缩）、`exp()`（libm）、积分量定序求和 |
| **H2 幂律槽道**（流变） | Ostwald–de Waele 剪切变稀流体在平板间充分发展流动：$\frac{d}{dz}\left(\mu\dot\gamma\frac{du}{dz}\right)=-G$，$\mu=K\dot\gamma^{\,n-1}$ | 一维狭缝 $H=1$ m，48 个内点，壁面 $u=0$ | $K=0.35$ Pa·s$^n$，$n=0.6$，$G=1$ Pa/m，Picard 迭代 **固定 300 轮**，每轮 Thomas 三对角求解 | `pow()` 每点每轮调用（libm 密集）、Thomas 前/后替代（串行依赖求和）、流量积分 |
| **H5 校准链** | 阻力/冰拖曳三阶段校准：A 幂律黏度 $(K,n)$ → B 拖曳律 $(a,b)$（权重 $w_j=\dot\gamma_j^{\hat n-1}$，把 A 的指数带进 B）→ C 附加阻力 $(c,p)$（3 轮 IRLS，权重含 $\hat b$） | 每阶段 12 个合成测点；固定 LCG 噪声 0.3 %，无时钟/无环境依赖 | 真值 $K=0.35,n=0.60,a=2.10,b=-0.25,c=0.150,p=2.00$ | `pow/exp/log` 全链路、三层嵌套归约、IRLS 除法放大末位差 |

**为什么这三个工件能承重**：它们的浮点敏感机制互补——H1 是**显式时间推进**（误差不放大，只游走），H2 是**迭代 + 病态串行求解**（误差可被条件数放大），H5 是**链式校准**（上一阶段末位差被下一阶段放大）。任何单一工件都不足以校准容差门。

内核位于 `tools/repro_digest/csrc/`（C11 + libm，无第三方依赖），每个内核以 `%a`（C99 精确十六进制）输出所有量，**内核本身不做哈希**；`--emit-digest` 入口在 Python 侧统一计算 digest，保证「算什么」与「怎么判」分离：

```bash
python3 -m tools.repro_digest.emit_digest --bench h1 --emit-digest
python3 -m tools.repro_digest.matrix ref    --outdir artifacts/repro_digest/ref --label ctl99
python3 -m tools.repro_digest.matrix node   --refdir artifacts/repro_digest/ref --outdir <dir> --label <node>
python3 -m tools.repro_digest.matrix assemble --summaries a b c --out artifacts/cross_isa_repro.json
```

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **参考定义**：`base = -O2`（工程默认）与 `canon = -O0 -ffp-contract=off -fno-tree-vectorize`（严格 IEEE 顺序语义）两套参考值，均 3 次重跑。所有候选变体同时与两者比较，因此「漂移」既有工程口径（vs 默认构建），也有归因口径（vs 严格语义）。
2. **构建矩阵（13 变体）**：`-O0/-O1/-O2/-O3`、FMA 开/关（`-ffp-contract=off|fast`）、自动向量化开/关（`-fno-tree-vectorize`）、CPU 型号（`-march=la464/la664`，ISA 无关槽位由 `MARCH_PROFILES` 映射，x86 上自动换成 `-march=x86-64-v2/v3`）、`-ffast-math`（IEEE 语义之外，仅作边界证据）、`-DREPRO_LIBM_ALT=1`（把 `pow/exp/log` 改走 long-double 入口，即另一条舍入路径——受控的 libm 代理）。
3. **执行**：每个 (机 × 变体 × 基准) **重复 3 次**，共 39 组合/机 × 3 机 = 117 次运行；节点侧运行用 `run_cluster` 把工作树 tar 直达 n315/n330（`stage_dir`），结果 tar 取回，避免手工抄数。
4. **两层判据**（`tools/repro_digest/ulp.py`）：
   - **canonical quantization**：把量映射到 `format(x, '.Ng')` 的规范十进制串，再对串取 SHA-256；
   - **ULP 门**：`ulp_distance` 在单调整数键线上计算两个 double 之间相隔多少个可表示数（含跨零、跨符号）。
   两层都与度量尺度无关，但一层怕「低于格点」的噪声、另一层怕「近零抵消」，故必须并联。
5. **计量分类（本任务的关键修正，见 ③.5）**：把每个输出量声明为 `observable`（物理/交付量，走双层门）或 `diagnostic`（近零抵消型求解诊断，走绝对容差）。声明写在 `tools/repro_digest/spec.py`，每条容差都有实测依据。
6. **归因**：以 `canon` 为参照，比较「FMA 开/关」「向量化 开/关」「libm 替换」三组配对漂移；再用 H1 的 1 s 分辨率轨迹探针定位**首个分岔检查点**。
7. **负控**：向参考记录注入 1e-9 相对扰动（标量与场各一次），协议必须拒收；另加 1e-13 扰动用例证明「量化层看不见、ULP 层仍能抓」。
8. **pytest 接口**：`tools/repro_digest/pytest_plugin.py` 提供 `assert_reproducible(ref, cur, ulp_gate=k)` fixture 与 `repro_gate_k`（读工件里声明的 $k$），`tests/conftest.py` 一行注册，任何 digest 生产基准都可一行接入。

## ③ 计算结果

<!-- BEGIN:GENERATED -->
### 3.1 Headline numbers (all injected from `artifacts/cross_isa_repro.json`)

| 判据 | 实测值 |
|---|---|
| 持平性地板：同机 3 次重跑 bit-digest 相等率 | **100.0 %**（117 个 (机 × 变体 × 基准) 组合，351 次运行） |
| 持平性地板：同机 3 次重跑 max ULP | **0** |
| 朴素 bit-digest 门（跨机 × 跨 flags）失败率 | **12/108 = 11.1 %** |
| 协议门（量化 + ULP）失败率 | **0/108 = 0.0 %** |
| 声明 ULP 门 $k$（全局 / 分基准） | **65536**；H1 64，H2 32768，H5 65536 |
| 声明量化位数（分基准） | H1 12，H2 11，H5 10 位有效数字 |
| 负控：1e-9 相对扰动被拒 | **18/18** |
| 实测 $k$ 的来源 | $4\times$ 实测最大漂移 1.06e+04 ULP，向上取 2 的幂 |

### 3.2 Machine table (架构串 / 编译器 / libc / CPU)

| machine | arch | kernel | compiler | libc | CPU model | python |
|---|---|---|---|---|---|---|
| ctl99 | loongarch64 | 6.6.52-1.7.lns23.loongarch64 | gcc (GCC) 12.3.0 20230508 (Anolis OS 12.3.0-9) | 2.38 | Loongson-3C6000/D | 3.11.6 |
| n315 | loongarch64 | 6.6.52-1.7.lns23.loongarch64 | gcc (GCC) 12.3.0 20230508 (Loongnix OS 12.3.0-9) | 2.38 | Loongson-3C6000/D | 3.11.6 |
| n330 | loongarch64 | 6.6.52-1.7.lns23.loongarch64 | gcc (GCC) 12.3.0 20230508 (Loongnix OS 12.3.0-9) | 2.38 | Loongson-3C6000/D | 3.11.6 |

构建矩阵（13 个变体，逐机执行；`MARCH_OLD/NEW` 由 ISA 映射表填充）：

| variant | flags |
|---|---|
| O0 | `-O0` |
| O1 | `-O1` |
| O2 | `-O2` |
| O3 | `-O3` |
| O2_nofma | `-O2 -ffp-contract=off` |
| O3_nofma | `-O3 -ffp-contract=off` |
| O3_fmafast | `-O3 -ffp-contract=fast` |
| O3_novec | `-O3 -fno-tree-vectorize` |
| O3_novec_nofma | `-O3 -fno-tree-vectorize -ffp-contract=off` |
| MARCH_OLD | `-O3 -march=la464` |
| MARCH_NEW | `-O3 -march=la664` |
| O3_fastmath | `-O3 -ffast-math` |
| O2_libm_alt | `-O2 -DREPRO_LIBM_ALT=1` |

### 3.3 三基准 × 三机（基准构造 `-O2`，每格 3 次重跑）

| bench | machine | arch | base flags | bit-digest 相等 | 量化 digest 相等 | max ULP | max rel |
|---|---|---|---|---|---|---|---|
| H1 | ctl99 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H2 | ctl99 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H5 | ctl99 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H1 | n315 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H2 | n315 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H5 | n315 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H1 | n330 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H2 | n330 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |
| H5 | n330 | loongarch64 | -O2 | 是 | 是 | 0 | 0.0e+00 |

> 同一 ISA、同一 gcc 12.3 仅发行版不同（Anolis 12.3 vs Loongnix 12.3，glibc 均 2.38）时，三个基准逐 bit 相同：本生态内**跨机器不是**分岔源，分岔全部来自**代码生成与 libm 路径**。


### 3.4 flags 敏感性矩阵：相对 `-O2` 的 max ULP（观测类量）

| variant | H1 | H2 | H5 | 备注 |
|---|---|---|---|---|
| O0 | 0 | 0 | 0 |  |
| O1 | 0 | 0 | 0 |  |
| O2 | 0 | 0 | 0 |  |
| O3 | 0 | 0 | 0 |  |
| O2_nofma | 0 | 0 | 0 |  |
| O3_nofma | 0 | 0 | 0 |  |
| O3_fmafast | 1 | 3136 | 1.06e+04 | 朴素门失败 |
| O3_novec | 0 | 0 | 0 |  |
| O3_novec_nofma | 0 | 0 | 0 |  |
| MARCH_OLD | 0 | 0 | 0 |  |
| MARCH_NEW | 0 | 0 | 0 |  |
| O3_fastmath | 5 | 2351 | 5537 | 朴素门失败 |
| O2_libm_alt | 0 | 4704 | 0 | 朴素门失败 |

（三机结果逐 bit 相同，故只列一机；完整 39 行/机见工件 `benches.*.table`。）


### 3.5 量化位数敏感性（为什么不是所有基准都用 12 位）

| bench | variant | 12 位 | 11 位 | 10 位 | 9 位 | max ULP |
|---|---|---|---|---|---|---|
| H1 | O3_fmafast | 通过 | 通过 | 通过 | 通过 | 1 |
| H1 | O2_libm_alt | 通过 | 通过 | 通过 | 通过 | 0 |
| H1 | O3_fastmath | 通过 | 通过 | 通过 | 通过 | 5 |
| H2 | O3_fmafast | **失败** | 通过 | 通过 | 通过 | 3136 |
| H2 | O2_libm_alt | **失败** | 通过 | 通过 | 通过 | 4704 |
| H2 | O3_fastmath | **失败** | 通过 | 通过 | 通过 | 2351 |
| H5 | O3_fmafast | 通过 | 通过 | 通过 | 通过 | 1.06e+04 |
| H5 | O2_libm_alt | 通过 | 通过 | 通过 | 通过 | 0 |
| H5 | O3_fastmath | 通过 | 通过 | 通过 | 通过 | 5537 |

> 声明规则：量化网格必须高于该工件实测噪声地板 100×。H2 的 12 位网格只比其 1e-13 的天花板高一个量级，实测出现跨舍入边界（1/9 个观测量翻转）；降到 11 位后稳定。


### 3.6 首个分岔归因（相对于严格 IEEE 参考 `-O0 -ffp-contract=off -fno-tree-vectorize`）

| bench | FMA 开 | FMA 关 | 向量化开 | 向量化关 | 替换 libm | 漂移下降比（开/关） |
|---|---|---|---|---|---|---|
| H1 | 1 | 0 | 0 | 0 | 0 | FMA inf / 向量化 not_significant / libm not_significant |
| H2 | 3136 | 0 | 0 | 0 | 4704 | FMA inf / 向量化 not_significant / libm inf |
| H5 | 1.06e+04 | 0 | 0 | 0 | 0 | FMA inf / 向量化 not_significant / libm not_significant |

首个分岔检查点（轨迹探针逐检查点比对，H1 分辨率 1 s）：

| bench | 首个分岔时刻（step） |
|---|---|
| H1 | O3_fmafast@180, O3_fastmath@60 |
| H2 | O3_fmafast@1, O3_fastmath@1, O2_libm_alt@75 |
| H5 | O3_fmafast@1, O3_fastmath@1 |

### 3.7 物理正确性（判据 15 % 口径，逐项显式回答）

| kernel | reference used | measured | error | within 15 %? |
|---|---|---|---|---|
| H2 power-law slit | velocity profile vs exact solution of $\mu\dot\gamma$ ODE | 1.685e-03 | 0.17 % | yes (limit 15 %) |
| H2 power-law slit | flow rate $Q$ vs analytic integral | 0.246411 vs 0.247098 | 0.278 % | yes (limit 15 %) |
| H5 calibration chain | 6 fitted parameters vs synthetic truth (noise 0.3 %) | K=0.34994 n=0.60003 a=2.0983 b=-0.2492 c=0.1505 p=2.0017 | 0.31 % (worst of 6) | yes (limit 15 %) |
| H1 channel 60 s | steady-state momentum balance $fH - 2\tau_w$ after 60 s | -2.334e-04 vs fH=0.02 | 1.17 % of driving force | consistent (still transiting) |

### 3.8 机器可算验收判据逐条对表

| 判据 | 结论 |
|---|---|
| (a) 同架构 3 次重跑 digest 相等率 100 % | **满足**：117 组合全部 bit-identical，max ULP=0 |
| (b) 跨机量化后 digest 相等率 100 %% 且 max ULP ≤ k | **跨机满足**（9/9 格，0 ULP）；**跨指令集未执行**——本生态无 x86 主机，见 §5 局限 |
| (c) 负控：注入 1e-9 相对扰动必须拒收 | **满足**：18/18 全部拒收（max ULP 量级 4.6e6–8.9e6，量化层同时翻转） |
| (d) 归因可复现：关闭 FMA 后漂移下降比 >10× | **满足**：三个基准的 FMA 开/关漂移比均为 `inf`（关掉 FMA 后漂移归零） |
| (e) 三基准 × 两架构表（架构串/编译器/flags） | **部分满足**：三基准 × **三机（均 loongarch64）** × 13 flags 完整入工件；x86 行为 honest gap |
| (f) 协议实现单测 ≥10 项 | **满足**：`tests/test_repro_digest_protocol.py` 19 项（ULP 边界/NaN/Inf/空输入负例/门限/artifact 契约） |
<!-- END:GENERATED -->

### 3.9 图

![Fig.1](../artifacts/repro_digest/figs/fig1_ulp_histogram.png)
*图 1　逐个观测量相对 `-O2` 参考的 ULP 漂移分布（FMA 收缩版 + 替代 libm 版）。H1 只有 1/134 个量发生漂移（1 ULP，误差只游走不放大）；H2 有 306/312 个量漂移（迭代 + Thomas 串行求解把末位差放大 10²–10³ 倍）；H5 有 16/18。*

![Fig.2](../artifacts/repro_digest/figs/fig2_flags_heatmap.png)
*图 2　flags 敏感性热图（3 基准 × 13 变体，色标 $\log_{10}(1+\text{max ULP})$，数字为实测 max ULP）。`-O0/-O1/-O2/-O3`、FMA 关、向量化开/关、CPU 型号全部逐 bit 相同（0 ULP）；唯一非零列是显式 FMA 收缩、`-ffast-math` 与替代 libm。*

![Fig.3](../artifacts/repro_digest/figs/fig3_divergence_trace.png)
*图 3　H1 首个分岔定位：FMA 收缩版在 $t=1.8$ s（第 180 步）首次与参考不同，$|\Delta u|$ 峰值 1.1e-16 m/s（约 1 ULP），到 60 s 时探针值又回到逐 bit 相同——典型的**末位游走而非误差增长**，正是「量化层应吸收、ULP 层应看见」的那类差。*

## ④ 注意点（踩过的坑 / 局限 / 适用范围）

1. **近零抵消量会毁掉任何相对度量**。H2 的 `momentum_residual` 在 `-O2` 下是**逐 bit 的 0.0**，FMA 版是 1.5e-13，于是「ULP 距离」= 7.3e14。这不是数值问题，是度量问题：ULP 与 12 位有效数字都是**相对**度量，只对自身量级代表其计算尺度的量有意义。协议因此引入 `observable / diagnostic` 分类（`spec.py`），诊断量走绝对容差（实测 6e-14，容差 1e-11）。
2. **12 位并非处处可用**。H2 的噪声地板 1e-13 只比 12 位网格低一个量级，实测出现跨舍入边界翻转（1/9 个观测量），降到 11 位才稳。故协议声明的位数是**按工件实测校准**的：H1 12 位、H2 11 位、H5 10 位，规则为「网格 ≥ 100× 实测噪声地板」。这符合「$k$ 取实测保守上界」的本意，也避免把「容差」写成拍脑袋常数。
3. **同 ISA 跨机器完全无损**。99（Anolis gcc 12.3 / glibc 2.38）与 n315/n330（Loongnix gcc 12.3 / glibc 2.38）三机 27 个基线组合全部逐 bit 相同。因此本生态内**跨机不是分岔源**——分岔只来自代码生成与 libm。这一条是实测结论，不是假设：它把「跨架构风险」的范围收窄到**编译器/库**，而不是「换一台机器」。
4. **`-ffast-math` 不进验收包络**。实测其漂移（H2 2351 ULP、H5 5537 ULP）与 FMA 收缩同量级，并非「爆炸」；但它允许重组，对其它代码可以任意改变结果，故按**政策**排除在包络外，实测值仅作边界证据列在工件 `gate_evidence.fastmath_out_of_envelope`。协议不能因为「这次恰好没炸」就为它背书。
5. **libm 轴是受控代理**。`REPRO_LIBM_ALT` 把 `pow/exp/log` 走 long-double 入口再舍回 double，得到另一条舍入路径（loongarch64 上 long double 是 128 位软浮点，x86 上是 80 位 x87）。它能测出**量级**（H2 4704 ULP，是三者中最大），但不能替代「真的换一份 glibc/libm 构建」。
6. **容差驱动的迭代被刻意排除**。三个内核都用**固定步数/固定迭代轮**，因为「迭代次数不同」是另一类失效（非算术分岔），混进来会让归因不可读。凡用 `tol` 收敛判据的求解器，本协议的 $k$ 不能直接搬用。
7. **H2 的中心线黏度奇点被 1e-12 地板截断**：$\mu=K(\dot\gamma+10^{-12})^{n-1}$，物理上剪切变稀流体的 $\mu(0)\to\infty$，数值上必须正则化；本实现用的是简单地板（实测 $\mu\in[0.28,3.13]$，未进奇点区），工程上更规范的做法是 $\mu=K(\dot\gamma^2+\dot\gamma_c^2)^{(n-1)/2}$。这是本基准的建模局限，不影响可复现性结论。

## ⑤ 误差判定（对照基准，逐项显式回答）

| 关键量 | 基准 | 误差 | 是否 < 3 % |
|---|---|---|---|
| H2 速度剖面 $\|u-u_{\rm exact}\|_2/\|u\|_\infty$ | 幂律狭缝解析解 | 1.69e-3（0.17 %） | **是** |
| H2 流量 $Q$ | 解析积分 0.247098 | 0.28 % | **是** |
| H5 六个校准参数 $(K,n,a,b,c,p)$ | 合成真值 | 最大 0.31 %（$b$ 项） | **是** |
| H1 动量平衡残差 $fH-2\tau_w$ | 驱动力 $fH=0.02$ | 1.17 % 的驱动力（60 s 尚未完全定常） | **是**（且该项是诊断量，非物理输出） |
| 可复现性判据 | 声明的 $k=65536$ ULP（1.5e-11 相对） | 实测最大 10592 ULP（1.5e-12 相对） | **是**（4× 余量） |

无基准项说明：H1 的 60 s 瞬态过程**没有解析解**（黏度-温度耦合非线性），故只给出动量平衡与壁面切应力一致性，不给「对实验值误差」；这不影响可复现性计量（本任务的判据是量值可复现，不是量值正确）。

## ⑥ 诚实 FAIL / 未完成项

1. **x86 腿未执行（honest gap，不可覆盖）**。任务书要求「n315/n330(loong64) 与 x86 控制面各跑 3 次」，但 2026-11 的本生态**没有 x86 主机**：控制面 99 本身已是 loongarch64；两节点均 loongarch64；无 qemu-user、无 docker/binfmt、无交叉工具链（`gcc -dumpmachine = loongarch64-anolis-linux`），唯一 x86 路径是非交互的 GitHub CI。故判据 (b)(e) 的 x86 列**如实标为未执行**，验收表只到「三机同 ISA × 13 flags」。好消息是矩阵与位数/门限逻辑完全 ISA 无关（`MARCH_PROFILES` + 纯 stdlib），一旦有 x86 主机，三条命令即可补齐该列，无需改代码。
2. **「跨架构」在本报告中的实际含义**：已实测覆盖的是**代码生成与 libm 分岔机制**（FMA 收缩、向量化、等价优化级别、替代舍入路径、CPU 型号），这些正是 x86 与 loong64 之间真正会变的量；未实测的是**指令集语义本身**（x87 80 位超精度、x86 libm 与 glibc 的具体实现差异）。
3. 未把 H1/H2/H5 接入平台 `/api/v1/calculations/*` 端点（本节点交付的是计量协议与工件，不是生产 API）；接入需要在平台解冻后另行讨论。

## ⑦ 审计链与复现方式

```bash
# 1. 参考值（控制面 99）
python3 -m tools.repro_digest.matrix ref  --outdir artifacts/repro_digest/ref --label ctl99
# 2. 本机全矩阵（39 组合 × 3 次）
python3 -m tools.repro_digest.matrix node --refdir artifacts/repro_digest/ref \
        --outdir artifacts/repro_digest/local --label ctl99
# 3. 节点（经 run_cluster stage_dir 直达，无需在节点安装任何依赖）
#    python3 -m tools.repro_digest.matrix node --refdir artifacts/repro_digest/ref \
#            --outdir artifacts/repro_digest/n315 --label n315
# 4. 合并 + 定门
python3 -m tools.repro_digest.matrix assemble \
        --summaries artifacts/repro_digest/local/summary.json \
                    artifacts/repro_digest/n315/summary.json \
                    artifacts/repro_digest/n330/summary.json \
        --out artifacts/cross_isa_repro.json
# 5. 图 + 报告表格（数字全部从工件注入）
python3 -m tools.repro_digest.make_figures
python3 -m tools.repro_digest.gen_report_md
# 6. 协议单测（19 项，含 artifact 契约）
python3 -m pytest tests/test_repro_digest_protocol.py -q
```

工件清单：`artifacts/cross_isa_repro.json`（判据 + 三机表 + 归因 + 负控 + 局限）、`artifacts/repro_digest/ref/`（参考值与 bundle）、`artifacts/repro_digest/{local,n315,n330}/summary.json`（逐机 39 组合原始摘要）、`artifacts/repro_digest/figs/`（图 + 元数据）。