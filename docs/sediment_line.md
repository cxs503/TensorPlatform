# 被动进水口 sediment_line —— 常设守护（锚点 FEAT-TK-GOV-INFRA）

> owner 2026-11-10 定位令：「每次 agent 干完活形成知识积累进来」的平台侧执行器。
> 交付物：`tools/sediment_line.py`（判定 + 派单 + 核验）+ `ops/sediment_line_loop.sh`（守护壳）
> + 本文件（部署/守护清单/日志规范/回放验收）。
> 代码本体零领域语义（平台铁律），域内容只经 mission 的 `result` 注入到提取子单。

## 1 它是什么（一句话）

**进水管，不是生产车间**：轮询平台 API，找出「干完活留下了可沉淀面」的 succeeded 任务，
按模板派一张**提取子单**（`task_key=tk-sediment-m<源单号>`），由子单的 agent 去源仓把
claim / evidence / provenance 三契约写进 TensorKnowledge `knowledge/sediment/m<源单号>/`，
**draft 态、生产者不得自审**。它自己不写一条知识、不裁一次定谳。

```
succeeded 单 ──▶ ①自噬闸 ─▶ ②噪点闸 ─▶ ③证据闸 ─▶ ④知识面闸 ─▶ ⑤配额闸 ─▶ 提取子单
                 tk-sediment-*  infra/文案   无工件/无锚点   验证/基准/校准/   单轮/当日/单仓
                 再沉淀即跳过   不收         不收           定谳/根因/否证     上限，超额顺延
```

## 2 判定逻辑（四道闸，每道都能失败）

| 闸 | 判据 | 落账原因码 |
|---|---|---|
| ① 自噬 | 源单 goal 含 `[SEDIMENT-LINE]` 标记（即它本身是提取单） | `self_loop` |
| ② 噪点 | infra 杂务（lint/依赖/docker/健康检查/重试/迁移…）、纯文案（README/CHANGELOG/补报…）、元层报表的噪声分 ≥ 正向分 | `noise_dominant` |
| ③ 证据 | `result` 无工件：`changed_files` 里没有测试/验证/报告/基准类文件，且无 `report_id` | `no_traceable_evidence` |
| ④ 知识面 | `result` 侧（headline/checks/decision_note）未命中任何知识族 | `no_sediment_surface` |

**知识族**（命中即进候选，权重只用于排序与账本；强命中族单条即可派单）：

| 知识族 | 判据（result 侧） | 权重 | 强命中 |
|---|---|---|---|
| `falsification` 负知识/否证 | falsif / 否证 / 证伪 / 反例 / honest-FAIL / 推翻 / 假设被否定 | 6 | ✅ 必收 |
| `verdict` QR/仲裁定谳 | QR / 仲裁 / 定谳 / 裁定 / 裁决 | 5 | ✅ |
| `root_cause` 根因判例 | 根因 / root-cause / 判例 / post-mortem / RCA | 4 | ✅ |
| `calibration` 校准数据 | 校准/标定 **且** 误差量带数字 | 3 | — |
| `physical_validation` 物理验证 | 误差量带数字 **且** 有外部基准（基准/参考值/解析解/实测/判据） | 3 | — |
| `benchmark_tooling` 基准工装 | 基准/benchmark/baseline/工装 带数字 **且** 留下 benchmark 类工件或声明「基准工装」 | 3 | — |

**关键设计决定（有实测依据）**：只读 `result` 侧，不读 `goal`。
战役 goal 是模板长文，「验证/基准/判据」几乎每单都有 —— 早期版本读 goal 时 4000 单里
3754 单命中（93.9%），那不是沉淀是噪声。改读 result 侧 + 证据工件闸后，命中率降到
**26.2%**（7 天 5359 单 → 1404 单），未收分布见 §6。

## 3 提取子单 goal 模板（四契约 + 反造假）

模板逐字引用源单 `result` 的原文（headline/checks/命中片段/文件路径），并给出来源块：

```
source_mission_id / source_repo / source_branch / source_pr(+merged) /
source_worktree / source_head_sha / source_model / source_status / task_key
```

其中 **sha 三段式**（诚实优先，绝不编造）：

1. `result.evidence.head_commit` 有 40 位 sha → 直接写；
2. 否则在源工作树 `/root/tp-agent/work/m<mid>` 跑 `git rev-parse HEAD` 解析；
3. 仍拿不到 → 写 `PENDING` + 给出解析命令，要求子单自行解析，解析失败写 `null` 并在
   `claim.notes` 注明「sha 未解析」。

子单硬约束：≥3 条 claim，每条 claim/evidence/provenance 齐备（`epistemic_status=draft`），
`evidence.locator` 必须给 repo + 仓内相对路径 + **行号区间** + ≥20 字符逐字引句（能在该 sha
的文件里 grep 到）；数值 claim 必须带基准与误差并显式回答「是否 <3%」；负知识单必须写明
「哪条假设被否证 + 反证工件 + 复现命令」；**禁止写 review-event、禁止 promoted**
（生产者不自审，评审另派独立席位）。

## 4 部署（平台侧执行）

### 4.1 前置

- 平台 API 可达（默认 `http://127.0.0.1:8010/api/v1`），账号需 `ADMIN|MANAGER|ENGINEER`
  角色（`POST /tensor/missions` 的门）；`TENSOR_AGENT_USER/TENSOR_AGENT_PASS` 已在 env。
- 目标仓 `TensorKnowledge` 可解析（`/root/tensor-ecosystem/TensorKnowledge/.git` 存在，
  与 `_repo_resolvable` 同口径；仓名在白名单内）。

### 4.2 daemon_guard 三行指令

```sh
# 1) 常驻壳（单实例锁 + 300s 节拍 + 失败不退出），日志落 logs/daemon_sediment.log
mkdir -p logs runs/sediment_line && nohup ops/sediment_line_loop.sh >> logs/daemon_sediment.log 2>&1 &
# 2) 自检：一轮判定/派单是否正常（dry-run 不落单，只看判定面）
python3 tools/sediment_line.py scan --dry-run --log-file logs/daemon_sediment.log
# 3) 健康判读：末轮台账 + 当日配额 + 积压
python3 -c "import json;s=json.load(open('runs/sediment_line/state.json'));print(s['rounds'],s['last_run'],s['last_summary']['derived'],s['last_summary']['deferred_over_cap'])"
```

任务平面托管（推荐：与其余守护同源，`ops/taskplane/`）：

```yaml
# ops/taskplane/taskplane.manifest.yaml  → daemons: 追加
  - name: sediment-line
    script: ../sediment_line_loop.sh      # 或容器内 /app/ops/sediment_line_loop.sh
    cadence: 5min 轮询
    enabled: gated                        # 门：SEDIMENT_LINE_ON=1
    live: stopped
    note: 被动进水口（FEAT-TK-GOV-INFRA）：succeeded 单→四契约知识提取子单，幂等+配额
```

> **未完成的接线（honest gap）**：上述 manifest 条目**没有**落进本分支。原因：任务平面
> 守护清单是三方一致红门（`taskplane.manifest.yaml` + `supervise.py::DAEMONS/GATED` +
> `backend/tests/test_taskplane_infra.py::PINNED`），「多收一个=未审变更」，且部署归平台侧。
> 挂载时须三处同步 + `deploy/taskplane/taskplane.env.example` 补 `SEDIMENT_LINE_ON`、
> `deploy/taskplane/docker-compose.taskplane.yml` 挂 `runs/sediment_line` 卷，跑
> `pytest backend/tests/test_taskplane_infra.py` 转绿后再开 gate。

### 4.3 配额（默认值，全部可用 env 覆盖）

| 键 | 默认 | 含义 |
|---|---|---|
| `SEDIMENT_INTERVAL` | 300 | 轮询秒数 |
| `SEDIMENT_DAYS` | 7 | 扫描窗口（守护停机后自动补扫） |
| `SEDIMENT_MAX_RUN` | 20 | 单轮派生上限 |
| `SEDIMENT_DAILY_CAP` | 40 | 当日派生总上限 |
| `SEDIMENT_PER_REPO` | 6 | 单仓当日上限（防某仓灌满进水口） |

超额不丢：进 `deferred` 账，下一轮按（强命中族→总分→新单）价值序继续消化；
积压规模在每轮 summary 的 `deferred_over_cap` 里可见（**不藏账**）。

## 5 日志 / 账本规范

**日志**：`logs/daemon_sediment.log`（容器内 `/var/log/tp-fleet/daemon_sediment.log`）。
逐行 JSON，字段固定：`ts` `level` `event` `…`。事件类型：

| event | 含义 | 关键字段 |
|---|---|---|
| `start` | 守护壳启动 | `interval` `days` `max_per_run` `daily_cap` |
| `derive` | 一条派单动作（含失败） | `mission_id` `task_key` `status` `extraction_mission_id` `families` |
| `round` | 一轮收尾 | `scanned` `derived` `already` `deferred` `dry_run` |
| `round_nonzero` | 该轮退出码非零（API 抖/部分派单失败） | `rc` |
| `lock` | 已有实例在跑，本次退出 | `detail` |

`status` 取值：`derived`（新派）/ `dry_run`（只判定）/ `conflict`（同 task_key 不同内容）
/ `http_<code>`。**任何非 derived 都带 `error` 原文**，不许静默。

**账本**（`runs/sediment_line/`，append-only，git 不入库）：

| 文件 | 内容 |
|---|---|
| `decisions.jsonl` | 每条扫描单的判定：`hit`/`families`/`positive_score`/`noise_score`/`reason`/`anchors` → 未收的也在，可审计「为什么没收」 |
| `dispatched.jsonl` | 每次派单动作（含失败与 dry-run） |
| `state.json` | `derived` 幂等表（源单号 → 提取单号/task_key/sha）+ 轮次 + 末轮 summary |
| `round_<ts>.json` | 单轮完整快照（含 deferred 明细） |

## 6 回放验收（2026-11-10 真实跑通）

窗口：近 7 天（cutoff `2026-11-03T03:58Z`，`succeeded` 全库 6875+，窗口内 **5359** 单，
扫 27 页满窗停止）。命令：

```sh
python3 tools/sediment_line.py replay --days 7 --max-per-run 24 --daily-cap 30 --per-repo-daily 6 --sample 5
```

| 指标 | 数值 |
|---|---|
| 扫描 succeeded 单 | 5359 |
| 命中过滤器 | **1404**（26.2%） |
| 实际派生提取单 | **32**（第 1 轮 `replay` 24 + 第 2 轮 6 + 第 4 轮 `scan` 2；第 3 轮 0 = 全部 `already_derived`，第 5 轮 dry-run 0） |
| 未收：`no_sediment_surface` | 1743 |
| 未收：`noise_dominant`（infra/文案） | 1359 |
| 未收：`no_traceable_evidence` | 852 |
| 超额顺延（可见积压） | 1374 |
| 抽样核验（5 张，六项机器检查） | **5/5 PASS** |

已派生提取单 32 张：`#8818–#8847` 与 `#8849–#8850`（`repo=TensorKnowledge`，`origin=agent`），
覆盖 9 个源仓（TensorLBM 6 / TensorBEM 5 / TensorDG 4 / ship-performance-platform 4 /
TensorFEM 3 / TensorSPH 3 / TensorPlatform 2 / TensorPhysics 2 / TensorDEM 1）。
第 4 轮走的是**守护本体 `scan` 子命令**（非 replay），证明常设路径端到端可用。

**幂等三重证据**：

1. 服务端：同一 `task_key` 同内容重放 → `200 {id:8826, idempotent:true}`；
   内容不同 → `409 task_key already identifies a different task`（实测两条）。
2. 本地账本：第 3 轮回放 `derived=0 / already_derived=30`，无重复单；第 5 轮 dry-run
   `decisions_logged=0`（高水位账：3 轮 59MB → 已加高水位 + 32MB 轮转，判定账不再每轮重写窗口）。
3. 自噬闸：提取单 goal 含 `[SEDIMENT-LINE]` 标记，再次扫描不会二次沉淀。

**抽样 5 张的质量（证据可回溯性六检）**：`source_id_cited`（源单号 + task_key 在 goal 内）、
`repo_cited`、`files_traceable`（goal 引的每个文件路径逐字出现在父单 result）、
`sha_present`（40 位 sha 或带 `rev-parse` 的 PENDING）、`four_contracts_present`（三契约
schema + draft + 禁止自审 + ≥3 条）、`quotes_verbatim`（goal 里每条 `> [family]` 摘录都能
在父单字段文本里逐字找回）。5/5 全过 → 无伪造引文、无臆造路径。样例 `tk-sediment-m8673`
（提取单 #8819）：源仓 TensorLBM、PR #1561（merged）、scheme 解析到
`source_head_sha: d60af0ae58fc76c3e073d3b27e6e849741d8632e`。

## 7 守护清单（运维自检，按序）

1. **进程在跑**：`ops/sediment_line_loop.sh` 存活 + `runs/sediment_line/loop.lock` 存在。
2. **心跳新鲜**：`state.json.last_run` 距今 ≤ 2×`SEDIMENT_INTERVAL`（默认 10 min）。
   陈旧 = API 不可达或进程哑死 → 看 `daemon_sediment.log` 尾部 `round`/`round_nonzero`。
3. **没在空转**：末轮 `derived + already_derived > 0` 或 `scanned>0`；连续多轮 `scanned=0`
   且 `deferred_over_cap` 大 → 说明配额过紧或 API 窗口空，需人工看。
4. **没有刷单**：当日 `derived` ≤ `SEDIMENT_DAILY_CAP`；`dispatched.jsonl` 里同 `task_key`
   不得出现两次 `derived`（出现即幂等破防，立即停守护 + 查 state.json）。
5. **没在自噬**：`dispatched.jsonl` 的 `source_repo` 不应大面积是 TensorKnowledge
   的 `tk-sediment-*` 单（自噬闸失效信号）。
6. **质量没漂**：每周一次 `verify --sample 5`，`all_pass` 必须为 true；出现
   `quotes_verbatim` 失败 = 模板或源数据被改坏（防伪造闸报警），先停再查。
7. **门禁在绿**：`pytest tools/test_sediment_line.py -q`（33 项，机制回归）。
8. **积压可见**：`deferred_over_cap` 记录在案；若长期单调增（进水 > 出水），提高
   配额或增设独立评审产能，**不要**放宽判定闸（那会把防噪拆掉）。

## 8 已知局限（honest）

- **命中率仍是 26%**：知识族判据是「过程词 + 定量误差 + 工件」的启发式，做不到语义级
  判断。宁可召回偏高（后面还有 30/日配额 + 人工复核 draft），也不放进 infra 噪声。
- **sha 依赖源工作树存活**：`/root/tp-agent/work/m<mid>` 被清理后只能走 PENDING 支路，
  子单须自行解析；解析不到就写 null（入账为「未解析」，不算造假但质量降级）。
- **行号不在父单里**：父单 result 只有文件路径与 checks 文本，`文件:行号` 由子单在
  指定 sha 上 grep 得到 —— 这正是模板把 sha 钉死的原因。
- **产出质量未端到端验证**：本次只验证了「派生单本身可回溯」。子单实际写出的
  `knowledge/sediment/` 知识条目质量，须等这 30 单回落后跑一次 KE 校验
  （`ke/validate.py` + 四 schema）才算闭环 —— 本轮**未**覆盖。
- **每日 40 上限对当前进水速度偏紧**：命中 ≈ 200/日 vs 40/日，积压会持续增长；
  这是有意选择（先证明质量再放量），放量须同时配评审产能。