"""cycle_baseline.simulator —— 三策略调度模拟器 + 理论预测 + 从 trace 反算公平指数。

为什么需要它
------------
真实语料只有现存这一个调度策略，无法回答"换策略会不会更公平"。要闭合预注册比较，必须有
可控、可复现、**带解析预测**的夹具台：同一份负载喂给三种策略，测 Jain/Gini，与该策略的
名义份额解析值逐项对比。

策略 = 派发规则（不是记账后处理）
--------------------------------
* ``round_robin``：全局 FIFO，按轮转指针把任务派进各 worker 私有队列（计数严格均分）。
* ``sticky``：按 repo 亲和固定指派（工位粘性；平台存在大量专属角色 worker）。
* ``weighted``：派给 ``served_i / w_i`` 最小者（等价 WFQ 虚拟时间最小者），
  名义份额 ρ_i = w_i/Σw。

**反例记录（值得留档）**：最初把 weighted 实现成"有空闲 worker 就派给它、按 w/(n+1) 打分"，
结果 weights=[1,2,3,4] 下计数仍近似均分（Jain 0.9993 vs 理论 0.433）——因为存在并行空位时
分配由"谁先干完"决定，权重根本不构成约束。必须先**目标派发**（决定谁收这活），再由 worker
按私有队列顺序执行，权重才真正生效。这条踩坑已写入报告"注意点"。

解析预测
--------
J(ρ) = (Σρ)²/(nΣρ²)、G(ρ)（ρ 为名义份额）。判据 (a) 的"Jain 恢复"更进一步：**只看 trace**
（谁做了哪些 job）用与真实平台相同的外部分析口径反算 Jain，与理论值比误差 ≤0.02。
"""
from __future__ import annotations

import heapq
import random
from typing import Any, Optional, Sequence

from . import spec
from .stats import gini, gini_from_weights, jain_from_weights, jain_index, rel_err


# ── 负载与工位 ──────────────────────────────────────────────────────────
def make_workload(n_jobs: int = 240, repos: Sequence[str] = ("r0", "r1", "r2", "r3"),
                  repo_probs: Optional[Sequence[float]] = None,
                  mean_service_s: float = 600.0, ivl_s: float = 90.0,
                  seed: int = spec.DEFAULT_SEED) -> list[dict[str, Any]]:
    """FIFO 到达的合成负载：repo 权重、服务时间（lognormal）、到达间隔（指数）。"""
    rng = random.Random(seed)
    probs = list(repo_probs) if repo_probs else [1.0 / len(repos)] * len(repos)
    jobs: list[dict[str, Any]] = []
    t = 0.0
    for i in range(n_jobs):
        t += rng.expovariate(1.0 / ivl_s)
        r = rng.random()
        acc = 0.0
        repo = repos[-1]
        for name, p in zip(repos, probs):
            acc += p
            if r <= acc:
                repo = name
                break
        service = float(rng.lognormvariate(0.0, 0.35)) * mean_service_s
        jobs.append({"job_id": i, "repo": repo, "service_s": service, "arrival_s": t})
    return jobs


def make_workers(n: int = 4, speeds: Optional[Sequence[float]] = None) -> list[dict[str, Any]]:
    sp = list(speeds) if speeds else [1.0] * n
    return [{"worker_id": f"w{i}", "speed": sp[i]} for i in range(len(sp))]


# ── 派发规则（目标派发：决定"谁收这活"） ────────────────────────────────
class Dispatcher:
    def __init__(self, name: str, workers: list[dict], jobs: list[dict]):
        self.name = name
        self.wids = [w["worker_id"] for w in workers]
        self.weights = {w["worker_id"]: max(float(w.get("weight", 1.0)), 1e-9)
                        for w in workers}
        self.served = {w: 0 for w in self.wids}
        self._rr = 0
        if name == "sticky":
            repos = sorted({j["repo"] for j in jobs})
            self.affinity = {r: self.wids[i % len(self.wids)]
                             for i, r in enumerate(repos)}
        else:
            self.affinity = {}

    def target(self, job: dict) -> str:
        if self.name == "round_robin":
            wid = self.wids[self._rr % len(self.wids)]
            self._rr += 1
        elif self.name == "sticky":
            wid = self.affinity[job["repo"]]
        elif self.name == "weighted":
            wid = min(self.wids, key=lambda x: (self.served[x] / self.weights[x], x))
        else:
            raise ValueError(f"unknown policy {self.name}")
        self.served[wid] += 1
        return wid


def nominal_shares(policy: str, workers: list[dict], jobs: list[dict]) -> dict[str, float]:
    """策略的名义份额 ρ（解析预测的输入）。"""
    wids = [w["worker_id"] for w in workers]
    if policy == "round_robin":
        return {w: 1.0 / len(wids) for w in wids}
    if policy == "weighted":
        ws = [max(float(w.get("weight", 1.0)), 1e-9) for w in workers]
        tot = sum(ws)
        return {w["worker_id"]: (x / tot) for w, x in zip(workers, ws)}
    if policy == "sticky":
        repos = sorted({j["repo"] for j in jobs})
        aff = {r: wids[i % len(wids)] for i, r in enumerate(repos)}
        counts = {w: 0.0 for w in wids}
        for j in jobs:
            counts[aff[j["repo"]]] += 1.0
        n = sum(counts.values()) or 1.0
        return {w: counts[w] / n for w in wids}
    raise ValueError(f"unknown policy {policy}")


# ── 事件驱动模拟 ────────────────────────────────────────────────────────
def simulate(policy: str, jobs: list[dict], workers: list[dict]) -> dict[str, Any]:
    """公共 FIFO → 目标派发进私有队列 → worker 逐个执行；返回 trace 与实测份额。"""
    wmap = {w["worker_id"]: w for w in workers}
    for w in workers:
        w.setdefault("weight", 1.0)
    wids = [w["worker_id"] for w in workers]
    disp = Dispatcher(policy, workers, jobs)
    pending = sorted(jobs, key=lambda j: (j["arrival_s"], j["job_id"]))
    busy: dict[str, Optional[float]] = {w: None for w in wids}
    private: dict[str, list[dict]] = {w: [] for w in wids}
    heap: list[tuple[float, int, str]] = []
    trace: list[dict[str, Any]] = []
    now_jobs: list[dict] = []
    seq = 0
    idx = 0
    t = 0.0

    def start_ready(tnow: float) -> None:
        nonlocal seq
        for wid in wids:
            if busy[wid] is not None or not private[wid]:
                continue
            job = private[wid].pop(0)
            busy[wid] = tnow
            dur = job["service_s"] / max(float(wmap[wid]["speed"]), 1e-9)
            seq += 1
            heapq.heappush(heap, (tnow + dur, seq, wid))
            trace.append({"job_id": job["job_id"], "worker": wid, "repo": job["repo"],
                          "arrival_s": job["arrival_s"], "start_s": tnow,
                          "end_s": tnow + dur, "service_s": job["service_s"],
                          "wait_s": tnow - job["arrival_s"]})

    while True:
        next_arr = pending[idx]["arrival_s"] if idx < len(pending) else float("inf")
        next_end = heap[0][0] if heap else float("inf")
        if next_arr == float("inf") and next_end == float("inf"):
            break
        t = min(next_arr, next_end)
        while idx < len(pending) and pending[idx]["arrival_s"] <= t:
            now_jobs.append(pending[idx])
            idx += 1
        while heap and heap[0][0] <= t:
            _, _, wid = heapq.heappop(heap)
            busy[wid] = None
        while now_jobs:
            job = now_jobs.pop(0)
            private[disp.target(job)].append(job)
        start_ready(t)

    counts = {w: 0.0 for w in wids}
    busy_t = {w: 0.0 for w in wids}
    for ev in trace:
        counts[ev["worker"]] += 1.0
        busy_t[ev["worker"]] += (ev["end_s"] - ev["start_s"])
    total_busy = sum(busy_t.values())
    return {
        "policy": policy,
        "n_jobs": len(trace),
        "trace": trace,
        "counts": counts,
        "busy_time_s": busy_t,
        "share_count": {w: (counts[w] / len(trace) if trace else None) for w in wids},
        "share_busy_time": {w: (busy_t[w] / total_busy if total_busy else None) for w in wids},
        "makespan_s": max((e["end_s"] for e in trace), default=None),
        "mean_wait_s": (sum(e["wait_s"] for e in trace) / len(trace)) if trace else None,
    }


# ── 从 trace 反算公平指数（平台侧黑盒观测口径） ─────────────────────────
def _per_worker(trace: Sequence[dict], wids: Sequence[str], basis: str) -> list[float]:
    vals = {w: 0.0 for w in wids}
    for ev in trace:
        w = ev.get("worker")
        if w not in vals:
            continue
        if basis == "count":
            vals[w] += 1.0
        else:
            vals[w] += float(ev.get("end_s", 0.0)) - float(ev.get("start_s", 0.0))
    return [vals[w] for w in wids]


def jain_from_trace(trace: Sequence[dict], wids: Sequence[str], basis: str = "count") -> Optional[float]:
    return jain_index(_per_worker(trace, wids, basis))


def gini_from_trace(trace: Sequence[dict], wids: Sequence[str], basis: str = "count") -> Optional[float]:
    return gini(_per_worker(trace, wids, basis))


def jain_with_observation_cutoff(trace: Sequence[dict], wids: Sequence[str],
                                 cutoff_frac: float = 0.9) -> dict[str, Any]:
    """模拟"观测窗口在 makespan 的 cutoff_frac 处关闭"：只统计窗口内完成的作业。

    真实平台永远在窗口边界处开口（在飞单未完成），因此从 trace 反算的 Jain 必然有偏。
    这里量化该偏差，作为判据 (a) 的鲁棒性检查（偏差应远小于 0.02 容差）。
    """
    if not trace:
        return {"cutoff_s": None, "jain": None, "n_observed": 0, "n_total": 0}
    makespan = max(e["end_s"] for e in trace)
    cutoff = makespan * cutoff_frac
    observed = [e for e in trace if e["end_s"] <= cutoff]
    return {"cutoff_s": cutoff, "cutoff_frac": cutoff_frac,
            "jain": jain_from_trace(observed, wids, "count"),
            "n_observed": len(observed), "n_total": len(trace)}


def evaluate_policies(n_jobs: int = 240, n_workers: int = 4,
                      weights: Optional[Sequence[float]] = None,
                      speeds: Optional[Sequence[float]] = None,
                      repo_probs: Optional[Sequence[float]] = None,
                      seed: int = spec.DEFAULT_SEED) -> dict[str, Any]:
    """三策略 × 解析预测 × trace 反算；逐项误差与判据 (a)/(e) 判定。"""
    jobs = make_workload(n_jobs=n_jobs, repo_probs=repo_probs, seed=seed)
    workers = make_workers(n_workers, speeds)
    if weights:
        for w, wt in zip(workers, weights):
            w["weight"] = float(wt)
    wids = [w["worker_id"] for w in workers]
    out: dict[str, Any] = {
        "seed": seed, "n_jobs": n_jobs, "n_workers": n_workers,
        "weights": list(weights) if weights else None,
        "speeds": list(speeds) if speeds else None,
        "policies": {},
        "tolerances": {"jain_recovery_tol": spec.JAIN_RECOVERY_TOL,
                       "policy_theory_tol": spec.POLICY_THEORY_TOL},
    }
    for policy in ("round_robin", "sticky", "weighted"):
        sim = simulate(policy, [dict(j) for j in jobs], [dict(w) for w in workers])
        rho = nominal_shares(policy, workers, jobs)
        rho_vec = [rho[w] for w in wids]
        j_theory = jain_from_weights(rho_vec)
        g_theory = gini_from_weights(rho_vec)
        j_count = jain_from_trace(sim["trace"], wids, "count")
        g_count = gini_from_trace(sim["trace"], wids, "count")
        waits = [e["wait_s"] for e in sim["trace"]]
        _cut = jain_with_observation_cutoff(sim["trace"], wids)
        if _cut["jain"] is not None and j_theory is not None:
            _cut["abs_err"] = abs(_cut["jain"] - j_theory)
        else:
            _cut["abs_err"] = None
        out["policies"][policy] = {
            "nominal_share": rho,
            "theory": {"jain": j_theory, "gini": g_theory},
            "measured": {
                "share_count": sim["share_count"],
                "share_busy_time": sim["share_busy_time"],
                "jain_count": j_count, "gini_count": g_count,
                "jain_busy_time": jain_from_trace(sim["trace"], wids, "busy"),
                "gini_busy_time": gini_from_trace(sim["trace"], wids, "busy"),
                "mean_wait_s": sim["mean_wait_s"],
                "wait_gini": gini(waits) if waits else None,
                "makespan_s": sim["makespan_s"],
            },
            "err": {
                "jain_recovery_abs": None if (j_count is None or j_theory is None)
                                      else abs(j_count - j_theory),
                "jain_rel": rel_err(j_count, j_theory),
                "gini_rel": rel_err(g_count, g_theory),
            },
            "pass_jain_recovery": None if (j_count is None or j_theory is None)
                                 else abs(j_count - j_theory) <= spec.JAIN_RECOVERY_TOL,
            "recovery_cutoff": _cut,
            "pass_theory": (rel_err(j_count, j_theory) is not None
                            and rel_err(j_count, j_theory) <= spec.POLICY_THEORY_TOL
                            and rel_err(g_count, g_theory) is not None
                            and rel_err(g_count, g_theory) <= spec.POLICY_THEORY_TOL),
        }
    out["verdict"] = {
        "jain_recovery_ok": all(p["pass_jain_recovery"] for p in out["policies"].values()),
        "theory_ok": all(p["pass_theory"] for p in out["policies"].values()),
        "jain_recovery_tol": spec.JAIN_RECOVERY_TOL,
        "policy_theory_tol": spec.POLICY_THEORY_TOL,
    }
    out["spread"] = {
        "jain_count_range": [min(p["measured"]["jain_count"] for p in out["policies"].values()),
                             max(p["measured"]["jain_count"] for p in out["policies"].values())],
        "gini_count_range": [min(p["measured"]["gini_count"] for p in out["policies"].values()),
                             max(p["measured"]["gini_count"] for p in out["policies"].values())],
        "mean_wait_range_s": [min(p["measured"]["mean_wait_s"] for p in out["policies"].values()),
                              max(p["measured"]["mean_wait_s"] for p in out["policies"].values())],
    }
    return out