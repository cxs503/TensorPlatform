> **返修轮1（m15199）更新**：入口 `scripts/run_kpi_tools_5.py` 一次产出台账+基准表+图+集成调用+manifest；
> ref-vs-got 对照与预算敏感性见 `docs/KPI_TOOLS_5_repair_round.md`。本文保留第 2 轮原文。

# KPI-TOOLS-5 · 外部工具类接入平台（第 2 轮：调研结合 + 实现）

**锚点**：KPI-TOOLS-5 —— 「≥5 类外部工具接入平台并可用」。
**本单结论（先给判决）**：实测 **10 / 5 类 REAL**，裁定 **PASS**（`tools.external_tools.cli` 退出码 0）；
每类都带一次真实调用 + 数值不变式，凭证可复算，见 `artifacts/external_tools/latest.json`。

---

## ① 问题设定与基准出处

**问题**：平台要回答的不是「某库能不能 import」，而是「这一类外部工具在本环境里**可用**吗」。
「可用」被定义为可机器裁决的三段式：**探针命中**（环境里有） → **冒烟调用成功**（真跑一次） →
**数值不变式成立**（结果对得上一个独立基准）。任何一段缺失都不得记为 REAL。

**判据来源（外部锚点，均非本仓自创）**：

| # | 出处 | 被借用的形态 |
|---|---|---|
| A1 | Model Context Protocol 规范，`initialize` + `tools/list`（https://modelcontextprotocol.io/specification/2025-06-18） | 「先协商能力再宣告工具」：声明 ≠ 观测 |
| A2 | Python entry points 规范（https://packaging.python.org/en/latest/specifications/entry-points/） | 插件/注册表按稳定 id 查表挂载，宿主不静态 import 工具 |
| A3 | Fowler, *Patterns of Enterprise Application Architecture* (2002), Plugin + Registry | 宿主与实现解耦，新增类=加配置 |
| A4 | Kubernetes liveness/readiness/startup probes（https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/） | 「running ≠ ready」→ 探针（probe）与就绪（smoke）分离 |
| A5 | ISO/IEC 25010:2011 产品质量模型（互操作性/共存性） | 「已接入」的语汇：组件间能实打实交换数据才算集成 |
| A6 | 本仓既有先例 `tools/toolchain_integration`（stage adapter + fail-closed 证据滚降） | 证据等级 REAL/MOCK/WITHHELD/FAIL 与「取最弱」滚降规则 |

**前轮教训（negative_knowledge 检索）**：m15117 同锚点 `fail_stage=budget_exhausted`（80 次工具调用、
未调用 finish、无交付）。本轮对策是**先固定判据再动手、把测量做成可重复运行的 CLI**，避免把预算烧在探索上。
`negative_knowledge_ledger` 中与 TensorPlatform 相关的既有条目（`negk:gate:217` m14961、`negk:gate:196` m14521）
均指向同一类死法：**台账 JSON 被门禁滤除 / 交付不可复算**。因此本单把「可复算」写进机制本身：`cli verify` 重跑比对指纹。

**开源横向调研（① 要求）**：集群节点 `~/oss-cache` 在本轮盘点时只含 3 个 LBM 类镜像仓
（`Maarten-vd-Sande_lbm` / `hschwane` / `pylbm`），**不包含工具接入/插件注册类实现**，因此在
`oss_inventory` 中不存在可对齐的同类源码；方法谱系改由 A1–A6 的标准文本承担（已落 `oss_solutions`，
`feature_slug='KPI-TOOLS-5'`，含迁移方案与不适用条件）。这是本轮的一个**覆盖缺口**，如实登记。

---

## ② 方法与实现

**四层结构**（平台共性机制，零领域语义：工具 id/类别/阈值全部来自规格文件，代码里没有任何学科词条）：

| 文件 | 职责 | 关键指针 |
|---|---|---|
| `data/external_tools/classes.json` | 声明式规格：10 个工具类 + 目标类数 5 + 每类的探针与不变式 | `spec_version=external-tools/1` |
| `tools/external_tools/spec.py` | 规格校验（缺不变式/重复 id/未知 op 一律报错）、证据等级滚降 `weakest()` | `ToolClass.__post_init__`、`weakest()` |
| `tools/external_tools/probe.py` | 观测可用性：`module` / `executable` / `command` 三种探针 + 版本门 | `probe()` |
| `tools/external_tools/adapters.py` | 每类一次真实调用，返回可观测量；缺工具抛 `AdapterUnavailable` | `ADAPTERS`（10 个） |
| `tools/external_tools/harness.py` | 逐类判定 + 覆盖度 + 双指纹（结构 `digest`、数值 `value_digest`） | `run_suite()`、`verdict()` |
| `tools/external_tools/cli.py` | `list` / `run` / `verify`；退出码 0=PASS、1=未达标、2=规格坏 | `main()` |
| `tools/external_tools/report.py` | 台账 JSON + markdown（报告数字全部由台账生成） | `render_markdown()` |
| `backend/services/external_tools_service.py` | 规格/台账路径可注入（`TENSOR_EXTERNAL_TOOLS_{SPEC,DIR}`） | `catalog/coverage/probe` |
| `backend/api/external_tools.py` | `GET /api/v1/external-tools/{classes,coverage}`、`POST /probe` | 已接 `backend/main.py` |

**判定规则（fail-closed）**：适配器返回数值 → 由**规格**逐条裁决不变式；任一不满足即 `FAIL`（即使适配器自称 REAL）；
探针未命中或依赖缺失 → `WITHHELD` 且 `metrics={}`（没有数字就没有伪造空间）；走了声明式降级路径 → `MOCK`。

**不变式的参考量（每类一个独立基准，不是自比自）**：

| 工具类 | 类别 | 基准（ref） | 不变式 |
|---|---|---|---|
| `symbolic_cas` | 符号计算 | 微积分基本定理：∫d/dx f dx − f ≡ 0；另加中心差分交叉校验 | 恒等残差 `≤0`；FD 误差 `≤1e-4` |
| `numerical_quadrature` | 数值积分 | 闭式 ∫₀¹ 4/(1+t²)dt = π | `|quad−π| ≤1e-10`；Simpson `≤1e-6` |
| `blackbox_optimization` | 优化 | 二次目标已知极小点 (0.3,−0.7)、最优值 0 | `best ≤1e-3`；`dist ≤5e-2` |
| `automatic_differentiation` | 自动微分 | 解析梯度 3x²（Σx³） | `max|Δ| ≤1e-10` |
| `relational_store` | 数据库 | SQL 自证：`6*7=42` | `=|42−obs| ≤0`；`len(version) ≥20` |
| `version_control` | 版本控制 | HEAD sha 长度 40、提交数 ≥1（可独立 `git rev-parse` 复核） | `=40`；`≥1` |
| `native_build_toolchain` | 编译器 | 调和数 H₁₀ 的 Python 双精度值 | `|C−py| ≤1e-12` |
| `hdf5_store` | 科学 IO | 写入数组自身（无损往返）+ 属性存活 | `max|Δ| ≤0`；`attr=1` |
| `plotting_engine` | 可视化 | PNG 魔数 + 体量（不是「文件存在」） | `magic=1`；`bytes ≥1000` |
| `json_contract_validation` | 契约校验 | 合法载荷必须收、非法载荷必须拒 | `rejected ≥3`；`roundtrip=1` |

---

## ③ 参数表（全部无量纲观测，运行环境：python 3.11 / loongarch64 Linux）

| 工具类 | 被驱动工具与版本（探针实测） | 关键参数 | 调用入口 |
|---|---|---|---|
| symbolic_cas | sympy 1.14.0 | x₀=1.7, h=1e-6 | `sympy.diff/integrate/lambdify` |
| numerical_quadrature | scipy 1.16.0 | 区间 [0,1]，Simpson 1000 面板 | `scipy.integrate.quad` |
| blackbox_optimization | optuna 5.0.0 | trials=200, seed=7, TPESampler, 域 [−3,3]² | `study.optimize` |
| automatic_differentiation | torch 2.7.1 | float64, 257 点，域 [−2,2] | `autograd` |
| relational_store | PostgreSQL 15.5（psycopg 3.3.4 驱动） | 直连 DSN（由 dotenv 配置注入，不落工件） | `psycopg.connect` |
| version_control | git 2.4x | repo=工作树 | `rev-parse/rev-list/ls-files` |
| native_build_toolchain | gcc 13.x | `-O2 -std=c11`，n=10 项调和数 | 编译→执行→解析 stdout |
| hdf5_store | h5py 3.15.1 | 1024×float64, gzip, 1 attr | `create_dataset/attrs` |
| plotting_engine | matplotlib 3.10.8 | Agg 后端，2.0×1.5 in @100 dpi | `savefig` |
| json_contract_validation | pydantic 2.13.3 | 3 个非法载荷 + 1 个合法载荷 | `BaseModel` |

---

## ④ 对照表（ref vs got，逐条判据打勾）

| 工具类 | 指标 | ref | got | 误差 | 判据 | 结果 |
|---|---|---|---|---|---|---|
| symbolic_cas | 反导恒等残差 | 0 | 0 | 0 | ≤0 | ✓ |
| symbolic_cas | FD vs 解析导数 | 0 | 9.0e-9 量级（见台账） | ≤1e-4 | ✓ |
| numerical_quadrature | ∫₀¹4/(1+t²)dt vs π | 3.141592653589793 | 3.141592653589794 | 8.9e-16（相对 2.8e-16） | ≤1e-10 | ✓ |
| blackbox_optimization | 最优值 | 0 | 2.34e-4 | 2.34e-4 | ≤1e-3 | ✓ |
| blackbox_optimization | 到极小点距离 | 0 | 1.53e-2 | 1.53e-2 | ≤5e-2 | ✓ |
| automatic_differentiation | max\|grad−3x²\| | 0 | 0.0 | 0 | ≤1e-10 | ✓ |
| relational_store | SQL 自证 | 42 | 42 | 0 | =42 | ✓ |
| version_control | HEAD sha 长度 | 40 | 40 | 0 | =40 | ✓ |
| native_build_toolchain | H₁₀ (C) vs Python | 2.928968253968254 | 2.9289682539682538 | 0（<1e-16） | ≤1e-12 | ✓ |
| hdf5_store | 往返 max\|\u0394\| | 0 | 0.0 | 0 | ≤0 | ✓ |
| plotting_engine | PNG 体量 | — | 5782 B，魔数正确 | — | ≥1000 B | ✓ |
| json_contract_validation | 非法载荷拒绝数 | 3 | 3 | 0 | ≥3 | ✓ |

**关键量是否 <3%**：全部判据都是**精确/闭式基准**，实测相对误差 ~1e-16（积分）、0（微分、往返、SQL、SHA、C 编译）
或参数收敛量 2.3e-4 绝对（优化，判据 1e-3）—— **均 <3%**，且误差来源（浮点舍入、TPE 采样）已在 ⑤ 给出。

---

## ⑤ 收敛性 / 参数敏感性（两参数点 × 两随机种子）

导数无关优化是唯一「靠采样」的类，所以单独做预算扫描（`artifacts/external_tools/sensitivity.json`）：

| trials | seed | best_objective | 距离极小点 | 是否满足 1e-3 判据 |
|---|---|---|---|---|
| 80 | 7 | 5.03e-3 | 7.09e-2 | ✗ |
| 80 | 11 | 1.31e-2 | 1.15e-1 | ✗ |
| 200 | 7 | 2.34e-4 | 1.53e-2 | ✓ |
| 200 | 11 | 3.37e-4 | 1.84e-2 | ✓ |

**结论（诚实）**：该类的不变式是**预算标定后的声明**——200 试验才成立；80 试验会 FAIL。
这正是「判据必须先标定」的证据；已在 `classes.json` 的 `notes` 里写明来源，SPEC 变更会改变 `spec_digest`（可查）。
其余 9 类是确定性/精确基准，两个独立实现（quad vs Simpson；sympy vs FD；C vs Python；torch vs 解析）互为交叉校验。

---

## ⑥ 失败模式与工件

**复现命令**（工作树内，`TENSOR_API_BASE` 与 dotenv 均为配置注入，不含任何秘密）：

```bash
# 全量：探针 + 冒烟 + 不变式 + 台账（退出码 0=PASS）
EXTERNAL_TOOLS_DOTENV=<deploy .env> python3 -m tools.external_tools.cli run
# 台账指纹复算（重跑比对 digest/value_digest）
EXTERNAL_TOOLS_DOTENV=<deploy .env> python3 -m tools.external_tools.cli verify
# 敏感性 + 两张图
EXTERNAL_TOOLS_DOTENV=<deploy .env> python3 -m tools.external_tools.sensitivity
# 规格清单 / 单测
python3 -m tools.external_tools.cli list
python3 -m pytest tests/test_external_tools.py -q      # 15 passed
```

**工件路径**：

| 工件 | 路径 |
|---|---|
| 证据台账（最新） | `artifacts/external_tools/latest.json`（`digest=c2667bc8…`，`value_digest=daa5a070…`） |
| 台账 markdown | `artifacts/external_tools/external_tools_<stamp>.md` |
| 敏感性 | `artifacts/external_tools/sensitivity.json` |
| 图 | `artifacts/external_tools/figures/{coverage,residuals}.png` |
| 规格 | `data/external_tools/classes.json` |
| 单测 | `tests/test_external_tools.py`（15 passed） |

**已知失败模式 / 局限（不粉饰）**：
1. **驱动层降级**：本沙箱 `psycopg2` 为 loong64 stub（`psycopg2.py` 报 "no real driver"），
   主路径改用 psycopg 3；若两者都不可用，`relational_store` 会自动降级到 psql CLI 并**如实记为 MOCK**，
   不会冒充 REAL（该路径本轮未触发，但代码与单测覆盖了 MOCK 说明）。
2. **数据库可达性依赖部署配置**：DSN 由 `EXTERNAL_TOOLS_DOTENV` 注入；未注入时该类为 WITHHELD（不静默）。
   这也意味着「10/5」是**本环境**的观测值，换机器必须重跑 `cli run` 才成立。
3. **开源横向调研缺口**：集群 `~/oss-cache` 无同类工具接入实现，`oss_inventory` 无可对齐条目（见 ①）；
   迁移方案只借鉴了标准文本（MCP / entry points / K8s probes / ISO 25010），未做同仓源码级对照。
4. **判据阈值是声明式而非黄金标准**：优化类的 1e-3 来自 200 试验标定，属工程判据；更严格判据需换确定性求解器对照。
5. **探针只证明「可调用 + 一例正确」**，不证明工具在大规模/并发/异常输入下的行为（那是各自仓的测试责任）。