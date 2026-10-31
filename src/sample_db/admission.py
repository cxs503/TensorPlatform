"""样本库入库三门准入（门缺陷 B1 修复）。

攻击面：`tools/sample_db/backfill.py::backfill_file()`（samples 表批量入库）。
历史行为：只做字段抽取 + INSERT，全程不读 RunManifest、不复算哈希、不看来源
mission 终态。m810 实测（`B1_sample_db_unverified_ingest`）：一个伪造
`verdict="PASS"` 且 τ=0.4（ν<0 非物理）的裸 JSON 入库成功 1 行 → 判决 FAIL。

三门（全过才准入）：
    门 1  MANIFEST : RunManifest 存在 + 契约校验通过 + validation_status == 'passed'
    门 2  MISSION  : 来源 mission 终态 succeeded（终态 = succeeded/failed/cancelled；
                     approval.finished_status 若存在也须为 succeeded）
    门 3  CHECKSUM : manifest.outputs[] 的 sha256 复算命中 + size_bytes 一致，
                     uri 必须落在 run_dir 内（禁止路径穿越）

拒绝语义：
    * 每一次判定（准入或拒绝）都写 **恰好一条** JSONL 审计记录 —— 「拒绝无留痕」
      本身就是失败（`AdmissionRejected.audit_written` 为 False 时测试立即报错）。
    * 结构化错误码见 `E_*` 常量；`AdmissionRejected.to_dict()` 给出机器可读载荷。

CLI（退出码与 m810 E2E-1b 探针一致）：
    0 = ADMITTED ／ 3 = REJECTED ／ 5 = 工具故障（不得冒充拒绝）

    python3 -m src.sample_db.admission <artifact.json> \
        --mission-status 811=succeeded --audit outputs/e2e1_b1/audit.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = REPO_ROOT / "contracts" / "tensorphysics-run-manifest-v1.schema.json"
DEFAULT_AUDIT_LOG = REPO_ROOT / "outputs" / "sample_db_admission_audit.jsonl"
MANIFEST_FILENAMES = ("run_manifest.json", "manifest.json", "RunManifest.json")
MANIFEST_EMBEDDED_KEYS = ("run_manifest", "manifest", "RunManifest")

# ---- 结构化错误码（拒绝理由） ------------------------------------------------
E_MANIFEST_MISSING = "E_MANIFEST_MISSING"
E_MANIFEST_NOT_OBJECT = "E_MANIFEST_NOT_OBJECT"
E_MANIFEST_UNREADABLE = "E_MANIFEST_UNREADABLE"
E_CONTRACT = "E_CONTRACT"
E_UNVERIFIED = "E_UNVERIFIED"
E_NO_OUTPUTS = "E_NO_OUTPUTS"
E_MISSION_UNRESOLVED = "E_MISSION_UNRESOLVED"
E_MISSION_STATE = "E_MISSION_STATE"
E_MISSION_ID_MISSING = "E_MISSION_ID_MISSING"
E_CHECKSUM_FORMAT = "E_CHECKSUM_FORMAT"
E_CHECKSUM_MISMATCH = "E_CHECKSUM_MISMATCH"
E_SIZE_MISMATCH = "E_SIZE_MISMATCH"
E_ARTIFACT_MISSING = "E_ARTIFACT_MISSING"
E_ARTIFACT_ESCAPE = "E_ARTIFACT_ESCAPE"

TERMINAL_MISSION_STATES = ("succeeded", "failed", "cancelled")

EXIT_ADMITTED = 0
EXIT_REJECTED = 3
EXIT_TOOL_ERROR = 5


# ---- 判定结果载体 ------------------------------------------------------------
@dataclass
class GateResult:
    """单门判定结果。status ∈ {pass, fail, skipped}。"""

    gate: str
    status: str
    code: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class AdmissionDecision:
    """三门准入的完整判定（可序列化，作为判据证据）。"""

    artifact: str
    artifact_sha256: str | None
    mission_id: int | None
    admitted: bool
    gates: list[GateResult]
    verified_outputs: list[dict[str, Any]] = field(default_factory=list)
    manifest_source: str | None = None
    manifest_sha256: str | None = None

    @property
    def failed_gate(self) -> GateResult | None:
        for gate in self.gates:
            if gate.status == "fail":
                return gate
        return None

    @property
    def code(self) -> str | None:
        gate = self.failed_gate
        return gate.code if gate else None

    @property
    def message(self) -> str:
        gate = self.failed_gate
        return gate.message if gate else "all three gates passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "artifact_sha256": self.artifact_sha256,
            "mission_id": self.mission_id,
            "admitted": self.admitted,
            "code": self.code,
            "message": self.message,
            "manifest_source": self.manifest_source,
            "manifest_sha256": self.manifest_sha256,
            "gates": [g.to_dict() for g in self.gates],
            "verified_outputs": self.verified_outputs,
        }


class AdmissionRejected(RuntimeError):
    """准入被拒（结构化错误）。携带完整判定，便于留痕与断言。"""

    def __init__(self, decision: AdmissionDecision, audit_written: bool = True):
        self.decision = decision
        self.code = decision.code or "E_REJECTED"
        self.audit_written = audit_written
        super().__init__(f"{self.code}: {decision.message}")

    def to_dict(self) -> dict[str, Any]:
        payload = self.decision.to_dict()
        payload["audit_written"] = self.audit_written
        return payload


# ---- mission 终态解析 -------------------------------------------------------
class MissionResolver(Protocol):
    def resolve(self, mission_id: int) -> dict[str, Any]:
        """返回 {'mission_id','status','finished_status','source'}；无法确定则抛
        `MissionUnresolved`（→ E_MISSION_UNRESOLVED，绝不当成 succeeded）。"""


class MissionUnresolved(RuntimeError):
    pass


def _normalise_mission(payload: Mapping[str, Any], mission_id: int, source: str) -> dict[str, Any]:
    status = payload.get("status")
    approval = payload.get("approval") or {}
    finished = payload.get("finished_status")
    if finished is None and isinstance(approval, Mapping):
        finished = approval.get("finished_status")
    if finished is None and isinstance(approval, Mapping):
        evidence = approval.get("evidence")
        if isinstance(evidence, Mapping):
            finished = evidence.get("finished_status")
    return {
        "mission_id": mission_id,
        "status": str(status).strip().lower() if isinstance(status, str) else status,
        "finished_status": str(finished).strip().lower() if isinstance(finished, str) else finished,
        "approval_status": (approval.get("status") if isinstance(approval, Mapping) else None),
        "source": source,
    }


class StaticMissionResolver:
    """离线/可复现的 mission 状态源（给定映射即为唯一事实）。

    用法：`StaticMissionResolver({811: "succeeded", 812: {"status": "failed"}})`
    """

    def __init__(self, mapping: Mapping[int, Any], source: str = "static"):
        self._mapping = {int(k): v for k, v in mapping.items()}
        self._source = source

    def resolve(self, mission_id: int) -> dict[str, Any]:
        if mission_id not in self._mapping:
            raise MissionUnresolved(f"mission {mission_id} not present in static resolver")
        entry = self._mapping[mission_id]
        if isinstance(entry, str):
            entry = {"status": entry}
        if not isinstance(entry, Mapping):
            raise MissionUnresolved(f"mission {mission_id} entry is not a mapping/status string")
        return _normalise_mission(entry, mission_id, self._source)


class NullMissionResolver:
    """安全默认：任何 mission 都解析不出来 → 一律 E_MISSION_UNRESOLVED（fail-closed）。"""

    def resolve(self, mission_id: int) -> dict[str, Any]:
        raise MissionUnresolved(
            f"no mission-state source configured for mission {mission_id} "
            "(pass a resolver explicitly; the default denies)"
        )


class ApiMissionResolver:
    """真值源：平台调度 API `GET {base}/tensor/missions/{id}`。

    token 优先取显式参数，其次环境变量 `TENSOR_TOKEN`，否则用
    `TENSOR_AGENT_USER` / `TENSOR_AGENT_PASS` 登录换取。
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or os.environ.get("TENSOR_API_BASE") or "").rstrip("/")
        self.timeout = timeout
        self._token = token or os.environ.get("TENSOR_TOKEN") or os.environ.get("TOKEN")

    def _login(self) -> str:
        user = os.environ.get("TENSOR_AGENT_USER")
        password = os.environ.get("TENSOR_AGENT_PASS")
        if not (user and password):
            raise MissionUnresolved("no token and no TENSOR_AGENT_USER/PASS for API login")
        body = json.dumps({"username": user, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/auth/login", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        self._token = payload.get("access_token") or payload.get("token")
        if not self._token:
            raise MissionUnresolved("login succeeded but no access_token in response")
        return self._token

    def _get(self, path: str) -> dict[str, Any]:
        if not self.base_url:
            raise MissionUnresolved("TENSOR_API_BASE not set")
        token = self._token or self._login()
        req = urllib.request.Request(
            f"{self.base_url}{path}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise MissionUnresolved(f"HTTP {exc.code} for {path}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MissionUnresolved(f"{type(exc).__name__} for {path}: {exc}") from exc

    def resolve(self, mission_id: int) -> dict[str, Any]:
        return _normalise_mission(self._get(f"/tensor/missions/{mission_id}"), mission_id, "api")


# ---- 基础工具 ---------------------------------------------------------------
def sha256_file(path: Path | str, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SHA256_RE_LEN = 64
_HEX = set("0123456789abcdef")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_RE_LEN
        and all(ch in _HEX for ch in value)
    )


def load_schema() -> dict[str, Any]:
    return json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))


def _builtin_contract_errors(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """jsonschema 不可用时的最小契约校验（与 contracts schema 的硬约束对齐）。"""
    errors: list[dict[str, str]] = []
    for key in ("schema_version", "run_id", "status", "validation_status", "outputs"):
        if key not in manifest:
            errors.append({"path": key, "message": "required property missing"})
    if manifest.get("schema_version") != 1:
        errors.append({"path": "schema_version", "message": "must be const 1"})
    if not isinstance(manifest.get("run_id"), str) or not manifest.get("run_id"):
        errors.append({"path": "run_id", "message": "must be a non-empty string"})
    if manifest.get("validation_status") not in ("not_run", "passed", "failed", "incomplete"):
        errors.append({"path": "validation_status", "message": "not in enum"})
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        errors.append({"path": "outputs", "message": "must be an array"})
    else:
        for idx, item in enumerate(outputs):
            base = f"outputs.{idx}"
            if not isinstance(item, Mapping):
                errors.append({"path": base, "message": "must be an object"})
                continue
            for key in ("uri", "media_type", "checksum_sha256", "size_bytes"):
                if key not in item:
                    errors.append({"path": f"{base}.{key}", "message": "required property missing"})
            if "checksum_sha256" in item and not _is_sha256(item.get("checksum_sha256")):
                errors.append(
                    {"path": f"{base}.checksum_sha256", "message": "must match ^[a-f0-9]{64}$"}
                )
            if "size_bytes" in item and not isinstance(item.get("size_bytes"), int):
                errors.append({"path": f"{base}.size_bytes", "message": "must be an integer"})
    return errors


def manifest_contract_errors(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """契约校验：优先 jsonschema（Draft2020），缺依赖时退化为内置硬约束检查。"""
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:  # pragma: no cover - 依赖缺失路径
        return _builtin_contract_errors(manifest)
    schema = load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        if err.validator == "required" and isinstance(err.instance, Mapping):
            for missing in err.validator_value:
                if missing not in err.instance:
                    out.append({"path": f"{path}.{missing}".lstrip("."), "message": "required property missing"})
            continue
        out.append({"path": path, "message": err.message})
    return out


def _locate_manifest(
    artifact_path: Path, run_dir: Path, manifest_path: Path | None
) -> tuple[Any, str | None]:
    """返回 (manifest, source)。manifest 为 None 表示缺失。"""
    if manifest_path is not None:
        candidate = Path(manifest_path)
        if not candidate.is_absolute():
            joined = (run_dir / candidate).resolve()
            candidate = joined if joined.is_file() else candidate.resolve()
        try:
            return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
        except OSError:
            return None, None
        except json.JSONDecodeError:
            return "INVALID_JSON", str(candidate)
    for name in MANIFEST_FILENAMES:
        candidate = run_dir / name
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
            except json.JSONDecodeError:
                return "INVALID_JSON", str(candidate)
    # 工件内嵌 manifest（run 产物自带）
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        artifact = None
    if isinstance(artifact, Mapping):
        for key in MANIFEST_EMBEDDED_KEYS:
            if key in artifact:
                return artifact[key], f"{artifact_path}#{key}"
    return None, None


def _append_audit(audit_log: Path | None, record: Mapping[str, Any]) -> bool:
    if audit_log is None:
        return False
    path = Path(audit_log)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def _gate_manifest(manifest: Any, source: str | None) -> GateResult:
    if manifest is None:
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_MANIFEST_MISSING,
            message=(
                "no RunManifest found (looked for run_manifest.json/manifest.json in the run "
                "directory and an embedded 'manifest' key); unverified products must not enter "
                "the sample library"
            ),
            details={"searched": list(MANIFEST_FILENAMES), "embedded_keys": list(MANIFEST_EMBEDDED_KEYS)},
        )
    if manifest == "INVALID_JSON":
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_MANIFEST_UNREADABLE,
            message=f"manifest at {source} is not valid JSON",
            details={"source": source},
        )
    if not isinstance(manifest, Mapping):
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_MANIFEST_NOT_OBJECT,
            message=f"manifest must be a JSON object, got {type(manifest).__name__}",
            details={"source": source},
        )
    errors = manifest_contract_errors(manifest)
    if errors:
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_CONTRACT,
            message=f"manifest violates tensorphysics-run-manifest-v1: {errors[0]['path']}: {errors[0]['message']}",
            details={"source": source, "errors": errors},
        )
    if manifest.get("validation_status") != "passed":
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_UNVERIFIED,
            message=(
                f"validation_status={manifest.get('validation_status')!r} != 'passed'; "
                "unverified run products must not enter the sample library"
            ),
            details={"source": source, "validation_status": manifest.get("validation_status")},
        )
    outputs = manifest.get("outputs") or []
    if not outputs:
        return GateResult(
            gate="manifest",
            status="fail",
            code=E_NO_OUTPUTS,
            message="manifest.outputs is empty; there is nothing to hash-verify",
            details={"source": source},
        )
    return GateResult(gate="manifest", status="pass", message=f"manifest valid ({source})", details={"source": source})


def _gate_checksums(
    manifest: Mapping[str, Any], run_dir: Path
) -> tuple[GateResult, list[dict[str, Any]]]:
    verified: list[dict[str, Any]] = []
    run_root = run_dir.resolve()
    for idx, item in enumerate(manifest.get("outputs") or []):
        uri = item.get("uri")
        declared = item.get("checksum_sha256")
        declared_size = item.get("size_bytes")
        if not _is_sha256(declared):
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_CHECKSUM_FORMAT,
                    message=f"outputs.{idx}.checksum_sha256={declared!r} is not a 64-hex sha256",
                    details={"index": idx, "uri": uri},
                ),
                verified,
            )
        target = (run_root / str(uri)).resolve() if uri else run_root
        if uri is None or not str(uri).strip():
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_ARTIFACT_MISSING,
                    message=f"outputs.{idx}.uri is empty",
                    details={"index": idx},
                ),
                verified,
            )
        if run_root not in target.parents and target != run_root:
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_ARTIFACT_ESCAPE,
                    message=f"outputs.{idx}.uri={uri!r} escapes the run directory {run_root}",
                    details={"index": idx, "uri": uri, "resolved": str(target)},
                ),
                verified,
            )
        if not target.is_file():
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_ARTIFACT_MISSING,
                    message=f"outputs.{idx}.uri={uri!r} does not exist under {run_root}",
                    details={"index": idx, "uri": uri, "resolved": str(target)},
                ),
                verified,
            )
        actual_size = target.stat().st_size
        if isinstance(declared_size, int) and declared_size != actual_size:
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_SIZE_MISMATCH,
                    message=(
                        f"outputs.{idx} ({uri}) declared size_bytes={declared_size} "
                        f"but the file is {actual_size} bytes"
                    ),
                    details={"index": idx, "uri": uri, "declared": declared_size, "actual": actual_size},
                ),
                verified,
            )
        actual = sha256_file(target)
        if actual != declared:
            return (
                GateResult(
                    gate="checksum",
                    status="fail",
                    code=E_CHECKSUM_MISMATCH,
                    message=(
                        f"outputs.{idx} ({uri}) declared={declared[:12]}... "
                        f"actual={actual[:12]}... ; the product was tampered with or is stale"
                    ),
                    details={
                        "index": idx,
                        "uri": uri,
                        "declared_sha256": declared,
                        "actual_sha256": actual,
                    },
                ),
                verified,
            )
        verified.append(
            {"uri": uri, "sha256": actual, "size_bytes": actual_size, "role": item.get("role")}
        )
    return (
        GateResult(
            gate="checksum",
            status="pass",
            message=f"{len(verified)} output(s) sha256 re-computed and matched",
            details={"verified": len(verified)},
        ),
        verified,
    )


def _gate_mission(mission_id: int | None, resolver: MissionResolver | None) -> tuple[GateResult, dict[str, Any] | None]:
    if mission_id is None:
        return (
            GateResult(
                gate="mission",
                status="fail",
                code=E_MISSION_ID_MISSING,
                message="source mission id cannot be determined (manifest.mission_id / path / argument)",
                details={},
            ),
            None,
        )
    try:
        state = (resolver or NullMissionResolver()).resolve(mission_id)
    except MissionUnresolved as exc:
        return (
            GateResult(
                gate="mission",
                status="fail",
                code=E_MISSION_UNRESOLVED,
                message=f"mission {mission_id} state unresolved: {exc}",
                details={"mission_id": mission_id},
            ),
            None,
        )
    status = state.get("status")
    finished = state.get("finished_status")
    if status != "succeeded":
        return (
            GateResult(
                gate="mission",
                status="fail",
                code=E_MISSION_STATE,
                message=(
                    f"mission {mission_id} terminal state status={status!r} != 'succeeded'"
                    + ("" if status in TERMINAL_MISSION_STATES else " (not even terminal)")
                    + "; products of failed/running missions must not enter the sample library"
                ),
                details={"mission_id": mission_id, "state": state},
            ),
            state,
        )
    if finished is not None and finished != "succeeded":
        return (
            GateResult(
                gate="mission",
                status="fail",
                code=E_MISSION_STATE,
                message=f"mission {mission_id} approval.finished_status={finished!r} != 'succeeded'",
                details={"mission_id": mission_id, "state": state},
            ),
            state,
        )
    return (
        GateResult(
            gate="mission",
            status="pass",
            message=f"mission {mission_id} terminal state succeeded ({state.get('source')})",
            details={"mission_id": mission_id, "state": state},
        ),
        state,
    )


def _resolve_mission_id(
    manifest: Mapping[str, Any] | None, artifact_path: Path, explicit: int | None
) -> int | None:
    if explicit is not None:
        return int(explicit)
    if isinstance(manifest, Mapping):
        for key in ("mission_id", "source_mission_id"):
            value = manifest.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    for part in reversed(artifact_path.parts):
        if part.startswith("m") and part[1:].isdigit():
            return int(part[1:])
    return None


def admit_sample(
    artifact_path: str | Path,
    *,
    mission_resolver: MissionResolver | None = None,
    audit_log: str | Path | None = DEFAULT_AUDIT_LOG,
    manifest_path: str | Path | None = None,
    run_dir: str | Path | None = None,
    mission_id: int | None = None,
    actor: str = "sample_db_ingest",
) -> AdmissionDecision:
    """三门准入判定。**永不**在判定失败时抛异常（由调用方 raise），但**必**留痕。"""
    artifact = Path(artifact_path)
    if not artifact.is_absolute():
        artifact = artifact.resolve()
    work_dir = Path(run_dir).resolve() if run_dir else artifact.parent
    gates: list[GateResult] = []
    verified: list[dict[str, Any]] = []
    artifact_sha: str | None = None
    if artifact.is_file():
        artifact_sha = sha256_file(artifact)

    manifest, source = _locate_manifest(artifact, work_dir, Path(manifest_path) if manifest_path else None)
    gate1 = _gate_manifest(manifest, source)
    gates.append(gate1)

    resolved_mission = _resolve_mission_id(manifest if isinstance(manifest, Mapping) else None, artifact, mission_id)

    if gate1.status == "pass":
        gate2, _state = _gate_mission(resolved_mission, mission_resolver)
    else:
        gate2 = GateResult(
            gate="mission",
            status="skipped",
            message="not evaluated: gate 1 (RunManifest) failed",
            details={"mission_id": resolved_mission},
        )
    gates.append(gate2)

    if gate1.status == "pass":
        gate3, verified = _gate_checksums(manifest, work_dir)  # type: ignore[arg-type]
    else:
        gate3 = GateResult(
            gate="checksum",
            status="skipped",
            message="not evaluated: no valid manifest to take declared hashes from",
            details={},
        )
    gates.append(gate3)

    admitted = all(g.status == "pass" for g in gates)
    decision = AdmissionDecision(
        artifact=str(artifact),
        artifact_sha256=artifact_sha,
        mission_id=resolved_mission,
        admitted=admitted,
        gates=gates,
        verified_outputs=verified,
        manifest_source=source,
        manifest_sha256=sha256_file(Path(source.split("#")[0])) if source and Path(source.split("#")[0]).is_file() else None,
    )
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "ts_epoch": time.time(),
        "event": "sample_db_admission",
        "actor": actor,
        "decision": "admitted" if admitted else "rejected",
        "code": decision.code,
        "message": decision.message,
        "artifact": decision.artifact,
        "artifact_sha256": decision.artifact_sha256,
        "mission_id": decision.mission_id,
        "manifest_source": decision.manifest_source,
        "gates": {g.gate: g.status for g in gates},
        "verified_outputs": verified,
    }
    _append_audit(Path(audit_log) if audit_log is not None else None, record)
    return decision


def require_admission(decision: AdmissionDecision, audit_written: bool = True) -> AdmissionDecision:
    if not decision.admitted:
        raise AdmissionRejected(decision, audit_written=audit_written)
    return decision


def _parse_mission_status_specs(specs: Iterable[str]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for spec in specs or ():
        parts = spec.split("=")
        if len(parts) < 2 or not parts[0].strip().isdigit():
            raise argparse.ArgumentTypeError(f"--mission-status expects MID=status[:finished], got {spec!r}")
        mid = int(parts[0])
        rest = parts[1].split(":")
        entry: dict[str, Any] = {"status": rest[0]}
        if len(rest) > 1:
            entry["finished_status"] = rest[1]
        mapping[mid] = entry
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.sample_db.admission",
        description="样本库入库三门准入（RunManifest 有效 / mission 终态 succeeded / sha256 复算）",
    )
    parser.add_argument("artifact", help="run 产物 JSON（样本来源）")
    parser.add_argument("--manifest", default=None, help="显式 RunManifest 路径（默认在 run 目录查找）")
    parser.add_argument("--run-dir", default=None, help="run 目录（哈希相对根；默认 artifact 所在目录）")
    parser.add_argument("--mission-id", type=int, default=None, help="显式来源 mission id")
    parser.add_argument(
        "--mission-status",
        action="append",
        default=[],
        metavar="MID=STATUS[:FINISHED]",
        help="静态 mission 状态源（离线复现）：可重复，如 811=succeeded",
    )
    parser.add_argument("--api", action="store_true", help="改用平台 API 解析 mission 终态")
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT_LOG), help="JSONL 审计留痕路径")
    parser.add_argument("--json-out", default=None, help="把判定载荷写成 JSON 文件")
    args = parser.parse_args(argv)

    if args.api:
        resolver: MissionResolver = ApiMissionResolver()
    elif args.mission_status:
        resolver = StaticMissionResolver(_parse_mission_status_specs(args.mission_status))
    else:
        resolver = NullMissionResolver()

    decision = admit_sample(
        args.artifact,
        mission_resolver=resolver,
        audit_log=args.audit,
        manifest_path=args.manifest,
        run_dir=args.run_dir,
        mission_id=args.mission_id,
    )
    payload = decision.to_dict()
    payload["audit_log"] = args.audit
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if decision.admitted:
        print(json.dumps({"verdict": "ADMITTED", "code": None, "detail": payload}, ensure_ascii=False)[:4000])
        return EXIT_ADMITTED
    print(text, file=sys.stderr)
    return EXIT_REJECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())