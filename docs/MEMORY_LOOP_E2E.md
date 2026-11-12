# 最小记忆回路 E2E：真实流量四环时间线（m15964 · 2026-11-12）

> 锚点 = FEAT-AGENT-HARNESS 第 2 轮。所有数字由 `tools/context_package/loop_e2e.py` 从**生产库**
> 读出后程序化注入（零手抄）。原始证据：`runs/memory_loop_e2e/e2e_20261112T062405Z.json`。

## 0. 决策与判据（先于运行声明）

| 项 | 声明 |
|---|---|
| 决策 | 回路四环能否在真实流量上闭环；不通则定位断点+处方 |
| 对照基线 | m15946 只交付出包器（A/B N=82：recall 0.00→1.00），回路未闭环；本单以"环②是否真带上该条"为判别量 |
| 命中阈值 | 显式 id 出现 或 与教训正文共享词元 ≥ `min_terms=3`（事后不调闸） |
| 覆盖率口径 | 注入单=当日 `knowledge_injections` 去重 mission_id ÷ 当日 `tensor_dev_missions` 总单 |

## 1. 环① 写入（真实失败单 → 一条负面教训）

| 表 | 行 | 时刻（原表原文） | 内容 |
|---|---|---|---|
| `tensor_dev_missions` | 15291 | finished_at=2026-11-11T22:14:07+00:00 | status=failed，fail_stage=`failed_no_signal`，repo=TensorPlatform |
| `negative_knowledge_ledger` | entry_id=`negk:loop:15291` | registered_at=2026-11-12T06:23:01+00:00 | mission_id=15291（来源单可溯） |
| `knowledge_base` | id=105 kb_key=`lesson:m15291` | created_at=2026-11-12T06:23:01+00:00 | ktype=lesson，计数器归此行 |

## 2. 环② 注入（下一同类单开工 → 干货包含该条，决策键齐）

| 表 | 行 | 时刻（原表原文） | 内容 |
|---|---|---|---|
| `tensor_dev_missions` | 15343 | created_at=2026-11-11T22:28:54+00:00 | 同类单（TensorLBM，status=failed，claimed_by=tp-qw38-19） |
| `knowledge_injections` | injection_id=`MISSING` | injected_at=MISSING | kb_key=MISSING；package_sha256=be915695dfc34991aacf0aefd475e951d81cdbe59dc516792d5046a3ba91b59b |

注入块原文（节选；`[id](decision)` 即决策键）：
```
 - [negk:loop:15291](avoid) m15291 failed｜【锚点=KPI-TOOLS-5】【交差审计|任务榜】owner 2026-11-11…｜failed｜result 未提供 checks.reason（见来源单 result 原文） ↳negative_knowledge_ledger#negk:loop:15291 (mission=15291)
 - [kb:105](avoid) m15291 failed｜【锚点=KPI-TOOLS-5】【交差审计|任务榜】owner 2026-11-11 任务榜…〔lesson·TensorPlatform〕同类单（audit·TensorPlatform）先避开 failed：result 未提供 checks.reason（见来源单 resu… ↳knowledge_base#105
```
包内条目全表：`['verdict:265', 'verdict:222', 'negk:loop:15291', 'negk:gate:385', 'kb:105', 'kb:48', 'kb:103', 'kb:9', 'anchor:GAP-LBM-SPHERE', 'anchor:H2-FRAZIL-RHEOLOGY']`；handoff 落盘 `/root/tp-agent/work/m15964/runs/memory_loop_e2e/handoff/m15343.md`；DB sink=`{'inserted': 10, 'kb_touched': 4}`。

## 3. 环③ 命中（该条被引用 → 计数 +1）

| 表 | 行 | 时刻 | 证据 |
|---|---|---|---|
| `knowledge_injections` | 同上 | cited_at=MISSING | citation_count+1，citation_ref=`tensor_dev_missions#15343/pre-deploy` |
| `knowledge_base` | id=105 | last_cited_at 同上 | cited_count+1（use_count 落账） |

命中 id 集：['kb:103', 'kb:105', 'kb:48', 'negk:loop:15291']；hit_rate=0.375（阈值 min_terms=3）。

## 4. 环④ 下游影响（记录该教训用在哪一步）

全引用行：`记忆回路引用：本单注入的 kb:103, kb:48, kb:105, negk:loop:15291 在【同上（pre-deploy 回溯）】被引用（cited_at=2026-11-12T06:24:05+00:00，min_terms=3，ref=/root/tp-agent/work/m15964/runs/memory_loop_e2e/2026-11-12.citations.jsonl）`
`used_in_step`=同上（pre-deploy 回溯）；真实调用方=后端完结钩子（`summary.used_in_step`
或 `mission#<id> 完结交付(report_id/branch)`），见 `backend/api/tensor_missions.py::_memory_loop_complete`。

## 5. 回路时延与覆盖率（首日口径）

| 量 | 值 | 口径 |
|---|---|---|
| 教训产生 → 首次注入 | 30.0 s | write.created_at → 首个含该条的 injected_at |
| 首次注入 → 首次命中 | 34.0 s | injected_at → cited_at |
| 注入单 / 当日总单 | 1 / 474 = 0.0021 | 首日=接线当日；钩子未部署前只有 E2E 直调的这一单 |
| 当日教训写入数 | 1 | ktype=lesson 且 created_at::date=今日 |

## 6. 断点、处方与诚实声明

1. **已修断点 A（③④无落账）**：`ledger.attribute()` 只写 JSONL；`_sink_db` 只插 `kb:` 行且
   `citation_count=0`，NKL 条目不进总线表。修法：`sink_db` 同表纳 NKL（`kb_id=NULL`、
   `kb_key=条目 id`）+ 新插入即 `injected_count+1`；`loop.cite()` 回写
   `citation_count/cited_at/citation_ref` 与 `cited_count/last_cited_at`。
2. **已修断点 B（②空转，本单实测发现）**：新教训 `cited/injected=0`，在 KB 排序垫底；
   同类不同主题的单出包时被预算挤掉——第一次 E2E 的 `item_ids` **不含** `negk:loop:15291`，
   环② MISSING。处方=`loop.fresh_lesson_ids()` + `inject(pin_newest_hours=72)`：每包保底带
   1 条本类新鲜教训（决策键 avoid），与相关性打分解耦。修后同一 E2E 立刻命中。
3. **honest-FAIL 面（未在活流量验证）**：本 E2E 的 ②③ 是 **pre-deploy 回溯**——该同类单的真实
   提示词**不含**该块（后端 `_memory_loop_start` 钩子与 worker `_context_block()` 尚未部署到在跑
   进程，生产 API 需重启装载）。故环③命中为**文本回溯**而非消费事实，`citation_ref` 已标
   `pre-deploy`；活流量首日覆盖率与真实"教训→首次注入"时延待合并部署后由钩子自动累计。
4. 局限：环③归因是词元机械判定（同义不同词会漏）；NKL 无计数列，靠镜像
   `knowledge_base(lesson:m<id>)` 承载 use_count（同事务写）。
5. 环境：2026-11-12 UTC 06:24，DSN=`postgresql://postgres@/tensorplatform`（本机 socket），
   当日 474 张真单（succeeded 437）。

## 7. 回归守卫

- `tests/test_context_package_loop.py`：11 项（①来源单+幂等、②决策键齐+handoff、③计数回写、
  ④引用行、fail-open、worker 消费面断言）。
- `python -m tools.context_package.loop_e2e`：可重复，产出四环时间线 JSON（本文即其渲染）。
