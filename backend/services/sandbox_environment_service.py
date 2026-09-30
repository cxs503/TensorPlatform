"""Persistent base runtimes, dependency sets and built sandbox environments."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException


ROOT = Path(os.getenv(
    "SANDBOX_ENV_ROOT",
    str(Path(__file__).resolve().parents[1] / "uploads" / "sandbox_environments"),
))
PYTHON_ROOT = ROOT / "pythons"
BUILDS_ROOT = ROOT / "builds"
WHEELHOUSE_ROOT = ROOT / "wheelhouse"
# Shared cache survives individual runtime rebuilds, so transient package-index
# errors do not force every download to start from zero.
PIP_CACHE_ROOT = ROOT / "pip-cache"
BASES_FILE = ROOT / "base_environments.json"
DEPENDENCIES_FILE = ROOT / "dependency_sets.json"
BUILDS_FILE = ROOT / "runtime_builds.json"
MAX_REQUIREMENTS = 100
MAX_REQUIREMENTS_BYTES = 20 * 1024
MAX_BUILD_LOG_BYTES = 200 * 1024
BUILD_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_ENV_BUILD_TIMEOUT_SECONDS", "900"))
PIP_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_PIP_DOWNLOAD_TIMEOUT_SECONDS", "120"))
PIP_DOWNLOAD_RETRIES = int(os.getenv("SANDBOX_PIP_DOWNLOAD_RETRIES", "5"))
PYTORCH_CPU_INDEX_URL = os.getenv(
    "SANDBOX_PYTORCH_CPU_INDEX_URL",
    "https://download.pytorch.org/whl/cpu",
).rstrip("/")
PYTORCH_PACKAGES = {"torch", "torchvision", "torchaudio"}
# Audited legacy source package; all other regular dependencies remain wheel-only.
REVIEWED_SOURCE_REQUIREMENTS = {"paho-mqtt==1.6.1"}

# Private indexes are intentionally not accepted from user-supplied requirement text.
# Each entry here is a reviewed, fixed source that may be selected from the UI.
SPECIAL_PACKAGE_CATALOG: dict[str, dict[str, str]] = {
    "kl_algorithms": {
        "id": "kl_algorithms",
        "name": "公司算法包（KlAlgorithms）",
        "requirement": "KlAlgorithms",
        "index_url": os.getenv("ODF_ALGORITHMS_INDEX_URL", "http://192.0.2.21:9527/foundation/algorithms").rstrip("/"),
        "trusted_host": os.getenv("ODF_ALGORITHMS_TRUSTED_HOST", "192.0.2.21").strip(),
        "description": "从公司算法私有源安装；构建完成后会锁定实际版本。",
        # KlAlgorithms 2.0.20 imports requests but its package metadata does not
        # declare it. Keep this compatibility dependency platform-controlled.
        "runtime_requirements": [
            "requests>=2.28,<3",
            "paho-mqtt==1.6.1",
        ],
    },
}

_lock = threading.RLock()
_active_build_ids: set[int] = set()


def _now() -> str:
    return datetime.now().isoformat()


def _read_json(path: Path, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    path.chmod(0o666)


def _next_id(items: list[dict[str, Any]], start: int = 1) -> int:
    return max((int(item.get("id") or 0) for item in items), default=start - 1) + 1


def _installed_python(version: str) -> Path | None:
    candidates = sorted(PYTHON_ROOT.glob(f"cpython-{version}*-linux-*-gnu/bin/python"), reverse=True)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _python_version(python_path: Path) -> str:
    result = subprocess.run(
        [str(python_path), "-c", "import platform; print(platform.python_version())"],
        text=True, capture_output=True, timeout=10, check=True,
    )
    return result.stdout.strip()


def _has_gpu() -> bool:
    return Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()


def _seed_bases() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, version in enumerate(("3.8", "3.10", "3.11"), start=1):
        path = _installed_python(version)
        items.append({
            "id": index,
            "name": f"Python {version} CPU 基础环境",
            "python_version": version,
            "compute_type": "cpu",
            "cuda_version": None,
            "python_path": str(path) if path else "",
            "status": "ready" if path else "unavailable",
            "status_reason": "" if path else f"Python {version} 运行时尚未安装",
            "system": True,
            "created_at": _now(),
            "updated_at": _now(),
        })
    return items


def _seed_dependencies() -> list[dict[str, Any]]:
    return [{
        "id": 1,
        "name": "Python 标准库",
        "description": "不安装额外第三方包",
        "requirements": "",
        "special_packages": [],
        "package_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "system": True,
    }]


def _load_state() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ROOT.mkdir(parents=True, exist_ok=True)
    BUILDS_ROOT.mkdir(parents=True, exist_ok=True)
    bases = _read_json(BASES_FILE, _seed_bases())
    dependencies = _read_json(DEPENDENCIES_FILE, _seed_dependencies())
    builds = _read_json(BUILDS_FILE, [])
    if not BASES_FILE.exists():
        _write_json(BASES_FILE, bases)
    if not DEPENDENCIES_FILE.exists():
        _write_json(DEPENDENCIES_FILE, dependencies)
    return bases, dependencies, builds


_bases, _dependencies, _builds = _load_state()


def _refresh_base(base: dict[str, Any]) -> dict[str, Any]:
    configured = str(base.get("python_version") or "").strip()
    path = _installed_python(configured)
    reason = ""
    actual = ""
    if path:
        try:
            actual = _python_version(path)
        except (OSError, subprocess.SubprocessError) as exc:
            reason = f"解释器校验失败：{exc}"
    else:
        reason = f"Python {configured} 运行时尚未安装"
    compute_type = str(base.get("compute_type") or "cpu").lower()
    schedulable = not reason
    if compute_type == "gpu" and not _has_gpu():
        schedulable = False
        reason = (reason + "；" if reason else "") + "当前节点未发现 GPU"
    return {
        **base,
        "python_path": str(path) if path else "",
        "actual_python_version": actual,
        "status": "ready" if path else "unavailable",
        "schedulable": schedulable,
        "status_reason": reason,
    }


def list_base_environments() -> list[dict[str, Any]]:
    with _lock:
        refreshed = [_refresh_base(item) for item in _bases]
        return sorted(refreshed, key=lambda item: (
            tuple(int(part) for part in str(item.get("python_version") or "0").split(".")),
            int(item["id"]),
        ))


def create_base_environment(data: dict[str, Any]) -> dict[str, Any]:
    version = str(data.get("python_version") or "").strip()
    if version not in {"3.8", "3.10", "3.11"}:
        raise HTTPException(400, "当前已安装的 Python 版本仅支持 3.8、3.10、3.11")
    compute = str(data.get("compute_type") or "cpu").lower()
    if compute not in {"cpu", "gpu"}:
        raise HTTPException(400, "计算类型只能是 CPU 或 GPU")
    cuda = str(data.get("cuda_version") or "").strip() or None
    if compute == "cpu":
        cuda = None
    if compute == "gpu" and not cuda:
        raise HTTPException(400, "GPU 基础环境必须指定 CUDA 版本")
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "基础环境名称不能为空")
    with _lock:
        if any(item.get("python_version") == version and item.get("compute_type") == compute and item.get("cuda_version") == cuda for item in _bases):
            raise HTTPException(409, "相同 Python/计算类型/CUDA 的基础环境已存在")
        item = {
            "id": _next_id(_bases), "name": name, "python_version": version,
            "compute_type": compute, "cuda_version": cuda,
            "created_at": _now(), "updated_at": _now(),
            "system": False,
        }
        item = _refresh_base(item)
        _bases.append(item)
        _write_json(BASES_FILE, _bases)
        return item


def update_base_environment(base_id: int, data: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        item = next((item for item in _bases if int(item["id"]) == base_id), None)
        if not item:
            raise HTTPException(404, "基础环境不存在")
        if any(int(build.get("base_environment_id") or 0) == base_id and not build.get("auto_base") for build in _builds):
            raise HTTPException(409, "该基础环境已有依赖构建，不能修改版本；请新建基础环境")
        allowed = {key: data[key] for key in ("name", "compute_type", "cuda_version") if key in data}
        item.update(allowed)
        item["updated_at"] = _now()
        refreshed = _refresh_base(item)
        item.update(refreshed)
        _write_json(BASES_FILE, _bases)
        return item


def delete_base_environment(base_id: int) -> None:
    with _lock:
        item = next((item for item in _bases if int(item["id"]) == base_id), None)
        if not item:
            raise HTTPException(404, "基础环境不存在")
        if int(item["id"]) in {1, 2, 3}:
            raise HTTPException(409, "平台预置基础环境不可删除")
        related = [build for build in _builds if int(build.get("base_environment_id") or 0) == base_id]
        if any(not build.get("auto_base") for build in related):
            raise HTTPException(409, "请先删除该基础环境的运行环境构建")
        for build in related:
            _builds.remove(build)
            target = BUILDS_ROOT / f"runtime_{build['id']}"
            if target.exists():
                shutil.rmtree(target)
        _bases.remove(item)
        _write_json(BASES_FILE, _bases)
        _write_json(BUILDS_FILE, _builds)


_REQ_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?$"
)


def normalize_requirements(raw: Any) -> tuple[str, list[str]]:
    text = str(raw or "").replace("\r\n", "\n")
    if len(text.encode("utf-8")) > MAX_REQUIREMENTS_BYTES:
        raise HTTPException(413, "依赖配置不能超过 20 KB")
    lines: list[str] = []
    for original in text.splitlines():
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if not _REQ_PATTERN.fullmatch(line):
            raise HTTPException(400, f"不安全或无效的依赖格式：{line}")
        lines.append(line)
    if len(lines) > MAX_REQUIREMENTS:
        raise HTTPException(400, "单个依赖配置最多包含 100 个包")
    return "\n".join(lines), lines


def list_special_packages() -> list[dict[str, str]]:
    """Return reviewed private packages without exposing arbitrary index configuration."""
    return [
        {key: spec[key] for key in ("id", "name", "requirement", "description")}
        for spec in SPECIAL_PACKAGE_CATALOG.values()
    ]


def normalize_special_packages(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else []
    normalized: list[str] = []
    for value in values:
        package_id = str(value or "").strip()
        if package_id not in SPECIAL_PACKAGE_CATALOG:
            raise HTTPException(400, f"不支持的特殊依赖包：{package_id}")
        if package_id not in normalized:
            normalized.append(package_id)
    return normalized


def _special_package_specs(raw: Any) -> list[dict[str, str]]:
    return [SPECIAL_PACKAGE_CATALOG[package_id] for package_id in normalize_special_packages(raw)]


def _dependency_package_count(requirements: list[str], special_packages: list[str]) -> int:
    return len(requirements) + len(special_packages)


def _requirement_name(requirement: str) -> str:
    match = re.match(r"^([A-Za-z0-9_.-]+)", requirement.strip())
    return match.group(1).lower().replace("_", "-") if match else ""


def _cached_cpu_wheel_exists(requirement: str, base: dict[str, Any]) -> bool:
    match = re.fullmatch(
        r"(torch|torchvision|torchaudio)==([A-Za-z0-9.*!_-]+\+cpu)",
        requirement,
        flags=re.IGNORECASE,
    )
    if not match:
        return False
    package, version = match.groups()
    python_version = str(base.get("actual_python_version") or base.get("python_version") or "")
    parts = python_version.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        return False
    python_tag = f"cp{parts[0]}{parts[1]}"
    prefixes = {package.lower() + "-", package.lower().replace("-", "_") + "-"}
    version_token = f"-{version.lower()}-"
    tag_token = f"-{python_tag}-"
    return any(
        wheel.name.lower().startswith(tuple(prefixes))
        and version_token in wheel.name.lower()
        and tag_token in wheel.name.lower()
        for wheel in WHEELHOUSE_ROOT.glob("*.whl")
    )


def _pip_install_command(
    python_path: Path,
    requirements_file: Path,
    base: dict[str, Any],
    requirements: list[str],
) -> list[str]:
    command = [
        str(python_path), "-m", "pip", "install", "--disable-pip-version-check",
        "--no-input", "--require-virtualenv", "--only-binary=:all:",
        "--prefer-binary", "--cache-dir", str(PIP_CACHE_ROOT),
        "--timeout", str(PIP_DOWNLOAD_TIMEOUT_SECONDS),
        "--retries", str(PIP_DOWNLOAD_RETRIES),
    ]
    pytorch_requirements = [line for line in requirements if _requirement_name(line) in PYTORCH_PACKAGES]
    if str(base.get("compute_type") or "cpu").lower() == "cpu" and pytorch_requirements:
        invalid = [line for line in pytorch_requirements if not re.fullmatch(
            r"(?:torch|torchvision|torchaudio)==[A-Za-z0-9.*!_-]+\+cpu",
            line,
            flags=re.IGNORECASE,
        )]
        if invalid:
            raise HTTPException(
                400,
                "CPU环境中的PyTorch必须明确使用CPU版本，例如 torch==2.1.0+cpu；"
                f"当前配置：{', '.join(invalid)}",
            )
        command.extend(["--find-links", str(WHEELHOUSE_ROOT)])
        if not all(_cached_cpu_wheel_exists(line, base) for line in pytorch_requirements):
            command.extend(["--extra-index-url", PYTORCH_CPU_INDEX_URL])
    command.extend(["-r", str(requirements_file)])
    return command


def _is_reviewed_source_requirement(requirement: str) -> bool:
    normalized = re.sub(r"\s+", "", str(requirement or "")).lower()
    return normalized in REVIEWED_SOURCE_REQUIREMENTS


def _normal_requirement(requirement: str) -> str:
    return re.sub(r"\s+", "", str(requirement or "")).lower()


def _source_pip_install_command(python_path: Path, requirement: str) -> list[str]:
    """Build only a user-confirmed direct requirement from source; dependencies stay wheel-only."""
    package_name = _requirement_name(requirement)
    return [
        str(python_path), "-m", "pip", "install", "--disable-pip-version-check",
        "--no-input", "--require-virtualenv", "--no-build-isolation",
        "--only-binary=:all:", "--prefer-binary", "--cache-dir", str(PIP_CACHE_ROOT),
        "--timeout", str(PIP_DOWNLOAD_TIMEOUT_SECONDS), "--retries", str(PIP_DOWNLOAD_RETRIES),
        f"--no-binary={package_name}", requirement,
    ]


def _source_candidate_from_wheel_failure(log: str, requirements: list[str]) -> str | None:
    matches = re.findall(r"Could not find a version that satisfies the requirement\s+([^\s(]+)", log, flags=re.IGNORECASE)
    for requested in matches:
        name = _requirement_name(requested)
        candidate = next((line for line in requirements if _requirement_name(line) == name), None)
        if candidate:
            return candidate
    return None


def approve_source_install(build_id: int, approved: bool) -> dict[str, Any]:
    with _lock:
        item = next((row for row in _builds if int(row["id"]) == int(build_id)), None)
        if not item:
            raise HTTPException(404, "运行环境不存在")
        requested = [str(row.get("requirement") or "") for row in (item.get("source_install_requests") or []) if row.get("requirement")]
        if item.get("status") != "awaiting_source_approval" or not requested:
            raise HTTPException(409, "当前构建没有等待确认的源码依赖")
        if approved:
            item["approved_source_requirements"] = list(dict.fromkeys([*(item.get("approved_source_requirements") or []), *requested]))
            item.update({"status": "building", "status_reason": "", "progress_stage": "等待重新构建", "progress_detail": "已确认源码安装，正在重新构建", "updated_at": _now()})
        else:
            item.update({"status": "cancelled", "status_reason": "用户取消了源码依赖安装", "progress_stage": "已取消", "progress_detail": "未执行任何源码安装", "updated_at": _now()})
        _write_json(BUILDS_FILE, _builds)
        return dict(item)


def _special_pip_install_command(python_path: Path, spec: dict[str, str]) -> list[str]:
    """Install a reviewed private source package with its fixed index only."""
    return [
        str(python_path), "-m", "pip", "install", "--disable-pip-version-check",
        "--no-input", "--require-virtualenv", "--no-build-isolation",
        "--cache-dir", str(PIP_CACHE_ROOT), "--timeout", str(PIP_DOWNLOAD_TIMEOUT_SECONDS),
        "--retries", str(PIP_DOWNLOAD_RETRIES),
        spec["requirement"], "--index-url", spec["index_url"],
        "--trusted-host", spec["trusted_host"],
    ]


def _pytorch_smoke_code(requirements: list[str], compute_type: str) -> str | None:
    names = {_requirement_name(line) for line in requirements}
    if not names.intersection(PYTORCH_PACKAGES):
        return None
    expected_cpu = str(compute_type or "cpu").lower() == "cpu"
    return (
        "import torch; "
        "version=str(torch.__version__); "
        f"assert {expected_cpu!r} is False or ('+cpu' in version and torch.version.cuda is None), "
        "f'CPU环境安装了非CPU版PyTorch: {version}, CUDA={torch.version.cuda}'; "
        "print('PyTorch import OK:', version, 'CUDA:', torch.version.cuda)"
    )


def list_dependency_sets() -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in sorted(_dependencies, key=lambda item: int(item["id"]))]


def create_dependency_set(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "依赖配置名称不能为空")
    requirements, lines = normalize_requirements(data.get("requirements"))
    special_packages = normalize_special_packages(data.get("special_packages"))
    with _lock:
        item = {
            "id": _next_id(_dependencies), "name": name,
            "description": str(data.get("description") or "").strip(),
            "requirements": requirements, "special_packages": special_packages,
            "package_count": _dependency_package_count(lines, special_packages),
            "created_at": _now(), "updated_at": _now(), "system": False,
        }
        _dependencies.append(item)
        _write_json(DEPENDENCIES_FILE, _dependencies)
        return item


def update_dependency_set(dependency_id: int, data: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        item = next((item for item in _dependencies if int(item["id"]) == dependency_id), None)
        if not item:
            raise HTTPException(404, "依赖配置不存在")
        if item.get("system"):
            raise HTTPException(409, "平台预置依赖配置不可修改")
        if any(int(build.get("dependency_set_id") or 0) == dependency_id for build in _builds):
            raise HTTPException(409, "该依赖配置已有运行环境构建；请新建版本，避免破坏复现")
        requirements, lines = normalize_requirements(data.get("requirements", item.get("requirements")))
        special_packages = normalize_special_packages(data.get("special_packages", item.get("special_packages") or []))
        item.update({
            "name": str(data.get("name", item.get("name")) or "").strip(),
            "description": str(data.get("description", item.get("description")) or "").strip(),
            "requirements": requirements, "special_packages": special_packages,
            "package_count": _dependency_package_count(lines, special_packages), "updated_at": _now(),
        })
        _write_json(DEPENDENCIES_FILE, _dependencies)
        return item


def delete_dependency_set(dependency_id: int) -> None:
    with _lock:
        item = next((item for item in _dependencies if int(item["id"]) == dependency_id), None)
        if not item:
            raise HTTPException(404, "依赖配置不存在")
        if item.get("system"):
            raise HTTPException(409, "平台预置依赖配置不可删除")
        if any(int(build.get("dependency_set_id") or 0) == dependency_id for build in _builds):
            raise HTTPException(409, "请先删除使用该依赖配置的运行环境构建")
        _dependencies.remove(item)
        _write_json(DEPENDENCIES_FILE, _dependencies)


def _base_by_id(base_id: int) -> dict[str, Any]:
    base = next((item for item in list_base_environments() if int(item["id"]) == base_id), None)
    if not base:
        raise HTTPException(404, "基础环境不存在")
    return base


def _dependency_by_id(dependency_id: int) -> dict[str, Any]:
    dependency = next((item for item in _dependencies if int(item["id"]) == dependency_id), None)
    if not dependency:
        raise HTTPException(404, "依赖配置不存在")
    return dependency


def _runtime_digest(base: dict[str, Any], dependency: dict[str, Any], packages: list[str]) -> str:
    raw = json.dumps({
        "python": base.get("actual_python_version"), "compute": base.get("compute_type"),
        "cuda": base.get("cuda_version"), "requirements": dependency.get("requirements"),
        "special_packages": dependency.get("special_packages") or [], "packages": packages,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sync_system_builds() -> None:
    with _lock:
        dependency = _dependency_by_id(1)
        changed = False
        for base in list_base_environments():
            build = next((item for item in _builds if item.get("auto_base") and int(item.get("base_environment_id") or 0) == int(base["id"])), None)
            if not build:
                build = next((
                    item for item in _builds
                    if int(item.get("base_environment_id") or 0) == int(base["id"])
                    and int(item.get("dependency_set_id") or 0) == 1
                    and item.get("system")
                ), None)
            payload = {
                "name": f"{base['name']} · 标准库",
                "base_environment_id": int(base["id"]), "dependency_set_id": 1,
                "status": "ready" if base.get("status") == "ready" else "unavailable",
                "status_reason": base.get("status_reason", ""),
                "python_path": base.get("python_path", ""),
                "python_version": base.get("actual_python_version") or base.get("python_version"),
                "compute_type": base.get("compute_type"), "cuda_version": base.get("cuda_version"),
                "package_count": 0, "locked_packages": [],
                "system": bool(base.get("system")), "auto_base": True,
                "schedulable": bool(base.get("schedulable")), "updated_at": _now(),
                "runtime_digest": _runtime_digest(base, dependency, []),
                "progress": 100, "progress_stage": "已就绪",
                "progress_detail": "平台预置基础运行时已校验",
            }
            if build:
                build.update(payload)
            else:
                payload.update({"id": _next_id(_builds, 1001), "created_at": _now(), "build_log": "平台预置基础运行时已校验"})
                _builds.append(payload)
            changed = True
        if changed:
            _write_json(BUILDS_FILE, _builds)


def list_runtime_builds() -> list[dict[str, Any]]:
    _sync_system_builds()
    with _lock:
        result = []
        for build in sorted(_builds, key=lambda item: int(item["id"])):
            base = _base_by_id(int(build["base_environment_id"]))
            dependency = _dependency_by_id(int(build["dependency_set_id"]))
            python_path = Path(str(build.get("python_path") or ""))
            # Rebuilds are staged.  Once a runtime has completed successfully,
            # its last verified venv remains safe to use while a replacement is
            # downloading, or even if that replacement subsequently fails.
            fallback_ready = bool(build.get("last_ready_at")) and python_path.is_file()
            ready = (build.get("status") == "ready" and python_path.is_file()) or fallback_ready
            item = {
                **build,
                "is_rebuilding": bool(build.get("status") == "building" and fallback_ready),
                "base_environment": {key: base.get(key) for key in ("id", "name", "python_version", "actual_python_version", "compute_type", "cuda_version")},
                "dependency_set": {key: dependency.get(key) for key in ("id", "name", "package_count")},
                "available": bool(ready and base.get("schedulable")),
                "unavailable_reason": "" if ready and base.get("schedulable") else str(build.get("status_reason") or base.get("status_reason") or "环境尚未构建完成"),
                "actual_python_version": build.get("python_version"),
                "mode": "managed_python" if build.get("auto_base") else "managed_venv",
                "progress": int(build.get("progress", 100 if ready else 0)),
                "progress_stage": str(build.get("progress_stage") or ("已就绪" if ready else "等待构建")),
                "progress_detail": str(build.get("progress_detail") or ""),
            }
            result.append(item)
        return result


def create_runtime_build(data: dict[str, Any]) -> dict[str, Any]:
    base_id = int(data.get("base_environment_id") or 0)
    dependency_id = int(data.get("dependency_set_id") or 0)
    base = _base_by_id(base_id)
    dependency = _dependency_by_id(dependency_id)
    if base.get("status") != "ready":
        raise HTTPException(400, base.get("status_reason") or "基础环境不可用")
    with _lock:
        existing = next((item for item in _builds if int(item.get("base_environment_id") or 0) == base_id and int(item.get("dependency_set_id") or 0) == dependency_id), None)
        if existing:
            if existing.get("auto_base"):
                return existing
            if existing.get("status") == "building":
                raise HTTPException(409, "该运行环境正在构建，请勿重复提交")
            # Preserve the fact that this venv was previously validated.  The
            # public status becomes building, but callers may keep using it
            # until the staged replacement is ready.
            if existing.get("status") == "ready" and Path(str(existing.get("python_path") or "")).is_file():
                existing["last_ready_at"] = existing.get("last_ready_at") or _now()
            existing.update({
                "status": "building", "status_reason": "", "build_log": "等待构建",
                "progress": 0, "progress_stage": "等待构建", "progress_detail": "任务已进入后台队列",
                "source_install_requests": [], "approved_source_requirements": [],
                "updated_at": _now(),
            })
            _write_json(BUILDS_FILE, _builds)
            return dict(existing)
        item = {
            "id": _next_id(_builds, 1001),
            "name": str(data.get("name") or f"{base['name']} · {dependency['name']}").strip(),
            "base_environment_id": base_id, "dependency_set_id": dependency_id,
            "status": "building", "status_reason": "", "python_path": "",
            "python_version": base.get("actual_python_version") or base.get("python_version"),
            "compute_type": base.get("compute_type"), "cuda_version": base.get("cuda_version"),
            "package_count": 0, "locked_packages": [], "system": False,
            "schedulable": bool(base.get("schedulable")), "build_log": "等待构建",
            "progress": 0, "progress_stage": "等待构建", "progress_detail": "任务已进入后台队列",
            "source_install_requests": [], "approved_source_requirements": [],
            "created_at": _now(), "updated_at": _now(),
        }
        _builds.append(item)
        _write_json(BUILDS_FILE, _builds)
        return dict(item)


def build_runtime(build_id: int) -> None:
    with _lock:
        if build_id in _active_build_ids:
            return
        build = next((item for item in _builds if int(item["id"]) == build_id), None)
        if not build or build.get("auto_base"):
            return
        _active_build_ids.add(build_id)
        build.update({
            "status": "building", "status_reason": "", "build_log": "开始创建隔离环境\n",
            "progress": 2, "progress_stage": "准备构建", "progress_detail": "正在初始化构建任务",
            "updated_at": _now(),
        })
        _write_json(BUILDS_FILE, _builds)
        base_id = int(build["base_environment_id"])
        dependency_id = int(build["dependency_set_id"])
        approved_source_requirements = {_normal_requirement(value) for value in (build.get("approved_source_requirements") or [])}
    base = _base_by_id(base_id)
    dependency = _dependency_by_id(dependency_id)
    target = BUILDS_ROOT / f"runtime_{build_id}"
    staging = BUILDS_ROOT / f".runtime_{build_id}.building"
    build_root = staging
    log_parts: list[str] = []

    def update_progress(progress: int, stage: str, detail: str = "") -> None:
        with _lock:
            current = next((item for item in _builds if int(item["id"]) == build_id), None)
            if not current:
                return
            current.update({
                "progress": max(0, min(100, int(progress))),
                "progress_stage": stage,
                "progress_detail": detail[:300],
                "build_log": "".join(log_parts)[-MAX_BUILD_LOG_BYTES:],
                "updated_at": _now(),
            })
            _write_json(BUILDS_FILE, _builds)

    def run(
        command: list[str],
        timeout: int = BUILD_TIMEOUT_SECONDS,
        *,
        stage: str,
        progress_start: int,
        progress_end: int,
    ) -> subprocess.CompletedProcess[str]:
        safe_env = {
            "PATH": str(Path(command[0]).parent) + ":/usr/local/bin:/usr/bin:/bin",
            "HOME": str(build_root), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_DEFAULT_TIMEOUT": str(PIP_DOWNLOAD_TIMEOUT_SECONDS),
            "PIP_RETRIES": str(PIP_DOWNLOAD_RETRIES), "PIP_CACHE_DIR": str(PIP_CACHE_ROOT),
            "PYTHONNOUSERSITE": "1",
        }
        log_parts.append(f"$ {' '.join(command)}\n")
        update_progress(progress_start, stage, "命令已启动")
        process = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=safe_env, bufsize=1,
        )
        command_output: list[str] = []
        current_progress = progress_start
        last_update = 0.0

        def collect_output() -> None:
            nonlocal current_progress, last_update
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                command_output.append(line)
                log_parts.append(line)
                detail = line.strip().replace("\r", " ")
                if not detail:
                    continue
                current_progress = min(progress_end - 1, current_progress + 1)
                now = time.monotonic()
                if now - last_update >= 0.25:
                    update_progress(current_progress, stage, detail)
                    last_update = now

        reader = threading.Thread(target=collect_output, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=5)
            reader.join(timeout=2)
            update_progress(current_progress, stage, f"执行超时（{timeout}秒）")
            raise RuntimeError(f"{stage}超时（{timeout}秒）") from exc
        reader.join(timeout=2)
        output = "".join(command_output)
        update_progress(progress_end, stage, "该阶段已完成")
        if returncode != 0:
            raise RuntimeError(f"{stage}失败（退出码 {returncode}）")
        return subprocess.CompletedProcess(command, returncode, output, "")

    try:
        update_progress(4, "准备构建", "正在创建新的构建目录")
        PIP_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
        run(
            [str(base["python_path"]), "-m", "venv", str(staging)], timeout=120,
            stage="创建隔离环境", progress_start=8, progress_end=20,
        )
        python_path = staging / "bin" / "python"
        requirements, lines = normalize_requirements(dependency.get("requirements"))
        special_specs = _special_package_specs(dependency.get("special_packages"))
        compatibility_requirements = [
            requirement for spec in special_specs
            for requirement in (spec.get("runtime_requirements") or [])
        ]
        total_declared = len(lines) + len(special_specs) + len(compatibility_requirements)
        # User packages need an explicit approval before source installation.
        # Fixed compatibility packages in REVIEWED_SOURCE_REQUIREMENTS have
        # already been audited by the platform and may be installed directly.
        source_lines = [line for line in lines if _normal_requirement(line) in approved_source_requirements]
        reviewed_compatibility_sources = [
            requirement for requirement in compatibility_requirements
            if _is_reviewed_source_requirement(requirement)
        ]
        source_lines = list(dict.fromkeys([*source_lines, *reviewed_compatibility_sources]))
        wheel_lines = [line for line in lines if _normal_requirement(line) not in approved_source_requirements]
        # Deduplicate against explicit user requirements while retaining the
        # controlled compatibility requirements required by selected private packages.
        wheel_lines = list(dict.fromkeys([
            *wheel_lines,
            *(requirement for requirement in compatibility_requirements if not _is_reviewed_source_requirement(requirement)),
        ]))
        if lines:
            update_progress(22, "准备依赖", f"共声明 {total_declared} 个依赖包")
        if wheel_lines:
            requirements_file = staging / "requirements.in"
            requirements_file.write_text("\n".join(wheel_lines) + "\n", encoding="utf-8")
            try:
                run(_pip_install_command(python_path, requirements_file, base, wheel_lines), stage="下载并安装标准依赖", progress_start=25, progress_end=58 if source_lines or special_specs else 75)
            except RuntimeError:
                candidate = _source_candidate_from_wheel_failure("".join(log_parts), wheel_lines)
                if not candidate:
                    raise
                with _lock:
                    current = next(item for item in _builds if int(item["id"]) == build_id)
                    current.update({
                        "status": "awaiting_source_approval",
                        "status_reason": f"依赖 {candidate} 没有可用预编译包，需要确认源码安装",
                        "source_install_requests": [{"requirement": candidate, "reason": "未找到当前 Python 环境可用的预编译包；继续将执行该包的安装脚本"}],
                        "progress": 58, "progress_stage": "等待源码安装确认",
                        "progress_detail": f"等待确认：{candidate}",
                        "build_log": "".join(log_parts)[-MAX_BUILD_LOG_BYTES:], "updated_at": _now(),
                    })
                    _write_json(BUILDS_FILE, _builds)
                return
        for requirement in source_lines:
            run(_source_pip_install_command(python_path, requirement), stage=f"安装已确认源码依赖（{requirement}）", progress_start=60 if wheel_lines else 25, progress_end=72 if special_specs else 75)
        if special_specs:
            for index, spec in enumerate(special_specs, start=1):
                start = 62 if lines else 25
                end = 75 if index == len(special_specs) else max(start + 1, 62 + int(13 * index / len(special_specs)))
                run(
                    _special_pip_install_command(python_path, spec),
                    stage=f"安装受控私有包（{spec['name']}）", progress_start=start, progress_end=end,
                )
        if not lines and not special_specs:
            update_progress(75, "依赖安装", "无需安装额外依赖")
        run(
            [str(python_path), "-m", "pip", "check"], timeout=120,
            stage="校验依赖", progress_start=78, progress_end=84,
        )
        smoke_code = _pytorch_smoke_code(lines, str(base.get("compute_type") or "cpu"))
        if smoke_code:
            run(
                [str(python_path), "-c", smoke_code], timeout=120,
                stage="验证框架", progress_start=85, progress_end=90,
            )
        freeze = run(
            [str(python_path), "-m", "pip", "freeze"], timeout=120,
            stage="锁定依赖版本", progress_start=91, progress_end=96,
        ).stdout.splitlines()
        actual = run(
            [str(python_path), "-c", "import platform; print(platform.python_version())"], timeout=30,
            stage="确认运行时", progress_start=97, progress_end=99,
        ).stdout.strip()
        digest = _runtime_digest(base, dependency, freeze)
        # Publish only a fully validated venv. A failed rebuild leaves the
        # previous ready runtime available instead of deleting it at the start.
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
        final_python_path = target / "bin" / "python"
        with _lock:
            build = next(item for item in _builds if int(item["id"]) == build_id)
            build.update({
                "status": "ready", "status_reason": "", "python_path": str(final_python_path),
                "python_version": actual, "locked_packages": freeze,
                "package_count": len(freeze), "runtime_digest": digest,
                "schedulable": bool(base.get("schedulable")), "updated_at": _now(),
                "last_ready_at": _now(),
                "build_log": "".join(log_parts)[-MAX_BUILD_LOG_BYTES:],
                "progress": 100, "progress_stage": "构建完成",
                "progress_detail": f"已安装并锁定 {len(freeze)} 个包",
            })
            _write_json(BUILDS_FILE, _builds)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        with _lock:
            build = next((item for item in _builds if int(item["id"]) == build_id), None)
            if build:
                build.update({
                    "status": "failed", "status_reason": str(exc), "updated_at": _now(),
                    "build_log": ("".join(log_parts) + f"\n构建失败：{exc}")[-MAX_BUILD_LOG_BYTES:],
                    "progress": int(build.get("progress") or 0),
                    "progress_stage": "构建失败", "progress_detail": str(exc),
                })
                _write_json(BUILDS_FILE, _builds)
    finally:
        with _lock:
            _active_build_ids.discard(build_id)


def delete_runtime_build(build_id: int) -> None:
    with _lock:
        item = next((item for item in _builds if int(item["id"]) == build_id), None)
        if not item:
            raise HTTPException(404, "运行环境构建不存在")
        if item.get("auto_base"):
            raise HTTPException(409, "平台预置基础运行环境不可删除")
        if item.get("status") == "building":
            raise HTTPException(409, "环境正在构建，暂不能删除")
        _builds.remove(item)
        _write_json(BUILDS_FILE, _builds)
    target = BUILDS_ROOT / f"runtime_{build_id}"
    if target.exists():
        shutil.rmtree(target)


def get_runtime_build(build_id: int | None) -> dict[str, Any]:
    builds = list_runtime_builds()
    if build_id is None:
        runtime = next((item for item in builds if item.get("system") and str(item.get("python_version", "")).startswith("3.11")), None)
    else:
        runtime = next((item for item in builds if int(item["id"]) == int(build_id)), None)
    if not runtime:
        raise ValueError("所选运行环境不存在，请重新选择")
    if not runtime.get("available"):
        raise ValueError(f"运行环境不可用：{runtime.get('unavailable_reason') or runtime.get('status')}")
    return runtime


_sync_system_builds()
