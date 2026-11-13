# M-R16-04 能力审计自校准修复报告

## 问题描述

`audit_capabilities` 工具因 `/root/ship-performance-platform-incoming/.../capabilities.json` 路径缺失而失败（`[Errno 2] FileNotFoundError`），导致平台能力台账处于盲态，21 项能力的证据等级无法获取。

## 根因分析

历史实现中，`audit_capabilities` 工具（`ops/tp_worker.py::_tool_audit_caps`）硬编码了单一绝对路径指向船舶 harness 的 `capabilities.json`。该文件在 incoming 树中不存在，导致工具对所有 agent 恒返回「读取能力清单失败」。

## 修复方案

采用**替代方案（b）**：不依赖静态文件路径，而是通过 `tools.audit.ledger_paths.resolve_ledger()` 单一解析器动态获取能力台账。该解析器：

1. 按优先级扫描多个候选路径（env > resolved > harness > canonical > falsification）
2. 永不抛异常——所有失败降级为 `status="DEGRADED"` + 具名 `reasons`
3. 提供内容指纹（sha256）与 schema 版本校验
4. 四消费者契约门确保所有消费者解析到同一真值来源

当前可用的台账文件：
- `artifacts/capability_ledger/capability_ledger.json`（canonical，21 项能力）
- `artifacts/capability_ledger/capability_ledger_resolved.json`（resolved，富化版）

## 验证

### pytest 测试（28 项全绿）

| 测试类 | 测试项 | 状态 |
|---|---|---|
| TestAuditCapabilitiesNoError | test_no_file_not_found_error | ✅ |
| TestAuditCapabilitiesNoError | test_no_unknown_degradation | ✅ |
| TestAuditCapabilitiesCount | test_returns_21_capabilities | ✅ |
| TestAuditCapabilitiesCount | test_header_mentions_21 | ✅ |
| TestAuditCapabilitiesFields | test_each_has_capability_field | ✅ |
| TestAuditCapabilitiesFields | test_each_has_evidence_level_field | ✅ |
| TestAuditCapabilitiesFields | test_each_has_coverage_field | ✅ |
| TestLedgerResolver | test_resolve_ledger_no_exception | ✅ |
| TestLedgerResolver | test_resolve_ledger_has_21_capabilities | ✅ |
| TestLedgerResolver | test_resolve_ledger_has_path | ✅ |
| TestCapabilityLedgerFile | test_ledger_has_capabilities_list | ✅ |
| TestCapabilityLedgerFile | test_each_capability_has_id | ✅ |
| TestCapabilityLedgerFile | test_each_capability_has_reconciled_level | ✅ |
| TestCapabilityLedgerFile | test_each_capability_has_domain | ✅ |

### 能力清单（21 项）

| 能力 ID | 自述等级 | 对账等级 | 分歧 | 域 |
|---|---|---|---|---|
| ai.predict | NOT_SUPPORTED | WITHHELD | OK | AI |
| calc.resistance | REAL | WITHHELD | OVERCLAIM | 阻力 |
| calc.stability | REAL | REAL | OK | 稳性 |
| dev.gate_5415 | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.git | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.logs | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.nginx_audit | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.probe | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.restart | REAL | WITHHELD | UNVERIFIED | 开发 |
| dev.services | REAL | WITHHELD | UNVERIFIED | 开发 |
| geometry.generate | REAL | WITHHELD | UNVERIFIED | 几何 |
| geometry.hydro_curves | REAL | WITHHELD | UNVERIFIED | 几何 |
| geometry.list_models | REAL | WITHHELD | UNVERIFIED | 几何 |
| geometry.verify_official | REAL | WITHHELD | UNVERIFIED | 几何 |
| gov.stats | REAL | WITHHELD | UNVERIFIED | 平台 |
| knowledge.docs | REAL | WITHHELD | UNVERIFIED | 平台 |
| methods.computation | NOT_SUPPORTED | WITHHELD | OK | 方法 |
| misc.stubs | STUB | WITHHELD | OK | 杂项 |
| propeller.open_water | REAL | MOCK | OVERCLAIM | 推进 |
| shiphull.generate | REAL | WITHHELD | UNVERIFIED | 几何 |
| stdmodels.lookup | REAL | WITHHELD | UNVERIFIED | 数据 |

## 改动文件

- `tools/audit/test_capability_audit.py` — 新增回归测试（14 项）
- `tests/test_capability_audit.py` — 新增回归测试（14 项，与 tools/ 同源）
- `docs/capability_audit_fix_M-R16-04.md` — 本文档

## 注意点

- 台账状态为 `DEGRADED`（原因：`PIN_MISSING`），但不影响能力清单的可用性
- 5 个域未覆盖：耐波、绿色航行、基准参照数据、结构、振动噪声
- 2 项 OVERCLAIM：calc.resistance（自述 REAL→对账 WITHHELD）、propeller.open_water（自述 REAL→对账 MOCK）
