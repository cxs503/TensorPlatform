#!/usr/bin/env python3
"""compute_gate.preflight —— 本机重计算**硬拦**（工具级，静态可判的那部分）。

分工：
  - 静态可判（本模块，run_shell 执行前）：并行度 ≥8 的测试/构建、作业托管期外的
    长睡/长算、后台逃逸（nohup & / setsid / disown / screen / tmux）→ 工具级拒绝并改写成递交需求。
  - 动态不可判（guard.py，进程级兜底）：真跑起来才知道吃多少核的长算力 → SIGSTOP+迁移。
两者是同一条门的两道闸；只有兜底=事故已发生，只有静态=绕过（本机 python 脚本吃 32 核）。

判据保守：命中即 REDIRECT（附可直接调 run_compute 的参数），绝不静默改写用户命令。
"""
import os
import re

# 并行度声明式：命中即知请求了多少核
_PAR_RULES = [
    (re.compile(r"pytest\b.*?-n\s*(\d+)"), "pytest -n N"),
    (re.compile(r"pytest\b.*?-n\s*auto"), "pytest -n auto"),
    (re.compile(r"\bxargs\b.*?-P\s*(\d+)"), "xargs -P N"),
    (re.compile(r"\bmake\b.*?-j\s*(\d+)"), "make -jN"),
    (re.compile(r"--jobs[= ](\d+)"), "--jobs N"),
    (re.compile(r"--shards\s+(\d+)"), "shards N"),
    (re.compile(r"\btaskset\b.*?-c\s*[\d,\-]+"), "taskset 绑核"),
    (re.compile(r"\bpsutil\b.*?-p\s*[^\s]*--parallel"), "psutil 并行"),
]
# 后台逃逸式：与 99 终态孤儿事故同源（进程脱离工具生命周期 → 无人回收）
_ESCAPE_RULES = [
    (re.compile(r"\bnohup\b"), "nohup 后台逃逸"),
    (re.compile(r"\bsetsid\b"), "setsid 后台逃逸"),
    (re.compile(r"\bdisown\b"), "disown 后台逃逸"),
    (re.compile(r"(?:^|[;&|]\s*)screen\s+-[dD]?[mS]?"), "screen 会话逃逸"),
    (re.compile(r"\btmux\s+new"), "tmux 会话逃逸"),
]
# 长阻塞/长算：>10min 的单步
_LONG_RULES = [
    (re.compile(r"\bsleep\s+(\d{3,})"), "长 sleep"),
    (re.compile(r"--timeout[= ](\d{4,})"), "长 timeout"),
    (re.compile(r"\btimeout\s+(\d{4,})"), "长 timeout"),
]
_TRAILING_BG = re.compile(r"(?<![&>])\&\s*$")


def _n(thr):
    try:
        return int(thr)
    except Exception:
        return 8


def check_local_command(cmd, cores_available=None, heavy_cores_threshold=8, long_seconds=600):
    """返回 {verdict: allow|redirect, reasons[], suggested: {cmd,...}}。

    verdict=redirect 时调用方（run_shell）应改为拒绝并把 suggested 交给 run_compute。
    """
    cmd = (cmd or "").strip()
    reasons = []
    if not cmd:
        return {"verdict": "allow", "reasons": [], "checked": cmd}

    for rx, why in _PAR_RULES:
        m = rx.search(cmd)
        if not m:
            continue
        if "auto" in m.group(0):
            reasons.append({"rule": why, "detail": "auto 并行未知的上界", "cores": None})
            continue
        if rx.groups:
            cores = _n(m.group(1))
            if cores >= heavy_cores_threshold:
                reasons.append({"rule": why, "detail": m.group(0), "cores": cores})
        else:
            reasons.append({"rule": why, "detail": m.group(0), "cores": None})

    for rx, why in _ESCAPE_RULES:
        m = rx.search(cmd)
        if m:
            reasons.append({"rule": why, "detail": m.group(0), "cores": None})
    if _TRAILING_BG.search(cmd):
        reasons.append({"rule": "尾部 & 后台", "detail": cmd[-60:], "cores": None})

    for rx, why in _LONG_RULES:
        m = rx.search(cmd)
        if m and rx.groups:
            secs = _n(m.group(1))
            if secs >= long_seconds:
                reasons.append({"rule": why, "detail": m.group(0), "seconds": secs, "cores": None})

    if not reasons:
        return {"verdict": "allow", "reasons": [], "checked": cmd}

    cores = max([r.get("cores") or 0 for r in reasons] + [0]) or heavy_cores_threshold
    return {"verdict": "redirect", "reasons": reasons, "checked": cmd,
            "suggested": {"action": "submit",
                          "cmd": _strip_background(cmd),
                          "cores": min(64, int(cores)),
                          "note": "本机重计算硬拦：改由 run_compute 递交，节点与排队由调度器决定"}}


def _strip_background(cmd):
    """迁移时去掉后台化包装（队列里不需要 &/nohup，且后台会脱离 runner 生命周期）。"""
    s = re.sub(r"^\s*nohup\s+", "", cmd)
    s = re.sub(r"^\s*setsid\s+", "", s)
    s = re.sub(r"(?<![&>])\&\s*$", "", s).strip()
    s = re.sub(r"\bdisown\b", "", s).strip()
    return s


def format_redirect(res, tool_hint="run_compute"):
    """把判定渲染成给 agent 看的硬拦回执（含可直接照抄的调用参数）。"""
    rs = "; ".join(r.get("detail") or r["rule"] for r in res["reasons"])
    sug = res.get("suggested") or {}
    return ("LOCAL-COMPUTE BLOCKED（本机重计算硬拦，owner 2026-11-13 计算调度令）："
            "命令命中 [%s]。重计算不得在本机跑——请改用 %s："
            "action=submit, repo=<仓库>, cmd=%r, cores=%s；递交返回 cjN+排队位次，"
            "ps/fetch 轮询取工件。本机只跑 <8 核、<10min 的短测试。"
            % (rs, tool_hint, sug.get("cmd", ""), sug.get("cores", "")))