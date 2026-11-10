"""工件盘点与读数 (纯 stdlib)：glob 扫描 + sha256 + 信封识别 + JSON pointer 取值。

设计要点:

* 每个工件记录 ``rel``/``bytes``/``sha256``，审计链可逐字节复核；
* 信封 (envelope) 是契约首选格式，缺失时退回 manifest 声明的 adapter 指针
  (legacy 工件也能入基线，不因格式未统一而丢失证据)；
* 节点检出 git HEAD，保证"工件属于哪个提交"可追溯。
"""

from __future__ import annotations

import glob
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provenance import sha256_file


class IngestError(RuntimeError):
    """工件不可读 / 指针无值。"""


def git_facts(repo: str | Path) -> dict[str, Any]:
    """只读 git 事实: 分支 + HEAD + 是否有未提交改动。"""
    def _run(*args: str) -> tuple[int, str]:
        try:
            cp = subprocess.run(["git", *args], cwd=str(repo),
                                capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return cp.returncode, cp.stdout.strip()

    rc_head, head = _run("rev-parse", "HEAD")
    rc_br, branch = _run("rev-parse", "--abbrev-ref", "HEAD")
    rc_st, status = _run("status", "--porcelain")
    return {
        "branch": branch if rc_br == 0 else None,
        "head": head if rc_head == 0 else None,
        "dirty": bool(status) if rc_st == 0 else None,
    }


def _pattern_hit(root: Path, pattern: str, rel: str) -> bool:
    return any(str(Path(h).relative_to(root)) == rel
               for h in glob.glob(str(root / pattern), recursive=True))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        cp = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return cp.stdout if cp.returncode == 0 else ""


def _status_map(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _git(root, "status", "--porcelain").splitlines():
        if len(line) < 4:
            continue
        out[line[3:].strip().strip('"')] = line[:2]
    return out


def _commit_epoch(root: Path, rel: str) -> int | None:
    raw = _git(root, "log", "-1", "--format=%ct", "--", rel).strip()
    try:
        return int(raw.splitlines()[0])
    except (ValueError, IndexError):
        return None


def _epoch_utc(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except ValueError:
        return None


def scan_node(node: dict[str, Any], work_root: str | Path,
              started_utc: str | None = None) -> dict[str, Any]:
    """盘点一个节点的工件: 必需/期望 glob 命中、sha256、git 事实、新鲜度。

    新鲜度 (``fresh``) 用于**归属隔离**: 工作树里往往躺着上一波留下的同名
    工件，glob 命中不等于本波产出。判定: 未跟踪 (``??``) 或最近一次提交
    时间 >= 波次开始时刻 (manifest ``campaign.started_utc``) 才算本波产物；
    陈旧命中只记 ``stale_excluded``，不静默丢弃也不计入完整性。
    """
    root = Path(work_root) / node["worktree"]
    result: dict[str, Any] = {
        "node_ext_id": node["ext_id"],
        "repo": node["repo"],
        "worktree": node["worktree"],
        "worktree_path": str(root),
        "exists": root.is_dir(),
        "git": git_facts(root) if root.is_dir() else {"branch": None, "head": None,
                                                      "dirty": None},
        "artifacts": [],
        "required": node["required_artifacts"],
        "required_present": [],
        "required_missing": list(node["required_artifacts"]),
        "expected_present": [],
        "expected_missing": list(node["expected_artifacts"]),
        "complete": False,
        "completeness": 0.0,
    }
    if not root.is_dir():
        return result

    seen: dict[str, dict[str, Any]] = {}
    status_map = _status_map(root)
    started_ts = _epoch_utc(started_utc)

    def _add(rel: str) -> None:
        p = root / rel
        if not p.is_file() or rel in seen:
            return
        st = status_map.get(rel, "")
        untracked = st.startswith("??")
        commit_ts = None if untracked else _commit_epoch(root, rel)
        fresh = (untracked or started_ts is None
                 or (commit_ts is not None and commit_ts >= started_ts))
        seen[rel] = {"rel": rel, "bytes": p.stat().st_size, "sha256": _sha256(p),
                     "git_status": st or None, "commit_epoch": commit_ts,
                     "fresh": bool(fresh)}

    for rel in node["required_artifacts"]:
        if (root / rel).is_file():
            result["required_present"].append(rel)
            _add(rel)
    result["required_missing"] = [r for r in node["required_artifacts"]
                                  if r not in result["required_present"]]
    matched_patterns: list[str] = []
    for pattern in node["expected_artifacts"]:
        hits = sorted(str(Path(h).relative_to(root))
                      for h in glob.glob(str(root / pattern), recursive=True)
                      if Path(h).is_file())
        if hits:
            matched_patterns.append(pattern)
            for h in hits:
                if h not in result["expected_present"]:
                    result["expected_present"].append(h)
                _add(h)
    result["expected_missing"] = [p for p in node["expected_artifacts"]
                                  if p not in matched_patterns]
    result["expected_patterns_matched"] = matched_patterns
    result["artifacts"] = sorted(seen.values(), key=lambda a: a["rel"])
    stale = sorted(a["rel"] for a in result["artifacts"] if not a["fresh"])
    result["stale_excluded"] = stale
    fresh_set = {a["rel"] for a in result["artifacts"] if a["fresh"]}
    result["required_present"] = [r for r in result["required_present"] if r in fresh_set]
    result["required_missing"] = [r for r in node["required_artifacts"]
                                  if r not in result["required_present"]]
    result["expected_present"] = [r for r in result["expected_present"] if r in fresh_set]
    matched_patterns = [p for p in matched_patterns
                        if any(f.startswith(p.replace("*", "").split("/")[0]) for f in fresh_set)] \
        if False else matched_patterns
    result["fresh_patterns_matched"] = [
        p for p in matched_patterns
        if any(_pattern_hit(root, p, rel) for rel in fresh_set)]
    result["expected_missing"] = [p for p in node["expected_artifacts"]
                                  if p not in result["fresh_patterns_matched"]]
    req_total = len(node["required_artifacts"])
    req_ok = len(result["required_present"])
    exp_total = req_total + len(node["expected_artifacts"])
    exp_ok = req_ok + len(result["fresh_patterns_matched"])
    result["completeness"] = (round(min(1.0, exp_ok / exp_total), 6)
                              if exp_total else 0.0)
    result["complete"] = bool(req_total == req_ok and not result["expected_missing"])
    return result


def load_json(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_file():
        raise IngestError(f"missing artifact: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"not valid JSON: {p}: {exc}") from exc


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer 取值 (只支持对象键与数组下标)。"""
    if pointer in ("", "/"):
        return doc
    cur = doc
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError) as exc:
                raise IngestError(f"pointer {pointer!r}: bad index {token!r}") from exc
        elif isinstance(cur, dict):
            if token not in cur:
                raise IngestError(f"pointer {pointer!r}: key {token!r} absent")
            cur = cur[token]
        else:
            raise IngestError(f"pointer {pointer!r}: cannot descend into "
                              f"{type(cur).__name__}")
    return cur


ENVELOPE_BASENAMES = ("envelope.json", "envelope.meta.json")


def find_envelope(scan: dict[str, Any]) -> str | None:
    """在盘点结果里找信封工件 (约定名或 *envelope*.json)。"""
    for art in scan["artifacts"]:
        base = Path(art["rel"]).name
        if base in ENVELOPE_BASENAMES or (base.endswith(".json") and "envelope" in base):
            return art["rel"]
    return None