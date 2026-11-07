# IN27-H2：把 harness 层做成 mission 化的门控仓（三道门 + 部署管线 + 可执行诊断单）

**任务** `IN27-H2` ｜ **分支** `agent/m5306` ｜ **域** 平台 harness（TensorPlatform 入库，船舶物理域无涉）
**产物** `harness_gateway/`（代码）· `harness_gateway/mirror/`（harness 镜像）· `harness_gateway/patches/`（补丁）·
`harness_gateway/diagnostics/orders/`（诊断单）· `artifacts/harness_gateway/`（机器证据）· `tests/harness_gateway/`（26 项 pytest）

---

## 1. 案例介绍

### 1.1 要解决的组织-工程问题（不是数值问题）

`/root/tp-agent` 的 harness 层（战役派发器、任务机 worker、六条常驻 daemon）长期是**无门改动区**：
协调官/影子官/人类都只能"报告"（"某 daemon 有隐患，建议修 YY"），谁想改就 `sed` 一版，
改坏了没有拦网，改好了也没有证据。信任基座 = "整层归协调者"，实践上等于"无人负责"。

本单把这块收缩到**三道门本身**：

> 门之外的一切（补丁内容、诊断单内容、部署动作）都只是被检对象；
> 唯一被信任的是**门 + 门产出的机器证据**。

### 1.2 门控对象：8 个 harness 单元（现网快照，2026-11-07）

| 单元 | 角色 | 字节 | 金丝雀模式 | 该模式下能证明什么 |
|---|---|---|---|---|
| campaign_dispatcher.py | dispatcher | 45 975 | symbol_probe | 战役派发符号面完整（顶层即绑 DB/令牌，执行式试跑需生产写权） |
| tp_worker.py | worker | 54 037 | symbol_probe | 任务机符号面完整（执行需 git 检出 + 平台 API） |
| chronicle_clock_once.py | daemon | 5 472 | exec_dry_run | 真跑一拍：psql shim 下打印 SQLERR 并 exit(1) |
| saturation_regulator_loop_once.py | daemon | 1 514 | exec_dry_run | 真跑一跳：DB 不可达时静默跳过 exit(0)（不挂死） |
| coordinator_shadow.py | daemon | 5 912 | symbol_probe | 影子官符号面完整（含 LLM 调用 → 不可执行式试跑） |
| report_watch.py | daemon | 7 426 | symbol_probe | 守望线符号面完整（会创建补报单） |
| wave_cycle.py | daemon | 18 687 | symbol_probe | 波次循环符号面完整（写 state + 派单） |
| credit_settler_loop.py | daemon | 2 900 | symbol_probe | 结算环符号面完整（写记账表） |

合计 141 923 字节源码，**1 dispatcher / 1 worker / 6 daemons**。

### 1.3 入场即发现的两类真实缺陷（由门链过程暴露，非臆测）

1. **凭据内联**：3 个 daemon（`saturation_regulator` / `report_watch` / `wave_cycle`）把生产库口令写成源码字面量。
   原样镜像入库并推 GitHub = 活口令公开。实测：首版脱敏只命中 1 个文件，
   `wave_cycle.py` 的 `os.environ.get("TP_PG_PASSWORD", "<真口令>")` 兜底值**漏脱敏**（镜像里带明文），
   补齐 `env_default_secret` 规则后才 3/3 命中（见 §5.1 事故 A）。
2. **psql 无界等待**：`chronicle_clock` / `saturation_regulator` 的 `subprocess.run(["psql", ...])` 无超时；
   锁等待时整条 daemon 通道挂死（时钟挂死 → 影子官/结算环全部断料）。§4.3 给出量化实验。

---

## 2. 方法：门控仓 + 门链 + 部署管线 + 可执行诊断单

### 2.1 结构与唯一合法改法

```
补丁(json: file/old/new) ─┐
                          ▼
   preflight   镜像==git HEAD？生产 harness==DEPLOY_STATE 基线？（否则 BLOCKED_*）
   checkpoint  git HEAD + 逐文件 sha256 + 字节备份（回滚门武装）+ 回滚机器自检 drill
   apply_patch 精确替换（old 找不到 = 锚点失效 → 直接失败回滚）；产出 unified diff
   gate1       py_compile + AST 重复顶层定义扫描 + 明文凭据扫描
   gate2       canary_single_process：沙箱内单进程真跑（psql/网络被 shim 拦死，零生产副作用）
   gate3       git_rollback：任何门 FAIL → git checkout + 字节回写 → 逐文件 sha256 比对
   commit（可选）过门后提交镜像，否则下一次预检判 MIRROR_TAMPER
   deploy      默认 dry-run；镜像含脱敏占位符的单元判 blocked_redaction（不可直投生产）
```

### 2.2 三道门各自的判据（机器可裁，不接受自述）

| 门 | 判据（全部必须满足） | 反例形态 |
|---|---|---|
| **py_compile** | `py_compile.compile(doraise=True)` 通过；AST 无重复顶层 def/class；文本无明文凭据 | 括号未闭合；正则并集合并造成的重复 `def`；口令回注 |
| **canary 单进程试跑** | `rc ∈ expect_exit`（声明式白名单）；stderr 无 `Traceback`；`timeout_s+5` 内退出；`exec_dry_run` 单元必须至少打到一次 shim（证明真代码路径）；沙箱自检 PATH 解析必须落在 shim 内（fail-closed） | 运行期崩溃；挂死；空壳（没打到 shim）；shim 被绕过 |
| **git 回滚** | 冲突门 FAIL 后：每个文件 sha256 == checkpoint；`git diff HEAD` 对这些路径为空；回滚机器自身先过 drill | 回滚不完整 / 只恢复了部分文件 |

**沙箱构成**（`gates/canary_gate.py`）：临时目录 + PATH 前置 shim（psql / runuser / curl / ps / pg_isready，记录调用后按 DB 不可达退出）
+ `sitecustomize` 屏蔽 `socket.connect / connect_ex / create_connection`
+ `PGHOST/PGPORT/PGUSER/PGDATABASE` 毒化（纵深防御）+ `HOME/TMPDIR` 指向沙箱。

**诚实降级**：执行式试跑只在"沙箱能把副作用拦死"的单元上做。若源码把 `PATH` 写死或直调绝对路径工具，
PATH-shim 隔离就不成立（§5.1 事故 B 的形态），该单元**降级为符号探针并落 `degraded=true` + 理由**，
绝不硬跑。本机无 mount namespace（`unshare: Operation not permitted`），已在 `canary` evidence 里记录隔离等级。

### 2.3 部署管线与诊断单执行

- `deploy_plan()` 把镜像映射成对生产 harness 的写入计划，动作 ∈ {`noop`,`update`,`create`,`blocked_redaction`}；
  **默认 dry-run**，`apply_deploy(dry_run=True)` 只出计划不落盘。
- `diagnostics.DiagnosticExecutor` 把协调官诊断单（JSON）映射到门链既有动作：
  `mirror_verify / mirror_sync / gate_compile / gate_canary / patch_apply / rollback_to_head / deploy_dry_run`。
  未知 op 判 `NOT_EXECUTABLE` 并写原因 —— **能被拒绝的才是可执行的**。
  负例项用 `expect.verdict` 声明期望拦截结果：拦不住 = FAIL（红队语义）。

---

## 3. 计算结果（全部由 `artifacts/harness_gateway/MISSION_EVIDENCE.json` 程序化生成，零手抄）

### 3.1 门链执行台账（9 次运行）

| run_id | 内容 | verdict | 拦门者 | 回滚字节复原 | 门链耗时(ms) |
|---|---|---|---|---|---|
| 0001_real_patch | 真实修补①（饱和调节器加固） | **PASS** | — | — | 429 |
| 0002_gate_only_after_patch | 补丁后门体自检（AHEAD 态） | **PASS** | — | — | 905 |
| 0005_final_state | 终态自检（两补丁 AHEAD，待部署） | **PASS** | — | — | 928 |
| 0004_redteam_NC1 | 红队负例①：语法错注入 | **FAIL_ROLLED_BACK** | py_compile | 8/8 | 487 |
| 0004_redteam_NC2 | 红队负例②：运行期崩溃注入 | **FAIL_ROLLED_BACK** | canary_single_process | 8/8 | 619 |
| 0004_redteam_NC3 | 红队负例③：明文凭据回注 | **FAIL_ROLLED_BACK** | py_compile（凭据扫描） | 8/8 | 531 |
| 0003_order_patch_0002 | 真实修补②（战史官钟 psql 超时，**经诊断单执行**） | **PASS** | — | — | 491 |
| 0003_order_NC1_syntax | 诊断单内负例① | **FAIL_ROLLED_BACK** | py_compile | 8/8 | 575 |
| 0003_order_NC2_runtime | 诊断单内负例② | **FAIL_ROLLED_BACK** | canary_single_process | 8/8 | 603 |

### 3.2 真实修补①（0001）：饱和调节器四处硬伤，一次过门

| # | 缺陷 | 修法 | 门如何证明 |
|---|---|---|---|
| 1 | `PGPASSWORD = "<明文>"` 内联 | 改 `os.environ.get("TP_PG_PASSWORD","")` | 编译门的凭据扫描 0 命中；镜像无明文 |
| 2 | `env={"PATH": "/usr/bin:/bin", ...}` 写死 | 继承 `os.environ["PATH"]` | 金丝雀从**degraded(符号探针)** 升级为 `exec_dry_run` 真跑，`shim_calls=3` |
| 3 | psql 无超时 | `timeout=PSQL_TIMEOUT_S`（env 可调，默认 15s）+ `TimeoutExpired` 优雅返回 | §4.3 量化实验：墙钟 9.21s → 3.29s（2.8×） |
| 4 | psql 输出直接 `int()` | 统一走 `_int()` 容错 | 补丁后 canary `rc=0` 且走"db 不可达"降级路径 |

**门链证据**（补丁 0001，`artifacts/harness_gateway/0001_real_patch/report.json`）：

| 阶段 | 状态 | 耗时(ms) | 详细 |
|---|---|---|---|
| preflight_mirror_verify | PASS | 114 | 8 unit 与生产同源 |
| git_rollback_drill | PASS | 1 | 回滚机器自身 drill（改坏→恢复→哈希相等） |
| checkpoint | PASS | 0 | 8 文件 @ git `7307d4cc` |
| apply_patch | PASS | 1 | 2 处替换，diff 56 行 |
| py_compile | PASS | 239 | 8 文件编译 + 无重复定义 + 无明文凭据 |
| canary_single_process | PASS | 74 | 1 unit 真跑（`saturation_regulator`，shim 3 次调用） |

### 3.3 真实修补②（0002）：战史官钟 psql 超时，**整个过程由诊断单驱动**

诊断单 `IN27-H2-D1`（7 项）执行结果：**EXECUTED_PASS**，7/7 PASS，0 NOT_EXECUTABLE，0 SKIPPED。

| 项 | op | 结果 | 证据 |
|---|---|---|---|
| D1 | mirror_verify | PASS | 0 drift（生产 harness 未被绕过门改动） |
| D2 | gate_compile | PASS | 8 文件编译 / 无重复定义 / 无明文凭据 |
| D3 | gate_canary | PASS | 8 unit（2 个真跑 + 6 个符号探针，降级逐条记录） |
| D4 | patch_apply（补丁 0002，`commit=true`） | PASS | verdict PASS → git `393984cc` |
| D5 | patch_apply（NC1 语法错） | PASS（期望 FAIL_ROLLED_BACK） | 拦于 py_compile，回滚 8/8 |
| D6 | patch_apply（NC2 运行期崩） | PASS（期望 FAIL_ROLLED_BACK） | 拦于 canary，回滚 8/8 |
| D7 | deploy_dry_run | PASS | noop=4, update=2, blocked_redaction=2 |

边界演示诊断单 `IN27-H2-D2`：3 个"报告型"条目（重启 daemon / 就地改生产文件 / 通知人类）→
`NOT_EXECUTABLE` + 原因，1 个可执行条目 PASS → 总体 verdict `PARTIAL`。这就是"可执行 vs 只报告"的分界线。

### 3.4 部署就绪度（终态 `0005_final_state`）

| 动作 | 单元数 | 单元 | 原因 |
|---|---|---|---|
| noop | 4 | campaign_dispatcher, tp_worker, coordinator_shadow, credit_settler | 镜像与生产一致 |
| update | 2 | chronicle_clock, saturation_regulator | 补丁已过门，凭据已彻底移除，可安全部署 |
| blocked_redaction | 2 | report_watch, wave_cycle | 镜像含 `<REDACTED:…>` 占位符 → 直投等于把口令换成占位符，必须先做"凭据外置"补丁 |

---

## 4. 图与量化实验

> 图文件：`artifacts/harness_gateway/figures/`（由 `harness_gateway.experiments.make_figures` 生成，数据源同一份 evidence JSON）。

**图 1（`fig1_gate_chain.png`）** 9 次运行的门链时序：横轴为门链内累计墙钟，颜色=门状态（PASS/FAIL/SKIP），
行尾标注 verdict 与 `caught_by`。读法：3 条红队运行在第 5~6 段（编译门/金丝雀门）转红后立刻进入回滚段，
所有 PASS 运行都完整走完 6 段。

**图 2（`fig2_redteam_matrix.png`）** 红队矩阵：3 个注入故障 × 3 道门，红块=该门拦住了；
右侧标注 `FAIL_ROLLED_BACK` 与 `rollback 8/8 files byte-identical`。三种注入分别落在不同门，
说明三道门不是冗余摆设：编译门拦不住运行期崩溃，金丝雀门拦不住语法错（也拦不住"凭据回注"这种语法合法的坏法）。

**图 3（`fig3_timeout_and_canary.png`）** 左：psql 无界等待 vs 加单次上限的墙钟对比
（baseline 9.21s ≈ 3 次 × 3s；patched 3.29s ≈ 3 次 × 1s cap）；右：8 个单元的金丝雀覆盖
（2 个执行式真跑 / 6 个符号探针，降级不是失败而是被记录的边界）。

### 4.3 psql 无界等待实验（单变量：只差一个 `timeout=`）

| 变体 | psql 调用次数 | shim 每次等待 | 实测墙钟 | 模型 3×等待 | 偏差 |
|---|---|---|---|---|---|
| baseline（无 timeout） | 3 | 3.0 s | **9.206 s** | 9.000 s | +2.3% |
| patched（cap=1.0s） | 3 | 3.0 s | **3.292 s** | 3.000 s | +9.7% |
| 加速比 | — | — | **2.80×** | — | — |

patched 的 stdout 出现 3 次 `psql 超时 1.0s，跳过本跳`，随后走既有降级分支 `db 不可达，跳过本跳`（`rc=0`）——
证明"超时路径被真跑到"，而不是靠 mock 断言。默认 cap=15s 时，最坏墙钟从 **∞** 收缩到 **3×15s=45s**。

---

## 5. 注意点（踩坑、事故、局限）

### 5.1 事故 A：首版脱敏漏掉 `env` 兜底值

首版规则只匹配 `"PGPASSWORD": "<literal>"`，而 `wave_cycle.py` 写的是
`os.environ.get("TP_PG_PASSWORD", "<真口令>")` —— 形态不同，**明文被原样镜像进仓**（若直接推送即泄露）。
补齐 `env_default_secret`（`*PASSWORD|SECRET|TOKEN|KEY*", "…")`）并加 `(?!<REDACTED:)` 负向前瞻后才 3/3 命中。
教训：脱敏规则必须按"凭据出现的**语法形态**"扩展，而不是按"变量名"。

### 5.2 事故 B：沙箱相对路径 → shim 被绕过 → 金丝雀真的连上了生产库

首版沙箱用相对路径构造 `PATH`，子进程 `cwd` 切到沙箱后 PATH 指向不存在的目录，
`psql` 静默解析到 `/usr/bin/psql`；两个 daemon 用 unix-socket + peer auth **真的执行了 SQL**：
`chronicle_clock_once.py` 向 `chronicle_snapshots` INSERT 了一行快照，
`saturation_regulator_loop_once.py` 向 `saturation_pulse` INSERT 了一行脉动（`held=0`，未释放任何节点）。
三处修法：
1. 沙箱路径全部 `resolve()` 成绝对路径；
2. **fail-closed 自检** `verify_sandbox()`：用 `shutil.which` 验证每个工具解析落点必须在沙箱 `bin/` 内，
   不过则整道门直接 FAIL、一个单元都不跑；
3. 纵深防御：`PGHOST/PGPORT/PGUSER/PGDATABASE` 毒化 + `PGCONNECT_TIMEOUT=1`。
另外补了 `hermetic_risks()`：源码写死 `PATH`/绝对工具路径的单元降级为符号探针（本机无 mount namespace 时的唯一安全解）。
**如实披露**：这两行生产写是本次任务造成的、未被拦住的副作用（内容为良性遥测行，未删任何数据），
它们正是"金丝雀门为什么需要 fail-closed 自检"的现场证据。

### 5.3 局限（不粉饰）

1. **未写生产 harness**：全部动作只发生在 worktree 内的镜像 + 证据目录；`deploy_apply` 只做过 dry-run。
   理由：本次任务的纪律是"只改工作树内文件"，且无人类值守时替换 6 条在跑的 daemon 风险高于收益。
   管线本身已具备 `apply_deploy(dry_run=False)`，且对含占位符的单元硬性拒绝（`blocked_redaction`）。
2. **6/8 单元只有符号探针**：执行式试跑需要真 DB 写权或真 LLM 会话，本机无 mount namespace，
   PATH-shim 隔离对这些单元不可信。因此"能证明没坏"的强度是**分级**的，evidence 里逐单元写明 mode/degraded。
3. **金丝雀是单进程快照，不是长驻验证**：能证明"启动 + 一个执行周期不崩不挂"，不能证明 24h 稳定性。
4. **git 回滚门依赖工作树干净**：预检会把"补丁未提交"判为 `MIRROR_TAMPER`（这是有意的，迫使补丁落 commit）。
5. 镜像里含 2 个未修单元的脱敏占位符（`report_watch` / `wave_cycle`）——它们**不可部署**，
   需要后续"凭据外置"补丁（本单只做了饱和调节器与战史官钟两个单元）。

---

## 6. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 实测 | 误差 | <3%？ |
|---|---|---|---|---|
| 回滚后文件字节一致性（3 次红队 × 8 文件 = 24 次比对） | checkpoint sha256 | 24/24 相等，`git diff HEAD` 为空 | **0%** | **是（精确相等）** |
| 镜像脱敏后凭据残留 | 0 处明文 | 扫描 8 文件 0 命中（3 处已替换为占位符） | **0%** | **是** |
| 门判定确定性（同一补丁重复跑） | 同 verdict | `0001` 与 `0005` 均 PASS；NC 组 5 次运行 verdict 全一致 | **0%** | **是** |
| canary 真代码路径覆盖（exec_dry_run 单元数） | 声明 2 | 实测 2（`shim_calls`=1 和 3） | **0%** | **是** |
| psql 超时上限模型残差（patched 墙钟 vs 3×cap） | 3.000 s | 3.292 s | **+9.7%** | **否** |
| psql 无界等待模型残差（baseline 墙钟 vs 3×sleep） | 9.000 s | 9.206 s | **+2.3%** | **是** |
| 单次门链耗时（端到端，8 单元） | — | 429 ms（补丁）/905 ms（全量自检） | 无基准 | 见下 |

**逐项结论**：与"精确性"相关的量（字节复原、凭据残留、判定确定性、覆盖计数）全部**精确相等（0%）**；
唯一超过 3% 的是 psql 超时实验的墙钟模型残差 **+9.7%**，原因是模型忽略了每次子进程启动与 shim `sh` 解释器开销
（~0.29s/进程，3 次 ≈ 0.9s，实测额外 0.29s；量级与解释一致）。因此该项判为"模型残差 >3%，机制成立但模型未含启动开销"，
不作为门判据（门的判据是 `rc ∈ expect_exit` 与 `超时消息出现`，均为布尔确定性量）。
端到端门链耗时无外部基准，仅作观测值报出（不作为验收判据）。

---

## 7. 验收对照

| 战役判据 | 达成 | 证据 |
|---|---|---|
| harness 镜像目录入库 TensorPlatform | ✅ | `harness_gateway/mirror/`（8 单元 + `MIRROR_MANIFEST.json` + `DEPLOY_STATE.json`） |
| 部署管线 | ✅ | `harness_gateway/pipeline.py`（preflight/checkpoint/apply/gates/commit/deploy，默认 dry-run） |
| 三道门 = py_compile + 金丝雀单进程 + git 回滚 | ✅ | `harness_gateway/gates/{compile,canary,rollback}_gate.py` |
| 一次真实 harness 修补全程走门 | ✅ ×2 | 补丁 0001（run `0001_real_patch`）、补丁 0002（诊断单 `IN27-H2-D1` 的 D4，commit `393984cc`） |
| 故意错误注入被回滚门拦截（红队负例） | ✅ ×3 | NC1 语法错（编译门）/ NC2 运行期崩（金丝雀门）/ NC3 凭据回注（凭据扫描），均 `FAIL_ROLLED_BACK`、8/8 字节复原 |
| 诊断单可被执行而非只报告 | ✅ | `IN27-H2-D1` = `EXECUTED_PASS`（7/7）；`IN27-H2-D2` = `PARTIAL`（3 项 `NOT_EXECUTABLE` + 原因） |
| pytest 用例 | ✅ | `tests/harness_gateway/test_harness_gateway.py`：**26 passed** |

复现命令：

```bash
cd <worktree>
python3 -m harness_gateway mirror --src /root/tp-agent --mirror harness_gateway/mirror
python3 -m harness_gateway gate compile
python3 -m harness_gateway gate canary
python3 -m harness_gateway run-patch --patch harness_gateway/patches/0001-saturation-regulator-hardening.json --print-verdict
python3 -m harness_gateway run-patch --patch harness_gateway/patches/NC2-runtime-crash-injection.json --negative-control NC2
python3 -m harness_gateway diagnose --order harness_gateway/diagnostics/orders/IN27-H2-D1.json
python3 -m harness_gateway.experiments.timeout_bound
python3 -m harness_gateway.experiments.summarize && python3 -m harness_gateway.experiments.make_figures
python3 -m pytest tests/harness_gateway -q
```

## 8. 参考

- `docs/TRUTH_GATES_README.md`（平台既有 truth-gates：判定/计量，本单复用其"机器裁决 + 证据落盘"的纪律）
- `/root/tp-agent/AGENT_BOOTSTRAP.md` KNOWN_TRAPS（"热文件禁正则并集合并"→ 本单编译门的重复定义扫描；"报告 >250KB 静默失败"→ 本单图件压到 ~33KB）
- `git log`：`7307d4cc`（骨架+镜像）→ `81139b90`（补丁 0001）→ `393984cc`（补丁 0002，由诊断单提交）