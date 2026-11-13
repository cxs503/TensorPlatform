#!/usr/bin/env python3
"""compute_gate.procs —— /proc 进程树读取与 CPU/内存核算（纯函数，便于单测）。

只读 /proc，不依赖 psutil；proc_root 可注入（红队试验用假 /proc，或指向 /proc）。
"""
import os
from dataclasses import dataclass, field

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


@dataclass
class Proc:
    pid: int
    ppid: int
    state: str
    comm: str
    cpu_ticks: int          # utime+stime+cutime+cstime（含已回收子进程，别重复累加子进程）
    own_ticks: int          # 仅 utime+stime（自身），聚合子树时用这个
    rss_kb: int
    start_ticks: int        # /proc/<pid>/stat 第 22 字段，pid 复用判别
    cmdline: str
    cwd: str = ""
    children: list = field(default_factory=list)

    @property
    def cpu_seconds(self) -> float:
        return self.cpu_ticks / CLK_TCK

    @property
    def own_cpu_seconds(self) -> float:
        return self.own_ticks / CLK_TCK

    @property
    def key(self) -> str:
        """跨扫描稳定的身份：pid+start_ticks（防 pid 复用把账算错）。"""
        return "%d:%d" % (self.pid, self.start_ticks)


def _read_stat(pid_dir):
    try:
        raw = open(os.path.join(pid_dir, "stat"), "rb").read().decode("utf-8", "replace")
    except OSError:
        return None
    # comm 可能含空格/括号 → 以最后一个 ')' 切
    try:
        head, rest = raw.split(")", 1)
        comm = head.split("(", 1)[1]
        f = rest.split()
    except Exception:
        return None
    # f[0]=state, f[1]=ppid, ... utime=f[11], stime=f[12], cutime=f[13], cstime=f[14],
    # ... starttime=f[19], rss(pages)=f[21]
    try:
        return {
            "comm": comm,
            "state": f[0],
            "ppid": int(f[1]),
            "utime": int(f[11]),
            "stime": int(f[12]),
            "cutime": int(f[13]),
            "cstime": int(f[14]),
            "start": int(f[19]),
            "rss_pages": int(f[21]),
        }
    except (IndexError, ValueError):
        return None


def _read_cmdline(pid_dir):
    try:
        raw = open(os.path.join(pid_dir, "cmdline"), "rb").read()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def _read_cwd(pid_dir):
    try:
        return os.readlink(os.path.join(pid_dir, "cwd"))
    except OSError:
        return ""


def _cwd_of_proc(pid_dir):
    """更稳的 cwd 读取：直读 /proc/<pid>/cwd；权限不足时回落 /proc/<pid>/task/<tid>/cwd。"""
    p = _read_cwd(pid_dir)
    if p:
        return p
    tid_dir = os.path.join(pid_dir, "task")
    try:
        for tid in os.listdir(tid_dir):
            p = _read_cwd(os.path.join(tid_dir, tid))
            if p:
                return p
    except OSError:
        pass
    return ""


def read_procs(proc_root="/proc") -> dict:
    """{pid: Proc}；权限不足读不到的进程跳过（如实少算，不猜）。"""
    out = {}
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        pid_dir = os.path.join(proc_root, name)
        st = _read_stat(pid_dir)
        if not st:
            continue
        pid = int(name)
        page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        out[pid] = Proc(
            pid=pid,
            ppid=st["ppid"],
            state=st["state"],
            comm=st["comm"],
            cpu_ticks=st["utime"] + st["stime"] + st["cutime"] + st["cstime"],
            own_ticks=st["utime"] + st["stime"],
            rss_kb=st["rss_pages"] * page // 1024,
            start_ticks=st["start"],
            cmdline=_read_cmdline(pid_dir),
            cwd=_cwd_of_proc(pid_dir),
        )
    for p in out.values():
        parent = out.get(p.ppid)
        if parent is not None:
            parent.children.append(p.pid)
    return out


def ancestors(procs, pid):
    """pid 自身 → 根（含自身）。"""
    chain, seen = [], set()
    cur = pid
    while cur in procs and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = procs[cur].ppid
    return chain


def subtree(procs, root):
    """root 及其全部子孙 pid（广度优先，防环）。"""
    if root not in procs:
        return []
    out, queue, seen = [], [root], set()
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        queue.extend(procs[cur].children)
    return out


def subtree_cpu_seconds(procs, root) -> float:
    """子树**自身** CPU 秒之和（用 own_ticks，避免 cutime 重复累加）。

    已退出的子进程其 CPU 已计入父进程 cutime/cstime；若子进程还在 procs 里，
    父的 cutime 只含已回收的，故 own+现存子树 = 实算量，不会双计也不会漏。
    """
    return sum(procs[p].own_cpu_seconds for p in subtree(procs, root))


def subtree_rss_mb(procs, root) -> float:
    return sum(procs[p].rss_kb for p in subtree(procs, root)) / 1024.0


def subtree_alive(procs, root) -> int:
    return sum(1 for p in subtree(procs, root) if procs[p].state not in ("Z", "X"))


def descendant_of_any(procs, pid, marked_pids) -> bool:
    """pid 是否落在任一 marked_pids 的子孙链上（含自身）。"""
    if not marked_pids:
        return False
    chain = set(ancestors(procs, pid))
    return bool(chain & set(marked_pids))


def matched_pids(procs, markers, protected=()):
    """指令行命中特征串的 pid 集合（runner 判据 / 保护名单）。"""
    hits = set()
    marks = [m for m in markers if m]
    for p in procs.values():
        line = p.cmdline or p.comm
        if any(m in line for m in marks):
            hits.add(p.pid)
    prot = [s for s in protected if s]
    if prot:
        for p in procs.values():
            if any(s in (p.comm or "") or s in (p.cmdline or "") for s in prot):
                hits.add(p.pid)
    return hits


def under_roots(path, roots):
    if not path:
        return False
    p = os.path.normpath(path)
    for r in roots:
        r = os.path.normpath(r)
        if p == r or p.startswith(r + os.sep):
            return True
    return False


def work_scope_roots(procs, roots):
    """挑出「根在托管 worktree 下、父进程在托管域外」的进程树根。

    判据（=本门作用域，spec「work/m{id} 内进程树」）：
      - 自身 cwd 在 work_roots 之下；
      - 其父进程不存在、或父进程 cwd 不在同一 work_root（即它是这棵树的入口）。
    孤儿进程（父=1，cwd 仍在 worktree）自然成为根——这正是 99 被打满的那种终态单孤儿。
    """
    out = []
    for p in procs.values():
        if not under_roots(p.cwd, roots):
            continue
        parent = procs.get(p.ppid)
        if parent is not None and under_roots(parent.cwd, roots):
            continue
        out.append(p.pid)
    return sorted(out)


def mission_id_from_path(path):
    """从 .../work/m17371[/...] 提 mid；提不到返回 None。"""
    if not path:
        return None
    parts = os.path.normpath(path).split(os.sep)
    for seg in reversed(parts):
        if seg.startswith("m") and seg[1:].isdigit():
            return int(seg[1:])
    return None