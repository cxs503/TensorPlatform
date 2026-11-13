# 需求自动接驳（FEAT-DEMAND-AUTOINTAKE）——任何需求源 → 榜上可领的锚

## 1. 病灶（实测，2026-11-13）
- `tensor_dev_missions` 中 `succeeded` 单 **10879** 张；把每张单的 `result` 过本包 parser：
  **971** 个候选 slug 被识别出来（845 张单带候选）。
- 其中 **509** 个 slug 已在 `feature_ledger`（等价锚，来源多为 coordinator/owner/panorama）；
  **462** 个 slug **在榜上完全不存在**——这就是「白提」：agent 在 finish 里说得清清楚楚的
  缺口/下一步，从未变成可领的锚。`feature_ledger` 里 `source='agent-proposed'` 的行数为 **0**。

## 2. 机制（四段流水线，全部机器可跑、可重放）
```
finish result (headline/summary/checks/声明 JSON)  ─┐
tensor_reports「局限/待办/注意点」等小节            ─┴─► parser ─► demand_intake_log（幂等审计表）
                                                              │
                                                              ├─► feature_ledger
                                                              │    status=pending_review
                                                              │    source=agent-proposed
                                                              │    ledger_group=demand-intake
                                                              ▼
                                                        gate（三判据）
                                                         ├ 过 → status=open（进 feeder / board-claim 供给面）
                                                         └ 不过 → status=dropped（原因码留痕）
                                                              │
tensor_reports 的待办条目 ──────────────────────────────────► demand_backlog（coordinator 周批采认）
```
每段结束写一条 `chronicle_snapshots` 事件（`payload->>'kind'='demand_intake'`）。

### 2.1 约定字段（agent 侧只需一个 JSON 块）
```json
{"proposed_anchors": [
  {"slug": "FEAT-SYNTH-DEMO", "title": "…", "domain": "platform", "repo": "TensorPlatform",
   "acceptance": "pytest 全绿且相对偏差 <3%（实测 1.2%）"}]}
```
别名 `suggested_anchors` / `anchor_proposals` / `proposed_features` 等价接受；元素可为对象或
`"SLUG — 标题：验收…"` 字符串；JSON 可出现在 `result` 任意深度或 fenced 代码块里。
无 JSON 时退到「锚语境行」兜底扫描（行内含 锚/anchor/proposal 语境词 + slug 形状 token），
并把同行/紧邻 2 行内**真实存在**的量化片段当验收条款（不编造）。

### 2.2 审计门三判据（`tools/demand_intake/gate.py`）
| 判据 | 机器判据 | 不过门原因码 |
|---|---|---|
| G1 slug 规范 | 段数≥2、段长2..16、`A-Z0-9`、至少一个 ≥3 字母实词段 | `slug_no_alpha_word_segment` / `slug_segments_lt_2` / `slug_segment_bad` |
| G2 量化验收非空 | 非空、≥4 字符、含数字 **且** 含比较符/百分号/计量词 | `acceptance_empty` / `acceptance_no_number` / `acceptance_no_threshold_marker` |
| G3 不与既有锚重复 | slug 精确撞已有行 → `duplicate`；标题 token Jaccard ≥0.85 → dropped | `duplicate_slug(status)` / `duplicate_title(slug,sim)` |

G1 的「实词段」条款专门拦 `A1-A2`/`B1-B4` 这类**步骤编号**：实测它们在 agent 报告里
「锚」语境行上占多数，形状像 slug 却不是需求。

## 3. 用法（dispatcher/operator 只调 CLI）
```
python3 -m tools.demand_intake.cli init
python3 -m tools.demand_intake.cli ingest-missions --since-id <水位> --limit 500   # 实时接驳
python3 -m tools.demand_intake.cli gate --apply                                    # 审计门
python3 -m tools.demand_intake.cli ingest-reports --since-id <水位> --limit 200     # 报告待办
python3 -m tools.demand_intake.cli measure                                         # 入榜率口径
python3 -m tools.demand_intake.cli redteam --out artifacts/demand_intake/redteam.json
```
幂等：`cand_key = sha1(source_kind|source_ref|slug)` 唯一 + `feature_ledger.slug` 唯一 +
`ON CONFLICT DO NOTHING`；红队实测二次全链 ingest 新增 = **0**，backlog 二次入库 = **0**。

## 4. 红队结果（真 PG 临时 schema 内全链路，跑完 DROP）
`artifacts/demand_intake/redteam.json`（`verdict=PASS`）：
- **断链红队**：合成 mission 里两条合格锚 → `feature_ledger` 出现 `status=open`、`source=agent-proposed` ✅
- **垃圾锚拦截红队**：`A1-A2`（步骤编号）、空验收、无数字验收、标题重复四条**全部** dropped，
  原因码逐条命中，`open_junk=[]` ✅
- **幂等红队**：全链复跑 inserted=0、backlog second=0 ✅
- 入榜率口径本链 `intake_rate=1.0`（detected 7 全部落库），`claimable_rate=0.286`（2/7，
  其余按设计拦下）

## 5. 未做/局限（honest）
1. **本单未对生产库执行写操作**：首刷 462 个候选锚需要 coordinator 采认后才 `ingest-missions`
   +`gate --apply`（一次跑完会把 462 条 `pending_review` 灌进账本，属榜单污染风险，留给周批）。
   机制与红队已在同构 schema 上端到端验证。
2. 无 embedding 语义去重：近义改写靠 `slug` 精确 + 标题 Jaccard≥0.85 兜底，会漏检。
3. 文本兜底路径依赖「锚语境词」白名单，英文 `TODO` 段落里若无 anchor/proposal 字样不会被抽到
   （宁漏不滥：文本路径抽出的候选若验收不量化一律 dropped，见 G2）。
4. `tests/test_demand_intake.py::test_parser_structured_field_wins_and_merges` 仍红（19 项中 1 项，
   见 `artifacts/demand_intake/pytest.log`）——结构化字段走通但该断言未过，未在本单预算内收口，
   按 honest 原则如实登记，不能算「已验收通过」。
