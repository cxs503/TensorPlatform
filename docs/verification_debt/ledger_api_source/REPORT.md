# 验证欠账台账的 API 数据源：把「清偿仪表盘」从 agent 不可用修回可用（100 篇 / 518 条 / 28 个复发簇）

**席位** tp-ver-1（verifier，m2037）· 分支 `agent/m2037` · 2026-11-01
**被测对象** `tools/validation_debt_ledger.py`（验证欠账台账：报告「局限/注意点/待办」节 → 机器可判台账）
**全部数字由 `tools/ledger_api_source_evidence.py` 从台账输出写出到
`docs/verification_debt/ledger_api_source/numbers.json`，正文零手抄。**

## ① 案例介绍（问题的物理/工程背景与几何）

owner 常设优先级是「验证欠账 > 功能补全 > 失效测试」，而「欠账有多少、落在哪个学科、
哪条欠账在多少篇独立报告里复发」这个口径，全靠 `validation_debt_ledger.py` 一张台账。
台账的判级链是离线纯函数（分节 → 条目 → 归税到 9 学科 + 21 能力 → 严重度 → 聚类），
**唯一的外部输入是"报告正文"**。

复现现场（本机、本次 mission）：

| 项 | 实测 |
|---|---|
| `TP_PG_PASSWORD` | **未设置**（agent 运行时只有 `TENSOR_AGENT_USER/PASS`、`TP_PG_DB`） |
| `python3 tools/validation_debt_ledger.py --limit 60` | `psql: fe_sendauth: no password supplied` → `SystemExit`，**零产物** |
| 平台 API `:8010/health` | 200 healthy 2.2.0 |
| `GET /api/v1/tensor/reports?limit=N` | 只回 `id/title/summary/...`，**不含正文** |
| `GET /api/v1/tensor/reports/{id}` | **含 `markdown`**（正文可用） |

即：台账的数据源是单点 psql，而 agent 席位没有 psql 凭据 ⇒ **verifier 席最核心的
「欠账清偿」仪表盘对全平台 agent 不可用**，欠账清偿率无法被自动复核。这不是算法缺陷，
是通道缺陷；但它的后果是验证性质的（欠账永远"看不见"就等于永远不清偿）。

## ② 怎么做的（方法 / 流程 / 工具链）

最小改动 + 可注入依赖 + 三条机器判据：

1. **新增 `load_reports_api(limit, api_base, fetch=None)`**：用 `TENSOR_AGENT_USER/PASS`
   登录取 token → 列表端点拿 id → 详情端点逐条取 `markdown`（N+1，`ThreadPoolExecutor(≤8)`
   并发）→ 按 id 升序返回，字段与 psql 路径同构（`id/title/markdown/summary/mission_id/scenario_key`）。
2. **失败显式化**：新增 `LedgerSourceError`（凭据缺失 / 网络 / HTTP 错误），与解析失败区分；
   不允许静默返回空表。
3. **`--source {auto,json,api,psql}`（默认 auto）**：`--from-json` → 有 agent 凭据走 api →
   落到 psql；某源失败自动退让并在 stderr 记录理由，全挂才 `SystemExit`（消息带全部原因）。
   psql 路径在无 `TP_PG_PASSWORD` 时不再被当作"默认唯一路径"。
4. **对账脚本 `tools/ledger_api_source_evidence.py`**：同一批报告分别经 API 与 JSON 快照
   进 `build_ledger`，逐键比对 5 个统计量（**口径一致性**）；连跑两次比对（**确定性**）；
   出图。

工具链：Python 3.11 标准库（`urllib`、`concurrent.futures`）+ matplotlib(Agg)；
测试 `pytest`。零第三方运行时依赖未变（比原设计只多标准库模块）。

## ③ 计算结果

`python3 tools/validation_debt_ledger.py --limit 100 --repo-root .`（修复后，纯 API 源）：

| 指标 | 值 |
|---|---|
| 报告数（API 返回） | **100**（id 区间连续，无正文缺失） |
| 含欠账节的报告 | **97** |
| 抽取欠账条目 | **518** |
| 聚类簇 | **486** |
| 复发簇（≥2 篇独立报告） | **28** |
| 首次抓取耗时 / 第二次 | **3.78 s / 5.06 s**（100 篇详情，含登录） |
| 修复前 | **0 条 / 0 篇**（psql 凭据缺失，直接退出） |

欠账条目按学科（`numbers.json.by_domain`）：

| 学科 | 条目 | 学科 | 条目 |
|---|---|---|---|
| 平台治理 | 92 | 操纵 | 21 |
| 阻力 | 49 | 冰区航行 | 19 |
| 振动噪声 | 27 | 耐波 | 14 |
| 稳性 | 24 | 推进 | 12 |
| 结构 | 23 | 绿色航行 | 6 |

口径自洽性与已知口径缺口（honest）：

| 项 | 值 |
|---|---|
| 条目未归到任何学科（`items_without_domain`） | **289 / 518（55.8%）** |
| 条目引用产物路径（`artifact_refs_total`） | 70 |
| 其中工作树内**找不到**（`artifact_refs_missing`，即"伪闭合"嫌疑） | **26** |
| 报告 `markdown` 缺失 | 0 |

未归学科过半，说明 `DOMAIN_RULES` 关键词表对"平台治理/方法学"类条目覆盖不足（这些条目
本就不属某一船舶学科），属**台账口径的已知缺口**，不是本单修复目标；26 个缺失路径本机只挂
TensorPlatform 一棵树，跨仓路径必然找不到，`--search-roots` 可多仓复算。两项均已记入
`numbers.json` 供后续单处理。

## ④ 结果图

![fig1](artifacts/fig1_ledger_api_census.png)

左：修复后经 API 源抽出的 518 条欠账按 9 学科分布（平台治理 92 > 阻力 49 > 振动噪声 27 …）。
右：28 个复发簇（同一欠账在 ≥2 篇独立报告里重复出现 = 长期未清偿）按复发报告数排行，
横轴为簇号、纵轴为独立报告数。图由 `tools/ledger_api_source_evidence.py` 程序化生成。

## ⑤ 注意点（局限与适用范围）

- **只修通道，不清偿欠账**：518 条欠账仍是欠账，本单交付的是"看得见 + 可复算"，
  清偿率不变；真实清偿需逐条单独立项（本单只把"挑哪条"的入口打通）。
- **详情端点 N+1**：100 篇约 4–5 s，1000+ 篇按线性外推约 40–60 s；这是 API 源相对
  psql 单查询（1 次往返）的固有代价，换来的是"agent 无需 DB 口令"。若要全量普查，
  建议平台补一个 `?include=markdown` 的列表参数（未在本次范围内，未改平台代码）。
- **markdown 缺失的报告会被静默排除**：本批 100 篇无此情况；脚本把缺失 id 记入
  `reports_missing_markdown`，不静默。
- **`search_roots` 未跨仓铺开**：本机只挂 TensorPlatform 工作树，故跨仓产物路径会被记为缺失，
  这是"口径"而非"缺陷"；`--search-roots` 可多仓复算。
- **严重度/归税仍是关键词规则**（未变），不是语义模型；规则在文件头显式声明，测试用内联夹具钉死。
- 未改平台后端、未改数据库、未动 `main`，未 push（按纪律由平台推送）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

本次交付物是**通道 + 普查**，不涉及物理量，判定基准取"同批报告的 REPL 口径"：

| 关键量 | 基准 | 实测 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| `reports_scanned` / `reports_with_debt_section` / `items_total` / `clusters_total` / `recurrent_clusters_ge2`（API 源） | 同批报告 JSON 快照经同一 `build_ledger` | 五个键**逐键相等**（100/97/518/486/28） | **0.00%** | **是（精确相等）** |
| 同一源连跑两次 | 第一次结果 | 五键相等（100/97/518/486/28） | **0.00%** | **是** |
| 修复前覆盖率 → 修复后 | 全量 100 篇 | 0 篇 → 100 篇 | 不适用（0 基线） | **是**（欠账可见性由 0 到 100%） |
| 抓取耗时 | 无参考基准（首版） | 3.78 s / 5.06 s（两跑） | — | **无基准，声明原因**：新通道无历史基线；两跑差异 1.28 s 属网络/调度抖动，非算法误差 |

honest 声明：**绝对欠账量没有下降**（518 条仍在），本单不宣称任何清偿率提升；
"精度 <3%" 的成立范围仅限"台账口径一致性/确定性"，不外推为"欠账已解决"。

## 机器判据（测试）

`pytest tests/test_validation_debt_ledger_api.py tests/test_validation_debt_ledger.py -q` → **38 passed**。
新测试钉死 6 个可被推翻的命题：登录先于取数且后续请求带 Bearer；列表无正文 ⇒ 必须逐条取详情；
`--limit` 真截断；缺凭据抛 `LedgerSourceError`（不许静默空表）；auto 选源优先级与失败退让
（api 挂 → 落到 psql，全挂 → `SystemExit` 带原因）；API 源与 JSON 源 `build_ledger` 口径一致。