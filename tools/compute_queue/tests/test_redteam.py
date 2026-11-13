"""红队机器门（验收标准 ⑥）—— 必须可失败：
  Gate 1  32 核作业必须落节点不落本机
  Gate 2  本机帽打满时新作业必须排队不挤占控制面
  Gate 3  控制面保留属性（local 在跑核数永 <= 24）
  Gate 4  CLI 传 --node 结构性拒绝
  Gate 5  端到端：ComputeQueue.cycle 真实派发路径（fake 执行器）
"""
import json
import subprocess
import sys
import pathlib

import pytest

from tools.compute_queue.compute_queue import ComputeQueue
from tools.compute_queue.ledger import InMemoryLedger
from tools.compute_queue.scheduler import (
    JobSpec, HostInfo, Policy, LOCAL_ID, decide_placement)

HERE = pathlib.Path(__file__).resolve().parent.parent


def H(id, ok=True, loadr=0.2, cores=128, mem=400, used=0, jobs=0):
    return HostInfo(id=id, ok=ok, loadr=loadr, cores=cores, mem_free_g=mem,
                    used_cores=used, running_jobs=jobs)


def make_cq(local_used=0, local_ok=True, local_loadr=0.2,
            n315=True, n330=True, n315_loadr=0.3, n330_loadr=0.5):
    L = InMemoryLedger()
    hosts = {
        LOCAL_ID: H(LOCAL_ID, ok=local_ok, loadr=local_loadr,
                    used=local_used),
        "n315": H("n315", ok=n315, loadr=n315_loadr),
        "n330": H("n330", ok=n330, loadr=n330_loadr),
    }
    cq = ComputeQueue(L, node_probe=lambda: {
        "n315": {"ok": n315, "loadr": n315_loadr, "cores": 128,
                  "mem_free_g": 400},
        "n330": {"ok": n330, "loadr": n330_loadr, "cores": 128,
                  "mem_free_g": 400}},
        local_probe=lambda: hosts[LOCAL_ID])
    return cq, L, hosts


# ── Gate 1 ───────────────────────────────────────────────────────────
def test_gate1_32core_must_land_on_node():
    cq, L, _ = make_cq()
    out = cq.submit(JobSpec(cmd="nproc", repo="R", cores=32))
    assert "jid" in out, out
    cq.cycle()
    g = L.get(int(out["jid"][2:]))
    assert g["state"] == "running", g
    assert g["placement"] in ("n315", "n330"), g
    assert g["placement"] != LOCAL_ID, "红队失败：32 核作业落到了本机 99"


# ── Gate 2 ───────────────────────────────────────────────────────────
def test_gate2_local_cap_full_must_queue():
    # local 帽 24 打满 + 双节点全挂 → 小作业必须排队
    cq, L, _ = make_cq(local_used=24, n315=False, n330=False)
    out = cq.submit(JobSpec(cmd="echo hi", repo="R", cores=4))
    assert "jid" in out
    cq.cycle()
    g = L.get(int(out["jid"][2:]))
    assert g["state"] == "queued", g
    assert g["placement"] is None, "红队失败：本机帽满仍派发"


def test_gate2b_local_cap_full_node_free_goes_node_not_local():
    cq, L, _ = make_cq(local_used=24)
    out = cq.submit(JobSpec(cmd="echo hi", repo="R", cores=4))
    cq.cycle()
    g = L.get(int(out["jid"][2:]))
    assert g["placement"] in ("n315", "n330"), g


# ── Gate 3 ───────────────────────────────────────────────────────────
def test_gate3_control_plane_reservation():
    p = Policy()
    for used in range(0, 25):
        for req in (1, 4, 8, 16, 32):
            hs = {LOCAL_ID: H(LOCAL_ID, used=used),
                  "n315": H("n315", ok=False), "n330": H("n330", ok=False)}
            pl = decide_placement(JobSpec(cmd="x", repo="R", cores=req), hs)
            if pl.placement == LOCAL_ID:
                assert used + req <= p.local_agent_cap_cores, \
                    "红队失败：local 占用 %d+%d 超过 24 核帽" % (used, req)


# ── Gate 4：CLI 结构性拒绝 --node ────────────────────────────────────
def test_gate4_cli_refuses_node_flag():
    r = subprocess.run(
        [sys.executable, str(HERE / "compute_queue.py"), "submit",
         "--cmd", "x", "--repo", "R", "--node", "n315"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "REFUSED" in r.stdout
    assert "--node" in r.stdout


def test_gate4b_cli_refuses_without_worktree():
    r = subprocess.run(
        [sys.executable, str(HERE / "compute_queue.py"), "submit",
         "--cmd", "x"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "REFUSED" in r.stdout


# ── Gate 5：端到端派发（fake 执行器，验账本字段） ────────────────────
class FakeLocal:
    def build_argv(self, spec, wd, strategy, jid):
        return ["true"]

    def run(self, *a, **k):
        raise NotImplementedError


class FakeNode:
    def argv(self, spec, node, jid):
        return ["true"]

    def run(self, spec, node, jid):
        return 0


def test_gate5_e2e_dispatch_fields():
    L = InMemoryLedger()
    hosts = {LOCAL_ID: H(LOCAL_ID, loadr=0.9),   # 本机过载
             "n315": H("n315", loadr=0.3),
             "n330": H("n330", loadr=0.5)}
    cq = ComputeQueue(L, node_probe=lambda: {
        "n315": {"ok": True, "loadr": 0.3, "cores": 128, "mem_free_g": 400},
        "n330": {"ok": True, "loadr": 0.5, "cores": 128, "mem_free_g": 400}},
        local_probe=lambda: hosts[LOCAL_ID])
    # 打桩执行器：不真跑
    cq._dispatch_local = lambda *a, **k: None
    cq._dispatch_node = lambda row, jid, spec, node, logp: None
    out = cq.submit(JobSpec(cmd="nproc", repo="R", cores=32))
    cq.cycle()
    g = L.get(int(out["jid"][2:]))
    assert g["state"] == "running"
    assert g["placement"] == "n315"          # 最低负载节点
    assert g["wait_s"] is not None and g["wait_s"] >= 0
    assert g["node"] == "n315"


def test_gate5b_local_dispatch_when_nodes_down():
    L = InMemoryLedger()
    hosts = {LOCAL_ID: H(LOCAL_ID, loadr=0.2),
             "n315": H("n315", ok=False), "n330": H("n330", ok=False)}
    cq = ComputeQueue(L, node_probe=lambda: {
        "n315": {"ok": False}, "n330": {"ok": False}},
        local_probe=lambda: hosts[LOCAL_ID])
    cq._dispatch_local = lambda *a, **k: None
    out = cq.submit(JobSpec(cmd="echo hi", repo="R", cores=4))
    cq.cycle()
    g = L.get(int(out["jid"][2:]))
    assert g["state"] == "running" and g["placement"] == LOCAL_ID
