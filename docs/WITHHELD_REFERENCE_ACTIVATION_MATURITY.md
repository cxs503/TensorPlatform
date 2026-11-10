# WITHHELD 参考真值激活机制成熟化（p380w2-n10）

**任务**：把「参考真值缺失 → 判据挂起（`WITHHELD`）→ 数据到位自动翻判」从报告里的一句自然语言，
变成平台可执行的机制（激活检测器 + 自动复判 + 审计 + 幂等 + 可回落 + 写时复算门）。

**落库**：`TensorPlatform` / `backend/services/withheld_activation/`（新模块）+ `backend/tools/withheld_activation.py`
（CLI）+ `backend/tests/test_withheld_activation.py`（27 条 pytest）。零领域语义：本机制不知道被判定量的物理含义，
判据主体、参考值、容差全部经**参考真值表 / 判据台账**注入（夹具一律 `synthetic_a` / `synthetic_b`）。

---

## ① 案例介绍（机制对象与参数）

`WITHHELD` 是平台证据分级里"**证据不足、不可裁**"的状态。过去它是**终点**：报告里写一句
"缺某某参考真值，待数据补齐后复判"，而数据真的到位时，没有任何东西会把它变成 `PASS`/`FAIL`——
翻判依赖人记得、人去找、人重跑。这一次成熟化把终点改成**状态机上的一个可离开状态**。

机制由三个文件构成的最小系统（全部 JSON/JSONL，原子写、append-only 审计）：

| 组件 | 载体 | 语义 |
|---|---|---|
| 参考真值表 | `reference_truth.json` | 每个判据主体一行：`quantity` / `reference_value` / `uncertainty_pct` / `source` / `revision`；**内容摘要**即行的身份 |
| 判据台账 | `criteria.json` | 每个主体：`computed_value` / `tolerance_pct` / `verdict` / `error_pct` / `reference_digest`（裁决所依据的参考行摘要）+ 计数器缓存 |
| 审计流水 | `audit.jsonl` | 一条判据跃迁一行，只追加：`seq` / `ts` / `actor` / `trigger` / `old_verdict` / `new_verdict` / `old_digest` / `new_digest` / `sha256` / `computed_value` / `reference_value` / `tolerance_pct` / `error_pct` / `idempotency_key` |

本单验收用两个合成判据主体（**不含任何物理量**；数值是刻意构造的账本数）：

<!-- BEGIN:SUBJECTS -->
| subject | verdict | error_pct | tolerance_pct |
|---|---|---|---|
| `synthetic_a` | PASS | 0.990099 | 3 |
| `synthetic_b` | FAIL | 20 | 5 |
<!-- END:SUBJECTS -->

`synthetic_a` 落在容差内（翻 `PASS`），`synthetic_b` 只有在参考行到位时才会翻（并翻成 `FAIL`，因为其误差
故意大于容差）——这两条一起把"翻绿"和"翻了但不是绿"都钉死在测试里，避免机制只会往好处翻。

## ② 怎么做的（方法 / 流程 / 工具链）

**状态机以"参考行内容摘要"为唯一驱动量**，不看时间戳、不看"已处理"标志、不看 `revision` 自增：

| 参考摘要变化 | 语义 | 记录的 `trigger` |
|---|---|---|
| `None → d` | 参考真值到位 | `activation` |
| `d1 → d2` | 参考真值被修正（值/来源/不确定度变化） | `reference_update` |
| `d → None` | 参考真值被撤回 | `withdrawal` |
| 无变化 | 无事可做（幂等空跑） | **不记录** |

复判是纯函数，与域无关：

```
error_pct = |computed - reference| / |reference| × 100        （reference 须有限且非零，否则 WITHHELD）
verdict   = PASS  if error_pct <= tolerance_pct  else  FAIL
```

参考行缺失或不可用 → 保持 `WITHHELD`（`reason` 区分 `no_reference_truth` / `reference_value_unusable`）。

**五条不变量在代码里而不是在评审里**（每条都有对应测试）：

1. **摘要驱动跃迁**：同一摘要重复触发不产生第二条审计（`idempotency_key` 里同时含主体、前后摘要、判决对、日志位序，
   既挡住"同一数据重放"，又不挡"同一对摘要的合法二次激活"——后者位序不同，是真实事件）。
2. **只追加审计**：审计记录从不改写；`actor=auto` 表明是机制自己翻的，不是人填的。
3. **不可只升不降**：删除参考行 → 判决回落 `WITHHELD` 且自身留审计（`withdrawal`），杜绝"翻绿后永久为真"。
4. **写时复算门**：台账里的计数器是**缓存**，先由底座（判据判决 + 审计流水）重算再比对，逐字段报差量；
   不一致 → **拒绝写入**（CLI 退出码 2），伪造的计数不会被写进账。
5. **并发不裂增**：读-改-写包在 advisory 文件锁内，锁内**重新读盘**再裁决，状态原子写（tmp + rename）——
   同一摘要变化的并发检测者最多留下一条审计。

**工具链**：

```
backend/services/withheld_activation/{ledger,detector,fixtures,acceptance,cli}.py   机制 + 判据
backend/tools/withheld_activation.py          CLI：seed / scan / watch / gate / recount / show / acceptance
backend/tools/withheld_activation_doc.py      把本文件的三张数字表从 artifact 程序化注入（零手抄）
tools/evidence_domain_scan.py + lexicon       零领域语义机检（本模块已登记为扫描目标）
```

关键 CLI 语义：`scan` 空跑也退出 0（幂等成功）；`gate` 只在复算通过时退出 0，否则退出 2 并打印差量；
`recount --repair` 从底座重建计数并**为修复本身留一条审计**（含被修复的差量）。

## ③ 计算结果（判据 A–F 机器可算结果）

<!-- BEGIN:CRITERIA -->
| criterion | meaning | result | key numbers |
|---|---|---|---|
| A | positive activation (WITHHELD -> PASS inside the budget) | PASS | audit_after=1, audit_before=0, budget_s=300, latency_s=0.138, reference_sha256=076e286c541596a1b817518f1c2bb5e0b5eb47c993d8ef811494fa9386c6f8e1 |
| B | negative control (no truth -> stays WITHHELD, exit 0, no audit) | PASS | audit_entries=0, exit_codes=`[0, 0]` |
| C | withdrawal (truth removed -> falls back to WITHHELD, audited) | PASS | new_verdict=WITHHELD |
| D | counter recompute gate (forged counter refused, delta reported) | PASS | audit_before=2, deltas=`{"fail": -1, "pass": 1}`, gate_exit=2, repair_exit=0, scan_exit=2 |
| E | zero domain vocabulary in new code / fixtures / artifacts | PASS | hit_count=0, scanned_files=26 |
| F | idempotence and concurrency (no audit inflation) | PASS | after_concurrent=1, sequential_counts=`[[0, 1], [0, 1], [0, 1]]` |
| G | figure artifact written | PASS | png_bytes=56371 |
<!-- END:CRITERIA -->

主账本审计链（程序化读出，非手抄）— 展示"翻绿 → 回落 → 再翻绿"的完整轨迹（`state/audit.jsonl`）：

<!-- BEGIN:AUDIT -->
| seq | trigger | subject | old | new | error_pct | actor | sha256 (16 hex) |
|---|---|---|---|---|---|---|---|
| 1 | activation | synthetic_a | WITHHELD | PASS | 0.990099 | auto | 076e286c541596a1... |
| 2 | activation | synthetic_b | WITHHELD | FAIL | 20 | auto | 5f5e6c29ede2b41b... |
<!-- END:AUDIT -->

撤回链（独立账本 `withdrawal/`：激活 → 删除参考行 → 回落，展示"可回滚"不是口号）：

<!-- BEGIN:AUDIT_WITHDRAWAL -->
| seq | trigger | subject | old | new | error_pct | actor | sha256 (16 hex) |
|---|---|---|---|---|---|---|---|
| 1 | activation | synthetic_a | WITHHELD | PASS | 0.990099 | auto | 076e286c541596a1... |
| 2 | withdrawal | synthetic_a | PASS | WITHHELD | - | auto | 076e286c541596a1... |
<!-- END:AUDIT_WITHDRAWAL -->

pytest：`python -m pytest backend/tests/test_withheld_activation.py -q` → **27 passed**
（覆盖：相对误差与判决三分支、参考行内容寻址与非法值拒收、激活/修正/撤回、审计字段契约、dry-run 不落盘、
复算门拒写与差量、修复审计、4 线程并发同摘要变化只 1 条审计、watch 无人触发自动翻判、CLI 退出码 0/2、
端到端 A–G）。全量领域词机检：`python tools/evidence_domain_scan.py` → **scanned_files=71, hits=0**。

## ④ 结果图

`artifacts/withheld_activation/activation_timeline.png`（matplotlib，Agg）：

* **左图**：判决跃迁时序——横轴为审计流水 `seq`，纵轴为判决档位（`WITHHELD`/`FAIL`/`PASS`），
  每级台阶对应一条审计记录，即"每条台阶都可追溯到一条 auto 审计"；
* **右图**：激活时刻的误差 vs 容差柱状对比（相对误差 `error_pct` 与 `tolerance_pct` 并排），
  直观给出"为什么翻成 PASS 或者 FAIL"，以及两道判据各自离容差线有多远。

图中所有数值来自 `acceptance.json` 与 `audit.jsonl`，由脚本生成，不是绘图时另填。

## ⑤ 注意点 / 局限 / 适用范围

1. **检测是轮询式而非事件式**：机制保证"下一趟扫描"翻判，端到端时延上界 = 轮询间隔（默认 60 s ≤ 300 s 预算）。
   数据库触发器/CDC 事件源是更强的形态，需要一层 `reference truth` 适配器（见下条）。
2. **存储层是文件（JSON/JSONL），不是 DB 表**：本单交付的是机制与语义，参考真值表/台账的**转移面**是
   `ReferenceStore.upsert/delete` 与 `CriterionLedger.save`——接 PG 表只需把这几个方法换成 SQL，
   摘要与门禁逻辑不变。未接 DB 是**明确未完成项**，不粉饰。
3. **并发保护是 advisory `flock`**：单机多进程可靠（已测 4 线程 + 3 子进程）；NFS/对象存储上 `flock` 语义不保证，
   跨机部署必须换成数据库行锁或分布式锁。
4. **摘要只覆盖真值字段**（`quantity`/`reference_value`/`uncertainty_pct`/`source`），不覆盖 `revision`/`updated_at`：
   同值重写不算新信息（这是刻意的幂等边界）；因此"来源改了但四个字段都没变"不会被当成事件——实际不会发生，
   因为 `source` 在摘要内。
5. **参考值为 0 被拒收**：相对误差对 0 无定义；把 0 当参考量需要绝对误差语义，属另一套判据，本机制不猜。
6. **未与任务级 verdict / 报告门直接联通**：本单只做"判据级"翻判；把翻判事件接到任务台账（mission 级 gate）
   需要一条事件总线，属后续节点。
7. **`watch` 每趟都会重读盘**（参考表与台账），因此外部进程写入能被看见；但同一进程内**非持久化**的
   `upsert(persist=False)` 在下一趟会读不到——机制以盘上内容为准，这是有意的（数据源必须是持久的）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答「是否 < 3%」）

本单是平台机制（无物理场、无解析解），因此"精度"对标的是**判据数字本身**：容差线、时间预算、审计条数、差量。

<!-- BEGIN:ERROR_VERDICT -->
* `synthetic_a`: relative error `0.990099 %` vs declared tolerance `3 %` -> within tolerance: **yes**; below the platform's 3 % bar: **yes**; verdict `PASS`.
* `synthetic_b`: relative error `20 %` vs declared tolerance `5 %` -> within tolerance: **no**; below the platform's 3 % bar: **no**; verdict `FAIL`.
<!-- END:ERROR_VERDICT -->

* 激活时延 vs 5 min 预算：见 ③ 表 A 行 `latency_s` 与 `budget_s`，**满足 < 300 s**（轮询间隔 0.2 s 的实验设置下为亚秒级；
  生产默认间隔 60 s）。
* 审计条数 vs 期望 1：见 ③ 表 A 行 `audit_before`/`audit_after`（差为 1）与 F 行 `sequential_counts`/`after_concurrent`
  （三次连跑与三并发均不增长），**满足"不裂增"**。
* 伪造计数 vs 底座：见 ③ 表 D 行 `deltas`（精确等于伪造时注入的差量），且拒绝写入后状态文件 sha256 不变、审计长度不变。
* 零领域语义：见 ③ 表 E 行 `hit_count`=0 / `scanned_files`。

**结论（显式）**：机制决策误差（唯一有基准的量）在 `synthetic_a` 上为 0.99 %，**< 3 %，是**；该判据的
`PASS` 不是"接近容差线的侥幸"，而是离容差线有 3 倍余量。若需要无基准声明：本单不存在"与实验/文献对照"的物理量，
其正确性由**判据 A–F 的算术关系**（误差定义、容差比较、计数器复算、幂等计数）自证，属于机制自洽性验证，
不是物理精度验证——这一点不掩盖。

---

## 附：可复现命令

```bash
# 端到端验收（生成 acceptance.json + 图 + 各子目录账本）
python backend/tools/withheld_activation.py acceptance --figures

# 单条 pytest
python -m pytest backend/tests/test_withheld_activation.py -q

# 手工走一遍状态机
python backend/tools/withheld_activation.py seed  --state-dir /tmp/wa
python backend/tools/withheld_activation.py scan  --state-dir /tmp/wa          # 空跑，退出 0
python backend/tools/withheld_activation.py gate  --state-dir /tmp/wa          # 复算门
python backend/tools/withheld_activation.py watch --state-dir /tmp/wa --interval 1 --timeout 60 --stop-on-event
python backend/tools/withheld_activation.py show  --state-dir /tmp/wa          # 判决 + 计数器双份（declared/recomputed）

# 零领域语义机检 + 文档数字程序化注入
python tools/evidence_domain_scan.py
python backend/tools/withheld_activation_doc.py
```