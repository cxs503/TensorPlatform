# p256w1-n02 · 证据第二实现成熟化：变形关系门 + 注入检出率双向定标

**节点** p256w1-n02（平台机制成熟化） · **仓库** TensorPlatform · **分支** `agent/m9793`
**代码** `tools/evidence_second_impl/` · **测试** `tests/test_evidence_second_impl.py`（31 例全绿）
**产物** `artifacts/evidence_second_impl/`（run_report.json / acceptance.json / bench_manifest.json /
capability_declarations.json / declaration_sources.json / evidence_second_impl.sqlite / figures/）

---

## ① 案例介绍：要解决什么问题

平台已有证据等级引擎 `tools/evidence_grade.py`（R00–R13 规则 DAG），它把等级从"作者声明"变成
"契约结构的函数"。但它留了一个洞：**一个契约可以在结构上完全合法、被判为 REAL，而它背后的数字
全部由被测代码自己产生**。此时 `REAL` 只意味着"格式规范"，不意味着"被独立佐证"——这正是伪造者
最省力的一条路径（自己算、自己比、自己写 REAL）。

本节点把 `REAL` 变成**抗伪造**含义，做法是把"证据独立性"和"证据可判别性"写成可执行判据：

| 维度 | 判据 | 交付物 |
|---|---|---|
| 独立性标注 | 每条 REAL 必须声明参考值来源类型（解析闭式 / 独立算法路径 / 外部数据 / 同代码自指）**且**给出可离线重读的证据定位（文件+行号+该行哈希） | `independence.py` + `capability_declarations.json` |
| 变形关系门 | 物理/数学上必须成立的变换写成可执行判据，每条自带**否定控制**（破坏该变换必须 FAIL） | `metamorphic.py` |
| 注入测试台 | 三类伪造（×1.05、常数偏移、符号翻转）注入到第二实现上，门必须检出；未改动群体必须 0 误报 | `injection.py` |
| 台账联动 | 重判写回 SQLite（改前备份 + 回滚演练 + 耗时），报告数字全部由 SQL 取 | `ledger.py` |

**被审计对象（域数据，来自审计快照 2026-11-10）**：12 项能力，其中 5 项声明 REAL：
`fem.simp.min_compliance`、`ice.maneuver.energy_ledger`、`platform.evidence_grade.engine`、
`structure.ice.plate_beam_elastic`、`structure.ice.plate_beam_ultimate`。

**机制层零领域语义**：`tools/evidence_second_impl/*.py` 内不出现任何领域词条；能力 id、探针点、
容差、证据行全部经工件（JSON）注入。句柄 id 一律 `synth.*`（合成域名），由
`tools/evidence_domain_scan.py` 的 37 词表机器扫描证明（0 命中）。

---

## ② 怎么做的（方法与工具链）

**流程**（`python -m tools.evidence_second_impl.runner`，端到端 0.19 s）

1. **定位器钉扎**：`build_declarations.py` 把 `declaration_sources.json` 里的 needle 解析成 1-based 行号，
   对**该行原文**取 sha256 前 16 位写入 `capability_declarations.json`。needle 不命中或命中多行 = 构建失败
   （宁可停下也不钉错行）。
2. **独立性重判**（`independence.regrade`）：REAL 保留需同时满足 ①来源类型独立 ②至少一条定位器重读且哈希一致；
   同代码自指 → `WITHHELD`；定位器重读失败 → `WITHHELD`（`UNVERIFIED`）；同代码自指但**另有**独立佐证定位器
   通过 → 保留 REAL 但标 `RETAIN_FLAGGED`，自指份额保持可见（不洗白）。
3. **变形关系门**（`metamorphic`）：6 类关系 `equivalence / scaling / zero_point / additivity /
   mirror_parity / conservation`，每类带适用域与容差；每个关系实例跑两遍——诚实候选（须 PASS）与
   **专为破坏它构造的变异体**（须 FAIL），两者并存才算"可判别"。
4. **注入测试台**（`injection`）：23 个可执行句柄（3 个 target + 20 个 control），每个句柄都有
   **两份同一规格的实现**（闭式主实现 vs 独立路径次实现：Kahan 补偿求和 / exp-log / 归一化整数权重，
   次实现按 9 位有效数字舍入，残留 ~1e-9 而非 0——所以"0 误报"不是靠两份代码逐位相同骗来的）。
   对 3 个 target 分别注入 ×1.05 / +5% 参考量级 / 取负，门报 FAIL 即检出。
5. **台账联动**（`ledger`）：SQLite 7 张表；写回前备份并记录备份 sha256；写回后立刻做**回滚演练**
   （备份覆盖 → 重算全表内容摘要 → 与写前摘要逐字比对），再恢复写入态。
6. **报告数字全部 SQL 取值**：`run_report.json → ledger.sql.*` 由 `q_grade_counts / q_downgrades /
   q_self_reference / q_self_reference_share / q_injection_rates / q_detection_matrix /
   q_negative_controls / q_evidence_lines` 产出，Python 侧不再重算任何统计量。

**工具链**：Python 3.11 / sqlite3（标准库）/ matplotlib(Agg) / pytest。无网络依赖，离线可复算。

---

## ③ 计算结果（数字均取自 SQL）

### 3.1 独立性重判（`q_grade_counts` / `q_downgrades` / `q_self_reference_share`）

| 指标 | 值 |
|---|---|
| 能力总数 | 12 |
| REAL（审计声明，重判前） | 5 |
| REAL（重判后） | **4** |
| 降级数 | **1** |
| 自指份额（REAL 声明中，重判前） | **0.2**（1/5） |
| 自指份额（REAL 重判后） | **0.0** |
| 同代码自指但保留（RETAIN_FLAGGED） | 0 项（无独立佐证，故不保留） |

**降级清单（含证据行号）**

| 能力 | 前 → 后 | 原因码 | 证据定位 |
|---|---|---|---|
| `ice.maneuver.energy_ledger` | REAL → **WITHHELD** | `SELF_REFERENCE_NO_INDEPENDENT_CORROBORATION` | `docs/m7924/INTEGRATION_REPORT.md:60`，行哈希 `a6f018972a5cabe4` |

保留 REAL 的 4 项及其来源类型：解析闭式 ×3（`fem.simp.min_compliance`、`structure.ice.plate_beam_elastic`、
`structure.ice.plate_beam_ultimate`）、独立算法路径 ×1（`platform.evidence_grade.engine`，其 30 例夹具标签由
`tools/evidence_fixtures.py` 独立构造，定位行哈希已钉）。声明来源分布：`analytic_closed_form` 3、
`independent_algorithm` 1、`same_code_self_reference` 1、`not_asserted` 7。

### 3.2 注入检出（`q_injection_rates` / `q_injection_total`）

| 注入类 | 检出/试管柄 | 检出率 |
|---|---|---|
| `scale_1p05`（比例 ×1.05） | 3/3 | 1.00 |
| `offset`（+5% 参考量级） | 3/3 | 1.00 |
| `sign_flip`（符号翻转） | 3/3 | 1.00 |
| **合计** | **9/9** | **1.00**（判据下界 2/3） |

### 3.3 误报（`q_metamorphic_failures` / `q_clean_alarms`）

20 个未改动 control 句柄合计 76 个适用关系实例（3 个 target 另计 11 个）= 全部 PASS，**误报 0/20**；连 3 个 target 的诚实态一并计，
**0/23 句柄报警**。

### 3.4 关系族 × 注入类 检出矩阵（`q_detection_matrix`，检出/评估）

| 关系 | scale ×1.05 | offset | sign flip |
|---|---|---|---|
| `equivalence`（第二实现一致性） | **3/3** | **3/3** | **3/3** |
| `conservation`（划分守恒总量） | **1/1** | **1/1** | **1/1** |
| `scaling`（齐次 k 次） | 0/2 | **2/2** | 0/2 |
| `zero_point`（零输入退化） | 0/3 | **3/3** | 0/3 |
| `additivity`（可加性） | 0/2 | **2/2** | 0/2 |
| `mirror_parity`（镜像对称） | — | — | — |

**这是本节点最重要的诚实结论**：`scaling / additivity / zero_point / mirror_parity` 这类**自指相对关系**
对"整体倍乘"和"整体取负"在数学上就是盲的（全局因子是任何齐次关系的自同构：`f'(x)=c·f(x)` 代入两边同时出现 `c`
而抵消；实测 0/2、0/3、0/2 全零）。只有引入**绝对锚**——与独立实现比（equivalence）或与声明的守恒总量比
（conservation）——才能看见 1.05 倍与符号翻转。因此规则②③合起来给出一条可执行要求：
**REAL 证据里至少要有一条绝对锚关系，纯相对（齐次/对称/可加）关系不构成判别力。**

### 3.5 否定控制（`q_negative_controls`，判据 c）

87 个关系实例，87 个否定控制变异体全部 FAIL（87/87 可判别），无"空转变形关系"。
按类：equivalence→×1.05、scaling→非齐次项、zero_point/additivity→常数偏移或二次交叉项、
mirror_parity→破坏宇称项、conservation→×1.05。

### 3.6 台账写回与回滚（判据 e）

| 指标 | 值 |
|---|---|
| 写回耗时 | **0.008 s**（阈值 < 5 s） |
| 回滚演练 | 备份覆盖后全表摘要 `48fc98…53d6` 与写前**逐字一致 → verified** |
| 备份 | `evidence_second_impl.sqlite.bak`，57344 B，sha256 `ad21ac…81b2`（仓策略 `.gitignore: *.sqlite` 不入库，由 runner 重建；备份哈希已记入 `run_report.json`） |
| 回滚耗时 | 0.007 s |

### 3.7 零领域语义（判据 f）

`tools/evidence_domain_scan.py` 扫 `tools/evidence_second_impl`（10 文件 × 37 词）= **0 命中**；
23 个句柄 id 全部 `synth.*`，无真实域名词条；该目录已登记进词表 `targets`，从此进 CI 常设门。

---

## ④ 结果图

![fig1](artifacts/evidence_second_impl/figures/fig1_detection_matrix.png)

**图 1** 关系族 × 注入类检出热图（格内 = 检出/评估）。红格即"这类关系对这类伪造结构性失明"，
是本节点最有信息量的一张图。

![fig2](artifacts/evidence_second_impl/figures/fig2_residual_separation.png)

**图 2** 门限分离：纵轴 = max(残留/容差)（对数）。诚实态（蓝）全部 ≤ 4.4e-3；
三类注入（红）全部落在 1e4 以上；虚线为 PASS/FAIL 边界（ratio=1）。分离度约 7 个数量级。

![fig3](artifacts/evidence_second_impl/figures/fig3_independence_regrade.png)

**图 3** 左：等级分布重判前后（REAL 5→4，WITHHELD 8→9）；右：12 项声明的参考来源类型分布。

---

## ⑤ 注意点（坑、局限、适用范围）

1. **比例注入对相对关系天然隐身（实测踩到，已修判据而非改数据）**：第一版否定控制用"按探针量级的 ε 加常数项"
   去破坏齐次性，结果 4 个实例仍然 PASS——因为 ε·|x| 本身是 1 次齐次的，`f(λx)` 与 `λf(x)` 一起变，
   抵消了。改成**0 次与 2 次的非齐次项** `0.05·(1+x²)` 后才 FAIL。教训：否定控制的变异体自己不能是
   被检变换的不变量，否则就是"空转"。
2. **句柄是合成的**：真实领域能力在别的仓/HTTP 后面，进程内无法扰动，所以 3 个 target 用
   合成代理（`ledger_proxy_for` 记录它替身的是哪条台账能力）。机制与句柄解耦（句柄 = 可调用 + 探针规格），
   接真实服务只需给一个 adapter，但**对真实服务的检出率本节点未验证**。
3. **独立性判据是"声明 + 行级哈希钉扎"，不是形式化证明**：它能防住"引用不存在/被改过的证据行"，
   防不住"引用一条真实但不相干的行"。缓解：定位器把审查成本降到 O(1)/条（每行哈希），且可执行能力另有变形门。
4. **审计快照是 2026-11-10 的 12 项**，不在运行时重新拉取线上审计（避免测试依赖活服务）；
   快照来源与时间写在 `declaration_sources.json`。
5. **关系只对声明了它的句柄生效**：未声明 = `NOT_APPLICABLE`（不计入 PASS），避免"没测=通过"。
6. **适用范围**：本机制只管"标量数值能力"的证据独立性；接口型/查询型能力不适用（与 R13 判定一致）。
7. 已知无关失败：`tests/evidence/test_evidence_grade_engine.py::test_domain_scan_finds_nothing_in_the_package`
   断言扫到 >50 文件，依赖**未入库的生成夹具树** `artifacts/evidence_grade/fixtures`；本节点未触碰该路径
   （`git status` 仅新增 `tools/evidence_second_impl/`、`tests/test_evidence_second_impl.py`、`artifacts/evidence_second_impl/`，
   另在词表 targets 追加一行），该失败在本节点改动前即存在。

---

## ⑥ 误差判定

本节点是**机制/V&V 节点**，不把物理量与实验对拍，因此"vs 实验/参考值 <3%"的物理判据**不适用**（如实声明原因，
不含糊替代）。与之等价的"标定误差"是**门限分离度**：

| 关键量 | 数值 | 是否有基准 | 结论 |
|---|---|---|---|
| 诚实态最坏 残留/容差 | 1.60e-3（equivalence）、4.40e-3（additivity）、0（conservation/mirror/zero_point） | 容差由声明给定（基准存在） | **0.16% ≪ 3%** ✓ |
| 注入态最小 残留/容差 | 5.00e+4（scale/offset）、2.00e+6（sign flip） | 同上 | 高出边界 ≥4.7 个数量级 ✓ |
| 分离度 | 5.0e4 / 1.6e-3 ≈ **3.1e7** | — | 阈值附近无临界样本（无 0.5~2 倍的噪声带） ✓ |
| 注入检出率 | 9/9 = 1.00（判据下界 0.667） | 判据 | ✓ |
| 未改动误报 | 0/20 = 0.00（判据 0） | 判据 | ✓ |
| 否定控制可判别率 | 87/87 = 1.00 | 判据 1.00 | ✓ |
| 台账写回耗时 | 0.008 s（判据 < 5 s） | 判据 | ✓ |

**是否 <3%：是**——诚实态残留仅为容差的 0.16%，远低于 3%；且注入态与诚实态之间没有模糊地带
（最近一次注入残留仍是容差的 5×10⁴ 倍）。**honest-FAIL 声明**：对真实领域服务（HTTP 后端）的注入检出率
本节点未测、如实记入第 5 节局限，不予声称。

---

## 复现

```bash
python -m tools.evidence_second_impl.build_declarations          # 钉扎证据行（可选，runner 会自建）
python -m tools.evidence_second_impl.runner                      # 端到端：重判 + 门 + 注入 + 台账 + 验收
python -m tools.evidence_second_impl.figures                     # 三张图
python -m pytest tests/test_evidence_second_impl.py -q           # 31 例
python tools/evidence_domain_scan.py                             # 机制层零领域语义（0 命中）
```

机器验收：`artifacts/evidence_second_impl/acceptance.json → all_pass = true, 6/6`。