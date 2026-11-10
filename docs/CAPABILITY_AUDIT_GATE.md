# 能力台账自动审计管线（capability_audit_gate）

**节点**：P304W2-N08 ｜ **仓库**：TensorPlatform（共性平台机制，零领域语义）
**产物**：`tools/capability_audit_gate/`、`config/capability_audit_gate/`（spec + 人审证据面 + 10 项金标准语料）、
`tests/capability_audit_gate/`（57 用例）、`.github/workflows/capability-audit.yml`、
`artifacts/capability_audit_gate/`（report.json / report.md / criteria.json / gate.json / baseline.json / metrics.json / 4 图）

---

## 1. 决策与被比较的备选方案（experimental design §1–2）

**决策**：把「某能力是 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED」从自述升级为
**CI 每次 PR 自动复算、可回归拦截**的机器判定，并给出可回溯的证据链。

| 备选 | 现状/做法 | 为什么没选它做本节点主线 |
|---|---|---|
| A. 扩写既有 `tools/capability_reconcile`（三源自述对账） | 已能出 code/test/doc 三源派生等级 + 过度声明检测 | 它解决「自述 vs 证据」，**没有**基线回归防护、没有标注语料一致率、没有 CI 门；而且实测其路径自动绑定把短词放大成整仓噪音，与人工裁决等级一致率仅 **38.1%**（本次实测：`python3 -m tools.capability_reconcile.cli --ledger artifacts/capability_ledger/capability_ledger.json --contrast .../capability_ledger_resolved.json`，`grade_agreement=0.381`）——在这个基线上加门只会把噪音变成红灯。 |
| B. 在线探针（既有 `tools/capability_audit`，四族探针打 :8001） | 抓硬编码/随机最有力 | 需要服务在线，PR 环境不可达 ⇒ 全部 WITHHELD；且探针本身含域判据（解析解转写），落 TensorPlatform 违反零领域语义。**保留为互补层**，不在本管线内复跑。 |
| **C. 本节点：新模块 `capability_audit_gate`（静态结构信号 + 落盘基准工件 + 基线回归门）** | **选它** | 代码零领域语义（能力/绑定/基准口径全在 `config/`），PR 可跑（秒级），且能把"等级下降"变成机器红灯。 |

**与既有四套能力工具的分工**（不重复造，边界写清）：

| 既有 | 关注点 | 本管线边界 |
|---|---|---|
| `tools/capability_reconcile` | 自述 vs 三源证据、过度声明 | 本管线不做过度声明裁决；用**基线回归**防退化（互补，可同跑） |
| `tools/capability_audit`（探针） | 运行期四族探针 | 本管线不执行代码；探针失败可落盘成 `json_verdict` 基准工件被本管线消费 |
| `tools/capability_auto_audit` | 分类器 + SQL provenance | 本管线无 DB，产物是单文件报告 + 基线 |
| `tools/capability_ledger_maturity` | 证据收集/裁决工作流 | 本管线不产生裁决，只把等级变化挡在 CI |

## 2. 假设与指标（§2–3）

* **H1**：静态结构信号（空壳占比、造假指纹族、数值断言、基准误差）足以在**人工绑定证据面**上复现人工裁决等级，一致率 ≥90%。
* **H2**：把等级冻结成基线后，任何"为了让 CI 变绿而删证据"的改动都可 100% 检出。
* **H0（对照）**：判定只是跟着类别不平衡走 ⇒ 一致率 ≈ 多数类基线。

**指标（全部机器可算）**

| 编号 | 指标 | 预声明阈值 | 来源 |
|---|---|---|---|
| C1 | 可审计覆盖率（每能力都有等级+理由码） | =100% | `report.json.summary.coverage` |
| C2 | 与标注真值一致率 | ≥90%，且**必须同时报多数类基线** | `validation.agreement` / `contrast.agreement` |
| C3 | 降级检出率（5×5 全组合 + 端到端 REAL→MOCK） | 100% | `tests/.../test_gate_report.py` |
| C4 | 证据链可回溯（文件路径 + 测试名 + 基准值/误差） | 21/21 交代；REAL 必须三件套齐全 | `summary.chain_sufficient` / `chain_full` |
| C5 | 审计耗时（21 能力，增量缓存） | < 60 s | `summary.runtime_sec` |
| C6 | pytest 用例数 | ≥15 | `tests/capability_audit_gate/` |

## 3. 判定级联与预声明阈值（§4，先于观测声明）

```
1 绑定路径存在但文件缺失        → NOT_SUPPORTED  BINDING_TARGET_MISSING
2 解析到测试/实现但无实现文件    → NOT_SUPPORTED  CODE_MISSING
3 空壳占比 ≥0.5（或无函数）      → STUB           STUB_DOMINATED
4 ≥2 个独立造假信号族            → MOCK           UNSEEDED_RANDOM/HARDCODE_MARKERS/CONSTANT_RETURN
5 解析型(analytic/identity)基准越界 → MOCK        PHYSICS_INVARIANT_VIOLATED
6 实验型基准误差 >15%            → WITHHELD       BENCHMARK_OVER_TOLERANCE
7 无测试 / 无数值断言            → WITHHELD       TESTS_MISSING / TESTS_NON_NUMERIC
8 无基准工件（或工件无有效行）    → WITHHELD       NO_BENCHMARK
9 基准误差 >3%（REAL 容差）      → WITHHELD       BELOW_REAL_TOLERANCE
10 全过且误差 ≤3%                → REAL           NUMERIC_VERIFICATION_PASS
11 其余                         → WITHHELD       EVIDENCE_NOT_BOUND / INSUFFICIENT
```

| 阈值 | 值 | 依据 |
|---|---|---|
| `real_tolerance_pct` | 3.0 | 平台精度标准（同 `PLATFORM_VERIFICATION_STATUS`） |
| `acceptable_tolerance_pct` | 15.0 | 平台"不可接受"红线；实验型越界**不指控造假**，只否决 REAL |
| `stub_ratio_max` | 0.5 | 空壳函数占比过半即空壳 |
| `mock_min_independent_signals` | 2 | 单信号（如仅未播种随机）只记 warning，避免把合法随机采样判成 MOCK |
| `pattern_can_refute` | **false** | 仅路径名模式匹配到的证据不得做正面反证（降为 WITHHELD 并留痕） |
| `sloc_min` | 6 | **find#1 后降为弱信号**：尺寸不足只记 `LOW_SLOC` warning，不单独定级 |

规则集版本 `cag-rules-v2`，随基线一起冻结；改规则必须显式 ack（`ack.json.ruleset`）。

## 4. 混杂与变量控制（§5–8）

| 混杂 | 处理 |
|---|---|
| 类别不平衡（真实台账 19/21 是 WITHHELD） | **不以真实台账单独作证据**：另建 10 项五级均衡标注语料（真值由构造给定），并强制报多数类基线 |
| 短词噪音（`stats`/`GM`/`generate` 命中整仓） | 路径**词元级**匹配（非任意子串）+ 上限 12 文件 + 未绑定证据不得反证 |
| 自作自评（同一作者写规则与真值） | 真值来自"按构造"的语料（每边界的定义先写进文档再跑）+ 真实台账仅作外部对照（`contrast`），不一致逐条列出 |
| 环境差异 | 只依赖 CPython 3.11 标准库 + pytest；无 numpy/matplotlib 依赖（图单独脚本） |
| 阈值事后调参 | 逐条记录：**find#1**（LOW_SLOC→warning）为跑完金标准语料后的一次修订，理由=语义（5 行含造假指纹是 MOCK 不是 STUB），修订后一致率 90%→100%，**未**回改其它阈值 |
| 重复/随机性 | 判定为确定性函数（AST 统计 + 工件数值），无采样；"重复"以**冷/热缓存两次运行等级完全一致**作为复现检验（`test_incremental_cache_makes_second_run_all_hits`） |

## 5. 结果（§9–12，数字全部落在 `artifacts/capability_audit_gate/`）

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| C1 覆盖率 | 100% | **21/21 = 100%** | ✓ |
| C2 金标准一致率（10 项均衡语料） | ≥90% | **100%**（多数类基线 30%，增益 **+70pp**） | ✓ |
| C2 真实台账一致率（21 vs `capability_ledger_resolved.json`） | ≥90% | **95.24%**（多数类基线 90.48%，增益 +4.76pp，**判别力弱，如实标注**） | ✓ |
| C3 降级检出 | 100% | 5×5 组合 10 个降级对**全检出**；端到端 REAL→MOCK → 退出码 1 | ✓ |
| C4 证据链 | 21/21 | `chain_sufficient=21`；三件套齐全 `chain_full=2`（其余因无基准/未绑定，理由码已交代） | ✓（口径见局限） |
| C5 耗时 | <60 s | 冷 **1.06 s** / 热 **0.53 s**（缓存命中 100%；随机器 1–2 s 波动，见 `metrics.json`） | ✓ |
| C6 pytest | ≥15 | **57 passed** | ✓ |

**本次 21 能力判定分布**：REAL 1（`calc.stability`）、WITHHELD 20、MOCK/STUB/NOT_SUPPORTED 0。
唯一与外部台账不一致项：`propeller.open_water` 台账判 MOCK，本管线判 WITHHELD（`NO_BENCHMARK`）——
台账 MOCK 来自**在线探针**（eta0 定义恒等式越界 237%），静态管线按设计不执行代码，故只能拒绝下判决。
这是**声明的能力边界**，不是沉默放过：把探针结果落盘成 `json_verdict` 基准工件即可被本管线消费
（`scan.scan_benchmark` 已支持该 kind，金标准语料里 `lambda.mesh` 就是这种形状的阳性对照）。

**图**（`artifacts/capability_audit_gate/figures/`）
1. `level_tally.png` — 五级分布（基线 vs 当前）；
2. `agreement_vs_baseline.png` — 自动判定一致率 vs 多数类基线（金标准 100% vs 30%；真实 95.2% vs 90.5%）；
3. `runtime_cache.png` — 冷/热缓存耗时 vs 60 s 预算；
4. `gate_matrix.png` — 5×5 等级变化判定矩阵（10 个降级对全部落入 DOWNGRADE/REFUTATION）。

## 6. 踩过的坑（findings）

* **find#1 尺寸启发式误判**：金标准里 5 行"硬编码+随机"样例先被判 STUB（`LOW_SLOC`）。语义上它是有正面反证的 MOCK，不是空壳 ⇒ 尺寸降为弱信号（warning），空壳只由 `stub_ratio` 定。修订后 10/10。
* **find#2 短词噪音**：任意子串匹配让 `stats`/`generate`/`predict` 命中 12 个无关文件，聚合出"未播种随机"假信号，一致率被压到 81%。改词元级匹配 + 未绑定证据不得反证后回到 95.2%。
* **find#3 缓存命名空间串味**：同一文件既被当实现又被当测试解析时，缓存键只含路径 ⇒ 第二次读到错误信号结构（`KeyError: 'test_names'`）。缓存键改为 `namespace:path`。

## 7. 局限与适用范围（honest）

1. **静态**：不执行代码；数值发散、未固定随机种子在运行期的表现、并发竞态都不覆盖（交 `tools/capability_audit` 探针）。
2. **基准工件是历史快照**：REAL 只代表"该工件所测版本"；工件与实现 sha256 的绑定由 CI 增量流水重跑保证，不是本管线能自证的事。
3. **真实台账对照判别力弱**：95.24% 只比多数类基线高 4.76pp；真正判别力来自均衡金标准语料（+70pp）。两者必须一起读。
4. **覆盖率≠证据密度**：`chain_full` 只有 2/21（其余缺基准或未绑定）。`chain_sufficient=21` 的口径是"有路径或有明确缺失理由"，不要读成"21 项都有完整证据"。
5. **21 项中 6 项未绑定证据**（`EVIDENCE_NOT_BOUND`）：只说明"没找过"，不构成"不存在"的正面反证。

## 8. 复现（§9 环境与命令）

```bash
# 0) 依赖：仅 python3.11 + pytest（图另需 matplotlib）
python3 -m scripts.gen_capability_audit_spec                       # 由注册表+人审证据面派生 spec
python3 -m pytest -q tests/capability_audit_gate                   # 57 用例：规则/扫描/回归/报告
python3 -m tools.capability_audit_gate.cli audit \
    --spec config/capability_audit_gate/golden/spec.json \
    --root config/capability_audit_gate/golden --out /tmp/golden --enforce-acceptance
python3 -m tools.capability_audit_gate.cli freeze \
    --spec config/capability_audit_gate/spec.json --root . \
    --out artifacts/capability_audit_gate/baseline.json --cache artifacts/capability_audit_gate/cache.json
python3 -m tools.capability_audit_gate.cli audit \
    --spec config/capability_audit_gate/spec.json --root . \
    --out artifacts/capability_audit_gate --cache artifacts/capability_audit_gate/cache.json \
    --baseline artifacts/capability_audit_gate/baseline.json \
    --contrast artifacts/capability_ledger/capability_ledger_resolved.json \
    --fail-on-gate --enforce-acceptance
python3 scripts/capability_audit_figures.py                        # 4 图 + metrics.json
```

退出码：`0` 通过 / `1` 回归违规 / `3` 验收判据未达 / `4` spec·基线·ack 配置错误。
降级需显式 ack（`--ack config/capability_audit_gate/ack.json`（模板见 `ack.example.json`），内含 id 与理由），ack 会写进报告 `gate.json`。

## 9. 安全与资源上限（§11）

* 只读工作树（写仅限 `artifacts/capability_audit_gate/` 与 `--out` 指定目录）；不联网、不启服务、不执行被审计代码。
* 单次运行解析 ≤ 21 能力 × (12 实现 + 12 测试) 文件，CI 时长秒级；`--cache` 命中率与耗时入报告。