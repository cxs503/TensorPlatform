# 三层记忆压缩策略 v1（global / seat / knowledge_base）

> 压缩的留/去必须可判定、可审计、可回滚。**episodes 可恢复，lessons 才进记忆。**

## L1 全局层：索引密度，详情下沉

- 对象：`AGENT_BOOTSTRAP.md`、`PANORAMA.md`（每单都进上下文的"世界说明书"）。
- 做法：`tools/memory_compress/global_view.py`
  - 标题 ≤ `max_depth` 保留；更深的标题保留**标题+指针**（结构不丢）。
  - 每段一句要点（优先取带数字/判据的那句）+ 该段 `·数值:` 索引 + 剩余数字进 `数字附录`。
  - 详情不删除，只给 `↳ doc#heading` 指针（可检索文件仍在）。
- 判据（先声明）：`heading_retention@depth = 1.0`；`numeric_retention ≥ 0.6`（压缩可丢叙述不可丢数字）。
- 实测（`runs/memory_compress/global_view_report.json`）：
  - bootstrap：ratio 0.284，numeric_retention **1.0** → 通过。
  - PANORAMA：ratio 0.028，numeric_retention **0.24** → **未过**（数字附录上限 400 条，表格密集文档不够）。
    诚实结论：索引视图对"纯叙述"成立，对"表格/数字密集"文档需提高附录上限或分节附录。

## L2 席位个体层：留能力证据/复发失败/胜任度，丢一次性过程

- 对象：`agent_directory` 席位字段 + 该席 episode 历史（`tensor_dev_missions` / `tensor_reports`）。
- 做法：`seat_memory.compress_seat`
  - **留**：带工件路径/数字/判据的教训（可核查=有证据）；`failure_kind|fail_stage` 出现 ≥2 次
    升格为**复发性失败模式**（附 `tensor_reports#id` 证据）；按任务类的 n/通过率/一次通过率。
  - **丢**：无判据的一次性过程细节（"先读了几个文件"），每条带 `recover: tensor_reports?mission_id=…`。
- 判据：丢弃项 100% 带恢复指针；复现门槛 `min_recur=2`（一次性事故不进记忆）。

## L3 knowledge_base 层：use_count / last_used 生命周期 + 遗忘日志

- 做法：`kb_lifecycle.decay_tiers / touch / snapshot / write_forgetting_log`
  - 引用即 +1：注入 → `injected_count+1, last_hit_at`；被交付命中 → `cited_count+1, last_cited_at`。
  - 分档：hot(≤30d 有引用)=1.0 · warm(≤90d)=0.6 · stale(>90d 且零引用)=0.3 · archive(>180d 零引用)=0.0。
  - 归档 **默认 dry-run**；`--apply` 才改 status，且**先落快照**（可回滚），每动作一行遗忘日志
    `runs/memory_compress/forgetting_log.jsonl`。

## 节奏（`cadence.py`）：定期 + 超阈值双触发

| 触发 | 判据 |
|---|---|
| PERIODIC | 距上次 ≥ `interval_days`(7) |
| BOOTSTRAP | 无上次记录 |
| SEAT_MEMORY_SIZE | 席位记忆 > `seat_memory_tokens`(1200) |
| KB_STALE_RATIO | 零引用占比 > `kb_stale_ratio`(0.35) |
| GLOBAL_VIEW_SIZE | 索引视图 > `global_view_tokens`(2400) |

防抖：距上次 < `min_interval_hours`(12) 一律 `blocked_by=MIN_INTERVAL`（不重复压缩）。
状态落 `runs/memory_compress/cadence_state.json`；每次触发条件与理由进状态 extras，可审计。
