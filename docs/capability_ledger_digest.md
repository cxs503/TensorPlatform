# 能力台账自动审计与证据链裁定机制（P226W3-N08 / mission 13887）

## 0 本单定位与诚实声明

本单是**机制类（平台共性能力）任务**，不是物理算例任务：被审计的对象是「能力等级主张是否被证据支持」，而不是某个流场。因此全篇没有几何/网格/雷诺数，替代它的是「注入的能力证据集 + 预声明阈值」这套可复现的参数面。

诚实声明 1：本单为**第 2 次尝试**。worktree 内未找到第 1 次尝试的任何报告或产物（`/tensor/reports` 无 mission 13887 记录），因此「先读任务报告吸取教训」无法执行；本次按既有前置模块（capability_ledger_maturity / maturity_pipeline / auto_audit）做三重检索后，选择它们**都没有覆盖**的一段：SQL 台账 ↔ 审计报告的**逐位摘要对照 + 幂等**。
诚实声明 2：本模块**不判断证据本身的真伪**，只保证「给定证据 → 等级 → 台账 → 报告」这条链的确定性与可复核性；证据采集仍是上游职责（见 §5 局限）。

## 1 案例介绍（参数面）

### 1.1 背景

平台能力台账原先靠人工登记能力等级（REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED）。人工登记的失败模式有二：（i）等级主张与证据脱钩（自述 REAL、实测误差 22%）；（ii）改动不可复核（说不清是哪一条证据把等级抬上去的）。本单把这条路径改成四段机器流水线，并要求**台账与报告逐位一致**。

### 1.2 输入（注入的 spec）

- spec_sha256 = `d525210631d490f3c2031a87aa1896a88715a25b3e9b6d2574b67dbb3f99666f`
- thresholds_sha256 = `cc7a691993559fa46a31deb68b447f1451ed51467ee7c41f1b6f4de7495a8c71`
- 阈值：test_pass_rate_min = 0.8，benchmark_error_max_pct = 15，require_independent_review = True
- 能力条数：13（其中 13 条带已知期望标签）

表 1 证据集（每条能力的三个度量与裁定结果；全部由 spec 注入，代码内零领域词条）

| 能力 ID | impl | 测试通过率 | 基准误差 % | 独立复核 | mock 标记 | 期望等级 | 派生等级 | 规则 | 迁移 |
|---|---|---|---|---|---|---|---|---|---|
| cap.alpha | True | 0.98 | 1.2 | pass | 0 | REAL | REAL | R6 | UPGRADE |
| cap.beta | True | 1 | 2.4 | pass | 0 | REAL | REAL | R6 | UPGRADE |
| cap.delta | True | 0.85 | 9.5 | pass | 0 | REAL | REAL | R6 | UPGRADE |
| cap.epsilon | True | 0.8 | 14.99 | pass | 0 | REAL | REAL | R6 | UPGRADE |
| cap.eta | True | 0.99 | 0.4 | pass | 3 | MOCK | MOCK | R2 | DOWNGRADE |
| cap.gamma | True | 0.9 | 6 | pass | 0 | REAL | REAL | R6 | UPGRADE |
| cap.iota | True | n/a | n/a | missing | 0 | STUB | STUB | R1 | DOWNGRADE |
| cap.kappa | True | n/a | 5 | missing | 0 | STUB | STUB | R1 | DOWNGRADE |
| cap.lambda | True | 0.75 | 8 | pass | 0 | WITHHELD | WITHHELD | R4 | HOLD |
| cap.mu | True | 0.92 | 3 | fail | 0 | WITHHELD | WITHHELD | R5 | HOLD |
| cap.nu | False | n/a | n/a | missing | 0 | NOT_SUPPORTED | NOT_SUPPORTED | R0 | HOLD |
| cap.theta | True | 0.6 | 30 | fail | 0 | MOCK | MOCK | R3 | DOWNGRADE |
| cap.zeta | True | 0.95 | 22 | pass | 0 | MOCK | MOCK | R3 | DOWNGRADE |

## 2 怎么做的

```
spec.json ──► 分类器(R0–R6) ──► 证据链(三项) ──► SQL 台账 ──► 规范转储 dump ──► sha256
                                                    │                              │
                                                    └────► 审计报告（内嵌 dump + 摘要）◄┘
                                                                   │
                                                         verify_audit() 从 .sqlite 复算
```

### 2.1 决策表（首条命中生效，规则号写入台账每一行）

| 规则 | 条件 | 等级 |
|---|---|---|
| R0 | impl_present = false | NOT_SUPPORTED |
| R1 | 无测试通过率（无测试证据 ⇒ 空壳） | STUB |
| R2 | mock_markers > 0（显式硬编码/随机信号） | MOCK |
| R3 | benchmark_error_pct ≥ 15（超出不可接受线） | MOCK |
| R4 | test_pass_rate < 0.80 | WITHHELD |
| R5 | 独立复核未通过 | WITHHELD |
| R6 | 三项证据全通过 | REAL |

排序理由：无实现 > 无测试的空壳 > 显式 MOCK 标记/超线误差（与 REAL 主张直接矛盾，优先于「证据不足」）> 证据不足。R2 刻意排在 R6 之前：数字全绿但存在 mock 标记时仍判 MOCK —— 这正是人工台账最常见的过度主张。

### 2.2 三条确定性契约（幂等与逐位对照的前提）

1. **无时钟**：台账与报告不含时间戳、文件名、主机名 —— 报告是 spec 的纯函数。
2. **规范转储**：所有度量经 `fmt_number()`（`repr(float)`，浮点文本可逆）归一，行按主键排序，`canonical_dump()` 是台账唯一文本投影。
3. **逐位对照**：报告内嵌 dump 原文与 `ledger.sha256`；校验器从 `.sqlite` 重新转储并与报告字符串逐位比较。

### 2.3 工具链与命令

- Python 3.11 + sqlite3（标准库）+ matplotlib(Agg) + pytest
- 复现全部验收证据：

```bash
python -m pytest tests/capability_ledger_digest -q        # 52 passed
python tools/capability_ledger_digest/materialize.py \
    --spec tests/capability_ledger_digest/fixtures/spec_known.json \
    --out artifacts/capability_ledger_digest --new-tests 52
```

## 3 计算结果

表 2 判据逐条结果（`artifacts/capability_ledger_digest/acceptance.json`）

| 判据 | 要求 | 实测 | 判定 |
|---|---|---|---|
| (a) 分类准确率 | ≥10 条已知标签（5 REAL + 5 MOCK/STUB），100% | 13/13 = 100.0% | PASS |
| (b) 证据链裁定一致率 | 三项全通过才升级，任一不通过不升级，100% | 8/8 = 100.0% | PASS |
| (c) 台账↔报告逐位一致 | sha256 相同 | `7cbd90ded1e0a5a4…` = `7cbd90ded1e0a5a4…` | PASS |
| (d) 零领域语义 | 命中数 = 0 | 0 命中 / 19 文件 × 37 词条 | PASS |
| (e) 幂等 | 重复审计逐位一致 | 3 次运行 → 1 个台账摘要 / 1 个报告摘要 | PASS |
| (f) 新增用例 | ≥15 | 52 例全绿 | PASS |

表 3 五条摘要（审计链）

| 摘要 | 值 |
|---|---|
| spec_sha256 | `d525210631d490f3c2031a87aa1896a88715a25b3e9b6d2574b67dbb3f99666f` |
| thresholds_sha256 | `cc7a691993559fa46a31deb68b447f1451ed51467ee7c41f1b6f4de7495a8c71` |
| results_sha256 | `4908e2164a43b7dfd6144cd64c7cdf7985d690ebdcf103a2621f22a56245ba32` |
| ledger_sha256（SQL 台账规范转储） | `7cbd90ded1e0a5a434083133ef463a93f5628a8489b4730c60ce7302ae162b41` |
| report_sha256（报告正文） | `c8c9a056d64e9bd1b85b821e010d5caaecf4c3bcabeb8c049df9524713c126d7` |

表 4 等级分布 / 规则触发 / 迁移

| 维度 | 计数 |
|---|---|
| level MOCK | 3 |
| level NOT_SUPPORTED | 1 |
| level REAL | 5 |
| level STUB | 2 |
| level WITHHELD | 2 |
| transition DOWNGRADE | 5 |
| transition HOLD | 3 |
| transition INIT | 0 |
| transition UPGRADE | 5 |

表 5 回归（既有套件 + 本单新增；逐目录单独运行）

| 套件 | 结果 | 用时 |
|---|---|---|
| tests/capability_ledger_digest（本单新增） | 52 passed | 0.9 s |
| tests/capability_ledger_maturity | 54 passed | 3.5 s |
| tests/capability_auto_audit | 60 passed | 2.3 s |
| tests/capability_maturity_pipeline | 30 passed | 0.5 s |
| tests/capability_ledger_auto | 未跑完（110 s 上限内 ~95%，既有慢用例） | >110 s |

注：多个能力套件**同时**收集时有既有的同名测试文件冲突（`test_evidence_chain.py` 在 `tests/capability_auto_audit/` 与 `tests/capability_ledger_auto/` 各一份，无 `__init__.py`），故上表逐目录运行；此为既有状况，与本改动无关。

## 4 结果图

- 图 1 `fig1_gate_matrix.png`：能力 × 三项证据 PASS/FAIL 矩阵，行尾标注派生等级与规则号（R3 的红行是误差超线被降为 MOCK 的例）。
- 图 2 `fig2_audit_profile.png`：等级分布 / 触发规则 / 台账迁移三联图；标题给出已知标签准确率与报告摘要前缀。
- 图 3 `fig3_idempotency.png`：三次重跑的台账摘要，同一摘要 = 幂等成立。

## 5 注意点（踩坑 / 局限 / 适用范围）

**踩坑 1（递归）**：`ChainVerdict.to_dict()` 内联调用 `self.sha256()`，而 `sha256()` 又调 `to_dict()` → `RecursionError`。修法：拆出 `body()`，摘要只对 body 计算。

**踩坑 2（幂等被文件名击穿）**：报告最初记录 `ledger.path = db_filename`，同一 spec 写到不同文件名就产生不同报告 → 判据 (e) 直接红。修法：报告不携带任何与运行环境相关的字段。

**踩坑 3（假绿风险）**：如果只是断言「扫描器返回 0 命中」，扫描器本身空转也会变绿。本单加了负控用例：从共享词表取一个词写进临时文件，扫描器必须抓到 —— 正控 0 命中、负控必命中，才证明「零领域语义」是真结论。

**局限 1**：证据数值仍由上游采集层提供（本模块不解 pytest/benchmark 工件）。证据本身若造假，本机制会**忠实盖章**；防伪要靠采集层的独立复核。

**局限 2**：规范转储的逐位一致性是 SQLite 语义下的结论（`REAL` 为 IEEE-754 double，文本经 `repr` 可逆）。迁到 PostgreSQL 需重定 dump 规范（`numeric` 精度、排序 collation）。

**局限 3**：本单的 13 条能力是 synthetic 已知标签集，用于验证分类器的判定正确性，**不代表**真实 21 项能力当前的真实等级；真实等级必须由真实证据跑出来的结果决定。

**局限 4**：`tests/capability_ledger_auto` 在 110 s 内未跑完（既有慢用例，位置约 95%），本单未把它纳入回归结论；TensorPlatform 全量套件超出单命令预算，未全量运行。

## 6 误差判定（逐项、显式回答是否 <3%）

本单是机制/软件正确性任务，**没有实验或解析解基准**可以对照物理量 —— 这是「无基准」的显式声明（不是省略）。作为替代，误差被定义为与机器基准的偏差：

| 关键量 | 基准 | 实测 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| 分类准确率 | spec 声明的期望等级 | 13/13 | 0 条不一致 | 是（0%） |
| 裁定一致率 | gate_matrix 声明的 8 组期望 | 8/8 | 0 组不一致 | 是（0%） |
| 台账↔报告一致性 | 报告内嵌 dump | 从 .sqlite 重新转储 | 字符差 0，sha256 相同 | 是（0%） |
| 幂等 | 第 1 次运行的摘要 | 第 2/3 次运行 | 摘要种类 = 1 | 是（0%） |
| 领域词命中 | 0 | 0 / 19 文件 | 0 | 是（0%） |

结论：四条主判据的偏差均为 0%，「<3%」成立。但必须强调：这是**软件确定性指标**，不能外推为任何求解器的物理精度；对物理量精度，本单不提供任何证据，也不做任何主张。

