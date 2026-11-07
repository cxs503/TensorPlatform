# G-A/G-B/G-C 词表契约收尾复核（m4964，2026-11-07）

> 本文件是 `docs/architecture/migration_disposition_2026-11.md` §3「单一契约源三缺口」的
> 收尾复核记录，**只增不改**：原 §3 表格保持原样，本文件另附实测证据。
>
> 工件：`artifacts/contracts_gabc_2026-11/`
> - `vocab_contract_audit.json`（逐项对拍机器可读结果 + 四份正典文件 sha256 审计链）
> - `CONTRACTS_GABC_CLOSEOUT.md`（同一结果的表格式报告，由工具生成，数字零手抄）
> - `fig_gabc_diff_matrix.png`（19 项 diff 矩阵 + 正典词表规模）
>
> 工具：`tools/contracts/vocab_contract_audit.py`（`--dry-run` 不落盘；pytest 见
> `backend/tests/test_vocab_contract_audit_tool.py`）
> 契约测试：`backend/tests/test_status_contracts.py`（G-A/G-B/G-C 三组，34 用例红绿判据）

## 1. 案例介绍（这是哪一类问题）

TensorPlatform（TP）是领域无关的智能体运行平台，TensorPhysics（TPh）是其上的科研运行租户。
两者之间原来有三组「词表」**各自定义、靠人脑同步**，这是 §5「执行序」里一切代码迁移的前置：

| 缺口 | 词表 | 原状 |
|---|---|---|
| G-A | `tensor_dev_missions.status` ↔ TPh `TaskStatus` | 两侧各写常量，`verifying↔?`、`cancelled↔revoked` 靠注释映射 |
| G-B | 会话事件流 `tensor_agent_session_events.kind`（M3/M4） | 手写 `EVENT_KINDS` 元组 + 事件表两处 `INSERT` 写手 |
| G-C | `POST /tensorphysics/runs` 的 run/validation status | `tensorphysics_runs.py:37-38` 手写正则 ↔ TPh `_RUN_STATUSES` 双源 |

落法（§3 已定）：**共享契约文件（JSON + 版本号）→ 两侧运行时常量从契约派生 → 逐词 diff=0 契约测试**。

本次复核的判据（节点验收口径）：

1. 三项各带契约测试；
2. 每项有 diff=0 的**实跑输出**（不是「看起来一致」）；
3. 「新增词项走正典列表、非手写分支」这条纪律可机器判——词加进 JSON 算合规，加进代码字面量算违规。

### 参数表（契约文件与词项规模）

| 契约文件 | 主列表 | 词数 | 次列表 | 词数 | 版本 |
|---|---|---|---|---|---|
| `mission_status_vocab.v1.json` | `statuses` | 11 | `terminal`（+`platform_only` 5） | 5 | 2 |
| `mission_task_status_map.v1.json` | `mapping`（tph→tp） | 9 | `platform_only` | 5 | **2（本次 1→2）** |
| `session_event_kinds.v1.json` | `kinds` | 13 | `governance_members` | 6 | **1（本次新建）** |
| `research_run_status_vocab.v1.json` | `run_statuses` | 8 | `validation_statuses` | 4 | 1 |

## 2. 怎么做的（方法与关键设置）

1. **正典装载层**（`backend/api/domain/status_contracts.py`，依赖仅 `json`+`pathlib`）：
   新增 `session_event_contract()`（G-B 词表装载器）；其余三个装载器已在 main。
2. **运行面从正典派生**（不改 API 面、不改表结构）：
   - `api/domain/tensor_common.py`：`EVENT_KINDS = tuple(session_event_contract()["kinds"])`
     ——原为手写 13 字面量元组，加词要同时手改本文件与 TPh 任务级发射器。
   - `api/tensor_mission_lifecycle.py` 不动（用 `MISSION_STATUSES`）；`tensorphysics_runs.py` 三常量
     已从契约派生（G-C 上一轮已收尾）。
3. **一事实单源（G-B）**：新增 `api/domain/tensor_common.insert_event_row()` 作为**唯一事件行写手**；
   批量端点 `api/tensor_sessions.py` 原先自己拼 `INSERT INTO tensor_agent_session_events`
   （第二写手 → 双写风险），改为调用该函数（保留单锁单事务语义，行为不变）。
4. **逐项 diff 审计器**（`tools/contracts/vocab_contract_audit.py`，19 项）：
   - 运行面用**真实 import**取常量（不是读源码猜）；
   - G-A 还比 DB 迁移 `CHECK (status IN ...)` 的终态定义与历史定义并集；
   - 映射性质：`tph→tp` 必须是**函数**（每状态恰一像）、像集 ⊆ 正典、`platform_only` 与像集不相交；
   - 「派生 vs 手写」用 **AST 判据**（`derives_symbol`）：赋值右式须调用 `api.domain.status_contracts`
     装载器；`X = ("plan", "note")` 这类字面量元组判违规；
   - 「唯一写手」用**全仓 SQL 字面量扫描**（生产面，跳过 `tests/`）。
   - 模式：`--dry-run`（只打印不落盘）/ `--json-out` / `--md-out` / `--plot-out`；
     退出码 0=全 diff=0，2=有非 0 diff；`--contracts-dir` 可指向被篡改的契约目录做负控。
5. **负控**（证明审计器不是橡皮图章）：pytest 里把契约文件加一个 `ghost_state`、
   删一个正典文件，审计器必须判红并点名该词；`derives_symbol` 对手写元组必须返回 False。

## 3. 计算结果（实跑，程序化注入）

命令：

```
python3 tools/contracts/vocab_contract_audit.py \
  --json-out artifacts/contracts_gabc_2026-11/vocab_contract_audit.json \
  --md-out  artifacts/contracts_gabc_2026-11/CONTRACTS_GABC_CLOSEOUT.md \
  --plot-out artifacts/contracts_gabc_2026-11/fig_gabc_diff_matrix.png
```

输出（摘要）：

| 契约 | 检查项数 | diff 总和 |
|---|---|---|
| G-A | 9 | 0 |
| G-B | 5 | 0 |
| G-C | 5 | 0 |
| **合计** | **19** | **0** → `verdict=PASS` |

逐项结果见 `artifacts/contracts_gabc_2026-11/CONTRACTS_GABC_CLOSEOUT.md` §1 表
（每项 正典来源 / 运行面来源 / 词数 / diff / 判定）。

**本次复核当场抓到的真实漂移（已修）**：

- **G-A `platform_only_consistency` 原为 diff=1**：`mission_status_vocab` v2 与
  `migrations/100_pr_merge_gate` 已在任务侧新增 `awaiting_pr_merge`，而
  `mission_task_status_map.v1.json` 落后一个词（4 vs 5）。这正是「靠人脑同步」的典型后果。
  修法走正典路径：映射契约 **版本 1→2**，`platform_only` 补 `awaiting_pr_merge`
  （附原因「PR open→merge gate owned by TP」），并新增契约测试
  `test_platform_only_agrees_across_the_two_contract_files` 锁死两文件 diff=0。
  文件名保留 `.v1.json` 以不破坏 TPh 侧按文件名 pin 的取用方式（契约内 `version` 才是语义源）。

**本次新增的正典词表（而非代码分支）**：`session_event_kinds.v1.json` 的 13 个事件种类
（`plan` … `code_commit`）与 6 个治理成员（`governance_members`）——以前它们只存在于
`tensor_common.py` 的字面量元组里，现在有契约文件、版本号与两侧判据。

## 4. 结果图

`artifacts/contracts_gabc_2026-11/fig_gabc_diff_matrix.png`（左：19 项 diff 矩阵，全绿=全 0；
右：四份正典文件词项规模，说明 diff=0 不是「与空集比较」）。

说明：本节点是**平台/后端契约任务**，不涉及任何 船舶总体性能 物理量（阻力/推进/耐波/操纵/稳性/
结构/振动噪声/绿色航行/冰区），因此**没有场量云图、没有与实验/解析解的对比曲线**——这是任务性质
决定的，不是缺证据。该图是唯一的「结果图」，其数据源即同一份 JSON 工件。

## 5. 注意点（踩坑与局限）

**踩到的坑**

1. 两份正典文件之间也会漂移（`awaiting_pr_merge`）：单看「运行时 vs 正典」会全绿，
   必须再加「正典 vs 正典」与「DB CHECK 终态」两条比较轴。
2. 单写手扫描会自命中测试文件（断言里写着 `INSERT INTO ...` 字符串）：扫描范围必须限定生产面
   （跳过 `tests/`、`test_*.py`），否则守卫测试永远红。
3. `EVENT_KINDS` 改成派生后顺序仍等于契约顺序：若有消费者按下标取词，改契约顺序就会改变行为
   ——契约文件里词序即运行时词序，这条写进了契约测试（`list(EVENT_KINDS) == list(canon)`）。

**局限**

1. TPh 仓库不在本工作树内：跨仓那一半用**冻结镜像常量** `TPH_TASK_STATUSES_MIRROR`
   （与 `backend/tests/test_status_contracts.py` 同值）代表，真漂移由 TPh 侧同名测试判红。
   本次未做 TPh 侧实跑（honest 声明）。
2. 审计是静态的（常量对拍 + 源码 AST/字面量扫描 + 迁移 SQL 解析），不做运行时事件追踪；
   动态拼出来的 `kind`（如变量传入）不在字面量扫描范围内。
3. 批量端点改为 `insert_event_row` 属内部去重，`POST /tensor/sessions/{id}/events` 的请求/响应
   契约与事务语义（单 advisory lock + 单事务 + 409 重试口径）未变；但本环境缺
   `cryptography`/`psycopg2`，`tests/test_campaign_plans.py`（全 app 启动）无法在此环境跑通，
   故该端点的**端到端**回归未在本工作树复跑（见 §6 判据表「未覆盖」行）。

## 6. 误差判定（关键量 vs 基准，逐项）

词表契约的「误差」是**离散集合差**，基准就是正典 JSON 本身（精确匹配，非统计量）：

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| G-A 状态词表（11 词） | `mission_status_vocab#statuses` | 11/11，diff=0 | 0.0% | **是**（0/11） |
| G-A 终态（5 词） | `#terminal` | 5/5，diff=0 | 0.0% | **是** |
| G-A DB CHECK 终态（11 词） | `migrations/100_pr_merge_gate` | 11/11，diff=0 | 0.0% | **是** |
| G-A 映射覆盖（9 状态） | TPh TaskStatus 冻结镜像 | 9/9，diff=0 | 0.0% | **是** |
| G-A platform_only（5 词） | 两文件互拍 | 5/5，diff=0（复核前 4/5=20% 缺） | 0.0%（修复前 20%） | **是**（修复后） |
| G-B 事件词表（13 词） | `session_event_kinds#kinds` | 13/13，diff=0 | 0.0% | **是** |
| G-B 事件行写手（1 处） | 唯一写手定义 | 1 处，diff=0（复核前 2 处） | 0.0%（修复前 +1 处） | **是**（修复后） |
| G-C run status（8 词） | `research_run_status_vocab#run_statuses` | 8/8，diff=0 | 0.0% | **是** |
| G-C validation status（4 词） | `#validation_statuses` | 4/4，diff=0 | 0.0% | **是** |
| G-C manifest 上限 | `#max_manifest_bytes`=1000000 | 1000000，diff=0 | 0.0% | **是** |
| 全部 19 项 | 逐项 diff | 19/19 diff=0 | 0.0% | **是** |

**显式回答：是，全部关键量误差 0.0%（<3%）**。注意这不是「拟合精度」，而是精确集合相等；
表中「修复前」列如实记录复核时抓到的两处真实漂移（G-A 20% 缺词、G-B 双写），修复后归零。

**未覆盖（honest）**：① TPh 侧实跑（仓库不在本工作树）；② 会话事件批量端点端到端回归
（本环境缺 `cryptography`/`psycopg2`，`test_campaign_plans.py` 在 setup 阶段即 ImportError，
与本次改动无关，main 上同样如此）。

## 7. 复现

```
python3 tools/contracts/vocab_contract_audit.py --dry-run            # 不落盘，打印 19 项
python3 tools/contracts/vocab_contract_audit.py                      # 重建 JSON 工件
cd backend && python3 -m pytest tests/test_status_contracts.py tests/test_vocab_contract_audit_tool.py -q
```