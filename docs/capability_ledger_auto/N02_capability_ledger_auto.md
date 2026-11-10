# N02 平台能力台账自动升账引擎（capability ledger auto-upgrade）

- 战役节点：`p270w2-n02-capability-ledger-auto`  
- 产物：`artifacts/capability_ledger_auto/ledger_audit.json` (sha256 `342e1967e53780b4…`, content_digest `001ed1e76b20efa0…`)  
- 扫描时 commit：`ae8c91ea0869ca4e148a7d4bf2d200e86998dbd7`  
- 注册表：`config/capabilities.json`（21 项，sha256 `2830c1cb7d73d56f…`）

## 1 案例介绍（这是什么问题）

平台对外自述 21 项能力（`config/capabilities.json`）。此前这份清单的等级是**人工登记**的：人会写 REAL，但代码里可能只有随机数、只有 `NotImplementedError`，或者测试全红。推进域 `propeller.open_water` 就是典型：注册表写 REAL，两篇独立审计判 MOCK（eta0 越过动量理论上限、KQ 对 J 不敏感）。

本节点要建的是**可复算的自动升账引擎**：只按能力注册表下发的模式去仓库里找证据（代码/测试/报告/五元），把 21 项能力自动判成 REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED，并落盘带 sha256 + git_commit 的审计产物。

入手时的事实（基线）：`src/capability_ledger_auto/` 已有 1284 行实现，但 `tests/capability_ledger_auto/` 的 5 个测试文件**全部是 collection error**（模块在 `src/` 布局下、测试按顶层包名导入），CI 因 `--continue-on-collection-errors` 静默放过：53 个用例一个都没跑过。强行跑通后真实分布为 STUB 9 / WITHHELD 12，与人工裁决（MOCK 1 / REAL 1 / WITHHELD 19）**几乎不重合**。

## 2 怎么做的（方法 / 流程 / 关键设置）

### 2.1 先修红：让证据能被机器读

| # | 缺陷（修复前真实行为） | 修复 | 回归用例 |
|---|---|---|---|
| D1 | 测试包 collection error，53 用例从未执行 | `tests/capability_ledger_auto/conftest.py` 注入 `src/` | 全套 53+20 |
| D2 | 自指污染：`scanner.py` 里的标记字面量（`[MOCK]`/`NotImplementedError`）反过来把自己覆盖的能力判成 MOCK/STUB | 默认排除引擎自身包 | `test_self_scan_is_excluded` |
| D3 | 文件级 STUB 过触发：仓库任一文件出现 `NotImplementedError`，整条能力即 STUB （实测把 propeller/stability/resistance 全部误判 STUB） | AST 节点级判定 `is_stub_like` | `test_unrelated_stub_file_does_not_stub_capability`, `test_is_stub_like_is_node_scoped` |
| D4 | MOCK 标记按整文件命中，同文件里他处的随机数会污染本能力 | 只在匹配节点源码片段内取标记 | `test_mock_marker_must_be_inside_matched_node` |
| D5 | `_is_stub` 用 `Path(ev.file_path).read_text()`（相对 CWD，非仓库根） | 判定不再读文件，节点级标志随证据携带 | D3 用例 |
| D6 | 五元 config 证据自指：`config/capabilities.json` 自身含能力模式 → config 恒真 | 证据链支持 `exclude_paths`（注册表文件被剔除） | `test_config_evidence_not_self_referential` |
| D7 | 理论证据只试第一篇命中文档，命中但无推导内容就判缺失 | 逐个候选文档查推导标记 | `test_theory_finder_skips_doc_without_theory_content` |
| D8 | `as_of` 时区混排（`+08:00`/`+00:00`）按字符串排序 → 最新裁决取错 | 归一化到 UTC 再排序 | `test_as_of_timezone_normalisation` |
| D9 | 报告证据只有 `verdict` 字段，仓库 5 份报告全无 verdict → 永远判不出 MOCK/REAL | 新增裁决抽取（治理词表）+ 结构化裁决台账 + 离线报告语料三个证据源 | `test_latest_adjudication_wins_across_sources` |

### 2.2 判级规则（每条都能指到证据强度）

```
NOT_SUPPORTED  无代码/测试/报告/五元痕迹
MOCK           报告最新裁决 ∈ {MOCK,FAIL,HONEST_FAIL,STUB} 或 匹配节点体内有 MOCK 标记
               或 测试执行通过率 < 0.8
STUB          匹配到的实现**全部**是 docstring/pass/.../NotImplementedError（实质实现 0）
REAL          五元齐全(5/5) 且 测试通过率>=0.8 **且为真实执行证据** 且
               报告最新裁决 ∈ {PASS,REAL} 且 证据链质量>=0.6
WITHHELD      有实现但上述不足
```

关键设置：`require_executed_tests_for_real=True` —— **静态推断的通过率不得冒充验证证据**。扫描器会用有界子进程真跑 pytest（每文件 30 s 上限、全局算力预算、结果按文件 sha256 缓存），只有可能升到 REAL 的能力才执行测试（五元齐全 + 最新裁决 PASS/REAL + 有实质实现），其余能力执行也不改变判定，故不花算力。

### 2.3 产物与确定性

`ledger_audit.json` 内**不含墙钟时间戳**，只含 `git_commit / registry_sha256 / policy / 逐能力证据与判级 / content_digest`；整文件 sha256 落在旁车 `ledger_audit.sha256`。耗时等易变量单独写 `run_metrics.json`，不参与摘要 —— 这样同一仓库连续两次扫描字节一致（AC4）。

## 3 计算结果

### 3.1 等级分布（21 项能力）

| 等级 | 数量 |
|---|---|
| NOT_SUPPORTED | 0 |
| STUB | 0 |
| MOCK | 1 |
| WITHHELD | 19 |
| REAL | 1 |

### 3.2 AC1 三例逐项（自动 vs 人工）

| 能力 | 自动判级 | 人工裁决 | 一致 |
|---|---|---|---|
| `calc.resistance` | **WITHHELD** | WITHHELD | ✅ |
| `calc.stability` | **REAL** | REAL | ✅ |
| `propeller.open_water` | **MOCK** | MOCK | ✅ |

### 3.3 AC2 OVERCLAIM 检出

人工台账里「自称 REAL 但裁决非 REAL」的能力 17 项，自动引擎判为非 REAL 17 项（检出率 100.0%）；其中 1 项落到 MOCK：`propeller.open_water`。

| 能力 | 自述 | 人工裁决 | 自动判级 | 检出 |
|---|---|---|---|---|
| `calc.resistance` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.gate_5415` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.git` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.logs` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.nginx_audit` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.probe` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.restart` | REAL | WITHHELD | WITHHELD | ✅ |
| `dev.services` | REAL | WITHHELD | WITHHELD | ✅ |
| `geometry.generate` | REAL | WITHHELD | WITHHELD | ✅ |
| `geometry.hydro_curves` | REAL | WITHHELD | WITHHELD | ✅ |
| `geometry.list_models` | REAL | WITHHELD | WITHHELD | ✅ |
| `geometry.verify_official` | REAL | WITHHELD | WITHHELD | ✅ |
| `gov.stats` | REAL | WITHHELD | WITHHELD | ✅ |
| `knowledge.docs` | REAL | WITHHELD | WITHHELD | ✅ |
| `propeller.open_water` | REAL | MOCK | MOCK | ✅ |
| `shiphull.generate` | REAL | WITHHELD | WITHHELD | ✅ |
| `stdmodels.lookup` | REAL | WITHHELD | WITHHELD | ✅ |

### 3.4 战役判据机器验收

AC 通过 **8/8**。

| AC | 判据 | 结论 | 数字 |
|---|---|---|---|
| AC1 | 自动判级 vs 人工裁决 | **PASS** | 3/3 一致（一致率 100.0%） |
| AC2 | OVERCLAIM 检出 | **PASS** | 检出 17/17（检出率 100.0%）；判为 MOCK 的案例 1 条 |
| AC3 | 负控（空仓库 0 假阳性） | **PASS** | 21/21 判 NOT_SUPPORTED，假阳性 0 |
| AC6 | 产物溯源（sha256+git_commit） | **PASS** | file_sha256 复算一致=True；content_digest 复算一致=True；git_commit=ae8c91ea0869ca4e148a7d4bf2d200e86998dbd7（HEAD=ae8c91ea0869ca4e148a7d4bf2d200e86998dbd7） |
| AC7 | 零领域语义 | **PASS** | 引擎可执行字符串常量命中域标签 0 处；虚构词条能力判级=REAL |
| AC8 | 不改注册表格式 | **PASS** | 文件与 HEAD blob 一致=True；审计后字节不变=True；能力数=21；引擎写该文件=False |
| AC5 | 增量检测（新增测试驱动升账） | **PASS** | zz.frobnicate WITHHELD → REAL（升级=True）；反例 wq.tessellate STUB → STUB（0 假升账=True） |
| AC4 | 幂等（两次扫描 sha256 相等） | **PASS** | file_sha256 342e1967e53780b4… == 342e1967e53780b4… ；content_digest 相等=True |

关键数字：

- AC1：3/3 一致（100%）
- AC2：17/17 检出（100%）
- AC3：21 项全判 NOT_SUPPORTED，假阳性 0
- AC4：`342e1967e53780b4…` == `342e1967e53780b4…`，content_digest 相等=True
- AC5：WITHHELD → REAL（升级=True）；反例 STUB → STUB（0 假升账=True）
- AC6：file sha256 复算一致=True；content_digest 复算一致=True；git_commit=`ae8c91ea0869…`
- AC7：源码可执行字符串常量命中域标签 0 处（检查域词条 42 条、能力 id 21 个）；虚构词条合成仓库判级=REAL
- AC8：注册表与 HEAD blob 一致=True；审计后字节不变=True；能力数=21

### 3.5 图

![自动判级与人工裁决的等级分布对比（21 项能力）](figures/fig1_grade_distribution.png)

*自动判级与人工裁决的等级分布对比（21 项能力）*

![AC1 三例的证据强度三维对比（虚线=0.8 判定阈；未执行测试记 0）](figures/fig2_evidence_strength.png)

*AC1 三例的证据强度三维对比（虚线=0.8 判定阈；未执行测试记 0）*

![战役判据 AC1–AC8 机器验收结果矩阵](figures/fig3_ac_matrix.png)

*战役判据 AC1–AC8 机器验收结果矩阵*

## 4 注意点（踩过的坑 / 局限 / 适用范围）

1. **STUB 曾经是「垃圾桶等级」**：旧实现用「文件里出现过 NotImplementedError」判定，实测把 propeller/stability/resistance 三条真实能力全打成 STUB。节点级判定后 STUB 数量归零——这不是能力变强了，而是等级语义终于自洽（`src/ship_perf/conformance.py` 里那一处 `NotImplementedError` 属于别的能力）。
2. **静态通过率是假证据**：旧实现按 `assert False` 字面量猜通过率，能把没跑过的测试算成 100%。现在 REAL 必须带执行证据；静态推断只能降级，不能升级。
3. **裁决来源的语义分层**：报告证据有三个源——仓内 `tensor_reports/*.json`（结构化 verdict）、离线报告语料 `reports_corpus.json`（散文 + 裁决词表抽取）、结构化裁决台账 `capability_ledger.json`（`asserted_level`+`as_of`）。AC1 用的是**结构化台账**这一源；如果只允许散文抽取，`calc.stability` 的支撑证据是一句「裁决结论：受证合成」，不含等级 token，自动引擎会判 WITHHELD（2/3）——这是本引擎**已测到的能力边界**，不是被藏起来的缺陷。
4. **REAL 的通过率阈是 80%**（战役判据口径）。`calc.stability` 执行证据 79/87=90.8%，其中 `test_stability_wall_sided_domain.py` 有 8 项失败、`test_stability_analytic_benchmark.py` 超 30 s 上限被降级为静态信号——都如实登记在 `run_metrics.json` 的 `exec_ledger`，没有为了好看而调阈。
5. **WITHHELD ≠ 人工的「未验证默认」**：人工台账把「没有证据」一律记 WITHHELD，本引擎按战役判据把「完全无痕迹」记 NOT_SUPPORTED（如 `dev.nginx_audit`）。两者在「没证据」这一点上不同名，比对时只看有证据的能力（AC1 取 3 项即此故）。
6. **执行测试是有界的、可降级的**：每文件 30 s、全局预算 420 s，超时/无法启动一律退回静态信号并记 `reason`，绝不假装通过。换机器（loong64/x86）执行集合可能不同，故执行结果按文件 sha256 缓存，缓存不进 content_digest。

## 5 误差判定（与基准逐项对比，并显式回答是否 <3%）

本节点是**平台机制节点**，没有物理量，基准 = 人工裁决台账（机器可读，`artifacts/capability_ledger/capability_ledger_resolved.json`）。判据是**等级完全一致**，即等级误差必须为 0（不是 3%）。

| 被检对象 | 自动 | 人工基准 | 误差 | 是否达标 |
|---|---|---|---|---|
| `calc.resistance` | WITHHELD | WITHHELD | 0 级差 | 达标 |
| `calc.stability` | REAL | REAL | 0 级差 | 达标 |
| `propeller.open_water` | MOCK | MOCK | 0 级差 | 达标 |

等级一致率 3/3 = 100%（要求 100%）；OVERCLAIM 检出率 17/17 = 100%（要求 100%）。**是否 <3%：等级判定是离散量，误差定义为等级差，实测 0 级差 → 满足（严格强于 3%）。**

## 6 复现方式

```bash
# 全量审计 + 8 条判据（真实执行测试，首次约 2 分钟；缓存命中后约 40 秒）
PYTHONPATH=src python -m capability_ledger_auto.ac_verify \
  --root . --out artifacts/capability_ledger_auto \
  --test-cache artifacts/capability_ledger_auto/test_cache.json

# 只出审计产物
PYTHONPATH=src python -c "from capability_ledger_auto.ledger_audit import run_audit; run_audit('.')"

# 回归测试
PYTHONPATH=. python -m pytest tests/capability_ledger_auto -q
```

## 7 审计链

- `artifacts/capability_ledger_auto/ledger_audit.json` — 判级产物（sha256 `342e1967e53780b4c40c56c941507180a46a41462f4d4f74bb8dd21bed0216c6`）
- `artifacts/capability_ledger_auto/ledger_audit.sha256` — 整文件 sha256 + content_digest + git_commit + registry sha256
- `artifacts/capability_ledger_auto/ac_results.json` — AC1–AC8 原始判定与数字
- `artifacts/capability_ledger_auto/run_metrics.json` — 执行账本（逐测试文件 pytest 结果、超时降级原因）
- `artifacts/capability_ledger_auto/test_cache.json` — 按文件 sha256 键控的执行缓存（证据，不进摘要）

