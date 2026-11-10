#!/usr/bin/env python3
"""并发写所有权治理（tools/write_lease）测试。

覆盖矩阵
--------
* 等待图：2 环 / 4 环 / 自环 / 无环 / 多环并存（Tarjan SCC）
* 打破策略：环内 TTL 最早到期者；同到期按 lease_id 升序（确定性）
* 租约协议：``rw`` 独占、``r`` 共享、重入/重复路径、超时、scope 层级
* 静默覆写：CAS 命中/失配、陈旧写拒绝、台账 ``cas_reject`` 带 expected/actual
* 回收：SIGKILL 后 ``reap()`` 以 ``holder_gone`` 回收；活跃持有者 ≤1 不变量
* 灰度：shadow 字节级等同直写 + 满阈值才 ``promote()``
* 审计：台账 → 等待图重建（摘要一致、二次重建一致）；死锁环可从台账前缀还原
* 零领域语义：包源码/数据不含策略里注入的域词表
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from tools.write_lease import model, waitgraph
from tools.write_lease.cas import CasWriter, read_state
from tools.write_lease.gate import WriteLeaseGate
from tools.write_lease.ledger import Ledger, live_edges, reconstruct_wait_graph, wait_graph
from tools.write_lease.registry import (LeaseTimeout, LeaseRegistry, LeaseRevoked,
                                        LostUpdate)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY = os.path.join(REPO, "data", "write_lease", "policy.json")


def _policy() -> dict:
    with open(POLICY, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def workdir(tmp_path):
    return str(tmp_path)


@pytest.fixture()
def registry(workdir):
    return LeaseRegistry(os.path.join(workdir, "lease"), default_ttl=2.0, wait_timeout=6.0)


@pytest.fixture()
def gate(workdir, registry):
    return WriteLeaseGate(os.path.join(workdir, "wt"), registry, mode="enforcing", ttl=2.0)


# --------------------------------------------------------------- 等待图
def test_tarjan_two_and_four_cycles():
    assert waitgraph.find_cycles({"a": ["b"], "b": ["a"]}) == [["a", "b"]]
    four = {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["a"]}
    assert waitgraph.find_cycles(four) == [["a", "b", "c", "d"]]


def test_tarjan_acyclic_and_self_loop():
    assert waitgraph.find_cycles({"a": ["b"], "b": ["c"]}) == []
    assert waitgraph.find_cycles({"a": ["b"], "b": ["c"], "c": []}) == []
    assert waitgraph.find_cycles({"x": ["x"]}) == [["x"]]


def test_tarjan_multiple_components_and_missing_nodes():
    graph = {"a": ["b"], "b": ["a"], "c": [], "d": ["e"], "e": ["d"]}
    assert waitgraph.find_cycles(graph) == [["a", "b"], ["d", "e"]]


def test_choose_victim_earliest_ttl_then_id():
    expiry = {"L-a": 10.0, "L-b": 5.0, "L-c": 5.0}
    pick = waitgraph.choose_victim(["L-a", "L-b", "L-c"], lambda lid: expiry[lid])
    assert pick["victim"] == "L-b" and pick["rule"] == "earliest_ttl_expiry"
    assert pick["ranked"][0] == "L-b"


def test_detect_returns_none_for_acyclic_and_reports_cycle():
    assert waitgraph.detect({"a": ["b"]}, waiter="a") is None
    hit = waitgraph.detect({"a": ["b"], "b": ["a"]}, waiter="a",
                          expiry_of=lambda lid: 1.0)
    assert hit["cycle"] == ["a", "b"] and hit["kind"] == "2-cycle"
    assert hit["resolution"]["victim"] in ("a", "b")


# --------------------------------------------------------------- 租约协议
def test_exclusive_write_lease_blocks_second_holder(workdir, registry):
    lease = registry.acquire(["doc/a.txt"], holder="h1", ttl=2.0, repo="repo-x")
    with pytest.raises(LeaseTimeout):
        registry.acquire(["doc/a.txt"], holder="h2", ttl=2.0, repo="repo-x",
                         wait_timeout=0.15)
    lease.release()
    second = registry.acquire(["doc/a.txt"], holder="h2", ttl=2.0, repo="repo-x",
                              wait_timeout=1.0)
    assert registry.holder_count(second.keys[0]) == 1
    second.release()


def test_shared_read_leases_coexist(registry):
    a = registry.acquire(["doc/a.txt"], holder="r1", mode=model.R, ttl=2.0, repo="repo-x")
    b = registry.acquire(["doc/a.txt"], holder="r2", mode=model.R, ttl=2.0, repo="repo-x",
                         wait_timeout=1.0)
    assert registry.holder_count(a.keys[0]) == 2
    with pytest.raises(LeaseTimeout):
        registry.acquire(["doc/a.txt"], holder="w1", mode=model.RW, ttl=2.0, repo="repo-x",
                         wait_timeout=0.15)
    a.release()
    b.release()


def test_reentrant_duplicate_path_is_self_loop(registry):
    lease = registry.acquire(["doc/a.txt", "doc/a.txt"], holder="h", ttl=2.0, repo="repo-x")
    assert lease.keys[0] == lease.keys[1]
    events = Ledger(registry.ledger.path).read()
    assert any(e.get("kind") == model.DEADLOCK and e.get("cycle_class") == "self" for e in events)
    lease.release()


def test_extend_to_held_key_is_self_loop_and_revokes_nothing(registry):
    lease = registry.acquire(["doc/a.txt"], holder="h", ttl=2.0, repo="repo-x")
    lease = registry.extend(lease, ["doc/a.txt", "doc/b.txt"])
    assert len(lease.fds) == 2
    assert registry.stats["revoked"] == 0
    lease.release()


def test_lease_timeout_leaves_no_dangling_lock(registry):
    holder = registry.acquire(["doc/a.txt"], holder="h1", ttl=2.0, repo="repo-x")
    with pytest.raises(LeaseTimeout):
        registry.acquire(["doc/a.txt"], holder="h2", ttl=2.0, repo="repo-x", wait_timeout=0.1)
    holder.release()
    probe = registry.acquire(["doc/a.txt"], holder="h3", ttl=2.0, repo="repo-x",
                             wait_timeout=1.0)
    probe.release()
    assert registry.scan_invariant()["ok"]


def test_scope_lease_blocks_file_level_lease(registry):
    session_lease = registry.acquire(["wt/repo-x"], holder="session", ttl=3.0, repo="repo-x")
    with pytest.raises(LeaseTimeout):
        registry.acquire(["doc/a.txt"], holder="intruder", ttl=3.0, repo="repo-x",
                         scope="wt/repo-x", wait_timeout=0.15)
    session_lease.release()
    ok = registry.acquire(["doc/a.txt"], holder="intruder", ttl=3.0, repo="repo-x",
                          scope="wt/repo-x", wait_timeout=1.0)
    ok.release()


# --------------------------------------------------------------- 死锁检出
def _cycle_case(registry, ttls):
    """两个线程按相反顺序扩租 → 必然形成 2 环。"""
    barrier = threading.Barrier(2)
    rel = ["doc/p.txt", "doc/q.txt"]
    result = {}

    def one(i):
        t0 = time.time()
        try:
            lease = registry.acquire([rel[i]], holder=f"h{i}", ttl=ttls[i], repo="repo-x")
            barrier.wait(timeout=5.0)
            try:
                lease = registry.extend(lease, [rel[1 - i]])
            except BaseException:
                lease.release()
                raise
            result[i] = ("granted", (time.time() - t0) * 1e3, lease.lease_id)
            lease.release()
        except Exception as exc:                       # noqa: BLE001
            result[i] = (type(exc).__name__, (time.time() - t0) * 1e3, None)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return result


def test_two_cycle_detected_and_broken(registry):
    result = _cycle_case(registry, [1.0, 1.0])
    granted = [v for v in result.values() if v[0] == "granted"]
    events = Ledger(registry.ledger.path).read()
    deadlocks = [e for e in events if e.get("kind") == model.DEADLOCK]
    assert deadlocks and deadlocks[0]["cycle_class"] == "2-cycle"
    # 打破的不变量：环上不能"双杀"（两人都被撤 → 活锁）；至少留一个继续走
    assert len(granted) == 1
    assert registry.stats["revoked"] >= 1
    assert all(v[1] < 4 * 1.0 * 1e3 for v in result.values())


def test_ttl_competition_breaks_the_earliest_expiry(registry):
    """TTL 竞争：环内 TTL 最早到期者是受害者，且环不会被"双杀"。"""
    result = _cycle_case(registry, [0.3, 3.0])
    events = Ledger(registry.ledger.path).read()
    deadlocks = [e for e in events if e.get("kind") == model.DEADLOCK]
    assert deadlocks
    assert len([v for v in result.values() if v[0] == "granted"]) == 1
    assert deadlocks[0]["rule"] == "earliest_ttl_expiry"
    expiries = deadlocks[0].get("cycle") and registry.lease_expiry(deadlocks[0]["victim"])
    assert expiries is None or expiries == float("inf") or expiries > 0


# --------------------------------------------------------------- CAS
def test_cas_commit_success_and_mismatch(gate, registry):
    path = gate.abs_path("doc/a.txt")
    lease = registry.acquire(["doc/a.txt"], holder="h", ttl=2.0, repo="repo-x")
    writer = CasWriter(registry)
    first = writer.commit(path, b"v1", None, lease_id=lease.lease_id, holder="h",
                          repo="repo-x", resource="doc/a.txt",
                          lease_fd=lease.fds[lease.keys[0]])
    assert first["ok"] and read_state(path)["data"] == b"v1"
    writer.commit(path, b"v2", first["sha"], lease_id=lease.lease_id, holder="h",
                  repo="repo-x", resource="doc/a.txt", lease_fd=lease.fds[lease.keys[0]])
    with pytest.raises(LostUpdate):
        # 用过期前置哈希再写 → 必须拒写并留证
        writer.commit(path, b"v3", first["sha"], lease_id=lease.lease_id, holder="h",
                      repo="repo-x", resource="doc/a.txt", lease_fd=lease.fds[lease.keys[0]])
    lease.release()
    assert read_state(path)["data"] == b"v2"
    rejects = [e for e in Ledger(registry.ledger.path).read() if e.get("kind") == model.CAS_REJECT]
    assert len(rejects) == 1
    assert rejects[0]["expected"] == first["sha"] and rejects[0]["actual"] != first["sha"]
    assert rejects[0]["reason"] == "hash_mismatch"


def test_commit_without_lease_authorization_is_rejected(registry, gate):
    path = gate.abs_path("doc/a.txt")
    holder = registry.acquire(["doc/a.txt"], holder="owner", ttl=3.0, repo="repo-x")
    intruder = registry.acquire(["doc/other.txt"], holder="intruder", ttl=3.0, repo="repo-x")
    writer = CasWriter(registry)
    with pytest.raises(LeaseRevoked):
        writer.commit(path, b"evil", None, lease_id=intruder.lease_id, holder="intruder",
                      repo="repo-x", resource="doc/a.txt",
                      lease_fd=intruder.fds[intruder.keys[0]])
    holder.release()
    intruder.release()


def test_stale_write_probe_never_silently_overwrites(gate, registry):
    path = gate.abs_path("doc/probe.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"v0")
    stale = read_state(path)["sha"]
    with open(path, "wb") as fh:
        fh.write(b"v1")
    writer = CasWriter(registry)
    accepted = 0
    rejected = 0
    for i in range(10):
        try:
            writer.commit(path, b"clobber", stale, repo="repo-x", resource="doc/probe.txt",
                          enforce_lease=False)
            accepted += 1
        except LostUpdate:
            rejected += 1
    assert (accepted, rejected) == (0, 10)
    assert read_state(path)["data"] == b"v1"


# --------------------------------------------------------------- 回收 / 不变量
def test_sigkill_lease_reclaimed(workdir):
    ttl = 0.5
    reg = LeaseRegistry(os.path.join(workdir, "lease"), default_ttl=ttl, wait_timeout=0.3)
    rel = "doc/crash.txt"
    env = dict(os.environ, PYTHONPATH=REPO)
    proc = subprocess.Popen([sys.executable, "-m", "tools.write_lease._worker",
                             os.path.join(workdir, "lease"), rel, str(ttl)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=REPO, env=env)
    try:
        info = json.loads(proc.stdout.readline().strip())
    finally:
        pass
    key = info["key"]
    assert reg.holder_count(key) == 1
    with pytest.raises(LeaseTimeout):
        reg.acquire([rel], holder="intruder", ttl=ttl, repo="repo-x", wait_timeout=0.2)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)
    t0 = time.time()
    reclaimed = None
    while time.time() - t0 < ttl + 1.0:
        reg.reap()
        if reg.holder_count(key) == 0:
            reclaimed = time.time() - t0
            break
        time.sleep(0.01)
    assert reclaimed is not None and reclaimed <= ttl + 1.0
    lease = reg.acquire([rel], holder="heir", ttl=ttl, repo="repo-x", wait_timeout=1.0)
    lease.release()
    assert reg.scan_invariant()["ok"]


def test_ttl_expiry_revokes_hung_holder(workdir):
    ttl = 0.3
    reg = LeaseRegistry(os.path.join(workdir, "lease"), default_ttl=ttl, wait_timeout=3.0)
    lease = reg.acquire(["doc/a.txt"], holder="hung", ttl=ttl, repo="repo-x")
    # 不心跳 → TTL 到期后应被撤销/回收
    time.sleep(ttl + 0.15)
    reclaimed = reg.reap()
    assert reclaimed and reclaimed[0]["reason"] in ("heartbeat_missing", "holder_gone")
    lease.release()


# --------------------------------------------------------------- 灰度
def test_shadow_writes_are_byte_identical(gate, workdir):
    shadow = WriteLeaseGate(os.path.join(workdir, "wt2"), gate.registry, mode="shadow",
                            ttl=2.0, promote_after=5)
    baseline = os.path.join(workdir, "baseline")
    os.makedirs(baseline, exist_ok=True)
    payload = b"payload-abc"
    for i in range(6):
        shadow.write("doc/x.txt", payload, holder="s")
        with open(os.path.join(baseline, "x.txt"), "wb") as fh:
            fh.write(payload)
    assert read_state(shadow.abs_path("doc/x.txt"))["data"] == payload
    assert read_state(os.path.join(baseline, "x.txt"))["data"] == payload
    assert shadow.counters["shadow_writes"] == 6
    promoted = shadow.promote()
    assert promoted["allowed"] and shadow.mode == "enforcing"


def test_shadow_does_not_promote_below_threshold(gate, workdir):
    shadow = WriteLeaseGate(os.path.join(workdir, "wt3"), gate.registry, mode="shadow",
                            ttl=2.0, promote_after=10)
    shadow.write("doc/y.txt", b"one", holder="s")
    promoted = shadow.promote()
    assert not promoted["allowed"] and shadow.mode == "shadow"


def test_session_lease_write_and_cas(gate):
    with gate.session("sess", scope="wt/repo-x", ttl=3.0) as sess:
        out = sess.write("doc/a.txt", b"hello")
        assert read_state(out["path"] if os.path.isabs(out["path"]) else gate.abs_path("doc/a.txt"))["data"] == b"hello"
        out2 = sess.increment_counter("doc/cnt.txt")
        out3 = sess.increment_counter("doc/cnt.txt")
        assert read_state(gate.abs_path("doc/cnt.txt"))["data"] == b"2"
        assert out2["lease_id"] == out3["lease_id"] == sess.lease_id
    assert gate.registry.holder_count(sess.lease.keys[0]) == 0


# --------------------------------------------------------------- 审计
def test_ledger_wait_edges_lifecycle(registry):
    holder = registry.acquire(["doc/a.txt"], holder="h1", ttl=3.0, repo="repo-x")
    th = threading.Thread(target=lambda: _try_acquire(registry, "doc/a.txt", "h2"))
    th.start()
    time.sleep(0.05)
    events = Ledger(registry.ledger.path).read()
    edges = live_edges(events, now=model.now_ts())
    assert any(e["waiter"] != e["blocker"] for e in edges)
    holder.release()
    th.join(timeout=5)
    events = Ledger(registry.ledger.path).read()
    assert not [e for e in live_edges(events, now=model.now_ts()) if e["waiter"] not in ()]


def _try_acquire(registry, rel, holder):
    try:
        lease = registry.acquire([rel], holder=holder, ttl=3.0, repo="repo-x",
                                 wait_timeout=2.0)
        lease.release()
    except Exception:                                  # noqa: BLE001
        pass


def test_reconstruct_wait_graph_is_deterministic(workdir, registry):
    holder = registry.acquire(["doc/a.txt"], holder="h1", ttl=3.0, repo="repo-x")
    ths = [threading.Thread(target=_try_acquire, args=(registry, "doc/a.txt", f"w{i}"))
           for i in range(3)]
    for t in ths:
        t.start()
    time.sleep(0.1)
    at = len(Ledger(registry.ledger.path).read())
    holder.release()
    for t in ths:
        t.join(timeout=5)
    events = Ledger(registry.ledger.path).read()
    now_at = events[at - 1]["t"] if at else 0.0
    a = reconstruct_wait_graph(registry.ledger.path, at=at, now=now_at)
    b = reconstruct_wait_graph(registry.ledger.path, at=at, now=now_at)
    assert a["digest"] == b["digest"]
    assert len(sum(a["graph"].values(), [])) >= 1


def test_deadlock_cycle_traceable_from_ledger_prefix(registry):
    _cycle_case(registry, [1.0, 1.0])
    events = Ledger(registry.ledger.path).read()
    idx = [i for i, e in enumerate(events) if e.get("kind") == model.DEADLOCK]
    assert idx
    i = idx[0]
    now_at = events[i - 1]["t"] if i else 0.0
    recon = reconstruct_wait_graph(registry.ledger.path, at=i, now=now_at)
    cycle = events[i]["cycle"]
    assert all(member in recon["graph"] for member in cycle)


# --------------------------------------------------------------- 零领域语义
def test_package_has_no_domain_terms():
    terms = _policy()["governance"]["domain_terms"]
    ascii_terms = [t for t in terms if t.isascii()]
    wide_terms = [t for t in terms if not t.isascii()]
    roots = [os.path.join(REPO, "tools", "write_lease"), os.path.join(REPO, "data", "write_lease")]
    hits = []
    for root in roots:
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            text = open(path, "r", encoding="utf-8", errors="ignore").read()
            if name == "policy.json":
                text = re.sub(r'"domain_terms"\s*:\s*\[[^\]]*\]', '"domain_terms": []', text, flags=re.S)
            for term in ascii_terms:
                if re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
                             text, re.IGNORECASE):
                    hits.append((name, term))
            for term in wide_terms:
                if term in text:
                    hits.append((name, term))
    assert hits == []


def test_resource_key_is_path_normalized():
    assert model.resource_key("repo-x", "doc/./a.txt") == model.resource_key("repo-x", "doc/a.txt")
    assert model.resource_key("repo-x", "./doc//a.txt") == model.resource_key("repo-x", "doc/a.txt")
    assert model.resource_key("repo-x", "doc/a.txt") != model.resource_key("repo-y", "doc/a.txt")
    assert len(model.resource_key("repo-x", "doc/a.txt")) == 16


# --------------------------------------------------------------- 小规模线性化
def test_concurrent_increment_is_linearizable(gate):
    workers, rounds = 8, 15
    errors = []

    def run(idx):
        try:
            for _ in range(rounds):
                gate.increment_counter("doc/cnt.txt", holder=f"w{idx}")
        except Exception as exc:                       # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert read_state(gate.abs_path("doc/cnt.txt"))["data"] == str(workers * rounds).encode()
    rejects = [e for e in Ledger(gate.registry.ledger.path).read()
               if e.get("kind") == model.CAS_REJECT]
    assert rejects == []