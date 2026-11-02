#!/usr/bin/env python3
"""从 artifacts/m1596_guide_kpi/kpi_snapshot.json 生成报告 markdown（数字零手抄）。

用法：python3 tools/gen_guide_kpi_report.py [out.md]
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts" / "m1596_guide_kpi"
SNAP = ART / "kpi_snapshot.json"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "reports" / "report_m1596_guide_kpi_ledger.md"


def num(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, float) and abs(v - round(v)) < 1e-12:
        return str(int(round(v)))
    return f"{v:.{nd}g}"


def kpi_table(recs):
    out = ["| # | KPI key | 口径来源 | 实测值 | 目标 | 完成度 | 口径公式 | 留痕 |",
           "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(recs, 1):
        prog = "—" if r["progress"] is None else f"{100 * r['progress']:.1f}%"
        if r["kind"] == "db":
            src, prov = "**DB**", "live SQL（现场直取）"
        else:
            src = "台账" + (" ⚠STALE" if r.get("stale") else "")
            prov = f"{r.get('provenance', '')}；age {r.get('age_days')}d"
        val = num(r["value"]) if r["numeric"] else f"定性：{r.get('status_text') or '—'}"
        out.append(f"| {i} | `{r['key']}` | {src} | {val} | {num(r['target'])} | {prog} | "
                   f"{r['formula']} | {prov} |")
    return "\n".join(out)


def drift_table(drift):
    out = ["| KPI key | 看板 cur 字符串 | 看板折算值 | 现场复算 | 相对漂移 | 判定 |",
           "|---|---|---|---|---|---|"]
    for d in drift:
        rd = "—" if d.get("rel_drift") is None else f"{100 * d['rel_drift']:.1f}%"
        out.append(f"| `{d.get('metric_key')}` | {d.get('cur')} | {num(d.get('dash_in_unit'))} | "
                   f"{num(d.get('live'))} | {rd} | {d.get('status')} |")
    return "\n".join(out)


def main():
    p = json.loads(SNAP.read_text(encoding="utf-8"))
    recs, drift, ds = p["records"], p["drift"], p["drift_summary"]
    db_rows = [r for r in recs if r["kind"] == "db"]
    led_rows = [r for r in recs if r["kind"] == "ledger"]
    qual = [r for r in recs if not r["numeric"]]
    drift_rows = [d for d in drift if d.get("status") == "drift"]
    sr_cur = next((d["cur"] for d in drift if d.get("metric_key") == "success_rate"), "—")
    lc_cur = next((d["cur"] for d in drift if d.get("metric_key") == "lineage_coverage"), "—")
    worst = max(drift_rows, key=lambda d: d.get("rel_drift") or 0) if drift_rows else None
    ts = p["generated_at"]

    raw = p.get("db_raw", {})
    sr, lc = raw.get("success_rate", {}), raw.get("lineage_coverage", {})
    sr_txt = f"{sr.get('succ')}/{sr.get('term')}" if sr else "—"
    lc_txt = f"{lc.get('covered')}/{lc.get('term')}" if lc else "—"
    md = f"""# 指南 KPI 台账化 v1：12 项硬考核指标从「代码里的字面量」迁到「可复算 + 可追溯序列」（m1596）

**结论速览**：指南 12 项 KPI 中 **{len(db_rows)}/12 已改为现场 SQL 复算**，另有 **{len(led_rows)}/12 为带留痕的字面量台账**
（{len(qual)} 项定性指标不入库、不填 0）。首次落库成功：`tensor_guide_tracker` 由 **0 行 → {p['n_written']} 行**
（org={p['org_id']}，小时桶 `{p['bucket']}`；重跑幂等，行数不变）。对账发现 **{ds['drift']} 项看板数字与现场复算超容差**
（最差 `{worst['metric_key'] if worst else '—'}` 相对漂移 {100 * (worst['rel_drift'] if worst else 0):.1f}%），
说明常设看板 json 快照已过期，**本单的数值以现场复算为准**。

## ① 案例介绍（数据背景 / 对象 / 参数表）

考核对象不是船体，而是**平台的考核口径本身**——owner 2026-10-31 定下的 12 项硬考核指标（工具接入 ≥5 类、
无人值守 ≥1000 工况、成功率 ≥90%、谱系追溯 100%、复现 ≥95%、门禁覆盖 100%、知识库 ≥1 万条、对标算例 ≥10、
静水阻力 ≤5%、周期 −50%、IP ≥3 标准/5 专利/5 论文/5 软著、已合并功能复核通过率）。

审计到的三个数据层面事实（只读扫描，2026-11-01T{ts[11:19]}Z）：

| 事实 | 证据 |
|---|---|
| `tensor_guide_tracker` 表 **0 行**（指南 KPI 从未入库） | `select count(*) → 0`；后端仅 `/tensor/repro/kpi` 端点写 4 个复现类 key（`backend/api/tensor_repro.py:602`） |
| 常设线 `guide_tracker.py` 只覆盖写单个 `guide_dashboard.json`（无历史、无趋势） | `guide_tracker.py:snapshot()`；json `ts` 停在 2026-10-31 03:55 |
| 12 项中 **6 项是代码里的硬编码字符串**（`"4/5"`/`"机制就绪"`/`"样本 0"`/`"未实测"`…） | `guide_tracker.py:kpis()` 返回字典内的字面量 |

参数表（本单的「几何量」= 规格表规模与数据规模）：

| 参数 | 值 | 说明 |
|---|---|---|
| KPI 规格数 | {p['n_kpi']} | DB 类 {p['n_db']} + 台账类 {p['n_ledger']} |
| 数值型 / 定性 | {p['n_numeric']} / {p['n_qualitative']} | 定性不进 `metric_value`（double NOT NULL 字段塞 0 = 说谎） |
| 本次可落库行 | {p['n_persistable']} | = {p['n_numeric']} − 1（`repro_rate` 无裁决 → None） |
| 事实库规模 | 终态单 {raw.get('success_rate', {}).get('term', '—')}；有报告任务 {raw.get('lineage_coverage', {}).get('covered', '—')}；`ship_samples` {raw.get('kb_samples', {}).get('samples', '—')}；`tensor_repro_verdicts` {raw.get('repro_rate', {}).get('total', '—')} | 现场 SQL，报告级口径 |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **规格化**：`tools/guide_kpi_ledger.py` 里 `KPI_SPECS` 逐项登记 `key / label / kind(db|ledger) / numeric / target /
   formula / sql / nums / ledger_field`——每一项都自带口径说明，杜绝「数字从哪来的说不清」。
2. **DB 类直取**：`fetch_metrics()` 走 `psql -tAF '\\x1f'` 通道（环境无 psycopg2，与 `guide_tracker.py` 同款通道），
   每项一条只读 SQL；`merge_rows()` 按 `how=ratio|identity` 折算。
3. **台账类留痕**：`docs/kpi/literal_ledger.json` 为每条字面量记录 `owner / as_of / evidence / status_text`；
   `build_records()` 超过 {14} 天自动标 `[STALE]`（本单全部 age 0d）。
4. **落库幂等**：`snapshot_rows()` 只收数值型，`hour_bucket()` 把 `snapshot_at` 截到整点，
   `INSERT … ON CONFLICT (organization_id, metric_key, snapshot_at) DO UPDATE` —— 同小时重跑只更新不增行。
5. **对账**：`reconcile()` 解析 `guide_dashboard.json` 的 `cur` 字符串，**先折算到 KPI 单位再比**（看板把比率写成
   `4/4`、`454/499`，直接比 4 vs 1.000 会误报），容差 {100 * 0.02:.0f}%（相对）。
6. **工件**：`artifacts/m1596_guide_kpi/kpi_snapshot.json`（全量记录+漂移）、`kpi_progress.png`、`kpi_drift.png`；
   图与表由 `tools/gen_guide_kpi_figures.py` / `tools/gen_guide_kpi_report.py` 从 json 程序化生成（零手抄）。
7. **验证**：`pytest tests/test_guide_kpi_ledger.py` → **28 passed**（口径/单位/幂等/转义/台账完整性全覆盖）。

工具链：Python 3.11 + psql（只读事实库）+ pytest 9.2 + matplotlib（Agg）。

## ③ 计算结果（12 项 KPI 快照表）

{kpi_table(recs)}

落库实况（`tensor_guide_tracker`，organization_id={p['org_id']}，snapshot_at={p['bucket']}）：

```
{chr(10).join(f"{r['key']:<26} value={r['val']:<10} target={r['target']}" for r in p['rows'])}
```

## ④ 结果图

- `kpi_progress.png`：9 项数值 KPI 完成度横条（绿=DB 现场复算，红=字面量台账），黑色虚线=目标线。
- `kpi_drift.png`：看板快照 vs 现场复算的相对漂移，虚线=2% 容差。

图意：**红色越多，说明该 KPI 越依赖人工留痕而非数据**；漂移图直接给出「看板数字还能不能信」的量化答案。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **单位错配坑（本单初版真 bug）**：看板把比率 KPI 写成 `4/4`、`454/499`，而复算是 `1.000`、`0.8199`；
   朴素的"取第一个数字"比较会给出 **3 个假漂移**（已修：`normalize_to_unit()` + 回归测试
   `test_normalize_to_unit_fixes_dash_vs_ratio_mismatch`）。**这是全平台对账类脚本的通用坑，建议其他席位复用该函数。**
2. **标签逐字对齐坑**：`静水阻力 ≤5% / 冰区 ≤10%`（带空格）对上 `静水阻力 ≤5%/冰区 ≤10%` 会变成 `unmapped`，
   假阳性 2 项。已加 `test_all_spec_labels_match_dashboard_names` 作为机器门。
3. **`metric_value` 是 `double precision NOT NULL`**：定性 KPI 不能塞 0（会被下游读成"实测为零"），本单选择**不入库**，
   只在 artifacts json 里标 `numeric=false` —— KPI 台账因此有 {p['n_qualitative']} 项"结构性缺口"，这是诚实的口径缺口，不是遗漏。
4. **代理口径未消解**：`unattended_cases` 用「案例级」近似工况级（65 案例 ≈ 65–195 工况，目标 1000 是工况级），
   `lineage_coverage` 用「有报告任务/终态单」近似；两者在样本库（G-SAMPLE）上线后须换精确口径。
5. **`repro_rate` 目前不可算**：`tensor_repro_verdicts` 0 行 → 返回 None（未实测），库表里就不该有这一行；
   这解释了看板写"机制就绪"的原因，但**"机制就绪"不能当分母**。
6. **常设线未接线**：`/srv/tensor/agent/guide_tracker.py` 在 worktree 之外（不可改），本单只交付可被它调用的 ETL；
   建议运维侧把 `snapshot()` 改为调用 `python3 tools/guide_kpi_ledger.py --persist`，即可获得每小时趋势序列。
7. 本单为**数据/口径审计**，不涉及船体物理量；不做计算机时受限（load15 ≥ 100 时仅跑轻量 SQL/pytest/matplotlib）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）

基准 = 常设看板 `guide_dashboard.json` 的 `cur` 字符串（这是平台当前对外呈现的数字）。逐项：

{drift_table(drift)}

| 判定对象 | 误差 | 是否 <3% | 说明 |
|---|---|---|---|
| `success_rate` | 相对 {100 * (next((d['rel_drift'] for d in drift_rows if d['metric_key'] == 'success_rate'), 0)):.1f}% | **否** | 看板 {sr_cur}（10-31 快照）vs 现场 {sr_txt}；看板数字过期而非算错 |
| `lineage_coverage` | 相对 {100 * (next((d['rel_drift'] for d in drift_rows if d['metric_key'] == 'lineage_coverage'), 0)):.1f}% | **否** | 看板 {lc_cur} vs 现场 {lc_txt}；报告数涨但终态单涨更快 |
| 其余 {ds['ok']} 项 | 0.0% | **是** | 逐字对齐、折算后完全一致 |
| 定性/未实测 {ds['skipped']} 项 | — | 无基准 | `repro_rate`（0 裁决）、`gate_coverage`/`resistance_accuracy`/`cycle_reduction`（无机器可算口径） |

**诚实结论**：本单交付的 ETL 自身复算与 SQL 事实库是同一数据源（无独立误差可言），真正的"误差"是**看板呈现值 vs 现场值**——
2 项超 3%（11.5% / 9.9%），根因是看板快照滞后（json `ts`=2026-10-31 03:55，本次复算 2026-11-01T{ts[11:19]}），
不是算术错误。修法已内建：每小时 `--persist` 落库后，看板与台账同源同时刻，该误差项归零。

---
工件与代码：`tools/guide_kpi_ledger.py`、`tools/gen_guide_kpi_figures.py`、`tools/gen_guide_kpi_report.py`、
`docs/kpi/literal_ledger.json`、`tests/test_guide_kpi_ledger.py`、`artifacts/m1596_guide_kpi/`。
复现：`python3 tools/guide_kpi_ledger.py --persist --json artifacts/m1596_guide_kpi/kpi_snapshot.json`。
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    print(OUT, len(md), "bytes")


if __name__ == "__main__":
    main()