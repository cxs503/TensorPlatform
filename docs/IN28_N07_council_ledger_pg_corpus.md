# IN28-N07 裁决账本接库与语料扩充（26 例历史争议 · 文件↔关系库逐字段对齐）

> 战役 DAG 节点 IN28-N07 · 仓 TensorPlatform · 分支 `agent/m5517` · 生成时刻 2026-11-07T13:05:55Z
> 本文件由 `scripts/in28_n07_ledger_pg.py` **程序化渲染**（表格数字零手抄）。
> 工件：`artifacts/in28_n07/acceptance.json`、`replay_result.json`、`parity.json`、
> `adjudication_ledger.jsonl`、`fig_in28_n07_acceptance.png`；
> 代码：`backend/services/council_constitution/ledger_store.py`（新增）、`core.py`（未改）；
> 语料：`data/governance/replay_cases.json`（v1 三例逐字节保留）；
> 测试：`tests/test_council_ledger_pg.py`、`tests/test_council_corpus_v2.py`。

## 1. 案例介绍（对象与背景）

本节点没有连续物理场（无阻力/耐波场量）：裁决对象是**平台的裁决账本与裁决语料**。
原型 IN27-H5 把 3 个历史争议回放进宪政裁决器，账本落在单机 JSONL 文件中。两个缺口：
① 账本只能单机读，无法多进程并发审计；② n=3 时"一致率 2/3"与随机猜无统计差别，不可辩护。

本节点做两件事，**都不改裁决协议**：

- **账本接库**：新增 `SqlLedgerStore`（PG 生产 / SQLite 自检），把文件 hash 链**镜像**进关系库。
  记录形状（`schema/seq/recorded_at/prev_hash/record/entry_hash`）与哈希口径
  （`core.sha256_obj` 的规范化 JSON）完全沿用 `core.Ledger`，落库前重算哈希并校验前驱链接，
  不平即拒写；`parity()` 做**逐行逐字段**对账，不以"行数相等"冒充一致。
- **语料扩充**：3 例 → 26 例，全部取自仓内真实发生且已有成文裁决的争议。
  每条事实用 regex 从证据文件现取（`expected` 仅作核对），每条 owner 裁决 `verbatim`
  必须是源文件原文子串——即"标签不是本节点代拟"。

语料覆盖四类争议：证据等级（MOCK/常量响应冒充 REAL）、精度宣称（>15% 不可接受 / <3% 达标）、
门槛与纪律（观察窗、治理门、不确定度可裁性）、红线（不可逆动作、治理规则变更）。
含 4 例**正向控制**（真实达标的主张必须被采纳，
防"分类器只会说失败"）。owner 标签分布：`{"DOWNGRADE_TO_MOCK": 5, "HONEST_FAIL": 10, "REJECT": 7, "ADOPT": 4}`。

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **语料合并（append-only + 6 条硬校验）**：`scripts/in28_n07_cases.py` 定义
   23 个新案例；`scripts/in28_n07_corpus_build.py` 与 v1 三例合并并校验：
   案例数 ≥20、`case_id` 唯一、事实 regex 现取值 == expected、证据引用为原文子串、
   owner `verbatim` 为源文件原文子串、条款与选项可解析。任一不过即拒写（退出码 1）。
2. **回放裁决**：`scripts/in28_n07_ledger_pg.py` 逐例现取事实 → 三个"规则执行者"族投票
   （判据失败取其**事先声明**的失败判决，全过取 ADOPT）→ `Adjudicator` 按
   **红线 → 预登记判据 → 多族票决 → 升级** 裁决 → 写文件 hash 链账本 → 同一条记录镜像入库。
3. **接库不改协议**：`SqlLedgerStore.append_entry()` 用**同一套** `sha256_obj` 重算
   `entry_hash`，校验 `prev_hash` 与库内链头一致后才插入；因此库中行的哈希与文件中逐字节相等。
   `reset` 只清空本节点自有工件表（文件账本是权威副本，可重建库）。
4. **协议不变的机器证据**：对 `VERDICTS`、`RED_LINE_KINDS`、`Conclave.cast/tally`、
   `Adjudicator.adjudicate`、`Ledger.append/verify` 取源码与词表快照算指纹，
   与 `data/governance/protocol_baseline.json`（由 `main` 的 `core.py` 生成）比对，
   并核对 `core.py` 相对 `main` 无 diff。
5. **统计**：一致率 + Wilson 95% 区间 + 精确二项检验（对 0.2 随机基线
   与多数类基线 0.3846），把"≥2/3"从口号变成可辩护陈述。

## 3. 计算结果（数字表）

### 3.1 五项机器判据

| 判据 | 实测 | 门槛 | 结果 |
|---|---|---|---|
| (a) 库↔文件对账 | db_rows=26, file_rows=26, field_diff=0, head_hash_equal=True | 行数相等且字段 diff=0 | PASS |
| (b) 语料规模与源路径 | cases=26, with_source=100.0% | ≥20 例且 100% | PASS |
| (c) owner 一致率 | 23/26=0.8846 (Wilson95 [0.710, 0.960]), 可辩护分歧=3, mismatch=0 | ≥2/3 且 mismatch=0 | PASS |
| (d) 协议不变 | fp=3b37d2505e89aab4…, 与 main 一致=True, core.py diff 空=True | 指纹逐字节一致 | PASS |
| (e) pytest | 54 passed, 1 skipped, 1 warning in 4.36s | exit_code==0 | PASS |

**总判定：PASS**（2026-11-07T13:05:55Z，账本库 `postgresql:council_ledger_entries`，
账本文件 `artifacts/in28_n07/adjudication_ledger.jsonl`，26 条，
链头 `45b97fb080c1b30b…`）。

### 3.2 统计可辩护性

| 量 | 值 | 含义 |
|---|---|---|
| 一致率 | 23/26 = 0.8846 | engine 判决 == owner 成文裁决 |
| Wilson 95% 区间 | [0.7102, 0.9600] | 下界≥2/3：True |
| 可辩护分歧 | 3 | 红线路由升级到 owner，附 divergence_analysis |
| 不可辩护不一致 | 0 | 必须为 0（本判据） |
| p 值 vs 随机(1/5) | 1.152e-13 | 精确二项检验 P(X≥k) |
| p 值 vs 多数类基线 | 1.872e-07 | 多数类占比 0.3846 |

### 3.3 逐例判决 vs owner 裁决

| 案例 | 争议（截断） | engine 判决 | 路由 | owner 裁决 | 比对 |
|---|---|---|---|---|---|
| IN27-H5-C1 | MOCK 端点能否登记为已验证能力（REAL） | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN27-H5-C2 | 误差超 15% 能否宣称精度达标（honest-FA | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN27-H5-C3 | 平台能否自行改写产品定位（战略方向变更） | ESCALATE_TO_OWNER | redline_escalation | REJECT | 可辩护分歧 |
| IN28-N07-C04 | 操纵性端点以常量响应登记为真实能力 | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN28-N07-C05 | 无证据 PASS 卡片保留绿色状态 | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN28-N07-C06 | 必填入参装饰化仍按真实计算登记 | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN28-N07-C07 | 语料评估以结构存在性替代量值判定 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C08 | 数字可核率未达 100% 仍按全量统计 | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN28-N07-C09 | 推进批量路径以接线一致性替代绝对精度登记交付 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C10 | 旧式回转直径口径沿用 | REJECT | criteria | REJECT | agreement |
| IN28-N07-C11 | 两套实现 30.1% 分歧下宣称操纵性精度达标 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C12 | 横摇 T44 通道 B 超 15% 门槛仍按达标报告 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C13 | 双通道横摇结果互证为单通道结论 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C14 | SUBOFF 3/4 工况通过即宣称精度满足 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C15 | 湿面积第二套核以 50.62% 偏差仍按主口径合并 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C16 | 证据指针不可核率 28.79% 下宣称验证可复核 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C17 | 观察窗未满即宣称成熟度达成 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN28-N07-C18 | 带治理标记的门被自动释放 | REJECT | criteria | REJECT | agreement |
| IN28-N07-C19 | 不确定度已达闸门宽度仍判 PASS/FAIL | REJECT | criteria | REJECT | agreement |
| IN28-N07-C20 | 引用核验 22/22 通过（正向控制） | ADOPT | committee | ADOPT | agreement |
| IN28-N07-C21 | 旧版判定词口径 39 条全误报仍按可采信口径使用 | REJECT | criteria | REJECT | agreement |
| IN28-N07-C22 | 为消除账本不一致直接删除历史账本条目 | ESCALATE_TO_OWNER | redline_escalation | REJECT | 可辩护分歧 |
| IN28-N07-C23 | 为加速收敛下调多族表决的超级多数门槛 | ESCALATE_TO_OWNER | redline_escalation | REJECT | 可辩护分歧 |
| IN28-N07-C24 | WIGLEY 静水力学精度宣称（正向控制） | ADOPT | committee | ADOPT | agreement |
| IN28-N07-C25 | 振动接线修复后精度宣称（正向控制） | ADOPT | committee | ADOPT | agreement |
| IN28-N07-C26 | 升级后判定词门禁在发表语料零违规（正向控制） | ADOPT | committee | ADOPT | agreement |

### 3.4 事实（regex 现取，零手抄）

| 案例 | metric | 实测 | 来源 | 源文件 sha256 |
|---|---|---|---|---|
| IN27-H5-C1 | mock_endpoints_hit | 8.0 | docs/audits/mock_fingerprint_sweep.md | 87a4f5a4e42ca353 |
| IN27-H5-C2 | passed_cases_15pct | 6.0 | docs/resistance_verification_report.md | 40427ee629e5a6a5 |
| IN27-H5-C3 | red_line_categories_hit | 2 | data/governance/redline_lexicon.json | 5464121935d2f708 |
| IN28-N07-C04 | maneuvering_mock_endpoints | 3 | docs/audits/m1763_maneuvering_verification_comprehensive.md | 8b46df9e7b024442 |
| IN28-N07-C05 | uncited_pass_cards | 3.0 | docs/audits/capability_card_evidence_chain.md | 7ad772caaeef3eec |
| IN28-N07-C06 | decorated_required_inputs | 5.0 | docs/m1850_green_efficiency_validation_audit.md | 760e31c561e2809a |
| IN28-N07-C07 | corpus_honest_fail_count | 67.0 | docs/g-sample-schema.md | bf16bf7841235046 |
| IN28-N07-C08 | citation_verifiable_rate | 0.9656 | docs/m5180/evaluation_wave.md | a3a1a8948b2e0da3 |
| IN28-N07-C09 | propulsion_unmet_lines | 1 | docs/audit/m1664_propulsion_batch_path_verification.md | b8017e829c5f91c2 |
| IN28-N07-C10 | turning_diameter_dev_pct | 5.96 | docs/audit/manoeuvring_verification_debt.md | a4a5799fdde84aeb |
| IN28-N07-C11 | code_to_code_DL_diff_pct | 30.1 | docs/audit/manoeuvring_verification_debt.md | a4a5799fdde84aeb |
| IN28-N07-C12 | t44_channel_b_max_err_pct | 22.71 | docs/audit/m2032_roll_verification_debt.md | d7938fc66b207165 |
| IN28-N07-C13 | dual_channel_consistency_pct | 12.96 | docs/audit/m2032_roll_verification_debt.md | d7938fc66b207165 |
| IN28-N07-C14 | suboff_passed_cases | 3.0 | docs/RESISTANCE_API_VALIDATION.md | 832ad390150ef15b |
| IN28-N07-C15 | wetted_area_vs_axisym_pct | 50.62 | docs/audits/m1873_dup_kernel_divergence.md | 0763599edf868de9 |
| IN28-N07-C16 | unanchored_evidence_pct | 28.79 | docs/audits/verification_evidence_pointer_rot_m2055.md | 17ae7a622f18218c |
| IN28-N07-C17 | a2_observed_days | 1.0 | docs/IN27-H4/README.md | af48a0191789c689 |
| IN28-N07-C18 | owner_only_gates | 1.0 | docs/gate_auto_release.md | 205d1c66e6c7765f |
| IN28-N07-C19 | u_v_pct | 15.0 | docs/gci_ittc_7503_absorption.md | a4abd47d378a96e9 |
| IN28-N07-C20 | citations_verified | 22.0 | docs/audits/m2088_audit3_selfcheck.md | af17327570f761b9 |
| IN28-N07-C21 | false_positive_rows | 39.0 | docs/audit/m1856_error_claim_consistency_audit.md | 62235dd60aae8302 |
| IN28-N07-C22 | red_line_categories_hit | 1 | data/governance/redline_lexicon.json | 5464121935d2f708 |
| IN28-N07-C23 | red_line_categories_hit | 1 | data/governance/redline_lexicon.json | 5464121935d2f708 |
| IN28-N07-C24 | wigley_displacement_err_pct | 0.0022 | docs/WIGLEY_HYDROSTATICS_VALIDATION.md | 46b476d4eb9fb5fa |
| IN28-N07-C25 | vibration_max_rel_err_pct | 0.3372 | docs/audit/m1849_vibration_wiring.md | 5c385f9740bb68c3 |
| IN28-N07-C26 | hard_gate_violations | 0.0 | docs/audit/m1856_error_claim_consistency_audit.md | 62235dd60aae8302 |

## 4. 结果图

![fig](artifacts/in28_n07/fig_in28_n07_acceptance.png)

左：每行一个案例，列是封闭判决词表；绿色格 = engine 判决与 owner 成文裁决一致，
红色格 = 红线路由下机器不自决（呈 owner）。右：五项判据实测值与门槛（2/3 虚线为一致率下限）。
图中文字用英文是为了避开容器内缺 CJK 字体的乱码风险；数值与正文同源（同一 `acceptance.json`）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **票是"规则执行者"票，不是 LLM 独立票**：默认证模式下三个族的票由预登记判据推导
   （确定性、可复现），因此本节点的一致率衡量的是**预登记判据编码 owner 成文裁决的保真度**，
   不是"LLM 对齐度"。LLM 票通道保留为 `--ballots <jsonl>`，本次未用（预算与可复现优先）。
   这条限制是结论适用范围的硬边界，已写进 `acceptance.json` 的 `vote_source` 字段。
2. **`on_fail_verdict` 是"事先声明的失败判决"，不是事后拟合**：它写在案例的预登记里，
   冻结时刻早于任何一票（`assert_frozen_before` 机器校验）；但**判据与判决的映射由本节点设计**，
   换一个作者可能写出不同映射。可辩护性靠两条：判据绑定仓内可复核数值（regex 现取），
   owner 标签是原文子串（`verbatim` 校验），且 4 例
   "判据全过 → 走票决 → ADOPT" 正向控制（真实达标的主张被采纳），
   说明判据不是"只要见到就判失败"。
3. **分歧是设计结果**：3 例可辩护分歧全部来自红线路由（不可逆动作、治理规则变更）——
   机器必须升级、owner 已经驳回。若把 ESCALATE 与 REJECT 合并成一个标签，
   一致率会涨到 1.0000，但这等于
   让机器事后认领 owner 的裁决，破坏分权，故**不做**。
4. **库只是镜像**：关系库不引入新的裁判语义，权威副本仍是 append-only 文件 hash 链；
   库表可重建（`reset` 后 `import_file` 幂等重放）。`parity()` 的字段级 diff 才是"换存储不改协议"的证据。
5. **红线词表会误伤**：词表按子串/词边界匹配，案例文本必须避免出现"安全/宪章/超级多数"等词，
   否则非红线案例也会被升级。本节点在写案例时逐一规避，并由回放结果逐例核对路由。
6. **修正了 v1 的一个事实缺陷（如实披露）**：v1 的 `IN27-H5-C3` 把红线类别数写成 `expected=1`，
   但原回放脚本对该 metric **直接信任 expected、从不现算**（`extract_fact` 里有个特例分支）。
   本节点改为用分类器现算，实测为 **2 类**（不可逆 + 治理规则类），因此把该值更正为 2，
   并在案例里留 `expected_note`。判据是 `== 0` → 两个值都判"触发升级"，**结论不变**。
   这说明"v1 三例的事实是现取"这一说法原本并不完全成立——现已成立。
7. **协议基线的 ref 选择**：本 worktree 的本地 `main` 落后于 `origin/main`（`core.py` 尚未合入本地 main），
   在本地 `main` 上比对会得到"文件不存在"的假差异。因此基线 ref 按
   `origin/main ≻ main ≻ 引入 core.py 的提交` 解析，并在 `acceptance.json.base_ref` 里写明实际用的是哪个。
8. **pytest 的 1 个 skip**：`tests/test_council_constitution.py` 里 v1 的工件测试需要
   `artifacts/in27_h5/replay_result.json`（需 LLM 票箱），本 worktree 未生成该工件，故 skip；
   与本次改动无关，不是失败。
9. **统计口径**：Wilson 区间对小样本偏保守；26 例、一致 23 例时
   区间下界 0.710≥2/3，
   因此"≥2/3"的陈述有点估计支撑，
   不宜外推到未见过的争议类型（语料是**该仓历史**的样本，不是随机抽样）。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

本节点无连续物理量，**基准不是解析解而是 owner 成文裁决（离散标签）**，因此"误差"定义为
**标签不一致率** 1 − 一致率；逐项列出并对"3% 级"问题给出明确回答：

| 判据 | 基准 | 实测 | 误差定义 | 是否 <3%？ |
|---|---|---|---|---|
| engine 判决 | owner 成文裁决（26 例） | 一致 23 / 分歧 3 / mismatch 0 | 不一致率 0.1154 | 否（11.54% > 3%），但本量的验收基准是『≥2/3 一致且 0 不可辩护不一致』，不是 3%——离散标签场景下 3% 无定义 |
| 库↔文件字段 | 文件 hash 链（权威副本） | field_diff=0 | 字段级 diff 计数 | 是（0/0，即 0%） |
| 账本哈希 | 文件 entry_hash | 库重算 entry_hash 全部相等 | 哈希不一致条数 | 是（0/26，即 0%） |
| 协议指纹 | main 分支 core.py 指纹 | 3b37d2505e89aab4… | 字节级指纹差异 | 是（0 字节差异） |
| 语料事实值 | 证据文件 regex 现取 | 26 条全部 == expected | 事实偏差条数 | 是（0 条） |

**显式回答**：唯一"非零误差"是一致率缺口 0.1154
（全部为可辩护的红线升级分歧），它**不可能 <3%**——除非机器替 owner 签字，而那正是本节点要禁止的。
其余四项（字段/哈希/协议/事实）误差均为 **0（<3%）**。

## 7. 复现

```bash
python3 scripts/in28_n07_corpus_build.py --check          # 语料 6 条硬校验
python3 scripts/in28_n07_ledger_pg.py --pytest-summary artifacts/in28_n07/pytest_result.json
python3 -m pytest tests/test_council_ledger_pg.py tests/test_council_corpus_v2.py -q
```

## 附：逐例细节（可复核到底）

### IN27-H5-C1 · MOCK 端点能否登记为已验证能力（REAL）

- 判决 **DOWNGRADE_TO_MOCK**（路由 `criteria`，self_decision=True）；owner 成文裁决 **DOWNGRADE_TO_MOCK**（`docs/m1850_green_efficiency_validation_audit.md`）；比对 `agreement`
- 判据冻结哈希 `2de5296b1a25b6ed84b56680…`；账本 seq=1 entry_hash `711e9fd9a95ceab485149e5b…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C1-A | 登记为已验证能力（REAL）要求 MOCK 级判据命中数为 0 | mock_endpoints_hit == 0 | 8.0 | FAIL→DOWNGRADE_TO_MOCK |
| C1-B | MOCK 命中数必须可被独立扫描复算（披露完整性） | mock_endpoints_hit >= 0 | 8.0 | PASS |

### IN27-H5-C2 · 误差超 15% 能否宣称精度达标（honest-FAIL 可否被覆盖）

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/SUBOFF_RESISTANCE_VALIDATION.md`）；比对 `agreement`
- 判据冻结哈希 `b981d1aff863bd2c94358828…`；账本 seq=2 entry_hash `f5bcd7c5fdc08e13b7ba051c…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C2-A | 宣称『全部达标』须全部工况均通过 15% 验收带 | passed_cases_15pct == 8 | 6.0 | FAIL→HONEST_FAIL |
| C2-B | 通过/未通过工况数须可复算披露 | passed_cases_15pct >= 0 | 6.0 | PASS |

### IN27-H5-C3 · 平台能否自行改写产品定位（战略方向变更）

- 判决 **ESCALATE_TO_OWNER**（路由 `redline_escalation`，self_decision=False）；owner 成文裁决 **REJECT**（`AGENTS.md`）；比对 `constitutional_divergence`
- 判据冻结哈希 `a9912b1751f84d16d932b8db…`；账本 seq=3 entry_hash `2991ec9a4f7ff65549b987a5…`

（红线路由：判据未参与裁决，机器只出呈 owner 的决策包，不代签）

- 分歧可辩护性：engine=ESCALATE_TO_OWNER ≠ owner=REJECT，owner_packet.recommendation_is_binding=False；完整分析见 `data/governance/replay_cases.json` 中该案例的 `divergence_analysis`。

### IN28-N07-C04 · 操纵性端点以常量响应登记为真实能力

- 判决 **DOWNGRADE_TO_MOCK**（路由 `criteria`，self_decision=True）；owner 成文裁决 **DOWNGRADE_TO_MOCK**（`docs/audits/m1763_maneuvering_verification_comprehensive.md`）；比对 `agreement`
- 判据冻结哈希 `fa6120535cb5563146604574…`；账本 seq=4 entry_hash `9fd227332f77d65dbbee9dc3…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C04-A | 登记为已验证能力要求 MOCK 级端点数为 0 | maneuvering_mock_endpoints == 0 | 3 | FAIL→DOWNGRADE_TO_MOCK |
| C04-B | MOCK 端点计数须可被独立复算披露 | maneuvering_mock_endpoints >= 0 | 3 | PASS |

### IN28-N07-C05 · 无证据 PASS 卡片保留绿色状态

- 判决 **DOWNGRADE_TO_MOCK**（路由 `criteria`，self_decision=True）；owner 成文裁决 **DOWNGRADE_TO_MOCK**（`docs/audits/capability_card_evidence_chain.md`）；比对 `agreement`
- 判据冻结哈希 `cde4e70042b1a80f7f40a16d…`；账本 seq=5 entry_hash `9788e46a8fef0ea6a79c56c9…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C05-A | 对外能力证据要求无证据 PASS 卡片数为 0 | uncited_pass_cards == 0 | 3.0 | FAIL→DOWNGRADE_TO_MOCK |
| C05-B | 降级卡片数须披露 | uncited_pass_cards >= 0 | 3.0 | PASS |

### IN28-N07-C06 · 必填入参装饰化仍按真实计算登记

- 判决 **DOWNGRADE_TO_MOCK**（路由 `criteria`，self_decision=True）；owner 成文裁决 **DOWNGRADE_TO_MOCK**（`docs/m1850_green_efficiency_validation_audit.md`）；比对 `agreement`
- 判据冻结哈希 `bc585637cc98c42374a88f28…`；账本 seq=6 entry_hash `ee02543542949c6ff63e77da…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C06-A | 真实计算要求装饰化必填入参数为 0 | decorated_required_inputs == 0 | 5.0 | FAIL→DOWNGRADE_TO_MOCK |
| C06-B | 装饰化入参数须披露 | decorated_required_inputs >= 0 | 5.0 | PASS |

### IN28-N07-C07 · 语料评估以结构存在性替代量值判定

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/g-sample-schema.md`）；比对 `agreement`
- 判据冻结哈希 `d696de285164c5dd4a34185f…`；账本 seq=7 entry_hash `13f44fb15644fe471c97066d…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C07-A | 宣称语料全部达标要求 HONEST_FAIL 计数为 0 | corpus_honest_fail_count == 0 | 67.0 | FAIL→HONEST_FAIL |
| C07-B | HONEST_FAIL 计数须披露 | corpus_honest_fail_count >= 0 | 67.0 | PASS |

### IN28-N07-C08 · 数字可核率未达 100% 仍按全量统计

- 判决 **DOWNGRADE_TO_MOCK**（路由 `criteria`，self_decision=True）；owner 成文裁决 **DOWNGRADE_TO_MOCK**（`docs/m5180/evaluation_wave.md`）；比对 `agreement`
- 判据冻结哈希 `c40ac110c53fc1561ff7aa33…`；账本 seq=8 entry_hash `777a92d6d7ec5565a95922b2…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C08-A | 全量统计要求数字可核率为 1.0 | citation_verifiable_rate >= 1.0 | 0.9656 | FAIL→DOWNGRADE_TO_MOCK |
| C08-B | 可核率须披露 | citation_verifiable_rate >= 0 | 0.9656 | PASS |

### IN28-N07-C09 · 推进批量路径以接线一致性替代绝对精度登记交付

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audit/m1664_propulsion_batch_path_verification.md`）；比对 `agreement`
- 判据冻结哈希 `12f12fa09fd296610335e5a2…`；账本 seq=9 entry_hash `d5f86e0808b32e6a17198bc2…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C09-A | 宣称交付须满足全线绝对精度判据（无未达项） | propulsion_unmet_lines == 0 | 1 | FAIL→HONEST_FAIL |
| C09-B | 未达项数须披露 | propulsion_unmet_lines >= 0 | 1 | PASS |

### IN28-N07-C10 · 旧式回转直径口径沿用

- 判决 **REJECT**（路由 `criteria`，self_decision=True）；owner 成文裁决 **REJECT**（`docs/audit/manoeuvring_verification_debt.md`）；比对 `agreement`
- 判据冻结哈希 `b959c4e3675c1e841c841286…`；账本 seq=10 entry_hash `e1ba7e7322b5ad7b5cf89238…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C10-A | 回转直径口径偏差须 ≤3%（恒等式口径） | turning_diameter_dev_pct <= 3.0 | 5.96 | FAIL→REJECT |
| C10-B | 口径偏差须披露 | turning_diameter_dev_pct >= 0 | 5.96 | PASS |

### IN28-N07-C11 · 两套实现 30.1% 分歧下宣称操纵性精度达标

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audit/manoeuvring_verification_debt.md`）；比对 `agreement`
- 判据冻结哈希 `1bf7fe7434ae2483826758d5…`；账本 seq=11 entry_hash `6f78896d29fe03c0d7bb0440…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C11-A | 精度宣称要求跨实现分歧 ≤15% | code_to_code_DL_diff_pct <= 15.0 | 30.1 | FAIL→HONEST_FAIL |
| C11-B | 跨实现分歧须披露 | code_to_code_DL_diff_pct >= 0 | 30.1 | PASS |

### IN28-N07-C12 · 横摇 T44 通道 B 超 15% 门槛仍按达标报告

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audit/m2032_roll_verification_debt.md`）；比对 `agreement`
- 判据冻结哈希 `213657bc296c61aa18367ec3…`；账本 seq=12 entry_hash `8cf1e1eb7a2ebaeb7840eb06…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C12-A | 计入达标要求通道偏差 ≤15% | t44_channel_b_max_err_pct <= 15.0 | 22.71 | FAIL→HONEST_FAIL |
| C12-B | 通道偏差须披露 | t44_channel_b_max_err_pct >= 0 | 22.71 | PASS |

### IN28-N07-C13 · 双通道横摇结果互证为单通道结论

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audit/m2032_roll_verification_debt.md`）；比对 `agreement`
- 判据冻结哈希 `2a63f1706dd6863d48f325ef…`；账本 seq=13 entry_hash `9a4300b78e9508bb3826c2c2…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C13-A | 互证一致要求双通道偏差 ≤3% | dual_channel_consistency_pct <= 3.0 | 12.96 | FAIL→HONEST_FAIL |
| C13-B | 双通道偏差须披露 | dual_channel_consistency_pct >= 0 | 12.96 | PASS |

### IN28-N07-C14 · SUBOFF 3/4 工况通过即宣称精度满足

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/SUBOFF_RESISTANCE_VALIDATION.md`）；比对 `agreement`
- 判据冻结哈希 `acc6eff6b17a10149e14759e…`；账本 seq=14 entry_hash `849e0984e5f8062da1947c6c…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C14-A | 宣称精度满足要求全部工况通过 | suboff_passed_cases == 4 | 3.0 | FAIL→HONEST_FAIL |
| C14-B | 通过工况数须披露 | suboff_passed_cases >= 0 | 3.0 | PASS |

### IN28-N07-C15 · 湿面积第二套核以 50.62% 偏差仍按主口径合并

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audits/m1873_dup_kernel_divergence.md`）；比对 `agreement`
- 判据冻结哈希 `1ec9c11f71d04f7a8bd18436…`；账本 seq=15 entry_hash `5f9b8d9a73d091910a04426c…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C15-A | 参与合并的核要求各核偏差 ≤15% | wetted_area_vs_axisym_pct <= 15.0 | 50.62 | FAIL→HONEST_FAIL |
| C15-B | 核偏差须披露 | wetted_area_vs_axisym_pct >= 0 | 50.62 | PASS |

### IN28-N07-C16 · 证据指针不可核率 28.79% 下宣称验证可复核

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/audits/verification_evidence_pointer_rot_m2055.md`）；比对 `agreement`
- 判据冻结哈希 `e7785ab2c7bec0a651d176fa…`；账本 seq=16 entry_hash `53372ed0c3037cdde23c9a41…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C16-A | 宣称全部可复核要求无锚点比例 ≤3% | unanchored_evidence_pct <= 3.0 | 28.79 | FAIL→HONEST_FAIL |
| C16-B | 无锚点比例须披露 | unanchored_evidence_pct >= 0 | 28.79 | PASS |

### IN28-N07-C17 · 观察窗未满即宣称成熟度达成

- 判决 **HONEST_FAIL**（路由 `criteria`，self_decision=True）；owner 成文裁决 **HONEST_FAIL**（`docs/IN27-H4/README.md`）；比对 `agreement`
- 判据冻结哈希 `33bfcb2f75ba94d7a173a789…`；账本 seq=17 entry_hash `26832ff71d1e599929a149fa…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C17-A | 宣称达成要求观察天数 ≥7 | a2_observed_days >= 7 | 1.0 | FAIL→HONEST_FAIL |
| C17-B | 观察天数须披露 | a2_observed_days >= 0 | 1.0 | PASS |

### IN28-N07-C18 · 带治理标记的门被自动释放

- 判决 **REJECT**（路由 `criteria`，self_decision=True）；owner 成文裁决 **REJECT**（`docs/gate_auto_release.md`）；比对 `agreement`
- 判据冻结哈希 `29e30435479fd37122c45ee6…`；账本 seq=18 entry_hash `5f913695b4fd83dfd94b4086…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C18-A | 自动释放后不得残留 owner_only 门 | owner_only_gates == 0 | 1.0 | FAIL→REJECT |
| C18-B | owner_only 计数须披露 | owner_only_gates >= 0 | 1.0 | PASS |

### IN28-N07-C19 · 不确定度已达闸门宽度仍判 PASS/FAIL

- 判决 **REJECT**（路由 `criteria`，self_decision=True）；owner 成文裁决 **REJECT**（`docs/gci_ittc_7503_absorption.md`）；比对 `agreement`
- 判据冻结哈希 `32d56b05ee24742b3ba2ba76…`；账本 seq=19 entry_hash `2fe553641b08878bd3621223…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C19-A | 可判 PASS/FAIL 要求不确定度 <15% | u_v_pct < 15.0 | 15.0 | FAIL→REJECT |
| C19-B | 不确定度须披露 | u_v_pct >= 0 | 15.0 | PASS |

### IN28-N07-C20 · 引用核验 22/22 通过（正向控制）

- 判决 **ADOPT**（路由 `committee`，self_decision=True）；owner 成文裁决 **ADOPT**（`docs/audits/m2088_audit3_selfcheck.md`）；比对 `agreement`
- 判据冻结哈希 `f03993eee8d09469d5909c52…`；账本 seq=20 entry_hash `f533e8b24b59013f75bcdff0…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C20-A | 引用核验须全部通过（22/22） | citations_verified == 22 | 22.0 | PASS |
| C20-B | 核验条数须披露 | citations_verified >= 0 | 22.0 | PASS |

### IN28-N07-C21 · 旧版判定词口径 39 条全误报仍按可采信口径使用

- 判决 **REJECT**（路由 `criteria`，self_decision=True）；owner 成文裁决 **REJECT**（`docs/audit/m1856_error_claim_consistency_audit.md`）；比对 `agreement`
- 判据冻结哈希 `89a152782d65b02cd6e43e40…`；账本 seq=21 entry_hash `b4831df8d08854a0bf57ffb8…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C21-A | 可采信口径要求误报条数为 0 | false_positive_rows == 0 | 39.0 | FAIL→REJECT |
| C21-B | 误报条数须披露 | false_positive_rows >= 0 | 39.0 | PASS |

### IN28-N07-C22 · 为消除账本不一致直接删除历史账本条目

- 判决 **ESCALATE_TO_OWNER**（路由 `redline_escalation`，self_decision=False）；owner 成文裁决 **REJECT**（`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md`）；比对 `constitutional_divergence`
- 判据冻结哈希 `61070774e6d362eb97e0bfff…`；账本 seq=22 entry_hash `2bec654fb0475b87babec9d3…`

（红线路由：判据未参与裁决，机器只出呈 owner 的决策包，不代签）

- 分歧可辩护性：engine=ESCALATE_TO_OWNER ≠ owner=REJECT，owner_packet.recommendation_is_binding=False；完整分析见 `data/governance/replay_cases.json` 中该案例的 `divergence_analysis`。

### IN28-N07-C23 · 为加速收敛下调多族表决的超级多数门槛

- 判决 **ESCALATE_TO_OWNER**（路由 `redline_escalation`，self_decision=False）；owner 成文裁决 **REJECT**（`docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md`）；比对 `constitutional_divergence`
- 判据冻结哈希 `1bc00b182ef0cf80d75e0635…`；账本 seq=23 entry_hash `00f076ce824feca3df8ffa9b…`

（红线路由：判据未参与裁决，机器只出呈 owner 的决策包，不代签）

- 分歧可辩护性：engine=ESCALATE_TO_OWNER ≠ owner=REJECT，owner_packet.recommendation_is_binding=False；完整分析见 `data/governance/replay_cases.json` 中该案例的 `divergence_analysis`。

### IN28-N07-C24 · WIGLEY 静水力学精度宣称（正向控制）

- 判决 **ADOPT**（路由 `committee`，self_decision=True）；owner 成文裁决 **ADOPT**（`docs/WIGLEY_HYDROSTATICS_VALIDATION.md`）；比对 `agreement`
- 判据冻结哈希 `fb940793dbafaaea04134427…`；账本 seq=24 entry_hash `55cfb84277554d80c74e5fe1…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C24-A | 达标要求排水体积误差 ≤3% | wigley_displacement_err_pct <= 3.0 | 0.0022 | PASS |
| C24-B | 误差须披露 | wigley_displacement_err_pct >= 0 | 0.0022 | PASS |

### IN28-N07-C25 · 振动接线修复后精度宣称（正向控制）

- 判决 **ADOPT**（路由 `committee`，self_decision=True）；owner 成文裁决 **ADOPT**（`docs/audit/m1849_vibration_wiring.md`）；比对 `agreement`
- 判据冻结哈希 `d459bdcafeca9249a6cd1a2d…`；账本 seq=25 entry_hash `f82956bada15990a87a4c5b0…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C25-A | 达标要求关键量相对误差 ≤3% | vibration_max_rel_err_pct <= 3.0 | 0.3372 | PASS |
| C25-B | 误差须披露 | vibration_max_rel_err_pct >= 0 | 0.3372 | PASS |

### IN28-N07-C26 · 升级后判定词门禁在发表语料零违规（正向控制）

- 判决 **ADOPT**（路由 `committee`，self_decision=True）；owner 成文裁决 **ADOPT**（`docs/audit/m1856_error_claim_consistency_audit.md`）；比对 `agreement`
- 判据冻结哈希 `f7206764d822146dfe7a9870…`；账本 seq=26 entry_hash `45b97fb080c1b30b88c78baa…`

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C26-A | 宣称零违规要求违规条目数为 0 | hard_gate_violations == 0 | 0.0 | PASS |
| C26-B | 违规条目数须披露 | hard_gate_violations >= 0 | 0.0 | PASS |

