# 锚点查重门（M-R17-01）：调度前只读预检

> 平台机制 · planner-w17 第 17 班派 · 实施 m18077 · 证据工件 `artifacts/r17/`
> 铁律：**零领域语义**（锚点一律来自 mission 数据）· **本轮只读告警**（不阻断任何派单路径）

## 1. 问题与基线（可复算）

planner-w17 第 17 班 2026-11-13T11:30Z 采集 `GET /api/v1/tensor/missions?limit=200`
（落盘 `artifacts/r17/missions_400.json` + `intel_summary.json`）：

| 指标 | 基线 | 含义 |
|---|---|---|
| 锚点单数 | 169 | 窗口内 goal 带 `【锚点=X】` 的单 |
| 锚点组数 | 118 | 归一化后不同锚点数 |
| 重复组数 | 48（40.7%） | 组内 ≥2 单（未剔派生） |
| 独立重复组数 | 28（23.7%） | 剔除返修/交差审计/重开/管到底派生后仍 ≥2 单 |
| 在飞重复锚点 | 20 | 上述独立重复组里此刻还有 `status='running'` 单的锚点数 |

白烧 28 个席位。平台此前**没有调度前查重门**：`tools/` 里 `anchor_dedup|同锚点|anchor_registry`
只有语义无关命中，派单侧不会问「这个锚点是不是已经有一单在飞/今天已经交付过」。

## 2. 判定规则（`tools/mission_governance/anchor_dedup_gate.py`）

`gate(anchor, in_flight, recent_terminal, now, window_hours=24) -> {decision, conflicts, reason}`

| 情形 | 判定 | 理由 |
|---|---|---|
| 同锚点**在飞**（`status` 不在终态集） | `reject` | 再派一份＝白烧席位；卡住的在飞单该被回收而不是被覆盖 |
| 同锚点 **24h 内 `succeeded`** | `reject` | 已交付，无需再派 |
| 同锚点 **24h 内 `failed`** | `allow` | 重试是合法动作，不得当成重复 |
| **派生单**（`parent_mission_id` 非空 / goal 带 `返修·交差审计·重开·管到底`） | `exempt` | 与被修单天然同锚点 |
| 无同锚点冲突 | `allow` | — |

实现细节与边界（逐条有测试）：

1. **在飞不看年龄**：3 天前创建仍 `running` 的单照样 `reject`（`test_in_flight_rejects_regardless_of_age`）。
2. **已交付看窗口**：`finished_at`（缺则 `created_at`）落在 `now - window_hours` 内才拦；
   出窗的交付记录进 `out_of_window`，不构成冲突（`test_allow_when_delivery_outside_window`）。
3. **交付过就不认 failed 抵账**：同锚点既有 `failed` 又有窗口内 `succeeded` → `reject`
   （`test_failed_then_succeeded_in_window_still_rejects`）。
4. **在飞的派生单不算冲突**：否则一条返修单会把同锚点新单永久卡住
   （`test_derived_in_flight_mission_is_not_a_conflict`）。
5. **锚点归一化**：NFKC（全半角）→ 各种 Unicode 破折号统一成 `-` → 去空白 → 小写。
   不做「去连字符」激进折叠：`A-B` ≠ `AB`，误合并比重复更贵
   （`test_normalization_does_not_merge_distinct_anchors`）。
6. **空锚点 → `reject`**（reason 明说「无法判重」）：宁可告警也不默认放行。
7. **纯函数**：`gate` 无副作用、不改入参（`test_gate_does_not_mutate_inputs`），
   时间由 `now` 注入所以可测；`window_hours` 夹在 `1..720`。

## 3. 预检端点（只读）

```
GET /api/v1/tensor/missions/anchor-precheck?anchor=<X>&window_hours=24
    [&parent_mission_id=<id>][&candidate_goal=<文本>]     # 可让派生判定生效
→ {decision, conflicts:[mission_id...], reason, readonly:true,
   normalized_anchor, window_hours, as_of, organization_id, counts{...}}
```

* `require_auth` 鉴权；`organization_id` 由 `resolved_org` 决定（租户隔离）。
* **只读**：仅两条 `SELECT`（在飞池 + 近期终态池），无 INSERT/UPDATE/DELETE，
  不改状态、不派单。测试用假 Session 记录每条 SQL 并断言「全为 SELECT 且不含写动词」。
* 候选池粗筛：`LOWER(goal) LIKE LOWER(:pat) OR LOWER(goal) LIKE LOWER(:skel)`
  —— 大小写不敏感 + `-`→`_` 破折号变体；`%` `_` `\` 全部转义。
* 路由位置：`anchor-precheck` 声明在 `/{mission_id}` **之前**，并有源码序测试锁死
  （2026-11-05 `pr-merge-queue` 被参数路由吞掉 422 的同型事故）。
* 派生单短路：`parent_mission_id` / `candidate_goal` 判定为派生时**一条 SQL 都不发**，
  不会把「父单在飞」误报成冲突。

**不阻断派单**：本轮不接 `POST /tensor/missions`、不接 `/dispatch`。要接到派单侧时，
调用 `anchor_precheck_query(...)` 拿 `decision` 写进派单事件/告警面即可；改成硬门
（reject 即拒单）属于策略变更，须 owner 批（见 §7）。

## 4. 复算器：`tools/mission_governance/anchor_dedup_audit.py`

```bash
# 从 API 快照精确复演基线（对拍用）
python3 tools/mission_governance/anchor_dedup_audit.py \
    --missions-json artifacts/r17/baseline_missions_snapshot.json \
    --json-out artifacts/r17/anchor_dedup_audit_replay.json
# 从库里现取（窗口滚动）
python3 tools/mission_governance/anchor_dedup_audit.py --window 200 --org 1 \
    --json-out artifacts/r17/anchor_dedup_audit.json
```

工件含：`sql.window`（取数 SQL 原文）、`sql_crosscheck`（SQL 侧 LIKE 粗筛 vs Python 正则的
独立交叉核对）、`as_of`、`filters.organization_id`、`baseline`、`diff`（逐项 delta + **偏差原因**）。
口径补充：基线「在飞」只算 `status='running'`（`intel_summary.json` 的 `running_dup` 长度=20），
本工具默认用更宽的非终态口径，并**并列**输出 `inflight_duplicate_groups_running_only`
以便逐位对拍——口径差异摆到台面上，不做静默对齐。

## 5. 验收证据（可逐条重跑）

| 判据 | 命令 | 结果 |
|---|---|---|
| pytest ≥12 例 | `python3 -m pytest tests/test_anchor_dedup_gate.py tests/test_anchor_precheck_endpoint.py -q` | **45 passed / 0 failed** |
| 基线与快照逐项对拍 | 见 §4 第一条命令 | 169 / 118 / 48 / 28 / 20 **五项全 match**（`anchor_dedup_audit_replay.json`） |
| 端点两例实测（真数据） | `python3 tools/mission_governance/anchor_precheck_live_probe.py` | 在飞锚点 → `reject` + `conflicts`；仅 failed 锚点 → `allow`（`anchor_precheck_live_evidence.json`） |
| 变异红队 ≥2 例转红 | `python3 tools/mission_governance/anchor_dedup_mutation_check.py` | 4 个变异分别杀死 17 / 12 / 4 / 4 例，复原后 sha256 一致、45 passed（`anchor_dedup_mutation_report.json`） |

变异清单（`anchor_dedup_mutation_check.py` 内定义，跑完自动恢复）：

| 变异 | 语义 | 杀死用例 |
|---|---|---|
| M1 `reject_becomes_allow` | 所有 `reject` 改成 `allow` | 17 |
| M2 `in_flight_ignored` | 忽略在飞候选池 | 12 |
| M3 `no_normalization` | 关掉大小写/空白归一化 | 4 |
| M4 `delivered_window_ignored` | 已交付永远算窗口外 | 4 |

## 6. 注意点（踩过的坑）

1. **`LIKE` 大小写敏感吃过一次假放行**：goal 里的锚点是大写、调用方常传小写，
   `goal LIKE '%anchor%'` 返回 0 行 → 端点给出 `allow` = 假放行。实库两例测试当场
   抓到（`decision=allow` 但实库确实有同锚点 running 单），改成 `LOWER(goal) LIKE LOWER(...)`。
   **教训**：查重门的漏检是「静默的」，只靠合成夹具测不出来，必须有真数据层。
2. **psql 通道要单行化 goal**：goal 里有换行，`psql -t -A` 会把一行切成多行、列错位
   （症状：`status` 全是空串）。测试侧用 `replace(replace(goal, E'\n',' '), E'\r',' ')` 单行化；
   真 DBAPI 不需要这层。
3. **`ESCAPE` 子句不能省**：锚点里带 `%`/`_` 时 LIKE 会退化成通配符 → 假冲突。
4. **判据没牙会被变异红队抓到**：M3 一开始只杀死 1 例（归一化测试太弱），补了
   `test_normalize_lowercases` / `test_normalize_collapses_inner_whitespace` 后才达标。
5. **`conflicts` 的 id 类型**：文本通道给出 `'18051'`，故 `_mission_id` 统一把数字串转 int，
   保证契约稳定（`conflicts: [int]`）。

## 7. 局限与后续（如实登记）

1. **本轮 advisory only**：端点未被 dispatch/create 调用，因此**此刻不会真的回收席位**——
   它提供的是可被派单侧消费的判定面。接入属下一步（plan 21 自主续航军），改硬门须 owner 批。
2. **SQL 粗筛抓不到全角字母数字锚点**（`ＳＹＮ` vs `SYN`）：归一化在 Python 侧生效，
   但这类行进不了候选池。缓解：调用方传规范形态；或后续把归一化锚点落成物化列/索引。
3. **在飞集合是瞬时量**：两次采集相隔 1 分钟，`running` 数就可能大幅变化（实测窗口滚动速率
   ~35 单/分钟），因此该指标的基线只能按「同刻同口径」对拍（本工具用快照复演做到了）。
4. **派生判定是词表匹配**：`返修/交差审计/重开/管到底` 命中即豁免，可能有误豁免；
   风险方向是「少拦」（不产生误拦），可接受。
5. **未覆盖**：锚点跨 organization 的重复（按租户隔离，本设计只看本租户）、
   锚点相似度（如 `X-A-V1` vs `X-A-V2` 视为不同锚点）。