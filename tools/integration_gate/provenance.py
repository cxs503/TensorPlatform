"""出处与确定性哈希 (纯 stdlib)。所有产物都带 sha256，审计链可复核。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """确定性序列化：键排序、无多余空白、非 ASCII 保真。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_opt_file(path: str | Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    return sha256_file(p)


def write_json(path: str | Path, obj: Any) -> str:
    """写 JSON (确定性格式) 并返回内容 sha256。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    p.write_text(text + "\n", encoding="utf-8")
    return sha256_text(text)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ───────────────────────── git facts (只读) ─────────────────────────

def _git(repo: str | Path, *args: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        cp = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                            text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        return 127, f"{type(exc).__name__}: {exc}"
    return cp.returncode, (cp.stdout or cp.stderr).strip()


def git_facts(repo: str | Path, commit: str | None, main: str = "main",
              branch: str | None = None) -> dict[str, Any]:
    """节点基线事实（只读 git）：该 commit/branch 相对 main 的位置。

    返回字段：
      * ``merged_into_main`` — commit (或 branch 头) 是否已是 main 祖先（已合并）；
      * ``ahead_of_main``    — branch 领先 main 的提交数；
      * ``drift_on_main``    — fork 点之后 main 又前进了多少提交（=基线漂移量）；
      * ``fork_point``       — ``git merge-base main <branch>`` 的分叉点；
      * ``changed_files``    — ``git diff --name-only main...<commit>``。

    任何 git 失败都以 ``known=False`` + ``error`` 返回，由上层降级为 WITHHELD。
    """
    facts: dict[str, Any] = {
        "commit": commit, "branch": branch, "main_ref": main, "known": False,
        "merged_into_main": None, "ahead_of_main": None, "drift_on_main": None,
        "fork_point": None, "main_head": None, "changed_files": [], "error": "",
    }
    rc, head = _git(repo, "rev-parse", main)
    if rc != 0:
        facts["error"] = f"rev-parse {main}: {head}"
        return facts
    facts["main_head"] = head
    target = commit
    if branch:
        rc, bh = _git(repo, "rev-parse", "--verify", branch)
        if rc == 0:
            target = target or bh
            facts["branch_head"] = bh
    if not target:
        facts["error"] = "no base_commit/branch declared"
        return facts
    rc, obj = _git(repo, "cat-file", "-t", target)
    if rc != 0 or obj.strip() != "commit":
        facts["error"] = f"{target!r} is not a commit in repo"
        return facts
    facts["commit"] = target
    rc, _ = _git(repo, "merge-base", "--is-ancestor", target, main)
    if rc not in (0, 1):
        facts["error"] = "merge-base --is-ancestor failed"
        return facts
    facts["merged_into_main"] = (rc == 0)
    rc, cnt = _git(repo, "rev-list", "--count", f"{main}..{target}")
    if rc == 0:
        try:
            facts["ahead_of_main"] = int(cnt)
        except ValueError:  # pragma: no cover
            pass
    if branch:
        rc, mb = _git(repo, "merge-base", main, branch)
        if rc == 0 and mb:
            facts["fork_point"] = mb
            rc2, drift = _git(repo, "rev-list", "--count", f"{mb}..{main}")
            if rc2 == 0:
                try:
                    facts["drift_on_main"] = int(drift)
                except ValueError:  # pragma: no cover
                    pass
    rc, names = _git(repo, "diff", "--name-only", f"{main}...{target}")
    if rc == 0:
        facts["changed_files"] = [n for n in names.splitlines() if n]
    facts["known"] = True
    return facts


def worktree_head(path: str | Path) -> dict[str, Any]:
    """兄弟 worktree 的 HEAD/branch（只读，用于未建本地分支的节点）。"""
    p = Path(path)
    out: dict[str, Any] = {"path": str(p), "exists": p.is_dir(),
                           "head": None, "branch": None}
    if not out["exists"]:
        return out
    rc, h = _git(p, "rev-parse", "HEAD")
    if rc == 0:
        out["head"] = h
    rc, b = _git(p, "branch", "--show-current")
    if rc == 0 and b:
        out["branch"] = b
    return out
