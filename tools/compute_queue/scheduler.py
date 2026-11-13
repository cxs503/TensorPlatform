"""compute_queue 裁决面（v1）—— 统一负载表 + 容量感知放置。

owner 2026-11-13 计算调度令（架构级）：
  「应该建计算调度系统，agent 计算提交给调度系统，跑在哪里是调度系统的事」

放置策略（验收标准 ②⑥）：
  - 统一负载表：n315/n330（ssh 探测，复用 cluster_exec nodes）+ local-99（本机探测）
  - local-99 只给小作业：单作业 cores <= local_max_job_cores（默认 8）
  - local-99 全部 agent 计算总帽 <= local_agent_cap_cores（默认 24 核），
    其余核保留给控制面（99 曾被终态单孤儿进程吃满 load 184/128 → 容量门停派 78 席闲置）
  - 集群节点准入：ok + loadr < load_cap + 内存下限 + 剩核 >= 请求 + 并发未满
  - 节点优先（702 双节点 72% 闲置），local 是小作业兜底
  - 无容量 -> Placement(None, ...)：排队等待（HPC 常态，不失败不丢弃）

本模块零 I/O（probe 函数除外，可注入）——红队测试直接打 decide_placement。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

LOCAL_ID = "local-99"


@dataclass(frozen=True)
class JobSpec:
    """作业规格（提交面契约）：cmd 或 pytest 目标 + 工作树来源 + 资源请求。

    工作树来源二选一：repo+ref（GitHub 检出）或 workdir（mission 工作树）。
    提交者永不说跑哪——本结构体没有 node 字段（HPC 语义，结构性保证）。
    """
    cmd: str = ""
    pytest_target: str = ""
    repo: str = ""
    ref: str = "main"
    workdir: str = ""
    cores: int = 1
    mem_g: int = 0
    timeout: int = 3600
    cmd_tpl: str = ""
    shards: int = 0
    submitter: str = "agent"

    def validate(self) -> str:
        """返回错误串；空串=合法。"""
        if self.shards:
            if not self.cmd_tpl:
                return "shard 型需 cmd_tpl"
            if not (1 <= self.shards <= 64):
                return "shards 须 1-64"
        elif not (self.cmd or self.pytest_target):
            return "需 cmd 或 pytest 目标"
        if not (self.repo or self.workdir):
            return "需工作树来源：repo(+ref) 或 workdir"
        if self.repo and self.workdir:
            return "repo 与 workdir 二选一"
        if not (1 <= int(self.cores) <= 512):
            return "cores 须 1-512"
        if int(self.mem_g or 0) < 0 or int(self.mem_g or 0) > 2048:
            return "mem_g 须 0-2048"
        if not (1 <= int(self.timeout or 0) <= 86400):
            return "timeout 须 1-86400 秒"
        return ""

    @property
    def mode(self) -> str:
        return "shard" if self.shards else "run"

    @property
    def effective_cmd(self) -> str:
        if self.shards:
            return self.cmd_tpl
        if self.pytest_target:
            return "python3 -m pytest %s -v" % self.pytest_target
        return self.cmd


@dataclass
class HostInfo:
    """统一负载表一行（n315/n330/local-99 同构）。"""
    id: str
    ok: bool = False
    loadr: float = 99.0          # load1/cores
    cores: int = 0
    mem_free_g: int = 0
    used_cores: int = 0          # 该主机上 running 作业请求核数之和（账本）
    running_jobs: int = 0


@dataclass(frozen=True)
class Policy:
    """裁决策略（验收标准 ②：本机 99 只给小作业且总帽 <=24 核，控制面保留）。"""
    local_max_job_cores: int = 8      # local 单作业核数上限
    local_agent_cap_cores: int = 24   # local 全部 agent 计算总帽
    load_cap: float = 0.85           # 负载准入线（load/cores）
    mem_floor_g: int = 30            # 空闲内存下限（GB）
    max_per_node: int = 4            # 每主机最大并发作业数
    node_preferred: bool = True      # 节点优先，local 小作业兜底


@dataclass(frozen=True)
class Placement:
    placement: Optional[str]   # None = 无容量 → 排队
    reason: str


def _local_eligible(h: Optional[HostInfo], cores: int, mem: int,
                    p: Policy) -> tuple:
    """返回 (eligible, reason)。"""
    if h is None or not h.ok:
        return False, "local-down"
    if cores > p.local_max_job_cores:
        return False, "local-size-exceeds(>%d核)" % p.local_max_job_cores
    if h.used_cores + cores > p.local_agent_cap_cores:
        return False, "local-cap-full(used=%d+req=%d>%d)" % (
            h.used_cores, cores, p.local_agent_cap_cores)
    if h.loadr >= p.load_cap:
        return False, "local-load-high(%.2f>=%.2f)" % (h.loadr, p.load_cap)
    if h.mem_free_g < mem + p.mem_floor_g:
        return False, "local-mem-low"
    return True, "local-small-ok"


def _node_eligible(h: HostInfo, cores: int, mem: int, p: Policy) -> tuple:
    if not h.ok:
        return False, "down"
    if h.loadr >= p.load_cap:
        return False, "load-high"
    if h.mem_free_g < mem + p.mem_floor_g:
        return False, "mem-low"
    if h.running_jobs >= p.max_per_node:
        return False, "concurrency-full"
    if h.cores - h.used_cores < cores:
        return False, "cores-short"
    return True, "ok"


def decide_placement(spec: JobSpec, hosts: Dict[str, HostInfo],
                     policy: Optional[Policy] = None) -> Placement:
    """容量感知放置（纯函数，红队靶面）。

    规则序：
      1) 集群节点：全部准入检查通过者中取 loadr 最低（同载取 id 序，确定性）
      2) local-99：仅小作业 + 总帽 + 负载 + 内存
      3) 都无 -> None（排队，HPC 常态）
    """
    p = policy or Policy()
    cores = max(1, int(spec.cores or 1))
    mem = int(spec.mem_g or 0)

    node_cands = []
    for hid, h in hosts.items():
        if hid == LOCAL_ID:
            continue
        ok, _why = _node_eligible(h, cores, mem, p)
        if ok:
            node_cands.append((h.loadr, hid))
    if node_cands:
        node_cands.sort()
        return Placement(node_cands[0][1], "node-lowest-loadr")

    local_ok, local_why = _local_eligible(hosts.get(LOCAL_ID), cores, mem, p)
    if local_ok:
        return Placement(LOCAL_ID, local_why)

    # 无容量：排队（HPC 常态），原因串带 local 诊断
    extra = ""
    if not node_cands:
        nd = [x for x in hosts if x != LOCAL_ID]
        if nd and all(not hosts[h].ok for h in nd):
            extra = "+all-nodes-down"
    return Placement(None, "no-capacity-queue(local:%s%s)" % (local_why, extra))


# ── 探测（可注入；测试用 fake） ───────────────────────────────────────
def probe_local(ledger=None) -> HostInfo:
    """探测本机 99：/proc/loadavg + nproc + MemAvailable + 账本 local 占用。"""
    try:
        load = float(open("/proc/loadavg").read().split()[0])
        cores = os.cpu_count() or 1
        mem_free_g = 0
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                mem_free_g = int(ln.split()[1]) // (1024 * 1024)
                break
        used, njobs = (0, 0)
        if ledger is not None:
            used, njobs = ledger.local_running()
        return HostInfo(LOCAL_ID, ok=True, loadr=load / cores, cores=cores,
                         mem_free_g=mem_free_g, used_cores=used,
                         running_jobs=njobs)
    except Exception:
        return HostInfo(LOCAL_ID, ok=False)


def build_load_table(node_probe: Callable[[], Dict[str, dict]],
                     local_probe: Callable[..., HostInfo],
                     ledger=None) -> Dict[str, HostInfo]:
    """统一负载表：{host_id: HostInfo}（节点探测结果 + 账本占用 + 本机）。"""
    hosts: Dict[str, HostInfo] = {}
    for hid, info in (node_probe() or {}).items():
        h = HostInfo(id=hid, ok=bool(info.get("ok")),
                      loadr=float(info.get("loadr", 99.0)),
                      cores=int(info.get("cores", 0)),
                      mem_free_g=int(info.get("mem_free_g", 0)))
        if ledger is not None and h.ok:
            h.used_cores, h.running_jobs = ledger.node_running(hid)
        hosts[hid] = h
    hosts[LOCAL_ID] = local_probe(ledger) if ledger is not None else local_probe()
    return hosts
