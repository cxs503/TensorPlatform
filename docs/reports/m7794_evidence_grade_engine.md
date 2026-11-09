# P350W1-N05 成熟化：证据等级机器推导引擎（TensorPlatform）

**结论先行**：证据等级 REAL/MOCK/STUB/WITHHELD 在本节点由**工件结构**机器推导，
不再是作者声明。30 例合成域对抗夹具判定复现 **30/30**，
同源复制陷阱检出 **6/6**，与人工标注
Cohen's κ = **1.000**，域词扫描命中
**0**（扫描 166 个文件），
缺真值来源时 `audit_capabilities` 返回 **UNKNOWN** 而非异常。

## 1. 案例介绍（问题背景与对象）

平台最核心的判据是「某个能力到底有多真」。历史上这个判据有三处结构性缺口：

1. **等级来自自述**。`capabilities_snapshot.json` 里 21 项能力的实体是「作者写了 REAL」，
   读者无法复算。声明不是证据，谁写台账谁就能写 REAL。
2. **仪器失灵表现为死锁或谎言**。m2031 记录了单一硬编码路径导致 `audit_capabilities`
   对全部席位恒返回读取失败；反向风险同样致命——返回一张空表会被误读为「能力都正常」。
3. **「数字齐全」被当成「证据齐全」**。锚点、残差、两条路径、负控四个字段凑齐即可自称
   REAL，但两条路径可能是同一次计算的两次复制。

本节点把等级定义成工件结构的纯函数，并用 30 例**合成域**夹具（名词 alpha/beta/gamma，
标量管线，无物理语义）把推导器逼到能分清「真独立」与「同源复制」。

### 对象与参数表

| 项 | 值 |
|---|---|
| contract schema | `schemas/evidence_contract.schema.json`（evidence-contract/1.0，纯结构） |
| 规则 DAG | R00…R13 十四道门（见 §2 表） |
| 夹具 | 30 例，种子 20261109，标签分布 {'WITHHELD': 8, 'STUB': 6, 'MOCK': 8, 'REAL': 8} |
| 陷阱 | 6 例同源复制（case-01, case-05, case-10, case-12, case-17, case-22） |
| 对账对象 | `tests/fixtures/capability_ledger/capabilities_snapshot.json`（21 项自述） |
| 域词语表 | 37 词条（配置豁免文件 `tools/evidence_domain_lexicon.json`） |

## 2. 怎么做的（方法 / 流程 / 关键设置）

**证据契约**（纯结构，禁域词）：`claim{kind,tolerance}` + `artifacts[]`
（`anchor` / `independent_path` / `residual` / `negative_control` / `interface`）
+ `repro_cmd` + `provenance.sha256`。每个 artifact 的 `source` 是
`{kind:file|url|inline|stdout, ref, sha256}`。

**规则 DAG**（顺序求值，第一个失败的门决定等级）：

| 门 | 判据 | 失败降级 |
|---|---|---|
| R00 | schema 合法 | UNKNOWN |
| R01 | 存在数值工件 | STUB |
| R02 | 有 repro_cmd | MOCK |
| R03 | 有 anchor | MOCK |
| R04 | anchor 可离线复算（file+sha256 命中） | MOCK（inline → HARDCODED_INLINE） |
| R05 | 有 residual | WITHHELD |
| R06 | ≥1 条 independent_path | WITHHELD |
| R07 | 每条路径可离线复算 | WITHHELD |
| R08 | 声明残差 == 重算残差（rtol 1e-9） | WITHHELD |
| R09 | 预声明容差存在且重算残差 ≤ 限值 | WITHHELD |
| R10 | 路径独立性（按 sha256/ref/derived_from，不信 method 标签） | WITHHELD |
| R11 | 负控 must_fail 且实测 fail（并校验其载荷） | WITHHELD |
| R12 | provenance.sha256 == 重算的工件集摘要 | WITHHELD |
| R13 | 全门通过 | REAL |

**关键设置**：`inline` 源一律判为不可复算（硬编码的定义）；相对路径以契约所在目录解析；
残差由 `|anchor − path| / |anchor|` 重算，声明值必须与重算值一致；工件集摘要
`provenance_digest = sha256(sorted([id, source.sha256]))`，任何人偷换工件都会破坏它。

**工具链**：`tools/evidence_grade.py`（引擎 + CLI）、`tools/evidence_fixtures.py`（夹具生成器）、
`tools/evidence_reconcile.py`（对账 + κ + metrics）、`tools/evidence_domain_scan.py`（域词门）、
`tools/evidence_grade_figures.py`（出图）、`.github/workflows/evidence-grade.yml`（CI）。

## 3. 计算结果（判据数字，程序化注入）

| 判据 | 实测 | 阈值 | 判定 | 说明 |
|---|---|---|---|---|
| a_fixture_reproduction_rate | 1.0 | 1.0 | PASS | derived grade equals the construction-time label |
| b_same_source_trap_detection | 6/6 | 6/6 | PASS | same-source copy graded WITHHELD with reason SAME_SOURCE_COPY |
| c_cohen_kappa_vs_labels | 1.0 | 0.9 | PASS | derived grade vs construction-time human label |
| d_reconciliation_json | artifacts/evidence_grade/reconciliation_vs_self_report.json | - | PASS | declared-vs-derived inconsistency ledger emitted as JSON |
| e_domain_word_hits | 0 | 0 | PASS | zero domain-vocabulary hits in engine/schema/fixtures |
| f_audit_unknown_on_missing_source | UNKNOWN | - | PASS | [证据等级] UNKNOWN |

**门归因**（每例落在哪道门）：

| 等级 | reason_code | 例数 |
|---|---|---|
| REAL | ALL_GATES_PASS | 8 |
| STUB | INTERFACE_ONLY | 6 |
| WITHHELD | SAME_SOURCE_COPY | 6 |
| MOCK | HARDCODED_INLINE | 3 |
| MOCK | NO_ANCHOR | 2 |
| MOCK | NO_REPRO_CMD | 2 |
| WITHHELD | NO_RESIDUAL | 2 |
| MOCK | ANCHOR_NOT_RECOMPUTABLE | 1 |

**混淆矩阵**：

| label \ derived | 分布 |
|---|---|
| WITHHELD | WITHHELD:8 |
| STUB | STUB:6 |
| MOCK | MOCK:8 |
| REAL | REAL:8 |

**逐例明细**：

| case | 标注 | 推导 | reason | 标注 reason |
|---|---|---|---|---|
| case-01 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-02 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-03 | MOCK | MOCK | HARDCODED_INLINE | HARDCODED_INLINE |
| case-04 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-05 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-06 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-07 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-08 | WITHHELD | WITHHELD | NO_RESIDUAL | NO_RESIDUAL |
| case-09 | MOCK | MOCK | NO_REPRO_CMD | NO_REPRO_CMD |
| case-10 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-11 | MOCK | MOCK | HARDCODED_INLINE | HARDCODED_INLINE |
| case-12 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-13 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-14 | WITHHELD | WITHHELD | NO_RESIDUAL | NO_RESIDUAL |
| case-15 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-16 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-17 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-18 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-19 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-20 | MOCK | MOCK | NO_ANCHOR | NO_ANCHOR |
| case-21 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-22 | WITHHELD | WITHHELD | SAME_SOURCE_COPY | SAME_SOURCE_COPY |
| case-23 | MOCK | MOCK | NO_REPRO_CMD | NO_REPRO_CMD |
| case-24 | MOCK | MOCK | NO_ANCHOR | NO_ANCHOR |
| case-25 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-26 | STUB | STUB | INTERFACE_ONLY | INTERFACE_ONLY |
| case-27 | MOCK | MOCK | HARDCODED_INLINE | HARDCODED_INLINE |
| case-28 | MOCK | MOCK | ANCHOR_NOT_RECOMPUTABLE | ANCHOR_NOT_RECOMPUTABLE |
| case-29 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |
| case-30 | REAL | REAL | ALL_GATES_PASS | ALL_GATES_PASS |

**与既有 21 项自述对账**（`artifacts/evidence_grade/reconciliation_vs_self_report.json`）：

| 项 | 值 |
|---|---|
| 能力数 | 21 |
| 一致 | 2 |
| 不一致 | 19 |
| 分类计数 | {'CONSISTENT': 2, 'UNVERIFIED': 19} |
| 自述快照 sha256 | f09ae98785e6b17a |

结论：**19 项自述等级没有任何可复算契约背书** → 判 `UNVERIFIED`。
这正是本节点要堵的缺口：自述 REAL 在机器可复算之前不带证据效力。

**对账逻辑双向验证**（合成自述 = 构造标签 + 3 例虚报 + 1 例低报）：

| 项 | 值 |
|---|---|
| 期望 | {'OVERCLAIM': 3, 'UNDERCLAIM': 1} |
| 实测 | {'OVERCLAIM': 3, 'CONSISTENT': 26, 'UNDERCLAIM': 1} |
| 双向命中的期望是否满足 | True |

## 4. 结果图

- `artifacts/evidence_grade/figures/fig1_confusion.png` — 推导等级 vs 标注等级（30 例全对角）。
- `artifacts/evidence_grade/figures/fig2_gate_attribution.png` — 降级归因：每道门拦下多少例。
- `artifacts/evidence_grade/figures/fig3_residual_vs_independence.png` — **本节点的核心图**：
  残差量级对 REAL 与同源复制陷阱完全不可分（都在容差线以下），能分开它们的只有出处
  （sha256/ref/derived_from）。这就是「等级必须是结构函数而不是数字函数」的直接证据。

## 5. 注意点（坑 / 局限 / 适用范围）

1. **R10 与数字无关**：6 例陷阱的锚点、残差、两条路径、负控、repro_cmd、provenance 全部齐全，
   其中 2 例还把第二条路径的 `method` 改成 `independent_beta`。若按字段计数或按 method 标签判
   独立性，全部会被误判为 REAL。**独立性只能由出处判**。
2. **κ 的口径**：标注与规则均由本节点作者按构造规格给出（非盲法双人标注），κ=1.0 是构造一致性的
   自证，不是人与人之间的一致性度量。这限制了 κ 的证据强度，见 §6 局限。
3. **inline 一律 MOCK**：这是有意的保守。任何「把数字抄进契约」的写法都无法被复算，
   即便数字恰好正确也只能是 MOCK。
4. **域词门是配置豁免式**：词表本身必须含域词，故扫描目标显式排除词表文件；
   同时用「注入域词的正控用例」证明扫描器会失败（`test_domain_scan_positive_control`）。
5. **pre-existing 缺陷（未在本节点修复）**：`tools/audit/ledger_contract_gate.py --check`
   在 HEAD 上即红（`PIN_MISSING`：`artifacts/capability_ledger/ledger_contract.json` 未入库），
   与本节点改动无关；补救命令是 `python3 tools/audit/ledger_paths.py --write-pin`，
   但那是台账治理决策，留给台账 owner，不在此静默改写。

## 6. 误差判定

本节点是**结构与判定逻辑**，无物理量基准可比，因此「关键量 vs 基准 <3%」不适用；改为对判据本身做
逐项误差判定：

| 判据 | 基准（构造真值） | 实测 | 误差 | 是否满足阈值 |
|---|---|---|---|---|
| 30 例判定复现率 | 30/30 | 30/30 | 0.00% | 是（阈值 100%） |
| 同源复制陷阱检出 | 6/6 | 6/6 | 6/6 全中 | 是（阈值 6/6） |
| Cohen's κ | ≥0.9 | 1.000 | 余量 +0.100 | 是 |
| 域词命中 | 0 | 0 | 0 | 是 |
| 缺源降级 | UNKNOWN | UNKNOWN | — | 是 |

**显式回答**：本节点不含可<3%对标的物理关键量，故该问不适用；上述**判据**全部满足预声明阈值
（这是本节点唯一可判的对标面）。**局限**：夹具标签与规则同源构造，κ 不能证明「独立第三方也这样判」；
真实价值需由后续把真实能力的工件迁成契约、再看机器判定与专家判定的分歧率来建立。

**未完成/留口**：21 项真实能力尚无一份 evidence contract（对账结果 19 项 UNVERIFIED）；
把其中若干迁移成契约是下一个节点的自然延伸，本节点只交付引擎、夹具、对账与降级修复。
