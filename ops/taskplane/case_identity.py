#!/usr/bin/env python3
"""case_identity —— 算例身份键 / 版本钉扎 / 证据失效检测 / 回滚 四合一机制层（零领域语义）。

为什么需要这一层
----------------
平台今天记录「某个验证结果有效」，靠的是报告文本 + 工件路径，缺三样东西：

1. **算例唯一标识**：同一个算例在不同任务书/不同节点里用不同名字出现（"case A" /
   "A-2" / "resistance case A"），无法机器判定「这两条证据说的是同一个算例」。
2. **版本钉扎**：证据产生时用的代码 sha / 依赖版本 / 工件 sha256 没有结构化落库，
   于是「上游改了公式、下游证据还挂着有效」的静默腐化无法发现。
3. **失效与回滚**：上游依赖变更后，没有任何机制把相关证据标 stale、进 blocked、
   生成重算任务，也没有把算例钉回上一个可用版本的能力。

本模块把四件事做成**可机裁、可复算、与数据库约束同级**的机制：

  ext_id 全局唯一（DB UNIQUE 约束做权威）→ 版本钉扎（git sha + 依赖清单 + 证据 sha256）
  → 上游指纹比对（stale 自动检测 + 24h SLA 记账）→ stale 证据进 blocked + 幂等重算任务
  → 回滚到旧版本并把被回滚版本的证据置 blocked。

设计纪律
--------
* **零领域语义**：模块内不含任何领域词条、判据、算例；命名空间白名单由调用方
  ``allowed_namespaces`` / 配置注入，测试夹具用合成命名空间（``acme_ns`` 等）。
* **单一时钟注入**：所有 ``now`` 由调用方传入（缺省 UTC）；同输入两次运行逐字节相同
  → 报告数字可复算、可审计。
* **表结构单一真源**：``SCHEMA`` 是唯一真源，同时生成 SQLite DDL（测试/本地）与
  PostgreSQL DDL（``backend/migrations/110_case_identity_evidence_staleness.sql``），
  并有 drift 测试保证「签入的 .sql == 生成器输出」。
* **权威在数据库**：ext_id 唯一性不靠应用层 if 判断，靠 UNIQUE 约束；违反时把
  ``sqlite3.IntegrityError`` 翻译成 ``DuplicateExtId`` 抛给调用方。

判据（对应战役节点 IN99-N09 的 C1–C5）
---------------------------------------
C1 ``register_case`` 重复 ext_id → DB 拒绝（DuplicateExtId），全库唯一。
C2 ``pin_version`` 落库记录含 git_sha + 依赖版本清单 + 每个证据文件的 sha256。
C3 ``detect_stale`` 在上游依赖指纹变化后把相关证据标 stale，并记检测时延 + 24h SLA。
C4 ``remediate_stale`` 把 stale 证据置 blocked 并生成重算任务（幂等，重复跑不重复建）。
C5 负控：同版本号但代码 sha 变化（版本冲突）→ 指纹比对必须触发 stale；只比版本号的
   朴素实现会漏检（测试里两条路径都跑，作为对照基线）。

CLI
---
    python3 ops/taskplane/case_identity.py \
        --db /tmp/m7341/case_identity.db \
        --ddl-out backend/migrations/110_case_identity_evidence_staleness.sql \
        --json-out artifacts/m7341/case_identity_acceptance.json \
        --demo

退出码 0 = 全部判据通过；2 = 有判据未通过；3 = 输入/DB 错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
DEFAULT_SLA_HOURS = 24.0
EXT_ID_PATTERN = r"^[a-z][a-z0-9_]{1,31}:[a-z0-9][a-z0-9_.\-]{1,63}$"

# 证据状态机（stale 是中间态，必须被 remediate 推进到 blocked 或 not_supported）
EVIDENCE_STATUSES = ("valid", "stale", "blocked", "not_supported", "recomputed")
CASE_STATUSES = ("registered", "pinned", "stale", "blocked", "rolled_back")

# ─────────────────────────── 表结构单一真源 ───────────────────────────
# 每列: (name, pg_type, sqlite_type, not_null, default_literal)
# 表约束: ("pk", [cols]) / ("unique", [cols]) / ("fk", [col, ref_table, ref_col])
SCHEMA: tuple[dict[str, Any], ...] = (
    {
        "name": "case_registry",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("ext_id", "TEXT", "TEXT", True, None),
            ("namespace", "TEXT", "TEXT", True, None),
            ("case_key", "TEXT", "TEXT", True, None),
            ("title", "TEXT", "TEXT", True, "''"),
            ("owner", "TEXT", "TEXT", True, "''"),
            ("active_version", "TEXT", "TEXT", False, None),
            ("status", "TEXT", "TEXT", True, "'registered'"),
            ("status_reason", "TEXT", "TEXT", True, "''"),
            ("created_at", "TEXT", "TEXT", True, None),
            ("updated_at", "TEXT", "TEXT", True, None),
        ],
        "constraints": [("pk", ["id"]), ("unique", ["ext_id"])],
        "indexes": [("idx_case_registry_namespace", ["namespace"])],
    },
    {
        "name": "case_version_pins",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("case_id", "BIGINT", "INTEGER", True, None),
            ("version_label", "TEXT", "TEXT", True, None),
            ("git_sha", "TEXT", "TEXT", True, None),
            ("git_ref", "TEXT", "TEXT", True, "''"),
            ("dep_manifest", "TEXT", "TEXT", True, "'[]'"),
            ("dep_digest", "TEXT", "TEXT", True, None),
            ("pin_digest", "TEXT", "TEXT", True, None),
            ("pinned_by", "TEXT", "TEXT", True, "''"),
            ("created_at", "TEXT", "TEXT", True, None),
        ],
        "constraints": [
            ("pk", ["id"]),
            ("unique", ["case_id", "version_label"]),
            ("unique", ["case_id", "pin_digest"]),
            ("fk", ["case_id", "case_registry", "id"]),
        ],
        "indexes": [("idx_case_version_pins_git_sha", ["git_sha"])],
    },
    {
        "name": "case_evidence",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("case_id", "BIGINT", "INTEGER", True, None),
            ("pin_id", "BIGINT", "INTEGER", True, None),
            ("node_ref", "TEXT", "TEXT", True, None),
            ("artifact_path", "TEXT", "TEXT", True, None),
            ("sha256", "TEXT", "TEXT", True, None),
            ("size_bytes", "BIGINT", "INTEGER", True, "0"),
            ("status", "TEXT", "TEXT", True, "'valid'"),
            ("status_reason", "TEXT", "TEXT", True, "''"),
            ("stale_unit", "TEXT", "TEXT", True, "''"),
            ("detected_at", "TEXT", "TEXT", False, None),
            ("created_at", "TEXT", "TEXT", True, None),
            ("updated_at", "TEXT", "TEXT", True, None),
        ],
        "constraints": [
            ("pk", ["id"]),
            ("unique", ["case_id", "pin_id", "node_ref", "artifact_path"]),
            ("fk", ["pin_id", "case_version_pins", "id"]),
        ],
        "indexes": [("idx_case_evidence_status", ["status"])],
    },
    {
        "name": "upstream_state",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("unit", "TEXT", "TEXT", True, None),
            ("kind", "TEXT", "TEXT", True, "'code'"),
            ("version", "TEXT", "TEXT", True, None),
            ("git_sha", "TEXT", "TEXT", True, None),
            ("artifact_sha256", "TEXT", "TEXT", True, "''"),
            ("changed_at", "TEXT", "TEXT", True, None),
            ("updated_at", "TEXT", "TEXT", True, None),
        ],
        "constraints": [("pk", ["id"]), ("unique", ["unit"])],
        "indexes": [],
    },
    {
        "name": "stale_events",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("case_id", "BIGINT", "INTEGER", True, None),
            ("pin_id", "BIGINT", "INTEGER", True, None),
            ("evidence_id", "BIGINT", "INTEGER", True, None),
            ("upstream_unit", "TEXT", "TEXT", True, None),
            ("old_fingerprint", "TEXT", "TEXT", True, None),
            ("new_fingerprint", "TEXT", "TEXT", True, None),
            ("upstream_changed_at", "TEXT", "TEXT", True, None),
            ("detected_at", "TEXT", "TEXT", True, None),
            ("latency_hours", "DOUBLE PRECISION", "REAL", True, None),
            ("sla_hours", "DOUBLE PRECISION", "REAL", True, "24.0"),
            ("sla_met", "INTEGER", "INTEGER", True, None),
            ("action", "TEXT", "TEXT", True, "'pending'"),
        ],
        "constraints": [
            ("pk", ["id"]),
            ("unique", ["evidence_id", "new_fingerprint"]),
        ],
        "indexes": [("idx_stale_events_detected", ["detected_at"])],
    },
    {
        "name": "recompute_tasks",
        "columns": [
            ("id", "BIGSERIAL", "INTEGER", True, None),
            ("case_id", "BIGINT", "INTEGER", True, None),
            ("pin_id", "BIGINT", "INTEGER", True, None),
            ("evidence_id", "BIGINT", "INTEGER", True, None),
            ("task_key", "TEXT", "TEXT", True, None),
            ("reason", "TEXT", "TEXT", True, "''"),
            ("status", "TEXT", "TEXT", True, "'queued'"),
            ("created_at", "TEXT", "TEXT", True, None),
        ],
        "constraints": [("pk", ["id"]), ("unique", ["task_key"])],
        "indexes": [("idx_recompute_tasks_status", ["status"])],
    },
)


# ─────────────────────────── 异常 ───────────────────────────
class CaseIdentityError(Exception):
    """本模块所有可预期错误的基类。"""


class InvalidExtId(CaseIdentityError):
    """ext_id 不满足全局唯一前缀规则。"""


class DuplicateExtId(CaseIdentityError):
    """ext_id 已存在（由 DB UNIQUE 约束拒绝，非应用层 if 判断）。"""


class UnknownCase(CaseIdentityError):
    """ext_id 未注册。"""


class UnknownVersion(CaseIdentityError):
    """version_label 在该算例下不存在。"""


class EvidenceMissing(CaseIdentityError):
    """证据文件不存在，无法计算 sha256。"""


# ─────────────────────────── 时间/哈希工具 ───────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime | str | None) -> str:
    """统一 ISO-8601 UTC 字符串（``2026-11-08T12:00:00Z``）；同输入同输出。"""
    if moment is None:
        moment = utcnow()
    if isinstance(moment, str):
        return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unit_fingerprint(unit: str, kind: str, version: str, git_sha: str,
                     artifact_sha256: str = "") -> str:
    """上游依赖单元指纹：版本号 + 代码 sha + 工件摘要一起进哈希。

    这是 C5 负控的关键：**只比版本号**会把「同版本号、代码已改」判成未变更，
    指纹比对则必然触发 stale。
    """
    return hashlib.sha256(
        _canonical_json(
            {"unit": unit, "kind": kind, "version": version,
             "git_sha": git_sha, "artifact_sha256": artifact_sha256}
        ).encode("utf-8")
    ).hexdigest()


def dep_digest(manifest: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for dep in manifest:
        rows.append(
            {
                "unit": dep["unit"],
                "kind": dep.get("kind", "code"),
                "version": dep["version"],
                "git_sha": dep["git_sha"],
                "artifact_sha256": dep.get("artifact_sha256", ""),
                "fingerprint": dep.get("fingerprint")
                or unit_fingerprint(dep["unit"], dep.get("kind", "code"), dep["version"],
                                    dep["git_sha"], dep.get("artifact_sha256", "")),
            }
        )
    rows.sort(key=lambda r: (r["unit"], r["version"]))
    return hashlib.sha256(_canonical_json(rows).encode("utf-8")).hexdigest()


# ─────────────────────────── DDL 生成（单一真源） ───────────────────────────
def generate_ddl(dialect: str = "postgres") -> str:
    """由 SCHEMA 生成 DDL；``dialect`` ∈ {postgres, sqlite}。"""
    if dialect not in ("postgres", "sqlite"):
        raise ValueError(f"unknown dialect: {dialect}")
    chunks: list[str] = []
    for table in SCHEMA:
        lines: list[str] = []
        for name, pg_type, sqlite_type, not_null, default in table["columns"]:
            if dialect == "sqlite" and name == "id":
                lines.append(f"    {name} INTEGER PRIMARY KEY AUTOINCREMENT")
                continue
            col_type = pg_type if dialect == "postgres" else sqlite_type
            line = f"    {name} {col_type}"
            if not_null:
                line += " NOT NULL"
            if default is not None:
                line += f" DEFAULT {default}"
            lines.append(line)
        for kind, cols in table["constraints"]:
            if kind == "pk":
                if dialect == "sqlite":  # id 已内联 AUTOINCREMENT 主键
                    continue
                lines.append(f"    PRIMARY KEY ({', '.join(cols)})")
            elif kind == "unique":
                lines.append(f"    UNIQUE ({', '.join(cols)})")
            elif kind == "fk":
                col, ref_table, ref_col = cols
                lines.append(
                    f"    FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col}) ON DELETE CASCADE"
                )
        chunks.append(f"CREATE TABLE IF NOT EXISTS {table['name']} (\n"
                      + ",\n".join(lines) + "\n);")
        for idx_name, idx_cols in table["indexes"]:
            chunks.append(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table['name']} "
                f"({', '.join(idx_cols)});"
            )
    header = (
        "-- 110_case_identity_evidence_staleness.sql\n"
        "-- 算例身份键 / 版本钉扎 / 证据失效检测 / 回滚 —— 机制层表结构\n"
        "-- 本文件由 ops/taskplane/case_identity.py:generate_ddl('postgres') 生成，\n"
        "-- 请勿手改；drift 由 tests/test_case_identity.py 的 DDL 一致性用例守护。\n"
        f"-- schema_version={SCHEMA_VERSION} dialect={dialect}\n"
    )
    return header + "\n".join(chunks) + "\n"


# ─────────────────────────── 连接/建表 ───────────────────────────
def connect(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(generate_ddl("sqlite"))
    conn.commit()


# ─────────────────────────── ext_id 规则（C1 前置校验） ───────────────────────────
def validate_ext_id(ext_id: str, allowed_namespaces: Iterable[str] | None = None) -> str:
    r"""校验并规范化 ext_id：``<namespace>:<case_key>``，小写，唯一前缀规则。

    - 命名空间段必须匹配 ``[a-z][a-z0-9_]{1,31}``；给 ``allowed_namespaces`` 时还须在册。
    - 算例键段 ``[a-z0-9][a-z0-9_.\-]{1,63}``（不许空格、不许大写、不许斜杠）。
    - 规范化：去空白 + 转小写；返回规范化字符串（库里只存这一个形态）。
    """
    if not isinstance(ext_id, str):
        raise InvalidExtId(f"ext_id must be str, got {type(ext_id).__name__}")
    normalized = ext_id.strip().lower()
    if not normalized:
        raise InvalidExtId("ext_id must be non-empty")
    if not re.match(EXT_ID_PATTERN, normalized):
        raise InvalidExtId(
            f"ext_id {ext_id!r} violates pattern {EXT_ID_PATTERN} "
            "(expected '<namespace>:<case_key>', lowercase, no spaces)"
        )
    namespace = normalized.split(":", 1)[0]
    if allowed_namespaces is not None:
        allowed = {ns.lower() for ns in allowed_namespaces}
        if namespace not in allowed:
            raise InvalidExtId(
                f"namespace {namespace!r} not in allowed_namespaces {sorted(allowed)}"
            )
    return normalized


# ─────────────────────────── 算例注册（C1） ───────────────────────────
def register_case(
    conn: sqlite3.Connection,
    ext_id: str,
    *,
    title: str = "",
    owner: str = "",
    allowed_namespaces: Iterable[str] | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """注册算例；ext_id 冲突由 DB UNIQUE 约束拒绝 → 抛 DuplicateExtId。"""
    ext = validate_ext_id(ext_id, allowed_namespaces)
    namespace, case_key = ext.split(":", 1)
    stamp = iso(now)
    try:
        conn.execute(
            "INSERT INTO case_registry (ext_id, namespace, case_key, title, owner, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?,'registered',?,?)",
            (ext, namespace, case_key, title, owner, stamp, stamp),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:  # UNIQUE(ext_id) 触发
        conn.rollback()
        raise DuplicateExtId(f"ext_id {ext!r} already registered (UNIQUE constraint)") from exc
    return get_case(conn, ext)


def get_case(conn: sqlite3.Connection, ext_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM case_registry WHERE ext_id = ?", (validate_ext_id(ext_id),)
    ).fetchone()
    if row is None:
        raise UnknownCase(f"ext_id {ext_id!r} not registered")
    return dict(row)


def _set_case_status(
    conn: sqlite3.Connection, case_id: int, status: str, reason: str, now: datetime | str | None
) -> None:
    conn.execute(
        "UPDATE case_registry SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
        (status, reason, iso(now), case_id),
    )


# ─────────────────────────── 上游状态台账 ───────────────────────────
def record_upstream_state(
    conn: sqlite3.Connection,
    unit: str,
    *,
    version: str,
    git_sha: str,
    artifact_sha256: str = "",
    kind: str = "code",
    changed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """登记/更新上游依赖单元当前状态；返回是否变更 + 是否版本冲突。

    ``version_conflict=True`` 表示「版本号没变，但代码/工件指纹变了」——最隐蔽的腐化
    形态，也是 C5 负控要制造的场景。
    """
    prev = conn.execute("SELECT * FROM upstream_state WHERE unit = ?", (unit,)).fetchone()
    new_fp = unit_fingerprint(unit, kind, version, git_sha, artifact_sha256)
    if prev is not None:
        old_fp = unit_fingerprint(
            prev["unit"], prev["kind"], prev["version"], prev["git_sha"], prev["artifact_sha256"]
        )
        changed = old_fp != new_fp
        if not changed:
            return {
                "unit": unit, "changed": False, "version_conflict": False,
                "old_fingerprint": old_fp, "new_fingerprint": new_fp,
            }
        conflict = prev["version"] == version
        conn.execute(
            "UPDATE upstream_state SET kind=?, version=?, git_sha=?, artifact_sha256=?,"
            " changed_at=?, updated_at=? WHERE unit=?",
            (kind, version, git_sha, artifact_sha256, iso(changed_at), iso(changed_at), unit),
        )
        conn.commit()
        return {
            "unit": unit, "changed": True, "version_conflict": conflict,
            "old_fingerprint": old_fp, "new_fingerprint": new_fp,
        }
    conn.execute(
        "INSERT INTO upstream_state (unit, kind, version, git_sha, artifact_sha256, changed_at,"
        " updated_at) VALUES (?,?,?,?,?,?,?)",
        (unit, kind, version, git_sha, artifact_sha256, iso(changed_at), iso(changed_at)),
    )
    conn.commit()
    return {
        "unit": unit, "changed": True, "version_conflict": False,
        "old_fingerprint": None, "new_fingerprint": new_fp,
    }


def current_upstream_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM upstream_state").fetchall():
        out[row["unit"]] = {
            "unit": row["unit"],
            "kind": row["kind"],
            "version": row["version"],
            "git_sha": row["git_sha"],
            "artifact_sha256": row["artifact_sha256"],
            "changed_at": row["changed_at"],
            "fingerprint": unit_fingerprint(
                row["unit"], row["kind"], row["version"], row["git_sha"], row["artifact_sha256"]
            ),
        }
    return out


def _manifest_entry(conn: sqlite3.Connection, unit: str) -> dict[str, Any]:
    state = current_upstream_fingerprints(conn).get(unit)
    if state is None:
        raise CaseIdentityError(f"upstream unit {unit!r} has no recorded state")
    return {
        "unit": state["unit"],
        "kind": state["kind"],
        "version": state["version"],
        "git_sha": state["git_sha"],
        "artifact_sha256": state["artifact_sha256"],
        "fingerprint": state["fingerprint"],
    }# ─────────────────────────── 版本钉扎（C2） ───────────────────────────
def pin_version(
    conn: sqlite3.Connection,
    ext_id: str,
    *,
    version_label: str,
    git_sha: str,
    deps: Sequence[str] | None = None,
    dep_manifest: Sequence[Mapping[str, Any]] | None = None,
    evidence: Sequence[Mapping[str, str]] = (),
    git_ref: str = "",
    pinned_by: str = "",
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """钉扎算例版本：git sha + 依赖清单 + 证据文件 sha256 快照。

    ``deps`` 给上游单元名（自动从 upstream_state 取版本/sha）；也可直接给
    ``dep_manifest``。``evidence`` 是 ``[{"artifact_path":..., "node_ref":...}]``，
    文件必须存在，sha256/size 当场算出并落库（C2）。
    """
    case = get_case(conn, ext_id)
    stamp = iso(now)
    sha_clean = (git_sha or "").strip().lower()
    if len(sha_clean) < 7 or any(ch not in "0123456789abcdef" for ch in sha_clean):
        raise CaseIdentityError(f"git_sha {git_sha!r} is not a git commit sha (>=7 hex chars)")
    if not version_label or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$", version_label):
        raise CaseIdentityError(f"version_label {version_label!r} is not a valid label")

    manifest: list[dict[str, Any]] = []
    if dep_manifest is not None:
        for dep in dep_manifest:
            entry = dict(dep)
            entry.setdefault("kind", "code")
            entry.setdefault("artifact_sha256", "")
            entry["fingerprint"] = entry.get("fingerprint") or unit_fingerprint(
                entry["unit"], entry["kind"], entry["version"], entry["git_sha"],
                entry.get("artifact_sha256", ""),
            )
            manifest.append(entry)
    for unit in deps or ():
        manifest.append(_manifest_entry(conn, unit))
    manifest.sort(key=lambda r: r["unit"])
    d_digest = dep_digest(manifest)

    evidence_rows = []
    for item in evidence:
        path = Path(item["artifact_path"])
        if not path.exists():
            raise EvidenceMissing(f"evidence artifact not found: {path}")
        evidence_rows.append(
            {
                "node_ref": item["node_ref"],
                "artifact_path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    evidence_rows.sort(key=lambda r: (r["node_ref"], r["artifact_path"]))

    pin_material = {
        "ext_id": case["ext_id"],
        "version_label": version_label,
        "git_sha": sha_clean,
        "dep_digest": d_digest,
        "evidence": [[r["node_ref"], r["artifact_path"], r["sha256"]] for r in evidence_rows],
    }
    p_digest = hashlib.sha256(_canonical_json(pin_material).encode("utf-8")).hexdigest()

    try:
        cur = conn.execute(
            "INSERT INTO case_version_pins (case_id, version_label, git_sha, git_ref, dep_manifest,"
            " dep_digest, pin_digest, pinned_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (case["id"], version_label, sha_clean, git_ref, _canonical_json(manifest), d_digest,
             p_digest, pinned_by, stamp),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise CaseIdentityError(f"pin conflict for {ext_id}@{version_label}: {exc}") from exc
    pin_id = int(cur.lastrowid)

    for row in evidence_rows:
        conn.execute(
            "INSERT INTO case_evidence (case_id, pin_id, node_ref, artifact_path, sha256,"
            " size_bytes, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'valid',?,?)",
            (case["id"], pin_id, row["node_ref"], row["artifact_path"], row["sha256"],
             row["size_bytes"], stamp, stamp),
        )
    conn.execute(
        "UPDATE case_registry SET active_version = ?, status = 'pinned', status_reason = '',"
        " updated_at = ? WHERE id = ?",
        (version_label, stamp, case["id"]),
    )
    conn.commit()
    return get_pin(conn, case["id"], version_label)


def get_pin(conn: sqlite3.Connection, case_id: int, version_label: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM case_version_pins WHERE case_id = ? AND version_label = ?",
        (case_id, version_label),
    ).fetchone()
    if row is None:
        raise UnknownVersion(f"version {version_label!r} not found for case_id={case_id}")
    out = dict(row)
    out["dep_manifest"] = json.loads(out["dep_manifest"])
    out["evidence"] = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM case_evidence WHERE pin_id = ? ORDER BY id", (out["id"],)
        ).fetchall()
    ]
    return out


def list_pins(conn: sqlite3.Connection, ext_id: str) -> list[dict[str, Any]]:
    case = get_case(conn, ext_id)
    rows = conn.execute(
        "SELECT * FROM case_version_pins WHERE case_id = ? ORDER BY id", (case["id"],)
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── 证据文件漂移自检 ───────────────────────────
def verify_evidence(conn: sqlite3.Connection, *, now: datetime | str | None = None) -> dict[str, Any]:
    """重算证据文件 sha256 与钉扎快照比对；不一致即标 stale（evidence_digest_drift）。"""
    stamp = iso(now)
    checked = 0
    drifted: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM case_evidence").fetchall():
        path = Path(row["artifact_path"])
        checked += 1
        actual = sha256_file(path) if path.exists() else None
        if actual != row["sha256"]:
            drifted.append({"evidence_id": row["id"], "artifact_path": row["artifact_path"],
                            "pinned_sha256": row["sha256"], "actual_sha256": actual})
            if row["status"] == "valid":
                conn.execute(
                    "UPDATE case_evidence SET status='stale', status_reason='evidence_digest_drift',"
                    " stale_unit='', detected_at=?, updated_at=? WHERE id=?",
                    (stamp, stamp, row["id"]),
                )
                _set_case_status(conn, row["case_id"], "stale", "evidence_digest_drift", stamp)
    conn.commit()
    return {"checked": checked, "drifted": drifted, "drift_count": len(drifted)}


# ─────────────────────────── stale 自动检测（C3） ───────────────────────────
def detect_stale(
    conn: sqlite3.Connection,
    *,
    now: datetime | str | None = None,
    sla_hours: float = DEFAULT_SLA_HOURS,
) -> dict[str, Any]:
    """比对钉扎清单与上游当前指纹，把受影响证据标 stale 并记时延/SLA。

    幂等：同一 (evidence_id, new_fingerprint) 只记一条 stale_event。
    """
    stamp_dt = parse_ts(iso(now))
    stamp = iso(now)
    upstream = current_upstream_fingerprints(conn)
    events: list[dict[str, Any]] = []
    newly_marked = 0

    for pin in conn.execute("SELECT * FROM case_version_pins").fetchall():
        manifest = json.loads(pin["dep_manifest"])
        changed_units = [
            (dep, upstream[dep["unit"]])
            for dep in manifest
            if dep["unit"] in upstream
            and upstream[dep["unit"]]["fingerprint"] != dep.get("fingerprint")
        ]
        if not changed_units:
            continue
        evidence_rows = conn.execute(
            "SELECT * FROM case_evidence WHERE pin_id = ?", (pin["id"],)
        ).fetchall()
        for dep, state in changed_units:
            for ev in evidence_rows:
                dup = conn.execute(
                    "SELECT id FROM stale_events WHERE evidence_id = ? AND new_fingerprint = ?",
                    (ev["id"], state["fingerprint"]),
                ).fetchone()
                if dup is not None:
                    continue
                latency = (stamp_dt - parse_ts(state["changed_at"])).total_seconds() / 3600.0
                event = {
                    "case_id": pin["case_id"],
                    "pin_id": pin["id"],
                    "evidence_id": ev["id"],
                    "upstream_unit": dep["unit"],
                    "old_fingerprint": dep.get("fingerprint"),
                    "new_fingerprint": state["fingerprint"],
                    "upstream_changed_at": state["changed_at"],
                    "detected_at": stamp,
                    "latency_hours": round(latency, 4),
                    "sla_hours": sla_hours,
                    "sla_met": 1 if latency <= sla_hours else 0,
                    "action": "pending",
                }
                conn.execute(
                    "INSERT INTO stale_events (case_id, pin_id, evidence_id, upstream_unit,"
                    " old_fingerprint, new_fingerprint, upstream_changed_at, detected_at,"
                    " latency_hours, sla_hours, sla_met, action)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')",
                    (event["case_id"], event["pin_id"], event["evidence_id"],
                     event["upstream_unit"], event["old_fingerprint"], event["new_fingerprint"],
                     event["upstream_changed_at"], event["detected_at"], event["latency_hours"],
                     event["sla_hours"], event["sla_met"]),
                )
                if ev["status"] == "valid":
                    conn.execute(
                        "UPDATE case_evidence SET status='stale', status_reason='upstream_changed',"
                        " stale_unit=?, detected_at=?, updated_at=? WHERE id=?",
                        (dep["unit"], stamp, stamp, ev["id"]),
                    )
                    newly_marked += 1
                    _set_case_status(conn, pin["case_id"], "stale",
                                     f"upstream_changed:{dep['unit']}", stamp)
                events.append(event)
    conn.commit()
    return {
        "detected_at": stamp,
        "newly_marked_evidence": newly_marked,
        "events": events,
        "event_count": len(events),
        "sla_breaches": sum(1 for e in events if not e["sla_met"]),
    }


def naive_version_only_detect(
    conn: sqlite3.Connection, *, now: datetime | str | None = None
) -> list[dict[str, Any]]:
    """对照基线：**只比版本号**的朴素实现（C5 负控用来证明它漏检）。"""
    upstream = current_upstream_fingerprints(conn)
    hits: list[dict[str, Any]] = []
    for pin in conn.execute("SELECT * FROM case_version_pins").fetchall():
        for dep in json.loads(pin["dep_manifest"]):
            state = upstream.get(dep["unit"])
            if state is None:
                continue
            if state["version"] != dep["version"]:
                hits.append({"pin_id": pin["id"], "unit": dep["unit"],
                             "pinned_version": dep["version"],
                             "current_version": state["version"]})
    return hits# ─────────────────────────── 失效处置/回滚（C4） ───────────────────────────
def remediate_stale(
    conn: sqlite3.Connection,
    *,
    now: datetime | str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """把 stale 证据推进到终态：默认 blocked + 幂等重算任务；``recompute=False`` 则 not_supported。

    task_key = sha256(ext_id|version|node_ref|artifact|new_fingerprint)，UNIQUE 约束保证
    重复运行（含并发重放）不重复建任务。
    """
    stamp = iso(now)
    blocked = not_supported = tasks_created = tasks_existing = 0
    for ev in conn.execute("SELECT * FROM case_evidence WHERE status = 'stale'").fetchall():
        case = conn.execute("SELECT * FROM case_registry WHERE id = ?", (ev["case_id"],)).fetchone()
        pin = conn.execute(
            "SELECT * FROM case_version_pins WHERE id = ?", (ev["pin_id"],)
        ).fetchone()
        events = conn.execute(
            "SELECT * FROM stale_events WHERE evidence_id = ? ORDER BY id DESC", (ev["id"],)
        ).fetchall()
        fingerprint = events[0]["new_fingerprint"] if events else (ev["status_reason"] or "unknown")
        if not recompute:
            conn.execute(
                "UPDATE case_evidence SET status='not_supported', status_reason=?, updated_at=?"
                " WHERE id=?",
                (f"{ev['status_reason'] or 'stale'}:no_recompute", stamp, ev["id"]),
            )
            not_supported += 1
            _set_case_status(conn, ev["case_id"], "blocked",
                             f"not_supported:{ev['node_ref']}", stamp)
            conn.execute("UPDATE stale_events SET action='not_supported' WHERE evidence_id = ?",
                         (ev["id"],))
            continue
        task_key = hashlib.sha256(
            _canonical_json(
                {"ext_id": case["ext_id"], "version": pin["version_label"],
                 "node_ref": ev["node_ref"], "artifact": ev["artifact_path"],
                 "fingerprint": fingerprint}
            ).encode("utf-8")
        ).hexdigest()
        conn.execute(
            "UPDATE case_evidence SET status='blocked', status_reason=?, updated_at=? WHERE id=?",
            (f"{ev['status_reason'] or 'stale'}:blocked", stamp, ev["id"]),
        )
        blocked += 1
        reason = f"stale:{events[0]['upstream_unit'] if events else (ev['status_reason'] or 'x')}"
        try:
            conn.execute(
                "INSERT INTO recompute_tasks (case_id, pin_id, evidence_id, task_key, reason,"
                " status, created_at) VALUES (?,?,?,?,?,'queued',?)",
                (ev["case_id"], ev["pin_id"], ev["id"], task_key, reason, stamp),
            )
            tasks_created += 1
        except sqlite3.IntegrityError:
            tasks_existing += 1  # 幂等：任务已在队列里
        conn.execute("UPDATE stale_events SET action='blocked' WHERE evidence_id = ?", (ev["id"],))
        _set_case_status(conn, ev["case_id"], "blocked",
                         f"recompute_queued:{ev['node_ref']}", stamp)
    conn.commit()
    return {
        "remediated_at": stamp,
        "blocked": blocked,
        "not_supported": not_supported,
        "tasks_created": tasks_created,
        "tasks_existing": tasks_existing,
        "tasks_queued_total": conn.execute(
            "SELECT COUNT(*) AS n FROM recompute_tasks WHERE status = 'queued'"
        ).fetchone()["n"],
    }


def rollback(
    conn: sqlite3.Connection,
    ext_id: str,
    *,
    to_version: str,
    now: datetime | str | None = None,
    reason: str = "manual_rollback",
    queue_recompute: bool = True,
) -> dict[str, Any]:
    """把算例回滚到 ``to_version``：active_version 切回旧钉扎，更新版本的证据置 blocked。

    回滚**不是删数据**：新版证据留在库里、状态 blocked 并写重算任务，可审计可再前进。
    目标版本自身的证据只有在「被回滚动作本身 block」时才恢复 valid；因上游变更被
    stale→blocked 的证据**不会**被回滚静默复活（那是粉饰，必须走重算）。
    """
    case = get_case(conn, ext_id)
    stamp = iso(now)
    target = conn.execute(
        "SELECT * FROM case_version_pins WHERE case_id = ? AND version_label = ?",
        (case["id"], to_version),
    ).fetchone()
    if target is None:
        raise UnknownVersion(f"version {to_version!r} not found for {ext_id}")
    superseded = conn.execute(
        "SELECT * FROM case_evidence WHERE case_id = ? AND pin_id != ? AND status NOT IN"
        " ('blocked','not_supported')",
        (case["id"], target["id"]),
    ).fetchall()
    blocked_ids: list[int] = []
    tasks_created = 0
    for ev in superseded:
        conn.execute(
            "UPDATE case_evidence SET status='blocked', status_reason=?, updated_at=? WHERE id=?",
            (f"superseded_by_rollback:{reason}", stamp, ev["id"]),
        )
        blocked_ids.append(ev["id"])
        if queue_recompute:
            task_key = hashlib.sha256(
                _canonical_json({"ext_id": case["ext_id"], "rollback_to": to_version,
                                 "node_ref": ev["node_ref"], "artifact": ev["artifact_path"],
                                 "fingerprint": f"rollback:{reason}"}).encode("utf-8")
            ).hexdigest()
            try:
                conn.execute(
                    "INSERT INTO recompute_tasks (case_id, pin_id, evidence_id, task_key, reason,"
                    " status, created_at) VALUES (?,?,?,?,?,'queued',?)",
                    (case["id"], ev["pin_id"], ev["id"], task_key,
                     f"superseded_by_rollback:{reason}", stamp),
                )
                tasks_created += 1
            except sqlite3.IntegrityError:
                pass
    # 仅恢复「被回滚动作 block」的证据；上游 stale 导致的 blocked 保持 blocked
    conn.execute(
        "UPDATE case_evidence SET status='valid', status_reason='', updated_at=?"
        " WHERE pin_id = ? AND status='blocked' AND status_reason LIKE 'superseded_by_rollback:%'",
        (stamp, target["id"]),
    )
    conn.execute(
        "UPDATE case_registry SET active_version = ?, status = 'rolled_back', status_reason = ?,"
        " updated_at = ? WHERE id = ?",
        (to_version, reason, stamp, case["id"]),
    )
    conn.commit()
    still_blocked = conn.execute(
        "SELECT COUNT(*) AS n FROM case_evidence WHERE pin_id = ? AND status = 'blocked'",
        (target["id"],),
    ).fetchone()["n"]
    return {
        "ext_id": case["ext_id"],
        "rolled_back_to": to_version,
        "superseded_blocked": blocked_ids,
        "tasks_created": tasks_created,
        "active_version": to_version,
        "target_pin_still_blocked_evidence": int(still_blocked),
    }


# ─────────────────────────── 审计快照 ───────────────────────────
def audit_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()["n"])

    return {
        "schema_version": SCHEMA_VERSION,
        "cases": count("SELECT COUNT(*) AS n FROM case_registry"),
        "pins": count("SELECT COUNT(*) AS n FROM case_version_pins"),
        "evidence": count("SELECT COUNT(*) AS n FROM case_evidence"),
        "evidence_status": {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM case_evidence GROUP BY status ORDER BY status"
            ).fetchall()
        },
        "upstream_units": count("SELECT COUNT(*) AS n FROM upstream_state"),
        "stale_events": count("SELECT COUNT(*) AS n FROM stale_events"),
        "sla_breaches": count("SELECT COUNT(*) AS n FROM stale_events WHERE sla_met = 0"),
        "queued_recompute_tasks": count(
            "SELECT COUNT(*) AS n FROM recompute_tasks WHERE status = 'queued'"
        ),
        "case_status": {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM case_registry GROUP BY status ORDER BY status"
            ).fetchall()
        },
    }


# ─────────────────────────── 演示 + 判据自证（C1–C5 + 回滚） ───────────────────────────
def run_acceptance_demo(
    workdir: str | Path, *, now: datetime | None = None, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """跑一遍 C1–C5 + 回滚场景，返回判据数字与证据链（时间全部注入，可复算）。

    ``conn`` 缺省用内存库；给 ``connect(path)`` 可把整个场景持久化落盘供审计。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    t0 = now or datetime(2026, 11, 8, 0, 0, 0, tzinfo=timezone.utc)
    conn = conn if conn is not None else connect(":memory:")
    init_schema(conn)
    ns = ["acme_ns", "acme_solver"]

    # ---- 上游依赖单元（合成命名，无领域语义）----
    record_upstream_state(conn, "acme_solver.core", version="1.4.0", git_sha="a" * 40,
                          kind="code", changed_at=t0 - timedelta(days=10))
    record_upstream_state(conn, "acme_ns.table_loader", version="0.9.2", git_sha="b" * 40,
                          kind="data", artifact_sha256="c" * 64,
                          changed_at=t0 - timedelta(days=9))

    # ---- 证据工件（真实文件，sha256 当场算）----
    ev_dir = workdir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}
    for label, body in (("fit", "alpha=0.512\nbeta=1.003\n"), ("grid", "n=200\nh=0.05\n")):
        p = ev_dir / f"case_alpha_{label}.txt"
        p.write_text(body, encoding="utf-8")
        artifacts[label] = p

    results: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "reference_clock": iso(t0)}

    # ---- C1: ext_id 唯一性（DB 约束拒绝重复插入 + 形态校验）----
    case = register_case(conn, "ACME_NS:Case_Alpha", title="synthetic acceptance case",
                         owner="agent/m7341", allowed_namespaces=ns, now=t0)
    c1: dict[str, Any] = {"normalized_ext_id": case["ext_id"],
                          "row_count_after_register": audit_snapshot(conn)["cases"]}
    try:
        register_case(conn, "acme_ns:case_alpha", allowed_namespaces=ns, now=t0)
        c1.update(duplicate_rejected=False, rejection=None)
    except DuplicateExtId as exc:
        c1.update(duplicate_rejected=True, rejection=type(exc).__name__, message=str(exc))
    invalid_cases = ["acme ns:case_alpha", "case_alpha", "acme_ns:Case Alpha",
                     "other_ns:case_x", ""]
    c1["invalid_rejected"] = []
    for bad in invalid_cases:
        try:
            validate_ext_id(bad, allowed_namespaces=ns)
        except InvalidExtId:
            c1["invalid_rejected"].append(bad)
    c1["invalid_all_rejected"] = len(c1["invalid_rejected"]) == len(invalid_cases)
    c1["rows_after_duplicate_attempt"] = audit_snapshot(conn)["cases"]
    c1["passed"] = bool(c1["duplicate_rejected"] and c1["invalid_all_rejected"]
                        and c1["rows_after_duplicate_attempt"] == 1)
    results["C1_ext_id_uniqueness"] = c1

    # ---- C2: 版本钉扎记录（git sha + 依赖清单 + 证据 sha256）----
    pin = pin_version(
        conn, case["ext_id"], version_label="v001", git_sha="d1e2f3a4b5c6" * 3 + "d1e2",
        deps=["acme_solver.core", "acme_ns.table_loader"],
        evidence=[{"artifact_path": str(artifacts["fit"]), "node_ref": "node-fit"},
                  {"artifact_path": str(artifacts["grid"]), "node_ref": "node-grid"}],
        git_ref="refs/heads/agent/m7341", pinned_by="agent/m7341", now=t0,
    )
    file_sha = {label: sha256_file(path) for label, path in artifacts.items()}
    ev_sha = {e["node_ref"]: e["sha256"] for e in pin["evidence"]}
    c2 = {
        "pin_id": pin["id"],
        "version_label": pin["version_label"],
        "git_sha": pin["git_sha"],
        "git_sha_len": len(pin["git_sha"]),
        "git_sha_is_hex40": len(pin["git_sha"]) == 40
        and all(ch in "0123456789abcdef" for ch in pin["git_sha"]),
        "dep_units": [d["unit"] for d in pin["dep_manifest"]],
        "dep_versions": {d["unit"]: d["version"] for d in pin["dep_manifest"]},
        "dep_digest": pin["dep_digest"],
        "pin_digest": pin["pin_digest"],
        "evidence_sha256": ev_sha,
        "sha256_matches_file": (ev_sha.get("node-fit") == file_sha["fit"]
                               and ev_sha.get("node-grid") == file_sha["grid"]),
        "evidence_rows": len(pin["evidence"]),
    }
    c2["passed"] = bool(c2["git_sha_is_hex40"] and len(c2["dep_units"]) == 2
                        and c2["sha256_matches_file"] and c2["evidence_rows"] == 2)
    results["C2_version_pin"] = c2

    # ---- C3: 上游依赖变更 → 24h 内标 stale ----
    change = record_upstream_state(conn, "acme_solver.core", version="1.4.1",
                                   git_sha="e" * 40, kind="code",
                                   changed_at=t0 + timedelta(hours=2))
    detect = detect_stale(conn, now=t0 + timedelta(hours=3))
    c3 = {
        "upstream_change": {"unit": change["unit"], "changed": change["changed"],
                            "from_version": "1.4.0", "to_version": "1.4.1",
                            "changed_at": iso(t0 + timedelta(hours=2))},
        "detected_at": detect["detected_at"],
        "newly_marked_evidence": detect["newly_marked_evidence"],
        "event_count": detect["event_count"],
        "max_latency_hours": max((e["latency_hours"] for e in detect["events"]), default=0.0),
        "sla_hours": DEFAULT_SLA_HOURS,
        "sla_breaches": detect["sla_breaches"],
        "evidence_status_after": audit_snapshot(conn)["evidence_status"],
        "detect_stale_rerun_no_dup": detect_stale(conn, now=t0 + timedelta(hours=3))["event_count"],
    }
    c3["passed"] = bool(c3["newly_marked_evidence"] == 2 and c3["max_latency_hours"] <= 24.0
                        and c3["sla_breaches"] == 0 and c3["detect_stale_rerun_no_dup"] == 0)
    results["C3_stale_detection_24h"] = c3

    # ---- C3b: 超 SLA 场景（隔离库）——如实记 breach，不隐藏 ----
    probe = connect(":memory:")
    init_schema(probe)
    record_upstream_state(probe, "acme_solver.core", version="1.0.0", git_sha="1" * 40,
                          changed_at=t0 - timedelta(days=2))
    register_case(probe, "acme_ns:probe_case", now=t0)
    probe_p = Path(workdir / "evidence" / "probe.txt")
    probe_p.write_text("u=1.2\n", encoding="utf-8")
    pin_version(probe, "acme_ns:probe_case", version_label="v001", git_sha="f" * 40,
                deps=["acme_solver.core"],
                evidence=[{"artifact_path": str(probe_p), "node_ref": "node-probe"}], now=t0)
    record_upstream_state(probe, "acme_solver.core", version="1.0.1", git_sha="2" * 40,
                          changed_at=t0 + timedelta(hours=1))
    late = detect_stale(probe, now=t0 + timedelta(hours=30))
    results["C3b_sla_breach_accounting"] = {
        "late_detection_latency_hours": late["events"][0]["latency_hours"] if late["events"] else None,
        "sla_hours": DEFAULT_SLA_HOURS,
        "sla_met": bool(late["events"][0]["sla_met"]) if late["events"] else None,
        "sla_breaches": late["sla_breaches"],
        "note": "超 24h 才检测到的场景被如实记为 breach，不做粉饰",
    }

    # ---- C5: 负控——同版本号、代码 sha 变化（版本冲突），隔离库里跑干净对照 ----
    conflict_conn = connect(":memory:")
    init_schema(conflict_conn)
    record_upstream_state(conflict_conn, "acme_solver.core", version="1.0.0", git_sha="6" * 40,
                          changed_at=t0 - timedelta(days=3))
    register_case(conflict_conn, "acme_solver:conflict_case", now=t0)
    conflict_ev = workdir / "evidence" / "conflict.txt"
    conflict_ev.write_text("s=0.42\n", encoding="utf-8")
    cpin = pin_version(conflict_conn, "acme_solver:conflict_case", version_label="v001",
                       git_sha="a1b2c3d4" * 5, deps=["acme_solver.core"],
                       evidence=[{"artifact_path": str(conflict_ev), "node_ref": "node-c"}],
                       now=t0)
    conflict = record_upstream_state(conflict_conn, "acme_solver.core", version="1.0.0",
                                     git_sha="8" * 40, changed_at=t0 + timedelta(hours=1))
    fp_detect = detect_stale(conflict_conn, now=t0 + timedelta(hours=1, minutes=30))
    naive = naive_version_only_detect(conflict_conn)
    c5 = {
        "manufactured_conflict": {
            "unit": conflict["unit"],
            "version_unchanged": True,
            "version_conflict_flag": conflict["version_conflict"],
            "pinned_git_sha": cpin["git_sha"],
            "current_git_sha": "8" * 40,
            "old_fingerprint": conflict["old_fingerprint"],
            "new_fingerprint": conflict["new_fingerprint"],
        },
        "fingerprint_detector_events": fp_detect["event_count"],
        "fingerprint_detector_tripped": fp_detect["event_count"] > 0,
        "fingerprint_detector_sla_met": all(e["sla_met"] for e in fp_detect["events"]),
        "naive_version_only_detector_hits": len(naive),
        "naive_detector_missed": len(naive) == 0,
        "positive_control_real_version_bump": {
            "note": "朴素检测器并非全瞎：真版本号变化它抓得到（主场景 C3 的 1.4.0→1.4.1）",
            "naive_hits_main_scenario": len(
                [h for h in naive_version_only_detect(conn) if h["unit"] == "acme_solver.core"]
            ),
            "naive_hits_on_conflict_unit": len(
                [h for h in naive_version_only_detect(conflict_conn)
                 if h["unit"] == "acme_solver.core"]
            ),
        },
    }
    c5["passed"] = bool(c5["manufactured_conflict"]["version_conflict_flag"]
                        and c5["fingerprint_detector_tripped"] and c5["naive_detector_missed"]
                        and c5["positive_control_real_version_bump"]["naive_hits_main_scenario"]
                        >= 1)
    results["C5_negative_control_version_conflict"] = c5

    # ---- 主场景里另一次真实版本推进（数据单元 0.9.2 → 0.9.3）----
    record_upstream_state(conn, "acme_ns.table_loader", version="0.9.3", git_sha="9" * 40,
                          kind="data", artifact_sha256="8" * 64,
                          changed_at=t0 + timedelta(hours=5))
    detect_stale(conn, now=t0 + timedelta(hours=5, minutes=30))

    # ---- C4: stale → blocked + 幂等重算任务 ----
    rem1 = remediate_stale(conn, now=t0 + timedelta(hours=6))
    rem2 = remediate_stale(conn, now=t0 + timedelta(hours=6, minutes=5))
    # not_supported 分支：隔离库，recompute=False
    ns_probe = connect(":memory:")
    init_schema(ns_probe)
    record_upstream_state(ns_probe, "acme_solver.core", version="2.0.0", git_sha="3" * 40,
                          changed_at=t0 - timedelta(days=1))
    register_case(ns_probe, "acme_solver:no_recompute_case", now=t0)
    nsp = Path(workdir / "evidence" / "no_recompute.txt")
    nsp.write_text("k=3\n", encoding="utf-8")
    pin_version(ns_probe, "acme_solver:no_recompute_case", version_label="v001", git_sha="4" * 40,
                deps=["acme_solver.core"],
                evidence=[{"artifact_path": str(nsp), "node_ref": "node-ns"}], now=t0)
    record_upstream_state(ns_probe, "acme_solver.core", version="2.1.0", git_sha="5" * 40,
                          changed_at=t0 + timedelta(hours=1))
    detect_stale(ns_probe, now=t0 + timedelta(hours=2))
    ns_rem = remediate_stale(ns_probe, now=t0 + timedelta(hours=3), recompute=False)
    c4 = {
        "first_run": {k: rem1[k] for k in ("blocked", "tasks_created", "tasks_existing",
                                           "tasks_queued_total")},
        "second_run": {k: rem2[k] for k in ("blocked", "tasks_created", "tasks_existing",
                                            "tasks_queued_total")},
        "case_status_after": audit_snapshot(conn)["case_status"],
        "evidence_status_after": audit_snapshot(conn)["evidence_status"],
        "not_supported_branch": {
            "not_supported": ns_rem["not_supported"],
            "evidence_status": audit_snapshot(ns_probe)["evidence_status"],
        },
    }
    c4["idempotent"] = bool(rem1["tasks_created"] == 2 and rem2["tasks_created"] == 0)
    c4["passed"] = bool(rem1["blocked"] >= 1 and rem1["tasks_created"] >= 1
                        and c4["idempotent"] and ns_rem["not_supported"] == 1)
    results["C4_blocked_and_recompute"] = c4

    # ---- 回滚机制：v002 前进后回滚到 v001 ----
    pin2 = pin_version(
        conn, case["ext_id"], version_label="v002", git_sha="7" * 40,
        deps=["acme_solver.core", "acme_ns.table_loader"],
        evidence=[{"artifact_path": str(artifacts["fit"]), "node_ref": "node-fit"}],
        git_ref="refs/heads/agent/m7341", pinned_by="agent/m7341",
        now=t0 + timedelta(hours=7),
    )
    rb = rollback(conn, case["ext_id"], to_version="v001", now=t0 + timedelta(hours=8),
                  reason="upstream_regression")
    results["rollback"] = {
        "pins": [p["version_label"] for p in list_pins(conn, case["ext_id"])],
        "v002_pin_id": pin2["id"],
        "rolled_back_to": rb["rolled_back_to"],
        "active_version": rb["active_version"],
        "superseded_blocked": rb["superseded_blocked"],
        "tasks_created": rb["tasks_created"],
        "target_pin_still_blocked_evidence": rb["target_pin_still_blocked_evidence"],
        "final_case_status": get_case(conn, case["ext_id"])["status"],
        "passed": bool(rb["active_version"] == "v001" and len(rb["superseded_blocked"]) == 1
                       and rb["target_pin_still_blocked_evidence"] == 2),
        "note": "回滚不复活因上游变更被 block 的旧证据（保持 blocked 等重算），只复活被回滚动作本身 block 的证据",
    }

    results["timeline"] = {
        "reference_clock": iso(t0),
        "pin_v001_at": iso(t0),
        "upstream_change_at": iso(t0 + timedelta(hours=2)),
        "detected_at": iso(t0 + timedelta(hours=3)),
        "remediated_at": iso(t0 + timedelta(hours=6)),
        "pin_v002_at": iso(t0 + timedelta(hours=7)),
        "rolled_back_at": iso(t0 + timedelta(hours=8)),
    }
    results["audit_snapshot"] = audit_snapshot(conn)
    results["ddl_sha256"] = {
        "postgres": hashlib.sha256(generate_ddl("postgres").encode("utf-8")).hexdigest(),
        "sqlite": hashlib.sha256(generate_ddl("sqlite").encode("utf-8")).hexdigest(),
    }
    criteria = {
        "C1": results["C1_ext_id_uniqueness"]["passed"],
        "C2": results["C2_version_pin"]["passed"],
        "C3": results["C3_stale_detection_24h"]["passed"],
        "C4": results["C4_blocked_and_recompute"]["passed"],
        "C5": results["C5_negative_control_version_conflict"]["passed"],
        "rollback": results["rollback"]["passed"],
    }
    results["criteria"] = criteria
    results["all_passed"] = all(criteria.values())
    return results


# ─────────────────────────── CLI ───────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="case identity / evidence staleness mechanisms")
    parser.add_argument("--ddl-out", help="write PostgreSQL DDL to this path")
    parser.add_argument("--json-out", help="write acceptance/demo JSON to this path")
    parser.add_argument("--workdir", default=None, help="scratch dir for demo evidence files")
    parser.add_argument("--db", default=None, help="persist demo DB to this sqlite path")
    parser.add_argument("--demo", action="store_true", help="run C1–C5 acceptance demo")
    parser.add_argument("--now", default=None, help="reference clock ISO-8601 (default 2026-11-08T00:00:00Z)")
    args = parser.parse_args(argv)

    exit_code = 0
    try:
        if args.ddl_out:
            Path(args.ddl_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.ddl_out).write_text(generate_ddl("postgres"), encoding="utf-8")
            print(f"ddl written -> {args.ddl_out}")
        if args.demo:
            workdir = Path(args.workdir) if args.workdir else Path(
                tempfile.mkdtemp(prefix="case_identity_")
            )
            now = (
                datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if args.now
                else None
            )
            demo_conn = connect(args.db) if args.db else None
            results = run_acceptance_demo(workdir, now=now, conn=demo_conn)
            if args.db:
                print(f"demo db persisted -> {args.db}")
            payload = json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True)
            if args.json_out:
                Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
                print(f"acceptance json -> {args.json_out}")
            print(json.dumps({"criteria": results["criteria"],
                              "all_passed": results["all_passed"]}, ensure_ascii=False))
            exit_code = 0 if results["all_passed"] else 2
        if not args.demo and not args.ddl_out:
            parser.print_help()
            return 3
    except (CaseIdentityError, OSError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())