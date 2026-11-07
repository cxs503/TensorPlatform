# 审查预算账本（SPEC §6.5 测量先行）

本文件由 `tools/governance/review_budget_backfill.py` 程序化生成，数字全部来自真实记录，
零手抄；账本 `artifacts/review_budget_ledger.jsonl` 只增不改（sha256 链可校验）。

## 1. 口径

- **四类审查**（SPEC §6.1）：Execution Audit / Technical Review / Outcome Assessment / Innovation Review。
  每条记录：审查对象（subject）、类型（review_type）、耗时（duration_s）、结论（conclusion）、
  审查者（reviewer），并附来源（evidence.source_ref）与耗时来源（duration_source）。
- **耗时来源分级**：`observed`（由原始记录时间戳相减）／`reported`（审查者申报）／
  `not_observable`（只有事件、没有耗时，计入事件数但不计入分钟数）。
- **比例定义**：`治理耗时 / 生产耗时`（§6.5 建议 ≤0.25）。另给事件口径比例与
  治理耗时占活动总耗时份额，三者并列，避免单一指标误读。
- **限额状态**：本期**只测量不限额**。`cap_status=insufficient_duration_coverage` 表示
  耗时覆盖率低于 0.5，此时不得据此判定是否达标；`measured` 才可读比例与上限对比。
- **豁免留痕**（§6.5「不得静默免审」）：`record_exemption` 与审查同账本同链，
  必须带 reason，结论固定 `exempted`，在周报里单列。

## 2. 接口（`/api/v1/governance/review-budget/*`）

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/types` | GET | 四类审查定义与合法取值 |
| `/reviews` | POST | 记一笔审查（类型/对象/耗时/结论/审查者/证据） |
| `/exemptions` | POST | 记一笔豁免（必填 reason） |
| `/production` | POST | 记一笔生产活动（比例分母） |
| `/entries` | GET | 按 ISO 周/类型/种类查账 |
| `/weekly` | GET | 周聚合报告（JSON） |
| `/verify` | GET | 链校验（只增不改证据） |

后台周聚合任务：`review_budget.weekly_aggregate`（Celery，周期 604800 s），
把周报落 `docs/governance/review_budget_weekly_<ISO周>.md`，摘要落 `artifacts/review_budget/`。
聚合逻辑在 `backend/services/review_budget_weekly.py`，不依赖 Celery 可直接复算。

## 3. 实测（真实回填窗口 2026-10-31 → 2026-11-07，周 2026-W45）

| 类型 | 审查数 | 豁免数 | 耗时 s | 可观测耗时 s |
| --- | --- | --- | --- | --- |
| execution | 135 | 0 | 0.0 | 0.0 |
| technical | 37 | 0 | 0.0 | 0.0 |
| outcome | 200 | 0 | 577.3 | 577.3 |
| innovation | 0 | 0 | 0.0 | 0.0 |

- 治理事件 372（审查 372 + 豁免 0），累计 577.3 s；
  生产事件 176，累计 281739.2 s。
- 分钟口径比例 0.002049；事件口径比例
  2.113636；耗时覆盖率 0.5376；
  `cap_status=measured`。

## 4. 局限（如实声明）

- 审批耗时含排队等待、任务墙钟含等待派发，两者都是「占用墙钟」而非纯算力，比例只能作上界参考。
- 门禁日志（pr-gate/ratchet）无耗时字段，故 Technical/Execution 两类真实事件只进事件口径；
  分钟口径覆盖率因此被压低，`cap_status` 如实标为覆盖率不足，不做达标结论。
- Innovation Review 在窗口内无真实记录，记 0（honest zero），不补造数据。
- 回填来源为平台自身记录（审批表、任务表、门禁日志），只能反映「记录的审查」，不覆盖离线/人工未入账的审查。
