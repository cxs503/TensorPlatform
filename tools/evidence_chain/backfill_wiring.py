#!/usr/bin/env python3
"""Run-manifest 回填器接线（Backfill Wiring）—— 五元证据链成熟化。

背景
====
历史 run 目录（runs/、artifacts/ 下）大多只有工件散落、没有 manifest，
或 manifest 结构不完整。平台需要一种**机器可复算**的方式把历史 run 的
五元证据链补齐，而非依赖人工填写。

五元证据链
==========
每个 run 的证据成熟度由五个元素构成：

1. **manifest**   —— RunManifest v1 契约结构（schema_version=1, 必填键齐全）
2. **digest**     —— manifest 的 sha256(canonical_json) 摘要
3. **artifact_verify** —— 逐工件 sha256 + size 复算对账
4. **chain_level** —— 证据等级（E1 / E2 / E3）
5. **reason**     —— 未通过时的结构化失败原因（fail-closed，禁止默认授予）

等级定义
========
- **E1**：仅有 run 目录存在。manifest 缺失或结构校验不通过。
          reason 字段给出机器可读的失败原因。
- **E2**：manifest 结构合法 + digest 已计算。但工件验证未全部通过
          （缺失/哈希不符/不可达）。
- **E3**：manifest 合法 + digest 已计算 + 全部工件验证通过。
          完整五元证据链。

纪律
====
- fail-closed：任何环节不通过，**绝不**默认授予更高等级。
- 幂等：同一输入多次运行，输出逐字节一致（canonical_json + sorted keys）。
- 只读工件面：不修改 run 目录内的任何文件（manifest 写入 out_dir）。
- 不依赖后端：纯本地文件操作，可离线运行。

用法
====
    python3 tools/evidence_chain/backfill_wiring.py \\
        --run-dirs runs/m591 runs/m1614 artifacts/m1386 \\
        --out-dir artifacts/backfill_wiring

退出码：0 全部 E3；2 存在 E1/E2（不阻断其它）；3 致命。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse primitives from the existing evidence_chain module
from .evidence_chain import (
    MANIFEST_BASENAMES,
    REQUIRED_MANIFEST_KEYS,
    build_manifest,
    canonical_json,
    load_vocab,
    manifest_digest,
    resolve_artifact_path,
    sha256_file,
    validate_manifest,
    ArtifactPathError,
)

EXIT_OK = 0
EXIT_PARTIAL = 2
EXIT_FATAL = 3

# Evidence chain levels
CHAIN_E1 = "E1"  # manifest missing or structurally invalid
CHAIN_E2 = "E2"  # manifest valid + digest, but artifact verification incomplete
CHAIN_E3 = "E3"  # full five-element chain: manifest + digest + all artifacts verified

CHAIN_LEVELS = (CHAIN_E1, CHAIN_E2, CHAIN_E3)

# Structured failure reasons (fail-closed vocabulary)
REASON_NO_RUN_DIR = "run_dir_not_found"
REASON_EMPTY_RUN_DIR = "run_dir_empty"
REASON_MANIFEST_MISSING = "manifest_missing"
REASON_MANIFEST_INVALID_JSON = "manifest_invalid_json"
REASON_MANIFEST_STRUCT = "manifest_struct_invalid"
REASON_ARTIFACT_MISSING = "artifact_missing"
REASON_ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
REASON_ARTIFACT_SIZE_MISMATCH = "artifact_size_mismatch"
REASON_ARTIFACT_URI_UNSUPPORTED = "artifact_uri_unsupported"
REASON_ARTIFACT_PATH_ERROR = "artifact_path_error"


@dataclass
class ArtifactVerifyResult:
    """Single artifact verification outcome."""
    index: int
    uri: str
    role: str | None
    declared_sha256: str | None
    declared_size: int | None
    actual_sha256: str | None = None
    actual_size: int | None = None
    verified: bool = False
    reason: str | None = None
    detail: str | None = None


@dataclass
class RunBackfillResult:
    """Backfill result for a single run directory."""
    run_dir: str
    run_id: str | None = None
    chain_level: str = CHAIN_E1
    manifest_digest: str | None = None
    manifest_present: bool = False
    manifest_built: bool = False
    manifest_valid: bool = False
    manifest_problems: list[str] = field(default_factory=list)
    artifact_results: list[dict[str, Any]] = field(default_factory=list)
    artifacts_total: int = 0
    artifacts_verified: int = 0
    artifacts_failed: int = 0
    reason: str | None = None
    reason_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "run_id": self.run_id,
            "chain_level": self.chain_level,
            "manifest_digest": self.manifest_digest,
            "manifest_present": self.manifest_present,
            "manifest_built": self.manifest_built,
            "manifest_valid": self.manifest_valid,
            "manifest_problems": self.manifest_problems,
            "artifact_results": self.artifact_results,
            "artifacts_total": self.artifacts_total,
            "artifacts_verified": self.artifacts_verified,
            "artifacts_failed": self.artifacts_failed,
            "reason": self.reason,
            "reason_detail": self.reason_detail,
        }


@dataclass
class BackfillReport:
    """Aggregate backfill report."""
    schema_version: int = 1
    tool: str = "backfill_wiring"
    tool_version: str = "1.0.0"
    runs: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "runs": self.runs,
            "summary": self.summary,
        }


def _find_existing_manifest(run_dir: Path) -> Path | None:
    """Look for an existing manifest file in the run directory."""
    for basename in MANIFEST_BASENAMES:
        candidate = run_dir / basename
        if candidate.is_file():
            return candidate
    return None


def _try_build_manifest(run_dir: Path) -> tuple[dict[str, Any] | None, bool, str | None, str | None]:
    """Attempt to build a manifest from run directory contents.

    Returns (manifest_or_none, was_built, reason, reason_detail).
    """
    # Collect all files (excluding any existing manifest)
    existing = _find_existing_manifest(run_dir)
    exclude = {existing} if existing else set()

    files = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in {Path(p).resolve() for p in exclude}:
            continue
        files.append(path)

    if not files:
        return None, False, REASON_EMPTY_RUN_DIR, f"no artifact files in {run_dir}"

    # Build a minimal manifest with status='succeeded' (conservative: we don't
    # know the real status, but the run dir exists so it was at least started).
    # We use 'not_run' for validation_status since we haven't validated anything.
    meta = {
        "run_id": run_dir.name,
        "status": "succeeded",
        "validation_status": "not_run",
        "producer": {"name": "TensorPlatform/backfill_wiring", "version": "1.0.0"},
        "limitations": [
            "Auto-built manifest from historical run directory. "
            "Status and validation_status are conservative defaults, not producer claims."
        ],
    }

    try:
        manifest = build_manifest(run_dir, meta)
        return manifest, True, None, None
    except ValueError as exc:
        return None, False, REASON_EMPTY_RUN_DIR, str(exc)


def _verify_single_artifact(ref: dict[str, Any], run_dir: Path) -> ArtifactVerifyResult:
    """Verify a single artifact reference against the filesystem."""
    idx = ref.get("_index", 0)
    uri = ref.get("uri", "")
    role = ref.get("role")
    declared_sha = ref.get("checksum_sha256")
    declared_size = ref.get("size_bytes")

    result = ArtifactVerifyResult(
        index=idx,
        uri=uri,
        role=role,
        declared_sha256=declared_sha,
        declared_size=declared_size,
    )

    try:
        path = resolve_artifact_path(uri, run_dir)
    except ArtifactPathError as exc:
        result.reason = REASON_ARTIFACT_PATH_ERROR
        result.detail = f"{exc.reason}: {exc.detail}"
        return result

    if not path.is_file():
        result.reason = REASON_ARTIFACT_MISSING
        result.detail = f"no such file: {path}"
        return result

    actual_sha = sha256_file(path)
    actual_size = path.stat().st_size
    result.actual_sha256 = actual_sha
    result.actual_size = actual_size

    if declared_sha and actual_sha != declared_sha:
        result.reason = REASON_ARTIFACT_HASH_MISMATCH
        result.detail = f"declared {declared_sha} != actual {actual_sha}"
        return result

    if declared_size is not None and actual_size != declared_size:
        result.reason = REASON_ARTIFACT_SIZE_MISMATCH
        result.detail = f"declared {declared_size} != actual {actual_size}"
        return result

    result.verified = True
    return result


def backfill_single_run(run_dir: Path, *, vocab: dict[str, Any] | None = None) -> RunBackfillResult:
    """Process a single run directory through the five-element chain.

    Steps:
    1. Find or build manifest
    2. Validate manifest structure
    3. Compute digest (sha256 of canonical JSON)
    4. Verify each artifact
    5. Assign chain level (fail-closed)
    """
    run_dir = Path(run_dir).resolve()
    result = RunBackfillResult(run_dir=str(run_dir))

    # --- Step 0: Check run directory exists ---
    if not run_dir.is_dir():
        result.chain_level = CHAIN_E1
        result.reason = REASON_NO_RUN_DIR
        result.reason_detail = f"directory does not exist: {run_dir}"
        return result

    # --- Step 1: Find or build manifest ---
    vocab = vocab or load_vocab()
    manifest: dict[str, Any] | None = None
    existing_manifest_path = _find_existing_manifest(run_dir)

    if existing_manifest_path:
        result.manifest_present = True
        try:
            manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            result.chain_level = CHAIN_E1
            result.reason = REASON_MANIFEST_INVALID_JSON
            result.reason_detail = str(exc)
            return result
    else:
        # Try to auto-build manifest from directory contents
        manifest, was_built, reason, detail = _try_build_manifest(run_dir)
        result.manifest_built = was_built
        if manifest is None:
            result.chain_level = CHAIN_E1
            result.reason = reason or REASON_MANIFEST_MISSING
            result.reason_detail = detail
            return result

    # --- Step 2: Validate manifest structure ---
    result.run_id = manifest.get("run_id") if isinstance(manifest, dict) else None
    problems = validate_manifest(manifest, vocab)
    result.manifest_problems = problems

    if problems:
        result.chain_level = CHAIN_E1
        result.reason = REASON_MANIFEST_STRUCT
        result.reason_detail = "; ".join(problems)
        result.manifest_valid = False
        return result

    result.manifest_valid = True

    # --- Step 3: Compute digest ---
    digest = manifest_digest(manifest)
    result.manifest_digest = digest

    # --- Step 4: Verify artifacts ---
    outputs = manifest.get("outputs") or []
    result.artifacts_total = len(outputs)

    all_verified = True
    for idx, ref in enumerate(outputs):
        ref_with_index = dict(ref, _index=idx)
        art_result = _verify_single_artifact(ref_with_index, run_dir)
        art_dict = {
            "index": art_result.index,
            "uri": art_result.uri,
            "role": art_result.role,
            "declared_sha256": art_result.declared_sha256,
            "declared_size_bytes": art_result.declared_size,
            "actual_sha256": art_result.actual_sha256,
            "actual_size_bytes": art_result.actual_size,
            "verified": art_result.verified,
        }
        if not art_result.verified:
            art_dict["reason"] = art_result.reason
            art_dict["detail"] = art_result.detail
            all_verified = False
            result.artifacts_failed += 1
        else:
            result.artifacts_verified += 1
        result.artifact_results.append(art_dict)

    # --- Step 5: Assign chain level (fail-closed) ---
    if all_verified and result.artifacts_total > 0:
        result.chain_level = CHAIN_E3
    elif result.manifest_valid and result.manifest_digest:
        result.chain_level = CHAIN_E2
        if result.artifacts_total == 0:
            result.reason = "manifest_valid_but_no_outputs"
            result.reason_detail = "manifest has no outputs declared"
        else:
            failed_reasons = [
                a["reason"] for a in result.artifact_results if not a.get("verified")
            ]
            result.reason = "artifact_verification_incomplete"
            result.reason_detail = f"failed reasons: {failed_reasons}"
    else:
        result.chain_level = CHAIN_E1
        result.reason = result.reason or REASON_MANIFEST_STRUCT

    return result


def backfill_runs(
    run_dirs: list[Path],
    *,
    vocab: dict[str, Any] | None = None,
) -> BackfillReport:
    """Process multiple run directories and produce an aggregate report."""
    report = BackfillReport()
    level_counts = {CHAIN_E1: 0, CHAIN_E2: 0, CHAIN_E3: 0}

    for run_dir in sorted(run_dirs, key=lambda p: str(p)):
        result = backfill_single_run(run_dir, vocab=vocab)
        report.runs.append(result.to_dict())
        level_counts[result.chain_level] = level_counts.get(result.chain_level, 0) + 1

    total = len(run_dirs)
    report.summary = {
        "total_runs": total,
        "by_level": level_counts,
        "full_chain_rate": level_counts[CHAIN_E3] / total if total > 0 else 0.0,
        "partial_chain_rate": level_counts[CHAIN_E2] / total if total > 0 else 0.0,
        "missing_chain_rate": level_counts[CHAIN_E1] / total if total > 0 else 0.0,
    }

    return report


def write_report(report: BackfillReport, out_dir: Path) -> Path:
    """Write the backfill report to disk as canonical JSON."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "backfill_report.json"
    text = json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
    report_path.write_text(text + "\n", encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backfill_wiring",
        description="Run-manifest backfill wiring: five-element evidence chain maturation",
    )
    parser.add_argument(
        "--run-dirs", nargs="+", required=True,
        help="Run directories to process",
    )
    parser.add_argument(
        "--out-dir", required=True,
        help="Output directory for backfill report",
    )
    parser.add_argument(
        "--vocab", default=None,
        help="Path to status vocabulary JSON (optional)",
    )

    args = parser.parse_args(argv)
    vocab = load_vocab(args.vocab) if args.vocab else None

    run_dirs = [Path(d) for d in args.run_dirs]
    report = backfill_runs(run_dirs, vocab=vocab)
    report_path = write_report(report, Path(args.out_dir))

    # Print summary
    s = report.summary
    print(f"Backfill wiring complete: {s['total_runs']} runs processed")
    print(f"  E3 (full chain):  {s['by_level'].get(CHAIN_E3, 0)}")
    print(f"  E2 (partial):     {s['by_level'].get(CHAIN_E2, 0)}")
    print(f"  E1 (missing):     {s['by_level'].get(CHAIN_E1, 0)}")
    print(f"  Full chain rate:  {s['full_chain_rate']:.1%}")
    print(f"Report: {report_path}")

    # Exit code
    if s["total_runs"] == 0:
        return EXIT_FATAL
    if s["by_level"].get(CHAIN_E1, 0) > 0 or s["by_level"].get(CHAIN_E2, 0) > 0:
        return EXIT_PARTIAL
    return EXIT_OK

# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 —— 内容可追溯链授权（P143W2C-N03 成熟化）
# ═══════════════════════════════════════════════════════════════════════════
"""Layer 1（上文）只把 run 目录形态化成 E1/E2/E3 等级；它有两个结构性缺口：

1. **自签 manifest 可获满级**：``_try_build_manifest`` 在 manifest 缺失时现场生成一份，
   工件摘要由它自己算、自己声明——「run 自己给自己发证」，任何历史目录都能刷到 E3。
2. **只验形态不验内容**：不检查 manifest 摘要是否与独立登记记录一致、不检查
   ``data`` 引用指向的工件摘要、不检查工件路径是否逃出 run 目录。

Layer 2 把「可复算」升到「可追溯」：证据必须由 **run 之外** 的独立锚
（``registry.local.jsonl`` 登记摘要，登记先于回填发生）背书，且每个工件逐件复算
sha256/size、data 工件摘要绑定为 ``data_anchor``、代码版本绑定为 ``commit_sha``。
全通过才授予 ``chain_status='complete'``（完整五元链），否则 fail-closed 封顶 ``E1``
并写入结构化 reason。

三分类（判据 a 的输出计数）
--------------------------
``full_chain_granted``  完整链授权：manifest 合法 + 摘要与独立锚一致 + 逐工件复算通过
                        + data_anchor/commit/config 锚齐全 → chain_status=complete
``capped_missing``      缺件封顶：无 manifest / 无工件 / 缺 data 工件 / 缺 commit 锚
``verify_failed``       校验失败：摘要与锚不符、工件被篡改、跨 run 混用、自签

纪律
----
- **不自动生成 manifest**：auto-build 出来的 manifest 一律视为自签 → 不授权。
- **不动 ``outcome_evaluations``**：回填只写链表与回填表，不改 grade/status/e_level
  （「不得借回填提级」闸：回填前后 S3+/S4/S5/S-N confirmed 行数**与行级指纹**必须相等）。
- **幂等**：同输入二次运行 → 两表 0 行变动（内容相同则完全不写，时间戳也不动）。
"""

import re
from pathlib import PurePosixPath
from typing import Mapping

from ..evidence_chain_link import (
    CHAIN_COLUMNS,
    CHAIN_TABLE,
    ELEMENT_KEYS,
    VIEW_CHAIN_AUDIT,
    analyze_chain,
    cap_e_level,
    dumps_json,
    ensure_chain_schema,
    now_utc,
)

TOOL_VERSION = "backfill_wiring/2.0.0"

# 三分类（判据 a 的输出计数键）
GRANT_GRANTED = "full_chain_granted"
GRANT_CAPPED = "capped_missing"
GRANT_FAILED = "verify_failed"
GRANT_CLASSES: tuple[str, ...] = (GRANT_GRANTED, GRANT_CAPPED, GRANT_FAILED)

# 结构化 reason 词表（fail-closed，只增不改）
B_NO_RUN_DIR = "BACKFILL_NO_RUN_DIR"
B_MANIFEST_MISSING = "BACKFILL_MANIFEST_MISSING"
B_MANIFEST_JSON = "BACKFILL_MANIFEST_INVALID_JSON"
B_MANIFEST_STRUCT = "BACKFILL_MANIFEST_STRUCT_INVALID"
B_NO_ATTESTATION = "BACKFILL_SELF_SIGNED_NO_ATTESTATION"
B_DIGEST_MISMATCH = "BACKFILL_MANIFEST_DIGEST_MISMATCH"
B_CROSS_RUN = "BACKFILL_CROSS_RUN_MIXED"
B_NO_OUTPUTS = "BACKFILL_NO_OUTPUTS"
B_ARTIFACT_MISSING = "BACKFILL_ARTIFACT_MISSING"
B_ARTIFACT_NO_SHA = "BACKFILL_ARTIFACT_NO_SHA"
B_ARTIFACT_HASH = "BACKFILL_ARTIFACT_HASH_MISMATCH"
B_ARTIFACT_SIZE = "BACKFILL_ARTIFACT_SIZE_MISMATCH"
B_ARTIFACT_ESCAPE = "BACKFILL_ARTIFACT_ESCAPES_RUN_DIR"
B_ARTIFACT_PATH = "BACKFILL_ARTIFACT_PATH_ERROR"
B_DATA_MISSING = "BACKFILL_DATA_ANCHOR_MISSING"
B_ANCHOR_MISMATCH = "BACKFILL_DATA_ANCHOR_MISMATCH"
B_COMMIT_MISSING = "BACKFILL_COMMIT_SHA_MISSING"
B_CONFIG_MISSING = "BACKFILL_CONFIG_REF_MISSING"
B_VERIFIED = "BACKFILL_CONTENT_VERIFIED"

# 「校验失败」类 code（→ verify_failed）；其余（缺件/形态不全）归 capped_missing
_FAILED_CODES = frozenset({
    B_MANIFEST_JSON, B_MANIFEST_STRUCT, B_DIGEST_MISMATCH, B_CROSS_RUN,
    B_ARTIFACT_NO_SHA, B_ARTIFACT_HASH, B_ARTIFACT_SIZE, B_ARTIFACT_ESCAPE,
    B_ARTIFACT_PATH, B_ANCHOR_MISMATCH, B_NO_ATTESTATION,
})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
_SHA_PREFIXES = ("sha256:",)
_PATH_PREFIXES = ("artifact:", "file:", "file://localhost/")


def _norm_digest(value: str) -> str:
    text = value.strip().lower()
    for prefix in _SHA_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _norm_uri(value: str) -> str:
    text = value.strip()
    for prefix in _PATH_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _clean_ref(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dedup(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def classify_codes(codes: list[str]) -> str:
    """结构化 code 集合 → 三分类（fail-closed：校验失败优先于缺件）。"""
    if any(code in _FAILED_CODES for code in codes):
        return GRANT_FAILED
    if B_VERIFIED in codes:
        return GRANT_GRANTED
    return GRANT_CAPPED


@dataclass
class ContentVerdict:
    """内容可追溯体检结论（run 级，与具体评价无关）。"""

    run_dir: str = ""
    expected_run_id: str | None = None
    actual_run_id: str | None = None
    manifest_present: bool = False
    manifest_valid: bool = False
    manifest_digest: str | None = None
    attested_digest: str | None = None
    digest_matches_attestation: bool = False
    commit_sha: str | None = None
    config_sha256: str | None = None
    data_anchor: str | None = None
    data_uri: str | None = None
    outputs_total: int = 0
    outputs_verified: int = 0
    outputs_failed: int = 0
    codes: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def content_verified(self) -> bool:
        return not self.codes

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "expected_run_id": self.expected_run_id,
            "actual_run_id": self.actual_run_id,
            "manifest_present": self.manifest_present,
            "manifest_valid": self.manifest_valid,
            "manifest_digest": self.manifest_digest,
            "attested_digest": self.attested_digest,
            "digest_matches_attestation": self.digest_matches_attestation,
            "commit_sha": self.commit_sha,
            "config_sha256": self.config_sha256,
            "data_anchor": self.data_anchor,
            "data_uri": self.data_uri,
            "outputs_total": self.outputs_total,
            "outputs_verified": self.outputs_verified,
            "outputs_failed": self.outputs_failed,
            "content_verified": self.content_verified,
            "codes": list(self.codes),
            "detail": self.detail,
        }


def load_attestations(path: Path | str | None) -> dict[str, str]:
    """读独立登记锚：``registry.local.jsonl``（run_id → manifest_sha256）。"""
    if not path:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        run_id = str(row.get("run_id") or "").strip()
        digest = str(row.get("manifest_sha256") or "").strip().lower()
        if run_id and _SHA256_RE.match(digest):
            out[run_id] = digest
    return out


def _verify_output_strict(ref: Mapping[str, Any], run_dir: Path) -> tuple[str | None, str]:
    """逐件复算：返回 (失败 code 或 None, detail)。**缺 sha 不等于通过**。"""
    uri = str(ref.get("uri") or "")
    declared_sha = str(ref.get("checksum_sha256") or "").strip().lower()
    declared_size = ref.get("size_bytes")
    try:
        path = resolve_artifact_path(uri, run_dir)
    except ArtifactPathError as exc:
        return B_ARTIFACT_PATH, f"{uri}: {exc.reason}/{exc.detail}"
    root = Path(run_dir).resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return B_ARTIFACT_ESCAPE, f"{uri} -> {resolved} 逃出 run 目录 {root}"
    if not resolved.is_file():
        return B_ARTIFACT_MISSING, f"{uri}: 文件不存在 ({resolved})"
    if not _SHA256_RE.match(declared_sha):
        return B_ARTIFACT_NO_SHA, f"{uri}: manifest 未声明合法 checksum_sha256（{declared_sha!r}）"
    actual = sha256_file(resolved)
    if actual != declared_sha:
        return B_ARTIFACT_HASH, f"{uri}: 声明 {declared_sha} ≠ 实算 {actual}"
    if declared_size is not None and int(declared_size) != resolved.stat().st_size:
        return B_ARTIFACT_SIZE, f"{uri}: 声明 {declared_size} ≠ 实测 {resolved.stat().st_size} 字节"
    return None, f"{uri}: ok"


def _select_data_output(
    outputs: list[Mapping[str, Any]], data_ref: str | None
) -> tuple[Mapping[str, Any] | None, str | None, str]:
    """挑 data 工件：评价声明的 data 引用优先 → role → 唯一工件。"""
    if data_ref:
        digest = _norm_digest(_clean_ref(data_ref))
        if _SHA256_RE.match(digest):
            for ref in outputs:
                if str(ref.get("checksum_sha256") or "").strip().lower() == digest:
                    return ref, None, f"data_ref 摘要命中 {ref.get('uri')}"
            return None, B_ANCHOR_MISMATCH, f"data_ref 摘要 {digest} 在 manifest outputs 中无对应工件"
        uri = _norm_uri(_clean_ref(data_ref))
        base = PurePosixPath(uri).name
        for ref in outputs:
            cand = _norm_uri(str(ref.get("uri") or ""))
            if cand == uri or cand.endswith("/" + base) or PurePosixPath(cand).name == base:
                return ref, None, f"data_ref 路径命中 {ref.get('uri')}"
        return None, B_ANCHOR_MISMATCH, f"data_ref {data_ref!r} 不在 manifest outputs 中（无法复算）"
    for ref in outputs:
        if str(ref.get("role") or "").lower() in ("data", "dataset", "raw", "result", "results"):
            return ref, None, f"按 role 命中 {ref.get('uri')}"
    if len(outputs) == 1:
        return outputs[0], None, "唯一工件充当 data 锚（该 run 未声明 data 引用）"
    return None, B_DATA_MISSING, "manifest 无可识别 data 工件"


def verify_run_content(
    run_dir: Path | str | None,
    *,
    expected_run_id: str | None = None,
    attestations: Mapping[str, str] | None = None,
    data_ref: str | None = None,
    vocab: dict[str, Any] | None = None,
) -> ContentVerdict:
    """一次内容可追溯体检：manifest → digest → 独立锚 → 逐工件复算 → data 锚。"""
    verdict = ContentVerdict(expected_run_id=expected_run_id)
    if not run_dir:
        verdict.codes.append(B_NO_RUN_DIR)
        verdict.detail = "评价无可解析的 run 目录"
        return verdict
    root = Path(run_dir).resolve()
    verdict.run_dir = str(root)
    if not root.is_dir():
        verdict.codes.append(B_NO_RUN_DIR)
        verdict.detail = f"run 目录不存在: {root}"
        return verdict

    vocab = vocab or load_vocab()
    manifest_path = _find_existing_manifest(root)
    if manifest_path is None:
        # 不自动生成：现场造的 manifest 是自签，不能当作可追溯证据
        verdict.codes.append(B_MANIFEST_MISSING)
        verdict.detail = "run 目录无 manifest（不自动生成，避免自签）"
        return verdict
    verdict.manifest_present = True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        verdict.codes.append(B_MANIFEST_JSON)
        verdict.detail = f"{manifest_path}: {exc}"
        return verdict

    problems = validate_manifest(manifest, vocab)
    if problems:
        verdict.codes.append(B_MANIFEST_STRUCT)
        verdict.detail = "; ".join(problems)
        return verdict
    verdict.manifest_valid = True
    verdict.actual_run_id = str(manifest.get("run_id") or "").strip() or None

    if expected_run_id and verdict.actual_run_id != expected_run_id:
        verdict.codes.append(B_CROSS_RUN)
        verdict.detail = f"manifest run_id={verdict.actual_run_id!r} ≠ 评价声明 {expected_run_id!r}"
        return verdict

    verdict.manifest_digest = manifest_digest(manifest)
    attested = (attestations or {}).get(verdict.actual_run_id or "")
    verdict.attested_digest = attested
    if attested is None:
        verdict.codes.append(B_NO_ATTESTATION)
        verdict.detail = f"run {verdict.actual_run_id!r} 无独立登记锚 → 自签，不授权"
    elif attested != verdict.manifest_digest:
        verdict.codes.append(B_DIGEST_MISMATCH)
        verdict.detail = f"登记摘要 {attested} ≠ 实算 {verdict.manifest_digest}"
    else:
        verdict.digest_matches_attestation = True

    producer_raw = manifest.get("producer")
    producer = producer_raw if isinstance(producer_raw, Mapping) else {}
    revision = str(producer.get("revision") or "").strip().lower()
    if _HEX_RE.match(revision):
        verdict.commit_sha = revision
    else:
        verdict.codes.append(B_COMMIT_MISSING)

    config_sha = str(manifest.get("configuration_sha256") or "").strip().lower()
    if _SHA256_RE.match(config_sha):
        verdict.config_sha256 = config_sha

    raw_outputs = manifest.get("outputs")
    outputs: list[Mapping[str, Any]] = [o for o in raw_outputs if isinstance(o, Mapping)] if isinstance(raw_outputs, list) else []
    verdict.outputs_total = len(outputs)
    if not outputs:
        verdict.codes.append(B_NO_OUTPUTS)
        verdict.detail = "manifest outputs 为空：无可复算工件"
        return verdict

    failures: list[str] = []
    verified: list[Mapping[str, Any]] = []
    for ref in outputs:
        code, detail = _verify_output_strict(ref, root)
        if code:
            failures.append(detail)
            verdict.codes.append(code)
        else:
            verified.append(ref)
    verdict.outputs_verified = len(verified)
    verdict.outputs_failed = len(failures)

    if not verified:
        verdict.detail = "; ".join(failures)
        return verdict

    data_out, data_code, data_detail = _select_data_output(outputs, data_ref)
    if data_code:
        verdict.codes.append(data_code)
    if data_out is not None and any(data_out is v for v in verified):
        verdict.data_uri = str(data_out.get("uri") or "")
        verdict.data_anchor = sha256_file(resolve_artifact_path(verdict.data_uri, root))
    elif data_out is None and B_DATA_MISSING not in verdict.codes:
        verdict.codes.append(B_DATA_MISSING)
    # data 工件存在但不通过复算：其失败 code 已记录，这里不重复标注

    if not verdict.codes:
        verdict.detail = f"{len(verified)}/{len(outputs)} 工件复算通过；data 锚 sha256:{verdict.data_anchor}"
    else:
        verdict.detail = "; ".join(failures + [data_detail])
    return verdict


@dataclass
class ChainGrant:
    """一条历史评价的回填裁决（可入库 / 可幂等比对）。"""

    evaluation_id: str
    run_id: str | None = None
    run_dir: str = ""
    reason_class: str = GRANT_CAPPED
    granted: bool = False
    chain_level: str = CHAIN_E1
    refs: dict[str, str] = field(default_factory=dict)
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    detail: str = ""
    verdict: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "reason_class": self.reason_class,
            "granted": self.granted,
            "chain_level": self.chain_level,
            "refs": dict(self.refs),
            "present": list(self.present),
            "missing": list(self.missing),
            "codes": list(self.codes),
            "verdict": self.verdict,
        }


def grant_chain_for_evaluation(
    evaluation: Mapping[str, Any],
    run_dir: Path | str | None,
    *,
    attestations: Mapping[str, str] | None = None,
    vocab: dict[str, Any] | None = None,
) -> ChainGrant:
    """单条历史评价 → 回填裁决：内容可追溯才授完整链，否则 fail-closed 封顶 E1。"""
    ev_id = _clean_ref(evaluation.get("evaluation_id"))
    grade = _clean_ref(evaluation.get("grade")) or None
    form = analyze_chain(evaluation.get("evidence_refs"), grade)
    data_ref = form.refs.get("data")
    expected_run_id = _clean_ref(evaluation.get("run_id")) or (
        Path(run_dir).name if run_dir else None
    )

    verdict = verify_run_content(
        run_dir,
        expected_run_id=expected_run_id,
        attestations=attestations,
        data_ref=data_ref,
        vocab=vocab,
    )
    grant = ChainGrant(
        evaluation_id=ev_id,
        run_id=verdict.actual_run_id or expected_run_id,
        run_dir=verdict.run_dir,
        codes=list(form.codes),
        verdict=verdict.to_dict(),
    )

    refs: dict[str, str] = dict(form.refs)
    # 内容可追溯补充的锚：data（复算摘要）/ code（producer.revision）/ config
    if verdict.data_anchor:
        refs["data"] = f"sha256:{verdict.data_anchor}"
    if verdict.commit_sha and not refs.get("code"):
        repo = _clean_ref(evaluation.get("repo"))
        refs["code"] = f"{repo}@{verdict.commit_sha}" if repo else verdict.commit_sha
    if verdict.config_sha256 and not refs.get("config"):
        refs["config"] = f"sha256:{verdict.config_sha256}"

    # 五元形态必须再由形态判据复核（layer2 自造的引用也要过同一把尺）
    form_after = analyze_chain(refs, grade)
    grant.refs = dict(form_after.refs)
    grant.present = list(form_after.present)
    grant.missing = list(form_after.missing)

    complete = all(k in grant.refs for k in ELEMENT_KEYS)
    grant.granted = bool(complete and not verdict.codes)
    if grant.granted:
        grant.codes = [B_VERIFIED]
        grant.reason_class = GRANT_GRANTED
        grant.chain_level = CHAIN_E3
        grant.detail = verdict.detail
    else:
        grant.codes = _dedup(list(form_after.codes) + list(verdict.codes))
        grant.reason_class = classify_codes(grant.codes)
        if grant.reason_class == GRANT_GRANTED:  # 无 code 但形态不全 → 仍封顶
            grant.reason_class = GRANT_CAPPED
        grant.chain_level = CHAIN_E1
        grant.detail = verdict.detail or "; ".join(grant.codes)
    return grant


# ───────────────────── 落库：回填表 + 链表（内容幂等 upsert） ───────────────────── #

BACKFILL_TABLE = "outcome_chain_backfill"

BACKFILL_DDL = f"""
CREATE TABLE IF NOT EXISTS {BACKFILL_TABLE} (
    evaluation_id     TEXT PRIMARY KEY,
    run_id            TEXT,
    run_dir           TEXT,
    reason_class      TEXT NOT NULL,
    granted           BOOLEAN NOT NULL DEFAULT FALSE,
    chain_level       TEXT NOT NULL,
    manifest_digest   TEXT,
    attested_digest   TEXT,
    data_anchor       TEXT,
    commit_sha        TEXT,
    config_sha256     TEXT,
    outputs_total     INTEGER NOT NULL DEFAULT 0,
    outputs_verified  INTEGER NOT NULL DEFAULT 0,
    outputs_failed    INTEGER NOT NULL DEFAULT 0,
    codes             TEXT NOT NULL DEFAULT '[]',
    detail            TEXT,
    tool_version      TEXT NOT NULL,
    verified_at       TEXT NOT NULL,
    CHECK (reason_class IN ('full_chain_granted','capped_missing','verify_failed')),
    CHECK (chain_level IN ('E1','E2','E3')),
    CHECK (outputs_total >= 0 AND outputs_verified >= 0 AND outputs_failed >= 0)
);
CREATE INDEX IF NOT EXISTS idx_chain_backfill_class
    ON {BACKFILL_TABLE} (reason_class, granted);
"""

BACKFILL_COLUMNS: tuple[str, ...] = (
    "evaluation_id", "run_id", "run_dir", "reason_class", "granted", "chain_level",
    "manifest_digest", "attested_digest", "data_anchor", "commit_sha", "config_sha256",
    "outputs_total", "outputs_verified", "outputs_failed", "codes", "detail",
    "tool_version", "verified_at",
)
_COLUMNS: dict[str, tuple[str, ...]] = {
    BACKFILL_TABLE: BACKFILL_COLUMNS,
    CHAIN_TABLE: CHAIN_COLUMNS,
}
# 幂等比对时忽略的时间戳列（内容相同 → 不写 → 时间戳不动）
_VOLATILE = frozenset({"verified_at", "created_at"})


def ensure_backfill_schema(conn) -> None:
    from sqlalchemy import text

    for stmt in [s.strip() for s in BACKFILL_DDL.split(";") if s.strip()]:
        conn.execute(text(stmt))
    conn.commit()


def backfill_record(grant: ChainGrant, *, verified_at: str | None = None) -> dict[str, Any]:
    verdict = grant.verdict or {}
    return {
        "evaluation_id": grant.evaluation_id,
        "run_id": grant.run_id,
        "run_dir": grant.run_dir or None,
        "reason_class": grant.reason_class,
        "granted": bool(grant.granted),
        "chain_level": grant.chain_level,
        "manifest_digest": verdict.get("manifest_digest"),
        "attested_digest": verdict.get("attested_digest"),
        "data_anchor": verdict.get("data_anchor"),
        "commit_sha": verdict.get("commit_sha"),
        "config_sha256": verdict.get("config_sha256"),
        "outputs_total": int(verdict.get("outputs_total") or 0),
        "outputs_verified": int(verdict.get("outputs_verified") or 0),
        "outputs_failed": int(verdict.get("outputs_failed") or 0),
        "codes": dumps_json(list(grant.codes)),
        "detail": (grant.detail or "")[:2000] or None,
        "tool_version": TOOL_VERSION,
        "verified_at": verified_at or now_utc(),
    }


def chain_row_from_grant(
    grant: ChainGrant,
    evaluation: Mapping[str, Any],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """裁决 → ``outcome_evidence_chains`` 行。**只降不升**：不授权 → 封顶 E1。"""
    requested = _clean_ref(evaluation.get("e_level")) or None
    granted_e = requested if grant.granted else cap_e_level(requested, "E1")
    status = "complete" if grant.granted else ("missing" if not grant.present else "partial")
    codes = _dedup(list(grant.codes) + ([] if grant.granted else ["CHAIN_CAPS_E_TO_E1"]))
    return {
        "chain_id": f"chain:{grant.evaluation_id}",
        "evaluation_id": grant.evaluation_id,
        "artifact_id": _clean_ref(evaluation.get("artifact_id")),
        "mission_id": _int_or_none(evaluation.get("mission_id")),
        "contributor": _clean_ref(evaluation.get("contributor")) or None,
        "conclusion_ref": f"evaluation:{grant.evaluation_id}",
        "grade": _clean_ref(evaluation.get("grade")) or None,
        "e_level_requested": requested,
        "e_level_granted": granted_e,
        "theory_ref": grant.refs.get("theory") or None,
        "code_ref": grant.refs.get("code") or None,
        "config_ref": grant.refs.get("config") or None,
        "data_ref": grant.refs.get("data") or None,
        "report_ref": grant.refs.get("report") or None,
        "present_elements": dumps_json(list(grant.present)),
        "missing_elements": dumps_json(list(grant.missing)),
        "invalid_elements": dumps_json([]),
        "completeness": round(len(grant.present) / len(ELEMENT_KEYS), 4),
        "chain_status": status,
        "capped": not grant.granted,
        "codes": dumps_json(codes),
        "repo": _clean_ref(evaluation.get("repo")) or None,
        "organization_id": evaluation.get("organization_id"),
        "created_at": created_at or now_utc(),
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _existing(conn, table: str, key_col: str, key: str) -> dict[str, Any] | None:
    from sqlalchemy import text

    row = conn.execute(text(f"SELECT * FROM {table} WHERE {key_col} = :k"), {"k": key}).first()
    return dict(row._mapping) if row is not None else None


def _normalised(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in _VOLATILE:
            continue
        if isinstance(value, bool):
            out[key] = bool(value)
        elif isinstance(value, (int, float)):
            out[key] = value
        else:
            out[key] = "" if value is None else str(value)
    return out


def upsert_row(conn, table: str, record: Mapping[str, Any], *, key_col: str = "evaluation_id") -> str:
    """按内容幂等 upsert：内容相同 → 不写（返回 unchanged，时间戳保持原值）。"""
    from sqlalchemy import text

    key = str(record[key_col])
    existing = _existing(conn, table, key_col, key)
    if existing is not None and _normalised(existing) == _normalised(record):
        return "unchanged"
    cols = [c for c in _COLUMNS[table] if c in record]
    payload = {c: record[c] for c in cols}
    if existing is None:
        conn.execute(
            text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"),
            payload,
        )
        return "inserted"
    sets = ", ".join(f"{c} = :{c}" for c in cols if c != key_col)
    payload["_key"] = key
    conn.execute(text(f"UPDATE {table} SET {sets} WHERE {key_col} = :_key"), payload)
    return "updated"

# ───────────────────────── 批量回填：计数 / 双闸 / 报表 ───────────────────────── #

def table_dump(conn, table: str, key_col: str = "evaluation_id") -> list[dict[str, Any]]:
    """整表规范化导出（按主键排序，忽略时间戳）——幂等与指纹比对的唯一口径。"""
    from sqlalchemy import text

    rows = conn.execute(text(f"SELECT * FROM {table} ORDER BY {key_col}")).fetchall()
    return [_normalised(dict(r._mapping)) for r in rows]


def table_fingerprint(conn) -> dict[str, str]:
    """两表内容指纹：二次回填后必须与首跑逐字节相等（判据 c）。"""
    return {
        table: hashlib.sha256(canonical_json(table_dump(conn, table)).encode("utf-8")).hexdigest()
        for table in (BACKFILL_TABLE, CHAIN_TABLE)
    }


def _evaluation_rows_guard(conn) -> dict[str, Any]:
    """评价表只读快照：行级指纹 + S3+/S4/S5/S-N confirmed 计数（判据 e）。"""
    from sqlalchemy import text

    rows = conn.execute(
        text(
            "SELECT evaluation_id, grade, e_level, status, artifact_id "
            "FROM outcome_evaluations ORDER BY evaluation_id"
        )
    ).fetchall()
    dump = [_normalised(dict(r._mapping)) for r in rows]
    high_grade = sum(
        1 for r in dump
        if r.get("status") == "confirmed" and r.get("grade") in ("S3", "S4", "S5", "S-N")
    )
    return {
        "rows": len(dump),
        "fingerprint": hashlib.sha256(canonical_json(dump).encode("utf-8")).hexdigest(),
        "confirmed_high_grade": high_grade,
    }


def _sql_counts(conn) -> dict[str, Any]:
    """手写 SQL 口径（判据 d 的对拍基准；报表侧 Python 计数必须与它精确相等）。"""
    from sqlalchemy import text

    def scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
        return conn.execute(text(sql), params or {}).scalar()

    classes = {
        row[0]: int(row[1])
        for row in conn.execute(
            text(f"SELECT reason_class, count(*) FROM {BACKFILL_TABLE} GROUP BY 1")
        ).fetchall()
    }
    statuses = {
        row[0]: int(row[1])
        for row in conn.execute(
            text(f"SELECT chain_status, count(*) FROM {CHAIN_TABLE} GROUP BY 1")
        ).fetchall()
    }
    return {
        "class_counts": {c: int(classes.get(c, 0)) for c in GRANT_CLASSES},
        "granted_rows": int(scalar(f"SELECT count(*) FROM {BACKFILL_TABLE} WHERE granted = TRUE")),
        "chain_status_counts": {s: int(statuses.get(s, 0)) for s in ("complete", "partial", "missing")},
        "chain_rows": int(scalar(f"SELECT count(*) FROM {CHAIN_TABLE}")),
        "backfill_rows": int(scalar(f"SELECT count(*) FROM {BACKFILL_TABLE}")),
    }


def _settlement_check(conn, granted_ids: list[str]) -> dict[str, Any]:
    """结算面闸（判据 a 后半）：授权行的 ``chain_blocked`` 必须全 0。"""
    from sqlalchemy import text

    if not granted_ids:
        return {"granted_rows": 0, "granted_blocked": 0, "missing_audit_rows": 0,
                "violations": [], "ok": True}
    placeholders = ", ".join(f":e{i}" for i in range(len(granted_ids)))
    params = {f"e{i}": eid for i, eid in enumerate(granted_ids)}
    rows = conn.execute(
        text(
            f"SELECT evaluation_id, chain_status, chain_blocked FROM {VIEW_CHAIN_AUDIT} "
            f"WHERE evaluation_id IN ({placeholders})"
        ),
        params,
    ).fetchall()
    seen = {r[0]: (r[1], bool(r[2])) for r in rows}
    blocked = [eid for eid, (_, b) in seen.items() if b]
    missing = [eid for eid in granted_ids if eid not in seen]
    return {
        "granted_rows": len(granted_ids),
        "granted_blocked": len(blocked),
        "missing_audit_rows": len(missing),
        "violations": sorted(blocked + missing),
        "ok": not blocked and not missing,
    }


def apply_backfill(
    conn,
    evaluations: list[Mapping[str, Any]],
    resolve_run_dir,
    *,
    attestations: Mapping[str, str] | None = None,
    vocab: dict[str, Any] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """批量回填：逐条评价裁决 → 两表幂等落库 → 三分类计数 + 双闸 + 对拍。

    ``resolve_run_dir(ev) -> Path | None`` 由调用方注入（目录布局是平台外知识，
    本模块不猜）。``write=False`` 只算不写（演练/对拍用）。
    """
    ensure_chain_schema(conn)
    ensure_backfill_schema(conn)

    guard_before = _evaluation_rows_guard(conn)
    writes = {"chain": {}, "backfill": {}}

    def bump(bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1

    grants: list[ChainGrant] = []
    for evaluation in sorted(evaluations, key=lambda e: _clean_ref(e.get("evaluation_id"))):
        run_dir = resolve_run_dir(evaluation)
        grant = grant_chain_for_evaluation(
            evaluation, run_dir, attestations=attestations, vocab=vocab
        )
        grants.append(grant)
        if not write:
            continue
        bump(writes["backfill"], upsert_row(conn, BACKFILL_TABLE, backfill_record(grant)))
        bump(writes["chain"], upsert_row(conn, CHAIN_TABLE, chain_row_from_grant(grant, evaluation)))
    if write:
        conn.commit()

    guard_after = _evaluation_rows_guard(conn)
    granted_ids = [g.evaluation_id for g in grants if g.granted]

    python_counts: dict[str, Any] = {c: 0 for c in GRANT_CLASSES}
    code_counts: dict[str, int] = {}
    for grant in grants:
        python_counts[grant.reason_class] += 1
        for code in grant.codes:
            code_counts[code] = code_counts.get(code, 0) + 1
    python_counts["granted_rows"] = len(granted_ids)
    python_counts["chain_status_counts"] = {
        "complete": sum(1 for g in grants if g.granted),
        "partial": sum(1 for g in grants if not g.granted and g.present),
        "missing": sum(1 for g in grants if not g.granted and not g.present),
    }

    sql_counts = _sql_counts(conn) if write else {}
    consistency = (
        {
            "class_counts_equal": sql_counts["class_counts"] == {
                c: python_counts[c] for c in GRANT_CLASSES
            },
            "granted_equal": sql_counts["granted_rows"] == python_counts["granted_rows"],
            "chain_status_equal": sql_counts["chain_status_counts"] == python_counts["chain_status_counts"],
            "chain_rows_equal": sql_counts["chain_rows"] == len(grants),
            "backfill_rows_equal": sql_counts["backfill_rows"] == len(grants),
        }
        if write else {}
    )
    consistency["ok"] = bool(consistency) and all(
        v for k, v in consistency.items() if k != "ok"
    )

    promotion = {
        "before": guard_before,
        "after": guard_after,
        "confirmed_high_grade_delta": guard_after["confirmed_high_grade"] - guard_before["confirmed_high_grade"],
        "fingerprint_unchanged": guard_after["fingerprint"] == guard_before["fingerprint"],
    }
    promotion["ok"] = (
        promotion["confirmed_high_grade_delta"] == 0 and promotion["fingerprint_unchanged"]
    )

    settlement = _settlement_check(conn, granted_ids) if write else {
        "granted_rows": len(granted_ids), "granted_blocked": 0,
        "missing_audit_rows": 0, "violations": [], "ok": True,
    }

    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "totals": {
            "evaluations": len(grants),
            "granted": len(granted_ids),
            "capped": python_counts[GRANT_CAPPED],
            "failed": python_counts[GRANT_FAILED],
        },
        "class_counts": {c: python_counts[c] for c in GRANT_CLASSES},
        "code_counts": dict(sorted(code_counts.items())),
        "chains": python_counts["chain_status_counts"],
        "settlement": settlement,
        "promotion_guard": promotion,
        "sql_consistency": {**consistency, "sql": sql_counts},
        "writes": writes,
        "grants": [g.to_dict() for g in grants],
    }
    report["report_digest"] = report_digest(report)
    return report


def report_digest(report: Mapping[str, Any]) -> str:
    """稳定摘要：只覆盖与「写了几行」无关的判定内容（判据 c 的逐字节比对口径）。"""
    stable = {
        key: report.get(key)
        for key in ("tool_version", "totals", "class_counts", "code_counts", "chains",
                    "settlement", "promotion_guard", "grants")
    }
    return hashlib.sha256(canonical_json(stable).encode("utf-8")).hexdigest()


def write_chain_report(report: Mapping[str, Any], out_dir: Path | str,
                       name: str = "chain_backfill_report.json") -> Path:
    """落盘**稳定视图**：``writes``（本次写了几行）属执行视图，不入文件。

    判定内容（三分类/结算闸/提级闸/对拍/逐条裁决）幂等 → 二次回填报表逐字节一致。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    stable = {k: v for k, v in report.items() if k != "writes"}
    path.write_text(
        json.dumps(stable, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def default_run_dir_resolver(run_root: Path | str | None, run_map: Mapping[str, str] | None = None):
    """默认目录解析：显式映射优先 → ``run_root/<run_id>`` → ``data`` 引用的祖先目录。

    目录布局属于平台外知识，调用方可完全接管（注入自己的 resolver）。
    """
    run_root = Path(run_root) if run_root else None
    mapping = dict(run_map or {})

    def resolve(evaluation: Mapping[str, Any]) -> Path | None:
        for key in (_clean_ref(evaluation.get("evaluation_id")),
                    _clean_ref(evaluation.get("artifact_id"))):
            if key and key in mapping:
                return Path(mapping[key])
        run_id = _clean_ref(evaluation.get("run_id"))
        if run_root is not None and run_id:
            candidate = run_root / run_id
            if candidate.is_dir():
                return candidate
        form = analyze_chain(evaluation.get("evidence_refs"), _clean_ref(evaluation.get("grade")) or None)
        data_ref = _norm_uri(_clean_ref(form.refs.get("data")))
        if run_root is not None and data_ref:
            candidate = (run_root / data_ref).resolve()
            for parent in candidate.parents:
                if _find_existing_manifest(parent) is not None:
                    return parent
        return None

    return resolve


def chain_backfill_main(argv: list[str] | None = None) -> int:
    """CLI：历史评价（DB）× run 目录 → 内容可追溯回填。退出码 0 全过 / 4 闸门不过。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="backfill_wiring chain-backfill",
        description="Run-manifest 接进回填器：内容可追溯的五元链授权（fail-closed）",
    )
    parser.add_argument("--db-url", required=True, help="SQLAlchemy URL（sqlite/postgresql）")
    parser.add_argument("--run-root", required=True, help="run 目录根（含 <run_id> 子目录）")
    parser.add_argument("--attestations", default=None, help="registry.local.jsonl 独立登记锚")
    parser.add_argument("--out-dir", default="artifacts/backfill_wiring/chain", help="报表输出目录")
    parser.add_argument("--run-map", default=None,
                        help="JSON 文件：evaluation_id/artifact_id → run 目录（目录布局属平台外知识）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（试跑）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写库")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, text

    run_map = {}
    if args.run_map:
        run_map = json.loads(Path(args.run_map).read_text(encoding="utf-8"))
        if not isinstance(run_map, dict):
            raise SystemExit("--run-map 必须是 {evaluation_id|artifact_id: run_dir} 的 JSON 对象")

    engine = create_engine(args.db_url, future=True)
    conn = engine.connect()  # 事务由工具自行提交（apply_backfill 逐条 upsert + commit）
    try:
        rows = conn.execute(
            text("SELECT * FROM outcome_evaluations ORDER BY evaluation_id" + (
                f" LIMIT {int(args.limit)}" if args.limit else ""
            ))
        ).fetchall()
        evaluations = [dict(r._mapping) for r in rows]
        report = apply_backfill(
            conn,
            evaluations,
            default_run_dir_resolver(args.run_root, run_map),
            attestations=load_attestations(args.attestations),
            write=not args.dry_run,
        )
    finally:
        conn.close()
    path = write_chain_report(report, args.out_dir)
    c = report["class_counts"]
    print(f"链回填：{report['totals']['evaluations']} 条评价")
    print(f"  完整链授权 {c[GRANT_GRANTED]} · 缺件封顶 {c[GRANT_CAPPED]} · 校验失败 {c[GRANT_FAILED]}")
    print(f"  结算面授权行被拦 {report['settlement']['granted_blocked']}（须为 0）")
    print(f"  提级闸 {report['promotion_guard']['ok']} · 对拍 {report['sql_consistency']['ok']}")
    print(f"Report: {path}")
    if not (report["settlement"]["ok"] and report["promotion_guard"]["ok"] and report["sql_consistency"]["ok"]):
        return 4
    return EXIT_OK


def _layer2_dispatch() -> int:  # pragma: no cover - 入口切换
    argv = sys.argv[1:]
    if argv and argv[0] == "chain-backfill":
        return chain_backfill_main(argv[1:])
    return main(argv)


if __name__ == "__main__":
    sys.exit(_layer2_dispatch())
