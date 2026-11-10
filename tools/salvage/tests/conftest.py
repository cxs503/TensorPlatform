"""Test fixtures: make ``tools.salvage`` importable and build throwaway git repos.

All fixtures are domain-neutral (``alpha``/``widget`` style names) — the salvage
module must never depend on any scientific vocabulary.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "salvage-test",
    "GIT_AUTHOR_EMAIL": "salvage@example.invalid",
    "GIT_COMMITTER_NAME": "salvage-test",
    "GIT_COMMITTER_EMAIL": "salvage@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=ENV, check=True
    )
    return proc.stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# synthetic fixture\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A baseline repo (``main``) plus two mission worktrees cloned from it."""
    base = tmp_path / "baseline"
    _init(base)
    work = tmp_path / "work"
    work.mkdir()
    for key in ("m1001", "m1002"):
        git(base, "worktree", "add", "-q", "-b", f"agent/{key}", str(work / key), "main")
    return tmp_path


@pytest.fixture()
def baseline_repo(sandbox: Path) -> Path:
    return sandbox / "baseline"


@pytest.fixture()
def work_root(sandbox: Path) -> Path:
    return sandbox / "work"