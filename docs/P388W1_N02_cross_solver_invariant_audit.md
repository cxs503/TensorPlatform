# P388W1-N02 — 跨求解器物理不变量审计（`invariant-audit/1.0`）

> 节点：`p388w1-n02-solver-invariant-audit`（战役 DAG，TensorPlatform）。落库：`tools/invariant_audit/`。
> 纪律：本仓零领域语义——机制代码不含任何真实船名/真实航道/真实工况/域判据数字；一切物理内容经
> `config/invariant_audit/plans/*.json` 注入。审计台账：`artifacts/invariant_audit/run_matrix/`。

## 1. 案例介绍（物理背景 / 关系 / 参数）

**问题**。既有平台的可信度机检是**单判据**的：`tools/verification_harness` 问「这个数
和权威值是否一致」。这只覆盖了可信度的一半——若某个求解器在一个没有权威值的工况上跑，
或权威值本身来自同一族离散，单判据无从判断。另一半是**结构律**：任何正确的离散实现都
必须满足的守恒、对称、量纲一致。这些律不需要参考数据，只用求解器自己的输入输出即可判定，
因此可以对任意求解器、任意工况做机器化回归。

本节点把这类判定做成平台能力：一个域无关的**不变量审计器**，输入是声明式审计计划
（目标=可调用对象+参数块，不变量=kind+参数块+容差），输出是**每（求解器-工况, 不变量）
对的 PASS/FAIL/WITHHELD/ERROR 台账**与可复核证据包。

**四类不变量（kind）与它们的判据**

| kind | 判据（残差 → 混合容差带 `|r| ≤ atol + rtol·scale`） | 物理含义 |
|---|---|---|
| `conservation` | `r = final − initial − flux` | 离散质量/动量/能量守恒（任一不透明量名，由配置给出） |
| `covariance` | `r = ‖f(Mx+o) − (P f(x)+q)‖_∞` | 旋转/反射协变（`M=P`）、伽利略平移协变（`M=I,o=Δu; P=I,q=Δu·T`） |
| `dimension_consistency` | 声明公式的量纲指数向量 ≟ 声明单位 | 量纲一致性（输出单位 vs 实现公式） |
| `relation` | `r = q_obs − Π x_i^{e_i}` | 代数关系一致性（如「力 = 质量×加速度」） |

**审计对象（应用层合成模型，`tools/invariant_audit/anchors/`）**。为给出闭合判定，锚点刻意
选纯数学/纯算术模型：周期网格线性输运（有限体积守恒型 vs 插值型）、对角线性响应映射、
量纲后处理器。合成域标签：`synthetic-grid-transport` / `synthetic-linear-response` /
`synthetic-postprocess`。**无任何船型/航道/工况词**。

关键注入参数（`config/invariant_audit/plans/synthetic_solver_matrix.json`）：

| 参数 | 值 | 说明 |
|---|---|---|
| cells / length | 200 / 1.0 | 周期网格单元数与物理长度 |
| stretch | 0.0 / 0.4 | 网格拉伸（0=均匀；0.4=单元宽度正弦变化，min 0.003 / max 0.007） |
| velocity / dt / steps | 0.2 / 0.01 / 50 | CFL = 0.4（含调制时峰值 0.6 < 1） |
| velocity_modulation | 0.5 | 逐步速度调制 `u_k = u(1+0.5 sin(2πk/N))`，`Σ sin = 0` |
| pulse_center / width | 0.3 / 0.05 | 高斯脉冲（远离周期缝，漂移 0.1 L 不跨越） |
| 容差（守恒） | rtol 1e-10 ~ 1e-12 | 精确守恒律应落在舍入级 |
| 容差（协变） | rtol 1e-9 ~ 1e-12 | 同上 |
| 容差（量纲/关系） | 精确 / rtol 1e-9 | 量纲为有理指数精确比较 |

## 2. 怎么做的（方法 / 流程 / 工具链）

```
tools/invariant_audit/
  dimensions.py       有理指数（Fraction）量纲代数；基符号全部注入，未知符号 fail-closed
  model.py            计划/目标/不变量/结果数据模型 + 状态折叠（PASS/FAIL/WITHHELD/ERROR）
  checks.py           四类判定器 + safe_eval（AST 白名单，无 eval）
  engine.py           计划校验（fail-closed）→ 执行 → 台账（字节确定）
  bundle.py           sha256 链 + 环境指纹 + 篡改分类（CONTENT/STRUCTURAL/CHAIN）
  cli.py              python3 -m tools.invariant_audit run|verify
  report_figures.py   图 + 图数据 JSON（程序化注入，零手抄）
  anchors/            应用层：合成模型（机制层不 import，方向由 AST 测试证明）
config/invariant_audit/plans/synthetic_solver_matrix.json   10 目标 / 16 判定对
config/invariant_audit/neutrality_vocab.json                零域语义词表（注入）
tests/invariant_audit/                                      59 个 pytest 用例
```

流程（每个目标）：

1. `resolve_callable(dotted_path)` → 以 `config` 调用一次，取观测字典（`_` 前缀键为元数据，
   `_dimensions` 为量纲声明）。
2. 逐不变量判定。**证据不足 → WITHHELD**（`observable_missing:*`/`declaration_missing:*`），
   绝不静默升级为 PASS；**接口违约（形状不符/可调用对象抛异常）→ ERROR**，与「物理违规」
   严格区分；**违反律 → FAIL**。
3. 目标状态折叠：有 FAIL→FAIL；否则有 ERROR→ERROR；否则有 WITHHELD→WITHHELD；否则 PASS。
4. 写入台账 `ledger.json` + `LEDGER.md` + 每目标记录，全部进 sha256 链证据包；
   `verify` 重算并分类篡改。

关键工程决策：

* **复用而非重造**：容差与误差约定复用 `tools.verification_harness.model.Tolerance`
  （混合带 `atol + rtol·|expected|`），哈希/链/环境指纹复用 `...evidence` 的公开原语。
  这正是「单判据机检 → 跨求解器不变量审计」的成熟化路径：同一判据约定、同一取证链。
* **一个协变判定器覆盖三种对称性**：`f(Mx+o) = P f(x)+q` 的仿射对；旋转/反射取
  `M=P`、平移取 `M=I,o=Δu; P=I,q=Δu·T`。判定器不理解物理，只做线性代数。
* **量纲用有理指数精确比较**：`Fraction` 而非 float，`length**2 · length**-2` 严格等于 1。
* **字典序 + 12 位有效数字舍入**：两次运行 `ledger.json` 字节相同（测试断言）。

## 3. 计算结果（数字表，程序化注入）

### 3.1 判定矩阵（`artifacts/invariant_audit/run_matrix/ledger.json`）

| 目标（求解器-工况对） | 不变量 | 状态 | 残差 | 尺度 | 判据 rtol |
|---|---|---|---|---|---|
| `synth-grid-flux-form` | mass-conservation | **PASS** | 1.39e-17 | 8.86e-2 | 1e-12 |
| `synth-grid-flux-form` | boost-covariance | **PASS** | 8.33e-17 | 1.25e-1 | 1e-9 |
| `synth-grid-flux-form-stretched` | mass-conservation | **PASS** | 0 | 8.86e-2 | 1e-12 |
| `synth-grid-interpolating` | mass-conservation | **FAIL** | 3.26e-05 | 8.86e-2 | 1e-10 |
| `synth-grid-interpolating` | boost-covariance | **FAIL** | 2.04e-04 | 1.24e-1 | 1e-9 |
| `synth-grid-unreported-flux` | mass-conservation | **WITHHELD** | — | — | 1e-12 |
| `synth-response-isotropic` | rotate-30deg | **PASS** | 0 | 9.20e-1 | 1e-12 |
| `synth-response-isotropic` | reflect-x | **PASS** | 0 | 8.00e-1 | 1e-12 |
| `synth-response-grid-aligned` | rotate-30deg | **FAIL** | 6.00e-1 | 1.52 | 1e-9 |
| `synth-response-grid-aligned` | reflect-x | **PASS** | 0 | 2.00 | 1e-12 |
| `synth-boost-heuristic` | boost-covariance | **FAIL** | 5.63e-4 | 1.27e-1 | 1e-9 |
| `synth-postproc-ok` | force-dimension | **PASS** | 0 | 1 | — |
| `synth-postproc-ok` | force-relation | **PASS** | 0 | 7.00 | 1e-12 |
| `synth-postproc-unit-bug` | force-dimension | **FAIL** | 2（失配项数） | 2 | — |
| `synth-postproc-formula-bug` | force-relation | **FAIL** | 3.50 | 10.5 | 1e-9 |
| `synth-postproc-formula-bug` | force-dimension | **PASS** | 0 | 1 | — |

汇总：**目标** PASS 4 / FAIL 5 / WITHHELD 1 / ERROR 0；**判定对** PASS 9 / FAIL 6 /
WITHHELD 1 / ERROR 0（共 16 对）。

**召回与误报**：植入缺陷集合 {interpolating 质量、interpolating 协变、grid-aligned 旋转、
boost 启发式、postproc 单位、postproc 公式} 6 项全部被判 FAIL（召回 6/6=100%）；
已知良好集合 {flux-form, flux-form-stretched, isotropic response, postproc-ok} 全部 PASS
（误报 0/4）。`synth-postproc-formula-bug` 的 `force-dimension` 判 PASS 是**正确**结果：
该实现声明的单位确实写对了，错在公式——因此只由 `relation` 判据检出，说明四类判据互补而非冗余。

### 3.2 关键量（`artifacts/invariant_audit/figures/figure_data.json`）

| 量 | 数值 | 来源 |
|---|---|---|
| 守恒型最终相对质量漂移（stretch 0.4） | 0.0（≤1e-16） | `mass_trace.flux_drift_final` |
| 插值型最终相对质量漂移（stretch 0.4） | −3.67e-4 | `mass_trace.interpolating_drift_final` |
| 网格细化漂移（cells=50/100/200/400/800，插值型） | 2.24e-3 / 8.71e-4 / 3.67e-4 / 4.03e-4 / 1.57e-4 | `mass_refinement` |
| 网格细化漂移（同，守恒型） | ≤9.0e-10（舍入级） | `mass_refinement` |
| 旋转协变残差（等向映射，0–90°） | 恒 0 | `rotation_sweep` |
| 旋转协变残差（轴对齐映射，5°/30°/90°） | 5.39e-2 / 3.95e-1 / 6.00e-1 | `rotation_sweep` |
| 平移协变 Δd（保守网格，Δu=0.05，T=0.5） | 0.025000（期望 0.025000） | `boost_sweep` |
| 平移协变 Δd（速度标度启发式，同） | 0.025562（偏差 2.2%） | `boost_sweep` |

### 3.3 证据与确定性

* 证据包 `artifacts/invariant_audit/run_matrix/`：13 条记录（plan/ledger/LEDGER.md + 10 目标记录），
  sha256 链 + 环境指纹；`root_digest = 53e820e7e663d91e6c8c366a74b6294466c112c1caa70d62f89bfcfd3d07cbfd`；
  `verify` 结论 `ok=true`，篡改类空。
* 确定性：同环境两次运行 `ledger.json`/`LEDGER.md`/`plan.json` 字节相同（测试断言）。
* 篡改检测：改台账内容 → 分类 `CONTENT`；在包里塞文件 → `STRUCTURAL`；改清单里记录的
  sha256 而不重算链 → `CHAIN`（三条测试各自断言）。

### 3.4 验收判据（机器判定，`artifacts/invariant_audit/run_meta.json`）

由 `scripts/p388w1_n02_acceptance.py` 一次跑完「审计 → 证据复核 → pytest → 零域词扫描」，
所有字段程序化产出（零手抄）：

| 判据 | 定义 | 实测 | 结论 |
|---|---|---|---|
| A1 零领域语义 | 8 个机制文件 × 24 个禁用域词，命中 0 | hits=[] | ✅ |
| A2 不变量族覆盖 | ≥4 类 kind | 4 类（conservation / covariance / dimension_consistency / relation） | ✅ |
| A3 灵敏度 | 植入缺陷 5 目标全部 FAIL | 5/5 | ✅ |
| A4 特异度 | 已知良好 4 目标全 PASS，无额外 FAIL | FAIL 集合 == 植入集合 | ✅ |
| A5 诚实 WITHHELD | 缺观测 ⇒ WITHHELD 且 residual 为 null | 1 对，residual=None | ✅ |
| A6 证据链 | 13 条记录 sha256 链+root_digest 复核通过 | ok=true，篡改类空 | ✅ |
| A7 测试 | `pytest tests/invariant_audit -q` 全绿 | 59 passed, 0 failed | ✅ |

判定对合计 16（9 PASS / 6 FAIL / 1 WITHHELD / 0 ERROR）；
`bundle_root_digest = 53e820e7…d07cbfd`，13 条 bundle 记录。

## 4. 结果图

![fig1](artifacts/invariant_audit/figures/fig1_verdict_matrix.png)

**图 1** 判定矩阵（行=求解器-工况对，列=不变量，颜色=状态）。绿 PASS / 红 FAIL /
黄 WITHHELD。可直接看出：同一求解器的不同不变量可以独立通过或失败（如
`synth-response-grid-aligned` 反射 PASS 而旋转 FAIL），以及「不报流量账」的目标是
WITHHELD 而非 PASS。

![fig2](artifacts/invariant_audit/figures/fig2_mass_drift_trace.png)

**图 2** 拉伸网格上离散质量守恒漂移随时间的演化（相对量）。守恒型（通量守恒/望远镜求和）
在舍入级；插值型（不给面通量记账）线性累积到 −3.67e-4。

![fig3](artifacts/invariant_audit/figures/fig3_mass_refinement.png)

**图 3** 漂移 vs 网格细化。守恒型恒在 1e-16–1e-9（纯舍入），插值型在 1.6e-4–2.2e-3
且非单调收敛（脉冲宽度固定、拉伸网格与非均匀 CFL 交互）——即该缺陷**不是某一套网格的偶然**。

![fig4](artifacts/invariant_audit/figures/fig4_rotation_sweep.png)

**图 4** 旋转协变残差 vs 旋转角。等向映射（权相等）对所有角度精确协变；轴对齐映射
（权不等）只在恒等变换（0°）与轴反射下精确，随角度单调恶化到 0.6。

![fig5](artifacts/invariant_audit/figures/fig5_boost_sweep.png)

**图 5** 参考系平移（boost）下的响应变化 Δd vs Δu。保守网格更新与期望 `Δu·T` 完全重合；
引入绝对速度标度的启发式偏离（Δu=0.05 时 +2.2%）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **坑：均匀周期网格上，任何一致插值格式都自动守恒**。首版用「线性插值半拉格朗日」
   作为「非守恒」对照，实测质量漂移仅 2.3e-13 → 判 PASS。原因是插值权重逐行和为 1，
   求和恒等。**结论（可复用知识）**：在均匀网格上用「质量守恒」区分守恒型/插值型格式是
   **无效判据**；必须引入非均匀单元（`stretch`）或通量账本（`flux` 观测）才有分辨力。
   本节点因此改成「拉伸网格 + 通量账本」两组配置，四次修订后才让判据具备区分度。
2. **坑：协变变换必须保持参数标量/向量形状**。把标量 `velocity` 写成 `[0.25]` 会让
   被审计实现抛 `TypeError`，判定器需保留原类型，否则真缺陷会被掩成 ERROR。
3. **局限（证据）**：链哈希无密钥。同时重写载荷与整份清单的一致性篡改无法仅靠哈希发现
   ——因此台账与代码同仓入库、附环境指纹；链只证明**内部一致性**，不证明作者。
4. **局限（覆盖面）**：本轮 10 个目标全是合成算术模型。跨求解器「平台能力」的机制面是
   通用的（任意 `module:func` + 参数块），但**尚未接入真实求解器仓**（TensorLBM/FVM/...）。
   接入只需写一个 plan 文件与一层观测适配器（返回不透明量名 + `_dimensions`），零机制改动；
   该接线属于后续节点，不在本节点声称完成。
5. **局限（判据强度）**：`covariance` 只对**线性/仿射**变换有意义（旋转、反射、平移）。
   非线性坐标系变换（曲线网格）需要 `M(x)` 逐点变换，当前未支持。
6. **量纲判据依赖被审计方自报**：`_dimensions` 由求解器声明；声明与实现同错则检不出
   （这正是 `relation` 数值判据的补充价值）。
7. **WITHHELD 的语义**：它既不是通过也不是失败，而是「证据不足」。审计台账把它单列，
   避免「不报账 = 合格」的偷懒路径。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

| 关键量 | 基准 | 实测 | 相对误差 | <3%？ |
|---|---|---|---|---|
| 守恒型离散质量守恒 | 精确 0（望远镜求和） | 1.39e-17（尺度 8.86e-2） | 1.6e-16 | **是** |
| 守恒型平移协变 Δd（Δu=0.05） | 解析 `Δu·T = 0.025` | 0.0250000 | 3.3e-15 | **是** |
| 等向映射旋转协变 | 精确 0 | 0 | 0 | **是** |
| 轴反射协变 | 精确 0 | 0 | 0 | **是** |
| 量纲一致性（`mass*length*time**-2`） | 精确指数相等 | 指数相等 | 0 | **是（精确）** |
| 代数关系 `force = mass*acceleration` | 7.0 | 7.0 | 0 | **是** |
| 插值型质量漂移（被判缺陷） | 0 | −3.67e-4 | 3.7e-4（相对积分） | 是（<3%，但被 1e-10 容差捕获） |
| 平移启发式偏差（被判缺陷） | 0.025 | 0.025562 | **2.2%** | **是（<3%）** |

**必须点明的诚实结论**：最后一行说明「<3%」这类工程容差**不足以**抓住某些不变量违规
——2.2% 的协变偏差在 3% 工程容差下会漏过。不变量审计的价值恰在于此：对**精确律**使用
**机器精度容差**（1e-9~1e-12），而不是复用工程容差。缺陷检出能力由容差决定，不由 3% 决定；
台账对每一对都记录 `residual`、`scale`、`tolerance`，审计者可自行复核。

**未做的检查（honest）**：未接入真实求解器仓；未做动量/能量守恒的独立合成对照（机制支持，
本轮只注入了质量与「力」量）；未做曲线坐标变换协变；未在有噪声的求解器上做误报率统计
（合成模型是精确算术，误报率为 0 属构造性结论，不外推到真实求解器）。

## 7. 复现

```bash
python3 -m tools.invariant_audit run \
  --plan config/invariant_audit/plans/synthetic_solver_matrix.json \
  --out artifacts/invariant_audit/run_matrix
python3 -m tools.invariant_audit verify --bundle artifacts/invariant_audit/run_matrix
python3 -m tools.invariant_audit.report_figures \
  --plan config/invariant_audit/plans/synthetic_solver_matrix.json \
  --out artifacts/invariant_audit/figures
python3 -m pytest tests/invariant_audit -q      # 59 passed
python3 scripts/p388w1_n02_acceptance.py        # 重算 run_meta.json 的 A1–A7
```

环境：Python 3.11，numpy 2.4.6，matplotlib 3.10.8；环境指纹与依赖版本随证据包入库。