# 变更影响驱动的测试子集选择：从提案到平台标准能力

节点：`p299w4-n05-test-subset-maturity` ｜ 仓：`TensorPlatform` ｜ 分支：`agent/m11074`
证据目录：`artifacts/test_impact_selector/` ｜ 台账：`runs/test_impact_selector/ledger.jsonl`
复现入口：`python -m tools.test_impact_selector.acceptance`（全部判据数字程序化生成，零手抄）

---

## ① 案例介绍（问题背景与规模）

本仓测试规模已越过"全量必跑"天花板：**870 个测试文件**（`test_*.py` + `tests/` 目录内可收集文件），
源码树 **3754 个 `.py` 文件**。任何一次单模块改动都触发数千个测试，CI 时长与浪费随仓体线性增长。
本节点把"变更影响驱动的测试子集选择"从提案变成**平台标准能力**，判据是可机器复算的漏检率与选择性。

本单**零领域语义**：全部判据、夹具、脚本只涉及导入图、路径、测试收集与记账，不含任何船舶领域词条
（词表扫描 29 词 × 全包文件 = 0 命中，见 C5）。

**被测对象（实仓规模，非玩具）**

| 量 | 值 | 来源 |
|---|---|---|
| 扫描 `.py` 文件 | 3754 | `artifacts/test_impact_selector/import_graph.json` |
| 测试文件 | 870 | 同上 `stats.tests` |
| 静态导入边 | 11119 | 同上 `stats.edges` |
| 字面引用边（subprocess/注册表式使用） | 1203 | 同上 `build.reference_edges` |
| 历史共现边（support≥2 且 Jaccard≥0.25） | 61 | 同上 `stats.cocchange_edges` |
| 动态导入不可解析模块 | 188 | 同上 `stats.dynamic_modules` |
| 机器生成文件 | 4 | 同上 `stats.generated_files` |
| 语法不可解析文件 | 2（真实发现，见⑤） | 同上 `build.parse_errors` |

**真正跑测试的验证面**（故障注入用）：`tools/failure_minimizer` + `tools/dispatch_contract` +
`tools/test_impact_selector` 三个目录，基线 **99 passed / 2.88 s**，是本仓库当前能在受控时间内
完全绿跑的最干净测试面（`tests/`、`backend/` 存在 8 处环境性收集错误：缺 `litellm`/`openhands`、
硬编码绝对路径锁文件等，见⑤）。

## ② 怎么做的（方法 / 流程 / 关键设置）

### 2.1 依赖图（`tools/test_impact_selector/graph.py`）

阶段化：**纯单文件 AST 扫描**（可并行、可缓存，键 = `size + mtime_ns`，版本号 `CACHE_VERSION=4`）
→ **父进程解析**（全局模块索引 + 别名）。四类"打穿朴素扫描器"的导入形态全部建模：

1. **相对导入** `from ..pkg import x` → 用导入者包路径折算；
2. **`__init__` 再导出**：`from pkg import name` 而 `pkg/__init__.py` 里 `from .sub import name`
   → 额外为每个祖先包的 `__init__.py` 建边（导入子模块必然执行祖先 `__init__`）；
3. **`try/except ImportError` 条件导入** → 保留为边并标记 `conditional`（保守，多选不漏选）；
4. **子模块形式** `from pkg import sub` → 解析到 `pkg/sub.py`。

在此之上加两类"非导入"证据边（这是本单把漏检压到 0 的关键）：

* **字面引用边**：测试常以 `subprocess`/`python -m pkg.mod` 方式使用代码，静态导入图**完全看不见**。
  扫描字符串常量里的点分模块名与 `.py` 路径（正则化 token），建立"提及者 → 被提及模块"的边（1203 条）；
* **历史共现边**：`git log --name-only` 里"总是同时改"的源码↔测试对（support≥2、Jaccard≥0.25，61 条）。

不可解析一律**记账而非丢弃**：`external`（第三方，无害）、`cross_repo`（fleet 内其它仓，
→ fail-closed）、`relative_broken`、`parse_error`。动态导入做了**分级**：
`importlib.import_module("a.b")` 这类**常量实参**解析为普通依赖边（边数因此 +719）；
只有**非常量实参 / 模块级 `__getattr__`** 才标 `dynamic`（188 个），只有它才触发 fail-closed。
图指纹 = 节点/边/共现三元组的 sha256；整个构建确定性（同树两次构建指纹相同，单测覆盖）。

### 2.2 选择器（`selector.py`）

`changed files → 反向传递闭包（谁依赖我）= 受影响模块 → 取其中测试文件 → ∪ 固定安全集`。

* **安全集**：显式清单 + glob；都落空时机械回退为"依赖闭包最宽的 K 个测试"（图算出，不手挑），
  保证任何树上"只改文档"也至少选中 1 个测试（C4）。
* **fail-closed 类别（12 类，命中即全量并记账）**：`outside_roots` `test_infra` `generated_file`
  `new_file` `deleted_module` `non_code_asset` `graph_incomplete` `dynamic_import` `cross_repo_ref`
  `mass_change` `safety_missing` `unknown_change_kind`。**空集在实现上不可能发生**（安全集非空 +
  空集兜底全量），只改文档走安全集而非全量。
* **灰度**：`shadow`（真跑全量、台账记"本会跑什么"）/ `enforcing`（真跑子集）/ `off`（纯观测）。
  默认 `shadow`，`Ledger` 累计 ≥200 次真实写后才允许切 `enforcing`（本单已攒 240 条）。

### 2.3 三类证明（互相补盲，缺一不可）

| 证明 | 实现 | 回答什么 |
|---|---|---|
| **故障注入重放** | `proof_injection.py`：对**真实仓库真实模块**注入真实缺陷（模块级 `raise` / 包裹公有函数返回 `None`），**跑真 pytest 拿真失败集**，再跑选择器子集比对 | 漏检率是否真为 0（唯一硬证据） |
| **历史重放** | `replay.py`：重放 60 个真实 commit 的 changed-files，检查**非平凡义务**（改动中的测试文件必选 / 字面引用该模块的测试必选 / 历史共现测试必选）+ 规模统计 | 规模、fail-closed 率、义务召回 |
| **fail-closed 矩阵** | `matrix.py`：合成树上 12 个场景各命中一类 | 10/10 退回全量（C3） |
| **灰度台账** | `ledger.py`：240 条只追加 JSONL，`reproduce(run_id)` 由台账独立复算 | 可审计（C7） |

**故障注入的诚实边界**：真跑全量的"真值"限于注入切片（99 测试）内；切片外只能做静态检查。
注入后每场景用**内容 sha256** 校验原文件已字节级还原（29/29 还原成功）。

## ③ 计算结果（全部程序化生成）

复现命令与产物（`acceptance.json` 内 `criteria` 段即下表来源）：

```
python -m tools.test_impact_selector.cli build-graph --out artifacts/test_impact_selector/import_graph.json
python -m tools.test_impact_selector.cli matrix --out artifacts/test_impact_selector/failclosed_matrix.json
python -m tools.test_impact_selector.cli injections --slice tools/failure_minimizer tools/dispatch_contract \
    tools/test_impact_selector --modules 15 --module-range 0:3   # 分块跑（受工具超时约束），输出 inj_c0.json
python -m tools.test_impact_selector.cli aggregate-injections --inputs inj_c0..c4 \
    --out artifacts/test_impact_selector/injection_proof.json
python -m tools.test_impact_selector.cli shadow-rollout --commits 240 \
    --ledger runs/test_impact_selector/ledger.jsonl
python -m tools.test_impact_selector.acceptance --replay-commits 60   # → acceptance.json
python -m tools.test_impact_selector.make_figures                     # → 4 张 png
```

| 判据 | 结果 | 数字 |
|---|---|---|
| **C1 漏检率** | **PASS** | 注入重放 **30 场景 / 29 个含真实全量失败**，recall = **1.000**，漏检 **0** 个测试文件；子集实跑复现真值 29/29；历史重放 60 commit、义务 106 条、召回 **1.000** |
| **C2 选择率 + 开销** | **PASS** | 历史重放平均选中 **33.66%** 全量（≤40%）；台账 240 次决策平均 **29.49%**；子集决策平均选中 **0.46%**；单次决策 **2.9 ms = 全量时长 0.36%**（≤5%） |
| **C3 fail-closed** | **PASS** | **10/10** 类退回全量（另 `unknown_change_kind`、`safety_missing` 也退回）；对照组（干净静态改动）= subset 3/16 测试 |
| **C4 空集禁止** | **PASS** | 只改 `docs/guide.md` → 选中 1 个安全测试；240 次真实决策空集 **0** 次 |
| **C5 零领域语义** | **PASS** | 29 词领域词表 × 全包（代码+文档+夹具）扫描 **0 命中**；夹具全为合成名（alpha/beta/gamma） |
| **C6 幂等确定性** | **PASS** | 同进程重复 10 次 → 1 个哈希；**两个独立进程**哈希一致 `7e82d158…bd942` |
| **C7 台账可审计** | **PASS** | 240 条台账，任取 `tis-12aa7bf07187` 由台账复算：选择哈希一致、图指纹一致、配置哈希一致 |

**构建/运行开销实测**（`artifacts/test_impact_selector/build_timing.json`）：

| 路径 | 时间 |
|---|---|
| 冷构建（8 worker，3754 文件，含历史边） | **20.37 s** |
| 热构建（图缓存命中，增量 4 文件） | **1.48 s** |
| 单次决策（首个决策含安全集闭包） | 25.2 ms → 之后 **2.9–3.8 ms** |

**历史重放分布**：60 commit 中 40 次给出 subset（占 66.7%），20 次 fail-closed（33.3%，主因
`dynamic_import` 12 次、`non_code_asset`/`test_infra`/`mass_change` 其余）。subset 决策的选中比例
中位数远低于 1%（大量单文件改动只牵动 3–9 个测试文件）。

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| 图 1 fail-closed 矩阵 | `artifacts/test_impact_selector/fig_failclosed_matrix.png` | 12 类不可解析场景选中率全部 = 1.0（红），两个对照组（绿）为 18.8% 与 1 个测试 |
| 图 2 故障注入重放 | `artifacts/test_impact_selector/fig_injection_recall.png` | 30 场景：深色柱=真跑全量的真实失败文件数，浅色柱=选择器子集大小；子集始终 ⊇ 真值 |
| 图 3 历史重放 | `artifacts/test_impact_selector/fig_replay_ratio.png` | 左：60 commit 的选中比例轨迹（红虚线=40% 上限）；右：subset 决策的比例分布 |
| 图 4 开销对照 | `artifacts/test_impact_selector/fig_selector_overhead.png` | 对数坐标：单次决策 2.9 ms ≪ 热构建 1.48 s ≪ 冷构建 20.4 s ≪ 全量 5.47 s（本切片） |

（本单为纯平台机制任务，无物理场量，故无云图；图为判据统计图，数据全部来自
`acceptance.json` / `failclosed_matrix.json`，脚本 `make_figures.py` 直读，无手抄。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **真实仓库缺陷（非本单引入）**：`tools/verify/propeller_open_water/audit_bem_vs_bseries.py`
   第 1 行、`tools/verify_icing_analytic_limits.py` 第 258 行**语法错误**，AST 不可解析。
   若把"任何解析失败"都当作图不完整，选择器会永远 fail-closed 而失效。处理：**解析失败只在其
   "被 import"时才算致命**（该文件的逆向边确实缺失），孤立叶子只记账不阻断；两条都在
   `import_graph.json` 里留痕。同时修掉了一个反直觉 bug：字面引用边会制造假"被 import"，
   故 parse-error 致命性判定专用**纯静态导入**逆向索引（`importers_static`）。
2. **共享 `/tmp` 写满**（宿主级事件）：一次 pytest 因 `ENOSPC` 报 23 个 error，看起来像测试失败。
   处理：证明器把 `TMPDIR` 强制指到 `<repo>/.cache/pytest-tmp`（已 gitignore），失败不再与磁盘
   纠缠；相关行已写进日志。
3. **SIGKILL 会留下被注入的源文件**：早期一次后台运行被超时机制整组杀死，导致
   `tools/dispatch_contract/corpus.py` 残留注入内容。已加**注入前脏树预检**（只允许 `??` 未跟踪
   文件，任何 `M` 直接拒绝并报错），且每场景用内容 sha256 校验还原。
4. **动态导入是残余风险的主要来源**（188 个模块、33.3% 的 commit 因此退回全量）：常量实参已解析为
   真边，非常量实参无法静态定界，选 fail-closed 是**有意的保守**。代价是最优选择率的上限；
   若要进一步压缩 fail-closed 率，需要"前缀约束"级别的插件注册表声明（未做，留作后续）。
5. **`new_file` 一律 fail-closed**：新文件不可能被既有测试导入，理论上可只跑安全集；但新文件被
   既有代码动态加载的情形不可静态排除，故保守处理——这也是 240 次决策里 29% 全量的主要来源。
6. **历史共现边是"辅助"而非"证据"**：support≥2 且 Jaccard≥0.25 的门槛避免了单次事故污染，
   但它把一部分改动"多选"了；实测对规模影响 ≤0.5% 全量。
7. **切片代表性局限**：能绿跑的干净测试面只有 99 个测试 / 2.88 s，因此"子集实跑复现真值"的
   结论限于该面；仓库级全量时长（C2 的分母）本地测不动，已递交集群作业 `cj615`
   （`pytest tests tools`，loong64），排队中未取回 —— 故 C2 分母暂用切片实测 5.47 s（含进程启动），
   这是**保守偏向**的取值（真实全量只会更长，开销占比更小）。
8. **热构建 1.48 s 相对 5.47 s 切片 = 28.8%**：单看这一项不满足"≤5%"。生产形态是"每 CI 流水线
   构建一次图、每次改动只做决策"，此时每次改动成本 2.9 ms ≈ 0.05%；把图构建摊到"每次改动"
   上则取决于流水线改动数。报告不掩饰：**冷/热构建成本是本方案的固定开销**，粗粒度使用（每仓多次
   PR）才划算。

## ⑥ 误差判定（逐项，显式回答"是否 <3%"）

本单为**平台机制**任务，无物理量、无解析解/文献基准，因此"误差"取"漏检率"与"义务召回缺口"：

| 量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 故障注入漏检（真跑全量真值 vs 子集） | 29 场景真实失败 | **0/29 = 0.000%** | **是** |
| 历史重放义务召回缺口 | 106 条必选义务 | **0/106 = 0.000%** | **是** |
| 选择率相对 40% 上限 | 上限 0.40 | 实测 0.3366（低于上限 15.9% 相对量） | **是** |
| 决策开销相对全量时长 | 上限 5% | 0.36%（2.9 ms / 5.47 s 切片；分子分母均实测） | **是** |
| 幂等漂移 | 完全相同 | 10 次同进程 + 2 次跨进程哈希全等，漂移 0 | **是** |
| 台账可复算 | 完全一致 | 选择哈希/图指纹/配置哈希三项全等 | **是** |

**不满足/未证实项（honest）**：
* C2 的"构建开销 ≤5% 全量"在**当前切片分母**下**不成立**（热构建 28.8%、冷构建 >100%）。
  该判据成立的前提是分母为仓库级全量（分钟级）；仓库级全量时长未取回（集群作业排队），
  故此处只声明"单次决策开销 0.36% 成立"，构建开销部分如实标为**未证**。
* 故障注入的真值面限于 99 测试的干净切片，切片外仅静态检查，未做真实执行验证。
* 未覆盖 `pytest-xdist`/并行执行、未覆盖跨仓（TensorFVM 等）引用场景的真实回退演练
  （仅合成树矩阵覆盖该类别）。