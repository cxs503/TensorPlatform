"""能力台账 × 工件树 双向对账器（战役节点 p176w1-n03 成熟化）。

背景
----
``tensor_capability_deltas`` 是组织能力账的流水（每个 succeeded mission 一行，
``artifacts`` 声明该 mission 交付的工件路径，``evidence`` 里带 ``head_commit``）。
台账长期存在两类静默腐化：

* 账上有、树上无——声明的工件被删/改名/从未提交，能力声明变成空头支票；
* 树上有、账上无——工件产出了但没进能力账，成果不可发现、不可复用。

本模块做**双向对账**，输出可审计指标（失配明细、失配率、report_owed 欠账），
不信任任何一方的自述，只认磁盘、git 树与 DB 三方的交叉证据。

三类失配（机器可分类、可计数）
------------------------------
1. ``declared_missing``  「声明了但不存在」：delta.artifacts 里的路径在工作树
   上找不到对应文件/目录（含 glob 无匹配）。
2. ``undeclared_present``「存在但没声明」：``artifact_roots`` 下扫描到的文件，
   没有任何一条 delta 声明覆盖（精确路径 / 目录前缀 / glob）。
3. ``not_in_head``      「存在但不在 head」：
   a) subject_type=``commit``：记录指向的 ``head_commit`` 不是当前 git ref 的
      祖先（sha 不存在/不可解析同样计入，reason 区分）；
   b) subject_type=``file``：声明工件在磁盘上存在，但不在当前 ref 的 git 树里
      （未提交或只存在于别的分支）。

可审计指标
----------
* ``declared_total`` = 去重后的声明路径数（多 mission 声明同一路径只算一次检查）
* ``present_total``  = ``artifact_roots`` 下扫描到的文件数
* ``commit_total``   = 去重后的 head_commit 引用数（git 不可用时计 0 并标注）
* ``total_checks``   = 上述三者之和（失配率分母，显式可复算）
* ``mismatch_rate``  = 0.0 if total_checks == 0 else mismatch_count / total_checks
* ``report_owed``    = 有（实质）delta 声明但没有报告记录的 mission 列表；
  无报告数据源时按「无证据即欠账」保守计（``check_available=False`` 标注）。

台账是多仓共享的：``mission_repo`` 过滤（CLI ``--mission-repo``）只保留本仓
mission 的声明，否则跨仓工件路径会全部误判为「声明了但不存在」。

边界
----
本模块只读：不写 DB、不改 git。仓库根、artifact 根、git ref 全部参数化，
默认值仅作 CLI 便利；测试用 tmp 目录合成数据，零领域语义（不含船型/公式/域词条）。

用法
----
    python -m backend.tools.capability_reconciler \
        --repo-root . --artifact-root artifacts \
        --deltas-json deltas.json --reports-json reports.json \
        --out runs/capability_reconcile.json [--fail-on-mismatch]

Python 调用::

    from tools.capability_reconciler import (
        CapabilityReconciler, DeltaRecord, load_deltas_from_json,
    )
    rec = CapabilityReconciler(repo_root=".")
    report = rec.reconcile(load_deltas_from_json("deltas.json"))
    print(report.to_dict()["mismatch_rate"])
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

__all__ = [
    "KIND_DECLARED_MISSING",
    "KIND_UNDECLARED_PRESENT",
    "KIND_NOT_IN_HEAD",
    "MISMATCH_KINDS",
    "NON_SUBSTANTIVE_CATEGORY",
    "DeltaRecord",
    "Mismatch",
    "ReconcileReport",
    "CapabilityReconciler",
    "normalize_path",
    "is_glob",
    "load_deltas_from_json",
    "load_deltas_from_db",
    "load_report_mission_ids_from_json",
    "load_report_mission_ids_from_db",
    "main",
]

KIND_DECLARED_MISSING = "declared_missing"
KIND_UNDECLARED_PRESENT = "undeclared_present"
KIND_NOT_IN_HEAD = "not_in_head"
MISMATCH_KINDS: Tuple[str, ...] = (
    KIND_DECLARED_MISSING,
    KIND_UNDECLARED_PRESENT,
    KIND_NOT_IN_HEAD,
)

NON_SUBSTANTIVE_CATEGORY = "no_substantive_delta"
COMMIT_EVIDENCE_KEYS: Tuple[str, ...] = (
    "head_commit",
    "commit",
    "commit_sha",
    "head_commit_sha",
)

DEFAULT_IGNORE_PATTERNS: Tuple[str, ...] = (
    ".gitkeep",
    ".gitignore",
    ".recycle/*",
    "__pycache__/*",
    "*.pyc",
    ".DS_Store",
    "*.tmp",
    "*.swp",
)

_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$", re.I)

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_json(value: Any, default: Any) -> Any:
    """DB/JSON 里的 JSONB 列在 sqlite/psycopg 下可能是 str，统一解析。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return default
        return parsed if isinstance(parsed, type(default)) else default
    return default


def normalize_path(raw: Any) -> Optional[str]:
    """把声明/磁盘路径归一到仓库相对 POSIX 路径；不可用引用返回 None。

    丢弃：空串、URL（含 ``://``）、绝对路径、纯占位符。保留 glob 通配模式。
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if "://" in text or text.startswith(("git@", "urn:", "http:")):
        return None
    text = text.replace("\\", "/")
    if text.startswith("/"):
        return None
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/{2,}", "/", text)
    if text in ("", "."):
        return None
    if len(text) > 1 and text.endswith("/"):
        text = text.rstrip("/")
    return text or None


def is_glob(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def path_matches_declaration(declaration: str, candidate: str) -> bool:
    """声明 ``declaration`` 是否覆盖工件 ``candidate``（精确 / 目录前缀 / glob）。"""
    if declaration == candidate:
        return True
    if is_glob(declaration):
        return fnmatch.fnmatch(candidate, declaration)
    return candidate.startswith(declaration + "/")


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


def extract_head_commit(evidence: Mapping[str, Any]) -> Optional[str]:
    for key in COMMIT_EVIDENCE_KEYS:
        val = evidence.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    nested = evidence.get("git")
    if isinstance(nested, Mapping):
        for key in ("head_commit", "commit", "commit_sha"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


@dataclass
class DeltaRecord:
    """一条能力增量声明的对账面（只取对账需要的字段）。"""

    mission_id: Optional[int] = None
    category: str = ""
    summary: str = ""
    artifacts: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    head_commit: Optional[str] = None
    row_id: Optional[int] = None
    mission_repo: Optional[str] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DeltaRecord":
        evidence = _coerce_json(row.get("evidence"), {})
        artifacts = _coerce_json(row.get("artifacts"), [])
        if not isinstance(artifacts, list):
            artifacts = []
        head_commit = row.get("head_commit")
        if not head_commit:
            head_commit = extract_head_commit(evidence)
        return cls(
            mission_id=_as_int(row.get("mission_id")),
            category=str(row.get("category") or ""),
            summary=str(row.get("summary") or ""),
            artifacts=[str(a) for a in artifacts if isinstance(a, (str, int, float))],
            evidence=evidence,
            head_commit=str(head_commit).strip() if head_commit else None,
            row_id=_as_int(row.get("id")),
            mission_repo=(str(row.get("mission_repo")).strip() if row.get("mission_repo") else None),
        )

    def commit_refs(self) -> List[str]:
        refs: List[str] = []
        if self.head_commit:
            refs.append(self.head_commit)
        for key in COMMIT_EVIDENCE_KEYS:
            val = self.evidence.get(key)
            if isinstance(val, str) and val.strip():
                refs.append(val.strip())
        nested = self.evidence.get("git")
        if isinstance(nested, dict):
            val = nested.get("head_commit") or nested.get("commit")
            if isinstance(val, str) and val.strip():
                refs.append(val.strip())
        seen = set()
        out = []
        for ref in refs:
            if ref not in seen:
                seen.add(ref)
                out.append(ref)
        return out


@dataclass
class Mismatch:
    kind: str
    subject_type: str  # artifact | file | commit
    subject: str
    mission_ids: List[int] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject_type": self.subject_type,
            "subject": self.subject,
            "mission_ids": list(self.mission_ids),
            "reason": self.reason,
        }


@dataclass
class ReconcileReport:
    schema_version: str = SCHEMA_VERSION
    generated_at: str = ""
    repo_root: str = ""
    git_ref: str = "HEAD"
    git_head_sha: Optional[str] = None
    git_available: bool = False
    artifact_roots: List[str] = field(default_factory=list)

    declared_total: int = 0
    present_total: int = 0
    commit_total: int = 0
    total_checks: int = 0

    mismatches: Dict[str, List[Mismatch]] = field(default_factory=dict)
    report_owed: Dict[str, Any] = field(default_factory=dict)
    ledger: Dict[str, Any] = field(default_factory=dict)
    ignored_refs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ---- 派生指标 ----
    @property
    def mismatch_count(self) -> int:
        return sum(len(v) for v in self.mismatches.values())

    @property
    def by_kind(self) -> Dict[str, int]:
        return {kind: len(self.mismatches.get(kind, [])) for kind in MISMATCH_KINDS}

    @property
    def mismatch_rate(self) -> float:
        if self.total_checks <= 0:
            return 0.0
        return round(self.mismatch_count / self.total_checks, 6)

    def audit_metric(self) -> Dict[str, Any]:
        """给看板/审计用的扁平指标（键集稳定）。"""
        return {
            "declared_total": self.declared_total,
            "present_total": self.present_total,
            "commit_total": self.commit_total,
            "total_checks": self.total_checks,
            "mismatch_count": self.mismatch_count,
            "mismatch_rate": self.mismatch_rate,
            "declared_missing": self.by_kind[KIND_DECLARED_MISSING],
            "undeclared_present": self.by_kind[KIND_UNDECLARED_PRESENT],
            "not_in_head": self.by_kind[KIND_NOT_IN_HEAD],
            "report_owed_count": int(self.report_owed.get("count", 0)),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at or _utc_now(),
            "repo_root": self.repo_root,
            "git_ref": self.git_ref,
            "git_head_sha": self.git_head_sha,
            "git_available": self.git_available,
            "artifact_roots": list(self.artifact_roots),
            "scope": {
                "declared_total": self.declared_total,
                "present_total": self.present_total,
                "commit_total": self.commit_total,
                "total_checks": self.total_checks,
            },
            "mismatch_count": self.mismatch_count,
            "mismatch_rate": self.mismatch_rate,
            "metrics": self.audit_metric(),
            "by_kind": self.by_kind,
            "mismatches": {
                kind: [m.to_dict() for m in self.mismatches.get(kind, [])]
                for kind in MISMATCH_KINDS
            },
            "report_owed": self.report_owed,
            "ledger": self.ledger,
            "ignored_refs": self.ignored_refs,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# 对账器
# ---------------------------------------------------------------------------


class CapabilityReconciler:
    """能力台账 × 工件树 × git head 三方对账。只读，不改任何状态。"""

    def __init__(
        self,
        repo_root: Any = ".",
        artifact_roots: Sequence[str] = ("artifacts",),
        git_ref: str = "HEAD",
        check_git: bool = True,
        ignore_patterns: Sequence[str] = DEFAULT_IGNORE_PATTERNS,
        max_declared_dir_files: int = 5000,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.artifact_roots = tuple(artifact_roots or ())
        self.git_ref = git_ref or "HEAD"
        self.check_git = bool(check_git)
        self.ignore_patterns = tuple(ignore_patterns or ())
        self.max_declared_dir_files = max_declared_dir_files
        self._head_sha: Optional[str] = None
        self._head_files_cache: Optional[Set[str]] = None

    # ---- git ----

    def _git(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def git_available(self) -> bool:
        if not self.check_git:
            return False
        try:
            res = self._git("rev-parse", "--git-dir")
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0

    def head_sha(self) -> Optional[str]:
        if self._head_sha is not None:
            return self._head_sha
        if not self.git_available():
            return None
        res = self._git("rev-parse", self.git_ref)
        if res.returncode == 0:
            self._head_sha = res.stdout.strip()
        return self._head_sha

    def head_files(self) -> Optional[Set[str]]:
        """当前 ref 的 git 树文件集（仓库相对 POSIX 路径）；git 不可用返回 None。"""
        if self._head_files_cache is not None:
            return self._head_files_cache
        if self.head_sha() is None:
            return None
        res = self._git("ls-tree", "-r", "--name-only", self.git_ref)
        if res.returncode != 0:
            return None
        self._head_files_cache = {
            line.strip() for line in res.stdout.splitlines() if line.strip()
        }
        return self._head_files_cache

    def commit_in_head(self, sha: str) -> Optional[bool]:
        """sha 是否为当前 ref 的祖先。None = 无法判定（git 不可用）。

        非 SHA 字面量 / 仓库里不存在的 sha → False（同样『不在 head』，reason 区分）。
        """
        if not sha or not _SHA_RE.match(sha):
            return False
        if self.head_sha() is None:
            return None
        res = self._git("merge-base", "--is-ancestor", sha, self.git_ref)
        if res.returncode == 0:
            return True
        return False

    # ---- 工件树 ----

    def _ignored(self, rel_path: str) -> bool:
        parts = rel_path.split("/")
        if any(part in (".git", ".recycle", "__pycache__") for part in parts):
            return True
        name = parts[-1]
        for pat in self.ignore_patterns:
            if "/" in pat:
                if fnmatch.fnmatch(rel_path, pat):
                    return True
            elif fnmatch.fnmatch(name, pat):
                return True
        return False

    def scan_present_files(self) -> List[str]:
        """扫描 artifact_roots 下的文件（仓库相对 POSIX 路径，已排序）。"""
        found: List[str] = []
        for root in self.artifact_roots:
            norm = normalize_path(root)
            if norm is None:
                continue
            base = self.repo_root / norm
            if not base.exists():
                continue
            if base.is_file():
                candidates = [base]
            else:
                candidates = [p for p in base.rglob("*") if p.is_file()]
            for path in candidates:
                try:
                    rel = path.relative_to(self.repo_root).as_posix()
                except ValueError:
                    continue
                if self._ignored(rel):
                    continue
                found.append(rel)
        return sorted(set(found))

    def _expand_declared(self, declaration: str) -> List[str]:
        """把一条声明展开成磁盘上实际存在的仓库相对路径列表。"""
        base = self.repo_root / declaration
        if is_glob(declaration):
            try:
                matches = list(self.repo_root.glob(declaration))
            except (ValueError, OSError):
                return []
            out: List[str] = []
            for m in matches:
                if m.is_file():
                    rel = m.relative_to(self.repo_root).as_posix()
                    if not self._ignored(rel):
                        out.append(rel)
            return sorted(out)
        if not base.exists():
            return []
        if base.is_file():
            return [declaration]
        # 目录声明：展开为目录下文件（上限保护）
        out = []
        for path in sorted(base.rglob("*"))[: self.max_declared_dir_files]:
            if path.is_file():
                rel = path.relative_to(self.repo_root).as_posix()
                if not self._ignored(rel):
                    out.append(rel)
        return out

    # ---- 核心对账 ----

    def reconcile(
        self,
        records: Sequence[DeltaRecord],
        report_mission_ids: Optional[Iterable[int]] = None,
        present_files: Optional[Sequence[str]] = None,
        include_non_substantive_in_owed: bool = False,
    ) -> ReconcileReport:
        report = ReconcileReport(
            generated_at=_utc_now(),
            repo_root=str(self.repo_root),
            git_ref=self.git_ref,
            artifact_roots=list(self.artifact_roots),
        )
        report.mismatches = {kind: [] for kind in MISMATCH_KINDS}

        # ---- 声明侧：路径 → mission 归属 ----
        declared: Dict[str, List[int]] = {}
        for rec in records:
            for raw in rec.artifacts:
                path = normalize_path(raw)
                if path is None:
                    report.ignored_refs.append(f"{raw!r}@mission={rec.mission_id}")
                    continue
                holders = declared.setdefault(path, [])
                if rec.mission_id is not None and rec.mission_id not in holders:
                    holders.append(rec.mission_id)

        report.declared_total = len(declared)

        # ---- 工件树侧 ----
        present = (
            list(present_files) if present_files is not None else self.scan_present_files()
        )
        present_set = sorted({p for p in present})
        report.present_total = len(present_set)
        if present_files is None:
            roots_on_disk = [
                self.repo_root / normalize_path(r)
                for r in self.artifact_roots
                if normalize_path(r)
            ]
            if not any(p.exists() for p in roots_on_disk):
                report.warnings.append(
                    "artifact_roots_absent: 声明的工件根在磁盘上不存在"
                )

        # ---- git 侧 ----
        head_files = self.head_files() if self.check_git else None
        report.git_available = head_files is not None
        report.git_head_sha = self.head_sha()
        if self.check_git and not report.git_available:
            report.warnings.append(
                "git_unavailable: 非 git 仓库或 git 不可用，commit/file-in-head 检查被跳过"
            )

        # ---- 第 1 类：声明了但不存在 / 第 3b 类：存在但不在 head ----
        for path, mission_ids in sorted(declared.items()):
            matches = self._expand_declared(path)
            if not matches:
                report.mismatches[KIND_DECLARED_MISSING].append(
                    Mismatch(
                        kind=KIND_DECLARED_MISSING,
                        subject_type="artifact",
                        subject=path,
                        mission_ids=list(mission_ids),
                        reason="glob_no_match" if is_glob(path) else "absent_on_disk",
                    )
                )
                continue
            if head_files is not None:
                missing_in_head = [m for m in matches if m not in head_files]
                if missing_in_head:
                    report.mismatches[KIND_NOT_IN_HEAD].append(
                        Mismatch(
                            kind=KIND_NOT_IN_HEAD,
                            subject_type="file",
                            subject=missing_in_head[0] if len(missing_in_head) == 1 else path,
                            mission_ids=list(mission_ids),
                            reason="file_untracked_in_head",
                        )
                    )

        # ---- 第 2 类：存在但没声明 ----
        declaration_list = list(declared.keys())
        for candidate in present_set:
            if not any(path_matches_declaration(d, candidate) for d in declaration_list):
                report.mismatches[KIND_UNDECLARED_PRESENT].append(
                    Mismatch(
                        kind=KIND_UNDECLARED_PRESENT,
                        subject_type="file",
                        subject=candidate,
                        mission_ids=[],
                        reason="no_delta_declares_path",
                    )
                )

        # ---- 第 3a 类：commit 不在 head ----
        commit_refs: Dict[str, List[int]] = {}
        for rec in records:
            for sha in rec.commit_refs():
                holders = commit_refs.setdefault(sha, [])
                if rec.mission_id is not None and rec.mission_id not in holders:
                    holders.append(rec.mission_id)
        report.commit_total = (
            len(commit_refs) if (self.check_git and report.git_available) else 0
        )
        if self.check_git and report.git_available:
            for sha, mission_ids in sorted(commit_refs.items()):
                ok = self.commit_in_head(sha)
                if ok is True:
                    continue
                reason = "not_ancestor_of_head"
                if not _SHA_RE.match(sha):
                    reason = "commit_unparsable"
                elif ok is None:
                    reason = "git_unavailable"
                report.mismatches[KIND_NOT_IN_HEAD].append(
                    Mismatch(
                        kind=KIND_NOT_IN_HEAD,
                        subject_type="commit",
                        subject=sha,
                        mission_ids=list(mission_ids),
                        reason=reason,
                    )
                )

        report.total_checks = (
            report.declared_total + report.present_total + report.commit_total
        )

        # ---- report_owed 欠账 ----
        owed_pool = [
            rec
            for rec in records
            if include_non_substantive_in_owed
            or rec.category != NON_SUBSTANTIVE_CATEGORY
        ]
        declared_missions = sorted(
            {rec.mission_id for rec in owed_pool if rec.mission_id is not None}
        )
        if report_mission_ids is None:
            owed = list(declared_missions)
            check_available = False
            reported: List[int] = []
            report.warnings.append(
                "report_source_missing: 无报告数据源，report_owed 按『无证据即欠账』保守计"
            )
        else:
            reported = sorted(
                {mid for mid in (_as_int(m) for m in report_mission_ids) if mid is not None}
            )
            reported_set = set(reported)
            owed = [m for m in declared_missions if m not in reported_set]
            check_available = True
        report.report_owed = {
            "check_available": check_available,
            "count": len(owed),
            "mission_ids": owed,
            "declared_missions": declared_missions,
            "reported_missions": reported,
        }

        report.ledger = {
            "delta_rows": len(records),
            "declared_missions": len(declared_missions),
            "distinct_declared_paths": report.declared_total,
            "distinct_commit_refs": len(commit_refs),
        }
        return report


# ---------------------------------------------------------------------------
# 数据源装载
# ---------------------------------------------------------------------------


def _execute(conn: Any, sql: str, params: Mapping[str, Any]) -> Any:
    """兼容 sqlite3.Connection / SQLAlchemy Session / Connection。

    SQLAlchemy 2.x 的 ``execute`` 只吃 TextClause，裸 DBAPI 连接只吃 str；
    按连接类模块分派，避免把底层异常（如 no such table）掩盖成 TypeError。
    """
    module = type(conn).__module__ or ""
    if module.startswith("sqlalchemy"):
        from sqlalchemy import text

        return conn.execute(text(sql), dict(params))
    return conn.execute(sql, dict(params))


def _rows_to_dicts(result: Any) -> List[Dict[str, Any]]:
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]
    rows = result.fetchall()
    cols = [desc[0] for desc in (getattr(result, "description", None) or [])]
    return [dict(zip(cols, row)) for row in rows]


def _is_missing_table(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "no such table" in msg
        or "does not exist" in msg
        or "undefinedtable" in msg
        or "unable to open database" in msg
    )


def load_deltas_from_db(
    conn: Any,
    org_id: Optional[int] = None,
    mission_repo: Optional[str] = None,
) -> Tuple[List[DeltaRecord], List[str]]:
    """从 tensor_capability_deltas 读声明流水。表缺失时返回 ([], warning) 不抛。

    LEFT JOIN tensor_dev_missions 取 ``mission_repo``：台账按仓库隔离时用于
    只对账本仓声明（跨仓工件路径天然无法在本工作树复现）。
    """
    warnings: List[str] = []
    sql = (
        "SELECT d.id, d.mission_id, d.category, d.summary, d.artifacts, d.evidence, "
        "m.repo AS mission_repo "
        "FROM tensor_capability_deltas d "
        "LEFT JOIN tensor_dev_missions m ON m.id = d.mission_id"
    )
    params: Dict[str, Any] = {}
    clauses: List[str] = []
    if org_id is not None:
        clauses.append("d.organization_id = :org")
        params["org"] = org_id
    if mission_repo:
        clauses.append("m.repo = :repo")
        params["repo"] = mission_repo
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY d.id"
    try:
        rows = _rows_to_dicts(_execute(conn, sql, params))
    except Exception as exc:  # noqa: BLE001 - 负控：空表/缺表必须优雅降级
        if _is_missing_table(exc):
            return [], ["delta_table_unavailable: tensor_capability_deltas 不可读，按空台账处理"]
        raise
    return [DeltaRecord.from_row(r) for r in rows], warnings


def load_report_mission_ids_from_db(conn: Any) -> Optional[List[int]]:
    """读 tensor_reports.mission_id 去重集合；表缺失返回 None（=无数据源）。"""
    try:
        rows = _rows_to_dicts(
            _execute(
                conn,
                "SELECT DISTINCT mission_id FROM tensor_reports WHERE mission_id IS NOT NULL",
                {},
            )
        )
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table(exc):
            return None
        raise
    out = []
    for row in rows:
        mid = _as_int(row.get("mission_id"))
        if mid is not None:
            out.append(mid)
    return sorted(set(out))


def _load_json(path: Any) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_deltas_from_json(path: Any) -> List[DeltaRecord]:
    """支持 ``[row, ...]`` 或 ``{"items": [row, ...]}`` 两种形态。"""
    data = _load_json(path)
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("deltas json 必须是 list 或 {'items': [...]}")
    return [DeltaRecord.from_row(r if isinstance(r, dict) else {}) for r in rows]


def load_report_mission_ids_from_json(path: Any) -> List[int]:
    """支持 [1,2] / {"mission_ids":[..]} / [{"mission_id":1}, ...]。"""
    data = _load_json(path)
    if isinstance(data, dict):
        raw = data.get("mission_ids", data.get("items", []))
    else:
        raw = data
    if not isinstance(raw, list):
        raise ValueError("reports json 必须是 mission id 列表或 {'mission_ids': [...]}")
    out: List[int] = []
    for item in raw:
        mid = _as_int(item.get("mission_id") if isinstance(item, dict) else item)
        if mid is not None:
            out.append(mid)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="能力台账 × 工件树 × git head 双向对账（只读）"
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--artifact-root",
        action="append",
        default=None,
        help="工件根（可重复），缺省 artifacts",
    )
    parser.add_argument("--git-ref", default="HEAD")
    parser.add_argument("--no-git", action="store_true", help="跳过 git 相关检查")
    parser.add_argument("--deltas-json", default=None, help="离线声明流水 JSON")
    parser.add_argument("--reports-json", default=None, help="离线报告 mission id JSON")
    parser.add_argument("--db-url", default=None, help="SQLAlchemy URL（在线读 DB）")
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument(
        "--mission-repo",
        default=None,
        help="只对账该仓库的 delta 声明（跨仓工件路径无法在本工作树复现）",
    )
    parser.add_argument("--out", default="-", help="报告输出路径，'-' 为 stdout")
    parser.add_argument("--fail-on-mismatch", action="store_true")
    parser.add_argument(
        "--include-non-substantive-in-owed",
        action="store_true",
        help="report_owed 是否计入 no_substantive_delta 声明",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    extra_warnings: List[str] = []

    conn = None
    engine = None
    if args.db_url:
        from sqlalchemy import create_engine

        engine = create_engine(args.db_url)
        conn = engine.connect()

    try:
        if args.deltas_json:
            records = load_deltas_from_json(args.deltas_json)
        elif conn is not None:
            records, w = load_deltas_from_db(
                conn, org_id=args.org_id, mission_repo=args.mission_repo
            )
            extra_warnings.extend(w)
        else:
            records = []

        if args.mission_repo:
            before = len(records)
            records = [r for r in records if r.mission_repo == args.mission_repo]
            if before and not records:
                extra_warnings.append(
                    f"mission_repo_filter: 声明源无 mission_repo 字段或全被过滤 "
                    f"(before={before}, repo={args.mission_repo})"
                )

        if args.reports_json:
            report_ids: Optional[List[int]] = load_report_mission_ids_from_json(
                args.reports_json
            )
        elif conn is not None:
            report_ids = load_report_mission_ids_from_db(conn)
        else:
            report_ids = None

        reconciler = CapabilityReconciler(
            repo_root=args.repo_root,
            artifact_roots=args.artifact_root or ("artifacts",),
            git_ref=args.git_ref,
            check_git=not args.no_git,
        )
        report = reconciler.reconcile(
            records,
            report_mission_ids=report_ids,
            include_non_substantive_in_owed=args.include_non_substantive_in_owed,
        )
        report.warnings.extend(extra_warnings)
        payload = report.to_dict()
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.out in ("-", None):
            print(text)
        else:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text + "\n", encoding="utf-8")
            metric = payload["metrics"]
            print(
                "reconciled: mismatch={mismatch_count} rate={mismatch_rate} "
                "declared={declared_total} present={present_total} "
                "commits={commit_total} owed={report_owed_count}".format(**metric)
            )
    finally:
        if conn is not None:
            conn.close()
        if engine is not None:
            engine.dispose()

    if args.fail_on_mismatch and payload["mismatch_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())