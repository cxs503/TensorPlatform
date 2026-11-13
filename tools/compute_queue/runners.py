"""compute_queue 执行面（v1）。

local-99 runner：硬限三级降级
  1) cgroup v2：cpu.max（核数硬限）+ memory.max（内存硬限）
  2) cgroup v1：cpu.cfs_quota_us + memory.limit_in_bytes（需可写挂载）
  3) taskset + nice：CPU 亲和硬限核数 + 最低优先级（内存软限，如实披露）
node runner：复用 cluster_exec.py run --node（既有资产，不重造）。

cpu_seconds 采集：cgroup cpu.stat（精确）→ 进程组 ps cputime 求和（树级）→ NULL。
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

from .scheduler import JobSpec, LOCAL_ID

CLUSTER_DIR = pathlib.Path(os.environ.get(
    "CQ_CLUSTER_DIR", "/root/tp-agent/cluster"))
CEXEC = str(CLUSTER_DIR / "cluster_exec.py")
CGROUP2_ROOT = os.environ.get("CQ_CGROUP2_ROOT", "/sys/fs/cgroup")
CGROUP1_ROOT = os.environ.get("CQ_CGROUP1_ROOT", "/sys/fs/cgroup")
CG_NS = "compute_queue"          # cgroup 命名空间
NICE_LEVEL = 19                  # 最低优先级（不抢控制面）


# ── 限流策略选择 ──────────────────────────────────────────────────────
def pick_limiter(cores: int, mem_g: int,
                 cgroup2_root: str = CGROUP2_ROOT,
                 cgroup1_root: str = CGROUP1_ROOT) -> tuple:
    """返回 (strategy, detail)。strategy ∈ {cgroup2, cgroup1, taskset_nice}。"""
    c2 = pathlib.Path(cgroup2_root) / "cgroup.controllers"
    try:
        if c2.is_file() and "cpu" in c2.read_text():
            return "cgroup2", "cpu.max+memory.max"
    except Exception:
        pass
    c1 = pathlib.Path(cgroup1_root) / "cpu,cpuacct"
    try:
        if c1.is_dir() and os.access(str(c1), os.W_OK):
            return "cgroup1", "cpu.cfs_quota_us+memory.limit_in_bytes"
    except Exception:
        pass
    if shutil.which("taskset") and shutil.which("nice"):
        return "taskset_nice", "taskset -c 0-%d + nice -n %d（内存软限）" % (
            max(0, int(cores) - 1), NICE_LEVEL)
    return "none", "无可用限流器（裸跑，披露）"


def _cgroup2_setup(jid: str, cores: int, mem_g: int,
                   root: str = CGROUP2_ROOT) -> Optional[str]:
    """建 cgroup v2 子组并设限；成功返回 cgroup 路径，失败 None。"""
    base = pathlib.Path(root) / CG_NS
    cg = base / ("cj%s" % jid)
    try:
        cg.mkdir(parents=True, exist_ok=True)
        (cg / "cpu.max").write_text("%d 100000" % int(cores))
        if mem_g > 0:
            (cg / "memory.max").write_text(str(int(mem_g) * 1024 ** 3))
        return str(cg)
    except Exception:
        return None


def _cgroup1_setup(jid: str, cores: int, mem_g: int,
                   root: str = CGROUP1_ROOT) -> Optional[str]:
    try:
        cpu = pathlib.Path(root) / "cpu,cpuacct" / ("cq_cj%s" % jid)
        mem = pathlib.Path(root) / "memory" / ("cq_cj%s" % jid)
        cpu.mkdir(parents=True, exist_ok=True)
        (cpu / "cpu.cfs_period_us").write_text("100000")
        (cpu / "cpu.cfs_quota_us").write_text(str(int(cores) * 100000))
        if mem_g > 0:
            mem.mkdir(parents=True, exist_ok=True)
            (mem / "memory.limit_in_bytes").write_text(
                str(int(mem_g) * 1024 ** 3))
        return str(cpu)
    except Exception:
        return None


def _preexec_cgroup(cg_path: str):
    """Popen preexec：把自己写进 cgroup.procs / tasks。"""
    def _pre():
        for f in ("cgroup.procs", "tasks"):
            p = pathlib.Path(cg_path) / f
            if p.exists():
                try:
                    p.write_text(str(os.getpid()))
                    return
                except Exception:
                    continue
    return _pre


def build_local_argv(spec: JobSpec, workdir: str, strategy: str,
                     jid: str) -> list:
    """构造本机执行 argv（strategy 已定）。"""
    cmd = spec.effective_cmd
    if strategy == "taskset_nice":
        return ["taskset", "-c", "0-%d" % max(0, int(spec.cores) - 1),
                "nice", "-n", str(NICE_LEVEL), "bash", "-c", cmd]
    if strategy in ("cgroup2", "cgroup1"):
        return ["bash", "-c", cmd]
    return ["bash", "-c", cmd]


def parse_cputime(s: str) -> float:
    """ps cputime [[dd-]hh:]mm:ss → 秒。"""
    s = s.strip()
    if not s:
        return 0.0
    days = 0
    if "-" in s:
        days, s = s.split("-", 1)
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts[-3], parts[-2], parts[-1]
    return int(days) * 86400 + h * 3600 + m * 60 + sec


def tree_cpu_seconds(pgid: int) -> Optional[float]:
    """进程组总 CPU 秒（树级，/proc 直读，10ms 分辨率；组已散返回 None）。"""
    try:
        hz = os.sysconf("SC_CLK_TCK")
        total = 0
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                st = open("/proc/%s/stat" % d).read()
                r = st.rsplit(")", 1)[1].split()
                if len(r) < 13 or int(r[2]) != pgid:
                    continue
                total += int(r[11]) + int(r[12])   # utime + stime
            except Exception:
                continue
        return total / hz if total > 0 else None
    except Exception:
        return None


def _cgroup_cpu_seconds(cg_path: str, v2: bool) -> Optional[float]:
    try:
        f = pathlib.Path(cg_path) / ("cpu.stat" if v2 else "cpuacct.usage")
        if not f.exists():
            return None
        txt = f.read_text()
        if v2:
            for ln in txt.splitlines():
                if ln.startswith("usage_usec"):
                    return int(ln.split()[1]) / 1e6
        else:
            return int(txt.strip()) / 1e6
    except Exception:
        return None


@dataclass
class LocalRunResult:
    rc: int
    cpu_seconds: Optional[float]
    strategy: str
    workdir: str
    output_tail: str = ""


class LocalRunner:
    """local-99 执行器：硬限 + 记账。run() 同步（调度器侧 Popen 包装）。"""

    def __init__(self, cgroup2_root: str = CGROUP2_ROOT,
                 cgroup1_root: str = CGROUP1_ROOT,
                 git_cache: Optional[str] = None):
        self.cgroup2_root = cgroup2_root
        self.cgroup1_root = cgroup1_root
        self.git_cache = git_cache

    def resolve_workdir(self, spec: JobSpec, jid: str) -> str:
        """工作树来源：workdir 直用；repo+ref → git 浅检出。"""
        if spec.workdir:
            return spec.workdir
        dest = pathlib.Path("/tmp/cq_%s" % jid)
        if not dest.exists():
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", spec.ref,
                 "git@github.com:TensorPlatform-%s.git" % spec.repo, str(dest)],
                capture_output=True, timeout=600)
        return str(dest)

    def run(self, spec: JobSpec, jid: str,
            workdir: Optional[str] = None) -> LocalRunResult:
        wd = workdir or self.resolve_workdir(spec, jid)
        strategy, _detail = pick_limiter(spec.cores, spec.mem_g,
                                        self.cgroup2_root, self.cgroup1_root)
        cg_path = None
        preexec = None
        if strategy == "cgroup2":
            cg_path = _cgroup2_setup(jid, spec.cores, spec.mem_g,
                                    self.cgroup2_root)
            if cg_path:
                preexec = _preexec_cgroup(cg_path)
            else:
                strategy = "taskset_nice"
        elif strategy == "cgroup1":
            cg_path = _cgroup1_setup(jid, spec.cores, spec.mem_g,
                                     self.cgroup1_root)
            if cg_path:
                preexec = _preexec_cgroup(cg_path)
            else:
                strategy = "taskset_nice"
        if strategy == "taskset_nice":
            argv = build_local_argv(spec, wd, strategy, jid)
        else:
            argv = build_local_argv(spec, wd, strategy, jid)
        t0 = time.time()
        samples = []
        stop = {"v": False}

        def _sample():
            while not stop["v"]:
                try:
                    v = tree_cpu_seconds(os.getpgid(p.pid))
                    if v is not None:
                        samples.append(v)
                except Exception:
                    pass
                time.sleep(0.05)

        import threading
        th = None
        try:
            p = subprocess.Popen(argv, cwd=wd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=preexec)
            th = threading.Thread(target=_sample, daemon=True)
            th.start()
            out, _ = p.communicate(timeout=int(spec.timeout or 3600))
            rc = p.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except Exception:
                p.kill()
            out, _ = p.communicate()
            rc = -9
        stop["v"] = True
        if th is not None:
            th.join(timeout=2)
        cpu = None
        if cg_path:
            cpu = _cgroup_cpu_seconds(cg_path, strategy == "cgroup2")
        if cpu is None and samples:
            cpu = samples[-1]
        return LocalRunResult(rc=rc, cpu_seconds=cpu, strategy=strategy,
                               workdir=wd,
                               output_tail=(out or b"")[-4000:].decode(
                                   "utf-8", "replace"))


class NodeRunner:
    """双节点 ssh 执行器：复用 cluster_exec.py（既有资产）。"""

    def __init__(self, cexec: str = CEXEC):
        self.cexec = cexec

    def argv(self, spec: JobSpec, node: str, jid: str) -> list:
        a = [sys.executable, self.cexec, "run", "--repo", spec.repo,
             "--ref", spec.ref, "--cmd", spec.effective_cmd,
             "--node", node, "--job-id", jid,
             "--timeout", str(int(spec.timeout or 3600))]
        if spec.workdir:
            a += ["--stage-dir", spec.workdir]
        return a

    def run(self, spec: JobSpec, node: str, jid: str) -> int:
        r = subprocess.run(self.argv(spec, node, jid), capture_output=True,
                           text=True, timeout=int(spec.timeout or 3600) + 600)
        return r.returncode
