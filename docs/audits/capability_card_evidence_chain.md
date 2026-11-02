# 能力卡证据链：从「格式合法的伪造摘要」到「可复算锚点」(m1819 / infrastructure 席)

> 一句话：平台 5 张科研能力卡（CAP-001…005）的 `validation_evidence` 里，**5/5 的锚点无法被任何人复算**
> ——`report_id` 是自由标签（`suboff_sail`、`e2e1`、`wave10_multiphase`、`dtmb5415_geometry`、`report_gate_v1`），
> `report_md5` 是 32 位语法合法但凭空写下的字符串（卡片自己承认 "Placeholder: report not yet frozen"），
> 而 **3 张卡照样标 `PASS`**。上一版闸门只校验 `re.fullmatch("[a-f0-9]{32}")`——任何伪造摘要在它面前都是绿的。
> 本单把这条链换成可机器裁判的 E1–E7 七条判据，重锚到真实报告正文的 sha256/md5，并把 3 个无证据 PASS 降级。

## ① 案例介绍（对象 / 范围 / 判据）

| 项 | 内容 |
|---|---|
| 对象 | `docs/capability_registry/cards/*.json`（5 张能力卡）+ `index.json` + `docs/capability_card.schema.json` |
| 证物 | :8010 `GET /api/v1/tensor/reports/{id}` 的报告正文（`markdown` 字段），钉扎为仓内快照 |
| 判据 | E1 注册表↔磁盘一致；E2 锚可解析；E3 摘要可复算；E4 无占位符；E5 无越权 PASS；E6 误差↔容忍度自洽；E7 引文逐字命中 |
| 最严门槛 | 任何 `PASS` 必须同时满足 E2+E3+E7，且引文不得自陈失败（E5） |
| 计算量 | 全离线，无重算进程（本机 load15 当时 ≈ 84/128 核，按容量纪律只做轻量任务） |

钉扎的 4 份报告正文（快照 `tests/fixtures/capability_evidence/reports_snapshot.json`，仅由
`tools/audit/pin_capability_evidence.py` 写入；规范形式 = `sha256/md5(markdown.encode("utf-8"))`）：

| 报告 id | 标题 | body_sha256 | body_md5 | 字节 |
|---|---|---|---|---|
| 1296 | SDAA 圆柱绕流 Re=20 验证报告（mission 487 补报） | `01615d61b84b3c37…` | `d356b92948bfa2845e64c044d5c0539c` | 4523 |
| 1313 | Mission 542 补报：DTMB 5415 参数化变形族生成器 | `4b325d5e7791fbe0…` | `f418d838e5a839888588a6acba40fca0` | 6437 |
| 1340 | SUBOFF 静水阻力精度实测（S0）：设计点误差 -4.92% → PASS | `5a0e9e91fc63f936…` | `b3edf7a4a98d471c90a439010ae59e5a` | 4377 |
| 1375 | M1775 — SUBOFF 阻力计算全面验证 | `b4da14a77c2dcc54…` | `1fb5fb22bdceb25a3aa71c582d0ea8fb` | 6291 |

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **量缺陷**：新写的 `tools/audit/capability_card_evidence.py` 先在**未改动的旧卡片**上跑一遍（`evidence_chain_before.json`），
   得到可复现的现场：**24 条 violation、0/5 张卡 ANCHORED、3 个 PASS 中 0 个有证据支撑**。
2. **钉锚**：`pin_capability_evidence.py --ids 1375 1296 1313 …` 把报告正文与摘要一次性写入 fixture，
   此后所有判定只读 fixture ⇒ CI 无需 :8010，且报告若被事后改写（retro-edit）摘要必变（E3 报警）。
3. **重锚**：按"有证据就锚、没证据就显式声明"重写 5 张卡的 `validation_evidence`：
   `anchor_type ∈ {platform_report, repo_file, none}` + `report_body_sha256` + `quote_match`（正则）+
   `quote_line_sha256`（引文行摘要，防转述/记忆/幻觉）+ `tolerance_pct`；误差数字由脚本从锚点正文解析注入，零手抄。
4. **修闸**：`scripts/validate_capability_card.py` 里原来的 `validate_report_id_exists()` 是一张**硬编码白名单**
   （`{"e2e1","suboff_sail","wave10_multiphase","dtmb5415_geometry","report_gate_v1","m597"}`）——
   即"卡片编的标签"就是"合法 id 集合"，真报告 id（如 `1375`）反而被判非法。现改为走 E2–E7 解析器。
5. **反例**：红队 demo 增加 RT-003（真实 id + 伪造 md5 + 幻觉引文），并让 RT-001/002/003 全部走同一判定器；
   `tests/test_capability_card_evidence.py` 另有 14 个变异体，逐条证明每个 E 编号**不是恒真**。

## ③ 计算结果（程序化注入，数字零手抄）

### 3.1 修复前 → 修复后

| 指标 | 修复前 | 修复后 |
|---|---|---|
| violations | 24 | 0 |
| ANCHORED 卡片 | 0/5 | 4/5 |
| DECLARED_EMPTY 卡片 | 0（无此态） | 1 |
| 无证据 PASS 断言 | 3 个 PASS / 0 个被证据支撑 | 1 个 PASS / 1 个被证据支撑 |

### 3.2 逐卡判决

修复前：

| card | anchor | report_id | 声明状态 | E2 | E3 | E4 | E5 | E7 | 判决 |
|---|---|---|---|---|---|---|---|---|---|
| CAP-001 | — | e2e1 | IN_PROGRESS | ❌ | ❌ | ❌ | ✅ | ❌ | UNVERIFIABLE |
| CAP-002 | — | suboff_sail | PASS | ❌ | ❌ | ❌ | ❌ | ❌ | UNVERIFIABLE |
| CAP-003 | — | wave10_multiphase | HONEST_FAIL | ❌ | ❌ | ❌ | ✅ | ❌ | UNVERIFIABLE |
| CAP-004 | — | dtmb5415_geometry | PASS | ❌ | ❌ | ❌ | ❌ | ❌ | UNVERIFIABLE |
| CAP-005 | — | report_gate_v1 | PASS | ❌ | ❌ | ❌ | ❌ | ❌ | UNVERIFIABLE |

修复后：

| card | anchor | report_id | 声明状态 | E2 | E3 | E4 | E5 | E7 | 判决 |
|---|---|---|---|---|---|---|---|---|---|
| CAP-001 | platform_report | 1296 | NOT_VERIFIED | ✅ | ✅ | ✅ | ✅ | ✅ | ANCHORED |
| CAP-002 | platform_report | 1375 | HONEST_FAIL | ✅ | ✅ | ✅ | ✅ | ✅ | ANCHORED |
| CAP-003 | none | — | NOT_VERIFIED | ✅ | — | — | ✅ | — | DECLARED_EMPTY |
| CAP-004 | platform_report | 1313 | NOT_VERIFIED | ✅ | ✅ | ✅ | ✅ | ✅ | ANCHORED |
| CAP-005 | repo_file | docs/REPORT_GATE.md | PASS | ✅ | ✅ | ✅ | ✅ | ✅ | ANCHORED |

### 3.3 被推翻的三条 PASS（逐条给证据）

| card | 原声明 | 锚点证据原文（逐字） | 新状态 |
|---|---|---|---|
| CAP-002 SUBOFF 阻力 | PASS | 报告 1375："**是否 <3%：否。** 4 例中仅 1 例 <3%（SUBOFF-02，−1.2%）；3/4 例 <15%；"（SUBOFF-04 达 +34.6% > 15% 不可接受线） | HONEST_FAIL |
| CAP-004 DTMB 5415 几何 | PASS | 报告 1313："需修复 `export_variant` 中门禁判定逻辑"（几何门禁本身有未修缺陷，且正文无 0.236 m 偏差对账） | NOT_VERIFIED |
| CAP-005 报告生成 | PASS | `docs/REPORT_GATE.md`："六要素=①案例介绍…"（仓内策略文件，摘要可复算） | PASS（保留，但锚换成文件摘要） |

CAP-001（圆柱 Re=100）与 CAP-003（多相 wave-10）**降为 NOT_VERIFIED**：前者的唯一平台证据是报告 1296
（Schäfer-Turek 2D-1 **Re=20**），其注意点原文即 "Re≥100 涡脱落：需额外验证"，Re=100 的 C_D=1.33 / St=0.165
在锚点里没有对应实测；后者平台 100 篇报告语料与仓内 docs/ 中均无任何可复算产出（显式声明空锚 + gap_reason）。

## ④ 结果图

`artifacts/capability_evidence/evidence_matrix_before.png`（24 条 violation：整表红/黄）与
`evidence_matrix_after.png`（0 violation：4 卡 ANCHORED + 1 卡显式空锚），同一渲染器产出，可直接目视对比。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **"格式合法" ≠ "可复算"**：`re.fullmatch("[a-f0-9]{32}")` 对伪造摘要恒真；旧闸门与其红队用例（RT-002）
   同源于一张硬编码白名单，因而"假 id 被拒"是重言式——它同时也会拒真 id。
2. **快照有保质期**：摘要是"钉扎时刻"的正文；报告若被平台重发/修订，E3 会红。这是**特性不是缺陷**（漂移不该静默通过），
   但意味着修订流程必须重跑 `pin_capability_evidence.py` 并同步卡片，否则 CI 会替你踩刹车。
3. **DECLARED_EMPTY 不是通过**：它只表示"卡片诚实地承认没有证据"。E2 强制这类卡必须带 `gap_reason` 且**不得**标 PASS；
   在平台上它们对应 NOT_VERIFIED，不能当 REAL 用。
4. **未覆盖**：E1–E7 只管"声明 ↔ 证据"的一致性，**不判断算法本身的精度**；CAP-002 的 −8.9%…+34.6% 是平台
   阻力核的既有精度事实（另见报告 1375/1340），本单只保证它不再被一张卡片的 PASS 遮住。
5. **报告语料口径**：`/tensor/reports?limit=100&offset=…` 当前 total 恒为 100（分页封顶），故"平台内无证据"的
   结论范围是这 100 篇 + 仓内 docs/，不能外推到全历史库。

## ⑥ 误差判定（逐项，显式回答"是否 <3%"）

本单为**证据链/基础设施**任务，无可与水动力基准比较的物理量，故"误差"定义为**判定一致性误差**：

| 关键量 | 基准 | 结果 | 是否 <3% |
|---|---|---|---|
| 卡片锚点摘要 vs 钉扎正文重算（5 张卡） | sha256/md5 逐字节 | 全部逐字节相同 | 是（0%） |
| 引文行摘要 vs 正文重算（有锚卡） | sha256 前 16 位 | 全部相同 | 是（0%） |
| 已声明 PASS 的被支撑率 | 100%（每 PASS 必须 E2∧E3∧E7） | 1/1 = 100% | 是（0%） |
| 变异体判别力（14 个注入缺陷） | 每个缺陷必须触发其 E 编号 | 14/14 触发（pytest 18 项全绿） | 是（0%） |
| CAP-002 阻力精度（卡外事实，如实记录） | 实验 Ct（Roddy 1990） | −8.9%…+34.6%，仅 1/4 例 <3% | **否**（>15% 的 1 例已判不可接受） |

**已知不可判定项**：若某能力在平台历史库（>100 篇，当前 API 不可分页取全）中其实存在真实报告，
本单把它判 NOT_VERIFIED 会偏保守——这是有意的方向性取舍（宁可扣等级，不可留伪造锚）。

报告 id：**1519**（:8010 `/tensor/reports/1519`）。

## 复现

```bash
python3 tools/audit/capability_card_evidence.py --check \
    --json artifacts/capability_evidence/evidence_chain_after.json \
    --figure artifacts/capability_evidence/evidence_matrix_after.png   # 0 violation, exit 0
python3 scripts/validate_capability_card.py --batch                    # 5/5 PASS
python3 scripts/validate_capability_card.py --red-team                 # RT-001/002/003 ALL PASS
python3 -m pytest tests/test_capability_card_evidence.py tests/capability/test_capability_registry.py -q
# 钉扎（需要 :8010 凭据；CI 不需要）
python3 tools/audit/pin_capability_evidence.py --ids 1375 1296 1313 1340 2>/dev/null || true
```
