# 交付前可复现性执行门（repro_exec_gate）

> 平台共性机制 · 零领域语义 · 独立工具包 `tools/repro_exec_gate/`
> 状态：**已实现并自证**（29 用例全绿 + 自检 PASS，见 §4/§5）

## 1. 它补的是哪个洞

`tools/evidence_grade.py` 的规则 R02 只检查契约里**有没有** `repro_cmd` 字段：

```python
repro = contract.get("repro_cmd")
if not repro:
    return fail("R02_REPRO_CMD", "numbers asserted but no repro_cmd")
```

字段存在 ≠ 命令能跑。交付审计里反复出现「声明了 repro_cmd，但一跑就崩 / 命令根本不存在 /
跑完不产出它声称的工件」，这类交付由此被判 L1–L2（近 24h 至少 4 例：m15882、m15884、
m15902、m15906）。全仓此前**没有任何工具执行过** `repro_cmd`（grep 无 subprocess/Popen 调用点）。

本包是那一半的补集，语义分工固定、互不替代：

| 组件 | 判什么 | 类型 |
|---|---|---|
| `tools/evidence_grade.py`（R02） | 是否**声明**了 repro_cmd | 存在型 |
| `tools/maturity_gate_h6/` | 成熟度五级 + 交叉一致性 | 结构型 |
| **`tools/repro_exec_gate/`（本包）** | 声明**执行起来**是否成立 | **执行型** |

按任务约束：不改 `evidence_grade.py`、不改 `maturity_gate_h6/`，只新增独立包。

## 2. CLI 契约（冻结）

```bash
python3 -m tools.repro_exec_gate --contract CONTRACT.json --worktree DIR \
        [--timeout SECONDS] [--json-out PATH]
```

退出码：`0=PASS` / `1=FAIL` / `2=用法错误`（参数缺失、契约不存在、契约非合法 JSON、契约结构非法）。

stdout **单行** JSON（键序固定）：

```json
{"status":"PASS|FAIL","reason":"<枚举>","exit_code":0,
 "duration_s":0.006,"missing_artifacts":[],"log_tail":"<末 40 行>"}
```

契约字段（域数据一律由 contract.json 注入，工具内无领域词条）：

| 字段 | 必填 | 含义 |
|---|---|---|
| `repro_cmd` | 是 | shell 命令行，`cwd=worktree` 执行；缺失或空白 → `NO_REPRO_CMD` |
| `artifacts` | 否 | `[{"path": REL, "sha256": HEX}]`，命令跑完后校验 |
| `timeout_s` | 否 | 硬超时（CLI `--timeout` 优先，再取契约值，最后取默认 120s） |

## 3. verdict 枚举与判定优先级

| reason | 触发条件 | exit_code 字段 |
|---|---|---|
| `OK` | 命令退出 0 且全部工件校验通过 | 0 |
| `NO_REPRO_CMD` | 契约无 `repro_cmd`（或仅空白） | `null` |
| `SPAWN_ERROR` | 进程起不来（cwd 不存在、shell 不可执行等） | `null` |
| `TIMEOUT` | 触到硬超时，**进程组整体 SIGKILL** | `null` |
| `EXIT_NONZERO` | 命令退出非 0 | 实际退出码 |
| `ARTIFACT_MISSING` | 声明工件未产出 | 命令退出码（0） |
| `ARTIFACT_SHA_MISMATCH` | 工件字节与声明摘要不符 | 命令退出码（0） |

优先级即上表顺序（自上而下首个失败决定，`OK` 在最前）。`EXIT_NONZERO` 先于工件判定是刻意的：
**跑崩的运行不能被改写成「工件问题」**——那会掩盖真实病因。
`missing_artifacts` 列出「缺失或摘要不符」的声明路径（两种工件失败都非空，便于调用方定位）。

`status="FAIL"` 时 `reason="USAGE_ERROR"` 只在退出码 2 出现（门压根没跑起来），它不是 verdict。

## 4. 关键设计（为什么不是「叫一下 subprocess」）

1. **杀进程组**：`Popen(..., start_new_session=True)` → 子进程为组长；超时后
   `os.killpg(pgid, SIGKILL)` 连打两轮（第二轮捕第一轮期间新 fork 的孩子），再 `wait()` 回收组长，
   最后扫 `/proc` 判「是否仍有**运行中**成员」，僵尸不算幸存者。幸存 pid 会写进 `log_tail`，
   门绝不会留孤儿进程在后台。
2. **输出走无名字临时文件，不走管道**：孙子进程继承管道 fd 会让 `communicate()` 永久挂住；
   写文件后按字节截尾，管道死锁因此在结构上不可能发生。
3. **超时只杀组，不改判定语义**：`TIMEOUT` 的 `duration_s` 实测 ≈ 超时阈值（夹具 1.0s → 1.05s 量级）。
4. **一条判定路径**：`core.judge()` 是唯一实现，`evaluate()`/CLI/`selftest.py` 共用；
   `test_core_and_cli_agree_on_every_fixture` 守住不漂移。
5. **工件摘要校验**：`sha256` 大小写归一、相对路径以 worktree 为根解析。
6. **`--json-out` 自封存**：写 `payload_sha256`=去掉该字段后正文的规范 JSON 摘要，
   `report.verify_seal()` 可离线复验；篡改正文必破封。
7. **零领域语义**：包内源码无任何域词条，`test_zero_domain_vocabulary_in_package_source`
   用词表 grep（中文子串 + 拉丁词边界匹配，`device`/`practice` 不误伤 `ice`）守住平台铁律。

## 5. 自证证据（machine-computed，非手抄）

### 5.1 自身 repro 命令真跑一次（判据 F）

```bash
$ python3 -m tools.repro_exec_gate.selftest --timeout 600
{"status":"PASS","reason":"OK","exit_code":0,"duration_s":4.624,"missing_artifacts":[],
 "log_tail":"........................... [100%]\n...\n27 passed, 1 warning in 3.88s"}
```

自检契约 `artifacts/repro_exec_gate/selftest_contract.json` 的 `repro_cmd` 就是本包验收套件
`python3 -m pytest -q tests/test_repro_exec_gate.py`，且声明该文件的 sha256 为工件——
跑完摘要仍一致，说明「跑的那份」正是「声明的那份」。
封存工件 `artifacts/repro_exec_gate/selftest.json`（`payload_sha256` 可离线复验，`log_sha256` 覆盖全量日志）。

### 5.2 夹具矩阵（程序化重跑，9/9 判级正确）

```bash
$ python3 -m tools.repro_exec_gate.matrix
{"all_match":true,"cases":9,"matched":9}
```

| 夹具类 | 期望 reason | 实测 reason | 命中 |
|---|---|---|---|
| pass_plain_command | OK | OK | ✅ |
| pass_artifact_digest | OK | OK | ✅ |
| pass_no_artifacts | OK | OK | ✅ |
| no_repro_cmd | NO_REPRO_CMD | NO_REPRO_CMD | ✅ |
| spawn_error_bad_cwd | SPAWN_ERROR | SPAWN_ERROR | ✅ |
| exit_nonzero | EXIT_NONZERO | EXIT_NONZERO | ✅ |
| artifact_missing | ARTIFACT_MISSING | ARTIFACT_MISSING | ✅ |
| artifact_sha_mismatch | ARTIFACT_SHA_MISMATCH | ARTIFACT_SHA_MISMATCH | ✅ |
| timeout | TIMEOUT | TIMEOUT | ✅ |

数据 → `artifacts/repro_exec_gate/fixture_matrix.json`；图 → `fixture_matrix.png`。

### 5.3 用例清单（29 passed）

| 组 | 覆盖 |
|---|---|
| PASS 三类 | 普通命令 / 生成并校验 sha256 / 无工件声明 / 无摘要仅存在性 |
| 坏路径 | `SPAWN_ERROR`（cwd 不存在） |
| 非零退出 | 退出码透传 + stderr 进尾部 + 优先于缺工件 |
| 工件 | 缺失 / 摘要不符（含 `missing_artifacts` 与尾部证据） |
| 超时 | `TIMEOUT` + **运行中标记采样非空**（证明不空转）+ 孙进程被杀 + 事后零残留 |
| 契约 | 无/空白 `repro_cmd` → FAIL 而非崩溃 |
| CLI 退出码 | `0` / `1` / `2`（缺参、契约不存在、JSON 非法、结构非法、摘要格式非法） |
| 其它 | `--timeout` 覆盖契约、`log_tail` 恰 40 行、`--json-out` 封存与篡改检测、矩阵一致、自检契约、导入无副作用 |

## 6. 注意点 / 局限

- **只判「跑通 + 工件一致」，不判科学正确性**：命令跑通不代表数字对。适用边界=交付卫生，
  精度对标的判据仍在各域仓的验证面。
- **shell 语义**：命令经 `/bin/sh -c` 执行，判定依赖宿主 shell 与 PATH（与交付现场一致才算复现）。
- **逃逸进程组**：自建 session 的孙进程（`setsid`）不在组内，本包不追（会出现在 `leftover_pids`
  之外）；夹具覆盖的是常规 fork/background 情形。
- **网络不隔离**：命令若联网，成功率取决于外部服务。若要彻底可复现，需 offload 到带网络的沙箱
  （后续可加 `--offline` 之类开关，本单未做）。
- **磁盘/时长开销**：单次门开销 = 命令本身；超时上限默认 120s，长套件请显式给 `--timeout`。
- 平台铁律：本包**不含**任何域词条，域数据仅经 contract.json 注入；不要往这里加域判据。

## 7. 复核命令（任何人可重跑）

```bash
python3 -m pytest -q tests/test_repro_exec_gate.py            # 29 passed
python3 -m tools.repro_exec_gate.matrix                        # all_match: true, 9/9
python3 -m tools.repro_exec_gate.selftest --timeout 600        # PASS / OK
python3 -c "import json;from tools.repro_exec_gate import report;\
print(report.verify_seal(json.load(open('artifacts/repro_exec_gate/selftest.json'))))"
```