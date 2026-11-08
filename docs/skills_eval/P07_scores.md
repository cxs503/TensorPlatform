# P07 盲评评分报告（Skills 训练试点 · 独立评分）

- 任务：TensorPlatform mission 6364；评分对象 = `/root/tp-agent/skills_eval/P07/1/`（下称 **产物 A**）与 `/root/tp-agent/skills_eval/P07/2/`（下称 **产物 B**）
- 练习原题：评审一个只有 happy-path 测试的文件上传端点——先自拟设计文档与测试清单，再独立评审（重建执行路径、探测失效模式、区分已确认缺陷与未测风险、严重度排序、处置意见）
- 评分标准：`/root/tp-agent/agent_skills/EVALUATION_RUBRIC.md`（六维各 0–3，档位 0–6/7–11/12–15/16–18），**无证据的项记 `not_run`，不给分**
- 评审者：`agent/m6364` worker（盲评；不推测、不评价作者/模型身份；只评可观察行为与产物）
- 评分环境（本人实测）：loongarch64 / Python 3.11.6 / fastapi 0.136.1 / sqlalchemy 2.0.51 / starlette 1.0.0；被评审仓 TensorPlatform 检出 head `0f3655a6`（两产物均自述基线 `4b3b8ae3`）
- 工作量：读两份正文 + 支撑代码/JSON/清单；**独立复跑两套探针**（A：10 探针、B：38 记录）与 A 的作者测试套件；复核 A 的 5 个 sha256 与 10 个静态 grep；抽查 B 的 15 处 `file:line` 与 4 处结构性断言
- 时间/预算：单次评分会话，本机复跑 A（含 200 MiB 流式上传）与 B（38 记录）均为数十秒量级，未精确计时、未上集群

---

## 1. 逐产物逐维评分

### 产物 A（`P07/1/P07-1.md` + `P07-1/`）

| 维 | 分 | 一句引证（可核） |
|---|---|---|
| ① 目标与约束 | **3** | 忠实复现题设：自拟契约 `design_doc.md` §3 AC-1..AC-8 + 清单 `test_checklist.md` T-01..T-12（status `planned/pass` 列区分已写/未写），并在 §4 末「未被采纳为缺陷的疑似问题（避免凑数）」显式权衡 404/403 枚举与 `Content-Type`——找到了边界而非照单全收。 |
| ② 方法纪律 | **3** | §8 给出 Procedure 1–10 逐条落点表（例如第 9 步「缺测标 unknown」→ §6 全表 `not_run`/unknown），§2 分列已执行/未执行，§3 逐层重建执行路径并就地标 ✗（属「按流程走 + 说明适应」）。 |
| ③ 证据质量 | **2** | 加分（我实测）：§0 五个 sha256 与我 `sha256sum` **5/5 完全一致**；10 个探针我独立复跑 **判定 10/10 一致**（9 CONFIRMED_DEFECT + 1 OK）；`runs/static_checks.txt` 的 10 个 grep 计数（`413`=0、`os.replace`=0、`owner`=3…）我复核 **10/10 一致**；行号引用 `:105/:106/:109/:111/:124/:128` 与 `candidate/upload_service.py` 一致。扣分（两项，均经复核）：(a) **证据链不可闭合**——§0 声明「数字表由 `gen_findings.py` 从 `probe_results.json` 程序化生成（报告中数字无手抄）」，但 `runs/probe_results.json`（mtime 22:12）已被第二轮复跑**原地覆盖**为 P10「2 个中间态 / sha `124d4a1285e0`」，而 `runs/findings_table.md`（22:10）与 `P07-1.md` §4 H4 引用的是第一轮读数「3 个中间态 / sha `18adbb501f53` + `e3b0c44298fc`（空串）」——即存档 JSON 与由它派生的表已不一致，重跑 `gen_findings.py` 会得到不同数字；我 22:57 独立复跑又得 2 个中间态 / `39ca14ce36f6`（判定不变）。(b) 评审对象是作者自写候选实现（`design_doc.md` 首段自述「由作者预先自拟」），缺陷由构造已知，故「happy-path 掩盖 critical 缺陷」只是**构造性演示**而非审计发现。 |
| ④ 备选与不确定性 | **3** | §6 七项 `not_run` 均带「为什么没做 + unknown」；§4 H1 显式分出「磁盘/配额=事实」与「RSS 线性增长=推断、未单独测量」；§5 设 overstated claims 表逐条把作者自述判为不成立；M2 主动记录「中断不破坏既有版本」这一缓解事实并声明是「AC-6 未实现」而非实现错误（在给自己减分）。 |
| ⑤ 产物可用性 | **3** | §7 七项整改**每项都配「必须由探针断言」的验收**（如「P02 得 403 且原文件 sha 不变」「P01 得 413，`UPLOAD_ROOT` 无残留」），下一位评审可机器裁定；§10 文件索引 + §9 五条可复用打法（如「先写后校验=回滚缺陷的根因形状」）。 |
| ⑥ 安全与边界 | **3** | §1 主动披露独立性冲突并说明缓解（结论只锚在可重放输出上），§6 把真实平台端点标 `not_run`、只作静态引用；探针在临时目录与内置令牌表内运行、未触生产。边界卫生扣留项（不足以扣分）：宿主 `/tmp/p07_escape.csv` 仍在（12 B，mtime 22:59，早于本次复跑 → 穿越探针产物从未清理），200 MiB 上传文件亦留在临时上传目录，探针无清理步骤。 |
| **小计** | **17 / 18** | 档位：**16–18 strong on this task** |

### 产物 B（`P07/2/P07-2.md` + `P07-2/`）

| 维 | 分 | 一句引证（可核） |
|---|---|---|
| ① 目标与约束 | **3** | 评审前冻结 `design_spec.md`（10 条 INV + 威胁模型 T1–T6 + NFR-1..5，57 行）与 `test_plan.md`（26 条用例，P0 14/P1 10/P2 2）；§0 主动纠正题设「既有测试覆盖 **0 个测试文件**，比题设更差」——我全仓检索 `test_*.py`/`*_test.py` 中 `file_upload\|uploaded_files` **命中 0**，该断言成立；§5 先立 S0–S3 判据再排序。 |
| ② 方法纪律 | **3** | §1.1 五步法 + §1.2 探针质量控制（`SKILL_OFF` 显式声明、`UPLOAD_DIR`/`MAX_FILE_SIZE` 仅测试期可逆替换、`DATABASE_URL` 前置注入、A-00 负控）；§2 执行路径含中间件栈 `backend/main.py:598 → middleware/auth_guard.py:179`——我按行号核对两处**均命中**。 |
| ③ 证据质量 | **3** | 加分（我实测）：38 条记录我独立复跑，**id/判定/严重度 38/38 完全一致**（17 CONFIRMED_DEFECT、10 OK、5 ACCEPTED_RISK、6 INFO；critical 3/high 5/medium 5/low 4），C-08「7/9 语句可执行」同样复现；抽查 15 处 `file:line`（`file_upload.py:104/114/116/79/84/143/152/165/303/311/325`、`main.py:598`、`auth_guard.py:179`）在当前 head `0f3655a6` 全部命中；`require_permission` 计数 `datasets.py`=15 / 本模块=0 复核成立。**留档缺陷（不足以降该维，但必改）**：§2「表有 … 等 **11 列**，实现只写 **6 列**」实测为**19 列**（`backend/migrations/011_file_upload.sql:5-23`）与 **7 列**（`backend/api/file_upload.py:134-136` 含 `uploaded_at`）——未绑定命令输出的手抄数字。 |
| ④ 备选与不确定性 | **3** | §0 把第一版「无鉴权」误判（隔离挂载 router 绕过 `main.py:598` 最外层守卫）保留为负控 A-00，属**自我证伪**；U-01..U-08 未测风险逐条给补测成本；§7 声明「D-03 严重度取决于部署取舍，需 owner 决策，而不是缺陷消失」，不把自拟契约当外部权威。 |
| ⑤ 产物可用性 | **3** | §6 处置分层（≤0.5 人日 / 1–2 人日 / 需决策）配 §4 每条 `file:line` 最小修复；§8 复现命令我按其运行成功；「P0 14 条转为回归门」给出落地口径。局限（如实记）：整改项未逐条附机器可判验收（对比 A §7），「26 条用例可直接转 pytest」是建议、未交付 pytest 文件。 |
| ⑥ 安全与边界 | **3** | 声明 `SKILL_OFF`、只产出 `docs/skills_training/`、`src/ backend/ migrations/` 零改动；探针仅改测试期常量并用临时目录；续传/去重/配额等跨模块项上抛决策；图标签用英文并说明原因（`fc-list :lang=zh` 为空），未掩盖。 |
| **小计** | **18 / 18** | 档位：**16–18 strong on this task** |

### 总分与档位

| 产物 | 六维合计 | 档位 | 一句话形态 |
|---|---|---|---|
| A（P07/1） | **17 / 18** | 16–18 strong | 方法论完整、整改可机器验收；但审计对象是自己写的代码 |
| B（P07/2） | **18 / 18** | 16–18 strong | 真实生产模块的真审计，缺陷台账可直接施工；§2 有两个手抄数字错 |

> 敏感性（诚实披露）：若把 B 的 §2 两个列数错判为「证据不可引用」，则 B ③ 降为 2 → 17:17 并列；本文按「该错不影响 17 条缺陷链（`uploaded_by` 从不写入已复核为真）」保留 3 分，并在 §4 记为必改项。反过来，若把 A 的自造对象判为致命，A ③ 可降为 1 → 16:18，档位不变。

---

## 2. 裁决

**产物 B 更好，差距 1 分（18 vs 17，≈5.6%），同属 16–18「strong」档，不构成档位差。**

理由（按影响力排序）：

1. **审计独立性（③ 的决定项）**：B 的评审对象是仓内已存在的生产模块（`backend/api/file_upload.py`）在固定提交上的代码，缺陷不可能「由评审者放置」，我按 `file:line` 与 38/38 复跑双向验真；A 的评审对象是作者自己写的 134 行候选实现，9 条缺陷属**构造性已知**，因此其招牌结论（「3 项 critical 在 happy-path 下完全不可见」）是演示、不是审计发现——这恰是 rubric 强制规则「用 held-out 任务测迁移、复述练习不证明通用能力」所指。
2. **产物可直接施工**：B 的 17 条缺陷每条带 `file:line` + 最小修复，且我按 `file_upload.py:104/114/116/79/84/143/152/303/311/325` 逐点核对成立，任何 agent 可立刻开修；A 的整改是「对合成端点的形状建议」，无法直接落到平台代码。
3. **A 的领先项（不足以翻转）**：A §7 的整改验收是探针可判的（B 缺这一层），A §8 与技能 Procedure 1–10 的逐条映射（B 声明 `SKILL_OFF`、无映射），A §5 overstated-claims 表对「自述 vs 证据」的式样更完整。

**未用于裁决的项**：文风、篇幅、能否画图（两份都有图，A `runs/findings.png`、B `p07_2_probe_summary.png`，均声明由 `probe_results.json` 程序化生成，我未逐像素核图，记 `not_run`）。

---

## 3. 败因记录：各自「最要害的一条缺陷」

**产物 A —— 证据链不可闭合：它宣称的「零手抄」在存档里已不成立。**
`P07-1.md` §0 写「数字表与图由 `gen_findings.py` 从 `probe_results.json` 程序化生成（报告中数字无手抄）」，`gen_findings.py:18` 确实只读该 JSON。但 `runs/probe_results.json`（mtime 22:12）被第二轮复跑**原地覆盖**：现在存的是 P10「2 个中间态 / `124d4a1285e0`」；而 `runs/findings_table.md`（22:10）与报告 §4 H4 引用的是第一轮「3 个中间态 / `18adbb501f53` + `e3b0c44298fc`（空串 sha）」。我 22:57 独立复跑又得 2 个中间态 / `39ca14ce36f6`（见 `runs/verification_log.txt`）。后果：**存档 JSON 与由它派生的表/正文已不一致，任何人都无法从现有工件重算出报告里的 P10 读数**；判定（无互斥 + 撕裂读 = CONFIRMED_DEFECT）在所有轮次一致，所以这是引用级而非结论级缺陷——但它正好落在这个产品的招牌声明（零手抄）上。
次要害：评审对象是同源自造（`design_doc.md` 首段、`P07-1.md` §1 自陈），使「happy-path 掩盖 3 项 critical」只是构造性演示，外部效度不成立——这是它相对 B 落后的结构性原因。

**产物 B —— §2 结构错位段的两个数字是手抄而非取证，恰违反它自己声明的取证纪律。**
`P07-2.md` §2 称「表有 … 等 **11 列**，实现只写 **6 列**」；实测迁移 `backend/migrations/011_file_upload.sql:5-23` 为 **19 列**，实现 `backend/api/file_upload.py:134-136` 写 **7 列**（多一个 `uploaded_at`）。结论（`uploaded_by` 从不写入 → 文件级授权不可能实现）在 `grep -c uploaded_by file_upload.py` = 0 下仍成立，所以 17 条缺陷链不受影响；但该段是报告里唯一**未绑定命令输出**的量化陈述，属可复现的引用级错误，必须改成「`SELECT`/`PRAGMA table_info` 输出 + 列数」再引用。

---

## 4. 事实 / 推断 / 未知（三分列）

### 事实（本会话实测，命令与读数）

| # | 事实 | 取证 |
|---|---|---|
| F1 | A 的 5 个产物 sha256 与报告 §0 列出的前 12 位**完全一致** | `sha256sum design_doc.md test_checklist.md candidate/*.py probe_upload.py` → `f04476187e74 / 19649f25c5ff / 7dfe56d9cd57 / b80463d0fcc5 / 5b990022f48d` |
| F2 | A 的 10 探针复跑判定与存档 **10/10 一致**（9 缺陷 + 1 OK） | 副本内 `python3 probe_upload.py` → `summary: confirmed_defect=9 ok=1 not_run=0 total=10`；逐 id 比对 `agree True` |
| F3 | A 的静态 grep 计数 10/10 与 `runs/static_checks.txt` 一致 | `grep -c` 于 `candidate/upload_service.py`：`MAX_UPLOAD`0 `413`0 `Content-Range`1 `os.replace`0 `logging`0 `owner`3 `403`0 `Range`1 `tempfile`0 `Lock`0 |
| F4 | A 的作者套件复跑 `3 passed` | `python3 -m pytest candidate/test_upload_happy.py -q` → `3 passed, 1 warning in 0.68s` |
| F5 | A 的 P10 读数**三处各不相同**，且存档 JSON 与其派生表不一致 | 第一轮 `runs/probe_output.txt:62-63`「3 个中间态，sha `18adbb501f53`+`e3b0c44298fc`」；存档 `runs/probe_results.json`（mtime 22:12）「2 个中间态，`124d4a1285e0`」；我复跑（22:57）「2 个中间态，`39ca14ce36f6`」。`runs/findings_table.md`（22:10）P10 行仍写「3 个不同中间态」→ 表与 JSON 矛盾，判定（CONFIRMED_DEFECT）三轮一致 |
| F6 | B 的 38 条记录复跑**逐条一致**（id/verdict/severity） | 把 `P07-2/` 置于 `<tmp>/docs/skills_training/` 并软链 `backend` 后 `python3 probe_upload.py --out /tmp/p07_2_rerun.json` → `38 records`、`{'OK':10,'CONFIRMED_DEFECT':17,'ACCEPTED_RISK':5,'INFO':6}`；比对 `verdict diffs: {}` |
| F7 | B 的 `file:line` 引用在当前 head 命中 | `sed -n` 抽查 `file_upload.py:76/79/84/88/101-104/114/116/130/143/152/165/303/311/325`、`main.py:597-598`、`auth_guard.py:179` 全部命中目标语句 |
| F8 | B 的「既有测试 0 个」成立 | 全仓 `grep -rl "file_upload\|uploaded_files" --include="test_*.py" --include="*_test.py"` → 0 命中；`backend/tests/` 存在 |
| F9 | B 的 §2 两个列数错 | 迁移 `CREATE TABLE uploaded_files` 列数 19（`011_file_upload.sql:5-23`）；`INSERT INTO uploaded_files (…)` 列数 7（`file_upload.py:134-136`） |
| F10 | B 的 C-08「7/9 迁移语句可执行」复现 | 复跑末行 `C-08 … 7/9 语句可执行；失败 2 条`；迁移 `;` 计数 9，失败项为 `CREATE OR REPLACE VIEW` 与 `FROM (VALUES …) AS t(cat)` 种子 `INSERT` |
| F11 | A 的图/表 mtime 早于 JSON，证明其「由 JSON 生成」的链条已被覆盖破坏 | `ls -l runs/`：`findings_table.md`/`findings.png`/`probe_output.txt` = 22:10，`probe_results.json`/`probe_output_rerun.txt` = 22:12（`runs/verification_log.txt` 同证） |

### 推断（有证据链但不是直接观测）

| # | 推断 | 依据与置信 |
|---|---|---|
| I1 | A 的 H4 具体读数依赖并发调度，不可作为稳定引用；结论本身（无互斥 + 撕裂读）稳定 | 由 F5 两次运行差异推断；置信高（判定稳定、读数不稳） |
| I2 | B 的 17 条缺陷修起来会互相牵连（先授权后围栏的顺序 B 自己已指出），改造风险高于其「≤0.5 人日」估值 | 由 D-01/D-03 共享根因族 + B §5「排序结论」推断；置信中 |
| I3 | A 的候选实现之所以能稳定复现 10/10，是因为它是确定性本地实现（无外部依赖、sqlite 单进程） | 由 A 的 `TOKENS` 内置表与 `_init_db()` 推断；置信高 |

### 未知 / 未测（一律记 `not_run`，不记 pass）

| # | 未知 | 为什么没做 |
|---|---|---|
| U1 | 两份产物在**不同架构/不同机器**上的复现性 | 仅在 loongarch64 单机复跑；B 自述 4b3b8ae3、我在 0f3655a6 复现，跨架构未测 |
| U2 | A 的 RSS 随文件尺寸曲线（其 §6 自认 unknown） | 需 `/proc/<pid>/status` 采样；我未测 |
| U3 | B 的 PostgreSQL 分支（U-04：`SELECT *` 列序、`.isoformat()` 崩溃） | 环境未起 PG；仅复现 SQLite + fake-row 部分 |
| U4 | 两份报告的图（A `findings.png`、B `p07_2_probe_summary.png`）是否与 JSON 数字逐项一致 | 只确认生成脚本存在（A `gen_findings.py`、B `make_figure.py`），未逐像素/逐柱核对 |
| U5 | A 的 `runs/findings_table.md` 与 `probe_results.json` 的数值一致性 | 未逐行 diff |
| U6 | B 的 `test_plan.md` 26 条用例与 38 条记录的**双向映射完整性** | 记录中 8/38 条 `dimension` 为 `n/a`/空、含 `T-01..T-07`/`auth_probes` 等聚合行（6 条 INFO），映射需读 779 行脚本，未逐条建表 |

---

## 5. 另一 agent 能否据此接手复现（可交接性）

能，两份都可复现，命令如下（我本人已跑通，A 在副本内执行、B 需保持 `<repo>/docs/skills_training/P07-2/` 相对位置）：

```bash
# A：副本内跑探针 + 作者套件
D=$(mktemp -d); cp -r /root/tp-agent/skills_eval/P07/1/P07-1/. "$D"/
cd "$D" && python3 probe_upload.py                     # → 9 CONFIRMED_DEFECT / 1 OK（10/10 与存档一致）
python3 -m pytest candidate/test_upload_happy.py -q    # → 3 passed

# B：在仓库相对位置跑探针（探针用 HERE.parents[2] 定位 repo）
S=$(mktemp -d); mkdir -p $S/docs/skills_training
ln -s <TensorPlatform>/backend $S/backend
cp -r /root/tp-agent/skills_eval/P07/2/P07-2 $S/docs/skills_training/P07-2
cd $S/docs/skills_training/P07-2 && python3 probe_upload.py --out /tmp/p07_2_rerun.json
# → 38 records；17 CONFIRMED_DEFECT / 10 OK / 5 ACCEPTED_RISK / 6 INFO
```

**交接注意（B）**：`probe_upload.py` 依赖 `HERE.parents[2]` 定位仓库根，脱离 `docs/skills_training/P07-2/` 直接运行会 ImportError；这是复现时唯一的路障。**交接注意（A）**：探针会在 `/tmp` 留下 `p07_escape.csv` 与 200 MiB 上传产物，复跑前先看磁盘。

---

## 6. 评分者自检（rubric 强制项）

- 是否理解任务：是——两产物同题、条件可比（同仓同基线、同探针方式、同宿主），未用「互相复述」的回忆型证据。
- 是否执行必需步骤：六维逐项判分、总分与档位、裁决与差距、败因、事实/推断/未知、交接说明、`not_run` 清单齐全。
- 声明是否可追溯：全部结论附 `file:line`、命令读数或产物路径；B 的两个错误数字直接给到行号与实测值。
- 是否考虑替代解释与反例：对 A 的 H4 差异给出去重解释（并发调度），对 B 的列数错判过「是否影响 17 条缺陷链」并给出反向证据（`uploaded_file` 无 `uploaded_by` 写入）。
- 局限与未知是否显式：见 §4 U1–U6。
- 权限与范围：只读被评审目录，写入仅限 `docs/skills_eval/`（本文件 + `runs/` 复跑快照），未改生产代码，未改 `.git` 配置。
- **盲评纪律**：全文不出现对作者/模型身份的推测或评价；不以文风、篇幅、语气作为评分依据；两产物一律以路径标识。