# 能力台账自动化证据链成熟化机制（p273w3-n05）

## 概述

本模块实现了能力台账自动化证据链成熟化机制，包含四个核心组件：

1. **证据采集器** (`evidence_collector.py`) — 扫描 pytest/benchmark/对标报告，提取关键量与误差
2. **裁定引擎** (`adjudication_engine.py`) — 按预声明阈值自动判定 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED
3. **升级工作流** (`upgrade_workflow.py`) — 证据达标→自动升账+审计日志+回滚点
4. **防 OVERCLAIM 门** (`overclaim_gate.py`) — 自述等级>证据等级时自动降级+告警

## 设计原则

- **零领域语义**：所有域名/能力名通过配置注入，不硬编码船舶域词汇
- **纯函数裁定**：`grade = f(evidence, thresholds)`，确定性、可复现
- **审计链完整**：每次裁定/升级/降级都有 sha256 签名的审计日志
- **可回滚**：每次状态变更保存快照，支持一致性校验回滚

## 裁定规则

| 规则 ID | 条件 | 裁定等级 |
|---------|------|----------|
| R0 | 无证据 | NOT_SUPPORTED |
| R1 | 仅接口证据 | STUB |
| R2 | MOCK 信号主导 (>50%) | MOCK |
| R3 | 证据不足 | WITHHELD |
| R4 | 误差 >15% | MOCK |
| R5 | 误差 3-15% | WITHHELD |
| R6 | 误差 ≤3% + 足够证据 | REAL |

## 验收判据

| 判据 | 要求 | 状态 |
|------|------|------|
| C1 | 21项能力自动判定vs人工审计一致率≥95% | ✅ 100% |
| C2 | propulsion MOCK案例100%自动捕获降级 | ✅ 通过 |
| C3 | 升级流程端到端<5min审计日志完整(sha256签名) | ✅ <1s |
| C4 | 回滚能力数据一致性校验通过 | ✅ 通过 |
| C5 | pytest≥20项全绿含规则边界/负控 | ✅ 54项全绿 |
| C6 | 零领域语义扫描红/绿对照 | ✅ 通过 |

## 使用示例

```python
from capability_ledger_maturity import (
    EvidenceCollector, AdjudicationEngine,
    UpgradeWorkflow, OverclaimGate,
)

# 1. 采集证据
collector = EvidenceCollector(worktree=Path("/path/to/repo"))
items = collector.collect_all()

# 2. 按能力分组
from collections import defaultdict
evidence_by_cap = defaultdict(list)
for item in items:
    evidence_by_cap[item.capability_id].append(item)

# 3. 裁定
engine = AdjudicationEngine()
results = engine.adjudicate_all(evidence_by_cap)

# 4. 防 OVERCLAIM 检查
gate = OverclaimGate(auto_downgrade=True)
alerts = gate.check_all(results)

# 5. 升级工作流
workflow = UpgradeWorkflow(current_levels={"cap.alpha": "WITHHELD"})
records = workflow.process_all(results)

# 6. 一致性校验
check = workflow.verify_consistency()
assert check["consistent"] is True
```

## 测试

```bash
cd /root/tp-agent/work/m9429
python -m pytest tests/capability_ledger_maturity/ -v
```

## 文件结构

```
tools/capability_ledger_maturity/
├── __init__.py              # 模块入口
├── evidence_collector.py    # 证据采集器
├── adjudication_engine.py   # 裁定引擎
├── upgrade_workflow.py      # 升级工作流
└── overclaim_gate.py        # 防 OVERCLAIM 门

tests/capability_ledger_maturity/
└── test_capability_ledger_maturity.py  # 54项测试
```
