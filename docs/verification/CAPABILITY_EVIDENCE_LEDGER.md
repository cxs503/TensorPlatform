# 能力证据台账对账（Capability Evidence Ledger）— mission 1567 / data 席

> 一句话：平台自述的 21 项能力等级里有 **2 项被自家审计报告直接推翻（OVERCLAIM）**、
> **1 项需要重新判定（REVERIFY）**、**15 项属"自述 REAL 但零证据"（UNVERIFIED）**，
> 另有 **3 个已被审计的学科在注册表上根本没有条目（覆盖缺口）**。
> 本单把这件事做成可机器裁决的单一真值文件 + 门禁 + 回归测试，而不是又一篇散落在正文里的判决。

## ① 案例背景与对象

平台侧两套"事实"长期并存、互不校验：

| 侧 | 载体 | 内容 | 缺陷 |
|---|---|---|---|
| 能力自述 | `audit_capabilities` 工具返回的 21 项清单 | `propeller.open_water = REAL`、`calc.stability = REAL` … | 无出处、无期限、不随审计报告更新 |
| 审计证据 | :8010 `GET /api/v1/tensor/reports/{id}` 的 10 篇近期报告 | 报告 1128 判敞水 MOCK、1136 判稳性 24 项 FAIL … | 结论埋在 markdown 正文，无人汇总 |

后果是实打实的：对外材料/看板读能力清单 → 看到 REAL → 而端点在解析基准上的误差早已
远超平台自定的 15% 门（例如部署态 R_T 偏高 224.5%，稳性 KB +33.33% / GM +61.54% /
GZ@20° +53.27%）。**本单不新增物理算法，而是把"证据等级"这件事变成可复算、可拦截的数据资产。**

补充事实（口径诚实）：`audit_capabilities` 的清单**没有对应 HTTP 端点** —— 在 :8001 上探测
`knowledge/capabilities`、`tensor/capabilities`、`platform/capabilities`、
`tensor/audit/capabilities`、`audit/capabilities`、`tensor/knowledge` 六个候选路径全部 404。
因此该清单只能以**快照**形式入库（`tests/fixtures/capability_ledger/capabilities_snapshot.json`，
provenance 写在快照里）。这也是本单提出的一条平台改进项：把注册表暴露成
`GET /api/v1/tensor/capabilities`，让"能力等级"本身也成为可复核的 API 对象。

## ② 方法：证据必须逐字可查，分歧必须显式成行

1. **台账**（`artifacts/capability_ledger/capability_ledger.json`）为 21 项能力逐条登记：
   `claimed_level`（注册表自述）+ `evidence[]`（报告 id / 日期 / 立场 / 断言等级 / `quote_match` 正则）+ `action`。
   注意：证据里**不存手抄引文**，只存正则；引文由工具在报告正文里实例化，并落盘
   `line_sha256`（前 16 位）—— 转述、记忆、幻觉都过不了这一关。
2. **对账规则**（台账 `level_rank` + `reconciliation_rules`，代码里唯一实现）：
   等级强度 `NOT_SUPPORTED(0) < STUB(1) < MOCK(2) < WITHHELD(3) < REAL(4)`；
   「最新证据」= `as_of` 最大、同日取 `report_id` 最大（后写覆盖先写）；
   `stance=contradicts` → 取该证据的 `asserted_level`，自述更强记 **OVERCLAIM**；
   `stance=conditional` → 记 **REVERIFY**；无证据 → 一律 **WITHHELD**，自述更强记 **UNVERIFIED**。
3. **七条自检**（`python3 tools/audit/capability_ledger.py --check`）：

   | 编号 | 判据 |
   |---|---|
   | C1 | 快照 21 项与台账逐项对齐（不多不少不重复） |
   | C2 | 每条证据的 `report_id` 必须在语料库内（禁止引用不存在的报告） |
   | C3 | 每条 `quote_match` 必须在报告正文里逐字命中 |
   | C4 | OVERCLAIM / REVERIFY 行必须带 owner 动作句 |
   | C5 | 台账记的 `claimed_level` 必须等于注册表快照（注册表翻转即破闸） |
   | C6 | 未注册域的缺口证据同样要逐字命中 |
   | C7 | 本次复算必须等于已入库快照（对账结论不得偷偷变好/变坏） |

4. **反例（变异体判负）**：`tests/test_capability_ledger.py` 对 C1~C7 各注入一个人造缺陷，
   要求**各自**触发对应编号——证明自检不是恒真。

## ③ 结果（数字全部由工具写入 artifacts，零手抄）

对账结果：`artifacts/capability_ledger/capability_ledger_resolved.json`；表格：`capability_ledger_table.md`。

| 分歧 | 数量 | 明细 |
|---|---|---|
| OVERCLAIM（自述被证据推翻） | 2 | `propeller.open_water` REAL→MOCK（报告 1128：6/6 判据不通过；报告 1133：0/7）；`calc.stability` REAL→MOCK（报告 1136：34 项判定 24 FAIL） |
| REVERIFY（已重写，绝对精度仍待外部基准） | 1 | `calc.resistance` REAL→**WITHHELD**（报告 1140 Holtrop-1984 重写 124 项检查 FAIL 0，但对照基准主要是自家实现/自家台账；报告 1144 裁决基准数据集仅 4/12 可判据、8/12 如实 WITHHELD） |
| UNVERIFIED（自述 REAL、零审计证据） | 15 | 几何 5 项、`stdmodels.lookup`、`gov.stats`、`knowledge.docs`、`dev.*` 7 项 |
| OK | 3 | `ai.predict` / `methods.computation`（自述 NOT_SUPPORTED）、`misc.stubs`（自述 STUB）—— 自述不高于证据等级 |

覆盖缺口（注册表上无条目、但已有审计报告的学科）：

| 域 | 最新证据判定 | 建议动作 |
|---|---|---|
| 耐波/seakeeping | REAL（报告 1139：11/11 判据 PASS；此前报告 1135 判 MOCK） | 新增 `seakeeping.rao`，初始 WITHHELD，待外部 RAO 基准复核 |
| 绿色航行/green-ship | WITHHELD（报告 1134 三层分级） | 新增 `green.eedi` / `green.cii`，三层不得合并成单个 REAL |
| 基准参照数据/benchmark-reference | WITHHELD（报告 1137：0 violations / 11 of 11 coherent，文献转录项 quarantined） | 新增 `data.benchmark_integrity`（canonical v3.0） |

图：`artifacts/capability_ledger/fig_capability_ledger.png`
（左：21 项"自述 vs 对账"色阶矩阵，右：分歧计数；底部列出注册表覆盖缺口。生成脚本
`tools/audit/gen_capability_ledger_figure.py`，数据直读 resolved.json。）

## ④ 注意点 / 踩过的坑

- **引文即判据**：证据若允许转述，"审计报告说敞水是 MOCK"这类记忆型断言就会混进来。
  用正则 + 命中行 sha256 后，任何引文都必须能在正文里点出来。
- **conditional 不是 REAL**：报告 1140 的 PASS 是对**自家参考实现/自家台账**的 PASS；
  把自洽性 PASS 直接读成"绝对精度 REAL"正是历史上 1131 判定的失败模式复现。
  故 `calc.resistance` 记为 REVERIFY 而非 OK。
- **注册表不可 HTTP 读取**是硬限制（六个路径 404）：C5 只能对"快照 vs 台账"设闸，
  无法对"平台真实状态 vs 台账"设闸。已作为改进项写进台账 provenance。
- **`misc.stubs` 自述 STUB 却是黑箱**：STUB 清单没有逐条端点名，本台账只能记 OK；
  建议平台把 stub 清单结构化（端点 + 原因 + 计划），否则"STUB"本身无法核验。
- 本单只做**证据对账**，不修任何端点的物理实现（那些由 1546/1548/1555/1556/1559 等修复单承担，
  目前多为 failed）；台账的 `action` 字段就是给这些修复单的验收口径。

## ⑤ 误差判定（关键量 vs 基准）

本单产出的是**分类裁决 + 引文可核验性**，不新增物理计算，因此：

| 关键量 | 基准 | 偏差 | 是否 <3% |
|---|---|---|---|
| 21 项能力清单完整性 | 注册表快照 21 项 | 0 项缺失 / 0 项多余（C1） | **是**（离散计数，0 偏差） |
| 证据引文可核验率 | 11 条证据行 × 正文逐字命中 | 11/11 = 100%（C2/C3/C6） | **是**（0 无可核验证据） |
| 分歧裁决一致性 | 台账声明规则 vs 工具复算 | 21/21 一致；C7 复算 == 入库快照 | **是**（0 不一致） |
| 引用误差值（如 KB +33.33%、GM +61.54%、GZ@20° +53.27%、R_T +224.5%） | 报告 1136 / 1131 原文 | **未复算**：本单只做逐字引用与等级裁决，数值精度以原报告为准 | **不适用（不回答）**——这些数值全部 >3%，本单不因此改变裁决方向 |

自检与测试：`python3 tools/audit/capability_ledger.py --check` → `PASS: C1~C7 全部通过`；
`python3 -m pytest tests/test_capability_ledger.py -q` → **15 passed**（含 7 个变异体判负）。

## ⑥ 复现

```bash
python3 tools/audit/capability_ledger.py --check --write        # 离线对账 + 刷新 artifacts
python3 tools/audit/capability_ledger.py --refresh --token-file /tmp/tok   # 先从 :8010 重取报告正文再对账
python3 tools/audit/gen_capability_ledger_figure.py             # 重出图
python3 -m pytest tests/test_capability_ledger.py -q            # 15 passed
```