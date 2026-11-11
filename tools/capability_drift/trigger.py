"""触发机制：git push / PR merge 后，从变更文件识别受影响的求解器能力。

两种取变更的方式：
1. CI 里由 workflow 把 ``git diff --name-only BASE HEAD`` 的结果写进文件（推荐，最稳）；
2. 本地/工具内直接调 ``changed_files(repo, base, head)``。

判定规则在 ``model.affected_capabilities``（精确/目录前缀/glob），命不中任何路径的能力
不重跑——文档/README 变更因此天然零告警（A2）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence, Tuple

from .model import Capability, affected_capabilities


def git(repo: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(repo: str | Path, base: str = "HEAD~1", head: str = "HEAD") -> Tuple[str, ...]:
    """``git diff --name-only base head``；base 为空/无效时退回 ``git show --name-only head``。"""
    try:
        out = git(repo, "diff", "--name-only", base, head)
    except RuntimeError:
        out = git(repo, "show", "--pretty=format:", "--name-only", head)
    return tuple(line.strip() for line in out.splitlines() if line.strip())


def trigger(
    capabilities: Sequence[Capability],
    changed: Sequence[str],
    *,
    solvers: Sequence[str] | None = None,
) -> Tuple[str, ...]:
    """变更 → 受影响能力；``solvers`` 可再按求解器仓过滤。"""
    hit = affected_capabilities(capabilities, changed)
    if solvers:
        allowed = set(solvers)
        hit = tuple(c for c in capabilities if c.id in hit and c.solver in allowed)
        return tuple(c.id for c in hit)
    return hit


def trigger_from_git(
    capabilities: Sequence[Capability],
    repo: str | Path,
    base: str = "HEAD~1",
    head: str = "HEAD",
) -> Tuple[str, ...]:
    return trigger(capabilities, changed_files(repo, base, head))