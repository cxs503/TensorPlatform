#!/usr/bin/env python3
"""多租户并发写冲突检测与合并预演门 —— 数据模型与规范化工具。

设计约束（平台铁律）
--------------------
1. **零领域语义**：本包内不得出现任何具体学科/业务词条。哪些路径可写、窗口多长、
   冲突怎么处置，全部由 ``config`` 与调用方注入的数据决定。
2. **可复算**：所有判定结果是纯函数输出；同一输入必得逐字节相同的报告。
   报告里的 ``digest`` 由内容派生（sha256 规范化 JSON），不含墙钟。
3. **fail-closed**：无法判定的情形（仓不可读、模式越界、merge-tree 失败）一律
   记为 ``unknown`` 并拒绝放行，绝不"默认安全"。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

SCHEMA = "tenancy_gate/v1"
LEASE_SCHEMA = "tenancy_gate/lease/v1"
INTAKE_SCHEMA = "tenancy_gate/intake_matrix/v1"
REHEARSAL_SCHEMA = "tenancy_gate/rehearsal/v1"

WRITE = "write"
READ = "read"


class TenancyError(ValueError):
    """输入不合法（调用方按 fail-closed 处理）。"""


def canonical_json(obj) -> str:
    """规范化 JSON 文本：键排序、紧凑分隔、非 ASCII 保留（中文可读且稳定）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(obj) -> str:
    """内容摘要：同一逻辑内容必得同一摘要。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def parse_ts(value: str) -> datetime:
    """解析 ISO-8601 时间戳（``Z`` 结尾按 UTC 处理），无时区按 UTC。"""
    if not isinstance(value, str) or not value.strip():
        raise TenancyError("bad_timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - 输入错误路径
        raise TenancyError(f"bad_timestamp:{value}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_path(path: str) -> str:
    """规范化变更文件路径；越界/绝对/空路径抛 :class:`TenancyError`。"""
    from .pattern import GlobSyntaxError, normalize_glob

    try:
        return normalize_glob(path)
    except GlobSyntaxError as exc:
        raise TenancyError(f"invalid_path:{path}:{exc}") from exc


@dataclass(frozen=True)
class PathLease:
    """一条路径预留：某 mission 在某个仓里预留了一片路径（模式）。"""

    repo: str
    path_glob: str
    mission_id: str
    ts: str
    mode: str = WRITE

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "path_glob": self.path_glob,
            "mission_id": self.mission_id,
            "ts": self.ts,
            "mode": self.mode,
        }

    @staticmethod
    def from_dict(raw: dict) -> "PathLease":
        repo = str(raw.get("repo", "")).strip()
        mission = str(raw.get("mission_id", "")).strip()
        if not repo or not mission:
            raise TenancyError("lease_missing_repo_or_mission")
        mode = str(raw.get("mode", WRITE)).strip().lower()
        if mode not in (WRITE, READ):
            raise TenancyError(f"bad_lease_mode:{mode}")
        return PathLease(
            repo=repo,
            path_glob=normalize_path(str(raw.get("path_glob", ""))),
            mission_id=mission,
            ts=str(raw.get("ts", "")),
            mode=mode,
        )


@dataclass(frozen=True)
class FinishRecord:
    """一次收单声明的成果面：改了哪些仓、哪些文件。"""

    mission_id: str
    repo: str
    changed_files: tuple
    ts: str
    path_globs: tuple = ()

    @staticmethod
    def from_dict(raw: dict) -> "FinishRecord":
        repo = str(raw.get("repo", "")).strip()
        mission = str(raw.get("mission_id", "")).strip()
        if not repo or not mission:
            raise TenancyError("finish_missing_repo_or_mission")
        files = raw.get("changed_files") or []
        if not isinstance(files, (list, tuple)):
            raise TenancyError("changed_files_not_list")
        globs = raw.get("path_globs") or []
        return FinishRecord(
            mission_id=mission,
            repo=repo,
            changed_files=tuple(str(f) for f in files),
            ts=str(raw.get("ts", "")),
            path_globs=tuple(str(g) for g in globs),
        )


def with_digest(body: dict) -> dict:
    """给报告体补上 ``digest`` 字段（不含 digest 自身）。"""
    out = dict(body)
    out.pop("digest", None)
    out["digest"] = digest(out)
    return out