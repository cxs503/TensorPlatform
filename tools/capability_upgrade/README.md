# tools/capability_upgrade — 能力证据自动升级管线（P218-N01）

把“能力证据等级”从**人工判读的散文结论**变成 **(证据集合, 验收判据) 的确定性函数**，并让
**证据入库事件自动触发重判**：证据满足判据时能力位自动自愈升级 `WITHHELD → REAL`，
不满足时**明确保持 WITHHELD 并留下失败原因**（禁止静默放行）。

## 为什么需要

旧的判级是批处理：`tools/capability_auto_audit` 跑一遍全量分类再改台账。它有三个结构性缺口：

1. 等级与“哪个算例/测试/参考值”之间没有机器可查的边（不可溯源）；
2. 新证据入库不触发重判，WITHHELD 位长期冻结（靠人重跑 CLI）；
3. 缺证据的能力位与证据已满足的能力位在台账里长得一样（不自愈、不可区分）。

本模块补上这三块：关联模型 + 事件驱动重判 + 升级历史。

## 结构

| 文件 | 职责 |
|---|---|
| `model.py` | 等级序、`CapabilitySlot` / `EvidenceItem` / `Criterion` 数据模型、内容寻址 `digest` |
| `criteria.py` | 判据求值引擎（纯数据 DSL：白名单/最小证据数/最小来源数/溯源哈希门/数值门/矛盾门） |
| `store.py` | SQLite 存储（slot / evidence / link / criteria / event / run / history 七张表，全 append-only） |
| `engine.py` | 重判引擎：只升不降、命中判据才改级、逐判据 verdict + 失败原因入历史 |
| `pipeline.py` | 入库管线：`ingest()` 内**同步**重判（返回即生效），延迟写回事件行；`chain()` / `coverage()` |
| `registry.py` | 注册表注入（能力位 + 判据来自 JSON，代码零领域语义） |
| `ledger_adapter.py` | 既有 `artifacts/capability_ledger/*.json` → 能力位/证据的映射（数据边界） |
| `numeric_probe.py` | 合成面 demo 的数值证据：梯形求积 vs 闭式解，`error_pct` 现场计算 |
| `api.py` | FastAPI 路由 + `create_app()`（可独立跑，也可 `include_router(..., prefix="/api/v1/capability-upgrade")`） |
| `figures.py` / `report.py` | 3 张图 + 论文级 Markdown 报告（数字全部由工件注入，零手抄） |
| `cli.py` | `run`（完整验收跑）/ `serve`（起 HTTP） |
| `tests/` | 19 条 pytest，全部用合成能力名（零领域语义） |

## 判据 DSL（示例）

```json
{
  "criterion_id": "demo.analytic_integration::real",
  "capability_id": "demo.analytic_integration",
  "target_level": "REAL",
  "spec": {
    "evidence_kinds": ["numeric_verification"],
    "min_matching": 2,
    "min_sources": 2,
    "require_source_sha256": true,
    "forbid_observed_levels": ["MOCK", "STUB"],
    "metrics": [
      {"name": "error_pct", "op": "<=", "value": 3.0, "agg": "all"},
      {"name": "n_samples", "op": ">=", "value": 32, "agg": "min"}
    ]
  }
}
```

结构性规则（不依赖配置，始终生效）：证据**自身标注**的等级若低于判据目标级，一律不得用于证明该目标级 ——
`MOCK/STUB/NOT_SUPPORTED` 记 `contradiction_gate`，`WITHHELD`（只是没证实）记 `below_target_assertion`，
两类分开报，避免把“缺证”误报成“造假”，反之亦然。

## 用法

```bash
# 完整验收跑（demo + 真实台账 dry run + 3 图 + verdict.json）
python3 -m tools.capability_upgrade.cli run --out artifacts/capability_upgrade

# 起 HTTP API（默认只装载 demo 注册表；换成 ledger_registry.json 即为真实面）
python3 -m tools.capability_upgrade.cli serve --port 8021

# 测试
python3 -m pytest tools/capability_upgrade/tests -q
```

`run` 退出码 0 = C1–C7 全部机器判定通过；1 = 至少一条失败（失败项打印并落 `verdict.json`，不静默）。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/evidence` | 入库证据 → 同调用内自动重判，返回等级/结果/延迟 |
| POST | `/capabilities` | 声明能力位 |
| POST | `/capabilities/{id}/rejudge` | 手动重判（C2 一致性对照） |
| GET | `/capabilities/{id}/chain` | 证据（含 source.path/locator/sha256/digest）→ 判据 → 运行 → 历史 |
| GET | `/capabilities/{id}/history` | 升级历史（证据 ID / 判据 / 时间 / 结果 / 失败原因） |
| GET | `/coverage` | WITHHELD 位是否有 ≥1 证据（缺口具名） |
| GET | `/events` | 入库事件日志（含实测重判延迟） |
| GET | `/health` | 台账统计 + 最大延迟 + 覆盖率 |

## 逐级升账状态机（P348-W3-N01）

P218-N01 的 `RejudgeEngine` 是一次判定：criterion 通过即跳到 `target_level`。P348-W3-N01 在其上加
"可审计的台阶"层，四个模块、零领域语义、阈值全在配置里：

| 模块 | 作用 |
|---|---|
| `upgrade_rules.py` | 规则表：`capability_upgrade_rules`（每级台阶的证据条件）+ `capability_rollback_rules`（缺陷落点），JSON 为源、SQLite 为镜像，回读 digest 用于漂移检测 |
| `completeness.py` | 证据链完整性检查器：证据记录 → 7 个事实（锚点按 sha256 去重、只算带 `error_pct` 的测量），`assess()` 给 satisfied + completeness(0-1)，`completeness_report()` 扫全队 |
| `state_machine.py` | `NOT_SUPPORTED→STUB→MOCK→WITHHELD→REAL` 逐级攀爬；先回归校验、缺陷阻塞、幂等；`inject_defect()`/`resolve_defect()` 回滚 |
| `upgrade_audit.py` | 哈希链 JSONL 审计：每条含 from/to、规则 id+digest、升级前后证据快照、完整度、operator、reason；`verify()` 检出篡改 |

配置：`config/capability_upgrade/upgrade_rules.json`（`UpgradeRuleTable.from_json`）。
一次跑完验收（C1–C5 数字 + 3 图 + 论文级报告）：

```
python -m tools.capability_upgrade.upgrade_acceptance --out-dir runs/p348w3_n01_capability_upgrade
```

用例：`tools/capability_upgrade/tests/test_upgrade_state_machine.py`（42 例，覆盖状态机/检查器/审计/回滚）。

## 纪律

* **零领域语义**：域词不得出现在 `tools/capability_upgrade/**.py`；`test_c7_*` 用
  `tools/evidence_domain_lexicon.json` 对全部源码做机器扫描。
* **诚实 FAIL 不可覆盖**：证据不足 / 证据自标矛盾 / 缺溯源哈希 → 保持 WITHHELD 并把原因写进历史。
* **不自动降级**：只升不降，矛盾证据留痕交审计处置（自动降级会把一次误标放大成回滚风暴）。