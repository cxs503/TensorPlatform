# Harness v3 门禁证据（程序化生成，数字零手抄）

- 生成时间: 2026-11-01T01:59:11+0000
- 总判定: **PASS**（场景 6/6 通过）
- 门禁可红性: 已验证（去掉 journal 后判据变红）
- 运行时环境: python 3.11.6

| 场景 | 判定 | 失败判据 | 关键观测 |
|---|---|---|---|
| `A_crash_resume` | PASS | — | {"kills": 1, "attempts": 2, "killed_at_progress": [0.5], "resumed": ["s1", "s2", "s3"], "resumed_step_count": 3, "done": 6, "status": "steps_done", "journal_valid_records": 9, "calls_used": 6} |
| `B_control_no_journal` | PASS | — | {"kills": 1, "attempts": 2, "resumed_step_count": 0, "calls_used": 6, "done": 6, "status": "steps_done"} |
| `C1_corrupt_total` | PASS | — | {"degraded": true, "reason": "journal_corrupt", "resumed_step_count": 0, "corrupt_records": 18, "done": 6} |
| `C2_corrupt_partial` | PASS | — | {"corrupt_before": 2, "valid_before": 4, "resumed": ["s1", "s2", "s3"], "degraded": true, "corrupt_after": 2, "done": 6} |
| `D_budget_cap` | PASS | — | {"kills": 1, "attempts": 2, "resumed_step_count": 3, "calls_used": 4, "budget": 4, "status": "budget_exhausted", "done": 4} |
| `E_red_capability` | PASS | — | {"resumed_step_count": 0, "gate_verdict_if_evaluated": "RED", "journal_on": false} |

## 判据明细

### A_crash_resume — PASS
- [x] `kill9_fired`
- [x] `killed_at_ge_one_third`
- [x] `supervisor_relaunched`
- [x] `resumed_at_least_one_step`
- [x] `all_steps_done`
- [x] `journal_clean`
- [x] `resume_brief_injected`

### B_control_no_journal — PASS
- [x] `kill9_fired`
- [x] `no_resume_possible`
- [x] `restarted_from_zero`
- [x] `rebuilt_all_evidence`

### C1_corrupt_total — PASS
- [x] `degraded_flagged`
- [x] `no_fabricated_resume`
- [x] `degradation_recorded_in_brief`
- [x] `work_still_completed`

### C2_corrupt_partial — PASS
- [x] `corruption_detected`
- [x] `valid_prefix_survives`
- [x] `degraded_flagged`
- [x] `resume_uses_only_verified`
- [x] `work_still_completed`
- [x] `degradation_in_brief`

### D_budget_cap — PASS
- [x] `killed_and_resumed`
- [x] `never_exceeds_cap`
- [x] `stopped_honestly`
- [x] `no_budget_extension`
- [x] `no_third_attempt_after_cap`

### E_red_capability — PASS
- [x] `gate_turns_red_without_journal`

## 与 m1121 事故根因对照

| 事故链环节 | m1121 事故表现 | Harness v3 对应控制 | 证据 |
|---|---|---|---|
| 进度只在内存 | 15 分钟工作随 stop 清空 | 每检查点 append-only journal | 场景 A：续跑 3 步 |
| 重领后从零 | 重读代码、重复探索 | claim 注入续跑简报 | 场景 A `resume_brief_injected` |
| 无日志时无法判断进度 | 只能靠模型自述 | 无日志=显式降级记录，不猜测 | 场景 B/C1 |
| 日志不完整时编造进度 | 无机制（易吹牛） | 逐步证据复核，缺证据即重做 | 场景 C1/C2 |
| 续跑变成无限预算 | 无（此前不续跑） | calls_used 跨续跑累计、用尽即停 | 场景 D |

