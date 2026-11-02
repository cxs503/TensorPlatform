# 能力证据台账 v2：门禁复活 + 仓内复核入账 + 防腐朽（m1811）

**任务** TP self-start m1811（models 席，自主工时）·**仓** TensorPlatform ·**分支** agent/m1811
**被测对象** `tools/audit/capability_ledger.py` + `artifacts/capability_ledger/capability_ledger.json`
**结论一句话**：m1567 立的「能力证据单一真值文件」在 HEAD 上是**死的**——台账工件从未入库，
`--check` 退出 2、15 条回归测试全红；且它只吃在线报告，于是**已合并的复核整批对它不可见**
（稳性在 m1564 受证合成后仍被钉在 MOCK）。本单把门禁拉活、把已合并复核入账，并加三条防腐朽判据。

## ① 案例：一个「真值文件」如何失效

平台有两套事实：`audit_capabilities` 自述的 21 项能力等级（无 HTTP 端点，只能快照）
与 :8010 报告里逐篇写下的裁决。m1567 把它们对账成一台机器可裁的台账（C1~C7 七条自检 +
变异体判负）。但两份失效同时发生：

| 失效 | 机器证据（可复算） |
|---|---|
| 台账工件未入库 | HEAD 上 `artifacts/capability_ledger/capability_ledger.json` 不存在 → `--check` 打印「台账不存在」退出 1；`tests/test_capability_ledger.py` = **2 failed + 13 error**（15 红） |
| 证据源单一 | 语料库 `reports` 只有 10 篇在线报告（1128…1144，全部 2026-11-01 07:00–07:22 UTC）；m1564/m1584/m1590/m1493 等**已合并复核文档完全不在视野** |

后果是实打实的误导：台账对 `calc.stability` 记 `OVERCLAIM (MOCK)`，而 m1564 已经在**有精确解的
箱型驳船**上把服务层做成受证合成（域内误差 ≤1e‑3 %）。对外展示的「证据等级」因此永久停在旧世界上。

## ② 怎么做的（四步，全部程序化）

1. **复活门禁**：从 m1567 工作树取回台账工件入库（`artifacts/capability_ledger/capability_ledger.json`），
   重跑 `--check --write` 生成 `_resolved.json` + `_table.md`，15 条红测试转绿。
2. **语料库 v2（第二路证据）**：`tools/audit/build_capability_corpus_docs.py` 把仓内**已合并复核文档**
   作为 `doc:<path>` 证据注入语料库，每篇带 `sha256`（读字节算）、`blob`（`git rev-parse HEAD:path`）、
   `as_of`（`git log -1 --format=%cI`，**不用正文里手写日期**——手写日期无法机器核验）。
3. **重裁**：`tools/audit/curate_capability_ledger_m1811.py` 把 5 篇文档挂到对应能力/覆盖缺口上，
   引文一律正则 + 逐字命中（C3）；并把所有在线证据的 `as_of` 归一为语料库 `created_at`，
   让「最新证据」按**时间戳**排序（同刻时在线报告优先于仓内文档，保守）。
4. **防腐朽三条新判据**（这正是 v1 死掉的原因，必须变成机器判据）：
   C8 证据新鲜度（带分歧的能力，最新证据不得超过 `max_evidence_age_days`）；
   C9 `must_consume`（已合并复核文档必须入账，或显式 waiver + 理由）；
   C10 文档正文 sha256 一致（文档被后续单改写 → 旧引文失效 → 强制重新 curation）。

## ③ 结果（数字全部由工具写入 artifacts，零手抄）

对账汇总（`artifacts/capability_ledger/capability_ledger_resolved.json`）：
能力 21 项，有证据 3 / 无证据 18，
分歧 `OVERCLAIM 2 · REVERIFY 0 · `
`UNVERIFIED 15 · OK 4`，
覆盖缺口 5 个，证据行 10，违规 0。

### 3.1 三条带证据的能力：v2 重裁

| 能力 | 自述 | 对账 | 分歧 | 最新证据 | 证据时刻 |
|---|---|---|---|---|---|
| `propeller.open_water` | REAL | **MOCK** | OVERCLAIM | `doc:docs/verification/PROPULSION_OPENWATER_M1584.md` | 2026-11-01T17:01:54+08:00 |
| `calc.stability` | REAL | **REAL** | OK | `doc:docs/reports/m1564_stability_adjudication.md` | 2026-11-01T15:56:14+08:00 |
| `calc.resistance` | REAL | **WITHHELD** | OVERCLAIM | `doc:docs/verification/inline_resistance_audit_2026-11-01.md` | 2026-11-01T17:37:17+08:00 |

裁决迁移（与 v1 对比，v1 数值取自 m1567 已入库快照）：

| 能力 | v1 | v2 | 迁移原因（逐字引文见 resolved.json 的 `line`/`line_sha256`） |
|---|---|---|---|
| `calc.stability` | OVERCLAIM (MOCK) | **OK (REAL)** | m1564 受证合成：域内三实现 vs 箱型驳船独立积分基准差 1e‑6 % 级，正典全程 ≤1e‑3 %，域外由 `atan(2·min(T,D−T)/B)` 解析截断 |
| `calc.resistance` | REVERIFY (WITHHELD) | **OVERCLAIM (WITHHELD)** | inline 审计（2026‑11‑01）10 条判据 12 案例：FAIL 8 / PASS 1 / WITHHELD 1；湿表面积 −20.9 %、SUBOFF Ct 到 +31.5 % → 自述 REAL 是被**推翻**，不是「待复核」 |
| `propeller.open_water` | OVERCLAIM (MOCK) | **OVERCLAIM (MOCK)** | m1584 用不依赖文献数值的硬判据再判：端点自报 η₀ 越过动量理论上限，最大越界 0.1421 → MOCK 维持，且证据更强 |

### 3.2 覆盖缺口：从 3 个到 5 个（诚实变差）

v2 把注册表**看不见的学科**补进台账，因此缺口数从 3 升到 5——这是如实记账，不是退步：

| 域 | 最新证据判定 | 证据 | 建议动作（节选） |
|---|---|---|---|
| 耐波/seakeeping | REAL | `1139` | 在注册表新增 seakeeping.rao（初始 WITHHELD，待外部 RAO 基准复核后升 REAL）。… |
| 绿色航行/green-ship | WITHHELD | `1134` | 新增 green.eedi、green.cii（等级按报告 1134 的三层拆分入账，不得合并成单个 REAL）。… |
| 基准参照数据/benchmark-reference | WITHHELD | `1137` | 新增 data.benchmark_integrity（canonical v3.0，WITHHELD 行逐条列出）。… |
| 结构/structure | MOCK | `doc:docs/reports/m1590_structure_verification_debt.md` | 注册表新增 structure.buckling（可 REAL）与 structure.fatigue / struct… |
| 振动噪声/vibration-noise | NOT_SUPPORTED | `doc:docs/audits/vibration_noise_domain_audit.md` | 把 backend/api/domain/ship_vibration.py 的 router 挂进主站并重启（属运维动… |

### 3.3 入账的仓内复核文档（5 篇，带哈希）

| 证据 id | as_of | blob | sha256[:16] | 裁决了谁 |
|---|---|---|---|---|
| `doc:docs/verification/PROPULSION_OPENWATER_M1584.md` | 2026-11-01T17:01:54+08:00 | `328e227f743b` | 88bc6b818cf81603 | 重裁 propeller.open_water：证据门 2/8，端点 eta0 越过动量理论上限（不依赖文献数值的硬判据） |
| `doc:docs/reports/m1564_stability_adjudication.md` | 2026-11-01T15:56:14+08:00 | `273f816e84fc` | 65552f94345b6498 | 重裁 calc.stability：域内 vs 箱型驳船精确解误差 ≤1e-3 %，域外由解析有效域截断 |
| `doc:docs/verification/inline_resistance_audit_2026-11-01.md` | 2026-11-01T17:37:17+08:00 | `bc97ac6bcdcd` | e294bbc3cc6c94ff | 重裁 calc.resistance：FAIL 8 / PASS 1 / WITHHELD 1，绝对精度仍不可判据 |
| `doc:docs/reports/m1590_structure_verification_debt.md` | 2026-11-01T17:09:35+08:00 | `b7e961bdcee1` | c5020a360c597559 | 覆盖缺口：结构域端点在跑而注册表 21 项无结构条目 |
| `doc:docs/audits/vibration_noise_domain_audit.md` | 2026-11-01T15:15:06+08:00 | `34173df3e1e9` | ac458f4acd3ecd05 | 覆盖缺口：振动噪声域在仓内已实现并解析复核，但注册表无条目、在线仍 NOT_SUPPORTED |

图 1：`artifacts/capability_ledger/fig_capability_ledger.png`（自述 vs 对账色阶矩阵 + 分歧计数）。
图 2：`artifacts/capability_ledger/fig2_ledger_v1_v2.png`（左：v1/v2 分歧分布；右：证据时间线，
星号=被采纳为「最新」，红=仓内文档、蓝=在线报告）。

## ④ 注意点 / 踩过的坑

1. **「最新」不能按日期字符串比**：v1 用 `(as_of 字符串, report_id)` 排序，而 `as_of` 全是
   `YYYY-MM-DD`（同一天并列），跨源比较必然失真。v2 一律带时间戳并显式规定同刻优先级
   （在线报告 > 仓内文档），否则「仓内自家文档压过第三方在线审计」会悄悄发生。
2. **仓内文档是自家写的**：所以它只能**升**能力等级的依据必须带独立基准（这里稳性用的是
   与平台代码零共享的箱型驳船精确积分），且同刻不压过在线审计。
3. **C10 会误伤**：任何后续单改写被引用文档的正文都会让台账破闸。这是设计意图
   （引文与正文脱钩比漏审更坏），代价是改文档必须重跑
   `build_capability_corpus_docs.py && curate_capability_ledger_m1811.py --write`。
4. **CURATION 单一真值**：`must_consume` 由 builder 的 `MANIFEST` 生成，不在两个文件里各写一份
   （否则改一处忘一处，正是 C9 要防的形态）。
5. **图内中文渲染为方框**：宿主无 CJK 字体，属环境问题，不影响数据。

## ⑤ 局限与适用范围（honest）

1. 本单**不新增任何物理算法**；它改变的是「平台的证据等级数字」这件事本身的可信度与新鲜度。
2. `calc.stability` 升 REAL 的适用范围**仅限壁式有效域内**（`φ ≤ atan(2·min(T,D−T)/B)`）；
   域外语义是「截断+报来源」，不是「更准」。
3. `calc.resistance` 仍是 OVERCLAIM：阻力通道**当前不可用于定量决策**；且 inline 审计同时发现
   **数据集侧**（Rt / Froude 恒等式 4/12 行不自洽）不可判据，绝对精度欠账未清。
4. 覆盖缺口 5 个（耐波/绿色/基准台账/结构/振动噪声）说明注册表仍是 21 项旧口径——
   `audit_capabilities` 无 HTTP 端点，本单仍只能以快照比对，无法机器读取真值。
5. 振动噪声域在线仍 404（仓内 REAL、未挂载），属运维动作，本单不动生产服务。

## ⑥ 误差判定（显式回答「是否 < 3%」）

| 关键量 | 基准 | 误差/一致性 | <3%？ |
|---|---|---|---|
| 台账复算 vs 已入库快照（C7 漂移闸） | `capability_ledger_resolved.json` | 逐字节 JSON 规范化后**完全一致** | 是（0 %） |
| 证据引文 vs 文档正文（C3） | 语料库 markdown | 正则命中率 100 %（10 条证据行 + 缺口证据全部命中，`line_sha256` 落库） | 是（0 %） |
| 文档正文一致性（C10） | 工作树字节 sha256 | 5/5 篇一致 | 是（0 %） |
| must_consume 入账率（C9） | 5 篇已合并复核文档 | 5/5 被引用，0 waiver | 是（0 %） |
| 回归测试 | 变异体判负 | 21/21 passed（含 C8/C9/C10 各自的变异体） | 是 |

## ⑦ 复现

```bash
python3 tools/audit/build_capability_corpus_docs.py            # corpus docs 段
python3 tools/audit/curate_capability_ledger_m1811.py --write   # 台账 curation
python3 tools/audit/capability_ledger.py --check --write        # C1~C10 + 刷新 artifacts
python3 -m pytest tests/test_capability_ledger.py -q            # 21 passed
python3 artifacts/capability_ledger/gen_fig2_v1_v2.py           # 图 2
```

## 附：自动生成的完整对账表

```
# 能力证据台账对账表（自动生成，勿手改）

- 能力数：21（有证据 3 / 无证据 18）
- 分歧：OVERCLAIM 2 · REVERIFY 0 · UNVERIFIED 15 · OK 4
- 未注册域缺口：5；证据行：10；违规：0

| 能力 | 域 | 自述 | 对账 | 分歧 | 最新证据 | 依据 |
|---|---|---|---|---|---|---|
| `propeller.open_water` | 推进 | REAL | **MOCK** | OVERCLAIM | doc:docs/verification/PROPULSION_OPENWATER_M1584.md | 最新证据 doc:docs/verification/PROPULSION_OPENWATER_M1584.md stance=contradicts → MOCK |
| `calc.stability` | 稳性 | REAL | **REAL** | OK | doc:docs/reports/m1564_stability_adjudication.md | 最新证据 doc:docs/reports/m1564_stability_adjudication.md 支持 REAL |
| `calc.resistance` | 阻力 | REAL | **WITHHELD** | OVERCLAIM | doc:docs/verification/inline_resistance_audit_2026-11-01.md | 最新证据 doc:docs/verification/inline_resistance_audit_2026-11-01.md stance=contradicts → WITHHELD |
| `geometry.hydro_curves` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.generate` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.list_models` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `geometry.verify_official` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `shiphull.generate` | 几何 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `stdmodels.lookup` | 数据 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `gov.stats` | 平台 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `knowledge.docs` | 平台 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.gate_5415` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.git` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.logs` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.nginx_audit` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.probe` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.restart` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `dev.services` | 开发 | REAL | **WITHHELD** | UNVERIFIED | - | 无证据 |
| `ai.predict` | AI | NOT_SUPPORTED | **WITHHELD** | OK | - | 无证据 |
| `methods.computation` | 方法 | NOT_SUPPORTED | **WITHHELD** | OK | - | 无证据 |
| `misc.stubs` | 杂项 | STUB | **WITHHELD** | OK | - | 无证据 |

## 注册表覆盖缺口

| 域 | 最新证据判定 | 缺口 | 动作 |
|---|---|---|---|
| 耐波/seakeeping | REAL（报告 1139） | 注册表 21 项里没有耐波能力条目，但平台已有耐波端点并有 2 份审计报告 → 注册表覆盖缺口（能力在飞而账上无条目）。 | 在注册表新增 seakeeping.rao（初始 WITHHELD，待外部 RAO 基准复核后升 REAL）。 |
| 绿色航行/green-ship | WITHHELD（报告 1134） | 注册表无绿色航行条目；报告 1134 交付了 EEDI/EEXI/CII 分层实现。 | 新增 green.eedi、green.cii（等级按报告 1134 的三层拆分入账，不得合并成单个 REAL）。 |
| 基准参照数据/benchmark-reference | WITHHELD（报告 1137） | 基准台账本身是能力判据的基石，注册表未列该能力；其自洽性已有独立审计。 | 新增 data.benchmark_integrity（canonical v3.0，WITHHELD 行逐条列出）。 |
| 结构/structure | MOCK（报告 doc:docs/reports/m1590_structure_verification_debt.md） | 注册表 21 项里没有结构能力条目，但平台有 4 个结构端点（板格屈曲/疲劳/板厚屈曲/极限强度），且 m1590 已做闭式基准复核 → 注册表覆盖缺口。 | 注册表新增 structure.buckling（可 REAL）与 structure.fatigue / structure.ultimate（记 MOCK），并按 m1590 的契约层建议让 actual_stress 缺输入时返回 WITHHELD 而不是 0.5 sigma_y。 |
| 振动噪声/vibration-noise | NOT_SUPPORTED（报告 doc:docs/audits/vibration_noise_domain_audit.md） | 注册表无振动噪声域条目；仓内 m1493 已实现梁/板固有频率、隔振传递率、板隔声、声级合成共 9 个解析端点并复核到 <3%，但**未挂进在线服务** → 在线仍 NOT_SUPPORTED。 | 把 backend/api/domain/ship_vibration.py 的 router 挂进主站并重启（属运维动作），挂载后本行寄存器新增 vibration.beam/plate、noise.panel_tl 并把在线等级升 REAL。 |

```
