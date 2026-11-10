# tools/maturity_ladder —— 能力成熟度五级自动升级门（p251w2-n03）

把「能力成熟度」从**人工判断**变成**机器可裁的五级证据链**：

```
L1 Implemented → L2 Tested → L3 Benchmark-verified
→ L4 Coupled prototype → L5 Engineering-qualified
```

每一级升级/降级都必须通过**机器可裁的证据**，并记录时间、证据、裁决者与回滚点。

## 五级阶梯与过渡判据

| 过渡 | 机器判据（全部合取） |
|---|---|
| L1→L2（Implemented→Tested） | 单元测试通过率 ≥ 90% + 代码覆盖率 ≥ 80% |
| L2→L3（Tested→Benchmark-verified） | 基准案例误差 < 容忍度 + 网格/时间步收敛性验证 |
| L3→L4（Benchmark-verified→Coupled prototype） | 跨求解器耦合测试通过 + 守恒量误差 < 容忍度 |
| L4→L5（Coupled prototype→Engineering-qualified） | 独立复核通过 + 实际工程案例验证 + 不确定度量化 |

**容忍度**（L2→L3 / L3→L4 的误差界）是**预声明**的（默认 15%，可经
`TransitionCriteria` 注入自定义值），不可在观测结果后调整。

## 裁定语义

* **升级**：证据满足目标级全部判据 → 升级 + 记录回滚点（升级前状态快照）。
* **拒绝（honest FAIL）**：证据不满足 → 能力**保持**当前级，拒绝原因入历史（绝不静默放行）。
* **降级**：新证据与当前级判据矛盾（如测试从绿变红）→ 显式降级 + 记录回滚点。
* **回滚**：用已记录的回滚点恢复前一级——审计链同时保留原过渡与回滚。

每次过渡记录：`timestamp`（时间）、`evidence_refs`（证据摘要）、
`adjudicator`（裁决者）、`rollback_point`（回滚点）。

## 零领域语义

本包**不硬编码任何领域词条/判据/阈值**——五级阶梯、过渡判据、证据度量
全部由 JSON 工件注入（`criteria.py` 的 DSL 是纯数据），代码只处理
「等级 → 判据 → 证据 → 裁定」的通用机器。测试夹具一律用合成域名 `synth.*`。

## 用法

```python
from tools.maturity_ladder import (
    MaturityLadderEngine, EvidenceBundle, validate_ladder_schema,
)

engine = MaturityLadderEngine()
engine.register("synth.cap")  # 默认 L1 Implemented

# L1→L2：单元测试通过率 95% + 代码覆盖率 85%
engine.upgrade("synth.cap", EvidenceBundle(
    "synth.cap", "Tested",
    {"unit_test_pass_rate": 0.95, "code_coverage": 0.85},
))

# 查看审计日志（每次过渡都记录）
for tx in engine.audit_log():
    print(f"{tx['from_level']} → {tx['to_level']} ({tx['direction']})")

# 五级 schema 验证
from tools.maturity_ladder.schema import ladder_declaration
decl = ladder_declaration(
    capabilities=[engine.get("synth.cap").as_dict()],
    transitions=engine.audit_log(),
)
assert validate_ladder_schema(decl)["valid"] is True
```

## 测试

```bash
python3 -m pytest tools/maturity_ladder/tests -q          # 36 条全绿
python3 -m pytest tools/maturity_ladder/tests --cov=tools/maturity_ladder  # 覆盖率 98%
```

## 文件结构

```
tools/maturity_ladder/
├── __init__.py          # 模块入口（公开面）
├── model.py             # 五级阶梯数据模型（等级/证据/回滚点/过渡）
├── criteria.py          # 过渡判据（数据驱动 DSL，纯数学谓词）
├── engine.py            # 升级/降级/回滚门（确定性、可审计）
├── schema.py            # JSON Schema 验证（五级声明 + 过渡记录）
└── tests/
    └── test_maturity_ladder.py   # 36 条 pytest（合成域夹具）
```

## 局限

1. 本门裁决的是「成熟度等级」，不是物理精度本身——能力真实精度取决于
   领域仓提交的基准数据质量（L2→L3 的 `benchmark_error` 由领域仓实测注入）。
2. 默认只支持**单级**过渡（L1→L2→L3→L4→L5 逐级爬）；跨级跳转需自定义
   判据注册表（`TransitionCriteria` 可定义多级跳）。
3. 降级是**显式**操作（需给出理由），不自动触发——避免「一次误标放大成
   回滚风暴」；矛盾证据留痕交审计处置。
