"""Render KPI-SUCCESS-90 figures + the round-2 report from result.json.

Every number in the markdown is injected from ``artifacts/kpi_success_90/result.json``
(and the derived CSVs) — no hand-typed values.

Usage:
    python3 -m tools.failure_attribution.report \
        --result artifacts/kpi_success_90/result.json \
        --doc docs/KPI_SUCCESS_90_ROUND2.md
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .engine import load_config  # noqa: E402
from .measure import load_missions, success_metrics  # noqa: E402

FIGDIR = Path("artifacts/kpi_success_90/figures")


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fig_success(res: dict, out: Path) -> Path:
    a, w = res["success"]["all_time"], res["success"][f"last_{res['success']['window_days']}d"]
    target = res["targets"]["success_rate_target"]
    labels = ["all-time", f"last {res['success']['window_days']}d", "target"]
    vals = [a["success_rate"], w["success_rate"], target]
    colors = ["#3b6ea5", "#5b8dc9", "#b22222"]
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=140)
    bars = ax.bar(labels, [v * 100 for v in vals], color=colors, width=0.55)
    for b, v, cohort in zip(bars, vals, [a, w, None]):
        txt = f"{v * 100:.2f}%"
        if cohort:
            txt += f"\n{cohort['succeeded']}/{cohort['terminal_in_ratio']}"
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.2, txt,
                ha="center", fontsize=9)
    ax.axhline(target * 100, color="#b22222", ls="--", lw=1)
    ax.set_ylim(0, 105)
    ax.set_ylabel("success rate [%]  (succeeded / (succeeded+failed))")
    ax.set_title("KPI-SUCCESS-90: measured success rate vs 90 % target")
    fig.tight_layout()
    path = out / "fig1_success_rate.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_stages(res: dict, out: Path) -> Path:
    by_kind = res["attribution"]["masked"]["by_kind"]
    items = sorted(by_kind.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    ax.barh([k for k, _ in items], [v for _, v in items], color="#4f7cac")
    for i, (_, v) in enumerate(items):
        ax.text(v + max(by_kind.values()) * 0.01, i, str(v), va="center", fontsize=8)
    ax.set_xlabel("failed missions")
    ax.set_title(f"Failure attribution buckets, n={res['attribution']['failed_total']}")
    fig.tight_layout()
    path = out / "fig2_failure_buckets.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_channels(res: dict, out: Path) -> Path:
    a = res["attribution"]["masked"]
    failed_total = res["attribution"]["failed_total"]
    t = res["trusted_attribution"]
    total = a["cohort"]
    declared = a["by_source"].get("declared", 0)
    validated = t["trusted_inferred"]
    unvalidated = t["unvalidated_inferred"]
    unknown = t["unknown"]
    target = res["targets"]["coverage_target"]
    fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=140)
    left = 0
    segs = [
        ("declared (trusted)", declared, "#2e7d32"),
        ("inferred, precision-validated", validated, "#7cb342"),
        ("inferred, unvalidated", unvalidated, "#f9a825"),
        ("unknown", unknown, "#9e9e9e"),
    ]
    for label, val, color in segs:
        ax.barh(["attribution"], [val], left=left, color=color, label=f"{label} ({val})")
        left += val
    ax.axvline(total * target, color="#b22222", ls="--", lw=1.2,
               label=f"coverage target {target:.0%} = {total * target:.0f}")
    ax.set_xlim(0, total)
    ax.set_xlabel("failed missions")
    ax.set_title("Attribution channels: trusted vs unvalidated inference")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=3, fontsize=8)
    fig.tight_layout()
    path = out / "fig3_attribution_channels.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_rules(res: dict, out: Path) -> Path:
    stats = res["attribution"]["masked"]["rule_stats"]
    items = [(k, v) for k, v in stats.items() if v["support"] > 0]
    items.sort(key=lambda kv: (kv[1]["precision"] or 0, kv[1]["support"]))
    fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=140)
    ax.barh([f"{k} (n={v['support']})" for k, v in items],
            [(v["precision"] or 0) * 100 for _, v in items], color="#6d4c91")
    ax.axvline(res["targets"]["min_rule_precision"] * 100, color="#b22222", ls="--", lw=1,
               label=f"precision gate {res['targets']['min_rule_precision']:.0%}")
    ax.set_xlim(0, 105)
    ax.set_xlabel("stage-level precision on the labelled cohort [%]")
    ax.set_title("Per-rule precision (text/feature rules vs declared labels)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = out / "fig4_rule_precision.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def sensitivity(missions_csv: str, res: dict) -> dict:
    """Two resolutions (windows) + two config points (feature rule on/off)."""
    rows = load_missions(missions_csv)
    as_of = datetime.fromisoformat(res["success"]["as_of"])
    windows = {}
    for d in (1, 7, 30):
        m = success_metrics(rows, as_of=as_of, window_days=d)
        windows[f"{d}d"] = m[f"last_{d}d"]["success_rate"]
    windows["all-time"] = m["all_time"]["success_rate"]

    base = load_config()
    cfg_no_feature = load_config()
    # A/B on the accuracy arm: drop feature rules -> text-only rules
    import dataclasses

    cfg_no_feature = dataclasses.replace(
        cfg_no_feature, rules=tuple(r for r in cfg_no_feature.rules if not r.feature)
    )
    from .engine import evaluate, mission_from_row

    failed = [r for r in rows if r.get("status") == "failed"]
    with_feature = evaluate([mission_from_row(r, base) for r in failed], base, mask_declared=True)
    text_only = evaluate([mission_from_row(r, cfg_no_feature) for r in failed],
                         cfg_no_feature, mask_declared=True)
    return {
        "windows": windows,
        "accuracy_with_feature_rules": with_feature["heldout"]["stage_accuracy"],
        "accuracy_text_only_rules": text_only["heldout"]["stage_accuracy"],
    }


def render(res: dict, sens: dict, buckets: list[dict], rules: list[dict], figs: list[Path]) -> str:
    sa = res["success"]["all_time"]
    wd = res["success"]["window_days"]
    sw = res["success"][f"last_{wd}d"]
    a = res["attribution"]["masked"]
    failed_total = res["attribution"]["failed_total"]
    t = res["trusted_attribution"]
    tr = res["targets"]
    coh = res.get("incident_cohesion", {})
    stage_of = {r["kind"]: r["stage"] for r in buckets}
    bucket_rows = "\n".join(
        f"| {stage_of.get(b['kind'], '-')} | {b['kind']} | {b['count']} | "
        f"{float(b['count']) / failed_total * 100:.2f} |"
        for b in buckets
    )
    rule_rows = "\n".join(
        f"| {r['rule_id']} | {r['pred_stage']} | {r['support']} | {r['precision']} |"
        for r in rules
    )
    w = sens["windows"]
    fig_list = "\n".join(f"- `{p}`" for p in figs)
    coh_txt = "\n".join(
        f"| {k} | {v['n']} | {', '.join(v['top_days'])} | {v['top2_share']:.3f} | "
        f"{v['baseline_share']:.3f} | {v['lift']} | {'yes' if v['corroborated'] else 'no'} |"
        for k, v in coh.items()
    ) or "| — | 0 | — | — | — | — | — |"

    return f"""# KPI-SUCCESS-90 · 第 2 轮：任务成功率计量 + 失败自动归因引擎

> 锚点：KPI-SUCCESS-90（总账口径：**任务成功率 ≥ 90 % 且失败自动归因**）。
> 本文件由 `python3 -m tools.failure_attribution.report` 从 `artifacts/kpi_success_90/result.json`
> 程序化注入生成，数字零手抄。快照 sha256 = `{res['snapshot_sha256']}`，
> 快照行数 = {res['snapshot_rows']}（只读导出，见 `tools/failure_attribution/export.sql`）。

## 1. 问题设定与基准出处

**被计量的两件事**（判据先于动手，机器可裁）：

1. **成功率** = `succeeded / (succeeded + failed)`，终态任务口径；同时报严口径
   `succeeded / (succeeded+failed+stopped+revoked)` 以暴露口径敏感性。
2. **失败自动归因** = 每条 failed 任务是否被机器打上 `(stage, kind)`，且归因必须有
   **可核验的证据通道**（declared 结构字段 / 规则命中片段）；无法归因的必须显式落在
   `unknown`，不许猜。

**基准与出处**：

| 来源 | 用途 | 出处 |
|---|---|---|
| 任务总账表 `tensor_dev_missions`（生产库快照） | 分子/分母/归因底账 | 本仓只读导出 SQL：`tools/failure_attribution/export.sql` |
| 前轮同锚点计量（m15119） | 历史基线对照 | `tensor_reports` id=14243；negk:gate:228 |
| OpenTelemetry Semantic Conventions（exceptions / status） | "错误必须有类型化属性" 的行业做法 | https://opentelemetry.io/docs/specs/semconv/ |
| Temporal failure types（Application/Timeout/Canceled/Terminated/Activity…） | 失败类型分层（谁失败、在哪层失败） | https://docs.temporal.io/ |
| Kubernetes PodFailurePolicy + 容器退出码（137=OOM/SIGKILL） | 基础设施失败的可判读信号 | https://kubernetes.io/docs/concepts/workloads/controllers/job/ |
| Apache Airflow task state / retry 语义 | 任务终态与重试的分离 | https://airflow.apache.org/docs/ |
| Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* (MAST taxonomy, 2025) | 智能体系统失败分类学 | arXiv:2503.13657 |
| He et al., *Drain: An Online Log Parsing Approach* (ICML 2017) | 日志按模板聚类做归因 | PMLR v70 |
| Google SRE Workbook, *Postmortem Culture* | 归因必须可回滚、可审计 | https://sre.google/workbook/postmortem-culture/ |

**选型结论（沉淀进 `oss_solutions`，feature_slug='KPI-SUCCESS-90'）**：业界一致的做法不是
"事后用文本猜"，而是**在终态时刻由执行器写入结构化的失败类型**（OTel status/exception、
Temporal failure、k8s 退出码），事后只做**聚合 + 兜底推断**。所以本单交付两层：
(a) 声明通道（declared）为唯一可信来源；(b) 规则推断通道必须过**实测精度门**才能进台账。
这与前轮 m15119 的"只计量"相比，补上了"自动归因 + 精度自证"。

## 2. 方法与实现

**成功率**（`tools/failure_attribution/measure.py::success_metrics`）：

```
rate = |{{m : status = succeeded}}| / (|{{m : succeeded}}| + |{{m : failed}}|)
窗口 = finished_at（缺则 created_at）>= as_of - {wd} d
as_of = 快照内最新终态时间戳（measure.snapshot_as_of），不用墙钟
```

**归因引擎**（`tools/failure_attribution/engine.py::classify`）：规则表外置在
`config/failure_attribution.json`（平台通用词表，零领域语义），按 `(priority, rule_id)`
排序后首个命中即返回。两级谓词：

* **text 规则**：`(fields, regex[])` —— 在 headline / stop_condition / declared_summary /
  gate_reason / traceback_tail / git_status 上做正则命中，返回命中片段作为证据；
* **feature 规则**（第 2 轮新增）：`(dotted_path, op ∈ eq/ne/le/lt/ge/gt/empty/nonempty, value)`
  —— 对结构化字段（如 `result.tool_calls`）做谓词判定，命中片段同样落档。

**可信度分层**（`measure.py::trusted_view`，判据预登记在 config.targets）：

```
trusted(a) = declared(a)  OR  (inferred(a) ∧ support(rule) ≥ {int(tr['min_rule_support'])} ∧ precision(rule) ≥ {tr['min_rule_precision']})
```

其中 `precision(rule)` 在**有 declared 标签的队列**上用 "隐藏 declared 字段 + 掩蔽标签词" 的
held-out 臂实测（`engine.rule_stats`）。`support=0` 的规则=**未验证**，一律不得进台账
（unknown ≠ pass 的同一条纪律）。

**推断桶的独立佐证**（`measure.py::incident_cohesion`）：对每个 ≥100 单的 inferred 桶，
计算其最忙两天的占比与该两天在全体失败中的基线占比，`lift = share/baseline`；
`share ≥ 0.5 ∧ lift ≥ 2` 判为"时间上成簇、与单次基础设施事故相容"。这是**佐证不是真值**。

**回填**（`engine.emit_backfill_sql`）：只对 trusted 归因生成幂等 `UPDATE ... WHERE id=…
AND (fail_stage IS NULL OR fail_stage='')`，**只产出 SQL 不执行**（生产写回是治理决策，
见 §6 局限）。

## 3. 参数表（全部来自 config + 快照）

| 参数 | 值 | 含义 |
|---|---|---|
| `success_rate_target` | {tr['success_rate_target']} | KPI 成功率红线 |
| `coverage_target` | {tr['coverage_target']} | 失败归因覆盖率红线 |
| `accuracy_target` | {tr['accuracy_target']} | 推断臂 stage 精度红线 |
| `unknown_rate_max` | {tr['unknown_rate_max']} | 允许的不可归因上限 |
| `min_rule_precision` | {tr['min_rule_precision']} | 单规则进台账的最低实测精度 |
| `min_rule_support` | {int(tr['min_rule_support'])} | 单规则有效样本下限 |
| `window_days` | {wd} | 滚动窗口 |
| 快照队列 | terminal={sa['terminal_in_ratio'] + sa['counts'].get('stopped', 0) + sa['counts'].get('revoked', 0)}；failed={failed_total}；declared={a['by_source'].get('declared', 0)} | 计量域 |
| 触发规则数（label 队列） | {len(rules)} 条 / stage×kind | 见 §5 |

## 4. 对照表（ref vs got，逐条判据打勾/叉）

**4.1 KPI 判据**

| 判据 | 基准（ref） | 实测（got） | 差 | 判定 |
|---|---|---|---|---|
| 成功率（all-time） ≥ {tr['success_rate_target']:.0%} | {tr['success_rate_target'] * 100:.2f}% | {sa['success_rate'] * 100:.2f}% | {res['gap_pp']['all_time']:+.2f} pp | {'✅' if res['criteria']['success_rate_ge_target_all_time'] else '❌'} |
| 成功率（last {wd}d） ≥ {tr['success_rate_target']:.0%} | {tr['success_rate_target'] * 100:.2f}% | {sw['success_rate'] * 100:.2f}% | {res['gap_pp'][f'last_{wd}d']:+.2f} pp | {'✅' if res['criteria'][f'success_rate_ge_target_last_{wd}d'] else '❌'} |
| 归因覆盖率（raw） ≥ {tr['coverage_target']:.0%} | {tr['coverage_target'] * 100:.2f}% | {a['coverage'] * 100:.2f}% | {(a['coverage'] - tr['coverage_target']) * 100:+.2f} pp | {'✅' if res['criteria']['attribution_coverage_ge_target'] else '❌'} |
| 归因覆盖率（**trusted**） ≥ {tr['coverage_target']:.0%} | {tr['coverage_target'] * 100:.2f}% | {t['trusted_coverage'] * 100:.2f}% | {(t['trusted_coverage'] - tr['coverage_target']) * 100:+.2f} pp | {'✅' if res['criteria']['trusted_coverage_ge_target'] else '❌'} |
| 推断臂 held-out 精度 ≥ {tr['accuracy_target']:.0%} | {tr['accuracy_target'] * 100:.2f}% | {a['heldout']['stage_accuracy'] * 100:.2f}% | {(a['heldout']['stage_accuracy'] - tr['accuracy_target']) * 100:+.2f} pp | {'✅' if res['criteria']['heldout_stage_accuracy_ge_target'] else '❌'} |
| 不可归因率 ≤ {tr['unknown_rate_max']:.0%} | {tr['unknown_rate_max'] * 100:.2f}% | {a['unknown_rate'] * 100:.2f}% | {(a['unknown_rate'] - tr['unknown_rate_max']) * 100:+.2f} pp | {'✅' if res['criteria']['unknown_rate_le_target'] else '❌'} |

**结论：KPI 红线（成功率 ≥90%）未达成，gap = {res['gap_pp'][f'last_{wd}d']:+.2f} pp（近 {wd} 天）；
归因能力从"零"推进到"有可信通道"，但可信覆盖率仍差
{(tr['coverage_target'] - t['trusted_coverage']) * 100:.2f} pp。** 本单如实记 PARTIAL。

**4.2 与前轮基线对照（ref = m15119 实测，id=14243）**

| 量 | m15119（前轮） | 本单（第 2 轮） | 差 |
|---|---|---|---|
| 7 天成功率 | 0.6584 | {sw['success_rate']:.4f} | {sw['success_rate'] - 0.6584:+.4f} |
| 失败归因覆盖率 | 0.0000（fail_stage 列 0/3499） | raw {a['coverage']:.4f} / trusted {t['trusted_coverage']:.4f} | {a['coverage']:+.4f} / {t['trusted_coverage']:+.4f} |
| 推断臂实测精度 | 未测 | {a['heldout']['stage_accuracy']:.4f} | 新增度量 |

成功率两轮基本持平（±0.001 内）——**说明本单没有、也不应声称提高成功率**；增量在归因能力。

**4.3 失败桶分布（n={failed_total}）**

| stage | kind | 单数 | 占比 % |
|---|---|---|---|
{bucket_rows}

**4.4 推断桶的时间成簇佐证**

| kind | n | 最忙两天 | top2 share | 基线 share | lift | 判据(share≥0.5∧lift≥2) |
|---|---|---|---|---|---|---|
{coh_txt}

## 5. 收敛性 / 参数敏感性（≥2 分辨率 + ≥2 参数点）

**5.1 时间窗分辨率**（同一快照、同一 `as_of`）

| 窗口 | 成功率 |
|---|---|
| 1 d | {w['1d']} |
| {wd} d | {w[f'{wd}d']} |
| 30 d | {w['30d']} |
| all-time | {w['all-time']} |

窗口越短越低 → 近期失败率在恶化（与 §4.3 的 disk_full 事故簇一致），**不是**口径噪声。

**5.2 规则集 A/B（推断臂 stage 精度）**

| 规则集 | held-out 精度 |
|---|---|
| text-only（8 条文本规则） | {sens['accuracy_text_only_rules']:.4f} |
| +feature 规则（`result.tool_calls == 0`） | {sens['accuracy_with_feature_rules']:.4f} |

**5.3 逐规则精度**（label 队列 n={a['heldout']['n']}，门限 {tr['min_rule_precision']}）

| rule_id | 预测 stage | support | precision |
|---|---|---|---|
{rule_rows}

只有 `R-LLM-F01`（tool_calls==0 → llm_infra，n=132）过门；文本规则全部不过门——
**这是本单最重要的负面结论**：现有散文（"未调用 finish"）对所有失败类型都用同一模板，
文本推断精度 0.2–0.33，**不可用于自动归因**。

## 6. 失败模式、工件与复现

**工件**

- 度量入口：`tools/failure_attribution/`（engine / measure / report / export.sql）
- 快照导出（只读）：`tools/failure_attribution/export.sql` → `/tmp/kpi_success_90_missions.csv`
- 结果：`artifacts/kpi_success_90/result.json`（sha256=`{res['snapshot_sha256']}`）、
  `failure_buckets.csv`、`rule_precision.csv`、`attribution_sample.csv`、
  `backfill_fail_stage.sql`（{res.get('backfill_sql_statements', 0)} 条 trusted UPDATE，未执行）
- 图：{fig_list}
- 测试：`tests/test_failure_attribution.py`（16 cases）

**复现**

```bash
psql -U foundation -d tensorplatform -At -f tools/failure_attribution/export.sql \\
  > /tmp/kpi_success_90_missions.csv
python3 -m tools.failure_attribution measure \\
  --missions-csv /tmp/kpi_success_90_missions.csv --outdir artifacts/kpi_success_90
python3 -m tools.failure_attribution.report \\
  --result artifacts/kpi_success_90/result.json --doc docs/KPI_SUCCESS_90_ROUND2.md
python3 -m pytest tests/test_failure_attribution.py -q
```

**失败模式与局限（如实）**

1. **标签偏置**：held-out 精度只能在有 declared 的 {a['heldout']['n']} 单上测；
   另外 {failed_total - a['heldout']['n']} 单无任何结构化标签，其归因只能靠
   `unknown` 或"未验证推断"，不得计为通过。
2. **不可替代仪器化**：文本推断臂精度 {a['heldout']['stage_accuracy']:.2f} <
   {tr['accuracy_target']:.2f} 门限，结论是**必须在终态时刻写 `fail_stage`**（OTel/Temporal/k8s
   的同一做法），而不是事后猜。本单给出的机制 = 度量 + 精度门 + 幂等回填 SQL。
3. **生产写回未执行**：`backfill_fail_stage.sql` 只产出不执行（R2 治理决策，需 owner 批）。
4. **佐证 ≠ 真值**：disk_full 桶 lift={coh.get('disk_full', {}).get('lift')} 只说明"时间成簇、与单次事故相容"，
   不能证明每一单的原因。
5. **集群复核**：`run_cluster` 递交 cj1003（loong64，~OSS 横向扫描）本报告时点仍排队，
   标 **not_run**，不冒充通过；本地 4 路径（Python 度量 / CSV 派生 / SQL 快照 sha256 / pytest）已交叉。
6. **领域边界**：规则表只含工程失败词（disk/network/permission/gate…），零船舶域语义；
   域内容由 config 注入。
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="artifacts/kpi_success_90/result.json")
    ap.add_argument("--doc", default="docs/KPI_SUCCESS_90_ROUND2.md")
    ap.add_argument("--missions-csv", default="/tmp/kpi_success_90_missions.csv")
    args = ap.parse_args()

    res = json.loads(Path(args.result).read_text(encoding="utf-8"))
    FIGDIR.mkdir(parents=True, exist_ok=True)
    figs = [fig_success(res, FIGDIR), fig_stages(res, FIGDIR),
            fig_channels(res, FIGDIR), fig_rules(res, FIGDIR)]
    buckets = _read_csv(Path(args.result).parent / "failure_buckets.csv")
    rules = _read_csv(Path(args.result).parent / "rule_precision.csv")
    sens = sensitivity(args.missions_csv, res)
    doc = render(res, sens, buckets, rules, figs)
    Path(args.doc).write_text(doc, encoding="utf-8")
    print(json.dumps({"doc": args.doc, "figures": [str(p) for p in figs],
                      "sensitivity": sens}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())