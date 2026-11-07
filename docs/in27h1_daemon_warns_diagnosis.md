# IN27-H1 诊断结案：器官 `daemon_warns` / `error_total` 越界（55 行/h，阈值 10）

- 诊断单：`shadowdiag-a242be993ae2d1d6`（影子 coordinator_shadow v1，beat 996703，as_of 2026-11-07T15:30Z）
- 器官：`daemon_warns`（守护告警流 WARN/ERROR 聚簇），指标 `error_total`（lines，滚动 60 min）
- 判读：**是真缺陷，非口径误报**（`diagnosis_disposition` 不适用 not_a_defect/duplicate/no_repro）
- 阈值：`warn=3 / crit=10` **未改动**（`tools/coordinator_shadow/registry.py` 原样只读引用）

## 1. 现象（零手抄，全部可复算）

| 量 | 值 | 来源 |
|---|---|---|
| `error_total` @15:30Z（影子读数） | 55 | 诊断单脉搏快照 |
| `error_total` @15:52Z（执行者复算） | 64 | `tools/coordinator_shadow/pulse.py::read_warn_stream` |
| 越界告警聚类（唯一 high 簇） | `KeyError: 'arbiter_attempts'` × 64 | 同上 `clusters` |
| ERROR 行首现 / 末现 | 14:47:19Z / 15:55:13Z（≈45 s 一拍） | `/var/log/tp-fleet/campaign_dispatcher.log` |

## 2. 根因（确定性，已用负控复现）

`campaign_dispatcher` 的自主提案波状态持久化在 `campaign_plans.metadata.autonomy`。该 dict 是
**外部可写**的持久状态：实测 plan 94 被写成旧形状

```json
{"wave": 2, "arbiter": 5498, "proposers": [5209, 5210, 5211]}     // 缺 arbiter_attempts / new_plan / exhausted
```

`ops/taskplane/campaign_dispatcher.py::_resolve_arbiter_outcome` 用直接下标推进重试计数：

```python
aut["arbiter_attempts"] += 1        # → KeyError: 'arbiter_attempts'
```

链路：plan 94 是 active 且节点全终态 → 每轮 `autonomy_phase()` 都会进入 resolve 分支
（仲裁者 m5498 已 `succeeded`，但 `_collect_new_plan()` 只认 `draft` 候选且候选=自身被自指守卫跳过
→ 返回 None）→ 命中 `aut["arbiter_attempts"] += 1` → KeyError。

**为什么自己修不好**：异常在 `_set_plan_autonomy(pid, aut)` **之前**抛出，坏状态永远写不回；
`main()` 的 `except Exception` 只 `log("ERROR KeyError: ...")` 后 `sleep(30)` 重来 ⇒ 每轮复现一次，
一小时 55~64 行 ERROR，且每轮都**中断整个 autonomy_phase**（迭代序在 plan 94 之后的计划全部拿不到
自主波——实测修复瞬间 4 个计划同时点火提案波，说明此前是系统性饥饿，不只是日志噪声）。

负控证据（旧代码 `git show HEAD:ops/taskplane/campaign_dispatcher.py` 直接跑，不依赖我的改动）：

```
NEGCTL 复现成功：旧代码抛 KeyError('arbiter_attempts') —— 与日志 'ERROR KeyError: arbiter_attempts' 同签
```

## 3. 修复（最小、可验证）

### 3.1 代码：读入即规整（不信任外部持久状态）

`ops/taskplane/campaign_dispatcher.py`

- 新增 `AUTONOMY_DEFAULTS` + 纯函数 `_norm_autonomy(aut) -> (state, repaired)`
  —— 缺键补默认、`wave`/`arbiter_attempts` 强制 int、`proposers` 非序列回退 `[]`；不改入参。
- `autonomy_phase()`：读到状态先规整，`repaired` 为真则**立即回写**并留一行 WARN 痕迹
  （坏状态在读入处被治好，本轮不再抛异常）。
- `_resolve_arbiter_outcome()` 入口再加一道规整（防御绕过 `autonomy_phase` 的调用方）。

### 3.2 数据：一次性补默认（幂等，与运行时同一实现）

`ops/taskplane/autonomy_state_repair.py`（默认 dry-run，`--apply` 才写；复用 `_norm_autonomy`，无第二套判据）

```
plan 94: 缺/坏键 ['new_plan', 'arbiter_attempts', 'exhausted']
  before={"wave": 2, "arbiter": 5498, "proposers": [5209, 5210, 5211]}
  after ={... , "new_plan": null, "arbiter_attempts": 0, "exhausted": false}
APPLIED 1 行           # 15:55:24Z；随后再跑：OK: 所有 autonomy 状态已符合当前 schema（幂等）
```

### 3.3 附带缺陷：窗口读数被 600 行尾读截断（同一器官的口径 bug）

指标名是「窗口内 ERROR 行数」，但 `_tail_lines(max_lines=600)` 在该日志 ~12 行/分钟下只覆盖
**约 50 分钟 < 60 分钟窗口** ⇒ 系统性少算。实测 15:57—15:58Z：独立复算 73 行 vs 平台读数 61 行（**-16.4%**；另一采样点 -19.7%，幅度随窗口拖尾变化，量级稳定在 15~20%）。

`tools/coordinator_shadow/pulse.py`：新增 `_window_covered()` + `_tail_covering_window()`
（600 行起，倍增到覆盖窗口为止，上限 20000 行 / 8 MB；触上限时把路径写进
`tail_capped_paths` 如实留痕，绝不静默少算）。只读取日志，不触碰读只读边界与立单纪律。

修复后同一时刻读数 71 行 vs 独立复算 71 行（**偏差 0.00%**）。

## 4. 验证

```
python3 -m pytest tests/test_campaign_dispatcher_autonomy_state.py tests/test_coordinator_shadow_warn_coverage.py -q
  → 13 passed
python3 -m pytest tests/test_coordinator_shadow_pulse.py tests/test_coordinator_shadow_judge_filing.py \
                  tests/test_coordinator_shadow_soak.py tests/test_propose_channel_guard.py -q
  → 66 passed（影子模块既有语义无回归：立单/判官/浸泡/提案通道全绿）
```

测试判据（脱库、纯函数/打桩）：

1. `_norm_autonomy` 对 plan 94 实测形状补默认；对良形状零改动（幂等）；不改入参。
2. 旧形状进 `_resolve_arbiter_outcome` 不再抛 KeyError，且**计数语义与健康状态一致**
   （0→1，派发 key `arbiter-p94-w2-a2`）；attempts 触顶仍走「开下一波、归零」原语义。
3. `autonomy_phase()` 读到旧形状时先回写规整态、本轮不抛异常。
4. 修复脚本只挑需修复的计划，`--apply` 后再跑为空操作。
5. 稠密日志（窗口内 800 条 ERROR）读数等于真值；窗口外老 ERROR 仍被排除；
   无时间戳行语义不变；触上限如实标 `tail_capped_paths`。

现场证据（`artifacts/in27h1/`）：

- `repair_applied_at.txt` = 2026-11-07T15:55:24Z；`post_fix_errors.txt`：修后 ERROR 行数 **0**（持续观察）
- 修复瞬间日志出现 `AUTONOMY plan 94 仲裁重派 m6108`（原路径此前每轮 KeyError 到此中断）
- `metric_recompute.txt`（修前 61 vs 73，-16.4%/-19.7%）/ `metric_recompute_after_fix.txt`（修后 71 vs 71，0.00%）
- `error_total_trajectory.json` + `in27h1_error_recovery.png`：越界轨迹、修复时刻、60 min 窗口衰减投影

**回阈时间**：`error_total` 是滚动 60 min 计数，最后一条缺陷 ERROR 在 15:55:13Z；历史行要等滑出窗口。
按窗口算术投影 `error_total ≤ 10` 首次成立 ≈ **16:48Z**（此后 0）。15:59Z 实测仍 71 > 10 属窗口拖尾，
不是新告警（修后新增 ERROR = 0）。

## 5. 注意点 / 局限

1. **部署副本未由本单修改**：线上跑的是 `/root/tp-agent/campaign_dispatcher.py`（工作树外，越界不得改）。
   该副本当前无害（数据已修好），但代码级防复发要等本分支合入后由部署管线覆盖；合入前若再出现
   同类旧形状状态仍会复发——`autonomy_state_repair.py --apply` 可幂等救急。
2. **`warn_total` 现在真实越界**：口径修好后读数由 388 → 462 行/h（crit=30）。原因是孤儿 draft
   每轮重刷 5~6 条同签 WARN（plan 74/93/95/99/101/114 长期不收养），这是**另一个真信号**，
   本单不动它（不属本诊断签名），建议另立诊断单：孤儿收养对同一批 draft 的重复告警应做「同签抑制/
   只报状态迁移」。
3. **窗口语义**：滑动窗口意味着缺陷止血后指标仍会「滞后一小时」才回阈；立单阈值 6 h 冷却 +
   窗口拖尾会叠加，判读时务必看「修后新增 ERROR 数」而不是只看窗口读数。
4. **未做**：未改任何阈值（口径变更需单独评审）；未改 `harness_gateway/mirror/campaign_dispatcher.py`
   （旧快照，与 ops 主体已分叉，避免双实现漂移掩盖真源）；未在 16:48Z 现场复核回阈（投影由窗口
   算术确定，另有可复算脚本 `ops/taskplane/in27h1_recovery.py` 随时补测）。