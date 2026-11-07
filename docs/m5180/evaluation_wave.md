# 评价波执行器（IN24-E1 · 全量独立评价波）· 落地报告

**波次 `wave-r1-20261107`** · 窗口 近 72 h · 批次 28 个（每批 ≤60）·
基准席位 3（加审 4）· 原子：`tools/evaluation_wave.py`（判级与统计）+ 迁移 104（审计列/波次表/引导表）。

## 1. 案例介绍（这不是物理算例，而是「评价体系本身的欠账」）

迁移 103 已经把 S×E 的**记录流**与**判据执行器**（`decide()` 硬判据、`summarize()` 分层统计）落了机器，
但记录流不会自己跑：本单开工前实测窗口内只有 91 行 = 60 `pending` + 30 `auto_scan` + 1 `independent`
——也就是**判级实质近乎为零**，S×E 分布对派发毫无牵引力。

本单交付的是「把它跑起来」的那台机器，并在真实数据上跑完首轮：

| 项 | 数值 |
|---|---|
| 评价窗口 | 近 72 小时 `succeeded` mission |
| 窗口 mission 数 | **1627** |
| 批次 | 28 批（每批 ≤60，硬上限 60 由代码夹住） |
| 每单席位数 | 3（`seat-delivery` / `seat-execution` / `seat-ledger` / `seat-adversarial` 中按哈希派生派 3 席，通道族两两不同） |
| 评价者 ≠ 贡献者 | 结构性保证：贡献者席位不进候选池 |
| 结果 | confirmed 1453 / candidate 162 / rejected（E0 不可登记）12 |

## 2. 怎么做的（方法 / 流程 / 关键设置）

1. **取窗口**：`select_window()` 取 `status='succeeded' AND finished_at > now()-72h`，按完结时间倒序。
2. **分批**：`plan_batches()` 每批 ≤60；`priority_repos` 会把「上一轮引导回路点为欠账」的仓排到前面
   （本轮实测改了 1563 张 mission 的批内位置）。
3. **派席位**：`assign_seats()` 对每单确定性派 3 席，约束三条——席位身份 ≠ 贡献者席位（`REVIEWER_NOT_INDEPENDENT`
   的结构性预防）、通道族两两不同（不许一个证据源说了算）、`salt` 含批次号（防止同批次系统性同派）。
4. **抽证据**：`build_packet()` + `extract_facts()` 只从三张表取「形状与计数」：报告 md5/字符数/图数、
   执行摘要里的命令数与 `passed` 计数、数字个数、**对照数字对**（基线标记 ± 窗口内至少两个数字的片段）、
   工件路径、突破/查新/证伪/复现标记。判级只用这些计数，不用任何自述形容词。
5. **判级（S 级）**：S5 ← 新颖性检索范围标记；S4 ← 突破声明 + 对照数字对；S3 ← **增量账本登记非空**
   （类别非 `no_substantive_delta` 且工件非空）；S2 ← 对照数字对可核；S1 ← 仅执行性证据；S-N ← 证伪标记 + E≥E2。
6. **判级（E 级）**：E2 ← 报告 ≥200 字符 + 真跑命令 + 通过计数 + ≥1 工件路径；E3 ← 上述 + 复现标记
   （`seat-delivery` 席要求 ≥2 次复现标记，单次降回 E2）；E1 ← 只有平台事实；E0 ← 无证据（不可登记 → rejected 留痕）。
7. **工件核验回灌判级**：只对本地有检出、且与本单同仓的路径下结论（`exists` / `not_found_local` / `unverified`）；
   若某单引用的工件**在检出与 `git log --all` 与同仓其它 mission 工作树里都查无此件** → 证据级封顶 E1，
   并打 `ARTIFACT_NOT_FOUND` 旗（本波 24 行）。这就是「抽查与工件对不上」被**判级吸收**的位置。
8. **共识**：等级取多数（无多数取保守档），证据级取**最小**；等级不一致 → `candidate`；
   E 级相差 1 级允许 confirmed（打 `E_LEVEL_SPREAD`），相差 ≥2 级 → `candidate`。
9. **封顶**：程序化席位不得确认 S4/S5/S-N → 一律 `candidate` + `MACHINE_SEAT_CAP`（本波 104 行）；
   引用不实则 `CITATION_UNVERIFIED`（43 行）、共识不可确认 `CONSENSUS_NOT_CONFIRMABLE`（35 行）。
10. **理由文本**：`compose_reason()` 用同一批计数拼装，≥50 字符（本波最小 368）；
    **理由里的每个数字都由 `verify_citations()` 回证据核对**（数字未命中 → 该行降 candidate 并打 `CITATION_UNVERIFIED`）。
11. **引导回路**：`guidance_policy()` 用**词典序规则**（先 S3+ 有无 → 再 S2+ 占比 → 再 S1-only 占比）
    给出离散档位 + 预算系数 + 准入判定（**禁加权、禁排名**），并落 `evaluation_guidance` 表；
    `guidance_effects()` 把统计换算成「下一轮真的变了多少」。

## 3. 计算结果（本波实测）

**判据达成**

| 判据 | 阈值 | 实测 | 通过 |
|---|---|---|---|
| 近 72h 覆盖率 | ≥80% | **99.26%**（1615/1627） | ✅ |
| 独立行占比 | ≥90% | **100.00%**（1627/1627） | ✅ |
| 理由长度分布 | 报告 + 全部 ≥50 | 见 §3.2（最小 368，`below_required`=0） | ✅ |
| 抽查 10 行与工件对得上 | 10/10 | **10/10 ok**（md5 10、引用 10、重放 10、工件 10；工件路径 114/118 可定位） | ✅ |
| pytest | ≥8 | **56 passed**（评价波 20 项 + 迁移 103 套件 36 项，`56 passed, 6 warnings in 3.45s`） | ✅ |
| 异构模型交叉复核 | （设计项） | **0.00%**（0/1627） | ❌ **honest FAIL**（见 §5.2） |

### 3.1 S×E 矩阵（confirmed）

| S\E | E1 | E2 | E3 |
|---|---|---|
| S1 | 348 | 37 | 6 |
| S2 | 189 | 818 | 55 |
| S3 | 0 | 0 | 0 |
| S4 | 0 | 0 | 0 |
| S5 | 0 | 0 | 0 |

S 分布：{'S2': 1062, 'S1': 391}；E 分布：{'E2': 855, 'E1': 537, 'E3': 61}。

> **S3 = 0 不是脚本坏了，是账本在说真话**：窗口内 `tensor_capability_deltas` 几乎全部申报
> `no_substantive_delta` 且 `artifacts=[]`，「增量登记非空」这条硬判据因此对谁都过不了。
> 同理 S4/S5 全为 candidate（程序化席位封顶）。

### 3.2 理由长度分布

n=1627 · min=368 · p10=392 · median=520 · p90=537 · max=559 · mean=491.44
· 全部 ≥50（低于阈值 0 行）· ≥120 字符占比 100.0%。

### 3.3 证据核验与共识

- 引用可核：**1571/1627（96.56%）**；数字命中 57472 次，未命中 197 次（未命中的行已降 candidate）。
- 工件存在性：`exists` 153 · `not_found_local` 190 · `unverified` 7447
  （`unverified` = 该仓无本地检出，**不下结论**）。
- 席位一致：1500/1627（92.19%）全一致；不一致者落 candidate。
- 判级旗标计数：`REPRO_ARTIFACT_CITED` 1033、`REPLICATION_MARKER` 156、`REPORT_ONLY` 153、`EXECUTION_WITHOUT_PASS_COUNT` 99、`E3_DOWNGRADED_SINGLE_REPLICATION` 85、`E_LEVEL_SPREAD` 81、`LEDGER_CONTRADICTION` 48、`SEAT_DISAGREEMENT` 46、`THIN_BASELINE_EVIDENCE` 32、`ARTIFACT_NOT_FOUND` 24、`NO_EVIDENCE` 12、`E2_NO_ARTIFACT_CITED` 11、`S4_WITHOUT_REPRO` 3、`S5_NO_SCOPE_ARTIFACT` 3
- 按仓分布：TensorDEM 233、TensorSPH 221、TensorFEM 211、TensorBEM 201、TensorCAD 168、TensorFVM 158、TensorLBM 112、TensorPlatform 90、TensorDG 79、ship-performance-platform 75、TensorPapers 73、TensorPhysics 6

## 4. 结果图

![sxe](../../artifacts/m5180_evaluation_wave/fig_sxe_matrix.png)

*图 1：S×E confirmed 分布热图。S2/E2 是主块；S3–S5 空白本身就是结论（账本登记与新颖性声明缺失）。*

![reason](../../artifacts/m5180_evaluation_wave/fig_reason_length.png)

*图 2：等级理由长度分布（左：分桶；右：分位数 + 50 字符阈值红线）。*

![repo](../../artifacts/m5180_evaluation_wave/fig_repo_outcome.png)

*图 3：按仓评价结果（总评价数 / S2+ confirmed / candidate 叠加）。*

![loop](../../artifacts/m5180_evaluation_wave/fig_loop_effect.png)

*图 4：引导回路增量（同一窗口 round2 只读重放，相对 round1 的变化）。*

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

### 5.1 踩过的坑（都已进代码）
1. **`ON CONFLICT` 只允许一个冲突目标**：同一行可能同时撞主键 `evaluation_id` 与分部唯一索引
   `artifact_id`，PostgreSQL 会先撞主键 → 改「先 UPDATE 后 INSERT」。
2. **被拒/不可登记行必须换 ID**：否则重放时同 ID 插入 → 主键冲突；负例按 `<artifact>:rejected:<rand>` 留痕。
3. **片段截断会切出证据里不存在的数字**（`0.03` 被切成 `03`）→ 抽查会（正确地）判为引用不实；
   修法是 `_tidy_snippet()` 切掉半个数字，而不是放宽核对。
4. **工件「查不到」不等于「不存在」**：报告来自各 mission 自己的分支，主工作树里查不到很常见；
   四级口径（exists / exists_in_history / exists_peer_worktree / not_found_local）避免误伤。
5. **加审名单必须截断**：ledger_debt 一开就是 11 个仓 → 全生态同时加审等于没加审；按行数取前 5 仓。

### 5.2 局限（不粉饰）
1. **席位不是异构模型**：`seat-*` 是确定性程序化 rubric 实例（同进程、可重放），
   `cross_model_verified=0/1626`。节点要求的「模型族交叉」**未达成**——贡献者族取自 `mission.model`
   （deepseek 425 / qwen 139 /
   unknown 1063），席位侧没有真实推理族可比。
   因此 S4/S5/S-N 一律封顶 candidate——**宁可判不动，也不冒充同行评议**。
2. **`unknown` 族占多数**（1063/1627）：窗口内 mission 大量没填 `model`，
   跨族约束在这些单上退化为「席位间通道族两两不同」。
3. **工件存在性跨仓基本未核**：`unverified` 7447 条路径（占 95.6%），
   因为本机只有本仓检出。跨仓「工件对得上」目前只能靠报告 md5 + 引用可核，**不能**说成已核实。
4. **判级阈值是治理阈值，不是物理阈值**：本单没有物理量，因此「<3% 误差」这套标准不适用（见 §6）。
5. **平台侧尚未强制**：准入判定（`admission_decision`）与引导表已落库并有测试，但**派发器未接线**
   （TensorPlatform 冻结期），线上 8010 也不服务本工作树的这份代码。

## 6. 误差判定（关键量 vs 基准）

| 关键量 | 基准/阈值 | 实测 | 偏差 | 是否达标 |
|---|---|---|---|---|
| 覆盖率 | ≥0.80 | 0.9926 | +19.26 pp | ✅ |
| 独立行占比 | ≥0.90 | 1.0000 | +10.00 pp | ✅ |
| 理由最小长度 | ≥50 字符 | 368 | +318 字符（636% 余量） | ✅ |
| 调用可核率 | 100% | 0.9656 | −3.44 pp（197 个数字未命中 → 降 candidate） | ⚠️ 未达 100%，已按保守规则降级而非忽略 |
| 抽查一致 | 10/10 | 10/10 | 0 | ✅ |
| 异构复核占比 | （设计目标 >0） | 0.0000 | — | ❌ honest FAIL |

**是否 <3%？** 本单没有被测物理量，故「相对实验/解析解的物理误差」不适用（无基准可对标）——
这一点必须说清楚，不能含糊。可对标的是**治理阈值**：覆盖率、独立性、理由长度三项余量都在
（见上表），引用可核率差 3.44 pp 未达 100%，且这部分行**没有被算进 confirmed**，
属于保守失败而不是粉饰通过。

## 7. 引导回路（评价不是记账，是牵引）

落库物 `evaluation_guidance`（按波次 × 主体）+ API `GET /api/v1/tensor/outcome-evaluations/guidance`。档位规则（词典序，无加权）：

| 主体 | 档位 | 触发条件 | 预算系数 | 准入 | 优先级 |
|---|---|---|---|---|---|
| agent | `leveraged` | 有 S3+ 且未判率 ≤10% | ×1.5 | open | +1 |
| agent | `standard` | S2+ 占比 ≥20% 且未判率 ≤20% | ×1.0 | open | 0 |
| agent | `probe` | 行数 ≥3 且 S1-only ≥90% | ×0.5 | evidence_required | −1 |
| repo | `healthy` | 有 S3+ | ×1.0 | open | 0 |
| repo | `probe_scrutiny` | 行数 ≥8 且 S1-only ≥90% | ×1.0 | open | +2 |
| repo | `ledger_debt` | 行数 ≥20 且 S2+ ≥50% 且 **S3+ = 0** | ×1.0 | open | +2 |

本波落库：repo `ledger_debt` 11 仓（加审名单截断取前 5：['TensorDEM', 'TensorSPH', 'TensorFEM', 'TensorBEM', 'TensorCAD']），
agent `probe` 1 个（['tp-qw36-1']，准入判定 `['ADMISSION_EVIDENCE_REQUIRED', 'tier=probe', 'rows=23']`）；
预算系数总增量 -11.5。

**可量效果（round1 → round2，同窗口只读重放）**：

| 变化 | 数量 |
|---|---|
| 批内位置改变 | **1563** / 1627 |
| 拿到第 4 席（加审） | **1034** |
| 等级/证据级/状态改变 | **24** |
| 理由中位数 | 520 → 532 字符 |

也就是说：**统计被消费了**，消费的结果是下一轮「谁先被看、谁多看一席、谁被要求补证据」都变了。
未接线的部分（派发器实际执行准入/预算）如实记为局限（§5.2-5）。

## 8. 复现

```bash
# 1) 迁移（新增审计列 + 波次表 + 引导表，幂等）
su postgres -c "psql -d tensorplatform -f backend/migrations/104_evaluation_wave.sql"

# 2) 判据单测（无 DB）
python3 -m pytest tests/test_evaluation_wave.py -q          # 20 passed

# 3) 跑一波（幂等，可重放；重放不增行）
python3 tools/evaluation_wave.py --window-hours 72 --batch-size 60 --n-seats 3 --salt r1 \
  --wave-id wave-r1-20261107 --artifact-root . --local-repo TensorPlatform \
  --out artifacts/m5180_evaluation_wave

# 4) 引导回路接入下一轮（只读重放，测增量）
python3 tools/evaluation_wave.py --guidance-from wave-r1-20261107 --dry-run \
  --out /tmp/wave_r2 --artifact-root . --local-repo TensorPlatform

# 5) 出图 + 生成本报告
python3 tools/plot_evaluation_wave.py --dir artifacts/m5180_evaluation_wave
python3 tools/gen_evaluation_wave_doc.py --dir artifacts/m5180_evaluation_wave \
  --out docs/m5180/evaluation_wave.md

# 6) 独立复算（换成实际 wave_id）
su postgres -c "psql -d tensorplatform -f artifacts/m5180_evaluation_wave/recompute.sql"
```

## 9. 审计链

- 平台报告：`/tensor/reports` id **5910**（mission_id 5180，含 4 张图内嵌）。
- 落库：`outcome_evaluations`（1627 行，新增审计列 wave_id/seat_ids/seat_families/seat_class/
  contributor_family/cross_model_verified/consensus/citation_check/value_flags/artifact_check）、
  `evaluation_waves`（窗口与指标快照）、`evaluation_guidance`（准入/预算/优先级）。
- 独立复算：`artifacts/m5180_evaluation_wave/recompute.sql`（9 段 SQL，psql 直接跑，与本报告逐格一致）。
- 只读读面：`GET /api/v1/tensor/outcome-evaluations/guidance`（迁移 104 未上时返回空集合，不阻断）。

产物清单见 `artifacts/m5180_evaluation_wave/`：`wave_report.json`（全量行 + 指标 + 判据）、`summary.json`、
`spot_check.json`、`guidance.json`、`round2_effect.json`、`rows.jsonl`、`recompute.sql`、4 张 png。
