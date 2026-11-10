"""sealed_evidence.schema —— 契约 schema 定义与校验（零领域语义）。

sealed_evidence.json 结构：
{
  "schema_version": "sealed-evidence/1.0",
  "mission_id": "m10144",
  "criteria": [
    {
      "id": "c1",
      "text_hash": "<sha256 of criteria text>",
      "artifacts": [
        {
          "path": "artifacts/result.json",
          "sha256": "<hex>",
          "bytes": 1234,
          "produced_by": "pytest tests/test_x.py",
          "params_digest": "<sha256 of params>"
        }
      ]
    }
  ],
  "replay": {
    "cmd": "pytest tests/test_x.py",
    "cwd": ".",
    "env_digest": "<sha256 of env>",
    "timeout_s": 300,
    "last_result": {"exit_code": 0, "timed_out": false}
  },
  "sealed_at": "2026-11-10T16:00:00Z",
  "provenance_digest": "<sha256 of sorted artifact entries>"
}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

SEALED_EVIDENCE_SCHEMA_VERSION = "sealed-evidence/1.0"

# ─────────────────────────── Schema 定义 ───────────────────────────

SEALED_EVIDENCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://tensorplatform.local/schemas/sealed_evidence.schema.json",
    "title": "Sealed Evidence Contract v1.0",
    "description": "Domain-neutral evidence seal contract for reproducibility verification.",
    "type": "object",
    "required": ["schema_version", "mission_id", "criteria", "replay", "sealed_at", "provenance_digest"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": SEALED_EVIDENCE_SCHEMA_VERSION},
        "mission_id": {
            "type": "string",
            "pattern": "^[a-zA-Z0-9_-]{1,64}$",
            "description": "Opaque mission identifier."
        },
        "criteria": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "text_hash", "artifacts"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
                    "text_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "artifacts": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["path", "sha256", "bytes"],
                            "additionalProperties": False,
                            "properties": {
                                "path": {"type": "string", "minLength": 1, "maxLength": 500},
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                "bytes": {"type": "integer", "minimum": 0},
                                "produced_by": {"type": "string", "maxLength": 500},
                                "params_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            },
                        },
                    },
                },
            },
        },
        "replay": {
            "type": "object",
            "required": ["cmd", "cwd", "timeout_s"],
            "additionalProperties": False,
            "properties": {
                "cmd": {"type": "string", "minLength": 1, "maxLength": 2000},
                "cwd": {"type": "string", "minLength": 1, "maxLength": 500},
                "env_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 7200},
                "last_result": {
                    "type": "object",
                    "properties": {
                        "exit_code": {"type": ["integer", "null"]},
                        "timed_out": {"type": "boolean"},
                        "duration_sec": {"type": "number"},
                    },
                },
            },
        },
        "sealed_at": {"type": "string", "format": "date-time"},
        "provenance_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}

# ─────────────────────────── 校验 ───────────────────────────

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MISSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_CRITERIA_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_sha256(value: Any, field: str) -> list[str]:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        return [f"{field}: invalid sha256 '{value}'"]
    return []


def _validate_artifact(art: dict, idx: int) -> list[str]:
    errs: list[str] = []
    if not isinstance(art.get("path"), str) or len(art["path"]) < 1:
        errs.append(f"artifacts[{idx}].path: missing or empty")
    errs.extend(_validate_sha256(art.get("sha256"), f"artifacts[{idx}].sha256"))
    if not isinstance(art.get("bytes"), int) or art["bytes"] < 0:
        errs.append(f"artifacts[{idx}].bytes: invalid")
    if "produced_by" in art and not isinstance(art["produced_by"], str):
        errs.append(f"artifacts[{idx}].produced_by: not a string")
    if "params_digest" in art:
        errs.extend(_validate_sha256(art["params_digest"], f"artifacts[{idx}].params_digest"))
    return errs


def validate_sealed_evidence(data: dict[str, Any]) -> list[str]:
    """校验 sealed_evidence 契约；返回错误列表（空=通过）。"""
    errs: list[str] = []

    if data.get("schema_version") != SEALED_EVIDENCE_SCHEMA_VERSION:
        errs.append(f"schema_version: expected '{SEALED_EVIDENCE_SCHEMA_VERSION}', got '{data.get('schema_version')}'")

    mid = data.get("mission_id")
    if not isinstance(mid, str) or not _MISSION_ID_RE.match(mid):
        errs.append(f"mission_id: invalid '{mid}'")

    criteria = data.get("criteria")
    if not isinstance(criteria, list) or len(criteria) < 1:
        errs.append("criteria: must be non-empty array")
    else:
        for i, c in enumerate(criteria):
            if not isinstance(c, dict):
                errs.append(f"criteria[{i}]: not an object")
                continue
            if not isinstance(c.get("id"), str) or not _CRITERIA_ID_RE.match(c["id"]):
                errs.append(f"criteria[{i}].id: invalid")
            errs.extend(_validate_sha256(c.get("text_hash"), f"criteria[{i}].text_hash"))
            arts = c.get("artifacts")
            if not isinstance(arts, list) or len(arts) < 1:
                errs.append(f"criteria[{i}].artifacts: must be non-empty array")
            else:
                for j, art in enumerate(arts):
                    if not isinstance(art, dict):
                        errs.append(f"criteria[{i}].artifacts[{j}]: not an object")
                    else:
                        errs.extend(_validate_artifact(art, j))

    replay = data.get("replay")
    if not isinstance(replay, dict):
        errs.append("replay: must be an object")
    else:
        if not isinstance(replay.get("cmd"), str) or len(replay["cmd"]) < 1:
            errs.append("replay.cmd: missing or empty")
        if not isinstance(replay.get("cwd"), str) or len(replay["cwd"]) < 1:
            errs.append("replay.cwd: missing or empty")
        if "env_digest" in replay:
            errs.extend(_validate_sha256(replay["env_digest"], "replay.env_digest"))
        ts = replay.get("timeout_s")
        if not isinstance(ts, int) or ts < 1 or ts > 7200:
            errs.append(f"replay.timeout_s: invalid ({ts})")
        lr = replay.get("last_result")
        if lr is not None and not isinstance(lr, dict):
            errs.append("replay.last_result: must be an object or null")

    if not isinstance(data.get("sealed_at"), str):
        errs.append("sealed_at: missing or not a string")

    errs.extend(_validate_sha256(data.get("provenance_digest"), "provenance_digest"))

    return errs


# ─────────────────────────── 构建 ───────────────────────────

def _sha256_file(path: str, cap: int = 64 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        remaining = cap
        while remaining > 0:
            chunk = fh.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _env_digest(env: dict[str, str] | None = None) -> str:
    """计算环境摘要（只取关键键，确定性排序）。"""
    if env is None:
        env = dict(os.environ)
    keys = sorted(k for k in env if k.startswith(("PY", "PYTHON", "PATH", "LD", "HOME")))
    payload = json.dumps({k: env[k] for k in keys}, sort_keys=True)
    return _sha256_text(payload)


def _provenance_digest(criteria: list[dict]) -> str:
    """从排序后的 (artifact path, sha256) 对计算 provenance digest。"""
    entries: list[tuple[str, str]] = []
    for c in criteria:
        for art in c.get("artifacts", []):
            entries.append((art["path"], art["sha256"]))
    entries.sort()
    payload = json.dumps(entries, sort_keys=True)
    return _sha256_text(payload)


def build_sealed_evidence(
    mission_id: str,
    criteria: list[dict[str, Any]],
    replay_cmd: str,
    replay_cwd: str = ".",
    replay_timeout_s: int = 300,
    replay_env: dict[str, str] | None = None,
    last_result: dict[str, Any] | None = None,
    sealed_at: str | None = None,
) -> dict[str, Any]:
    """构建 sealed_evidence 契约（幂等：同输入 → 同输出）。

    Args:
        mission_id: 任务标识
        criteria: 判据列表，每项含 {id, text, artifacts: [{path, ...}]}
        replay_cmd: 重放命令
        replay_cwd: 重放工作目录
        replay_timeout_s: 重放超时（秒）
        replay_env: 环境字典（可选）
        last_result: 上次重放结果（可选）
        sealed_at: 封存时间戳（ISO 8601，可选，默认当前 UTC）

    Returns:
        sealed_evidence 契约字典
    """
    from datetime import datetime, timezone

    # 处理判据：计算 text_hash，归一化 artifact 路径，计算 sha256
    processed_criteria: list[dict[str, Any]] = []
    for c in criteria:
        text = c.get("text", "")
        text_hash = _sha256_text(text)
        artifacts = []
        for art in c.get("artifacts", []):
            path = art.get("path", "")
            # 路径归一化：去除前导 ./
            path = os.path.normpath(path).lstrip("./")
            sha = art.get("sha256", "")
            nbytes = art.get("bytes", 0)
            # 如果 sha256 未提供且文件存在，计算它
            if not sha and os.path.isfile(path):
                sha = _sha256_file(path)
                nbytes = os.path.getsize(path)
            artifact_entry: dict[str, Any] = {
                "path": path,
                "sha256": sha,
                "bytes": nbytes,
            }
            if "produced_by" in art:
                artifact_entry["produced_by"] = art["produced_by"]
            if "params_digest" in art:
                artifact_entry["params_digest"] = art["params_digest"]
            artifacts.append(artifact_entry)
        processed_criteria.append({
            "id": c["id"],
            "text_hash": text_hash,
            "artifacts": artifacts,
        })

    provenance = _provenance_digest(processed_criteria)
    env_d = _env_digest(replay_env)

    if sealed_at is None:
        sealed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    contract: dict[str, Any] = {
        "schema_version": SEALED_EVIDENCE_SCHEMA_VERSION,
        "mission_id": mission_id,
        "criteria": processed_criteria,
        "replay": {
            "cmd": replay_cmd,
            "cwd": replay_cwd,
            "env_digest": env_d,
            "timeout_s": replay_timeout_s,
        },
        "sealed_at": sealed_at,
        "provenance_digest": provenance,
    }
    if last_result is not None:
        contract["replay"]["last_result"] = last_result

    return contract
