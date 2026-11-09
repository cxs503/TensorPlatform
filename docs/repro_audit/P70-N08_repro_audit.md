# 复证审计报告 P70-N08

**审计编号**: P70-N08
**生成时间**: 2026-11-09 08:00:00 UTC
**审计范围**: 280 候选 → 28 选中
**策略**: stratified (seed=repro-audit)

---

## 1. 摘要

| 指标 | 值 |
|---|---|
| 总节点数 | 28 |
| 通过 | 0 |
| 漂移 | 0 |
| 失败 | 0 |
| 阻塞 | 28 |
| **复现率** | **N/A (全阻塞)** |
| 阻塞占比 | 100.00% |
| 耗时 | 245.3s |

**关键发现**：28/28 节点全部阻塞（21 timeout, 7 依赖不可得）。repro_rate 未定义（分母为零），如实记账不落库。

## 2. 覆盖度

| 维度 | 总数 | 覆盖 | 覆盖率 |
|---|---|---|---|
| 节点 | 280 | 28 | 10.00% |
| 层（repo×年） | 45 | 11 | 24.44% |

**覆盖度分析**：分层抽样保证了仓覆盖（11/45 层），但受限于 per_stratum_cap=3，小仓未被充分代表。

## 3. 探针预检

| mission_id | 通过 | 失败数 | 错误 |
|---|---|---|---|
| m7309 | ✓ | 0 | |
| m7315 | ✓ | 0 | |
| m7407 | ✗ | 2 | workdir_not_found |
| ... | ... | ... | ... |

**探针通过率**: 21/28 (75.00%)

主要失败原因：
- 7 个节点工作目录不存在（已清理或未完成）
- 3 个节点测试文件语法错误
- 2 个节点 JSON 工件格式损坏

## 4. 逐项判决

| mission_id | 判决 | 仓 | 检查数 | 通过数 | 漂移数 | 归因 |
|---|---|---|---|---|---|---|
| m7309 | blocked | TensorLBM | 0 | 0 | 0 | timeout |
| m7315 | blocked | TensorFEM | 0 | 0 | 0 | timeout |
| m7407 | blocked | TensorPlatform | 0 | 0 | 0 | dependency_missing |
| ... | ... | ... | ... | ... | ... | ... |

**阻塞原因分布**：
- timeout (21): 单节点重跑超过 10min 预算
- dependency_missing (5): ModuleNotFoundError
- collection_error (2): pytest 收集阶段失败

## 5. 漂移归因

*本波次无漂移项（全阻塞），归因分析不适用。*

**四类归因标签**（供后续波次参考）：
- `source_change`: 源码变更导致的数值差异
- `dependency_version`: 依赖版本差异（如 numpy 1.24 → 2.1）
- `float_path`: 浮点路径差异（如 CPU 架构、编译器优化）
- `environment`: 环境差异（如 CPU 核数、内存）

## 6. 环境信息

- **Python**: 3.11
- **机器**: loongarch64
- **CPU**: 128
- **工作目录**: /root/tp-agent/work

**架构说明**：本审计在 loong64 架构执行，与 GitHub CI (x86_64) 不同。浮点路径差异可能导致数值不完全一致，但不影响阻塞判定。

## 7. 附录

### 7.1 审计计划

```json
{
  "candidates_count": 280,
  "selected_count": 28,
  "strategy": "stratified",
  "seed": "repro-audit",
  "coverage": {
    "total": 280,
    "selected": 28,
    "coverage_rate": 0.1,
    "strata_total": 45,
    "strata_covered": 11,
    "strata_coverage": 0.2444
  }
}
```

### 7.2 原始数据

- 检查点: artifacts/repro_audit/audit_run1.json
- KPI SQL: artifacts/repro_audit/kpi_upsert_run1.sql
- 节点快照: artifacts/repro_audit/nodes_snapshot.json

### 7.3 实测记录

**N=28, 11 仓**：28/28 blocked（21 timeout, 7 依赖不可得），repro_rate 未定义未落库，如实记账。

**阻塞根因分析**：
1. **timeout (21/28)**：多数节点的测试套件包含集成测试/参数扫描，单节点重跑成本超过 10min 预算。建议：
   - 缩小测试范围（只跑单元/验证测试）
   - 增加 timeout 预算（需战役资源审批）
   - 标记"重计算"节点，走集群执行

2. **dependency_missing (5/28)**：部分节点依赖特定版本的第三方库（如 taichi==1.6.0），当前环境不满足。建议：
   - 建立依赖版本快照（requirements.lock）
   - 在干净检出里安装锁定版本

3. **collection_error (2/28)**：pytest 收集阶段失败（conftest.py 导入错误）。建议：
   - 检查 PYTHONPATH 设置
   - 确保 conftest.py 无副作用

## 8. 结论与建议

### 8.1 结论

本波次复证审计**未能计算 repro_rate**（全阻塞），但完成了以下目标：
- ✓ 建立了审计工具链（scan_select → probe_runner → run_audit → make_report）
- ✓ 验证了分层抽样策略的仓覆盖能力
- ✓ 识别了阻塞的主要根因（timeout/dependency/collection）
- ✓ 如实记账，未伪造通过率

### 8.2 建议

1. **短期**：针对 timeout 节点，缩小测试范围或增加预算
2. **中期**：建立依赖版本快照机制
3. **长期**：将重计算节点迁移到集群执行（run_cluster）

### 8.3 下一步

- 修复 7 个工作目录缺失的节点（重新执行或标记为不可复证）
- 对 21 个 timeout 节点进行二次重跑（缩小测试范围）
- 下一波次目标：repro_rate ≥ 50%（排除阻塞后）

---

*本报告由 `tools.repro_audit.make_report` 自动生成，零手抄。*
*审计编号: P70-N08 | 生成时间: 2026-11-09 08:00:00 UTC*
