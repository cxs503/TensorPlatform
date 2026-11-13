# FEAT-COMPUTE-WORKER-GATE —— tp_worker 计算递交门（run_compute + 本机 hog guard）

owner 2026-11-13 计算调度令（架构级）：**「应该建计算调度系统，agent 计算提交给调度系统，
跑在哪里是调度系统的事」**。本单把「提交」做成工具面唯一入口，把「本机重计算」做成硬拦。

## 1. 问题（实测，不是描述）
- 99 控制节点曾因**终态单孤儿进程**吃满负载（load 184/128 核 → 容量门停派 → 78 席闲置）；
  702 双计算节点仅用 28%（180 核闲）。根因不是算力不够，是**算力投错了地方**。
- 本单开工时段 99 的 load 实测 119 → 242/128 核；同刻 `ps` 快照显示 load 来源正是
  `work/m*/` 内的**本机**计算（不是集群作业）：
  | 任务 | 本机进程 | 实测核数 | 已耗 CPU 秒 |
  |---|---|---|---|
  | m17363 | python3 scripts/m17363_xval_run.py run | 9.91 | 4276 |
  | m17366 | 5× python3 (xval_akai2018_wetting) | ≈24 | 各 ~2300 |
  | m17375 | python3 runs/scratch_m17375/sweep.py | 8.42 | 1056 |
  | m17360 | python3 xval_convergence | 6.00 | 688 |
  | m17386 | python3 -m pytest papers/... | 4.32 | 891 |
- 同一时段 audit 到的**孤儿遗留树**（gate 视角 advisory）：
  pid 2078464 / `m15384` / `python -m pytest tests -q -n 16 -p no:cacheprovider`，
  **累计 273 629 CPU 秒（= 4 560 核·分钟 = 76 核·小时）**，仍存活 273 个进程，当前速率 0。
  这正是「吃满 99 后烂在树下」的同型遗留物。

## 2. 设计：一条门两道闸
| 闸 | 位置 | 判据 | 动作 |
|---|---|---|---|
| ① 静态硬拦（preflight） | `run_shell` 执行**前** | ≥8 并行（`pytest -n N`/`xargs -P`/`make -jN`）、后台逃逸（`nohup`/`setsid`/`&`/`tmux`）、长阻塞（`sleep ≥600`、`--timeout ≥600`） | 工具级拒绝 + 给出可直接照抄的 `run_compute` 参数 |
| ② 动态兜底（hog guard） | 常驻守护，`/proc` 每 60 s 扫 | work/m{id} 内进程树**累计 CPU 秒 ≥ 8 核×600 s（4800）** 且 **当前速率 ≥ 4 核** 且非队列 runner 子孙 | `SIGSTOP` 整树 → 账本贴「违例待迁移」→ 递交同命令到队列（`stage_dir`=原 worktree）→ 递交成功后 `SIGKILL` 本地；**递交失败则 SIGCONT 恢复、不杀** |

关键判断：
- **累计 CPU 秒**判「持续 ≥8 核 10 min」：单调、与采样抖动无关，等价刻画「8 核×600 s」与「32 核×150 s」。
  生产阈值 = 8.0 × 600 = **4800 CPU 秒**。
- 追加「当前速率 ≥ 阈值一半」：把**历史超限、当前空闲**的遗留树降级为 `leak_advisory`（只记账不动手）。
  理由：慢作业（0.5 核跑 3 h = 5400 CPU 秒）也满足累计判据，贸然杀 = 丢工作。
- 作用域 = **cwd 在 `/root/tp-agent/work` 之下、且父进程在域外**的子树入口（含孤儿，父=1 也算根）。
  系统守护进程（postgres/sshd/systemd）因此天然在门外，不存在误停控制面的可能。
- 豁免 = 队列 runner 特征串（`cluster_queue.py --sched`、`cluster_exec.py`、`sched_launch.sh`）的子孙
  （调度器派下来的活本来就是它的活），以及 gate 自己的**祖先链**（绝不杀启动自己的进程）。
- 上线预热窗 600 s：守护进程每轮启动先只看不动，账本先攒基线，避免接管瞬间与在飞任务对撞。

## 3. 递交面（run_compute，取代 run_cluster v2）
`submit` 返回 `{job_id, queue_position, queued_ahead, semantics}` **立即返回**；`status`/`fetch`（可 `--wait`）轮询；
`nodes`/`usage`；每份回执附 `receipt`（**local vs queued 用量**，JSONL 为真相源、PG 表 `compute_gate_events` 双写）。
节点指派/容量准入/FIFO 排队全部由既有 `cluster_queue.py --sched` 负责——本门**永不指定节点**。

## 4. 验证（跑了什么、数字是多少）
### 4.1 单测（机器门）`tests/tools/compute_gate/test_compute_gate.py`：**29 passed**
覆盖：/proc 解析与子树 CPU 聚合（own_ticks 不双计）、作用域取树入口（不取子进程/系统进程）、
runner 子孙豁免、阈值语义（8×600）、两采样才判（首扫只建档）、advisory 分流、
静态硬拦正/负样本（`pytest -n 32` 拦、`pytest -n 4` 放行、`nohup …&` 拦）、
迁移递交**同一条命令**且 `stage_dir`=原 worktree、递交失败 → SIGCONT 不杀、dry-run 不动手、账本回执。

### 4.2 红队实证（真起 32 核进程树 + 真递交）+ 负对照
`python3 -m tools.compute_gate.redteam --out artifacts/compute_gate/redteam.json`，
判据同形加速（`hog_seconds` 30 s → 阈值 240 CPU 秒；生产 600 s）：
- A1 检出违例树：32 核燃烧，`cpu_s=283.2`（阈值 240）、`cores_now≈20.8`，检出耗时 ≈9 s；
- A1b `SIGSTOP` 生效：整树进入 `T` 态；
- A2a 本地树被杀（无残留）；**A2b 拦截前已落盘的部分结果仍在原 worktree**（工作不丢）；
- A2c 递交返回 `cj1524`（排队位次如实回传）；A2d 作业已被调度系统跟踪；
- A3a 账本两条事件 `hog_detected`（note=违例待迁移）/`migrated`（带 jid）；
- A3b 负对照：**红队 harness 本轮判 RED**（4 核 25 s 的树在 harness 复扫里被判违例）——
  经查为 harness 复用 state/work_roots 的口径缺陷（真实产物：`artifacts/compute_gate/redteam.json`
  的 `checks[A3b]`，verdict=RED 已如实保留，不覆盖）。同判据的等价负样本在单测
  `test_scan_flags_active_hog_only_after_two_samples` 中为绿（4 核短树在 8×600 阈值下不违例）。
  修 harness 口径=下一轮待办（honest：负对照在本轮**未被 harness 证明**，只有单测级证明）。

### 4.3 生产切面自检
`guard --once --dry-run` 生产域扫描：174–428 棵树在册、**active violations=0**（此刻在跑的重算都还没到
8 核×10 min）、advisory=1（即上文 76 核·小时孤儿树）。说明门槛不误伤、且把遗留树看见了。

### 4.4 tp_worker 接线（机制化、可回滚）
`python3 -m tools.compute_gate.install --apply`：同步部署包 → 幂等打补丁（幂等锚点校验 + 自动备份
`tp_worker.py.bak_compute_gate_*` + `--rollback`）→ 起守护（systemd 单元 `deploy/systemd/tp-compute-guard.service`，
不可用时退化为 `setsid` 常驻 + PID 文件，仍然机制化、不依赖任何人类会话）。
接线后实测：`TOOLS` 含 `run_compute`；`tool_run_shell_gated(root,"pytest -n 32 tests/ --timeout 1200")`
→ `LOCAL-COMPUTE BLOCKED … cores=32`；`echo light-ok` 正常返回。

## 5. 误差判定（逐项 vs 基准）
| 关键量 | 基准 | 实测 | 误差 | 判定 |
|---|---|---|---|---|
| 阈值 CPU 秒（8×600） | 定义值 4800 | 4800 | **0%**（构造性相等，单测断言） | <3% ✔ |
| 排队位次 `queue_position` | SQL `count(state='queued' AND id<self)+1` | cj1524 回传 ahead=… | **0%**（同源查询） | <3% ✔ |
| 检出超调（阈值→检出时已多耗） | 理想 0 | +43.2 CPU 秒/240 = **+18%**（红队 2 s 采样周期） | 生产 60 s 周期下超调 ≤ 8 核×60 s = **≤+10%** | 超调是「晚一拍」，不改变判据；如实披露 |
| 迁移核数请求 `cores` | 命令真实并行度 32 | 20.8（EMA 冷启动 2 s 窗） | **−35%** | ✖ 超 15% 门槛：**如实记 FAIL 项**——准入记账偏保守（可能温和超订），不影响「停+迁+不丢工作」；后续用两拍以上速率或解析命令并行度修正 |
| 生产 load 下降 | 前 119–242/128 | 本单未做长时段对照 | — | not_run（预热窗 + 队列拥塞，见 6） |

## 6. 局限与未完成（honest）
- **cores 估计偏差 −35%**（上表），未在本单修正；建议下一轮用 2 拍 EMA + 命令并行度解析取 max。
- 生产守护进程以**预热窗 600 s 观察态**上线；窗口后转强拦。首小时只有 advisory/hog_detected 记录，
  未取得「生产上真的拦住并在节点跑完」的长时段证据（not_run）。红队已证明该路径可用。
- 迁移用 `stage_dir`=原 worktree 打 tar 上节点（排除 .git）：**递交后的本地改动不会进节点**；
  若 agent 继续改而队列作业在跑，两侧产物可能不一致（已在事件 detail 里记 `child_outputs_preserved_in`）。
- 队列拥塞时会话：递交作业可能长时间 `queued`（红队首轮 cj1514 等 >4.6 min 仍排队，同刻 157 个在飞作业）。
  这是调度系统语义（排队等待），不是门失败；但「迁移后多久拿到结果」取决于集群容量。
- 红队用同形加速阈值（30 s 而非 600 s）；真实 32 核跑 20 min = 38 400 CPU 秒，远超 4800，判据必然触发，
  但**未做 20 min 实时长跑**（成本考虑，如实记 not_run）。
- 事故归因（哪个 agent 在本机跑重算）不在本门范围；本门只保证「跑错地方的算力会被搬到队列」。

## 7. 复现命令
```bash
# 单测（机器门）
python3 -m pytest tests/tools/compute_gate -q                    # 29 passed
# 红队（真起 32 核树 + 真递交；同形加速阈值）
python3 -m tools.compute_gate.redteam --out artifacts/compute_gate/redteam.json
# 生产切面只读体检（不动任何进程）
python3 -m tools.compute_gate.cli guard --once --dry-run --work-roots /root/tp-agent/work
# 递交面
python3 -m tools.compute_gate.cli submit --repo TensorBEM --cmd "pytest -q" --cores 8
python3 -m tools.compute_gate.cli status --jid cjNNN
# 部署 / 体检 / 回滚
python3 -m tools.compute_gate.install --check | --apply | --rollback
```
