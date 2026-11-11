# TensorPlatform 质量审计报告 · AUDIT-ASSET-12REPO（m14484）

- 审计对象：`cxs503/TensorPlatform`，`main` = `bce00c4eb`（2026-11-11 15:09 +0800），工作树 `agent/m14484`
- 审计窗口 **W1**：`19b9f30c0`（Merge #966，2026-11-10 16:04）→ `HEAD`，含 **73 个已并 agent PR / 297 commits**
- 审计人：`audit2-ab`（本单 m14484）
- 唯一核心标准：**可验证的功能交付**（非任务数 / PR 数 / 行数）
- 产物（全部可复算）：
  - 扫描器 `tools/asset_audit/repo_asset_scan.py`、`tools/asset_audit/mock_smell_scan.py`、`tools/asset_audit/audit_quality_signals.py`
  - 独立复算探针 `artifacts/audit_m14484/probe_{moment_remap,conformal,capability_sql}.py`
  - 机器产物 `artifacts/audit_m14484/*.json`、图 `artifacts/audit_m14484/figures/*.png`
  - 判级落表 `tensor_maturity_verdicts`（repo=`TensorPlatform`，verdict_by=`audit2-ab`，本报告附表）

---

## 0. 方法与边界（先声明限制，避免读者高估覆盖面）

| 项 | 内容 |
|---|---|
| 覆盖方式 | **全量人口普查**（2482 个变更文件逐文件分类，非抽样）+ **抽样深审**（14 个功能域，含 6 次真实执行） |
| 未覆盖 | 全仓 4612 个 `.py` 的逐文件人工阅读；全量 pytest 全仓回归（未跑，见 §7 not_run）；窗口外的历史代码 |
| 环境限制（诚实标注） | 沙箱内 `psycopg2` 为 stub（`RuntimeError: psycopg2 stub: no real driver in loong64 sandbox`），因此 **Postgres 路径的端点无法在沙箱内实跑**；SQLite 路径可实跑，已实跑 |
| 证据口径 | 每条结论附 `文件:行号` / 工件路径 / 测试名 / DB 查询；L≥2 全部有执行证据；L≥4 有基准对照数字（本报告 §5） |

复算命令（任何人可重跑，均在窗口 HEAD 上验证通过）：

```bash
python3 tools/asset_audit/repo_asset_scan.py --base 19b9f30c0 --head HEAD \
        --out artifacts/audit_m14484/asset_scan_w1.json
python3 tools/asset_audit/audit_quality_signals.py --scan artifacts/audit_m14484/asset_scan_w1.json \
        --out artifacts/audit_m14484/quality_signals.json
python3 artifacts/audit_m14484/probe_moment_remap.py      # 17.4s
python3 artifacts/audit_m14484/probe_conformal.py         # 2s
python3 artifacts/audit_m14484/probe_capability_sql.py    # 3s
python3 artifacts/audit_m14484/make_audit_figures.py
```

---

## 1. 资产盘点（窗口 W1，全量 2482 文件 / +618,876 / −1,958 行）

| 类别 | 文件 | 新增行 | 删除行 | 说明 / 判定口径 |
|---|---|---|---|---|
| core（疑真算法） | 1,256 | 342,594 | 365 | 有函数体、非空壳、非 API/测试/文档 |
| test（测试代码） | 457 | 108,888 | 474 | `tests/`、`*_test.py`。其中窗口内新增可统计测试文件 314 个 |
| evidence（证据工件/数据快照） | 254 | 107,184 | 718 | `quality/`、`data/`、`*.json` 快照。**不是功能** |
| doc（文档报告） | 264 | 41,875 | 211 | `*.md`。**不是功能** |
| api（新增接口） | 32 | 7,102 | 73 | 含 `APIRouter` 的路由文件 |
| reexport（`__init__.py` 导出垫片） | 157 | 7,064 | 115 | 不计功能、不计占位 |
| script（无函数脚本） | 33 | 2,093 | 0 | 一次性脚本 |
| other | 25 | 1,966 | 2 | 配置/二进制等 |
| placeholder（AST 级空壳） | 4 | 110 | 0 | 见 §4 方法学修正 |

图 2（`fig2_asset_categories.png`）= 上述分布的 log 刻度条形图，数字由 `asset_scan_w1.json` 程序化注入。

**分类学修正（本报告的方法学增量）**：首版扫描用「关键字密度」判占位，得 244 文件 / 25,761 行；改用 **AST 函数体检查**（函数体仅 `pass`/`...`/`return None`/`raise NotImplementedError`）后降到 **4 文件 / 110 行**，差异 3.2 倍来自把「文档里讨论 STUB 一词的模块」误判成占位。全局口径：窗口内新增 Python 共 **12,418 个函数，其中空壳 36 个 = 0.29%**。→ **本仓的"占位实现"问题被普遍高估，真实空壳几乎为零。**

---

## 2. 功能真实性六问（逐功能域）

标记：✅ 是 / ❌ 否 / ➖ 未验证（honest）

| # | 功能域（入口文件） | ①入口 | ②真执行计算 | ③接入主程序 | ④真实 I/O | ⑤完整运行案例 | ⑥独立基准 | 判级 |
|---|---|---|---|---|---|---|---|---|
| 1 | `backend/coupling/moment_remap.py:563 MomentRemap.apply` | ✅ | ✅ 矩修正闭式解 + Sutherland-Hodgman 裁剪 | ✅ 经 `backend/coupling/__init__.py:37` 导出、`spacetime_exchange.py` 使用 | ✅ `apply()→MomentRemapResult` 33 字段审计 | ✅ 探针 3 例 | ✅ **审计方独立复算**：线性场 m1 误差 3.2e-16 / m2 3.2e-15；负控制 1.86e-3；观测阶 2.22→2.35（见 §5.1） | **L4** |
| 2 | `src/coupling/temporal_sync.py:201 EnergyConservationTracker` | ✅ | ⚠️ 只做**记账**（喂入能量→算相对误差），不实现保守耦合格式 | ❌ 调用方仅 `generate_p251w2_n07_figures.py` + 自身测试 | ✅ API 返回 `conservation_error()` | ✅ 单测 25 例 | ❌ 无；单测是**公式代入自证**（手工喂 100.0/101.0 再断言误差<1%），非守恒格式验证 | **L1** |
| 3 | `backend/coupling/spacetime_exchange.py` | ✅ | ✅ 时空交换 + `composition_roundtrip` | ✅ `backend/coupling/__init__.py` 导出 | ✅ | ✅ `tests/coupling/test_spacetime_exchange.py` 通过 | ➖ 未独立复算 | **L3** |
| 4 | `tools/coupling/conservation_check.py:323 refinement_order` | ✅ | ✅ 重构/守恒残差/收敛阶证书 | ✅ 4 个 campaign 节点引用（DB 查询见 §6） | ✅ | ✅ `tests/test_conservation_check.py` **43 passed** | ⚠️ 仓内自带理论闸（donor 阶∈[0.85,1.25]、linear ≥0.95），非第三方基准 | **L3** |
| 5 | `tools/fidelity/multi_fidelity.py:169 conformal_quantile` | ✅ | ✅ split-conformal 有限样本分位 | ➖ 仅经报告脚本/测试引用 | ✅ | ✅ `tests/fidelity` **32 passed** | ✅ **审计方独立 MC**：20 种子覆盖 0.8992，CI95 [0.8916, 0.9067] 含目标 0.90（§5.2） | **L4** |
| 6 | `tools/fidelity/multi_fidelity.py`（多保真链 TierModel/FidelityChain） | ✅ | ✅ | ➖ | ✅ | ✅ 同上 32 passed | ➖ 无独立基准 | **L3** |
| 7 | `tools/maturity_inference`（五级成熟度推断） | ✅ `cli.py` | ✅ 证据图→等级推断、fail-closed | ⚠️ 2 个 campaign 节点引用；**未接入 CI**（`.github/workflows` 12 个门禁无一调用） | ✅ 产物 JSON | ✅ `cli golden` 实跑：40/40 正确、acc=1.0、macro-F1=1.0 | ⚠️ 基准=自建黄金集（40 行），naive baseline acc=0.5；**标签自撰，故不升 L4** | **L3** |
| 8 | `tools/mock_detector`（结构化 MOCK 探测） | ✅ `cli.py` | ✅ 确定性/幂等/单调三性质探针 | ❌ 无生产调用方、无 CI、campaign 引用 0 | ✅ | ✅ `cli fixtures` 实跑 **11/11 correct, FN=0 FP=0**（真/伪端点混合地面真值） | ✅ 有地面真值的判别力验证 | **L2** |
| 9 | `tools/criteria_drift` | ✅ `run_audit.py` | ✅ 判据漂移解析/匹配/量化 | ✅ 1 campaign 节点 | ✅ `quality/criteria_lint/evidence/*.json` | ✅ `tests/test_criteria_drift.py` **31 passed** | ⚠️ 无独立标注集校验 | **L3** |
| 10 | `tools/failure_miner` | ✅ `__main__.py` | ✅ 签名挖掘/聚类/台账 | ✅ `backend/api/tensor_failure_signatures.py` 暴露 4 端点 | ✅ 47 tests passed | ✅ | ➖ | **L3** |
| 11 | `backend/api/tensor_failure_signatures.py`（4 端点） | ✅ | ✅ 真 SQL | ✅ 挂载 `api/domain/tensor.py:28,60` | ❌ **平台库无 `failed_signature_ledger` 表**（`information_schema` 查询=0；建表仅发生在 `tools/failure_miner/ledger.py:18` 惰性路径） | ➖ 沙箱 psycopg2 stub，无法实跑 | ❌ | **L2** |
| 12 | `backend/api/capability_sql_trace.py`（10 端点） | ✅ | ✅ | ✅ `api/domain/tensor.py` | ✅ **实跑 PASS**：register 200 → SQLite 落库 → trace 200 回读；非法等级 400；未知 id 404（§5.3） | ✅ | ➖ | **L3** |
| 13 | `tools/vv/withheld_closure.py` | ✅ | ✅ | ✅ 51 个 campaign 节点引用 | ✅ | ✅ `--check` 实跑：43 单 PASS16/FAIL4/WITHHELD33、`audit_chain_ok=true` | ➖ | **L3** |
| 14 | `ops/taskplane/criteria_vitality.py` | ✅ | ✅ 变异注入/杀死率 | ✅ CLI 四子命令 | ➖ **未跑通**（`preregister/check` 均需 `--gate/--run` 前置工件，预算内未生成） | ❌ | ❌ | **L1** |

### 2.1 PR/报告声明 vs 代码实况的差异（逐条）

| 声明 | 实况 | 差异性质 |
|---|---|---|
| `docs/reports/p478w1_n03_mock_detector.md:204` 声明 `{"fixtures": 11, "correct": 11, "false_negatives": 0, "false_positives": 0}` | 审计方重跑 `cli fixtures` 得 **11/11, FN=0, FP=0** | ✅ **一致（声明可复现）** |
| `tools/maturity_inference cli golden` 断言 acc=1.0 且"passes": true | 实跑复现 40/40、acc=1.0；但对照 baseline 亦由同仓自撰 | ⚠️ 数字真实，基准独立性不足 |
| `src/coupling/temporal_sync.py` 文档字符串称 "For a conservative coupling scheme … Acceptance: conservation_error < 0.01" | 单测确证的是**误差公式本身**（手工喂入能量），没有任何耦合格式被证明守恒 | ⚠️ 措辞容易被读成"已证守恒"，实为记账工具 |
| `tools/failure_miner` 声称台账已接入平台（4 个 API 端点） | 端点挂载成立；**但平台库缺表**，首次调用会 500，直到 miner 跑过一次 | ❌ 集成声明与运行时实况不符 |
| 262 个新增 md 报告共引用 499 处 `artifacts/...` 路径 | 仓内存在 **28 处**（缺失 471 = **94.4%**） | ❌ 报告数字不可从 main 复现（详见 §6 膨胀清单） |

---

## 3. 成熟度判级（owner 六级，逐模块落表）

| 级别 | 条目数 | 条目 |
|---|---|---|
| **L5** | 0 | —（窗口内无"解决此前未解决问题/新方法/新规律"的实证条目） |
| **L4** | 2 | moment_remap（2D 保守重映射）；conformal_quantile（覆盖率） |
| **L3** | 8 | spacetime_exchange；conservation_check；multi_fidelity 链；maturity_inference；criteria_drift；failure_miner；capability_sql_trace；withheld_closure |
| **L2** | 2 | mock_detector；tensor_failure_signatures API |
| **L1** | 3 | temporal_sync（记账自证）；criteria_vitality（未跑通）；窗口内 test additions（作为能力证据面） |
| **L0** | 4 | 窗口内 doc additions；evidence 工件（quality/、data/）；占位空壳（4 文件/36 函数）；74 个无生产调用方的 core 模块 |

> 合计 19 条 = L4 2 + L3 8 + L2 2 + L1 3 + L0 4。注：L 分布只统计本报告**逐条落表**的 19 个 scope，不是全仓能力全集；窗口内 1,256 个 core 文件中绝大多数未被逐条判级（见 §7 not_run）。

---

## 4. 六问表之外的三个结构性发现

### 4.1 「未接入」的真实含义：不是死代码，多数是"只有测试养着"

- 扫描判"无 import、无 `__main__`"的 core 模块 **74 个**；
- 其中 **68 个（92%）被 `tests/` 引用** → 它们是**库模块，唯一调用方是测试**，不是垃圾代码；
- 真正无任何引用（orphan）**6 个**：`post_report_m11186.py`、`tools/capability_drift/examples/selfcheck_case.py`、`tools/capability_maturity/trials/synthetic_kernels.py`、`tools/mock_detector/ledger_link.py`、`tools/salvage/gitutil.py`、`tools/task_salvage/sift.py`。

**含义**：平台的主要欠账不是"写了没人用"，而是**能力停在第 2 级——有实现、有单测，但没有生产调用路径**。晋级 L3 的工作量远小于新写功能。

### 4.2 测试强度：断言密度高，但 29.5% 属弱断言

| 指标 | 数值 |
|---|---|
| 窗口新增测试文件（含用例） | 314 |
| 断言总数 | 12,280 |
| 弱断言（`is not None` / `isinstance` / 纯真值 / `len==0`） | 3,627 = **29.5%** |
| 零断言测试文件（"只跑不判"） | 2（均为 `conftest.py`/fixtures，无害） |
| 平均断言/用例 | 见 `quality_signals.json` |

→ 没有"只执行不断言"的伪测试；弱断言占比属正常量表型测试水准。

### 4.3 重复实现：3 个候选（全部带指针，未定谳）

| 新实现 | 既有实现 | 函数名 Jaccard | 共有函数 | 判断 |
|---|---|---|---|---|
| `ops/taskplane/campaign_dispatcher.py` | `harness_gateway/mirror/campaign_dispatcher.py` | 0.80 | 12 | **疑似镜像复制**，需作者说明是否刻意（mirror 可能是有意为之） |
| `tools/p93w3_n10/report.py` | `tools/coupling/report.py` | 0.80 | 4 | 低置信（report 骨架雷同常见） |
| `tools/coupling_conservation/run_acceptance.py` | `tools/coupling_contracts/acceptance.py` | 0.60 | 6 | 低置信 |

### 4.4 性能/规模局限（L4 也不等于可用）

`moment_remap` 的重叠几何是**全对全**计算：实测量级 n=10→0.7s、20→7.3s、40→**91.9s**（≈O(N²) 单元对）。这意味着它在 2D 中等网格下已经不可用，3D 明确不支持（`MomentRemap.__init__` 对 `dim!=2` 抛错）。**L4 的"算法正确"不能外推为"工程可用"。**

---

## 5. 独立复算结果（数字表，全部程序化生成）

### 5.1 moment_remap（`artifacts/audit_m14484/probe_moment_remap.json`，图 1）

| 算例 | m1 相对误差 | m2 相对误差 | 可行性 |
|---|---|---|---|
| 线性场 n=8, seed=1 | **3.23e-16** | **3.24e-15** | feasible=True |
| 线性场 n=12, seed=7 | **4.85e-16** | **1.46e-15** | feasible=True |
| 负控制（donor + 无矩修正） | **1.86e-03** | **1.59e-03** | 未启用 |

收敛（光滑场 sin(πx)cos(πy)，体积加权 L1，h=1/8,1/12,1/16）：

| 算子 | 观测阶（两段） | 与理论一致性 |
|---|---|---|
| donor-cell | 1.67 → 1.13 | ≈1 阶（受负控制限定） |
| linear + vertex limiter | 2.19 → 2.26 | ≈2 阶 ✅ |
| linear + moment-2 | 2.22 → 2.35 | ≈2 阶 ✅ |

**判定**：线性场矩守恒达机器精度（~1e-15），负控制失败（~1e-3）证明矩修正项非装饰；收敛阶与二阶理论一致 → **独立基准对照通过，误差远 < 15%（实际 <1e-12 相对误差）**。

### 5.2 conformal_quantile 覆盖率（`probe_conformal.json`，图 3）

| 量 | 值 |
|---|---|
| 目标 level | 0.90 |
| 经验覆盖（20 种子均值，n_cal=400, n_test=4000） | **0.8992** |
| 均值 CI95 | [0.8916, 0.9067] — **含 0.90** ✅ |
| 单种子最小 / 最大 | 0.8618 / 0.9240（标定随机性，理论预期内） |
| 饱和次数 | 0 |

统计口径修正（审计自身纠错记录）：首版探针拿"逐种子二项下界"当判据，得到误报 FAIL；split-conformal 的保证是对标定随机性的**边缘**保证，正确判据是种子均值 CI 是否覆盖 level。**这是审计方判据的错，不是被审代码的错**——记录于此以免后续审计重复此错。

### 5.3 capability_sql_trace 数据通路（`probe_capability_sql.json`）

| 步骤 | 期望 | 实测 |
|---|---|---|
| 非法 `claimed_level=BOGUS` | 400 + 原因 | 400 `Invalid claimed_level: BOGUS` ✅ |
| 合法注册 | 200 created | 200 + `registered_at` ✅ |
| trace 回读 | 200 + claimed_level=REAL | 200，字段一致 ✅ |
| 未知 id | 404 | 404 `Capability not found` ✅ |
| 持久化 | SQLite 有行 | `[('audit.probe','REAL')]` ✅ |

**附带结构发现**：该 API 的证据库是**旁路 SQLite**（`backend/services/capability_sql_tracer.py:63` 默认 `data/capability_evidence.db`），不是平台 Postgres 台账 → 与 `tensor_maturity_verdicts`/`capability_ledger` 存在双写/分叉风险，建议纳入统一台账或明确声明为只读派生副本。

---

## 6. Top 问题（按影响排序）

| # | 问题 | 证据 | 影响 | 建议 |
|---|---|---|---|---|
| P1 | **报告工件 94.4% 不入库**，262 个新报告的 499 处 `artifacts/` 引用只有 28 处能在 main 找到 | `asset_scan_w1.json` + 引用存在性扫描（本报告脚本可复算） | 报告里的数字从 main 无法追溯=证据纪律失效（"报告≠证据"的根因） | 报告只引用**入库**工件，或在报告内嵌 sha256+生成命令；CI 增加"引用路径存在性门" |
| P2 | **两套证据存储**：平台 Postgres 台账 vs `data/capability_evidence.db` SQLite | `capability_sql_tracer.py:63`、`api/capability_sql_trace.py:32` | 台账分叉、审计口径漂移 | 明确唯一真源或声明派生关系 |
| P3 | **API 读到不存在的表**：`failed_signature_ledger` 在平台库缺失，建表只在 miner 惰性路径 | `information_schema` 查询=0；`tools/failure_miner/ledger.py:18`；`backend/api/tensor_failure_signatures.py:64` | 端点首次调用 500 | API 启动时 `ensure_schema()`（幂等），或加"未初始化则 503+提示" |
| P4 | **L2 平台期**：74 个无生产调用方模块中 68 个只有测试养着 | `asset_scan_w1.json:unwired_core_modules` | 能力不产生平台价值 | 每波强制"至少一个模块接到生产路径"的验收项 |
| P5 | **自证型验证**：`temporal_sync` 等模块单测只证公式，不证物理格式 | `tests/test_temporal_sync.py:144-180` | 报告易被读成"守恒已验证" | 补"守恒格式+参考解"案例，或把措辞降级为"记账" |
| P6 | 收敛/守恒类工具无 CI 门（12 个 workflow 无一调用 maturity_inference / mock_detector / conservation_check） | `.github/workflows/` 清点 | 能力易回退 | 把 `mock_detector fixtures`（11/11）与 `maturity_inference golden`（40/40）挂成 CI 步骤，成本秒级 |

### 6.1 「曾声称为功能/验证，实为文档或测试自证」膨胀清单

| 条目 | 声称为 | 实为 | 判级依据 |
|---|---|---|---|
| `src/coupling/temporal_sync.py` 能量守恒 | "conservation_error < 0.01 验收" | 记账类 + 公式代入单测；无守恒格式、无参考解、无生产调用方 | L1 |
| `ops/taskplane/criteria_vitality.py` 变异体检 | CLI 完整四子命令 | 需前置工件，本审计窗口内**无法跑通一次**；路径存在但未证 | L1（honest 未验证） |
| 262 个新增报告 | 引用 499 处证据工件 | 94.4% 路径在 main 不存在（且未被 `.gitignore` 全量豁免） | L0（证据面） |
| `quality/**/evidence/*.json`（32,106 行）与 `data/**`（40,949 行） | 审计/治理证据 | 证据快照本身，非功能；行数常被误计入"交付量" | L0 |
| `backend/api/tensor_failure_signatures.py` | "失败签名台账 API" | 路由完整、SQL 真实，但**后端表缺失** | L2 |
| 窗口内 457 个测试文件 / 108,888 行 | 质量证据 | 真测试（12,280 断言，29.5% 弱断言），但**不是功能交付**，不计级别 | L1 |

---

## 7. 未验证 / 未知（honest，不得读作通过）

| 项 | 状态 | 原因 |
|---|---|---|
| 全仓 pytest 回归（4612 py / 761 测试文件） | **not_run** | 预算内只跑了 6 个目标套件（25+62+32+31+43+47+11 fixtures），全量需集群长时间 |
| Postgres 依赖的端点运行时行为 | **unknown** | 沙箱 `psycopg2` 为 stub；表存在性用 SQL 直查确认，但 500 未实测 |
| 1,256 个 core 文件中 1,242 个 | **未逐条判级** | 抽样 14 域深审 + 全量结构扫描 |
| 平台真实流量下的 MOCK 率 | **unknown** | `mock_detector scan` 需运行中的服务（本沙箱未起 backend） |
| 3 个重复实现候选 | **未定谳** | 需作者意图说明（镜像可能有意） |
| `L5` 条目 | **0** | 无实证；不为凑表拔高 |

---

## 8. 结论与下一步（按性价比排序）

1. **先把"证据入库"变成机器门**（P1）：报告引用的工件必须存在，否则 CI 红。单条规则即可消掉最大一类虚高。
2. **修 P3 的建表路径**：`ensure_schema()` 幂等前置，成本 < 30 行。
3. **把已有 L2/L3 能力接到生产路径**（P4）：68 个"只有测试养着"的模块里挑 5 个接进 backend service/API，比新写 5 个模块更有价值。
4. **挂两个秒级 CI 门**（P6）：`mock_detector fixtures`、`maturity_inference golden`，均已在本次审计中实跑通过，接入风险低。
5. `moment_remap` 若要上工程（L5 路径），先解决重叠几何 O(N²)：候选方案=单元包围盒 BVH + 只对相交对做裁剪；当前 40×40 需 92s 不可接受。

---

## 附表：判级落表清单（`tensor_maturity_verdicts`，repo=TensorPlatform，verdict_by=audit2-ab）

| scope | level | 主要证据指针 |
|---|---|---|
| `backend/coupling/moment_remap.py` | 4 | `artifacts/audit_m14484/probe_moment_remap.json`；`tests/test_coupling_remap.py`(25 passed) |
| `backend/coupling/spacetime_exchange.py` | 3 | `tests/coupling/test_spacetime_exchange.py`；`backend/coupling/__init__.py:45` |
| `src/coupling/temporal_sync.py` | 1 | `tests/test_temporal_sync.py:144-180`（公式代入）；调用方仅绘图脚本 |
| `tools/coupling/conservation_check.py` | 3 | `tests/test_conservation_check.py`(43 passed, `order_certificate`)；DB 4 campaign 节点 |
| `tools/fidelity/multi_fidelity.py:conformal_quantile` | 4 | `artifacts/audit_m14484/probe_conformal.json`；`tests/fidelity`(32 passed) |
| `tools/fidelity/multi_fidelity.py:chain` | 3 | `tests/fidelity`(32 passed)；无独立基准 |
| `tools/maturity_inference` | 3 | `artifacts/audit_m14484/maturity_golden.log`(40/40, acc=1.0, baseline 0.5) |
| `tools/mock_detector` | 2 | `cli fixtures` 11/11 FN=0 FP=0；无生产/CI 调用方 |
| `tools/criteria_drift` | 3 | `tests/test_criteria_drift.py`(31 passed)；`quality/criteria_lint/evidence/` |
| `tools/failure_miner` | 3 | `tests/test_failure_miner.py`(47 passed)；API 挂载点 `api/domain/tensor.py:28` |
| `backend/api/tensor_failure_signatures.py` | 2 | 4 路由可挂载；`failed_signature_ledger` 缺失（SQL 查询 0 行结构） |
| `backend/api/capability_sql_trace.py` | 3 | `artifacts/audit_m14484/probe_capability_sql.json`(PASS) |
| `tools/vv/withheld_closure.py` | 3 | `--check` 实跑 43 单（PASS16/FAIL4/WITHHELD33）；51 campaign 节点 |
| `ops/taskplane/criteria_vitality.py` | 1 | 未跑通（缺 `--gate/--run` 前置工件） |
| `window:test_additions` | 1 | 314 文件/12,280 断言/弱断言 29.5%（`quality_signals.json`） |
| `window:doc_additions` | 0 | 262 md；499 引用中 471 缺失 |
| `window:evidence_artifacts` | 0 | 254 文件 / 107,184 行，非功能 |
| `window:placeholder_stubs` | 0 | 36/12,418 空壳函数 = 0.29% |
| `window:unwired_core_modules` | 0 | 74 个无生产调用方（68 仅测试引用，6 orphan） |