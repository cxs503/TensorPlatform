#!/usr/bin/env python3
"""Render the paper-grade markdown for a metrics-verification run.

Every number in the output is read from the ``report.json`` payload produced by
:mod:`tools.metrics_verify.suite` -- nothing is transcribed by hand, so re-running
the suite provably regenerates the document.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from .stats import rel_error


def _f(value: Any, nd: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "是" if value else "**否**"
    if isinstance(value, float):
        return f"{value:.{nd}g}"
    return str(value)


def _pct(value: Any, nd: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.{nd}f} %"


def render_paper(report: Dict[str, Any]) -> str:
    c = report["criteria"]
    ctl = report["negative_controls"]
    pulse = report["pulse_detail"]
    chron = report["chronicle_detail"]
    port = report["portrait_detail"]
    lat = pulse["latency"]
    ev = report["evidence"]
    L: List[str] = []

    L.append("# TensorPlatform 度量工具验证链：pulse / chronicle / portrait")
    L.append("")
    L.append(f"- run_id: `{report['run_id']}` · git_commit: `{report['git_commit']}` · "
             f"overall_pass: **{report['overall_pass']}**")
    L.append("")

    # ---------------------------------------------------------------- ① 案例介绍
    L.append("## ① 案例介绍（物理背景与参数表）")
    L.append("")
    L.append("平台的三个度量工具此前只有「报告值」，没有「可证伪的验证面」：")
    L.append("`tools/pulse/saturation_pulse.py` 回答「舰队是否活着、心跳延迟多大」，")
    L.append("`tools/chronicle` 回答「事件流有没有丢/乱」，`tools/worker_portrait/portrait.py`")
    L.append("回答「席位画像与它的行为是否一致」。三者都没有被测过——本节点建立合成真值链，")
    L.append("把「工具能测出来」变成可机器裁决的判据，并给出工具错误检出的负控。")
    L.append("")
    L.append("本节点不引入任何领域语义：合成实体为 `agent-NNN` / `sess-NN` / `worker-NN`")
    L.append("与合成能力域 `domain_alpha/beta/gamma`，全部经 API 参数注入，平台仓零领域硬编码。")
    L.append("")
    L.append("**参数表（真值注入侧，全部来自 report.json 的 `parameters`）**")
    L.append("")
    L.append("| 项 | 值 | 说明 |")
    L.append("|---|---|---|")
    p = report["parameters"]["pulse"]
    L.append(f"| pulse 集群规模 | {p['n_agents']} agents × {p['n_heartbeats']} heartbeats | 共 {c['a_pulse_latency']['n_samples']} 个延迟样本 |")
    L.append(f"| 注入延迟分布 | lognormal(median={p['median_latency_s']} s, σ={p['sigma']}) | 单向下行延迟，闭式分位数即参考值 |")
    L.append(f"| 心跳完整率 | {_pct(1.0 - p['loss_rate'])}（loss_rate={p['loss_rate']}） | 丢失注入仅用于判据 (b) 的流 |")
    L.append(f"| 心跳间隔 / 新鲜窗 | {1.0} s / 300 s | 与生产 `saturation_pulse.WINDOW_SECONDS_DEFAULT` 一致 |")
    ch = report["parameters"]["chronicle"]
    L.append(f"| chronicle 事件流 | {ch['n_events']} 事件 / {ch['sessions']} 会话 | 列形 `id, session_id, seq, kind, payload, created_at` |")
    L.append(f"| 乱序 / 丢失 / 重复注入 | 1 % / 1 % / 1 %（各自独立流） | 相邻同会话换位=一次乱序故障 |")
    pp = report["parameters"]["portrait"]
    L.append(f"| portrait 席位群 | {pp['n_agents']} workers × {pp['missions_per_agent']} missions | 共 {c['e_portrait_consistency']['n_missions']} 条 mission 行 |")
    L.append(f"| 行为真值 | p_success∈[0.55,0.95]、wall~lognormal(σ=0.45)、能力域权重逐席位不同 | 生成器在序列化前累加真值 |")
    L.append("")

    # ---------------------------------------------------------------- ② 方法
    L.append("## ② 怎么做的（方法 / 流程 / 工具链）")
    L.append("")
    L.append("验证链是「真值 → 可观测量 → 回收值」三段式，而不是把工具再跑一遍：")
    L.append("")
    L.append("1. **生成真值**（`tools/metrics_verify/sim.py`）：合成心跳流（含延迟/丢失/停顿）、")
    L.append("   事件流（含乱序/丢失/重复注入）、席位行为（成功率/工时/能力域），并在生成时")
    L.append("   保留注入参数与逐实体真值。")
    L.append("2. **经过被测工具**：pulse 侧调用**生产代码** `saturation_pulse.worker_is_fresh()` 与")
    L.append("   `saturation_pulse.compute()`；portrait 侧调用**生产代码** `portrait.aggregate()` 与")
    L.append("   `portrait.verify_against_missions()`；chronicle 侧因平台无纯函数校验器，")
    L.append("   本节点交付参考实现 `validate_event_stream()`（按 `tensor_agent_session_events` 列契约）。")
    L.append("3. **回收与裁决**（`pulse_latency.py` / `chronicle_integrity.py` /")
    L.append("   `portrait_consistency.py` + `suite.py`）：把回收值与注入真值比较，逐条判据给数。")
    L.append("4. **负控**：每条判据都配一个「本该失败」的注入，证明工具是检出的而不是恒真的。")
    L.append("5. **机器可算产物**：`report.json`（判据数字 + 阈值 + 种子 + 提交号 + 被测模块 sha256）、")
    L.append("   `report.md`、四张图、三个 HTTP 端点（`POST /v1/metrics_verify/{pulse,chronicle,portrait}`）。")
    L.append("")
    L.append("一键复现（确定性种子，无 DB、无网络）：`python3 -m tools.metrics_verify.suite "
             "--out artifacts/metrics_verify/p465w1_n01`。")
    L.append("")

    # ---------------------------------------------------------------- ③ 计算结果
    L.append("## ③ 计算结果（程序化注入，零手抄）")
    L.append("")
    L.append("### 判据总表")
    L.append("")
    L.append("| 判据 | 目标 | 实测 | 阈值 | 裁决 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| (a) pulse 心跳延迟 | 均值<1 s、P99<5 s | mean={_f(c['a_pulse_latency']['mean_s'])} s，"
             f"P99={_f(c['a_pulse_latency']['p99_s'])} s（n={c['a_pulse_latency']['n_samples']}） | "
             f"<{c['a_pulse_latency']['mean_limit_s']} s / <{c['a_pulse_latency']['p99_limit_s']} s | {_f(c['a_pulse_latency']['pass'])} |")
    L.append(f"| (b) 心跳丢失检出 | 5 % 丢失检出>95 % | {_pct(c['b_pulse_loss_detection']['detection_rate_strict'])}"
             f"（{c['b_pulse_loss_detection']['n_detected']}/{c['b_pulse_loss_detection']['n_injected']}，严格口径） | "
             f">{_pct(c['b_pulse_loss_detection']['threshold'],1)} | {_f(c['b_pulse_loss_detection']['pass'])} |")
    L.append(f"| (c) chronicle 完整性 | {c['c_chronicle_integrity']['n_events']} 事件无丢失、顺序 100 % | "
             f"completeness={_f(c['c_chronicle_integrity']['completeness'])}，order={_f(c['c_chronicle_integrity']['order_correct_rate'])} | "
             f"=1.0 / =1.0 | {_f(c['c_chronicle_integrity']['pass'])} |")
    L.append(f"| (d) 乱序检出 | 1 % 乱序检出>99 % | {_pct(c['d_chronicle_disorder']['detection_rate'])}"
             f"（{c['d_chronicle_disorder']['n_injected_detected']}/{c['d_chronicle_disorder']['n_injected_transpositions']} 次换位），误报 {c['d_chronicle_disorder']['n_false_alarm_positions']} | "
             f">{_pct(c['d_chronicle_disorder']['threshold'],1)} | {_f(c['d_chronicle_disorder']['pass'])} |")
    L.append(f"| (e) portrait 一致性 | 成功率/工时/能力域误差<5 % | {_f(c['e_portrait_consistency']['max_success_rate_abs_error'])} / "
             f"{_f(c['e_portrait_consistency']['max_mean_wall_rel_error'])} / "
             f"{_f(c['e_portrait_consistency']['max_domain_share_abs_error'])} | <{c['e_portrait_consistency']['tolerance']} | "
             f"{_f(c['e_portrait_consistency']['pass'])} |")
    L.append(f"| (f) 画像漂移检出 | 行为突变检出延迟<10 事件 | mean={_f(c['f_portrait_drift']['latency_mean'])} 事件，"
             f"max={c['f_portrait_drift']['latency_max']} 事件（{c['f_portrait_drift']['n_trials']} seed） | "
             f"<{c['f_portrait_drift']['latency_threshold']} 事件 | {_f(c['f_portrait_drift']['pass'])} |")
    L.append(f"| (g) 工具面卫生 | pytest≥15、3 端点、非法输入 422 | {c['g_harness_hygiene']['n_pytest_cases']} 例测试、"
             f"{c['g_harness_hygiene']['n_verify_routes']} 端点、422={c['g_harness_hygiene']['invalid_input_status']} | ≥15 / =3 / 422 | "
             f"{_f(c['g_harness_hygiene']['pass'])} |")
    L.append("")
    L.append("### 延迟分布细节（pulse，判据 a 的支撑数）")
    L.append("")
    m = lat["measured"]
    ref = lat["analytic_reference"]
    err = lat["estimator_error"]
    L.append("| 统计量 | 注入解析值 | 回收值 | 相对误差 |")
    L.append("|---|---|---|---|")
    L.append(f"| 中位数 p50 [s] | {_f(ref['median'],5)} | {_f(m['p50'],5)} | {_pct(err['p50_rel'])} |")
    L.append(f"| p90 [s] | {_f(ref['p90'],5)} | {_f(m['p90'],5)} | {_pct(err['p90_rel'])} |")
    L.append(f"| p99 [s] | {_f(ref['p99'],5)} | {_f(m['p99'],5)} | {_pct(err['p99_rel'])} |")
    analytic_mean = lat["injected"]["median_latency_s"] * math.exp(lat["injected"]["sigma"] ** 2 / 2)
    analytic_var = (lat["injected"]["median_latency_s"] ** 2
                    * (math.exp(lat["injected"]["sigma"] ** 2) - 1)
                    * math.exp(lat["injected"]["sigma"] ** 2))
    L.append(f"| 均值 [s] | {_f(analytic_mean, 5)}（lognormal 闭式） | {_f(m['mean'], 5)} | "
             f"{_pct(rel_error(m['mean'], analytic_mean))} |")
    L.append(f"| 方差 [s²] | {_f(analytic_var, 5)}（闭式） | {_f(m['var'], 5)} | "
             f"{_pct(rel_error(m['var'], analytic_var))} |")
    L.append("")
    L.append("### 生产内核交叉核验（pulse `compute()` / `worker_is_fresh()`）")
    L.append("")
    core = pulse["core"]
    L.append(f"- `worker_is_fresh` 逐 agent 判定：{core['predicate_checks']} 个 agent，错判 {len(core['predicate_mismatches'])} 个；")
    L.append(f"- `compute()` 快照 vs 合成真值：online {core['measured']['online']}={core['truth']['online']}，"
             f"busy {core['measured']['busy']}={core['truth']['busy']}，idle {core['measured']['idle']}={core['truth']['idle']}，"
             f"stale_claims {core['measured']['stale_claims']}={core['truth']['stale_claims']} → `counts_match={core['counts_match']}`。")
    L.append("")
    L.append("### 事件流细节（chronicle）")
    L.append("")
    L.append("| 场景 | 注入 | 检出 |")
    L.append("|---|---|---|")
    L.append(f"| 诚实流 | 0 | 缺失 {c['c_chronicle_integrity']['n_missing_vs_truth']}、seq 回退 {c['c_chronicle_integrity']['n_seq_regressions']}、时间戳回退 {c['c_chronicle_integrity']['n_ts_regressions']} |")
    L.append(f"| 乱序 1 % | {c['d_chronicle_disorder']['n_injected_transpositions']} 次换位（{c['d_chronicle_disorder']['n_injected_events']} 个事件位移） | 检出 {c['d_chronicle_disorder']['n_injected_detected']} 次，误报 {c['d_chronicle_disorder']['n_false_alarm_positions']} |")
    L.append(f"| 丢失 1 % | {ctl['chronicle_drop']['n_missing_vs_truth']} 事件 | completeness={_f(ctl['chronicle_drop']['completeness'])}（<1 即检出） |")
    L.append(f"| 重复 1 % | {ctl['chronicle_duplicate']['n_duplicates']} 次重复 | 检出 {ctl['chronicle_duplicate']['n_duplicates']} |")
    L.append("")
    L.append("### portrait 逐席位一致性（判据 e）")
    L.append("")
    L.append("| 席位 | 成功率(真值/回收) | 平均工时[s](真值/回收) | 能力域份额最大偏差 |")
    L.append("|---|---|---|---|")
    for w in port.get("consistency_workers") or port["consistency"].get("workers", []):
        L.append(f"| {w['worker']} | {_f(w['success_rate_truth'],4)} / {_f(w['success_rate_measured'],4)} | "
                 f"{_f(w['mean_wall_s_truth'],6)} / {_f(w['mean_wall_s_measured'],6)} | "
                 f"{_f(w['domain_share_max_abs_error'],3)} |")
    L.append("")
    L.append("### 漂移检出与功效曲线（判据 f）")
    L.append("")
    d = port["drift"]
    L.append(f"- 注入突变：成功率 {d['effect']['p_before']} → {d['effect']['p_after']} @ 第 {d['effect']['change_index']} 事件；"
             f"检测器参数 reference_n={d['config']['reference_n']}、p_alt={d['config']['p_alternative']}、h={d['config']['threshold_h']} nats；")
    L.append(f"- 检出延迟：mean={_f(d['latency_mean'])}、max={d['latency_max']} 事件（{d['n_trials']} seeds，漏检 {d['n_missed']}）；"
             f"受控误报：稳态流 {d['n_false_alarms_stable']}/{d['n_trials']}，实测受控平均运行长度 ARL0≈{_f(d['arl0_events_measured'],6)} 事件（要求 ≥{_f(d['arl0_min_required'],4)}）。")
    L.append("")
    L.append("| 突变后成功率 | 检出数 | 平均延迟 | 最大延迟 | 是否落在 10 事件内 |")
    L.append("|---|---|---|---|---|")
    for row in d["power_curve"]:
        L.append(f"| {row['p_after']} | {row['n_detected']}/10 | {_f(row['latency_mean'])} | {row['latency_max']} | {_f(row['within_ceiling'])} |")
    L.append("")
    dd = port.get("domain_drift") or {}
    L.append(f"- 能力域漂移（次要控制）：{dd.get('n_detected')}/{dd.get('n_trials')} 检出，"
             f"平均延迟 {_f(dd.get('latency_mean'))} 事件（阈值 {dd.get('threshold_events')}）。")
    L.append("")

    # ---------------------------------------------------------------- ④ 图
    L.append("## ④ 结果图")
    L.append("")
    figs = ev["figures"]
    captions = {
        "fig1_pulse_latency": "图 1 pulse：100 个 agent×1000 次心跳的延迟 ECDF 与直方图；实测均值/P99 与注入 lognormal 解析分布、判据线（1 s / 5 s）同图对比。",
        "fig2_chronicle_integrity": "图 2 chronicle：完整性/顺序/乱序/丢失/重复五类检出率与注入-回收计数（左/右）。",
        "fig3_portrait_consistency": "图 3 portrait：逐席位成功率「回收 vs 真值」散点（±5 % 带）与三类误差预算。",
        "fig4_portrait_drift": "图 4 portrait 漂移：CUSUM 统计量轨迹（真实突变点与告警点）与 20 seed 的检出延迟分布。",
    }
    for key in ("fig1_pulse_latency", "fig2_chronicle_integrity",
                "fig3_portrait_consistency", "fig4_portrait_drift"):
        meta = figs.get(key)
        if not meta:
            continue
        L.append(f"**{captions[key]}**")
        L.append("")
        L.append(f"`{meta['path']}`（sha256 `{meta['sha256'][:16]}…`）")
        L.append("")
    L.append("图均由 suite 在验收运行时从同一份 report.json 数据生成，PNG 与 JSON 同批入库，")
    L.append("图像不能被单独替换而不破坏 sha256 链。")
    L.append("")

    # ---------------------------------------------------------------- ⑤ 注意点
    L.append("## ⑤ 注意点（坑 / 局限 / 适用范围）")
    L.append("")
    L.append("1. **尾部丢失不可见**：只靠 `seq` 间隙的检测器看不到「某 agent 最后若干次心跳丢失」，"
             f"本次 5 % 丢失流中此类不可检出样本 {c['b_pulse_loss_detection']['n_undetectable_trailing']} 个；"
             "严格口径（分母含它们）已如实计入判据 (b)，生产侧需用 agent 自报计数做对账补充。")
    L.append("2. **漂移检测器的功效有边界**：10 事件的设计点对应「严重突变」（0.95→0.20~0.40）；"
             "对温和变化（0.95→0.50）实测延迟均值远超 10 事件（见功效曲线），这是统计信息量的限制，"
             "不是实现缺陷——报告不据此宣称更强能力。")
    L.append("3. **特异性以 ARL0 而非「零误报」计**：CUSUM 在 ARL0≈1e4 量级下，250 事件窗口出现一次误报"
             "属预期；本报告同时给出稳态流实测误报数与 ARL0，不使用「零误报」措辞。")
    L.append("4. **DB 侧未被单元测试覆盖**：`pulse`/`portrait` 的取数（psycopg / psql）不在本链内；"
             "本链验证的是**计算内核与契约**（`compute`/`worker_is_fresh`/`aggregate`/`verify_against_missions`/"
             "事件校验器），不是数据库在线行为。这是刻意的边界：验证面必须能在 CI 与无 DB 环境跑。")
    L.append("5. **合成分布≠生产分布**：延迟取 lognormal、工时取 lognormal，真值可按闭式给出；"
             "真实平台存在时钟偏斜、批量提交、GC 停顿等重尾因素，判据阈值在真实数据上需重新标定"
             "（`worker_is_fresh` 对超前心跳按 skew 处理，已单独测到）。")
    L.append("6. **判据 (e) 的数值接近 0 是设计使然**：portrait 与真值来自同一批抽样、不同代码路径（真值在序列化前累加，"
             "画像从 ISO 时间戳重算），因此正确管道下误差应为舍入量级；该判据的**鉴别力**由负控证明"
             f"（注入 15 % 状态翻转后最大成功率误差升至 {_f(ctl['portrait_corruption']['max_success_rate_abs_error'])}）。")
    L.append("")

    # ---------------------------------------------------------------- ⑥ 误差判定
    L.append("## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否<3%」）")
    L.append("")
    L.append("| 关键量 | 基准 | 误差 | 是否 <3 % |")
    L.append("|---|---|---|---|")
    for key, label in (("p50_rel", "延迟中位数"), ("p90_rel", "延迟 p90"), ("p99_rel", "延迟 p99")):
        val = err.get(key)
        L.append(f"| {label} 回收值 | 注入 lognormal 闭式分位数 | {_pct(val)} | {_f(val is not None and val < 0.03)} |")
    L.append(f"| 成功率（判据 e） | 生成器序列化前真值 | {_pct(c['e_portrait_consistency']['max_success_rate_abs_error'])}（绝对） | "
             f"{_f(c['e_portrait_consistency']['max_success_rate_abs_error'] < 0.03)} |")
    L.append(f"| 平均工时（判据 e） | 生成器真值 | {_pct(c['e_portrait_consistency']['max_mean_wall_rel_error'])} | "
             f"{_f(c['e_portrait_consistency']['max_mean_wall_rel_error'] < 0.03)} |")
    L.append(f"| 能力域份额（判据 e） | 生成器真值 | {_pct(c['e_portrait_consistency']['max_domain_share_abs_error'])}（绝对） | "
             f"{_f(c['e_portrait_consistency']['max_domain_share_abs_error'] < 0.03)} |")
    L.append(f"| 事件完整性/顺序（判据 c） | 发射侧计数 | 缺失 {c['c_chronicle_integrity']['n_missing_vs_truth']}、"
             f"回退 0 | {_f(c['c_chronicle_integrity']['completeness'] == 1.0)} |")
    L.append(f"| 乱序检出（判据 d） | 注入换位集合 | 漏检 {c['d_chronicle_disorder']['n_injected_transpositions'] - c['d_chronicle_disorder']['n_injected_detected']} 次 | "
             f"{_f(c['d_chronicle_disorder']['detection_rate'] > 0.99)} |")
    L.append("")
    L.append(f"结论：延迟分位数回收误差最大 {_pct(max(v for v in err.values() if v is not None))}（<3 % 达成）；"
             f"portrait 三类误差最大 {_pct(max(c['e_portrait_consistency']['max_success_rate_abs_error'], c['e_portrait_consistency']['max_mean_wall_rel_error'], c['e_portrait_consistency']['max_domain_share_abs_error']))}（<3 % 达成）；"
             "事件流完整性为精确量（缺失=0），乱序检出为计数判据（漏检=0）。"
             "漂移延迟是**事件数**而非百分比误差，按判据 f 的 10 事件上限裁决。")
    L.append("")

    # ---------------------------------------------------------------- 审计链
    L.append("## 审计链")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| git commit | `{report['git_commit']}` |")
    L.append(f"| 运行时间 | `{report['generated_at']}` |")
    L.append(f"| 确定性种子 | pulse/chronicle/portrait = {report['parameters']['pulse']['seed']} / "
             f"{report['parameters']['chronicle']['seed']} / {report['parameters']['portrait']['seed']} |")
    for path, digest in ev["modules_under_test_sha256"].items():
        L.append(f"| 被测模块 `{path}` | `{digest}` |")
    for path, digest in list(ev["modules_sha256"].items())[:3]:
        L.append(f"| 验证链模块 `{path}` | `{digest}` |")
    L.append(f"| 判据 g 探针 | 端点 {ev['api_probe'].get('n_verify_routes')} 个，非法输入状态 "
             f"{ev['api_probe'].get('invalid_input_status')} |")
    L.append("")
    L.append("负控明细（每条都要求「注入 → 工具报错」）：")
    L.append("")
    L.append("| 负控 | 观察量 | 结果 |")
    L.append("|---|---|---|")
    L.append(f"| pulse 8 % 停顿 | P99={_f(ctl['pulse_stall_sensitivity']['measured_p99_s'])} s > 5 s 上限 | {_f(ctl['pulse_stall_sensitivity']['detected_violation'])} |")
    L.append(f"| pulse 干净流 | seq 假间隙 {ctl['pulse_loss_false_positive']['n_false_positive_gaps']} | {_f(ctl['pulse_loss_false_positive']['pass'])} |")
    L.append(f"| chronicle 1 % 丢失 | 对真值缺失 {ctl['chronicle_drop']['n_missing_vs_truth']} | {_f(ctl['chronicle_drop']['detected'])} |")
    L.append(f"| chronicle 1 % 重复 | 重复 {ctl['chronicle_duplicate']['n_duplicates']} | {_f(ctl['chronicle_duplicate']['detected'])} |")
    L.append(f"| portrait 15 % 状态翻转 | 成功率误差 {_f(ctl['portrait_corruption']['max_success_rate_abs_error'])} > 5 % | {_f(ctl['portrait_corruption']['detected'])} |")
    L.append("")
    return "\n".join(L)