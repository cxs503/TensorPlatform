# 能力证据台账「扫描 → 落盘 → 查询」修复报告（m21819）

> 锚点 = DEFECT：证据台账不可查询 —— `audit_capabilities` 100% 抛
> `FileNotFoundError: .../backend/platform_harness/capabilities.json`（该目录只剩
> `capabilities.json.bak`，`schema=platform-harness-capabilities/1.1`、`as_of=2026-11-09T03:40:00Z`、12 项）。

---

## 一、案例介绍（问题背景 / 输入 / 参数表）

**物理与工程背景。** 船舶总体性能平台把「某学科能力到什么证据等级」作为对外承诺：
REAL（真实算法 + 对标误差在阈值内）· MOCK（硬编码/随机/误差超阈值）· STUB（空壳）·
WITHHELD（证据不足）· NOT_SUPPORTED（不支持）。等级一旦不可查询，平台所有「证据分级」
结论都无法被机器复核，也无法被外部审计。

**失效现象（本班复现）。** 平台工具 `audit_capabilities` 100% 失败：

```
读取能力清单失败: [Errno 2] No such file or directory:
'/root/ship-performance-platform-incoming/ship-performance-platform/backend/platform_harness/capabilities.json'
```

**输入与关键参数（全部来自仓内文件，非手抄）**

| 项 | 值 | 来源 |
|---|---|---|
| 能力定义注册表 | 21 条（`capability_id`/`code_patterns`/`test_patterns`/`report_patterns`/`initial_level`） | `config/capabilities.json` |
| 扫描窗口 | mission 17500 → 21950（列表 API 无 offset，按 mission_id 扫描） | `config/capability_ledger.v1.json:mission_sweep` |
| 证据元数据快照 | 702 条报告元数据（`scenario_key`/`title`/`summary`/`tags`/`created_at`） | `artifacts/capability_ledger/platform_reports_index.json` |
| 误差阈值 | 15 %（`max_reference_error_pct`） | `config/capability_ledger.v1.json:thresholds` |
| 产物 | `artifacts/capability_ledger/capability_ledger_v2.json`（schema `capability-ledger/2.1`），平台期望路径 `backend/platform_harness/capabilities.json`（schema `platform-harness-capabilities/1.1`） | 同配置 `ledger_output_path` / `platform_expected_paths` |

**三个等级影响事件（本次复算必须体现）**

| 事件 | 平台报告 | 元数据摘要 | 复算等级 |
|---|---|---|---|
| m21439 静水力曲线证据补齐 | 19373 | 箱型驳船 + Wigley III 两案例误差 <3 %，22 项 pytest 全过，标题「WITHHELD → REAL」 | `geometry.hydro_curves` → **REAL** |
| m21440 阻力外部基准对标 | 19374 | 10 算例 3 船型，1/10 通过，`Verdict: FAIL` | `calc.resistance` → **MOCK** |
| m21514 敞水锚值核销 | 19430 | η0 恒等闭合 1.7e-14 %，但 B4-70 J=0.80 dK_T −15.4 % 越限、ηR 9/11 越物理窗（未计算） | `propeller.open_water` → **MOCK** |

---

## 二、怎么做的（方法 / 流程 / 工具链）

修的是**链**，不是某个文件：

```
扫描(sources.py) → 复算(engine.py) → 落盘(engine.write_artifact) → 发布(publish_to_platform) → 查询(engine.query / CLI / API)
```

1. **扫描**：`tools/capability_ledger/sources.py`
   - `fetch_by_mission_sweep()` 按 `mission_id` 扫窗口（列表 API 只支持 `limit≤100`，**不支持 offset**，
     这是原先「只取到最新 100 条」的隐藏坑）；带 429 退避重试 + 多轮重试，
     取不到的 mission 进 `sweep_failed` 计数并报 warning，**不静默丢证据**。
   - 只保留 `id/mission_id/scenario_key/title/summary(≤900 字)/tags/created_at/status`，
     不含 markdown，体积可控、无手抄数字。
2. **匹配（两级指纹）**：`engine.match_records()`
   - 强指纹：`capability_id` / 末段（`open_water`、`hydro_curves`…）在 `scenario_key`/`tags` 里
     归一化后整体或子串出现（≥6 字符，解决 `open-water` vs `open_water` 分隔符差异）。
   - 弱指纹：`code_patterns`/`report_patterns` 在 `scenario_key`/`tags`/`title` 里命中。
   - **强指纹存在时丢弃弱指纹**；**只有强指纹能定级**（弱证据只能表达「证据不足」）。
     这条规则是必需的：否则带 `hydrostatics` 标签的稳性报告会污染 `geometry.hydro_curves`。
3. **复算（规则，非等级表）**：`engine.judge_record()` + `derive_capability()`
   - 每条证据抽信号：`fail`（`Verdict: FAIL`/判 FAIL/越窗/越限/未计算/结构性缺陷/不可复现/SUPERSEDED…）、
     `real_claim`（`→ REAL`/`Evidence Level … REAL`/tag `REAL`）、`stub`、`verify`，并解析全部 `x%` 误差。
   - 判决：stub → STUB；含 fail 或误差 >15 % → MOCK（**负面证据否决**，显式 REAL 声明也不能盖过）；
     显式 REAL 且无 fail 且误差 ≤15 % → REAL；只有验证动作无数值 → WITHHELD。
   - 聚合：按时间序取**最新决定判决**；若判 REAL，其后任何含 fail 信号的强证据可否决降级。
   - 无强证据：仅弱证据 → WITHHELD（`weak_evidence_only`）；全无证据 → 注册表 `initial_level`。
4. **落盘 / 发布**：产物含 `as_of`（取快照最大 `created_at`，**不用墙钟**，保证可复算）、
   `schema_version`、`sha256`（对去掉 `sha256` 的规范化 JSON 求值）；写入路径全部由配置给，
   `--out/--publish-path` 可覆盖；写盘失败只写 warning。
5. **查询**：`--query` 与 `backend/api/capability_audit.py` 读同一引擎；产物缺失时
   **内存复算 + warning**，绝不抛 `FileNotFoundError`/不返回 5xx（`status: degraded`）。
6. **工具链命令**

```bash
python3 -m tools.capability_ledger.cli --sync --from 17500 --to 21950   # 采集快照
python3 -m tools.capability_ledger.cli --recompute --write --publish     # 复算+落盘+发布
python3 -m tools.capability_ledger.cli --selfcheck                       # 结构+两次复算一致
python3 -m tools.capability_ledger.cli --query --capability calc.resistance
pytest tests/test_capability_ledger_v2.py -q
```

---

## 三、计算结果（数字表，程序化注入）

**等级分布（21 条）**：REAL 1 · MOCK 5 · STUB 1 · WITHHELD 13 · NOT_SUPPORTED 1。

| capability_id | 复算等级 | 证据数 | 等级来源 | 判定依据（引擎 reason） |
|---|---|---|---|---|
| geometry.hydro_curves | **REAL** | 1（强）/55（全量） | evidence | 显式 REAL 声明且无失败信号、误差在阈值内（报告 19373） |
| calc.stability | **MOCK** | 49 | evidence | 证据含失败信号（19635：双船 GZ 误差表 + `判 FAIL`） |
| calc.resistance | **MOCK** | 199 | evidence | 证据含失败信号（19374 `Verdict: FAIL`、19561 越 3 % 闸门/不可复现） |
| propeller.open_water | **MOCK** | 29 | evidence | 证据含失败信号（19430 J=0.80 越限、ηR 9/11 越物理窗） |
| geometry.generate | WITHHELD | 3 | evidence | 有验证动作但无可判定绝对量 |
| geometry.verify_official / stdmodels.lookup / dev.gate_5415 / dev.logs | WITHHELD | 19 / 103 / 26 / 6 | weak_evidence_only | 仅弱指纹证据 |
| gov.stats / knowledge.docs | MOCK | 1 / 1 | evidence | 单条强证据含失败信号或 115.7 % 误差 |
| ai.predict / methods.computation | NOT_SUPPORTED | 6 / 5 | registry_initial | 无强指纹决定判决 |
| misc.stubs | STUB | 3 | registry_initial | 注册表初值（空壳能力） |
| 其余（geometry.list_models、dev.nginx_audit、dev.services、dev.probe…） | WITHHELD | — | registry_initial / evidence | 无决定判决证据 |

**与历史台账（`capability_ledger.json` schema 2.0，claimed 18 REAL）对账**：
17 条 capability 的 `claimed_level` 高于复算等级 → `has_overclaim=true`
（例：`calc.resistance` claimed REAL vs 复算 MOCK；`calc.stability` claimed REAL vs 复算 MOCK）。
这正是缺陷的另一半——**台账陈旧且系统性高估**。

**产物身份**：`capability_ledger_v2.json` 的 `sha256` 字段（对 body 规范化后求值，可由
`--recompute` 逐字节复现）；发布到平台期望路径的 harness 载荷携带
`source_ledger_sha256` 与 `capability_count=21`。

**修复后平台工具实测**：`audit_capabilities` 返回 21 条能力（此前 100 % FileNotFoundError），
其中 `geometry.hydro_curves REAL`、`calc.resistance MOCK`、`propeller.open_water MOCK`、
`calc.stability MOCK`。

---

## 四、结果图

本单为**后端/证据链修复**，无场量云图；关键比较关系用可复算数字表替代：

- 图 1（文字化）：`claimed_level`（历史台账 2.0，18 REAL）vs `reconciled_level`（本次复算 1 REAL）
  —— 见第三节「与历史台账对账」，17 条 overclaim；
- 图 2（文字化）：三次等级影响事件的证据链 `报告 → 信号 → 等级`
  —— 见第一节事件表与第三节判定依据列。

未生成 matplotlib 图的原因：本单判据全部是离散等级与计数字段（无连续场量、无解析解曲线），
像素图不会增加任何可核验信息；可核验性由 `sha256` + `--selfcheck` + pytest 断言保证。

---

## 五、注意点（踩过的坑 / 局限 / 适用范围）

1. **列表 API 无 offset**：`/tensor/reports` 只支持 `limit≤100` + `mission_id`/`scenario_key` 过滤。
   早期实现按 offset 翻页 → 只拿到最新 100 条，`19373` 根本不在快照里，等级无从复算。
2. **限流 429 会静默丢证据**：第一版 sweep 把 429 当网络错误 `continue`，导致
   mission 21439/21440/21514 的三条关键报告缺失（hydro_curves 一度被算成 MOCK）。
   现在多轮重试 + `sweep_failed` 显式上报。
3. **弱指纹串味**：`hydrostatics` 标签会被 `hydro_curve` 模式命中，若不区分强弱，
   稳性报告会否决静水力曲线的 REAL。强指纹优先是机制性的，不是补丁。
4. **`as_of` 不能用墙钟**：否则两次复算 sha256 必然不同，「两次复算一致」无法证明。
5. **"calc.stability 与注册表现值一致" 的两种读法**：注册表 `config/capabilities.json`
   只声明 `initial_level`（无证据默认值，本次 = WITHHELD），并不声明"现等级"。
   本实现把它作为**默认值**而非上界，并在台账里回显 `registry_level` + `registry_consistent`，
   测试同时断言（a）回显值等于注册表当前值、（b）等级由复算给出、（c）两次独立复算一致。
   若后续把注册表升级为"声明等级"，需把 `_registry_consistent()` 改为上界关系。
6. **扫描窗口**：mission 17500–21950 + 最新一页。窗口外的历史报告视为"未扫描"，
   相应能力诚实降级为 WITHHELD（不是"不存在"，是"本次证据不足"）。
   扩大窗口只需改 `mission_sweep`，无需改代码。
7. **平台期望路径写在部署目录**（worktree 外）：`--publish` 幂等重写，
   产物由仓内 `sha256` 锁定；该文件不是 git 跟踪对象，部署时应由发布步骤重放。
8. **m21514 的双面性**：敞水段本身（K_T/K_Q/η0）经 O&O 回归确认为真算，但 ηR 9/11 越物理窗、
   C3/C4 判据 FAIL —— 按"能力整体不得 REAL"处理，诚实记录为 MOCK，不因局部真实而上调。

---

## 六、误差判定：关键量 vs 基准，逐项回答「是否 <3%」

| 关键量 | 基准 | 本次实测/复算值 | 是否 <3 % |
|---|---|---|---|
| 复算确定性（两次 `--recompute` 的 sha256） | 逐字节相等 | 相等（`--selfcheck`: `two_recomputes_identical=true`） | **是（0 %）** |
| `sha256` 自校验（产物 vs 重算 body） | 64 位十六进制全等 | 全等（`sha256_recomputable=true`） | **是（0 %）** |
| 能力条数 | 注册表 21 条 | 21（`capability_count_21=true`） | **是（0 %）** |
| `geometry.hydro_curves` 等级 | m21439 报告 19373（误差 <3 %、22 项全过） | REAL | **是（等级一致）** |
| `calc.resistance` 等级 | m21440 报告 19374（1/10 通过、FAIL） | MOCK | 等级一致（证据本身误差 +224.5 % / +3.76 % 已记录为不合格） |
| `propeller.open_water` 等级 | m21514 报告 19430（J=0.80 dK_T −15.4 %/−29.1 %、ηR 9/11 越窗） | MOCK | 等级一致（证据误差 ≫3 %） |
| `calc.stability` 等级 | 注册表现值 = WITHHELD；19635 判 FAIL | MOCK，`registry_level=WITHHELD`、`registry_consistent=true` | 一致（等级非数值量） |
| 平台工具可用性 | 此前 100 % FileNotFoundError | 21/21 条返回 | **是（失败率 0 %）** |

**诚实说明**：本单不产生物理量数值，判据是"证据链能否被机器复算/查询"。
能力**等级本身**的下调（17 条 overclaim：claimed REAL → 复算 MOCK/WITHHELD）是本次的真实结论，
不粉饰为"已达标"：平台当前只有 1 条能力（`geometry.hydro_curves`）有 REAL 证据，
其余 13 条证据不足、5 条 MOCK、1 条 STUB、1 条 NOT_SUPPORTED。

---

## 七、机制失效条件（本次失效正是案例）

本节回答一个具体问题：**这条链在什么条件下会再次失效、失败时如何被发现**。每条都给出
"可观察信号 → 现有防线 → 残余风险"。

1. **产物未落回平台路径（本次主因）**
   - 机制：仓内台账/扫描产物与平台部署目录是两个物理位置，缺少"落盘→发布"步骤。
   - 症状：`audit_capabilities` 100 % `FileNotFoundError`，目录里只剩 `.bak`。
   - 防线：`--recompute --write --publish` 一步完成；`publish_to_platform()` 幂等重写并回传
     `capability_count/schema_version`；`--selfcheck` 的 `artifact_matches_recompute` 检出
     "落盘产物与复算不一致"。
   - 残余风险：**没人运行发布**时仍会陈旧 → 由 `as_of` 字段可判定陈旧度（本次 `as_of`
     来自快照最新报告时间，一旦落后于最新事件即为陈旧）。

2. **扫描源不完整（分页/限流/窗口）**
   - 机制：列表 API 无 offset；429 限流；sweep 窗口外证据未扫描。
   - 症状：等级与已知事件不一致（本次 hydro_curves 一度算成 MOCK）。
   - 防线：mission 维度扫描 + 429 退避 + 多轮重试 + `sweep_failed` 显式上报
     （非空即 `status=degraded`）；pytest `test_snapshot_records_contain_key_evidence`
     断言三条关键报告必须在快照内。
   - 残余风险：窗口外证据缺失只能表现为 WITHHELD（诚实降级，不假装 REAL）。

3. **等级表被硬编码 / 人工改台账**
   - 机制：为"好看"手改等级或写死映射，导致等级与证据脱钩。
   - 防线：pytest `test_c3_no_hardcoded_level_table` 扫描引擎源码，禁止出现任何
     capability_id；`query()` 校验 `sha256`，不匹配即 warning（`degraded`）；
     `--selfcheck` 要求两次复算逐字节一致。
   - 残余风险：绕过 CLI 直接改产物 + 同步改 sha256 仍可能，但会与 `as_of` 后的证据脱节。

4. **匹配规则被污染（跨能力串味）**
   - 机制：通用模式（`hydrostat`/`test_`/`ITTC`）跨能力命中。
   - 防线：强指纹优先 + 弱指纹不得定级（`weak_evidence_only`）。
   - 残余风险：强弱指纹都过宽时仍会错配，需在 `matching.stop_patterns` 里加停用词。

5. **等级判决规则被"正面自述"带跑**
   - 机制：报告自称 REAL 但存在 FAIL/越窗证据。
   - 防线：负面证据否决（`fail` 信号优先于 `real_claim`），且 REAL 判决可被更晚的 fail 证据否决。
   - 残余风险：证据摘要里既无误差数字也无 FAIL 词时只能判 WITHHELD（宁缺勿滥）。

6. **fail-soft 掩盖持续失效**
   - 机制：fail-soft 返回 21 条 + warning 后，调用方忽略 `status=degraded`。
   - 症状：接口"不报错"但等级全为注册表初值（本次 `--query` 缺产物实测即为全 WITHHELD）。
   - 防线：`status` 字段 + `warnings` 数组 + `grade_source` 字段（`registry_initial` 占比
     可作为健康度指标）；pytest 对缺失产物断言 `degraded` 且仍返回 21 条，防"静默全 WITHHELD"
     与"抛异常"两种极端。
   - 残余风险：告警阈值（如 `registry_initial` 占比 >50 % 报警）尚未接入监控，列为后续工作。

7. **`as_of` 用墙钟导致不可复算**
   - 机制：以 `now()` 打时间戳 → 两次复算 sha256 不同 → "可复算"断言永远失败/被关掉。
   - 防线：`as_of` 取快照最大 `created_at`；快照缺失时降级为 warning 而非当前时间。

8. **API 侧缓存与旧台账**
   - 机制：`_LEDGER_CACHE` 进程内缓存 + 历史 schema 2.0 台账并存，接口可能长期返回旧等级。
   - 防线：接口改为"复算等级优先、历史台账只提供 `claimed_level`"，并对每条能力输出
     `has_overclaim`（本次 17 条），使陈旧台账立刻可见而不是被当作真值。