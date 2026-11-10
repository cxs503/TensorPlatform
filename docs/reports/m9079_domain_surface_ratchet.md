# P162-N10 · TensorPlatform 结构性领域面棘轮治理（成熟化节点报告）

- **节点**：P162W1-N10（战役 DAG，平台仓治理层）
- **仓**：TensorPlatform（分支 `agent/m9079`）
- **判据日**：2026-11-10（豁免/审批链到期判定基准日，写死在工件与用例里，保证可复现）
- **结论**：结构级域面门 + 只减不增棘轮**已落地并接入 CI**；`check` 判定 **pass(0)**，
  38 个模块全部入账，45 处存量依赖全部登记为带到期豁免，词级门（#608）零改动、零命中。

---

## ① 案例介绍（问题背景与对象）

判例 #608 给平台装了一道**词级**门：`tools/audit/domain_leak_scan.py` + `config/domain_word_denylist.json`，
把「域词条/域判据/域算例硬编码进 `backend/tools/tests`」变成可机器判决的事。它解决的是
**词汇渗漏**，但存在一个结构性盲区：

```python
# backend/api/auth.py（真实仓注入探针，实测）
from api.domain.holtrop_mennen import *
```

这一行**不含任何 denylist 词条**，词级门判它绿（实测 `word_scan_hits = 0`，
见 `artifacts/m9079/negative_controls.json → word_level_blind_spot`）；但依赖方向已经反了——
平台核心层（认证）反向绑住了一个具体领域的经验公式模块。于是：

- 领域工件一删改，平台核心跟着碎（`auth` 与某个领域的 Holtrop 实现同生共死）；
- 领域结论随平台代码流动，"零领域语义"从词级看是干净的，从结构级看是穿孔的。

**治理对象（本单的结构面定义）**：
`backend/api/domain/**/*.py` 共 **38** 个模块（战役口径 ≥36），
平台侧分三层（`config/domain_surface_policy.json`）：

| 层 | 路径（政策声明） | 实测文件数 |
|---|---|---|
| core | `backend/api/core`, `backend/services`, `backend/models`, `backend/utils`, `backend/methods`, `backend/physics`, `backend/tasks` | 308 |
| route | `backend/api`（递归，除 `api/domain`） | 179 |
| tooling | `tools/`, `scripts/` | 1140 |

`backend/services/platform` 在政策里被声明但仓内不存在 → 记入 `manifest.absent_layers`
（申报面，不伪绿）。战役文本假设的 `api/core`、`services/platform` 本单**新建/命名对齐**：
`backend/api/core/` 作为核心层锚点首次存在（`__init__.py` + 接缝模块）。

---

## ② 怎么做的（方法 / 工具链）

### 2.1 五个部件

| 部件 | 路径 | 作用 |
|---|---|---|
| 结构清单工具 | `tools/audit/domain_surface_manifest.py` | `generate` / `check` / `confirm-removal` / `bless` 四子命令；退出码 0/1/3/5 |
| 政策 | `config/domain_surface_policy.json` | 结构面根、分层、归属规则、默认到期、审批权威、预算；指纹被清单钉死 |
| 基线清单 | `config/domain_surface_manifest.json` | 38 模块 × (sha256, owner_repo, target_layer, status, expiry, migration_ref, reason) + 41 条依赖豁免 |
| 审批链 | `config/domain_surface_approvals.json` | append-only，每条 = 一次被 owner 接受的基线变更（`by` 必须是 `owner`） |
| 接缝 | `backend/api/core/domain_provider.py` | `register_domain` / `load_registry` / `provide_domain`：核心层唯一允许的域能力解析路径 |

### 2.2 判定逻辑（`check`）

1. **自洽性**：清单为空 → `manifest_empty`（fail-closed）；每条 7 个字段非空；status ∈ {migrated, DECLARED-exempt}。
2. **政策漂移**：`manifest.policy_digest != sha256(canonical(policy))` → `policy_drift`。
3. **反手改**：`content_sha256` 覆盖 `{policy_digest, surface_roots, modules, dependency_exemptions}` 的
   规范化 JSON；手改清单字段而不重签 → `manifest_hand_edited`。
4. **棘轮（面）**：磁盘 ⊖ 基线 → 新增 `surface_growth`(红) / 删除 `removal_pending`(3) / 内容漂移 `content_drift`(红)。
5. **依赖方向**：一次 AST 遍历各层文件，抓 `import api.domain.*` / `from api.domain.* import` /
   `import_module("api.domain.*")` 三种形态，按 `(path, module, kind)` 归并成 sites；
   未登记 → `dependency_not_registered`（报 `file:line`）；`sites > allowed_sites` → `dependency_expanded`；
   豁免缺到期/过期 → `exemption_missing_expiry` / `exemption_expired`。
6. **审批链**：末条记录 `content_sha256_after` 必须等于当前 `integrity.content_sha256`，`by=owner`，
   `modules_after` 等于模块数，否则 `approval_stale` / `approval_authority_invalid` / `approval_module_count_mismatch`。
7. **词级复用**：`run_word_scan()` 直接 `import` 并调用 `domain_leak_scan.scan()`（同一 denylist、同一 `scan_paths`、
   同一判定），结果原样放进 `result["word_scan"]` 作为补充证据；词级命中只写 warning，**不改其自身判定**。

### 2.3 性能工程（为什么现在能进 CI）

首版逐层遍历全部 1627 个层内文件做 AST 解析，单次 `check` **45.9 s**；改为
「文本预筛 + 单次遍历同时产出依赖边与接缝面」后 **1.96–2.9 s**（>15× 加速，预算 120 s）。
预筛不降召回：被检形态本身就是含 `api.domain` / 接缝模块名的 import 或字符串常量。

### 2.4 接缝（域能力唯一允许的解析路径）

`domain_provider.py` 只依赖标准库（`importlib`/`dataclasses`/`typing`），域包装载采用
`"dotted.module:attribute"` **惰性** 解析，因此核心层对域包**没有静态依赖**；
未声明即 `DomainNotProvided`（fail-closed，不返回 `None`）。每份声明带 `owner_repo` 与可选 `expires`。

---

## ③ 计算结果（判据数字，全部程序化取自 `artifacts/m9079/`）

| 判据 | 要求 | 实测 | 证据工件 |
|---|---|---|---|
| C1 清单完备性 | ⊇ 36 模块、差异 0、无空字段 | **38 / 38，added=0 missing=0**，38 条全字段非空 | `domain_surface_check.json.surface`, `manifest_stats.json` |
| C2 依赖方向门 | 注入必须红并报 `file:line` | 注入 `from api.domain.holtrop_mennen import *` → **fail(1)**，报 `backend/api/auth.py:454`；还原后 pass(0) | `c2_injection_probe.json` |
| C2 现状登记 | 带到期豁免 | **45 处 import 点 / 41 对 (path,module) / 36 个文件**，全部 `expiry=2027-03-31` | `dependency_census.json` |
| C3 棘轮（增） | 新增 domain 文件→红 | **fail(1)** `surface_growth` | `negative_controls.json` |
| C3 棘轮（删） | 删→3 待确认；确认后基线 −1 且转绿 | **3 → confirm-removal → pass(0)，modules 3→2** | `negative_controls.json` |
| C3 棘轮（漂移） | 内容漂移→红，bless 转绿 | 用例 `test_c3_content_drift_red_then_bless` 通过 | `tests/test_domain_surface_manifest.py` |
| C4 词级兼容 | 同一 denylist/路径/判定，逐字节不变 | scanner sha `4a007c99…`、denylist sha `70f43ff6…` 未变；改动前 CLI 产物 sha **`bdd2fbe4…`** 与改动后一致；命中 0 / 42 文件 / 37 词条 | `word_scan_contract.json` |
| C5 确定性 | 两次扫描 sha256 相同 | 两次 `check --json` 文件 sha **`f42f0896…`**（`det equal = True`） | 本节测量 + `test_c5_*` |
| C5 预算 | ≤120 s | **1.96 s**（单次）/ **2.21 s、2.87 s**（两次） | 本节测量 |
| C6 负控三件套 | 缺清单→WITHHELD；空清单→红；豁免无到期→红 | **5 / 1 / 1** 三闸实测 | `negative_controls.json`, `test_c6_*` |
| C7 向后兼容 | 不改现有 API 行为 | 静态路由普查 **179 文件 / 1500 路由**，指纹 `ee4118bf…` 变化=0；`git status` 仅新增文件 | `api_route_census.json` |

**模块归属（38 个，按 `owner_repo`）**：

| owner_repo | 模块数 | 目标层 |
|---|---|---|
| ship-performance-platform | 18 | `domains/ship_performance` |
| TensorPlatform | 17 | `backend/api/core` |
| TensorPhysics | 3 | `domains/physics_providers` |

状态分布：`DECLARED-exempt` 38 / `migrated` 0（本单不要求迁移，见 C7）；到期日统一 **2027-03-31**。

**依赖边分布（按层）**：

| 层 | (path,module) 对 | import 点 | 文件 |
|---|---|---|---|
| route | 29 | 31 | 26 |
| tooling | 12 | 14 | 10 |
| **合计** | **41** | **45** | **36** |

被引用的域模块 **9** 个（`tensor_common` 占绝对多数——它是"平台机制误住在域包里"的典型，
C1 表里归 TensorPlatform/`backend/api/core`，迁出即消除绝大部分依赖边）。

---

## ④ 结果图

![结构面普查与依赖边](../../artifacts/m9079/fig1_domain_surface_ratchet.png)

**图 1**（`artifacts/m9079/fig1_domain_surface_ratchet.png`）：左=38 个域面模块按归属仓的分布
（18 / 17 / 3）；右=核心层→域模块的存量依赖边（45 处 import 点 / 41 对登记豁免），
标题即本次 `check` 判定 `pass`。

![负控矩阵](../../artifacts/m9079/fig2_negative_controls.png)

**图 2**（`artifacts/m9079/fig2_negative_controls.png`）：六个负控/正控场景的**实测**退出码：
绿基线 0、新增域文件 1、核心层注入依赖 1、删除已登记模块 3、确认删除后 0（基线 −1）、
清单缺失 5（WITHHELD）。右下角标注**同一注入行在词级门上的命中数 = 0**——这就是结构门补的盲区。

> 说明（本单无场量云图/解析解曲线的原因）：本节点治理对象是**代码结构与依赖方向**，不存在
> 连续场量或物理量，故没有云图；"与基线比较"以**门的有无**为基线：改动前该泄漏只有词级门
> （盲，命中 0），改动后结构门判红——图 2 即为该对照。

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **坑：豁免按行号钉会假红**。最初把豁免登记成 `file:line`，任意一次上方插行就全变红。
   改为按 `(path, module, kind)` 归并 + `allowed_sites` 封顶：新增依赖必红，上方插行不误报，
   仍能定位到首个违规 `file:line`。
2. **坑：dataclass 装载**。按路径 `importlib` 装载 `domain_provider.py` 时未注册 `sys.modules`，
   `@dataclass` 解析注解抛 `AttributeError`；用例里先 `sys.modules[name] = module` 再 `exec_module`。
3. **坑：性能**。初版 `check` 45.9 s（依赖边与接缝面各遍历一遍全层 AST）。文本预筛 + 单遍历后 1.96 s。
   CI 里这是"能不能真跑起来"的差别。
4. **局限：审批链不是密码学防伪**。owner 若同时改清单与审批链，仍可造出一致的 `content_sha256`；
   本机制保证的是"任何基线变更必须在 diff 里以一条 `by=owner` 记录显形"，属**可审计**，不是不可伪造。
   真正的门是 code review + 分支保护。
5. **局限：静态依赖检测的绕过面**。`getattr(import_module("api." + "domain.x"), ...)` 这类拼接
   字符串的写法不会被抓到（预筛也依赖字面量）。这是已知上限，正确姿势是走 `provide_domain()`。
6. **局限：C7 用静态路由普查替代 live 200 抽样**。本环境缺运行期依赖
   （`cryptography` 等），`main:app` 无法导入，故以"路由声明面指纹不变 + 本单零修改既有后端文件"
   作为不改变 API 行为的证据；诚实标注：**未做 100 端点 live 200 抽样**。
7. **偏差声明（方法自主权）**。战役文本写的核心层是 `api/core、services/platform、tools/`，
   实测仓内 `backend/api/core`、`backend/services/platform` **不存在**。处理：政策同时声明这两条
   （缺者在 `absent_layers` 显形）+ 把 `backend/api/core` 新建为核心层锚点，其余按实测层
   （`services/models/utils/methods/physics/tasks` + `route` + `tooling`）登记，未删任何声明面。
8. **口径偏差**。战役 C2 提到的"35 处依赖"与实测 **45 处 import 点 / 41 对**不一致
   （口径差：是否含 `scripts/`、是否按去重对计数、是否含 `backend/api` 子目录）。
   报告以实测为准并登记 45 处，未向种子数字凑数。
9. **适用范围**：本门只管"结构面与方向"，不管域结论对错；域证据分级仍归各域仓 / 证据台账。

---

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否 <3%"）

| 关键量 | 基准 | 实测 | 相对偏差 | <3%？ |
|---|---|---|---|---|
| 域面模块数 | 磁盘 `backend/api/domain/*.py`（38） | 清单 38 | 0.00% | **是** |
| 战役 C1 口径模块数 | ≥36 | 38 | +5.6%（超集，非误差） | **是**（⊇ 成立，差异 0） |
| 存量依赖点 | AST 全量扫描（45） | 登记 45 | 0.00% | **是** |
| 词级门输出 | 改动前 CLI JSON (`bdd2fbe4…`) | 改动后 CLI JSON | 0 字节 | **是** |
| 词级命中数 | 0（#608 基线） | 0 | 0 | **是** |
| 路由声明面 | 改动前 (179 文件/1500 路由，`ee4118bf…`) | 同 | 0.00% | **是** |
| 检查耗时 | 预算 120 s | 1.96 s | −98.4% | **是** |
| 注入探针判定 | 期望 fail(1) + `file:line` | fail(1)，`auth.py:454` | 一致 | **是**（离散判定，无误差带） |

确定性复核：同仓两次 `check` 输出文件 sha256 **完全相同**（`f42f0896…`）；
所有工件由 `tools/audit/m9079_artifacts.py` 现场生成，`artifacts/m9079/provenance.json` 记录
每个产物的 sha256 与输入 sha256（零手抄）。

**诚实 FAIL 申报**：C6 的"降级为仅词级"在**合成工作树**里实际表现为 `word_scan.status="unavailable"`
（合成树没有扫描器），真实仓缺清单时才会真跑词级门；该分支已在真实仓语义下由
`test_c6_missing_manifest_degrades_to_word_only_withheld` 的断言覆盖（不崩、不绿、不红），
但"真实仓缺清单 + 词级门同时跑"的组合**未在真实仓物理演练**（不敢为演练删基线）。

---

## 复现

```bash
# 门（CI 同一命令）
python3 tools/audit/domain_surface_manifest.py check --today 2026-11-10 --json /tmp/check.json

# 判据用例（19 条，覆盖 C1–C7）
python3 -m pytest tests/test_domain_surface_manifest.py -q

# 工件重建（含图与迁移账本）
python3 tools/audit/m9079_artifacts.py
```

`check` 退出码：`0` 绿 / `1` 红 / `3` 删除待确认 / `5` WITHHELD（清单或政策缺失，仅词级，不假绿）。
CI 接线：`.github/workflows/ci.yml` 的 `contracts` job 新增
`Structural domain-surface gate (P162-N10)` 一步，`--today "$(date -u +%F)"` 让豁免到期自动生效。