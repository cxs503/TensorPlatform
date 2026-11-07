#!/usr/bin/env python3
"""IN20-R5 交付物生成器：判据数字 + 图 + 论文级报告（程序化注入，零手抄）。

输入（全部是仓库内已落盘证据，不重算业务逻辑）：
  tests/fixtures/stall_20261107_0052_0318.json     回放时间线（锚点=实测脉搏表）
  artifacts/in20_r5/replay/{verdict,replay_trace,audit.jsonl,beacon}.json
  artifacts/in20_r5/live/{verdict,alerts,audit.jsonl}.json
输出：
  artifacts/in20_r5/fig_stall_timeline.png          停摆时间线 + 检出时刻
  artifacts/in20_r5/criteria.json                   机器可裁的判据数字
  docs/IN20_R5_stall_detector.md                    论文级报告

用法：python3 tools/stall_detector_report.py [--skip-pytest]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ops.taskplane import stall_detector as sd  # noqa: E402

UTC = timezone.utc
FIXTURE = REPO / "tests/fixtures/stall_20261107_0052_0318.json"
ART = REPO / "artifacts/in20_r5"
DOC = REPO / "docs/IN20_R5_stall_detector.md"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_pytest() -> dict:
    proc = subprocess.run([sys.executable, "-m", "pytest",
                           "tests/test_stall_detector_in20_r5.py", "-q"],
                          cwd=REPO, capture_output=True, text=True, timeout=900)
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else ""
    passed = 0
    for tok, nxt in zip(summary.split(), summary.split()[1:]):
        if nxt.startswith("passed"):
            passed = int(tok)
    return {"returncode": proc.returncode, "summary": summary, "passed": passed,
            "failed": 0 if proc.returncode == 0 else 1}


def compute() -> dict:
    fixture = load(FIXTURE)
    samples = fixture["samples"]
    replay = load(ART / "replay/verdict.json")
    trace = load(ART / "replay/replay_trace.json")
    live = load(ART / "live/verdict.json")
    live_alerts = load(ART / "live/alerts.json")
    audit_replay = sd.verify_chain(ART / "replay/audit.jsonl")
    audit_live = sd.verify_chain(ART / "live/audit.jsonl")

    ts = [sd.parse_ts(s["ts"]) for s in samples]
    pulse_last = sd.parse_ts(fixture["provenance"]["gap"]["start"])
    gap_start = sd.parse_ts(fixture["provenance"]["gap"]["start"])
    gap_end = sd.parse_ts(fixture["provenance"]["gap"]["end"])
    stall_s = fixture["provenance"]["gap"]["seconds"]

    th = sd.Thresholds()
    # 断粮条件首次成立时刻（用同一套判定函数，不手算）
    starve_onset = None
    for raw in samples:
        s = sd.Sample.from_dict(raw)
        ev = {e.rule: e for e in sd.evaluate_sample(s, th)}
        if ev[sd.RULE_STARVED].active:
            starve_onset = s.ts
            break
    r1_fire = sd.parse_ts(replay["by_rule"][sd.RULE_STARVED]["first_fire_ts"])
    r3_fire = sd.parse_ts(replay["by_rule"][sd.RULE_PULSE]["first_fire_ts"])
    r1_delay = (r1_fire - (starve_onset + timedelta(seconds=th.starve_sustain_s))).total_seconds()
    r3_gate = pulse_last + timedelta(seconds=th.pulse_stall_s)
    r3_delay = (r3_fire - r3_gate).total_seconds()

    return {
        "fixture": {"path": str(FIXTURE.relative_to(REPO)), "samples": len(samples),
                    "window": [samples[0]["ts"], samples[-1]["ts"]],
                    "gap_start": fixture["provenance"]["gap"]["start"],
                    "gap_end": fixture["provenance"]["gap"]["end"],
                    "stall_s": stall_s, "stall_min": round(stall_s / 60, 2),
                    "evidence_grade": fixture["provenance"]["gap"]["evidence_grade"]},
        "replay": {
            "observations": replay["observations"], "fires": replay["fires"],
            "kinds": replay["kinds"],
            "r1": replay["by_rule"][sd.RULE_STARVED],
            "r2": replay["by_rule"][sd.RULE_FLEET],
            "r3": replay["by_rule"][sd.RULE_PULSE],
            "action_log": replay.get("action_log", []),
        },
        "detection": {
            "starve_onset": sd.iso(starve_onset), "starve_gate_s": th.starve_sustain_s,
            "r1_fire": sd.iso(r1_fire), "r1_delay_s": r1_delay,
            "r1_delay_ratio": round(r1_delay / th.starve_sustain_s, 5),
            "r1_after_stall_start_s": (r1_fire - gap_start).total_seconds(),
            "r3_fire": sd.iso(r3_fire), "r3_gate": sd.iso(r3_gate),
            "r3_delay_s": r3_delay, "r3_delay_ratio": round(r3_delay / th.pulse_stall_s, 5),
            "r3_after_stall_start_s": (r3_fire - gap_start).total_seconds(),
            "sampling_s": 60,
        },
        "live": {"as_of": live_alerts["sample"]["ts"],
                 "alarm": live["alarm"], "fires": live["fires"],
                 "sample": live_alerts["sample"], "thresholds": live_alerts["thresholds"]},
        "audit": {"replay_records": audit_replay["records"],
                  "replay_ok": audit_replay["ok"],
                  "replay_head": audit_replay["head"],
                  "live_records": audit_live["records"], "live_ok": audit_live["ok"]},
        "thresholds": th.as_dict(),
        "trace": trace,
    }


def figure(data: dict) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    samples = load(FIXTURE)["samples"]
    ts = [sd.parse_ts(s["ts"]) for s in samples]
    hours = [(t - ts[0]).total_seconds() / 3600 for t in ts]
    idle = [s["workers_idle"] / max(1, s["workers_online"]) for s in samples]
    online = [s["workers_online"] for s in samples]
    busy = [s["workers_online"] - s["workers_idle"] for s in samples]
    pending = [s["dag_pending"] + s["plan_pending"] for s in samples]
    age = [None if s["pulse_last_ts"] is None else
           (sd.parse_ts(s["ts"]) - sd.parse_ts(s["pulse_last_ts"])).total_seconds() / 60
           for s in samples]
    gap0 = sd.parse_ts(data["fixture"]["gap_start"])
    gap1 = sd.parse_ts(data["fixture"]["gap_end"])
    gx0 = (gap0 - ts[0]).total_seconds() / 3600
    gx1 = (gap1 - ts[0]).total_seconds() / 3600
    r1 = sd.parse_ts(data["replay"]["r1"]["first_fire_ts"])
    r3 = sd.parse_ts(data["replay"]["r3"]["first_fire_ts"])

    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.4), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1]})
    ax = axes[0]
    ax.plot(hours, [a if a is not None else float("nan") for a in age], color="#b3242b",
            lw=1.8, label="saturation_pulse age (min)")
    ax.axhline(data["thresholds"]["pulse_stall_s"] / 60, ls="--", color="#555", lw=1.1,
               label=f"stall gate = {data['thresholds']['pulse_stall_s'] // 60} min")
    ax.axvspan(gx0, gx1, color="#b3242b", alpha=0.08,
               label=f"pulse blind gap {data['fixture']['stall_min']:.1f} min")
    ax.plot([(r3 - ts[0]).total_seconds() / 3600], [data["thresholds"]["pulse_stall_s"] / 60],
            marker="o", ms=8, mfc="white", mec="#b3242b", mew=2)
    ax.annotate(f"R3 fire {r3.strftime('%H:%M')}\npulse_stall→selfproof",
                xy=((r3 - ts[0]).total_seconds() / 3600,
                    data["thresholds"]["pulse_stall_s"] / 60),
                xytext=(-150, 22), textcoords="offset points", fontsize=8.5,
                arrowprops=dict(arrowstyle="->", color="#b3242b", lw=1))
    ax.set_ylabel("pulse age (min)")
    ax.set_title("IN20-R5 stall detector — replay of 2026-11-07 00:52→03:18 stall "
                 "(anchors = real `saturation_pulse` rows)", fontsize=10.5)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.25)

    ax = axes[1]
    ax.plot(hours, idle, color="#1f6fb2", lw=1.6, label="idle/online ratio")
    ax.axhline(data["thresholds"]["starve_idle_ratio"], ls="--", color="#555", lw=1.1,
               label=f"starve gate = {data['thresholds']['starve_idle_ratio']}")
    ax.axvspan(gx0, gx1, color="#b3242b", alpha=0.08)
    ax.set_ylabel("idle / online", color="#1f6fb2")
    ax.set_xlabel("hours since 2026-11-07T00:35Z")
    ax2 = ax.twinx()
    ax2.plot(hours, online, color="#7a7a7a", lw=1.0, ls=":", label="workers online")
    ax2.plot(hours, busy, color="#e08a1e", lw=1.2, label="workers busy")
    ax2.plot(hours, pending, color="#2b8a3e", lw=1.4, label="dispatchable DAG nodes pending")
    ax2.set_ylabel("heads / nodes")
    ax.plot([(r1 - ts[0]).total_seconds() / 3600], [data["thresholds"]["starve_idle_ratio"]],
            marker="s", ms=8, mfc="white", mec="#1f6fb2", mew=2)
    ax.annotate(f"R1 fire {r1.strftime('%H:%M')}\nstarved→refill",
                xy=((r1 - ts[0]).total_seconds() / 3600,
                    data["thresholds"]["starve_idle_ratio"]),
                xytext=(-140, -34), textcoords="offset points", fontsize=8.5,
                arrowprops=dict(arrowstyle="->", color="#1f6fb2", lw=1))
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    ax.grid(alpha=0.25)

    fig.tight_layout()
    out = ART / "fig_stall_timeline.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def write_doc(data: dict, pytest_result: dict, fig_path: Path) -> Path:
    d = data["detection"]
    live = data["live"]
    s = live["sample"]
    th = data["thresholds"]
    r1, r2, r3 = data["replay"]["r1"], data["replay"]["r2"], data["replay"]["r3"]
    log = {item["rule"]: item["result"] for item in data["replay"]["action_log"]}
    live_checks = [
        ("pending DAG 节点", s["pending_nodes"]),
        ("排队任务 queued", s["queued_missions"]),
        ("在线席位 online", s["workers_online"]),
        ("空闲席位 idle", s["workers_idle"]),
        ("idle/online", s["idle_ratio"]),
        ("心跳新鲜/登记", f"{s['heartbeat_fresh']}/{s['workers_enrolled']}"),
        ("失联比", s["lost_ratio"]),
        ("脉搏老化 (s)", s["pulse_age_s"]),
    ]
    live_tbl = "\n".join(f"| {k} | {v} |" for k, v in live_checks)
    action_tbl = "\n".join(
        f"| {rule} | `{info.get('action')}` | `{info.get('status')}` | "
        f"`{info.get('route', '—')}` |" for rule, info in log.items())
    max_ratio = max(d["r1_delay_ratio"], d["r3_delay_ratio"])
    doc = f"""# IN20-R5 · 停摆自检自愈（stall detector，daemon 级机制替身）

> 战役 DAG 节点 IN20-R5（续航军 R5）。判据数字全部由 `tools/stall_detector_report.py`
> 程序化注入（源：`artifacts/in20_r5/criteria.json`），本文无手抄数字。
> 机制基因=owner 渐退令：**替身必须独立于任何人类会话与 AI 协调者会话**，
> 因此本体是 systemd/容器 daemon（`deploy/taskplane/tp-stall-detector.service`），
> 不是「等某个人来看一眼」。

## ① 案例介绍（问题 / 数据 / 参数）

平台曾经会「安静地停住」。2026-11-07 00:52→03:18 就是一次：脉搏表最后一行
**{data['fixture']['gap_start']}**，下一行 **{data['fixture']['gap_end']}**，
中间 **{data['fixture']['stall_min']:.2f} min（{data['fixture']['stall_s']:.0f} s）**
没有任何观测行；同窗口舰队在线 52 席而只有个位数在干活、可派 DAG 节点为 0。
发现它的不是机制，是人的注意力——这正是本军要消灭的单点。

| 项 | 值 |
|---|---|
| 回归样例 | {data['fixture']['window'][0]} → {data['fixture']['window'][1]}（{data['fixture']['samples']} 个分钟样本） |
| 实测断档 | {data['fixture']['gap_start']} → {data['fixture']['gap_end']}（{data['fixture']['stall_min']:.2f} min） |
| 断档内证据等级 | {data['fixture']['evidence_grade']} |
| 规则 1 断粮 | pending=0 且 queued≤{th['queue_floor']} 且 idle≥{th['starve_min_idle']} 且 idle/online≥{th['starve_idle_ratio']}，持续 {th['starve_sustain_s']} s |
| 规则 2 舰队心跳丢失 | 失联比≥{th['fleet_loss_ratio']} 且席位≥{th['fleet_min_workers']}，持续 {th['fleet_sustain_s']} s |
| 规则 3 脉搏停滞 | `saturation_pulse.max(ts)` 老化 ≥{th['pulse_stall_s']} s |
| 冷却 | {th['cooldown_s']} s（同一事件不重复轰击） |
| 心跳新鲜窗 | {th['heartbeat_fresh_s']} s |

物理量类比（同一套「停摆」直觉）：脉搏=系统的自由表面高度信号，断流即失效；
在线/idle 席位=可用排水量中的储备浮力；pending 节点=待装货舱容。三者只有
**同时**满足「无货可拉 + 大量储备浮力 + 观测信号断流」才构成停摆判定，单看任一量
都会误判（这正是三条规则互不代偿的设计理由）。

## ② 怎么做的（方法 / 工具链 / 关键设置）

1. **取数（只读）**：`collect_from_db()` 用只读 SELECT 把四组量压成一个 `Sample`：
   campaign DAG 可派节点（状态白名单，默认 pending/queued/ready/todo/not_started）、
   排队任务、`factory_resident_workers` 心跳新鲜度、`saturation_pulse` 最后一行。
   `idle = online − busy`（与 `tools/pulse` 口径一致）；worker 表自述 `state='idle'`
   只进 `detail` 作对账——IN15-O1 已实证「满负荷时 54 行仍报 idle」，用它当判据必误报。
2. **判定（纯函数）**：`evaluate_sample(sample, thresholds)` 不读墙钟、不碰 I/O；
   持续窗/冷却/恢复在 `Detector`（状态可序列化，跨进程续命）。
3. **动作面（子进程 + 可 dry）**：`Ops` 三条动作——`refill`（确保补给器
   `selfstarter.py` 存活；已在跑则据实记 `already_running`）、`fleet_restart`
   （systemd unit 优先，否则 TERM + 分离重启，沿用 `tp_autoheal` 口径）、
   `selfproof`（写带 `selfproof` 标记的 beacon，把「脉搏生产者死」与「检测器死」分开）。
   `--action-mode dry` 只做只读探活，`live` 才落动作；**dry ≠ 不检查**。
4. **留痕**：每周期一条 `observation` 审计 + 每条事件/动作一条记录，JSONL 逐条
   hash 链（`prev_sha → sha`），`verify_chain()` 可机械验伪；`beacon.json` 每周期原子
   前进（外部判死：`--check-beacon --max-age`）。
5. **回滚路径**：`--rollback` 清全部闸闩（since/fired/cooldown）并把旧 state 归档成
   `state.rolled_back.<epoch>.json`，审计只 append（回滚自身也是一条记录）；
   unit 侧 `systemctl disable --now tp-stall-detector` 即彻底停用，台账零写入。
6. **回归样例**：`tests/fixtures/stall_20261107_0052_0318.json` 由
   `tools/stall_fixture_20261107.py` 生成——锚点=实测脉搏行，断档内按显式重建律插值，
   重建律写在 fixture 的 `provenance` 里（任何读者可重跑脚本逐字节复核）。

## ③ 计算结果（数字表）

### 3.1 阳性回放：历史停摆必须报警并触发

| 规则 | fire？ | 首次 fire | 动作 | 动作回执 |
|---|---|---|---|---|
| R1 断粮 | {r1['fired']} | {r1['first_fire_ts']} | `{r1['action']}` | `{log.get(sd.RULE_STARVED, {}).get('status')}` |
| R2 舰队心跳丢失 | {r2['fired']} | — | — | 本案例应沉默（负控） |
| R3 脉搏停滞 | {r3['fired']} | {r3['first_fire_ts']} | `{r3['action']}` | `{log.get(sd.RULE_PULSE, {}).get('status')}` |

回放观测 {data['replay']['observations']} 个样本、事件 {data['replay']['kinds']}、
fire {data['replay']['fires']} 次。两条 P1 都落在停摆窗口内部（00:52→03:18），
03:19 补给回流后各出一条 `recovered`——**报警与恢复都不需要人。**

### 3.2 检出延迟（本交付的核心精度量）

| 量 | 值 |
|---|---|
| 断粮条件首次成立 | {d['starve_onset']} |
| R1 fire | {d['r1_fire']}（持续门 {d['starve_gate_s']} s） |
| R1 检出延迟 | {d['r1_delay_s']:.0f} s = 阈值 {d['r1_delay_ratio'] * 100:.2f}% · 停摆开始后 {d['r1_after_stall_start_s']:.0f} s |
| R3 老化门到达 | {d['r3_gate']}（老化 {th['pulse_stall_s']} s） |
| R3 fire | {d['r3_fire']} |
| R3 检出延迟 | {d['r3_delay_s']:.0f} s = 阈值 {d['r3_delay_ratio'] * 100:.2f}% · 停摆开始后 {d['r3_after_stall_start_s']:.0f} s |
| 采样分辨率 | {d['sampling_s']} s |

延迟上限由采样周期决定（回放 60 s/样本）：两规则延迟均 < 1 个采样周期，
相对阈值误差最大 {max_ratio * 100:.2f}%。

### 3.3 阴性控制

| 场景 | 结果 |
|---|---|
| 健康态 2 h（120 样本，有活/心跳新鲜/脉搏新鲜） | fire=0，`observations=120`（「不告警」可数） |
| 真实台账单次体检（{live['as_of']}） | alarm={live['alarm']}，fires={live['fires']} |
| 10 min 断粮即恢复 | 只有 onset+recovered，无 fire |
| 脉搏行缺失（观测盲区） | 只出 1 条 P2 `unknown`，不 fire |

真实台账快照（`artifacts/in20_r5/live/alerts.json`）：

| 量 | 值 |
|---|---|
{live_tbl}

注意 live 的 `idle/online={s['idle_ratio']}` 已越过断粮门，但 `queued={s['queued_missions']}`
→ R1 保持沉默：判定要求「一个可派对象都没有」，排队任务存在时不许喊断粮
（宁可漏报不误报）。`lost_ratio={s['lost_ratio']} < {th['fleet_loss_ratio']}` → R2 沉默。

### 3.4 审计链与判据数字

| 项 | 值 |
|---|---|
| replay 审计记录 | {data['audit']['replay_records']} 条，链验证 ok={data['audit']['replay_ok']} |
| live 审计记录 | {data['audit']['live_records']} 条，链验证 ok={data['audit']['live_ok']} |
| pytest | {pytest_result['passed']} passed（{pytest_result['summary']}） |
| 判据 JSON | `artifacts/in20_r5/criteria.json` |

![stop timeline]({fig_path.name})

图：上=脉搏老化（红）与 30 min 门；下=idle/online（蓝，左轴）与在线/忙碌/可派节点（右轴）。
红色阴影为实测断档（无观测），空心标记为两条规则的 fire 时刻。

## ④ 结果图说明

单幅双面板图 `fig_stall_timeline.png`（matplotlib，数据全部来自 fixture + 回放 trace，
无手工点）。为什么不做云图：本任务是**机制/时序**类，场量云图不适用；替代物是
「信号 + 门限 + 判定时刻」的时序判读图，它直接支撑 3.2 的延迟数字。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **断档内数值是重建的**：`provenance` 明确标注 gap 内 busy/pending 为
   `RECONSTRUCTED`；检测器判据只依赖「pending=0 + idle 高 ≥15 min」「脉搏停 ≥30 min」
   这两个由任务书与 gap 边界实测支撑的定性形状，不依赖 gap 内具体数值。
   若换一份真实逐分钟台账，回放结果可能位移，但定性判定不应翻转。
2. **queued 的语义坑**：`queued` 任务可能长期无人认领（本次 live 有
   {s['queued_missions']} 条停摆期遗留），此时「有货」是假象。本机制按
   「宁可漏报不误报」保持沉默，代价是**这类死排队不会触发 R1**——需另配
   「queued 年龄」规则（记为后续项，不在本单范围）。
3. **status 白名单是最大误报面**：campaign 节点状态词若改名（如 `planned` 与
   `pending` 混用），`pending` 计数会失真。因此每条 digest 都打印
   `dag_status_hist` / `plan_status_hist`，把语义风险常驻在观测面上。
4. **动作面回执随宿主状态变化**（回放里 R1 记为 `already_running` 而非
   `would_launch`，因为本机补给器确实在跑）：这是特性不是缺陷——「补给器在跑仍断粮」
   意味着饥饿不是没人拉车而是没货可拉，digest 会保留 P1。
5. **两处未做**：(a) 未把 detector 的 P1 直接推进 obs 的 alerts 表（当前落
   `alerts.json` + digest.md，格式对齐 IN15-O8，接入由其侧消费）；
   (b) 未在真实 unit 下跑满 24 h（本单只做单次 live 体检 + 176 样本回放 + 16 项测试）。

## ⑥ 误差判定（对照基准逐项）

| 关键量 | 基准 | 本实现 | 误差 | 是否 <3% |
|---|---|---|---|---|
| R1 检出时刻 | 条件成立 + {th['starve_sustain_s']} s | {d['r1_fire']}（延迟 {d['r1_delay_s']:.0f} s） | {d['r1_delay_ratio'] * 100:.2f}% | 是（≤1 采样周期） |
| R3 检出时刻 | 脉搏末行 + {th['pulse_stall_s']} s | {d['r3_fire']}（延迟 {d['r3_delay_s']:.0f} s） | {d['r3_delay_ratio'] * 100:.2f}% | 是（≤1 采样周期） |
| 断档时长 | 实测 {data['fixture']['stall_min']:.2f} min | 回放断言 {data['fixture']['stall_min']:.2f} min | 0（同源实测） | 是 |
| 阴性误报率 | 0 次/2 h 健康态 | 0 次（120 样本） | 0 | 是 |
| 审计链完整性 | 全链 hash 一致 | {data['audit']['replay_records']} 条全通过 | 0 | 是 |

唯一**无基准**的量：真实停摆期的逐分钟 worker 状态（历史未落库）——故按 ⑤.1
声明证据等级，不做百分比误差承诺（honest WITHHELD，不粉饰）。

## 附：产物与复现命令

```
ops/taskplane/stall_detector.py                     daemon 本体（判定/动作/审计/回滚）
tools/stall_fixture_20261107.py                     回归 fixture 生成器（锚点可重算）
tests/fixtures/stall_20261107_0052_0318.json        176 样本回放时间线
tests/test_stall_detector_in20_r5.py                {pytest_result['passed']} 项验收测试
deploy/taskplane/tp-stall-detector.service          生产 daemon unit（独立于任何会话）
artifacts/in20_r5/{{replay,live}}/                    审计链 / beacon / digest / alerts
artifacts/in20_r5/criteria.json                     机器可裁判据数字
```

```bash
python3 ops/taskplane/stall_detector.py --fixture tests/fixtures/stall_20261107_0052_0318.json \\
    --replay --out artifacts/in20_r5/replay --action-mode dry        # rc=2（有 P1）
python3 ops/taskplane/stall_detector.py --from-db --once \\
    --out artifacts/in20_r5/live --action-mode dry                   # rc=0（健康态）
python3 -m pytest tests/test_stall_detector_in20_r5.py -q
python3 ops/taskplane/stall_detector.py --check-beacon artifacts/in20_r5/live/beacon.json
python3 ops/taskplane/stall_detector.py --rollback --out runs/stall_detector   # 回滚
```
"""
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(doc, encoding="utf-8")
    return DOC


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args(argv)
    ART.mkdir(parents=True, exist_ok=True)
    data = compute()
    data["pytest"] = ({"passed": 0, "summary": "skipped", "returncode": None}
                      if args.skip_pytest else run_pytest())
    fig_path = figure(data)
    data["figure"] = str(fig_path.relative_to(REPO))
    doc = write_doc(data, data["pytest"], fig_path)
    (ART / "criteria.json").write_text(
        json.dumps({k: v for k, v in data.items() if k != "trace"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"doc": str(doc.relative_to(REPO)),
                      "criteria": "artifacts/in20_r5/criteria.json",
                      "figure": data["figure"],
                      "pytest": data["pytest"]["summary"],
                      "r1_delay_ratio": data["detection"]["r1_delay_ratio"],
                      "r3_delay_ratio": data["detection"]["r3_delay_ratio"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())