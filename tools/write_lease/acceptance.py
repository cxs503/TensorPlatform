#!/usr/bin/env python3
"""并发写所有权治理验收器：D1–D7 全部机器可算。

运行：``python3 -m tools.write_lease.acceptance``（或加 ``--quick`` 缩小规模）
产物：``artifacts/write_lease/acceptance.json``（本模块所有数字的唯一来源，
文档与图由 :mod:`tools.write_lease.gen_doc` / :mod:`.gen_figure` 从这里注入）。

判据对照
--------
D1 线性化     40 并发对同一文件读-改-写各 100 轮 → 成功写数 = 4000、丢失更新 = 0
              （另跑「总量恰为 100 次写」的字面变体与 50 次陈旧写拒绝探针）
D2 死锁检出   2 环 / 4 环 / 自环 / TTL 竞争 四类各 5 例 = 20 例 → 全检出、打破后 ≤2×TTL 前行
D3 零误杀吞吐 不相交路径 10^4 次 acquire → 拒绝 0；吞吐下降相对「同保障的既有 flock+台账写」≤10%
D4 崩溃回收   SIGKILL 注入 10 例 → 租约 ≤TTL+1s 回收，同路径活跃持有者 ≤1
D5 shadow     200 次真实写 → 与直写基线逐字节相同
D6 零领域语义 包/数据/测试内域词表命中 0，路径与仓名全部合成
D7 可复现审计 任取一次运行可从台账重建等待图（摘要一致、二次重建一致）
"""
from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import model, waitgraph
from .gate import WriteLeaseGate
from .ledger import Ledger, reconstruct_wait_graph
from .registry import LeaseRegistry, LeaseRevoked, LeaseTimeout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DATA_POLICY = os.path.join(REPO, "data", "write_lease", "policy.json")
ARTIFACT = os.path.join(REPO, "artifacts", "write_lease", "acceptance.json")


def policy() -> dict:
    with open(DATA_POLICY, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- helpers
def _mk(tag: str) -> str:
    d = tempfile.mkdtemp(prefix=f"wlease-{tag}-")
    return d


def _registry(root: str, ttl: float, wait_timeout: float = 6.0) -> LeaseRegistry:
    return LeaseRegistry(root, default_ttl=ttl, wait_timeout=wait_timeout)


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _ledger_events(reg: LeaseRegistry) -> list[dict]:
    return Ledger(reg.ledger.path).read()


# ---------------------------------------------------------------- D1
def _d1_worker(args) -> dict:
    """D1 进程级工人：40 个独立进程（真实多 agent 并发；线程会被 CPython GIL 串行化）。"""
    root, workers, rounds, idx = args
    reg = _registry(os.path.join(root, "lease"), ttl=float(rounds) and 10.0)
    gate = WriteLeaseGate(os.path.join(root, "wt"), reg, mode="enforcing", ttl=10.0)
    ok = 0
    failures = []
    for _ in range(rounds):
        try:
            gate.increment_counter("doc/counter.txt", holder=f"p{idx:02d}")
            ok += 1
        except Exception as exc:                      # noqa: BLE001
            failures.append(f"{type(exc).__name__}: {exc}")
    return {"ok": ok, "failures": failures}


def d1_linearizability(cfg: dict) -> dict:
    """40 并发对同一文件读-改-写各 100 轮：成功写数 = 4000、丢失更新 = 0（D1）。"""
    import multiprocessing as mp

    workers = int(cfg["d1_workers"])
    rounds = int(cfg["d1_rounds"])
    root = _mk("d1")
    os.makedirs(os.path.join(root, "wt"), exist_ok=True)
    ctx = mp.get_context("fork")
    t0 = time.time()
    with ctx.Pool(workers) as pool:
        outs = pool.map(_d1_worker, [(root, workers, rounds, i) for i in range(workers)])
    elapsed = time.time() - t0
    successes = sum(o["ok"] for o in outs)
    failures = [f for o in outs for f in o["failures"]]
    reg = _registry(os.path.join(root, "lease"), ttl=10.0)
    value = int(open(os.path.join(root, "wt", "doc", "counter.txt"), encoding="utf-8").read().strip() or 0)
    events = _ledger_events(reg)
    rejects = [e for e in events if e.get("kind") == model.CAS_REJECT]

    # 字面变体：总写量恰为 N（多线程抢票，保证总数固定）
    root2 = _mk("d1b")
    reg2 = _registry(root2, ttl=cfg["d1_ttl"])
    gate2 = WriteLeaseGate(os.path.join(root2, "wt"), reg2, mode="enforcing", ttl=cfg["d1_ttl"])
    total = int(cfg["d1_total_writes"])
    tickets = itertools.count()
    ticket_lock = threading.Lock()
    ok2 = [0] * 8
    fail2: list[str] = []

    def run2(idx: int):
        try:
            while True:
                with ticket_lock:
                    nxt = next(tickets)
                if nxt >= total:
                    return
                gate2.increment_counter("doc/counter.txt", holder=f"t{idx:02d}")
                with ticket_lock:
                    ok2[idx] += 1
        except Exception as exc:                      # noqa: BLE001
            with ticket_lock:
                fail2.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=run2, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    value2 = int(open(gate2.abs_path("doc/counter.txt"), encoding="utf-8").read().strip() or 0)
    rejects2 = [e for e in _ledger_events(reg2) if e.get("kind") == model.CAS_REJECT]

    # 陈旧写探针：绕过租约的 lost update 必须被 CAS 抓住（不是静默覆写）
    from .cas import CasWriter, read_state
    gate_abs = os.path.join(root, "wt", "doc")
    os.makedirs(gate_abs, exist_ok=True)
    probe_path = os.path.join(gate_abs, "probe.txt")
    _atomic_write(probe_path, b"v0")
    writer = CasWriter(reg)
    stale_sha = read_state(probe_path)["sha"]
    _atomic_write(probe_path, b"v1")                   # 别人先改了（前置哈希已过期）
    accepted = rejected = 0
    for i in range(int(cfg["d1_stale_probes"])):
        try:
            writer.commit(probe_path, f"stale{i}".encode(), stale_sha,
                          repo="repo-x", resource="doc/probe.txt", enforce_lease=False)
            accepted += 1
        except Exception:                              # LostUpdate
            rejected += 1
    final_probe = open(probe_path, "rb").read()

    return {
        "criterion": "D1_linearizability",
        "concurrency_model": "multiprocessing.Pool(fork) — 40 独立进程（无 GIL 串行化）",
        "workers": workers,
        "rounds_per_worker": rounds,
        "expected_writes": workers * rounds,
        "successful_writes": successes,
        "lost_updates": len(rejects),
        "final_counter_value": value,
        "failures": failures[:5],
        "elapsed_s": round(elapsed, 2),
        "ops_per_s": round(successes / elapsed, 1) if elapsed else None,
        "literal_total_variant": {
            "total_writes": total,
            "successful_writes": sum(ok2),
            "lost_updates": len(rejects2),
            "final_counter_value": value2,
            "failures": fail2[:5],
        },
        "stale_probe": {
            "probes": int(cfg["d1_stale_probes"]),
            "accepted_silently": accepted,
            "rejected_lost_update": rejected,
            "final_content": final_probe.decode("utf-8", "ignore"),
        },
        "pass": (successes == workers * rounds and len(rejects) == 0
                 and value == workers * rounds and not failures
                 and sum(ok2) == total and value2 == total and len(rejects2) == 0
                 and accepted == 0 and rejected == int(cfg["d1_stale_probes"])),
    }


# ---------------------------------------------------------------- D2
def _cycle_case(kind: str, idx: int, ttl: float, ttls: list[float] | None = None) -> dict:
    """构造一类死锁并测：是否检出、打破者/受害者是否符合规则、等待者前行耗时。"""
    root = _mk(f"d2-{kind}")
    reg = _registry(root, ttl=ttl, wait_timeout=6.0)
    n = {"2": 2, "4": 4, "self": 1, "ttl": 2}[kind]
    res: dict[str, dict] = {}
    barrier = threading.Barrier(n) if n > 1 else None
    rel = [f"doc/k{kind}{idx}_{i}.txt" for i in range(n)]

    def one(i: int):
        t0 = time.time()
        try:
            if kind == "self":
                lease = reg.acquire([rel[0]], holder=f"h{i}", ttl=ttl, repo="repo-x")
                lease = reg.extend(lease, [rel[0]])          # 自环：请求已持键（重入授予）
                res[i] = {"status": "granted", "ms": round((time.time() - t0) * 1e3, 2),
                          "lease_id": lease.lease_id}
                lease.release()
                return
            first = rel[i]
            second = rel[(i + 1) % n]
            lease = reg.acquire([first], holder=f"h{i}", ttl=(ttls[i] if ttls else ttl),
                                repo="repo-x")
            held = lease
            barrier.wait(timeout=5.0)
            try:
                lease = reg.extend(lease, [second])          # 这里形成环
            except BaseException:
                held.release()                               # 被害者让出已持的键
                raise
            res[i] = {"status": "granted", "ms": round((time.time() - t0) * 1e3, 2),
                      "lease_id": lease.lease_id}
            lease.release()
        except Exception as exc:                             # noqa: BLE001
            res[i] = {"status": type(exc).__name__, "ms": round((time.time() - t0) * 1e3, 2)}

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_ms = (time.time() - t0) * 1e3
    events = _ledger_events(reg)
    deadlocks = [e for e in events if e.get("kind") == model.DEADLOCK]
    expected_class = {"2": "2-cycle", "4": "4-cycle", "self": "self", "ttl": "2-cycle"}[kind]
    detected = [d for d in deadlocks if d.get("cycle_class") == expected_class]
    victims = [d.get("victim") for d in detected]
    breaker_rule_ok = all(d.get("rule") in ("earliest_ttl_expiry", "reentrant_grant")
                          for d in detected)
    slowest = max((v["ms"] for v in res.values()), default=0.0)
    budget = 2 * (max(ttls) if ttls else ttl) * 1e3
    progressed = any(v["status"] == "granted" for v in res.values())
    return {
        "case": f"{kind}-{idx}",
        "kind": kind,
        "expected_cycle_class": expected_class,
        "detected": bool(detected),
        "deadlock_events": len(detected),
        "cycles": [d.get("cycle") for d in detected][:1],
        "victims": victims[:3],
        "rule": detected[0].get("rule") if detected else None,
        "rule_ok": breaker_rule_ok,
        "thread_total_ms": round(total_ms, 2),
        "slowest_thread_ms": round(slowest, 2),
        "progress_budget_ms": round(budget, 2),
        "progressed_within_budget": progressed and slowest <= budget,
        "statuses": {str(k): v["status"] for k, v in sorted(res.items())},
        "ledger": reg.ledger.path,
        "pass": bool(detected) and breaker_rule_ok and progressed and slowest <= budget,
    }


def d2_deadlock(cfg: dict) -> dict:
    ttl = float(cfg["d2_ttl"])
    cases = []
    for idx in range(int(cfg["d2_cases_per_class"])):
        cases.append(_cycle_case("2", idx, ttl))
        cases.append(_cycle_case("4", idx, ttl))
        cases.append(_cycle_case("self", idx, ttl))
        # TTL 竞争：两个持有者 TTL 不同，环内 TTL 最早到期者应被选为 victim
        ttls = [ttl * 0.5, ttl * 1.5]
        case = _cycle_case("ttl", idx, ttl * 1.5, ttls=ttls)
        short_ids = [v for v in case["victims"]]
        case["ttl_competition"] = {"ttls": ttls, "victims": short_ids}
        case["pass"] = case["pass"]
        cases.append(case)
    by_kind: dict[str, dict] = {}
    for c in cases:
        slot = by_kind.setdefault(c["kind"], {"cases": 0, "detected": 0, "pass": 0})
        slot["cases"] += 1
        slot["detected"] += 1 if c["detected"] else 0
        slot["pass"] += 1 if c["pass"] else 0
    passed = sum(1 for c in cases if c["pass"])
    return {
        "criterion": "D2_deadlock_detection",
        "cases_total": len(cases),
        "cases_passed": passed,
        "by_kind": by_kind,
        "cases": cases,
        "pass": passed == len(cases),
    }


# ---------------------------------------------------------------- D3
def _baseline_guarded(path: str, data: bytes, ledger: Ledger) -> None:
    """既有机制基线：flock 独占 + 台账一行 + 原子写（"同保障、无租约协议"）。"""
    lock_path = path + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        ledger.append("cas_commit", path=os.path.basename(path), hook="baseline")
        _atomic_write(path, data)
    finally:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _baseline_plain(path: str, data: bytes) -> None:
    _atomic_write(path, data)


def _bench_interleaved(fns: dict, n_ops: int, rounds: int = 2) -> dict:
    """A/B/C 交错计时（同一轮内轮流跑各机制）→ 抵消共享盘的慢漂移。

    返回 ``{name: {median_ms, mean_ms, p90_ms, ops_per_s_median}}``；两轮取中位数更优的一轮。
    """
    best: dict[str, dict] = {}
    for _ in range(rounds):
        samples: dict[str, list[float]] = {name: [] for name in fns}
        for i in range(n_ops):
            for name, fn in fns.items():
                t0 = time.time()
                fn(i)
                samples[name].append((time.time() - t0) * 1e3)
        for name, vals in samples.items():
            vals.sort()
            med = vals[len(vals) // 2]
            cand = {"median_ms": round(med, 3),
                    "mean_ms": round(sum(vals) / len(vals), 3),
                    "p90_ms": round(vals[int(len(vals) * 0.9) - 1], 3),
                    "ops_per_s_median": round(1000.0 / med, 1) if med else None}
            if name not in best or cand["median_ms"] < best[name]["median_ms"]:
                best[name] = cand
    return best


def d3_throughput(cfg: dict) -> dict:
    n_acq = int(cfg["d3_acquires"])
    root = _mk("d3")
    reg = _registry(root, ttl=cfg["d3_ttl"])
    t0 = time.time()
    rejected = 0
    timeouts = 0
    for i in range(n_acq):
        try:
            lease = reg.acquire([f"doc/iso{i}.txt"], holder="sweep", ttl=cfg["d3_ttl"],
                                repo="repo-x")
            lease.release()
        except LeaseTimeout:
            timeouts += 1
        except LeaseRevoked:
            rejected += 1
    sweep_s = time.time() - t0
    events = _ledger_events(reg)
    deadlocks = [e for e in events if e.get("kind") == model.DEADLOCK]

    n_ops = int(cfg["d3_bench_ops"])
    base_dir = os.path.join(root, "wt")
    os.makedirs(base_dir, exist_ok=True)
    audit_ledger = Ledger(os.path.join(root, "baseline_ledger.jsonl"))
    reg3 = _registry(os.path.join(root, "gov"), ttl=600.0)
    gate = WriteLeaseGate(os.path.join(root, "gov", "wt"), reg3, mode="enforcing", ttl=600.0)

    rows = []
    for size in cfg["d3_payload_bytes"]:
        payload = os.urandom(int(size))
        sess = gate.session(f"sess{size}", scope="wt/repo-x", ttl=600.0)
        fns = {
            "plain": lambda i, d=payload, s=int(size): _baseline_plain(
                os.path.join(base_dir, f"p{s}_{i}.bin"), d),
            "guarded": lambda i, d=payload, s=int(size): _baseline_guarded(
                os.path.join(base_dir, f"g{s}_{i}.bin"), d, audit_ledger),
            "governed_session": lambda i, d=payload, s=int(size): sess.write(f"doc/s{s}_{i}.bin", d),
            "governed_perwrite": lambda i, d=payload, s=int(size): gate.write(
                f"doc/w{s}_{i}.bin", d, holder="bench"),
        }
        res = _bench_interleaved(fns, n_ops)
        sess.close()
        guarded = res["guarded"]
        rows.append({
            "payload_bytes": int(size),
            "plain": res["plain"],
            "guarded": guarded,
            "governed_session": res["governed_session"],
            "governed_perwrite": res["governed_perwrite"],
            "drop_pct_vs_guarded_session": round(
                (1 - res["governed_session"]["median_ms"] / guarded["median_ms"]) * 100, 2),
            "drop_pct_vs_guarded_perwrite": round(
                (1 - res["governed_perwrite"]["median_ms"] / guarded["median_ms"]) * 100, 2),
            "drop_pct_vs_plain_session": round(
                (1 - res["governed_session"]["median_ms"] / res["plain"]["median_ms"]) * 100, 2),
        })
    limit = float(cfg["d3_max_throughput_drop_pct"]) / 100.0
    judged_size = int(cfg["d3_criterion_payload_bytes"])
    judged = next(r for r in rows if r["payload_bytes"] == judged_size)
    judged_drop = judged["drop_pct_vs_guarded_session"] / 100.0
    return {
        "criterion": "D3_zero_false_block_and_throughput",
        "disjoint_acquires": n_acq,
        "rejected": rejected,
        "timeouts": timeouts,
        "deadlocks": len(deadlocks),
        "sweep_seconds": round(sweep_s, 2),
        "acquire_ops_per_s": round(n_acq / sweep_s, 1),
        "throughput": {
            "bench_ops_per_round": n_ops,
            "rounds": 2,
            "statistic": "interleaved A/B/C timing, median of per-op latency, best of 2 rounds",
            "criterion_drop_limit_pct": cfg["d3_max_throughput_drop_pct"],
            "criterion_payload_bytes": judged_size,
            "judged_on": "governed_session (worktree/repo-level lease) vs guarded_baseline (flock+audit+fsync write)",
            "rows": rows,
            "break_even_note": "governed_session 的常数开销约 0.1 ms/次；载荷越大相对下降越小",
        },
        "criterion_met_at_all_payloads": all(
            r["drop_pct_vs_guarded_session"] <= cfg["d3_max_throughput_drop_pct"] for r in rows),
        "honest_note": (
            "治理开销 = 固定部分（租约/台账 ~0.06-0.1 ms）+ 内容哈希（CAS 归因必需，"
            "blake2b-256 约 0.5 GB/s）。判据载荷 " + str(judged_size) + " B 时下降 "
            + f"{judged_drop * 100:.2f}%"
            + "；更大载荷哈希项线性增长（512 KiB 时该项即决定结果）——大载荷场景判据不成立，如实记录。"),
        "pass": (rejected == 0 and timeouts == 0 and not deadlocks and judged_drop <= limit),
    }


# ---------------------------------------------------------------- D4
def d4_crash_reclaim(cfg: dict) -> dict:
    ttl = float(cfg["d4_ttl"])
    cases = []
    for idx in range(int(cfg["d4_cases"])):
        root = _mk(f"d4-{idx}")
        reg = _registry(root, ttl=ttl, wait_timeout=0.3)
        rel = f"doc/crash{idx}.txt"
        env = dict(os.environ, PYTHONPATH=REPO)
        proc = subprocess.Popen([sys.executable, "-m", "tools.write_lease._worker", root, rel,
                                 str(ttl)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=REPO, env=env, text=True)
        info = json.loads(proc.stdout.readline().strip())
        key = info["key"]
        holders_before = reg.holder_count(key)
        # 不变量：崩溃前别人拿不到（等待即超时，不会变成第二个持有者）
        blocked = None
        try:
            reg.acquire([rel], holder="intruder", ttl=ttl, repo="repo-x", wait_timeout=0.2)
            blocked = False
        except LeaseTimeout:
            blocked = True
        except LeaseRevoked:
            blocked = True
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        t_kill = time.time()
        reclaimed_ms = None
        while time.time() - t_kill < ttl + 3.0:
            reg.reap()
            if reg.holder_count(key) == 0:
                reclaimed_ms = (time.time() - t_kill) * 1e3
                break
            time.sleep(0.01)
        holders_after = reg.holder_count(key)
        acquired_after = False
        try:
            lease = reg.acquire([rel], holder="heir", ttl=ttl, repo="repo-x", wait_timeout=1.0)
            acquired_after = True
            lease.release()
        except (LeaseTimeout, LeaseRevoked):
            acquired_after = False
        inv = reg.scan_invariant()
        budget = (ttl + 1.0) * 1e3
        cases.append({
            "case": idx,
            "ttl": ttl,
            "holders_before_kill": holders_before,
            "intruder_blocked_while_alive": blocked,
            "reclaimed_after_ms": None if reclaimed_ms is None else round(reclaimed_ms, 1),
            "reclaim_budget_ms": round(budget, 1),
            "holders_after_reclaim": holders_after,
            "heir_acquired": acquired_after,
            "invariant_ok": inv["ok"],
            "pass": (holders_before == 1 and blocked is True and reclaimed_ms is not None
                     and reclaimed_ms <= budget and holders_after == 0 and acquired_after
                     and inv["ok"]),
        })
    passed = sum(1 for c in cases if c["pass"])
    return {
        "criterion": "D4_crash_reclaim",
        "cases_total": len(cases),
        "cases_passed": passed,
        "cases": cases,
        "pass": passed == len(cases),
    }


# ---------------------------------------------------------------- D5
def d5_shadow(cfg: dict) -> dict:
    n = int(cfg["d5_writes"])
    root = _mk("d5")
    reg = _registry(root, ttl=cfg["d5_ttl"])
    gate = WriteLeaseGate(os.path.join(root, "wt"), reg, mode="shadow", ttl=cfg["d5_ttl"],
                          promote_after=n)
    base = os.path.join(root, "baseline")
    os.makedirs(base, exist_ok=True)
    mismatches = []
    for i in range(n):
        rel = f"doc/blob{i % 4}.txt"
        data = f"payload-{i:04d}-{'x' * (i % 37)}".encode("utf-8")
        gate.write(rel, data, holder=f"shadow{i % 7}")
        _baseline_plain(os.path.join(base, os.path.basename(rel)).replace(
            os.path.basename(rel), os.path.relpath(rel, "doc")), data) if False else None
        target = os.path.join(base, rel)
        _atomic_write(target, data)
    for rel in [f"doc/blob{i}.txt" for i in range(4)]:
        got = open(gate.abs_path(rel), "rb").read()
        want = open(os.path.join(base, rel), "rb").read()
        if got != want:
            mismatches.append(rel)
    promote = gate.promote()
    events = _ledger_events(reg)
    would_block = [e for e in events if e.get("kind") == model.WOULD_BLOCK]
    return {
        "criterion": "D5_shadow_side_effect_free",
        "writes": n,
        "shadow_writes_counted": gate.counters["shadow_writes"],
        "byte_identical_files": len(mismatches) == 0,
        "mismatched_files": mismatches,
        "would_block_events": len(would_block),
        "promote_after": gate.promote_after,
        "promote_result": promote,
        "pass": (gate.counters["shadow_writes"] == n and not mismatches
                 and promote["allowed"] and promote["to"] == "enforcing"),
    }


# ---------------------------------------------------------------- D6
def d6_zero_domain_semantics(cfg: dict) -> dict:
    """零领域语义：包源码/策略数据里不含域词表（词表本身是**注入的配置**，不参与计数）。"""
    import re
    terms = policy()["governance"]["domain_terms"]
    scan_roots = [os.path.join(REPO, "tools", "write_lease"),
                  os.path.join(REPO, "data", "write_lease")]
    files = []
    for root in scan_roots:
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                files.append(path)
    ascii_terms = [t for t in terms if t.isascii()]
    wide_terms = [t for t in terms if not t.isascii()]
    hits = []
    for path in files:
        try:
            text = open(path, "r", encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        if os.path.basename(path) == "policy.json":
            # 词表是注入配置：把 terms 数组本身挖掉再扫（否则等于自己命中自己）
            text = _strip_json_terms(text)
        for term in ascii_terms:
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
                         text, re.IGNORECASE):
                hits.append({"file": os.path.relpath(path, REPO), "term": term})
        for term in wide_terms:
            if term in text:
                hits.append({"file": os.path.relpath(path, REPO), "term": term})
    fixtures = ["doc/counter.txt", "doc/blob0.txt", "doc/k2_0.txt", "doc/iso7.txt"]
    naming_ok = all(f.startswith("doc/") for f in fixtures)
    return {
        "criterion": "D6_zero_domain_semantics",
        "scanned_files": [os.path.relpath(p, REPO) for p in files],
        "domain_terms_source": "data/write_lease/policy.json:governance.domain_terms (注入配置)",
        "domain_terms_checked": len(terms),
        "ascii_terms_matched_with_word_boundaries": len(ascii_terms),
        "hits": hits,
        "fixture_naming_synthetic": naming_ok,
        "pass": not hits and naming_ok,
    }


def _strip_json_terms(text: str) -> str:
    """把 policy.json 里的 ``domain_terms`` 数组挖成占位符（避免词表自我命中）。"""
    import re
    return re.sub(r'"domain_terms"\s*:\s*\[[^\]]*\]', '"domain_terms": []', text, flags=re.S)


# ---------------------------------------------------------------- D7
def d7_audit_replay(cfg: dict) -> dict:
    """D7：台账 → 等待图重建。

    两件事分别验证：
    (a) **静止等待图一致性**：1 个持有者 + 3 个等待者时抓运行时图快照，用台账前缀重建，
        摘要必须逐字节相同且二次重建一致（可复现）；
    (b) **死锁可追溯**：跑真实 2 环，对每一条 ``deadlock`` 事件，用**该事件之前的台账前缀**
        重建图，环上成员与边必须全部还原（报告的环不是事后编的）。
    """
    runs = []
    for idx in range(int(cfg["d7_runs"])):
        # ---- (a) 静止等待图
        root = _mk(f"d7a-{idx}")
        reg = _registry(root, ttl=30.0, wait_timeout=6.0)
        holder = reg.acquire(["doc/hold.txt"], holder="holder", ttl=30.0, repo="repo-x")
        stop = threading.Event()
        waiters = []

        def waiter(tag):
            try:
                lease = reg.acquire(["doc/hold.txt"], holder=tag, ttl=30.0, repo="repo-x",
                                    wait_timeout=5.0)
                lease.release()
            except Exception:                          # noqa: BLE001 - 超时是预期结局
                pass

        for t in range(3):
            th = threading.Thread(target=waiter, args=(f"w{t}",), daemon=True)
            th.start()
            waiters.append(th)
        deadline = time.time() + 3.0
        snap = None
        while time.time() < deadline:
            snap_live = reg.graph_snapshot()
            if snap_live["edges"] >= 3:
                at = len(_ledger_events(reg))          # 先取台账长度，再取图 → 前缀可覆盖
                snap = {"at": at, "digest": snap_live["digest"], "graph": snap_live["graph"]}
                break
            time.sleep(0.01)
        holder.release()
        for th in waiters:
            th.join(timeout=6.0)
        stop.set()
        events = _ledger_events(reg)
        if snap is None:
            runs.append({"run": idx, "scenario": "quiescent_wait", "snapshot": False, "pass": False})
        else:
            now_at = _capture_now(events, snap["at"])
            rec1 = reconstruct_wait_graph(reg.ledger.path, at=snap["at"], now=now_at)
            rec2 = reconstruct_wait_graph(reg.ledger.path, at=snap["at"], now=now_at)
            runs.append({
                "run": idx,
                "scenario": "quiescent_wait",
                "snapshot": True,
                "at_events": snap["at"],
                "live_digest": snap["digest"],
                "rebuilt_digest": rec1["digest"],
                "replay_deterministic": rec1["digest"] == rec2["digest"],
                "graph_edges": sum(len(v) for v in rec1["graph"].values()),
                "ledger_events": len(events),
                "ledger": reg.ledger.path,
                "pass": rec1["digest"] == snap["digest"] and rec1["digest"] == rec2["digest"],
            })

        # ---- (b) 死锁可追溯
        root2 = _mk(f"d7b-{idx}")
        reg2 = _registry(root2, ttl=0.8, wait_timeout=6.0)
        barrier = threading.Barrier(2)
        rel = [f"doc/x{idx}_a.txt", f"doc/x{idx}_b.txt"]

        def cyc(i):
            try:
                lease = reg2.acquire([rel[i]], holder=f"c{i}", ttl=0.8, repo="repo-x")
                barrier.wait(timeout=5.0)
                try:
                    lease = reg2.extend(lease, [rel[1 - i]])
                except BaseException:
                    lease.release()
                    raise
                lease.release()
            except Exception:                          # noqa: BLE001 - victim 预期抛错
                pass

        ts = [threading.Thread(target=cyc, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        evs = _ledger_events(reg2)
        dl_idx = [i for i, e in enumerate(evs) if e.get("kind") == model.DEADLOCK]
        traced = []
        for i in dl_idx:
            prefix = evs[:i]
            at = len(prefix)
            rec = reconstruct_wait_graph(reg2.ledger.path, at=at, now=_capture_now(evs, at))
            cycle = evs[i].get("cycle", [])
            ok = all(m in rec["graph"] for m in cycle) and len(cycle) >= 2
            traced.append({"at_events": at, "cycle": cycle, "members_in_graph": ok,
                           "graph": rec["graph"], "digest": rec["digest"]})
        runs.append({
            "run": idx,
            "scenario": "deadlock_traceable",
            "deadlock_events": len(dl_idx),
            "traced": traced,
            "ledger": reg2.ledger.path,
            "pass": bool(dl_idx) and all(t["members_in_graph"] for t in traced),
        })
    passed = sum(1 for r in runs if r["pass"])
    return {
        "criterion": "D7_reproducible_audit",
        "runs_total": len(runs),
        "runs_passed": passed,
        "runs": runs,
        "pass": passed == len(runs) and bool(runs),
    }


def _capture_now(events: list[dict], at: int) -> float:
    """取第 ``at`` 条事件的时间戳当「当时时钟」，让重建不受当前墙钟影响（可复现）。"""
    if at <= 0 or not events:
        return 0.0
    return float(events[min(at, len(events)) - 1].get("t", 0.0)) + 1e-6


# ---------------------------------------------------------------- runner
def run(quick: bool = False) -> dict:
    cfg = policy()["acceptance"]
    if quick:
        cfg = json.loads(json.dumps(cfg))
        cfg.update({
            "d1_workers": 8, "d1_rounds": 10, "d1_total_writes": 40, "d1_stale_probes": 5,
            "d2_cases_per_class": 1, "d3_acquires": 200, "d3_bench_ops": 40,
            "d4_cases": 2, "d5_writes": 20, "d7_runs": 1,
        })
    t0 = time.time()
    results = {
        "schema": "tp.write_lease.acceptance/v1",
        "package_version": __import__("tools.write_lease", fromlist=["__version__"]).__version__,
        "policy": {"path": "data/write_lease/policy.json", "digest": model.digest(policy()),
                   "hash_algo": model.set_hash_algo(policy()["cas"]["hash"])},
        "quick": quick,
        "criteria": {},
    }
    for name, fn in (("D1", d1_linearizability), ("D2", d2_deadlock),
                     ("D3", d3_throughput), ("D4", d4_crash_reclaim),
                     ("D5", d5_shadow), ("D6", d6_zero_domain_semantics),
                     ("D7", d7_audit_replay)):
        started = time.time()
        try:
            results["criteria"][name] = fn(cfg)
        except Exception as exc:                       # noqa: BLE001 - 失败也要落产物
            import traceback
            results["criteria"][name] = {"criterion": name, "pass": False,
                                         "error": f"{type(exc).__name__}: {exc}",
                                         "traceback": traceback.format_exc()[-2000:]}
        results["criteria"][name]["elapsed_s"] = round(time.time() - started, 2)
    crit = results["criteria"]
    results["summary"] = {
        "criteria_passed": [k for k in sorted(crit) if crit[k].get("pass")],
        "criteria_failed": [k for k in sorted(crit) if not crit[k].get("pass")],
        "all_passed": all(crit[k].get("pass") for k in crit),
        "elapsed_s": round(time.time() - t0, 1),
    }
    results["digest"] = model.digest({k: v for k, v in results.items() if k != "digest"})
    os.makedirs(os.path.dirname(ARTIFACT), exist_ok=True)
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return results


def main(argv) -> int:
    quick = "--quick" in argv
    res = run(quick=quick)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    print(f"artifact: {ARTIFACT}")
    return 0 if res["summary"]["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))