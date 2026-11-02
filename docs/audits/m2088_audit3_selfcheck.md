# m2088 / audit3 自检记录（阶段一/C 审计单）

- 任务：TensorPlatform vs TensorPhysics 职责边界现状审计（只读 + 产文档，**不改生产代码**）
- 分支：`agent/m2088` · 基线：`57e72968` · 日期：2026-11-02 UTC
- 主交付：`docs/architecture/platform_physics_boundary_audit.md`
- 机器产物：`artifacts/audit3/`

## 1. 跑了什么（命令 + 结果）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python3 artifacts/audit3/collect_audit3.py` | exit 0；产出 12 个工件（6 JSON/CSV + 3 PNG + findings.md + collector） |
| 2 | 同上重复运行，比较 `md5sum` | `audit3_import_edges.json` = `6ec822c6…` 两次一致；`audit3_evidence_check.json` = `cd695a61…` 两次一致 → **采集幂等** |
| 3 | 同上，`grep -c UserWarning` | **0**（图 2 原用中文标签触发 DejaVu 缺字，已改为英文标签，无缺字告警） |
| 4 | 引用核验（内置） | **22/22 通过**（逐条打开 `文件:行号` 比对期望 token；允许 ±8 行漂移并记录 `actual_line`） |
| 5 | 独立 grep 交叉核对导入边 | AST 符号级 95 边 / 35 语句 / 14 文件；独立 grep 绝对导入 29 语句 / 14 文件；差额 6 条为相对导入 → 口径自洽（报告 §3.6） |
| 6 | `git show --stat 4f2ca17b` / `2684b6ae` | 原文粘贴进报告 §4.3，未改写 |
| 7 | `grep -rln tensor_dev_missions --include=*.py \| grep -v artifacts/audit3 \| wc -l` | **82**（含本审计采集器则 83 → 统计须排除自产文件，已在报告标注） |
| 8 | `git -C /root/tensor-ecosystem/TensorPhysics rev-list --left-right --count HEAD...origin/main` | `0  76` → 本地 checkout 落后 origin/main 76 提交；统计双口径并报（263 / 339） |

## 2. 明确没做的事（不粉饰）

1. **未改任何生产代码**（按本阶段纪律）；`backend/`、`tools/`、`migrations/` 零改动。
2. **未跑后端测试套件**：本单无生产代码改动、无回归面；既有测试的通过与否不能证明本审计结论（HTTP 200 / 单测绿 ≠ 架构结论正确）。
3. **未查 DB 运行态**：`tensor_dev_missions` / `tensor_agent_session_events` / `tensorphysics_runs` 的真实行数与内容未读 → "是否存在同一事实双写"仍为【未验证】。
4. **未做物理数值验证**：本单是架构/职责审计，无数值物理量比对，"误差 <3%"不适用（报告 §7 显式声明不适用，而非含糊通过）。
5. **未判断 TensorPhysics 与 TP 耦合时钟的语义等价性**：`coupling_clock_sync.py` 与 `coupling.py` 只做了"存在重复"的判定；语义 diff 留阶段二（报告 §6 第 3 条）。
6. **未度量物理工作量**：提交数是规模指标，不能推出"物理研究减少"；报告 §4.4 / §8.4 已标注。

## 3. 结论三态统计

| 三态 | 条数（报告正文） | 备注 |
|---|---|---|
| 【已证实】 | C1–C6、C8、C9 + §2/§3/§4 各表逐项 | 均附 `文件:行号` 或命令输出，22 条关键引用机器核验通过 |
| 【推测】 | C7 的因果归因、§3.2 `benchmark_canon` 长歪的动因、§3.4 密度差 0.02% 的量级估计 | 均已标注，未当结论使用 |
| 【未验证】 | DB 运行态、TensorPhysics 契约真实消费方、组织层资源归因、历史分支可达性 | 列入报告 §8 与 §6 阶段二动作 |

## 4. 交付清单

- `docs/architecture/platform_physics_boundary_audit.md`（主报告：依赖图 + 双状态机盘点 + 95 边接点清单 + 弱化证据链 + 20 项迁移清单草案 + 阶段二建议 + 局限）
- `artifacts/audit3/collect_audit3.py`、`audit3_*.json|csv|png`、`audit3_findings.md`
- 本文件 `docs/audits/m2088_audit3_selfcheck.md`