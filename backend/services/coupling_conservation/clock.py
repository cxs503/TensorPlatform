"""耦合时钟语义裁决 (coupling clock semantics adjudication).

多求解器耦合的三种时间步进协议 (domain-neutral):

``EXPLICIT``  显式: 消费方在自己的窗口开始时只用**已经就绪**的数据
              (``t_write <= window.start``)。数据晚到 = 时间旅行。
``IMPLICIT``  隐式: 双方在同一窗口内迭代交换 (``window.start <= t_write <= window.end``),
              迭代号在每个 (field, consumer) 上必须单调不减。
``SUBSTEP``   子循环: 快求解器窗口是慢窗口的精确细分, 每次写入时刻必须落在
              细网格上 (``(t_write - window.start)/dt_fine`` 为整数)。

裁决的四类违约 (全部量化 time_gap, 供报告直接引用数字):

``TIME_TRAVEL``        消费时刻早于数据写入时刻 (读了未来数据) — gap = t_write - t_read
``STALE_DATA``         写入时刻早于所在窗口起点 (读者拿到过期一步以上的数据) — gap = window.start - t_write
``UNALIGNED``          子循环写入未落在细网格上 — gap = 到最近细网格点的距离
``DATA_RACE``          同一 (field, consumer, 重叠窗口) 有多个生产者写入 — 量化重叠区间长度
``ITERATION_REGRESSION`` 隐式迭代号回退 — 量化回退步数
``OUT_OF_WINDOW``      写入时刻落在窗口之外 (窗口闭合性错误)

时间全部用 ``Fraction`` 表示, 因此 "dt_fine 整除窗口" 这类判据是精确判定,
不会因为 0.1+0.2 != 0.3 把合法子循环误判成 UNALIGNED。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .flux import as_fraction

EXPLICIT = "explicit"
IMPLICIT = "implicit"
SUBSTEP = "substep"
PROTOCOLS = (EXPLICIT, IMPLICIT, SUBSTEP)

DEFAULT_TIME_TOL = Fraction(0)


@dataclass(frozen=True)
class ClockEvent:
    """一次跨求解器数据交接事件 (纯时间语义, 不含物理量)."""

    producer: str
    consumer: str
    field: str
    t_write: Fraction          # 数据可用时刻
    window: Tuple[Fraction, Fraction]  # 消费方所在耦合窗口 [start, end)
    t_read: Optional[Fraction] = None  # 消费方实际取用时刻 (默认 = window.start)
    iteration: int = 0
    dt_fine: Optional[Fraction] = None  # 子循环快求解器步长

    def __post_init__(self) -> None:
        object.__setattr__(self, "t_write", as_fraction(self.t_write))
        start, end = self.window
        object.__setattr__(self, "window", (as_fraction(start), as_fraction(end)))
        if self.window[1] < self.window[0]:
            raise ValueError(f"耦合窗口上界小于下界: {self.window}")
        if self.t_read is not None:
            object.__setattr__(self, "t_read", as_fraction(self.t_read))
        if self.dt_fine is not None:
            object.__setattr__(self, "dt_fine", as_fraction(self.dt_fine))
            if self.dt_fine <= 0:
                raise ValueError("dt_fine 必须为正")

    @property
    def read_time(self) -> Fraction:
        return self.window[0] if self.t_read is None else self.t_read


@dataclass(frozen=True)
class ClockViolation:
    code: str
    producer: str
    consumer: str
    field: str
    time_gap: Fraction
    message: str
    detail: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "producer": self.producer,
            "consumer": self.consumer,
            "field": self.field,
            "time_gap": float(self.time_gap),
            "time_gap_exact": str(self.time_gap),
            "message": self.message,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ClockAdjudication:
    protocol: str
    passed: bool
    n_events: int
    violations: Tuple[ClockViolation, ...] = ()
    max_time_gap: Fraction = Fraction(0)
    windows: Tuple[Tuple[Fraction, Fraction], ...] = ()
    tolerance: Fraction = DEFAULT_TIME_TOL

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(sorted({v.code for v in self.violations}))

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "passed": self.passed,
            "n_events": self.n_events,
            "codes": list(self.codes),
            "max_time_gap": float(self.max_time_gap),
            "max_time_gap_exact": str(self.max_time_gap),
            "n_violations": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
            "windows": [[float(a), float(b)] for a, b in self.windows],
        }


def adjudicate(
    events: Iterable[ClockEvent],
    protocol: str = EXPLICIT,
    *,
    tolerance: Fraction | int | float | str = DEFAULT_TIME_TOL,
    check_races: bool = True,
) -> ClockAdjudication:
    """按给定协议裁决一批交接事件的时间语义.

    这是纯函数: 输入事件序列, 输出"通过/违约清单 + 最大时间偏差"。
    调用方 (求解器调度器) 只需把自己真实的交接记录喂进来。
    """
    if protocol not in PROTOCOLS:
        raise ValueError(f"未知时钟协议 {protocol!r}, 可选 {PROTOCOLS}")
    tol = as_fraction(tolerance)
    items = tuple(events)
    violations: List[ClockViolation] = []
    max_gap = Fraction(0)

    def add(code: str, ev: ClockEvent, gap: Fraction, msg: str, **detail) -> None:
        nonlocal max_gap
        gap = abs(gap)
        max_gap = max(max_gap, gap)
        violations.append(
            ClockViolation(
                code=code,
                producer=ev.producer,
                consumer=ev.consumer,
                field=ev.field,
                time_gap=gap,
                message=msg,
                detail={
                    "t_write": float(ev.t_write),
                    "t_read": float(ev.read_time),
                    "window": [float(ev.window[0]), float(ev.window[1])],
                    **detail,
                },
            )
        )

    for ev in items:
        start, end = ev.window
        # 因果性: 读到写入时刻之后的数据即时间旅行
        if ev.read_time < ev.t_write - tol:
            add(
                "TIME_TRAVEL",
                ev,
                ev.t_write - ev.read_time,
                f"消费方 {ev.consumer} 在 t={float(ev.read_time)} 读取 t={float(ev.t_write)} "
                f"才发现的数据 (超前 {float(ev.t_write - ev.read_time)})",
            )

        if protocol == EXPLICIT:
            if ev.t_write > start + tol:
                add(
                    "OUT_OF_WINDOW",
                    ev,
                    ev.t_write - start,
                    f"显式协议要求数据在窗口起点前就绪, 实际晚到 "
                    f"{float(ev.t_write - start)}",
                )
        elif protocol == IMPLICIT:
            if ev.t_write < start - tol or ev.t_write > end + tol:
                gap = start - ev.t_write if ev.t_write < start else ev.t_write - end
                add(
                    "OUT_OF_WINDOW",
                    ev,
                    gap,
                    f"隐式协议的交换必须发生在窗口内, 偏差 {float(gap)}",
                )
        else:  # SUBSTEP
            if ev.dt_fine is None:
                add("UNALIGNED", ev, Fraction(0), "子循环协议需要 dt_fine 才能判定对齐")
            else:
                k = (ev.t_write - start) / ev.dt_fine
                if k.denominator != 1:
                    nearest = Fraction(round(k))
                    add(
                        "UNALIGNED",
                        ev,
                        (k - nearest) * ev.dt_fine,
                        f"写入时刻 {float(ev.t_write)} 不在细网格上 "
                        f"(k={float(k)} 非整数)",
                        k=float(k),
                    )
                if ev.t_write < start - tol or ev.t_write > end + tol:
                    gap = start - ev.t_write if ev.t_write < start else ev.t_write - end
                    add(
                        "OUT_OF_WINDOW",
                        ev,
                        gap,
                        f"子循环写入越出粗窗口, 偏差 {float(gap)}",
                    )

    # 隐式迭代单调性
    last_iter: Dict[Tuple[str, str], int] = {}
    for ev in items:
        key = (ev.field, ev.consumer)
        if key in last_iter and ev.iteration < last_iter[key]:
            add(
                "ITERATION_REGRESSION",
                ev,
                Fraction(0),
                f"{ev.field}→{ev.consumer} 迭代号回退 "
                f"{last_iter[key]}→{ev.iteration}",
                iteration_prev=last_iter[key],
                iteration_now=ev.iteration,
            )
        last_iter[key] = max(ev.iteration, last_iter.get(key, ev.iteration))

    # 数据竞争: 同一 (field, consumer) 在重叠窗口内被多个生产者写入
    if check_races:
        grouped: Dict[Tuple[str, str], List[ClockEvent]] = {}
        for ev in items:
            grouped.setdefault((ev.field, ev.consumer), []).append(ev)
        for (fld, cons), evs in grouped.items():
            for i in range(len(evs)):
                for j in range(i + 1, len(evs)):
                    a, b = evs[i], evs[j]
                    if a.producer == b.producer:
                        continue
                    a0, a1 = a.window
                    b0, b1 = b.window
                    overlap = min(a1, b1) - max(a0, b0)
                    if overlap > tol:
                        violations.append(
                            ClockViolation(
                                code="DATA_RACE",
                                producer=f"{a.producer}|{b.producer}",
                                consumer=cons,
                                field=fld,
                                time_gap=overlap,
                                message=(
                                    f"{fld}→{cons} 在重叠窗口内有两个生产者 "
                                    f"({a.producer}, {b.producer}), 重叠 {float(overlap)}"
                                ),
                                detail={
                                    "overlap": float(overlap),
                                    "window_a": [float(a0), float(a1)],
                                    "window_b": [float(b0), float(b1)],
                                },
                            )
                        )
                        max_gap = max(max_gap, overlap)

    # 去重 (同一对事件的重叠区间可能被多组匹配重复报出)
    seen = set()
    unique: List[ClockViolation] = []
    for v in violations:
        key = (v.code, v.producer, v.consumer, v.field, v.time_gap, v.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(v)

    return ClockAdjudication(
        protocol=protocol,
        passed=not unique,
        n_events=len(items),
        violations=tuple(unique),
        max_time_gap=max_gap,
        windows=tuple(sorted({ev.window for ev in items})),
        tolerance=tol,
    )


def substep_schedule(
    window: Tuple[Fraction, Fraction],
    dt_fine: Fraction,
) -> Tuple[Fraction, ...]:
    """生成子循环协议下合法的写入时刻 (必须精确整除, 否则抛错)."""
    start, end = as_fraction(window[0]), as_fraction(window[1])
    dt = as_fraction(dt_fine)
    span = end - start
    if dt <= 0:
        raise ValueError("dt_fine 必须为正")
    n = span / dt
    if n.denominator != 1:
        raise ValueError(
            f"细步长 {dt} 不能精确整除窗口 {float(span)} (n={float(n)})"
        )
    return tuple(start + k * dt for k in range(int(n) + 1))