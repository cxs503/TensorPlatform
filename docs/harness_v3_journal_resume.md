# Harness v3：席位工作日志（journal）与断点续跑

优先级：owner 2026-11-01「传统 IT → AI 原生 IT」演化图**优先一 = 持续运行的科研 Harness**。
本单交付可执行原语 + 集成钩子 + 可红机器门，不改动 `tp_worker.py` 现有行为语义（该文件在
平台守护侧、不在本工作树，故以「钩子 + 精确补丁说明」交付）。

> 全部判据数字见 `docs/harness_v3/gate_evidence.md` 与 `gate_report.json`（由
> `scripts/run_harness_v3_gate.py` **程序化生成**，零手抄）；格式样例见
> `docs/harness_v3/sample_journal_m101.jsonl`；判据图见 `docs/harness_v3/figs/harness_v3_gate.png`。

---

## 0. 差距分析（只读阅读 `tp_worker.py` 后的结论）

阅读对象：`/srv/tensor/agent/tp_worker.py`（782 行，守护侧，只读）。

现有与"状态"有关的机制只有三处：

| 机制 | 代码位置 | 内容 | 对"续跑"的贡献 |
|---|---|---|---|
| `STATE = state-<AGENT_ID>.json` | L68 / `ensure_worker()` L135-168 | **只有** `{agent_session_id, agent_id}` | 无。跨进程记住"我是谁"，不记"我做到哪" |
| 心跳 `/heartbeat` | `run_mission._hb_loop` + 每 3 次调用 | `tool_calls / elapsed_seconds / head=<工具名 @ call N>` | 只有计数与最近工具名，任务语义为零；且只进平台 DB，席位重启不读回 |
| `evidence_snapshot()` | L408-420 | `git status --porcelain --ignored` **起始基线**，收尾做差集 | 仅在**验收结算时**用，运行期不落盘；进程一死基线随之消失 |

关键缺口（逐条对应本单任务项）：

1. **无 append-only 工作日志**。`messages` 列表是唯一"做到哪了"的载体，全在内存；
   `compact_messages()` 压缩后更只剩摘要。进程退出 → 连续性归零（m1121：15 分钟内存工作全丢）。
2. **claim 路径无续跑注入**。`main()` 拿到 `mission` 后直接 `api(.../start)` → `run_mission()`
   用 `_seat_memory()`（席位级长期记忆，跨单通用）+ `mission['goal']` 组装首条消息；
   同一 mission 被 reclaim 后重领，首条消息与第一次领单**完全一样**——模型必然从零开始。
3. **崩溃无证据、无归因**。`run_mission` 被 `except Exception` 包住写 `failed`，但 kill -9 /
   OOM 连这段都不执行：DB 里只剩"心跳停在 call N"，没有任何"已完成步骤/已验证事实"。
4. **预算语义只有单次**。`budget.max_tool_calls / max_wall_seconds` 在 `run_mission` 内以
   `calls = 0` 起步；若引入续跑而无累计口径，重领即等于**预算翻倍**——必须显式防住。
5. **无"日志不可信"分支**。平台已有反吹牛闸门（`has_evidence` 判工作树改动），但没有
   "日志本身坏了怎么办"的口径；朴素实现极易把半行/坏行当成"已完成"，即伪造续跑。

结论：缺的不是"进度条"，而是**一份 append-only、可校验、缺了就明确降级的落盘事实**，
以及 claim 时的**注入点**与**跨尝试预算口径**。

---

## 1. 设计

### 1.1 路径布局（按 mission id 组织）

```
$HARNESS_JOURNAL_ROOT/                 # 默认 /srv/tensor/agent/journals（运行态，不入库）
└── missions/m<mission_id>/
    ├── meta.json                      # 首建时间、repo、steps_total、预算额度
    └── journal.jsonl                  # append-only，一条记录一行
```

按 mission 组织而非按席位/单次尝试：mission 是"同一单"，席位与尝试号是记录字段，
这样 reclaim 后**新席位/新进程**依然命中同一份日志。

### 1.2 记录 schema（每条一行 + 链式摘要）

```json
{"schema":"harness.journal.v3","seq":4,"ts":"...","mission_id":1231,
 "agent_id":"tp-native-1","kind":"checkpoint|attempt_start|attempt_end",
 "goal":"...","plan":["..."],
 "done_steps":[{"id":"s3","what":"...","evidence":"<磁盘路径>","attempt":1}],
 "verified_facts":["..."],"unverified_assumptions":["..."],
 "dirty_tree":{"kind":"git","head":"...","dirty_count":2,"dirty_paths":["a.py"]},
 "next_intent":"...","calls_used":7,"steps_total":6,"progress":0.5,
 "attempt":2,"status":"running|steps_done|budget_exhausted",
 "deviations":[...],"prev_digest":"sha256:...","digest":"sha256:..."}
```

- `digest = sha256(canonical(record - digest))`，`prev_digest` 串成链。
- 写用 `O_APPEND` 单次 `os.write`：进程被 kill -9 最多留半行，**不会写坏整份文件**。
- 读时三重校验：`json 可解析` → `schema 正确` → `digest 重算一致`；另查链断/seq 回退。

### 1.3 四条不可让步约束（对应任务项 1~4）

| # | 约束 | 实现 | 门禁 |
|---|---|---|---|
| 1 | append-only，只追加 | `append()` 从不重写；`merge()` 折叠成当前状态 | A 场景 journal 9 条有效、0 损坏 |
| 2 | 脏日志不许"猜" | `read()` 把坏行记进 `corrupt[]`，`degraded=True`，只采信通过校验的前缀 | C1 / C2 |
| 3 | 续跑必须回到磁盘证据 | `resume_brief(verified_evidence=...)`：日志说完成但证据不在 → 移入 `evidence_missing`，**必须重做**，并写 `deviations` | C1（全损→0 步）、C2（前缀→3 步） |
| 4 | 预算跨尝试累计，绝不追加 | `calls_used` 取历史最大值；`budget_remaining = total - used`；`extended=False`；耗尽写 `budget_exhausted` 并停 | D |

### 1.4 检查点节律

与平台已有心跳节律对齐：**每 3 次工具调用**一次 checkpoint，另加两个自然检查点
`attempt_start` / `attempt_end`。理由：③ 重放成本 = 至多丢 2 次调用的语义增量，
而写放大可忽略（单条 ≤1.5 KB）。

### 1.5 claim 注入（续跑，而非从零）

`resume_brief()` 产出的文本前置到首条 user 消息，`mode=resume` 时以
**「【断点续跑 · 你在续跑，不是从零开始】」** 开头，内容按固定顺序：

```
journal 路径 / 有效记录数 / 损坏数 / degraded 标志 / 上次进度 / 已用工具调用
[目标] [计划]
[已完成步骤 · 已复核磁盘证据，勿重做]   ← 每条带证据路径
[日志声称完成但磁盘无证据 · 必须重做]
[已验证事实 · 可直接采用] / [未验证假设 · 用前先验]
[脏工作树] 未提交路径（可能停在写入中途）
[下一步意图]
★纪律：以上是 append-only 日志事实……与工作树冲突时以工作树为准并记录偏差；已用预算不因续跑恢复。
```

`mode=fresh` 时以 **「【无可用工作日志 · 从零开始】」** 开头并显式写明
"不要声称续跑"——这就是任务项 ② "降级从零并如实记录"的注入侧形态。

---

## 2. 交付物

| 文件 | 作用 |
|---|---|
| `scripts/harness_journal.py` | 核心原语：append-only 记录、digest 链、读+校验、`merge`、`resume_brief`、`budget_remaining`、`tree_snapshot`；CLI `brief/verify/show/append` |
| `scripts/harness_seat.py` | 席位参考实现（= `run_mission` 的最小同构）：每检查点落日志、证据复核、预算累计、`--die-after-step` 自证崩溃点 |
| `scripts/harness_supervisor.py` | 守护：拉起席位、到点**真发 SIGKILL**、重领同单、按日志续跑、汇总 |
| `scripts/harness_journal_hook.py` | **tp_worker 集成钩子**：`claim_injection / checkpoint / attempt_end`；"已完成步骤"取自磁盘证据（与平台 `evidence_snapshot` 同源）；journal 异常永不影响主流程 |
| `scripts/run_harness_v3_gate.py` | 机器验收门（6 场景，PASS/FAIL + 可红性自证），产出 `gate_report.json` / `gate_evidence.md` / 图 |
| `tests/test_harness_journal.py` | 原语单测（链、篡改、损坏、降级、证据复核、预算） |
| `tests/test_harness_journal_hook.py` | 钩子单测（证据驱动步骤、重启去重、产物消失→重做、env 关闭、根目录不可写→降级不崩） |
| `tests/test_harness_v3_resume.py` | 门禁 pytest 形态（kill -9 续跑、恢复步**不重做**、无日志从零、全损/部分损坏、预算帽、可红性、整门 PASS） |

---

## 3. tp_worker 集成补丁（3 处，语义不变）

`tp_worker.py` 属守护侧文件（不在本任务工作树/EDIT_WHITELIST 内），故**未改动**，
交由平台 owner 应用。补丁只增不改，`HARNESS_JOURNAL=0` 可整体关闭。

```diff
@@ run_mission() 组装 messages 之后、进入 while 循环之前
-    messages = [{"role": "system", "content": SYSTEM},
-                {"role": "user", "content": _seat_memory() + f"任务目标:\n{mission['goal']}..."}]
+    messages = [{"role": "system", "content": SYSTEM},
+                {"role": "user", "content": _seat_memory() + f"任务目标:\n{mission['goal']}..."}]
+    # ── Harness v3 ①：claim 注入续跑上下文（无日志则显式声明从零）
+    try:
+        import harness_journal_hook as _HJ
+        _inj = _HJ.claim_injection(mission, root)
+        if _inj:
+            messages[1]["content"] = _inj + messages[1]["content"]
+    except Exception:
+        pass

@@ 工具调用循环内，心跳之后（每 3 次调用）
             if time.time() - hb_at > 15:
                 api(f"/tensor/missions/{mission['id']}/heartbeat", ...)
                 hb_at = time.time()
+            # ── Harness v3 ②：检查点落盘（每 3 次工具调用）
+            if calls % 3 == 0 and _HJ_OK:
+                try:
+                    _HJ.checkpoint(mission, root, tool_calls=calls,
+                                   next_intent=(msg.get("content") or "")[:200])
+                except Exception:
+                    pass

@@ 收尾（run_mission 返回前，_hb_stop.set() 旁）
     _hb_stop.set()
+    # ── Harness v3 ③：尝试收尾（succeeded/failed 都记，供下次续跑判决）
+    try:
+        _HJ.attempt_end(mission, root,
+                        status="steps_done" if final is not None else (fail_stage or "failed"),
+                        tool_calls=calls, summary=(final or {}).get("summary", ""))
+    except Exception:
+        pass
```

`import harness_journal_hook as _HJ` 放在 `run_mission` 开头（`sys.path` 已含 `scripts/`），
`_HJ_OK` 用 `HARNESS_JOURNAL` 开关控制。三处全部 `try/except`：**journal 永不让主流程失败**
（`test_hook_never_breaks_worker_on_bad_root` 覆盖：根目录不可写时降级为一句声明）。

---

## 4. 机器验收门（可红）

`python3 scripts/run_harness_v3_gate.py`（6 场景，全 PASS 退出 0，任一 FAIL 退出 1）：

| 场景 | 判据 | 说明 |
|---|---|---|
| `A_crash_resume` | 进度 ≥1/3 时 SIGKILL → supervisor 拉起 → 续跑 **≥1** 步、6 步全成、日志 0 损坏、简报确被注入 | 主判据 |
| `B_control_no_journal` | 同样 kill -9、关掉 journal → 续跑 **=0** 步 | 对照：A−B 即 journal 净贡献 |
| `C1_corrupt_total` | 日志被随机字节覆盖 → `degraded=True`、续跑 **0** 步、简报记降级、工作仍能完成 | 不伪造续跑 |
| `C2_corrupt_partial` | 尾部半行 + 坏行 → 只采信校验前缀（3 步），坏行 `corrupt≥2` 且不入证据 | 部分损坏的分寸 |
| `D_budget_cap` | 额度 4，杀后续跑 → `calls_used ≤ 4`、`budget_exhausted`、不第三次拉起 | 预算不追加 |
| `E_red_capability` | 关掉 journal 再要求"续跑 ≥1 步" → 判据必须变红 | **门禁非空转证明** |

实测结果（本机 python 3.11.6）见 `gate_evidence.md`：`verdict=PASS`，
A 恢复 3 步 / B 0 步 / C1 0 步且 degraded / C2 用前缀 3 步 / D 恰好用满 4 次后诚实停 / E 判红。
pytest 形态：`pytest tests/test_harness_v3_resume.py -q`（7 项，含"恢复步不重做"——被恢复步骤
证据文件 `attempt` 字段保持 1，新步骤为 2，可机器区分"复用"与"重做"）。

---

## 5. 与 m1121 事故根因对照

| 事故链环节 | m1121 表现 | Harness v3 控制 | 证据 |
|---|---|---|---|
| 进度只存在于内存 | 15 分钟工作随 stop 全丢 | 每 3 次调用 append-only 落盘 | A 场景恢复 3 步 |
| 重领后从零 | 重读代码、重复探索 | claim 前置续跑简报（含计划/下一步意图/脏工作树） | A `resume_brief_injected` |
| 崩溃无归因 | DB 只剩心跳停在 call N | `attempt_start/attempt_end` 记 status + deviations | `sample_journal_m101.jsonl` |
| 无日志时无法判断 | 只能信模型自述 | 无日志 → `mode=fresh` 显式声明"不要声称续跑" | B / C1 |
| 日志坏时易吹牛 | 无机制 | 逐步证据复核，缺证据即重做 | C1 / C2 |
| 续跑可能放大预算 | 此前不续跑，无此口径 | `calls_used` 跨尝试累计、耗尽即停 | D（4/4） |

---

## 6. 注意点与局限（如实）

1. **恢复上限是"已落盘的最后一条记录"**：kill -9 落在两次 checkpoint 之间时，最多丢
   2 次调用的语义增量（节律 3 次调用）。要更细就提高频率，代价是写放大。
2. **"已完成步骤"的定义是产物路径级**，不是任务语义级。产物被覆盖/移动后，续跑只保证
   "这条路径存在"，不保证内容正确——因此简报把它标为"已复核存在"，并要求冲突以工作树为准。
3. **本单未改 `tp_worker.py`**（守护侧文件，不在工作树/白名单）。补丁与钩子已就绪，
   但"平台侧真的接上"需要 owner 应用 §3 的 3 处改动；在此之前，端到端证据由
   `harness_seat.py`（`run_mission` 最小同构）+ supervisor 提供。
4. **多席位并发同单**未有仲裁（平台 claim 语义本身保证一单一会话）；若未来放开，
   需要在 `append()` 外再加跨进程 flock。
5. **journal 不含任何凭据/密钥**：只记路径、状态、意图、计数。
6. 背景报告 `/srv/tensor/agent/bench/ai_native_infra_map_v1.md` §4 在本机**不存在**
   （`/srv/tensor/agent/bench/` 下只有 `frontier_benchmark_v1.md`），故本单的"优先一"解读
   以任务书原文为准，未引用该文件内容（记为 WITHHELD，不臆造）。

---

## 7. 复现

```bash
# 全门禁（约 6 s）
python3 scripts/run_harness_v3_gate.py --outdir docs/harness_v3
cat docs/harness_v3/gate_evidence.md

# pytest（21 项）
python3 -m pytest tests/test_harness_journal.py tests/test_harness_journal_hook.py \
                 tests/test_harness_v3_resume.py -q

# 手工看某单续跑简报 / 日志完整性
python3 scripts/harness_journal.py brief  --mission-id 101 --root /tmp/j
python3 scripts/harness_journal.py verify --mission-id 101 --root /tmp/j
```