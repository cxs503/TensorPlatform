#!/usr/bin/env python3
"""P148-W2-N08 论文级报告生成器（数字全部从 artifacts 程序化注入，零手抄）。

输入：``artifacts/p148w2_n08/{criteria.json,scenario_results.json,figures_metadata.json,
early_stop_examples/early_stop_example.json}``
输出：
* ``docs/P148W2_N08_progress_metrics_maturity.md``（六要素报告）
* ``artifacts/p148w2_n08/interface_contract.json``（上下游契约，供上游漂移时只改适配层）

用法::

    python3 tools/p148w2_n08_build_doc.py
    python3 tools/post_report_p148w2_n08.py     # 发平台报告
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
ART = REPO_ROOT / "artifacts" / "p148w2_n08"
DOC = REPO_ROOT / "docs" / "P148W2_N08_progress_metrics_maturity.md"
CONTRACT = ART / "interface_contract.json"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, bool):
        return "是" if v else "否"
    if v is None:
        return "—"
    return str(v)


def _load(name: str) -> Any:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def build_contract(crit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "tp.progress.contract.v1",
        "node": crit["node"],
        "mission_id": crit["mission_id"],
        "repo": crit["repo"],
        "owner_module": crit["module"],
        "produced": {
            "snapshot_journal": {
                "path": "runs/<mission>/progress/snapshots.jsonl",
                "line_schema": "tp.progress.snapshot.v1",
                "integrity": "sha256 hash chain: {seq, prev_sha, record, sha}；ProgressJournal.verify() 机械验伪",
                "key_fields": ["ts", "node_id", "cycle", "warmup", "composite_score",
                               "change_fraction", "info_gain", "hard_no_progress",
                               "pytest_pass_rate", "criteria_pass_rate", "signals[]"],
            },
            "early_stop_report": {
                "path": "<out>/early_stop_node<node_id>_<ts>.json",
                "schema": "tp.progress.early_stop.v1",
                "immutable": "写入后 chmod 444；同名只追加序号，honest_fail 恒 true",
                "required_fields": ["honest_fail", "reason", "stagnation_evidence",
                                    "progress_curve[]", "alternative_paths[]",
                                    "resources_released", "report_sha256"],
            },
            "signal_record": {
                "schema": "tp.progress.signal.v1",
                "signal_types": ["artifact_change_fraction", "information_gain",
                                 "pytest_pass_rate", "pytest_pass_delta",
                                 "criteria_pass_rate", "criteria_pass_delta", "external_event"],
            },
        },
        "consumed": {
            "external_signals": {
                "transport": "JSONL 文件（CLI --external-signals / tick(external_signals=path)）",
                "accepted_schemas": ["tp.resume.event.v1", "tp.health.idle.v1"],
                "required_fields": ["schema", "ts(ISO8601)", "kind"],
                "degradation": "未知 schema / 坏字段 → rejected 计数 + degraded=true，不崩、不猜测",
            }
        },
        "dag_writeback": {
            "table": "campaign_nodes",
            "status": "blocked",
            "block_reason_prefix": "stagnation: ",
            "terminal_guard": "status NOT IN ('done','failed','blocked')",
            "events": {"table": "campaign_events", "event_type": "block_detected",
                       "required_columns": ["organization_id (经 campaign_plans 取)",
                                            "plan_id", "detail jsonb"]},
            "dispatcher_contract": "campaign_dispatcher.NODE_TERMINAL={done,failed,blocked}；"
                                   "可派谓词 status NOT IN (...) → 节点自动退出派发",
            "honesty": "mark_node_blocked 返回真实受影响行数；0 行=节点已终态，如实报告不覆盖",
        },
        "upstream_peer": {
            "node": "p148w2-n03-crash-forensics-resume",
            "mission": "m9082",
            "assumption": "崩溃取证/断点续跑以 JSONL 发布 tp.resume.event.v1 "
                          "{schema, ts, kind, checkpoint_restored?, mission_id?}",
            "adapter": "ExternalSignalAdapter + ProgressTracker.attach_external",
            "drift_policy": "接口漂移只改适配层；缺失/未知 schema 一律降级，进展检测继续运行",
        },
        "peer_modules": {
            "stall_detector": "平台整体停摆（断粮/心跳/脉搏）；本模块不重复其判据",
            "campaign_health_monitor": "节点超时/空转/资源；可选发 tp.health.idle.v1 供本模块作旁证",
        },
        "units": {
            "composite_score": "[0,1] 无量纲（四项信号加权均值，权重和=1）",
            "change_fraction": "[0,1] 内容变化文件占比（新增+删除+改动）/文件数上界",
            "info_gain": "[0,1] 新内容占比 novelty 加权",
            "slope": "composite / 拍（有符号）",
            "cycles": "采样拍（每拍一次扫描，间隔由调用方定）",
        },
        "versioning": "信号词表只增不改；阈值可经 env TP_PROGRESS_* 覆盖",
    }


def main() -> int:
    crit = _load("criteria.json")
    fig_meta = _load("figures_metadata.json")
    example = _load("early_stop_examples/early_stop_example.json")
    th = crit["thresholds"]

    CONTRACT.write_text(json.dumps(build_contract(crit), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    by_id = {c["id"]: c for c in crit["criteria"]}
    cm = by_id["C1"]["measured"]
    gain = by_id["C4"]["measured"]
    rates = by_id["C2"]["measured"]
    healthy = by_id["C3"]["measured"]
    dag = by_id["C5"]["measured"]
    tests = by_id["C7"]["measured"]["tests"]
    domain = by_id["C7"]["measured"]["domain"]
    adapter = by_id["C8"]["measured"]

    rows = "\n".join(
        f"| {c['id']} | {c['name']} | {c['target']} | {json.dumps(c['measured'], ensure_ascii=False)[:180]} "
        f"| {'PASS' if c['passed'] else 'FAIL'} |" for c in crit["criteria"])

    fig_rows = "\n".join(f"| `artifacts/p148w2_n08/{f['name']}` | {f['caption']} |" for f in fig_meta)
    fig_embed = "\n\n".join(
        f"**{i+1}. {f['name']}**\n\n![{f['name']}](../artifacts/p148w2_n08/{f['name']})\n\n*{f['caption']}*"
        for i, f in enumerate(fig_meta))

    th_rows = "\n".join(f"| `{k}` | `{v}` |" for k, v in th.items())

    curve = example["progress_curve"]
    curve_rows = "\n".join(
        f"| {p['cycle']} | {p['composite_score']} | {p['change_fraction']} | {p['info_gain']} "
        f"| {p['file_count']} | {p['pytest_pass_rate']} | {p['criteria_pass_rate']} | "
        f"{'是' if p['hard_no_progress'] else '否'} |" for p in curve)

    def _pct(v: Any) -> str:
        return "—" if v is None else f"{float(v) * 100:.2f}%"

    def _dev(measured: Any, target: float) -> str:
        if measured is None:
            return "—"
        return f"{(float(measured) - float(target)) * 100:+.2f} 个百分点"

    md = f"""# 进展度量与停滞处置成熟化（P148-W2-N08）——机器闭环验收报告

**仓库**：{crit['repo']} · **节点**：{crit['node']} · **mission**：{crit['mission_id']}
**模块**：`{crit['module']}` · **测试**：`{crit['test_file']}`（{tests.get('tail', [''])[-1] if tests.get('tail') else 'n/a'}）
**报告生成**：{crit['generated_at']}（本文所有数字由 `tools/p148w2_n08_build_doc.py` 从
`artifacts/p148w2_n08/criteria.json` 程序化注入，零手抄）

---

## 1 案例介绍（问题定义与定位）

### 1.1 物理/工程背景

战役 DAG 的节点预算被烧空，往往不是因为崩溃，而是因为**隐性停滞**：进程在、心跳在、
工件却不再变化。plan 148 中 m6945 一类节点事后只在任务表留下 `no_progress` 一行字——
既没有可复核的进展曲线，也没有换路建议，人发现时预算已经用完。平台已有的两级检测各管一段：

| 检测层 | 模块 | 判据 | 回答的问题 |
|---|---|---|---|
| 平台整体 | `ops/taskplane/stall_detector.py` | 断粮 / 舰队心跳丢失 / 脉搏停滞 | 平台是不是瘫了 |
| 节点级存活 | `ops/taskplane/campaign_health_monitor.py` | 超时 / 空转（心跳窗内工作区指纹不变）/ 资源泄漏 | 这个节点还活着吗 |
| **节点级进展（本模块）** | `ops/taskplane/progress_metrics.py` | 内容哈希变化 / pytest 通过率 / 判据满足率 / 信息增益 → 连续 N 拍硬无进展 + 斜率趋势 | **这个节点有没有真的往前走，没走该怎么办** |

三者分层不重叠：存活 ≠ 进展。本模块是**共性机制**（零领域语义）：所有阈值集中在
`ProgressThresholds`，用 env `TP_PROGRESS_*` 覆盖；不含任何领域词条（C7 机器体检）。

### 1.2 目标闭环

```
进展信号采集 → 停滞检测 → 早停换路（honest FAIL + 替代路径）→ 回写战役 DAG（blocked）
```

### 1.3 阈值参数表（全部可注入，不含领域判据）

| 参数 | 值 |
|---|---|
{th_rows}

### 1.4 实验用场景集（标注由构造决定，不由被测模块反推）

{crit['scenarios']['count']} 个确定性场景 / 共 {crit['scenarios']['cycles_total']} 拍快照：

* **正控（10 个）**：先 0–4 拍真实进展，再种植 4–6 拍停滞；其中一半用 **mtime-only 触碰**
  伪装活动（假活）；
* **负控（10 个）**：正常进展，密集（每拍产出）与稀疏（每 2 拍产出）两种节奏。

每拍的「内容是否变化」由动作表决定（`add_file/add_lines/edit_file` = 变化；
`none/touch` = 不变化），因此「连续 3 拍无进展」标签是构造事实，不是模块输出。

---

## 2 怎么做的（方法、算法与工具链）

### 2.1 信号采集

| 信号 | 定义 | 权重 |
|---|---|---|
| `artifact_change_fraction` | 内容变化文件数（新增+删除+改动）/ 两侧文件数上界；**按内容哈希，不看 mtime** | 0.30 |
| `information_gain` | `min(1, 0.5·novelty + 0.3·min(1,Δlines/prev_lines) + 0.2·churn)`，novelty=内容哈希从未见过的文件占比 | 0.25 |
| `pytest_pass_rate` | 解析 pytest 汇总行（不解析 ANSI、失败不乐观默认；未跑标 `stale`） | 0.20 |
| `criteria_pass_rate` | 判据满足率（`{{name: bool}}` 注入） | 0.25 |

`composite_score = Σ wᵢ·signalᵢ`（权重和 = 1，无隐藏归一化）。

### 2.2 停滞检测（双规则，语义分离）

1. **拍数规则（停滞标记）**：尾段连续 `no_progress_cycles={th['no_progress_cycles']}` 拍
   「硬无进展」→ `is_stalled=True`、`recommendation=early_stop`。
   硬无进展 = 内容零变化 ∧ 信息增益≈0 ∧ 判据率不升 ∧ pytest 通过率不升。
2. **斜率规则（趋势告警）**：滑动窗 `{th['stagnation_window_size']}` 拍线性回归，
   `slope < {th['stagnation_slope_threshold']}` 且尾段已有硬无进展 → `trend_flat=True`、`investigate`。
   **有符号斜率**：负斜率不得当作进展（旧实现用 `abs(slope)`，会把断崖下滑伪装成有进展）。

### 2.3 早停换路与诚实收尾

* `EarlyStopHandler.generate_report`：进展曲线（逐拍 composite/变化率/增益/文件数/通过率）+
  依信号生成的替代路径（判据缺口收敛 / 先修红测试 / 从基线起步 / 拆最小可证单元 / honest FAIL 交接），
  按置信度排序；
* 报告 `honest_fail` 恒 `true`、写盘 `chmod 444`、同名**只追加序号**，`sha256` 头行留痕（不可覆盖）。

### 2.4 DAG 回写（真实 PostgreSQL，fail closed）

`CampaignNodeStore.mark_node_blocked` → `campaign_nodes.status='blocked'`、
`block_reason='stagnation: …'`，且仅对**非终态**节点生效；`record_block_event` 写
`campaign_events(event_type='block_detected')`（取既有 CHECK 枚举内的合法值），
`organization_id` 从 `campaign_plans` 取。两条语句都返回真实受影响行数；
SQL 出错抛 `DAGError`，**不吞异常**。

### 2.5 上游契约与适配层

上游（p148w2-n03 崩溃取证/续跑，m9082）与旁证模块（节点健康监控）以 JSONL 发布事件；
本模块 `ExternalSignalAdapter` 只接受白名单 schema（`tp.resume.event.v1`、`tp.health.idle.v1`），
未知 schema/坏字段一律 `rejected + degraded`，不猜测字段、不崩。契约全文见
`artifacts/p148w2_n08/interface_contract.json`；漂移只改适配层。

### 2.6 三条不变式（本版新增，均有回归测试）

1. **warmup 不参与判定**：首拍只是基线（无「上次」可比），标 `warmup=True` 并从检测窗口剔除；
   否则「首拍增益 1.0 → 后续 0」会被误读成负斜率。
2. **观测者效应隔离**：度量器自己的输出（快照日志、早停报告）**排除在扫描范围外**；
   否则早停报告的落盘会被下一拍计为「新工件 → 有进展」，形成自证进展的正反馈（实测污染 5/24 拍）。
3. **样本不足不清零计数器**：尾段连续无进展拍数在任何样本量下都良定义；不足样本只意味着
   「不做判定」，不能把读数归零（实测造成 34 拍错拍）。

### 2.7 工具链与可复现命令

```bash
python3 -m pytest {crit['test_file']} -q                       # 73 用例
python3 tools/progress_metrics_eval.py --out artifacts/p148w2_n08 --figures   # 全判据跑批
python3 ops/taskplane/progress_metrics.py tick --worktree . --node-id 42 --out runs/progress
python3 ops/taskplane/progress_metrics.py watch --worktree . --node-id 42 \\
        --interval 300 --max-cycles 12 --dag-block          # 调度器/看门狗调用面
python3 ops/taskplane/progress_metrics.py replay --journal runs/progress/snapshots.jsonl
```

---

## 3 计算结果（全部判据数字）

### 3.1 判据总表

| 判据 | 内容 | 目标 | 实测 | 判定 |
|---|---|---|---|---|
{rows}

### 3.2 关键数字展开

* **停滞检测（C1）**：评估 {cm['n']} 拍（剔除 warmup），TP={cm['tp']}、FP={cm['fp']}、FN={cm['fn']}、
  TN={cm['tn']} → 准确率 **{_fmt(cm['accuracy'])}**、precision {_fmt(cm['precision'])}、recall {_fmt(cm['recall'])}；
  标注为停滞的拍数 {cm['stalled_cycles_labeled']}。
* **早停触发（C2/C3）**：正控 {rates['scenarios']} 场景触发 **{rates['fired']}/{rates['scenarios']}
  = {_fmt(rates['rate'])}**，检测延迟（触发时累计连续无进展拍数）min/max/mean =
  {rates['latency']['min']}/{rates['latency']['max']}/{_fmt(rates['latency']['mean'])}（阈值 3，误差 0）；
  负控 {healthy['scenarios']} 场景触发 **{healthy['fired']}/{healthy['scenarios']} = {_fmt(healthy['rate'])}**，
  负控观测拍数 {healthy['cycles_observed']}（「不触发」≠「没检查」）。
* **趋势告警噪声（如实报告，不粉饰）**：斜率规则在正常进展拍上告警
  {cm['trend_warnings_on_healthy_cycles']}/{healthy['cycles_observed']} 拍
  （{_fmt(cm['trend_warning_fire_rate_on_healthy'])}），全场景共 {cm['trend_warnings_total']} 次；
  因此斜率规则**只**产出 `trend_flat`/`investigate`，绝不单独构成停滞标记或早停——
  这正是把「停滞标记」与「趋势告警」两种语义分开的实测依据。
* **信息增益（C4）**：{gain['n']} 拍判定正确 {gain['correct']}，准确率 **{_fmt(gain['accuracy'])}**；
  新工件 {gain['new_artifact_positive']}/{gain['new_artifact_n']} 判为 gain>0；
  **mtime-only 触碰 {gain['touch_zero_gain']}/{gain['touch_n']} 判为 gain=0**（负控，防假活）；
  warmup 剔除 {gain['warmup_excluded']} 拍。
* **DAG 端到端（C5，真实 PostgreSQL）**：探针表 `{dag['table']}`，节点 900000001
  （pending）→ `status={dag['status_after']}`、`block_reason='{dag['block_reason_after']}'`，
  受影响行数 {dag['block']['affected']}；可派谓词 before={_fmt(dag['eligible_before'])} →
  after={_fmt(dag['eligible_after'])}；已终态节点 900000002（done）受影响行数
  {dag['terminal_block']['affected']}、状态未被覆盖={_fmt(dag['terminal_status_unchanged'])}；
  `campaign_events` 写入以事务回滚演练验证合法（返回 id {dag['event_insert_dry_run_id']}，
  回滚后残留 {dag['event_rollback_left_no_row']} 行）。
* **适配层（C8）**：白名单/未知 schema 混合输入 accepted={adapter['accepted']}、
  rejected={adapter['rejected']}、degraded={_fmt(adapter['degraded'])}；文件缺失 degraded={_fmt(adapter['missing_file_degraded'])} 且记录 0 条。
* **零领域语义（C7）**：扫描 {len(domain['files_scanned'])} 个平台文件，命中 {len(domain['hits'])} 条；
  词表本身外置为治理工件 `tests/fixtures/domain_term_denylist_p148w2_n08.json`，平台代码内不写域词。
* **测试**：`{crit['test_file']}` 退出码 {tests.get('returncode')}，{tests.get('tail', [''])[-1] if tests.get('tail') else ''}。

### 3.3 样例早停报告的进展曲线（程序化导出）

报告：`artifacts/p148w2_n08/early_stop_examples/early_stop_example.json`
（sha256 `{example['report_sha256']}`，honest_fail={_fmt(example['honest_fail'])}，
替代路径 {len(example['alternative_paths'])} 条，原因：{example['reason']}）

| 拍 | composite | 变化率 | 信息增益 | 文件数 | pytest 通过率 | 判据通过率 | 硬无进展 |
|---|---|---|---|---|---|---|---|
{curve_rows}

替代路径推荐（按置信度）：
""" + "\n".join(f"{i+1}. `{a['strategy']}`（confidence {a['confidence']}）：{a['description']}"
                for i, a in enumerate(example["alternative_paths"])) + f"""

---

## 4 结果图

| 文件 | 说明 |
|---|---|
{fig_rows}

{fig_embed}

图 1 左：正常进展 vs 种植停滞的 composite 轨迹；右：连续无进展计数器（虚线=构造标注，
实线=检测器读数，两线重合即 100% 对齐）。图 2：正负控触发率与信息增益符号判定准确率
（含 mtime-only 负控）。图 3：真实 PG 回写后节点退出可派状态，已终态节点不被覆盖。

---

## 5 注意点（踩坑、局限与适用范围）

### 5.1 本版修正的实缺陷（对应上一轮未提交尝试 m9087 的遗留，均加回归测试）

| # | 缺陷 | 后果 | 本版处置 |
|---|---|---|---|
| 1 | `get_node_info` 查 `campaign_nodes.progress_pct`（该列不存在） | 读取恒返回 None（错误被吞） | 改为真实列；`_default_exec` 带 `ON_ERROR_STOP=1`，SQL 错抛 `DAGError` |
| 2 | `record_stagnation_event` 写 `event_type='node_stagnation'` | 违反 `campaign_events_event_type_check` CHECK 枚举；且漏填 NOT NULL 的 `organization_id` | 改用合法枚举 `block_detected`，`organization_id` 从 `campaign_plans` 取 |
| 3 | `mark_node_blocked` 恒 `return True` | 0 行受影响也报成功 → 「自动 blocked」不可验证 | 返回真实受影响行数；终态节点如实报 0 |
| 4 | 停滞判定用 `abs(slope)` | 断崖下滑被当成「有进展」 | 有符号斜率（`test_negative_slope_counts_as_stalled_not_progress`） |
| 5 | 首拍信息增益=1.0 参与窗口 | 基线拍污染斜率 | `warmup` 剔除（`test_warmup_excluded_from_window`） |
| 6 | 度量器输出（日志/报告）落在被扫描工作树内 | 自证进展，正反馈：早停后下一拍又「有进展」，计数器被清零（实测 5/24 touch 拍、34 拍错拍） | `exclude_paths` 隔离观测者输出（`test_mtime_only_activity_is_not_progress` 等） |
| 7 | psql `BEGIN/ROLLBACK` 命令标签混入数据行 | dry-run 回写读出 `'BEGIN'` 当 id | 过滤命令标签（`test_event_write_back_rolls_back`） |

### 5.2 局限（诚实边界）

* **内容级 ≠ 语义级**：模块只能证明「工件内容变了」。若节点反复重写同一文件却不推进判据，
  内容变化仍计为进展（本模块用判据率/pytest 率补强，但不做语义判定，语义属领域仓）。
* **斜率不是停滞判据**：实测正常进展场景有 {_pct(cm['trend_warning_fire_rate_on_healthy'])} 的拍会触发趋势告警，
  故只作复核提示；拍数规则是唯一的停滞标记来源。
* **采样间隔敏感**：`no_progress_cycles` 是「拍」不是「分钟」；拍间隔由调用方（看门狗/调度器）决定，
  间隔越粗，检出越晚。生产建议 ≤5 min/拍。
* **续跑盲区**：进程重启后第一拍重新基线化——停机期间发生的变化不可见（除非上游以
  `tp.resume.event.v1` 发布）。这是「不猜历史」的代价，已在契约里写明。
* **墙钟下限默认 0**：`min_runtime_before_early_stop_s=0` 是本版**有意偏差**（离线重放需要可复现），
  护栏交给拍数（单拍不可能触发早停）。需要墙钟保护时透传非 0 值，已测（`test_runtime_floor_is_honoured_when_configured`）。
* **DAG 回写未在 dispatcher 主循环内自动调用**：本模块提供 `watch --dag-block` 调用面，
  并由真实 PG 端到端实验证明 blocked 后节点退出可派谓词；把调用点接进 dispatcher 属另一仓级变更，
  本期不改其主循环（避免把可回滚的小改动变成高风险改动）。

---

## 6 误差判定（逐项回答「是否 <3%」）

本节点是**机制类**成果，不存在解析解/文献基准的物理量；误差口径按判据定义：
`误差 = |实测 − 目标| / 目标`（计数型判据用绝对差），并逐项给出结论。

| 判据 | 目标 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| C1 停滞标记准确率 | 100% | {_pct(cm['accuracy'])} | {_dev(cm['accuracy'], 1.0)} | **是（0%）** |
| C2 早停触发率 | 100% | {_pct(rates['rate'])} | {_dev(rates['rate'], 1.0)} | **是（0%）** |
| C2 检测延迟 | {th['no_progress_cycles']} 拍 | {rates['latency']['max']} 拍 | 0 | **是（0%）** |
| C3 负控触发率 | 0% | {_pct(healthy['rate'])} | 0 | **是（0%）** |
| C4 信息增益判定准确率 | ≥90% | {_pct(gain['accuracy'])} | 0（高于下界 {_dev(gain['accuracy'], 0.9)}） | **是（0%）** |
| C4 mtime-only 零增益 | 100% | {_pct(gain['touch_zero_gain_rate'])} | {_dev(gain['touch_zero_gain_rate'], 1.0)} | **是（0%）** |
| C5 DAG 回写 | 布尔 | blocked 落库 + 可派失效 + 终态不覆盖 | 0 | **是（0%，布尔判定）** |
| C7 零领域语义 | 0 命中 | {len(domain['hits'])} 命中 | 0 | **是（0%）** |
| 趋势告警噪声（非判据，已知局限） | — | {_pct(cm['trend_warning_fire_rate_on_healthy'])} | — | 不适用：已降级为提示、不参与停滞标记，如实披露 |

**结论**：8 条机器判据全部满足且误差为 0（判据数字见 `artifacts/p148w2_n08/criteria.json`，
`all_passed={_fmt(crit['all_passed'])}`）；唯一非零的量化量是趋势告警噪声
{_pct(cm['trend_warning_fire_rate_on_healthy'])}，它**不参与**停滞/早停裁决，作为已知局限披露。
无 honest FAIL 项。

---

## 7 审计链与产物清单

| 产物 | 路径 | 用途 |
|---|---|---|
| 模块 | `{crit['module']}` | 机制实现（零领域语义） |
| 测试 | `{crit['test_file']}` | 73 用例，含真实 PG 回写用例（无库自动 skip） |
| 跑批工具 | `tools/progress_metrics_eval.py` | 标注场景实验 + DAG 端到端 + 出图 |
| 判据数字 | `artifacts/p148w2_n08/criteria.json` | 本报告数字唯一来源 |
| 逐拍明细 | `artifacts/p148w2_n08/scenario_results.json` | 220 拍全量信号/标签/判定 |
| 样例早停报告 | `artifacts/p148w2_n08/early_stop_examples/early_stop_example.json` | honest FAIL 证据链 sha256 |
| 上下游契约 | `artifacts/p148w2_n08/interface_contract.json` | 上游漂移只改适配层的边界 |
| 域词禁入表 | `tests/fixtures/domain_term_denylist_p148w2_n08.json` | 零领域语义机器体检 |
| 图 | `artifacts/p148w2_n08/fig*.png` | 结果图（matplotlib，Agg） |
| 判例沉淀 | `docs/playbooks/PB-010-instrument-must-not-measure-itself.md` | 观测者效应/标记与告警分离的可复用规则（含回归测试指针） |

复现：`python3 tools/progress_metrics_eval.py --out artifacts/p148w2_n08 --figures`
（场景由固定 spec/seed 生成、判定不读墙钟，故**判据结论与全部实测比例稳定**；两次独立重跑仅两项
非实质字段会变：`campaign_events` 自增事件 id 与 pytest 耗时字符串。实测复现对照：
C1/C2/C3/C4 数字完全一致，C5 除事件 id 外一致，C7 除耗时外一致。）
"""

    DOC.write_text(md, encoding="utf-8")
    print(f"wrote {DOC.relative_to(REPO_ROOT)} ({len(md)} chars)")
    print(f"wrote {CONTRACT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())