# P187-W1-N09 — 平台能力治理三位一体：自动升账 · 跨求解器归因链 · 结构化样本库

- 任务：mission 9811 / 节点 P187-W1-N09（仓库 TensorPlatform，分支 agent/m9811）
- 生成时刻（程序化注入）：2026-11-10T14:57:38.153524+00:00
- 全判据结论：**ALL PASS**

## 0. 一句话结论

把「某个能力是真算法」这句自述，拆成三段可机器复核的算术：① 端点探针 + 声明判据 → 内容寻址证据链 → 追加式台账（REAL 只在全判据通过且链自校验通过时落笔，失败/篡改/报告哈希不符一律拒绝）；② 跨求解器偏差按 格式→输出定义→输入定义→离散化 四维伸缩分解，残余未解释量显式报数；③ 声明式样本规格展开成带内容寻址去重键与溯源块的 ship_samples 行，幂等入库、审计数字从库里读回。

## 1. 为什么需要（问题背景）

平台此前已有三个相邻件：`tools/capability_auto_audit`（批处理分类）、`tools/capability_upgrade`（事件驱动重判）、`tools/capability_audit`（对在线端点跑四族探针 + Merkle/签名锚）。它们各自可用，但三件事仍缺：

1. **判级器本身的准确率没人量**。升账逻辑是分类器；没有带标签的冻结集与假阳性率，「自动升账」就只是一段会写文件的代码。
2. **偏差归因是定性标签**（normalisation / baseline / discretisation 疑点），没有「这条 X% 分歧里解释掉多少、剩多少没解释」的算账。
3. **样本表只有行数字段**，没有内容寻址去重键与溯源块，于是「一万条样本」无法证明不重复、也无法回溯到生成它的规格。

本节点按这三点建三个模块，并把「机制自己的准确率」写成机器可复跑的数字。

## 2. 怎么做的（方法 / 流程 / 关键设置）

### 2.1 自动升账闭环

```
冻结端点表 (config/capability_governance/probe_set.json, sha256 冻结)
   └─ 探针执行  probes.run_endpoint   网格扫描 + 同点重复调用
        └─ 五门判据 grading.grade_probe
             ① deterministic_repeat   同输入两次必须同值
             ② input_response_spread  输出必须随输入动（展布 > 1e-6）
             ③ reference_error_pct    最细网格 vs 独立参考解 ≤ 声明容差
             ④ empirical_order        误差随加密下降的经验阶 ≥ 声明下限
             ⑤ provenance_sha256       探针源码 + 端点规格内容寻址
                 └─ 证据链 grading.build_chain   (测试报告 sha256/判据表/测量值/裁决/时间/任务号)
                       └─ 台账 ledger.apply     (只升不静默降；幂等；回归留痕；拒绝留痕)
```

关键设置：容差在冻结表里按端点**声明**（`tol_pct` = 诚实例程最细网格误差的 3 倍），不是运行时算出来的；冻结表整体 sha256 冻结（`frozen_set_digest`），改一行就换摘要。证据链的 `test_report` 不是随便一个 sha256 字符串：台账落笔时会**核对报告文件哈希**，文件缺失或哈希不符直接判 `rejected`。

### 2.2 跨求解器四维归因链

对同一物理问题、以某一 arm 为参考，逐 arm 做固定顺序的纠正，每一步的「偏差下降量」就是该维份额（伸缩求和，故份额之和 + 残差 = 1 恒成立）：

| 步 | 维度 | 纠正依据 |
|---|---|---|
| 1 | format 格式 | arm 声明的单位缩放 / 归一化基准 / 符号 |
| 2 | output 输出定义 | 同一量名的定义差异（基准面积/参考量）→ 规范化因子 |
| 3 | input_definition 输入定义 | 参考 arm 声明的对数-对数灵敏度指数，把 arm 值映射回参考输入 |
| 4 | discretisation 网格 | arm 自带网格序列做 Richardson 外推（用其声明阶） |

口径统一的三件套：输入以规范化 JSON 的 sha256 标识；输出字段与量名由 schema/声明注入；缺失任何一项必需声明（灵敏度、阶、网格序列）**报错而非默认**——不可归因必须可见。

### 2.3 结构化样本库

`config/capability_governance/sample_spec.json` 声明轴（3 学科 × 3 几何类）、12 个工况、100 个重复点与数值模型；代码把规格展开成行，`input_digest_sha256` = sha256(规范化输入) 作去重键，`provenance` 记录规格摘要/模型摘要/生成器源码摘要/时间/run id；入库用 ORM 表，已存在摘要跳过（幂等）。审计数字由数据库读回，不由生成器自报。

## 3. 计算结果（全部程序化注入）

### 3.1 判据总表

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| C1 通过→台账自动建 REAL | 每个通过端点一条有效链 | REAL=9, 链 63/63 有效 | PASS |
| C2 失败→不升账 | 失败端点 REAL 数 = 0 且 4 项负控全过 | REAL=0, 非 REAL 条目 54 | PASS |
| C3 回测准确率≥90%、假阳性<5% | acc ≥ 0.90, FPR < 0.05 | acc=1.0000, FPR=0.0000 (n=63) | PASS |
| C4 四维归因链完整 | 格式/输出/输入/网格四维可复现 + 残差门 | 6/6 用例通过, 归因完备率 0.8333 | PASS |
| C5 样本行数≥10000、每族≥2000、每类≥1000 | 行数/覆盖 | n=10800, 族最小 3600, 类最小 3600 | PASS |
| C6 溯源≥95%、去重唯一率≥99% | 0.95 / 0.99 | 溯源 100.00 %, 唯一 100.00 %, 幂等重载插入 0 | PASS |
| C7 零领域语义 | 扫描命中 = 0 | 扫描 19 文件, 命中 0 | PASS |

测试门：`pytest` 返回码 0，junit 报告 sha256 `fc1a7e6f022beb49bd7c77354c247e677b9be14dea661bc3af657db8a5a6539f`；台账计数器 create=63, update=0, noop=0, rejected=0, regression=0。

### 3.2 回测：四门机制 vs 单门基线

| 评分器 | 准确率 | 假阳性率 | 假阴性率 | 假阳性数 |
|---|---|---|---|---|
| 四门机制（本节点） | 1.0000 | 0.0000 | 0.0000 | 0 |
| 单门基线（设计点 + 1% 宽松容差） | 0.6349 | 0.3704 | 0.3333 | 20 |

| 缺陷模式 | 用例数 | 判级分布 |
|---|---|---|
| biased | 12 | WITHHELD×12 |
| clamped | 6 | WITHHELD×6 |
| constant | 6 | MOCK×6 |
| drifting | 6 | MOCK×6 |
| jitter | 6 | WITHHELD×6 |
| lattice | 6 | MOCK×2, WITHHELD×4 |
| ok | 9 | REAL×9 |
| scaled_constant | 6 | MOCK×6 |
| unit_error | 6 | WITHHELD×6 |

冻结集摘要 `9520bcdb2a37f24b3bba7730194f8f4071ef5b177353d058d44c6442f3e30a39`；脏数据（构造的缺陷模式）63 例中负例 54 例。四门机制假阳性 0 例，单门基线 20 例——**同一批负例、同一容差口径下，多门设计把假阳性压到 0**。

### 3.3 四维归因链

| 用例 | 注入维度 | 主因维（实测） | 该维份额 | 残差份额 | 是否归因 | 伸缩恒等式 |
|---|---|---|---|---|---|---|
| case_format | format | format | 1.000000 | 0 | True | True |
| case_output | output | output | 1.000000 | 0 | True | True |
| case_input | input_definition | input_definition | 1.000000 | 0 | True | True |
| case_mesh | discretisation | discretisation | 1.000000 | 0 | True | True |
| case_combined | — | format | 0.509800 | 0 | True | True |
| case_unexplained | — | format | 0.000000 | 1 | False | True |

四维平均份额： format=0.2516, output=0.2450, input_definition=0.1700, discretisation=0.1667

### 3.4 样本库覆盖与质量

| 轴 | 取值 | 行数 |
|---|---|---|
| 学科轴 | resistance (`fam_a`) | 3600 |
| 学科轴 | stability (`fam_b`) | 3600 |
| 学科轴 | seakeeping (`fam_c`) | 3600 |
| 几何类轴 | tanker (`cls_a`) | 3600 |
| 几何类轴 | bulk_carrier (`cls_b`) | 3600 |
| 几何类轴 | container (`cls_c`) | 3600 |

合计 **10800** 行；去重唯一率 100.00 %；溯源完整率 100.00 %；output 非空率 100.00 %；幂等重载插入 0 行；入库库文件 sha256 `af3c8d3180709bb62a022975ed876f1c9ec41f99ab69eeddbea3c86d6b378f0a`（13955072 字节，按仓库卫生不提交二进制库，语料以 gzip 提交且行数可 zcat 复核）。

### 3.5 负控（机制必须能说不）

| 负控 | 结果 |
|---|---|
| failing_endpoints_never_real | PASS |
| tampered_chain_rejected | PASS |
| forged_pass_rejected | PASS |
| chain_without_matching_report_rejected | PASS |

## 4. 图

![fig_backtest](../../artifacts/capability_governance/fig_backtest.png)

*Frozen-set replay: four-gate mechanism vs single numeric gate; confusion of diagnostic sub-levels.*

![fig_attribution](../../artifacts/capability_governance/fig_attribution.png)

*Per-case attribution shares; the last case is an unmodelled 3% difference and is refused attribution (residual = 1).*

![fig_samples](../../artifacts/capability_governance/fig_samples.png)

*Structured sample corpus coverage per axis and corpus-quality ratios.*

## 5. 注意点 / 局限（honest）

- Frozen negative set is constructed (defect modes), so the reported FPR is a lower bound, not a field estimate.
- Sample corpus rows are produced by the spec-declared generic IVP model; they are structurally complete and traceable but are not validated against any external reference. Plugging a real solver in means replacing spec.model, not code.
- Acceptance writes to the SQLite instance at samples/ when no DATABASE_URL is supplied; the loader uses the same ORM table as production, but no PostgreSQL driver is available in this sandbox (psycopg2 is a stub).
- Attribution corrections are first-order log-linear in the declared sensitivity exponents; strongly non-linear input response will leak into the residual (visible, not hidden).
- 冻结集的负例是**构造**缺陷模式（常数、等比常数、偏置、抖动、单位错、截断、量化格、调用序漂移），比生产流量容易，因此 FPR=0 应读作**下界**，不是现场估计。
- 四维归因的输入维用一阶对数-线性灵敏度；强非线性输入响应会漏进残差（残差可见，不隐藏）。
- 库里 10000+ 行是**机制产出**，数值来自规格声明的通用 IVP 模型，未与任何外部参考实验对标；换真实求解器只需换 `spec.model`，代码不动——但也因此这批行不能当作领域精度证据。
- 沙箱无 PostgreSQL 驱动（psycopg2 是桩），验收在 SQLite 上跑；加载器用的是与生产同一张 ORM 表，换 `DATABASE_URL` 即可对 PG 执行，`backend/migrations/113_ship_sample_governance.sql` 已备。

## 6. 误差判定（对基准逐项）

| 关键量 | 基准 | 实测 | 是否 < 3% |
|---|---|---|---|
| 诚实探针最细网格相对误差（9 例） | 独立解析参考解 | 最大 1.255e-03 % | 是（远小于 3%） |
| 判级器决策准确率 | 冻结集标签 | 1.0000 | 是（判据线 90%） |
| 假阳性率 | 0 | 0.0000 | 是（判据线 5%） |
| 单维归因残差（4 例） | 0（注入维已知） | 最大 0.000e+00 | 是 |

样本库数值**无外部基准**（规格声明的通用 IVP 模型，非领域算例），因此不声明精度，只声明结构完整性与可追溯性——见 §5 第 3 条。

## 7. 审计链（复跑路径）

```bash
# 1) 一键验收（含 pytest 门、台账、回测、归因、样本、扫描、图）
python -m services.capability_governance.cli run --out-dir artifacts/capability_governance
# 2) 单元测试
python -m pytest tests/capability_governance -q
# 3) 本报告重生成（数字从 artifacts 注入，零手抄）
python3 tools/capability_governance/gen_report.py
```

产物清单：

| 产物 | 内容 |
|---|---|
| `artifacts/capability_governance/criteria.json` | C1–C7 判据数字 + 证据路径 + 局限 |
| `artifacts/capability_governance/ledger.json` / `ledger_events.jsonl` | 台账状态 + 追加式事件日志 |
| `artifacts/capability_governance/evidence_chains/*.json` | 每端点一条内容寻址证据链 |
| `artifacts/capability_governance/backtest.json` | 冻结集逐例裁决 + 混淆矩阵 + 单门基线 |
| `artifacts/capability_governance/attribution.json` | 四维分解逐例账 |
| `artifacts/capability_governance/samples/corpus.jsonl.gz` | 全量语料（行数可复核） |
| `artifacts/capability_governance/samples/sample_audit.json` | 覆盖/去重/溯源/库哈希 |
| `artifacts/capability_governance/negative_controls/negative_controls.json` | 四项负控 |

## 8. 与既有能力的关系（不是重复造）

| 既有件 | 它回答的问题 | 本节点补的缝 |
|---|---|---|
| `tools/capability_auto_audit` | 全量批处理分类 | 增量：判级器自身准确率/假阳性率在冻结集上可测 |
| `tools/capability_upgrade` | 证据入库触发重判 | 增量：链自校验 + 报告哈希核对 + 回归留痕 + 冻结回测 |
| `services.xsolver.adjudicate` | 分歧是否存在、粗标签归因 | 增量：四维伸缩分解 + 未解释残差 + 完备率门 |
| `backend/api/domain/ship_samples` | 单次样本写入/查询 | 增量：内容寻址去重键 + 溯源块 + 幂等批量装载 + 库上审计 |

