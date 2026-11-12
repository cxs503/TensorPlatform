# 任务干货包协议 v1（context-package/v1）

> owner 2026-11-12 策展令：大模型=一次性输入输出，上下文=当次工作台面；爆炸根源是**无用/重复啰嗦**
> 占篇幅，不是信息量本身。系统必须**主动组织干货输入**，留/去要策划不要靠感觉。

## 1. 一句话

按**任务类**声明"必带哪几槽信息"，每槽从**权威源**取候选，经**密度门**（每条注入必须回答
"它改变本任务的哪个决策"）与**token 预算**裁剪后，渲染成一段带决策键与详情指针的注入块，
落审计账本（注入了什么 / 命中了什么）。**不另开注入通道**——它就是首条 user 消息里
`_seat_memory()`、`_skill_block()` 旁边的第三段。

## 2. 任务类与必带信息清单（`config/context_package.v1.json`）

| 任务类 | 触发（配置驱动） | 必带槽 | 默认预算 |
|---|---|---|---|
| `factory_step_01..05` | 工序01…05 / factory-step-0N | 按工序侧重（01 来源·02 方法·03 证据·04 判档·05 交付） | 1100–1300 |
| `case_run`（算例单） | 算例 / 案例 / benchmark | anchor_status · tolerance_criteria · negative_knowledge · ko_index | 1500 |
| `dev`（开发单） | 开发 / 实现 / 修复 / feat- | ko_index · negative_knowledge · domain_verdict · tolerance_criteria | 1400 |
| `audit`（审计单） | 审计 / 交差 / 复核 / 盘点 | domain_verdict · negative_knowledge · ko_index · anchor_status | 1300 |
| `generic` | 兜底 | ko_index · negative_knowledge | 900 |

**五个信息槽 = 五类来源，全部既有资产，零新建知识库**：

| 槽 | 源（既有资产） | 决策键 |
|---|---|---|
| `ko_index` | `knowledge_base`（m15722 的 KO 总线表） | `reuse` / `avoid`（按 ktype 映射） |
| `negative_knowledge` | `negative_knowledge_ledger` | `avoid` |
| `domain_verdict` | `tensor_maturity_verdicts` | `gate` / `scope` |
| `tolerance_criteria` | `data/criteria/threshold_registry.json`（只取 `decidable≠false`） | `tolerance` |
| `anchor_status` | `tensor_benchmark_cases` | `anchor` |

## 3. 组装规则（`tools/context_package/assembler.py`）

1. **判定任务类**：只按配置触发词，命中数 > 触发词长度 > 配置序；无命中 → `generic`（留痕）。
2. **取候选**（只读、按 `as_of` 时间窗取，支持时间回放防未来信息泄漏）。
3. **相关性** = 目标文本词元与候选词元的重合饱和值 `inter/(inter+4)`；同 repo/domain 加 0.25。
4. **密度门（硬门）**：无 `decision` 键 / 键不在 `decisions` 词表 / 不在该槽允许决策集 → **丢弃**，
   记 `NO_DECISION` / `UNKNOWN_DECISION`。
5. **去重**：4-gram shingle Jaccard ≥ `dedupe_jaccard`(0.6) → 丢 `DUPLICATE`（与
   `config/negative_knowledge.json` 的 shingle 口径同源，不另立标准）。
6. **预算**：槽预算 = 类预算 × 槽 share 归一；单条 ≤ `per_entry_max_tokens`(110)，
   单槽 ≤ `per_slot_max_items`(4)；**包体随任务规模缩放**：
   `预算 = min(类预算, max(300, 0.40 × 目标文本token))`（防小单被包体淹没，A/B 实测把
   包体/目标文本比从 1.27 压到 0.61，中位 0.386）。
7. **审计**：`audit.candidate_pool / injected_items / decision_bearing / density_yield /
   dropped_by_reason / degraded_sources` 全量落包。

## 4. 渲染（`render.py`）

索引密度：一行一条，格式 `- [id](decision) 一句话 ↳详情指针`；详情**不入包**，按指针回原表取。
块尾必带自检行：`→ 密度自检：注入 N 条（带决策键 N）/ 候选池 M · 丢弃 原因×数 · 利用率 x`。

## 5. 审计与命中归因（`ledger.py`）

- **JSONL**（默认）：`runs/context_injections/<date>.jsonl`，含 `package_sha256`、条目 id/决策/相关度、
  渲染块 sha、类判定证据。
- **总线表**（`TENSOR_CONTEXT_DB_SINK=1`）：写 m15722 的 `knowledge_injections`
  （`mission_id/kb_id/consumer/package_sha256/match_score/injected_at`），**复用总线，不另开通道**。
- **命中**：`attribute(entry, artifact_text)` 比对交付文本（id 直击或 ≥2 个专有词元），
  写 `<date>.hits.jsonl`，返回 `hit_rate`。

## 6. 与 tp_worker 注入面整合（`hook.py`）

```python
from tools.context_package.hook import inject as ctxpkg_inject
_ctx_block, _ctx_info = ctxpkg_inject(mission.get("goal",""), mission.get("repo",""),
                                      mission_id=mission.get("id"), consumer=AGENT_ID)
content = _seat_memory() + _skill_block(goal) + _ctx_block + f"任务目标:\n{goal}\n..."
```
模式 `CONTEXT_PACKAGE_MODE=off|report|enforce`；任何异常 **fail-open** 返回空块并记 `degraded`。
三查/全貌（PANORAMA、bootstrap）仍由现注入面负责，干货包只补"本单专属的干货"。

## 7. 工作包契约新增字段

派单契约可声明（`tools/dispatch_contract` 已接线校验）：

```json
{"context_package": {"task_class": "dev", "slots": ["ko_index","negative_knowledge"],
                     "budget_tokens": 1200, "modes": ["report"]}}
```
校验：槽名必须字符串、预算必须正数、模式必须列表；校验器 fail-open（声明缺失/损坏不影响派单）。

## 8. 可验证性

- `python -m pytest tests/test_context_package.py -q`
- `python -m tools.context_package.ab_experiment --n 48`（离线 A/B，落 `runs/ab_ctxpkg_2026-11-12/`）
- 唤醒判据：`density_yield == 1.0`（A/B 实测 82/82 单一票通过）。
