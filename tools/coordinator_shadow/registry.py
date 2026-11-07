#!/usr/bin/env python3
"""器官字典（IN27-H1）：影子协调官判读与立单的**唯一命名空间**。

为什么要有字典
--------------
LLM 判读最常见的失效不是「说不出问题」，而是「说到一个系统里不存在的器官/指标」
（幻觉到 ``queue_latency``、``worker_saturation`` 之类）。v1 用一张**闭合**的器官/指标
表把判读语汇钉死：判读里出现表外器官/指标 → 不得立单，只能作为 advisory 文本留在
verdict 里。这样「立单」与「可复算的数字」在结构上绑在一起。

三类指标
--------
- ``probe``：SQL 探针直接算出来的计数/占比（credit_backlog / adoption_stall / declaration_rate …）；
- ``warn``：daemon WARN 流聚簇后的计数（error_total / warn_total）；
- ``chronicle``：chronicle s1–s8 面里的字段（原样引用，不在本层重算）。

方向与阈值
----------
``direction='higher_worse'`` 是「越大越病」，``lower_worse`` 反之。``warn`` / ``crit`` 是
**工程默认**阈值（可在 ``Policy`` 里注入覆盖）；它们决定 severity，从而决定「够不够格立单」。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    name: str
    organ: str
    label: str
    unit: str
    direction: str            # higher_worse | lower_worse
    warn: float | None = None
    crit: float | None = None
    zero_flow: bool = False
    source: str = "probe"

    def severity_for(self, value: float | None) -> str | None:
        """返回 ``high``/``mid``/``None``（未越界）。无定义值永远不判越界。"""
        if value is None:
            return None
        if self.direction == "higher_worse":
            if self.crit is not None and value >= self.crit:
                return "high"
            if self.warn is not None and value >= self.warn:
                return "mid"
            return None
        if self.direction == "lower_worse":
            if self.crit is not None and value <= self.crit:
                return "high"
            if self.warn is not None and value <= self.warn:
                return "mid"
            return None
        raise ValueError(f"未知方向：{self.direction}")


#: 器官（11 个）：chronicle s1–s8 的八个面 + 三个零流器官；外加脉搏鲜度与告警流两个观测面
ORGANS: dict[str, str] = {
    "s1_missions": "任务供应（入池/领单/完工吞吐）",
    "s2_campaign": "战役节点与门",
    "s3_capability": "能力增量产出",
    "s4_fleet_pulse": "舰队在位与心跳",
    "s5_outcomes": "成果评价（S×E）",
    "s6_daemons": "守护进程健康",
    "s7_voice": "声音/声明通道",
    "s8_harness": "执行框架事故",
    "credit_backlog": "信用积压（零流：定谳未入账）",
    "adoption_stall": "收养停滞（零流：入池无人领）",
    "declaration_rate": "声明率（零流：完工未声明）",
    "pulse_freshness": "脉搏鲜度（chronicle 年龄）",
    "daemon_warns": "守护告警流（WARN/ERROR 聚簇）",
}

#: 指标台账：判读只能引用这里的名字（``metric`` 字段）
METRICS: dict[str, MetricSpec] = {
    m.name: m
    for m in (
        # ── 零流器官一：信用积压 ───────────────────────────────────────
        MetricSpec("credit_backlog", "credit_backlog", "定谳未入账评价条数", "evals",
                   "higher_worse", warn=20, crit=50, zero_flow=True),
        # ── 零流器官二：收养停滞 ───────────────────────────────────────
        MetricSpec("oldest_queued_minutes", "adoption_stall", "最老未领任务年龄", "minutes",
                   "higher_worse", warn=120, crit=240, zero_flow=True),
        MetricSpec("queued_stuck_2h", "adoption_stall", "入池超 2h 未领条数", "missions",
                   "higher_worse", warn=5, crit=15, zero_flow=True),
        MetricSpec("queued_open", "adoption_stall", "队列存量", "missions",
                   "higher_worse", warn=25, crit=60),
        # ── 零流器官三：声明率 ─────────────────────────────────────────
        MetricSpec("declaration_rate", "declaration_rate", "完工任务声明占比", "ratio",
                   "lower_worse", warn=0.60, crit=0.40, zero_flow=True),
        # ── 舰队/供应 ─────────────────────────────────────────────────
        MetricSpec("running", "s4_fleet_pulse", "在用执行器数", "missions",
                   "lower_worse", warn=1.0, crit=0.5),
        MetricSpec("awaiting_pr_merge", "s4_fleet_pulse", "待并 PR 存量", "missions",
                   "higher_worse", warn=15, crit=40),
        MetricSpec("capability_deltas_24h", "s3_capability", "24h 能力增量", "deltas",
                   "lower_worse", warn=1.0, crit=0.5, zero_flow=True),
        # ── 战役 ─────────────────────────────────────────────────────
        MetricSpec("campaign_blocked", "s2_campaign", "阻塞节点数", "nodes",
                   "higher_worse", warn=5, crit=12),
        MetricSpec("campaign_pending", "s2_campaign", "待启动节点数", "nodes",
                   "higher_worse", warn=40, crit=80),
        # ── 脉搏鲜度（自观测） ─────────────────────────────────────────
        MetricSpec("chronicle_age_minutes", "pulse_freshness", "chronicle 脉搏年龄", "minutes",
                   "higher_worse", warn=60, crit=90, source="probe"),
        # ── 守护告警流 ────────────────────────────────────────────────
        MetricSpec("error_total", "daemon_warns", "窗口内 ERROR 行数", "lines",
                   "higher_worse", warn=3, crit=10, source="warn", zero_flow=True),
        MetricSpec("warn_total", "daemon_warns", "窗口内 WARN 行数", "lines",
                   "higher_worse", warn=12, crit=30, source="warn"),
    )
}


def spec(name: str) -> MetricSpec | None:
    return METRICS.get(name)


def organ_of(metric: str) -> str | None:
    s = METRICS.get(metric)
    return None if s is None else s.organ


def known_metric(name: str) -> bool:
    return name in METRICS


def zero_flow_metrics() -> tuple[str, ...]:
    return tuple(m for m, s in METRICS.items() if s.zero_flow)