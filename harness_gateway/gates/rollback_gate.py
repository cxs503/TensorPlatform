"""门 3：git 回滚门。

门链的逻辑是"任何一道门失败 → 世界必须回到打补丁前的字节状态"，所以回滚门
不是事后清理，而是**全程武装**：进管线第一步就 checkpoint（git HEAD + 每文件
sha256 + 字节备份），任何后续门 FAIL 立刻恢复并**逐文件校验哈希**。
校验不过 = FAIL_ROLLBACK_INCOMPLETE，比"没拦住"更严重，必须告警。

三道保障：
    git checkout -- <path>   已跟踪文件走 git（可审计、可 diff）
    备份字节回写             未跟踪文件（新增/临时件）走备份
    hash 逐文件比对           回滚完成的唯一硬判据（不看 git 退出码）

另有 drill()：对沙箱内的临时副本做"改坏→恢复→比对"，证明回滚机器本身可用
（"门自己也要被验"），证据落 rollback_drill。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from harness_gateway.gates.base import GateResult, GateStatus, fail, pass_, timed


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@dataclass
class FileState:
    relpath: str
    exists: bool
    sha256: str = ""
    mode: int = 0o644
    tracked: bool = False
    backup: str = ""  # 备份文件绝对路径（若 run_dir 提供）

    def to_dict(self) -> dict:
        return {
            "relpath": self.relpath,
            "exists": self.exists,
            "sha256": self.sha256,
            "mode": oct(self.mode),
            "tracked": self.tracked,
            "backup": self.backup,
        }


@dataclass
class Checkpoint:
    repo_root: str
    git_head: str
    files: dict[str, FileState] = field(default_factory=dict)
    pre_existing_diffs: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "repo_root": self.repo_root,
            "git_head": self.git_head,
            "created_at": self.created_at,
            "files": {k: v.to_dict() for k, v in self.files.items()},
            "pre_existing_diffs": self.pre_existing_diffs,
        }


def checkpoint(
    repo_root: str | Path,
    relpaths: Iterable[str],
    *,
    run_dir: str | Path | None = None,
) -> Checkpoint:
    repo_root = Path(repo_root)
    head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    relpaths = [str(p) for p in relpaths]
    diffs = _git(repo_root, "diff", "--name-only", "HEAD", "--", *relpaths).stdout.split()
    ckpt = Checkpoint(
        repo_root=str(repo_root),
        git_head=head,
        pre_existing_diffs=sorted(diffs),
        created_at=subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip(),
    )
    backup_dir = Path(run_dir) / "checkpoint" if run_dir else None
    if backup_dir:
        backup_dir.mkdir(parents=True, exist_ok=True)
    for rel in relpaths:
        path = repo_root / rel
        state = FileState(relpath=rel, exists=path.exists())
        if path.exists():
            state.sha256 = _sha256(path)
            state.mode = path.stat().st_mode & 0o777
            tracked = _git(repo_root, "ls-files", "--error-unmatch", rel).returncode == 0
            state.tracked = tracked
            if backup_dir:
                dst = backup_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
                state.backup = str(dst)
        ckpt.files[rel] = state
    return ckpt


def rollback(repo_root: str | Path, ckpt: Checkpoint, *, gate: str = "git_rollback") -> GateResult:
    """把 checkpoint 覆盖的路径恢复到 checkpoint 时的字节状态，并逐文件校验。"""
    repo_root = Path(repo_root)
    result = GateResult(gate, GateStatus.PASS, "")
    restored: list[dict] = []
    problems: list[str] = []
    with timed(result):
        for rel, state in ckpt.files.items():
            path = repo_root / rel
            action = ""
            if state.tracked:
                proc = _git(repo_root, "checkout", "--", rel)
                action = "git checkout --"
                if proc.returncode != 0:
                    problems.append(f"{rel}: git checkout failed: {proc.stderr.strip()[:200]}")
            if not state.tracked or not path.exists():
                if state.exists and state.backup:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(state.backup, path)
                    path.chmod(state.mode)
                    action = (action + "+byte-restore").lstrip("+")
                elif not state.exists and path.exists():
                    path.unlink()
                    action = (action + "+unlink").lstrip("+")
            now_exists = path.exists()
            now_sha = _sha256(path) if now_exists else ""
            match = now_exists == state.exists and (not state.exists or now_sha == state.sha256)
            restored.append(
                {
                    "relpath": rel,
                    "action": action or "noop",
                    "expected_exists": state.exists,
                    "now_exists": now_exists,
                    "expected_sha256": state.sha256,
                    "now_sha256": now_sha,
                    "match": match,
                }
            )
            if not match:
                problems.append(f"{rel}: hash mismatch after rollback")
        dirty = _git(repo_root, "diff", "--name-only", str(ckpt.git_head), "--", *ckpt.files.keys()).stdout.split()
        result.evidence = {
            "git_head": ckpt.git_head,
            "files": restored,
            "problems": problems,
            "git_diff_vs_head_after_rollback": sorted(dirty),
            "criterion": "every file byte-identical to checkpoint (sha256), tracked files via git checkout",
        }
        if problems:
            result.status = GateStatus.FAIL
            result.detail = "rollback incomplete: " + "; ".join(problems)[:600]
        else:
            result.detail = f"{len(restored)} file(s) restored byte-exactly to checkpoint"
    return result


def drill(work_dir: str | Path, *, gate: str = "git_rollback_drill") -> GateResult:
    """回滚机器自检：临时副本改坏 → 备份恢复 → 哈希比对。"""
    result = GateResult(gate, GateStatus.PASS, "")
    work = Path(work_dir) / "rollback_drill"
    with timed(result):
        work.mkdir(parents=True, exist_ok=True)
        target = work / "victim.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        good = _sha256(target)
        backup = work / "victim.py.bak"
        shutil.copy2(target, backup)
        target.write_text("VALUE = 1\ndef broken(:\n", encoding="utf-8")
        broken = _sha256(target)
        shutil.copy2(backup, target)
        healed = _sha256(target)
        ok = broken != good and healed == good
        result.evidence = {
            "good_sha256": good,
            "injected_sha256": broken,
            "healed_sha256": healed,
            "injection_changed_bytes": broken != good,
            "restore_restored_bytes": healed == good,
            "criterion": "injected corruption changes sha256 AND restore returns exact pre-injection sha256",
        }
        if not ok:
            result.status = GateStatus.FAIL
            result.detail = "rollback machinery failed its own drill"
        else:
            result.detail = "rollback machinery verified (corruption->restore round trip)"
    return result