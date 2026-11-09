# 复证审计报告模板

**审计编号**: {audit_id}
**生成时间**: {timestamp}
**审计范围**: {candidates_count} 候选 → {selected_count} 选中
**策略**: {strategy} (seed={seed})

---

## 1. 摘要

| 指标 | 值 |
|---|---|
| 总节点数 | {total} |
| 通过 | {pass_count} |
| 漂移 | {drift_count} |
| 失败 | {fail_count} |
| 阻塞 | {blocked_count} |
| **复现率** | **{repro_rate:.2%}** |
| 阻塞占比 | {blocked_share:.2%} |
| 耗时 | {duration_sec:.1f}s |

## 2. 覆盖度

| 维度 | 总数 | 覆盖 | 覆盖率 |
|---|---|---|---|
| 节点 | {cov_total} | {cov_selected} | {cov_rate:.2%} |
| 层（repo×年） | {cov_strata_total} | {cov_strata_covered} | {cov_strata_rate:.2%} |

## 3. 探针预检

{probe_section}

## 4. 逐项判决

{verdict_table}

## 5. 漂移归因

{drift_attribution_section}

## 6. 环境信息

- **Python**: {python_version}
- **机器**: {machine}
- **CPU**: {cpu_count}
- **工作目录**: {work_root}

## 7. 附录

### 7.1 审计计划

```json
{plan_json}
```

### 7.2 原始数据

- 检查点: {checkpoint_path}
- KPI SQL: {sql_path}

---

*本报告由 `tools.repro_audit.make_report` 自动生成，零手抄。*
*审计编号: {audit_id} | 生成时间: {timestamp}*
