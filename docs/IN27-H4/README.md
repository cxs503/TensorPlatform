# IN27-H4 · 周期性质询机制（战略质询官）

> owner 的「是否做到位了」式审计职能转移件。每 24h 自动生成 3-5 个战略质询使命
> （问对不问错），质询结果回填账本，被证实的质询转化真实修补/改进单；
> 连续性与转化率由机器判据裁定，不允许自述通过。

- 代码：`src/interrogator/`（7 模块，纯标准库，无后端依赖）
- 测试：`tests/test_interrogator.py`（24 用例）
- 调度：`ops/interrogator_h4_loop.sh`（24h，flock 单实例）
- 账本/产物：`runs/IN27-H4/`（`interrogator.db` + `.audit.jsonl` + 周期/信号快照 + `verdict.json`）
- 图：`docs/IN27-H4/figures/`（脚本 `tools/make_interrogator_figures.py`，数字零手抄）

## 1. 为什么需要它（问题设定）

owner 的审计职能不是"检查交付物有没有"，而是**把注意力压到结构性失真上**：
一个器官长期零吞吐、证据分级永远停在 S2、预算账面写满而实耗恒零、失败从不入经验账本——
这些都不是某张单的对错问题，而是**机制在自我遮蔽**。人做这件事靠每周问几个刁钻问题；
转移给智能体后必须满足三条硬约束，否则就是表演：

1. **可复跑**：每个问题引用的数字必须来自探针与快照，能回溯到端点/表/文件行；
2. **可证伪**：每题必须给 ≥3 个互斥解释分支 + 各自的判别方法，禁止"我觉得"式结论；
3. **可转化**：问出的结论必须落到"立不立修补单"的二元判断，且转化要带证据文件与单号。

## 2. 怎么做的（机制）

```
probes.py          只读采集 13 类信号（失败记 unavailable，绝不补零）
   ↓ signals（带 source/status，落 signals_<date>.json）
question_bank.py   五族题目：ZERO_FLOW / GRADE_CEIL / EXP_BUDGET / SN_TRACK / CONVERSION
   ↓ 候选（每题含 evidence/branches≥3/criteria/convert_rule）
generator.py       配额 3-5（按日确定）+ 7 周期去重 + 跨族轮转 + 严重度优先
   ↓ mission 参数（goal ≥80 字、含判据与 honest FAIL、task_key 幂等）
ledger.py          SQLite：cycles / interrogations / conversions + append-only 审计 JSONL
   ↓
daemon.py          24h 循环：采集→出题→记帐→立单（POST /tensor/missions，task_key 幂等）→回写单号
   ↓
acceptance.py      A1 题数∈[3,5] · A2 连续 7 天 live 产出 · A3 ≥2 件验证过的转化
```

### 关键设计取舍

| 取舍 | 做法 | 理由 |
|---|---|---|
| 分级键 | `fingerprint`=族:题号（题目身份）；`occ_id`=fp@日期#序（一次真实提问） | 题库有限（~8 题），必须允许证据变化后复用题目；但同一天不许问两遍（防刷量） |
| 去重窗口 | 7 周期内优先不重复，池耗尽才复用最旧 | 质询的价值在"注意到变化"，连问同一题会掩盖新失真 |
| 题数 | 按日期 hash 定 3-5（不是随机） | 可复跑、可预期；避免"随机造成连续性判据不可复核" |
| 立单幂等 | `task_key=interrogator:<fp>` | 平台对同 task_key 返回既有单（`idempotent=True`），重复周期不重复立单 |
| 转化门槛 | 证据文件存在 + 非空 + sha256 当场计算 + 必须挂单号/幂等键 | 防"口头转化"与事后改写证据（A3 会拦下篡改） |
| backfill | 历史快照回放标 `mode=backfill`，不计入 A2 | 绝不用回放冒充"当日产出" |
| 探针失败 | 记 `unavailable`，并转成"度量缺失"型质询 | 补零会把"没测"伪装成"测到 0"，质询会因此问错对象 |

## 3. 本期真实结果（2026-11-07，cycle `2026-11-07#1`）

### 3.1 探针（真实平台信号，`runs/IN27-H4/signals_2026-11-07.json`）

| 信号 | 值 | 状态 |
|---|---|---|
| missions_by_status | running 18 / succeeded 153 / failed 17 / queued 8 / awaiting_pr_merge 4 | ok |
| missions_failed_window / honest_FAIL | 17 / **0** | ok |
| mission_grade_counts | `{}`（200 条 result 无 grade 字段） | partial |
| plans_budget | 34 个主计划中 **26 个 budget_actual 全零**（76%） | ok |
| capability_coverage | total_deltas 1366 / **substantive 0** / succeeded 4049 / covered 1359（33.6%） | ok |
| playbook_entries | 15 | ok |
| zero_flow_organs | 无端点 | unavailable（=度量缺失，作为一等事实） |

### 3.2 出题与立单

4 题（配额按日 = 4，落于 [3,5]），跨 3 族，全部立单成功（platform mission 5309-5312）：

| 题 | 族 | 严重度 | mission | 一句话 |
|---|---|---|---|---|
| GC-03 | GRADE_CEIL | 5 | 5309 | 1366 条能力增量实质增量=0：天花板是结构性的 |
| EB-01 | EXP_BUDGET | 5 | 5310 | 26/34 主计划 budget_actual 全零：预算真的被用了吗 |
| GC-02 | GRADE_CEIL | 4 | 5311 | mission.result 无 grade 字段：天花板结论本身无据 |
| SN-01 | SN_TRACK | 4 | 5312 | 17 failed / 0 honest-FAIL：负结果入账了吗 |

### 3.3 转化（本单最重要的产出：两个真实缺陷被钉到行级）

| 转化 | 类型 | 修补/改进单 | 根因（行级） |
|---|---|---|---|
| EB-01 → 5313 | repair | platform mission **5313** | `backend/api/campaign_plans.py:418-429` 聚合"实耗"（实为计划上限）后只在 `:489-506` 写 `campaign_snapshots`；全仓无 `UPDATE campaign_plans SET budget_actual` ⇒ plans 端点恒零（`:165`） |
| GC-03 → 5314 | repair | platform mission **5314** | `backend/api/tensor_approvals.py:268-275` 审批 approved 时若无 `mission.result.capability_delta` 则落诚实兜底 `no_substantive_delta`；complete 侧从不引导声明 ⇒ 100% 走兜底（1366/1366） |

另两题回填为结论：GC-02 = 分级链断线（`evidence_grade` 只存在于船舶域响应，不进 mission.result），
并入 5314；SN-01 = 负结果入账率**不可计算**（failed 单不带 verdict/lessons 字段），记负结论并关账，
把建议字段 `mission.result.lessons` 写进证据文档供 IN27-H6 对接。

### 3.4 验收门（`runs/IN27-H4/verdict.json`）

| 判据 | 实测 | 要求 | 判定 |
|---|---|---|---|
| A1 每周期题数 | 4（越界 0） | ∈[3,5] | **PASS** |
| A2 连续 7 天 live 产出 | 1/7（streak=1） | 7 | **NOT MET → PENDING** |
| A3 ≥2 件验证转化 | 2（篡改 0 / 证据缺失 0 / 未挂单 0） | 2 | **PASS** |

`verdict = PENDING`，退出码 3（0=PASS/3=PENDING/1=FAIL）。
**honest-FAIL 声明**：A2 在本单周期内不可能达成——它要求"连续 7 个 UTC 日各至少一个 live 周期"，
而机制今天才第一次真实上线（账本 `live_days_total=1`）。我不做历史伪造、不用 backfill 充数
（`test_gate_rejects_backfill_days_disguised_as_live` 就是拦这个的），
机制按日运转、由 `ops/interrogator_h4_loop.sh` 累积，第 7 个 live 日后 A2 自动转 PASS。

## 4. 部署

```bash
# 一次性（cron 方式，推荐）：每天 03:17 UTC 跑一周期 + 验收
17 3 * * * cd /root/tp-agent/work/TensorPlatform && \
  bash ops/interrogator_h4_loop.sh --once >> /var/log/interrogator_h4.log 2>&1

# 或常驻（nohup/systemd），24h 间隔，flock 单实例
nohup bash ops/interrogator_h4_loop.sh >> /var/log/interrogator_h4.log 2>&1 &
```

凭据来自环境（`TENSOR_API_BASE` / `TENSOR_AGENT_USER` / `TENSOR_AGENT_PASS`），脚本内不含任何秘密。
**本单未在宿主安装 cron/systemd 条目**（纪律：只改工作树内文件），故"7 天连续"需部署方执行上面一行。

## 5. 局限与适用边界（诚实清单）

1. **A2 未达成**（1/7 天）。这不是机制缺陷，而是观察窗口未满；但按判据它就是未达成，不许粉饰。
2. **零流器官族只有"度量缺失"题**（ZF-01）：平台无器官吞吐只读端点，机制无法在数值上判定谁零流；
   本轮该题因配额（日题数 4）未被选中，属正常配额竞争。
3. **探针只读 200 行上限**（`limit=200`）：`succeeded=4049` 是平台 summary 给的全体数，
   而窗口终态统计基于最近 200 行，"7 日窗口"实际接近"最近 200 单"，量级可比但非严格窗口。
4. **能力 grade 分布不可得**：平台只有 `/capability/deltas|summary`，无 grade 清单端点，
   故"grade 天花板"只能从能力账本语义推断（GC-03），不能直接统计。
5. **题目库有限（~8 题）**：7 天后进入复用循环。复用是设计（证据变化即新问题），
   但若要持续 30 天以上不重复，需扩充题库（每族 ≥4 题），这是明确的下一步。
6. **转化质量依赖人工/审计方判定**：机制只强制"证据 + 单号 + 摘要"，无法自动判断修补单是否被真正执行；
   执行侧的闭环属于 IN27-H2/H3（门控与手术权）。

## 6. 复现

```bash
# 全测试
python3 -m pytest tests/test_interrogator.py -q          # 24 passed

# 只读出题（不立单）
python3 -m src.interrogator.cli --out runs/IN27-H4 cycle --dry-run

# 真实周期（需凭据）
python3 -m src.interrogator.cli --api-base "$TENSOR_API_BASE" --out runs/IN27-H4 cycle --create-missions

# 验收门（0/3/1）
python3 -m src.interrogator.cli --ledger runs/IN27-H4/interrogator.db --out runs/IN27-H4 verify

# 图
python3 tools/make_interrogator_figures.py
```