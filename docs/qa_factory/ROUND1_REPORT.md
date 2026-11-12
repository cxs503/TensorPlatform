# QA 产线第 1 轮报告（FEAT-QA-FACTORY / m15877）

> 任务：QA 产线化设计与试点。owner 2026-11-12 并行机制令：当前最大串行段=验证/质检
> （实测 QA 10033 pending / 28 passed，生产并行度 ~100 vs 验证并行度 ~1 = Amdahl 病）。
> 判据预注册于 `docs/qa_factory/PREREG_QA_FACTORY.md`（判据先于动手；阈值不事后调）。

## 1. 交付面（做了什么）

| # | 交付物 | 路径 | 状态 |
|---|---|---|---|
| ① | 程序化三查机器门（结构 / 证据指针 / 关键数字） | `tools/qa_factory/gate.py` + 复用 `tools/report_corpus_audit.py` 六要素判定 | ✅ 全量跑通 |
| ② | 分层抽样 + 独立深检（工序 04 硬门：reviewer ≠ producer） | `tools/qa_factory/consistency.py`、`tools/qa_factory/dispatch.py`、`docs/qa_factory/deep_review_round1.jsonl` | ✅ 抽样+深检裁决；派单器 dry-run 就绪 |
| ③ | 口径一致性验证（机器判 vs 深检判，≥0.90 才投用） | `artifacts/qa_factory/round1/consistency.json/.md` | ✅ **0.9667 ≥ 0.90 → 机器门投用** |
| ④ | 还债调度（machine-pass→passed 带 machine_audit 标记；存量优先老报告） | `tools/qa_factory/source.py`（clear_machine_pass）+ `backend/migrations/122_report_qa_machine.sql` | ✅ 清账 1731 篇 |
| ⑤ | QA 产线 daemon（flock 单实例 + 心跳 + autoheal 可纳管） | `tools/qa_factory/daemon.py`、`ops/qa_factory.md` | ✅ 试运行通过 |

字段：`qa_machine_tier`（pass/machine-flag/suspect）、`qa_machine_audit`（逐篇三查证据 JSON）、
`qa_machine_at`、`qa_audit_source`（`machine` = 机器门授权清账，NULL/human = 人工深检路径）。

## 2. 全量扫描结果（O(批)，不占 Agent 席位）

语料：`tensor_reports` where `qa_status='pending'`，10042 篇（开工时 10033 篇，扫描期间新增 9 篇）。

| 档 | 篇数 | 占比 | 处置 |
|---|---|---|---|
| pass | 1730 | 17.2% | 机器授权清账（qa_status→passed，qa_audit_source=machine） |
| machine-flag | 3203 | 31.9% | 深检队列（不自动清账） |
| suspect | 5109 | 50.9% | 深检队列（优先） |

| 查 | ok | flag | fail |
|---|---|---|---|
| A 结构完整 | 6586 | 29 | 3427 |
| B 证据指针 | 5310 | 4579 | 153 |
| C 关键数字 | 3991 | 2698 | 3353 |

非 ok 主因（前 8）：

| 原因 | 篇数 | 含义 |
|---|---|---|
| `structure:corpus_audit:RESUBMIT` | 3417 | 六要素缺项（多为早期补报单：无结果数字表/无误差判定） |
| `pointers:no_checkable_pointer_root_unavailable` | 2906 | 工件根已回收 → **不可判（不是失败）** |
| `numbers:numbers_partly_bound` | 2688 | 部分关键数字有出处，部分没有 |
| `numbers:numbers_unbound` | 2503 | 大半精度数字无任何溯源绑定 |
| `pointers:no_pointer_at_all` | 1051 | 全文一个指针都没有 |
| `numbers:no_numeric_claim` | 850 | 全文无精度数字（无内容） |
| `pointers:pointer_partly_contradicted` | 622 | 宣称的工件部分不存在 |
| `pointers:pointer_mostly_contradicted` | 153 | 过半宣称工件不存在（造假嫌疑档） |

吞吐（同一作业两代实现，同机测量）：
- 单进程：1500 篇 / 95.3 s = **15.7 篇/s**
- 16 进程（`--jobs 16`）：8542 篇 / 61.8 s = **138 篇/s**（≈8.8×）
- 全量 10042 篇总耗时 ≈ 2 分钟（对比：`report_qa` 深检席 ~1 篇/分钟量级 → 串行段 ~167 小时）

## 3. 口径一致性（红队可失败）

抽样：三层各 10 篇（seed=`qa-factory-r1`，层内 sha256 排序取前 10，确定性）。
深检：本单执行者按同一预注册判据独立复核 30 篇（证据 `docs/qa_factory/deep_review_round1.jsonl`）。

| 指标 | 值 | 95% CI | 门限 | 判定 |
|---|---|---|---|---|
| G1 三分类一致率 | **0.9667** (29/30) | [0.833, 0.994] | ≥0.90 | 过 |
| G2 pass/非pass 一致率 | **0.9667** | [0.833, 0.994] | ≥0.90 | 过 |
| Cohen's κ（参考） | 0.95 | — | — | — |

| 层 | n | 一致 | 一致率 |
|---|---|---|---|
| pass | 10 | 9 | 0.90 |
| machine-flag | 10 | 10 | 1.00 |
| suspect | 10 | 10 | 1.00 |

**危险误判（机器 pass 但深检非 pass）：报告 #9977** —— 指针 1 可解析 / 22 不可判，
机器按预注册规则判 ok，深检判 machine-flag（证据面 96% 不可判时判 pass 过强）。
这是 v1 指针规则的**已知弱点**：`resolved ≥ 1 且 contradicted = 0` 可被"1 个可解析 + 大量不可判"稀释。
**建议 v2 收紧为 `resolved ≥ max(2, 0.3 × 总指针数)` 或显式声明**（本轮不改判据=不事后调参）。

## 4. 还债清账

- 清账 1731 篇（machine-pass 档，`created_at ASC` 存量优先），标记 `qa_audit_source='machine'`，
  `qa_rejection_reason='machine_audit:pass gate=consistency.json:ok'`（可追溯、可回滚：
  逐篇三查证据在 `qa_machine_audit`，清账 id 全表在 `artifacts/qa_factory/round1/clear_r1.json`）。
- 清账后库存：pending 8317（suspect 5109 + machine-flag 3203 + 新增 5）、passed 1758（machine 1730 + 人工 28）、rejected 12。
- **已知未对账 1 篇**：`clear_r1.json` 记录 1731 个 id（去重后仍 1731），库内 `passed & qa_audit_source='machine'`
  计 1730，差 1 篇未定位（疑似同秒其它写者改写该行）。已登记为下轮 daemon 的逐批对账项（见 §6 局限）。

## 5. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 本轮值 | 基准/门限 | 误差 | 是否 <3% |
|---|---|---|---|---|
| G1 三分类一致率 | 0.9667 | 预注册门 0.90 | +6.67 pp（高于门限） | 不适用（门限型判据，非物理量；以"是否过门"判：**过**） |
| 机器判 pass 档的深检一致率 | 0.90 (9/10) | 1.00（理想） | −10 pp | **否**（该层是唯一有偏差的层，见 #9977） |
| 全量分档成功率（三档占比和） | 100%（10042/10042） | 100% | 0 | 是 |
| 扫描吞吐加速比 | 138 篇/s | 15.7 篇/s（单进程基线） | +779%（8.8×） | 是 |
| 清账目标达成率 | 1731 / 2000 | 2000 篇 | −13.4% | **否（honest FAIL，见 §6）** |

物理量误差：本单为后端/QA 工具链任务，无物理场量输出，故无"物理量 vs 解析解/文献"误差项——
图件按报告纪律给出工程判据图（分档分布 + 各查状态 + 一致性分层），非场量云图，理由即此。

## 6. 注意点（坑 / 局限 / 适用范围）

1. **2000 篇清账目标未达成（honest FAIL）**：machine-pass 仅 1730 篇（+1 对账差异），达成 86.6%。
   要凑到 2000 只能（a）放宽预注册门限，或（b）把 machine-flag 也自动清账——两者都会破坏
   "机器门必须红队可失败"的铁则，故**拒绝**，如实报缺口。
2. **深检不是全文盲审**：30 篇深检基于压缩摘要（标题+首段+三查证据+抽样工件核对），
   非逐字全文；且执行者能看到机器判定（非盲）→ 存在确认偏倚，已在 PREREG §8 声明。
   规模化路径已就绪（`dispatch.py`，工序 04 硬门 reviewer≠producer），本轮未批量派发（预算）。
3. **统计功效不足**：n=30 恰在最小值，CI 半宽 ±0.13。要"认证"≥0.90 需累计 n≈140（下轮扩样）。
4. **不可判 ≠ 失败**：2906 篇指针根（worktree）已回收 → 记 `unverifiable` 且只到 machine-flag。
   这是为免冤杀老报告，代价是老报告几乎不可能进 pass 档。
5. **机器门只判纪律形态与可追溯性，不判物理正确性**：`pass` 不等于结论正确；
   物理正确性仍归深检/复算（本单不声称覆盖）。
6. **报告类型分层是必需的**：质检元报告（scenario=report-qa 等）没有物理案例节，
   套六要素会系统性误杀（实测 meta 204 篇 pass，若不分层会被判 RESUBMIT）。
7. **门版本化**：`GATE_VERSION=qa-gate/1` 写入每篇审计 JSON；判据变动必须升版本并重跑全量，
   禁止在旧分档上打补丁。

## 7. 复现

```bash
# 三查全量（增量、可并行、可限时）
python3 -m tools.qa_factory.cli scan --only-untiered --batch 6000 --jobs 16 --write-tiers
python3 -m tools.qa_factory.cli export --out artifacts/qa_factory/round1/latest.json
# 分层抽样 → 深检 → 一致性裁决
python3 -m tools.qa_factory.cli sample --scan artifacts/qa_factory/round1/latest.json --per-stratum 10
python3 -m tools.qa_factory.cli consistency --scan artifacts/qa_factory/round1/latest.json \
        --deep docs/qa_factory/deep_review_round1.jsonl --out artifacts/qa_factory/round1/consistency.json
# 清账（一致性门未过则拒绝执行，退出码 3）
python3 -m tools.qa_factory.cli clear --batch 2000
# daemon（flock + 心跳；autoheal 可纳管）
python3 -m tools.qa_factory.daemon
```

单测：`python3 -m pytest tests/qa_factory -q`（22 例，含 4 组红队用例：伪造工件、
手抄数字、结构缺项、垃圾报告——**门必须能被造假语料打穿**）。