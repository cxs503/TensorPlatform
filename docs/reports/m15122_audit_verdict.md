# m15122（KPI-GATE-100）交差核验判级：L2（board-audit，本单 m15168）

> 对象：mission 15122「KPI-GATE-100 · 门禁覆盖 100%」（TensorPlatform，已 succeeded）。
> 本单是**交差核验**，不是重做。判级落表 `tensor_maturity_verdicts` id=234
> （repo=TensorPlatform, scope='m15122 KPI-GATE-100', level=2, verdict_by='board-audit',
> gate_state=pending → 由 30 min 周期 maturity_gate 自动裁定；本单已用同一 JUDGE_SCHEME 预演：
> gate_level=2，reasons=[]）。
> 一切数字来自 `artifacts/audit_m15122/{triad_verification,db_ground_truth,repro_out/*}.json`，核验器
> `verify_m15122.py` 一键复算（零手抄）。

## ① 案例介绍（核验对象与口径）

KPI-GATE-100 的"算例"不是物理场，而是平台的**产出线门禁覆盖率**：5 条产出线（PR/报告/论文/基准/知识）
每条须有"能失败的机器门"。原单定义主值 = 严格口径 `100×|{G1∧G2∧G3_prod}|/5`，
次值 = 结构口径 `100×|{G1∧G2∧(G3_prod∨G3_neg)}|/5`，target=100%。

核验侧四判据（先定义后测量）：

| 判据 | 含义 | 判定方式 |
| --- | --- | --- |
| T1 | run_artifacts 真存在 | 9 个声明工件：文件存在 ∧ 非空 ∧ git 追踪 ∧ sha256 |
| T2 | benchmark ref vs got | 登记基准 `tensor_benchmark_cases WHERE anchor_slug='KPI-GATE-100'` + KPI target vs got |
| T3 | repro_cmd 可执行 | 实跑 rc ∧ 头条逐字段一致 ∧ G1–G3 布尔一致 |
| T4 | 报告六段 | `tensor_reports` 正文含 ①②③④⑤⑥ 且 figures≥1 |

## ② 怎么做的（方法/工具链）

1. 读原单产物：`/root/tp-agent/work/m15122/artifacts/kpi_gate_100/`（result.json / 计量器 / 决算件 / 报告 / 2 图）+ DB 记录（mission 15122、报告 id=14246）。
2. **独立复跑**原单计量器（不改原工作树、输出落本单）：`python3 artifacts/kpi_gate_100/measure_gate_coverage.py --out-dir artifacts/audit_m15122/repro_out` → rc=0。
3. 活库直查（同一 PostgreSQL 实例）取真值并与原单记录值逐项对差 → 漂移量化。
4. 登记基准查表（锚点算例/容差）、报告六段与图计数、**集成调用方 grep**（判 L3）、commit 归属（是否入 main 树）。
5. 判级后用平台自己的 `/root/tp-agent/maturity_gate.py` JUDGE_SCHEME 预演机器复核，再落表。

## ③ 计算结果（三件套逐项核验）

**T1 run_artifacts = True**：9/9 工件存在非空且 git 追踪；
但 result.json **无具名 `run_artifacts` 键**（named_key_present=False）→ 形式欠缺、事实满足。

**T2 benchmark**：锚点登记算例 = **0 行**（登记裁判为空）→ "got 是否在容差内" 不能由登记基准裁决。
唯一可用裁判 = KPI 自身 target：target=100%、got(严格)=60%、
got(结构)=80%、**gap=40pp → KPI 未达标（误差 40%，远超 15% 不可接受线）**。
原单引用的内部决算（12 行船体阻力正典）是"基准门在拒绝"的证据，非本锚点 ref/got 对：

| case | ref_ct | got_ct | err | verdict |
| --- | --- | --- | --- | --- |
| DTMB5415-01 | 0.0045 | 0.004343 | -3.4889% | WITHHELD |
| DTMB5415-02 | 0.0038 | 0.004687 | 23.3421% | WITHHELD |
| DTMB5415-03 | 0.0035 | 0.005374 | 53.5429% | WITHHELD |
| DTMB5415-04 | 0.0038 | 0.00606 | 59.4737% | WITHHELD |
| KVLCC2-01 | 0.0028 | 0.002886 | 3.0714% | WITHHELD |
| KVLCC2-02 | 0.003 | 0.002913 | -2.9% | WITHHELD |
| KVLCC2-03 | 0.0031 | 0.002927 | -5.5806% | WITHHELD |
| KVLCC2-04 | 0.0032 | 0.002941 | -8.0938% | WITHHELD |
| SUBOFF-01 | 0.0038 | 0.006418 | 68.8947% | FAIL_OVER_15 |
| SUBOFF-02 | 0.004 | 0.006724 | 68.1% | FAIL_OVER_15 |
| SUBOFF-03 | 0.0042 | 0.00703 | 67.381% | FAIL_OVER_15 |
| SUBOFF-04 | 0.0044 | 0.007336 | 66.7273% | FAIL_OVER_15 |

**T3 repro_cmd = True（rc=0）**：头条逐字段一致={'current': True, 'current_structural': True, 'target': True, 'gap': True, 'covered_strict': True, 'covered_structural': True, 'no_gate_lines': True}、G1–G3 布尔一致=True；
测试探针 4 项 rc = 报告 0 / 基准 0 / PR 0 / 知识 1（知识 1 项预存 FAILED，原单已如实记录）。
活库漂移（复跑间隔 ~25 min）：

| 产出线 | 原单分母 | 复跑分母 | Δ | G4 变化 |
| --- | --- | --- | --- | --- |
| PR | 6268 | 6284 | +16 | 0.85434 → 0.854392 |
| 报告 | 9429 | 9445 | +16 | 0.003606 → 0.0036 |
| 基准 | 12 | 12 | +0 | 1.0 → 1.0 |
| 知识 | 29 | 30 | +1 | 0.0 → 0.0 |
| 论文 | 0 | 0 | +0 | None → None |

**T4 报告六段 = True**（id=14246，figures=2，qa_status=pending，honest-fail 声明=True）。

原单矩阵（复跑逐一相符）：

| 产出线 | G1 | G2 | G3 生产 | G3 负控 | G4 |
| --- | --- | --- | --- | --- | --- |
| PR | True | True | True | True | 5355/6268 = 85.43% |
| 报告 | True | True | True | False | 34/9429 = 0.36% |
| 基准 | True | True | True | True | 12/12 = 100.00% |
| 知识 | True | True | False | True | 0/29 = 0.00% |
| 论文 | False | False | False | False | 0/0 = 0.00% |

## ④ 结果图

图 1（target vs got）、图 2（线×判据矩阵 + G4）、图 3（漂移）。
说明：本单对象是治理计量不是流场 → 无场量云图，按纪律用可复算统计图替代并显式声明原因（图为英文标注：
宿主无 CJK 字体，`matplotlib` 中文会渲染为方框，非数据问题）。

## ⑤ 注意点（发现清单）

- **F1 KPI 未达标**：严格 60% / 结构 80% vs target 100%，缺口 40pp；`succeeded` 只等于"计量交付合格"，不等于"门禁覆盖 100% 达成"。
- **F2 基准裁判为空**：`tensor_benchmark_cases` 对 KPI-GATE-100 零登记（全表 13 行覆盖 13 个其它锚点）；报告 §⑥ 的 0.00% 偏差是**同一 SQL 自比对（自洽恒等式）**，不能当独立验证。
- **F3 产物无具名 run_artifacts 键**：三件套纪律在产物自身上未成键（实体齐全）。
- **F4 复算不可移植**：`dsn()` 依赖 `DATABASE_URL` 或本机 :8010 uvicorn 进程 → 干净检出即 SystemExit；G4 分母随线上产出增长（9429→9445 报告、6268→6287 PR 流水、29→30 知识），只有布尔/口径值可复现。
- **F5 PR 线 G3 间接**：关闭未合 13 个由 note 文本反推，门无独立判决字段；报告线缺 G3 负控测试（矩阵如实显示 False）。
- **F6 原单交付体缺 gate 块**：`server_quality_audit={gate_valid:false, key_present:false, gate_missing_warning:true}` → 机器门无锚可核。
- **F7 工件未入 main 树**：commit 31f42275f 仅在 `refs/heads/agent/m15122`，`ls-tree -r HEAD | grep -c kpi_gate_100 = 0` → 干净检出主线复跑不到本单工件。

正面复核（主张成立项）：commit 31f42275f 仅新增 10 文件、0 处改既有代码（"只计量不改动"成立）；
`tensor_papers.py` 门判据命中 0（论文线无门成立）；`knowledge_base` 全 30 行 status 全 active（知识线无生产拒绝实证成立）；
`tensor_reports.rejected`=7（报告门"能失败"成立）。

## ⑥ 误差判定（逐项显式回答是否 <3%）

| 关键量 | 原单值 | 本单独立复跑/直查 | 相对偏差 | <3%？ |
| --- | --- | --- | --- | --- |
| KPI 严格口径 | 60% | 60% | 0.00% | 是 |
| KPI 结构口径 | 80% | 80% | 0.00% | 是 |
| 报告 gated 计数 | 34 | 34 | 0.00% | 是 |
| 报告总数（G4 分母） | 9429 | 9445 | 0.17% | 是（活库漂移） |
| 知识库行数 | 29 | 30 | 3.45% | **否**（活库新增 1 行，分母极小故百分比放大；原始计数差=1 行，非计算误差） |
| **KPI vs target** | target 100% | got 60% | **40.00%** | **否（KPI 未达标，honest FAIL）** |

结论：计数类关键量逐项恒等（偏差 0.00%）；两处分母因线上产出增长而漂移（报告 +0.17% 达标；知识 +1 行 = 3.45% 超 3%，但属新增行而非计算误差，已如实标注不粉饰）。**KPI 本身距 target 差 40pp，<3% 明确不成立 → honest FAIL。**（本段为修订版，替换 id=14272 中知识行的一处『是』误判）
判级 = **L2**：真代码+入口+可复跑工件齐备（机器预演 gate_level=2, reasons=[]）；L3 缺集成调用方、L4 缺登记基准与外部真值 → 不得上抬。
