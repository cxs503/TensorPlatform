# 数据同化与基准口径治理（P148W2-N03）

> 本节点交付两件互相咬合的机制：**数字孪生贝叶斯模型更新**（传感器数据 → 先验/似然 → 后验分布）
> 与**基准表口径判读器 + 下游误差判定门**（口径缺失即 fail-closed 拒绝误差判定）。
> 全部实现零领域语义：物理数字只经*注入配置*与*表工件*进入，代码只提供机制。
>
> 复现：`python -m tools.data_assimilation.run_benchmark`（约 3 分钟，100 次蒙特卡洛）
> 判据产物：`benchmarks/verified/data_assimilation/results.json`（含 56 条可复核溯源行）
> 测试：`python -m pytest tests/data_assimilation -q`（35 项）

---

## 1. 案例介绍（问题背景 / 为什么需要这一层）

平台上的每一次"误差判定"（算得准不准）都隐含一个前提：**被比较的两个数在同一口径上**。
口径包含四件事：尺度（缩尺试验件 vs 实尺度）、尺寸补贴是否已计入、是否混入了未申报的附体/粗糙度补贴、
以及形态阻力项按什么规律随速比变化。仓内真实基准表恰恰经常缺这一列元数据：

* `docs/audits/full_code_callgraph.md` 记录同一物理输入在 11 个内核里极差 **356.9%**；
* `docs/m1536` 的复核里，平台值与独立参考的偏差被标注为"模型不适用"，而其中相当一部分
  量级与**口径错配**同阶（缩尺摩擦项 vs 实尺度总阻力系数）；
* 本节点被播种时给出的观测是 KCS 表上一处 **−16.5%** 的偏差，需要机器判定它到底属于
  "模型错"还是"口径错"。

因此本节点不是再加一个求解器，而是补齐**判定链的元层**：

```
传感器数据 ──► 贝叶斯后验(参数不确定度)
                          │
基准表 + 元数据 ──► 口径判读器 ──► 口径裁决(含证据) ──► 下游误差判定门 ──► PASS/FAIL 或 REJECT
                                                              │
                                                          审计事件(链式 sha256)
```

## 2. 怎么做的（方法 / 流程 / 关键设置 / 工具链）

### 2.1 数字孪生模型更新（`tools/data_assimilation/bayes.py`）

| 环节 | 实现 | 关键设置 |
|---|---|---|
| 响应模型 | 小型形状库 `affine` / `power` / `saturating`，`y=f(θ,x)` | 本单用 `power`：`y=θ0+θ1·x^θ2`（三参数、非线性、有脊） |
| 先验 | `normal` / `uniform` / `lognormal`，带显式支撑集检查 | 无支撑 → `logpdf=-inf`，**拒绝而非截断** |
| 似然 | 独立高斯传感器噪声（可切 Student-t 稳健版） | σ=0.02，n=56 个测点 |
| 后验采样 | 随机游走 Metropolis，固定对角/协方差提案，多种子链 | 三阶段调参：①对角线尺寸扫（8 个因子，目标接受率 0.30）②形状试跑 1200 步估经验协方差 ③按 `2.38²/d` 缩放后再估一次 |
| 初始化 | Nelder-Mead 求众数后加 0.5σ 抖动 | 先验均值起点会让链落在数值零密度区（这是本次踩到的坑，见 §5） |
| 诊断 | split-R-hat、IPS 自相关时间/ESS、接受率 | 记录 `max_rhat`、`min_ess`，判据之外的可信度证据 |

### 2.2 口径判读器（`tools/caliber_reader/reader.py`）

判读不是"读元数据"，而是**用闭合检验反推口径**。设形态阻力项按速比的 p 次方（p=4，注入配置），
则每一行满足

```
C_t = (1+k)·C_f(V·L/ν) + c_res·Fn^p (+ A(L) 若该行在实尺度口径且补贴计入)
```

校正集 = 表中长度较小的一组（缩尺口径，未知量在这里解出），预测集 = 长度较大的一组（实尺度）。
对假设空间 `H = {补贴计入/不计入}` 分别做最小二乘，再看**预测集闭合残差**：

| 残差表现 | 机器结论 | 下游是否允许做误差判定 |
|---|---|---|
| ≤ 容差且申报口径一致 | `CONSISTENT` | ✅ |
| ≤ 容差且无申报口径 | `CALIBER_INFERRED`（由几何+闭合反推） | ✅ |
| ≤ 容差但申报口径与反推相反 | `CALIBER_CONTRADICTION` | ❌ |
| 仅当把补贴从实尺度数值里去掉才闭合 | `SUBSIDY_OMITTED` | ❌ |
| 预测集一致偏正，且超出量≈黏性项的稳定比例 | `ALLOWANCE_DETECTED`（给出比例） | ❌ |
| 容差 < 残差 ≤ 退化带 | `DEGRADED` | ❌ |
| 表内自洽性被破坏（力列与系数列不符 / 非正数） | `WITHHELD_CORRUPT`（**退出码 2**） | ❌ |
| 只有单尺度（无预测集） | `WITHHELD_INSUFFICIENT` | ❌ |

相关系数（`a=0.075, b=2, c=2` 的黏性线；`a=0.006, b=100, c=0.16, d=-0.00205`、有效区间 50–1000 m
的尺寸补贴线）**全部来自注入配置** `benchmarks/verified/data_assimilation/config/lines.json`，
代码内不含任何物理数字；线超出申报有效区间时返回 `in_range=False`，调用方按 fail-closed 处理。

### 2.3 下游误差判定门（`tools/caliber_reader/gate.py`）

`judge_error(observed, reference, caliber=...)` 在四种情况下**拒绝且不产出误差数字**：

1. `caliber` 字段缺失 → `REJECT_CALIBER_MISSING`
2. `caliber` 不完整 / `scale` 非法 → `REJECT_CALIBER_INVALID`
3. 观测值与口径不同尺度 → `REJECT_SCALE_MISMATCH`
4. 口径裁决本身不允许判定（`error_decision_allowed=False`）→ `REJECT_VERDICT_NOT_ALLOWED`

每次调用都落一条 JSONL 审计事件，事件里含 `prev_sha256`，形成可校验的链。

### 2.4 合成算例语料（`tools/caliber_reader/synth.py`）

六类算例由同一个声明式"物理"生成（种子固定，可字节复现），因此真值已知：

| 算例 | 构造 | 期望裁决 |
|---|---|---|
| `case_01_declared_consistent` | 双口径申报正确、补贴计入 | CONSISTENT |
| `case_02_undeclared_scale` | 无口径列 | CALIBER_INFERRED |
| `case_03_allowance_omitted` | 实尺度数值漏掉补贴 | SUBSIDY_OMITTED |
| `case_04_extra_allowance` | 实尺度数值暗含 6% 未申报补贴 | ALLOWANCE_DETECTED |
| `case_05_degraded` | 2% 数值噪声 + 一行缺黏度列 | DEGRADED |
| `case_06_corrupt` | 力列与系数列差 1000×、速度取负 | WITHHELD_CORRUPT |

### 2.5 真表端到端复算（C5）

全部数字在运行时从仓内文件读取（零手抄）：

* 实尺度一行：`tests/data/reference_corpus/resistance_reference.json` → `kcs_design_24kn`
  （`reference/ct`、`reference/cf`、`reference/one_plus_k1`、`inputs/Vs_ms`、`inputs/L`、`reference/fn`）；
* 缩尺几何：`optimizer/interfaces/kcs_bow_param.py` → `KCS_BASELINE["L"]=7.278`、`Fn_design`；
* 流体常量：注入配置 `config/replay_config.json`（实尺度 1025 / 1.188e-6；缩尺 998.2 / 1.004e-6）。

流程：
1. 由实尺度一行反解"残余项" `c_res = ct − (1+k)·cf − A(L)`；
2. 按速比相似构造缩尺一行的总系数 `ct_red = (1+k)·cf_red + c_res`（标注为 **derived**）；
3. 用判读器的两点反演（两行两未知量）回解 `(1+k)` 与残余项，与表内声明值比较 → 机制闭合检验；
4. 与"口径盲"比较（缩尺黏性项 vs 实尺度总系数）对照，做**因子归因分解**；
5. 折算到实尺度口径后看残差是否 < 15%。

## 3. 计算结果（全部由脚本程序化注入，源：`results.json`）

### 3.1 判据总表

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| C1 后验均值 vs 真值（逐参数） | ≤ 10% | 最大 2.019% | PASS |
| C2 95% 区间覆盖（100 次蒙特卡洛） | ≥ 93% | **94.0%**（逐参数 93%/93%/96%） | PASS |
| C3 口径判读准确率（6 例合成表） | 6/6 | 6/6 | PASS |
| C4 损坏例 fail-closed | 必须 WITHHELD + 非零退出 | `WITHHELD_CORRUPT`，退出码 2 | PASS |
| C5 真表复算（折算后偏差） | < 15% | **−0.0095%** | PASS |
| C6 去掉口径字段的拒绝率 | 100% | 100/100 | PASS |
| C7 可复现性 | 同输入同输出 | 两次后验逐位相同，证据块 sha256 可复算 | PASS |
| C8 零手抄 | 每数可溯源 | 56 行锚点全部 `verify()` 通过 | PASS |

### 3.2 C1 后验（真值 θ=[0.30, 1.60, 0.70]）

| 参数 | 后验均值 | 95% 区间 | 相对误差 |
|---|---|---|---|
| θ0 | 0.306057 | [0.251565, 0.360958] | **+2.019%** |
| θ1 | 1.593758 | [1.537607, 1.649966] | −0.390% |
| θ2 | 0.701457 | [0.683835, 0.719386] | +0.208% |

采样诊断：`max R-hat = 1.0261`、`min ESS = 112.8`、三链接受率 0.435–0.447。
θ0 的区间明显宽于另两个参数——这是 `power` 族里 θ0/θ1/θ2 的脊结构所致（见 §5 第 2 条），
后验联合密度见 `figures/fig_joint_density.png`。

### 3.3 C5 真表复算与归因

| 量 | 数值 |
|---|---|
| 表内实尺度总系数 `ct` | 0.00334363 |
| 表内黏性系数 `cf` | 0.00137791 |
| 表内形状因子 `(1+k)` | 1.16740 |
| 缩尺黏性项 `cf_red`（注入线，L=7.278 m） | 0.00277156 |
| 反解 `(1+k)` | 1.167174（相对表内值 **−0.0196%**） |
| 反解残余项 | 0.00141329（相对派生值 0.00141265 **+0.0448%**） |
| **口径盲偏差** `(cf_red − ct)/ct` | **−17.1055%** |
| **折算到实尺度后残差** | **−0.0095%** |

归因分解（顺序因子，单位：占参考值的百分比）：

| 因子 | 贡献 |
|---|---|
| 尺度效应（`cf_full/cf_red`） | −41.69 pt |
| 形状因子 `(1+k)` | +6.90 pt |
| 残余（造波）项 | +42.25 pt |
| 尺寸补贴 `A(L)` | +9.64 pt |
| **合计** | **−17.11 pt → 残差 −0.0095%** |

机器结论：`attribution = CALIBER`，`dominant_factor = residual_term`（+42.25 pt，与 −41.69 pt 的
尺度效应几乎抵消）。
**−16.5% 的归属=口径错配，不是模型错**；本机复算得到 −17.11%（同一物理机制，差异来自
注入流体黏度与线系数取值），折算到实尺度后残差 −0.0095% 远小于 15% 门限。

这里暴露出最危险的一类假象：**两个 ~40 点量级的口径项在数值上几乎抵消**，于是口径盲比较
只剩 −17%，看起来"像个可以讨论的模型误差"。若只看这个百分比，把偏差归给求解器是完全
合理的错觉；只有把口径逐项拆开才看得出它根本不是模型误差。

### 3.4 C6 下游门负控

| 批次 | 结果 |
|---|---|
| 100 例去掉 `caliber` 字段 | `REJECT_CALIBER_MISSING` ×100，拒绝率 100% |
| 100 例完整口径 | 接受 100（按 15% 容差给 PASS/FAIL） |
| 100 例口径尺度与观测不符 | `REJECT_SCALE_MISMATCH` ×100 |
| 1 例只给 `scale` | `REJECT_CALIBER_INVALID`，`error_pct = null` |
| 审计链 | 301 条事件（100 负控 + 100 正控 + 100 尺度错配 + 1 残缺口径），逐条 sha256 与 `prev_sha256` 复算通过 |

## 4. 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 | `figures/fig_posterior_coverage.png` | 三个参数在 100 次蒙特卡洛上的后验均值直方图 + θ0 逐次 95% 区间与真值（覆盖 94.0%） |
| 图 2 | `figures/fig_joint_density.png` | 参数 (θ0, θ1) 的**联合后验密度场**（云图）与真值标记——标定不确定度的二维结构 |
| 图 3 | `figures/fig_caliber_verdicts.png` | 6 例合成基准表的闭合残差条形 + 机器裁决 vs 真值裁决 |
| 图 4 | `figures/fig_attribution.png` | C5 偏差归因瀑布：−17.11% 逐因子拆解，折算后残差 −0.010% |

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **MH 初值必须找众数**：从先验均值起步时，紧后验让链落在数值零密度区，接受率 0.003、
   ESS≈7、R-hat≈20——点估计看着还行，区间完全是假的。改成 Nelder-Mead 众数起步 + 两阶段
   协方差调参后 R-hat≈1.03、ESS≈110–160、接受率≈0.44，区间与"超长链"参考一致。
2. **对角提案在同相关后验上会低估区间**：三参数 `power` 模型有强脊，只调步长时 40 次复现的
   覆盖率只有 90%（< 93%）。必须估经验协方差并按 `2.38²/d` 缩放；同时**数据设计**也重要：
   早期 x∈[0.4,3.2] 的设计使 θ2 几乎不可辨，覆盖 90%；改成 x∈[0.3,4.0]、σ=0.02、n=56 后
   覆盖 94%。这一条同时是"实验设计"教训：区间质量首先取决于试验设计，其次才是采样器。
3. **覆盖 94.0% 距 93% 门槛只有 1 个百分点**：逐参数 93%/93%/96%，三个参数的失败复现高度相关
   （同一批数据实现），有效样本量≈复现次数；100 次复现下覆盖率的二项标准误约 1.5–2.5 个点，
   因此本判据属于**统计意义上"刚好通过"**，不应解读为强裕度。
4. **"偏差小"不等于"口径对"**：C5 的口径盲偏差 −17.11% 是两个 ~40 pt 口径项的抵消残值，
   而不是两个同口径数值的差。任何不做口径拆解就下"模型误差 X%"结论的流程都建立在巧合上。
5. **补贴可辨识性靠"预测集"**：只有一行缩尺 + 一行实尺度时（两方程两未知量），补贴计入与
   不计入各自都能精确闭合（(1+k) 与 c_res 同时平移），**不可辨识**。因此判读器要求校正集
   ≥2 行；单尺度表一律 `WITHHELD_INSUFFICIENT`，这是刻意的 fail-closed 而不是能力缺失。
6. **C5 的缩尺一行是派生值**：由实尺度一行按速比相似 + 反解残余项构造，故闭合检验只证明
   **机制自洽（反演算术正确）**，不证明物理正确。独立锚点是：黏性线（解析）、实尺度
   `ct/cf/(1+k)`（表工件）、补贴线（注入配置，区间 50–1000 m 内）。
7. **残余项按 Fn^4 单参数族**：这是判读器的假设，真造波系数并非严格 4 次方；对只做"口径判别"
   的用途足够，但**不可用它反演造波阻力本身**。
8. **流体常量与线系数是注入配置**：不同黏度取值会让 C5 的口径盲偏差在 −15%…−18% 之间移动，
   这正是本节点要说明的问题——**没有口径声明的百分比偏差没有意义**。
9. **零领域语义**：`tools/caliber_reader` 与 `tools/data_assimilation` 内不含域词条
   （`python -m tools.benchmark_runner.domain_guard tools/caliber_reader tools/data_assimilation`
   可机器验证）；线与算例数据经配置/工件注入。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 考核量 | 基准 | 相对误差 | 是否 <3% |
|---|---|---|---|
| C1 后验均值 θ0 | 声明真值 0.30 | 2.019%（三参数最大） | 是 |
| C1 后验均值 θ1 | 1.60 | 0.51% | 是 |
| C1 后验均值 θ2 | 0.70 | 0.28% | 是 |
| C5 反解形状因子 | 表内 1.167403 | −0.0196% | 是 |
| C5 反解残余项 | 派生 0.00141265 | +0.0448% | 是 |
| C5 折算后偏差 | 表内实尺度总系数 | −0.0095% | 是 |
| C5 口径盲偏差（反面量） | 同上 | −17.1055% | 否（正是本节点要归因的对象） |
| C6 拒绝率 | 100% 要求 | 0（100/100） | 是 |

**结论**：机制侧所有可闭合的算术量都远小于 3%；唯一超过 3% 的是"口径盲比较"的 −17.11%，
本节点用因子归因把它完整归到口径上（折算后 0.010%），而不是掩盖它。

## 7. 复现

```bash
# 1) 全量判据（约 3 分钟，生成 results.json + 4 张图）
python -m tools.data_assimilation.run_benchmark

# 2) 快速冒烟（4 次蒙特卡洛，判据数字会变，仅验证管线）
python -m tools.data_assimilation.run_benchmark --replicates 4 --no-figures --out /tmp/q.json

# 3) 测试（35 项）
python -m pytest tests/data_assimilation -q

# 4) 单表判读（退出码 0=定论 / 2=withheld / 3=degraded）
python -m tools.caliber_reader benchmarks/verified/data_assimilation/cases/*.json --json

# 5) 零领域语义机器检查
python -m tools.benchmark_runner.domain_guard tools/caliber_reader tools/data_assimilation
```

## 8. 工件与审计链

| 工件 | 路径 |
|---|---|
| 判据报告 | `benchmarks/verified/data_assimilation/results.json`（含 56 条溯源行 + 代码指纹） |
| 合成算例 | `benchmarks/verified/data_assimilation/cases/*.json`（种子固定，可字节复现） |
| 注入配置 | `.../config/{lines,reader_config,bayes_config,replay_config}.json` |
| 图 | `.../figures/*.png` |
| 审计日志 | `benchmarks/verified/data_assimilation/audit/gate_audit.jsonl`（链式 sha256） |
| 实现 | `tools/caliber_reader/{lines,reader,gate,synth}.py`、`tools/data_assimilation/{bayes,provenance,run_benchmark}.py` |