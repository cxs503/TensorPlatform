# tools/capability_audit_gate — 能力台账自动审计 + 回归防护

把「能力是 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED」变成 **CI 每次 PR 复算**的机器判定，
并把等级下降（尤其 REAL→MOCK 这类正面反证）变成 CI 红灯。**代码零领域语义**：能力清单、
证据绑定、基准口径全在 `config/capability_audit_gate/`（数据），本目录只放机制。

```
spec.json / curated.json ──► resolve（绑定优先/词元兜底）──► scan（AST 信号 + 基准数值）
                                                              │
                                                              ▼
                                          rules（五级级联，cag-rules-v2）
                                                     │              │
                                      report（JSON+MD）        gate（基线对比/ack）
```

| 文件 | 作用 |
|---|---|
| `taxonomy.py` | 五级偏序 + 变化分类（STABLE/UPGRADE/DOWNGRADE/**REFUTATION**） |
| `spec.py` | spec 装载/校验 + 阈值默认值 + 注册表转换 |
| `resolve.py` | 证据路径解析：显式绑定优先，路径**词元**匹配兜底，未绑定 ⇒ WITHHELD |
| `scan.py` | 实现/测试 AST 信号，基准工件数值（json_cases / json_values / json_verdict） |
| `cache.py` | 内容 sha256 增量缓存（键含 namespace，防 impl/tests 信号串味） |
| `rules.py` | 判定级联 + 理由码 + 证据链（阈值先声明，规则带版本） |
| `gate.py` | 基线冻结 / 降级检出 / ack / 规则集版本门 |
| `report.py` | JSON + Markdown（逐能力证据链、一致率与多数类基线、回归结论、局限） |
| `engine.py` | 编排 + 覆盖率/一致率/证据链/耗时 + 验收判据（C1–C5） |
| `drill.py` | 回归演练：逐个把 REAL 能力注入造假实现，要求 gate 100% 检出（C3） |
| `cli.py` | `audit` / `freeze` / `validate` / `drill`；退出码 0/1/3/4 |

快速上手见 `docs/CAPABILITY_AUDIT_GATE.md`（含预声明阈值、判例坑、局限）。

```bash
python3 -m pytest -q tests/capability_audit_gate                    # 64 用例
python3 -m tools.capability_audit_gate.cli drill \
  --spec config/capability_audit_gate/golden/spec.json \
  --root config/capability_audit_gate/golden --out /tmp/drill        # C3 演练
python3 -m tools.capability_audit_gate.cli audit \
  --spec config/capability_audit_gate/spec.json --root . \
  --out artifacts/capability_audit_gate \
  --baseline config/capability_audit_gate/baseline.json \
  --contrast artifacts/capability_ledger/capability_ledger_resolved.json \
  --fail-on-gate --enforce-acceptance
```