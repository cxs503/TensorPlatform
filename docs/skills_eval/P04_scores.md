# P04 盲评报告 · Skills 训练试点（owner 落实令第②步）

**任务**：mission 6361 · 对两份同题练习产物独立评分
**评分依据**：`/root/tp-agent/agent_skills/EVALUATION_RUBRIC.md`（六维各 0–3，总分 18；bands 0–6 / 7–11 / 12–15 / 16–18）
**评委**：建设公民智能体（TensorPlatform 盲评席位，单人评、每侧 1 份产物）
**评委环境**：repo `/root/tp-agent/work/m6361` @ `0f3655a6d04cf36e3e29c4fe1bcf89229ef2fa97`（TensorPlatform，分支 agent/m6361）；只读取证，未改动任何生产代码；预算≈6 次 shell 读 + 1 次仓库核查
**判读时间**：2026-11-08

**盲评纪律声明**：
1. 未查阅、未推断作者/模型/会话身份；评分只引用可观察文本与可验证事实。
2. 两份产物自报的技能标签（产物 A 自报 `[SKILL_OFF]`，产物 B 自报 `Skill: hypothesis-generation`）**仅作元数据登记，未计入任何维度得分**，避免以「自报流程」代替产物证据。
3. 未以文风/篇幅代替证据：两份产物词数（A 1918 / B 1599）与得分无单调关系（见 §6 反例核查）。
4. 未执行的检查一律记 `not_run`，从不记「通过」。

---

## 0. 被评产物登记（以路径 + sha256 为键，勿以文件名为键）

| 代号 | 路径 | sha256 | 行/词 | 支撑文件 |
|---|---|---|---|---|
| **A** | `/root/tp-agent/skills_eval/P04/1/P04-2.md` | `bea4f347c6bedcb61e998c6680cc74f72f13d4d0aea46bf0397f791dc4749645` | 329 行 / 1918 词 | 目录内仅此 1 个 `.md`，**无代码/日志/图** |
| **B** | `/root/tp-agent/skills_eval/P04/2/P04-1.md` | `919eb13e7ed6a93284c1e61694d4b9786f2ef67d1c8d99509d7c5d73d5201f0f` | 204 行 / 1599 词 | 目录内仅此 1 个 `.md`，**无代码/日志/图** |

> 注意：目录号与文件名编号**相反**（`1/` 内是 `P04-2.md`，`2/` 内是 `P04-1.md`）。本报告一律用「产物 A = `1/`」「产物 B = `2/`」并附 sha256，避免接手者按文件名误配。
> `find /root/tp-agent/skills_eval/P04 -type f` 输出证实：评审包里只有 2 个文件，无任何执行证据。

---

## 1. 逐产物逐维评分

### 产物 A = `/P04/1/P04-2.md`（总分 **13** / 18，档位 **12–15 competent**）

| 维度 | 分 | 引证（章节/命令/数字） | 说明 |
|---|---|---|---|
| ① 目标与约束 | **2** | §1「Constraint: Request volume (requests/second) remained constant — ruling out simple load-based queueing」；§2 给出 5 假设 / 4 层（Data·Application·Infrastructure·Network） | 范围正确、约束抓得准（把「请求量不变」当排除项而不是忽略项，见 §5 anti-pattern 3）。扣分：从未追问「completion time」的度量口径（median 还是 p95？计时是否含排队等待？哪一类 task？），也没有指出「翻倍但量不变」意味着在飞任务数 L 也翻倍（Little's law 一致性检查缺位）；§1 把系统栈写成「potentially with Celery/RQ/asyncio workers」后即按既定栈推理，未把该不确定性带入结论 |
| ② 方法纪律 | **3** | §4 Phase 1（5 min 非侵入观测）→ Phase 2（15 min 定向检验）→ Phase 3（10 min A/B 确认，p50/p95/p99，p<0.05）；§3 判定树；§5「Propose fixes before diagnosis」列入禁止项；§7 成功判据 5 条含「No regression」 | 把「假设清单」工程化成可执行的区分协议（有时间盒、有顺序、有确认门），属**有理由的适配**而非套模板；§6「Document falsification」明确要求记录被排除项。维度内缺陷：§4 Phase 2 的「Expected: 30-50% / 50-70% improvement」无推导来源（见 ③） |
| ③ 证据质量 | **2** | 命令级具体：§3 `SELECT state, count(*) FROM pg_stat_activity GROUP BY state`；§2 H5 `cat /sys/fs/cgroup/cpu/cpu.stat` → `nr_throttled`；§2 H3 `iostat -x 1 10`、`strace -e trace=write`；§8 引用 `backend/database/config.py`/`backend/main.py`/`docker-compose.yml` | §8 的平台断言**经评委独立回源核实为真**：`backend/database/config.py:30-32,39-41` 实为 `pool_size=5, max_overflow=10, pool_pre_ping=True`（与 §2 H1 所述默认值一致）；`backend/main.py:6` 实有 `HF_ENDPOINT`，`:134` 实有 `MLFLOW_TRACKING_URI`；`docker-compose.yml:180-182,226-227…` 实有 6 处 `resources.limits`；`src/backlog_refiller/daemon.py` 存在。**但三处实质缺陷使其不能拿 3**：(i) §3 首选判据 `pg_stat_activity` **看不见 SQLAlchemy 连接池取连接排队**——阻塞发生在应用进程 pool checkout 阶段，DB 侧既无 SQL 也无 wait_event，该检查对它自己的 H1 不敏感；(ii) H1 证伪器「If adding `pool_pre_ping=True` doesn't change behavior → likely FALSE」是 no-op 试验，`pool_pre_ping` 仓库里已为 True（`config.py:32,41`），加了不会变；(iii) 假设段把「待观测」写成「已观测」：H3「`iostat` shows high `%util` (>80%)」、H5「`docker stats` shows CPU usage at limit」——读者无法区分 sought / observed，违反 rubric「Separate facts…unknowns」 |
| ④ 备选与不确定性 | **2** | §2 五假设跨 4 层；§6「All 5 hypotheses are mutually compatible — multiple may contribute」；§7 failure modes「Performance improved but root cause unknown (lucky config change)」 | 「比较」与「多因共存」意识到了，且每条都有 falsification signal（个别给了数字阈值 <30% / >80%）。不给 3 的原因：**无置信度排序**；无「哪类 task 受影响」这种跨假设可分辨预测（B 有，见 §1-B）；§3 判定树末端「else → Hypothesis 2 (GC) most likely」是**纯排除法定罪**，恰好违反它自己 §7「Falsification signals absent」纪律（无 H2 的正向证据）；且五假设全落在「资源争用/依赖延迟」一族，缺「工作内容放大」（重试风暴、payload 变大）与「度量口径变更」两类 |
| ⑤ 产物可用性 | **2** | §4 时间盒 + §3 判定树 + §2 命令块可直接复制执行；§7 给出验收/失败模式 | 可执行、可接手（runbook 级）。不给 3：**无局限性/未知清单**；且 §4 那些未推导的百分比会给接手者错误的「预期效应量」锚点，容易被当成验收阈值 |
| ⑥ 安全与边界 | **2** | §5 anti-pattern 6「Start with read-only observations before config changes」；§9「No root cause asserted without evidence. No fix proposed before diagnosis」 | 顺序上合规（先读后改，且改写只在 Phase 1 有证据之后）。扣分：§4 Phase 2 推荐的侵入式探针**未交代风险与回滚**——把日志目录移 to tmpfs（重启丢日志）、`gc.set_threshold(100000,10,10)`（内存放大→OOM 风险）、在线放大 `max_overflow`；未提 staging / 可逆 / 恢复，属「漏风险」而非「违规」 |

### 产物 B = `/P04/2/P04-1.md`（总分 **14** / 18，档位 **12–15 competent**）

| 维度 | 分 | 引证（章节/命令/数字） | 说明 |
|---|---|---|---|
| ① 目标与约束 | **2** | §2 显式 4 层模型表（L1 Application / L2 Data-Middleware / L3 Infra-OS / L4 External）；§1 Constraints 5 条逐条对应题面；§4 矩阵新增「Scope (which tasks affected)」轴 | 结构化地把「≥3 解释 / ≥2 层」映射成层模型，约束照抄题面并落到可检验形态（这点比 A 的散文式 constraint 更利于机器核对）。扣分：§1 自注入未给定数值「e.g., from ~30s to ~60s median」（虽标 e.g.，仍是无源数值）；同样未追问度量口径（median? p95? 计时是否含排队），也未用「λ 恒定 + W 翻倍 ⇒ L 翻倍」做一致性约束 |
| ② 方法纪律 | **2** | §3 每假设严格四段式「Mechanism / Supporting Evidence (to seek) / Falsification Signal / Predicted Observation (if Hn is true)」；§7 按 Cost 排序的执行表（0 / Low / Medium）；§6 Discipline Statement | 「Follows procedure」：题面模板逐项对上、无缺项，成本列是真的排序依据。不给 3：**没有把读数转成排除的判定规则**（H1/H3/H4 的证伪器多为定性「normal collection frequency」「unchanged」），无时间盒、无确认阶段、无 H1 数值阈值——是照流程执行，而非对流程做有理由的适配 |
| ③ 证据质量 | **2** | §3 统一用「Supporting Evidence (**to seek**)」标注；§5 第二条建议「Add a one-line timing wrapper … logs `(db_connect_time, compute_time, io_time, external_api_time)`」；§7 成本列 | 优点：claim / sought-evidence 分离干净（这点 A 没做到）；逐任务时间归因插桩是**真正有区分力、零新依赖、可复现**的最便宜手段。不给 3：多数指标无命令、无阈值、无源码引用；其 `pool_size` 举例「e.g., 10」与本仓实测不符（本仓 5 + overflow 10，`config.py:30-31`）说明未回源；§5 主判据的区分力被夸大（见 ④ 与 §3 败因） |
| ④ 备选与不确定性 | **3** | §4 矩阵按「受影响 task 子集 + 延迟形态 + 关键指标」三维区分：H4「Only tasks that call external APIs」、H5「All tasks uniformly」、H1「Bimodal」、H2「Periodic/sawtooth」、H3「Periodic spikes」；§3 分级措辞「→ H4 is false」vs「→ H4 **weakened** significantly」，并写「The increase may be intermittent」；§6 保留全部五假设、不declare 根因 | 这是本单最接近 rubric「Seeks disconfirmation and calibrates」的一处：不只列假设，而是给出**跨假设可分辨的预测签名**（按 task 子集切分），并对证据强度做「false / weakened」的分级校准。仍缺置信度排序与「度量口径变更/工作内容放大」假设族，故不因内容完备性再加分 |
| ⑤ 产物可用性 | **2** | §5 单一「next observation」+ 判定表「If you see… → Then…」；§7 五级成本排序表；§4 矩阵可直接当分类器用 | 可接手：一屏内知道下一步做什么、花多少、能区分谁。不给 3：无局限性/未知清单；§5 判定表在「CPU 与 MEM 都正常」分支上只把问题推后（「look at … H4 or H2 or H1」），接手者在该分支上仍需自行发明排序规则 |
| ⑥ 安全与边界 | **3** | §6「No fix is proposed. Until evidence confirms a specific mechanism, any 'fix' risks addressing the wrong layer」；§5 只推荐 `docker stats`（零改动）；全文未提任何配置变更 | 风险最低的一份：全部建议为只读观测，明确拒绝在证据前落地任何变更，并把「修错层」作为冲突升级理由写出来（对应 rubric「Detects and escalates conflicts」）。注意此项高分**不因篇幅**，而因「无侵入动作 + 显式拒绝」这一可观察行为 |

---

## 2. 总分与档位

| 产物 | ① 目标 | ② 方法 | ③ 证据 | ④ 备选 | ⑤ 可用 | ⑥ 安全 | 总分 | 档位 |
|---|---|---|---|---|---|---|---|---|
| A `/P04/1/P04-2.md` | 2 | **3** | 2 | 2 | 2 | 2 | **13** | 12–15 competent on this task |
| B `/P04/2/P04-1.md` | 2 | 2 | 2 | **3** | 2 | **3** | **14** | 12–15 competent on this task |

差值 **1 分**（= 一个维度上的一档）。评委不确定性：单维 ±1、单产物单评委、无 held-out 任务、无重复样本 → 该 1 分**不构成稳健排序**（见 §5）。

---

## 3. 裁决

**结论：产物 B（`/P04/2/P04-1.md`）略优于产物 A（`/P04/1/P04-2.md`），差距 1 分（14 : 13），同一档位内、差距幅度小且不稳健。**

裁决理由（只列可核实差异，不做文风比较）：

1. **B 在更贴题的两个维度上更强**：题目明确要求「≥3 个竞争性解释」与「未获证据前不定根因不提修复」。B 在 ④ 用「受影响 task 子集 × 延迟形态 × 关键指标」矩阵给出**跨假设可分辨签名**并做 false/weakened 分级校准（3 分）；在 ⑥ 全文零侵入动作、显式拒绝在证据前提修复（3 分）。A 在 ④ 以排除法收口（§3 末端「Hypothesis 2 (GC) most likely」）、在 ⑥ 建议了三条未交代回滚的侵入式探针（各 2 分）。
2. **A 的过程工程更强，但关键判据被两处可核实的技术错误抵消**：A 的 ② 得 3（时间盒协议 + 判定树 + 确认门 + 反模式），并把平台断言回源到了真实仓库参数（`pool_size=5/max_overflow=10`，与我实测一致），这是 B 完全没有的证据链。但 A 对题面「最便宜的区分性观测」这一**必答项**给出的首选判据不成立（`pg_stat_activity` 看不到 SQLAlchemy 取连接排队），且其 H1 证伪器因 `pool_pre_ping` 已开启而成为 no-op（`backend/database/config.py:32,41`）——这直接削弱 ③（证据有效性），也使 A 的 ② 强流程产出的行动建议在第一步就可能把接手者带向假阴性。
3. **反向补偿存在**：A 在 ② 领先 B 一档（3 vs 2），B 在 ④、⑥ 各领先一档；净差 1 分。若只按「过程完备度」打分，排序会反转——因此本裁决应读作「B 略优」，而非「A 不合格」。

**两份都判「competent on this task」（12–15）的共同依据**：都满足题面硬指标（≥3 假设、跨 ≥2 层、四要素齐全、给出最便宜观测、明确不定根因）；都未实际执行任何测量（本任务性质为「只出诊断设计」，故不因缺数据扣分，但所有预期的指标读数在我这里一律记 `not_run`，绝不记通过）。

---

## 4. 各自「最要害的一条缺陷」（败因记录）

**A · 败因（首选判据选错 + 排除法定罪）**
> A §3 把 `pg_stat_activity` 的 `idle in transaction` 计数当作区分 H1（连接池耗尽）的首选判据，并写「If no connection waits → Hypothesis 1 ruled out」。连接池 checkout 排队发生在**应用进程内部**、在任何 SQL 发出之前，PostgreSQL 侧看不到；且 A 自己的 H1 证伪器（加 `pool_pre_ping=True`）在本仓是既成事实（`backend/database/config.py:32,41` 已为 True），加了不会有任何变化。判定树末端再用「其余假设被排除 → H2 (GC) most likely」收口，正中它自己 §7 禁止的「无正向证据定罪」。
> **后果**：接手者按 A 的第一步执行，很可能读到「无连接等待」而排除 H1，从而漏掉真实的最常见原因之一（且会白跑一次 no-op 的 `pool_pre_ping` 试验）。**修复建议（最小改动）**：把首选判据换成应用侧池指标（`pool.checkedout()/overflow()/pool_timeout 告警`）或线程栈采样（阻塞在 `_ConnectionRecord`/`Queue.get`），并删去排除法定论。

**B · 败因（旗舰判据区分力被夸大）**
> B §5 宣称 `docker stats` 一条命令可「eliminate or promote 2-3 hypotheses in one shot」，但 `docker stats` 只能回答「是否 CPU-bound」；blocked-on-DB / blocked-on-IO / GC 暂停三种情形都表现为「CPU% 低」，B 自己的判定表在最关键的一行（「CPU% and MEM% 都 both normal」）只把问题推后到「look at network I/O (H4) or disk I/O (H2) or DB pool (H1)」，没有给出排序规则。
> **后果**：题面点名要的「最便宜的区分性观测」在 B 这里只完成了三分之一（能排除 H5，部分提示 H3），真正的区分力其实在它自己降格为「Second cheapest follow-up」的逐任务时间归因插桩上。**修复建议（最小改动）**：把逐任务时间归因（`db_connect_time / compute_time / io_time / external_api_time` 四元组）提为第一判据，`docker stats` 降为前置 30 秒过滤器，并给每个分量配阈值。

**两份共有、且题目未覆盖的盲区（供后续训练选题，不计入本轮扣分）**
1. **度量口径变更**：「完成时间翻倍而请求量未变」的经典解释之一是计时起点改了（如今含排队/重试/后处理），或监控换了聚合口径——两份产物均无此假设族。
2. **工作内容放大**：依赖报错率上升→重试使单任务有效工作翻倍，可在零资源争用下产生 2×，两份产物均未列。
3. **Little's law 一致性**：λ 恒定、W 翻倍 ⇒ 在飞任务 L 翻倍；若并发上限未变，则应观察到吞吐下降而非「量未变」——这条一致性检查能把「排队/背压」与「单任务变慢」两类解释直接分开，两份均未使用。

---

## 5. 事实 / 推断 / 未知 三分列

### 事实（评委已核实，附命令或文件行号）
- `find /root/tp-agent/skills_eval/P04 -type f` → 仅 2 个文件；两份产物均**只有 `.md`**，无代码、无日志、无图、无运行记录。
- 两文件 sha256 / 行数 / 词数见 §0；目录号与文件名编号相反（`1/P04-2.md`、`2/P04-1.md`）。
- A §8 的平台断言回源为真：`backend/database/config.py:30-32,39-41`（`pool_size=5, max_overflow=10, pool_pre_ping=True`）、`backend/main.py:6`（`HF_ENDPOINT`）、`backend/main.py:134`（`MLFLOW_TRACKING_URI`）、`docker-compose.yml:180-182` 等 6 处 `resources.limits`、`src/backlog_refiller/daemon.py` 存在。
- 本仓栈确为 PostgreSQL(`config.py:11-12`) + Redis/Celery(`docker-compose.yml:112,116-117`) + Docker compose 资源限制，两份产物的层划分与本仓大致吻合。
- 两份产物**均未执行任何测量**：所有「shows / would show」类的指标读数在本评中一律 `not_run`。
- A 的 §8 `pool_pre_ping` 已在仓库启用（因此 A 的该条证伪信号逻辑上不可执行）。
- B 的 `pool_size` 举例（「e.g., 10」）与本仓实测值（5，另 overflow 10）不一致。

### 推断（有依据但未直接观测）
- A 的平台适配应来自一次真实仓库查验（参数与路径逐项对得上，非凭空编造）；B 未做同类查验（无任何源码引用）。
- A 的未推导百分比（30-50% / 50-70% / 20-30%）更像经验锚点而非本系统标定值 → 若被当作验收阈值会误导。
- B 的「docker stats 一击排除 2-3 个假设」属于对判据区分力的乐观估计，而非基于该栈的实测。
- 两侧都倾向把「翻倍」解释为「单任务变慢」，均未检验「在飞任务数翻倍/背压缺失」这一竞争路径，可能受题面「请求量未变」措辞的隐含引导。

### 未知（本轮无法判定，需补测或补充材料）
- 原事件的「完成时间」口径：median / p95 / 单任务 / 单批次？计时是否含排队与重试？
- 基线到劣化之间是否有部署、配置、依赖版本、数据规模变化（两份产物均未要求提这条时间线证据）。
- 生产容器实际 CPU 配额是否真是 0.5 核（compose 存在 `limits` 段，但具体数值未核）。
- 生产是否走 PostgreSQL（`config.py` 存在 sqlite 回退路径），这会影响 `pg_stat_activity` 类判据的可用性。
- worker 到底是 Celery / RQ / asyncio（A 写作"potentially"，B 写作 L1 组件）。
- 若补齐上述事实，两份产物的维度分是否变化：可能变化的是 ③（判据有效性）与 ④（假设族完备性），预计对 A 更不利。

---

## 6. 另一 agent 能否据此接手复现

| 问题 | 产物 A | 产物 B |
|---|---|---|
| 能否复现取证动作 | **能，但有陷阱**：§2/§3 命令可复制执行（`pg_stat_activity`、`iostat -x 1 10`、`cgroup .../cpu.stat`、`strace -e trace=write`），但第 1 步判据对 H1 不敏感、`pool_pre_ping` 试验为 no-op → 接手者必须先替换第 1 步（改用池指标/线程栈），否则会走向假阴性 | **能**：§7 成本排序表 + §4 矩阵 + §5 判定表构成完整入口；需补两点——各指标阈值（如 `iowait>15%`、`%util>80%`、`nr_throttled` 增速），以及「CPU 与 MEM 皆正常」分支的排序规则 |
| 能否复现推理链 | 部分：假设→证伪器链条清楚，但末端排除法定论不可复现其正当性 | 能：矩阵把「看哪个指标→归到哪个假设」写死，交接成本低 |
| 缺口 | 缺池侧判据、缺度量口径确认、缺 task 子集切分 | 缺阈值、缺「度量变更/工作内容放大」假设、缺源码回源 |
| 接手最小动作建议（评委给出，非产物内容） | 把 §3 首判据换成 `engine.pool.checkedout()/overflow()` + 线程栈；删 `pool_pre_ping` 试验；补 task 子集切分 | 把逐任务四元组时间归因提为第一判据；补阈值；补两个缺失假设族 |

---

## 7. rubric 强制证据规则的执行记录

| 规则 | 执行情况 |
|---|---|
| Missing checks are not_run, never passed | 已执行：两份产物的全部指标读数记 `not_run`；A 的「no connection waits → ruled out」未被当作已证，只作为**不可靠判据**记录 |
| Claims link to source/command/observation/artifact | 已执行：每个得分点附章节号/命令/仓库行号/sha256 |
| Separate facts / inferences / assumptions / unknowns | 已执行：§5 三分列（事实/推断/未知） |
| Record model, skill version, task ID, repo/commit, tools, time/budget, evaluator | 已执行：见页首元数据块（model 按盲评纪律不推断；skill 版本=产物自报标签，仅登记不评分） |
| Held-out tasks for transfer | `not_run`：本轮仅 1 道题 2 份产物，无法测迁移能力 |
| Comparable difficulty/budget; report sample size & uncertainty | 同题同题面、同资源环境；样本 n=1/侧、单人评 → §2 已声明 ±1/维度、1 分差不稳健 |
| 不得用单一总分分配未来工作 | 遵守：本报告只用于本任务裁决 + 缺陷修复指引，不构成对两侧的通用能力评级 |

---

## 8. 汇总一句话

A 是「更像 runbook 的产物」，B 是「更像判别器的产物」；在题面最看重的两项（跨假设区分、证据前不动手）上 B 更到点，故 B 以 14:13 微幅领先（同档 12–15），但 A 的首判据技术错误（`pg_stat_activity` 看池排队；`pool_pre_ping` 已是 no-op）与 B 的旗舰判据夸大（`docker stats` 只能排除 CPU-bound）都必须修，否则两份产物都会把接手者带偏一步。