# 补报：节点 25「ITTC 冰池试验对标框架」证据复原与报告（mission 718 / 补报单 m8104）

**补报性质**：报告治理门自动派单（终态无已发布报告）。本报告为 **honest backfill**：不重写历史、
不补数；把 mission 718 的现存证据从数据库与 git 中程序化提取、实测可复原性、并据实判定证据等级。

## 1. 案例介绍（物理背景 / 几何 / 参数）

**物理背景**：冰区船舶在层冰/碎冰中的冰阻力预报须与 ITTC 冰池模型试验规程对标
（ITTC 7.5-04-01-01.1 Ice Model Tests 类流程），以形成"规范→实船预报→池试验"闭环。
节点 25（plan 1，ext_id **E02**，层 L2_validation，物理域 validation）的执行体为 mission **718**
（repo `TensorLBM`，base `main`，执行者 `tp-impl-5`，
尝试记录：`mission failed x2`，实际起止 `2026-10-31T04:07:33.525148+00:00` → `2026-10-31T04:20:38.783691+00:00`）。

**原单目标（DB 原文，逐字引用）**：
> 建立 ITTC 冰池试验规范对标流程：≥3 船型（集装箱/散货/科考），自动对比报告

**几何/参数表**：⚠️ **不可复原**。原单记录只保留"≥3 船型（集装箱/散货/科考）"这一功能要求，
未落库任何船长/船宽/吃水/方形系数/冰厚/冰浓度等物理参数，且 11 个交付文件在 TensorLBM
全部分支与全历史中均检索不到（图 2）。**因此本报告不给出任何几何参数或冰况参数**——若给出，
即为编造。

## 2. 怎么做的（方法 / 流程 / 关键设置 / 工具链）

补报流程（全部可重跑：`runs/m8104/ittc_backfill_audit.py`）：

1. **取真源**：`campaign_nodes(plan_id=1, id=25)`、`tensor_dev_missions(id=718)`、
   `tensor_reports(mission_id=718)`、`tensor_agent_session_events(session_id=718)`
   —— 经 `psql -Atc` 取 `row_to_json`，写入 `runs/m8104/evidence/m718_evidence.json`。
2. **解析自报判据**：从 `result.checks` 字符串（唯一留存的验收数字来源）正则提取
   `ship_types` / `reproducibility` / `pytest`；不做任何插值或补全。
3. **可复原性实测**：对 11 个声称交付文件的 basename 在 TensorLBM 执行
   `git log --all --oneline -- *<basename>`，命中即"可复原"。
4. **工件/图表**：matplotlib 生成 3 张 PNG（图 1–3），base64 内嵌发表。
5. **一致性交叉检验**：比对前序补报（`tensor_reports#9375`）
   引用的模块路径与本单记录的实际模块目录。

**工具链**：PostgreSQL 16 (`tensorplatform`) + psql；git 全历史检索；matplotlib；Python 3。计算集群未使用（无重计算）。

## 3. 计算结果（数字表，程序化注入）

### 3.1 机器判据（阈值来自 `campaign_nodes.metadata.acceptance_criteria`；取值来自 DB `result.checks`）

| 判据 | 算子 | 阈值 | 自报实测 | 判据达成 | 证据等级 |
|---|---|---|---|---|---|
| `ship_types_validated` | >= | 3 | 4 | ✅ 自报达成 | SELF_REPORTED |
| `auto_report_generated` | == | True | True | ✅ 自报达成 | SELF_REPORTED |
| `reproducibility_across_runs` | < | 0.02 | 1.32e-16 | ✅ 自报达成 | SELF_REPORTED |

自报判据达成 **3/3** 项；`pytest` 自报通过数 **34.0**（原始 pytest 日志未归档）。

### 3.2 交付物与可复原性（git 全分支/全历史实测）

| 项 | 数值 |
|---|---|
| 声称交付文件数 | 11 |
| 在 git 中可复原 | **0** |
| 原 worktree `/root/tp-agent/work/m718` 是否还在 | **否（已回收）** |
| mission 记录的 branch / head_commit | `None` / `None` |
| 记录在案的模块目录 | `artifacts/ittc_ice_benchmark_report.json`, `artifacts/ittc_ice_benchmark_report.md`, `artifacts/ittc_ice_benchmark_results.png`, `docs/ittc_ice_benchmark_framework.md`, `scripts/generate_ittc_benchmark.py`, `src/tensorlbm/ittc_ice_benchmark`, `tests/test_ittc_ice_benchmark.py` |
| 会话事件类型 | `plan, note, note, approval_requested, approval_decided` |

### 3.3 补报现状与一致性缺陷

- 本单已存在补报 `tensor_reports#9375`
  （status=`published`, qa_status=`pending`），
  但它引用的模块为 `ice_fullship`，而 mission 记录的实际模块目录是
  `artifacts/ittc_ice_benchmark_report.json`, `artifacts/ittc_ice_benchmark_report.md`, `artifacts/ittc_ice_benchmark_results.png`, `docs/ittc_ice_benchmark_framework.md`, `scripts/generate_ittc_benchmark.py`, `src/tensorlbm/ittc_ice_benchmark`, `tests/test_ittc_ice_benchmark.py` → **模块路径不一致 = 是**（该补报含不可溯源的模块与参数描述，属治理缺陷，已在本文标注，不采信其数字）。
- 证据等级总判定：**WITHHELD(工件不可复原)**。原因：工件与运行产物 0/11 可复原。

## 4. 结果图

图 1–3 见文末内嵌（`artifacts/m8104_ittc_backfill/`）。
**为何没有场量云图与对标曲线**：节点 25 的原产物（冰阻力场量云图、与 ITTC/Lindqvist 基线的比较曲线）
随 worktree 回收而永久丢失，且从未进入 git；本补报**不会**用合成数据重绘以伪装成原单结果。
可复现的替代物只有"判据达成 / 交付物可复原性 / 证据维度"三类治理级图表。若需真正的场量对标，
须以新节点重做（见第 5 节建议）。

## 5. 注意点（踩坑 / 局限 / 适用范围）

1. **陷阱（实证）**：`mission.result.checks` 这类"自报字符串"是唯一留存数字来源——但它既非
   pytest 报告也非求解器输出，只能作 SELF_REPORTED 证据，不能升级为 REAL。
2. **陷阱（实证）**：mission 成功 ≠ 工件入库。本单 `branch=null, head_commit=null`
   ——"验收通过未 push"导致交付物随 worktree 回收蒸发。**教训：工件必须在 finish 前 commit。**
3. **前序补报污染**：`tensor_reports#9375` 引用了
   mission 记录中不存在的模块路径（ice_fullship），属"补报编造"，其 qa_status 仍为
   `pending` —— 报告 QA 门未拦截此级别错误。
4. **局限**：本报告只做证据复原与治理判定，不含任何冰阻力物理计算；不适用于任何定量对标引用。
5. **建议**（可执行）：① 报告 QA 增加"声称模块路径 ∈ mission.changed_files"硬校验；
   ② 终态验收把 `head_commit is null` 判为 FAIL，杜绝"无入库验收"。

## 6. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 本单可核值 | 误差 | 是否 < 3% |
|---|---|---|---|---|
| 船型验证数 ship_types_validated | 阈值 3（判据） | 4.0（自报） | 超阈值 1 | 判据满足，但**非精度量，不适用 3% 判据** |
| 跨轮可复现性 reproducibility | 阈值 <0.02 | 1.32e-16（自报） | 距阈值 100.0000% | **无法判定**（无第 2 次独立运行的日志可复核） |
| 冰阻力 vs ITTC/Lindqvist 基线 | 文献/池试验值 | **不可复原** | — | **否/无基准数据 → WITHHELD** |

**显式结论**：①"是否 <3%"——对**唯一带基准性质的物理量（冰阻力对标）无法回答**，因为原单未留存
任何阻力数值与基线值，证据等级 WITHHELD，不做粉饰；②判据层面的两个数值<3% 型陈述
（可复现性 0.02 阈值）虽自报通过（1.32e-16 << 0.02），但缺原始日志，只记 SELF_REPORTED；
③本补报**不是 honest-FAIL**：mission 718 自身据其自报判据为 succeeded，但**证据链断裂**，
故证据等级判 WITHHELD，并已修复"终态无报告"这一治理缺环。
