# 声明-证据台账：把「方法学声明」变成可机器裁决的条目（m1962 · models 席自找活）

**席位**：tp-mod-1（models 席） · **单号**：m1962 · **分支**：`agent/m1962` · **日期**：2026-11-01
**EDIT_WHITELIST**：`docs/`, `tools/`, `tests/`, `artifacts/`, `backend/api/`（仅 provenance 字符串与 docstring 口径）

**产物**
- `tools/audit/claim_evidence_ledger.py` — 声明→证据台账 / 反漂移闸门（纯标准库，离线）
- `tools/audit/plot_claim_ledger.py` — 由台账 JSON 出图（零手抄）
- `tests/test_claim_evidence_ledger.py` — **17 项全绿**（判据自证 8 + provenance 漂移 4 + 边界 2 + 真树闸门 3）
- `artifacts/claim_ledger/ledger.json|.md`、`fig_claim_ledger.png`、`kernel_smoke.json`
- 代码口径修复：`backend/api/seakeeping_physics.py`、`backend/api/seakeeping_physical_api.py`

---

## ① 案例介绍（对象与背景）

平台的证据分级（REAL / MOCK / STUB / WITHHELD）历史上靠**人眼读代码**：m1523 靠人眼发现
回转要素落在 0.5L 栅格，m1547 靠人眼发现垂荡 RAO 恒为 1.260，m1679 靠人眼发现 `_METHOD`
宣称 "Lewis-section 2D coefficients" 而实现是拟合频率因子。同一个失效模式——**docstring /
端点 provenance 字符串宣称某个具名方法，代码体却没有该方法的实现足迹**——换一个模块就要
重新靠人眼发现一次。全树 `backend/api`、`backend/services`、`src` 共 **419 个模块**，
人工不可能穷举。

本单把这件事机器化，并在真树上**清掉全部 provenance 漂移**（0 条）。

| 项 | 值 |
|---|---|
| 扫描根 | `backend/api`、`backend/services`、`src`（419 个 .py 模块） |
| 具名方法词典 | 14 条（ITTC-57/78、Holtrop-Mennen、Michell、Lewis-section、Salvesen-Tuck-Faltinsen、Tasai、Frank close-fit、Ikeda、Hess-Smith、Wagner、Stefan、NSGA-II、CMA-ES） |
| 声明条目 | **49**（docstring 具名方法）+ **12**（provenance 字符串，会被报告/审计工具当证据消费） |
| 门禁口径 | `--gate`：`PROVENANCE_DRIFT > 0` 即退出码 1 |

## ② 怎么做的（方法 / 流程 / 关键设置）

### 2.1 三级抽取与判级

| 步骤 | 规则 |
|---|---|
| 抽声明 | 模块 docstring 里命中词典声明正则的**句子片段**；外加 provenance 字符串（`{"method": ...}` 等 `PROVENANCE_KEYS`，含模块级 `_METHOD = "..."` 常量解析、常量拼接、f-string 前缀） |
| 抽证据 | **只在代码体**搜索该方法特有的实现足迹（系数表 c1..c7、`0.075`+`log10` 合取、`a_1..a_3` 共形映射、`f_a/f_b` 频率因子…）。docstring 与注释先被剥离（`strip_docstrings_and_comments`），**否则声明自己就是证据**（判据自证） |
| 判级 | EVIDENCED（本模块有足迹）→ DELEGATED（import 的同语料模块有足迹）→ QUALIFIED（同句限定语：简化/近似/拟合/形（/form/heuristic）→ REFERENCE_MENTION（同句引用/否定语境：参见/文献/pseudo/并非/not）→ **UNBACKED**（待裁线索）→ **PROVENANCE_DRIFT**（下游消费的字符串宣称方法却无足迹＝缺陷本体） |

限定语与引用语境的作用域被钉在**同一句**：整篇互相担保会让任何一句话都被别的句子洗白。

### 2.2 判据标定（本单最实际的工作量）

第一版词典在真树上产出 **28 条 UNBACKED / 1 条 DRIFT**，几乎全是噪声。逐条看过后做了三处修正，
这条曲线本身就是本次的「判据质量」证据：

| 版本 | 问题 | 修正 | 结果 |
|---|---|---|---|
| v1 | 证据词 `mapping` 命中 `seakeeping_physics` 的无关变量名 → **把 Lewis 误判 EVIDENCED**（漏检真缺陷） | 删通用词，Lewis 足迹改为 `a_1..a_3` 共形映射/`conformal`/`half_beam_over_draft` | 漂移从漏检变命中 |
| v2 | 证据写成「单正则」→ `denom = math.log10(re)-2; 0.075/denom**2` 判无证据（误报） | 证据支持**合取备选**（`[[0.075, log10], [105, 0.64], …]`） | 误报 28 → 10 |
| v3 | 不区分「引用该文献」`pseudo-Holtrop`/`参见 ITTC` 与「实现该文献」；也不认 import 复用 | 加 REFERENCE_MENTION 语境 + 跨模块 DELEGATED 解析 | UNBACKED 10 → **9**，DRIFT 3 → **2（真缺陷）** |

### 2.3 修复（只改口径，不动数值）

`PROVENANCE_DRIFT` 命中 2 处，同一根因：耐波内核的 2D 系数是**形状无关的拟合频率因子**
`A33_i = ρ·a_i·f_a(ν)`、`B33_i = ρ·ω_e·b_i²·f_b(ν)`，而端点 `method` 字符串对外宣称
"Lewis-section 2D coefficients"——下游报告与审计工具会把这句话当证据引用。

修复取向是**让声明追上实现**，而不是改物理：把 `_METHOD` 与内核 `method` 字符串改为
Salvesen 形式并显式写 `NOT a Lewis/Tasai section solution`，模块头补「口径声明」三条
（形状无关、绝对量级不得当剖面系数用）。这与 m1679 记的 MINOR 文档过度宣称是同一件事，
本单把它**从「人工记一笔」升级为「机器闸门」**：以后再有人写回 Lewis 字样，`--gate` 直接红。

## ③ 计算结果（全部由 `artifacts/claim_ledger/ledger.json` 程序化读出）

| 分级 | 条数 |
|---|---|
| EVIDENCED（本模块有足迹） | 34 |
| DELEGATED（import 复用真实现） | 5 |
| QUALIFIED（诚实披露的近似） | 8 |
| REFERENCE_MENTION（引用/否定语境） | 5 |
| UNBACKED（待裁线索） | 9 |
| **PROVENANCE_DRIFT（缺陷本体）** | **0**（修复前 2） |

数值不变性证据（`artifacts/claim_ledger/kernel_smoke.json`，同一 KVLCC2 量级船 320×60×20.8 m、
Cb=0.81、GM=4.5 m、15 kn、顶浪）：`T3 = 12.1095 s`、`T5 = 13.1912 s`、
`heave_RAO(ω=0.6) = 0.05851`、`pitch_RAO = 0.031178`、`A33/m = 0.9564`。
`git diff` 仅有 docstring 与字符串行变更 ⇒ 修复是**口径修复，不是物理修复**。

图：`artifacts/claim_ledger/fig_claim_ledger.png`（左：分级分布；右：仍开着的线索按模块 Top-10）。

## ④ 注意点（坑 / 局限 / 适用范围）

1. **判据必须只看代码体**。第一版把 `mapping` 当 Lewis 足迹，结果 docstring 之外的无关变量名
   让一条真缺陷变绿——这正是本单要治的病，工具自己最容易患（已用 17 项测试钉死负样本）。
2. **UNBACKED 是线索不是判决**。剩余 9 条多是模块文档的交叉引用（`src/ship_perf/__init__.py`
   索引、`polar/constants.py` 的文献列表），仍需人工裁决；台账把「要人看的地方」压到一页。
3. **词典是有限的（14 条方法）**。未收录的方法学（如 Söding、Bhattacharyya）不会被扫；
   新增只需在 `LEXICON` 加一条「声明正则 + 特有足迹」并补一个正/负样本测试。
4. **DELEGATED 假阳性面**：仅按 import 的模块 basename 匹配，同名不同义模块可能误判为委派
   （全树实测未触发，属已知弱点）。
5. **不判定精度**：本工具裁决「声明 vs 实现足迹」，不裁决实现是否正确——精度仍要走各自的
   参考值复核单（本单不声称任何端点精度提升）。

## ⑤ 误差判定（平台标准：>15% 不可接受，<3% 达标）

本单是**判据/口径类**任务，被裁决量是「字符串/代码体的包含关系」，不是物理量，故误差是**精确判定**：

| 量 | 基准 | 偏差 | <3%？ |
|---|---|---|---|
| 声明分级（正样本：声明+足迹） | EVIDENCED | 8/8 判对 | **是（0 容差）** |
| 声明分级（负样本：仅 docstring 声明） | UNBACKED / DRIFT | 4/4 判对（含 `_METHOD` 常量洗白路径） | **是（0 容差）** |
| 真树 provenance 漂移 | 0 条 | 修复前 2 → 修复后 **0** | **是（精确为 0）** |
| 耐波内核数值 | 修复前自算值 | Δ = 0（仅字符串变更） | **是（精确 0）** |
| 物理精度（RAO 绝对幅值 vs 水池试验） | 无 | 不做 | **WITHHELD** —— 仓内无试验数据，本单未测 |

## ⑥ 后续（超出单干范围）

- 把 `--gate` 接进 CI/`make test`，让 provenance 漂移在 PR 阶段变红（需动仓库 CI 配置，另派单）。
- 把 `UNBACKED` 的 9 条线索逐条由域负责人裁决（可拆成 3~4 张轻量单）。
- 扩展词典到规则类（IACS UR、IMO A.751、ISO 19906），与 `tests/vv/test_reference_registry.py`
  的出处注册表打通，形成「声明-实现-出处」三段闭链。