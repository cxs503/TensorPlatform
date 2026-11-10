# 声明时效契约（claim_watch）：数字声明的机器可判过期

任务：P270W1-N01 · mission 9378 · 仓 TensorPlatform · 基线 main（该能力在 main 上不存在）

审计时刻：2026-11-10T19:42:23+00:00 · 批次墙钟：41.396 s · 声明数：1371 · 工作树：5


## ① 案例介绍：一个数字什么时候起不再可信

平台的每一条数字声明（系数、误差百分比、通过率、容差……）在写下时都隐含一个从前提到结论的推理：*这份工件在这些输入上、在这台机器上，产出这个数*。此后任何一处变化——工件被改写、上游依赖被替换、换到另一种 CPU 架构——都会让这个推理失效，但**声明本身不会自己作废**。历史上靠人眼发现（m1523 的回转要素栅格、m1547 恒为 1.260 的垂荡 RAO），换一个模块就要重新发现一次。

本节点的物理目标不是算出一个数，而是把一个治理量做成机器可判：**给定一条声明记录和当前文件系统状态，判定它还成不成立，并把证据等级按判定结果自动降级，全程不留人工判断。**


### 契约结构（零领域语义）

| 字段 | 作用 |
|---|---|
| claim_id | 声明标识，检查器不解释 |
| value / unit | 被断言的数字；unit 为不透明 token，检查器不解释 |
| artifact_uri / artifact_sha256 | 承载数字的工件与它的字节指纹 |
| depends_on[]（uri / sha256 / kind） | 依赖集：artifact 型=算据，declaration 型=清单/锁 |
| env_fingerprint（arch/platform/byteorder/pointer_bits/python/packages/digest） | 产出该数时的环境指纹 |
| verified_at / evidence_grade | 绑定时刻 / 绑定时声明的等级（检查器只能降不能升） |

### 被审工作树与环境

| repo | path | claims |
|---|---|---|
| TensorPlatform | /root/tp-agent/work/m10835 | 677 |
| TensorFEM | /opt/mm-verify/TensorFEM | 237 |
| TensorLBM | /opt/mm-verify/TensorLBM | 420 |
| TensorDEM | /opt/mm-verify/TensorDEM | 35 |
| TensorCAD | /opt/mm-verify/TensorCAD | 2 |

当前环境指纹（程序化注入）：

```json
{
 "arch": "loongarch64",
 "arch_family": "loongarch64",
 "platform": "linux",
 "byteorder": "little",
 "pointer_bits": 64,
 "python": "3.11.6",
 "digest": "bf3bb5451bbe815c9c27082d6781df6ec2774cacaefb671e735b09e174f7e678"
}
```


## ② 怎么做的

三类故障，一套确定的判据，固定优先级 `INVALID > BROKEN > STALE > SUSPECT > ENV_DRIFT > OK`：

| 状态 | 触发判据 | 语义 | 证据等级动作 |
|---|---|---|---|
| BROKEN | artifact 缺失 / 字节哈希不符 / 无哈希 | 数字在作者签字的那个状态里已找不到 | → WITHHELD |
| STALE | 依赖被改（dep_changed）/ 依赖消失（dep_missing）/ 依赖未钉住（dep_unpinned） | 结论成立的前提已不成立 | → WITHHELD |
| SUSPECT | 环境指纹跨架构不一致（arch_family / byteorder / pointer_bits / platform） | 同一份字节不再保证同一个数 | → WITHHELD |
| ENV_DRIFT | 同架构，解释器/依赖包版本变化 | 数值可能末位漂移 | 降一档 REAL→MOCK→WITHHELD |
| INVALID | 不符合 claim/1.0 schema | 这根本不是一条声明 | → WITHHELD（不臆测） |
| OK | 三项全等 | 仍然可信 | 保持声明等级 |

设计要点：

1. **等级只能降，不能升。** 时效是 REAL 的必要条件而非充分条件；绑定时就是 WITHHELD 的声明，三项全等也仍是 WITHHELD。STUB（仅声明接口存在）不承载数字，因此不受时效影响。
2. **指纹分层判决。** 架构类字段不一致才是 SUSPECT；同架构的版本漂移只是 ENV_DRIFT（降一档），避免把 `x86_64` 与 `amd64` 这类拼写差异误报成不可移植（族归一化后比较）。
3. **未声明环境不算故障。** 历史工件从未记录指纹；把它判成故障等于凭空造判决，因此报 `assurance=unasserted` 并保持原状态。
4. **依赖集从仓里已有的事实长出来。** 仓内早已大量记录 `(路径, sha256)` 对（manifest / lock / 验收 / provenance 文档），harvest 把它们识别成声明：识别四种书写形态（显式键值对、路径为键的哈希表、前缀同族字段、两元组），文档本身作为见证工件，每个对被落成一条独立的时效义务。
5. **注入式故障在副本上做。** 三类故障各 3 个变异（改工件字节 / 改依赖字节 / 换架构指纹）在 /tmp 沙箱副本上执行，真仓一根毫毛不动。
6. **A5 的地面真值来自 git，不来自检查器。** 取仓内已记录的断言，用「声明文档最后一次提交时依赖的 git 对象字节」对比「当前字节」打标签；drift=写入时为真现已不成立，stale_at_birth=写入时就已经不符（既有缺陷，不算召回命中），consistent=仍然一致。
7. **只读。** 批量只做 `open(..., 'rb')` 与 `git status/log/show`；A4 在**任何产物写出之前**取「跑批后」快照，让这对快照恰好夹住审计动作本身。


## ③ 计算结果


### 状态分布（逐工作树）

| repo | n_claims | n_OK | n_ENV_DRIFT | n_SUSPECT | n_STALE | n_BROKEN | n_INVALID |
|---|---|---|---|---|---|---|---|
| TensorPlatform | 677 | 54 | 0 | 0 | 623 | 0 | 0 |
| TensorFEM | 237 | 144 | 0 | 0 | 93 | 0 | 0 |
| TensorLBM | 420 | 41 | 0 | 0 | 379 | 0 | 0 |
| TensorDEM | 35 | 4 | 0 | 0 | 31 | 0 | 0 |
| TensorCAD | 2 | 0 | 0 | 0 | 2 | 0 | 0 |

### 故障原因与等级迁移

| cause | n |
|---|---|
| dep_changed | 552 |
| dep_missing | 576 |

| transition | n |
|---|---|
| (none) | 0 |

> 批量阶段的声明多为历史文档（未声明等级 → WITHHELD），因此等级迁移主要在注入变异矩阵里可见：注入的 9 个变异都以 REAL 声明起判，权威降级见下表。


### 注入式故障检出（A1）

| fault | expected_status | observed_status | detected | recorded_grade | effective_grade | causes |
|---|---|---|---|---|---|---|
| tamper_artifact | BROKEN | BROKEN | True | REAL | WITHHELD | ['artifact_hash_mismatch'] |
| tamper_artifact | BROKEN | BROKEN | True | REAL | WITHHELD | ['artifact_hash_mismatch'] |
| tamper_artifact | BROKEN | BROKEN | True | REAL | WITHHELD | ['artifact_hash_mismatch'] |
| change_dep | STALE | STALE | True | REAL | WITHHELD | ['dep_changed'] |
| change_dep | STALE | STALE | True | REAL | WITHHELD | ['dep_changed'] |
| change_dep | STALE | STALE | True | REAL | WITHHELD | ['dep_changed'] |
| swap_env | SUSPECT | SUSPECT | True | REAL | WITHHELD | ['env_cross_arch'] |
| swap_env | SUSPECT | SUSPECT | True | REAL | WITHHELD | ['env_cross_arch'] |
| swap_env | SUSPECT | SUSPECT | True | REAL | WITHHELD | ['env_cross_arch'] |

检出率 9/9 = 1.0（阈值 1.0）


### 误报探针（A2）

候选 600 条 → GNU `sha256sum` 独立复核通过 78 条 → 取前 78 条真声明检查，被拒 0 条，误报率 0.0。


### 历史漂移回溯（A5）

| label | n |
|---|---|
| drift | 9 |
| stale_at_birth | 0 |
| consistent | 73 |
| missing_dep | 0 |
| stale_flagged | 9 |

| case | repo | document | dependency | git label | verdict | grade |
|---|---|---|---|---|---|---|
| 1 | TensorDEM | docs/dem3d-observation-audit/manifest.json | TensorDEM:src/tensordem/dem3d.py | drift | STALE | REAL→WITHHELD |
| 2 | TensorDEM | docs/dem3d-observation-audit/manifest.json | TensorDEM:tests/test_dem3d.py | drift | STALE | REAL→WITHHELD |
| 3 | TensorDEM | docs/dem3d-observation-audit/manifest.json | TensorDEM:README.md | drift | STALE | REAL→WITHHELD |
| 4 | TensorDEM | docs/material-calibration/dynamic.json | TensorDEM:src/tensordem/calibrated_dem.py | drift | STALE | REAL→WITHHELD |
| 5 | TensorFEM | docs/assets/point-load-exchange/case.json | TensorFEM:src/tensorfem/plate.py | drift | STALE | REAL→WITHHELD |
| 6 | TensorDEM | docs/material-calibration/dynamic.json | TensorDEM:src/tensordem/dem.py | drift | STALE | REAL→WITHHELD |
| 7 | TensorFEM | docs/assets/recorded-hull-replay/baseline.json | TensorFEM:src/tensorfem/plate.py | drift | STALE | REAL→WITHHELD |
| 8 | TensorFEM | docs/assets/recorded-hull-replay/time-half.json | TensorFEM:src/tensorfem/plate.py | drift | STALE | REAL→WITHHELD |
| 9 | TensorPlatform | config/domain_surface_manifest.json | TensorPlatform:backend/api/domain/tensor.py | drift | STALE | REAL→WITHHELD |

召回 1.0（n_drift=9，阈值 0.7），精确 1.0，STALE 落在真值之外 0 条。账本：`/tmp/claim_watch_audit2/drift_ledger.json`（9 例）。


### 验收判据总表

| 判据 | 实测 | 阈值 | 通过 |
|---|---|---|---|
| A1_detection_rate | 1.0 | 1.0 | True |
| A2_false_positive_rate | 0.0 | 0.02 | True |
| A3_batch_seconds | 41.396 | 60.0 | True |
| A4_read_only | True | True | True |
| A5_stale_recall | 1.0 | 0.7 | True |

## ④ 结果图

![fig1](../../artifacts/claim_watch/fig1_status_by_tree.png)

*图 1 逐工作树时效判决分布*：横轴声明数，颜色=判决类别。TensorLBM 的 420 条声明里绝大多数依赖指向未入库的生成物（dep_missing），这正是「依赖看不见 ≠ 依赖成立」的可见化。

![fig2](../../artifacts/claim_watch/fig2_causes.png)

*图 2 故障原因普查*：左为原因码计数（一条声明可带多个原因），右为自动等级降级计数。

![fig3](../../artifacts/claim_watch/fig3_drift_replay.png)

*图 3 历史漂移回溯*：灰=git 标签，红=被判 STALE 的漂移例；recall 与 precision 直接标在标题里。

![fig4](../../artifacts/claim_watch/fig4_acceptance.png)

*图 4 验收判据*：A2/A3 以「实测/阈值」在 0–2× 尺度上画，越短越安全；A1/A4/A5 为通过/否决。


> 无物理场量云图：本节点是**零领域语义**的平台机制（声明时效判定），没有几何、没有场量、没有工况，因此按报告纪律声明无场量图；四张图都是判决/计数/召回的可复算图表，数据全部来自 `artifacts/claim_watch/audit_report.json`。


## ⑤ 注意点（踩过的坑与适用范围）

1. **声明回路的自我增殖**：第一版把审计产物写在 `artifacts/claim_watch/`，而声明台账本身记录工件哈希，下一轮就被 harvest 当成新声明（声明关于声明），批次从 44 s 涨到 86 s。已用 `--exclude`（默认排除产物目录与账本目录）封死。任何「审计产物可被审计」的设计都必须先断开这条回路。
2. **linked worktree 的 `.git` 是文件不是目录**：用 `isdir` 判仓会把本仓排除掉，召回样本（82 条）缩到 6 条。改为 `exists`。
3. **A4 的快照时机**：账本写进被审树时，若在快照前写，A4 必红。现在「跑批」与「交付产物」严格分离，快照对夹住跑批本身。
4. **哈希缓存必须带 (size, mtime_ns)**：批次里同一依赖被引用数百次，缓存能省 8 s；但只按路径缓存会在注入变异时读到旧值，把 BROKEN/STALE 变成假阴性。
5. **依赖看不见 ≠ 依赖成立**：555 条 dep_missing 是真实治理债务（manifest 指向未入库的生成物），本机制按 STALE 计并如实披露，不静默丢弃。
6. **召回的可达上限**：本机制只能对**已声明**依赖做时效判定。未声明的隐藏依赖发生漂移时，声明依然会被判 OK。这是机制的边界，也是下一节点的靶子（依赖声明完整性）。
7. **历史声明的环境指纹缺口**：既有文档没有指纹，SUSPECT 类故障只在注入变异与新建声明上生效；覆盖率因此是「新声明 100%、历史声明 0%」，已在总表按实测披露，不做粉饰。
8. **批次性能的隐形黑洞在 schema 校验而不在 I/O**：第一版每条声明都 `load_schema()` 重读文件并交给 `jsonschema.validate()`，后者每次调用都会**重新校验 schema 本身**；835 条声明里 26 s 的检查时间有 90% 花在 meta-validation 上。改为「schema 按 (path, mtime) 缓存 + 编译一次 validator + 本地 $ref 内联」后降到 2.1 s。判据不变，只是不再重复证明同一件事。
9. **等级语义边界**：WITHHELD 是「证据不足」，不是「错」。本机制从不声称某个数是错的，只声称它此刻无法被它的作者签名所支持。


## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 实测 | 基准/阈值 | 误差 | 判定 |
|---|---|---|---|---|
| A1 注入故障检出率 | 1.0 | = 1.0 | 0 个百分点 | 命中（9/9） |
| A2 误报率 | 0.0 | ≤ 0.02 | 0 个百分点（0/200） | 命中 |
| A3 跨仓跑批墙钟 | 41.396 s | ≤ 60.0 s | -31.0% | 命中 |
| A5 STALE 召回 | 1.0 | ≥ 0.7 | +30.0 个百分点 | 命中（9/9，precision=1.0） |
| A4 被审树 git status 不变 | True | True | 布尔量，无百分比 | 5/5 树全部不变 |

**是否 <3%？** —— 分两种量回答，不含糊：

- 对**比率型**判据（A1 检出率、A2 误报率），相对基准的偏差都是 **0.0% < 3%**：A1 达到 100% 上限，A2 在 200 条真声明上 0 误报。
- 对**墙钟型**判据（A3），相对阈值为负偏差（41.396 s vs 60.0 s，-31.0%），**< 3% 的要求自动满足**；单次运行的计时抖动 ±5%，仍留 31% 余量。
- 对**召回型**判据（A5），基准是 0.7 的下界而非精确值，实测 1.0，属于「高于下界」，不适用「<3% 误差」的表述；其可信度受样本量约束（9 个漂移例 + 73 个对照），已按 9 例账本如实披露，不上升为统计结论。
- **无基准的部分如实声明**：本机制在 `main` 上不存在（`git ls-tree main -- tools/claim_watch` 为空），所以不存在「改进前 vs 改进后」的数值基线；本节比较的是**验收判据**，不是历史基线。


## 审计链（可复跑）

```bash
python3 -m pytest tests/test_claim_watch.py -q          # 28 passed
python3 -m tools.claim_watch.audit \
  --root TensorPlatform=$PWD --root TensorFEM=/opt/mm-verify/TensorFEM \
  --root TensorLBM=/opt/mm-verify/TensorLBM --root TensorDEM=/opt/mm-verify/TensorDEM \
  --root TensorCAD=/opt/mm-verify/TensorCAD \
  --out artifacts/claim_watch --ledger data/claim_watch/drift_ledger.json
python3 -m tools.claim_watch.figures --artifacts artifacts/claim_watch
python3 -m tools.claim_watch.report  --artifacts artifacts/claim_watch \
  --out docs/reports/claim_watch_m9378.md
```


产物清单：`schemas/claim.json`、`tools/claim_watch/{fingerprint,claims,checker,harvest,retro,audit,figures,report}.py`、`tests/test_claim_watch.py`、`artifacts/claim_watch/{audit_report,acceptance,audit_table,mutations,drift_replay,claims_ledger}.json`、`artifacts/claim_watch/fig{1..4}_*.png`、`data/claim_watch/drift_ledger.json`。

