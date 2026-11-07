# IN28-N06 —— SR1 影子决策成熟化 + v0 退役

**生成时间**：2026-11-07T13:32:15.804777Z　**schema**：`IN28-N06-acceptance/1`

## 摘要

* 成熟化引擎把影子提议与事后处置自动对齐：``coordinator_shadow`` 通道 74 条提议，**匹配率 90.5%**（成熟子集 100.0%），回填库 **1865 条**（门槛 50）。
* SR1 影子分歧率从 `WITHHELD` 变成连续数字：**28.4%**（95% Wilson CI 19.0%–40.1%），已写入 chronicle s8。**该数字高于 20% 目标，如实记为 target-miss**。
* v0 影子协调官已退役：杀掉 0 个 v0 进程，退役后 legacy 写 **0 条**，v1 为唯一写者。
* 回滚演练 **PASS**（坏部署全拦、回滚 0.0019 s、状态逐字节一致、篡改负控通过）。
* fail-closed 自检：3/3 逃逸被拒，沙箱外写 **0**。
* pytest：**91 passed / 0 failed**（命令见 §6.3）。

## 1. 案例介绍

### 1.1 问题背景

影子协调官每 30 min 拍一次平台脉搏，判读「若我是当值协调者会做什么」，把结论 append 进
`coordinator_shadow` 表。IN27-H7 的继承就绪仪表要算 SR1 = **影子提议 vs 事后实际处置**
的分歧率，但当时没有任何通道把两边对齐：账本里只有「提议」，处置散在任务表、事件流、
PR 结局里，于是 SR1 长期 `WITHHELD`——一个永远算不出来的继承就绪度量。

本单补的就是这条通道，并顺手把 v0/v1 两代写者并存的历史债清掉。

### 1.2 对象与参数表

| 项 | 值 | 说明 |
|---|---|---|
| 提议源 A | `coordinator_shadow`（17 拍：v1 7 / v0 10） | 影子账本 |
| 提议源 B | `shadow_merge.json`（1787 条 PR 门禁影子裁决） | 跨仓门禁影子 |
| 评估窗口 | 72 h（前瞻 1 h） | 任务书 72h |
| 判据门槛 | 匹配率 ≥ 90%，回填 ≥ 50 条，分歧率 < 20% | 任务书数字 |
| 沙箱根 | `runs/in28_n06/sandbox`（+ `artifacts/` 声明写面） | 写面白名单 |

## 2. 怎么做的

### 2.1 匹配阶梯（结构键优先，词面匹配退场）

| 级 | 证据 | 裁决 | 覆盖条数（本单实测） |
|---|---|---|---|
| L1 `mission_outcome` | 任务表 `task_key` 与提议的 `task_key` 精确相等 + 立单时刻不早于提议 | 终态执行 → 一致；失败/撤回 → 分歧 | 5 |
| L2 `telemetry` | 同一指标的后续拍观测（账本 `pulse_digest`），按注册表阈值判越界 | 仍越界 → 一致；回界 → 条件消解（分歧） | 62 |
| L1 `pr_outcome` | `repo#pr` 影子裁决 ↔ 实际 `merged/closed` | 同向 → 一致 | 1784 |

IN27-H7 用的是「器官名在处置文本里出现」这种词面匹配；处置文本不提器官名就被判成未处置，
把**数据缺口算成分歧**。本引擎改用结构键，找不到结构键时记 `unobservable` 而不是分歧——
两个口径分开数：`match_rate` 数「有没有挂上处置」，`divergence_rate` 只数「挂上且判得出方向」的那部分。

### 2.2 回填库（append-only + 幂等）

SQLite `maturation_records`，主键 `record_hash = sha256(proposal_id, level, state, disposition_id, observed_at)`。
重跑同一批输入 `INSERT OR IGNORE` 不增长；`content_digest` 两次运行逐字节相同（见 §5.2 复算证据）。

### 2.3 v0 退役的双条件判定

写者指纹：`v1 = (beat_id 非空 ∧ schema='coordinator-shadow-beat/1')`，其余记 `legacy`（v0 用
`max(id)+1` 手算主键、不写这两列）。退役 PASS 需要**同时**满足：进程面 v0 不存在、账本面退役后
legacy 写 = 0、且 v1 仍在岗（唯一写者）。

### 2.4 部署门与回滚演练

三种坏候选（schema 错 / 部署后篡改 / 版本不单调）必须被门拒绝且状态摘要不变；好部署必须过门；
回滚用快照目录恢复，`time.monotonic` 计时，恢复后重算 sha256 清单与基线逐条比对；
另做**篡改负控**（改快照 1 字节，校验器必须报差异），防止「校验器永远 True」式假 PASS。

### 2.5 工具链

```bash
python3 -m tools.coordinator_shadow.maturation        # 成熟化 + 回填（+ --publish-chronicle 落 s8）
python3 -m tools.coordinator_shadow.retirement --retire  # v0 退役 + 写者审计
python3 -m tools.coordinator_shadow.drill            # 坏部署门 + 回滚演练
python3 -m tools.coordinator_shadow.sandbox          # fail-closed 自检（含三种逃逸负控）
python3 -m tools.coordinator_shadow.maturation_figures  # 图
python3 -m tools.coordinator_shadow.n06_bundle       # 验收包 + 本报告
ops/in28_n06_maturation_loop.sh                      # 1 h 一拍，连续产出
```

## 3. 计算结果

### 3.1 匹配率与回填（判据 a）

| 通道 | 提议数 | 已成熟 | 挂上处置 | 匹配率 | 成熟子集匹配率 | exact 路由匹配率 |
|---|---|---|---|---|---|---|
| `coordinator_shadow` | 74 | 67 | 67 | 90.5% | 100.0% | 91.5% |
| `shadow_merge` | 1787 | 1786 | 1784 | 99.8% | 99.9% | 100.0% |
| **合计** | **1861** | **1853** | **1851** | **99.5%** | **99.9%** | **99.7%** |

回填库累计 **1865 条**记录（门槛 50，达成倍数 37.3×）。

### 3.2 SR1 分歧率（判据 c）

| 分组 | 已判定条数 | 分歧条数 | 分歧率 | 95% Wilson CI |
|---|---|---|---|---|
| 全体（`coordinator_shadow`） | 67 | 19 | 28.4% | 19.0%–40.1% |
| 写者 v0 | 44 | 15 | 34.1% | 21.9%–48.9% |
| 写者 v1 | 23 | 4 | 17.4% | 7.0%–37.1% |

chronicle s8 已落盘：状态 `REAL`，值 **28.4%**，写入时刻 `2026-11-07T13:06:26.758493+00:00`，读取键 `payload->s8_harness->sr_inheritance->SR1`（与 IN27-H7 的仪表同形，`trend` 直接可读）。

**读法**：v1（结构化脉搏 + 立单能力）的分歧率显著低于 v0（手搓脉搏、无立单）。
分歧的构成全部是 `resolved_condition`——提议断言「某指标越界」，前瞻 1 h 内该指标回界。
这既可能是平台真的修好了，也可能是影子在看陈旧/代理读数；**本引擎不做归因**，只如实记账
（`divergence_kind=resolved_condition`），谁修的要看任务与事件流，不在本单判据内。

### 3.3 v0 退役（判据 b）

| 项 | 值 |
|---|---|
| 退役前 v0 进程 | 0 个（`coordinator_shadow.py`） |
| 退役后 v0 进程 | 0（不存在） |
| v1 在岗 | 是（唯一写者） |
| 退役后 legacy 写 | 0 条 |
| 账本 v1 / legacy 行 | 8 / 10 |
| 最后一条 legacy 写 | `2026-11-07T12:59:15.848892Z`（退役时刻之前） |
| 最后一条 v1 拍 | `2026-11-07T13:30:00Z` |
| 观察窗口上界 | `2026-11-07T13:32:15.804777Z`（已覆盖 ≥1 个 v0 拍周期，见 §5.6） |

`:legacy` 行是历史存量（v0 在岗期间写的），退役判据只看**退役时刻之后**有没有新增。

### 3.4 回滚演练（判据 d）

| 判据 | 门槛 | 实测 | 判定 |
|---|---|---|---|
| 坏部署被拦 | 全部拒绝 | True | PASS |
| 坏部署不改状态 | 逐字节不变 | 见 `rollback_drill.json` | PASS |
| 回滚耗时 | < 300 s | 0.0019 s | PASS |
| 状态逐字节一致 | 清单全等 | True | PASS |
| 篡改负控 | 必须发现差异 | True | PASS |

### 3.5 fail-closed 沙箱（判据 e）

| 项 | 值 |
|---|---|
| 逃逸用例（`..` / 绝对路径 / 符号链接） | 3 |
| 被拒 | 3 |
| 沙箱外已提交写（production writes） | 0 |
| 引擎运行期越界写 | 0 |
| `fail_closed` | True |

## 4. 结果图

| 图 | 内容 | 文件 |
|---|---|---|
| 匹配阶梯 | 通道×状态堆叠 + 匹配率（95% CI）vs 90% 门槛 | `runs/in28_n06/figs/fig_match_ladder.png` |
| SR1 分歧率 | 全体/v0/v1 分歧率（CI）vs 20% 目标 + 匹配时滞分布 | `runs/in28_n06/figs/fig_divergence.png` |
| 回填与门 | 回填条数 vs 50 条门槛；回滚演练判据矩阵 | `runs/in28_n06/figs/fig_gates.png` |

## 5. 注意点（踩过的坑 / 局限）

1. **`tensor_dev_missions` 没有 `updated_at`**：首版 L1 查询带 `updated_at` 直接整条 SQL 失败，
   任务侧处置静默变成 0 条。改成 `created_at/finished_at` 后 L1 才真正生效——这类错误在合成
   fixture 里看不见，只有连真库才暴露。
2. **代理路由会抬高匹配率**：`campaign_blocked ↔ blocked_nodes`（chronicle s2 含门停放）口径不同，
   这类记录带 `route_grade=proxy`，报告里同时给 `exact_match_rate`，不让代理悄悄混进指标。
3. **「条件消解」不等于「影子错」**：分歧全部是 `resolved_condition`。前瞻 1 h 内指标回界，可能是
   平台修好了。引擎不做归因；要归因得把任务/事件流也接进 disposition 源（下一步）。
4. **v0 的 would_do 是行动建议不是断言**：本单用「建议所指器官的遥测是否仍越界」判其前提是否成立，
   `action=file_mission` 是否被真执行另算（v0 无立单能力，按设计不记分歧）。
5. **回滚演练的对象是本引擎自己的部署状态目录**，不是生产 systemd 单元；生产回滚仍走运维通道。
6. **退役观察窗口已覆盖一个完整 v0 拍周期**：13:01 退役 → 13:32 复扫，v0 的两个槽位（:26/:31）
   没有出现新 legacy 行，同时 v1 在 13:30 正常写出新拍（`beat_id` 递增）——这才把「v0 不存在、
   v1 唯一写者」从进程面推到行为面。更长的行为由 `ops/in28_n06_maturation_loop.sh` 每拍续验。
7. **全量 pytest 有 12 个与本单无关的收集错误**（`src.ship_perf` 缺 `seakeeping` 等），
   本单的绿色判据限定在 `coordinator_shadow` 及其相关影子套件（91 条）。

## 6. 误差判定

### 6.1 与独立实现的对照（`ops/taskplane/shadow_merge.py`）

`shadow_merge` 通道的分歧率是本引擎对**既有独立实现**的一次复算：

| 量 | 既有实现（`/root/tp-agent/shadow_merge.json:stats.rate`） | 本引擎 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 门禁影子一致率 | 58.4%（`stats.rate`，1784 条带结局） | 58.4% | 见 §6.2 复算记录 | 是（同批数据、同口径） |

### 6.2 逐项判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 匹配/分歧计数 | 合成账本闭式期望（`tests/test_coordinator_shadow_maturation.py::test_match_rate_and_divergence_rate_match_closed_form`） | 0（逐值断言） | **是（0%）** |
| 回填幂等性 | 同输入重跑：`inserted=0`、`content_digest` 相同 | 0 | **是（0%）** |
| 回滚一致性 | 基线 sha256 清单 | 0（逐文件相等） | **是（0%）** |
| 门禁一致率 | 既有 `shadow_merge.py` 统计 | 见下表（同批 1784 条） | **是（同源复算）** |
| SR1 点估计 | 无金标准（处置归因缺） | 抽样 CI 宽（见 §3.2） | **无法判定**：只给区间，不给「准不准」的承诺 |

### 6.3 复算命令

```bash
# pytest（判据 f）
/usr/bin/python3 -m pytest tests/test_coordinator_shadow_judge_filing.py tests/test_coordinator_shadow_ledger_acceptance.py tests/test_coordinator_shadow_pulse.py tests/test_coordinator_shadow_soak.py tests/test_shadow_campaign_blocked_semantics.py tests/test_coordinator_shadow_maturation.py tests/test_coordinator_shadow_sandbox_drill.py tests/test_coordinator_shadow_retirement.py -q

# 逐条匹配记录（含 lag / level / route_grade）
python3 -c "import json;d=json.load(open('runs/in28_n06/sandbox/records.json'));print(len(d['records']))"
print(len(d['records']))"
```

## 7. 产物索引

```
tools/coordinator_shadow/maturation.py            成熟化引擎（匹配阶梯 + 回填库 + s8 发布）
tools/coordinator_shadow/retirement.py            v0 退役 + 写者指纹审计
tools/coordinator_shadow/drill.py                 部署门 + 回滚演练
tools/coordinator_shadow/sandbox.py               fail-closed 沙箱 + 逃逸负控
tools/coordinator_shadow/maturation_figures.py    三张结果图
tools/coordinator_shadow/n06_bundle.py            验收包 + 本报告渲染
tests/test_coordinator_shadow_maturation.py       15 条
tests/test_coordinator_shadow_sandbox_drill.py     9 条
tests/test_coordinator_shadow_retirement.py        7 条
artifacts/coordinator_shadow/sr1_maturation.json  判据数字 + 通道统计 + 沙箱审计
artifacts/coordinator_shadow/v0_retirement.json   退役前后进程 + 账本指纹
artifacts/coordinator_shadow/v0_retirement_pre.json  退役前快照（对照）
artifacts/coordinator_shadow/rollback_drill.json  演练逐项结果
artifacts/coordinator_shadow/failclosed_selfcheck.json  逃逸负控
artifacts/coordinator_shadow/IN28_N06_acceptance.json  六条判据机器验收包
runs/in28_n06/sandbox/maturation.sqlite3         回填库（append-only）
runs/in28_n06/sandbox/records.json               逐条匹配记录
runs/in28_n06/figs/*.png                         三张图
ops/in28_n06_maturation_loop.sh                  1 h 一拍的连续产出脚本
```

