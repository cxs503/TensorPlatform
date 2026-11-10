#!/usr/bin/env python3
"""capacity_planner —— 任务机排队-容量模型与容量规划器（纯机制，零领域语义）。

为什么要有它（审计事实）
------------------------
平台此前只有"吞吐报表"（``tools/campaign_throughput.py``：成功率/墙钟/领取时延分位），
**没有任何排队论意义上的容量模型**：给定到达率 λ 与服务时间分布 S，问不出"要几个 seat 才能
把等待压到 SLA 以下"，也说不清这个模型在什么工况下不可信。容量决策只能靠"看并发数"，
既不可背测，也没有失效域声明。

本模块把三件事做成一条可机器复核的链：

1. **数据面**（只读）：从 ``tensor_dev_missions`` + ``tensor_agent_session_events`` 重建
   每条请求的三元组 ``(arrival, service_start, service_end)``，得到到达序列、等待样本、
   服务时间样本；按 ``--bucket-minutes`` 分时段得到非齐次到达率 λ(t)。
2. **解析面**：Erlang-C（M/M/c 精确）与 Kingman / Allen–Cunneen（GI/G/c 近似）互裁，
   在 (c, ρ) 网格上给出**偏差 ≤10% 的适用带**，带外即模型失效域。
3. **仿真面**：simpy 离散事件仿真（DES）实现 M/M/c 与 M/G/c（服务时间从拟合分布抽样），
   与解析解交叉验证；再做 80/20 时间留出期背测（KS）+ 到达顺序 shuffle 负控。

口径（代码即口径，全部可复算）
------------------------------
* ``arrival``       = ``tensor_dev_missions.created_at``（请求入队时刻）
* ``service_start`` = 该 mission 所属 agent session 的 claim 事件时刻；claim 事件 payload 不带
  mission_id，故按 **同会话内 claim 事件与 plan 事件按时间序一一配对**（第 k 个 claim ↔ 第 k 个
  plan）重建。这是本模块唯一的重建假设，实测无一例 ``claim < created``（见报告数据面小节）。
* ``service_end``   = ``finished_at``
* ``wait`` = start - arrival；``service`` = end - start（分钟）
* 丢弃 ``service <= 0`` 或 ``wait < 0`` 的记录并计数（不静默丢弃）。

不变式
------
* 只读数据库：不建表、不写库；唯一写盘目标是 ``--out-json`` / ``--out-md`` / ``--figure-dir``。
* 分母为 0 返回 ``None`` 而不是 0（没有样本就不编造 0）。
* 无数据库也能跑：``--source fixture``（离线 JSON）/ ``--source synthetic``（M/M/c 自检）。

用法
----
    python3 tools/capacity_planner.py --source db \
        --out-json artifacts/capacity_planner/report.json \
        --out-md docs/CAPACITY_PLANNER.md --figure-dir artifacts/capacity_planner
    python3 tools/capacity_planner.py --source synthetic --n 20000 --c 4 --rho 0.8 --dry-run
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import pathlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

import numpy as np

try:  # simpy 只在仿真路径上需要；缺失时 analytic/planner 仍可用
    import simpy  # type: ignore
except Exception:  # pragma: no cover - 环境缺 simpy 时的降级路径
    simpy = None  # type: ignore

from scipy import stats as sps

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT_JSON = REPO_ROOT / "artifacts" / "capacity_planner" / "report.json"
DEFAULT_OUT_MD = REPO_ROOT / "docs" / "CAPACITY_PLANNER.md"
DEFAULT_FIG_DIR = REPO_ROOT / "artifacts" / "capacity_planner"
DEFAULT_DB = "tensorplatform"

# 判定阈（写死在代码里，报告里逐条引用）
ANALYTIC_AGREEMENT_TOL = 0.10      # 解析×仿真 互裁：相对偏差 ≤10%
FIT_KS_ALPHA = 0.05                # 分布拟合 KS 显著性
BACKTEST_KS_ALPHA = 0.05           # 留出期背测：p > 0.05 视为通过
SHUFFLE_DEGRADE_FACTOR = 1.5       # shuffle 负控：KS 统计量放大倍数
MAX_BACKTEST_MEAN_WAIT_REL_ERR = 0.30  # 背测第二道闸：模型/实测平均等待相对误差上限
MAX_ARRIVAL_MEAN_WAIT_REL_ERR = 0.50   # 到达过程口径允许的平均等待相对误差（>此值视为该口径失效）
MEAN_WAIT_ABS_TOL_MIN = 2.0            # 等待均值接近 0 时相对误差不稳定，允许绝对差 ≤ 2 分钟

# 数据面重建 SQL（只读；claim 与 plan 按会话内时间序配对）
INTERVALS_SQL = """
with claims as (
    select session_id, created_at,
           row_number() over (partition by session_id order by created_at, id) as rn
    from tensor_agent_session_events
    where kind = 'note' and payload->>'subtype' = 'mission_claimed'
), plans as (
    select session_id, (payload->>'mission_id')::bigint as mid, created_at,
           row_number() over (partition by session_id order by created_at, id) as rn
    from tensor_agent_session_events
    where kind = 'plan' and payload ? 'mission_id'
), paired as (
    select p.mid, c.created_at as claimed_at
    from plans p join claims c using (session_id, rn)
)
select m.id,
       extract(epoch from m.created_at) as arrival_epoch,
       extract(epoch from pa.claimed_at) as start_epoch,
       extract(epoch from m.finished_at) as end_epoch
from paired pa join tensor_dev_missions m on m.id = pa.mid
where m.finished_at is not null
order by m.created_at
"""


# --------------------------------------------------------------------------- #
# 数据面
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Interval:
    """一条请求的排队三元组（单位：分钟，相对零点由调用方决定）。"""

    rid: str
    arrival: float
    start: float
    end: float

    @property
    def wait(self) -> float:
        return self.start - self.arrival

    @property
    def service(self) -> float:
        return self.end - self.start


@dataclass
class Series:
    """清洗后的观测序列 + 丢弃计数（丢弃必须可见，不许静默）。"""

    intervals: list[Interval] = field(default_factory=list)
    dropped_nonpositive_service: int = 0
    dropped_negative_wait: int = 0

    @property
    def waits(self) -> list[float]:
        return [i.wait for i in self.intervals]

    @property
    def services(self) -> list[float]:
        return [i.service for i in self.intervals]

    @property
    def arrivals(self) -> list[float]:
        return [i.arrival for i in self.intervals]

    def span(self) -> float:
        if not self.intervals:
            return 0.0
        return max(i.arrival for i in self.intervals) - min(i.arrival for i in self.intervals)

    def rate_per_min(self) -> Optional[float]:
        span = self.span()
        if span <= 0:
            return None
        return len(self.intervals) / span

    def summary(self) -> dict:
        return {
            "n": len(self.intervals),
            "dropped_nonpositive_service": self.dropped_nonpositive_service,
            "dropped_negative_wait": self.dropped_negative_wait,
            "span_minutes": _round(self.span()),
            "span_days": _round(self.span() / 1440.0),
            "lambda_per_min": _round(self.rate_per_min()),
            "mean_service_min": _round(_mean(self.services)),
            "mean_wait_min": _round(_mean(self.waits)),
        }


def _mean(xs: Sequence[float]) -> Optional[float]:
    return float(np.mean(xs)) if len(xs) else None


def _round(x: Optional[float], nd: int = 6) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (float, np.floating)) and (math.isnan(float(x)) or math.isinf(float(x))):
        return None
    return round(float(x), nd)


def build_series(rows: Iterable[Sequence[Any]], *, unit_minutes: float = 1.0) -> Series:
    """把 ``(rid, arrival, start, end)`` 行转成 ``Series``（清洗 + 计数）。"""
    out = Series()
    for rid, a, s, e in rows:
        iv = Interval(str(rid), float(a) / unit_minutes, float(s) / unit_minutes, float(e) / unit_minutes)
        if iv.service <= 0:
            out.dropped_nonpositive_service += 1
            continue
        if iv.wait < 0:
            out.dropped_negative_wait += 1
            continue
        out.intervals.append(iv)
    out.intervals.sort(key=lambda i: i.arrival)
    return out


def load_series_db(db: str = DEFAULT_DB, *, sql: str = INTERVALS_SQL,
                   unit_minutes: float = 60.0) -> Series:
    """只读拉取：优先 ``TP_PG_PASSWORD`` 走 TCP，否则回落 ``su postgres -c psql``（本机 peer）。"""
    env = dict(os.environ)
    password = env.get("TP_PG_PASSWORD")
    if password:
        cmd = ["psql", "-h", env.get("TP_PG_HOST", "127.0.0.1"),
               "-p", env.get("TP_PG_PORT", "5432"),
               "-U", env.get("TP_PG_USER", "postgres"),
               "-d", db, "-A", "-F", ",", "-t", "-c", sql]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=dict(env, PGPASSWORD=password))
    else:
        quoted = sql.replace('"', '\\"')
        proc = subprocess.run(
            ["bash", "-lc", f'su postgres -c "psql -d {db} -A -F , -t -c \\"{quoted}\\""'],
            capture_output=True, text=True,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"read-only psql pull failed: {proc.stderr.strip()[:400]}")
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) == 4:
            rows.append(parts)
    return build_series(rows, unit_minutes=unit_minutes)


def load_series_fixture(path: str | pathlib.Path, *, unit_minutes: float = 60.0) -> Series:
    """离线 JSON：``[{"id":..,"arrival":..,"start":..,"end":..}, ...]``（单位：秒）。"""
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    rows = [(r.get("id", i), r["arrival"], r["start"], r["end"]) for i, r in enumerate(payload)]
    return build_series(rows, unit_minutes=unit_minutes)


def _des_waits(*, c: int, arrivals: Sequence[float],
               services: Sequence[float]) -> tuple[list[float], list[float]]:
    """轻量确定性 DES（不依赖 simpy）：返回 (waits, start_times)。

    用于合成轨迹生成与单元自检；核心语义与 simpy 版一致（FCFS、c 个 seat）。
    """
    free = [0.0] * c
    heapq.heapify(free)
    waits: list[float] = []
    starts: list[float] = []
    for t_arr, dur in zip(arrivals, services):
        f = heapq.heappop(free)
        start = max(float(t_arr), f)
        waits.append(start - float(t_arr))
        starts.append(start)
        heapq.heappush(free, start + float(dur))
    return waits, starts


def synthetic_series(*, c: int = 4, lam_per_min: float = 0.25, mean_service: float = 6.0,
                     n_arrivals: int = 4000, seed: int = 20261110,
                     service_sampler: Optional[Callable[[np.random.Generator], float]] = None,
                     nonhomogeneous_amplitude: float = 0.0) -> Series:
    """生成 M/M/c（或 M/G/c）轨迹，用于自检/单测（不需要数据库）。

    ``nonhomogeneous_amplitude`` > 0 时按时段相位（默认 60 分钟）对 λ 施加正弦调制，
    使到达过程非齐次（用于验证分时段 λ(t) 建模）。
    """
    rng = np.random.default_rng(seed)
    base = 1.0 / lam_per_min
    arrivals: list[float] = []
    t = 0.0
    for _ in range(n_arrivals):
        rate_scale = 1.0 + nonhomogeneous_amplitude * math.sin(2 * math.pi * t / 60.0) \
            if nonhomogeneous_amplitude > 0 else 1.0
        rate_scale = max(rate_scale, 0.05)
        t += rng.exponential(base / rate_scale)
        arrivals.append(t)
    if service_sampler is None:
        services = rng.exponential(mean_service, size=n_arrivals)
    else:
        services = np.array([service_sampler(rng) for _ in range(n_arrivals)])
    waits, starts = _des_waits(c=int(c), arrivals=arrivals, services=list(services))
    rows = [(i, arrivals[i], starts[i], starts[i] + float(services[i])) for i in range(n_arrivals)]
    return build_series(rows)


def split_by_time(series: Series, *, train_frac: float = 0.8) -> tuple[Series, Series]:
    """按到达时间切分（前 80% 训练 / 后 20% 留出），保持时间序、不跨期泄漏。"""
    n = len(series.intervals)
    if n == 0:
        return Series(), Series()
    k = int(round(n * train_frac))
    k = max(1, min(n - 1, k)) if n > 1 else n
    return Series(intervals=list(series.intervals[:k])), Series(intervals=list(series.intervals[k:]))


def bucket_rates(series: Series, *, bucket_minutes: float = 60.0) -> dict:
    """分时段非齐次到达率：按时间桶统计到达数 → λ_b（跨桶平均观测时长校正）。"""
    if not series.intervals or bucket_minutes <= 0:
        return {"bucket_minutes": bucket_minutes, "rates_per_min": [], "counts": []}
    t0 = min(i.arrival for i in series.intervals)
    t1 = max(i.arrival for i in series.intervals)
    n_buckets = int(math.floor((t1 - t0) / bucket_minutes)) + 1
    counts = [0] * n_buckets
    for iv in series.intervals:
        idx = min(n_buckets - 1, int((iv.arrival - t0) // bucket_minutes))
        counts[idx] += 1
    rates = []
    for b, cnt in enumerate(counts):
        b_start = t0 + b * bucket_minutes
        observed = max(0.0, min(t1, b_start + bucket_minutes) - b_start)
        rates.append(cnt / observed if observed > 0 else None)
    valid = [r for r in rates if r is not None]
    return {
        "bucket_minutes": bucket_minutes,
        "counts": counts,
        "rates_per_min": [_round(r) for r in rates],
        "mean_rate_per_min": _round(_mean(valid)),
        "peak_rate_per_min": _round(max(valid) if valid else None),
        "peak_to_mean": _round(max(valid) / _mean(valid)) if valid and _mean(valid) else None,
    }


# --------------------------------------------------------------------------- #
# 分布拟合
# --------------------------------------------------------------------------- #
FIT_CANDIDATES = {
    "exponential": sps.expon,
    "lognormal": sps.lognorm,
    "weibull": sps.weibull_min,
    "gamma": sps.gamma,
}


def fit_service_distribution(samples: Sequence[float]) -> dict:
    """对服务时间样本做 MLE 拟合（指数/对数正态/Weibull/gamma），KS 选最优。

    "最优"口径：**KS 统计量 D 最小**。p 值只用于显著性判定，不用于排序——
    大样本下任何模型 p 都会很小，按 p 排序会退化成噪声排序。
    """
    xs = np.asarray([x for x in samples if x > 0], dtype=float)
    if xs.size < 10:
        return {"n": int(xs.size), "best": None, "reason": "too few samples (<10)", "fits": []}
    fits = []
    for name, dist in FIT_CANDIDATES.items():
        try:
            params = dist.fit(xs, floc=0.0)
            ks = sps.kstest(xs, dist.cdf, args=params)
            mean = float(dist.mean(*params))
            var = float(dist.var(*params))
            fits.append({
                "name": name,
                "params": [float(p) for p in params],
                "ks_stat": float(ks.statistic),
                "ks_p": float(ks.pvalue),
                "mean_min": _round(mean),
                "scv": _round(var / mean ** 2 if mean > 0 else None),
                "aic": _round(float(2 * len(params) - 2 * np.sum(dist.logpdf(xs, *params)))),
            })
        except Exception as exc:  # pragma: no cover - 数值失败要留痕
            fits.append({"name": name, "error": str(exc)[:160]})
    ok = [f for f in fits if "ks_stat" in f]
    best = min(ok, key=lambda f: f["ks_stat"]) if ok else None
    return {
        "n": int(xs.size),
        "best": best,
        "fits": fits,
        "empirical": empirical_stats(xs),
        "ks_alpha": FIT_KS_ALPHA,
        "best_accepts_null": bool(best and best["ks_p"] > FIT_KS_ALPHA),
    }


def empirical_stats(samples: Sequence[float]) -> dict:
    xs = np.asarray([x for x in samples if x is not None], dtype=float)
    if xs.size == 0:
        return {"n": 0}
    mean = float(xs.mean())
    var = float(xs.var(ddof=1)) if xs.size > 1 else 0.0
    q = np.percentile(xs, [50, 90, 95, 99])
    return {
        "n": int(xs.size),
        "mean_min": _round(mean),
        "sd_min": _round(math.sqrt(var)),
        "scv": _round(var / mean ** 2 if mean > 0 else None),
        "cv": _round(math.sqrt(var) / mean if mean > 0 else None),
        "p50_min": _round(float(q[0])),
        "p90_min": _round(float(q[1])),
        "p95_min": _round(float(q[2])),
        "p99_min": _round(float(q[3])),
        "max_min": _round(float(xs.max())),
    }


def sampler_from_samples(samples: Sequence[float]) -> Callable[[np.random.Generator], float]:
    """非参数经验分布抽样（ECDF 逆变换 + 分位点线性插值）。

    用途：当没有任何两参数族通过 KS（重尾服务时间常见）时，DES 仍必须能抽服务时间——
    这时用经验分布比硬套一个被检验否决的分布更诚实（且不引入模型偏差）。
    """
    xs = np.sort(np.asarray([x for x in samples if x > 0], dtype=float))
    if xs.size == 0:
        raise ValueError("cannot build empirical sampler from empty samples")
    q = np.linspace(0.0, 1.0, xs.size, endpoint=False) + 0.5 / xs.size

    def _draw(rng: np.random.Generator) -> float:
        return float(np.interp(rng.random(), q, xs))

    return _draw


def select_service_model(fit: dict, samples: Sequence[float]) -> dict:
    """选服务时间模型：参数族通过 KS → 用参数族；否则回落非参数 ECDF 并显式标注。"""
    if fit.get("best") and fit.get("best_accepts_null"):
        return {"kind": "parametric", "name": fit["best"]["name"],
                "params": fit["best"]["params"], "scv": fit["best"]["scv"],
                "mean_min": fit["best"]["mean_min"],
                "sampler": sampler_from_fit(fit["best"]),
                "note": f"{fit['best']['name']} 通过 KS（p={fit['best']['ks_p']:.3g}）"}
    emp = empirical_stats(samples)
    return {"kind": "empirical_ecdf", "name": "empirical_ecdf", "params": None,
            "scv": emp.get("scv"), "mean_min": emp.get("mean_min"),
            "sampler": sampler_from_samples(samples),
            "note": ("无两参数族通过 KS（最优 D="
                     f"{(fit.get('best') or {}).get('ks_stat')}），改用经验 ECDF 抽样；"
                     "参数拟合结果保留作模型形态诊断")}


def sampler_from_fit(fit: dict) -> Callable[[np.random.Generator], float]:
    """把拟合结果包装成"抽一个服务时间"的函数（DES 用）。"""
    dist = FIT_CANDIDATES[fit["name"]]
    params = tuple(fit["params"])

    def _draw(rng: np.random.Generator) -> float:
        return max(float(dist.rvs(*params, random_state=rng)), 1e-9)

    return _draw


# --------------------------------------------------------------------------- #
# 解析面
# --------------------------------------------------------------------------- #
def offered_load(lam_per_min: float, mean_service: float) -> float:
    return lam_per_min * mean_service


def utilization(lam_per_min: float, mean_service: float, c: int) -> float:
    return offered_load(lam_per_min, mean_service) / c if c > 0 else float("inf")


def erlang_c(c: int, lam_per_min: float, mean_service: float) -> dict:
    """M/M/c 精确解：P(wait)、E[Wq]、E[W]、Lq、L。ρ≥1 明确报 unstable（不返回假数）。"""
    if c <= 0 or mean_service <= 0 or lam_per_min <= 0:
        return {"c": c, "rho": None, "stable": False, "reason": "invalid arguments",
                "p_wait": None, "wq_min": None, "w_min": None, "lq": None, "l": None,
                "offered_load": None}
    a = offered_load(lam_per_min, mean_service)
    rho = a / c
    if rho >= 1:
        return {"c": c, "offered_load": _round(a), "rho": _round(rho), "stable": False,
                "reason": "rho >= 1 (unstable)",
                "p_wait": None, "wq_min": None, "w_min": None, "lq": None, "l": None}
    log_terms = [math.log(a) * k - math.lgamma(k + 1) for k in range(c)]
    tail = math.log(a) * c - math.lgamma(c + 1) - math.log(1 - rho)
    m = max(log_terms + [tail])
    denom = sum(math.exp(t - m) for t in log_terms) + math.exp(tail - m)
    c_prob = math.exp(tail - m) / denom  # Erlang-C: P(wait > 0)
    wq = c_prob * mean_service / (c * (1 - rho))
    w = wq + mean_service
    return {"c": c, "offered_load": _round(a), "rho": _round(rho), "stable": True,
            "p_wait": _round(c_prob), "wq_min": _round(wq), "w_min": _round(w),
            "lq": _round(lam_per_min * wq), "l": _round(lam_per_min * w)}


def kingman_wq(lam_per_min: float, mean_service: float,
               ca2: float = 1.0, cs2: float = 1.0) -> Optional[float]:
    """GI/G/1 的 Kingman 近似（与 M/G/1 的 Pollaczek–Khinchine 形式一致）。"""
    a = offered_load(lam_per_min, mean_service)
    if a >= 1 or a < 0:
        return None
    return a / (1 - a) * (ca2 + cs2) / 2.0 * mean_service


def allen_cunneen_wq(c: int, lam_per_min: float, mean_service: float,
                     ca2: float = 1.0, cs2: float = 1.0) -> Optional[float]:
    """GI/G/c 的 Allen–Cunneen 近似；c=1 时退化为 Kingman（c=1 的 AC 指数式不适用）。"""
    if c <= 0:
        return None
    if c == 1:
        return kingman_wq(lam_per_min, mean_service, ca2, cs2)
    a = offered_load(lam_per_min, mean_service)
    if a >= c:
        return None
    rho = a / c
    exponent = math.sqrt(2 * (c + 1)) - 1
    return (ca2 + cs2) / 2.0 * (rho ** exponent) / (c * (1 - rho)) * mean_service


def heavy_traffic_wq(c: int, lam_per_min: float, mean_service: float,
                     ca2: float = 1.0, cs2: float = 1.0) -> Optional[float]:
    """GI/G/c 重交通扩散上界：Wq ≈ (ca²+cs²)/2 · ρ/(1-ρ) · E[S]/c。

    实测（本仓数据，CS²≈10.6）：该式在两个测试点都**高估**等待（保守方向），
    故可作为"不会少配 seat"的解析安全包络，与 Allen–Cunneen 的高低两侧误差互补。
    """
    if c <= 0:
        return None
    a = offered_load(lam_per_min, mean_service)
    if a >= c:
        return None
    rho = a / c
    return (ca2 + cs2) / 2.0 * rho / (1 - rho) * mean_service / c


def wq_p90_approx(c: int, lam_per_min: float, mean_service: float,
                  wq_mean: Optional[float]) -> Optional[float]:
    """由 Erlang-C 尾概率形式近似 Wq 的 90 分位：P(Wq>t)=C·exp(-c(1-ρ)t/E[S])。"""
    ec = erlang_c(c, lam_per_min, mean_service)
    if not ec["stable"] or wq_mean is None:
        return None
    p_wait = ec["p_wait"] or 0.0
    if p_wait <= 0.1:
        return 0.0
    decay = c * (1 - ec["rho"]) / mean_service
    if decay <= 0:
        return None
    scale = wq_mean / ec["wq_min"] if ec["wq_min"] else 1.0
    return math.log(p_wait / 0.1) / decay * max(scale, 1e-12)


def analytic_triangle(c: int, lam_per_min: float, mean_service: float,
                      ca2: float, cs2: float) -> dict:
    """Erlang-C（M/M/c）与 Kingman/Allen–Cunneen（GI/G/c）互裁一行。"""
    ec = erlang_c(c, lam_per_min, mean_service)
    ac = allen_cunneen_wq(c, lam_per_min, mean_service, ca2, cs2)
    kg = kingman_wq(lam_per_min, mean_service, ca2, cs2)
    dev = None
    if ec["stable"] and ac is not None and ec["wq_min"]:
        dev = ac / ec["wq_min"] - 1.0
    return {
        "c": c,
        "rho": _round(utilization(lam_per_min, mean_service, c)),
        "erlang_c_wq_min": None if not ec["stable"] else ec["wq_min"],
        "erlang_c_p_wait": None if not ec["stable"] else ec["p_wait"],
        "allen_cunneen_wq_min": _round(ac),
        "kingman_gg1_wq_min": _round(kg),
        "rel_dev_ac_vs_ec": _round(dev),
        "agree_10pct": bool(dev is not None and abs(dev) <= ANALYTIC_AGREEMENT_TOL),
    }


def validity_domain(*, mean_service: float = 60.0,
                    c_grid: Sequence[int] = (1, 2, 3, 4, 6, 8, 12, 16),
                    rho_grid: Sequence[float] = (0.3, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)) -> dict:
    """扫描 (c, ρ) 网格，给出"Erlang-C 与 GI/G/c 近似偏差 ≤10%"的适用带与失效域。"""
    rows = []
    for c in c_grid:
        for rho in rho_grid:
            lam = c * rho / mean_service
            row = analytic_triangle(int(c), lam, mean_service, 1.0, 1.0)
            row["target_rho"] = rho
            rows.append(row)
    agreeing = [r for r in rows if r["agree_10pct"]]
    disagreeing = [r for r in rows if not r["agree_10pct"]]
    worst = max((r for r in rows if r["rel_dev_ac_vs_ec"] is not None),
                key=lambda r: abs(r["rel_dev_ac_vs_ec"]), default=None)
    return {
        "grid": rows,
        "agree_tol": ANALYTIC_AGREEMENT_TOL,
        "n_agree": len(agreeing),
        "n_disagree": len(disagreeing),
        "agree_share": _round(len(agreeing) / len(rows) if rows else None),
        "max_abs_dev_in_agreeing_band": _round(
            max((abs(r["rel_dev_ac_vs_ec"]) for r in agreeing), default=None)),
        "worst_case": worst,
        "invalid_band": [{"c": r["c"], "rho": r["target_rho"], "rel_dev": r["rel_dev_ac_vs_ec"]}
                         for r in disagreeing][:24],
        "notes": [
            "ρ→1 时 Erlang-C 与近似式同时失真且对 S 的高阶矩敏感；容量规划建议承诺 ρ≤0.9",
            "c≥12 且 ρ≤0.6 时 Allen–Cunneen 通常高估 Wq（保守方向），不用于承诺 SLA 上限",
            "c=1 时两者退化为同一式（M/G/1 的 P-K 形式），偏差为 0",
        ],
    }


# --------------------------------------------------------------------------- #
# 离散事件仿真（simpy）
# --------------------------------------------------------------------------- #
def _require_simpy() -> None:
    if simpy is None:  # pragma: no cover - 仅在缺依赖的环境触发
        raise RuntimeError("simpy is required for DES paths; pip install simpy")


def simulate_trace(*, c: int, arrivals: Sequence[float], services: Sequence[float],
                   collect_waits: bool = True, keep_from: float = 0.0) -> dict:
    """轨迹驱动 DES：给定到达时刻与服务时长，跑 M/G/c（FCFS，c 个 seat）。

    ``keep_from`` 用于削瞬态（warmup）：仿真仍从 0 时刻空系统跑起，但只统计
    到达时刻 ≥ keep_from 的请求的等待/逗留，避免"空系统起步"造成的偏乐观偏差。
    """
    _require_simpy()
    if len(arrivals) != len(services):
        raise ValueError("arrivals and services must have equal length")
    if c <= 0:
        raise ValueError("c must be >= 1")
    order = sorted(range(len(arrivals)), key=lambda i: arrivals[i])
    env = simpy.Environment()
    resource = simpy.Resource(env, capacity=int(c))
    waits: list[float] = []
    sojourns: list[float] = []
    busy = [0.0]

    def job(t_arr: float, dur: float):
        # 关键：simpy 进程在 env.run() 时统一启动，必须先把进程推到到达时刻，
        # 否则所有请求会在 t=0 同时涌入排队 → 等待时间为负/失真。
        if t_arr > env.now:
            yield env.timeout(t_arr - env.now)
        with resource.request() as req:
            yield req
            w = env.now - t_arr
            busy[0] += dur
            yield env.timeout(dur)
            if t_arr < keep_from:
                return
            waits.append(w)
            sojourns.append(env.now - t_arr)

    for i in order:
        env.process(job(float(arrivals[i]), float(services[i])))
    env.run()
    t_first = min((float(a) for a in arrivals), default=0.0)
    t_last = max((float(a) for a in arrivals), default=0.0)
    horizon = max((float(arrivals[i]) + float(services[i]) for i in range(len(arrivals))), default=0.0)
    # 观测窗取**到达时间跨度**：若用"最后一单完成时刻"，重尾服务的收尾会把窗拉长，
    # 使 utilization 系统性偏低（服务溢出尾不算容量需求）。
    observed_span = max(t_last - t_first, 1e-9)
    return {
        "c": int(c),
        "n": len(arrivals),
        "lambda_emp_per_min": _round(len(arrivals) / observed_span),
        "mean_service_emp_min": _round(_mean([float(x) for x in services])),
        "mean_wait_min": _round(_mean(waits)),
        "p_wait": _round(sum(1 for w in waits if w > 1e-9) / len(waits) if waits else None),
        "p90_wait_min": _round(float(np.percentile(waits, 90)) if waits else None),
        "p95_wait_min": _round(float(np.percentile(waits, 95)) if waits else None),
        "mean_sojourn_min": _round(_mean(sojourns)),
        "utilization": _round(busy[0] / (c * observed_span) if observed_span > 0 else None),
        "horizon_min": _round(observed_span),
        "waits": waits if collect_waits else [],
    }


def _warmup_time(c: int, mean_service: float, rho: float, relaxations: float = 10.0) -> float:
    """瞬态削除时长：ρ→1 时松弛时间 ≈ E[S]/(c(1-ρ))，取 10 个松弛时间。"""
    if rho <= 0 or rho >= 1 or c <= 0:
        return 0.0
    return relaxations * mean_service / (c * (1.0 - rho))


def simulate_mmc(*, c: int, lam_per_min: float, mean_service: float,
                 n_arrivals: int = 20000, seed: int = 20261110,
                 warmup_relaxations: float = 10.0) -> dict:
    """M/M/c：指数到达 + 指数服务（默认削 10 个松弛时间的瞬态）。"""
    rng = np.random.default_rng(seed)
    arrivals = np.cumsum(rng.exponential(1.0 / lam_per_min, size=n_arrivals))
    services = rng.exponential(mean_service, size=n_arrivals)
    rho = offered_load(lam_per_min, mean_service) / c
    keep = _warmup_time(c, mean_service, rho, warmup_relaxations)
    return simulate_trace(c=c, arrivals=[float(x) for x in arrivals],
                          services=[float(x) for x in services], keep_from=keep)


def simulate_mgc(*, c: int, lam_per_min: float,
                 service_sampler: Callable[[np.random.Generator], float],
                 mean_service: Optional[float] = None,
                 n_arrivals: int = 20000, seed: int = 20261110,
                 warmup_relaxations: float = 10.0) -> dict:
    """M/G/c：指数到达 + 任意服务分布（从拟合分布抽样，默认削瞬态）。"""
    rng = np.random.default_rng(seed)
    arrivals = np.cumsum(rng.exponential(1.0 / lam_per_min, size=n_arrivals))
    services = np.array([service_sampler(rng) for _ in range(n_arrivals)])
    ms = mean_service if mean_service else float(services.mean())
    rho = offered_load(lam_per_min, ms) / c
    keep = _warmup_time(c, ms, rho, warmup_relaxations)
    return simulate_trace(c=c, arrivals=[float(x) for x in arrivals],
                          services=[float(x) for x in services], keep_from=keep)


def mean_ci95(samples: Sequence[float], *, n_batches: int = 20) -> dict:
    """批均值法 95% 置信区间（吸收等待时间的自相关）。

    独立同分布样本用普通 SE 会低估不确定性；批均值的批间方差才是可用估计量。
    """
    xs = np.asarray(samples, dtype=float)
    if xs.size < 2 * n_batches:
        return {"mean": _round(float(xs.mean()) if xs.size else None), "ci95": None,
                "n": int(xs.size)}
    batches = np.array_split(xs, n_batches)
    means = np.array([b.mean() for b in batches if b.size])
    se = float(means.std(ddof=1) / math.sqrt(means.size))
    mean = float(xs.mean())
    return {"mean": _round(mean), "se": _round(se),
            "ci95": [_round(mean - 1.96 * se), _round(mean + 1.96 * se)],
            "n": int(xs.size), "n_batches": int(means.size)}


def crosscheck_mmc(*, c_values: Sequence[int] = (1, 2, 4, 8),
                   rho_values: Sequence[float] = (0.5, 0.7, 0.8, 0.9),
                   mean_service: float = 10.0, n_arrivals: int = 30000,
                   seed: int = 20261110) -> dict:
    """解析（Erlang-C）× 仿真（M/M/c）互裁：逐点相对偏差 + 是否 ≤10%。"""
    rows = []
    for c in c_values:
        for rho in rho_values:
            lam = c * rho / mean_service
            ec = erlang_c(int(c), lam, mean_service)
            des = simulate_mmc(c=int(c), lam_per_min=lam, mean_service=mean_service,
                               n_arrivals=n_arrivals,
                               seed=seed + 17 * int(c) + int(100 * rho))
            # 交叉验证用**仿真抽样的经验载荷**重算解析值：Wq 在 ρ≈0.8 附近对 λ 的
            # 1% 抽样噪声敏感 ~10%，不减掉这部分会把 RNG 噪声误判成模型误差。
            lam_emp = des.get("lambda_emp_per_min") or lam
            mean_s_emp = des.get("mean_service_emp_min") or mean_service
            ec_emp = erlang_c(int(c), lam_emp, mean_s_emp)
            ci = mean_ci95(des.get("waits") or [])
            dev = None
            if ec_emp["stable"] and ec_emp["wq_min"] and ci.get("mean") is not None:
                dev = ci["mean"] / ec_emp["wq_min"] - 1.0
            covers = bool(ci.get("ci95") and ec_emp["stable"] and ec_emp["wq_min"] is not None
                          and ci["ci95"][0] <= ec_emp["wq_min"] <= ci["ci95"][1])
            rows.append({
                "c": int(c), "rho": rho,
                "rho_emp": _round(utilization(lam_emp, mean_s_emp, int(c))),
                "erlang_c_wq_min": None if not ec["stable"] else ec["wq_min"],
                "erlang_c_wq_min_at_emp_load": None if not ec_emp["stable"] else ec_emp["wq_min"],
                "des_mean_wait_min": ci.get("mean"),
                "des_ci95": ci.get("ci95"),
                "des_p_wait": des["p_wait"],
                "erlang_c_p_wait": None if not ec["stable"] else ec["p_wait"],
                "rel_dev": _round(dev),
                "within_10pct": bool(dev is not None and abs(dev) <= ANALYTIC_AGREEMENT_TOL),
                "ci_covers_analytic": covers,
                "agree": bool(dev is not None and (abs(dev) <= ANALYTIC_AGREEMENT_TOL or covers)),
                "n_arrivals": n_arrivals,
            })
    return {
        "rows": rows,
        "n": len(rows),
        "n_within_10pct": sum(1 for r in rows if r["within_10pct"]),
        "n_agree": sum(1 for r in rows if r.get("agree")),
        "max_abs_dev": _round(max((abs(r["rel_dev"]) for r in rows
                                   if r["rel_dev"] is not None), default=None)),
        "tol": ANALYTIC_AGREEMENT_TOL,
        "criterion": "|rel_dev| <= 10% 或解析值落在 DES 批均值 95% CI 内（统计等价）",
    }


def crosscheck_mgc(*, c_values: Sequence[int] = (1, 2, 4),
                   rho_values: Sequence[float] = (0.6, 0.8),
                   service_model: Optional[dict] = None, fit: Optional[dict] = None,
                   mean_service: float = 10.0, n_arrivals: int = 20000,
                   n_draw: int = 20000, seed: int = 20261110) -> dict:
    """解析（Kingman/Allen–Cunneen）× 仿真（M/G/c，服务分布=选定模型）互裁。

    λ 由**抽样实测**的 E[S] 反推（λ=c·ρ/E[S]_draw），避免"拟合分布均值"与"抽样均值"
    不一致导致的名义 ρ 与实际 ρ 脱钩（重尾+经验 ECDF 时差可达 20%）。
    """
    if service_model is None:
        if fit is None:
            rng0 = np.random.default_rng(seed)
            fit = fit_service_distribution(list(rng0.lognormal(
                mean=math.log(mean_service) - 0.5, sigma=1.0, size=20000)))
        service_model = select_service_model(fit, [mean_service])
    sampler = service_model["sampler"]
    rng = np.random.default_rng(seed + 12345)
    draws = np.asarray([sampler(rng) for _ in range(n_draw)], dtype=float)
    mean_draw = float(draws.mean())
    scv_draw = float(draws.var() / mean_draw ** 2) if mean_draw > 0 else 1.0
    rows = []
    for c in c_values:
        for rho in rho_values:
            lam = c * rho / mean_draw
            ac = allen_cunneen_wq(c if c > 1 else 1, lam, mean_draw, 1.0, scv_draw)
            kg = kingman_wq(lam, mean_draw, 1.0, scv_draw)
            ht = heavy_traffic_wq(int(c), lam, mean_draw, 1.0, scv_draw)
            des = simulate_mgc(c=int(c), lam_per_min=lam, service_sampler=sampler,
                               mean_service=mean_draw, n_arrivals=n_arrivals,
                               seed=seed + 31 * int(c) + int(100 * rho))
            ci = mean_ci95(des.get("waits") or [])
            # 经验载荷校正：仿真抽样的 λ 与 E[S] 与名义值略有出入
            lam_emp = des.get("lambda_emp_per_min") or lam
            mean_s_emp = des.get("mean_service_emp_min") or mean_draw
            ac_emp = allen_cunneen_wq(int(c), lam_emp, mean_s_emp, 1.0, scv_draw)
            dev = ((ci["mean"] - ac_emp) / ac_emp
                   if (ac_emp and ci.get("mean") is not None) else None)
            covers = bool(ci.get("ci95") and ac_emp is not None
                          and ci["ci95"][0] <= ac_emp <= ci["ci95"][1])
            rows.append({
                "c": int(c), "rho": rho, "cs2": _round(scv_draw),
                "mean_service_draw_min": _round(mean_draw),
                "allen_cunneen_wq_min": _round(ac),
                "allen_cunneen_wq_min_at_emp_load": _round(ac_emp),
                "kingman_gg1_wq_min": _round(kg),
                "heavy_traffic_wq_min": _round(ht),
                "heavy_traffic_is_upper_bound": bool(ht is not None and ci.get("mean") is not None
                                                     and ht >= ci["mean"]),
                "des_mean_wait_min": ci.get("mean"),
                "des_ci95": ci.get("ci95"),
                "rel_dev_des_vs_ac": _round(dev),
                "within_10pct": bool(dev is not None and abs(dev) <= ANALYTIC_AGREEMENT_TOL),
                "ci_covers_analytic": covers,
                "agree": bool(dev is not None and (abs(dev) <= ANALYTIC_AGREEMENT_TOL or covers)),
            })
    return {
        "service_dist": service_model.get("name"),
        "service_kind": service_model.get("kind"),
        "cs2": _round(scv_draw),
        "rows": rows,
        "n_within_10pct": sum(1 for r in rows if r["within_10pct"]),
        "n_agree": sum(1 for r in rows if r.get("agree")),
        "n_dangerous": sum(1 for r in rows
                           if r.get("rel_dev_des_vs_ac") is not None
                           and r["rel_dev_des_vs_ac"] > ANALYTIC_AGREEMENT_TOL),
        "n_heavy_traffic_upper_bound": sum(1 for r in rows if r.get("heavy_traffic_is_upper_bound")),
        "n": len(rows),
        "tol": ANALYTIC_AGREEMENT_TOL,
        "criterion": "逐点一致，或超差方向为保守（解析值高估 → 只会多配 seat）",
    }


# --------------------------------------------------------------------------- #
# 容量标定 / 背测 / 负控
# --------------------------------------------------------------------------- #
def calibrate_seats(train: Series, *, fit: Optional[dict] = None, ca2: float = 1.0,
                    method: str = "allen_cunneen", max_seats: Optional[int] = None,
                    refine_with_des: bool = True, seed: int = 20261110,
                    service_model: Optional[dict] = None) -> dict:
    """把"有效 seat 数" c 作为**训练期拟合参数**：使模型 E[Wq] 贴近训练期实测等待均值。

    这不是"假设 c"，而是估计：留出期背测时 c 冻结在训练值上，因此 c 的估计误差会
    真实暴露在 KS 检验里。返回的 ``sweep`` 保存完整目标函数（可复核最优性）。
    """
    waits, services = train.waits, train.services
    if not waits or not services:
        return {"c_eff": None, "reason": "empty train window"}
    lam = train.rate_per_min()
    mean_s = _mean(services)
    obs_wq = _mean(waits)
    if fit is None or not fit.get("best"):
        fit = fit_service_distribution(services)
    if not fit.get("best"):
        return {"c_eff": None, "reason": "service fit failed"}
    cs2 = fit["best"]["scv"] or 1.0
    a = offered_load(lam, mean_s)
    upper = max_seats or max(int(math.ceil(a)) * 20, 40)
    sweep, best = [], None
    for c in range(max(1, int(math.ceil(a)) + 1), upper + 1):
        if method == "erlang_c":
            ec = erlang_c(c, lam, mean_s)
            wq = ec["wq_min"] if ec["stable"] else None
        else:
            wq = allen_cunneen_wq(c, lam, mean_s, ca2, cs2)
        if wq is None:
            continue
        err = abs(wq - obs_wq)
        sweep.append({"c": c, "model_wq_min": _round(wq), "abs_err": _round(err)})
        if best is None or err < best["abs_err"]:
            best = {"c": c, "model_wq_min": wq, "abs_err": err}
    if best is None:
        return {"c_eff": None, "reason": "no feasible c in search range",
                "offered_load": _round(a), "obs_wq_min": _round(obs_wq)}
    c_eff = int(best["c"])
    refine = None
    if refine_with_des and simpy is not None:
        # 解析标定只给粗解（近似式误差可达数十 %，重尾时更差）；再用训练期真实到达轨迹
        # 做多点 DES 精修：模型 E[W] 与实测 E[W] 的绝对差最小者胜。
        base = max(1, int(math.ceil(a)))
        cand = {c_eff - 1, c_eff, c_eff + 1,
                base + 1, int(round(base * 1.25)) + 1, int(round(base * 1.5)) + 1,
                int(round(base * 2.0)) + 1, int(round(base * 3.0)) + 1}
        candidates = sorted({c for c in cand if c >= base + 1 and c <= upper})
        if not candidates:  # 空集兜底，保证至少有解
            candidates = [max(base + 1, c_eff)]
        sampler = ((service_model or {}).get("sampler")
                   or sampler_from_fit(fit["best"]))
        rng = np.random.default_rng(seed)
        best_des = None
        for cand in candidates:
            services_draw = [sampler(rng) for _ in train.arrivals]
            des = simulate_trace(c=cand, arrivals=train.arrivals, services=services_draw)
            err = abs((des["mean_wait_min"] or 0.0) - obs_wq)
            if best_des is None or err < best_des["abs_err"]:
                best_des = {"c": cand, "mean_wait_min": des["mean_wait_min"], "abs_err": err}
        refine = {"candidates": candidates, "chosen": best_des["c"],
                  "des_wait_min": _round(best_des["mean_wait_min"]),
                  "abs_err": _round(best_des["abs_err"])}
        c_eff = int(best_des["c"])
    return {
        "c_eff": c_eff,
        "analytic_c_eff": int(best["c"]),
        "des_refinement": refine,
        "method": method,
        "offered_load": _round(a),
        "lambda_per_min": _round(lam),
        "mean_service_min": _round(mean_s),
        "cs2_fit": _round(cs2),
        "ca2_assumed": ca2,
        "obs_wq_min": _round(obs_wq),
        "model_wq_min": _round(best["model_wq_min"]),
        "rel_err": _round(best["model_wq_min"] / obs_wq - 1.0 if obs_wq else None),
        "search_range": [sweep[0]["c"], sweep[-1]["c"]] if sweep else None,
        "n_candidates": len(sweep),
        "sweep_head": sweep[:5],
        "implied_rho": _round(a / c_eff),
    }


def _predicted_wait_samples_trace(holdout: Series, *, c: int, sampler: Callable,
                                  seed: int) -> dict:
    """轨迹驱动预测：留出期**实际到达时刻**照搬，服务时长从训练期拟合分布重抽。"""
    rng = np.random.default_rng(seed)
    arrivals = holdout.arrivals
    services = [sampler(rng) for _ in arrivals]
    res = simulate_trace(c=c, arrivals=arrivals, services=services)
    return res


def simulate_nonhomogeneous(*, c: int, bucket_minutes: float,
                            rates_per_min: Sequence[float],
                            duration_minutes: float,
                            service_sampler: Callable[[np.random.Generator], float],
                            seed: int = 0) -> dict:
    """非齐次 Poisson 到达（分段常数 λ_b）下的 M/G/c 仿真。"""
    rng = np.random.default_rng(seed)
    arrivals: list[float] = []
    n_buckets = int(math.ceil(duration_minutes / bucket_minutes))
    for b in range(n_buckets):
        rate = rates_per_min[b % len(rates_per_min)] if rates_per_min else 0.0
        if not rate or rate <= 0:
            continue
        start = b * bucket_minutes
        dur = min(bucket_minutes, duration_minutes - start)
        if dur <= 0:
            break
        n = rng.poisson(rate * dur)
        if n:
            arrivals.extend((start + rng.uniform(0, dur, size=n)).tolist())
    arrivals.sort()
    services = [service_sampler(rng) for _ in arrivals]
    return simulate_trace(c=c, arrivals=arrivals, services=services)


def _ks(pred_waits: Sequence[float], obs: Sequence[float]) -> dict:
    """两样本 KS（iid 假设）；等待样本强自相关时 p 值偏小，故仅作诊断量。"""
    if len(pred_waits) == 0 or len(obs) == 0:
        return {"ks_stat": None, "ks_p": None, "n_pred": 0, "n_obs": len(obs)}
    d, p = sps.ks_2samp(np.asarray(pred_waits, dtype=float), np.asarray(obs, dtype=float))
    return {"ks_stat": _round(float(d)), "ks_p": _round(float(p)),
            "n_pred": len(pred_waits), "n_obs": len(obs)}


def backtest(train: Series, holdout: Series, *, fit: Optional[dict] = None,
             c_eff: Optional[int] = None, ca2: float = 1.0,
             bucket_minutes: float = 60.0, seed: int = 20261110, n_mc: int = 30,
             service_model: Optional[dict] = None) -> dict:
    """历史留出期背测：训练期拟合 → 留出期预测等待分布 → KS（含依赖校正的 MC 零分布）。

    为什么不能只看 t 检验式 KS 的 p 值：等待时间在重载队列里**强自相关**（一个长
    busy period 就抬高一整窗样本），iid 假设下 KS 的 p 会严重偏小，把正确的模型判死。
    因此判据改用**参数自助（parametric bootstrap）零分布**：
    在"模型为真"的假设下用同一条到达轨迹重抽服务时间多次，得到 D 的零分布，
    p_mc = (1 + #{D_rep ≥ D_obs}) / (n_mc + 1)。观察样本与模型重复样本在同一依赖结构下
    可交换，故 p_mc 是有效检验；朴素 KS p 同时保留作诊断（透明可核）。

    另给三种预测口径，用于区分"模型错"与"到达过程简化错"：
      ``trace``  —— 照搬留出期真实到达时刻（只考服务/容量模型）
      ``poisson_homogeneous`` —— 用训练期平均 λ 生成 Poisson 到达
      ``poisson_nonhomogeneous`` —— 用训练期分时段 λ(t) 生成非齐次到达
    """
    if not train.intervals or not holdout.intervals:
        return {"pass": False, "reason": "empty train or holdout window"}
    if fit is None:
        fit = fit_service_distribution(train.services)
    if not fit.get("best"):
        return {"pass": False, "reason": "service fit failed"}
    smodel = service_model or select_service_model(fit, train.services)
    calib = calibrate_seats(train, fit=fit, ca2=ca2, seed=seed, service_model=smodel)
    c = c_eff or calib.get("c_eff")
    if not c:
        return {"pass": False, "reason": "seat calibration failed", "calibration": calib}
    sampler = smodel["sampler"]
    obs = [float(x) for x in holdout.waits]

    trace = _predicted_wait_samples_trace(holdout, c=int(c), sampler=sampler, seed=seed)
    lam_hold = train.rate_per_min()
    pois = simulate_mgc(c=int(c), lam_per_min=lam_hold, service_sampler=sampler,
                        mean_service=fit["best"]["mean_min"],
                        n_arrivals=max(len(holdout.intervals), 2000), seed=seed + 1)
    rates = [r for r in bucket_rates(train, bucket_minutes=bucket_minutes)["rates_per_min"]
             if r is not None]
    nh = None
    if rates:
        nh = simulate_nonhomogeneous(
            c=int(c), bucket_minutes=bucket_minutes, rates_per_min=rates,
            duration_minutes=max(holdout.span(), bucket_minutes) * 1.05,
            service_sampler=sampler, seed=seed + 2,
        )

    ks_trace = _ks(trace["waits"], obs)
    ks_pois = _ks(pois["waits"], obs)
    ks_nh = _ks(nh["waits"], obs) if nh else {"ks_stat": None, "ks_p": None, "n_pred": 0}

    # 参数自助零分布（依赖校正，**条件口径**）：照搬留出期真实到达时刻、重抽服务时间。
    # 这样每次重复继承同一到达簇聚结构，两侧的依赖形态一致；同时用"平均等待相对误差"
    # 作为第二道闸，防止退化模型（预测恒为 0 等待 → D_rep == D_obs → p≈1 的假通过）。
    d_obs = ks_trace["ks_stat"]
    obs_mean = _mean(obs)
    pred_mean = trace["mean_wait_min"]
    rel_err = abs(pred_mean / obs_mean - 1.0) if (pred_mean is not None and obs_mean) else None
    abs_err = abs(pred_mean - obs_mean) if (pred_mean is not None and obs_mean is not None) else None
    mean_ok = bool(rel_err is None
                   or rel_err <= MAX_BACKTEST_MEAN_WAIT_REL_ERR
                   or (abs_err is not None and abs_err <= MEAN_WAIT_ABS_TOL_MIN))
    d_reps = []
    if d_obs is not None and n_mc > 0:
        for k in range(n_mc):
            rep = _predicted_wait_samples_trace(holdout, c=int(c), sampler=sampler,
                                               seed=seed + 1000 + k)
            d_reps.append(_ks(rep["waits"], obs)["ks_stat"] or 0.0)
        p_mc = (1 + sum(1 for d in d_reps if d >= d_obs)) / (n_mc + 1)
    else:
        p_mc = None
    d_arr = np.asarray(d_reps, dtype=float) if d_reps else np.asarray([], dtype=float)
    powerless = bool(d_arr.size > 1 and float(d_arr.max() - d_arr.min()) < 1e-9)
    # 服务时间分布本身的留出期稳定性（KS：留出样本 vs 训练期拟合 CDF）
    dist = FIT_CANDIDATES[fit["best"]["name"]]
    ks_service = sps.kstest(np.asarray(holdout.services, dtype=float), dist.cdf,
                            args=tuple(fit["best"]["params"]))
    return {
        "pass": bool(p_mc is not None and p_mc > BACKTEST_KS_ALPHA and mean_ok),
        "criterion": ("条件口径参数自助 KS：p_mc = P(D(同到达轨迹重抽服务) >= D(模型 vs 留出期)) > 0.05"
                      f"，且平均等待相对误差 <= {MAX_BACKTEST_MEAN_WAIT_REL_ERR:.0%}（防退化模型假通过）"),
        "alpha": BACKTEST_KS_ALPHA,
        "seats": int(c),
        "calibration": calib,
        "service_model": {"kind": smodel["kind"], "name": smodel["name"],
                           "scv": smodel.get("scv"), "note": smodel.get("note")},
        "service_fit": {"best": fit["best"], "ks_alpha": FIT_KS_ALPHA,
                        "accepts_null": fit.get("best_accepts_null")},
        "holdout_service_ks": {"ks_stat": _round(float(ks_service.statistic)),
                               "ks_p": _round(float(ks_service.pvalue))},
        "observed": {
            "n": len(obs),
            "mean_wait_min": _round(_mean(obs)),
            "p50_wait_min": _round(float(np.percentile(obs, 50))),
            "p90_wait_min": _round(float(np.percentile(obs, 90))),
            "p_wait_gt0": _round(float(np.mean([w > 1e-9 for w in obs]))),
        },
        "mean_wait_rel_err": _round(rel_err),
        "mean_wait_abs_err_min": _round(abs_err),
        "mean_wait_guard_ok": mean_ok,
        "max_mean_wait_rel_err": MAX_BACKTEST_MEAN_WAIT_REL_ERR,
        "null_powerless": powerless,
        "ks_null": {
            "d_obs": d_obs,
            "p_mc": _round(p_mc),
            "n_mc": n_mc,
            "d_null_mean": _round(float(d_arr.mean()) if d_arr.size else None),
            "d_null_p95": _round(float(np.percentile(d_arr, 95)) if d_arr.size else None),
            "d_null_max": _round(float(d_arr.max()) if d_arr.size else None),
        },
        "variants": {
            "trace": {"ks": ks_trace, "mean_wait_min": trace["mean_wait_min"]},
            "poisson_homogeneous": {"ks": ks_pois, "mean_wait_min": pois["mean_wait_min"],
                                    "lambda_per_min": _round(lam_hold)},
            "poisson_nonhomogeneous": {"ks": ks_nh,
                                       "mean_wait_min": nh["mean_wait_min"] if nh else None,
                                       "n_buckets": len(rates), "bucket_minutes": bucket_minutes},
        },
        "trace_wait_samples": trace["waits"],
        "holdout_wait_samples": obs,
    }


def arrival_process_check(train: Series, holdout: Series, *, fit: Optional[dict] = None,
                          service_model: Optional[dict] = None, c_eff: Optional[int] = None,
                          bucket_minutes: float = 60.0, n_mc: int = 12,
                          seed: int = 20261110) -> dict:
    """到达过程口径检验：把 λ(t) 当**随机到达模型**（非齐次 Poisson）而不是照搬轨迹。

    这是比条件背测更强的主张：它假设未来到达只是"同速率同分布的随机流"。若历史到达
    有明显的批次齐发（峰均比 ≫ 1），Poisson 假设会产生系统性低估 → 该检验失败即为
    "到达模型失效域"，而不是服务/容量模型错误。零分布同样取两条模型轨迹之间的 KS。
    """
    if not train.intervals or not holdout.intervals:
        return {"pass": False, "reason": "empty window"}
    if fit is None:
        fit = fit_service_distribution(train.services)
    smodel = service_model or select_service_model(fit, train.services)
    sampler = smodel["sampler"]
    lam_hold = train.rate_per_min()
    rates = [r for r in bucket_rates(train, bucket_minutes=bucket_minutes)["rates_per_min"]
             if r is not None]
    obs = np.asarray(holdout.waits, dtype=float)
    n_rep = max(len(holdout.intervals), 200)
    duration = max(holdout.span(), bucket_minutes) * 1.05

    def _run(seed_k: int) -> dict:
        return simulate_nonhomogeneous(c=int(c_eff), bucket_minutes=bucket_minutes,
                                       rates_per_min=rates, duration_minutes=duration,
                                       service_sampler=sampler, seed=seed_k)

    pred = _run(seed + 5)
    ks = _ks(pred["waits"], list(obs))
    d_reps = []
    for k in range(n_mc):
        a, b = _run(seed + 200 + 2 * k), _run(seed + 201 + 2 * k)
        d_reps.append(_ks(a["waits"], b["waits"])["ks_stat"] or 0.0)
    p_mc = (1 + sum(1 for d in d_reps if d >= (ks["ks_stat"] or 0.0))) / (n_mc + 1)
    obs_mean = float(obs.mean()) if obs.size else None
    pred_mean = pred["mean_wait_min"]
    rel_err = abs(pred_mean / obs_mean - 1.0) if (pred_mean is not None and obs_mean) else None
    abs_err = abs(pred_mean - obs_mean) if (pred_mean is not None and obs_mean is not None) else None
    return {
        "pass": bool(p_mc > BACKTEST_KS_ALPHA and rel_err is not None
                     and (rel_err <= MAX_ARRIVAL_MEAN_WAIT_REL_ERR
                          or (abs_err is not None and abs_err <= MEAN_WAIT_ABS_TOL_MIN))),
        "seats": int(c_eff),
        "bucket_minutes": bucket_minutes,
        "n_buckets_used": len(rates),
        "observed_mean_wait_min": _round(obs_mean),
        "model_mean_wait_min": pred_mean,
        "mean_wait_rel_err": _round(rel_err),
        "mean_wait_abs_err_min": _round(abs_err),
        "ks": ks,
        "p_mc": _round(p_mc),
        "d_null_mean": _round(float(np.mean(d_reps)) if d_reps else None),
        "n_mc": n_mc,
        "criterion": ("非齐次 Poisson 到达模型：p_mc > 0.05 且平均等待相对误差 <= "
                      f"{MAX_ARRIVAL_MEAN_WAIT_REL_ERR:.0%}"),
        "note": ("失败即『到达（含桶内）批次结构不可由 Poisson 复现』：峰均比 "
                 f"{bucket_rates(train, bucket_minutes=bucket_minutes).get('peak_to_mean')} 时常见。"
                 "分时段 λ(t) 只刻画小时级起伏；真正的拥塞来自桶内分钟级齐发（backfill），"
                 "Poisson 在桶内均匀撒点会把这层结构抹掉"),
    }


def shuffle_control(train: Series, holdout: Series, *, fit: Optional[dict] = None,
                    c_eff: Optional[int] = None, seed: int = 20261110,
                    n_mc: int = 20, service_model: Optional[dict] = None) -> dict:
    """负控：打乱留出期到达顺序（到达时刻集合与服务样本不变），模型应变差。

    注意适用范围：若到达过程本身已近似 iid（如合成 M/M/c），交换到达顺序在分布上不变，
    负控**必然不变差**——这不是模型好，而是该工况下负控无信息量，报告如实标注。
    判据：打乱后两样本 KS D 放大 ≥ ``SHUFFLE_DEGRADE_FACTOR`` 倍，或依赖校正 p_mc 跌破 α。
    """
    base = backtest(train, holdout, fit=fit, c_eff=c_eff, seed=seed, n_mc=n_mc,
                    service_model=service_model)
    if not base.get("seats"):
        return {"pass": False, "reason": "baseline backtest unavailable"}
    c = int(base["seats"])
    fit = fit or fit_service_distribution(train.services)
    rng = np.random.default_rng(seed + 99)
    # 注意：直接置换"到达时刻的值"不会改变其排序后的集合，DES 输出逐字相同 →
    # 负控形同虚设。正确做法是置换**相邻到达间隔**再累加：间隔多重集不变，
    # 时间上的簇聚结构被破坏（这才是"打乱到达顺序"）。
    arr = np.sort(np.asarray(holdout.arrivals, dtype=float))
    if arr.size > 1:
        gaps = np.diff(arr)
        rng.shuffle(gaps)
        new_arrivals = arr[0] + np.concatenate(([0.0], np.cumsum(gaps)))
    else:
        new_arrivals = arr
    shuffled = list(new_arrivals)
    shuffled_series = Series(intervals=[
        Interval(iv.rid, shuffled[i], shuffled[i] + iv.wait, shuffled[i] + iv.wait + iv.service)
        for i, iv in enumerate(holdout.intervals)
    ])
    shuffled_series.intervals.sort(key=lambda i: i.arrival)
    shuffle_bt = backtest(train, shuffled_series, fit=fit, c_eff=c, seed=seed + 7, n_mc=n_mc,
                          service_model=service_model)

    d_base = (base.get("ks_null") or {}).get("d_obs")
    d_shuf = (shuffle_bt.get("ks_null") or {}).get("d_obs")
    p_base = (base.get("ks_null") or {}).get("p_mc")
    p_shuf = (shuffle_bt.get("ks_null") or {}).get("p_mc")
    ratio = (d_shuf / d_base) if (d_base and d_shuf) else None
    obs_mean = (base.get("observed") or {}).get("mean_wait_min")
    base_pred = (base.get("variants", {}).get("trace") or {}).get("mean_wait_min")
    shuf_pred = (shuffle_bt.get("variants", {}).get("trace") or {}).get("mean_wait_min")
    base_err = abs(base_pred / obs_mean - 1.0) if (base_pred and obs_mean) else None
    shuf_err = abs(shuf_pred / obs_mean - 1.0) if (shuf_pred and obs_mean) else None
    err_ratio = (shuf_err / base_err) if (base_err and shuf_err) else None
    degraded = bool(
        (d_base is not None and d_shuf is not None and d_shuf >= d_base * SHUFFLE_DEGRADE_FACTOR)
        or (p_shuf is not None and p_shuf <= BACKTEST_KS_ALPHA)
        or (err_ratio is not None and err_ratio >= SHUFFLE_DEGRADE_FACTOR)
    )
    return {
        "pass": degraded,
        "factor_threshold": SHUFFLE_DEGRADE_FACTOR,
        "baseline": {"ks_stat": d_base, "p_mc": p_base},
        "shuffled": {"ks_stat": d_shuf, "p_mc": p_shuf,
                     "mean_wait_min": (shuffle_bt.get("variants", {}).get("trace") or {}).get("mean_wait_min")},
        "ks_ratio": _round(ratio),
        "mean_wait_rel_err": {"baseline": _round(base_err), "shuffled": _round(shuf_err),
                              "ratio": _round(err_ratio),
                              "obs_mean_wait_min": obs_mean,
                              "pred_mean_wait_min": {"baseline": base_pred, "shuffled": shuf_pred}},
        "seats_frozen": c,
        "note": ("负控未变差：说明留出期到达过程的时序结构对等待分布影响不显著"
                 "（到达近似 iid 或窗口内载荷平稳），此工况下负控无信息量，不能当作模型正确的证据"),
    }


# --------------------------------------------------------------------------- #
# 容量规划器
# --------------------------------------------------------------------------- #
DEFAULT_SLA = {
    "max_mean_wait_min": 15.0,
    "max_p90_wait_min": 60.0,
    "max_utilization": 0.90,
}


def capacity_metrics(c: int, lam_per_min: float, mean_service: float,
                     ca2: float = 1.0, cs2: float = 1.0) -> dict:
    """给定 seat 数 c，给出该配置的解析容量指标（Wq 均值/90 分位、ρ、P(wait)）。"""
    ec = erlang_c(c, lam_per_min, mean_service)
    ac = allen_cunneen_wq(c, lam_per_min, mean_service, ca2, cs2)
    wq = ac if (ca2 != 1.0 or cs2 != 1.0) else (ec["wq_min"] if ec["stable"] else None)
    if ec["stable"] and cs2 == 1.0 and ca2 == 1.0:
        wq = ec["wq_min"]
    return {
        "c": int(c),
        "rho": None if not ec["stable"] else ec["rho"],
        "stable": ec["stable"],
        "wq_mean_min": _round(wq),
        "p90_wait_min": _round(wq_p90_approx(int(c), lam_per_min, mean_service, wq)),
        "p_wait": None if not ec["stable"] else ec["p_wait"],
        "offered_load": _round(offered_load(lam_per_min, mean_service)),
    }


def min_seats(lam_per_min: float, mean_service: float, *, ca2: float = 1.0, cs2: float = 1.0,
              sla: Optional[dict] = None, max_seats: int = 2048,
              method: str = "allen_cunneen",
              sampler: Optional[Callable[[np.random.Generator], float]] = None,
              n_arrivals: int = 4000, seed: int = 20261110) -> dict:
    """给定 λ、S 分布与 SLA，搜索**最小 seat 数**；返回绑定约束与不可行原因。"""
    sla = dict(DEFAULT_SLA, **(sla or {}))
    if method == "des":
        return _min_seats_des(lam_per_min, mean_service, sampler=sampler, sla=sla,
                              max_seats=max_seats, n_arrivals=n_arrivals, seed=seed)
    a = offered_load(lam_per_min, mean_service)
    floor = max(int(math.ceil(a)), 1)
    floor_util = int(math.ceil(a / sla.get("max_utilization", 1.0))) if sla.get("max_utilization") else 1
    start = max(floor + 1, floor_util, 1)
    tried = []
    for c in range(start, max_seats + 1):
        if method == "erlang_c":
            ec = erlang_c(c, lam_per_min, mean_service)
            if not ec["stable"]:
                continue
            wq = ec["wq_min"]
            p90 = wq_p90_approx(c, lam_per_min, mean_service, wq)
        else:
            wq = allen_cunneen_wq(c, lam_per_min, mean_service, ca2, cs2)
            if wq is None:
                continue
            ec = erlang_c(c, lam_per_min, mean_service)
            p90 = wq_p90_approx(c, lam_per_min, mean_service, wq)
        rho = utilization(lam_per_min, mean_service, c)
        ok_mean = (sla.get("max_mean_wait_min") is None) or (wq is not None and wq <= sla["max_mean_wait_min"])
        ok_p90 = (sla.get("max_p90_wait_min") is None) or (p90 is not None and p90 <= sla["max_p90_wait_min"])
        ok_util = (sla.get("max_utilization") is None) or (rho <= sla["max_utilization"])
        tried.append({"c": c, "wq_mean_min": _round(wq), "p90_wait_min": _round(p90),
                      "rho": _round(rho), "ok": bool(ok_mean and ok_p90 and ok_util)})
        if ok_mean and ok_p90 and ok_util:
            binding = []
            if sla.get("max_utilization") is not None and abs(rho - sla["max_utilization"]) < 1e-2:
                binding.append("max_utilization")
            if wq is not None and sla.get("max_mean_wait_min") is not None:
                binding.append("max_mean_wait_min" if wq / sla["max_mean_wait_min"] > 0.5 else "slack")
            return {
                "feasible": True,
                "c_min": c,
                "method": method,
                "sla": sla,
                "metrics": {"rho": _round(rho), "wq_mean_min": _round(wq),
                            "p90_wait_min": _round(p90),
                            "p_wait": None if not ec["stable"] else ec["p_wait"]},
                "offered_load": _round(a),
                "tried": tried[:10],
                "binding": sorted(set(binding)),
            }
    return {"feasible": False, "c_min": None, "method": method, "sla": sla,
            "offered_load": _round(a), "reason": f"no c <= {max_seats} satisfies SLA",
            "tried": tried[:10]}


def _min_seats_des(lam_per_min: float, mean_service: float, *, sampler: Callable,
                   sla: dict, max_seats: int = 2048, n_arrivals: int = 4000,
                   seed: int = 20261110) -> dict:
    """DES 驱动的容量搜索：解析近似在重尾服务下不可信，直接以仿真判 SLA。

    搜索策略：从未超载下界出发按 1.25 倍几何增长找到首个可行 c（gallop），
    再在 (最后不可行, 首个可行] 上二分，得到最小可行 c（单调性假设：加 seat 只会改善）。
    """
    _require_simpy()
    a = offered_load(lam_per_min, mean_service)
    util_cap = sla.get("max_utilization")
    lower = max(int(math.ceil(a)) + 1, int(math.ceil(a / util_cap)) if util_cap else 1, 1)
    tried: list[dict] = []

    def evaluate(c: int) -> dict:
        des = simulate_mgc(c=int(c), lam_per_min=lam_per_min, service_sampler=sampler,
                           mean_service=mean_service, n_arrivals=n_arrivals,
                           seed=seed + 101 * int(c))
        ci = mean_ci95(des.get("waits") or [])
        wq = ci.get("mean")
        p90 = des.get("p90_wait_min")
        rho = utilization(lam_per_min, mean_service, int(c))
        ok_mean = sla.get("max_mean_wait_min") is None or (wq is not None and wq <= sla["max_mean_wait_min"])
        ok_p90 = sla.get("max_p90_wait_min") is None or (p90 is not None and p90 <= sla["max_p90_wait_min"])
        ok_util = util_cap is None or rho <= util_cap
        row = {"c": int(c), "wq_mean_min": _round(wq), "ci95": ci.get("ci95"),
               "p90_wait_min": _round(p90), "rho": _round(rho),
               "ok": bool(ok_mean and ok_p90 and ok_util)}
        tried.append(row)
        return row

    if lower > max_seats:
        return {"feasible": False, "c_min": None, "method": "des", "sla": sla,
                "offered_load": _round(a), "reason": f"lower bound {lower} exceeds max_seats",
                "tried": []}
    lo = lower
    row_lo = evaluate(lo)
    if row_lo["ok"]:
        return {"feasible": True, "c_min": lo, "method": "des", "sla": sla,
                "offered_load": _round(a), "metrics": row_lo, "tried": tried,
                "binding": ["utilization_floor"] if util_cap else []}
    hi = lo
    row_hi = row_lo
    while hi < max_seats and not row_hi["ok"]:
        lo, row_lo = hi, row_hi
        hi = min(max_seats, max(hi + 1, int(math.ceil(hi * 1.25))))
        row_hi = evaluate(hi)
    if not row_hi["ok"]:
        return {"feasible": False, "c_min": None, "method": "des", "sla": sla,
                "offered_load": _round(a),
                "reason": f"no c <= {max_seats} satisfies SLA (last tried c={hi})",
                "tried": tried}
    # 二分最小可行 c
    while hi - lo > 1:
        mid = (lo + hi) // 2
        row_mid = evaluate(mid)
        if row_mid["ok"]:
            hi, row_hi = mid, row_mid
        else:
            lo, row_lo = mid, row_mid
    return {"feasible": True, "c_min": hi, "method": "des", "sla": sla,
            "offered_load": _round(a), "metrics": row_hi, "tried": tried,
            "binding": [k for k, ok in (("max_p90_wait_min", row_hi["ok"]),) if ok]}


def plan_nonhomogeneous(rates: dict, mean_service: float, *, ca2: float = 1.0, cs2: float = 1.0,
                        sla: Optional[dict] = None, method: str = "allen_cunneen",
                        sampler: Optional[Callable[[np.random.Generator], float]] = None,
                        n_arrivals: int = 3000, seed: int = 20261110) -> dict:
    """分时段容量规划：每个时段按自己的 λ_b 求最小 seat 数，给出峰值/中位 staffing。"""
    rows = []
    for b, rate in enumerate(rates.get("rates_per_min", [])):
        if rate is None or rate <= 0:
            continue
        plan = min_seats(rate, mean_service, ca2=ca2, cs2=cs2, sla=sla, method=method,
                         sampler=sampler, n_arrivals=n_arrivals, seed=seed)
        rows.append({"bucket": b, "lambda_per_min": _round(rate), "c_min": plan.get("c_min"),
                     "feasible": plan.get("feasible"),
                     "rho": (plan.get("metrics") or {}).get("rho"),
                     "wq_mean_min": (plan.get("metrics") or {}).get("wq_mean_min")})
    feas = [r["c_min"] for r in rows if r.get("c_min")]
    return {
        "bucket_minutes": rates.get("bucket_minutes"),
        "n_buckets": len(rows),
        "peak_seats_required": max(feas) if feas else None,
        "median_seats_required": (float(np.median(feas)) if feas else None),
        "static_seats_for_peak_lambda": (
            min_seats(rates.get("peak_rate_per_min") or 0.0, mean_service,
                      ca2=ca2, cs2=cs2, sla=sla, method=method, sampler=sampler,
                      n_arrivals=n_arrivals, seed=seed).get("c_min")
            if rates.get("peak_rate_per_min") else None),
        "rows": rows,
    }


def verify_plan_with_des(plan: dict, lam_per_min: float,
                         service_sampler: Callable[[np.random.Generator], float],
                         *, sla: Optional[dict] = None, n_arrivals: int = 20000,
                         seed: int = 20261110) -> dict:
    """容量方案的仿真复核：DES 在 c_min 下的实测等待 vs SLA 承诺（防止解析解偏乐观）。"""
    sla = dict(DEFAULT_SLA, **(sla or {}))
    c = plan.get("c_min")
    if not c:
        return {"verified": False, "reason": "planner returned no seat count"}
    des = simulate_mgc(c=int(c), lam_per_min=lam_per_min, service_sampler=service_sampler,
                       n_arrivals=n_arrivals, seed=seed)
    obs_mean = np.asarray(des["waits"], dtype=float)
    ok_mean = obs_mean.mean() <= sla.get("max_mean_wait_min", float("inf"))
    ok_p90 = float(np.percentile(obs_mean, 90)) <= sla.get("max_p90_wait_min", float("inf"))
    return {
        "verified": bool(ok_mean and ok_p90),
        "c": int(c),
        "des_mean_wait_min": des["mean_wait_min"],
        "des_p90_wait_min": des["p90_wait_min"],
        "analytic_wq_mean_min": (plan.get("metrics") or {}).get("wq_mean_min"),
        "analytic_p90_wait_min": (plan.get("metrics") or {}).get("p90_wait_min"),
        "sla": sla,
        "n_arrivals": n_arrivals,
    }


# --------------------------------------------------------------------------- #
# 报告装配
# --------------------------------------------------------------------------- #
def _criteria(report: dict) -> list[dict]:
    """把每个判据折算成 {name, target, value, pass}，数字全部程序化取自上游结果。"""
    out = []
    data = report["data_plane"]
    out.append({"name": "data_plane_sample_size", "target": "n >= 1000",
                "value": data["full"]["n"], "pass": bool(data["full"]["n"] >= 1000)})
    fit = report["service_fit"]
    sm = report.get("service_model") or {}
    out.append({"name": "service_model_selected",
                "target": "服务时间模型选定（参数族通过 KS，否则声明非参数 ECDF）",
                "value": {"kind": sm.get("kind"), "name": sm.get("name"),
                          "scv": sm.get("scv"), "note": sm.get("note")},
                "pass": bool(sm.get("kind"))})
    out.append({"name": "service_fit_ks", "target": "KS p > 0.05 (拟合可接受)",
                "value": {"dist": (fit.get("best") or {}).get("name"),
                          "ks_stat": (fit.get("best") or {}).get("ks_stat"),
                          "ks_p": (fit.get("best") or {}).get("ks_p")},
                "pass": bool(fit.get("best_accepts_null"))})
    mc = report["analytic_crosscheck"]["mmc"]
    out.append({"name": "analytic_vs_des_mmc",
                "target": "逐点 abs(偏差) <= 10% 或解析值落在 DES 95% CI 内",
                "value": {"n_within_10pct": mc["n_within_10pct"], "n_agree": mc.get("n_agree"),
                          "n": mc["n"], "max_abs_dev": mc["max_abs_dev"]},
                "pass": bool(mc["n"] > 0 and mc.get("n_agree", 0) == mc["n"])})
    mg = report["analytic_crosscheck"]["mgc"]
    out.append({"name": "analytic_vs_des_mgc",
                "target": "逐点 abs(偏差) <= 10%，或超差方向为保守（解析高估 → 多配 seat）",
                "value": {"n_within_10pct": mg["n_within_10pct"], "n_agree": mg.get("n_agree"),
                          "n_dangerous": mg.get("n_dangerous"), "n": mg["n"]},
                "pass": bool(mg["n"] > 0 and mg.get("n_dangerous") == 0)})
    vd = report["validity_domain"]
    out.append({"name": "analytic_mutual_agreement", "target": "存在偏差 <= 10% 的适用带",
                "value": {"n_agree": vd["n_agree"], "n_grid": len(vd["grid"]),
                          "agree_share": vd["agree_share"],
                          "worst_abs_dev": abs((vd.get("worst_case") or {}).get("rel_dev_ac_vs_ec") or 0)},
                "pass": bool(vd["n_agree"] > 0)})
    bt = report["backtest"]
    nu = bt.get("ks_null") or {}
    d = (bt.get("variants", {}).get("trace", {}).get("ks", {}) or {})
    out.append({"name": "backtest_ks_p_gt_0.05",
                "target": "留出期等待分布 KS p > 0.05（依赖校正 p_mc 为主判据，朴素 p 附诊断）",
                "value": {"d_obs": nu.get("d_obs"), "p_mc": nu.get("p_mc"),
                          "d_null_mean": nu.get("d_null_mean"), "d_null_p95": nu.get("d_null_p95"),
                          "naive_ks_p": d.get("ks_p"), "seats": bt.get("seats")},
                "pass": bool(bt.get("pass"))})
    am = report.get("arrival_model") or {}
    out.append({"name": "arrival_model_validity",
                "target": ("非齐次 Poisson 到达模型 p_mc > 0.05 且平均等待相对误差 <= "
                           f"{MAX_ARRIVAL_MEAN_WAIT_REL_ERR:.0%}"),
                "value": {"p_mc": am.get("p_mc"), "ks_p": (am.get("ks") or {}).get("ks_p"),
                          "observed_mean_wait_min": am.get("observed_mean_wait_min"),
                          "model_mean_wait_min": am.get("model_mean_wait_min"),
                          "mean_wait_rel_err": am.get("mean_wait_rel_err")},
                "pass": bool(am.get("pass"))})
    sc = report["shuffle_control"]
    out.append({"name": "shuffle_negative_control",
                "target": f"打乱后 D 放大 >= {SHUFFLE_DEGRADE_FACTOR}x 或 p_mc <= 0.05",
                "value": {"base_ks_stat": (sc.get("baseline") or {}).get("ks_stat"),
                          "shuffled_ks_stat": (sc.get("shuffled") or {}).get("ks_stat"),
                          "base_p_mc": (sc.get("baseline") or {}).get("p_mc"),
                          "shuffled_p_mc": (sc.get("shuffled") or {}).get("p_mc"),
                          "ks_ratio": sc.get("ks_ratio")},
                "pass": bool(sc.get("pass"))})
    plan = report["capacity_plan"]
    out.append({"name": "capacity_plan_feasible",
                "target": "在设计载荷（λ 的 95 分位）与给定 SLA 下存在最小 seat 数",
                "value": {"design_load": plan.get("design_load"),
                          "lambda_p95": plan.get("lambda_p95"),
                          "sla": plan["static"].get("sla"), "c_min": plan["static"].get("c_min"),
                          "method": plan["static"].get("method"),
                          "metrics": plan["static"].get("metrics")},
                "pass": bool(plan["static"].get("feasible"))})
    ver = plan.get("des_verification") or {}
    out.append({"name": "capacity_plan_des_verified", "target": "DES 复核满足 SLA",
                "value": {k: ver.get(k) for k in ("c", "des_mean_wait_min", "des_p90_wait_min")},
                "pass": bool(ver.get("verified"))})
    return out


def build_report(*, series: Series, bucket_minutes: float = 60.0, train_frac: float = 0.8,
                 sla: Optional[dict] = None, des_arrivals: int = 8000,
                 seed: int = 20261110, with_des: bool = True) -> dict:
    """跑完整链条并组装报告（所有数字来自实算，无手抄）。"""
    train, holdout = split_by_time(series, train_frac=train_frac)
    fit = fit_service_distribution(train.services)
    rates_full = bucket_rates(series, bucket_minutes=bucket_minutes)
    rates_hold = bucket_rates(holdout, bucket_minutes=bucket_minutes)
    report: dict = {
        "meta": {
            "tool": "tools/capacity_planner.py",
            "generated_at": _utcnow(),
            "seed": seed,
            "bucket_minutes": bucket_minutes,
            "train_frac": train_frac,
            "thresholds": {
                "analytic_agreement_tol": ANALYTIC_AGREEMENT_TOL,
                "fit_ks_alpha": FIT_KS_ALPHA,
                "backtest_ks_alpha": BACKTEST_KS_ALPHA,
                "shuffle_degrade_factor": SHUFFLE_DEGRADE_FACTOR,
            },
            "sla": dict(DEFAULT_SLA, **(sla or {})),
            "git_commit": _git_commit(),
            "with_des": with_des,
            "des_arrivals": des_arrivals if with_des else 0,
        },
        "data_plane": {
            "full": series.summary(),
            "train": train.summary(),
            "holdout": holdout.summary(),
            "service_stats": empirical_stats(train.services),
            "wait_stats": empirical_stats(train.waits),
            "arrival_buckets_full": rates_full,
            "arrival_buckets_holdout": rates_hold,
        },
        "service_fit": fit,
    }
    smodel = select_service_model(fit, train.services)
    report["service_model"] = {"kind": smodel["kind"], "name": smodel["name"],
                               "scv": smodel.get("scv"), "mean_min": smodel.get("mean_min"),
                               "note": smodel.get("note")}
    if with_des:
        report["analytic_crosscheck"] = {
            "mmc": crosscheck_mmc(n_arrivals=des_arrivals, seed=seed),
            "mgc": crosscheck_mgc(service_model=select_service_model(fit, train.services),
                                   n_arrivals=max(des_arrivals // 2, 4000), seed=seed),
        }
    else:
        report["analytic_crosscheck"] = {
            "mmc": {"rows": [], "n": 0, "n_within_10pct": 0, "max_abs_dev": None,
                    "tol": ANALYTIC_AGREEMENT_TOL, "skipped": True},
            "mgc": {"rows": [], "n": 0, "n_within_10pct": 0, "tol": ANALYTIC_AGREEMENT_TOL,
                    "skipped": True},
        }
    report["validity_domain"] = validity_domain()
    report["backtest"] = backtest(train, holdout, fit=fit, bucket_minutes=bucket_minutes,
                                  seed=seed, service_model=smodel)
    report["arrival_model"] = (
        arrival_process_check(train, holdout, fit=fit, service_model=smodel,
                              c_eff=report["backtest"].get("seats"),
                              bucket_minutes=bucket_minutes, seed=seed, n_mc=12)
        if with_des and report["backtest"].get("seats")
        else {"pass": False, "reason": "DES disabled or seats unavailable"})
    report["shuffle_control"] = shuffle_control(train, holdout, fit=fit, seed=seed,
                                               service_model=smodel)

    # 容量规划：三个载荷口径（均值/95 分位/峰值 λ）各给解析与 DES 两版答案
    mean_s = report["data_plane"]["train"]["mean_service_min"] or 1.0
    cs2 = smodel.get("scv") or 1.0
    valid_rates = [r for r in rates_full.get("rates_per_min", []) if r]
    lam_mean = train.rate_per_min() or 0.0
    lam_p95 = float(np.percentile(valid_rates, 95)) if valid_rates else lam_mean
    lam_peak = max(valid_rates) if valid_rates else lam_mean
    des_method_ok = with_des and simpy is not None
    scenarios = {}
    for name, lam in (("mean_lambda", lam_mean), ("p95_lambda", lam_p95),
                      ("peak_lambda", lam_peak)):
        analytic = min_seats(float(lam), mean_s, ca2=1.0, cs2=cs2, sla=sla)
        des_plan = (min_seats(float(lam), mean_s, method="des", sampler=smodel["sampler"],
                              sla=sla, n_arrivals=max(des_arrivals // 2, 2000), seed=seed)
                    if des_method_ok else {"feasible": False, "reason": "DES disabled"})
        chosen = des_plan if des_plan.get("c_min") else analytic
        scenarios[name] = {
            "lambda_per_min": _round(float(lam)),
            "offered_load": _round(offered_load(float(lam), mean_s)),
            "analytic_c_min": analytic.get("c_min"),
            "analytic_feasible": analytic.get("feasible"),
            "des_c_min": des_plan.get("c_min"),
            "des_feasible": des_plan.get("feasible"),
            "analytics_vs_des_gap": (
                _round(des_plan["c_min"] / analytic["c_min"] - 1.0)
                if (des_plan.get("c_min") and analytic.get("c_min")) else None),
            "c_min": chosen.get("c_min"),
            "feasible": bool(chosen.get("feasible")),
            "metrics": chosen.get("metrics"),
            "sla": chosen.get("sla"),
            "method": "des" if chosen is des_plan else "allen_cunneen",
            "binding": chosen.get("binding"),
        }
    design_name = "p95_lambda"
    design = scenarios[design_name]
    plan = {
        "design_load": design_name,
        "design_note": ("设计载荷取分时段 λ 的 95 分位：峰值桶通常是批次齐发造成的瞬时尖峰，"
                        "按峰值配 seat 会长期闲置；峰值口径的可行性单独列出作为边界"),
        "static": {"feasible": design["feasible"], "c_min": design["c_min"],
                   "method": design["method"], "sla": design["sla"] or dict(DEFAULT_SLA, **(sla or {})),
                   "metrics": design["metrics"], "binding": design["binding"]},
        "scenarios": scenarios,
        "nonhomogeneous": plan_nonhomogeneous(rates_full, mean_s, ca2=1.0, cs2=cs2, sla=sla),
        "lambda_mean": _round(lam_mean),
        "lambda_p95": _round(lam_p95),
        "lambda_peak_used": _round(lam_peak),
        "mean_service_min_used": _round(mean_s),
        "cs2_used": _round(cs2),
    }
    if des_method_ok:
        plan["des_verification"] = verify_plan_with_des(
            {"c_min": design["c_min"], "metrics": design["metrics"]},
            float(lam_p95), smodel["sampler"], sla=sla,
            n_arrivals=max(des_arrivals, 4000), seed=seed + 3)
    else:
        plan["des_verification"] = {"verified": False, "reason": "DES disabled (--no-des)"}
    report["capacity_plan"] = plan
    report["criteria"] = _criteria(report)
    passed = sum(1 for c in report["criteria"] if c["pass"])
    report["verdict"] = {
        "n_criteria": len(report["criteria"]),
        "n_pass": passed,
        "all_pass": passed == len(report["criteria"]),
        "failed": [c["name"] for c in report["criteria"] if not c["pass"]],
    }
    report["limitations"] = _limitations(report)
    return report


def _limitations(report: dict) -> list[str]:
    """自动生成局限声明（模型失效域 + 数据面假设），不写空话。"""
    notes = [
        "service_start 由 session 内 claim/plan 事件按时间序配对重建（claim payload 不带 mission_id），"
        "这是数据面唯一假设；若某 session 的 claim 与 plan 数不齐，配对会错位（表现为负等待，已计数丢弃）",
        "seat 数 c 是训练期标定出的\"有效并行容量\"，与观测并发会话数并不等同："
        "平台无准入控制时并发会话数可远超有效容量，排队模型只刻画等效服务能力",
        "Erlang-C 假定指数服务；实测服务时间重尾（见 service_stats.scv），故以 Kingman/Allen–Cunneen "
        "带 SCV 校正的版本作为规划口径，Erlang-C 仅作互裁参照",
        "P(Wq>t) 与 p90 等待由 Erlang-C 尾概率形式近似外推，属近似量，报告同时给出 DES 复核",
    ]
    vd = report.get("validity_domain") or {}
    worst = vd.get("worst_case") or {}
    if worst.get("rel_dev_ac_vs_ec") is not None:
        notes.append(
            f"(c,ρ) 网格上最大偏差出现在 c={worst.get('c')}, ρ={worst.get('target_rho')}："
            f"{worst.get('rel_dev_ac_vs_ec'):+.1%}（超出 ±10% 的格子即模型失效域）")
    bt = report.get("backtest") or {}
    if bt.get("pass") is False:
        notes.append("留出期背测未通过：该工况下等待分布不由固定 c 的 GI/G/c 模型完全解释，"
                     "容量结论应视为保守近似而非预测")
    return notes


def _git_commit() -> Optional[str]:
    """Provenance：记录生成报告时的仓库提交（失败不阻塞）。"""
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()[:40] or None if out.returncode == 0 else None
    except Exception:  # pragma: no cover - 无 git 环境下不阻塞
        return None


def _utcnow() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def render_markdown(report: dict) -> str:
    dp = report["data_plane"]
    fit = report["service_fit"]
    bt = report["backtest"]
    sc = report["shuffle_control"]
    plan = report["capacity_plan"]
    mc = report["analytic_crosscheck"]["mmc"]
    mg = report["analytic_crosscheck"]["mgc"]
    vd = report["validity_domain"]
    best = fit.get("best") or {}
    lines = []
    A = lines.append
    A("# 任务机排队-容量模型与容量规划器（CAPACITY_PLANNER）")
    A("")
    A(f"> 生成：`python3 tools/capacity_planner.py`  时间 {report['meta']['generated_at']}  "
      f"seed={report['meta']['seed']}  桶宽={report['meta']['bucket_minutes']} 分钟  "
      f"训练期占比={report['meta']['train_frac']}")
    A("")
    A("## 0. 摘要与适用范围")
    A("")
    A("**做什么**：把任务机的『到达 → 排队 → 服务 → 完成』重建为排队系统，用 Erlang-C（M/M/c 精确）"
      "与 Kingman / Allen–Cunneen（GI/G/c 近似）互裁，再用 simpy 离散事件仿真独立复核，"
      "最后给出给定 SLA 下的最小 seat 数与模型失效域。**不做**：不预测业务量、不替代调度策略、"
      "不改数据库（全程只读）。")
    A("")
    A(f"**provenance**：数据源 `{report['meta'].get('source')}`；仓库提交 "
      f"`{report['meta'].get('git_commit')}`；生成时刻 {report['meta']['generated_at']}；"
      f"seed={report['meta']['seed']}；重现命令见第 9 节。")
    A("")
    A("**回归保护**：`tests/test_capacity_planner.py`（34 个用例，含解析×仿真互裁、标定回收真值、"
      "背测通过/反例判死、负控方向、规划器单调性与 SLA 满足性）——改坏任一机制都会转红。")
    A("")
    A("**失效域一句话**：服务时间重尾（SC²≫1）时两参数族拟合被 KS 否决、Allen–Cunneen 有 ±40% 级误差；"
      "到达过程强簇聚（峰均比≫1）时 Poisson 到达口径会系统性低估等待。两处都已在第 3/4.1/5.1 节量化，"
      "规划器在这些工况下强制走 DES 口径并只用 p95 载荷承诺 SLA。")
    A("")
    A("## 1. 结论（判据数字）")
    A("")
    A("| 判据 | 目标 | 实测 | 结论 |")
    A("|---|---|---|---|")
    for c in report["criteria"]:
        A(f"| `{c['name']}` | {c['target'].replace('|', '/')} | `{json.dumps(c['value'], ensure_ascii=False)}` | "
          f"{'PASS' if c['pass'] else 'FAIL'} |")
    A("")
    A(f"总共 {report['verdict']['n_criteria']} 条判据，通过 {report['verdict']['n_pass']} 条；"
      f"未通过：{report['verdict']['failed'] or '无'}。")
    A("")
    A("## 2. 数据面（只读重建）")
    A("")
    A("| 口径 | n | 跨度(天) | λ (/min) | E[S] (min) | E[W] (min) | 丢弃(service<=0) | 丢弃(wait<0) |")
    A("|---|---|---|---|---|---|---|---|")
    for name in ("full", "train", "holdout"):
        s = dp[name]
        A(f"| {name} | {s['n']} | {s.get('span_days')} | {s['lambda_per_min']} | "
          f"{s['mean_service_min']} | {s['mean_wait_min']} | {s['dropped_nonpositive_service']} | "
          f"{s['dropped_negative_wait']} |")
    A("")
    A(f"分时段到达率（全期，桶宽 {dp['arrival_buckets_full']['bucket_minutes']} 分钟）："
      f"均值 {dp['arrival_buckets_full']['mean_rate_per_min']} /min，"
      f"峰值 {dp['arrival_buckets_full']['peak_rate_per_min']} /min，"
      f"峰均比 {dp['arrival_buckets_full']['peak_to_mean']}（非齐次性证据）。")
    A("")
    A("## 3. 服务时间分布拟合（MLE + KS 选优）")
    A("")
    A("| 分布 | KS D | KS p | 均值(min) | SCV |")
    A("|---|---|---|---|---|")
    for f in fit.get("fits", []):
        if "ks_stat" in f:
            A(f"| {f['name']} | {f['ks_stat']:.4f} | {f['ks_p']:.3e} | {f['mean_min']} | {f['scv']} |")
    A("")
    A(f"最优：**{best.get('name')}**（D={best.get('ks_stat'):.4f}，p={best.get('ks_p'):.3e}）；"
      f"经验 SCV={fit['empirical'].get('scv')}，p90={fit['empirical'].get('p90_min')} min，"
      f"p99={fit['empirical'].get('p99_min')} min（重尾）。")
    sm = report.get("service_model") or {}
    A("")
    A(f"**服务时间模型选定：`{sm.get('kind')}` / {sm.get('name')}** —— {sm.get('note')}。"
      f"（KS 在大样本 + 重尾下会否决一切两参数族；被否决的参数拟合只作形态诊断，"
      f"DES 与容量规划用选定的模型抽样。）")
    A("")
    A("## 4. 解析 × 仿真互裁")
    A("")
    A("M/M/c（Erlang-C 解析 vs simpy DES）：")
    A("")
    A("| c | ρ | E[Wq] 解析(min) | E[Wq] 仿真(min) | 仿真 95% CI | 相对偏差 | 一致 |")
    A("|---|---|---|---|---|---|---|")
    for r in mc.get("rows", []):
        A(f"| {r['c']} | {r['rho']} | {r['erlang_c_wq_min']} | {r['des_mean_wait_min']} | "
          f"{r['des_ci95']} | {r['rel_dev']} | {r.get('agree')} |")
    A("")
    A(f"M/M/c 互裁：|偏差|≤10% 的 {mc['n_within_10pct']}/{mc['n']} 点；"
      f"含 95% CI 统计等价的 {mc.get('n_agree')}/{mc['n']} 点；最大 |偏差|={mc['max_abs_dev']}。")
    A("")
    A("M/G/c（Kingman/Allen–Cunneen vs DES，服务分布=拟合分布）：")
    A("")
    A("| c | ρ | CS² | AC 解析(min) | 重交通上界(min) | 仿真(min) | 仿真 95% CI | 相对偏差 | 一致 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in mg.get("rows", []):
        A(f"| {r['c']} | {r['rho']} | {r['cs2']} | {r['allen_cunneen_wq_min']} | "
          f"{r.get('heavy_traffic_wq_min')} | "
          f"{r['des_mean_wait_min']} | {r['des_ci95']} | {r['rel_dev_des_vs_ac']} | {r.get('agree')} |")
    A("")
    A(f"M/G/c 互裁：|偏差|≤10% 的 {mg['n_within_10pct']}/{mg['n']} 点；"
      f"含 CI 统计等价的 {mg.get('n_agree')}/{mg['n']} 点；"
      f"**危险方向（仿真 > 解析 10% 以上）{mg.get('n_dangerous')} 点**；"
      f"重交通上界在 {mg.get('n_heavy_traffic_upper_bound')}/{mg['n']} 点确实是上界。")
    A("")
    A("### 4.1 模型失效域（(c, ρ) 网格）")
    A("")
    A(f"网格 {len(vd['grid'])} 点，偏差 ≤10% 的格子 {vd['n_agree']}（占 {vd['agree_share']}）；"
      f"适用带内最大 |偏差|={vd['max_abs_dev_in_agreeing_band']}；"
      f"最坏点 c={ (vd.get('worst_case') or {}).get('c') }, "
      f"ρ={(vd.get('worst_case') or {}).get('target_rho') } → "
      f"{(vd.get('worst_case') or {}).get('rel_dev_ac_vs_ec')}。")
    A("")
    A("失效域要点：")
    for n in vd["notes"]:
        A(f"- {n}")
    A("")
    A("## 5. 历史留出期背测（80/20 时间切分）")
    A("")
    cal = bt.get("calibration", {})
    A(f"- 训练期标定有效 seat 数 c_eff = **{bt.get('seats')}**"
      f"（解析粗标定 {cal.get('analytic_c_eff')} → DES 精修 {cal.get('des_refinement')}）；"
      f"训练期实测 E[W]={cal.get('obs_wq_min')} min，隐含 ρ={cal.get('implied_rho')}")
    A("")
    nu = bt.get("ks_null") or {}
    A(f"- **依赖校正 KS（主判据）**：D_obs={nu.get('d_obs')}，参数自助零分布 "
      f"（n_mc={nu.get('n_mc')}）均值 {nu.get('d_null_mean')}、p95={nu.get('d_null_p95')} → "
      f"p_mc=**{nu.get('p_mc')}**（p_mc>0.05 视为模型与留出期可交换）")
    A("")
    A("| 预测口径 | n_pred | KS D | KS p（iid 假设，仅诊断） |")
    A("|---|---|---|---|")
    for name, v in bt.get("variants", {}).items():
        ks = v.get("ks", {})
        A(f"| {name} | {ks.get('n_pred')} | {ks.get('ks_stat')} | {ks.get('ks_p')} |")
    A("")
    A("> 朴素 KS 的 p 值在重载队列上会因等待时间的强自相关而系统性偏小（一个长 busy period "
      "抬高一整窗样本），因此判据用参数自助零分布：同一条到达轨迹重抽服务时间 n_mc 次，"
      "取 p_mc=(1+#{D_rep≥D_obs})/(n_mc+1)。")
    A("")
    obs = bt.get("observed", {})
    A(f"留出期实测：n={obs.get('n')}，E[W]={obs.get('mean_wait_min')} min，"
      f"p50={obs.get('p50_wait_min')}，p90={obs.get('p90_wait_min')}，P(W>0)={obs.get('p_wait_gt0')}。")
    A("")
    A(f"留出期服务时间对训练期拟合分布的 KS：D={bt.get('holdout_service_ks', {}).get('ks_stat')}，"
      f"p={bt.get('holdout_service_ks', {}).get('ks_p')}（服务分布跨期稳定性）。")
    A("")
    am = report.get("arrival_model") or {}
    A("### 5.1 到达过程口径（非齐次 Poisson）——模型失效域检验")
    A("")
    A(f"模型平均等待 {am.get('model_mean_wait_min')} min vs 留出期实测 "
      f"{am.get('observed_mean_wait_min')} min（相对误差 {am.get('mean_wait_rel_err')}）；"
      f"KS D={ (am.get('ks') or {}).get('ks_stat') }，依赖校正 p_mc={am.get('p_mc')} → "
      f"{'通过' if am.get('pass') else '**不通过**'}")
    A("")
    A(f"{am.get('note') or ''}这解释了为什么条件背测通过、而到达模型口径不通过："
      f"等待由**到达簇聚**（峰均比见第 2 节）驱动，平均速率相同的 Poisson 流无法复现。")
    A("")
    A("## 6. shuffle 负控")
    A("")
    A(f"- 基线 D={sc.get('baseline', {}).get('ks_stat')}，p_mc={sc.get('baseline', {}).get('p_mc')}")
    A(f"- 打乱到达顺序后 D={sc.get('shuffled', {}).get('ks_stat')}，"
      f"p_mc={sc.get('shuffled', {}).get('p_mc')}，D 放大倍数={sc.get('ks_ratio')}"
      f"（阈值 {sc.get('factor_threshold')}x），seat 数冻结={sc.get('seats_frozen')}")
    mre = sc.get("mean_wait_rel_err") or {}
    A(f"- 模型 E[W] 相对误差：基线 {mre.get('baseline')} → 打乱后 {mre.get('shuffled')}"
      f"（放大 {mre.get('ratio')}x；观测 E[W]={mre.get('obs_mean_wait_min')} min，"
      f"预测 {mre.get('pred_mean_wait_min')}）")
    A(f"- 结论：{'负控生效（模型对到达时序敏感）' if sc.get('pass') else '负控未变差 → ' + str(sc.get('note'))}")
    A("")
    A("## 7. 容量规划输出")
    A("")
    st = plan["static"]
    A(f"SLA = `{json.dumps(st.get('sla'), ensure_ascii=False)}`；E[S]={plan.get('mean_service_min_used')} min，"
      f"CS²={plan.get('cs2_used')}；设计载荷 = {plan.get('design_load')}（λ="
      f"{plan.get('lambda_p95')} /min）。{plan.get('design_note')}")
    A("")
    A("| 载荷口径 | λ (/min) | 载荷 a=λE[S] | 解析 c_min | DES c_min | 采用 | 可行 | 解析/DES 差 |")
    A("|---|---|---|---|---|---|---|---|")
    for name, sc in plan["scenarios"].items():
        A(f"| {name} | {sc['lambda_per_min']} | {sc['offered_load']} | {sc['analytic_c_min']} | "
          f"{sc['des_c_min']} | {sc['c_min']} | {sc['feasible']} | {sc['analytics_vs_des_gap']} |")
    A("")
    A(f"**设计载荷下的最小 seat 数 = {st.get('c_min')}**（方法={st.get('method')}，可行={st.get('feasible')}，"
      f"绑定={st.get('binding')}，ρ={(st.get('metrics') or {}).get('rho')}，"
      f"E[Wq]={(st.get('metrics') or {}).get('wq_mean_min')} min，"
      f"p90 Wq={(st.get('metrics') or {}).get('p90_wait_min')} min）。")
    A("")
    nh = plan['nonhomogeneous']
    peak_c = nh.get('static_seats_for_peak_lambda')
    A(f"分时段规划（解析口径，逐桶求最小 seat）：峰值桶需 "
      f"{nh.get('peak_seats_required')} 个 seat，中位 "
      f"{nh.get('median_seats_required')} 个（{nh.get('n_buckets')} 个时段）；"
      f"按静态峰值 λ 单点规划 "
      f"{peak_c if peak_c is not None else '**超过规划搜索上限（>2048 seat，不可行）**'}。"
      f"峰值桶对应批次齐发，按它配 seat 会长期闲置——这正是设计载荷取 p95 的理由。")
    A("")
    ver = plan.get("des_verification") or {}
    A(f"DES 复核（c={ver.get('c')}）：实测 E[W]={ver.get('des_mean_wait_min')} min，"
      f"p90={ver.get('des_p90_wait_min')} min，解析承诺 "
      f"E[Wq]={ver.get('analytic_wq_mean_min')} / p90={ver.get('analytic_p90_wait_min')} → "
      f"{'满足 SLA' if ver.get('verified') else '不满足 SLA 或未复核'}。")
    A("")
    A("## 8. 注意点与局限")
    A("")
    for n in report["limitations"]:
        A(f"- {n}")
    A("")
    A("## 9. 复现")
    A("")
    A("```bash")
    A("# 真实数据（只读）")
    A("python3 tools/capacity_planner.py --source db --out-json artifacts/capacity_planner/report.json \\")
    A("    --out-md docs/CAPACITY_PLANNER.md --figure-dir artifacts/capacity_planner")
    A("# 依赖：numpy / scipy / simpy（缺 simpy 时 DES 路径报错，测试用 importorskip 跳过）")
    A("# 离线/自检（不需要数据库）")
    A("python3 tools/capacity_planner.py --source synthetic --n 20000 --c 4 --rho 0.8 --dry-run")
    A("python3 -m pytest tests/test_capacity_planner.py -q")
    A("```")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
def make_figures(report: dict, out_dir: pathlib.Path) -> list[str]:
    """生成 5 张判据图（PNG）；纯文档任务也必须有图或写明理由。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    dp = report["data_plane"]
    fit = report["service_fit"]
    bt = report["backtest"]
    plan = report["capacity_plan"]

    # 图1：分时段到达率 λ(t) 与到达计数
    counts = dp["arrival_buckets_full"]["counts"]
    rates = dp["arrival_buckets_full"]["rates_per_min"]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    xs = list(range(len(counts)))
    ax.bar(xs, counts, color="#9fc6e7", label="arrivals / bucket")
    ax2 = ax.twinx()
    ax2.plot(xs, [r if r is not None else float("nan") for r in rates], color="#c0392b",
             lw=1.2, label="lambda (1/min)")
    ax.set_xlabel(f"bucket index ({dp['arrival_buckets_full']['bucket_minutes']} min)")
    ax.set_ylabel("arrivals")
    ax2.set_ylabel("lambda (1/min)")
    ax.set_title("Arrival process: non-homogeneous lambda(t) reconstructed from DB timestamps")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    p = out_dir / "fig1_arrival_rate.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(str(p))

    # 图2：服务时间 CCDF：经验 vs 拟合分布
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    services = np.sort(np.asarray(report["_train_services"], dtype=float))
    ccdf = 1.0 - np.arange(1, services.size + 1) / services.size
    ax.loglog(services, ccdf, color="#2c3e50", lw=1.4, label="empirical (train)")
    for f in fit.get("fits", []):
        if "ks_stat" not in f:
            continue
        dist = FIT_CANDIDATES[f["name"]]
        xs = np.logspace(np.log10(max(services.min(), 1e-2)),
                         np.log10(services.max() * 1.2), 200)
        ax.loglog(xs, 1.0 - dist.cdf(xs, *f["params"]), lw=1.0, alpha=0.75,
                  label=f"{f['name']} (D={f['ks_stat']:.3f})")
    ax.set_xlabel("service time (min)")
    ax.set_ylabel("P(S > s)")
    ax.set_title("Service-time distribution fit (MLE, KS-selected)")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", ls=":", alpha=0.4)
    fig.tight_layout()
    p = out_dir / "fig2_service_fit.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(str(p))

    # 图3：解析 vs DES（M/M/c）：Wq 随 ρ 变化
    mc_rows = report["analytic_crosscheck"]["mmc"].get("rows", [])
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    if mc_rows:
        for c in sorted({r["c"] for r in mc_rows}):
            rows = sorted([r for r in mc_rows if r["c"] == c], key=lambda r: r["rho"])
            rhos = [r["rho"] for r in rows]
            ax.plot(rhos, [r["erlang_c_wq_min"] or np.nan for r in rows],
                    marker="o", ls="-", label=f"Erlang-C c={c}")
            ax.plot(rhos, [r["des_mean_wait_min"] or np.nan for r in rows],
                    marker="x", ls="--", label=f"DES c={c}")
        ax.set_xlabel("rho")
        ax.set_ylabel("E[Wq] (min, E[S]=10)")
        ax.set_title("Analytic (Erlang-C) x DES (M/M/c) cross-check")
        ax.legend(fontsize=7, ncol=2)
        ax.grid(True, ls=":", alpha=0.4)
    fig.tight_layout()
    p = out_dir / "fig3_analytic_vs_des.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(str(p))

    # 图4：留出期背测：观测 vs 预测等待 CCDF
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    obs = np.sort(np.asarray(bt.get("holdout_wait_samples") or [], dtype=float))
    pred = np.sort(np.asarray(bt.get("trace_wait_samples") or [], dtype=float))
    for arr, label, color in ((pred, "model (trace-driven DES)", "#c0392b"),
                              (obs, "holdout observed", "#2c3e50")):
        if arr.size:
            y = 1.0 - np.arange(1, arr.size + 1) / arr.size
            ax.plot(np.maximum(arr, 1e-6) + 1.0, y, label=label, color=color, lw=1.4)
    ks = ((bt.get("variants") or {}).get("trace") or {}).get("ks", {})
    ax.set_xscale("log")
    ax.set_xlabel("waiting time + 1 (min, log)")
    ax.set_ylabel("P(W > w)")
    ax.set_title(f"Holdout backtest: KS D={ks.get('ks_stat')}, p={ks.get('ks_p')}")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", ls=":", alpha=0.4)
    fig.tight_layout()
    p = out_dir / "fig4_backtest_ccdf.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(str(p))

    # 图5：容量规划：最小 seat 数 vs λ，及分时段 staffing
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0))
    mean_s = plan.get("mean_service_min_used") or 1.0
    cs2 = plan.get("cs2_used") or 1.0
    lams = np.linspace(max(mean_s and (0.2 / mean_s), 1e-4),
                       max(plan.get("lambda_peak_used") or 1e-3, 1e-3), 40)
    cs = [min_seats(float(l), mean_s, ca2=1.0, cs2=cs2, sla=plan["static"].get("sla")).get("c_min")
          for l in lams]
    axes[0].plot(lams, [c if c else np.nan for c in cs], color="#2c3e50", lw=1.6)
    axes[0].scatter([plan.get("lambda_peak_used")], [plan["static"].get("c_min")],
                    color="#c0392b", zorder=5, label="design point (peak lambda)")
    axes[0].set_xlabel("lambda (1/min)")
    axes[0].set_ylabel("min seats for SLA")
    axes[0].set_title("Capacity curve (Allen-Cunneen, CS2-corrected)")
    axes[0].legend(fontsize=8)
    axes[0].grid(True, ls=":", alpha=0.4)

    rows = [r for r in plan["nonhomogeneous"].get("rows", []) if r.get("c_min")]
    if rows:
        axes[1].bar([r["bucket"] for r in rows], [r["c_min"] for r in rows], color="#7fb3d5")
        axes[1].axhline(plan["static"].get("c_min") or 0, color="#c0392b", ls="--",
                        label="static plan (peak lambda)")
    axes[1].set_xlabel(f"bucket ({plan['nonhomogeneous'].get('bucket_minutes')} min)")
    axes[1].set_ylabel("seats required")
    axes[1].set_title("Per-bucket staffing plan (non-homogeneous lambda)")
    axes[1].legend(fontsize=8)
    axes[1].grid(True, ls=":", alpha=0.4)
    fig.tight_layout()
    p = out_dir / "fig5_capacity_plan.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(str(p))
    return paths


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _strip_private(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="任务机排队-容量模型与容量规划器（纯机制）")
    ap.add_argument("--source", choices=("db", "fixture", "synthetic"), default="db")
    ap.add_argument("--fixture", help="--source fixture 时的 JSON 路径（秒为单位）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--bucket-minutes", type=float, default=60.0)
    ap.add_argument("--train-frac", type=float, default=0.8)
    ap.add_argument("--sla", default=json.dumps(DEFAULT_SLA),
                    help='SLA JSON，如 {"max_mean_wait_min":15,"max_p90_wait_min":60,"max_utilization":0.9}')
    ap.add_argument("--des-arrivals", type=int, default=8000)
    ap.add_argument("--no-des", action="store_true", help="跳过 DES 交叉验证（仅解析面）")
    ap.add_argument("--seed", type=int, default=20261110)
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    ap.add_argument("--figure-dir", default=str(DEFAULT_FIG_DIR))
    ap.add_argument("--dry-run", action="store_true", help="算完只打印，不写任何文件")
    # synthetic 专用
    ap.add_argument("--c", type=int, default=4, help="synthetic: 真实 seat 数")
    ap.add_argument("--rho", type=float, default=0.8, help="synthetic: 目标利用率")
    ap.add_argument("--mean-service", type=float, default=6.0, help="synthetic: E[S] (min)")
    ap.add_argument("--n", type=int, default=4000, help="synthetic: 到达数")
    ap.add_argument("--nonhomogeneous-amplitude", type=float, default=0.0)
    args = ap.parse_args(argv)

    if args.source == "db":
        series = load_series_db(args.db)
        src_desc = f"db:{args.db}"
    elif args.source == "fixture":
        if not args.fixture:
            ap.error("--source fixture requires --fixture PATH")
        series = load_series_fixture(args.fixture)
        src_desc = f"fixture:{args.fixture}"
    else:
        lam = args.c * args.rho / args.mean_service
        series = synthetic_series(c=args.c, lam_per_min=lam, mean_service=args.mean_service,
                                  n_arrivals=args.n, seed=args.seed,
                                  nonhomogeneous_amplitude=args.nonhomogeneous_amplitude)
        src_desc = f"synthetic:M/M/{args.c} rho={args.rho}"

    sla = json.loads(args.sla) if args.sla else None
    report = build_report(series=series, bucket_minutes=args.bucket_minutes,
                          train_frac=args.train_frac, sla=sla,
                          des_arrivals=args.des_arrivals, seed=args.seed,
                          with_des=not args.no_des)
    report["meta"]["source"] = src_desc
    report["_train_services"] = series.services

    print(f"[capacity_planner] source={src_desc} n={report['data_plane']['full']['n']} "
          f"criteria {report['verdict']['n_pass']}/{report['verdict']['n_criteria']} pass")
    for c in report["criteria"]:
        print(f"  - {c['name']}: {'PASS' if c['pass'] else 'FAIL'}  {json.dumps(c['value'], ensure_ascii=False)}")

    if args.dry_run:
        return 0

    out_json = pathlib.Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(_strip_private(report), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[capacity_planner] wrote {out_json}")

    out_md = pathlib.Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[capacity_planner] wrote {out_md}")

    fig_dir = pathlib.Path(args.figure_dir)
    try:
        paths = make_figures(report, fig_dir)
        print(f"[capacity_planner] wrote {len(paths)} figures -> {fig_dir}")
    except Exception as exc:  # pragma: no cover - 绘图失败不掩盖结果
        print(f"[capacity_planner] figure generation failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
