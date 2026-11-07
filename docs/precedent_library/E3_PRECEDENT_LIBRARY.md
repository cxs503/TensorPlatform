# IN24-E3 · 高质量判例库（S 分级活例 + 反例）

> 战役节点 `IN24-E3` · 仓库 TensorPlatform · 机器可读源：`tools/precedent_library/data/precedents.json`
> 评分权威：`tools/outcome_evaluations.py`（S1–S5 / S-N × E0–E4，`reviewer_kind` 封顶）。
> 本库只做**分层计数**，不做加权、不做排名（`weighted_score=None`、`ranking=None`）。

## 0. 这个库解决什么

弱模型（和一部分强模型）开工前看不到"什么是好"，于是把**形式完成**当目标：测试绿、PR 并、
文件写了，但数字对不上、没有基线、没有工件。本库把这些失败与成功**逐例固化**，并从每例
提炼一条 ≤120 字、append-only 的 PLAYBOOK 规则注入开工通道（见
`INJECTION_PLAYBOOK_BLOCK.md`）。

分级口径（与 `outcome_evaluations` 一致，不自造新梯）：

| 级 | 含义 | 本库的硬门槛（代码可拒） |
|---|---|---|
| S1 | 执行合格：按判据完成，不达标项如实降级 | 不得携带对照基线（那是 S2 的门槛） |
| S2 | 基线对照改进：同判据下有前后 A/B | 必须有 `baseline{before,after,metric,delta}` 且带数字 |
| S3 | 能力建设：把一类失败模式从结构上移除 | 必须有 artifact/run/commit 证据 ≥2 条（能力可检视，不接受断言） |
| S4 | 突破 | 必须有独立评审证据；否则只能 `status=vacant` 并写明差距 |
| anti | 反例（形式闭环/打转/AI 自审冒充终判） | 必须引用真实失败现场（mission id 或证据） |

门槛是代码不是散文：`python3 -m tools.precedent_library.library validate` 会红。
当前 12 例（S1×4、S2×2、S3×2、S4×1 空缺、anti×3），全部来自真实战史。

## 1. S1 · 执行合格例

### E3-S1-01 基准对标：几何门失败如实降级（m5118）

- **问题**：对标单最易死在"总分好看"——把不合格的几何验收项隐去，只报阻力误差。本单吃水项
  G2 实际失败（低 50%），隐去则结论不可信。
- **增量证据**：`R_T=2108.2 kN` vs 实验 `2113.2 kN`（**−0.24%**，EFD ±1% 带 −1.61%~+1.17%）；
  几何 G1 PASS 0.000%、G3 PASS 0.08%、**G2 FAIL** 如实标注；单链内部口径 −5.07% 一并披露；
  `pytest 11 passed, 1 xfailed`（G2 已知缺陷）；报告 id=5871。
- **为何是 S1**：判据可复算、不达标项降级留痕（xfail/partial-FAIL），但没有任何对照基线或
  能力增量 → 按规则不得自报 S2 及以上。

### E3-S1-02 外部真值面：机制跑通 ≠ 指标达标（m5101）

- **问题**：度量类任务常把"机制演示成功"当成"指标达标"。
- **增量证据**：`pytest 33 passed`（解析器、cgroup v1/v2 夹具、四态决策表、确认窗、账本权重、
  端到端静默死）；criteria **C1/C3/C4/C5/C6 PASS**，**C2 honest FAIL 30.5% < 90%**；
  SIGKILL 回放 4/4 检出、延迟中位/最大 2.15 s、干净与晚释放队列 0 误报。
- **为何是 S1**：判据逐条给出 PASS/FAIL 且 FAIL 不被粉饰；机制增量不等于指标达标 → 不进 S3。

### E3-S1-03 只读聚合端点：判据写成等式（m5121）

- **问题**：聚合 API 最容易"看起来对"——形状对、口径与库内 SQL 不一致。
- **增量证据**：`pytest 8 passed`；12 项 KPI 每项含 `current/target/pct`；与 DB 直查**逐项误差 = 0**；
  PR 641 合并。
- **为何是 S1**：验收判据是"与 DB 直查一致"这种机器可裁的等式且达成；无基线对照增量。

### E3-S1-04 计时基线：先量再判可行（m5123）

- **问题**：预算类任务的风险是把"能跑通"当"可批量"。
- **增量证据**：单工况全链 **80 s**（全文重跑产出 JSON/图/报告）；`pytest 11 passed`；
  报告 id=5873、payload **157.4 KB**（< 250 KB 上限）；commit `027537a4`，工作树 clean。
- **为何是 S1**：耗时/工件/测试三门机器可裁并达成，且给出"批量不可行"的裁决口径。

## 2. S2 · 基线对照改进例

### E3-S2-01 原子批入库 vs 降级循环（m5115）

| 口径 | before（degraded loop） | after（atomic batch） | 增量 |
|---|---|---|---|
| 时延 | 400.9 ms | 92.1 ms | **−77.0%（4.35×）** |
| 失败残留 | 1 行 | 0 行 | −1 行 |

- **问题**：批量入库的两种实现语义不同，只报"提交成功"无法比较代价与失败残留。
- **增量证据**：上表数字来自 `artifacts/m5115/batch_bench.py`；`pytest 13 passed`（50 条入库、
  重复提交 409 且行数不变、原子性负控 0 残留、401/422）；生产探针 50/50 queued、重投 409、
  回滚后 4763→4763 行。
- **为何是 S2**：同判据下前后 A/B，两项指标同时改善且负控可失败。

### E3-S2-02 词表外置：#614 前后（commit `71e740bd` + `f39ed941`）

| 口径 | before | after |
|---|---|---|
| 代码内域词字面量 | 15 条（`figures.py`） | 0 条（落 `domain_display.json`） |
| 缺/坏配置文件 | 无恢复路径（改代码） | 恒等降级，不崩 |
| 变更规模 | — | `figures.py` +21/−6 行；新增数据文件 15 条 |

- **问题**：显示词表硬编码在绘图代码里（评审判例 #614）——平台代码携带领域词，缺配置即崩。
- **增量证据**：`git show 71e740bd` 删除 15 条字面量；`tools/chronicle/report/figures.py` 的
  `_load_domain_display()` 捕获 `OSError/ValueError` 返回 `{}` → 按原始名恒等显示；
  本库测试 `test_domain_display_fallback_is_identity` 对"缺文件/坏 JSON"两种输入现场复证降级。
- **为何是 S2**：同一行为口径的前后对照（字面量 15→0、崩溃→恒等降级），且降级有可失败测试。

## 3. S3 · 能力建设例

### E3-S3-01 报告图管线重写为 SQL-only + 每图 sha256（结构性防造假）

- **问题**：旧管线的失败模式（PG9）是"图数组在绘图脚本内合成"——文字/路径校验全绿，数字却
  无法回溯到任何工件；事后补文字规则无法根治。
- **增量证据**：`figures.py` 三图只经 snapshot 上的 **SQL** 取数（绘图代码不构造数组）；
  snapshot sqlite 字节 SHA-256 锚定；每图 PNG/PDF 的 sha256 写入 provenance，替换即
  `FIGURE_HASH_MISMATCH`；端到端 demo 现场 **verifier_status=PASS**、
  `snapshot_hash=f46ab6f3…`（**两次独立重跑同值**，可复现）、**23 条 provenance**、
  **20 条 SQL 重执行**、**3 图/6 文件**。`manuscript_sha256` 随稿件文本变化，不作判据。
- **为何是 S3**：不是修一个 bug，而是把"可造假面"从流程上移除（绘图代码无数据构造入口 + 哈希
  锚定 + 可失败码枚举），并声明它突破的旧失败模式与增量载体。

### E3-S3-02 S×E 成果评价机：可拒、可封顶、禁加权（`tools/outcome_evaluations.py`）

- **问题**：评价靠人嘴——等级可自报、无证据也能登记、机器扫描被当成同行评审、等级被加权排名。
- **增量证据**：DDL（SQLite/PG 通用子集）用 CHECK 硬拒非法 `status/grade/e_level/reviewer_kind`；
  `decide()` 纯函数落判据：理由 <50 字符→rejected、证据空→rejected、E0 不可登记、
  **S4/S5 由 `auto_scan` 判出→candidate**（`CANDIDATE_NEEDS_INDEPENDENT_REVIEW`）、
  S5 须 E≥3 且独立评审；`summarize()` 只分层计数。现场重跑 **62 passed**（含封顶负控）。
- **为何是 S3**：把"等级语义"与"机器可拒"分离，评价体系从此可复算、可红。

## 4. S4 · 突破例：如实空缺

**本库无 S4 案例，`status=vacant`，不填充。** 依据：

- S4 的判据是"能力/认识上的突破 + 独立评审 + 外部基准或多主体复现"。本轮审计的真实战史里，
  没有一例同时满足三者。
- 最接近的三例及差距：
  - `E3-S3-01`（结构性防造假）：能力是新的，但只有自证 demo，**无独立评审**；
  - `E3-S1-01`（−0.24% 对标）：结果好，但几何门 G2 仍 FAIL，且是复现既有基准，**无新颖性**；
  - `E3-S2-01`（4.35× 时延）：是工程改进，不是认识突破。
- 处理方式：留空位并写明差距，等独立评审席位（`reviewer_kind=independent`）裁决后再升格。
  把自证 demo 抬成 S4 是等级通胀，正是本库要防的事。

## 5. 反例

### E3-A-01 打转裸死单：58 次工具调用、0 commit、0 finish（m5047）

- **现场**：终局 `no_progress`；`evidence.gate=no-evidence`、`reason='模型未调用 finish'`；
  `changed_files` 仅是工作树内未提交文件；该线零推进。
- **为何是反例**：无判据、无提交、无回执 ⇒ 不进任何正面等级。正确做法是先把最小可证单元落盘
  （见 `docs/playbooks/PB-008-harness-death-modes.md`）。

### E3-A-02 形式闭环：PG1–PG8 全绿，数字仍无法回溯（W2 判例）

- **现场**：报告生产线两稿被否，`docs/papers/chronicle-report-v2/manuscript.md` 原句：
  "验证规则各自有效，但二者都**假定被检验的数据有一个不动的状态**"。
- **为何是反例**：检查通过 ≠ 结论成立。判据必须指向"不冻状态"的工件（哈希锚定 snapshot），
  否则正则校验只是装饰。该线因此作废 2 稿，后来才由 S3 例（SQL-only + sha256）补上。

### E3-A-03 auto_scan 冒充终判：机器回填是草案不是评审（m5018）

- **现场**：批量回填全部 `reviewer_kind='auto_scan'`、`reason_source='rule_derived'`；
  文档明确"回填是草案不是评审结论"，**S4/S5/S-N 只能落 candidate**。
- **为何是反例**：机器扫描冒充独立同行评审 = 等级通胀。规则（封顶）+ 1 个负控测试同时留痕，
  才能让"冒充实例"变成可失败的红灯。

## 6. 注入通道

- 渲染：`python3 -m tools.precedent_library.library render`
- 预演/落地：`python3 -m tools.precedent_library.library inject --target <BOOTSTRAP.md> [--apply]`
- 语义：**append-only**（既有字节与顺序原样保留）、**幂等**（case token 已存在即跳过）、
  **锚点缺失即报错**（绝不静默写错位置）、**单条 ≤120 字**、落位 `## 逃生规则` 之前。
- 本次落地回执（`artifacts/precedent_library/injection_receipt.json`）：首次 `apply`
  **插入 13 行 / 跳过 0**；随即复跑 **插入 0 / 跳过 13**，目标文件逐条读回 **13/13** ——
  幂等与 append-only 同时得证。
- 注意：目标文件被守护进程周期性更新（心跳行），注入前后应各取一次快照并读回校验；
  本次操作期间就观察到 `WORLD` 行被刷新，属于活文件竞态，已按"注入+读回"处理。
- 已注入内容与回执见 `INJECTION_PLAYBOOK_BLOCK.md`。

## 7. 审计链与复现

```bash
python3 -m tools.precedent_library.library validate          # 12 例，门槛全绿，空缺级如实标注
python3 -m tools.precedent_library.goal_quality --out artifacts/precedent_library/goal_quality_comparison.json
python3 -m tools.precedent_library.build_goal_samples        # 从控制面重取真实 goal（带时间戳）
python3 -m pytest tests/test_precedent_library.py -q          # 19 项验收
python3 -m tools.chronicle.report.demo                        # S3 例的证据重放（verifier PASS）
```

证据锚点分三类，均可独立复查：①`tensor/missions/<id>` 的 checks 原文；②`git show <sha>` 的
代码前后；③仓内工件/测试路径（测试 `test_repo_relative_evidence_refs_exist` 逐条校验存在性，
跨单交付的文件显式标 `external` 并给 mission 锚点，不冒充本树产物）。

## 8. 局限（不粉饰）

1. **S4 空缺**：这是本轮最诚实也最难受的结论——体系里还没有被独立评审确认的突破级成果。
2. **`after` 样本是注入改写**：goal 质量对比测的是"注入块覆盖 8 项可证伪属性"的能力，不是
   已交付任务的行为改变；真实行为改变需要后续单的对照（n=4，不作统计推断）。
3. **证据解析到"存在"为止**：本库校验引用可解析、等级门槛可拒，但不替独立评审判断
   "该证据是否真的支持该等级"——那仍是 `reviewer_kind=independent` 的职责。
4. **目标文件是活文件**：bootstrap 由守护心跳更新，注入不是事务性的；本库用"最小插入 +
   逐条读回"降低风险，但不能排除并发写。不把这条写成"已解决"。
5. **反例样本来自控制面**：m5047/m5018 的现场取自 mission 记录，非本树代码；若控制面记录被
   清理，反例的可复查性下降（已在此注明，避免读者误以为可 `git show`）。