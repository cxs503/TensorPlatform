# P362-W2-N01 能力台账自动升级机制（证据链驱动的 REAL/MOCK/STUB 自动分级）

**仓库** TensorPlatform · **节点** P362-W2-N01 · **mission** 13850
**生成时间** 2026-11-11T05:26:27.584420+00:00 · **验收总判** all_passed = **True**
判据工件 `runs/p362w2_n01_capability_auto_upgrader/acceptance.json` · 机制代码 `backend/services/capability_auto_upgrader/`

## ① 案例介绍

平台的能力证据等级（`REAL`/`MOCK`/`STUB`/`WITHHELD`/`NOT_SUPPORTED`）此前由**人工审计**或
判据引擎（`tools/capability_upgrade`，P348-W3-N01）给出。那些判据是**证据集合的静态完整性**
（锚点数、最大误差、独立复核），没有**时间持续性**要求：同一份报告被重放三次，机制无法区分
"跑了三次"与"跑了一次、抄了三遍"；也没有"回归失败自动回退"的路径——证据退化只能等下一次
全量审计。（`tools/capability_upgrade` 源码亦明示其降级只由缺陷注入触发，不由回归失败触发。）

本节点把"升级"做成**证据链上的时间断言**：一个能力位只有在其最近
`promotion_window = 3` 次**互不相同**的运行中，每次都同时满足
`pytest 通过率 ≥ 90%` 且 `对标误差 < 15%`，
才允许 `MOCK`/`STUB`/`WITHHELD → REAL`；一次**结论性**回归失败（红测试或误差越界）就把该等级
按台阶下退一级（`REAL → WITHHELD → MOCK`，下限 `MOCK`，绝不自动判成空壳）。

几何/工况：本节点是**平台机制节点**，不含几何体，故无 Cb/L/B 几何校验项；全部算例为合成能力位
（`synth.compute_alpha` / `synth.compute_beta` / `synth.guard_delta`）与合成数值证据，域词条经配置化 deny-list 扫描 0 命中。

升级/降级规则（全部以数据形式存放，代码零常量；策略 digest `13a2c8d89f9d62c15f7e2f19e48970b3dbc5d2db7901a0a3597a68a974d904a8`）：

| 方向 | 触发条件（默认阈值） | 目标等级 |
|---|---|---|
| 升级 | 连续 3 次不同运行：通过率 ≥ 90% ∧ 误差 < 15% ∧ 样本数 ≥ 1 | `REAL` |
| 降级 | 连续 1 次结论性失败（红测试或误差越界） | 下退 1 级，下限 `MOCK` |
| 保持 | 证据不足（无执行测试/样本不足） | 不动等级（缺证 ≠ 反证） |
| 记录 | 外部 `audit_capabilities` 与推导等级不一致 | 记 `mismatch`，**不覆盖**等级 |

## ② 怎么做的

1. `model.py` — 内容寻址证据模型：`TestEvidence`（junit 计数 + 报告 sha256）、`BenchmarkEvidence`
   （`error_pct` 由 measured/reference **现场推导**，不接受自报值）、`EvidenceRun`（digest 覆盖
   证据内容、**不含**墙钟时间，因此"重放同一份报告"可被识别）。
2. `policy.py` — 阈值数据类 + 策略 digest；非法配置（窗口 0、通过率>1、下限=REAL）构造即拒。
3. `streak.py` — 单次判定 `judge_run`（红测试 > 误差越界 > 证据不足 的优先级）、尾部连续计数
   `analyse`、决策 `decide`（纯函数，可离线复算）。
4. `ledger.py` — `feature_ledger` 追加写：JSONL 事件 + 平台同构 SQLite 表（`ledger_id/tenant/
   item_type/payload/created_at`），每条事件含 `prev_sha256`/`sha256` 哈希链；等级由**重放**事件推导；
   `verify()` 同时校验 JSONL 哈希链与 SQLite 镜像。
5. `watcher.py` — 监听 `audit_capabilities` 负载（`capabilities`/`items`/`data`/裸列表四种形态），
   做快照 diff（added/level_changed/removed），快照本身带 digest；监听器**从不写等级**。
6. `upgrader.py` — 编排：入账运行 → 连续计数 → 决策 → 追加事件（含证据快照与窗口 digest）；
   重复证据返回 `duplicate_run`（不延长 streak）；seq 必须严格递增。
7. `scanner.py` + `config/capability_auto_upgrader/deny_list.json` — 零领域语义自检（词表在配置里）。
8. `acceptance.py` / `figures.py` / `cli.py` — 本报告与 4 张图、`acceptance.json` 一键重放。

工具链：CPython 3.11 · pytest 9.2（junit-xml 摘要入证）· matplotlib 3.10 (Agg) · sqlite3 (stdlib)。

## ③ 计算结果

### (a) 自动升级：MOCK → REAL（合成能力 `synth.compute_alpha`）

| seq | 通过率 % | 误差 % | 决策 | 等级 | 原因码 |
|---|---|---|---|---|---|
| 1 | 100.0 | 2.00 | hold | MOCK → MOCK | pass_streak=1<3 |
| 2 | 100.0 | 2.00 | hold | MOCK → MOCK | pass_streak=2<3 |
| 3 | 100.0 | 2.00 | promote | MOCK → REAL | pass_streak=3>=3, policy_satisfied |
| 4 | 75.0 | 40.00 | demote | REAL → WITHHELD | fail_streak=1>=1, test_pass_rate_below_floor, accuracy_breach |
| 5 | 70.0 | 60.00 | demote | WITHHELD → MOCK | fail_streak=2>=1, test_pass_rate_below_floor, accuracy_breach |

- 等级轨迹（含回归段）：`MOCK → MOCK → REAL → WITHHELD → MOCK`
- `STUB` 路径（`synth.compute_beta`）：`STUB → STUB → REAL`
- 升级事件：seq=3，`MOCK → REAL`，
  sha256 `9005590c58f90cde406e2dca6d9c34fa803794240b9df0888c07464de8bf912c`，policy digest `13a2c8d89f9d62c15f7e2f19e48970b3dbc5d2db7901a0a3597a68a974d904a8`，
  窗口 digest 数 = 3
- 事件行字段完整性（REQUIRED_KEYS 全在）= **True**

### (b) 自动降级：回归失败 → 回退（无静默覆盖）

| seq | 等级变化 | 事件 sha256 | 原因码 |
|---|---|---|---|
| 4 | REAL → WITHHELD | `87680de4abf45cc4…` | fail_streak=1>=1, test_pass_rate_below_floor, accuracy_breach |
| 5 | WITHHELD → MOCK | `9ad0fd525e17f1f3…` | fail_streak=2>=1, test_pass_rate_below_floor, accuracy_breach |

- 追加写历史未被改写（升级事件仍在且仍为 `REAL`）= **True**
- 重放 `feature_ledger.jsonl` 得到的等级 = `{'synth.compute_alpha': 'MOCK', 'synth.compute_beta': 'REAL'}`，与运行期观测一致
- 防抖动对照（`scenario_guards`）：重放同一报告 4 次 → 等级 `MOCK`
  （streak=1，升级事件 0）；
  好坏交替 6 次 → 等级 `MOCK`（升级事件 0）；
  证据不足 → 决策 `hold`、等级仍 `REAL`；
  外部审计报 `MOCK` 而台账为 `REAL` →
  记 mismatch 1 条、等级仍 `REAL`。
- 全部防作弊对照成立 = **True**

### (c) 溯源：sha256 哈希链 + 篡改检出

| 项 | 值 |
|---|---|
| 事件总数 / 链头 sha256 | 6 / `c1a3b4830a5dfa127f27d307…` |
| 哈希链自校验 | **True** |
| 篡改 JSONL 一行 → 检出 | **True**（问题 2 条） |
| 篡改 SQLite payload → 检出 | **True**（问题 1 条） |
| 评估日志自校验（8 条） | **True** |

### (d) pytest 覆盖

命令：`/usr/bin/python3 -m pytest /root/tp-agent/work/m13850/backend/tests/test_capability_auto_upgrader.py -q --junit-xml=/root/tp-agent/work/m13850/runs/p362w2_n01_capability_auto_upgrader/junit_module.xml` → 退出码 0，
用例 39 条（通过 39，失败 0，
跳过 0），通过率 100.0%，
junit sha256 `277c513e8fe41d5af1ac6dba4c5f148a2420d356721303be84260e40ccffc4a9`。覆盖：窗口边界（2 vs 3 次）、通过率 90%/89% 边界、
误差 15.0%/15.01% 边界、抖动、重放、证据不足、降级窗口可配、下限、审计监听与 mismatch、
哈希链与两处篡改、策略校验、junit 解析、证据模型校验。

### (e) 零领域语义自检

deny-list（52 词条，配置 `/root/tp-agent/work/m13850/config/capability_auto_upgrader/deny_list.json`）扫描
14 个文件（模块包 + 本节点测试）→ 命中 **0**。

### 决策区域图（阈值灵敏度）

pass-rate × error 网格 546 格中，可升级格 78
（14.3%）；边界严格取 `通过率 ≥ 90%`
与 `误差 < 15%`（图为 fig3）。

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| fig1 升级闸门时间线 | `runs/p362w2_n01_capability_auto_upgrader/figures/fig1_upgrade_gate_timeline.png` | 每次运行的通过率与对标误差 + 两条阈值线 + 升级/降级标注 |
| fig2 等级轨迹 | `runs/p362w2_n01_capability_auto_upgrader/figures/fig2_level_trajectory.png` | 由台账事件重放得到的等级阶梯（含回归段） |
| fig3 决策区域 | `runs/p362w2_n01_capability_auto_upgrader/figures/fig3_decision_region.png` | 通过率 × 误差 → hold / promote / demote 的区域图 |
| fig4 溯源审计 | `runs/p362w2_n01_capability_auto_upgrader/figures/fig4_provenance_audit.png` | 链完整性 + 两处篡改检出结果 |

（本节点为平台机制，无场量云图：输入是 junit 摘要与标量对标误差，不存在空间场。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **digest 不能含墙钟时间**（本节点实际踩到）：第一版把 `recorded_at` 计入 run digest，自测里
   "重放同一份报告"被判为 `seq must be N` 的新证据路径，即"重放即可延长 streak"的作弊口子被
   打开。现 digest 只覆盖证据内容（capability/seq/run_id/junit 计数/对标数值），重放返回
   `duplicate_run`，streak 不动。
2. **缺证不等于反证**（设计约束，非事后补救）：junit 全 skip/空报告、样本数不足，单列
   `insufficient` 类别——断 streak 但**不降级**。若把缺证当反证，一条没跑起来的回归就能把 `REAL`
   打下去。约束由 `test_insufficient_evidence_breaks_the_streak_but_never_demotes` 钉死。
3. **降级下限**：自动化只降到 `MOCK`。`STUB`/`NOT_SUPPORTED` 是关于"实现是否
   存在"的声明，红测试无法证明实现不存在，因此不交给自动规则。
4. **适用边界**：本机制只管理"等级随证据时间演化"，不产生证据本身；它相信上游 junit 与对标
   记录的真实性（这点由证据链中 `report_sha256`/`source_sha256` 与平台审计链承担）。对
   **一次性**验收（只跑一次）的场景，窗口 ≥3 会拒绝升级——这是刻意的，一次通过不是持续性证据。
5. 与既有 `tools/capability_upgrade`（P348-W3-N01，逐级状态机 + 规则表 + 缺陷阻塞）互补：
   本节点新增的是**时间持续性判据**（streak/hysteresis）、junit 摘要入证、以及针对
   `audit_capabilities` 负载的监听与 mismatch 记录；两者共用同一等级阶梯语义。
6. **HTTP 适配层不是规则层**：`api.py` 只有读路径（`/status`、`/events`、`/verify`）与两条写路径
   （`/runs`、`/audit-snapshot`），阈值与决策全部在 `policy.py`/`streak.py`；适配层由两条 pytest
   用例（200 / 422 / 409）覆盖。首版把 `payload: Any` 当请求体，FastAPI 把它解析成了 **query**
   参数（实测返回 422 且 `loc=["query","payload"]`），改为 `Body(...)` 后修复。
7. **单进程假设**：SQLite 镜像 + JSONL 追加写在单进程内自洽（`fsync` + 只追加），尚未做多进程
   并发的写入仲裁；多实例部署前需要加文件锁或改为平台数据库事务。

## ⑥ 误差判定（逐项）

| 关键量 | 本节点结果 | 基准 | 误差 / 判定 |
|---|---|---|---|
| 升级触发位置 | 第 3 次运行（窗口 = 3） | 判据"窗口满即升级" | 0 次偏差 → 一致（严格 = 0，<3%） |
| 降级后等级 | `WITHHELD` | `REAL` 下退一级 = `WITHHELD` | 一致（=0，<3%） |
| 低等级对照（无晋升） | 重放/抖动场景等级 `MOCK`/`MOCK` | 应保持 `MOCK` | 一致（=0，<3%） |
| 哈希链校验 | 6 条事件全部通过 | 全部通过 | 0 条不符（<3%） |
| 篡改检出 | JSONL 2 条问题、SQLite 1 条问题 | 必须被检出 | 检出率 2/2（<3%） |
| 域词条命中 | 0 | 0 | 一致（<3%） |

**是否 < 3%**：上表 6 项关键量全部为**确定性结果**（计数/等级之一），与基准逐项完全一致，
误差 0% < 3%。本节点不涉及连续物理量，故无"对标实验误差"一项；数值误差阈值（15%）本身作为
**策略输入**被单独测试（15.0% 接受 / 15.01% 拒绝、通过率 90% 接受 / 89% 拒绝的行边界用例见 pytest）。

## 验收判据汇总

| 判据 | 结果 | 数字 |
|---|---|---|
| (a) MOCK→REAL 自动升级 + 台账完整 | True | 等级轨迹 `MOCK → MOCK → REAL → WITHHELD → MOCK`；行完整=True |
| (b) 回归失败自动降级 + 无静默覆盖 | True | 降级事件 2 条；追加写历史保留=True |
| (c) 升级/降级均有 sha256 可溯源 | True | 链 6 条 OK；篡改检出 2/2 |
| (d) pytest ≥ 12 覆盖升级/降级/边界 | True | 39 条，通过率 100.0% |
| (e) 零领域语义（合成域名，deny-list 0 命中） | True | 命中 0 / 14 文件 |

**最终判定：PASS**（诚实申报：如任一项为 False，本报告保留原值不修饰。）
