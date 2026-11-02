# 验证证据登记册：指针腐烂审计（m2055）

**EDIT_WHITELIST**：`tools/`, `tests/`, `docs/`, `artifacts/`

## ① 案例介绍

平台头号 KPI「已合并功能复核通过率」长期是人工口算（看板写作 `4/4（在飞 0）`），
没有任何工具把「哪条证据算数」变成机器判据。已有工具 `tools/validation_debt_ledger.py`（m1705）
抽的是报告正文的「局限/待办」=**负空间**；本单补**正空间**：工作树内验证证据文档凭什么被判「已验证」，
以及它引用的指针还落不落得地。

语料 = `docs/**/*.md` 共 275 篇 md，其中含验证/判据线索的**证据文档 264 篇**（叙事文档 11 篇不计入分母）；抽出可解析指针 **1222** 条。

## ② 怎么做的

| 环节 | 规则（显式常量，测试钉死） |
|---|---|
| 指针抽取 | 反引号内联代码中，首段目录命中白名单 + 扩展名命中 → 指针；`file.py::Name` 追加 nodeid |
| 指针解析 | EXACT → MOVED（同基名唯一）→ AMBIGUOUS（多拷贝）→ OFFBRANCH（`git log --all` 有）→ LOST |
| nodeid 校验 | 文件内 `def/class` 或模块级常量赋值；通配名按前缀族；散文串/多指针标 NOT_A_NODEID 不计缺陷 |
| 假阳性抑制 | 花括号/区间缩写 → SHORTHAND；提案/计划上下文 → PROPOSED；两者均不计入分母与腐烂 |
| 判级 | ROTTED > OFFBRANCH_ONLY > UNANCHORED > SELF_CONSISTENT_ONLY > REAL_ANCHORED |
| 确定性 | 输出按路径排序、JSON 无墙钟时间，可逐位重算比对 |

工具：`tools/verification_evidence_registry.py`（纯标准库，只读；git 仅用 `log --all --name-only`）；
判据测试：`tests/test_verification_evidence_registry.py`（40 项）；
门禁：`--check --max-rot N` 以退出码表达腐烂预算。

## ③ 计算结果

**机器口径复核通过率 = 59/264 = 0.2235**（REAL_ANCHORED / 证据文档）。

### 判级分布

| 等级 | 文档数 | 占比 | 判据 |
|---|---:|---:|---|
| REAL_ANCHORED | 59 | 22.35% | 外部基准词 + 数值容差 + 现存测试指针 + 无 LOST/OFFBRANCH |
| SELF_CONSISTENT_ONLY | 2 | 0.76% | 有容差与测试，但无外部基准词（只有自洽/互校） |
| UNANCHORED | 76 | 28.79% | 无容差声明、或无现存测试指针 |
| OFFBRANCH_ONLY | 92 | 34.85% | 指针只在别的分支历史里，本树不可复核 |
| ROTTED | 35 | 13.26% | 指针全历史不存在 / nodeid 名缺失 |

### 指针健康

| 状态 | 条数 | 口径 |
|---|---:|---|
| EXACT | 858 | 相对路径在树内直接命中（含 glob 命中） |
| MOVED | 46 | 原路径不在、同基名在树内唯一命中 |
| AMBIGUOUS | 15 | 同基名多拷贝，无法判定 |
| OFFBRANCH | 218 | 历史里有、本树没有 |
| LOST | 85 | 全历史都没有 |
| nodeid 名缺失 | 1 | 文件在、函数/类/常量不在 |
| 花括号/区间缩写 | 15 | 非单一指针，不计分母 |
| 提案上下文 | 68 | 计划/提案里的路径，不计分母 |

- 腐烂率（文档口径）= 0.1326；腐烂率（指针口径）= 0.0696

### LOST 指针按顶层目录

| 顶层目录 | 条数 |
|---|---:|
| `artifacts/` | 33 |
| `backend/` | 19 |
| `docs/` | 16 |
| `api/` | 9 |
| `tools/` | 3 |
| `agent_work/` | 2 |
| `schemas/` | 2 |
| `tests/` | 1 |

### OFFBRANCH 指针按顶层目录

| 顶层目录 | 条数 |
|---|---:|
| `artifacts/` | 169 |
| `docs/` | 32 |
| `runs/` | 7 |
| `tests/` | 3 |
| `tools/` | 3 |
| `backend/` | 2 |
| `scripts/` | 2 |

### LOST 清单（前 15 条，全量见 `registry.json`）

| 文档 | 指针 |
|---|---|
| `docs/QUALITY_BAR.md` | `agent_work/quality/report_debt.json` |
| `docs/audit/m1649_seakeeping_verification_debt.md` | `backend/data/seakeeping_benchmark_reference.json` |
| `docs/audit/propulsion_openwater_audit.md` | `backend/methods/propulsion/wageningen.py` |
| `docs/audit/propulsion_openwater_audit.md` | `backend/data/test_data/propulsion_wageningen.json` |
| `docs/audits/m1763_maneuvering_verification_comprehensive.md` | `artifacts/m1456_maneuver/fig_maneuver_audit.png` |
| `docs/audits/m1763_maneuvering_verification_comprehensive.md` | `artifacts/m1566/fig_mmg_verification.png` |
| `docs/audits/maneuvering_verification_m1663.md` | `artifacts/m1663/probe/*.json` |
| `docs/audits/structure_vibration_verification_m1600.md` | `api/structure.py` |
| `docs/council/session-002/r1-proposal-solver.md` | `tools/sdaa_baseline_extract.py` |
| `docs/council/session-002/r1-proposal-solver.md` | `tests/test_sdaa_baseline_extract.py` |
| `docs/council/session-002/r1-proposal-solver.md` | `artifacts/sdaa_baseline_extract.json` |
| `docs/council/session-002/r1-proposal-solver.md` | `docs/sdaa_baseline/CASE_VERDICTS.md` |
| `docs/council/session-002/r1-proposal-solver.md` | `artifacts/sdaa_baseline_v1.json` |
| `docs/council/session-002/r1-proposal-solver.md` | `artifacts/sdaa_perf_baseline.json` |
| `docs/council/session-002/r1-proposal-solver.md` | `docs/sdaa_baseline/PERF.md` |

### nodeid 名缺失

| 文档 | 指针 | 解析到 |
|---|---|---|
| `docs/verification/GEOMETRY_HYDROSTATICS_AUDIT.md` | `api/hydrostatics.py::_section_props` | `src/ship_perf/hydrostatics.py` |

## ④ 结果图

- `artifacts/verification_evidence/fig_grade_distribution.png`：证据文档判级分布
- `artifacts/verification_evidence/fig_pointer_status.png`：指针解析状态分布

## ⑤ 注意点

- **OFFBRANCH ≠ 臆造**：指针在别的任务分支提交过、但没进本树——这是合并/清理欠账，不是伪造证据；工具把它与 LOST 分开计。
- **判级是「本树可复核性」，不是物理正确性**：REAL_ANCHORED 只说明有外部基准词、有容差数字、有能跑到的测试；基准本身是否选对仍归审计单。
- **通配与散文**：花括号缩写、提案上下文、散文里拼起来的多指针都会被排除，否则腐烂率会被假阳性抬高三倍以上（本轮从 335 条 LOST 收敛到 85 条）。
- **语料偏置**：只扫 `docs/`；`backend/tests/` 内的 docstring 证据不在内。

## ⑥ 误差判定

本单无物理量求解，**不存在与实验/解析基准的数值误差**；可判定的是「审计判定本身」的一致性：

| 判据 | 基准 | 结果 |
|---|---|---|
| 判据测试 | `tests/test_verification_evidence_registry.py` 全部用例 | 40/40 PASS |
| 确定性 | 同输入两次构建逐位相同（`test_registry_is_deterministic`） | PASS |
| 假阳性抑制 | 三类已知假阳性（缩写/提案/散文 nodeid）各有独立用例 | PASS |
| 腐烂预算门禁 | `--check --max-rot 35` → exit 0 | PASS |

结论：工具与判据层面误差为 0（逐位可复现）；审计结论相对「任意人工口径」的偏差无法量化，已通过显式常量与用例把口径固定下来。
