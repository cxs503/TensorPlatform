# P34-N7 平台报告归属门与判据变异门成熟化（mission 5496）

作者：tp-agent · 战役 DAG 节点 P34-N7 · 分支 `agent/m5496` · 全部数字由
`artifacts/m5496/make_report_payload.py` 从 JSON 台账程序化注入（零手抄）

## ① 案例介绍（物理/工程背景与参数）

被测对象不是船舶水动力内核，而是**平台的报告提交链路**——它决定了整个生态里
「一份带数字的成果报告能不能被相信」。历史事故（本仓 `KNOWN_TRAPS` 与判例 #608、
#825/#823）给出三条真实失效线：

1. **写错单**：`POST /tensor/reports` 只有角色门（「你能不能发报告」），没有归属门
   （「这份报告是不是你的单」）。任何持 ENGINEER token 的智能体都能把 A 单成果写进
   B 单号下，HTTP 200、静默落库。战役 DAG 按 `mission_id` 归并战绩与验收 →
   甲的绿灯记到乙头上，且无法从数据层分辨。
2. **配图门形同虚设**：只做 `startswith(b"\x89PNG")` 四位前缀判定，于是截断 PNG、
   WebP/RIFF 改扩展名、只剩文件头的空壳都能落盘（报告页图裂，判据图像证据失效）。
3. **判据不可证伪**：每条验收门都有自己的单测，绿着就当成「门有效」；但一条
   `return True` 的恒真判据与真判据在测试里完全同构（都绿）。没有一把统一的尺子
   回答「把违规样本喂进去，它会不会亮红灯」。

本单把这三条线做成**服务端结构性阻断 + 客户端 fail-fast + 全台账 KPI 门禁**。

参数与产物（本单实体）：

| 项 | 值 |
|---|---|
| 目标端点 | `POST /tensor/reports`（backend/api/tensor_reports.py） |
| 新增纯判据模块 | `backend/services/report_ownership.py`（归属门 + PNG 魔数/IEND 门） |
| 新增客户端工装 | `tools/report_payload.py`（独占文件名 + 独占写盘 + 自证 sha256 + 提交前复算） |
| 新增判据台账 | `tools/audit/criteria_mutation.py`（M1–M5 变异类 + KPI + 准入门禁） |
| 新增测试 | `tests/test_report_ownership.py`（21 条）、`tests/test_criteria_mutation.py`（30 条）、`backend/tests/test_paper_traceability_gate_kills_v3.py`（12 条） |
| CI 门禁 | `.github/workflows/criteria-gate.yml` |
| 纳入台账的验收门 | 4 个（PAPER-TRACE / REPORT-QUALITY / REPORT-OWNERSHIP / CLIENT-PAYLOAD） |
| 台账判据数 | 12 条（含 1 条负控），变异体 66 个 |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**(a) 归属门（服务端结构性阻断）**。判据抽成纯函数 `ownership_verdict(user, mission, ...)`，
端点只做适配，因此生产与测试 import 同一份实现（不抄副本）。身份解析顺序
`claimed_user_id`（0090 迁移绑定的认证账号）> `claimed_by`（历史字符串）> 无领取记录。
判定表：无 `mission_id` → 放行（历史路径不回归）；mission 不存在/跨组织 → 404
`REPORT_MISSION_NOT_FOUND`；领取者与提交者不一致 → 403 `REPORT_MISSION_NOT_OWNED`；
**无领取记录 → 也拒**（没有「谁该交这份报告」的证据，落库即不可归属数据）；
ADMIN 补录放行但响应里带 `ownership.bypass="role:admin"`（可审计的明口，不是暗门）。
错误体是 JSON（含 `code`），并带 `X-Error-Code` 响应头，客户端按 code 分支 fail-fast。

**(b) 配图门（魔数 + IEND，先验后落）**。`validate_png_payload` 三条判据：base64 严格
解码 → 首八字节必须等于 `89 50 4E 47 0D 0A 1A 0A` → 必须以带 CRC 的 IEND 块收尾。
JPEG/RIFF-WebP/GIF/BMP/TIFF 命中容器魔数表 → 422 `REPORT_FIGURE_NOT_PNG`，
错误体同时给**期望魔数十六进制** `89 50 4E 47 0D 0A 1A 0A`
与实测魔数；缺 IEND → 422 `REPORT_FIGURE_TRUNCATED`。旧实现「先 INSERT 再写盘、失败再
DELETE」的窗口已消除：**先验后落**，异常路径上不再出现指向缺失图片的报告行。

**(c) 客户端工装**。`write_report_payload(path, mission_id, payload)`：文件名必须含
mission_id 令牌（`report_m5496.json`），否则 `PayloadNameNotExclusive`；payload
内嵌 `mission_id` 与入参不符 → `PayloadMissionMismatch`；写盘走「临时文件 + `os.link`」
实现**原子且独占**（目标已存在 → `PayloadExists`，绝不覆盖他人成果）；返回自证
`{path, mission_id, sha256, bytes, verified}` 并**立刻回读复算**。提交前
`preflight(path, mission_id, proof)` 现场重算 sha256/字节数并核对内嵌单号，漂移即
`PayloadDrift`。PNG 判据直接 `import` 服务端同一份 `report_ownership`，本地与线上不会分叉。

**(d) 判据变异台账**。`tools/audit/criteria_mutation.py` 用**适配器**消费现有各门的
真实判据（PAPER-TRACE 的 `check_table_arithmetic/check_inline_errors/artifact_provenance`、
REPORT-QUALITY 的 `check_report_quality`、REPORT-OWNERSHIP 的 `ownership_verdict/validate_png_payload`、
CLIENT-PAYLOAD 的 `require_exclusive_name/preflight`），对每条判据生成五类「应当被拒」的
变异样本：M1 数值扰动 ε∈{1%,3%,15%}、M2 单位/量纲/容器错位、M3 常数替身、M4 样本伪装、
M5 基准替换。判据亮红灯 = kill。**基准样本必须放行**（否则判据假阳性，单独记 `base_clean`）。

**(e) KPI + 准入门禁**。逐门 kill rate、最弱 mutant 清单、不可证伪判据清单、M1 单调性、
覆盖面（门数/判据数/可实例化率）。准入门禁 `pr_gate`：新判据没有 killer test →
`NO_KILLER_TEST` 拒；登记的 killer test 不存在 → `KILLER_TEST_NOT_FOUND` 拒；
killer test **不咬**（把判据经 `CRITERIA_MUTATION_DISABLE` 打成恒放行后它仍绿）→
`KILLER_TEST_TOOTHLESS` 拒。CI 接线在 `.github/workflows/criteria-gate.yml`（含负控
期望 exit 2 的分支）。

工具链（全部离线可复算）：

```bash
python3 tools/audit/criteria_mutation.py ledger --ast-audit artifacts/m5496/audit_c6_baseline.json \
        --ast-audit artifacts/m5496/audit_c7_v3.json --json-out artifacts/m5496/criteria_mutation_ledger.json
python3 tools/audit/paper_gate_mutation_audit.py --tests <4 个 PAPER-TRACE 测试文件> --jobs 32 \
        --json-out artifacts/m5496/audit_c7_v3.json
python3 tools/audit/criteria_mutation.py gate --spec artifacts/m5496/pr_new_criteria_no_killer.json
```

## ③ 计算结果（数字表，程序化注入）

### 3.1 逐门判据杀伤率（KPI 主表）

| 验收门 | 判据数 | killed/mutants | kill rate | ≥90% 门禁 |
|---|---|---|---|---|
| CLIENT-PAYLOAD | 2 | 14/14 | 100.0% | ✅ |
| PAPER-TRACE | 3 | 11/18 | 61.1% | ⚠️ |
| REPORT-OWNERSHIP | 3 | 15/15 | 100.0% | ✅ |
| REPORT-QUALITY | 3 | 7/12 | 58.3% | ⚠️ |
| NEGATIVE-CONTROL | 1 | 0/7 | 0.0% | — |

- 判据可实例化率 **100%**（12/12），
  覆盖面 **4 个验收门**（要求 ≥4）。
- 全部判据合计 killed 47/66 = 71.2%；
  扣除负控后 79.7%。
- **M1 单调性**：ε=1%→3 个、3%→3 个、15%→5 个，违例
  **0**（要求 0）。数值扰动越大越容易被抓，说明判据的容差带是单调的。
- **负控**：恒真判据 `NEG-TAUTOLOGY` killed 0/7
  （0/5 类），
  且被标为 UNFALSIFIABLE（清单：NEG-TAUTOLOGY）。

### 3.2 逐判据台账（节选全表）

| 判据 | 门 | killed/mutants | kill rate | 判据内容 |
|---|---|---|---|---|
| PT-PG4 | PAPER-TRACE | 4/7 | 57% | PG4 表内误差列必须由「数值/参考」复算得出（容差 0.05pp + 0.5%） |
| PT-PG5 | PAPER-TRACE | 3/7 | 43% | PG5 正文「A vs B，误差 Z%」必须能复算（H2/H3 行内数字） |
| PT-PG7 | PAPER-TRACE | 4/4 | 100% | PG7 带数字的结果表必须给出工件级出处（path#pointer） |
| RQ-PHYSICS | REPORT-QUALITY | 2/4 | 50% | 六要素①：物理/案例介绍必须给问题背景 **且** 几何或参数 |
| RQ-VALIDATION | REPORT-QUALITY | 2/4 | 50% | 六要素③：必须有对标/验证入口（基准、文献、解析解之一） |
| RQ-FIGURES | REPORT-QUALITY | 3/4 | 75% | 六要素④：必须有真图（带 caption）或显式无图理由 |
| RO-MISSION | REPORT-OWNERSHIP | 7/7 | 100% | POST /tensor/reports 的 mission_id 必须属于提交者（claimed_us |
| RO-PNG-MAGIC | REPORT-OWNERSHIP | 4/4 | 100% | 配图必须是 PNG（八字节魔数 89 50 4E 47 0D 0A 1A 0A），其它容器 422 |
| RO-PNG-IEND | REPORT-OWNERSHIP | 4/4 | 100% | PNG 必须以 IEND 段收尾；截断/半截上传 422 REPORT_FIGURE_TRUNCATED |
| PL-NAME | CLIENT-PAYLOAD | 7/7 | 100% | 报告文件名必须含 mission_id 令牌（否则无法反查归属、不同单互相覆盖） |
| PL-DRIFT | CLIENT-PAYLOAD | 7/7 | 100% | 提交前本地复算：磁盘字节 sha256 必须等于写盘自证（漂移即拒） |
| NEG-TAUTOLOGY | NEGATIVE-CONTROL | 0/7 | 0% | 负控：恒真判据（必须 0 kill；用来证明台账本身不是恒真） |

### 3.3 最弱 mutant 清单（真实盲区，点名到变异体）

| 判据 | 类 | mutant | 未杀证据 |
|---|---|---|---|
| PT-PG4 | M1 | M1@1% | 0 处误差列与值/参考不平 |
| PT-PG4 | M1 | M1@3% | 0 处误差列与值/参考不平 |
| PT-PG4 | M4 | M4 | 0 处误差列与值/参考不平 |
| PT-PG5 | M1 | M1@1% | 0 处行内误差复算不上 |
| PT-PG5 | M1 | M1@3% | 0 处行内误差复算不上 |
| PT-PG5 | M2 | M2 | 0 处行内误差复算不上 |
| PT-PG5 | M4 | M4 | 0 处行内误差复算不上 |
| RQ-FIGURES | M3 | M3 | codes=['figures_missing_caption'] |
| RQ-PHYSICS | M2 | M2 | codes=[] |
| RQ-PHYSICS | M5 | M5 | codes=[] |
| RQ-VALIDATION | M2 | M2 | codes=[] |
| RQ-VALIDATION | M5 | M5 | codes=[] |

读法：`REPORT-QUALITY` 与 `PAPER-TRACE` 的弱项几乎全在 **M2/M5**（单位错位、基准替换）——
关键词式判据只能判断「有没有提到验证/参数」，无法判断「提到的是不是同一个量纲、同一份基准」。
这正是本单要把**结构性判据**（归属/魔数/自证）拉满 100% 的原因：能被结构化的判据必须结构化，
留下来靠关键词的部分要如实记在台账上（不粉饰）。

### 3.4 PAPER-TRACE 门自身的 AST 变异审计（C6/C7）

| 轮次 | 测试集 | 变异体 | killed | kill rate |
|---|---|---|---|---|
| 基线 | 原 21 条单测 | 186 | 92 | **49.5%** |
| 现状 | +51 条杀手测试（含本单 12 条 v3） | 186 | 182 | **97.8%** |

基线复现 92/186 = 49.46%（判据要求与 92/186=49.5% 一致）；
现状 182/186 = 97.85% ≥ 90%（判据要求 ≥168/186）。
残留 survivor 4 个：binop_swap[5], binop_swap[6], return_empty[0], return_empty[1]
——经逐个探针（`artifacts/m5496/probe_kill.py`）确认全部是**等价变异体**：
`_close()` 取三者最小值的符号扰动被第三项吃掉；早退分支 `return findings` 处 findings 恒空。

### 3.5 判据级验收（C1–C12）

| 判据 | 实测 | 结论 |
|---|---|---|
| C1 归属红→绿 | 打桩复现修复前 2xx（红留证，`test_c1_prefix_behaviour_would_have_been_2xx`）；修复后 403 `REPORT_MISSION_NOT_OWNED` 且零落库 | 通过 |
| C2 并发串号 | 8 线程并发提交 8 个不同 mission，串号 **0/8** | 通过 |
| C3 配图门 | JPEG→422 `REPORT_FIGURE_NOT_PNG`（错误体含期望魔数）、WebP→422、截断 PNG→422 `REPORT_FIGURE_TRUNCATED`、4 字节前缀→拒 | 通过 |
| C4 正常路径 | 本单报告 2xx，回查正文/图元数据一致，落盘字节 == 上传字节 | 通过 |
| C5 既有测试 | 223 passed / 0 failed / 1 skipped（批次见 artifacts/m5496/test_runs.json） | 通过 |
| C6 基线复现 | 92/186 = 49.46% | 通过（=49.5%） |
| C7 目标达成 | 182/186 = 97.85% ≥ 168/186 | 通过 |
| C8 覆盖面 | 4 个验收门，判据可实例化 100% ≥ 90% | 通过 |
| C9 M1 单调 | 序列 [3, 3, 5]，违例 0 | 通过 |
| C10 负控 | killed 0/7，0/5 类，标记 UNFALSIFIABLE | 通过 |
| C11 门禁生效 | 负控 spec（无 killer test）→ exit 2 `NO_KILLER_TEST`；正控 spec → exit 0，killer 破坏性验证 bites=True | 通过 |
| C12 零领域语义 | `domain_leak_scan.py` 扫 3 个新改文件 / 37 域词条 → 命中 0 | 通过 |

## ④ 结果图（数据全部由脚本从 JSON 台账注入）

图 1（`fig1_killrate_by_gate.png`）：逐门判据杀伤率，绿=≥90%、红=低于门禁线、
灰=负控恒真判据（0%）。结构性判据（归属门、客户端工装）拉满 100%，关键词式判据
（六要素、溯源）落在 58–61%——同一张图上就能看出「哪扇门靠结构、哪扇门靠自觉」。
图 2（`fig2_m1_monotonic.png`）：M1 数值扰动 ε 从 1%→3%→15% 的 kill 数曲线与负控平线，
单调性违例 0。
图 3（`fig3_paper_trace_ast.png`）：PAPER-TRACE 门自身的 AST 变异审计，总体
49.5%→97.8%，右图按变异算子拆开（基线 vs 现状）。
图 4（`fig4_criteria_heatmap.png`）：判据 × 变异类热图，红块即该判据在该类变异上的盲区
（M2/M5 整列偏红 = 单位错位与基准替换是全平台的共性盲区）。

## ⑤ 注意点 / 局限 / 适用范围

1. **等价变异体不可杀**：PAPER-TRACE 残留 4 个 survivor 已逐个证明语义等价（见 3.4），
   记在账上但**不冒充欠账**；97.85% 是**下界**（只做单点变异，不做二阶组合）。
2. **关键词判据天生不可证伪**：`REPORT-QUALITY` 的六要素与 `PAPER-TRACE` 的 PG7 都是
   文档级关键词/正则判定，M2「单位错位」与 M5「基准替换」在原理上抓不到（例如把
   「L = 100 m」写成「L = 100000 mm」仍是合法几何描述，把 ITTC-1957 换成「基线 v0」
   仍有「对标」字样）。台账把它们如实标成 58–61%，**不粉饰为合格**；补齐路径是把
   这类要素升级成结构化断言（几何量带单位解析、基准表要能解析到具体工件与版本），
   属于后续单的欠账。
3. **ADMIN 明口**：`OWNERSHIP_BYPASS_ROLES = {"admin"}` 允许管理员补录/归档，响应里
   强制回显 `ownership.bypass`，可在审计面按字段筛出。若未来要求「任何角色都不得跨单
   提交」，把该集合置空即可（一行改动，判据表随之扩展）。
4. **老库兼容**：`_fetch_mission` 先试 `claimed_user_id`（0090 迁移），失败回退到只有
   `claimed_by` 的老 schema；回退路径把「无领取记录」判成 403，因此历史 `queued` 单
   需要先领取再交报告——这是有意的行为变化（旧行为是静默接受不可归属数据）。
5. **测试用假 DB**：`tests/test_report_ownership.py` 用内存假 Session 走真实 FastAPI 栈
   （依赖注入替换 `get_db`/`require_auth`），覆盖路由/序列化/依赖链，但不覆盖真实
   PostgreSQL 的类型/约束（例如 `figures JSONB` 的写入）。生产等价性由既有
   `backend/tests/test_report_gate*.py` 与 CI 集成环境兜底。
6. **未跑全量测试套件**：本单只跑了与报告链路相关的 223 条测试
   （报告门、溯源门、鉴权守卫、本单新增），未跑仓内数万条全量用例——这是工时限制，
   已在账上写明，不冒充「全绿」。

## ⑥ 误差判定（关键量 vs 基准，逐项 + 显式结论）

| 关键量 | 基准 | 本单实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| PAPER-TRACE 基线 kill rate | 92/186 = 49.46%（m1994 台账 JSON） | 92/186 = 49.46% | 0（逐条 JSON 计数相等） | 是（0%） |
| PAPER-TRACE 现状 kill rate | 判据 ≥168/186（90.0%） | 182/186 = 97.85% | +7.85pp（超门槛） | 是（余量 7.8pp） |
| 并发串号率 | 0/8 | 0/8 | 0 | 是（0%） |
| 判据实例化率 | ≥90% | 100% | +10pp | 是 |
| M1 单调性违例 | 0 | 0 | 0 | 是（0%） |
| 域词命中 | 0 | 0 | 0 | 是（0%） |
| 期望/实测 PNG 魔数 | RFC 2083 八字节签名 | 逐字节相等（`89 50 4E 47 0D 0A 1A 0A`） | 0 字节 | 是（0/8 字节） |
| 线程并发时序噪声 | 串号 0 | 8 线程 × 1 轮全对 | 离散判据，无连续量 | 是（离散口径） |

**是否 <3%**：是。所有关键量都是**离散/程序化**判据（JSON 逐条计数、HTTP 状态码、
字节相等、AST 变异体逐个复算），误差为 0；连续物理量基准（海况/阻力/频率）在本单
不适用——这是纯平台后端与判据工程任务，无船体水动力场量，故以离散判据口径给出并
显式声明，未用模糊表述掩盖。诚实声明：**58–61% 的关键词判据不合格**（3.1 表），
本单不把总体 kill rate 当成绩，而是把它当**欠账清单**交给后续单。

## 附录：复算入口

| 产物 | 复算命令 |
|---|---|
| 归属/魔数/工装单测 | `python3 -m pytest tests/test_report_ownership.py -q --noconftest` |
| 判据台账单测 | `python3 -m pytest tests/test_criteria_mutation.py -q --noconftest` |
| 台账 JSON + KPI | `python3 tools/audit/criteria_mutation.py ledger --json-out artifacts/m5496/criteria_mutation_ledger.json` |
| AST 变异审计 | `python3 tools/audit/paper_gate_mutation_audit.py --tests <4 文件> --jobs 32` |
| 单变异体探针 | `python3 artifacts/m5496/probe_kill.py 'binop_swap[38]'` |
| 图 | `python3 artifacts/m5496/make_figures.py` |
| 域词扫描 | `python3 tools/audit/domain_leak_scan.py --paths backend/services/report_ownership.py backend/api/tensor_reports.py tools/report_payload.py` |
