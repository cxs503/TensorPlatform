# IN59-A2 · 预注册假设台账 hypothesis_registry（落库 TensorPlatform）+ Wigley 兴波假设全程示范

**仓库** TensorPlatform · **分支** `agent/m6329` · **任务** 战役 DAG 节点 IN59-A2（物理域）
**台账** `artifacts/hypothesis_registry.db` · **示范实验** Wigley 抛物线型船体 Cw(Fn) 干涉结构
**验收数字** `artifacts/m6329/in59a2_acceptance.json` · **明细** `artifacts/m6329/hypothesis_registry_summary.{json,md}`

## 1. 案例介绍

### 1.1 问题背景（为什么要一张表）

科研循环里最容易被污染的环节不是计算，而是**判定**。同一条实验结果，「我先猜 A、结果不是 A」
与「我猜的就是 B」在没有登记时间戳与预测承诺时，文本上无法区分；后果是 falsified 被系统性
改写成 confirmed，失败知识库（A4）永远收不到料，S-N 轨的权4 激励被架空。
IN59-A2 要求把「登记 / 不可改 / 开盲流」做成机器门，而不是流程约定。

### 1.2 物理示范案例（Wigley 兴波干涉结构）

选它做示范，因为它的**理论预测是纯推导来的**（不依赖任何实验数值），因而预测可以被诚实地
「盲登」；同时它有独立实现可交叉验证（薄船理论两条数值路径）。

半宽 $y(x,z)=\frac{B}{2}\left(1-(2x/L)^2\right)\left(1-(z/T)^2\right)$，$x\in[-L/2,L/2]$，$z\in[0,T]$：

| 量 | 值 | 来源 |
|---|---|---|
| L / B / T | 100.0 / 10.0 / 6.25 m | 标准 Wigley（L/B=10, B/T=1.6） |
| Cb | 0.444444 = 4/9 | 闭式 |
| ∇ | 2777.7778 m³ = 4LBT/9 | 闭式 |
| S（湿表面积） | 1487.90631050 m² | Gauss-Legendre 谱积分（一次算定） |
| ρ / g | 1025.0 kg/m³ / 9.81 m/s² | 平台默认海水 |
| Fn 扫描 | 0.18 … 0.42，步长 0.004（61 点） | 覆盖 m=3,2,1 三个干涉相位 |

理论与工具链：深水色散 $\lambda=2\pi U^2/g=2\pi L\,Fn^2$（Newman 1977 §6.1）；首尾波系同相
$\lambda/L=1/m$ 给 hump 的 Froude 数 $Fn=1/\sqrt{2\pi m}$ = 0.2303 / 0.2821 / 0.3989（m=3/2/1）；
兴波用 Michell (1898, Phil. Mag. 45:106-123) 薄船积分，Wehausen & Laitone (1960, Handbuch der
Physik 9) 给同一相位判据。数值实现复用
`docs/verification_debt/wigley_michell/michell_wigley.py`（route A：内层解析积分 + θ 方向
Gauss-Legendre，$n_\theta=400$；引擎 sha256 `fde3cbc07b49d575…`）。

## 2. 怎么做的

### 2.1 台账层（本任务主交付物）

`tools/hypothesis_registry.py` —— 三张表 + 13 个触发器 + 一个冻结规则执行器：

1. **登记门**：四段叙述非空；`prediction_before` 非空；若预测带 `rule`，`validate_rule`
   当场校验其可执行性（**冻结不了的东西不许登记**）；`evidence_at_registration` 必须为空；
   声明的 `result_paths` 必须尚不存在（机器事实，不是自述）。
2. **不可改门**：`prediction_commitment = sha256(canonical_json(prediction_before))`；9 个核心字段
   由 `trg_hr_immutable_core` 硬拒 UPDATE；即便有人 `DROP TRIGGER` 再改，`verify_integrity`
   复算承诺即报 `commitment_mismatch`。
3. **开盲流门**：`experiment_runs` 插入必须 (a) 假设已登记、(b) 仍 pending、(c)
   `run_utc >= registered_utc` —— 不登记先跑、倒填时间戳都是 `ABORT`；`close` 前必须有 run；
   `outcome` 终态单向；`verdict_basis` ≥20 字；`falsified` 另需 ≥40 字复盘。
4. **历史门**：`experiment_runs` / `event_log` 禁 UPDATE/DELETE；`event_log` 逐行哈希链。

    红队负例 **15 条**（`tests/test_hypothesis_registry.py::test_n1_*…test_n6_*`），
    每条都既走 API 又走裸 `sqlite3` 语句，证明防线在数据库而不是 Python 自觉：
    事后补登（自述清单 / 结果文件已存在）、改预测（触发器拒 + 承诺复算抓 `DROP TRIGGER`）、
    不登记先跑/先审、倒填时间戳、结题翻案、结题后再跑、无实验裁决、空裁决依据、
    falsified 无复盘、哈希链篡改。

### 2.2 物理实验流程（两进程，构成「开盲流」）

| 步骤 | 命令 | 说明 |
|---|---|---|
| ① 盲登记 | `python3 runs/m6329_hypothesis_registry/preregister.py` | 只写假设+推导+判据+预测，**不算任何物理量**；声明结果文件 `artifacts/m6329/experiment_results.json` 尚不存在 |
| ② 执行+结题 | `python3 runs/m6329_hypothesis_registry/execute.py` | 先自检承诺一致 → 计算 → `log_run`（外部时间戳）→ 从 **DB 里**读冻结规则 `evaluate_rule` → `close` |

关键设置：$C_w=R_w/(0.5\rho S_{ref}U^2)$，$U=Fn\sqrt{gL}$，$n_\theta=400$；局部极大 = 内部点严格大于左右邻点（端点不计）。
`execute.py` 不读本地判据常量，只读台账里那条在实验前写下的规则，因此「改判据」在结构上不可能。

## 3. 计算结果

### 3.1 三条假设的判定（冻结判据 vs 实测）

| hypothesis_id | 冻结判据 | 实测 | outcome | S-N |
|---|---|---|---|---|
| `m6329-IN59-A2-H1-hump-structure` | n_humps 等 2 项（见 JSON） | n_humps=3 >= 3 → pass; first_hump_Fn=0.204 in_range [0.21, 0.245] → fail | **falsified** | S-N w=4 |
| `m6329-IN59-A2-H2-main-hump-dominance` | global_max_Fn 等 2 项（见 JSON） | global_max_Fn=0.42 in_range [0.385, 0.415] → fail; ratio_global_over_first_hump=3.47091 >= 1.3 → pass | **falsified** | S-N w=4 |
| `m6329-IN59-A2-H3-froude-invariance` | rel_diff_scale 等 1 项（见 JSON） | rel_diff_scale=2.02644e-16 < 1e-09 → pass | **confirmed** | S-C w=1 |

台账合计：假设 3 条 / 已结题 3 / pending 0；
outcome {'confirmed': 1, 'falsified': 2, 'inconclusive': 0, 'pending': 0}；S-N 记账 {'S-C': {'count': 1, 'weight': 1.0}, 'S-N': {'count': 2, 'weight': 8.0}}；失败知识库条目 2；
完整性审计 **PASS**。

### 3.2 Cw(Fn) 结构与 hump 位置

| # | 局部极大 Fn | Cw | 最近的干涉相位 Fn（λ/L=1/m） | 位置相对差 |
|---|---|---|---|---|
| 1 | 0.204 | 9.888864e-04 | 0.2303 (m3) | 11.4% |
| 2 | 0.240 | 1.383620e-03 | 0.2303 (m3) | 4.2% |
| 3 | 0.300 | 2.140116e-03 | 0.2821 (m2) | 6.3% |

区间端点：Cw(0.20)=8.860759e-04, Cw(0.24)=1.383620e-03, Cw(0.30)=2.140116e-03, Cw(0.42)=3.432335e-03；全区间最大值 $C_w$ = 3.432335e-03 @ Fn=0.420
（在扫描右端点，说明 m=1 主 hump 在本区间内**没有**形成极大，Cw 单调上升）；
全区间最大 / 最低 Fn hump 峰值比 = 3.4709。

### 3.3 求积收敛与实现互核

| Fn | n_theta | Cw | 相对 n_theta=1600 的差 |
|---|---|---|---|
| 0.30 | 100 | 2.140115830e-03 | 3.67e-14 |
| 0.30 | 200 | 2.140115830e-03 | 3.28e-14 |
| 0.30 | 400 | 2.140115830e-03 | 2.80e-14 |
| 0.30 | 800 | 2.140115830e-03 | 3.38e-14 |
| 0.30 | 1600 (ref) | 2.140115830e-03 | — |
| 0.40 | 100 | 2.732332183e-03 | 6.19e-15 |
| 0.40 | 200 | 2.732332183e-03 | 6.35e-15 |
| 0.40 | 400 | 2.732332183e-03 | 6.83e-15 |
| 0.40 | 800 | 2.732332183e-03 | 7.30e-15 |
| 0.40 | 1600 (ref) | 2.732332183e-03 | — |

| 检查项 | 量 | 独立值 | 本任务值 | 相对差 |
|---|---|---|---|---|
| 实现路径互核（route B 二维面元） | Cw(Fn=0.24) | 1.383619953e-03 | 1.383619953e-03 | 9.31e-14 |
| 实现路径互核（route B 二维面元） | Cw(Fn=0.30) | 2.140115830e-03 | 2.140115830e-03 | 4.56e-14 |
| 陈旧工件 `reaudit_2026-11-02.json`（m1817 修复前） | Cw(Fn=0.22) | 1.304054677e-03 | 6.520273383e-04 | 2.000000× |
| 陈旧工件 `reaudit_2026-11-02.json`（m1817 修复前） | Cw(Fn=0.28) | 3.201815767e-03 | 1.600907884e-03 | 2.000000× |
| 陈旧工件 `reaudit_2026-11-02.json`（m1817 修复前） | Cw(Fn=0.32) | 3.419018389e-03 | 1.709509194e-03 | 2.000000× |

## 4. 结果图

![Cw(Fn) 与 hump 结构](../artifacts/m6329/fig1_cw_vs_fn.png)

**图 1** 细长体薄船理论 Cw(Fn) 扫描（61 点）+ 检出的局部极大（红点）+ 干涉相位
λ/L=1/m 的理论位置（灰虚线，m=3/2/1 = 0.2303/0.2821/0.3989）。
可见实测 hump 相对相位预测有系统性偏移，且 m=3 之前还有一支更低的极大（Fn≈0.204）。

![求积收敛与台账 S-N 记账](../artifacts/m6329/fig2_convergence_and_ledger.png)

**图 2** 左：θ 求积阶 $n_\theta$ 从 100 增到 800 相对 $n_\theta$=1600 的相对差（两 Fn 均在 $10^{-14}$ 量级即机器精度，
说明谱精度求积已收敛，测得的 hump 结构不是离散化噪声）；右：台账三条假设的 outcome 与 S-N 记账
（falsified → S-N 权4，不计失败率、进失败知识库）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **陈旧工件会立刻骗过你**：`docs/verification_debt/wigley_michell/artifacts/reaudit_2026-11-02.json`
   里的 `michell_curve` 与本次计算**恰好差 2×**。原因是该工件生成于 m1817 之前，用的是被
   m1817 判定为「双倍前置因子」的旧版（$8\rho g^2/\pi U^2$）；模块 docstring 明确写着
   正解应为 $4\rho g^2/\pi U^2$，且本次 route A 与 route B（二维面元、完全独立实现）互核到
   4.6e-14，可判定**本次数值正确、旧工件未随修复重生成**。教训：工件的口径（工件与代码谁更新）
   必须写进工件本身，否则下游会把正确结果当成错误。
2. **「相位判据」不是「峰值判据」**：λ/L=1/m 只约束波系相位；hump 是否可见还取决于振幅项
   $|I(\theta)|^2$ 随 Fn 的变化，细长体理论的散波/高阶横波分量会移动甚至抹平峰。这是 H1/H2
   被证伪的物理原因（见 6.2），也是「理论推导 → 数值预测」必须留证伪空间的理由。
3. **本地 SQLite 时钟不可信**：台账只能用「库内顺序 + 承诺哈希 + 外部时间戳 ≥ 登记时刻」逼近
   盲性；库外先跑后登无法被证明。此残余风险写在 `limitations()` 并由 `verify` 输出，不粉饰。
4. **规则必须可执行才允许登记**：早期版本的 `verdict_basis` 允许自由文本，等价于结题时重新
   发明判据；现改为「登记时校验 `rule` 结构 + 结题时 `evaluate_rule` 执行」。
5. **适用范围**：UQ/力学的假设登记流程可直接复用（`domain` 字段区分阻力/耐波/操纵等）；
   多机构博弈场景需外部时间戳/公证服务，超出本模块能力。
6. **平台冻结纪律**：TensorPlatform 自 2026-11-02 冻结，本任务按任务书要求只新增独立领域模块
   （`tools/`、`docs/science/`、`runs/`、`artifacts/`），未改动平台核心（无 `main.py`/路由/迁移改动）。

## 6. 误差判定（关键量 vs 基准，逐项回答「是否<3%」）

### 6.1 数值可信度（引擎侧）

| 关键量 | 基准 | 本任务值 | 相对差 | 是否 <3% |
|---|---|---|---|---|
| Cw(Fn=0.24) | route B 独立实现（二维面元）1.383619953e-03 | 1.383619953e-03 | 9.31e-14 | **是** |
| Cw(Fn=0.30) | route B 独立实现 2.140115830e-03 | 2.140115830e-03 | 4.56e-14 | **是** |
| Cw(Fn=0.30) | θ 求积 $n_\theta$=100 → 1600 收敛 | — | 3.67e-14 | **是** |
| Cb / ∇ / S | 闭式 4/9、4LBT/9、谱积分 | 0.444444444 / 2777.7778 / 1487.906310 | 几何门先过（Cb/L/B 自洽） | **是** |

### 6.2 预测 vs 实测（盲预测侧，这才是本任务的科学内容）

| 假设 | 预测 | 实测 | 相对差 | 是否 <3% | 判定 |
|---|---|---|---|---|---|
| H1 首个 hump Fn（m=3 相位） | 0.2303 | 0.204 | 11.4% | **否**（11.4%>3%） | falsified |
| H1 hump 个数 | ≥3 | 3 | — | 是（计数相符） | 但位置项失败 → 整体 falsified |
| H2 全区间最大位置（m=1） | 0.3989 | 0.420 | 5.3% | **否**（5.3%>3%，且落在网格端点） | falsified |
| H2 全区间最大 / 最低 Fn hump 峰值比 | ≥1.3 | 3.471 | +167% | 是 | 该项通过 |
| H3 Froude 不变性 | <1e-9 | 2.03e-16 | 机器精度 | 是 | confirmed |

**结论（不粉饰）**：H1/H2 被证伪——「λ/L=1/m 相位判据直接给出可见 hump 位置」这一推论不成立
（实测首个极大在 Fn=0.204，比 m=3 相位预测左移 11.4%；m=1 主 hump 在本区间未形成极大，
Cw 到 Fn=0.42 仍单调上升）。这正是设计上要的产出：falsified 按 bootstrap 权4 进 S-N 轨
（本次 S-N 累计权 8），不计失败率，自动进失败知识库
（2 条），且复盘写明物理解释（相位 vs 振幅）。引擎侧数值可信度与 H3 的
Froude 不变性均在机器精度内通过，说明**证伪针对的是理论推论，不是代码**。

## 7. 产物清单

| 路径 | 内容 |
|---|---|
| `tools/hypothesis_registry.py` | 台账模块（DDL/触发器/登记/run/结题/审计/冻结规则执行器/PG DDL/CLI） |
| `tests/test_hypothesis_registry.py` | 28 个用例（含 15 条红队负例） |
| `runs/m6329_hypothesis_registry/preregister.py` | 第 1 步：盲登记（预测冻结） |
| `runs/m6329_hypothesis_registry/execute.py` | 第 2 步：执行 → run → 按冻结规则结题 → 报告/图/验收 |
| `runs/m6329_hypothesis_registry/render_report.py` | 本报告的渲染器（数字全部程序化注入） |
| `artifacts/hypothesis_registry.db` | 台账实例（假设 / run / 事件链，明细见 summary.json） |
| `artifacts/m6329/experiment_results.json` | 全部原始数字（61 点扫描 + 收敛 + 引擎 sha256） |
| `artifacts/m6329/hypothesis_registry_summary.{json,md}` | 台账摘要（含不可改预测快照） |
| `artifacts/m6329/in59a2_acceptance.json` | 机器验收判据数字与产物路径 |
| `artifacts/m6329/crosscheck.json` | 实现互核 + 陈旧工件 2× 差异取证 |
| `docs/science/hypothesis_registry.md` | 领域文档（表结构 / 机器门 / 用法 / 残余风险） |
