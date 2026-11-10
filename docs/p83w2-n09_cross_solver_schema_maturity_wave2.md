# p83w2-n09 跨求解器验证框架成熟化（wave 2）：可比较 · 可追溯 · 可复现

> 节点 `p83w2-n09-cross-solver-schema-maturity` · mission `9262` · 仓 TensorPlatform
> 运行产物：`artifacts/xsolver/acceptance.json`（本文件所有数字的来源）
> 图：`artifacts/xsolver/wave2/fig4_criteria_margins.png`
> 结论一句话：**七条判据全部 PASS**，其中 wave 2 的两项增量是「探针溯源跟随代码而非输出路径」与
> 「用 content_digest（剔除墙钟）而非 corpus_digest 作为可复现门禁」。

## ① 案例介绍（物理背景 / 几何 / 参数）

跨求解器互裁的对象是**生态内已显式给出验证结论的算例**（verified 案例），本波 114 个 case 来自
`config/xsolver/corpus_sources.json` 声明的 glob，覆盖 LBM / BEM / CAD / FVM 等族；其中 LBM 族
有 16 条工况记录了质量守恒残差，12
条超过 1e-6（已标记 `conservation_fail`，被守恒门禁排除、不参与互裁；名单见同工件 C2 段的
`mass_exceeding_tolerance`）。

本波新增的**物理量测**不是引用来的，而是在本机跑 LBM 核测得的：周期箱 + 均匀平衡态 + 小周期扰动下，
streaming 是总体置换、BGK 碰撞保持零阶/一阶矩，因此质量与动量的参考答案就是"零漂移"，任何差距属于
被测算子本身。探针参数（grid / τ / 速度 / 各视野）见 `artifacts/xsolver/probe/lb_conservation_probe.json`
的 `setup` 段；认证视野 100 步。

## ② 怎么做的（方法 / 流程 / 工具链）

四段机械流程（代码只含机制，域词汇在 `config/xsolver/*.json`）：

1. **normalize**：任意形状工件 → 统一 input/output 记录（geometry / physics / solver / provenance；
   primary_quantities / conservation_residuals / convergence）。
2. **conservation**：质量/动量/能量残差门禁，超限即 `conservation_fail` 并从比较中剔除。
3. **adjudicate**：按"回答同一物理问题"分组，同量同名比较，差异必须归因（term coverage / normalization /
   discretisation / formulation），未归因即 FAIL。
4. **dag_consumer**：把互裁结论投影成战役 DAG 节点判据文档（`NodeVerdictUpdate`），判决携带数字。

wave 2 的两处改动：

* **探针溯源**：`to_case_record` 原先从 `artifact_path.parents[3]` 推断"本仓检出"，即**从产物被写到哪
  推断产它的是哪份代码**。任何 `--out` 指到检出之外的运行都会得到 `git_commit: null`——一个测出来的
  残差无法归属到任何代码，而 C5 只是从 114/114 掉到 113/114，不报错。改为 `FRAME_REPO = Path(__file__).parents[3]`：
  **溯源跟随代码，不跟随输出路径**；同时几何块内的 `source_path` 改为常量标记，使 `input_digest` 也不再随
  `--out` 漂移。
* **可复现指纹**：`corpus_digest = sha256(整个 corpus 文档)` 内含 `harvested_utc`，每次运行都变，
  因而**不能**回答"这是不是同一次采集"。新增 `content_fingerprint`：逐 case 取
  `(case_id, source_sha256, git_commit, solver_version, input_digest, condition)` + 解析配置各文件的 sha256，
  剔除墙钟与主机路径；并新增判据 **C7**：同一次运行内二次采集，比较 content_digest。
  `corpus_digest` 仍保留（字节同一性），与 C7 并列报告，正是为了展示它不是门禁。

## ③ 计算结果（数字表，由 `scripts/xsolver_wave2_report.py` 从工件注入）

| 判据 | 状态 | 实测（摘自 acceptance.json） |
|---|---|---|
| C1_schema_coverage | PASS | 109/114 cases parse geometry (95.6%), 112/114 parse output quantities (98.2%) |
| C2_conservation_residuals | PASS | gate calibrated at mass 1e-06 / momentum 1e-04 (LB); measured kernel probe at horizon 100 steps: mass 7.105e-15, momentum 7.216e-15 (OK), provenance verified (/root/tensor-ecosystem/TensorLBM/src/tensorlbm/__init__.py);  |
| C3_cross_solver_adjudication | PASS | 3 arms, 9 C_T pairs, max relative difference 100.00%, attribution ['unattributed', 'term_coverage', 'term_absent', 'unresolved_after_alignment', 'normalization'] |
| C4_negative_controls | PASS | deleting primary_quantities raises a schema error; forcing mass residual to 1.0 marks conservation_fail and removes the arm; a backend outside the declared repo is refused instead of silently imported |
| C5_traceability | PASS | 114/114 cases carry input_digest + solver_version + git_commit |
| C6_dag_consumption | PASS | verdict document validated and projected onto NodeVerdictUpdate: reproduction_verdict=fail |
| C7_reproducibility | PASS | 113/113 cases re-harvested, content_digest identical (df46d8aca690 vs df46d8aca690), 0 case(s) changed, 0 appeared, 0 vanished; byte digest differs (wall clock) |

关键计数：corpus 114 个 case（files_seen=174，
not_verified=71，linked=3，matrix=9）；
geometry 109/114 = 95.6%；
primary_quantities 112/114 = 98.2%；
可追溯 114/114 = 100.0%（distinct commits 6）。

守恒探针（实测，非引用）：mass 7.105e-15 / momentum 7.216e-15
@ horizon 100 步；门禁 mass ≤ 1e-06、
momentum ≤ 1e-04；每步漂移
7.105e-17，1e-6 将在约 1.407e+10 步被跨越
（视野是结论的一部分，故与数字同存）。探针来源已核验：`/root/tensor-ecosystem/TensorLBM/src/tensorlbm/__init__.py`（sha256 b867879b486b0ae8123ada0b960503d1e8752f459c97b5f4b2d46540dde2c981）。

互裁（['TensorBEM', 'TensorFVM', 'TensorLBM'] 臂 / 9 个 C_T 对）：max relative difference
100.00%，归因类别 ['unattributed', 'term_coverage', 'term_absent', 'unresolved_after_alignment', 'normalization']，findings 15；
DAG 判决 `{'block_reason': 'cross-solver verdict not established (attribution=unattributed,term_coverage,term_absent,unresolved_after_alignment,normalization); conservation_gate: no arm records a conservation residual | cross_solver_agreement: max relative difference on C_T over 9 pair(s) | definition_attribution: unattributed: C_T differs by 99.0% between TensorFVM and TensorBEM and no definitional explanation was found', 'reproduction_verdict': 'fail'}`。

可复现（C7）：content_digest df46d8aca690b6d1… == df46d8aca690b6d1…（稳定
True）；corpus_digest b6bafe6174ad… vs
201ed87a803e…（稳定 False）；case 变化 0，
新增 0，消失 0；frame 配置指纹
929a6a3c29c3ad91…（采集与解析配置一同入指纹，否则"同一份工件换了解析规则"会被
误判为同一结果）。

## ④ 结果图

`artifacts/xsolver/wave2/fig4_criteria_margins.png`：左=覆盖率与可追溯率对阈值；中=探针残差对门禁（对数轴）；
右=content_digest（稳定）与 corpus_digest（随墙钟变化）的并排证据。图为
`scripts/xsolver_wave2_report.py` 从 `acceptance.json` 直接生成，无手抄数字。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **溯源必须跟随代码**：从产物路径反推检出在"产物与代码不同仓/不同深度"时静默失效，且失效方向是
   "少一条 provenance"而非报错——这类缺陷只能靠"同一测量、两个输出路径"的对照测试抓住。
2. **字节摘要不是复现指纹**：任何内嵌墙钟/主机路径的摘要都会在一次接一次的运行中漂移；把它当门禁会
   造成"每次都不复现"或"永远通过"两种假结论之一。本波的做法是双摘要并存：内容摘要做门禁，字节摘要
   做同一性记录。
3. **守恒失败先于精度讨论**：LBM 族 12 条臂质量残差超限（最大 0.03215），
   它们被排除在互裁之外——不在残差可接受的前提下比较 C_T 差异，等于比较两个不可信的舍入。
4. **探针的适用范围**：周期箱 + 等温模型，因此只主张质量/动量；能量残差显式记为 undefined（无能量方程），
   壁面动量交换不在主张内。
5. **C7 的边界**：它证明"同一环境、同一源字节、同一解析配置 ⇒ 同一指纹"，不证明"跨主机也一致"
   （绝对路径与解析结果中的度量单位仍需由 schema 约束）；也不覆盖算子浮点复现性，那是 C2 探针的职责。
6. **契约漂移**：本波发现 API 的 `NodeVerdictUpdate` 已扩展 `metrics` / `metric_name` 两个可选字段，
   而框架侧的镜像 schema 仍停在两字段——已同步镜像，并把"整体回环相等"的断言收窄为"我们发出的字段
   经校验后不变"，因为可选字段的默认值是 API 侧权力，不是我们可以在镜像里凭空补的。

## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答是否 < 3%）

| 量 | 基准 | 实测 | 相对误差 | < 3%？ |
|---|---|---|---|---|
| 质量守恒残差（周期箱，100 步） | 0（解析：streaming 置换 + BGK 保矩） | 7.105e-15 | 绝对残差 7.105e-15（相对参考质量） | 是（远低于 1e-6 门禁） |
| 动量守恒残差（同上） | 0 | 7.216e-15 | 绝对残差 7.216e-15 | 是（远低于 1e-4 门禁） |
| 几何 Cb / S_wet 闭合 | 三臂各自声明（CAD 解析解为参照） | 见 `artifacts/xsolver/adjudication.json` groups | 见归因 finding | 由互裁报告逐条给出，本表不重复抄写 |
| 跨求解器 C_T 差异 | 无共同定义基准 | max 100.00% | — | **否（100%）**，且已归因为口径差异（C_F/C_W 项覆盖不同），不是数值误差；结论是"先统一口径，再谈精度" |

诚实声明：C_T 的 100% 差异**不满足**"< 3%"，本波**不主张**跨求解器精度一致性；本波主张的是口径与
证据链的机械一致性（七条判据 PASS）。把差异压到 3% 以内需要各求解器补齐同一物理项，属于后续节点。

## 审计链

* 判据：`artifacts/xsolver/acceptance.json`（node/mission 由 `config/xsolver/acceptance.json` 注入）
* 语料：`artifacts/xsolver/corpus/corpus.json` + `index.json`（逐 case sha256 / commit / input_digest /
  content_digest）
* 互裁：`artifacts/xsolver/adjudication.json`；DAG 判决：`artifacts/xsolver/verdicts/*.json`
* 探针：`artifacts/xsolver/probe/lb_conservation_probe.json`
* 测试：`tests/xsolver/test_xsolver_wave2_reproducibility.py`（14 条，基线上 13 失败）
