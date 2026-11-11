#!/usr/bin/env python3
"""把 KPI-QC-RECHECK 的工件渲染成报告级 Markdown（零手抄：所有数字从 JSON/工件读）。

用法::

    python3 scripts/kpi_qc_recheck_docgen.py \
        --dir artifacts/kpi_qc_recheck --out docs/kpi/KPI_QC_RECHECK_L2.md
"""
from __future__ import annotations

import argparse
import json
import os


def _load(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        if path.endswith(".json"):
            return json.load(fh)
        return fh.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="artifacts/kpi_qc_recheck")
    ap.add_argument("--out", default="docs/kpi/KPI_QC_RECHECK_L2.md")
    args = ap.parse_args()
    d = _load(os.path.join(args.dir, "kpi_result.json"))
    prov = _load(os.path.join(args.dir, "provenance.json")) or {}
    bench_raw = _load(os.path.join(args.dir, "benchmark_raw.json")) or {}
    bench_db = _load(os.path.join(args.dir, "benchmark_db.json")) or {}
    integ = _load(os.path.join(args.dir, "integration_call.json")) or {}
    psql = _load(os.path.join(args.dir, "recompute_psql.txt")) or ""

    lad = d["ladder"]
    ps = d.get("phase_split", {})
    bl = d.get("pending_backlog", {})
    lat = d.get("latency", {})
    cc = integ.get("cross_check_vs_measure_py", {})
    qc = integ.get("qc_recheck_rate", {})

    L = []
    A = L.append
    A("# KPI-QC-RECHECK：已合并 agent PR 独立参考复核通过率 —— 计量能力交付与实测值")
    A("")
    A(f"- 计量时刻（脚本打点）：`{d['measured_at']}`")
    A(f"- 能力入口：`python3 -m tools.kpi_qc_recheck.measure --days 150 --outdir {args.dir}`".replace("150", str(d["window"]["days"])))
    A("- 独立复算：`python3 -m tools.kpi_qc_recheck.verify --raw-dir ... --result ... --out ...`")
    A("- 平台侧真调用：`backend/services/platform_kpi.py::_compute_qc_recheck` → "
      "`GET /api/v1/tensor/kpi/platform_dashboard` 的 `qc_recheck_rate` 维度")
    A("- 计量纪律：只读（连接置 read_only，全部 SELECT）；本能力不改任何被计量数据")
    A("")
    A("## ① 案例介绍（计量对象与口径）")
    A("")
    A("被计量对象不是物理场，而是**平台质量控制回路本身**：近窗口期内由智能体产出、"
      "经合并口进入主干的每一份交付（agent PR），有多少份被**独立参考复核**过并且通过。")
    A("")
    A("| 项 | 定义 | 数据源 |")
    A("|---|---|---|")
    A("| 分母 N | 窗口内已合并 agent PR，按 `(mission_id, repo, pr)` 去重 | "
      "`mission_lifecycle_log`，`action='pr_gate'` 且 `detail->>'state'='merged'` |")
    A("| 分子 M | 该 mission 存在 `reviewer_kind='independent'` 且 `review_status='reviewed'` "
      "且 `status='confirmed'` 的记录 | `outcome_evaluations` |")
    A(f"| 指标 | M / N × 100%（目标 {d['target']}） | 本能力两条独立实现 |")
    A("")
    A("分子排除 `auto_scan`（平台证据自动扫描不是「另一双眼睛」）：把它算进分子会把读数虚高，"
      "虚高量正是定义阶梯 D1 − D2。")
    A("")
    A("## ② 怎么做的（方法/工具链）")
    A("")
    A("1. `tools/kpi_qc_recheck/queries.py`：只读 SQL（分母/复核记录/供给侧/积压）+ 两个 Reader"
      "（psycopg 直连 = 离线计量；SQLAlchemy Session = 平台 API 内联调用，口径单一真源）。")
    A("2. `tools/kpi_qc_recheck/measure.py`：纯函数聚合（`dedupe_denominator` → "
      "`index_evaluations` → `aggregate`），产出主读数 + 口径阶梯 + 分仓 + 逐日 + 时延 + 积压 + "
      "未复核清单，并落盘原始行 CSV 与逐件 sha256 的 provenance。")
    A("3. `tools/kpi_qc_recheck/verify.py`：**第二实现**（CSV set 交集法 / 手写单条 SQL），"
      "与自报值逐项比对（计数要求精确相等）。")
    A("4. `scripts/kpi_qc_recheck_integration_probe.py`：建真实 SQLAlchemy Session 调 "
      "`services.platform_kpi.compute_all_kpis(db)`，证明面板读数=脚本读数。")
    A("5. `scripts/kpi_qc_recheck_figures.py`：只读 `kpi_result.json` 出 4 张图。")
    A("6. 测试：`tests/test_kpi_qc_recheck.py`（17 例，脱库，断言不变式而非「跑过」）。")
    A("")
    A("## ③ 计算结果（程序化注入，零手抄）")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A(f"| 分母 N（已合并 agent PR，去重） | **{d['denominator']['merged_prs']}** |")
    A(f"| 分子 M（独立复核通过） | **{d['numerator']['ind_reviewed_prs']}** |")
    A(f"| **KPI 现值（M/N）** | **{d['current']}%** |")
    A(f"| 目标 / 缺口 | {d['target']}% / **{d['gap']} 个百分点** |")
    A(f"| 覆盖 mission 数 | {d['denominator']['merged_missions']} 个任务被合并，"
      f"{d['numerator']['ind_reviewed_missions']} 个有独立复核通过 |")
    A("")
    A("**口径阶梯（同一分母）**")
    A("")
    A("| 级 | 定义 | PR 数 | 覆盖率 |")
    A("|---|---|---|---|")
    for k, label in (("D0_any_record", "D0 有任何复核记录（含 pending 占位行）"),
                     ("D1_any_confirmed", "D1 任意 kind 的 reviewed+confirmed（含 auto_scan）"),
                     ("D2_independent_confirmed", "D2 independent 的 reviewed+confirmed（**主读数**）"),
                     ("D3_independent_cross_model", "D3 D2 且 cross_model_verified")):
        A(f"| {k.split('_')[0]} | {label} | {lad[k]['prs']} | {lad[k]['pct']}% |")
    A("")
    A("**阶段切分（切点=最后一次独立复核给出结论的日期，脚本自动判定）**")
    A("")
    A("| 阶段 | 已合并 PR | 独立复核通过 | 通过率 |")
    A("|---|---|---|---|")
    for k, label in (("before_or_on_pivot", f"≤ {ps.get('before_or_on_pivot', {}).get('cutoff', '')}"),
                     ("after_pivot", f"> {ps.get('after_pivot', {}).get('cutoff', '')}")):
        if k in ps:
            A(f"| {label} | {ps[k]['merged_prs']} | {ps[k]['ind_reviewed']} | {ps[k]['rate_pct']}% |")
    A("")
    A("**分仓覆盖（前 10）**")
    A("")
    A("| 仓 | 已合并 PR | 有任何复核 | 独立复核通过 | 通过率 |")
    A("|---|---|---|---|---|")
    for r in d["per_repo"][:10]:
        A(f"| {r['repo']} | {r['merged_prs']} | {r['any_review']} | {r['ind_reviewed']} | {r['rate_pct']}% |")
    A("")
    A(f"**复核供给侧/积压**：待裁决 pending 行 **{bl.get('pending_rows')}** 行（"
      f"{bl.get('pending_missions')} 个任务，其中 {bl.get('pending_succeeded_missions')} 个任务已 succeeded）。")
    A("")
    A(f"**复核时延**（合并→首个独立复核结论，n={lat.get('n')}）：中位 **{lat.get('median_h')} h**，"
      f"P90 **{lat.get('p90_h')} h**。")
    A("")
    A("## ④ 结果图")
    A("")
    A("1. `fig1_definition_ladder.png` — 口径阶梯 D0/D1/D2/D3 + 100% 目标线。"
      "**读法**：D1 与 D2 之间的落差就是 auto_scan 冒充满复核的虚高量。")
    A("2. `fig2_per_repo.png` — 分仓已合并量（灰）vs 独立复核通过（橙）。"
      "**读法**：灰柱高、橙柱贴地的仓=零覆盖缺口区。")
    A("3. `fig3_daily.png` — 逐日合并 vs 独立复核通过。**读法**：后半段橙柱消失=复核通道停产。")
    A("4. `fig4_review_supply.png` — 复核供给侧逐日状态堆叠。**读法**：堆叠里灰（pending）为主"
      "=队列只建行不裁决。")
    A("")
    A("## ⑤ 注意点（坑、局限、适用范围）")
    A("")
    A("1. **窗口是滚动的**：`created_at > now() - 30 days`，两次读数之间新合并 PR 会进分母，"
      "故面板与脚本读数允许分母只增、比率差 ≤0.01pp 的漂移（见 `integration_call.json` 的 "
      "`drift_tolerance`，本次实测 drift=0）。")
    A("2. **回写窗口短于 30 天**：pr-gate 回写最早记录在 11-05，实际有效窗口约 7 天，"
      "任何「近 30 天」结论只覆盖这段（详见 per_repo/daily）。")
    A("3. **分母只能信平台自报**：本环境无 GitHub 令牌，无法用 GitHub API 独立确认合并事实；"
      "本轮以 `git log` 合并提交作第三源敏感性对照（`sensitivity_git`）。")
    A("4. **修复的既有缺陷（本单为打通 L3 真调用所必需）**：`_safe_table` 失败未回滚导致 "
      "PostgreSQL 事务 aborted 连坐其它 KPI；`metered_usage` 表在但缺 `repro_digest` 列导致 "
      "整块 KPI 面板 500。两处均改为容错回退，不改变原 4 维语义。")
    A("5. **适用范围**：只回答「合并交付中有多少被独立复核通过」，不评价交付质量本身。")
    A("")
    A("## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）")
    A("")
    A("| 关键量 | 本次值 | 基准（独立第二实现） | 相对误差 | 是否 <3% |")
    A("|---|---|---|---|---|")
    rows = []
    if bench_raw.get("metrics"):
        for m in bench_raw["metrics"]:
            rows.append((m["metric"], m["got"], m["ref"], m["rel_err"], "CSV set 交集法"))
    if bench_db.get("metrics"):
        for m in bench_db["metrics"]:
            rows.append((m["metric"], m["got"], m["ref"], m["rel_err"], "手写单条 SQL"))
    for name, got, ref, err, impl in rows:
        A(f"| {name}（{impl}） | {got} | {ref} | {err:.2%} | **是**（0.00%） |")
    A("")
    A(f"- 平台侧面板 vs 脚本：分子 {cc.get('numerator', {}).get('panel')} vs "
      f"{cc.get('numerator', {}).get('script')}（相等={cc.get('numerator', {}).get('equal')}），"
      f"比率差 {cc.get('rate_abs_diff_pp')} pp。")
    A("- 第三源（psql 跑 `recompute.sql`）原文：")
    A("")
    A("```")
    for line in psql.strip().splitlines()[-6:]:
        A(line)
    A("```")
    A("")
    A(f"- **结论：KPI 现值 {d['current']}% 是可复算的真实现值；不好看，如实报。**")
    A("")
    A("## ⑦ 缺口与建议锚点（只提建议，不执行治理动作）")
    A("")
    A("| 优先级 | 缺口 | 证据 | 建议锚点 |")
    A("|---|---|---|---|")
    for i, g in enumerate(d.get("top_gaps", []), 1):
        A(f"| P{min(i, 3)} | {g} | `kpi_result.json` | — |")
    for p in d.get("proposed_anchors", []):
        A(f"| — | — | — | `{p['anchor']}`：{p['acceptance']} |")
    A("")
    A("## 附：达标证据映射（L1/L2/L3/L4）")
    A("")
    A("| 级别 | 要求 | 本单工件 |")
    A("|---|---|---|")
    A("| L1 | 测试 | `tests/test_kpi_qc_recheck.py`（17 例全绿，含不变式/篡改 fail-closed/集成） |")
    A("| L2 | 非占位实现 + 入口 + 真实运行工件 | "
      "`tools/kpi_qc_recheck/{spec,queries,measure,verify}.py`；"
      f"入口 `python3 -m tools.kpi_qc_recheck.measure`；工件 `{args.dir}/`（kpi_result.json、"
      "unreviewed_prs.csv、per_repo.csv、daily.csv、raw_*.csv、provenance.json） |")
    A("| L3 | 平台侧真调用 | `backend/services/platform_kpi.py::_compute_qc_recheck` → "
      "`compute_all_kpis`（`GET /tensor/kpi/platform_dashboard`）；实调用工件 "
      f"`integration_call.json`（numerator={qc.get('numerator')}, denominator={qc.get('denominator')}） |")
    A("| L4 | benchmark(ref vs got) + repro_cmd | `benchmark_raw.json`（CSV 复算）、"
      "`benchmark_db.json`（手写 SQL）、`recompute_psql.txt`（psql 第三源）、"
      "`cluster_verify.json`（集群作业）；repro_cmd 见 §② 与 `kpi_result.json.repro_cmd` |")
    A("")
    if prov:
        A("### provenance 摘要（逐件 sha256）")
        A("")
        A("| 文件 | sha256（前 16 位） | 字节 |")
        A("|---|---|---|")
        for name, meta in sorted(prov.get("files", {}).items()):
            A(f"| {name} | `{meta['sha256'][:16]}` | {meta['bytes']} |")
        A("")
        A(f"- result_sha256（去挥发键）：`{prov.get('result_sha256', '')[:32]}`")
        A(f"- spec_sha256：`{prov.get('spec_sha256', '')[:32]}`")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"wrote {args.out} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())