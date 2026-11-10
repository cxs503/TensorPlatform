# P399W2-N02 能力台账证据链自动化：从等级判定到 sha256 溯源闭环

- 战役节点：`p399w2-n02-capability-evidence-automation`（mission 10701）
- 仓：TensorPlatform（共性机制，零领域语义）
- 代码：`src/capability_ledger_auto/{fingerprint,confidence,eval_validity,scanner,grader,ledger_audit}.py`
- 产物：`artifacts/capability_ledger_auto/`（4 个 JSON + sha256 旁车 + 3 张图）
- 复算入口：`tools/audit/run_p399w2_n02_evidence_automation.py`、`tools/audit/plot_p399w2_n02_figures.py`

## 1 案例介绍（这是什么问题）

平台的 21 项能力（`config/capabilities.json`）对外自述证据等级（REAL/MOCK/STUB/
WITHHELD/NOT_SUPPORTED）。要让"自述"变成"可复核"，需要三件机器可算的东西：

1. **判级**：按仓库里的代码/测试/报告/五元证据推出等级，而不是人写等级；
2. **溯源**：每条能力的判定都要能回指到具体证据行，且任何改动都会改变指纹；
3. **置信度**：判定本身有多确定，必须随证据强度变化。

入手时的三个真实缺陷（都有对照测量）：

| # | 缺陷 | 修复前实测 |
|---|---|---|
| D1 | 置信度是**规则常量**（NOT_SUPPORTED 0.95 / MOCK 0.85 / STUB 0.85 / REAL 0.90 / WITHHELD 0.70），同一等级强弱证据给出同一个数字 | 21 项能力里 19 项置信度完全由等级决定 |
| D2 | 代码证据**只扫 `src/`**（`scan_code_for_patterns(subdir="src")`），平台实现大量在 `backend/` | 带标注算例上 REAL 召回 0.50，`backend_root` 变体 3/6；真实台账里 8/21 项能力代码证据为 0 |
| D3 | 仅**文本行命中**（.md/.json 语料、测试替身）被算作"实质实现" | 多根扫描后 `calc.stability` 被测试文件里的 `mock_response` 判成 MOCK（假阳性） |

## 2 怎么做的（方法 / 流程 / 关键设置）

### 2.1 证据指纹（`fingerprint.py`）

每条能力的证据展开成叶子，按 `(kind, locator, digest)` 排序后链接成哈希链：

```
h_0 = sha256("GENESIS:<capability_id>")
h_i = sha256(h_{i-1} + "|" + canonical(leaf_body_i))
root = h_n
```

- 叶子 `kind ∈ {code, test, report, five_tuple}`，`digest` 是"文件 sha256 + 匹配单元 sha256"
  的组合；`verify_leaves()` 可逐条复算，改写/删除任一叶子都会被抓到（漏删靠
  `expected_root` 比对）。
- 落盘有界（每类 ≤32 条）但**根哈希覆盖全部叶子**，并显式登记 `truncated_by_kind`。
- `record_digest` 把 `registry_sha256 + grade + confidence + evidence_root + leaf_count`
  绑成一行摘要——"证据根没变但判级被改"同样可被发现。

### 2.2 证据驱动置信度（`confidence.py`）

特征：五元完整度 `complete`、链条质量 `quality`、测试证据强度 `source`
（executed=1.0 / static=0.4 / 无=0.0）、判定裕度 `margin`、反证条数 `reasons`、
仅接口节点数 `stub`、`ready = 0.5·complete + 0.3·quality + 0.2·source`。
各等级公式与权重全部外置（`DEFAULT_CONFIDENCE_WEIGHTS`），并裁剪到等级带内，
不出现 1.0 的伪确定性。特征随产物落盘（`confidence_features`），可独立复算。

### 2.3 判级器效度算例（`eval_validity.py`）

构造**带真值的合成仓库**（零领域语义：`fx.*` / `fxalpha*` 命名），每级 3 复本 +
2 类变体 × 3 复本，共 **21 例**：

- `standard`：证据放在 `src/`；
- `backend_root`：同一份 REAL/MOCK 证据放在 `backend/modules/`（把 D2 变成可度量假阴性）。

真值由构造过程决定，判定走**真实扫描器 + 真实 pytest 子进程**，输出混淆矩阵、
逐级 recall/precision/F1、变体命中率。同一套算例可用 `code_roots=("src",)`
复现修复前基线——"改进"因此有对照，而不是"变了"。

### 2.4 判定精度修复（`scanner.py` / `grader.py`）

| 修复 | 内容 | 依据 |
|---|---|---|
| F1 多根扫描 | `DEFAULT_CODE_ROOTS = (src, backend, api, services)`，可按能力 `code_roots` 覆盖 | D2 假阴性 |
| F2 弱证据分离 | `match_type="text"`（文件文本行命中）不再计入"实质实现"，单列 `weak_code_count` | 语料/散文不是实现 |
| F3 测试代码排除 | `is_test_path()`：目录段 `tests/test` 或文件名 `test_*`/`conftest` 不作为实现证据 | `backend/tests` 里的 `mock_response` 造成假 MOCK |
| F4 MOCK 标记主导性 | 节点级 MOCK 标记只在 `marker_nodes/impl_nodes ≥ 0.5`（或实现节点 ≤1）时定罪，否则记 `mock_marker_suspicion` | 单个 A/B 测试随机数否决整条能力 = 假阳性 |
| F5 有界扫描 | `CodeIndex`（建索引一次、AST 按需解析并缓存）+ 命中/解析/弱证据三档预算 | 多根扫描的算力开销（实测 21 能力全扫从 >900 s 降到分钟级） |

## 3 计算结果（全部程序化注入，零手抄）

### 3.1 判级器效度：21 例带标注算例

| 等级 | 算例数 | 召回(前) | 召回(后) | 精确率(前) | 精确率(后) |
|---|---|---|---|---|---|
| REAL | 6 | 0.50 | 1.00 | 1.00 | 1.00 |
| MOCK | 6 | 1.00 | 1.00 | 1.00 | 1.00 |
| STUB | 3 | 1.00 | 1.00 | 1.00 | 1.00 |
| WITHHELD | 3 | 1.00 | 1.00 | 0.50 | 1.00 |
| NOT_SUPPORTED | 3 | 1.00 | 1.00 | 1.00 | 1.00 |

- 总体准确率 **0.8571 → 1.0000**，
  macro-recall **0.9000 → 1.0000**。
- 变体命中（同一份证据、不同放置位置）：

| 变体 | 命中(前) | 命中(后) |
|---|---|---|
| backend_root | 3/6 | 6/6 |
| standard | 15/15 | 15/15 |

- 修复前唯一失配类型：`truth=REAL, predicted=WITHHELD`，理由恒为
  "五元不全：4/5，缺 code"——即 D2 的可度量证据。

### 3.2 平台 21 项能力真实台账

- 等级分布：修复前 MOCK=1, NOT_SUPPORTED=0, REAL=1, STUB=0, WITHHELD=19；修复后 MOCK=1, NOT_SUPPORTED=0, REAL=1, STUB=0, WITHHELD=19。
- **等级改变 0/21**，但证据覆盖显著变化：证据指纹叶子
  1097 → 2585（+136%），
  实质实现节点 101 → 716，
  代码证据为 0 的能力 9 → 5 项，
  19/21 项能力的证据指纹根发生变化。
- 台账级证据状态摘要：`51bf4784a405707d…` → `c0f3a7a27531cfc4…`
- 逐行（`record_digest` 自检：**True**）：

| 能力 | 等级(前) | 等级(后) | 置信度(前) | 置信度(后) | 实质实现(前) | 实质实现(后) | 证据指纹根(后) | 五元 | 测试证据 |
|---|---|---|---|---|---|---|---|---|---|
| ai.predict | WITHHELD | WITHHELD | 0.467 | 0.467 | 3 | 124 | `266ee90a5003…` | 4 | static |
| calc.resistance | WITHHELD | WITHHELD | 0.424 | 0.424 | 41 | 179 | `765e02d1f59e…` | 5 | static |
| calc.stability | REAL | REAL | 0.859 | 0.859 | 19 | 96 | `81c13dfcb013…` | 5 | executed |
| dev.gate_5415 | WITHHELD | WITHHELD | 0.524 | 0.467 | 0 | 0 | `06028cd0d45e…` | 4 | static |
| dev.git | WITHHELD | WITHHELD | 0.537 | 0.537 | 0 | 0 | `836ae022a6e8…` | 3 | static |
| dev.logs | WITHHELD | WITHHELD | 0.553 | 0.495 | 0 | 1 | `9ccf539abb25…` | 4 | - |
| dev.nginx_audit | WITHHELD | WITHHELD | 0.602 | 0.555 | 0 | 0 | `a49e288a9f14…` | 3 | - |
| dev.probe | WITHHELD | WITHHELD | 0.467 | 0.467 | 3 | 14 | `1eda59d5bb61…` | 4 | static |
| dev.restart | WITHHELD | WITHHELD | 0.524 | 0.467 | 0 | 0 | `1e95cba99e5c…` | 4 | static |
| dev.services | WITHHELD | WITHHELD | 0.537 | 0.537 | 0 | 0 | `23116d11562c…` | 3 | static |
| geometry.generate | WITHHELD | WITHHELD | 0.431 | 0.431 | 16 | 110 | `fab417461842…` | 5 | static |
| geometry.hydro_curves | WITHHELD | WITHHELD | 0.467 | 0.467 | 1 | 12 | `d2c8cb1f66dc…` | 4 | static |
| geometry.list_models | WITHHELD | WITHHELD | 0.524 | 0.467 | 0 | 13 | `c75142a5e81d…` | 4 | static |
| geometry.verify_official | WITHHELD | WITHHELD | 0.467 | 0.467 | 2 | 9 | `c8bf24dd58d6…` | 4 | static |
| gov.stats | WITHHELD | WITHHELD | 0.467 | 0.467 | 3 | 90 | `57d22239044d…` | 4 | static |
| knowledge.docs | WITHHELD | WITHHELD | 0.469 | 0.467 | 1 | 24 | `47074e89d886…` | 4 | static |
| methods.computation | WITHHELD | WITHHELD | 0.439 | 0.431 | 0 | 3 | `2a490e8fc225…` | 5 | static |
| misc.stubs | WITHHELD | WITHHELD | 0.467 | 0.467 | 0 | 1 | `17a25a34d0d7…` | 4 | static |
| propeller.open_water | MOCK | MOCK | 0.756 | 0.756 | 4 | 27 | `2a843c6e6c86…` | 5 | static |
| shiphull.generate | WITHHELD | WITHHELD | 0.495 | 0.495 | 2 | 7 | `3369985fe6d9…` | 4 | - |
| stdmodels.lookup | WITHHELD | WITHHELD | 0.467 | 0.467 | 6 | 6 | `c4d15377698a…` | 4 | static |

### 3.3 产物与审计链

| 产物 | sha256 |
|---|---|
| artifacts/capability_ledger_auto/eval_validity_p399w2.json | `ef3614ff42801b8c9d3b9ddcbade2e12f54939909a5e7319bf62850845ae2f81` |
| artifacts/capability_ledger_auto/eval_validity_p399w2_baseline.json | `eb6140f2a40458bfeb4d004e3b52792638ce27360d351bcb697e7f09df8f5986` |
| artifacts/capability_ledger_auto/ledger_audit_p399w2.json | `ba7b0654814ace8f6e96fc19769cba4b4b8877012e6f29d0cf3bae7d96a692cc` |
| artifacts/capability_ledger_auto/ledger_audit_p399w2_baseline.json | `e54f3a1f3cd30b2a81df3fbc2b8eeb4374a50b45e20ae2ab84b11e5bbfc6a131` |

每个 JSON 旁车 `.sha256` 即文件摘要；台账行内 `record_digest` 可用
`fingerprint.record_digest()` 独立复算。

## 4 结果图

![confusion](figures/fig1_confusion_matrix.png)

图 1：21 例带标注算例的混淆矩阵，(a) 修复前（代码只扫 `src/`），(b) 修复后。
唯一的对角外格子是 `REAL→WITHHELD`（`backend_root` 变体）。

![recall](figures/fig2_per_level_recall.png)

图 2：逐级召回前后对照。REAL 召回 0.50 → 1.00，其余四层维持 1.00（修复没有
用牺牲其它等级的方式来换 REAL 召回）。

![conf](figures/fig3_confidence_information.png)

图 3：置信度不再是常量。左：平台 21 项能力的等级内分布（黑=均值，红虚线=旧常量）；
右：算例 21 例。同一等级内出现离散度，即"证据强度"这一维终于进入了这个数字。

## 5 注意点（踩过的坑 / 局限）

1. **宽模式 + 大仓 = 有界扫描**。`Cf`/`Rt` 这类短模式在 958 个文件里命中 900+，
   全量 `ast.parse` 在共享控制面（load average≈260）单能力就要 20 s。因此引入
   索引 + 三档预算（命中 ≤300 条、AST ≤80 文件/能力、弱证据 ≤120 行/能力），
   被截断/跳过的数量写进 `scan_notes` 与 `truncated_by_kind`，不静默。
   代价：宽模式能力的"实质实现计数"是下界。
2. **多根扫描不改变平台等级（0/21）**。这是本节点最重要的诚实结论：平台的
   等级瓶颈不是"看不到代码"，而是"没有可升级的验证证据"——21 项里 19 项在
   自身审计链里被裁为 WITHHELD/UNKNOWN，判级器 fail-closed，不因证据变多而
   自动升账。想真正升账，要补的是执行证据与裁决，不是扫描根。
3. **阻力预报（`calc.resistance`）仍判 WITHHELD**。节点目标里的"已知 REAL 能力
   应判 REAL"用**带真值算例**验证（truth=REAL 三复本全中）；平台上的
   `calc.resistance` 判 WITHHELD 是正确行为：即便强制执行 4 个测试文件
   （68 通过 / 8 失败，通过率 89.5%），其审计链最新裁决仍是
   `docs/verification/inline_resistance_audit_2026-11-01.md` 的 WITHHELD，
   机器不得越过自己的诚实审计去升账。
4. **MOCK 标记主导性阈值（0.5）是启发式**。它把"A/B 测试里的 `random.random`"
   与"整条能力的实现都是随机数"区分开，但阈值本身没有理论最优值；被标
   `mock_marker_suspicion` 的能力（ai.predict, geometry.generate）不是清白证明，
   只是"证据不足以下 MOCK 判决"。台账里 `propeller.open_water` 的 MOCK 来自
   两篇独立审计裁决，与标记规则无关。
5. **测试证据依赖 pytest 子进程**。执行预算（文件数/超时/总预算）用尽时，
   认证降级为 static 并如实记录；static 不得冒充执行证据（`require_executed_tests_for_real`）。
6. **指纹覆盖"扫描到的证据"，不覆盖全仓**。叶子只来自注册表下发的模式命中，
   未被模式覆盖的代码不在指纹里——指纹防的是"已登记证据被偷改"，不是"漏登记"。

## 6 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

基准 = 带标注算例的**植入真值**（21 例，构造即真值），误差 = 误判率。

| 关键量 | 修复前 | 修复后 | 相对基准的误差 | 是否 <3% |
|---|---|---|---|---|
| 判级准确率 | 0.8571 | 1.0000 | 0.1429 → 0.0000 | 修复前否（14.3%），**修复后是（0.0%）** |
| 逐级召回（REAL） | 0.5000 | 1.0000 | 0.5000 → 0.0000 | 修复前否，**修复后是** |
| 逐级召回（其它四层） | 1.0000 | 1.0000 | 0.0000 | 是（前后均是） |
| `backend_root` 变体命中 | 3/6 | 6/6 | 50% → 0% | 修复前否，**修复后是** |
| 台账 `record_digest` 复算一致 | — | True | 0 行不一致 | 是 |
| 平台 21 项等级变化 | — | 0/21 | 与"多根扫描会改变平台等级"的预期不符 | 不适用（见 §5.2，假说被否定） |

诚实边界：算例是**合成**的，样本量 21，不是统计意义上的总体估计；
它在测量"判级器对已知证据形态的识别能力"（效度），不是在估计真实仓库的分布。
真实仓库上的结论只有 §3.2 那张表，且没有任何一项 REAL 是本节点新造的。
