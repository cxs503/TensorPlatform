#!/usr/bin/env python3
"""证据链批量入账 CLI（Evidence-chain bulk ingest）—— 自回归燃料②：喂证据。

背景（为什么需要这个工具）
==========================
平台已有「运行登记」契约（``POST /api/v1/tensorphysics/runs``，
``contracts/tensorphysics-run-manifest-v1.schema.json``），但它**只存元数据、
不碰文件**：端点文档明确写着 "does not itself ingest files, verify remote object
existence, enforce the complete nested JSON Schema"。于是每次战役要把一批工件
（结果 CSV / 校验 JSON / 图）登记进证据链，只能人工：逐个算 sha256、逐个抄进
manifest、粘贴 POST、遇到坏文件整批回滚、中断后不知道登记到哪里了。

本工具把这段手工活变成**可重入的批量流水线**：

    RunManifest ──resolve──> 逐文件 sha256/size 复算 ──对账──> 批量登记
                                                          ├── 通过 → 登记（sink）
                                                          └── 不符 → 隔离（不阻断整批）

判据（全部机器可裁，见 ``tests/test_evidence_chain_m4960.py``）
==============================================================
I1 幂等重入：同一批工件跑第二次，**登记表/状态台账/隔离报告/入账报告逐字节不变**
              （``diff=0``），且 ``already_registered`` 计数 = 首轮 ``registered``；
              已入账项不再触发任何 sink 调用（断点续传：中断后重跑只补缺口）。
I2 逐文件哈希复算：``declared.checksum_sha256`` 与实算值不等 → 该 run 隔离，
              隔离报告给出 declared/actual/size 三元组；**其余 run 照常登记**。
I3 结构拒收：非法 JSON / 缺必填键 / 状态词表外 / sha256 非 64hex / 空目录
              一律 ``rejected`` 并给出机器可读 reason，不抛栈、不登记。
I4 冲突不吞：同 ``run_id`` 但 manifest 摘要变化 → 沿用端点 409 语义，
              记为 ``conflict`` 隔离，旧记录不被覆盖。
I5 只读冻结面：本工具不 import、不修改 ``backend/``，不改表结构、不新增端点；
              只通过既有公开 HTTP 契约登记（``--sink http``）或本地 JSONL 演练
              （``--sink local``，离线/回归用）。

诚实边界
========
* 远端 URI（``s3://`` 等）无法在此处复算哈希 → 标 ``artifact_uri_unsupported``
  隔离，**不假装已验证**（宁可 WITHHELD，不可假 REAL）。
* 本工具证明「工件与声明一致、登记幂等」，**不证明数值正确性**；数值验收仍归
  各域验证器（manifest 的 ``validation_status`` 由生产者声明，本工具照抄不背书）。
* ``--sink local`` 是对端点语义的**本地等价实现**（幂等 + 409），不是端点本身；
  端到端真登记需可达后端（本容器无后端/数据库进程，报告里如实标注 WITHHELD）。

用法
====
    # 1) 扫工件目录、算哈希、写出 RunManifest（不登记）
    python3 tools/evidence_chain/evidence_chain.py build \
        --run-dir artifacts/m4960/demo/run_a --meta run_a_meta.json --dry-run

    # 2) 批量登记（离线演练 / 真发 HTTP）
    python3 tools/evidence_chain/evidence_chain.py ingest \
        --source-dir artifacts/m4960/demo --out-dir artifacts/m4960/evidence_chain \
        --sink local --registry artifacts/m4960/evidence_chain/registry.local.jsonl

    # 3) 真正写进平台登记表
    python3 tools/evidence_chain/evidence_chain.py ingest \
        --source-dir artifacts/m4960/demo --out-dir artifacts/m4960/evidence_chain \
        --sink http --base-url http://127.0.0.1:8000/api/v1 --token-env TENSOR_API_TOKEN

退出码：0 全部通过；2 存在被隔离行（不阻断其它行）；3 致命（无 manifest/参数错）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

TOOL_NAME = "evidence_chain"
TOOL_VERSION = "1.0.0"
STATE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_ISOLATED = 2
EXIT_FATAL = 3

MANIFEST_BASENAMES = ("run_manifest.json", "manifest.json", "RunManifest.json")
REQUIRED_MANIFEST_KEYS = ("schema_version", "run_id", "status", "validation_status", "outputs")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

OUTCOME_REGISTERED = "registered"
OUTCOME_ALREADY = "already_registered"
OUTCOME_VERIFIED = "verified"          # --sink none：只校验不登记
OUTCOME_REJECTED = "rejected"          # 结构/JSON 层面拒收
OUTCOME_QUARANTINED = "quarantined"    # 工件缺失/哈希不符/不可验证
OUTCOME_CONFLICT = "conflict"          # 同 run_id 不同 manifest（409）
OUTCOME_FAILED = "failed"              # sink 传输/服务错误（可重试）

ISOLATED_OUTCOMES = (OUTCOME_REJECTED, OUTCOME_QUARANTINED, OUTCOME_CONFLICT, OUTCOME_FAILED)
# 命中即可认为「这条 run 已入账，重跑应跳过」的状态
SETTLED_OUTCOMES = (OUTCOME_REGISTERED, OUTCOME_ALREADY, OUTCOME_VERIFIED)

MEDIA_TYPES = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".ndjson": "application/x-ndjson",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".npy": "application/x-npy",
    ".npz": "application/zip",
    ".h5": "application/x-hdf5",
    ".hdf5": "application/x-hdf5",
    ".vtk": "application/octet-stream",
    ".dat": "application/octet-stream",
    ".bin": "application/octet-stream",
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOCAB_PATH = _REPO_ROOT / "backend" / "contracts" / "research_run_status_vocab.v1.json"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """端点同款规范化（sort_keys + 紧凑分隔符），保证 manifest 摘要跨机一致。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _bytes_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def media_type_for(path: Path) -> str:
    return MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def load_vocab(path: Path | str | None = None) -> dict[str, Any]:
    """状态词表单一契约源（端点也读它）；读不到则用契约内置默认值。"""
    vocab_path = Path(path) if path else DEFAULT_VOCAB_PATH
    try:
        data = json.loads(vocab_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("vocab must be an object")
    except (OSError, ValueError):
        return {
            "run_statuses": ["planned", "validating", "queued", "running", "succeeded",
                             "failed", "cancelled", "preempted"],
            "validation_statuses": ["not_run", "passed", "failed", "incomplete"],
            "max_manifest_bytes": 1_048_576,
            "source": "builtin-default",
        }
    data = dict(data)
    data["source"] = str(vocab_path)
    return data


# --------------------------------------------------------------------------- #
# manifest 结构校验（端点不做的调用侧责任）
# --------------------------------------------------------------------------- #
def validate_manifest(manifest: Any, vocab: dict[str, Any]) -> list[str]:
    """返回问题清单（空 = 通过）。只做结构/词表检查，不判数值正确性。"""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            problems.append(f"missing required key {key!r}")
    if problems:
        return problems
    if manifest.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        problems.append("run_id must be a non-empty string")
    elif len(run_id) > 200:
        problems.append("run_id exceeds 200 characters")
    run_statuses = set(vocab.get("run_statuses", ()))
    validation_statuses = set(vocab.get("validation_statuses", ()))
    if manifest.get("status") not in run_statuses:
        problems.append(f"status {manifest.get('status')!r} not in vocabulary {sorted(run_statuses)}")
    if manifest.get("validation_status") not in validation_statuses:
        problems.append(
            f"validation_status {manifest.get('validation_status')!r} not in vocabulary "
            f"{sorted(validation_statuses)}"
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        problems.append("outputs must be a list")
    else:
        for idx, ref in enumerate(outputs):
            base = f"outputs[{idx}]"
            if not isinstance(ref, dict):
                problems.append(f"{base} must be an object")
                continue
            for key in ("uri", "media_type", "checksum_sha256", "size_bytes"):
                if key not in ref:
                    problems.append(f"{base}.{key} missing")
            if "uri" in ref:
                uri = ref.get("uri")
                if not isinstance(uri, str) or not uri:
                    problems.append(f"{base}.uri must be a non-empty string")
            if "media_type" in ref:
                media = ref.get("media_type")
                if not isinstance(media, str) or not media:
                    problems.append(f"{base}.media_type must be a non-empty string")
            if "checksum_sha256" in ref:
                checksum = ref.get("checksum_sha256")
                if not isinstance(checksum, str) or not _SHA256_RE.match(checksum):
                    problems.append(f"{base}.checksum_sha256 must match ^[a-f0-9]{{64}}$")
            if "size_bytes" in ref:
                size = ref.get("size_bytes")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    problems.append(f"{base}.size_bytes must be a non-negative integer")
    try:
        size_bytes = len(canonical_json(manifest).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        problems.append(f"manifest is not finite JSON: {exc}")
        return problems
    max_bytes = int(vocab.get("max_manifest_bytes", 1_048_576))
    if size_bytes > max_bytes:
        problems.append(f"manifest is {size_bytes} bytes > max_manifest_bytes {max_bytes}")
    return problems


# --------------------------------------------------------------------------- #
# 工件定位与逐文件复算
# --------------------------------------------------------------------------- #
class ArtifactPathError(ValueError):
    """uri 无法映射到本地可校验文件（含越界相对路径）。"""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def resolve_artifact_path(uri: str, run_dir: Path) -> Path:
    """把 manifest 的 uri 映射为本地路径；不支持的模式抛 ArtifactPathError。"""
    if not isinstance(uri, str) or not uri:
        raise ArtifactPathError("artifact_uri_invalid", "uri is empty or not a string")
    if uri.lower().startswith("file://"):
        rest = uri[len("file://"):]
        host, _, path_part = rest.partition("/")
        if host not in ("", "localhost"):
            raise ArtifactPathError("artifact_uri_unsupported", f"file:// host {host!r} is not local")
        return Path("/" + path_part) if path_part else Path("/")
    if "://" in uri:
        scheme = uri.split("://", 1)[0]
        raise ArtifactPathError(
            "artifact_uri_unsupported",
            f"scheme {scheme!r} cannot be hash-verified locally (needs object-store fetch)",
        )
    if PurePosixPath(uri).is_absolute():
        return Path(uri)
    root = Path(run_dir).resolve()
    candidate = (Path(run_dir) / uri).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ArtifactPathError(
            "artifact_uri_escapes_run_dir",
            f"relative uri {uri!r} resolves outside run dir {root}",
        ) from None
    return candidate


def verify_artifacts(manifest: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """逐文件复算 sha256/size。返回 (对账行, 问题行)；问题行非空即该 run 隔离。"""
    artifacts: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for idx, ref in enumerate(manifest.get("outputs") or []):
        uri = ref.get("uri")
        row: dict[str, Any] = {
            "index": idx,
            "uri": uri,
            "declared_sha256": ref.get("checksum_sha256"),
            "declared_size_bytes": ref.get("size_bytes"),
            "role": ref.get("role"),
        }
        try:
            path = resolve_artifact_path(uri, run_dir)
        except ArtifactPathError as exc:
            row.update({"verifiable": False, "verified": False, "reason": exc.reason, "detail": exc.detail})
            artifacts.append(row)
            problems.append({"reason": exc.reason, "detail": exc.detail, "artifact": row})
            continue
        row["path"] = str(path)
        if not path.is_file():
            row.update({"verifiable": True, "verified": False, "reason": "artifact_missing",
                        "detail": f"no such file: {path}"})
            artifacts.append(row)
            problems.append({"reason": "artifact_missing", "detail": f"no such file: {path}", "artifact": row})
            continue
        actual_sha = sha256_file(path)
        actual_size = path.stat().st_size
        row.update({"verifiable": True, "actual_sha256": actual_sha, "actual_size_bytes": actual_size,
                    "verified": True})
        if actual_sha != row["declared_sha256"]:
            row["verified"] = False
            row["reason"] = "artifact_hash_mismatch"
            row["detail"] = f"declared {row['declared_sha256']} != actual {actual_sha} ({path.name})"
            problems.append({"reason": "artifact_hash_mismatch", "detail": row["detail"], "artifact": row})
        elif actual_size != row["declared_size_bytes"]:
            row["verified"] = False
            row["reason"] = "artifact_size_mismatch"
            row["detail"] = f"declared {row['declared_size_bytes']} != actual {actual_size}"
            problems.append({"reason": "artifact_size_mismatch", "detail": row["detail"], "artifact": row})
        artifacts.append(row)
    return artifacts, problems


# --------------------------------------------------------------------------- #
# 工件目录 → RunManifest（author 侧）
# --------------------------------------------------------------------------- #
def _iter_artifact_files(run_dir: Path, exclude: Iterable[Path]) -> list[Path]:
    excluded = {Path(p).resolve() for p in exclude}
    found: list[Path] = []
    for path in sorted(Path(run_dir).rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        found.append(path)
    return found


def build_manifest(
    run_dir: Path,
    meta: dict[str, Any],
    *,
    output_name: str = "run_manifest.json",
    vocab: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """扫工件目录 → 逐文件 sha256 → 生成符合 v1 契约的 RunManifest。

    ``meta`` 必须显式给出 status/validation_status（不猜：不替生产者声明"已验证"）。
    """
    vocab = vocab or load_vocab()
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run dir does not exist: {run_dir}")
    if not isinstance(meta, dict):
        raise ValueError("meta must be a JSON object")
    for key in ("status", "validation_status"):
        if key not in meta:
            raise ValueError(f"meta must declare {key!r} explicitly (no implicit validation claims)")
    output_path = run_dir / output_name
    run_id = str(meta.get("run_id") or run_dir.name)
    roles = meta.get("roles") or {}
    files = _iter_artifact_files(run_dir, [output_path])
    if not files:
        raise ValueError(f"no artifact files in {run_dir} (empty run dir cannot be registered)")
    outputs: list[dict[str, Any]] = []
    for path in files:
        relative = path.relative_to(run_dir).as_posix()
        outputs.append({
            "uri": relative,
            "media_type": media_type_for(path),
            "checksum_sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "role": roles.get(relative, "artifact"),
        })
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": meta["status"],
        "validation_status": meta["validation_status"],
    }
    for key in ("goal", "producer", "inputs", "environment", "resources", "metrics",
                "validation", "limitations", "parent_run_ids", "created_at", "completed_at"):
        if key in meta:
            manifest[key] = meta[key]
    manifest.setdefault("producer", {"name": "TensorPlatform/evidence_chain", "version": TOOL_VERSION})
    manifest["outputs"] = outputs
    problems = validate_manifest(manifest, vocab)
    if problems:
        raise ValueError("built manifest is not contract-valid: " + "; ".join(problems))
    return manifest


# --------------------------------------------------------------------------- #
# Sink：登记落点（local = 端点语义的本地等价实现 / http = 真端点 / none = 只校验）
# --------------------------------------------------------------------------- #
class RegistryConflict(RuntimeError):
    """同 run_id 不同 manifest（端点语义 = HTTP 409）。"""


class RegistryError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class LocalRegistrySink:
    """JSONL 登记表；语义与端点一致：同 (run_id, manifest 摘要) 幂等、异摘要冲突。"""

    kind = "local"

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return records
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            run_id = record.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise RegistryError(f"{self.path}:{line_no} record without run_id")
            records[run_id] = record
        return records

    def register(self, manifest: dict[str, Any], digest: str, *, dry_run: bool) -> dict[str, Any]:
        run_id = str(manifest.get("run_id"))
        records = self._load()
        if run_id in records:
            if records[run_id].get("manifest_sha256") == digest:
                return {"created": False, "sink": self.kind}
            raise RegistryConflict(
                f"run_id {run_id!r} already registered with manifest_sha256 "
                f"{records[run_id].get('manifest_sha256')} != {digest}"
            )
        if not dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json({
                    "run_id": run_id,
                    "manifest_sha256": digest,
                    "status": manifest.get("status"),
                    "validation_status": manifest.get("validation_status"),
                    "artifact_count": len(manifest.get("outputs") or []),
                    "manifest": manifest,
                }) + "\n")
        return {"created": True, "sink": self.kind}


class HttpRegistrySink:
    """真端点：POST {base_url}/tensorphysics/runs（既有冻结契约，只读使用）。"""

    kind = "http"

    def __init__(self, base_url: str, token: str | None, organization_id: int | None = None,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.organization_id = organization_id
        self.timeout = timeout

    def _url(self) -> str:
        return f"{self.base_url}/tensorphysics/runs"

    def register(self, manifest: dict[str, Any], digest: str, *, dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"created": True, "sink": self.kind, "dry_run": True, "endpoint": self._url()}
        body: dict[str, Any] = {
            "run_id": manifest["run_id"],
            "status": manifest["status"],
            "validation_status": manifest["validation_status"],
            "manifest": manifest,
        }
        if self.organization_id is not None:
            body["organization_id"] = int(self.organization_id)
        request = urllib.request.Request(
            self._url(),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # pragma: no cover - 读数失败不影响判定
                err_body = "<unreadable>"
            if exc.code == 409:
                raise RegistryConflict(
                    f"run_id {manifest['run_id']!r} already exists with a different manifest"
                ) from exc
            raise RegistryError(f"HTTP {exc.code} from registry", status=exc.code, body=err_body) from exc
        except urllib.error.URLError as exc:
            raise RegistryError(f"registry unreachable: {exc.reason}") from exc
        return {
            "created": bool(payload.get("created", True)),
            "sink": self.kind,
            "endpoint": self._url(),
            "manifest_sha256": payload.get("manifest_sha256", digest),
        }


class NullSink:
    """--sink none：只校验不登记（预演 / CI 冒烟）。"""

    kind = "none"

    def register(self, manifest: dict[str, Any], digest: str, *, dry_run: bool) -> dict[str, Any]:
        return {"created": False, "sink": self.kind, "verified_only": True}


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
@dataclass
class IngestConfig:
    source_dirs: list[Path]
    out_dir: Path
    sink_kind: str = "local"
    registry_path: Path | None = None
    base_url: str | None = None
    token: str | None = None
    organization_id: int | None = None
    dry_run: bool = False
    allow_quarantine: bool = False
    vocab_path: Path | str | None = None
    state_name: str = "state.json"
    quarantine_name: str = "quarantine.json"
    report_name: str = "ingest_report.json"
    ledger_name: str = "ledger.jsonl"


@dataclass
class IngestResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    quarantine: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = EXIT_OK


def discover_manifests(source_dirs: Iterable[Path], max_depth: int = 2) -> list[Path]:
    """确定性发现：root 及 ≤max_depth 层内的标准 manifest 文件名，按绝对路径排序。"""
    found: list[Path] = []
    for source in source_dirs:
        root = Path(source)
        if not root.exists():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            if len(current.parts) - base_depth > max_depth:
                dirnames[:] = []
                continue
            dirnames.sort()
            for name in MANIFEST_BASENAMES:
                if name in filenames:
                    found.append(current / name)
                    break
    uniq: dict[str, Path] = {}
    for path in found:
        uniq.setdefault(str(path.resolve()), path)
    return [uniq[key] for key in sorted(uniq)]


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA_VERSION, "sink_kind": None, "runs": {}}
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("runs", {}), dict):
        raise RegistryError(f"state file {path} is not a valid evidence-chain state")
    return data


def _make_sink(config: IngestConfig):
    if config.sink_kind == "local":
        if config.registry_path is None:
            raise ValueError("--sink local requires --registry")
        return LocalRegistrySink(Path(config.registry_path))
    if config.sink_kind == "http":
        if not config.base_url:
            raise ValueError("--sink http requires --base-url")
        return HttpRegistrySink(config.base_url, config.token, config.organization_id)
    if config.sink_kind == "none":
        return NullSink()
    raise ValueError(f"unknown sink kind {config.sink_kind!r}")


def ingest(config: IngestConfig) -> IngestResult:
    """批量入账主流程：发现 → 校验 → 复算 → 登记 → 落账（隔离不阻断）。"""
    vocab = load_vocab(config.vocab_path)
    out_dir = Path(config.out_dir)
    state_path = out_dir / config.state_name
    quarantine_path = out_dir / config.quarantine_name
    report_path = out_dir / config.report_name
    ledger_path = out_dir / config.ledger_name

    manifests = discover_manifests(config.source_dirs)
    if not manifests:
        # 空目录/无 manifest：拒收且不留任何账本残骸（FATAL，判据 I3）
        counts = _zero_counts()
        report = _build_report(
            config, manifest_paths=[], rows=[], counts=counts,
            evidence_state={"sink": config.sink_kind, "runs": {}, "digests": {}},
            quarantine=[],
        )
        return IngestResult(rows=[], counts=counts, report=report, quarantine=[],
                            exit_code=EXIT_FATAL)
    sink = _make_sink(config)
    # dry-run 也读状态台账（只读），这样它的预测与随后 apply 的实际结果对齐
    state = _load_state(state_path)
    resumed_from: dict[str, Any] = dict(state.get("runs") or {})
    # 台账携带：本批未见到的历史 run 必须保留，否则跨批断点续传会失效
    new_state_runs: dict[str, Any] = dict(resumed_from)

    rows: list[dict[str, Any]] = []
    for manifest_path in manifests:
        run_dir = manifest_path.parent
        row: dict[str, Any] = {
            "manifest_path": str(manifest_path),
            "run_dir": str(run_dir),
            "run_id": None,
            "manifest_sha256": None,
            "outcome": None,
            "reason": None,
            "detail": None,
            "artifacts": [],
        }
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            row.update({
                "outcome": OUTCOME_REJECTED,
                "reason": "manifest_unparsable",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            rows.append(row)
            continue
        problems = validate_manifest(manifest, vocab)
        if problems:
            run_id = manifest.get("run_id") if isinstance(manifest, dict) else None
            row.update({
                "run_id": run_id if isinstance(run_id, str) else None,
                "outcome": OUTCOME_REJECTED,
                "reason": "manifest_invalid",
                "detail": "; ".join(problems),
                "problems": problems,
            })
            rows.append(row)
            continue

        digest = manifest_digest(manifest)
        row["run_id"] = manifest["run_id"]
        row["manifest_sha256"] = digest
        artifacts, artifact_problems = verify_artifacts(manifest, run_dir)
        row["artifacts"] = artifacts
        if artifact_problems:
            row.update({
                "outcome": OUTCOME_QUARANTINED,
                "reason": artifact_problems[0]["reason"],
                "detail": artifact_problems[0]["detail"],
                "artifact_problems": artifact_problems,
            })
            rows.append(row)
            continue

        previous = resumed_from.get(manifest["run_id"])
        already_settled = (
            isinstance(previous, dict)
            and previous.get("manifest_sha256") == digest
            and previous.get("outcome") in SETTLED_OUTCOMES
            and state.get("sink_kind") in (None, sink.kind)
        )
        if already_settled:
            outcome = OUTCOME_VERIFIED if previous.get("outcome") == OUTCOME_VERIFIED else OUTCOME_ALREADY
            row.update({
                "outcome": outcome,
                "detail": f"resumed from state (previous outcome {previous.get('outcome')}); sink not called",
                "sink_receipt": previous.get("sink_receipt", {}),
                "resumed": True,
            })
            new_state_runs[manifest["run_id"]] = previous
            rows.append(row)
            continue

        try:
            receipt = sink.register(manifest, digest, dry_run=config.dry_run)
        except RegistryConflict as exc:
            row.update({"outcome": OUTCOME_CONFLICT, "reason": "registry_conflict", "detail": str(exc)})
            rows.append(row)
            continue
        except RegistryError as exc:
            row.update({
                "outcome": OUTCOME_FAILED,
                "reason": "registry_error",
                "detail": str(exc),
                "registry_status": exc.status,
                "registry_body": exc.body,
                "retriable": True,
            })
            rows.append(row)
            continue

        if sink.kind == "none":
            outcome = OUTCOME_VERIFIED
        elif receipt.get("created"):
            outcome = OUTCOME_REGISTERED
        else:
            outcome = OUTCOME_ALREADY
        row.update({"outcome": outcome, "sink_receipt": receipt})
        new_state_runs[manifest["run_id"]] = {
            "manifest_sha256": digest,
            "outcome": outcome,
            "status": manifest.get("status"),
            "validation_status": manifest.get("validation_status"),
            "artifact_count": len(artifacts),
            "artifacts": [
                {
                    "uri": art.get("uri"),
                    "sha256": art.get("actual_sha256"),
                    "size_bytes": art.get("actual_size_bytes"),
                    "role": art.get("role"),
                }
                for art in artifacts
            ],
            "sink_receipt": {k: v for k, v in receipt.items() if k != "endpoint"},
        }
        rows.append(row)

    rows.sort(key=lambda r: (str(r.get("run_id") or ""), str(r.get("manifest_path"))))
    counts = {
        "manifests_found": len(manifests),
        "registered": sum(1 for r in rows if r["outcome"] == OUTCOME_REGISTERED),
        "already_registered": sum(1 for r in rows if r["outcome"] == OUTCOME_ALREADY),
        "verified": sum(1 for r in rows if r["outcome"] == OUTCOME_VERIFIED),
        "rejected": sum(1 for r in rows if r["outcome"] == OUTCOME_REJECTED),
        "quarantined": sum(1 for r in rows if r["outcome"] == OUTCOME_QUARANTINED),
        "conflict": sum(1 for r in rows if r["outcome"] == OUTCOME_CONFLICT),
        "failed": sum(1 for r in rows if r["outcome"] == OUTCOME_FAILED),
    }
    counts["isolated"] = sum(counts[key] for key in ("rejected", "quarantined", "conflict", "failed"))
    counts["artifacts_verified"] = sum(1 for r in rows for art in r["artifacts"] if art.get("verified"))
    counts["artifacts_total"] = sum(len(r["artifacts"]) for r in rows)

    quarantine_rows = [
        {
            "run_id": r.get("run_id"),
            "manifest_path": r["manifest_path"],
            "outcome": r["outcome"],
            "reason": r.get("reason"),
            "detail": r.get("detail"),
            "artifacts": [
                {
                    "uri": art.get("uri"),
                    "declared_sha256": art.get("declared_sha256"),
                    "actual_sha256": art.get("actual_sha256"),
                    "declared_size_bytes": art.get("declared_size_bytes"),
                    "actual_size_bytes": art.get("actual_size_bytes"),
                    "verified": art.get("verified"),
                    "reason": art.get("reason"),
                }
                for art in r["artifacts"]
            ],
        }
        for r in rows
        if r["outcome"] in ISOLATED_OUTCOMES
    ]

    if config.dry_run:
        report = _build_report(
            config, manifest_paths=manifests, rows=rows, counts=counts,
            evidence_state={"sink": sink.kind, "runs": {}, "digests": {}},
            quarantine=quarantine_rows,
        )
        return IngestResult(rows=rows, counts=counts, report=report, quarantine=quarantine_rows,
                            exit_code=_exit_code(counts, config))

    new_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "sink_kind": sink.kind,
        "runs": {key: new_state_runs[key] for key in sorted(new_state_runs)},
    }
    state_text = json.dumps(new_state, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(state_path, state_text)

    quarantine_text = json.dumps(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "tool": f"{TOOL_NAME} {TOOL_VERSION}",
            "count": len(quarantine_rows),
            "rows": sorted(quarantine_rows, key=lambda r: (str(r.get("run_id") or ""), r["manifest_path"])),
        },
        sort_keys=True, indent=2, ensure_ascii=False,
    ) + "\n"
    _atomic_write(quarantine_path, quarantine_text)

    _append_ledger(ledger_path, rows)

    digests = {
        "state_sha256": _bytes_digest(state_text.encode("utf-8")),
        "quarantine_sha256": _bytes_digest(quarantine_text.encode("utf-8")),
        "registry_sha256": _registry_digest(config),
        "ledger_sha256": _bytes_digest(ledger_path.read_bytes()) if ledger_path.exists() else None,
    }
    evidence_state = {
        "sink": sink.kind,
        "runs": new_state["runs"],
        "digests": digests,
    }
    report = _build_report(config, manifest_paths=manifests, rows=rows, counts=counts,
                           evidence_state=evidence_state, quarantine=quarantine_rows)
    _atomic_write(report_path, json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    return IngestResult(rows=rows, counts=counts, report=report, quarantine=quarantine_rows,
                        exit_code=_exit_code(counts, config))


def _registry_digest(config: IngestConfig) -> str | None:
    if config.sink_kind == "local" and config.registry_path and Path(config.registry_path).exists():
        return _bytes_digest(Path(config.registry_path).read_bytes())
    return None


def _append_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    """追加式审计流水；同内容行已存在则跳过（内容级幂等，重入不膨胀）。"""
    path = Path(path)
    existing: set[str] = set()
    if path.exists():
        existing = {line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    new_lines: list[str] = []
    for row in rows:
        if row.get("resumed"):
            continue
        entry = {
            "run_id": row.get("run_id"),
            "manifest_sha256": row.get("manifest_sha256"),
            "outcome": row.get("outcome"),
            "reason": row.get("reason"),
            "artifacts_verified": sum(1 for art in row["artifacts"] if art.get("verified")),
        }
        line = canonical_json(entry)
        if line not in existing:
            existing.add(line)
            new_lines.append(line)
    if not new_lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in new_lines:
            handle.write(line + "\n")


def _build_report(config: IngestConfig, *, manifest_paths: list[Path], rows: list[dict[str, Any]],
                  counts: dict[str, int], evidence_state: dict[str, Any],
                  quarantine: list[dict[str, Any]]) -> dict[str, Any]:
    """报告分两段：``evidence_state``（证据状态，幂等重跑逐字节相同）与
    ``invocation`` 视图（本次调用做了什么，按定义每轮不同）。"""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "mode": "dry_run" if config.dry_run else "apply",
        "sink": config.sink_kind,
        "source_dirs": sorted(str(Path(p).resolve()) for p in config.source_dirs),
        "out_dir": str(Path(config.out_dir).resolve()),
        "manifests": [str(Path(p).resolve()) for p in manifest_paths],
        "counts": counts,
        "evidence_state": evidence_state,
        "rows": [
            {
                "run_id": r.get("run_id"),
                "manifest_path": str(Path(r["manifest_path"]).resolve()),
                "manifest_sha256": r.get("manifest_sha256"),
                "outcome": r["outcome"],
                "reason": r.get("reason"),
                "detail": r.get("detail"),
                "artifact_count": len(r["artifacts"]),
                "artifacts_verified": sum(1 for art in r["artifacts"] if art.get("verified")),
                "sink_receipt": {k: v for k, v in (r.get("sink_receipt") or {}).items() if k != "endpoint"},
            }
            for r in rows
        ],
        "quarantine_count": len(quarantine),
        "idempotency": {
            "settled_outcomes": list(SETTLED_OUTCOMES),
            "resume_skips_sink_calls": True,
            "report_contains_no_timestamps": True,
            "invariant_across_reruns": ["registry bytes", "state.json bytes", "quarantine.json bytes",
                                        "ledger.jsonl bytes", "evidence_state block"],
        },
    }


def _zero_counts() -> dict[str, int]:
    counts = {key: 0 for key in ("manifests_found", "registered", "already_registered", "verified",
                                 "rejected", "quarantined", "conflict", "failed", "isolated",
                                 "artifacts_verified", "artifacts_total")}
    return counts


def _exit_code(counts: dict[str, int], config: IngestConfig) -> int:
    if counts["manifests_found"] == 0:
        return EXIT_FATAL
    if counts["isolated"] and not config.allow_quarantine:
        return EXIT_ISOLATED
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_build(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    try:
        meta = _load_json(Path(args.meta)) if args.meta else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FATAL: cannot read meta {args.meta}: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if not isinstance(meta, dict):
        print("FATAL: meta must be a JSON object", file=sys.stderr)
        return EXIT_FATAL
    try:
        manifest = build_manifest(run_dir, meta, output_name=args.output_name, vocab=load_vocab(args.vocab))
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return EXIT_FATAL
    output_path = run_dir / args.output_name
    if output_path.exists() and not args.force and not args.dry_run:
        print(f"FATAL: {output_path} exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_FATAL
    payload = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        sys.stdout.write(canonical_json({"mode": "dry_run", "output": str(output_path),
                                         "manifest_sha256": manifest_digest(manifest),
                                         "artifact_count": len(manifest["outputs"]),
                                         "outputs": manifest["outputs"]}) + "\n")
        return EXIT_OK
    _atomic_write(output_path, payload)
    print(canonical_json({"mode": "apply", "output": str(output_path),
                          "manifest_sha256": manifest_digest(manifest),
                          "artifact_count": len(manifest["outputs"])}))
    return EXIT_OK


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = IngestConfig(
        source_dirs=[Path(p) for p in args.source_dir],
        out_dir=Path(args.out_dir),
        sink_kind=args.sink,
        registry_path=Path(args.registry) if args.registry else None,
        base_url=args.base_url,
        token=os.environ.get(args.token_env) if args.token_env else None,
        organization_id=args.organization_id,
        dry_run=args.dry_run,
        allow_quarantine=args.allow_quarantine,
        vocab_path=args.vocab,
    )
    try:
        result = ingest(config)
    except (ValueError, RegistryError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return EXIT_FATAL
    summary = {
        "mode": config.dry_run and "dry_run" or "apply",
        "sink": config.sink_kind,
        "counts": result.counts,
        "digests": (result.report.get("evidence_state") or {}).get("digests", {}),
        "exit_code": result.exit_code,
    }
    if config.dry_run:
        summary["quarantine"] = [
            {"run_id": q["run_id"], "reason": q["reason"], "detail": q["detail"]} for q in result.quarantine
        ]
    print(canonical_json(summary))
    if result.exit_code == EXIT_FATAL:
        print("FATAL: no RunManifest found in source dirs", file=sys.stderr)
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence_chain",
        description="证据链批量入账：RunManifest → 逐文件 sha256 复算 → 批量登记（幂等/可续传/坏文件隔离）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="扫工件目录生成 RunManifest（算哈希，不登记）")
    build_p.add_argument("--run-dir", required=True)
    build_p.add_argument("--meta", default=None, help="meta JSON（必须显式含 status/validation_status）")
    build_p.add_argument("--output-name", default="run_manifest.json")
    build_p.add_argument("--vocab", default=None)
    build_p.add_argument("--force", action="store_true")
    build_p.add_argument("--dry-run", action="store_true", help="只打印将写出的 manifest 摘要，不落盘")
    build_p.set_defaults(func=_cmd_build)

    ingest_p = sub.add_parser("ingest", help="批量校验并登记一批 RunManifest")
    ingest_p.add_argument("--source-dir", action="append", required=True,
                          help="工件根目录（可重复）；向下 ≤2 层查找 run_manifest.json/manifest.json")
    ingest_p.add_argument("--out-dir", required=True, help="账本/quarantine/报告输出目录")
    ingest_p.add_argument("--sink", choices=("local", "http", "none"), default="local")
    ingest_p.add_argument("--registry", default=None, help="--sink local 的 JSONL 登记表路径")
    ingest_p.add_argument("--base-url", default=None, help="--sink http 的 API 基址（含 /api/v1）")
    ingest_p.add_argument("--token-env", default="TENSOR_API_TOKEN", help="存放 bearer token 的环境变量名")
    ingest_p.add_argument("--organization-id", type=int, default=None)
    ingest_p.add_argument("--allow-quarantine", action="store_true", help="有隔离行也返回 0（CI 用）")
    ingest_p.add_argument("--vocab", default=None)
    ingest_p.add_argument("--dry-run", action="store_true", help="全流程演算但不落盘、不登记")
    ingest_p.set_defaults(func=_cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())