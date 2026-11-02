# m2049 · 闸门接线真实性审计（Gate wiring reality audit）

**席位**：tp-mod-1（models 席自找活） · **单号**：m2049 · **分支**：`agent/m2049`
**EDIT_WHITELIST**：`docs/`, `tools/`, `tests/`, `artifacts/`
**产物**：`tools/audit/gate_wiring_audit.py`（审计器，纯标准库）· `tools/audit/gen_gate_wiring_figure.py`（出图，零手抄）·
`tests/test_gate_wiring_audit.py`（7 项全绿）· `artifacts/m2049_gate_wiring/gate_wiring.json|.md` · 图 `fig_gate_wiring.png`

---

## ① 案例介绍（背景 / 对象 / 参数）

owner 的三大优先级是「验证欠账 > 功能补全 > 失效测试补齐」。平台治理 KPI 里有一条原文是
「M-G1 schema+perf_gate **已就绪待全量接线**」——**"就绪"与"接线"是两件事**：一个门禁模块存在、
单测全绿，却可能在**生产路径上从不执行**，此时它的"保护"只存在于报告里。这正是本次要清偿的验证欠账：
**没有人知道 32 个门禁模块里，有几个真的挂在了活的路径上。**

审计对象不是某个物理量，而是平台的**诚实性基础设施**本身（闸门/门禁模块）。参数表：

| 项 | 值 |
|---|---|
| 仓库 | TensorPlatform（本工作树，只读扫描） |
| 解析 python 文件 | **1410** |
| 发现的闸门模块（生产根，排除测试树） | **37** |
| 运行时入口 | `backend/main.py`, `backend/factory_app.py` |
| CI 实际执行的脚本 | _无_ |
| CI 执行的测试目标 | `tests`, `tests/factory`, `tests/test_research_factory.py` |
| 文档中作为命令被调用的工具 | 133 个 |

判据（机器可判、离线可重跑）——每条闸门按**静态可达性**落入五级判决梯：

`WIRED_RUNTIME`（可从活的应用 import 闭包到达）> `WIRED_CI`（被 CI/Makefile 实际执行的脚本拉入）>
`TOOL_ONLY`（只在 tools/ 脚本可达，无常驻/CI 路径）> `TEST_ONLY`（只有测试 import，**生产零路径**）> `ORPHAN`（全仓零引用）。

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **AST 建图**：对全仓 .py 做 `ast.parse`，抽取本地 import 边；点号名→文件用"包后缀索引"解析（一个文件在不同
   sys.path 切法下可有多个合法点号名，例如 `backend/services/x.py` 既可 `backend.services.x` 也可 `services.x`）。
2. **入口识别**：运行时入口取 `backend/main.py` / `*factory_app.py`；CI 入口**只取命令体**（workflow 的 `run:`
   块、YAML 内联 `- run:`、Makefile 的 tab 配方行）——**`paths:` 触发器里的文件名不是执行**。
3. **传递闭包**：从每类入口做 BFS 得到可达集，判决梯取最高命中。
4. **旁证维度**：文档里以命令形式出现的工具（`python3 tools/x.py`）记为"**带外调用**"（弱证据，文档可能过期）。
5. **可红性**：审计器自身用合成小仓 fixture 钉住五级判决（每级一个模块，由构造保证答案），
   另加两条反例：`paths:` 触发器不得算接线、`backend/` 无 `__init__.py` 时的点号解析必须落到正确文件。

## ③ 计算结果（程序化注入，零手抄）

判决分布（n=37）：

| 判决 | 数量 | 含义 |
|---|---|---|
| `WIRED_RUNTIME` | **7** | 确实挂在活路径 |
| `WIRED_CI` | **0** | 由 CI/Makefile 脚本执行 |
| `TOOL_ONLY` | **24** | 只能靠人手跑 tools 脚本 |
| `TEST_ONLY` | **2** | **仅测试 import，生产零路径** |
| `ORPHAN` | **4** | **全仓零引用** |

判决为 `TEST_ONLY` / `ORPHAN` 的 6 条（硬欠账本体）：

| 模块 | 判决 | 非测试 importer | 测试 importer | 文档带外调用 |
|---|---|---|---|---|
| `backend/middleware/truth_gates_meter.py` | `ORPHAN` | 0 | 0 | — |
| `backend/services/ice_integration/gate.py` | `TEST_ONLY` | 0 | 1 | — |
| `scripts/maturity_gate.py` | `TEST_ONLY` | 0 | 2 | — |
| `scripts/open_source_gate.py` | `ORPHAN` | 0 | 0 | — |
| `scripts/release_consistency_gate.py` | `ORPHAN` | 0 | 0 | — |
| `scripts/run_harness_v3_gate.py` | `ORPHAN` | 0 | 0 | docs/harness_v3_journal_resume.md:178, docs/harness_v3_journal_resume.md:231 |

**头条发现（逐条可复核）**：
1. `backend/middleware/truth_gates_meter.py` = **ORPHAN**：这是 truth-gates 三支柱之二「平台侧独立计量」的
   ASGI 中间件，全仓**零 import、`backend/main.py` 无 `add_middleware`**。换言之 `metered_usage` 表只能由
   `/truth-gates/metered-usage` 端点被动写入，**"基础设施视角的真实用量"这条证据链从未自动产生**。
2. `backend/models/truth_gates.py` 经解析器修正后为 `WIRED_RUNTIME`（首版误判 ORPHAN —— 见⑤踩坑）。
3. 4 个 `scripts/*_gate.py`（open_source / release_consistency / run_harness_v3）为 ORPHAN，
   `scripts/maturity_gate.py` 为 TEST_ONLY：**成熟度门、开源门、发布一致性门都只有单测在跑**，
   CI 只执行 `python -m pytest -q tests/`，没有任何 gate 脚本被 CI 调用（`WIRED_CI=0`）。
4. 平台核心报告门 `backend/services/report_gate.py` 为 `TOOL_ONLY`（只有 `backend/tests/*` 与
   `tools/audit/report_gate_audit.py` import 它）：**门禁函数存在、可被工具调用，但发布路径本身不 import 它**
   ——与报告 1743「路由改单一真源」的结论互为独立旁证（不同方法、同一方向）。
5. 附带的**真 bug**：`tools/verify_icing_analytic_limits.py` 静态 `SyntaxError: '{' was never closed`（第 258 行）
   ——该工具在任何调用下都无法运行，属于纯失效件。

## ④ 结果图

`fig_gate_wiring.png`：左图 = 五级判决分布条形图（`WIRED_RUNTIME 7 / WIRED_CI 0 /
TOOL_ONLY 24 / TEST_ONLY 2 / ORPHAN 4`）；右图 = 每个闸门一个点的
判决条带图（▸ 标记文档以命令形式调用过、但不在 CI 路径上的工具）。

> 无场量云图与解析解比较曲线：本单审计对象是**静态代码可达性**（布尔分类），不存在物理场量，也没有"解析解"
> 可画——按报告纪律显式声明理由，不以装饰性曲线充数。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **首版解析器有真 bug（已修，记录在案）**：把 `from models.truth_gates import ...` 解析到了
   `backend/api/truth_gates.py`（同 stem 三文件中的按字典序首个），导致 `backend/models/truth_gates.py`
   被误判 **ORPHAN**。根因：点号名后缀只生成了"整路径 + 裸 stem"两种，漏掉 `models.truth_gates` 这种
   **中间切法**（`backend/` 无 `__init__.py`，但 `backend/models/` 是包）。修法：对每个"首段是包的切法"都生成候选名，
   并优先精确匹配；`tests/test_gate_wiring_audit.py::test_package_suffix_resolution` 钉死该回归。
2. **首版 CI 入口识别有假阳性（已修）**：`resident-community.yml` 的 `paths:` 过滤器里写了
   `scripts/verify_soul_phase1.py`，首版把触发器当执行，凭空"接线"了一个脚本。现在只认 `run:` 块与 Makefile 配方。
3. **同类误判第二例（已修）**：`_is_test_path` 曾按文件名 `test_*.py` 判定测试，把生产模块
   `backend/services/test_gate.py` 这类文件**整条踢出清单**；现改为**只按目录**判定。
4. **静态可达性只能证"无"，不能证"有"**：`import` 闭包证明"没人 import"是硬证据；但 `WIRED_RUNTIME` 只说明
   import 链存在，不说明该门禁在请求路径上真被**调用**（可能 import 了却条件短路）。`getattr`/字符串动态派发、
   subprocess 字符串、另一进程（如 :8001 服务侧）里的调用，本审计一律看不见。
5. **"声明 vs 判决"交叉核对在树内**WITHHELD**：全仓 .md 里**没有**任何一行同时含「已就绪/已接线」与 gate 字样，
   真正的声明写在仓外的 `guide_dashboard.json`（"M-G1 schema+perf_gate 已就绪待全量接线"）。因此本单只给判决，
   不作"声明违背"指控；`contradictions` 计数为 0 是**语料缺失**所致，不是"零矛盾"的证明。
6. **TOOL_ONLY 不等于坏**：tools/ 下大量是一次性审计/出图脚本（如本单的审计器自己），本就不该常驻；
   它们被单列为一类，正是为了避免把"一次性工具"与"该常驻却缺席的门禁"混为一谈。判坏要看该模块的**设计意图**。
7. **容量纪律**：开工 `load15 = 73.8`（nproc=128），全程单进程静态解析（~10 s）+ 一次 matplotlib 出图，未起重算力进程。

## ⑥ 误差判定（逐项，含是否 <3%）

| 关键量 | 基准（判据来源） | 结果 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| 五级判决分类正确性（合成 fixture 5 例） | 由 fixture 构造保证的答案 | **5/5 命中** | 0（布尔） | ✅ 是 |
| 反例①：`paths:` 触发器不得算接线 | 同上 | 通过（未见假接线） | 0（布尔） | ✅ 是 |
| 反例②：`backend/` 非包时的点号解析 | 同上 | 通过（落到 `backend/models/`） | 0（布尔） | ✅ 是 |
| 真仓人工复核抽样（n=5：middleware/truth_gates_meter、api/truth_gates、services/report_gate、scripts/maturity_gate、scripts/release_consistency_gate） | grep 全仓引用 + `main.py` 的 `add_middleware`/`include_router` 名单 | **5/5 与机器判决一致** | 0（布尔） | ✅ 是（样本内） |
| 同一量的**统计功效** | 二项 95% 下界（rule of three, n=5） | 下界仅 **≈54%** | — | ❌ **否**（n=5 太小，不能说"错误率<3%"） |
| 静态解析覆盖率 | `ast.parse` 成功文件 / 全部 .py | 1409/1410 | 解析失败 **1** 例（含工具本身 SyntaxError） | ❌ 否（非零，但已逐条列出） |

**诚实结论**：本单**没有**证明"平台门禁体系有效"，证明的是——**在当前工作树上，6 个门禁模块
（2 TEST_ONLY + 4 ORPHAN）没有任何生产路径**，其中 `truth_gates_meter` 的缺席直接意味着
「独立计量」支柱不产生数据。分类器在合成 fixture 上 5/5、真仓抽样 5/5，但**样本量不足以支撑"<3% 错误率"**的声明，
故按纪律如实标 ❌。人工复核是**偏向异常子集**的有偏抽样，不能外推为整体精度。

## 后续建议（给下次自找活的线索）

1. **接线补丁（需 :8001 服务侧仓权限，本单只出判定）**：在 `backend/main.py` 里 `app.add_middleware(TruthGatesMeterMiddleware)`
   并在 `metered_usage` 写入路径补 DB 会话工厂；验收门可直接用本审计器（`truth_gates_meter` 变为 `WIRED_RUNTIME` 才算清偿）。
2. **把闸门接进 CI**：`scripts/*_gate.py`（成熟度/开源/发布一致性）当前无人执行；CI 加一步即可把 4 个 ORPHAN 变成 WIRED_CI，
   并把本审计器作为"接线回归测试"跑在 CI 里。
3. **动态补位**：静态只能证"无"，下一步应在活服务上做**运行时埋点**（请求级计数器 × 门禁判定次数），
   把 `WIRED_RUNTIME` 的"有 import"升级为"有调用"的证据等级。
4. **可复用打法**：任何「机制就绪」类 KPI（M-G1、复现 ≥95%、谱系追溯 100%）都可套本单管道——入口×可达性×判决梯，
   先量"真的在跑吗"，再谈精度。
