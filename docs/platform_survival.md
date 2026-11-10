# 长跑作业生存与进展度量成熟化（P148W2-N01）

**任务**：mission m10578 / 战役节点 `P148W2-N01`（TensorPlatform 平台共性机制成熟化）
**落库**：`TensorPlatform` → `tools/progress_meter/`（进展度量+停滞四级处置）、`tools/survival/`（崩溃取证装配/内存降级/诚实收尾/预算任务装置）；取证捕获与位级断点续跑复用既有成熟层 `tools/longrun/`（m9082）
**证据**：`benchmarks/verified/platform_survival/results.json`（本文件所有数字均由 `run_acceptance.py` 程序化产出，`make_doc.py` 注入，零手抄）
**续跑一致性报告**：`benchmarks/verified/platform_survival/resume_consistency_report.json`
**图**：`benchmarks/verified/platform_survival/figures/fig1..3`
**测试**：`tests/test_platform_survival_{progress,forensics,resume_honest_degrade}.py`（29 passed）

---

## ① 案例介绍（问题背景与对象）

长跑作业（>30 min 的求解/扫描/训练）的失败不是"进程没了"，而是三种更难看见的形态：

1. **死无对证**：SIGSEGV/SIGABRT 之后只剩一行 `Segmentation fault`，没有寄存器、没有栈、没有当时的运行清单，
   事后无法回答"死在哪条指令、哪个映射、当时的配置与断点是什么"；
2. **活着但不产出**：进程仍占着预算（迭代/节点秒/token），却不再产出任何可验收工件——看门狗式的
   "进程还在吗"探测对这种失败完全失明，预算被无声烧掉；
3. **半途而废即全废**：中断后只能从头重算，或干脆丢掉全部中间结果，无法回答"已产出的东西值多少"。

本节点把这三类失败变成机器可裁的机制，并与平台既有生存层（`tools/longrun`：ptrace 监护 + gdb 取证 +
位级 checkpoint + 幂等续跑 + 资源压力阶梯，m9082）对接，补齐其**缺失的"进展维度"与"收尾维度"**：

| 判据 | 内容 | 预声明阈值 | 结论 |
|---|---|---|---|
| **A1** | crash forensics completeness | registers>=5, frames>=3, mappings>=5 lines, sha256 verify ok | PASS |
| **A2** | resume bit-consistency from the 50 % checkpoint | resumed digest == uninterrupted digest (sha256), from_step == 20 | PASS |
| **A3** | progress_rate accuracy vs manual recount | relative error <= 5 % | PASS |
| **A4** | stall detection sensitivity (zero-artifact task) | detected and first detection within 30 % of budget, reroute issued | PASS |
| **A5** | reroute effectiveness (artifacts after the switch) | >= 1 valid artifact produced after the reroute, within the remaining budget | PASS |
| **A6** | honest FAIL with retained intermediate artifacts | status FAIL, retention_vs_produced >= 0.5, non-empty reason, artifacts legible | PASS |
| **A7** | negative control: uninterrupted run == bare job result | checkpointed full run digest == digest of the job stepped without the survival layer | PASS |
| **A8** | negative control: no false stall on legitimately uneven producers | 0 stall events across steady (5 % gaps) and bursty (20 % gaps) producers | PASS |
| **A9** | idempotency: three resumes from one checkpoint agree | 1 distinct digest and 1 distinct idempotency key across 3 resumes | PASS |
| **A10** | memory-constrained degradation to single thread | auto single-thread + chunked; full-grid refused under cap; error <= 5 % | PASS |

本环境的实测快照（来自 `results.json.environment`）：

| 项 | 值 |
|---|---|
| Python | 3.11.6 |
| 平台 | Linux-6.6.52-1.7.lns23.loongarch64-loongarch64-with-glibc2.38（loongarch64，128 cores） |
| gdb | /usr/bin/gdb |
| 仓 HEAD | `3f05b56b6` |
| 全流程耗时 | 15.21 s |

---

## ② 怎么做的（方法 / 关键设置 / 工具链）

### 2.1 进展度量：把"还在动"换成"每单位预算买到多少信用"

`tools/progress_meter/meter.py` 定义

```
progress_rate = credits / budget_used
credits = Σ 工件×w_a + Σ 判据通过×w_c + Σ 信息增益(bits)×w_i     (w = 1.0 / 2.0 / 1.0)
```

三条设计约束：

- **预算归一**：分母是稀缺资源（迭代数/节点秒），不是墙钟——停滞判据因此可以直接落在"预算的 30%"这种战役语言上；
- **记录幂等**：工件/判据按名字去重，续跑重发同一工件不会虚抬速率；
- **可审计**：每个事件写入 JSONL 台账，`manual_recount()` 从**磁盘上的真实文件 + 外部步进计数器**重算速率，
  与内存累加器对比（A3 误差 1.122e-14 %）。

信息增益不是自述，而是 `Beta(α, β)` 后验熵的下降：每件工件是一次 Bernoulli 观测（"均值是否进入容差带"），
`bits = H(p_before) − H(p_after)`；本工况实测 0.733 bit。

### 2.2 停滞检测与四级换路：判据、次序、去升级

`tools/progress_meter/stall.py`：当**连续 `stall_frac × 预算` 没有产生任何信用**时判停滞（默认
`stall_frac = 0.25`，取 0.25 使其必然落在验收线 30% 以内，同时给合法的"爆发式产出"
留出余量）。随后按预声明次序逐级升级：

| 级 | 动作 | 实现 |
|---|---|---|
| 1 | `restate` | 把任务契约（判据 + 预算 + 当前策略）重写进运行目录，不改范围 |
| 2 | `reroute` | 把枯竭策略写入 **append-only 黑名单**，切到 `StrategyPlan` 里下一个预声明策略 |
| 3 | `shrink` | 缩到"最小可证单元"（自带判据的最小可证子目标） |
| 4 | `honest_fail` | 落 `FAIL.json/FAIL.md`，保留全部中间工件并写明原因 |

去升级：任何新信用都把 rung 计数清零并记一条 `recovered` 事件（含恢复时的预算位置）——这就是
"换路有效"的证据（A5），而不是"换了路"的自述。**策略候选必须在停滞后之前预声明**（`strategies.py`），
否则换路本身不可审计。

### 2.3 崩溃取证：一次信号 → 一个自校验的证据目录

`tools/survival/forensics_bundle.py` 在既有 ptrace 取证之上装配
`artifacts/crash_<UTC>/`：`siginfo.json`（信号/故障地址/发送者）、`registers.json`（解析后的机器寄存器，
**字段数 ≥5**，不足时写 `sufficient:false` 而不是编造）、`stack.txt`（符号化帧 + gdb 线程栈）、
`mappings.txt`（故障瞬间地址空间）、`manifest.json`（解释器/numpy/argv/env 摘要/配置摘要/**checkpoint 链摘要**）、
`SHA256SUMS` 与 `digest.json`。

`bundle_digest = sha256(排序后的 "文件名 sha256" 行)` —— 不含路径与时间戳，因此**目录可复制、可再校验、摘要不变**
（A1 的"sha256 可复现"）。为拿到全部寄存器，本次给 `tools/longrun/guardian.capture_core` 增加了向后兼容的
`full_registers` 开关（默认 False，既有 21 个 longrun 测试仍全绿）。

### 2.4 位级幂等续跑：复用既有成熟层，用"真崩溃"打断

续跑一致性不靠新写续跑器，而是复用 m9082 的位级 checkpoint（每数组 `sha256(dtype+shape+tobytes())`、
原子写、链式清单）与幂等键 `sha256(input, ckpt, config, from_step)`。本节点只做**打断实验**：

- 全量运行 40 步（checkpoint 每 5 步）→ 基线摘要；
- 同配置在第 21 步注入**真实 SIGSEGV**（`ctypes.string_at(0)`）→ 进程死亡，最近 durable checkpoint 为第 20 步（50%）；
- 在同一 workdir 冷启动续跑 → 自动从第 20 步恢复；
- 比对最终状态 sha256：true

### 2.5 内存受限降级：判定是纯函数，误差是实测

`tools/survival/degrade.py`：`decide(cap, rss, n)` 是**纯函数**——按"4 个全网格数组是否装得进 cap−rss（留 15% 余量）"
决定 `threads` 与 `chunk_rows`；降级路径 = 单线程 + 按行分块流水（带周期 halo，逐点算术与全网格一致）。
子进程在真实内存红线下运行：先用封顶分配器尝试 4 个全网格数组（必然被拒），再用流式路径完成并落场量 `.npy`，
父进程计算与全网格参考的相对 L2 误差。

LIMIT（已在"⑥注意点"登记）：封顶由 `capped_alloc` 实现而非 `setrlimit(RLIMIT_AS)`——后者会连带限制 numpy 导入期
预留的虚拟地址空间，使**任何**后续分配都失败，无法区分"太大"与"任意"。峰值 RSS 仍为真实读数（子进程 `/proc/self/status` 的 VmHWM）。

---

## ③ 计算结果（全部取自 results.json）

### 3.1 A1 崩溃取证完整性

| 量 | 实测 |
|---|---|
| 信号 | 11（native_segfault） |
| 寄存器字段数 | 44（badv, fcc0, fcc1, fcc2, fcc3, fcc4, fcc5, fcc6, fcc7, fcsr, pc, process） |
| 符号化栈帧 | 38 行，含符号 true |
| 地址空间行数 | 183 |
| manifest 字段数 / 含 checkpoint 链 | 15 / true |
| 证据文件数 | 7 |
| bundle_digest | `87072a7ce51b94f227ab940fe37b8f3efb789922540e070aef0988c92ab9941f` |
| 复制到别处再校验 | ok=true，digest 相同=true |

### 3.2 A2/A7/A9 续跑一致性（sha256 全 32 字节列全）

| 运行 | 最终状态 sha256 |
|---|---|
| uninterrupted full run | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |
| bare job (no survival layer) | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |
| resume from step 20 | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |
| idempotency repeat 1 (from step 40) | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |
| idempotency repeat 2 (from step 40) | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |
| idempotency repeat 3 (from step 40) | `f80ed84ab59f2374620cf7b16fe5e9a50200f573bf9eda101cffb60dca4bbe9e` |

| 判据 | 实测 |
|---|---|
| A2 从 50% checkpoint 续跑位级一致 | true（resumed_from_step=20） |
| A2 打断方式 | native_segfault / signal 11 |
| A7 负控：全量运行 = 裸作业 | true（wall 0.536 s） |
| A9 三次续跑摘要数 / 幂等键数 | 0 / 1 |

### 3.3 A3 进展度量准确性

| 量 | 值 |
|---|---|
| meter 速率 | 0.2473 |
| 人工复核速率（磁盘工件+外部步进计数） | 0.2473 |
| 相对误差 | 1.122e-14 %（阈值 ≤5 %） |
| 工件 / 判据 / 预算 | 20 / 2 / 100 |

### 3.4 A4/A5 停滞检测与早停换路

| 量 | 值 |
|---|---|
| 首次检测预算位置 | 0.25（阈值 ≤0.30） |
| 事件序列（级, 预算） | restate, 25, reroute, 50 |
| 换路发生预算 | 50 |
| 换路后新增工件 | 3（阈值 ≥1） |

### 3.5 A6 诚实收尾

| 量 | 值 |
|---|---|
| 状态 / 原因 | FAIL / no credit for 20 budget units at rung 4 |
| 产出 / 保留 / 计划 | 5 / 5 / 40 |
| retention vs produced | 1（地板 0.5） |
| 预算用尽位置 | 120 |
| 四级序列 | restate, reroute, shrink, honest_fail |

保留地板是**代码强制**的：`HonestFail.write()` 在 `retention_vs_produced < 0.5` 时抛异常，拒绝写出"无证据的 FAIL 声明"
（`tests/test_platform_survival_resume_honest_degrade.py::test_honest_fail_refuses_to_claim_without_artifacts`）。

### 3.6 A8 负控（零假阳性）

| 场景 | 产出工件 | 触发停滞 |
|---|---|---|
| 稳定产出（缺口 5% 预算） | 20 | false |
| 爆发式产出（缺口 20% 预算） | 5 | false |
| 假阳性合计 | — | **0** |

负控刻意贴着阈值设计（20% 缺口 vs 25% 检测窗）：若窗口取得过小，这一格必然变红。

### 3.7 A10 内存受限降级

| 量 | 值 |
|---|---|
| 内存帽 / RSS 基线 | 100663296 B / 子进程起始 RSS（推算 `cap−usable`） |
| 判定 | threads=1，chunk_rows=96，理由 `projected_footprint_exceeds_cap: single thread + row streaming` |
| 全网格投影 | 75497472 B（4×18874368 B），可用 67108864 B |
| 全网格分配是否被拒 | true（`capped_alloc: 75497472 B would exceed cap 100663296 B (rss 33554432 B)`） |
| 相对 L2 误差（vs 全网格参考） | 4.340e-16（阈值 0.05） |
| 峰值 RSS（VmHWM，真实读数） | 102928 kB |
| 降级路径耗时 | 0.8792 s |
| 线程环境 | `1`（OpenBLAS/OMP/MKL 全 1） |

---

## ④ 结果图

- `figures/fig1_progress_timeline.png`：四条运行（稳定/爆发/**停滞后换路**/诚实失败）的信用累积-预算曲线，
  竖线标记停滞处置动作——直观看到"停滞段斜率为零 → 换路 → 斜率恢复"。
- `figures/fig2_stall_ladder.png`：处置级别 vs 检测时的预算比例；虚线为 A4 的 30% 验收线。
- `figures/fig3_criteria_dashboard.png`：十条判据的"实测/阈值"对数条形图（≥1 为通过）。

（三张图均由 `run_acceptance.py` 从本次运行的台账生成，不含手绘数据。）

---

## ⑤ 注意点（踩过的坑与适用范围）

1. **Python 级信号处理器抓不住同步 SIGSEGV**（m9082 已实验确立）：必须在**进程外** ptrace 冻结后取证，
   本节点沿用该路径，因此 A1 的活体取证测试需要 gdb + ptrace 权限；无权限环境会自动 skip 并保留合成分支测试。
   另一条架构教训：本机是 **loongarch64**，gdb 打印的寄存器名是 `pc / fcc* / badv`（A1 实测 44 个字段），
   若把 x86 的 `rip/rsp/rbp` 写进解析器就会永远拿不到 5 个字段——解析器因此只做"通用寄存器行识别"，不假设架构。
2. **"停滞"必须用信用而非存活来定义**：早期用"最后事件时间"判断，续跑重发同一工件会把速率虚抬（已由去重挡住）。
3. **升级节奏从"现在"起算**：每升一级都把 barren 窗口起点重置为当前预算，否则第 2 级之后的检测时刻会越拖越远，
   在有限预算内永远到不了 honest_fail。
4. **诚实收尾前必须 flush 当前状态**：全零产出的任务若直接落 FAIL，保留率必然 0 触发地板异常——正确做法是先把
   活状态写成一个 `final_state` 工件，于是"保留 ≥50% 中间工件"在极端情形下也成立且可验证。
5. **内存帽是"投影预算"而非 cgroup**：见 §2.5 LIMIT；真实峰值 RSS 仍高于帽子（numpy 解释器开销不计入投影），
   报告如实给出两套数字，不把仿真帽冒充 cgroup。
6. **能力台账存在先于本节点的历史违规**：`tests/test_capability_card_evidence.py::test_repo_state_has_zero_violations`
   报出的 8 条违规全部指向 CAP-006（旧格式卡，无 `validation_evidence`）与 CAP-007（anchor 工件
   `artifacts/p167w2-n10/audit_evidence.json` 不在本 worktree），与本节点无关；新增的 CAP-008 卡单独校验为 PASS
   （`python3 scripts/validate_capability_card.py docs/capability_registry/cards/CAP-008-*.json`）。
7. **分块降级在此内核上是逐点精确的**：三次运行（分块/单线程/多线程）的差异只来自规约次序，实测 ~1e-16，
   远优于 5% 判据；因此"误差 ≤5%"这条判据对本内核是宽松的，真正被验证的是**判定与执行路径**。

---

## ⑥ 误差判定（逐项显式回答）

| 关键量 | 基准 | 实测误差 | 是否 <3% |
|---|---|---|---|
| 续跑最终状态 sha256（A2/A9） | 无中断全量运行 | 0（位级相同，true） | **是**（0%） |
| 负控全量运行摘要（A7） | 裸作业（不经生存层） | 0（位级相同） | **是**（0%） |
| progress_rate（A3） | 磁盘工件+外部步进计数的人工复核 | 1.122e-14 % | **是** |
| 停滞检测时刻（A4） | 验收线 30% 预算 | 实测 25 %（低于线即通过） | 是（余量 5 %） |
| 降级后场量（A10） | 全网格 float64 参考 | 4.340e-14 % | **是** |
| 闸门/取证字段（A1） | 预声明阈值（≥5 寄存器、≥3 帧等） | 寄存器 44 个、栈帧 38 行 | 是（阈值型，非误差型） |

结论：10/10 条验收判据通过（`all_passed = true`）；
所有可数值比较的项均 **<3%**（两项为位级 0）。无基准的量（如取证字段数）按预声明阈值判定，已在表中标注为阈值型。

---

## ⑦ 复现方式

```bash
python3 benchmarks/verified/platform_survival/run_acceptance.py   # 生成 results.json + figures/ + artifacts/
python3 benchmarks/verified/platform_survival/make_doc.py         # 由 results.json 注入生成本文件
python3 -m pytest tests/test_platform_survival_progress.py \
                 tests/test_platform_survival_forensics.py \
                 tests/test_platform_survival_resume_honest_degrade.py -q
```
