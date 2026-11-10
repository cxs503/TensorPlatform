# 交付面卫生机制（delivery hygiene）：内容等价三分 + 处置动作 + 只减不增门

> 模块：`tools/delivery_hygiene/`（`gitio` / `classify` / `ledger` / `dispose` / `gate` / `cli`）
> 用例：`tests/test_delivery_hygiene.py`（33 例，全部在临时合成仓库里跑，零领域语义）
> 账本：`artifacts/delivery_hygiene/<repo>/ledger.json.gz`（CLI `--ledger` 直读 gz）；
> 补单：同目录 `tickets.jsonl`（一分支一行，任务机可批量收养）；复核：同目录 `audit.json`；
> 处置演示工件：`artifacts/delivery_hygiene/apply-demo/`；图：`artifacts/delivery_hygiene/figures/`
> 门：`.github/workflows/branch-hygiene-gate.yml` + 宿主 cron（同一条 CLI）

## 1. 问题：分支只增不减的熵

每单都在工作树上建 `agent/m<id>` 分支，交付后由平台推送/合并，但**没有任何机制回收分支**。
实测两个仓库合计 **3846** 条 `agent/*` 分支，其中 **2936** 条未合并
（占 76.3%），独有提交 **9381** 条。
既有工具 `tools/branch_ledger.py` 只能回答「哪些分支有主干没有的提交」（UNIQUE），
但它**只读**（不建/删/改 ref），也不区分「同一处改动被重复 cherry-pick 成多条分支」——
于是台账指出了问题，问题却照旧增长。

## 2. 四类内容等价判据

| 类 | 含义 | 判据 | `decided_by` | 可安全关闭 |
|---|---|---|---|---|
| **D** | 孤儿 | `base..b` 无提交（tip 已在 base 历史内） | `rule:D-tip-in-base-history/v1` | 是 |
| **A** | 已覆盖 | ① 独有提交为空（patch-id 全命中 base）② 净 `git diff base..b` 为空 ③ squash 合成 patch-id 命中 base | `rule:A-patch-id-all-in-base/v1` / `rule:A-net-diff-empty/v1` / `rule:A-squash-combined-patch/v1` | 是 |
| **B** | 超集覆盖 | 本分支独有 patch-id 集合**真包含于**另一分支（链式解析到极大元）；等集簇里除 canonical 外全部判 B，而 canonical 若有严格超集也同样判 B | `rule:B-patch-id-subset-of-superset/v1` | 是（内容留在 S） |
| **C** | 真未交付 | 独有 patch-id 既不在 base 也不在别分支 | `rule:C-unique-patch-ids/v1` | 否（开补单） |

优先级 **D > A > B > C**，四类互斥且穷尽。三条 A 判据缺一不可：
实测平台交付走 squash/push-merge，squash 会重写 patch-id 使①全漏，主干后续修改又使②漏，
只有③（`git diff merge-base..b | git patch-id` 与 base 提交集合比对）能兜住。
关掉③会被单测 `test_squash_detection_is_load_bearing` 抓住（该分支从 A 退回 C）。

## 3. 两个引擎与可审计自证

* `pidset`（默认）：一次算 base 的 patch-id 集合 + 一次性批量算候选提交 patch-id，本地做集合运算。
  `git cherry` 逐分支实现要重算 base 侧 patch-id，实测 2173 条分支需 ~11 min；pidset 降到 ~2 min。
* `cherry`：`git cherry` 逐分支，与 `tools/branch_ledger.py` 完全同口径，用作**交叉校验**：
  `--crosscheck N` 抽样 N 条用 cherry 复算，一致率写进账本 `stats.engine_crosscheck`（下表末列）。

## 4. 机器可读账本

每行 `{repo, branch, class, class_label, decided_by, safe_to_close, retention_owner, mission_id,
sha, tip_date, superset_source, evidence{ahead, unique_shas, equivalent_count, empty_commits,
unique_patch_ids, net_diff_empty, squash_patch_in_base, combined_patch_id, tip_in_base_history,
merge_base, changed_files, changed_files_total}, engine}`。
除 `generated_at` 外所有字段由 git 状态唯一决定（`test_ledger_is_recomputable` 断言两次扫描逐字节一致）。

## 5. 处置动作（默认 dry-run，可回滚）

| 类 | 动作 | 说明 |
|---|---|---|
| D/A | `archive_ref` | 建 `refs/attic/<date>/<branch>` 并**回读校验**，再删 `refs/heads/<branch>` |
| B | `annotate` + `archive_ref` | 账本标注 `superset_source`；内容留存链解析成功才归档 |
| C | `open_ticket` | 一分支一单 `tickets/<repo>--<branch>.json`（kind=delivery，含独有提交与改文件） |

回滚：manifest 里每条都带 `rollback = git update-ref refs/heads/<branch> <sha>`；
`--apply` 必须同时给 `--i-mean-it`（`test_cli_apply_requires_double_confirmation`）；
C 类永不进归档列表（`test_plan_never_archives_undelivered`）；重复执行幂等
（`test_archive_apply_is_idempotent`）。

## 6. 只减不增门

* **冻结基线** `freeze`：把当前 `unmerged = B + C` 写成 ceiling。
* **棘轮**：新 ceiling 必须 ≤ 旧 ceiling − `min_reduction`；要抬高必须 `--accept-increase <理由>`，
  理由与前后数字进 `history`（`test_ratchet_refuses_increase_without_reason`）。
* **裁决** `gate`：`current_unmerged ≤ ceiling`；另报 `V2`（冻结后新增 C 类，`--strict-new` 下判失败）、
  `V4`（base 已移动未重新冻结，warning）。
* **CI**：`.github/workflows/branch-hygiene-gate.yml`（PR 跑机制自测；定时拉全部分支跑门）。
  边界声明：CI 只能看到已推送分支，堆积主要发生在宿主本地 refs 上，宿主侧用同一条 CLI 定时跑门。

## 7. 实测结果（数字由本脚本从 ledger/gate JSON 注入）

### 7.1 分类总表

| 仓库 | 分支总数 | A 已覆盖 | B 超集覆盖 | C 真未交付 | D 孤儿 | 未合并(B+C) | 未合并占比 | 可安全关闭 | 独立补单 | 引擎交叉校验一致率 |
|---|---|---|---|---|---|---|---|---|---|---|
| `TensorPlatform` | 2199 | 98 | 101 | 1412 | 588 | **1513** | 68.8% | 787 | 1412 | 100%（抽样 24） |
| `ship-performance-platform` | 1647 | 0 | 47 | 1376 | 224 | **1423** | 86.4% | 271 | 1376 | 100%（抽样 20） |

合计：**3846** 条分支 / **2936** 条未合并 / **1058** 条可安全关闭 /
**2788** 张补单。A 98 / B 148 / C 2788 / D 812。

### 7.2 判据命中分布

| decided_by（判据） | `TensorPlatform` | `ship-performance-platform` | 合计 |
|---|---|---|---|
| `rule:A-patch-id-all-in-base/v1` | 89 | 0 | 89 |
| `rule:A-squash-combined-patch/v1` | 9 | 0 | 9 |
| `rule:B-patch-id-subset-of-superset/v1` | 101 | 47 | 148 |
| `rule:C-unique-patch-ids/v1` | 1412 | 1376 | 2788 |
| `rule:D-tip-in-base-history/v1` | 588 | 224 | 812 |

### 7.3 B 类等价簇（重复 cherry-pick / rebase 的留存源）

`TensorPlatform`：13 个留存源，`agent/m9692`(27)、`agent/m1026`(18)、`agent/m1515`(13)、`agent/m1897`(12)、`agent/m9613`(10)、`agent/m1140`(6)、`agent/m9596`(5)、`agent/m9781`(4)

`ship-performance-platform`：16 个留存源，`agent/m8748`(13)、`agent/m1842`(11)、`agent/m5004`(5)、`agent/m7995`(4)、`agent/m5674`(2)、`agent/m9585`(2)、`agent/m1984`(1)、`agent/m3092`(1)

### 7.4 门裁决

| 仓库 | 门裁决 | 未合并 | ceiling | 余量 | 冻结时基线 | 冻结时间 |
|---|---|---|---|---|---|---|
| `TensorPlatform` | PASS | 1513 | 1513 | 0 | 1513 | 2026-11-10T14:41:24+00:00 |
| `ship-performance-platform` | PASS | 1423 | 1423 | 0 | 1423 | 2026-11-10T14:41:25+00:00 |

## 8. 局限（必须与结论一起读）

1. **squash 语义**：若 squash 合并**同时改写了内容**，合成 patch-id 也不会命中，该分支仍判 C。
   也就是说 C 类只承诺「base 里没有等价补丁」，不承诺「一定是新内容」。反过来，A 类可以放心关。
2. **B 类只保证包含关系**：B 的独有补丁在 S 里存在一份，但 S 的质量/后续演进不由本机制判断。
3. **历史不可比**：分支只与当前 `main` tip 比较；base 一动，`V4` 会提示重新冻结基线。
4. **空提交不计内容**（记在 `evidence.empty_commits`）：比 `git cherry` 更严于内容守恒、更宽于计数。
5. **归档是本地操作**：`refs/attic/` 不 push，因此远端不会出现归档 ref；远端删除仍需人工/平台推送。
6. **CI 覆盖有限**：见 §6 边界声明，宿主侧 cron 才是主战场。
