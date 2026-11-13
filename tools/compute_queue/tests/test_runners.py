"""执行面单测：限流策略选择 + 本机 runner 真实小作业 + cputime 解析。"""
import os
import pathlib
import textwrap

import pytest

from tools.compute_queue.runners import (
    LocalRunner, NodeRunner, build_local_argv, parse_cputime, pick_limiter)
from tools.compute_queue.scheduler import JobSpec


def test_pick_limiter_cgroup2(tmp_path):
    root = tmp_path / "cg2"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
    strat, _ = pick_limiter(4, 8, cgroup2_root=str(root),
                            cgroup1_root=str(tmp_path / "nope"))
    assert strat == "cgroup2"


def test_pick_limiter_cgroup1(tmp_path):
    c1 = tmp_path / "cg1" / "cpu,cpuacct"
    c1.mkdir(parents=True)
    strat, _ = pick_limiter(4, 8, cgroup2_root=str(tmp_path / "nope"),
                            cgroup1_root=str(tmp_path / "cg1"))
    assert strat == "cgroup1"


def test_pick_limiter_fallback(tmp_path):
    strat, detail = pick_limiter(4, 8, cgroup2_root=str(tmp_path / "nope"),
                                 cgroup1_root=str(tmp_path / "nope"))
    assert strat == "taskset_nice"
    assert "taskset" in detail and "nice" in detail


def test_taskset_nice_argv():
    s = JobSpec(cmd="sleep 1", repo="R", cores=4)
    argv = build_local_argv(s, "/tmp", "taskset_nice", "cj1")
    assert argv[:4] == ["taskset", "-c", "0-3", "nice"]
    assert argv[5] == "19" and argv[-1] == "sleep 1"


def test_parse_cputime():
    assert parse_cputime("00:00:05") == 5
    assert parse_cputime("01:02:03") == 3723
    assert parse_cputime("1-02:03:04") == 93784
    assert parse_cputime("") == 0


def test_local_runner_real_job_taskset_nice(tmp_path):
    """真实执行：4 核请求 → taskset 0-3 + nice 19，rc=0，cpu 有值。"""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "run.sh").write_text(
        'echo hello-cq; python3 -c "import time; t=time.time()\n'
        'while time.time()-t<0.3: pass"')
    r = LocalRunner(cgroup2_root=str(tmp_path / "nope"),
                    cgroup1_root=str(tmp_path / "nope"))
    res = r.run(JobSpec(cmd="bash run.sh", workdir=str(wd), cores=4),
                "cjtest1", workdir=str(wd))
    assert res.rc == 0
    assert "hello-cq" in res.output_tail
    assert res.strategy == "taskset_nice"
    # cpu_seconds 为采样值（best-effort）：CPU 密集作业应采到 >0
    assert res.cpu_seconds is not None and res.cpu_seconds > 0.05


def test_local_runner_timeout_kills(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    r = LocalRunner(cgroup2_root=str(tmp_path / "nope"),
                   cgroup1_root=str(tmp_path / "nope"))
    res = r.run(JobSpec(cmd="sleep 30", workdir=str(wd), cores=1,
                        timeout=2), "cjtest2", workdir=str(wd))
    assert res.rc == -9


def test_node_runner_argv_reuses_cluster_exec():
    nr = NodeRunner(cexec="/root/tp-agent/cluster/cluster_exec.py")
    s = JobSpec(cmd="pytest -q", repo="TensorBEM", ref="main", cores=32,
                 timeout=1800)
    a = nr.argv(s, "n315", "cj99")
    assert a[1] == "/root/tp-agent/cluster/cluster_exec.py"
    assert "run" in a and "--node" in a and a[a.index("--node") + 1] == "n315"
    assert a[a.index("--job-id") + 1] == "cj99"
    s2 = JobSpec(cmd="x", workdir="/root/tp-agent/work/m1", cores=2)
    a2 = nr.argv(s2, "n330", "cj100")
    assert a2[-2:] == ["--stage-dir", "/root/tp-agent/work/m1"]
