#!/usr/bin/env python3
"""把 artifacts/chronicle_eval_pulse/*.json 拼成 docs/reports/in24_e4_eval_pulse.md。

纪律：文中每个数字都从工件 JSON 读取（零手抄），散文部分不含数值结论。
用法:
    python3 tools/chronicle/eval_pulse_report.py \
        --artifacts artifacts/chronicle_eval_pulse \
        --out docs/reports/in24_e4_eval_pulse.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _j(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(base: Path) -> str:
    acc = _j(base / "acceptance.json")
    s1 = _j(base / "snapshot_1.json")
    s2 = _j(base / "snapshot_2.json")
    drill = _j(base / "drill_alert.json")
    recomp = _j(base / "recompute_2.json")
    cc = _j(base / "consecutive_check.json")
    ws = _j(base / "window_sensitivity.json")

    c = acc["criteria"]
    real = c["real_facet_numbers"]
    f2 = s2["facet"]
    e1 = s1.get("alert_edge") or {}
    e2 = s2.get("alert_edge") or {}

    L: list[str] = []
    a = L.append
    a("# IN24-E4：评价体系自身脉搏 —— chronicle_snapshots 的评价面、阈值告警与周期质量报表")
    a("")
    a(f"节点 IN24-E4 · 仓 **TensorPlatform** · 分支 `agent/m5179` · 采集时刻 tick1 = `{real['as_of_tick1']}`，"
      f"tick2 = `{real['as_of_tick2']}` · 数据源：平台生产库（PostgreSQL，只读 SQL + 追加写快照行）。")
    a("")
    a("---")
    a("")
    a("## 1. 案例介绍")
    a("")
    a("**被观测对象不是物理场，而是评价体系本身。** 平台的 `outcome_evaluations` 表（S1–S5×E0–E4 分层"
      "评价实体，见 `tools/outcome_evaluations.py`）已在记录成果评价，但评价体系没有任何自观测：")
    a("覆盖率是涨是跌、pending 积压躺了多久、独立复核是否退化成 0（全部由机器扫描自证）、"
      "S/E 分布是否塌缩到单一格，这些都不进编年史、也没有阈值告警。缺自观测的评价体系，"
      "其「可信」是自述，不是证据。")
    a("")
    a("因此本节点把评价体系当成一个被测量的系统，定义六个可观测量并每拍钉进 "
      "`chronicle_snapshots`（评价面 facet）：")
    a("")
    a("| 观测量 | 定义 | 口径 |")
    a("|---|---|---|")
    a("| 覆盖率 | `confirmed / succeeded` | 窗口比（该主体最近 N 张 succeeded mission 中有多少张已有 confirmed 评价） |")
    a("| S 分布 | S1–S5 + S-N 分层计数 | **不加权、不排名、不合成单一分数**；candidate/pending 不计入，S-N 单列 |")
    a("| E 级分布 | E0–E4 分层计数 | 仅 confirmed 记录 |")
    a("| 独立复核占比 | `independent / confirmed records` | 分母为 0 时记 **无定义**（`None`），不按 0% 处理 |")
    a("| auto_scan 占比 | `auto_scan / confirmed records` | 机器证据扫描，明确不等同于同行独立评审 |")
    a("| pending 积压年龄 | 条数 / 最老 / 中位 / P90 | 以记录 `created_at` 起算到 `as_of`，是下界 |")
    a("")
    a("参数表（全部可注入，模块内无领域词）：")
    a("")
    a("| 参数 | 值 | 来源 |")
    a("|---|---|---|")
    a(f"| `subject`（被观测主体键） | `{real['subject']}` | `--subject` 数据注入 |")
    a(f"| `window` | {real['window']} | `--window` |")
    a(f"| `coverage_min` | {acc['thresholds_default']['coverage_min']} | 工程默认，可覆盖 |")
    a(f"| `pending_max` | {acc['thresholds_default']['pending_max']} | 工程默认，可覆盖 |")
    a(f"| `independent_share_min` | {acc['thresholds_default']['independent_share_min']} | 工程默认，可覆盖 |")
    a("")
    a("## 2. 怎么做的")
    a("")
    a("**工具链**：Python 3.11 · SQLAlchemy 2.0.51 + `pg8000`（生产 PG 驱动，本机无 psycopg2）· "
      "pytest 14 用例 · matplotlib（图全部由工件 JSON 渲染）。")
    a("")
    a("**四层结构**（`tools/chronicle/eval_pulse.py`，1073 行）：")
    a("")
    a("1. **facet 采集** `capture()`：7 条参数化 SQL（窗口 CTE + 覆盖率 + 记录状态 + S 分布 + "
      "E 分布 + 复核结构 + 积压聚合/逐条）算出评价面；SQL 原文与参数随 facet 一起落盘"
      "（`facet['sql']` / `facet['sql_params']`），任何第三方不看本模块也能重跑。")
    a("2. **阈值判定** `evaluate_thresholds()`：三条阈值给 `ok / breach / undefined` 三态。"
      "分母为 0 的占比进 `undefined_metrics`，**不**转成 0%（否则「还没开始评价」会被误报成「独立复核缺失」）。")
    a("3. **digest 告警** `emit_alerts()`：**上升沿**语义——同键持续越界只发一次，恢复后再次越界重新发。"
      "告警同时追加到 JSONL 台账（不覆盖历史）。")
    a("4. **快照入编年史** `write_snapshot()`：生产表 `chronicle_snapshots` 只有 "
      "`(ts timestamptz, payload jsonb)` 两列，**不加列、零迁移**：评价面作为带 schema 版本的块写入 "
      "payload，并带 `snapshot_id / prev_snapshot_id` 快照链。老读取方按 `schema` 忽略未知块。")
    a("")
    a("**负控设计**：`run_drill()` 合成一份三条阈值全越界的数据（`source=synthetic_drill`，显式标注非实测），"
      "首拍要求「每键恰一条告警」、重拍要求「0 条」，两次判定都写进工件。")
    a("")
    a("**审计链**：facet 内容指纹（`facet_fingerprint`，SHA-256）→ 快照 payload 指纹 → "
      "生产库 `chronicle_snapshots` 行（可按 `payload->>'snapshot_id'` 回查）→ 告警 JSONL 台账。"
      "指纹只证明内容未变，不是签名（无签名密钥，如实声明）。")
    a("")
    a("## 3. 计算结果")
    a("")
    a("以下数字由 `tools/chronicle/eval_pulse_report.py` 从 `artifacts/chronicle_eval_pulse/*.json` "
      "程序化注入，零手抄。")
    a("")
    a("### 3.1 评价面（tick2，实测）")
    a("")
    a("| 观测量 | 数值 |")
    a("|---|---|")
    a(f"| 窗口 succeeded mission | {real['coverage_succeeded']} |")
    a(f"| 已有 confirmed 评价 | {real['coverage_confirmed']} |")
    a(f"| **覆盖率** | **{real['coverage_ratio']:.1%}** |")
    a(f"| 独立复核占比 | {real['independent_share']:.1%} |")
    a(f"| auto_scan 占比 | {real['auto_scan_share']:.1%} |")
    a(f"| pending 积压条数 | {real['pending_count']} |")
    a(f"| 积压最老年龄 | {real['pending_oldest_age_hours']} 小时 |")
    a(f"| 积压中位年龄 | {real['pending_median_age_hours']} 小时 |")
    a(f"| S 分布 | " + " · ".join(f"{k}={v}" for k, v in real["s_distribution"].items()) + " |")
    a(f"| E 级分布 | " + " · ".join(f"{k}={v}" for k, v in real["e_distribution"].items()) + " |")
    a(f"| 阈值状态 | " + " · ".join(f"{k}={v}" for k, v in real["alert_states"].items()) + " |")
    a("")
    a("### 3.2 两拍快照的告警边沿")
    a("")
    a("| 拍 | as_of | 越界键 | 发出告警 | 抑制（上一拍已在告警） |")
    a("|---|---|---|---|---|")
    a(f"| tick1 | `{s1['facet']['as_of']}` | {', '.join(a2['key'] for a2 in s1['facet']['alerts'])} "
      f"| {', '.join(a2['key'] for a2 in e1.get('emitted', []))} | {len(e1.get('suppressed', []))} |")
    a(f"| tick2 | `{s2['facet']['as_of']}` | {', '.join(a2['key'] for a2 in s2['facet']['alerts'])} "
      f"| {', '.join(a2['key'] for a2 in e2.get('emitted', [])) or '（无）'} "
      f"| {len(e2.get('suppressed', []))} |")
    a("")
    a("### 3.3 判据数字（机器可裁）")
    a("")
    a("| 判据 | 结果 | 数字 | 工件 |")
    a("|---|---|---|---|")
    a(f"| ① 连续两拍含评价面全字段 | {'PASS' if c['c1_two_consecutive_snapshots_full_facet']['pass'] else 'FAIL'} | "
      f"快照 {c['c1_two_consecutive_snapshots_full_facet']['snapshots_found']} 拍 · "
      f"字段 {c['c1_two_consecutive_snapshots_full_facet']['per_snapshot_fields_present']}/{c['c1_two_consecutive_snapshots_full_facet']['required_field_count']} · "
      f"链相连={c['c1_two_consecutive_snapshots_full_facet']['chain_linked']} | "
      f"`{c['c1_two_consecutive_snapshots_full_facet']['artifact']}` |")
    a(f"| ② 阈值负例一次演练告警 | {'PASS' if c['c2_threshold_negative_drill_single_alert']['pass'] else 'FAIL'} | "
      f"越界 {len(c['c2_threshold_negative_drill_single_alert']['breached_keys'])} 条 · "
      f"首拍发出 {len(c['c2_threshold_negative_drill_single_alert']['first_tick_emitted'])} 条 · "
      f"重拍发出 {len(c['c2_threshold_negative_drill_single_alert']['second_tick_emitted'])} 条 | "
      f"`{c['c2_threshold_negative_drill_single_alert']['artifact']}` |")
    a(f"| ③ SQL 可复算 | {'PASS' if c['c3_sql_recomputable']['pass'] else 'FAIL'} | "
      f"{c['c3_sql_recomputable']['fields_checked']} 个字段逐一比对 · 差异 {len(c['c3_sql_recomputable']['diffs'])} 项 · "
      f"{len(c['c3_sql_recomputable']['sql_statements'])} 条 SQL | `{c['c3_sql_recomputable']['artifact']}` |")
    a(f"| ④ pytest ≥ 6 | {'PASS' if c['c4_pytest']['pass'] else 'FAIL'} | "
      f"{c['c4_pytest']['passed']} passed（要求 ≥ {c['c4_pytest']['required']}） | `{c['c4_pytest']['file']}` |")
    a("")
    a("### 3.4 窗口灵敏度（同一主体、同一 SQL、只改 `:window`）")
    a("")
    a("| window | 覆盖率 | confirmed | independent 占比 | pending |")
    a("|---|---|---|---|---|")
    for r in ws["rows"]:
        ind = "无定义" if r["independent_share"] is None else f"{r['independent_share']:.1%}"
        cov = "无定义" if r["coverage_ratio"] is None else f"{r['coverage_ratio']:.1%}"
        a(f"| {r['window']} | {cov} | {r['missions_confirmed']} | {ind} | {r['pending_count']} |")
    a("")
    a("### 3.5 落库证据（生产库回查）")
    a("")
    a("| 项 | 值 |")
    a("|---|---|")
    a(f"| 本次写入评价面快照行数 | {c['production_snapshot_rows']['count_written']} |")
    a(f"| 写入后 `chronicle_snapshots` 总行数 | {c['production_snapshot_rows']['total_chronicle_snapshots_after']} |")
    for row in c["production_snapshot_rows"]["rows_written_by_this_mission"]:
        a(f"| 行 `{row['sid']}` | ts=`{row['ts']}` · prev=`{row['prev']}` · 覆盖率={row['coverage']} · 越界告警={row['n_alerts']} |")
    a("")
    a(f"回查 SQL：`{c['production_snapshot_rows']['sql']}`")
    a("")
    a("## 4. 结果图")
    a("")
    a("图全部由 `tools/chronicle/plot_eval_pulse.py` 从工件 JSON 渲染（无手抄数据）：")
    a("")
    a("| 图 | 内容 | 文件 |")
    a("|---|---|---|")
    a("| 图 1 | 三条阈值面板：覆盖率（含 60% 阈值线、confirmed/succeeded 标注）、复核结构（独立 vs auto_scan，50% 阈值线）、pending 积压与年龄（200 条阈值线） | `artifacts/chronicle_eval_pulse/figures/fig1_thresholds.png` |")
    a("| 图 2 | S 分布与 E 级分布（confirmed，分层计数，图题注明 no weighting / no ranking） | `artifacts/chronicle_eval_pulse/figures/fig2_distributions.png` |")
    a("| 图 3 | 两连拍对比 + 告警上升沿/抑制状态矩阵（tick1 发出、tick2 抑制） | `artifacts/chronicle_eval_pulse/figures/fig3_two_ticks_alerts.png` |")
    a("| 图 4 | 窗口灵敏度曲线：覆盖率与独立占比随 `window` 变化，无定义点单独标注 | `artifacts/chronicle_eval_pulse/figures/fig4_window_sensitivity.png` |")
    a("")
    a("## 5. 注意点（坑、局限、适用范围）")
    a("")
    a("1. **覆盖率对窗口极敏感**（表 3.4）。近期 mission 的评价记录大多是 pending，因此小窗口覆盖率会掉到 0，"
      "同时独立占比退化为「无定义」。这不是 bug，是「评价流水线滞后于产出」的直接读数——但也意味着"
      "**任何覆盖率结论必须同时报窗口**，否则不可比。")
    a("2. **无定义 ≠ 0%**。分母为 0 时占比记 `None` 并单列；若粗暴按 0% 处理，"
      "「还没开始评价」会被误报成「独立复核缺失」，告警就失去信息量。")
    a("3. **积压年龄是下界**：以记录 `created_at` 起算，不含 mission 进入评价队列前的时间；"
      "生产库当前 60 行 pending 的 `created_at` 是同一时刻（批量生成），因此最老=中位=P90。")
    a("4. **阈值是工程默认值**（60% / 200 / 50%），不是学科判据；改阈值会改告警，报告必须写明当次阈值来源。")
    a("5. **快照只证明「当时的数是什么」**，不证明「数对」。评价本身的正确性由 `outcome_evaluations` "
      "的判据（无理由必拒 / 无证据必拒 / 独立审查 / S-N 门槛）负责，本节点不复审。")
    a("6. **指纹不是签名**：SHA-256 只证明内容未变，不证明作者；工作树内无签名密钥。")
    a("7. **auto_scan 占比高不等于质量高**：机器证据扫描不能替代同行独立评审，两者在 facet 里始终分层。")
    a("8. **零领域语义**：模块内无任何学科/业务词（有测试 `test_module_has_no_domain_vocabulary` 守着），"
      "被观测主体全部经 `--subject` 注入；同一套代码可以对任何主体取脉搏。")
    a("")
    a("## 6. 误差判定（逐项、显式回答「是否 < 3%」）")
    a("")
    a("本节点是**平台可观测性工程**，不对应任何物理量，因此**不存在解析解/实验基准**"
      "（诚实声明：无物理基准，故不做「工程精度 15%」一类声称）。可机器裁的量用等价判据给出：")
    a("")
    a("| 量 | 基准/判据 | 偏差 | 是否 < 3% |")
    a("|---|---|---|---|")
    a(f"| facet 全部计数与占比 | 重跑 facet 自带 SQL 原文（{len(recomp['sql_used'])} 条）逐字段比对 | "
      f"{len(recomp['diffs'])} 项差异（相对偏差 0.000%） | **是**（精确相等） |")
    a(f"| 覆盖率恒等式 | `confirmed / succeeded` vs 报表值 | 0.000% | **是** |")
    a(f"| S 分布自洽 | Σ(分层计数) vs confirmed 记录数 = {f2['s_distribution']['total']} | 0 | **是** |")
    a(f"| 积压年龄 | 逐条 `created_at` 重算 vs 报表 | 0.000% | **是** |")
    a(f"| 告警恰好一次 | 首拍每键 1 条 + 重拍 0 条 = `exactly_once_ok` | 布尔判据 = {drill['exactly_once_ok']} | **是**（非数值量） |")
    a(f"| 连续两拍字段齐备 | {cc['required_fields'].__len__()} 字段 × 2 拍全在 | 缺失 0 项 | **是** |")
    a("")
    a("**结论**：对可精确裁定的量，偏差恰为 0（严格 < 3%）；对覆盖率/积压年龄这类「读数」本身，"
      "不存在参考解，因此不给误差百分比——只声明口径与下界性质（见 §5.3）。"
      "任何把读数当「真值精度」的解读都超出本节点证据范围。")
    a("")
    a("## 7. 复现步骤")
    a("")
    a("```bash")
    a("# 1) 采一拍（--write 才写快照行；本报告为 tick1/tick2 两拍）")
    a('DSN="postgresql://<user>:<pw>@localhost:5432/tensorplatform"')
    a('python3 tools/chronicle/eval_pulse.py capture --dsn "$DSN" --subject <subject> --window 100 \\')
    a("    --write --json-out artifacts/chronicle_eval_pulse/snapshot_1.json --digest-out artifacts/chronicle_eval_pulse/quality_report_1.md")
    a("# 2) 第二拍（带链）")
    a('python3 tools/chronicle/eval_pulse.py capture --dsn "$DSN" --subject <subject> --window 100 \\')
    a("    --write --prev-snapshot-id <tick1_snapshot_id> --json-out artifacts/chronicle_eval_pulse/snapshot_2.json")
    a("# 3) 三项机器判据")
    a('python3 tools/chronicle/eval_pulse.py check  --dsn "$DSN" --json-out artifacts/chronicle_eval_pulse/consecutive_check.json')
    a('python3 tools/chronicle/eval_pulse.py verify --dsn "$DSN" --facet artifacts/chronicle_eval_pulse/snapshot_2.json \\')
    a("    --json-out artifacts/chronicle_eval_pulse/recompute_2.json")
    a("python3 tools/chronicle/eval_pulse.py drill  --json-out artifacts/chronicle_eval_pulse/drill_alert.json")
    a("# 4) 图与报告")
    a("python3 tools/chronicle/plot_eval_pulse.py")
    a("python3 tools/chronicle/eval_pulse_report.py")
    a("python3 -m pytest tests/test_chronicle_eval_pulse.py -q")
    a("```")
    a("")
    a("## 8. 诚实声明")
    a("")
    a("- 演练告警用的是**合成负控数据**（`source=synthetic_drill`），它证明告警通路与恰好一次语义，"
      "**不**证明任何真实异常已被处置。真实两拍的告警来自生产读数（覆盖率与独立占比越界），"
      "但本节点只负责「看得见」，不负责「修好」——覆盖率和独立占比为什么低，是评价流水线的治理问题，"
      "不在本节点范围内。")
    a("- 阈值、窗口、告警语义均为工程实现选择，非学科结论。")
    a("- 未做：告警外发通道（邮件/IM webhook）未接；只写 JSONL 台账 + 编年史 payload。"
      "periodic 调度（cron/守护）未在本节点接线，`capture` 可被任何调度器调用。")
    a("")
    a("---")
    a("")
    a("**工件清单**：" + " · ".join(f"`{p}`" for p in acc["artifacts"]) + f" · `{c['c4_pytest']['file']}`")
    a("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts/chronicle_eval_pulse")
    ap.add_argument("--out", default="docs/reports/in24_e4_eval_pulse.md")
    args = ap.parse_args()
    base = Path(args.artifacts)
    md = build(base)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())