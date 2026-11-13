"""裁决面单测：统一负载表 + 容量感知放置（红队靶面）。"""
import pytest

from tools.compute_queue.scheduler import (
    JobSpec, HostInfo, Policy, Placement, LOCAL_ID, decide_placement)


def H(id, ok=True, loadr=0.2, cores=128, mem=400, used=0, jobs=0):
    return HostInfo(id=id, ok=ok, loadr=loadr, cores=cores, mem_free_g=mem,
                    used_cores=used, running_jobs=jobs)


def hosts(local_used=0, local_ok=True, local_loadr=0.2, n315=True, n330=True,
          n315_loadr=0.3, n330_loadr=0.5, n315_used=0, n330_used=0):
    return {
        LOCAL_ID: H(LOCAL_ID, ok=local_ok, loadr=local_loadr, used=local_used),
        "n315": H("n315", ok=n315, loadr=n315_loadr, used=n315_used),
        "n330": H("n330", ok=n330, loadr=n330_loadr, used=n330_used),
    }


# ── 红队 Gate 1：32 核作业必须落节点不落本机 ─────────────────────────
def test_redteam_32core_goes_to_node():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=32), hosts())
    assert pl.placement in ("n315", "n330"), pl
    assert pl.placement != LOCAL_ID


def test_redteam_32core_even_if_local_idle():
    # local 完全空闲也不能要 32 核（单作业上限 8）
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=32),
                          hosts(local_used=0, local_loadr=0.0))
    assert pl.placement in ("n315", "n330")


def test_32core_nodes_down_queues():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=32),
                          hosts(n315=False, n330=False))
    assert pl.placement is None and "queue" in pl.reason


# ── 红队 Gate 2：本机帽打满 → 排队，不挤占控制面 ─────────────────────
def test_redteam_local_cap_full_queues():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4),
                          hosts(local_used=24, n315=False, n330=False))
    assert pl.placement is None
    assert "local-cap-full" in pl.reason


def test_redteam_local_cap_full_node_available_goes_node():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4),
                          hosts(local_used=24))
    assert pl.placement in ("n315", "n330")
    assert pl.placement != LOCAL_ID


# ── 控制面保留：总帽 24 核（属性网格） ───────────────────────────────
@pytest.mark.parametrize("used,req", [
    (0, 8), (8, 8), (16, 8), (20, 4), (24, 1), (17, 8), (23, 2), (24, 8)])
def test_local_cap_boundary(used, req):
    # 节点全挂，local 是唯一候选：used+req <= 24 才许落 local
    hs = hosts(local_used=used, n315=False, n330=False)
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=req), hs)
    if used + req <= 24:
        assert pl.placement == LOCAL_ID, (used, req, pl)
    else:
        assert pl.placement is None, (used, req, pl)


def test_control_plane_reservation_property():
    """任意放置结果下，local 在跑核数永不超过 24（控制面保留 104 核）。"""
    p = Policy()
    for used in range(0, 25):
        for req in range(1, 13):
            hs = hosts(local_used=used, n315=False, n330=False)
            pl = decide_placement(JobSpec(cmd="x", repo="R", cores=req), hs)
            if pl.placement == LOCAL_ID:
                assert used + req <= p.local_agent_cap_cores


# ── 常规放置语义 ─────────────────────────────────────────────────────
def test_node_preferred_lowest_load():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4), hosts())
    assert pl.placement == "n315"  # loadr 0.3 < 0.5


def test_node_load_cap():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4),
                          hosts(n315_loadr=0.9, n330_loadr=0.9))
    assert pl.placement == LOCAL_ID  # 节点过载 → 小作业落 local


def test_node_cores_short():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=64),
                          hosts(n315_used=80, n330_used=80))
    assert pl.placement is None  # 128-80=48 < 64，local 又超单作业上限


def test_node_mem_floor():
    hs = hosts()
    hs["n315"].mem_free_g = 20
    hs["n330"].mem_free_g = 20
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4, mem_g=10), hs)
    assert pl.placement == LOCAL_ID  # 20 < 10+30 → 节点内存不足


def test_node_concurrency_cap():
    hs = hosts()
    hs["n315"].running_jobs = 4
    hs["n330"].running_jobs = 4
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4), hs)
    assert pl.placement == LOCAL_ID


def test_local_load_high_refused():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4),
                          hosts(local_loadr=0.9, n315=False, n330=False))
    assert pl.placement is None and "local-load-high" in pl.reason


def test_local_down():
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=4),
                          hosts(local_ok=False, n315=False, n330=False))
    assert pl.placement is None


def test_policy_override():
    p = Policy(local_max_job_cores=16, local_agent_cap_cores=48)
    pl = decide_placement(JobSpec(cmd="x", repo="R", cores=16),
                          hosts(n315=False, n330=False), p)
    assert pl.placement == LOCAL_ID


# ── JobSpec 契约 ─────────────────────────────────────────────────────
def test_spec_no_node_field():
    import dataclasses
    fields = {f.name for f in dataclasses.fields(JobSpec)}
    assert "node" not in fields  # 结构性保证：提交者永不说跑哪


def test_spec_validate():
    assert JobSpec(cmd="x", repo="R").validate() == ""
    assert JobSpec(pytest_target="tests/t.py::t", repo="R").validate() == ""
    assert JobSpec(cmd="x", repo="R", workdir="/w").validate() != ""
    assert JobSpec(cmd="x").validate() != ""          # 无工作树来源
    assert JobSpec(cmd="x", repo="R", cores=0).validate() != ""
    assert JobSpec(cmd="x", repo="R", cores=513).validate() != ""
    assert JobSpec(cmd_tpl="c {i}/{n}", shards=16, repo="R").validate() == ""
    assert JobSpec(cmd_tpl="c", shards=0, repo="R").validate() != ""


def test_effective_cmd_pytest():
    s = JobSpec(pytest_target="tests/t.py::t1", repo="R")
    assert s.effective_cmd == "python3 -m pytest tests/t.py::t1 -v"
    assert s.mode == "run"
    s2 = JobSpec(cmd_tpl="c {i}/{n}", shards=4, repo="R")
    assert s2.mode == "shard" and s2.effective_cmd == "c {i}/{n}"
