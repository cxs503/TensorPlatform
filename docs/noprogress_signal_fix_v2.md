# no_progress 早停信号修正 v2（r15 M-R15-02）

## 1. 问题描述

r15 分析发现 96 例 `no_progress` 失败单的 `mean(tool_calls − cap) = −29.2`，即任务在预算上限前约 29 次调用就被提前终止。根因是平台把「集群等待/长计算」误判为「无进展」。

## 2. 改进方案

新增 `tools/progress_signal_classifier.py` 模块，实现四分类进展信号分类器：

| 分类 | 含义 | 是否早停 |
|------|------|----------|
| `true_stall` | 真无进展（无工件变化、无在飞作业、composite≈0） | ✅ 是 |
| `cluster_waiting` | 集群等待中（有在飞作业或已知队列位置） | ❌ 否 |
| `long_computing` | 长计算中（在飞作业 + 写盘间隔 < 1h） | ❌ 否 |
| `long_write_interval` | 写盘间隔长（composite>0 但无工件变化） | ❌ 否 |

### 决策树

```
hard_no_progress=False → observe（有进展）
consecutive_cycles < 3 → observe（证据不足）
in_flight_jobs 非空 → cluster_waiting / long_computing / long_write_interval
queue_position 已知 → cluster_waiting
composite_score > 0 → long_write_interval
tool_activity=True + wall_delta 大 → cluster_waiting
否则 → true_stall（早停）
```

### 设计原则

- **零领域语义**：只观察通用信号（工件哈希、在飞作业 ID、墙钟增量、composite 分数）
- **歧义时开放**：信号不足时默认 `observe`（不早停），而非 `true_stall`
- **可组合**：分类器是 `SignalContext` 数据类的纯函数

## 3. 测试覆盖

`tests/test_noprogress_signal.py` 含 19 个测试用例：

- **场景 A（写盘-等待-写盘）**：4 个测试，验证在飞作业期间不误判
- **场景 B（纯空转）**：4 个测试，验证真无进展时正确早停
- **场景 C（长计算）**：4 个测试，验证在飞作业/队列位置/composite 信号
- **边界情况**：4 个测试
- **历史复核**：2 个测试（10 例合成案例，误判率 0/7 = 0%）
- **端到端周期**：1 个测试

## 4. 历史复核结果

| 指标 | 值 |
|------|-----|
| 复核总数 | 10 |
| 真无进展案例 | 3 |
| 非无进展案例 | 7 |
| 误判数（分子） | 0 |
| 误判率 | 0.0% |

详细报告见 `artifacts/noprogress_signal_review.json`。

## 5. 文件变更

| 文件 | 变更类型 |
|------|----------|
| `tools/progress_signal_classifier.py` | 新增 |
| `tests/test_noprogress_signal.py` | 新增 |
| `artifacts/noprogress_signal_review.json` | 新增（测试产物） |
| `docs/noprogress_signal_fix_v2.md` | 新增 |

## 6. 验收判据

- ✅ `pytest tests/test_noprogress_signal.py` 全绿（19/19）
- ✅ `pytest tests/test_progress_metrics_p148w2_n08.py` 全绿（73/73，无回归）
- ✅ 误判率报告含明确分子分母（0/7）
- ✅ git commit 至少 1 个
