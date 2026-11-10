# repro_audit —— 历史验收声明的抽样式重跑复证

**零领域语义** · **只读** · **幂等** · **可复证**

## 功能

对已完成（done）的战役节点进行抽样式重跑复证，验证其验收声明的可复现性。

### 核心模块

| 模块 | 职责 |
|---|---|
| `core.py` | 纯函数层：判据解析、数值比对、分层抽样、干净检出 |
| `scan_select.py` | 扫描候选 + 多策略选择（stratified/priority/coverage/random） |
| `probe_runner.py` | 轻量级探针（工件存在性、判据可解析性、测试语法、JSON 格式） |
| `run_audit.py` | 完整审计流水线（计划 → 探针 → 复证 → 聚合） |
| `make_report.py` | Markdown 报告生成（零手抄，程序化注入） |
| `launch.py` | 高层启动入口（一键式审计） |
| `persist.py` | KPI 持久化（API → psql → pending 工件三级降级） |
| `canonical.py` | 任务交付物规范化表示 |
| `shims.py` | 兼容性垫片（Python/pytest/git/API 版本差异） |

## 快速开始

```python
from tools.repro_audit.launch import launch_audit

result = launch_audit(
    nodes_source="artifacts/repro_audit/nodes_snapshot.json",
    n=32,
    strategy="stratified",
    output_dir="artifacts/repro_audit",
)
print(f"复现率: {result['pipeline_result']['kpi']['repro_rate']:.2%}")
print(f"报告: {result['report_path']}")
```

## 命令行用法

```bash
# 1. 快照战役节点
python3 -m tools.repro_audit.cli --snapshot-nodes artifacts/repro_audit/nodes_snapshot.json

# 2. 抽样复证（默认 N=32）
python3 -m tools.repro_audit.cli --audit --n 32 --out artifacts/repro_audit.json

# 3. 持久化 KPI
python3 -m tools.repro_audit.cli --audit --n 32 --persist
```

## 数值通道成熟度 + 入库门（p167w2-n09）

「工件里有关键数字」不等于「关键数字可重放」。这一层把**数值通道**做成可机械裁决的对象：

**通道** = 一个已提交工件里的一个关键数字（头部断言量）+ 一条在提交树内重放它的派生式。

```bash
# 审计 36 条冻结通道，写台账（artifacts/repro_audit/channel_maturity.json + channels.json）
python3 -m tools.repro_audit.generators.maturity --audit

# fail-closed 入库门（任一通道 < L3 / 覆盖 < 24/36 / 台账陈旧 → exit 1）
python3 -m tools.repro_audit.generators.maturity --gate --min-coverage 24 --min-grade L3

# 报告引用门：报告里出现的每个 artifacts/*.json 都必须有成熟通道
python3 -m tools.repro_audit.generators.maturity --gate --report validation_report.json

# 历史 gen_*.py 注册表的同口径基线（MOCK 面测量）
python3 -m tools.repro_audit.generators.maturity --baseline-legacy artifacts/repro_audit/channel_baseline_legacy.json

# 门禁效力实证（篡改一个头部数字 → 门红 → git 复原 → 门绿，留证据）
python3 -m tools.repro_audit.generators.provoke --artifact artifacts/lineage_test.json \
    --json-path total_lineage_records --delta -1
```

| 等级 | 判据 |
|---|---|
| L0 | 工件/目标路径缺失、派生报错、重放不一致 |
| L1 | 派生值 == 工件记录值（相对 1e-9 / 近零绝对 1e-12） |
| L2 | + 两次派生逐字节相等 + engine/spec/source 三指纹齐全 |
| L3 | + 无字面量回抄（派生式里没有等于被断言值的常量） + 扰动敏感性（输出非常函数） |

模块：`derive.py`（AST 白名单派生引擎）· `channel_specs.py`（冻结清单）·
`maturity.py`（评级 / 入库门 / 基线）· `provoke.py`（门禁效力实证）·
`figures.py`（配图）· `report.py`（报告程序化生成）。

报告：`docs/repro_audit/P167W2_N09_numeric_channel_maturity.md`。

## 判据口径

四类判据（与平台既有 ULP/量化协议对齐）：

- **observed**（物理/交付量）：相对容差 `OBS_REL_TOL = 1e-6`
- **diagnostic**（近零量）：绝对容差 `DIAG_ABS_TOL = 1e-12`
- **drift**：数值不在容差内但同一条重跑可复现
- **blocked**：工件缺失/依赖不可得/超时 —— 不进通过率分母

## 设计约束

1. **零领域语义**：本包不认识任何学科词/船型/仓名；仓、学科、判据全部来自调用方传入的节点 JSON。
2. **只读**：仅在临时目录里 `git archive` 出干净检出并执行，不写任何被审计仓。
3. **幂等**：输出 JSON `sort_keys=True`，抽样用 `(seed, node_id)` 的 sha256 定序。

## 测试

```bash
pytest tests/test_repro_audit.py -v
```

所有测试使用合成夹具（无领域词），符合平台铁律。
