# capability_audit_v2 —— 可执行证据面的能力台账

在 v1 静态审计（`tools/capability_audit_gate`）之上补一层**机器可执行**证据：
按配置声明的 pytest 选择器真的跑测试、按配置声明的不变量探针真的调实现，然后把
静态信号 + 执行证据按预声明级联合并成 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED。

## 用法

```bash
# 完整审计（含可复现校验、图表、验收判据）
python3 -m tools.capability_audit_v2.cli audit \
  --root . \
  --spec config/capability_audit_v2/spec.json \
  --labels artifacts/capability_ledger/capability_ledger_resolved.json \
  --paths config/capability_audit_v2/upgrade_paths.json \
  --out artifacts/capability_audit_v2 --repro-runs 2

# 只做可复现校验
python3 -m tools.capability_audit_v2.cli verify --root . \
  --spec config/capability_audit_v2/spec.json --paths config/capability_audit_v2/upgrade_paths.json --runs 2

# 机制测试（合成能力名，零领域语义）
python3 -m pytest -q tests/capability_audit_v2
```

退出码：0 全过 / 1 判据未全过 / 2 路线图数据不合格 / 4 spec 配置错误。

## 文件

| 文件 | 作用 |
|---|---|
| `spec_v2.py` | v2 spec 装载/校验（基础台账 spec + 执行证据声明，全部外部注入） |
| `exec_evidence.py` | pytest 选择器执行器 + 不变量探针执行器（确定性证据，无耗时/时间戳） |
| `cascade_v2.py` | 判定级联 `cav2-rules-v1` + 一致率/多数类基线 |
| `roadmap.py` | MOCK/STUB → REAL 路线（算法引用 + ≥3 步 + 可执行 verification，缺项即报错） |
| `ledger_v2.py` | 规范化 JSON、sha256 证据索引、复现口径 |
| `figures.py` | 等级分布 / 一致率对比 / 证据面矩阵 |
| `cli.py` | `audit` / `verify` |

## 零领域语义

代码与测试**不含**任何域词条/域判据/域算例：能力 id、选择器、探针目标、不变量表达式、
基准口径、升级路径全部来自 `config/`（或 `--spec/--paths` 指向的任意数据文件）。
回归防护：`tests/capability_audit_v2/test_zero_domain_v2.py` 用仓内扫描器把"零命中"变成红灯。

设计与结果见 `docs/CAPABILITY_AUDIT_V2.md`。