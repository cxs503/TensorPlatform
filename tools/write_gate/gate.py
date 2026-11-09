#!/usr/bin/env python3
"""三合一写入边界门（编排层）：白名单越界 + 跨仓污染 + 凭证泄漏 + 大文件拦截。

一条变更走四道判据，任一判红即整体 ``FAIL``（默认拒绝、fail-closed）：

    path ──canonicalize──┬─ 跨仓判据（>root/绝对/外部根/嵌套仓/.git/符号链接/仓不符）
                         ├─ 白名单判据（deny → read_only → allow → 兜底拒绝）
                         ├─ 内容判据（凭证模式 + 熵闸 + 脱敏留痕）
                         └─ 大文件/二进制判据（超阈值或二进制内容且不在豁免列表）

判决是纯函数：同一输入必得逐字节相同 JSON（``digest`` 由内容派生，无墙钟）。
每条违规含 ``evidence_sha256``（patch 片段摘要），整份判决有稳定 ``digest``。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import binary_gate, crossrepo
from .model import Change, Violation, Verdict, WriteGateError, digest as model_digest
from .pathnorm import canonicalize
from .policy import policy_ref
from .secrets import looks_binary, scan_text
from .whitelist import decide

SAFE_GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")

_KIND_BY_STATUS = {"A": "ADD", "M": "MODIFY", "T": "MODIFY", "C": "ADD", "R": "MODIFY", "D": "DELETE"}


def _evidence_hash(content: Optional[str], path: str, line: Optional[int] = None) -> str:
    """计算违规证据摘要哈希（patch 片段 sha256）。

    对内容违规：取命中行前后上下文（或全内容截断）的 sha256。
    对路径违规：取路径 + 违规类型的 sha256。
    """
    if content is not None:
        if line is not None:
            lines = content.splitlines()
            start = max(0, line - 3)
            end = min(len(lines), line + 2)
            fragment = "\n".join(lines[start:end])
        else:
            fragment = content[:2000]  # 截断避免大文件拖慢
    else:
        fragment = f"{path}:no_content"
    return hashlib.sha256(fragment.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────── 核心判决 ───────────────────────────
def evaluate_changes(changes: Sequence[Change], cfg: Dict[str, Any], *,
                     root: Optional[str | Path] = None,
                     active_repo: Optional[str] = None,
                     policy_path: Optional[str] = None) -> Verdict:
    """对一组变更做四合一判决，返回可归档的 :class:`Verdict`。"""
    verdict = Verdict(policy_ref=policy_ref(cfg, policy_path),
                      meta={"generator": "tools/write_gate", "active_repo": active_repo,
                            "root": None if root is None else str(root),
                            "changes": len(changes)})
    try:
        root_path = crossrepo.resolve_root(root)
    except WriteGateError as exc:
        verdict.add(Violation("root_unreadable", "invalid", "", rule_id="internal",
                              detail=str(exc)))
        verdict.decision = "unknown"
        return _finalize(verdict, changes)

    for change in changes:
        per_file_before = len(verdict.violations)
        entry: Dict[str, Any] = {"path": change.path, "raw_path": change.path, "kind": change.kind,
                                 "repo": change.repo, "source": change.source,
                                 "content_bytes": None, "content_sha256": None}
        try:
            canon = canonicalize(change.path)
        except WriteGateError as exc:
            verdict.add(Violation(str(exc), "invalid", change.path, rule_id="pathnorm",
                                  detail="路径非法：无法规范化（fail-closed）"))
            entry.update({"canonical": None, "flags": [], "status": "unknown"})
            verdict.files.append(entry)
            continue

        entry["path"] = canon.text
        entry["canonical"] = canon.to_json()
        entry["flags"] = list(canon.flags)

        # R2: 跨仓判据
        boundary = crossrepo.check_path(canon, cfg, root=root_path,
                                        change_repo=change.repo, active_repo=active_repo)
        for violation in boundary:
            violation.evidence_sha256 = _evidence_hash(change.content, canon.text)
            verdict.add(violation)

        # R1: 白名单判据
        decision = decide(canon.text, cfg)
        # 归因优先边界类：越界已判红的路径不再按"白名单未命中"重复计一次，
        # 否则一次越界会在 by_category 里出现两条（审计口径失真）。
        if not decision.allowed and not boundary:
            if decision.code == "deny_glob" and cfg["whitelist"]["treat_deny_as"] == "cross_repo":
                category = "cross_repo"
            else:
                category = "whitelist"
            v = Violation(decision.code, category, canon.text,
                          rule_id=decision.rule_id, detail=decision.detail)
            v.evidence_sha256 = _evidence_hash(change.content, canon.text)
            verdict.add(v)

        # R3: 内容判据（凭证扫描）
        if change.content is not None and change.kind != "DELETE":
            entry["content_bytes"] = len(change.content.encode("utf-8"))
            entry["content_sha256"] = model_digest(change.content)
            hits, suppressed = scan_text(change.content, cfg, path=canon.text)
            for hit in hits:
                v = Violation("secret_leak", "secret", canon.text, rule_id=hit.rule_id,
                              detail="疑似凭证原文进入提交内容（已脱敏，不落原文）",
                              line=hit.line, excerpt=hit.excerpt)
                v.evidence_sha256 = _evidence_hash(change.content, canon.text, hit.line)
                verdict.add(v)
            verdict.advisories.extend(suppressed)

        # R4: 大文件/二进制判据
        content_bytes = entry.get("content_bytes")
        is_binary = False
        if change.content is not None and change.kind != "DELETE":
            # 简单二进制检测：含 NUL 或解码异常
            is_binary = "\x00" in change.content[:8192]
        binary_violations = binary_gate.check_binary_violations(
            canon.text, cfg, file_size=content_bytes, is_binary=is_binary)
        for v in binary_violations:
            v.evidence_sha256 = _evidence_hash(change.content, canon.text)
            verdict.add(v)

        blocked_here = [v for v in verdict.violations[per_file_before:]]
        categories = {v.category for v in blocked_here}
        entry["status"] = "unknown" if "invalid" in categories else ("block" if blocked_here else "allow")
        verdict.files.append(entry)

    return _finalize(verdict, changes)


def _finalize(verdict: Verdict, changes: Sequence[Change]) -> Verdict:
    """去重 + 汇总。同一违规可能由多条采集通道命中（staged+paths），去重后计数才有意义。"""
    seen: set = set()
    unique: List[Violation] = []
    for violation in verdict.violations:
        key = (violation.category, violation.code, violation.path,
               violation.rule_id, violation.line, violation.excerpt)
        if key in seen:
            continue
        seen.add(key)
        unique.append(violation)
    verdict.violations = unique

    seen_files: set = set()
    files: List[Dict[str, Any]] = []
    for entry in verdict.files:
        key = (entry["path"], entry["kind"])
        if key in seen_files:
            continue
        seen_files.add(key)
        files.append(entry)
    verdict.files = files

    verdict.meta["changes"] = len(files)
    verdict.meta["by_category"] = verdict.counts()
    return verdict


# ─────────────────────────── git 采集 ───────────────────────────
def _git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=SAFE_GIT_ENV)
    if proc.returncode != 0:
        raise WriteGateError(f"git_failed:{' '.join(args)}:{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def parse_name_status_z(raw: bytes) -> List[Change]:
    """解析 ``git diff --cached --name-status -z``（含 R/C 的双路径形态）。"""
    parts = raw.split(b"\x00")
    changes: List[Change] = []
    i = 0
    while i < len(parts):
        status = parts[i].decode("utf-8", "replace")
        if not status:
            i += 1
            continue
        code = status[0]
        if code in ("R", "C"):
            if i + 2 >= len(parts):
                break
            path = parts[i + 2].decode("utf-8", "replace")
            i += 3
        else:
            if i + 1 >= len(parts):
                break
            path = parts[i + 1].decode("utf-8", "replace")
            i += 2
        changes.append(Change(path=path, kind=_KIND_BY_STATUS.get(code, "MODIFY"), source="staged"))
    return changes


def collect_staged(root: str | Path, cfg: Dict[str, Any]) -> Tuple[List[Change], List[Dict[str, Any]]]:
    """采集暂存区变更（只读 git，不动索引/工作树），带内容用于凭证扫描。"""
    root_path = crossrepo.resolve_root(root)
    raw = _git(root_path, "diff", "--cached", "--name-status", "-z", "--diff-filter=ACMRT")
    advisories: List[Dict[str, Any]] = []
    out: List[Change] = []
    max_bytes = int(cfg["secrets"]["max_blob_bytes"])
    for change in parse_name_status_z(raw):
        content: Optional[str] = None
        try:
            blob = _git(root_path, "show", f":{change.path}")
        except WriteGateError as exc:
            advisories.append({"path": change.path, "reason": "index_blob_unreadable",
                               "detail": str(exc)[:200]})
            blob = b""
        # 只做"可判性"筛选；凭证判据只在 evaluate_changes 一处实现（单一判据，避免双扫漂移）
        if blob:
            if len(blob) > max_bytes:
                advisories.append({"path": change.path, "reason": "blob_too_large", "bytes": len(blob)})
            elif looks_binary(blob):
                advisories.append({"path": change.path, "reason": "binary_skipped", "bytes": len(blob)})
            else:
                content = blob.decode("utf-8", "replace")
        out.append(Change(path=change.path, kind=change.kind, content=content, source="staged"))
    return out, advisories


def evaluate_staged(root: str | Path, cfg: Dict[str, Any], *,
                    active_repo: Optional[str] = None,
                    policy_path: Optional[str] = None) -> Verdict:
    """预提交门主入口：读索引 → 四合一判决。"""
    changes, advisories = collect_staged(root, cfg)
    verdict = evaluate_changes(changes, cfg, root=root, active_repo=active_repo,
                               policy_path=policy_path)
    verdict.advisories.extend(advisories)
    return verdict


def evaluate_paths(paths: Iterable[str], cfg: Dict[str, Any], *,
                   root: Optional[str | Path] = None,
                   active_repo: Optional[str] = None,
                   policy_path: Optional[str] = None,
                   contents: Optional[Dict[str, str]] = None) -> Verdict:
    """按路径判（本地自检/CI 复用）；``contents`` 可注入文本做凭证扫描。"""
    contents = contents or {}
    changes = [Change(path=p, content=contents.get(p), source="paths") for p in paths]
    return evaluate_changes(changes, cfg, root=root, active_repo=active_repo,
                            policy_path=policy_path)
