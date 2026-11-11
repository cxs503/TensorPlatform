# 判据活力门（criteria vitality gate）：判据自身的变异注入体检

> 任务 `P158W3-N07` · 机制 `ops/taskplane/criteria_vitality.py`（sha256 `52137c3457f50996…`） · 演示/产物生成器 `ops/taskplane/criteria_vitality_demo.py` · 测试 `tests/test_criteria_vitality.py` · 生成于 `2026-11-10T23:26:37+00:00`。
> 本文件由 `ops/taskplane/criteria_vitality_demo.py::render_docs` 从
> `artifacts/criteria_vitality/report.json` 渲染，文中每个数字都有工件出处，禁止手抄。

## 1 问题：判据可以是「恒真装饰」

门自称 REAL，靠的是判据能分辨合规与不合规。但一条 `observed >= threshold` 的判据
有三种静默失效，任何一种都不会让门变红：

| 失效 | 现象 | 为什么现有门看不出来 |
|---|---|---|
| 阈值悬空 | 阈值写 10，样本观测全在 100 以上；阈值改成 11 或 9 判决一模一样 | 判据仍然「全绿」，因为它确实没判错任何样本 |
| 观测式可平移 | 观测式整体 +10% 常数偏移，判决不变 | 偏移不改变通过/不通过 |
| 单位错位 | 阈值按 mm 写、按 m 解读（差 1000 倍） | 只影响量纲，不影响全绿结论 |

三者共同点：**判据对自己参数的扰动不敏感**。判据参数是判据的全部信息量——参数随便改
都不改变判决 ⇒ 参数没被证据约束 ⇒ 判据退化成常量，门退化成装饰。本机制把它变成机器
可裁的指标：**变异注入 + 杀死率 MS**。

## 2 机制设计（语义契约先于实现）

1. **判据 = 验收条件**：`op`/`threshold` 描述「必须成立」的条件（例：`observed >= 0.80`
   表示该量不得低于 0.80），不成立即判 FAIL。观测式是 JSON 指针，取不到值一律算
   *输入错误*（`VitalityInputError`），绝不被折算成判据不合格。
2. **体检样本 = 输入 + 预注册判决**：每个样本带 `expect: PASS/FAIL`，判决来自样本
   构造方式（干净实现 → PASS；注入了已知缺陷的实现 → FAIL），**不来自被体检的判据**。
   这条是机制可信度的地基：若判决由判据自己算，MS 恒等于 1。
3. **变异体只扰动判据参数**：阈值 ±10%、比较方向翻转、观测式常数偏移、单位换算错误。
4. **预注册**：变异库在**执行之前**落盘，每个变异体带规格指纹 `digest`，库带门定义指纹
   `gate_sha256`；执行引擎只吃库里的变异体，门定义或变异载荷被改即拒跑（exit 3）。
5. **杀死率**：变异体在样本池上给出与预注册判决不一致的判决 ⇒ KILLED；全部一致 ⇒
   SURVIVED（该扰动在此样本分辨率下不改变判决）。`MS = killed / total`，
   `MS >= 0.5` 达标，`gate_vitality = MS × 100`。
6. **前置条件（防「拿坏尺子量」）**：原始判据必须在全部样本上判对（accuracy = 1.0），
   否则记 `DEFECTIVE`、门封为 BLOCKED、不输出活力分数。
7. **三态裁决**：`VITAL`（覆盖达标 + MS ≥ 达标线 + 矩阵完整）/ `INERT`（MS 不足）
   / `BLOCKED`（判据自身判错、覆盖不足或矩阵不完整）。只有 VITAL 才 `real_claim_allowed`。

## 3 变异算子库

| 算子 | 语义 |
|---|---|
| `THRESHOLD_PLUS_10` | 阈值 x1.10 |
| `THRESHOLD_MINUS_10` | 阈值 x0.90 |
| `OP_FLIP` | 比较方向翻转（>= -> <=、== -> != ...） |
| `OBSERVED_OFFSET_PLUS` | 观测式常数偏移 +0.10*scale |
| `OBSERVED_OFFSET_MINUS` | 观测式常数偏移 -0.10*scale |
| `UNIT_ERROR` | 单位换算错误注入（阈值按邻级单位解读） |

每条判据生成 6 个变异体（A1 要求 ≥3 个 / ≥2 类算子），扰动幅度固定为 `relative_perturbation = 0.1`。

## 4 判据机裁（A1~A8）与实测

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| A1 变异体生成 | 每条判据 ≥3 个、≥2 类算子 | 最少 6 个 / 6 类 | PASS |
| A2 杀死率 | 设计门 MS ≥ 0.5 且 MS 不足者不得自报 REAL | 逐门 MS {'synthetic.alpha': 1.0, 'synthetic.beta': 0.333333333333, 'synthetic.gamma': 0.666666666667} | PASS |
| A3 kill 矩阵完整性 | 判据 × 变异体逐格有裁决 | {'synthetic.alpha': {'alpha.ratio': 6, 'alpha.span': 6}, 'synthetic.beta': {'beta.drift': 6}, 'synthetic.gamma': {'gamma.flag': 6}} | PASS |
| A4 survived 清单 | 每个存活变异体有原因码 + 数值明细 | 存活 6 个，原因码 ['DEFECTIVE_BASE_MASKED', 'SLACK_EXCEEDS_PERTURBATION'] | PASS |
| A5 分数换算 | `gate_vitality == MS×100`（逐判据同） | {'synthetic.alpha': 100.0, 'synthetic.beta': 33.3333333333, 'synthetic.gamma': 66.6666666667} | PASS |
| A6 台账入账 | 写入 `vitality_score`；未体检能力封 REAL | 台账 artifacts/capability_ledger/capability_ledger.json：体检 1 / 未体检 21 / 可自报 REAL 1 | PASS |
| A7 零领域语义 | 模块/演示/测试经 `tools.evidence_domain_scan` 0 命中 | 扫描 3 文件 / 37 词条，命中 0 | PASS |
| A8 pytest | ≥15 例全绿 | 48 passed, 1 warning in 0.39s | PASS |

## 5 合成演示：三门三态（数字取自 `artifacts/criteria_vitality/run.json`）

| 门 | 判据 | 变异体 | 杀死 | 存活 | MS | gate_vitality | 裁决 | 可自报 REAL | 阻塞项 |
|---|---|---|---|---|---|---|---|---|---|
| `synthetic.alpha` | `alpha.ratio` | 6 | 6 | 0 | 1.000000 | 100.000000 | VITAL | 是 | - |
| `synthetic.alpha` | `alpha.span` | 6 | 6 | 0 | 1.000000 | 100.000000 | VITAL | 是 | - |
| `synthetic.beta` | `beta.drift` | 6 | 2 | 4 | 0.333333 | 33.333333 | INERT | 否 | 杀死率 MS=0.3333 < 达标线 0.5 |
| `synthetic.gamma` | `gamma.flag` | 6 | 4 | 2 | 0.666667 | 66.666667 | BLOCKED | 否 | 判据 gamma.flag 原始形态就判错 1 个预注册样本（accuracy=0.667）——判据自身不可用，活力分数无意义 |

存活原因码分布：['DEFECTIVE_BASE_MASKED', 'SLACK_EXCEEDS_PERTURBATION']；完整矩阵见 `artifacts/criteria_vitality/kill_matrix.md`，图为 `artifacts/criteria_vitality/figs/kill_matrix.png`。

## 6 台账入账语义

* `criteria_vitality.gates.<gate>`：门级 `verdict/ms/gate_vitality/real_claim_allowed/blockers/matrix_complete/overfit_suspect` + 逐判据 `vitality_score`；
* `criteria_vitality.capabilities.<cap>`：能力级 `vitality_score`（多门合并按`killed/total` 加权）、`real_claim_allowed`（所有映射门都必须 VITAL）、`status`；
* 未体检能力：`vitality_score = null`、`real_claim_allowed = false`、原因 「未体检：没有活力门体检记录，按机制不得自报 REAL」；
* 写入是**附加式**的：`capabilities[*].vitality_score / vitality_status / vitality_real_claim_allowed` 三个字段 + 一个顶层 `criteria_vitality` 段，其余内容逐字节不动（写入前后指纹比对，越界即中止），并沿用台账既有的 `indent=1` 序列化惯例（重复入账幂等）。

## 7 注意点与局限（honest）

* synthetic.alpha 的 MS=1.0：全部变异体被杀。过拟合诊断显示边界邻域样本占比 0.0213 （见 per-gate overfit_suspect）；MS=1.0 只是「在样本池分辨率下参数均被咬住」，不等于判据参数在更大样本上正确，仍需外部样本复核。
* synthetic.beta 的 MS=0.333 < 0.5：阈值悬空判据，按机制封 REAL——本节点把这条负面结果入账，而不是把 beta 从演示里删掉。
* synthetic.gamma 的判据在原始形态下就判错 g2（accuracy<1.0），任意变异体统计都无意义，机制按 BLOCKED 封门，不输出活力分数。
* 变异库预注册指纹只绑定「门定义 + 实现入口」，不绑定运行环境；跨机复核需同版本实现入口。
* MS 高只说明「判据参数在**这批样本**的分辨率下被咬住」，不证明判据语义正确；语义正确性由前置条件（原始判据 accuracy=1.0）与外部基准共同保证。
* 变异算子只覆盖参数级扰动（阈值/方向/偏移/单位）；**观测式本身写错**（取错量）不在覆盖范围，需要独立的语义审计。
* 预注册指纹绑定「门定义 + 实现入口引用」，不绑定运行环境与依赖版本；跨机复核需同版本实现。
* 本节点的真实能力能力台账条目（21 条）**全部为 UNEXAMINED**：本机制已就位，但「对哪条真实能力跑哪套判据」属领域侧注入，不在平台仓硬编码。

## 8 复现

```bash
python3 -m ops.taskplane.criteria_vitality_demo        # 出全部产物 + 入账 + 自跑 pytest
python3 -m pytest tests/test_criteria_vitality.py -q   # 机制验收
python3 -m ops.taskplane.criteria_vitality preregister --gate artifacts/criteria_vitality/gates.json --out /tmp/lib.json
python3 -m ops.taskplane.criteria_vitality run --gate artifacts/criteria_vitality/gates.json --library /tmp/lib.json --out /tmp/run.json --markdown /tmp/kill.md
python3 -m ops.taskplane.criteria_vitality check --run /tmp/run.json   # 0 全绿且全 VITAL / 2 有未达标门 / 3 输入错（本演示刻意含 INERT+BLOCKED 门，故为 2）
```

## 9 审计链

* 机制源码：`ops/taskplane/criteria_vitality.py` sha256 `52137c3457f50996515247d6d3b905e2d893622ce6a47a4d5d7a44b501f18711`
* 变异库：`artifacts/criteria_vitality/mutant_library.json`；运行记录：`artifacts/criteria_vitality/run.json`
* 报告：`artifacts/criteria_vitality/report.json`；台账段：`artifacts/criteria_vitality/capability_ledger_block.json`
* 生成时间：`2026-11-10T23:26:37+00:00`（同一轮：预注册 → 执行 → 图 → 入账 → 机裁 → pytest）
