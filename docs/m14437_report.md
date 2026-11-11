# m14437: 能力台账 PIN 落盘闭环报告

## ① 案例介绍

**问题**：`/health` 长期返回 `capability_ledger.status = DEGRADED`，`reasons=["PIN_MISSING"]`。
`ledger_contract_gate.py --check` 在 HEAD 即红（C3/C4 FAIL），pin 图章从未入库。
m12601 标 succeeded 但 pin 未落地 → "success ≠ landed" 活标本。

**基线数据**（pin 落盘前）：
- `GET /health` → sha256=618d37ff38a162da385c05e84e23c04c9f761fc3f8355f4e37d5b4316b58ac89
- capabilities=21 / unverified=17 / overclaim=2
- `ledger_contract_gate --check` → verdict=FAIL, failed=['C3_no_ledger_drift','C4_consumers_healthy']

## ② 怎么做的

1. **生成 pin**：`python3 tools/audit/ledger_paths.py --write-pin` → 产出 `artifacts/capability_ledger/ledger_contract.json`
   - sha256: 618d37ff38a162da385c05e84e23c04c9f761fc3f8355f4e37d5b4316b58ac89
   - schema_version: 2.0
   - path_rel: artifacts/capability_ledger/capability_ledger.json

2. **验证 gate**：`python3 tools/audit/ledger_contract_gate.py --check` → verdict=PASS（C1-C4 全 PASS）

3. **正控反例**：篡改台账 sha256 → gate 红（C3 FAIL）；还原 → gate 绿

4. **运行时一致性**：`backend/api/capability_health.py` 直接调用 `ledger_paths.resolve_ledger()`，
   读的是同一份 `artifacts/capability_ledger/capability_ledger.json`，sha256 与 pin 一致。

5. **域词扫描**：`domain_leak_scan.py` 对新增文件扫描 → 0 命中（零领域语义）

## ③ 计算结果

| 判据 | 落盘前 | 落盘后 |
|------|--------|--------|
| C1_consumer_sha256_equal | PASS | PASS |
| C2_schema_version_registered | PASS | PASS |
| C3_no_ledger_drift | FAIL | PASS |
| C4_consumers_healthy | FAIL | PASS |
| /health status | DEGRADED(PIN_MISSING) | OK |
| pytest (5 cases) | N/A | 5/5 PASS |

## ④ 结果图

见 `artifacts/capability_ledger/fig1_consumer_health.png`：四消费者 + pin 的 sha256 一致性图，
全部绿色（sha256 前缀 618d37ff38a1 完全一致）。

## ⑤ 注意点

- pin 文件必须随台账内容变更同步更新（`--write-pin`），否则 C3 判 LEDGER_DRIFT 红灯
- 消费者链（audit_capabilities/CLI/health/report_gate）硬编码读真实仓路径，
  测试中用 tmp_path 隔离时需用 monkeypatch 或直接用真实仓
- 本单未修改部署配置，/health 运行时读的是同一份台账副本，无需外部修改单

## ⑥ 误差判定

本单为平台完整性修复，无数值计算误差。关键量对比：
- pin sha256 vs 台账 sha256：完全一致（0 差异）
- 四消费者 sha256：完全一致（0 差异）
- 正控反例：篡改后 C3 必红，还原后必绿 → 机器判据可裁

**结论**：PIN_MISSING 已消除，C1-C4 全 PASS，/health 返回 OK。
