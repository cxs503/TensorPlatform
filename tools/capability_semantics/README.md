# tools/capability_semantics —— 能力台账语义三元分层 + NOT_SUPPORTED 误用治理

把平台能力台账从**扁平结构**（`id / domain / 声明等级 / 自由文本证据`）重构成
**语义三元分层**，并为「误用」与「证据」建立机器判据。P266W1-N10 交付。

## 三层是什么

| 层 | 回答什么 | 数据从哪来 | 例子（合成名） |
|---|---|---|---|
| **capability** | 平台声称能做什么 | 台账 + 能力配置（声明源） | `synth.calc`（声明 REAL） |
| **delivery** | 具体交付了什么（可复核） | 仓内扫描：代码/测试/验证工件/端点模块，带路径 + 行号 + 文件 sha256 | `backend/api/domain/synth_x.py`（2 端点） |
| **evidence** | 凭什么信（验证证据） | 审计裁决（stance/asserted_level）+ 带数值判据的验证工件 | 误差 1.2% ≤ 3% → supports_real |

**核心纪律：代码存在 ≠ 证据存在。** 有实现但缺独立数值验证工件的能力一律 `WITHHELD`，
不允许「有交付就算 REAL」。

## 证据等级判定级联（顺序即优先级）

| 序 | 条件 | 派生等级 / 理由码 |
|---|---|---|
| 1 | 有 `stance=contradicts` 的审计裁决 | 取最弱裁决等级 / `CONTRADICTED_BY_AUDIT` |
| 2 | 无 code 交付、无测试、无证据 | `NOT_SUPPORTED` / `NO_DELIVERY_NO_EVIDENCE` |
| 3 | 无 code 交付、有测试引用 | `WITHHELD` / `NO_CODE_DELIVERY_WITH_TEST_REFERENCE` |
| 4 | 无 code 交付、有证据/验证工件 | `WITHHELD` / `NO_CODE_DELIVERY_WITH_EVIDENCE` |
| 5 | 有 code + 数值通过工件（误差 ≤ 3%） | `REAL` / `NUMERIC_PASS_VERIFIED` |
| 6 | 有 code + 含数值断言的测试 | `WITHHELD` / `UNVERIFIED_IMPLEMENTATION` |
| 7 | 有 code + 无数值断言的测试 | `WITHHELD` / `TEST_WITHOUT_NUMERIC_ASSERT` |
| 8 | 只有 code | `STUB` / `CODE_ONLY` |

精度是**正交维度**（`accuracy_flag`：≤3% / 3–15% / >15% / 无数值），
不与等级混同——「算得不准」不得写成「没实现」，反之亦然。

## NOT_SUPPORTED 误用治理

| kind | 定义 | 严重度 | 改判 |
|---|---|---|---|
| `UNDERCLAIM_NOT_SUPPORTED` | 声明 NOT_SUPPORTED 但仓内有 code 交付 | high | 有数值断言测试 → MOCK；否则 STUB |
| `UNREGISTERED_DELIVERY` | 端点模块有路由但账上无条目（对外等价 NOT_SUPPORTED） | high | 注册条目，等级 STUB |
| `CLAIM_WITHOUT_DELIVERY` | 声明有等级但扫描零 code 交付、零证据 | low（可能关键词未覆盖） | 只给 `REVIEW_SCAN_KEYWORDS`，不自动降级 |

改判**只写补丁工件**（`registry_patch.json`，带 `applies_to_ledger_sha256` 与逐条
`basis_sha256`），并做补丁后复扫验证；规范台账本体不在本节点改写（避免自证式修复）。

## 用法

```bash
# 全流程：工件 + 图 + 报告文档（真实扫描约 45 秒）
python3 -m tools.capability_semantics --figures \
    --doc docs/reports/P266W1_N10_capability_semantics.md --coverage-pct 85.0

# 只算不落工件（CI/调试）
python3 -m tools.capability_semantics --dry-run --json

# 单测（合成域名 synth.*，注入假扫描器，秒级）
python3 -m pytest tools/capability_semantics/tests -q
```

产物：`artifacts/capability_semantics/`
（`semantic_layers.json` / `evidence_audit.json` / `evidence_audit_after_patch.json` /
`misuse_report.json` / `registry_patch.json` / `dependency_graph.json` + `.dot` /
`acceptance.json` / `audit_log.json`），全部走 sha256 链（`audit_log.json` 的
`prev` 必须等于上一条 `record_sha256`）。

API：`backend/api/capability_semantics.py`
（`/api/v1/tensor/capabilities/semantics{,/layers,/evidence,/misuse,/graph,/acceptance,/refresh}`）。

## 模块地图

| 文件 | 职责 |
|---|---|
| `model.py` | 三层数据结构、等级序、`normalise_level` |
| `sources.py` | 声明源装载（台账/能力配置/注册表快照三形状 + 别名）、配置默认值、端点清单 |
| `scan.py` | 交付扫描适配（默认按**文件路径**加载仓内三源扫描内核，避开 `backend/tools` 影子包） |
| `layering.py` | 三层装配、归类完备性、条目丢失、孤立端点 |
| `evidence.py` | 等级级联、精度档、WITHHELD 逐因分析 |
| `misuse.py` | 误用三分类、补丁生成、补丁后复扫验证 |
| `graph.py` | 依赖图（环/自环/缺目标/孤立/拓扑序/DOT/Mermaid） |
| `pipeline.py` | 一键流水线、验收判据 (a)–(f)、sha256 链、文档后回填 (e) |
| `figures.py` | 三张报告图（等级分布 / 三层覆盖+治理前后 / 依赖图） |
| `cli.py` | 命令行 + Markdown 报告渲染（数字程序化注入） |

## 零领域语义

代码里没有任何能力 id、学科名或域词条；能力表、依赖边、扫描关键词与端点扫描根目录
全部来自 `config/capability_semantics.json`（本模块）与 `config/capabilities.json`（扫描
关键词，既有资产）。单测夹具一律用合成域名 `synth.*`。

## 自指证据治理（重要）

扫描器会把仓库里带数值判据的文档当"验证工件"。如果本节点自己的报告/工件也在扫描范围内，
就会出现循环论证：**报告里写「误差 ≤3%」→ 扫描判 pass → 变成能力证据**。
因此 `config/capability_semantics.json` 的 `delivery_discovery.exclude_glob_patterns`
必须排除本节点产物（`artifacts/capability_semantics/**`、`docs/reports/P266W1_N10_*`、
`docs/reports/capability_semantics/**`、`tools/capability_semantics/**`），
排除项会写进 `semantic_layers.json::stats.warnings`（可复核）。

## 确定性

同一仓库内容下重跑，`semantic_layers.json` / `acceptance.json` 逐字节一致
（无时间戳、键排序、路径相对化）——sha256 链因此可作为回归基线：

```bash
python3 -m tools.capability_semantics --figures --doc docs/reports/P266W1_N10_capability_semantics.md
sha256sum artifacts/capability_semantics/semantic_layers.json   # 重跑应不变
```

## 局限

1. 交付绑定依赖注入的关键词/判别词表：门控保守会造成**假阴性**（有实现但未绑定），
   这类条目落 `WITHHELD / NO_CODE_DELIVERY_WITH_TEST_REFERENCE` 并进
   `REVIEW_SCAN_KEYWORDS`，不会被误判成「已验证」。
2. `REAL` 归因分两级披露：`subject_backed`（工件自身主题命中）与 `keyword_only`
   （仅关键词共现）——后者不是独立复核，报告里必须点名。
3. 依赖图只在**已声明依赖边**的能力上判环与孤立；补丁新增条目尚无依赖声明，
   不纳入分母（强行连边等于发明证据）。
4. 孤立率主口径是 `graph_role=domain` 子图；全体（含工具类节点）比例单列披露。