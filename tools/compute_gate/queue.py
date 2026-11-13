#!/usr/bin/env python3
"""compute_gate.queue —— cluster_queue 递交/查询客户端（递交语义，永不带 --node）。

对 agent 只暴露「递交需求」：repo + cmd(/cmd_tpl+shards) + cores/timeout + stage_dir + mid。
节点指派、排队、容量准入全部由 cluster_queue.py --sched 负责（owner 11-09 HPC 常态令）。
"""
import json
import os
import pathlib
import shlex
import subprocess

PGENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def queue_py(cfg):
    return str(pathlib.Path(cfg["queue_dir"]) / "cluster_queue.py")


def _run(argv, timeout=180):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=PGENV)
    return r.stdout + r.stderr


def submit(cfg, repo, cmd=None, cmd_tpl=None, shards=None, ref="main", cores=None,
           timeout=7200, stage_dir=None, mid=None, submitter="agent-run_compute"):
    """递交 → dict(jid, queued_ahead, state, queue_position, ...)。失败抛 RuntimeError。"""
    if not repo:
        raise RuntimeError("submit 需要 repo")
    if not (cmd or (cmd_tpl and shards)):
        raise RuntimeError("submit 需要 cmd，或 cmd_tpl+shards")
    argv = ["python3", queue_py(cfg), "submit", "--repo", repo, "--ref", ref or "main",
            "--timeout", str(int(timeout or 7200)), "--submitter", submitter]
    if cmd:
        argv += ["--cmd", cmd]
    else:
        argv += ["--cmd-tpl", cmd_tpl, "--shards", str(int(shards))]
    if cores:
        argv += ["--cores", str(int(cores))]
    if stage_dir:
        argv += ["--stage-dir", stage_dir]
    if mid:
        argv += ["--mid", str(int(mid))]
    out = _run(argv, timeout=180)
    jid, ahead = None, None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                jid, ahead = d.get("jid"), d.get("queued_ahead")
            except Exception:
                pass
    if not jid:
        raise RuntimeError("递交失败：" + out[-800:])
    return {"jid": jid, "queued_ahead": ahead, "state": "queued",
            "queue_position": (int(ahead) + 1) if ahead is not None else None,
            "semantics": "递交即返回；节点由调度器指派，无资源排队等待（不指定节点）",
            "repo": repo, "cores": cores, "timeout": int(timeout or 7200),
            "stage_dir": stage_dir, "mid": mid}


def ps(cfg, jid=None):
    argv = ["python3", queue_py(cfg), "ps"] + (["--jid", jid] if jid else [])
    return _run(argv, timeout=180)


def fetch(cfg, jid):
    return _run(["python3", queue_py(cfg), "fetch", "--jid", jid], timeout=900)


def kill(cfg, jid):
    return _run(["python3", queue_py(cfg), "kill", "--jid", jid], timeout=180)


def nodes(cfg):
    return _run(["python3", str(pathlib.Path(cfg["queue_dir"]) / "cluster_exec.py"), "nodes"], timeout=180)


# ── 本地命令 → 递交需求 的推导（迁移与 preflight 共用） ─────────────
def repo_of(path):
    """从目录推 repo 名：git remote origin 名 → 否则 git 顶层目录名 → 否则目录名。"""
    if not path or not os.path.isdir(path):
        return None
    try:
        r = subprocess.run(["git", "-C", path, "config", "--get", "remote.origin.url"],
                           capture_output=True, text=True, timeout=20)
        url = (r.stdout or "").strip()
        if url:
            name = url.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except Exception:
        pass
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=20)
        top = (r.stdout or "").strip()
        if top:
            return os.path.basename(top)
    except Exception:
        pass
    return os.path.basename(os.path.normpath(path)) or None


def cmd_from_argv(argv, shell_wrapped=False):
    """argv(list) → 递交用命令串。shell 包裹（bash -lc "..."）优先还原原命令串。"""
    if not argv:
        return None
    if shell_wrapped:
        for flag in ("-lc", "-c"):
            if flag in argv:
                i = argv.index(flag)
                if i + 1 < len(argv):
                    return argv[i + 1]
    return " ".join(shlex.quote(a) for a in argv)