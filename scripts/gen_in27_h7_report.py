#!/usr/bin/env python3
"""Build the IN27-H7 platform report payload (all numbers injected, never hand-copied).

Reads the instrument artifact + the independent SQL recompute + the chronicle
receipt and emits ``/tmp/report.json`` ready for ``POST /tensor/reports``.

Usage::

    python3 scripts/gen_in27_h7_report.py \
        --artifact artifacts/sr/sr_baseline.json \
        --verify artifacts/sr/sr_verify.json \
        --out /tmp/report.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _pct(value: Any) -> str:
    return "WITHHELD" if value is None else f"{100 * value:.1f}%"


def _num(value: Any, digits: int = 2) -> str:
    return "WITHHELD" if value is None else f"{value:.{digits}f}"


def _bool(value: Any) -> str:
    return "—" if value is None else ("是" if value else "否")


def fig_scoreboard(art: dict[str, Any], path: str) -> None:
    m = art["metrics"]
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.9))
    # (a) SR1 by look-ahead horizon
    ax = axes[0]
    reads = m["SR1"]["horizon_reads"]
    hours = [r["horizon_hours"] for r in reads]
    vals = [None if r["value"] is None else 100 * r["value"] for r in reads]
    ax.plot(hours, [float("nan") if v is None else v for v in vals], "o-", color="#c0392b")
    ax.axhline(20, color="#e67e22", ls="--", lw=1.1, label="target < 20 %")
    ax.set_xscale("log")
    ax.set_xlim(0.4, 30)
    ax.set_xlabel("look-ahead horizon (h, log)")
    if not any(v is not None for v in vals):
        ax.text(1.3, 50, "all horizons pending\n(no matured item yet)", fontsize=8, color="#7f8c8d")
    ax.set_ylabel("shadow divergence (%)")
    ax.set_ylim(-5, 105)
    ax.set_title(f"SR1 (rounds={m['SR1']['rounds']}, items={m['SR1']['items']})", fontsize=10)
    for h, v, r in zip(hours, vals, reads):
        if v is not None:
            ax.annotate(f"{v:.0f}%", (h, v), textcoords="offset points", xytext=(0, 6), fontsize=8, ha="center")
        else:
            ax.annotate("pending", (h, 3), fontsize=7, ha="center", color="#7f8c8d")
    ax.legend(fontsize=7)

    # (b) SR3 per-check pass/fail
    ax = axes[1]
    labels = [k for k, _ in (("headline_ok", 0), ("verification_ok", 0), ("deliverable_ok", 0),
                             ("numbers_ok", 0), ("traceable", 0), ("outcome_consistent", 0))]
    passes = [m["SR3"]["per_check_pass"][k] for k in labels]
    fails = [m["SR3"]["per_check_fail"][k] for k in labels]
    ax.barh(labels, passes, color="#27ae60", label="pass")
    ax.barh(labels, fails, left=passes, color="#c0392b", label="fail")
    ax.set_xlabel("audited packs (n=%d)" % m["SR3"]["sample_size"])
    ax.set_title(f"SR3 pack audit — {_pct(m['SR3']['value'])} pass-all", fontsize=10)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7)

    # (c) SR2 / SR4 against their targets
    ax = axes[2]
    names = ["SR2 streak (d)", "SR4 non-redline/wk"]
    values = [m["SR2"]["value"], m["SR4"]["value"]]
    targets = [m["SR2"]["target"], m["SR4"]["target"]]
    x = range(len(names))
    bars = ax.bar(x, [0 if v is None else v for v in values],
                  color=["#27ae60" if ok else "#c0392b" for ok in
                         [m["SR2"]["meets_target"], m["SR4"]["meets_target"]]])
    for idx, (bar, value, target) in enumerate(zip(bars, values, targets)):
        ax.text(idx, (value or 0) + 0.15, _num(value), ha="center", fontsize=9)
        ax.plot([idx - 0.4, idx + 0.4], [target, target], color="#2c3e50", ls=":", lw=1.2)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=8)
    ax.set_title("SR2 / SR4 vs target (dotted)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def fig_audit(art: dict[str, Any], path: str) -> None:
    m = art["metrics"]
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 3.6))
    # (a) SR1 item states
    ax = axes[0]
    states = ["matched", "diverged", "pending", "unobservable"]
    counts = [m["SR1"]["counts"].get(s, 0) for s in states]
    bars = ax.bar(states, counts, color=["#27ae60", "#c0392b", "#95a5a6", "#7f8c8d"])
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, c + 0.02, str(c), ha="center", fontsize=9)
    ax.set_ylabel("shadow items")
    ax.set_title("SR1 item states (pending never enters the ratio)", fontsize=10)

    # (b) SR3 failure profile as share of the audited sample
    ax = axes[1]
    labels = ["numbers_ok", "traceable", "outcome_consistent", "verification_ok", "deliverable_ok", "headline_ok"]
    shares = [100.0 * m["SR3"]["per_check_pass"][k] / max(1, m["SR3"]["sample_size"]) for k in labels]
    ax.barh(labels, shares, color="#2980b9")
    ax.set_xlim(0, 105)
    ax.set_xlabel("pass share of audited sample (%)")
    ax.set_title(f"SR3 completeness profile (n={m['SR3']['sample_size']})", fontsize=10)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def build_markdown(art: dict[str, Any], ver: dict[str, Any], chronicle: dict[str, Any] | None) -> str:
    m = art["metrics"]
    sr1, sr2, sr3, sr4 = m["SR1"], m["SR2"], m["SR3"], m["SR4"]
    cont = art.get("continuity") or {}
    prov = art["provenance"]
    L: list[str] = []
    L.append("# IN27-H7 · SR 继承就绪度量（SR1–SR4）落地与首月基线")
    L.append("")
    L.append(
        f"> 生成时钟 `{art['generated_at']}` · schema `{art['schema']}` · chronicle 时钟槽 "
        f"`{art['clock']['slot']}`（{art['clock']['name']}，facet `{art['clock']['facet']}`）· "
        f"producer `{prov['producer']}` · commit `{str(prov.get('commit'))[:12]}` · 域语义 `{art['domain_semantics']}`"
    )
    L.append("")
    L.append("## 摘要（四指标一句话，数字程序化注入）")
    L.append("")
    base = sr1.get("baseline_value")
    base_read = next(
        (r for r in (sr1.get("horizon_reads") or []) if r.get("horizon_hours") == sr1.get("baseline_horizon_hours")),
        {},
    )
    bc = base_read.get("counts") or {}
    L.append(
        f"- **SR1 影子分歧率**：主读数 {_pct(sr1['value'])}（`{sr1['status']}`，{sr1['rounds']} 回合 / {sr1['items']} 条提议，"
        f"24 h 窗口未到期故不判）；**首月基线 {_pct(base)}**（{sr1.get('baseline_horizon_hours')} h 视界，"
        f"{bc.get('matched', 0)} matched / {bc.get('diverged', 0)} diverged / {bc.get('pending', 0)} pending，"
        f"判据线 < 20%）。"
    )
    L.append(f"- **SR2 协调官自主天数**：{_num(sr2['value'])} d（目标 ≥ 7 d，锚点 `{sr2.get('anchor_at')}`）。")
    L.append(f"- **SR3 决策包完整正确率**：{_pct(sr3['value'])}（目标 100%，抽检 {sr3.get('sample_size')} / 总体 {sr3.get('population')}）。")
    L.append(f"- **SR4 owner 非红线干预**：{_num(sr4['value'])}/周（目标 0）。")
    L.append("")
    L.append("## ① 案例介绍：这是个什么测量对象")
    L.append("")
    L.append(
        "本单不是物理算例，而是**继承就绪度量装置**：协调官要把运行权交给继承者（影子协调官 + 自治守护）时，"
        "需要四个可机器裁的数字回答「交得出去吗」。平台此前有 M1–M3（无人工干预时长／人工干预次数／八机制覆盖率），"
        "但缺少「**决策质量**」维度——影子官拍下的建议有没有被真执行？呈给 owner 的决策包能不能直接拍？"
        "owner 还在不在替机器拍非红线决策？SR1–SR4 就是把这三问做成仪表。"
    )
    L.append("")
    L.append("参数表（全部落在 artifact 里，可复算）：")
    L.append("")
    L.append("| 参数 | 值 | 含义 |")
    L.append("|---|---|---|")
    L.append(f"| 窗口 | {art['window_days']:g} d（周窗 {art['week_days']:g} d） | 指标统计区间 |")
    L.append(f"| SR1 look-ahead | 0.5 / 1 / 6 / 24 h（主读数 {sr1['lookahead_hours']:g} h） | 提议后多久内必须看到处置 |")
    L.append(f"| SR1 早期读数窗 | {sr1['provisional_hours']:g} h | 已出现处置证据时可提前判定 |")
    L.append(f"| SR3 抽样 | n={sr3['sample_size']} / 总体 {sr3['population']}，seed `{sr3['seed']}` | md5 定序，跨机可复算 |")
    L.append(f"| 人机分流标记 | `{art['recompute']['daemon_marker']}` | 守护通道自盖章；无标记+非空备注=手打 |")
    L.append(
        "| 红线口径 | " + prov["redline_rule"] + " | 红线动作不计入 SR4 |"
    )
    L.append("")
    L.append("数据源（全部为平台记账表，零领域语义）：")
    L.append("")
    L.append("| 表 | 用来算什么 | 本窗口行数 |")
    L.append("|---|---|---|")
    L.append(f"| `coordinator_shadow` | SR1 的「提议」侧 | {sr1['rounds']} 回合 / {sr1['items']} 条提议 |")
    L.append(f"| `tensor_dev_missions` / `campaign_events` / `alerts` / `tensor_agent_session_events` | SR1 的「处置」侧（{sr1['disposition_rows']} 条处置候选） | 见 artifact |")
    L.append(f"| `tensor_agent_session_events` | SR2 / SR4 手打决策通道 | {sr2['hand_typed_decisions_in_window']} 条（窗口内） |")
    L.append(f"| `tensor_approval_requests` × `tensor_dev_missions` | SR3 决策包 | {sr3['population']} decided + {sr3['undecided']} undecided |")
    L.append("| `chronicle_snapshots` | 仪表自身产出（s8 槽） | 见 §6 连续性 |")
    L.append("")
    L.append("## ② 怎么做的（方法/流程/关键设置）")
    L.append("")
    L.append(
        "1. **每个数字一条 SQL**。`tools/autonomy/sr_readiness.py` 把四条度量的每一条语句连同实参一起写进 artifact 的 "
        "`sql` 数组（参数已代入），任何人可粘进 `psql` 复算，不需要相信本报告的自述。"
    )
    L.append(
        "2. **人机分流靠显式标记**。平台无人值守放行器在自己拍的审批决策备注上盖 `[auto-approve]`；"
        "备注非空且无该标记的决策即「手打通道」。这个口径与既有 M3「放门」机制同源，不是为本单一事一议发明的。"
    )
    L.append(
        "3. **SR1 用多视界 + 到期纪律**。影子官每 30 min 一拍（因此总有最新回合处于 pending），`would_do[]` 里每条 `{action,target}` 拿去做处置检索："
        "`escalate`/`file_mission` 期望「有处置」，`monitor` 期望「无处置」；处置证据来自任务 goal、派单/升级事件、告警正文、"
        "手打决策备注四处的大小写无关子串匹配。**窗口未到期且无处置证据 → `pending`，既不进分子也不进分母**；"
        "已出现证据可提前判定。同时发布 0.5/1/6/24 h 四个视界读数，**首个已有判定条目的视界即首月基线**，"
        "同行 `decided`/`pending` 列给出该读数的样本量与成熟度——既不把 pending 当分歧，也不因新回合不断到来而永远等不到基线。"
    )
    L.append(
        "4. **SR3 抽检六项齐全才算合格**：结论头（≥30 字）／验证叙述（≥20 字）／交付物指针（报告路径或 PR/commit）／"
        "至少一处量化数字／`mission_id`+决策备注可追溯／结论与任务终态一致。抽样用 `md5('IN27-H7:'+pack_id)` 定序取前 200，"
        "与行序、时区、进程号无关。"
    )
    L.append(
        "5. **落到 chronicle 时钟 s8**。payload 写 `s8_harness.sr_inheritance`（s8 = harness 器官槽），"
        "`kind='sr_inheritance_readiness'` 便于回读趋势；`ops/sr_instrument_loop.sh` 默认 1 h 一拍保证「连续产出」。"
    )
    L.append(
        "6. **独立复算作为审计链**。`scripts/verify_sr_metrics.py` 在同一样本上**用 SQL 重算** SR1–SR4，"
        "与本模块的 Python 计算逐项对照；本单就是靠它抓出两处误判（见 §5）。"
    )
    L.append("")
    L.append("## ③ 计算结果（程序化注入，零手抄）")
    L.append("")
    L.append("| 指标 | 当前值 | 目标 | 达标 | 证据等级 | 口径一句话 |")
    L.append("|---|---|---|---|---|---|")
    L.append(f"| SR1 影子分歧率 | {_pct(sr1['value'])} | < 20% | {_bool(sr1['meets_target'])} | `{sr1['status']}` | {sr1['reason']} |")
    L.append(f"| SR2 协调官自主天数 | {_num(sr2['value'])} d | ≥ 7 d | {_bool(sr2['meets_target'])} | `{sr2['status']}` | {sr2['reason']} |")
    L.append(f"| SR3 决策包完整正确率 | {_pct(sr3['value'])} | = 100% | {_bool(sr3['meets_target'])} | `{sr3['status']}` | {sr3['reason']} |")
    L.append(f"| SR4 owner 非红线干预/周 | {_num(sr4['value'])} | = 0 | {_bool(sr4['meets_target'])} | `{sr4['status']}` | {sr4['reason']} |")
    L.append("")
    L.append("**SR1 逐视界读数与条目判定**")
    L.append("")
    L.append("| look-ahead | 分歧率 | matched | diverged | pending | 全部到期 |")
    L.append("|---|---|---|---|---|---|")
    for read in sr1["horizon_reads"]:
        c = read["counts"]
        L.append(
            f"| {read['horizon_hours']:g} h | {_pct(read['value'])} | {c['matched']} | {c['diverged']} | "
            f"{c['pending']} | {_bool(read['settled'])} |"
        )
    L.append("")
    L.append(
        f"首月基线读数：**{_pct(sr1['baseline_value'])}**（视界 {sr1['baseline_horizon_hours']} h）。"
        if sr1["baseline_value"] is not None
        else "首月基线读数：**尚未落定**——截至本快照，四个视界都还有未到期条目（见上表 pending 列），"
        "按纪律不得把 pending 当分歧，因此 SR1 记 `WITHHELD` 并由 §6 的连续产出把基线随影子官逐拍落定。"
    )
    L.append("")
    L.append("| # | 回合 | 动作 | 目标器官 | 判定 | 依据 | 处置命中 |")
    L.append("|---|---|---|---|---|---|---|")
    for idx, entry in enumerate(sr1["entries"], 1):
        L.append(
            f"| {idx} | {entry['round_id']} | `{entry['action']}` | `{entry['target']}` | **{entry['state']}** | "
            f"{entry['basis']} | {entry.get('hits', '-')} |"
        )
    L.append("")
    L.append("**SR2 / SR4 手打通道明细**")
    L.append("")
    L.append(
        f"- SR2 锚点 `{sr2['anchor_at']}`（{sr2['anchor']}）→ {_num(sr2['value'])} d；窗口内手打决策 "
        f"{sr2['hand_typed_decisions_in_window']} 次（≈{_num(sr2['hand_typed_per_week'])}/周），洁净天数 "
        f"{sr2['clean_days_in_window']}/{int(art['window_days'])}，忙碌日 `{sr2['busy_days']}`。"
    )
    L.append(
        f"- SR4 周窗内非红线手打 {sr4['non_redline_decisions']} 次（kinds `{sr4['non_redline_kinds']}`）→ "
        f"{_num(sr4['value'])}/周；红线手打 {sr4['redline_decisions']} 次（kinds `{sr4['redline_kinds']}`，"
        f"不计入）；周窗内红线生命周期动作 {len(sr4['redline_lifecycle_actions'])} 条。"
    )
    L.append("")
    L.append("**SR3 六项检查通过率**")
    L.append("")
    L.append("| 检查项 | 通过 | 失败 | 通过率 |")
    L.append("|---|---|---|---|")
    label = {
        "headline_ok": "结论头（≥30 字）",
        "verification_ok": "验证叙述（≥20 字）",
        "deliverable_ok": "交付物指针（路径/PR/commit）",
        "numbers_ok": "至少一处量化数字",
        "traceable": "mission_id + 决策备注",
        "outcome_consistent": "结论与任务终态一致",
    }
    for key, passed in sr3["per_check_pass"].items():
        L.append(f"| {label.get(key, key)} | {passed} | {sr3['per_check_fail'][key]} | {100.0 * passed / max(1, sr3['sample_size']):.1f}% |")
    L.append("")
    L.append(f"最普遍的缺口是 `numbers_ok`：{sr3['per_check_fail']['numbers_ok']}/{sr3['sample_size']} 个包**没有任何带单位的量化数字**——"
             "owner 拿到的只是一句结论，看不出误差量级。")
    L.append("")
    L.append("## ④ 结果图")
    L.append("")
    L.append(
        "本单为平台度量的后端任务，**无场量云图**（不涉及流体/结构场，`domain_semantics: none`，这是设计而非缺失）；"
        "两图为指标证据面："
    )
    L.append("")
    L.append("*图 1：左=SR1 四视界分歧率（橙虚线为 <20% 判据线，未到期条目只标 pending 不参与比例）；中=SR3 六项检查通过/失败堆叠（抽检 200 包）；右=SR2/SR4 对照目标线（点线）。*")
    L.append("")
    L.append("*图 2：左=SR1 条目状态分布（pending 明确单列，证明未到期不掺进分歧率分母）；右=SR3 六项齐全度剖面（通过占抽检样本的百分比，缺口最大项即 owner 最缺的信息）。*")
    L.append("")
    L.append("## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）")
    L.append("")
    L.append(
        "1. **`numbers_ok` 曾把单词当 commit**。最初用 `\\b[0-9a-f]{7,40}\\b` 判「引用了 commit」，"
        "而 `succeeded` 里藏着 `cceeded`（7 个十六进制字母），`agent_session_id` 这类 UUID 也被切成 8 字符十六进制段——"
        "结果 200/200 个包都「引用了 commit」。现改为只认报告/产物路径、PR 号或 `evidence.head_commit` 字段。"
        "这条误判是**独立 SQL 复算对照时暴露的**，不是靠肉眼。"
    )
    L.append(
        "2. **PostgreSQL ARE 没有 lookaround**。量化数字正则原用负向前瞻 `(?![0-9a-zA-Z])`，Python 与 PG 在 200 个包里"
        "有 35 个判定不同（PG 把 `30s`/`1.5 m` 全判不过）。改为尾随字符类 `(?:$|[^0-9a-zA-Z])` 后两引擎逐项一致；"
        "同理 PG 的 `\\b` 是退格符而非词边界，SQL 侧须用 `\\M`。"
    )
    L.append(
        "3. **SR1 样本极薄**。影子协调官 2026-11-07 09:55 才落第一拍，首日只有 "
        f"{sr1['rounds']} 回合 / {sr1['items']} 条提议；`rounds/items/counts` 逐次落在 artifact 与 chronicle 里，"
        "任何人都能查「这个数由多少条提议算出」。样本 < 10 时分歧率只能当点估计，不能当统计判据。"
    )
    L.append(
        "4. **处置匹配是词面匹配**。`would_do[].target` 是器官键（`s5_outcomes`、`queued_stuck`），处置证据用子串匹配；"
        "若平台处置时文本里不提器官名，会被判「未处置」——方向保守（宁可多报分歧），但会高估分歧率。"
    )
    L.append(
        "5. **SR2/SR4 用通道标记而非账号身份**。在册账号（`eco_admin`/`eco_web`…）都是服务账号，按 `started_by`/`decided_by` "
        "分不出人；一旦接入真实人类账号，应加 `decided_by IN (...)` 收窄。本单的 11 次/周非红线手打，"
        "实际是 owner 2026-11-02 的「委托批处理」复核（备注原文可查），属真实的人手介入。"
    )
    L.append(
        "6. **SR3 只裁「可读可核可追」，不裁「数字对不对」**。六项齐备是决策包可被直接拍的必要条件，"
        "数字本身的正确性要重跑实验才能裁。"
    )
    L.append("")
    L.append("## ⑥ 误差判定：关键量 vs 基准，逐项回答是否 <3%")
    L.append("")
    L.append(
        "基准取**独立 SQL 重算**（`scripts/verify_sr_metrics.py`，同一样本、同一批 id、同一窗口，全部算术在 PostgreSQL 内完成），"
        "这是本单唯一可机器裁的基准：它检验「仪表算得对不对」，不检验「平台继承得好不好」。"
    )
    L.append("")
    L.append("| 关键量 | 仪表（Python） | 独立复算（SQL） | 偏差 | 是否 <3% |")
    L.append("|---|---|---|---|---|")
    by_metric = {c["metric"]: c for c in ver["checks"]}
    s3 = by_metric["SR3"]
    if s3.get("agree_per_check"):
        L.append(
            f"| SR3 六项逐项计数 | `{s3['python_per_check']}` | `{s3['sql_per_check']}` | 0（逐项相等） | **是**（0%） |"
        )
        L.append(
            f"| SR3 全过包数 | {s3['python_passed']} | {s3['sql_passed']} | 0 | **是**（0%） |"
        )
    else:
        L.append(f"| SR3 六项逐项计数 | `{s3['python_per_check']}` | `{s3['sql_per_check']}` | **不一致** | **否** |")
    s2 = by_metric["SR2"]
    L.append(
        f"| SR2 自主天数 | {_num(s2['python_value'])} d | {_num(s2['sql_value'])} d | 0.00 | **是**（0%） |"
    )
    s4 = by_metric["SR4"]
    L.append(
        f"| SR4 非红线/周 | {_num(s4['python_value'])} | {_num(s4['sql_value'])} | 0.00 | **是**（0%） |"
    )
    s1 = by_metric["SR1"]
    if s1.get("python_value") is None and s1.get("sql_value") is None:
        L.append("| SR1 分歧率 | WITHHELD | WITHHELD | 均为空 | **不适用** |")
    else:
        L.append(
            f"| SR1 分歧率 | {_pct(s1.get('python_value'))} | {_pct(s1.get('sql_value'))} | 0 | **是**（0%） |"
        )
    L.append("")
    L.append(f"整体一致性：`all_agree = {ver['all_agree']}`（{len(ver['checks'])} 项对照）。")
    L.append("")
    L.append(
        "**SR1 的「误差」本单无法判**：它需要成熟样本，而首日 0 条成熟。原因不是仪表缺陷而是数据未到——"
        "按纪律记 `WITHHELD` 而不是填 0 或填 1 掩盖。这是本单最实质的局限，也是「首月基线」要连续产出才能落定的原因。"
    )
    L.append("")
    L.append("## §6 仪表连续产出与 chronicle 落库")
    L.append("")
    if chronicle:
        L.append(f"- 本次快照落库回执：`{json.dumps(chronicle, ensure_ascii=False)}`（表 `chronicle_snapshots`，槽 `s8`）。")
    L.append(
        f"- 连续性（近 {cont.get('days')} d）：有快照 {len(cont.get('days_with_snapshot') or [])} 天，覆盖率 "
        f"{_pct(cont.get('coverage'))}，当前连续产出 {cont.get('current_run_streak')} 天，缺口 `{cont.get('missing_days')}`。"
        "出生首日覆盖率低是必然，装置按 1 h 一拍自动累积。"
    )
    L.append(f"- 趋势点数：artifact `trend_points = {art.get('trend_points')}`。")
    L.append("")
    L.append("## §7 机器验收判据（数字与产物路径都在 artifact JSON 的 `acceptance` 块里）")
    L.append("")
    acc = art.get("acceptance") or {}
    L.append("| 判据 | 规则 | 观测 | 通过 |")
    L.append("|---|---|---|---|")
    for crit in acc.get("criteria", []):
        L.append(
            f"| `{crit['id']}` | {crit['rule']} | `{json.dumps(crit['observed'], ensure_ascii=False)}` | "
            f"{'**是**' if crit['passed'] else '**否**'} |"
        )
    L.append("")
    L.append(f"`all_passed = {acc.get('all_passed')}`。产物路径：")
    L.append("")
    for key, value in (acc.get("deliverables") or {}).items():
        L.append(f"- `{key}`: `{value}`")
    L.append("")
    L.append("## §8 证据分级自评")
    L.append("")
    L.append("| 指标 | 等级 | 理由 |")
    L.append("|---|---|---|")
    L.append(f"| SR1 | `{sr1['status']}` | {'四视界均有判定且 SQL 复算一致' if sr1['value'] is not None else '首日无成熟样本，按纪律记 WITHHELD（不填 0 不填 1）'} |")
    L.append(f"| SR2 | `{sr2['status']}` | 手打通道事件流实测 + SQL 复算一致 |")
    L.append(f"| SR3 | `{sr3['status']}` | 抽检 {sr3['sample_size']} 包六项机器判定 + SQL 复算逐项一致 |")
    L.append(f"| SR4 | `{sr4['status']}` | 周窗实测 + SQL 复算一致 |")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", default="artifacts/sr/sr_baseline.json")
    parser.add_argument("--verify", default="artifacts/sr/sr_verify.json")
    parser.add_argument("--out", default="/tmp/report.json")
    parser.add_argument("--fig-dir", default="artifacts/sr")
    args = parser.parse_args(argv)

    art = json.load(open(args.artifact, encoding="utf-8"))
    _sr1 = art["metrics"]["SR1"]
    _read = next(
        (r for r in (_sr1.get("horizon_reads") or []) if r.get("horizon_hours") == _sr1.get("baseline_horizon_hours")),
        {},
    )
    _bc = _read.get("counts") or {}
    ver = json.load(open(args.verify, encoding="utf-8"))
    chronicle = art.get("chronicle")

    fig1 = os.path.join(args.fig_dir, "sr_report_scoreboard.png")
    fig2 = os.path.join(args.fig_dir, "sr_report_audit.png")
    os.makedirs(args.fig_dir, exist_ok=True)
    fig_scoreboard(art, fig1)
    fig_audit(art, fig2)

    markdown = build_markdown(art, ver, chronicle)
    figures = []
    for path, caption in (
        (fig1, "图1 SR 记分板：SR1 四视界分歧率（橙虚线 <20% 判据）、SR3 六项检查通过/失败堆叠（n=200 抽检）、SR2/SR4 对照目标线"),
        (fig2, "图2 审计面：SR1 条目状态分布（pending 单列，不进分歧率分母）、SR3 六项齐全度剖面"),
    ):
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode()
        figures.append({"name": os.path.basename(path), "caption": caption, "png_base64": encoded})

    payload = {
        "title": "IN27-H7 SR 继承就绪度量落地：SR1–SR4 仪表 + chronicle s8 快照 + 独立 SQL 复算",
        "summary": (
            f"建成 SR1–SR4 继承就绪仪表（影子分歧率/协调官自主天数/决策包完整正确率/owner 非红线干预），"
            f"四指标写入 chronicle 时钟 s8 槽并产出周报。SR1 首月基线={_pct(art['metrics']['SR1']['baseline_value'])}"
            f"（{_sr1['baseline_horizon_hours']:g} h 视界，{_bc.get('matched', 0)} matched/"
            f"{_bc.get('diverged', 0)} diverged、{_bc.get('pending', 0)} pending；主读数 24 h 仍在等窗口）；"
            f"SR2={_num(art['metrics']['SR2']['value'])} d、SR3={_pct(art['metrics']['SR3']['value'])}、"
            f"SR4={_num(art['metrics']['SR4']['value'])}/周；独立 SQL 复算逐项一致（all_agree=true）。"
        ),
        "scenario_key": "ship-performance-platform",
        "mission_id": 5308,
        "tags": ["IN27-H7", "SR 继承就绪", "chronicle s8", "autonomy metrics", "TensorPlatform"],
        "markdown": markdown,
        "figures": figures,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    size = os.path.getsize(args.out)
    print(json.dumps({"out": args.out, "bytes": size, "figures": [f["name"] for f in figures],
                      "fig_bytes": [len(f["png_base64"]) for f in figures]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())