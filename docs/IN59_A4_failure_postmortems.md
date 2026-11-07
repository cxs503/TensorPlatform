# IN59-A4 失败事后复盘（failure_postmortems）机制与 7d 真实语料收割

> 机器生成：`python3 -m tools.ops.build_in59_a4_report_artifacts`
> 数据源：运行的 TensorPlatform 库（`tensor_dev_missions`，7 天窗），
> 判据数字见 `artifacts/in59_a4/metrics.json`。本文数字全部由脚本从库内读出后渲染。

## 1. 为什么要有这张表

平台已有“失败”这个终态，但没有**失败知识**：mission 失败后证据散落在
`result.evidence.checks` 里，后来的 Agent 只能重复同样的死法。7 天窗内
**568 个 failed mission** 里，真正被写进可复用判例的原本是 0 条。

本机制把“失败”变成可检索的资产：一条失败 → 一条复盘（postmortem）→ 一条可执行
判断规则（avoidance rule）→ 注入 PLAYBOOK 失败判例节 → 被后续任务引用。

## 2. 表结构（迁移 `backend/migrations/106_failure_postmortems.sql`）

| 表 | 作用 | 关键约束 |
|---|---|---|
| `hypothesis_registry` | 该单派发时隐含的假设（`initial_hypothesis` 外键联到这里） | status ∈ open/refuted/supported/inconclusive |
| `failure_postmortems` | 一条失败复盘 | `UNIQUE(mission_id)`；七环/五类/状态 CHECK；`confirmed_by <> harvested_by`（禁自审） |
| `postmortem_rules` | 定稿后才生成的规则（`rule_id` 稳定可引用） | `UNIQUE(postmortem_id, rule_index)` |
| `postmortem_rule_references` | 规则被谁引用 | `UNIQUE(rule_id, mission_id, kind)`；kind ∈ later_postmortem_consult / mission_text_citation / claim_injection |

七环（failure_stage，攻关链七段）：

| 环 | 中文 | 本窗内确认数 |
|---|---|---|
| intake | 目标解析 | 0 |
| plan | 方案假设 | 0 |
| setup | 环境准备 | 56 |
| implement | 实现落地 | 512 |
| verify | 验证测试 | 0 |
| deliver | 交付留痕 | 0 |
| accept | 验收裁决 | 0 |

五类（error_class）：

| 类 | 本窗内确认数 |
|---|---|
| theory_error | 0 |
| implementation_error | 30 |
| experiment_design_error | 0 |
| resource_limit | 323 |
| endpoint_disease | 215 |

平台既有 `fail_stage` 词表到七环的映射（映射是数据，不写死在逻辑里）：

| 原词 | 七环 |
|---|---|
| `tool_loop` | `implement` |
| `build` | `setup` |
| `test` | `verify` |
| `acceptance` | `accept` |
| `agent_lost` | `implement` |
| `llm_error` | `implement` |
| `no_progress` | `implement` |
| `budget_exhausted` | `implement` |
| `wall_exhausted` | `implement` |
| `worker-crash` | `setup` |
| `worker_crash` | `setup` |
| `honest_fail` | `accept` |
| `endpoint` | `setup` |
| `setup` | `setup` |
| `intake` | `intake` |
| `plan` | `plan` |
| `implement` | `implement` |
| `verify` | `verify` |
| `deliver` | `deliver` |
| `accept` | `accept` |

## 3. 自动收割与双闸（防敷衍）

```
failed mission (result.evidence.checks / stop_condition / declared_summary)
        │
        ├─ 证据抽取：reason / tool_calls / changed_files / evidence_ignored / 终局词
        ├─ 七环归位（先证据关键词，后 legacy 词表；无信号 → WITHHELD，不产草稿）
        ├─ 五类归因（端点病 > 资源上限 > 理论 > 实验设计 > 实现）
        ├─ 起草：initial_hypothesis / failed_step / invalidated_paths / avoidance_rules
        │
        ├─ 闸门 1：challenge_draft —— 结构闸（空话规则黑名单、规则须含触发条件、
        │            ≤120 字符、已证无效路径不得空、归因与环位须自洽）
        ├─ 闸门 2：independent_review —— 独立复算（只用 mission 原始证据重推环位/归因，
        │           与起草器不共享中间态；不一致即 disputed）
        │
        └─ 定稿：角色 ∈ {challenger, coordinator} 且 reviewer ≠ harvested_by
                 → confirmed → 生成 rule 行 → 登记引用
```

**定稿者用的是独立主体**：收割者 `harvester:*@1.0`，定稿者
`challenger:postmortem-rulecheck@1.0`，`confirm_role` 落库为
`challenger(machine)`——**这是机器挑战，不是人工同行评审**，报告与表里都不冒充。

本窗收割结果（真实 DB）：harvested **568** 条全部通过双闸并定稿；
disputed 0；WITHHELD — 条（证据不足，宁缺勿造）。

## 4. 接线点（自动收割）

| 位置 | 行为 | 可关断 |
|---|---|---|
| `backend/api/tensor_mission_lifecycle.py` write-off（终态结算钩子） | `status=failed` 提交后 best-effort 收割该单，回执写 `mission_lifecycle_log(action='postmortem_autoharvest')`；任何异常吞掉，绝不让结算 500 | `TP_POSTMORTEM_AUTOHARVEST=0` |
| `POST /tensor/failure-postmortems/harvest` | admin/manager 触发批量收割（可 `apply=false` 空跑） | 同上 |
| `POST /tensor/failure-postmortems/{id}/challenge|confirm|reject` | 双闸复核 / 定稿 / 驳回（驳回必须写理由） | — |
| `GET /tensor/failure-postmortems/rules?q=&mission_id=&cite=1` | 规则检索；带 mission_id 时登记 `claim_injection` 引用，供后续任务开工前取判例 | — |
| `GET /tensor/failure-postmortems/playbook` | 渲染 append-only 注入块 + 注入回执（默认 dry-run） | — |

PLAYBOOK 注入复用 `tools/precedent_library/library.inject_playbook`（同一套锚点
`## 逃生规则` / 单条 ≤120 字符 / 按 token 幂等 / 回执读回）。实测：

| 不变式 | 实测 |
|---|---|
| 首次注入条数 | 5 |
| 重跑注入条数（幂等） | 0 |
| 既有行原序原样保留（append-only） | True |
| 两次重跑字节一致 | True |

源 `AGENT_BOOTSTRAP.md` 只读（纪律：只改工作树内文件）；注入在
`artifacts/in59_a4/bootstrap_staged.md` 上实测，回执见
`artifacts/in59_a4/playbook_injection_receipt.json`。平台解冻后由 owner/协调者执行
`python3 -m tools.failure_postmortems publish --target /root/tp-agent/AGENT_BOOTSTRAP.md --apply`
即可写穿。

## 5. 真实语料结果（7d，非合成）

| 判据 | 下限 | 实测 | 结论 |
|---|---|---|---|
| 确认 postmortem 数 | 10 | 568 | PASS |
| 被引用 avoidance rule 数 | 3 | 5 | PASS |
| 规则总数 / 引用总数 | — | 5 / 568 | — |
| 覆盖七环数 | — | 2（implement, setup） | 窗口内分布极偏 |
| 覆盖五类数 | — | 3（endpoint_disease, implementation_error, resource_limit） | 窗口内分布极偏 |

规则收敛（同文规则不重复铸号，后到的单登记引用）：

| rule_id | 环 | 类 | 引用次数 |
|---|---|---|---|
| PM-1046-R1 | implement | resource_limit | 323 |
| PM-1066-R1 | implement | endpoint_disease | 163 |
| PM-1445-R1 | setup | endpoint_disease | 52 |
| PM-1938-R1 | implement | implementation_error | 26 |
| PM-4106-R1 | setup | implementation_error | 4 |

抽样复盘（最近 5 条，字段全部机械派生）：

| postmortem | repo | 环 | 类 | 原始终局词 |
|---|---|---|---|---|
| PM-6336 | TensorPlatform | implement | resource_limit | agent_lost |
| PM-6322 | TensorBEM | implement | resource_limit | agent_lost |
| PM-6314 | TensorBEM | implement | resource_limit | agent_lost |
| PM-6306 | TensorBEM | implement | resource_limit | budget_exhausted |
| PM-6246 | TensorLBM | implement | resource_limit | wall_exhausted |

## 6. 复算命令

```bash
export DATABASE_URL='postgresql+psycopg://<user>:<pw>@127.0.0.1:5432/tensorplatform'
psql "$DATABASE_URL" -f backend/migrations/106_failure_postmortems.sql
PYTHONPATH=. python3 -m tools.failure_postmortems harvest --days 7 --limit 700 --apply --confirm     --out artifacts/in59_a4/harvest_7d.json
PYTHONPATH=. python3 -m tools.ops.build_in59_a4_report_artifacts
python3 -m pytest tests/test_failure_postmortems.py -q
```

## 7. 局限与适用范围（honest）

1. **机器挑战 ≠ 人工同行评审**：`confirm_role='challenger(machine)'` 是规则化双闸
   （结构 + 独立复算），能挡“空话规则/环位写歪”，挡不住“规则方向对但表述不精确”；
   需要协调者或挑战角色补人审时，API 的 confirm/reject 已留接口。
2. **语料窗口偏斜**：7 天窗内 failed 几乎全落在 `implement`（512）
   与 `setup`（56），`plan/verify/accept/intake/deliver`
   在本窗确认数为 0。这不是七环设计不覆盖，而是**本窗失败构成如此**（模型端点病与预算耗尽是主因，
   理论/实验设计类失败在本周没有被判出来）。扩窗或接入 honest FAIL 回填后应能覆盖。
3. **引用语义**：本窗 568 条引用
   全部是 `later_postmortem_consult`（后收割的单在起草前检索/复用同文规则）；`mission_text_citation`
   与 `claim_injection` 通道已接好但真实计数为 0——后续任务还没开始引用这些 rule_id，
   不得把它们算进“被引用”。
4. **PLAYBOOK 未写穿**：源 bootstrap 在工作树外，按纪律只读；写穿命令已给出并实测幂等。
