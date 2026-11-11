# 能力台账自动审计与证据链裁定：SQL 台账 + 逐位可复核摘要（capability_ledger_digest）

把「人工登记能力等级」换成**一条可重跑的机器流水线**：

```
spec.json ──► 分类器 ──► 证据链（三项）──► SQL 台账 ──► 规范转储(dump) ──► sha256
                                                                  │
                                            审计报告（内嵌 dump + 摘要）◄┘
                                                                  │
                                                     复算校验 verify_audit()
```

一条命令产出全部验收证据：

```bash
python tools/capability_ledger_digest/materialize.py \
    --spec tests/capability_ledger_digest/fixtures/spec_known.json \
    --out artifacts/capability_ledger_digest --new-tests 52
# → acceptance.json: a/b/c/d/e/f 六条判据逐条 true
```

## 四个部件

| 文件 | 职责 |
|---|---|
| `spec.py` | 值对象：`Thresholds`（三项阈值 + 指纹）、`EvidenceRecord`（原始测量，不含等级）、`AuditSpec`（注入的输入） |
| `evidence_chain.py` | 三项证据链（test_pass_rate / benchmark_error_pct / independent_review）逐项裁定 + 链摘要 |
| `classifier.py` | R0–R6 决策表：测量值 → REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED（纯函数） |
| `sql_ledger.py` | SQLite 台账（`ledger_capability` / `ledger_gate` / `ledger_meta`）+ **规范转储** + 摘要 |
| `audit.py` | 一轮审计：分类 → 写 SQL → 规范转储 → 摘要落入报告 → `verify_audit()` 复算 |
| `figures.py` / `cli.py` / `materialize.py` | 图件、命令行、验收包物化 |

## 决策表（首条命中即生效，规则号写进台账每一行）

| 规则 | 条件 | 等级 |
|---|---|---|
| R0 | `impl_present = false` | NOT_SUPPORTED |
| R1 | 无测试通过率（无测试证据） | STUB |
| R2 | `mock_markers > 0` | MOCK |
| R3 | `benchmark_error_pct >= 15` | MOCK |
| R4 | `test_pass_rate < 0.8` | WITHHELD |
| R5 | 独立复核未通过（且本轮要求复核） | WITHHELD |
| R6 | 三项证据全通过 | REAL |

排序理由：无实现 > 无测试的空壳 > 显式 MOCK 标记/超不可接受线（与 REAL 主张直接矛盾）> 证据不足。

## 三条不可协商的确定性契约

1. **无时钟**：台账与报告里没有任何时间戳、文件名、主机信息；报告因此是 spec 的纯函数。
2. **规范转储**：所有度量经 `fmt_number()` 归一（`repr(float)`，保证浮点文本可逆），行按主键排序，
   `canonical_dump()` 是台账的唯一文本投影。
3. **逐位对照**：报告内嵌该转储原文 + `ledger.sha256`；`verify_audit()` 从数据库文件重新转储，
   与报告**字符串逐位相等**且摘要相等才算通过（判据 c）。

## 验收数字（`artifacts/capability_ledger_digest/acceptance.json`）

| 判据 | 要求 | 实测 | 产物 |
|---|---|---|---|
| (a) 分类准确率 | ≥10 条已知标签（5 REAL + 5 MOCK/STUB），准确率 100% | 13/13 = 100%（5 REAL、3 MOCK、2 STUB） | `acceptance.json` `a_…` |
| (b) 证据链裁定一致率 | 三项全通过才升级，任一不通过不升级，一致率 100% | 8/8 组合一致 = 100% | `a…`/`b…`、`gate_matrix.json` |
| (c) 台账↔报告逐位一致 | SQL 更新后与报告 sha256 相同 | `7cbd90de…2b41` = `7cbd90de…2b41` | `ledger_canonical.txt`、`verification.json` |
| (d) 零领域语义 | 命中数 = 0 | 0 / 17 文件 × 37 词条 | `acceptance.json` `d_…` |
| (e) 幂等 | 重复审计逐位一致 | 3 次运行 1 个台账摘要、1 个报告摘要 | `fig3_idempotency.png` |
| (f) 回归 + 新增用例 | 既有套件绿 + 新增 ≥15 | 新增 52 例全绿（本模块） | 见 `docs/capability_ledger_digest.md` |

> `artifacts/capability_ledger_digest/ledger.sqlite` 是构建产物（`*.sqlite` 被 `.gitignore` 排除）；
> 用上面那条 `materialize.py` 命令可一键重建，`ledger_canonical.txt` 与 `audit_report.json` 已入库供审计。

## 图件

| 文件 | 内容 |
|---|---|
| `fig1_gate_matrix.png` | 能力 × 三项证据的 PASS/FAIL 矩阵，右侧标注派生等级与规则号 |
| `fig2_audit_profile.png` | 等级分布 / 触发规则 / 台账迁移（UPGRADE/HOLD/DOWNGRADE）三联图 |
| `fig3_idempotency.png` | 三次重跑的台账摘要（同一个摘要 = 幂等） |

## 与既有模块的分工（本模块不重复实现的部分）

| 既有模块 | 它做什么 | 本模块补什么 |
|---|---|---|
| `tools/capability_ledger_maturity` | 证据采集器 + 裁定引擎（从 pytest/benchmark 工件里解析） | 本模块**不解析工件**：输入是已采集好的测量值（spec 注入），职责边界清晰 |
| `tools/capability_maturity_pipeline` | 矛盾检测、证据驱动回滚、前驱链审计 | 本模块不回溯历史，只保证**单轮判定与台账/SQL/报告的逐位一致** |
| `tools/capability_auto_audit` | 文件监听式自动升降级 + append-only 日志 | 本模块的账本是**可查询 SQL 表**且摘要可与报告逐位对照 |

## 零领域语义

能力 ID、阈值、测量值、期望标签全部来自注入的 spec；模块代码只认
`test_pass_rate / benchmark_error_pct / independent_review / mock_markers` 四个度量名。
`tests/capability_ledger_digest/test_domain_semantics.py` 复用 `tools/evidence_domain_scan.py`
做正控（0 命中）与负控（种子违规词必须被抓到），防「扫描器空转导致的假绿」。

## 测试

```bash
python -m pytest tests/capability_ledger_digest -q      # 52 passed
```