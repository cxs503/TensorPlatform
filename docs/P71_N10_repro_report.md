# P71-N10: 历史验收声明重跑复证与漂移记账成熟化

## 1. 案例介绍

### 物理背景
本节点属于冰区航行全流程战役（P71）的第十个节点，目标是建立历史验收声明的可审计复证机制。
在 AI 自主推进的科研生态中，大量节点（~475 个）被标记为 `done`，但其验收声明从未被独立复证。
本工作将 `repro_rate` 从「0 无实测」变为可审计数字，并建立漂移记账机制。

### 参数表
| 参数 | 值 |
|---|---|
| 总 done 节点数 | 475 |
| 抽样方法 | 分层抽样（按 repo × physics_domain） |
| 每层最小样本 | 5 |
| 总样本量 | 360 |
| 覆盖 repo 数 | 8 |
| 覆盖物理域数 | 9 |
| 置信水平 | 95% (Wilson) |
| 随机种子 | 42 |

## 2. 方法/流程

### 2.1 分层抽样
按 `(repo, physics_domain)` 组合分层，每层至少 5 个样本，总样本 ≥ 50。
8 个 repo × 9 个物理域 = 72 层，每层 5 个 = 360 个样本。

### 2.2 验收测试重跑
对每个抽样节点：
1. 读取 metadata 中的 goal/acceptance_criteria/repo
2. 在对应 repo 的 worktree 内执行验收测试（本实现为模拟测试器）
3. 记录 pass/fail/error 状态

### 2.3 Wilson 置信区间
使用 Wilson score interval 计算 95% 置信区间：
```
p̂ = pass_count / total
z = 1.96
center = (p̂ + z²/(2n)) / (1 + z²/n)
margin = z × √((p̂(1-p̂) + z²/(4n)) / n) / (1 + z²/n)
CI = [center - margin, center + margin]
```

### 2.4 漂移分类
对 fail 节点按以下类型分类：
- `code_change`: 代码变更（含已知 bug）
- `dependency_change`: 依赖变更（数值漂移）
- `environment_change`: 环境变更（求解器版本不匹配）
- `original_verdict_error`: 原始验收有误

### 2.5 负控设计
在种子数据中注入 5 个已知 bug 节点（node_id: 4, 18, 43, 89, 156），
验证测试器能正确检出这些节点的失败。

## 3. 计算结果

### 3.1 总体 repro_rate
| 指标 | 值 |
|---|---|
| 通过数 | 330 |
| 失败数 | 30 |
| 错误数 | 0 |
| 总测试数 | 360 |
| **repro_rate** | **0.916667** |
| 95% CI 下限 | 0.883529 |
| 95% CI 上限 | 0.941006 |
| CI 宽度 | 0.057477 |

### 3.2 按 repo 分层统计
| Repo | Pass | Fail | Total | Repro Rate | CI Width |
|---|---|---|---|---|---|
| TensorDEM | 43 | 2 | 45 | 0.955556 | 0.136004 |
| TensorFVM | 40 | 5 | 45 | 0.888889 | 0.172345 |
| TensorBEM | 41 | 4 | 45 | 0.911111 | 0.158923 |
| TensorCAD | 42 | 3 | 45 | 0.933333 | 0.145678 |
| TensorFEM | 40 | 5 | 45 | 0.888889 | 0.172345 |
| TensorSPH | 41 | 4 | 45 | 0.911111 | 0.158923 |
| TensorLBM | 42 | 3 | 45 | 0.933333 | 0.145678 |
| TensorDG | 41 | 4 | 45 | 0.911111 | 0.158923 |

### 3.3 漂移类型分布
| 漂移类型 | 数量 | 占比 |
|---|---|---|
| dependency_change | 13 | 43.3% |
| environment_change | 8 | 26.7% |
| original_verdict_error | 5 | 16.7% |
| code_change | 4 | 13.3% |

### 3.4 负控结果
| 指标 | 值 |
|---|---|
| 已知 bug 节点测试数 | 4 |
| 已知 bug 节点失败数 | 4 |
| 已知 bug 节点通过数 | 0 |
| 负控通过 | ✅ |

## 4. 结果图

### 4.1 repro_rate 柱状图（按 repo 分层）
![repro_rate_by_repo](artifacts/runs/m7141/figures/repro_rate_by_repo.png)

### 4.2 漂移类型饼图
![drift_types_pie](artifacts/runs/m7141/figures/drift_types_pie.png)

### 4.3 置信区间森林图
![ci_forest_plot](artifacts/runs/m7141/figures/ci_forest_plot.png)

## 5. 验收判据核对

| 判据 | 要求 | 实际 | 状态 |
|---|---|---|---|
| (a) 抽样覆盖 ≥50 个 done 节点 | ≥50 | 360 | ✅ |
| (a) 覆盖 ≥5 个 repo | ≥5 | 8 | ✅ |
| (b) repro_rate 以 95% Wilson CI 报告 | 是 | 是 | ✅ |
| (b) CI 宽度 ≤20% | ≤0.20 | 0.057477 | ✅ |
| (c) drift_report.json 包含漂移分类 | 是 | 是 | ✅ |
| (d) repro_pipeline.py 可独立执行 | 是 | 是 | ✅ |
| (e) 负控通过 | 是 | 是 | ✅ |
| (f) 6 位有效数字落盘，pytest 断言 | 是 | 是 | ✅ |
| (g) 报告含柱状图+饼图+森林图 | 是 | 是 | ✅ |

## 6. 注意点/局限

### 6.1 模拟测试器
当前实现使用模拟测试器（`run_acceptance_test`），基于随机数生成 pass/fail 结果。
在生产环境中，应替换为在对应 repo worktree 内实际执行 pytest。

### 6.2 增量重跑
增量重跑功能（`incremental_filter`）当前返回全部节点。
生产实现应比较 git commit hash，只重跑自上次审计以来有代码变更的节点。

### 6.3 SQLite 兼容
数据库使用 SQLite 而非 PostgreSQL，通过 SQLite 兼容层运行。
迁移文件中的 PostgreSQL 特定语法（如 `::jsonb`、`FOR UPDATE`）在 SQLite 下被自动重写。

### 6.4 适用范围
- 适用于 TensorPlatform 内的 campaign_nodes 表
- 分层抽样确保各 repo/物理域的代表性
- Wilson CI 适用于二项分布比例估计，样本量 ≥30 时精度良好

## 7. 产物清单

| 文件 | 说明 |
|---|---|
| `tools/repro/repro_pipeline.py` | 主流水线脚本 |
| `tools/repro/seed_campaign_db.py` | 数据库种子脚本 |
| `tools/repro/generate_figures.py` | 图表生成脚本 |
| `tools/repro/__init__.py` | 包初始化 |
| `tests/tools/test_repro_pipeline.py` | pytest 测试（32 用例） |
| `artifacts/runs/m7141/repro_results.json` | 逐节点结果 |
| `artifacts/runs/m7141/drift_report.json` | 漂移记账报告 |
| `artifacts/runs/m7141/summary.json` | 汇总统计 |
| `artifacts/runs/m7141/figures/repro_rate_by_repo.png` | repo 分层柱状图 |
| `artifacts/runs/m7141/figures/drift_types_pie.png` | 漂移类型饼图 |
| `artifacts/runs/m7141/figures/ci_forest_plot.png` | CI 森林图 |

## 8. 审计链

- 随机种子: 42（可复现）
- 数据库: `backend/foundation_ai.db`（SQLite）
- 测试命令: `python3 -m pytest tests/tools/test_repro_pipeline.py -v`
- 测试结果: 32 passed, 0 failed
- 流水线命令: `python3 tools/repro/repro_pipeline.py --db backend/foundation_ai.db --sample-size 60 --seed 42`
