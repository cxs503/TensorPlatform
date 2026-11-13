"""控制节点（99）本机 CPU 采样器：证明「本机烧了多少」不是拍脑袋。

三件事：
  1. ``proc_cpu_seconds(pid)``：读 /proc/<pid>/stat 的 utime+stime（clock tick → 秒）；
  2. ``scan_pids(pattern)``：按 cmdline 匹配进程（默认全量枚举），用于给 agent 进程树记账；
  3. ``system_cpu_seconds()``：/proc/stat 累计 CPU 秒（bench 前后取差 = 真实机器 CPU 消耗）。

全部接受 ``proc_root`` 注入（默认 "/proc"）→ 单测可用假 /proc 目录钉死行为。
"""
from __future__ import annotations

import os
import pathlib
import time

CLK_TCK = float(os.sysconf("SC_CLK_TCK"))


class SamplerError(RuntimeError):
    pass


def _read(p: pathlib.Path) -> str:
    return p.read_text(errors="replace")


def proc_cpu_seconds(pid: int | str, proc_root: str | pathlib.Path = "/proc") -> float | None:
    """进程累计 CPU 秒（utime+stime）；进程已消失 → None。"""
    st = pathlib.Path(proc_root) / str(pid) / "stat"
    try:
        raw = _read(st)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    try:
        tail = raw[raw.rindex(")") + 2:].split()
        utime, stime = float(tail[11]), float(tail[12])
    except (ValueError, IndexError):
        return None
    return (utime + stime) / CLK_TCK


def scan_pids(pattern: str | None = None, proc_root: str | pathlib.Path = "/proc") -> list[int]:
    """枚举 pid；pattern 非空则要求 cmdline 内含该子串。"""
    root = pathlib.Path(proc_root)
    out: list[int] = []
    for d in root.iterdir():
        if not d.name.isdigit():
            continue
        cmd = pathlib.Path(proc_root) / d.name / "cmdline"
        try:
            text = _read(cmd).replace("\x00", " ")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if not text.strip():
            continue
        if pattern and pattern not in text:
            continue
        out.append(int(d.name))
    return sorted(out)


def snapshot(pids: list[int], proc_root: str | pathlib.Path = "/proc") -> dict[int, float]:
    out: dict[int, float] = {}
    for pid in pids:
        v = proc_cpu_seconds(pid, proc_root)
        if v is not None:
            out[pid] = v
    return out


def delta_cpu_seconds(before: dict[int, float], after: dict[int, float]) -> float:
    """两次快照之差 = 这些进程在这段时间烧的 CPU 秒（消失的进程按最后值计）。"""
    total = 0.0
    for pid, v in after.items():
        total += max(v - before.get(pid, 0.0), 0.0)
    return total


def measure(pids: list[int], *, duration: float | None = None, interval: float = 0.5,
            proc_root: str | pathlib.Path = "/proc", clock=time.monotonic,
            sleep=time.sleep) -> dict:
    """采样一组进程的 CPU 秒。

    duration=None → 直到所有 pid 消失（作业结束）。返回 {cpu_seconds, wall_seconds, samples}。
    """
    t0 = clock()
    before = snapshot(pids, proc_root)
    peak = dict(before)
    samples = 0
    while True:
        sleep(interval)
        samples += 1
        cur = snapshot(pids, proc_root)
        for pid, v in cur.items():
            peak[pid] = max(peak.get(pid, 0.0), v)
        if duration is not None:
            if clock() - t0 >= duration:
                break
        elif not cur:
            break
    wall = clock() - t0
    return {"cpu_seconds": round(delta_cpu_seconds(before, peak), 3),
            "wall_seconds": round(wall, 3), "samples": samples}


def system_cpu_seconds(proc_root: str | pathlib.Path = "/proc") -> dict[str, float]:
    """整机累计 CPU 秒（/proc/stat 首行）：两次取差 = 机器真实消耗。"""
    try:
        line = _read(pathlib.Path(proc_root) / "stat").splitlines()[0]
    except (FileNotFoundError, IndexError):
        raise SamplerError("读不到 /proc/stat") from None
    parts = line.split()[1:]
    vals = [float(x) / CLK_TCK for x in parts[:8]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)
    total = sum(vals)
    return {"total": round(total, 3), "idle": round(idle, 3),
            "busy": round(total - idle, 3)}


def system_busy_between(before: dict[str, float], after: dict[str, float]) -> dict:
    busy = max(after["busy"] - before["busy"], 0.0)
    total = max(after["total"] - before["total"], 0.0)
    return {"busy_cpu_seconds": round(busy, 3), "total_cpu_seconds": round(total, 3),
            "busy_ratio": round(busy / total, 4) if total else 0.0}