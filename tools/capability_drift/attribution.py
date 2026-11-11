"""退化定位：对能力声明的 blame 定位点做 ``git blame``，指出引入退化的提交。

为什么不直接 ``git log -- path``：路径级日志会把所有历史提交都列出来，无法定位到
「改了什么导致数字变了」。注册表为每个能力显式声明「哪些文件的哪一行决定这个数字」
（例如求解器默认松弛因子所在行），blame 该行 → 最后修改它的提交即嫌疑 commit。
定位点的选择是领域知识（域仓注入），本模块只做通用解析。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .model import BlamePattern, Capability


@dataclass(frozen=True)
class Suspicion:
    capability: str
    path: str
    pattern: str
    line: int
    text: str
    commit: str
    short: str
    subject: str = ""
    author: str = ""
    committed_at: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "capability": self.capability,
            "path": self.path,
            "pattern": self.pattern,
            "line": self.line,
            "text": self.text.strip(),
            "commit": self.commit,
            "short": self.short,
            "subject": self.subject,
            "author": self.author,
            "committed_at": self.committed_at,
        }


def _git(repo: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def parse_blame_porcelain(text: str) -> List[Tuple[str, int, str]]:
    """把 ``git blame --line-porcelain`` 解析成 [(commit, 行号, 行文本)]。"""
    rows: List[Tuple[str, int, str]] = []
    commit = ""
    final_line = 0
    for raw in text.splitlines():
        if re.match(r"^[0-9a-f]{40}\s+\d+\s+\d+", raw):
            commit = raw.split()[0]
            final_line = int(raw.split()[2])
            continue
        if raw.startswith("\t"):
            rows.append((commit, final_line, raw[1:]))
    return rows


def blame_pattern(repo: str | Path, capability_id: str, bp: BlamePattern) -> List[Suspicion]:
    path = Path(repo) / bp.path
    if not path.exists():
        return []
    try:
        out = _git(repo, "blame", "--line-porcelain", "--", bp.path)
    except RuntimeError:
        return []
    regex = re.compile(bp.pattern)
    found: List[Suspicion] = []
    for commit, line_no, text in parse_blame_porcelain(out):
        if regex.search(text):
            found.append(
                Suspicion(
                    capability=capability_id,
                    path=bp.path,
                    pattern=bp.pattern,
                    line=line_no,
                    text=text,
                    commit=commit,
                    short=commit[:10],
                )
            )
    return found


def commit_info(repo: str | Path, commit: str) -> Dict[str, str]:
    try:
        out = _git(repo, "log", "-1", "--format=%H%x00%s%x00%an%x00%cI", commit, "--")
    except RuntimeError:
        return {}
    parts = out.strip().split("\x00")
    if len(parts) < 4:
        return {}
    return {"commit": parts[0], "subject": parts[1], "author": parts[2], "committed_at": parts[3]}


def attribute(
    repo: str | Path,
    capability: Capability,
    *,
    limit: int = 5,
) -> List[Suspicion]:
    """返回该能力的嫌疑提交（按提交时间新→旧，去重）。"""
    raw: List[Suspicion] = []
    for bp in capability.blame:
        raw.extend(blame_pattern(repo, capability.id, bp))
    # 同一提交可能命中多行：按 commit 去重，保留第一行作为证据
    by_commit: Dict[str, Suspicion] = {}
    for s in raw:
        info = commit_info(repo, s.commit)
        enriched = Suspicion(
            capability=s.capability,
            path=s.path,
            pattern=s.pattern,
            line=s.line,
            text=s.text,
            commit=s.commit,
            short=s.short,
            subject=info.get("subject", ""),
            author=info.get("author", ""),
            committed_at=info.get("committed_at", ""),
        )
        prev = by_commit.get(s.commit)
        if prev is None or enriched.committed_at > prev.committed_at:
            by_commit[s.commit] = enriched
    ordered = sorted(by_commit.values(), key=lambda s: s.committed_at, reverse=True)
    return ordered[:limit]


def attribute_report(
    repo: str | Path, capabilities: Sequence[Capability], drifting: Iterable[str], *, limit: int = 5
) -> Dict[str, List[Dict[str, object]]]:
    """只对漂移能力做定位（省时）；返回 cap_id → 嫌疑提交列表。"""
    out: Dict[str, List[Dict[str, object]]] = {}
    by_id = {c.id: c for c in capabilities}
    for cap_id in drifting:
        cap = by_id.get(cap_id)
        if cap is None:
            continue
        out[cap_id] = [s.as_dict() for s in attribute(repo, cap, limit=limit)]
    return out