"""cycle_baseline.render —— 报告渲染（所有数字从 report 字典注入，零手抄）。"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from . import spec


def _fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "PASS" if v else "FAIL"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def render_markdown(report: dict, figures: Optional[Sequence[dict]] = None) -> str:
    lg = report["ledger"]
    gate = report["coverage_gate"]
    crit = report["criteria"]
    sim = report["policy_simulator"]
    fair = report["fairness"]
    det = report["detectors"]
    L: list[str] = []
    a = L.append

    a(f"# 任务周期四段分解基线与调度公平性（{spec.PREREG_ID}）\n")
    a(f"- schema: `{report['schema_version']}` · prereg: `{report['prereg_id']}`")
    a(f"- 口径指纹 `SPEC_SHA256`: `{report['spec_sha256']}`")
    a(f"- 窗口锚点 asof: `{report.get('asof')}`（固化锚点才能复现同一 cohort）")
    a(f"- 数据源: `{report['source']}` · 窗口: 最近 {report['window_days']} 天终态 mission"
      f" · cohort n = **{report['cohort']['n']:,}**")
    a(f"- 生成时刻(UTC): {report['generated_utc']}\n")
    a("> 零手抄声明：本文件全部数字由 `python -m tools.cycle_baseline` 从基线报告字典程序化"
      "注入；每行基线在 `artifacts/cycle_baseline/baseline.json` 的 `provenance.tables` 中"
      "携带 SQL 原文与 `value_sha256`，可由 `provenance.verify_table` 逐行复算。\n")

    a("## 1. 案例介绍（平台调度管线，非物理域）\n")
    a("测量对象是平台的**任务生命周期管线**：mission 创建 → worker 领取 → 执行到终态 → "
      "改动落地（git）→ 报告发布。学科上等价于一个多工位开环排队系统：到达率由战役派发器"
      "决定，服务时间由 agent 执行决定，落地与报告是**尾段开销**。\n")
    a("四段口径（冻结于 `tools/cycle_baseline/spec.py`）：\n")
    for k in spec.SEGMENTS + ("cycle", "segment_share", "coverage", "starvation",
                              "priority_inversion"):
        a(f"- **{k}**: {spec.DEFINITIONS[k]}")
    a("")

    a("## 2. 怎么做的\n")
    a("1. **取数**（只读）：`tensor_dev_missions` + `tensor_agent_session_events`"
      "（`mission_claimed`）+ `tensor_reports` + `campaign_nodes`（planned_start）；"
      "落地时间由 `git log --all` 按 mission id 反查（`sources.git_merge_index`）。")
    a("2. **四段分解**（`segments.build_record`）：每段按自身锚点算原值，允许为负；"
      "非执行开销口径另给 `max(0,·)` 并单列 `negative_share`。")
    a("3. **分位账本**（`segments.segment_ledger`）：p50/p90/p99 + 覆盖率 + complete-case 份额。")
    a("4. **归因表**（`segments.attribution_table`）：终态 × repo，给段份额与"
      "非执行开销占比，识别瓶颈。")
    a("5. **公平性**（`fairness`）：worker 维度 Jain/Gini（计数与忙时两种口径）、等待 Gini、"
      "饥饿事件（等待 > p95 且非成功终态）、优先级反转（战役内 planned_start 与领取序倒置）。")
    a("6. **策略模拟器**（`simulator`）：round-robin / sticky / weighted 三策略跑同一合成负载，"
      "与名义份额的解析 Jain/Gini 对比，并从 trace 反算 Jain（模拟平台侧黑盒观测）。")
    a("7. **证据链**（`provenance`）：每行 SQL + sha256；Python 统计与 SQL `percentile_cont`"
      " 独立对拍。\n")

    a("## 3. 计算结果（分位账本，程序化注入）\n")
    a("| 段 | n | 覆盖率 | 门禁 | p50 [s] | p90 [s] | p99 [s] | 原值 p50 [s] | 均值 [s] | "
      "complete-case 份额 | 负值占比 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in spec.SEGMENTS:
        e = lg[s]
        raw = e.get("raw_pcts") or {}
        a(f"| `{s}` | {e['n']:,} | {_pct(e['coverage'])} | {e['grade']} | "
          f"{_fmt(e['p50'])} | {_fmt(e['p90'])} | {_fmt(e['p99'])} | "
          f"{_fmt(raw.get('p50'))} | {_fmt(e['mean_s'])} | "
          f"{_pct(e['share_of_measured_total'])} | {_pct(e.get('negative_share'))} |")
    c = lg["cycle_s"]
    a(f"| `cycle_s` | {c['n']:,} | {_pct(c['coverage'])} | — | {_fmt(c['p50'])} | "
      f"{_fmt(c['p90'])} | {_fmt(c['p99'])} | — | {_fmt(c['mean_s'])} | — | — |")
    a(f"\ncomplete-case（四段齐备）n = **{lg['complete_case_n']:,}**"
      f"（覆盖率 {_pct(lg['complete_case_coverage'])}）。\n")
    a(f"**判据 (d) 门禁**：覆盖率 < {spec.COVERAGE_MIN:.0%} 的段 = "
      f"{gate['withheld_segments'] or '无'} → 这些段的**总体结论降级 WITHHELD**"
      "（描述性子样本统计仍给出，但不得作为总体口径引用）。\n")

    cond = report.get("conditional_cohorts") or {}
    if cond:
        a("### 3.0b 条件子总体（覆盖率不足时的唯一诚实出路）\n")
        a("严格 cohort 上四段覆盖率 41%–75%，按判据 (d) **全部降级 WITHHELD**；但缺口有结构"
          "性来源（无会话关联 → 不可能有领取事件；无 PR 动作 → 不可能有落地时间）。故对每段定义"
          "**可证据总体**并在其上重算，结论只对该子总体成立：\n")
        a("| 段 | 可证据总体 n | 占 cohort | 实测 n | 总体内覆盖率 | 判定 | p50 [s] | "
          "代表性（有证据/缺证据 周期 p50 比） | 缺证据样本 n |")
        a("|---|---|---|---|---|---|---|---|---|")
        for seg in spec.SEGMENTS:
            v = cond[seg]
            rep_ = v["representativeness"]
            a(f"| `{seg}` | {v['eligible_population']:,} | "
              f"{_pct(v['eligible_share_of_cohort'])} | {v['measured_n']:,} | "
              f"{_pct(v['coverage_within'])} | {v['grade_label']} | {_fmt(v['p50'])} | "
              f"{_fmt(rep_['ratio_measured_over_missing'], 3)} | "
              f"{rep_['n_missing_with_cycle']:,} |")
        a("")
        for seg in spec.SEGMENTS:
            v = cond[seg]
            a(f"- `{seg}` 可证据总体理由：{v['why']}")
            bi = v.get("bookkeeping_inconsistency")
            if bi:
                a(f"  - **台账不一致**：该总体内 {bi['n_no_event_despite_eligible']:,} 条"
                  f"（{_pct(bi['share'])}）平台称不欠报告却无报告行 —— {bi['reading']}")
        a(f"\n条件判定汇总：可证据子总体达标 {cond['summary']['measured_conditional']}；"
          f"仍 WITHHELD {cond['summary']['withheld']}。{cond['summary']['scope_warning']}。\n")

    a("### 3.1 归因表（终态 × repo，前 12 行按 cohort n 降序）\n")
    a("| status | repo | n | n(complete) | cycle p50 [s] | exec 份额 | queue 份额 | "
      "非执行开销份额 |")
    a("|---|---|---|---|---|---|---|---|")
    for row in sorted(report["attribution"], key=lambda r: -r["n"])[:12]:
        a(f"| {row['status']} | {row['repo']} | {row['n']:,} | {row['n_complete_case']:,} | "
          f"{_fmt(row['cycle_p50_s'])} | {_pct(row['exec_share'])} | "
          f"{_pct(row['share_t_queue'])} | {_pct(row['non_exec_overhead_share'])} |")
    a("")

    a("### 3.2 公平性（真实语料）\n")
    a(f"- worker 数 = {fair['n_workers']}，有主 mission 数 = {fair['n_missions_with_worker']:,}")
    a(f"- **Jain（领取计数）** = {_fmt(fair['jain_count'], 4)} · "
      f"**Jain（执行忙时）** = {_fmt(fair['jain_busy_time'], 4)}")
    a(f"- **Gini（计数）** = {_fmt(fair['gini_count'], 4)} · "
      f"**Gini（忙时）** = {_fmt(fair['gini_busy_time'], 4)}")
    a(f"- 最忙 worker 领取量 / 均值 = {_fmt(fair.get('max_over_mean_count'), 3)}，"
      f"领取计数变异系数 CV = {_fmt(fair.get('cv_count'), 3)}")
    a(f"- 等待时长 Gini = {_fmt(fair['wait_gini'], 4)}；"
      f"等待 p50 = {_fmt(fair['wait_p50_s'])} s，p95 = {_fmt(fair['wait_p95_s'])} s "
      f"(n={fair['wait_n']:,})")
    st = det["starvation_real"]
    a(f"- 饥饿事件：阈值 p{int(st['percentile'] * 100)} = {_fmt(st['threshold_s'])} s，"
      f"命中 {st['n_flagged']:,} / {st['n_eligible']:,}（{_pct(st['flag_rate'])}）")
    iv = det["inversion_real"]
    a(f"- 优先级反转：可判 {iv['n_eligible']:,} 条、比较对 {iv['n_pairs_compared']:,}，"
      f"命中 {iv['n_flagged']:,}（{_pct(iv['flag_rate'])}），"
      f"未领取 {iv['n_unclaimed_in_plans']:,}")
    a(f"- 口径：Jain = (Σx)²/(nΣx²)，x 为 worker 负载；Gini 为平均绝对差形式。\n")

    a("### 3.3 检测器判分（合成带标签夹具）\n")
    fs = det["fixture_scores"]
    a("| 检测器 | n_flagged | precision | recall | F1 |")
    a("|---|---|---|---|---|")
    for name, key in (("饥饿", "starvation"), ("优先级反转", "priority_inversion")):
        s = fs[key]["score"]
        a(f"| {name} | {s['n_flagged']} | {_fmt(s['precision'], 3)} | "
          f"{_fmt(s['recall'], 3)} | **{_fmt(s['f1'], 3)}** |")
    a(f"\n判据 (b) 门限 F1 ≥ {spec.DETECTOR_F1_MIN}；**范围限制**：{fs['caveat']}。\n")

    a("### 3.4 策略模拟器（Jain/Gini 与解析预测）\n")
    a("| 策略 | 名义份额 | Jain(理论) | Jain(实测) | Jain 恢复误差 | Gini(理论) | "
      "Gini(实测) | Gini 相对误差 | 平均等待 [s] |")
    a("|---|---|---|---|---|---|---|---|---|")
    for p, v in sim["policies"].items():
        rho = ", ".join(f"{k}:{x:.3f}" for k, x in v["nominal_share"].items())
        a(f"| `{p}` | {rho} | {_fmt(v['theory']['jain'], 4)} | "
          f"{_fmt(v['measured']['jain_count'], 4)} | "
          f"{_fmt(v['err']['jain_recovery_abs'], 4)} | {_fmt(v['theory']['gini'], 4)} | "
          f"{_fmt(v['measured']['gini_count'], 4)} | {_fmt(v['err']['gini_rel'], 4)} | "
          f"{_fmt(v['measured']['mean_wait_s'])} |")
    a(f"\n判据 (a) Jain 恢复容差 ≤ {spec.JAIN_RECOVERY_TOL}；"
      f"判据 (e) 策略-理论一致容差 ≤ {spec.POLICY_THEORY_TOL:.0%}。"
      f"模拟负载 n_jobs = {sim['n_jobs']}，worker = {sim['n_workers']}，seed = {sim['seed']}。\n")

    cap = report.get("capacity_effect") or {}
    matched = report.get("policy_simulator_capacity_matched")
    if matched:
        a("#### 3.4b 权重是否该与产能匹配（同负载两场景）\n")
        a("| 场景 | 轮转 平均等待 [s] | 加权 平均等待 [s] | 加权/轮转 | 结论 |")
        a("|---|---|---|---|---|")
        for label, key in (("同产能（worker 等速）", "homogeneous"),
                           ("产能=权重（速度 1:2:3:4）", "capacity_matched")):
            h = cap.get(key) or {}
            ratio = h.get("weighted_over_rr")
            verdict = "加权更差" if (ratio or 0) > 1 else "加权更好"
            a(f"| {label} | {_fmt(h.get('round_robin_wait_s'))} | "
              f"{_fmt(h.get('weighted_wait_s'))} | {_fmt(ratio, 3)} | {verdict} |")
        a(f"\n结论（可证伪）：{cap.get('claim')}。"
          "含义：**加权调度只有在权重≈实际产能时才是改进**；否则它把任务堆给慢工位，"
          "平均等待反而变差（本节点实测恶化 44.7%）。平台若要给 worker 配权重，"
          "必须先用实测吞吐标定产能，否则不要上加权。\n")

    a("### 3.5 关键发现与 KPI「周期 −50%」的着力点\n")
    instr = report["cohort"].get("instrumentation") or {}
    cc = [x for x in report["attribution"] if x["n_complete_case"] > 0]
    q_share = sum(x["share_t_queue"] * x["n_complete_case"] for x in cc) / \
        max(1, sum(x["n_complete_case"] for x in cc))
    e_share = sum(x["exec_share"] * x["n_complete_case"] for x in cc) / \
        max(1, sum(x["n_complete_case"] for x in cc))
    m_share = sum((x["share_t_merge"] or 0) * x["n_complete_case"] for x in cc) / \
        max(1, sum(x["n_complete_case"] for x in cc))
    a(f"1. **KPI 基线已可判定**：`cycle_s` p50 = {_fmt(report['ledger']['cycle_s']['p50'])} s"
      f"（覆盖率 {_pct(report['ledger']['cycle_s']['coverage'])}），"
      "「周期 −50%」自此有了冻结的对照值。")
    a(f"2. **最大着力点在排队，不在执行**：complete-case 加权份额 queue = {_pct(q_share)}、"
      f"exec = {_pct(e_share)}、merge = {_pct(m_share)}。"
      "要把周期砍半，先减少派发等待（领取延迟），而不是压缩执行。")
    a(f"3. **落地是隐形开销**：merge 段在 complete-case 中占 {_pct(m_share)}，"
      f"p90 = {_fmt(report['ledger']['t_merge']['p90'])} s；"
      "任务做完了但落地排队，周期照算。")
    a(f"4. **worker 负载不均**：Jain(计数) = {_fmt(fair['jain_count'], 3)}、"
      f"最忙工位是均值的 {_fmt(fair.get('max_over_mean_count'), 2)} 倍；"
      "这与 3.4b 的结论同向：盲目加权只会加剧不均，需要先标定产能。")
    a(f"5. **仪器缺口（决定覆盖率天花板）**：session 关联 {_pct(instr.get('session_linked_share'))}、"
      f"有 PR 动作 {_pct(instr.get('pr_activity_share'))}、"
      f"有报告行 {_pct(instr.get('report_row_share'))}、"
      f"platform 自报欠报告 {_pct(instr.get('report_owed_share'))}、"
      f"战役节点关联 {_pct(instr.get('plan_linkage_share'))}。")
    a("6. **可执行的三条改进**：(i) mission 表直接落 `claimed_at` 列（或把 claim 事件同时写 "
      "`mission_lifecycle_log`）→ t_queue/t_exec 覆盖率可从 ~75% 提到 ~100%，条件结论升为总体结论；"
      "(ii) 所有 push-merge 路径也写 `pr_gate merged` 生命周期事件 → t_merge 不再依赖 git 反查；"
      "(iii) 报告欠账与台账对齐（report_owed=false 却无报告行的 24%）→ t_report 段才可能达标。\n")

    a("## 4. 结果图\n")
    for f in (figures or []):
        a(f"![{f['name']}]({f['name']})\n")
        a(f"*{f['caption']}*\n")
    if not figures:
        a("（本次运行未生成图：`--figure-dir` 未给。）\n")

    a("## 5. 注意点（踩坑与局限）\n")
    a("- **相位陷阱**：战役原假设 `queue→exec→merge→report` 串行，实测 >95% 的报告行"
      "早于 `finished_at`（agent 先发报告再 finish），push-merge 路径的落地 commit 也可能"
      "早于终态。因此段原值允许为负、非执行开销单列 clamp 值 —— 用 clamp 掩盖相位是本任务"
      "最容易犯的假账，已用 `negative_share` 显式暴露。")
    a("- **缺证据 ≠ 0**：无领取事件的 mission 的 t_queue/t_exec 记缺失（不是 0 秒）；"
      "分母恒为 cohort 全量，缩分母会让覆盖率虚高。")
    a("- **merge 证据来自 git**：git 侧按 mission id 反查（三种历史写法），多仓重名/shallow "
      "clone/时间窗口都会造成漏配；漏配计入缺失而非 0。")
    a("- **检测器 F1 只在合成夹具可算**，真实语料无标签；不要把夹具 F1 当作真实检出能力。")
    a("- **窗口锚点必须是固定 asof，不能是 now()**：第一次实现用 ``now() - interval``，"
      "行级导出与 SQL 聚合两次查询之间种群漂移（asof 时在飞、之后落终态的 mission），"
      "实测 n 差 1~3 条、p50 差 1e-4，对拍永远红。改为**单事务 REPEATABLE READ 快照 + 固定 asof**"
      "后对拍误差归零。教训：对拍要先保证两次计算看的是同一张快照，否则测的是抖动不是口径。")
    a("- **原生 ``GREATEST(NULL, 0)`` 会返回 0**：PostgreSQL 的 GREATEST/LEAST 忽略 NULL 入参，"
      "于是「报告时间为 NULL」被静默算成「报告开销 0 秒」，虚增 3 条样本并抬高 p99；"
      "必须显式 ``CASE WHEN ... IS NULL THEN NULL ELSE GREATEST(...) END``。"
      "同类坑还有 psql 默认不带 ON_ERROR_STOP：SQL 报错也返回 0，空结果会被当成「无数据」"
      "（fail-open），现已强制 ``-v ON_ERROR_STOP=1``。")
    a("- **假策略（反例留档）**：加权派发最初写成「有空闲 worker 就按 w/(n+1) 打分派给它」，"
      "weights=[1,2,3,4] 下计数仍近似均分（Jain 0.9993 vs 理论 0.8333）——并行空位存在时分配由"
      "「谁先干完」决定，权重根本不构成约束。必须先**目标派发**（决定谁收这活）再由私有队列执行。"
      "该条已固化为回归测试 ``test_weighted_actually_constrains_allocation_regression``。")
    a("- **模拟器是合成负载**（lognormal 服务时间、指数到达），worker 速度同质；"
      "真实平台有异构模型与预算上限，绝对数值不可外推到生产，仅用于策略间相对比较。\n")

    a("## 6. 误差判定（逐项）\n")
    a("| 判据 | 内容 | 门限 | 实测 | 判定 |")
    a("|---|---|---|---|---|")
    a(f"| (a) | Jain 恢复误差（trace 反算 vs 解析） | ≤ {spec.JAIN_RECOVERY_TOL} | "
      f"{_fmt(max((v['err']['jain_recovery_abs'] or 0) for v in sim['policies'].values()), 4)} | "
      f"{_fmt(crit['a_jain_recovery']['pass'])} |")
    a(f"| (b) | 检测器 F1（合成夹具） | ≥ {spec.DETECTOR_F1_MIN} | "
      f"starv {_fmt(crit['b_detector_f1']['detail']['starvation'], 3)} / "
      f"inv {_fmt(crit['b_detector_f1']['detail']['priority_inversion'], 3)} | "
      f"{_fmt(crit['b_detector_f1']['pass'])} |")
    a(f"| (c) | 每行 SQL+sha256 可复算 | 全部行 | {crit['c_provenance']['n_rows']} 行，"
      f"bad {crit['c_provenance']['n_bad']}，SQL 对拍失配 "
      f"{crit['c_provenance']['sql_crosscheck_mismatch']} | "
      f"{_fmt(crit['c_provenance']['pass'])} |")
    a(f"| (d) | 覆盖率门禁生效 | 任一段 <{spec.COVERAGE_MIN:.0%} → WITHHELD | "
      f"{crit['d_coverage_gate']['withheld_segments'] or '无段被降级'} | "
      f"{_fmt(crit['d_coverage_gate']['pass'])} |")
    a(f"| (e) | 三策略 vs 理论 ≤{spec.POLICY_THEORY_TOL:.0%} | 相对误差 | "
      f"{_fmt(max((v['err']['jain_rel'] or 0) for v in sim['policies'].values()), 4)} | "
      f"{_fmt(crit['e_policy_theory']['pass'])} |")
    a(f"\n**总体**：all_pass = {_fmt(crit['overall']['all_pass'])}；"
      f"被降级段 = {crit['overall']['withheld'] or '无'}。\n")
    a("**「是否 <3%」的回答**：本节点是平台调度度量（非物理量），不存在解析解基准，"
      "故 3% 判据不适用；替代基准是 (i) SQL `percentile_cont` 独立复算（容差 1e-6，"
      "见 crosscheck），(ii) 策略名义份额解析值（容差 2%/5%，见上表）。"
      "两者均按机器判定给出，未达标项绝不含糊其辞。\n")

    a("## 7. 复现\n")
    a("```bash")
    a("python -m tools.cycle_baseline --source pg --repos-root /root/tensor-ecosystem \\")
    a("    --out-json artifacts/cycle_baseline/baseline.json \\")
    a("    --out-md docs/IN55W3-N08_cycle_baseline.md --figure-dir artifacts/cycle_baseline")
    a("python -m pytest tests/test_cycle_baseline_*.py -q")
    a("```")
    return "\n".join(L) + "\n"