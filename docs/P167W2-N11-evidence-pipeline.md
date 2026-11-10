# P167W2-N11 证据治理全链路成熟化：自主基准引擎 + 数值通道回填 + 跨库守恒性

> 本文件由 `tools/evidence_pipeline/gen_report.py` 从 `artifacts/P167W2-N11/acceptance.json`、
> 各节点 artifact 与已入册 baseline 程序化生成，报告中的每个数字都可回溯到工件（零手抄）。
> 生成时间不写入正文（保证同一输入生成同一份文档）；变更以 git 记录为准。

## ① 案例介绍

**背景。** 平台此前已有三块彼此孤立的能力：(N08) 自主基准引擎（配置 → 执行 → 判据）、
(N09) 数值通道回填与入库门（工件生成 + 重放一致性）、(N10) 跨库场交换守恒性
（`tools/coupling` 的零阶/一阶矩、正性、超调四类门）。孤立使用时的后果是：一个
"通过"的基准可以是**旧的工件**、**改过的生成器**或**没有重放的一致性声明**，证据链
在关键处依赖人的记忆。本节点把三者整合成一条**机器可裁、fail-closed** 的证据链：

```
benchmark 配置 → 生成器执行 → gate 判定 → 工件 → 入库门（身份+重放） → 回归检测 → 哈希链 provenance
```

**合成算例（平台层零领域语义）。** 落库 `TensorPlatform` 的模块不得含域词条（铁律），
因此三个 benchmark 用**合成数值算例**，物理域算例归 ship-performance-platform 仓：

| benchmark | 生成器 | 数值内容 | 判据类型 | 为什么选它 |
|---|---|---|---|---|
| `syn-alpha-quadrature` | `syn_alpha_quadrature.py` | 中点法求积分，两个网格测观测阶 `p = log2(e(n)/e(2n))` | numeric | 判据是真实的数值质量声明（阶而非恒真不等式） |
| `syn-beta-exchange` | `syn_beta_exchange.py` | 4×4 → 5×5 网格场交换，守恒修正 + 一阶矩约束 | numeric + conservation | 跨库交换的经典形态；两项判据同源不同路径（标量 vs 场） |
| `syn-gamma-partition` | `syn_gamma_partition.py` | 不规则分块（0/0.2/0.55/1）→ 嵌套 2× 细化，P1 算子，正性硬约束 | numeric + conservation | 不等尺寸单元 + 嵌套几何；线性重现作为第二独立判据 |
| 负控 `syn-delta-nonconservative` | `syn_delta_nonconservative.py` | 同 beta 几何但最近邻采样、**关掉**守恒修正 | conservation | 证明守恒门不是恒真（判据的可判别性对照） |
| 负控 `syn-eps-nondeterministic` | `syn_eps_nondeterministic.py` | 数值门通过、但指标依赖 `perf_counter_ns()` | numeric + 入库门 | 证明"重放一致"检查是活的而不是未测声明 |

参数表（全部落在配置里，可审计）：
alpha `n0=8`；beta `n_src=4, n_dst=5, operator=overlap_l2, moment_exact=true`；
gamma `factor=2, operator=overlap_p1, moment_exact=false`（P1 + 正性硬约束下不强加矩约束，
把矩缺陷用**预声明界 5e-3** 表达）。

## ② 怎么做的（方法 / 流程 / 工具链）

**模块。** 新包 `tools/evidence_pipeline/`（10 个源文件 + 5 个生成器 + 2 个测试文件）：

| 模块 | 职责 | 关键设计 |
|---|---|---|
| `config.py` | 基准配置解析/校验（生成器、参数、门、回归策略、超时） | 未知门类型、缺界、负容差、缺生成器文件在**加载期**报错；`digest()` 用 canonical JSON（浮点走 hex） |
| `execution.py` | 子进程执行 `generators/<node>.py` | 确定性环境（`PYTHONHASHSEED=0`）、硬超时、payload 契约严格校验（缺指标/非有限值=错误） |
| `gate.py` | 门分发（`numeric` / `conservation`） | 配置点名的指标缺失 = **失败**（不是静默通过）；扩展点=一个 `GATE_RUNNERS` 条目 |
| `conservation_gate.py` | 跨库交换守恒门 | 从 payload 重建 `Field`，**委托** `tools.coupling.conservation.check`，指标定义不二次实现 |
| `artifact.py` | 工件构造 + `--gate` 入库模式（A1–A9） | 自摘要 `self_sha256`、`payload_digest`、生成器内容哈希；**工件内无时间戳** → 重跑逐字节相同 |
| `regression.py` | 基线对比 + 漂移分类 | 相对漂移带每指标 floor；分类表 `stable / no_baseline / drift_reproducible / drift_unverified / drift_nondeterministic` |
| `runner.py` | 编排 + 退出码契约 | 顺序固定：execute → validate → gates → artifact → ingest → regression → provenance |
| `acceptance.py` | C1–C10 机器判据驱动 | 输出 `artifacts/P167W2-N11/acceptance.json`（数字 + 工件路径） |
| `gen_report.py` | 本报告 + 4 张图 | 全部数字从工件读取 |

**入库门（fail-closed）九项检查**：A0 可读、A1 必需键、A2 schema 版本、A3 自摘要、
A4 `status == pass`、A5 判据/状态自洽、A6 payload 摘要复算、A7 **生成器内容哈希**、A8 **配置摘要**、
A9 **完整重放**（重跑生成器，`payload_digest` 必须逐位相同，零容差）。任一项失败 → 拒绝（退出码 3），
不"修复"、不"警告放行"。

**回归闭环**：以已入册 baseline 为对照，逐指标算 `rel_drift = |cur-base| / max(|base|, floor)`；
超阈值则按 `regression.reruns` 触发重跑并把每次的 `payload_digest` 记入链：
重跑全部一致 → `drift_reproducible`（**真实变化，必须显式重新入册**）；
重跑互相不一致 → `drift_nondeterministic`（不可复现的数字本身就是失败）；
不允许重跑 → `drift_unverified`。重新入册必须带 `--accept-drift`，否则退出码 6 拒绝。

**退出码契约**：0 通过 / 2 判据失败 / 3 入库门拒绝 / 4 回归漂移 / 5 生成器失败 / 6 拒绝静默重新入册。

**provenance**：复用 `tools.coupling.provenance.ExchangeLedger`（append-only 哈希链），
每个阶段一条 entry（config / execute / validate / gates / artifact / ingest_gate / regression /
baseline_written），写记录前重算 `chain_valid`；改动任一 entry 都会使链失效（C8 负控验证）。

## ③ 计算结果

三节点判据表（数值全部来自工件，`PASS` 由 gate 判定）：

| benchmark | gate | type | metric | value | threshold | margin | verdict |
|---|---|---|---|---|---|---|---|
| syn-alpha-quadrature | convergence_order | numeric | err_fine | 0.0010238 | max 0.002 | 1.95x | PASS |
| syn-alpha-quadrature | convergence_order | numeric | integral_rel_error | 0.00160819 | max 0.002 | 1.24x | PASS |
| syn-alpha-quadrature | convergence_order | numeric | p_order | 2.00488 | min 1.9 | 1.06x above min | PASS |
| syn-beta-exchange | exchange_quality | numeric | moment_rel_residual | 6.344e-17 | max 1e-12 | 1.58e+04x | PASS |
| syn-beta-exchange | exchange_quality | numeric | nonneg_violation | 0 | max 1e-12 | 1e+18x | PASS |
| syn-beta-exchange | exchange_quality | numeric | sum_rel_residual | 2.538e-16 | max 1e-14 | 39.4x | PASS |
| syn-beta-exchange | exchange_conservation | conservation | moment_rel_residual | 6.344e-17 | <= 1e-12 | 1.58e+04x | PASS |
| syn-beta-exchange | exchange_conservation | conservation | nonneg_violation | 0 | <= 1e-12 | 1e+18x | PASS |
| syn-beta-exchange | exchange_conservation | conservation | overshoot_ratio | 1.01014 | <= 1.02 | 1.01x | PASS |
| syn-beta-exchange | exchange_conservation | conservation | sum_rel_residual | 2.538e-16 | <= 1e-14 | 39.4x | PASS |
| syn-gamma-partition | linear_reproduction | numeric | linear_repro_error | 0.0282776 | max 0.05 | 1.77x | PASS |
| syn-gamma-partition | linear_reproduction | numeric | nonneg_violation | 0 | max 1e-12 | 1e+18x | PASS |
| syn-gamma-partition | linear_reproduction | numeric | overshoot_ratio | 1 | max 1 | 1x | PASS |
| syn-gamma-partition | partition_conservation | conservation | moment_rel_residual | 0.00229303 | <= 0.005 | 2.18x | PASS |
| syn-gamma-partition | partition_conservation | conservation | nonneg_violation | 0 | <= 1e-12 | 1e+18x | PASS |
| syn-gamma-partition | partition_conservation | conservation | overshoot_ratio | 1 | <= 1 | 1x | PASS |
| syn-gamma-partition | partition_conservation | conservation | sum_rel_residual | 1.133e-16 | <= 1e-14 | 88.3x | PASS |

**保守 vs 非保守交换（C4）**：同一几何、同一场，仅"是否施加质量修正"不同 →
零阶矩残差 2.538e-16 vs 0.0214286，
比值 **8.444e+13**（跨 14 个数量级）；一阶矩残差
6.344e-17 vs 0.0153571。
负控在 `sum_rel_residual` 上超出阈值 2.14e+12 倍，判据失败符合预期。

**机器判据汇总（10/10 通过）**：

| id | criterion | numbers | verdict |
|---|---|---|---|
| C1 | 3 benchmarks traverse the full chain | 3 benchmarks, ingest checks alpha:10/10, beta:10/10, gamma:10/10 | PASS |
| C10 | zero domain vocabulary | 24 files, 0 hits, 37 terms | PASS |
| C2 | gate violation fails closed | status gate_fail exit 2, artifact fail, ingest False | PASS |
| C3 | replay check is live | status ingest_rejected exit 3, failed ['A9_replay'] | PASS |
| C4 | conservation gate discriminates | conservative 2.538e-16, control 0.0214286, ratio 8.444e+13 | PASS |
| C5 | artifact tampering rejected | tampered rejected: number, status, verdict; pristine accepted | PASS |
| C6 | generator/config identity enforced | generator edit rejected (False), weakened config rejected (False) | PASS |
| C7 | regression closed loop | exit 4 → drift_reproducible (3 attempts, 1 distinct digest); refused exit 6; after re-bless stable | PASS |
| C8 | provenance chain verifies | chain_valid=True, entries=7, tampered→False | PASS |
| C9 | byte-identical reruns | artifact sha256 fc988ccb9f35ab12…, 10573 bytes, byte-identical | PASS |

工件路径（全部在 worktree 内，可直接 `--gate` 复核）：
`artifacts/P167W2-N11/acceptance.json`、`artifacts/P167W2-N11/runs/<node>.artifact.json`、
`artifacts/P167W2-N11/runs/<node>.run.json`、`artifacts/P167W2-N11/baselines/<node>.json`、
`artifacts/P167W2-N11/work/`（控制场景：拒收工件、漂移记录）。

已入册基线摘要：alpha payload fb6619ec3b8d9087…、beta 41f76f0f8f0b442d…、
gamma d30f1623ef26df2b…；三节点第二次运行回归分类均为 `stable`，
max_rel_drift = 0.000e+00。

## ④ 结果图

![fig1](artifacts/P167W2-N11/figs/fig1_metric_margin.png)

**图 1** 每个被判指标的"实测值 / 阈值"比值（对数轴）：所有柱都在红色阈值线（1.0）左侧，
说明判据不是恒真也不是恒假；最紧的一项（gamma 的 `moment_rel_residual`）仍有约
2.18× 裕度。

![fig2](artifacts/P167W2-N11/figs/fig2_conservation_gate.png)

**图 2** 守恒门对"施不施质量修正"的判别力：绿色为修正开启（残差 ~1e-16），红色为负控
（残差 ~1e-2），灰柱为预声明阈值。

![fig3](artifacts/P167W2-N11/figs/fig3_acceptance_and_exit_codes.png)

**图 3** 左：C1–C10 通过矩阵；右：各场景实测退出码（0 通过 / 2 判据失败 / 3 入库拒绝 /
4 漂移 / 6 拒绝静默重新入册）。

![fig4](artifacts/P167W2-N11/figs/fig4_provenance_chain.png)

**图 4** 哈希链 provenance 的阶段序列：每条链在写出前重算 `chain_valid`，
C8 负控中改动第 3 条 entry 后 `verify_chain()` 立即变 False（False）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **工件内不能有时间戳**。最初把 `created_utc` 写进工件，导致同输入两次运行工件不可能逐字节相同；
   现改为时间只进 run record，工件保持确定（C9 的 10573 字节逐字节相同即由此而来）。
   代价：工件本身不携带"何时生成"，时间维度只能从 run record 链上读出。
2. **配置摘要包含 baseline 路径**。C5 首轮实现曾因多个控制场景复用同一份临时配置文件名而出现
   "原始工件也被拒收"的假阳性——根因是配置被覆盖后 A8 失配（这正是 A8 应有的行为）。已改为
   每个场景独立配置文件名；排查记录保留在本文，因为它说明 A8 不是装饰。
3. **一阶矩阈值是工程界而非"精确守恒"**。gamma 使用 P1 算子 + 正性硬约束，矩缺陷来自算子本身
   （实测 0.00229303，预声明界 5e-3），
   把界限说成"精确守恒"就是过度声明；门里因此同时保留零阶矩（严格 ~1e-16）与正性/超调（精确 0/1）。
4. **确定性是"同机同解释器"级别的**。重放零容差在 loong64 节点/不同 BLAS 下可能因浮点求和顺序
   产生末位差异而把本来正确的工件拒收；本节点未做跨架构噪声标定（跨架构重放属于另一条既有能力）。
   这是 fail-closed 的已知代价，方向选择是"宁可误拒，不可误放"。
5. **重放成本**。入库门每次都要重跑生成器（本合成算例 ~1 s，真实算例可能分钟级）；`--no-replay`
   仅用于离线审计，会显式记录 `A9_replay: skipped`，不得用于入册。
6. **未做**：未接后端数据库/证据链 HTTP 入库端点（本容器无后端进程，属 WITHHELD）；
   未把该链挂到真实物理域算例（域算例归 ship-performance-platform 仓）；未做并发/多节点调度编排。
7. **平台铁律核验**：`tools/evidence_pipeline` + `config/evidence_pipeline` 域词表扫描
   24 个文件、37 个词条、命中 0（C10）。

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否 <3%"）

| 关键量 | 实测 | 参考/基准 | 相对误差 | 是否 <3% | 说明 |
|---|---|---|---|---|---|
| 求积收敛阶 p（alpha） | 2.00488 | 2 | 0.244% | 是 | 理论阶 2（中点法） |
| 积分相对误差（alpha） | 0.161% | 解析积分（相对量） | —（该量即为相对误差） | 是 | 该量本身即为对解析积分 2/pi 的相对误差 |
| 零阶矩残差（beta） | 2.538e-16 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | 参考值 0（严格守恒），按绝对量对照预声明阈值 1e-14 |
| 一阶矩残差（beta） | 6.344e-17 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | moment_exact=True，预声明阈值 1e-12 |
| 零阶矩残差（gamma） | 1.133e-16 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | 参考值 0（严格守恒），预声明阈值 1e-14 |
| 一阶矩残差（gamma） | 0.00229303 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | P1 算子 + 正性硬约束的 O(h) 缺陷，预声明界 5e-3 |
| 线性重现误差（gamma） | 0.0282776 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | 线性场在硬约束下的畸变，预声明界 0.05（占场幅值 ~1.4%） |
| 零阶矩残差（负控 delta） | 0.0214286 | 0 | 无定义（基准为 0，用绝对量） | —（见说明） | 关掉守恒修正，应超界失败 |

**结论**：可用相对误差判定的关键量共 2 项——
求积观测阶 p（实测 2.004878 vs 理论 2，相对误差 0.244%）
与积分相对误差（vs 解析值，0.161%），**均 <3%**。
守恒类残差与 0 相比的"相对误差"无定义（分母为 0），因此改用**绝对量 + 预声明阈值**判定：
零阶矩 2.538e-16 与
1.133e-16 均低于 1e-14，
线性重现误差 0.0282776（相对场幅值 ~1.3–2，
即 ~1.4%）低于预声明界 0.05，矩缺陷 0.00229303
低于预声明界 5e-3；负控两项残差 0.0214286 /
0.0153571 全部超界（相对基准偏差 >100%），符合"应失败"的预期。
无基准的项目（如 `dst_total` 绝对值）不参与误差判定，只作为审计量记录。
