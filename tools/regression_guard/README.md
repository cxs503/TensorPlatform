# tools/regression_guard —— 跨任务回归守卫

代码变更 → 依赖解析 → 精准重跑 → 基线比对 → 结构化报告。节点 `p380w2-n01-regression-guard`。

```bash
# 单次使用（CI / pre-push 最常用）
python3 -m tools.regression_guard.cli from-git \
  --ledger config/regression_guard/task_ledger.json \
  --rev-range origin/main..HEAD \
  --report artifacts/regression_guard/report.json \
  --cache-dir .cache/regression_guard
# 退出码：0 干净或无依赖；1 回归/新失败；2 守卫无法完成（含“有选择器没跑成”）

# 只看影响面（不跑测试）
python3 -m tools.regression_guard.cli plan --ledger L.json --changes C.json
```

模块：

| 文件 | 职责 |
|---|---|
| `model.py` | 变更清单 / 台账 / 报告的数据模型；判定与退出码契约 |
| `scanner.py` | AST 静态 import 图（suffix 解析、逐段通配路径匹配） |
| `impact.py` | 变更→artifact→feature(闭包)→task→test，每条选择带 reason trail；import 边第二实现复核 |
| `cache.py` | 内容寻址结果缓存（选择器 + 依赖闭包 hash），命中即不启动子进程 |
| `runner.py` | pytest 子进程执行（按测试根分批）、JUnit XML 解析、`FakeRunner` |
| `guard.py` | 编排 + 基线差分 + 报告 digest |
| `scenarios.py` | 检出率 / 误触发率度量（对照期望集合与负控矩阵） |
| `cli.py` | `scan` / `plan` / `run` / `from-git` |
| `demo_real_repo.py` | 真实仓 7 个演示 → `artifacts/regression_guard/criteria.json` |
| `make_figures.py` / `make_doc.py` / `post_report.py` | 图 / 文档 / 平台报告（全部从工件渲染，零手抄） |

铁律：本包与 `tests/regression_guard/` **不含任何领域词条**——域能力经 `feature_ledger` 行或
`config/regression_guard/task_ledger.json` 注入。设计与坑见 `docs/REGRESSION_GUARD.md`。