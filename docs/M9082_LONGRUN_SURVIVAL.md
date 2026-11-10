# 长跑作业生存层：核心转储取证 + 位级幂等断点续跑 + 资源降级阶梯

**任务**：mission m9082 / 战役节点 `p148w2-n03-crash-forensics-resume`（DAG 层：长跑作业生存层）
**落库**：`TensorPlatform` → 新模块 `tools/longrun/`（零领域语义，纯共性机制）
**证据**：`runs/m9082/evidence.json`（全部数字由 `tools/longrun/bench_m9082.py` 程序化生成，零手抄）
**图**：`runs/m9082/figures/fig1..4`（matplotlib 生成）
**测试**：`tests/test_longrun_{forensics,checkpoint_resume,ladder,overhead}.py` 共 32 用例

---

## ① 案例介绍（问题背景与对象）

plan 148 中 m6622/m6945 等长跑作业（>30 min 计算）崩溃后**无尸检**：进程被 SIGSEGV 杀死，只留下
一行 "Segmentation fault"，既没有栈、也没有可续跑的状态，整段算力被丢弃；另一类失败是内存压力下
OOM kill/超时，作业直接消失，无法回答"死在哪一步、当时内存多少、能否从上一个断点继续"。

节点要求的四个能力，本实现对应为四个可独立测试的部件：

| 能力 | 部件 | 关键判据 |
|---|---|---|
| 崩溃取证 | `guardian.py`（ptrace 监护 + gdb 取证）、`forensics.py`（机载记录仪/分类/完整率） | 故意崩溃 → 取证完整率 100% |
| 断点续跑 | `checkpoint.py`（位级摘要+原子写+链式清单）、`resume.py`（幂等键+审计） | 同输入+同断点 → 同输出（位级） |
| 资源降级 | `ladder.py`（四级阶梯） | 压力上升 → 按序降级 → 优雅退出且末态可续 |
| 负控 | `runner.py` + `bench_m9082.py` | 正常作业取证开销 ≤ 2% |

测试作业 `job.py::SyntheticIterativeJob` 是刻意"无物理语义"的线性迭代松弛
`u ← u + dt·(∇²u + c)`（周期网格、种子固定、纯逐元素 IEEE-754 运算），只用于让上述机制可被
位级验证；**不含任何船舶/物理域词条**（TensorPlatform 铁律：零领域语义）。

---

## ② 怎么做的（方法 / 关键设置 / 工具链）

### 2.1 一个决定性的试验事实：Python 层信号处理器抓不住同步 SIGSEGV

先做了三次对照实验（全部记录在案）：

1. `signal.signal(SIGSEGV, handler)` + `ctypes.string_at(0)` → **处理器永不执行**，进程挂死在
   信号-重试循环（40 s 超时），因为同步故障信号在返回后会重执行故障指令，而 CPython 的
   Python 级处理器只在字节码边界运行。
2. 在处理器里 `fork+exec gdb`（breakpad 式）→ 同样进不去，说明"在 Python 里写崩溃处理器"这条路不通。
3. 改为**进程内 C watchdog**（`faulthandler.dump_traceback_later`）由内核直接回调 C 代码 →
   能持续输出全线程 Python 栈，但它只能"记录"，不能冻结进程取核。

结论：取证必须由**外部监护进程**完成 → 采用 crashpad/breakpad 的 ptrace 方案。

### 2.2 ptrace 监护（`guardian.py::run_guarded`）

```
fork → 子进程 ptrace(PTRACE_TRACEME) → execve(job)
父进程 poll waitpid：
  SIGTRAP(exec)      → PTRACE_CONT(0)
  非致命信号          → PTRACE_CONT(sig)   # 语义透传，作业行为不变
  致命信号(SIGSEGV/ABRT/BUS/FPE/ILL)
        → PTRACE_GETSIGINFO（si_signo/si_code/si_addr）
        → PTRACE_DETACH(data=SIGSTOP)     # 冻结，等 gdb 接管
        → gdb -p <pid> -batch: info threads / thread apply all bt 12 /
                               info proc mappings / thread apply all info registers /
                               [可选] gcore <path>
        → addr2line -f -C 补全 ? 帧
        → SIGKILL 收尸
```

**踩到的坑（已修，逐条留证）**：

* `siginfo_t` 缓冲区必须 128 字节：`PTRACE_GETSIGINFO` 整块拷内核结构，24 字节的短缓冲会
  静默写穿父进程堆，30 行之后才随机 SIGSEGV（表现为"监护者自己崩了"）。修复后
  `segv` 现场给出 `si_code=1 SEGV_MAPERR, si_addr=0x0`。
* `PTRACE_DETACH` 之后的 tracee 处于 group-stop，只能 `SIGKILL`；早期写 `PTRACE_CONT` 导致
  守护进程永久阻塞（120 s 超时）——已改为 `os.kill(pid, SIGKILL)`。

### 2.3 两级取证工件（为什么不是"永远一个 4 GB core"）

| 级别 | 内容 | 体积（实测） | 默认 |
|---|---|---|---|
| tier-1 `stackcore.json` | 全线程 backtrace、符号化帧、内存映射、寄存器、siginfo、gdb 版本 | **8.9–30.8 KB** | 开 |
| tier-2 ELF core | `gcore` 真核，可独立 `gdb <exe> <core> -ex bt` 复现栈 | 小进程 **14 MB**；numpy 进程 **4.44 GB** | 关（可开） |

原因：本运行时给**每个** python 进程映射了一个 4 GiB 匿名段（`/proc/self/maps` 可查，
`runtime_largest_mapping_bytes = 4294967296`），所以 numpy 进程的 ELF core 实测 4.44 GB、
耗时 43 s。因此 `max_core_bytes`（默认 2 GiB）守卫磁盘：超限 core 先算 sha256 与体积留证、
再删除，`core_status = stackcore_only:elf_core_oversize`。**不做静默降级**：状态字段始终说明
保留了哪一级、为什么。

### 2.4 机载记录仪（`forensics.py::FlightRecorder`）

* `flight.log`：`faulthandler.dump_traceback_later(1 s, repeat)` 的 C watchdog 全线程栈快照
  → 崩溃后栈快照最多 1 s 陈旧（明确写进 bundle 的时间戳）。
* `journal.jsonl`：状态日志（步序号、检查点摘要、阶梯事件、内存探测），**限流 0.5 s/条**
  （每步一条要 21 µs，见 §⑤ 的开销账），末步另存 `last_step` 并在 `close()` 写入。
* `meta.json`：环境指纹、`code_version`（commit）、内存探测、成分开销计数器。

### 2.5 位级检查点（`checkpoint.py`）

* 每个数组摘要 = `sha256(dtype|shape|order ‖ tobytes())` → **位级**，不是容差比较。
* 原子写：同目录 `.tmp` → `fsync(file)` → `os.replace` → `fsync(dir)`；崩溃不可能留下半成品。
* 链式清单：`prev_digest` 串联历史检查点（回滚/删档可检测），`digest` 覆盖清单除自身外的全部字段。
* 失败必响：`CheckpointCorruption` / `CheckpointUnreadable`，**禁止**静默回退到旧状态。
* 保留策略：`prune(keep=4)`，默认只留最近 4 个（本次踩坑：`from_step=20` 的续跑因 20 步档被
  prune 掉而报 `state_corruption` —— 行为正确，是配置问题，测试改用 `keep_checkpoints=10`）。

### 2.6 幂等续跑（`resume.py`）

```
key = sha256(canonical{input_digest, checkpoint_digest, config_digest, from_step})
```

`plan()` 先验证清单摘要、再核对 `input_digest`/`config_digest`（脏档直接拒绝并留审计），
然后 `restore()` 载入状态；每次决策写 `resume_audit.jsonl`（`resume_plan` / `resume_applied` /
`resume_rejected` / `checkpoint` / `final_checkpoint` / `graceful_exit`）。

### 2.7 降级阶梯（`ladder.py`）

四级、有序、不可逆（不允许回到更贵的配置，否则结果不可复现）：

| 顺序 | rung | 触发（利用率） | 作业侧动作（实测） |
|---|---|---|---|
| 1 | `reduce_precision` | ≥0.80 | float64 → float32（占用 ÷2） |
| 2 | `reduce_grid` | ≥0.88 | 网格隔点降采样（占用 ÷4） |
| 3 | `reduce_timestep` | ≥0.94 | dt ×2 |
| 4 | `graceful_exit` | ≥0.98 | 刷末态检查点 → 退出码 **75**（sysexits EX_TEMPFAIL） |

探测源按 cgroup v2 → cgroup v1 → `/proc/meminfo` 依次回退，探测限流 0.5 s/次；
一次跨多阈值时**按序补齐**所有被跳过的 rung（状态与策略保持一致）。
`ScriptedProbe` 让"压力"可脚本化，从而把策略与主机真实内存解耦、可单测。

---

## ③ 计算结果（全部数字来自 `runs/m9082/evidence.json`）

### 3.1 负控：正常作业取证开销 ≤ 2%

方法：ABBA 分块（同进程同状态，交替"裸块 / 带取证块"两序，抵消位置偏置），CPU 时间
（`process_time`，隔离宿主机他进程竞争），grid=1024、6 步/块、12 轮，单核绑核。

| 指标 | 值 |
|---|---|
| 逐轮开销（%） | −6.39, −15.85, −14.49, 3.93, −1.45, −0.69, 0.80, −12.00, 1.46, 3.33, −0.60, 14.52 |
| **中位数** | **−0.65 %**（噪声主导，低于测噪底） |
| 最大（干扰尖峰） | +14.52 % |
| 仪器自计成分开销 | **0.0889 %**（journal + 内存探测，`overhead_ns / layer_cpu_seconds`） |
| 判据 | 中位数 ≤2 % ✔，仪器自计 ≤2 % ✔ |

**方法学留证**：AB-only（每轮固定先裸后带）曾测出"幻影 +5.27 %"，把顺序对调后变成 −3.03 %，
即位置偏置（cache/TLB 预热与频率漂移）主导了信号 → 故改用 ABBA 与中位数门，并把这条写进
`tests/test_longrun_overhead.py` 的文档字符串，防止后人再踩。

### 3.2 崩溃取证：五种故意崩溃 → 完整率 100 %

| 模式 | 判定 | 信号(si_code) | 退出 | tier-1 工件 | 原生帧/已符号化 | 完整率 |
|---|---|---|---|---|---|---|
| `segv` | native_segfault | 11 (1 = SEGV_MAPERR, addr 0x0) | −11 | 30831 B | 19/19 | **100 %** |
| `abort` | native_abort | 6 (−6 = SI_TKILL) | −6 | 30712 B | 19/19 | **100 %** |
| `exception` | python_exception | — | 70 | 无核（非信号） | — | **100 %** |
| `memory_error` | python_memory_error | — | 71 | 无核（非信号） | — | **100 %** |
| `sigkill` | killed_externally | 9（cgroup oom_kill 未增长，故不判 OOM） | −9 | 无核 | — | **100 %** |

完整率按 15 个必需字段计（schema/job_id/pid/起止时间/exit_code/term_signal/failure_mode/
分类理由/Python 栈快照/状态日志尾/内存探测/运行环境/代码版本/最后检查点摘要；ELF core 在
信号类崩溃额外计入必需）。网格/序列号/摘要均可回溯到 `runs/m9082/artifacts/bundle_crash_*.json`。

### 3.3 位级幂等续跑

测试作业 40 步（grid 32、每 5 步存档、保留 10 档）：

| 续跑点 | 结果摘要（sha256 前 16） | 与不间断运行位级一致 | 幂等键（前 12） | 两次重复键一致 |
|---|---|---|---|---|
| 不间断 | `f80ed84ab59f2374` | — | — | — |
| 10 | `f80ed84ab59f2374` | ✔ | `1dbad8a55a4f` | ✔ |
| 20 | `f80ed84ab59f2374` | ✔ | `93f94d256fec` | ✔ |
| 35 | `f80ed84ab59f2374` | ✔ | （见 evidence.json） | ✔ |

位级 = `sha256(dtype‖shape‖状态字节)`，即 **0 ULP、0 位差**，不是容差意义下的"一致"。

### 3.4 检查点损坏检出

| 探针 | 翻转数 | 检出 | 检出率 |
|---|---|---|---|
| 数组载荷内随机字节（zip 条目数据区，按 header 偏移精确取样） | 40 | 40 | **100 %** |
| 整文件盲翻转（含 zip 簿记区） | 60 | 60 | 100 %（本次盲取样全部落在载荷内） |
| 清单字段篡改（改 `scalars.dt`） | 1 | 1 | 100 %（清单摘要不符） |
| 删除一个数组 | 1 | 1 | 100 %（`unexpected/missing arrays`） |
| 残留 `.tmp` 半成品存在 | 1 | — | 正确忽略，仍加载完整档 |

注：盲翻转若落进 zip 的 extra-field/padding，任何读者都看不到且状态**可证明未变**，这不是
"漏检"而是"无所谓"；开发期一次盲取样确实出现 58/60，故测试改为按载荷取样以断言真正的性质。

### 3.5 降级阶梯

| 项 | 值 |
|---|---|
| 触及顺序 | `reduce_precision → reduce_grid → reduce_timestep → graceful_exit` |
| 状态占用 | 65536 B → 8192 B（**0.125×**，下降 8 倍） |
| 末态 | grid 32→16、precision float32、dt 0.25→0.5 |
| 退出码 | **75**（`GRACEFUL_EXIT_CODE`），`failure_mode = graceful_degraded_exit` |
| 末态检查点 | 可加载、`digest` 自校验通过，`bundle completeness = 1.0` |

### 3.6 ELF 真核（tier-2）

| 项 | 值 |
|---|---|
| 小进程真核 | 14 048 136 B，`core_status = stackcore+elf`，独立 `gdb exe core -ex bt` **reopen_ok=True**，13 帧 |
| 取证耗时 | 0.264 s |
| 尺寸守卫（cap=1 MiB） | 核先算 sha256/体积 → 删除 → `stackcore_only:elf_core_oversize`，tier-1 仍保留 |
| 运行时最大匿名映射 | 4 294 967 296 B（4 GiB）→ numpy 进程真核实测 4 448 817 440 B（开发期一次，未重跑以省磁盘） |

---

## ④ 结果图

| 图 | 内容 | 读法 |
|---|---|---|
| `fig1_overhead.png` | 12 轮 ABBA 逐轮开销柱状 + 2 % 预算线 + 中位线 | 柱子在 0 附近正负抖动（噪声），无系统性抬升；红线远在上方 |
| `fig2_autopsy.png` | 五种崩溃模式完整率（左）与 tier-1 工件体积（右） | 左图全 100 %；右图 `exception`/`memory_error`/`sigkill` 无核（Python 级/外部杀），标注说明 |
| `fig3_resume.png` | 断点位置 vs 位级一致性 | 所有断点落在 "identical" 水平线（3/3） |
| `fig4_ladder.png` | 上：内存利用率探测与四级阈值；下：状态占用阶梯 | 每次跨阈值，占用阶梯下降，末端落到 graceful exit |

---

## ⑤ 注意点（踩坑、局限、适用范围）

**踩坑（都已修，且写了回归测试）**

1. Python 级 SIGSEGV 处理器对同步故障无效（会自旋）→ 必须外部 ptrace 监护。
2. `PTRACE_GETSIGINFO` 的 24 B 短缓冲写穿堆 → 延迟随机 SIGSEGV；改为 128 B 全尺寸 `siginfo_t`。
3. `PTRACE_DETACH`+SIGSTOP 后只能 SIGKILL；写 `PTRACE_CONT` 会永久阻塞。
4. `os.execv` 不接 `env`，须用 `os.execve`（否则子进程 127 静默失败）。
5. 工作树里 `.git` 是文件（`gitdir: ...`），朴素读 `.git/HEAD` 会得到 `commit=None`；已加
   worktree/packed-refs/`git rev-parse` 三级回退 —— 取证工件里"代码版本为空"是不可接受的证据洞。
6. 默认 `prune(keep=4)` 会让 `from_step` 指向已被清掉的老档 → `state_corruption`（行为正确）。
7. AB-only 开销测量有位置偏置（幻影 +5 %）→ ABBA + 中位数。
8. 每步写 JSON 日志=21 µs/步，对 10 ms 级步长就是 0.2 % → 日志限流 0.5 s，末步另存内存。

**局限（honest）**

* **内核 core_pattern 在本容器不可用**：`core_pattern` 是 `|/usr/lib/systemd/systemd-coredump`，
  而容器无 systemd（`coredumpctl` 不可用）→ 内核自发 core 拿不到，本层用 ptrace+gdb 自取
  （这一路由实测可用，见 §3.6）。若宿主有可用 `core_pattern`，两者可并存。
* **ELF core 体积受运行时地址空间支配**：numpy 进程 4.44 GB。默认只留 tier-1；需要真核时显式
  开 `full_core=True` 并用 `max_core_bytes` 约束。
* **位级一致限于同机同库**：跨 ISA（loong64 vs x86）浮点结果可能不同，届时须改用
  `tools/repro_digest`（ULP/12 位有效数字）判据，而不是 sha256。
* **栈快照陈旧度 ≤1 s、日志陈旧度 ≤0.5 s**（限流换开销）；崩溃时刻的精确栈由 tier-1 的 gdb 帧
  提供，Python 帧来自记录仪快照。
* **阶梯阈值是策略默认值**（0.80/0.88/0.94/0.98），非从数据标定；`ladder.enabled=false` 可整体关闭。
* **ptrace 语义副作用**：被监护进程每个信号都会先停一下（父进程 10 ms 轮询），对信号密集型作业
  有额外延迟；纯计算作业无影响。容器内 ptrace 需 `CAP_SYS_PTRACE`（本环境 root 可用）。
* 合成测试作业是纯逐元素运算，**没有**归约顺序、FFT、并行归约等非确定性来源；真实求解器接入时
  须保证"状态量全部入档"，否则位级幂等不成立（本层只提供机制，不替作业保证完备性）。

---

## ⑥ 误差判定（逐项，显式回答"是否 < 3 %"）

| 关键量 | 基准 | 实测 | 相对误差 | < 3 % ? |
|---|---|---|---|---|
| 取证层开销（中位数） | 裸循环 CPU 时间 | −0.65 % | \|0.65 %\| | **是** |
| 取证层开销（仪器自计） | 裸循环 CPU 时间 | +0.0889 % | 0.09 % | **是** |
| 续跑末态一致性 | 不间断运行 sha256 | 位级相同（0 位差） | 0 % | **是** |
| 断电/崩溃后检查点完整性 | 写入即自校验 | 40/40 载荷翻转检出、清单/缺数组/半成品全检出 | 0 % 漏检 | **是** |
| 崩溃取证完整率 | 100 %（判据） | 5/5 模式 = 100 % | 0 % | **是** |
| 阶梯末态可续 | 可加载且自校验 | 通过 | 0 % | **是** |
| 崩溃判定分类正确率 | 5 种注入模式 + 11 条单元矩阵 | 5/5 与 11/11 | 0 % | **是** |
| ELF 核可解析性 | `gdb exe core -ex bt` 成功 | 成功（13 帧） | — | **是** |

无基准项：**内核自发 core 采集**（容器 `core_pattern` 管道目标缺失）—— 不以"未测"充数，已如实
记为不可用并给出替代路径与其自身验证结果。

---

## 复现

```bash
python3 -m pytest tests/test_longrun_forensics.py tests/test_longrun_checkpoint_resume.py \
                 tests/test_longrun_ladder.py tests/test_longrun_overhead.py -q
python3 -m tools.longrun.bench_m9082 --outdir runs/m9082     # 重生成 evidence.json + 图
```

单次使用：

```python
from tools.longrun import supervise
rep = supervise.supervise({"job": {"grid": 256}, "steps": 200, "checkpoint_every": 20,
                           "crash": {"at_step": 120, "mode": "segv"}},
                          workdir="/tmp/run1", job_id="job1")
rep["failure_mode"], rep["core"]["status"], rep["bundle_completeness"]["completeness"]
```