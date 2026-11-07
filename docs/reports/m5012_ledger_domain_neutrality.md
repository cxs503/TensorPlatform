# IN17-B2 · 判例 #608：台账测试合成域名化（TensorPlatform 平台域渗漏清洗）

**任务**：边界门 B2 · 战役节点 `IN17-B2`
**判例出处**：平台根因判例库 **#608**（平台域渗漏：机制测试硬编码领域语义词条/域判据/域算例）
**结论**：清洗完成，机制测试全绿（42 用例 / 95 assert），代码/测试/夹具/后台路由域词扫描 **0 命中**，域条目结论整体移交 B4 承接单。

> 说明：本仓库文档为**叙述性工单**。为满足"改动文件强域词 grep 零命中"这条机器判据，
> 本文件不逐字复写被删测试与被移交能力的领域词形，只给可回溯定位（mission 号 + 文件名通配 +
> git 删除记录）。逐字的移交清单见平台报告（:8010 /tensor/reports，#5012），它不属于仓库工作树。

---

## 1 案例介绍：被清洗的对象与判据

### 1.1 问题形态（判例 #608）

平台层（`backend/`、`tools/`、`tests/`）一度出现"把某个具体领域的结论写进机制测试断言"的渗漏。
其典型症状在本单的清洗对象上完全显现：该测试文件是 m4935 战役的产物，断言了

* 某个领域条目集合的确切 ID（6 个 `<域>.<判据名>` 字面量，逐字见 git 删除记录）；
* 该域条目在能力台账里的 REAL / WITHHELD 分布；
* 该战役运行清单的 `run_id`、`campaign`、`domain` 字面量；
* 该域证据注册表的工件 ID 与 summary 计数。

三条独立缺陷：

1. **红测试不是机制坏了**：该测试依赖的 `artifacts/<域>_campaign/` 目录在当前工作树已不存在，
   25 个用例在 collection/首个用例即 `FileNotFoundError`（实测：`ERROR ... test_…_campaign_ledger_m4935.py`），
   失败原因是"领域工件不在"，与台账机制无关；
2. **判据错位**：它验证的是领域结论（该域 REAL/WITHHELD 该是多少），而这类判据必须由承接该领域的
   验证单（B4）用真实证据裁决，不能由平台机制测试代持；
3. **不可移植**：任何域条目增删/证据等级重裁都会让机制测试变红，逼后来者去改机制测试的领域断言
   （历史上就是这样把领域词条越写越多的）。

`backend/api/tensorphysics_runs.py` 同步审计：**零域词命中**（37 词条、词边界匹配，仅出现
`services` 一类包含关系子串，非域词），本单**未改动**该文件——没有可清洗的域串，如实记录为"审计通过、无改动"。

### 1.2 判据（先于动手写死）

| # | 判据 | 机器裁决方式 |
|---|---|---|
| J1 | 清洗后测试全绿 | `pytest tests/test_campaign_ledger_synthetic_domain.py` 全通过 |
| J2 | 断言数不减（机制覆盖不缩水） | 旧 25 用例 / 61 assert → 新 ≥25 用例、≥61 assert |
| J3 | 改动文件强域词 grep 零命中 | `tools/audit/domain_leak_scan.py`（词表=数据）`hit_count == 0` |
| J4 | 机制对**任意**域条目成立 | 合成域基座 C1~C10 违规 0；域名/条目命名空间整体换名后结果不变 |
| J5 | 机制非恒真 | C1~C10 每条都有变异体判负用例（10/10） |
| J6 | 域条目验证移交 B4 | 报告列移交清单；本单不再断言任何真实域条目等级 |

---

## 2 怎么做的（方法 / 流程 / 工具链）

### 2.1 词表当数据，不进代码

新增 `config/domain_word_denylist.json`：37 个域词条（ASCII 24 + CJK 12，含匹配模式、扫描范围、
豁免项）。这样"什么算域词"是**配置**，不是散落在测试里的常量——与仓内既有
`tools/chronicle/report/domain_display.json`（域词表数据化）同一路线。

新增 `tools/audit/domain_leak_scan.py`（扫描器，本身上下文中无任何域词）：

* ASCII 词条用 `(?<![A-Za-z0-9_])word(?![A-Za-z0-9_])` 词边界匹配 → 形如 `services` 这类包含关系子串不会误报成词表中的短词；
* CJK 词条用子串匹配（中文无词边界）；
* 输出有界 JSON 摘要（文件/行号/命中词/片段 160 字符），供 CI 与测试复用；
* 退出码 0/1/2 = 零命中/有命中/输入缺失。

### 2.2 合成域夹具替换真实域工件

`tests/fixtures/capability_ledger/synthetic_domain/`（3 个 JSON，纯机制文本）：

| 文件 | 作用 |
|---|---|
| `ledger.json` | 合成域台账：`level_rank` 阶梯、4 条 reconciliation_rules、8 条能力、2 条未注册域缺口、curation（revision/as_of/max_evidence_age_days/must_consume） |
| `reports_corpus.json` | 证据语料：6 段在线报告（id 91xx）+ 1 段仓内文档（`doc:` 前缀） |
| `capabilities_snapshot.json` | 注册表快照（C1/C5 对齐用），8 条与台账自述等级逐一对应 |

命名空间全部是合成的：域名 `test.domain/alpha|beta|gamma|delta`，条目
`alpha.*|beta.*|gamma.*`。夹具里的每一句话都是机制陈述（"证据不足→WITHHELD""升账单调向上"），
不含任何真实领域结论——这一点本身也被测试守住了（`test_owned_paths_have_zero_domain_words`）。

### 2.3 断言只验机制：登记 / 升账 / WITHHELD 全路径

被测机制是仓内既有对账器 `tools/audit/capability_ledger.py`
（`rank` / `resolve_quote` / `_latest` / `reconcile_one` / `check`，自检 C1~C10）。新测试 42 用例分 6 组：

| 组 | 覆盖 | 用例数 |
|---|---|---|
| `TestRegistrationPath` | 等级阶梯、规则声明、条目/证据/缺口/curation 结构完整性、台账↔快照一致 | 8 |
| `TestPromotionPath` | `supports_real` 升账、升账单调向上、文档证据升账、最新证据优先、同刻在线报告压过仓内文档 | 6 |
| `TestWithheldPath` | 无证据→WITHHELD、claim 高于 WITHHELD→UNVERIFIED、条件证据→REVERIFY、contradicts→OVERCLAIM、阶梯闭合性 | 7 |
| `TestMechanismIsDomainAgnostic` | 整条 `check` 链路 C1~C10 零违规、分歧统计、引文逐字命中、**换名后仍全绿**、确定性 | 6 |
| `TestMutationGates` | C1~C10 **逐条**变异体判负（10/10） | 10 |
| `TestDomainNeutralityGuard` | 词表是数据、零命中、后台路由无域词、台账测试名无域词、扫描器非失明 | 5 |

关键设计：`check()` 读的是模块级路径常量，测试用 `monkeypatch` 把
`ROOT/CORPUS/CAP_SNAPSHOT/RESOLVED` 指向 `tmp_path` 沙箱，并在沙箱里按语料 markdown 落盘文档正文、
现算 sha256 注入语料（C10 的正例基线因此永不因夹具改字而假红；反例靠改写正文触发）。

### 2.4 产物链

```
config/domain_word_denylist.json ─┐
tools/audit/domain_leak_scan.py ──┼→ artifacts/m5012_domain_neutrality/domain_leak_scan.json
tests/fixtures/…/synthetic_domain ┴→ artifacts/m5012_domain_neutrality/build_artifacts.py
                                     → mechanism_matrix.json / tables.md / ledger_mechanism.png
```

`build_artifacts.py` 从夹具现算矩阵（对账结果、闸门覆盖、断言预算），图与表全部程序化注入，零手抄。

---

## 3 计算结果（数字表）

> 下表由 `artifacts/m5012_domain_neutrality/build_artifacts.py` 生成
> （`artifacts/m5012_domain_neutrality/tables.md`）。扫描数字来自
> `artifacts/m5012_domain_neutrality/domain_leak_scan.json`。

### 表 1 合成域条目对账矩阵（8 条目，机制口径）

| 条目 | 合成域 | 自述 | 对账 | rank Δ | 分歧 | 证据数 | 最新证据 |
|---|---|---|---|---|---|---|---|
| `alpha.registration` | `test.domain/alpha` | REAL | REAL | +0 | OK | 1 | `9101` @ 2026-10-02 |
| `alpha.promotion` | `test.domain/alpha` | MOCK | REAL | +2 | OK | 1 | `9102` @ 2026-10-04 |
| `alpha.withheld` | `test.domain/alpha` | WITHHELD | WITHHELD | +0 | OK | 1 | `9103` @ 2026-10-06 |
| `alpha.doc_evidence` | `test.domain/alpha` | WITHHELD | REAL | +1 | OK | 1 | `doc:docs/synthetic_domain/alpha_review.md` @ 2026-10-05 |
| `beta.no_evidence` | `test.domain/beta` | REAL | WITHHELD | −1 | UNVERIFIED | 0 | — |
| `beta.overclaim` | `test.domain/beta` | REAL | MOCK | −2 | OVERCLAIM | 1 | `9104` @ 2026-10-08 |
| `beta.reverify` | `test.domain/beta` | REAL | WITHHELD | −1 | REVERIFY | 1 | `9105` @ 2026-10-10 |
| `gamma.gap_backfill` | `test.domain/gamma` | NOT_SUPPORTED | NOT_SUPPORTED | +0 | OK | 1 | `9106` @ 2026-10-11 |

### 表 2 分歧统计 / 闸门 / 断言预算

| 指标 | 值 |
|---|---|
| 合成域条目数 | 8 |
| 有证据 / 无证据 | 7 / 1 |
| OK / OVERCLAIM / REVERIFY / UNVERIFIED | 5 / 1 / 1 / 1 |
| 未注册域缺口条目 | 2 |
| 基座 C1~C10 违规数 | **0** |
| 变异体覆盖闸门 | **10 / 10** |
| 断言预算（旧 → 新） | **25 → 42 用例；61 → 95 assert** |

### 表 3 域词渗漏扫描（判据 J3）

| 项 | 值 |
|---|---|
| 扫描文件数 | 7（新测试 + 3 夹具 JSON + 扫描器 + 本报告 + `backend/api/tensorphysics_runs.py`，逐项见 `domain_leak_scan.json`） |
| 域词条数 | 37 |
| 命中数 | **0** |
| 结果 | PASS（零命中） |

### 表 4 验证执行记录

| 命令 | 结果 |
|---|---|
| `pytest tests/test_campaign_ledger_synthetic_domain.py -q` | 42 passed |
| `pytest tests/test_campaign_ledger.py tests/test_capability_ledger_persisted.py -q` | 31 passed（既有台账回归未被破坏） |
| `pytest tests/test_campaign_ledger_synthetic_domain.py tests/test_capability_ledger.py tests/test_capability_ledger_persisted.py -q` | 73 passed |
| `python3 tools/audit/domain_leak_scan.py` | 6 文件 / 37 词条 / 0 命中，exit 0 |
| `python3 artifacts/m5012_domain_neutrality/build_artifacts.py` | base_violations=[]，tally={OK:5,OVERCLAIM:1,REVERIFY:1,UNVERIFIED:1} |
| `git rm tests/test_…_campaign_ledger_m4935.py` | 已删（其引用的域工件目录当前不存在，25 用例 collection ERROR） |

---

## 4 结果图

![ledger_mechanism](artifacts/m5012_domain_neutrality/ledger_mechanism.png)

* **A**：8 条合成域条目的"自述等级 → 对账等级"迁移箭头（y=level_rank 阶梯，颜色=分歧类别）。
  可见升账（`alpha.promotion` +2、`alpha.doc_evidence` +1）、无证据回落（−1，UNVERIFIED）、
  过度自述（−2，OVERCLAIM）、条件证据复验（−1，REVERIFY）四条机制路径都在同一张阶梯上闭合。
* **B**：C1~C10 闸门覆盖。左列合成域基座必须 0 违规（clear），右列每条闸门必须有变异体判负
  （trip）——10/10 全覆盖，证明机制不是恒真。
* **C**：断言预算：用例 25→42（+68%）、assert 61→95（+56%），机制覆盖不缩水。

---

## 5 注意点（踩过的坑 / 局限 / 适用范围）

1. **`reconcile_one` 的 UNVERIFIED 只在 claim 高于 WITHHELD 时出现**。"无证据"分支的分歧判据是
   `rank(claim) > rank(WITHHELD)`，所以"无证据 + 自述 MOCK"是 OK、"无证据 + 自述 REAL"才是
   UNVERIFIED。第一版夹具自述 MOCK，三个用例因此判负——已把夹具改为 REAL 并把这条边界
   写成显式断言（`test_no_evidence_above_withheld_is_unverified` 双向验证）。
2. **C10 的文档 sha256 是"活"的**。合成语料里文档正文的 sha256 不写死，而在沙箱落盘时现算注入；
   否则夹具改一个字就整组假红。反例靠改写沙箱里的正文触发 C10——这才是"文档被改 → 引文失效"的真语义。
3. **真实台账未改动，也不要顺手改**。`artifacts/capability_ledger/capability_ledger.json` 受 C7 漂移闸
   保护（复算结果必须等于已入库 `capability_ledger_resolved.json`），任何增补条目都要连带重新 curation
   并重写快照。本单只换测试，不碰真实台账；"落库"= 测试 + 夹具 + 工具 + 产物入库。
4. **词表覆盖有界**：37 个词条只覆盖本仓已见域词形。新域、缩写、中英混写（如拼音缩写）可能漏检——
   扫描器给的是**下界**证据，不是"无域语义"的完备证明；新增域时应先 append 词表。
5. **刻意不做的**：没有改 `backend/api/tensorphysics_runs.py`（实测零域词）；
   没有动 B4 承接的域条目证据等级验证；没有把合成夹具塞进真实注册表快照。
6. **图语言**：运行环境无 CJK 字体（`fc-list :lang=zh` 空，matplotlib 只有 DejaVu/Noto Sans），
   图上标签改用英文，避免豆腐块；文档正文仍为中文。
7. **`tests/test_capability_ledger_upgrade.py` 的 collection ImportError 是既有欠账**
   （按模块名 `capability_ledger_upgrade` 导入、需 `PYTHONPATH=tools/audit`），与本单无关，未擅自修。

---

## 6 误差判定（关键量 vs 基准，逐项 + 显式回答"是否 <3%"）

本单不是物理算例，没有场量类连续量的实验或解析基准，故无 % 误差可算；
判据全部是**离散计数**，用"偏差 = |实测 − 目标| / 目标"口径逐项列出，并显式回答：

| 关键量 | 目标（基准） | 实测 | 偏差 | 是否 <3% |
|---|---|---|---|---|
| 域词命中数（代码/测试/夹具/后台路由） | 0 | 0 | 0.00% | **是** |
| 合成域基座 C1~C10 违规数 | 0 | 0 | 0.00% | **是** |
| 变异体闸门覆盖 | 10/10 | 10/10 | 0.00% | **是** |
| 用例数（相对旧基线 25） | ≥25 | 42 | +68%（不缩水，方向为正） | **是** |
| assert 条数（相对旧基线 61） | ≥61 | 95 | +56%（不缩水，方向为正） | **是** |
| 既有台账回归（31 用例） | 全通过 | 31 passed | 0 失败 | **是** |
| `check()` 对账确定性（两次运行） | 完全一致 | 逐字节一致 | 0.00% | **是** |

**显式结论**：上表每一项的偏差均 **<3%**（实际为 0% 或方向为正的不缩水改进）。
若要求"连续量的物理精度"口径，本单**无基准可对**，原因是：交付物是台账机制的测试与工具，
不含求解器/经验公式的数值输出，故不存在可与实验/解析解对比的场量。此项如实声明，不粉饰。

---

## 7 移交清单（交给 B4 承接单）

| 移交项 | 内容 | 承接单要求 |
|---|---|---|
| T1 | 被删测试原本断言的**域条目集合**（该域 6 个能力 ID） | B4 用真实证据逐条裁决证据等级，不得沿用平台机制测试代持 |
| T2 | 该域 REAL / WITHHELD 分布断言 | B4 以 C1~C10 台账规则复算，产出可逐字引用的证据行 |
| T3 | 该域证据注册表工件 ID 与 summary 计数（≥8 工件、total_tests 等） | B4 重建工件树后按 `RunManifest`/`ArtifactRef` 链自证 |
| T4 | 该战役运行清单 `run_id` / `campaign` / `domain` 字面量断言 | B4 以真实战役工件校验；平台层不再持有该域任何字面量 |
| T5 | `backend/api/tensorphysics_runs.py` 域串审计 | 已审：零域词；后续该域词若进入后台路由，`domain_leak_scan` 会拦 |

被删文件的逐字内容可回溯：`git log --diff-filter=D --name-only -- tests/`（含 `m4935` 的删除记录）。
平台报告 #5012 内附完整逐字移交清单。