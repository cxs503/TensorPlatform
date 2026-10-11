"""
算法商店API — 预设方法浏览 + 关联数据集 + 触发训练
"""
import ast
import hashlib
import io
import json
import os
import pickletools
import re
import shutil
import stat
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
import joblib
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from api.deps import require_auth, require_permission
from database.config import get_db
from services.algorithm_store_service import get_algorithm_store

router = APIRouter(prefix="/algorithm-store", tags=["算法商店"])

ALGORITHM_ARTIFACT_DIR = Path("/app/uploads/algorithms")
ALGORITHM_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512
MAX_ARCHIVE_UNPACKED_BYTES = 1024 * 1024 * 1024


class AlgorithmCreateReq(BaseModel):
    name: str
    category: str = "custom"
    task_type: Optional[str] = None
    algorithm_type: Optional[str] = None
    framework: str = "sklearn"
    description: str = ""
    version: str = "1.0.0"
    tags: List[str] = Field(default_factory=list)
    entry_point: str = ""
    params_schema: str = ""
    dependency_id: Optional[int] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class AlgorithmUpdateReq(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    task_type: Optional[str] = None
    algorithm_type: Optional[str] = None
    framework: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    tags: Optional[List[str]] = None
    entry_point: Optional[str] = None
    params_schema: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None


def _adapter_metadata(task_type, algorithm_type, framework, metrics=None):
    from services.training_adapter_registry import resolve_training_adapter

    metadata = dict(metrics or {})
    spec = resolve_training_adapter(task_type, algorithm_type, framework)
    metadata["task_type"] = task_type
    metadata["algorithm_type"] = algorithm_type
    if spec:
        metadata.update({
            "training_adapter": spec.id,
            "runtime_model_type": spec.runtime_model_type,
            "runtime_task_type": spec.runtime_task_type,
            "split_strategy": spec.split_strategy,
        })
    else:
        for key in (
            "training_adapter", "runtime_model_type",
            "runtime_task_type", "split_strategy",
        ):
            metadata.pop(key, None)
    return metadata, spec


def _infer_python_source_identity(source: str) -> dict:
    """Infer display metadata only; uploaded Python still executes as source."""
    modules: set[str] = set()
    symbols: set[str] = set()
    try:
        tree = ast.parse(source or "")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".", 1)[0].lower())
            elif isinstance(node, (ast.Name, ast.Attribute)):
                symbols.add(getattr(node, "id", None) or getattr(node, "attr", ""))
    except SyntaxError:
        return {"framework": "python", "algorithm_type": "custom_python", "confidence": "low"}

    lowered_symbols = {value.lower() for value in symbols if value}
    if "torch" in modules:
        framework = "pytorch"
    elif "tensorflow" in modules or "keras" in modules:
        framework = "tensorflow"
    elif "xgboost" in modules:
        framework = "xgboost"
    elif "lightgbm" in modules:
        framework = "lightgbm"
    elif "sklearn" in modules:
        framework = "sklearn"
    else:
        framework = "python"

    if {"randomforestregressor", "randomforestclassifier"} & lowered_symbols:
        algorithm_type = "random_forest"
    elif "xgboost" in modules or {"xgbregressor", "xgbclassifier"} & lowered_symbols:
        algorithm_type = "xgboost"
    elif "lightgbm" in modules or {"lgbmregressor", "lgbmclassifier"} & lowered_symbols:
        algorithm_type = "lightgbm"
    elif (
        {"linear", "sequential", "dense"} & lowered_symbols
        and framework in {"pytorch", "tensorflow"}
    ):
        algorithm_type = "mlp"
    else:
        algorithm_type = "custom_python"

    # ``user_script`` is only an execution mode.  Infer the user-facing task
    # type from explicit time-series behaviour in the uploaded source, rather
    # than silently retaining the form's default ``regression``.  Requiring a
    # time column/order signal together with a temporal CV/window operation
    # keeps ordinary tabular regressors from being misclassified.
    lower_source = (source or "").lower()
    has_time_column = any(token in lower_source for token in (
        "time_col", "time_column", "timestamp", "datetime", "date_col", "时间列", "特征时间",
    ))
    has_temporal_operation = (
        "timeseriessplit" in lowered_symbols
        or ".rolling(" in lower_source
        or ".shift(" in lower_source
        or ".resample(" in lower_source
    )
    sorts_temporally = "sort_values" in lower_source and has_time_column
    detected_task_type = (
        "time_series_forecasting"
        if has_time_column and (has_temporal_operation or sorts_temporally)
        else None
    )
    return {
        "framework": framework,
        "algorithm_type": algorithm_type,
        "confidence": "static",
        "detected_task_type": detected_task_type,
    }


def _safe_archive_name(name: str, *, repair_legacy_zip: bool = False) -> str:
    """Normalise an archive path and repair legacy GBK ZIP names when possible."""
    original = name
    if repair_legacy_zip:
        # Python's zipfile decodes legacy Chinese ZIP filenames as CP437 when the
        # UTF-8 flag is absent.  Repair only when it becomes CJK text, so normal
        # western CP437 filenames are never changed.
        try:
            repaired = name.encode("cp437").decode("gb18030")
            if any("\u4e00" <= char <= "\u9fff" for char in repaired):
                name = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail=f"压缩包包含不安全路径: {original}")
    return normalized


def _archive_summary(names: List[str], total_size: int) -> dict:
    basenames = {Path(name).name for name in names}
    docker_files = {
        "Dockerfile", "serve.py", "requirements.txt", "model.onnx",
        "model-metadata.json", "README.txt",
    }
    exe_files = {"build.sh", "serve.py", "requirements.txt", "model.pkl"}
    if docker_files.issubset(basenames):
        return {
            "artifact_format": "docker_bundle",
            "artifact_kind": "deployment_bundle",
            "artifact_label": "Docker部署包",
        }
    if exe_files.issubset(basenames):
        return {
            "artifact_format": "exe_bundle",
            "artifact_kind": "deployment_bundle",
            "artifact_label": "EXE构建包",
        }
    if any(name.lower().endswith(".py") for name in names):
        return {
            "artifact_format": "source_archive",
            "artifact_kind": "algorithm_source",
            "artifact_label": "算法源码压缩包",
        }
    raise HTTPException(
        status_code=400,
        detail="压缩包结构无法识别：需要包含Python源码，或完整的部署包文件",
    )


def _validate_tar(content: bytes) -> dict:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise HTTPException(status_code=400, detail="压缩包为空或文件数量超过限制")
            names = []
            total_size = 0
            for member in members:
                name = _safe_archive_name(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise HTTPException(status_code=400, detail=f"压缩包不允许链接或设备文件: {name}")
                if member.isfile():
                    names.append(name)
                    total_size += max(member.size, 0)
            if total_size > MAX_ARCHIVE_UNPACKED_BYTES:
                raise HTTPException(status_code=400, detail="压缩包解压后的总大小超过1GB限制")
    except HTTPException:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"TAR压缩包损坏: {exc}")
    return {**_archive_summary(names, total_size), "archive_files": names[:100]}


def _validate_zip(content: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                raise HTTPException(status_code=400, detail="ZIP为空或文件数量超过限制")
            names = []
            total_size = 0
            for info in infos:
                name = _safe_archive_name(info.filename, repair_legacy_zip=True)
                if not info.is_dir():
                    names.append(name)
                    total_size += max(info.file_size, 0)
            if total_size > MAX_ARCHIVE_UNPACKED_BYTES:
                raise HTTPException(status_code=400, detail="ZIP解压后的总大小超过1GB限制")
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"ZIP压缩包损坏: {exc}")
    return {**_archive_summary(names, total_size), "archive_files": names[:100]}



CONTROL_CONTRACT_FILENAMES = {"foundation_algorithm.json", "foundation_control.json"}
CONTROL_RUN_MODES = {"dataset_replay", "parameterized"}
CONTROL_PARAMETER_TYPES = {"number", "integer", "boolean", "string", "select"}


def _normalise_control_contract(raw: Any) -> dict[str, Any]:
    """Validate the small, declarative run contract bundled with an algorithm project.

    This is intentionally metadata only: it describes a form and an executable
    entry point, but never causes code to be imported during upload.
    """
    if not isinstance(raw, dict):
        raise HTTPException(400, detail="foundation_algorithm.json 必须是 JSON 对象")
    kind = str(raw.get("kind") or "control_simulation").strip()
    if kind != "control_simulation":
        raise HTTPException(400, detail="当前仅支持 kind=control_simulation 的算法运行描述")
    raw_modes = raw.get("modes") or raw.get("run_modes") or ["dataset_replay"]
    modes: dict[str, dict[str, Any]] = {}
    if isinstance(raw_modes, list):
        for mode in raw_modes:
            name = str(mode).strip()
            if name in CONTROL_RUN_MODES:
                modes[name] = {}
    elif isinstance(raw_modes, dict):
        for mode, mode_config in raw_modes.items():
            name = str(mode).strip()
            if name not in CONTROL_RUN_MODES:
                continue
            if mode_config is not None and not isinstance(mode_config, dict):
                raise HTTPException(400, detail=f"运行方式 {name} 的配置必须是对象")
            config = dict(mode_config or {})
            entry = str(config.get("entry_point") or "").strip().replace("\\", "/")
            if entry:
                path = Path(entry)
                if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".py":
                    raise HTTPException(400, detail=f"运行方式 {name} 的 entry_point 无效")
                config["entry_point"] = entry
            modes[name] = config
    if not modes:
        raise HTTPException(400, detail="算法运行描述至少要声明一种运行方式：dataset_replay 或 parameterized")

    raw_parameters = raw.get("parameters")
    if raw_parameters is None and isinstance(modes.get("parameterized"), dict):
        raw_parameters = modes["parameterized"].get("parameters")
    raw_parameters = raw_parameters or []
    if not isinstance(raw_parameters, list) or len(raw_parameters) > 40:
        raise HTTPException(400, detail="parameters 必须是最多 40 项的数组")
    parameters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_parameters:
        if not isinstance(item, dict):
            raise HTTPException(400, detail="parameters 中每一项必须是对象")
        key = str(item.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) or key in seen:
            raise HTTPException(400, detail="参数 key 必须唯一，且只能使用字母、数字和下划线")
        seen.add(key)
        parameter_type = str(item.get("type") or "string").strip()
        if parameter_type not in CONTROL_PARAMETER_TYPES:
            raise HTTPException(400, detail=f"参数 {key} 的 type 不支持")
        result = {
            "key": key,
            "label": str(item.get("label") or key)[:80],
            "type": parameter_type,
            "required": bool(item.get("required", False)),
            "help": str(item.get("help") or "")[:300],
        }
        for field in ("default", "minimum", "maximum", "step", "placeholder"):
            if field in item:
                result[field] = item[field]
        options = item.get("options")
        if parameter_type == "select":
            if not isinstance(options, list) or not options or len(options) > 100:
                raise HTTPException(400, detail=f"选择参数 {key} 必须提供 options")
            result["options"] = [
                option if isinstance(option, (str, int, float, bool)) else {
                    "label": str(option.get("label") or option.get("value") or "")[:80],
                    "value": option.get("value"),
                }
                for option in options
                if isinstance(option, (str, int, float, bool, dict))
            ]
            if not result["options"]:
                raise HTTPException(400, detail=f"选择参数 {key} 的 options 无效")
        parameters.append(result)
    return {
        "version": int(raw.get("version") or 1),
        "kind": kind,
        "modes": modes,
        "parameters": parameters,
        "description": str(raw.get("description") or "")[:500],
        "outputs": [str(item)[:120] for item in (raw.get("outputs") or []) if isinstance(item, str)][:20],
    }


def _validate_control_run_parameters(contract: dict[str, Any], mode: str, parameters: dict[str, Any]) -> None:
    modes = contract.get("modes") if isinstance(contract.get("modes"), dict) else {}
    if mode not in modes:
        raise HTTPException(400, detail=f"该算法未声明 {mode} 运行方式")
    for spec in contract.get("parameters") or []:
        key = spec.get("key")
        value = parameters.get(key)
        if spec.get("required") and (value is None or value == ""):
            raise HTTPException(400, detail=f"缺少必填参数：{spec.get('label') or key}")
        if value is None:
            continue
        kind = spec.get("type")
        if kind in {"number", "integer"}:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, detail=f"参数 {spec.get('label') or key} 必须是数值") from exc
            if kind == "integer" and not number.is_integer():
                raise HTTPException(400, detail=f"参数 {spec.get('label') or key} 必须是整数")
            if spec.get("minimum") is not None and number < float(spec["minimum"]):
                raise HTTPException(400, detail=f"参数 {spec.get('label') or key} 小于允许范围")
            if spec.get("maximum") is not None and number > float(spec["maximum"]):
                raise HTTPException(400, detail=f"参数 {spec.get('label') or key} 大于允许范围")
        if kind == "select":
            options = spec.get("options") or []
            allowed = {item.get("value") if isinstance(item, dict) else item for item in options}
            if value not in allowed:
                raise HTTPException(400, detail=f"参数 {spec.get('label') or key} 的取值不在可选范围内")


def _read_control_contract_from_archive(content: bytes) -> dict[str, Any] | None:
    """Read an optional project contract without extracting or executing it."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                name = _safe_archive_name(info.filename, repair_legacy_zip=True)
                if info.is_dir() or Path(name).name.lower() not in CONTROL_CONTRACT_FILENAMES:
                    continue
                if info.file_size > 128 * 1024:
                    raise HTTPException(400, detail="foundation_algorithm.json 不能超过 128KB")
                try:
                    raw = json.loads(archive.read(info).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HTTPException(400, detail=f"foundation_algorithm.json 不是有效 JSON：{exc}") from exc
                contract = _normalise_control_contract(raw)
                contract["source_file"] = name
                return contract
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(400, detail=f"算法项目压缩包损坏: {exc}") from exc
    return None


def _dmc_control_parameter_candidates(source_root: Path) -> list[dict[str, Any]]:
    """Read DMC's declarative JSON config without importing project code."""
    config_files = [
        *source_root.rglob("dmc_config.example.json"),
        *source_root.rglob("dmc_controller_config.json"),
    ]
    excluded_config_dirs = {"docs", "reports", "dist", "test", "tests", "fixture", "fixtures", "example", "examples", "__pycache__"}
    config_files = [item for item in config_files if not any(part.lower() in excluded_config_dirs for part in item.parts)]
    config_files.sort(key=lambda item: (0 if "conf" in item.parts else 1 if "config" in item.parts else 2, str(item)))
    if not config_files:
        return []
    try:
        raw = json.loads(config_files[0].read_text(encoding="utf-8"))
        config = raw.get("dmc_config", raw)
        timing = config.get("timing") or {}
        solver = config.get("solver") or {}
        cv = next((item for item in config.get("cvs") or [] if item.get("enabled", True)), {})
        mv = next((item for item in config.get("mvs") or [] if item.get("enabled", True)), {})
        process = next((item for item in config.get("process_models") or [] if item.get("interaction_enabled", True)), {})
        plant = process.get("parameters") or {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []

    def spec(key: str, label: str, default: Any, help_text: str, minimum: float | None = None, maximum: float | None = None, *, integer: bool = False):
        if not isinstance(default, (int, float)) or isinstance(default, bool):
            return None
        result = {"key": key, "label": label, "type": "integer" if integer else "number", "default": default, "origin": "project_config", "confidence": "high", "help": help_text}
        if minimum is not None: result["minimum"] = minimum
        if maximum is not None: result["maximum"] = maximum
        result["step"] = 1 if integer else 0.1
        return result

    candidates = [
        spec("steps", "steps", 30, "平台调用控制器的循环次数", 2, 500, integer=True),
        spec("sample_time_s", "sample_time_s", 5, "每次控制计算的采样间隔", 0.1, 3600),
        spec("model_horizon", "model_horizon", timing.get("N"), "模型响应长度；平台会自动满足 DMC 最小时域约束", 5, 2000, integer=True),
        spec("prediction_horizon", "prediction_horizon", timing.get("P"), "预测未来过程响应的步数", 5, 200, integer=True),
        spec("control_horizon", "control_horizon", timing.get("M"), "允许优化的控制动作步数", 1, 50, integer=True),
        spec("execution_interval_cycles", "execution_interval_cycles", timing.get("execution_interval_cycles", 1), "每隔多少个采样周期执行一次控制计算；默认每个周期执行", 1, 10000, integer=True),
        spec("solver_maxiter", "solver_maxiter", solver.get("maxiter"), "优化器单次求解的最大迭代次数", 10, 2000, integer=True),
        spec("plant_gain", "plant_gain", plant.get("K"), "FOPDT 过程模型增益"),
        spec("plant_time_constant_s", "plant_time_constant_s", plant.get("T"), "FOPDT 过程模型时间常数", 0.1),
        spec("plant_dead_time_s", "plant_dead_time_s", plant.get("D"), "FOPDT 过程模型纯滞后", 0),
        spec("cv_lower_bound", "cv_lower_bound", (cv.get("ll") or {}).get("value"), "CV 软/硬下限"),
        spec("cv_upper_bound", "cv_upper_bound", (cv.get("hl") or {}).get("value"), "CV 软/硬上限"),
        spec("mv_lower_bound", "mv_lower_bound", mv.get("lower_bound"), "MV 下限"),
        spec("mv_upper_bound", "mv_upper_bound", mv.get("upper_bound"), "MV 上限"),
        spec("dmv_lower_bound", "dmv_lower_bound", mv.get("dmv_lower_bound"), "单步 MV 最大下降量"),
        spec("dmv_upper_bound", "dmv_upper_bound", mv.get("dmv_upper_bound"), "单步 MV 最大上升量"),
        spec("feedback_beta", "feedback_beta", cv.get("feedback_beta"), "测量反馈修正权重", 0, 1),
        spec("move_suppression", "move_suppression", mv.get("move_suppression"), "增大可减少控制动作频繁变化", 0),
    ]
    return [item for item in candidates if item]


def _backfill_control_parameter_candidates(model) -> bool:
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    if str(metrics.get("task_type") or model.category) != "control_optimization":
        return False
    root = Path(str(metrics.get("source_project_root") or "")).resolve()
    artifact_root = ALGORITHM_ARTIFACT_DIR.resolve()
    if not root.is_dir() or artifact_root not in root.parents:
        return False
    candidates = _dmc_control_parameter_candidates(root)
    if not candidates:
        return False
    # Refresh old uploads too: project configuration detection may gain newly
    # required fields, and stale candidates must not hide them in the form.
    if metrics.get("control_parameter_candidates") == candidates:
        return False
    metrics["control_parameter_candidates"] = candidates
    metrics["control_parameter_detection"] = {"source": "project_json", "count": len(candidates)}
    model.metrics = metrics
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(model, "metrics")
    return True


def _source_archive_entry(content: bytes, requested_entry: str = "") -> dict:
    """Safely select one executable Python entry from a ZIP source project."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates: list[str] = []
            runnable_candidates: list[str] = []
            sources: dict[str, str] = {}
            for info in archive.infolist():
                name = _safe_archive_name(info.filename, repair_legacy_zip=True)
                mode = (info.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    raise HTTPException(400, detail=f"源码包不允许软链接: {name}")
                if info.is_dir() or not name.lower().endswith(".py"):
                    continue
                try:
                    source = archive.read(info).decode("utf-8")
                    ast.parse(source, filename=name)
                except (UnicodeDecodeError, SyntaxError) as exc:
                    raise HTTPException(400, detail=f"源码包中的Python文件无效 {name}: {exc}") from exc
                candidates.append(name)
                sources[name] = source
                # 没有约定命名的算法项目，只有存在 __main__ 守卫且不在测试、
                # 报告或工具目录中的脚本，才可作为候选运行入口。
                parts = {part.lower() for part in Path(name).parts}
                excluded_entry_dirs = {"test", "tests", "docs", "report", "reports", "tool", "tools", "__pycache__"}
                if (
                    not (parts & excluded_entry_dirs)
                    and re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", source)
                ):
                    runnable_candidates.append(name)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(400, detail=f"ZIP压缩包损坏: {exc}") from exc

    if not candidates:
        raise HTTPException(400, detail="算法项目压缩包中没有有效的 Python 文件")
    requested = requested_entry.strip().replace("\\", "/")
    if requested:
        requested = _safe_archive_name(requested, repair_legacy_zip=True)
        if requested not in sources:
            # Users may zip either the project directory itself or just its
            # contents. Accept a unique path differing only by that root directory.
            suffix_matches = [
                candidate for candidate in sources
                if candidate.endswith("/" + requested) or requested.endswith("/" + candidate)
            ]
            if len(suffix_matches) == 1:
                requested = suffix_matches[0]
            else:
                raise HTTPException(
                    400,
                    detail="指定的训练入口不存在: " + requested + "；可选: " + ", ".join(sorted(candidates)[:20]),
                )
        entry = requested
    else:
        # 常规项目优先采用约定入口名。工业控制服务常用 App.py；统一按小写匹配，
        # 因此也覆盖 app.py。若没有约定入口，只接受唯一的非测试可执行脚本，
        # 避免把库内部模块、测试或构建工具误当作运行入口。
        priority = {"train.py": 0, "main.py": 1, "run.py": 2, "run_all.py": 3, "app.py": 4, "__main__.py": 5}
        ranked = sorted(candidates, key=lambda item: (priority.get(Path(item).name.lower(), 9), len(Path(item).parts), item))
        if priority.get(Path(ranked[0]).name.lower(), 9) < 9:
            entry = ranked[0]
        elif len(runnable_candidates) == 1:
            entry = runnable_candidates[0]
        elif len(candidates) == 1:
            entry = candidates[0]
        else:
            raise HTTPException(
                400,
                detail=(
                    "无法自动确定训练入口，请填写入口文件路径；可选: "
                    + ", ".join(ranked[:20])
                ),
            )
    return {"entry": entry, "source": sources[entry], "python_files": sorted(candidates)}


def _extract_source_project(content: bytes, destination: Path) -> None:
    """Extract a validated ZIP project while preserving relative imports."""
    root = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist():
            name = _safe_archive_name(info.filename, repair_legacy_zip=True)
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise HTTPException(400, detail=f"源码包不允许软链接: {name}")
            if info.is_dir():
                continue
            target = (root / name).resolve()
            if root not in target.parents:
                raise HTTPException(400, detail=f"源码包包含不安全路径: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))

def _validate_artifact(filename: str, content: bytes) -> dict:
    lower_name = filename.lower()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_ARTIFACT_BYTES:
        raise HTTPException(status_code=413, detail="上传文件不能超过256MB")
    if not lower_name.endswith((".py", ".zip", ".pkl", ".onnx", ".tar.gz", ".tgz")):
        raise HTTPException(
            status_code=400,
            detail="算法商店仅接受 Python源码(.py)、Python项目(.zip)、可继续训练的PKL、ONNX推理模型或Docker部署包(.tar.gz)",
        )

    if content.startswith(b"\x1f\x8b") or lower_name.endswith((".tar.gz", ".tgz")):
        result = _validate_tar(content)
        if result.get("artifact_format") != "docker_bundle":
            raise HTTPException(
                status_code=400,
                detail=".tar.gz在算法商店中必须是包含model.onnx和完整运行文件的Docker部署包",
            )
    elif lower_name.endswith(".zip"):
        result = _validate_zip(content)
    elif lower_name.endswith(".py"):
        try:
            source = content.decode("utf-8")
            ast.parse(source)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise HTTPException(status_code=400, detail=f"Python源码无效: {exc}")
        result = {
            "artifact_format": "python",
            "artifact_kind": "algorithm_source",
            "artifact_label": "Python算法源码",
            "source_code": source,
        }
    elif lower_name.endswith(".onnx"):
        input_names, output_names, input_shapes = [], [], []
        try:
            import onnx
            model = onnx.load_model_from_string(content)
            onnx.checker.check_model(model)
            input_names = [item.name for item in model.graph.input]
            output_names = [item.name for item in model.graph.output]
            input_shapes = []
            for item in model.graph.input:
                shape = []
                for dim in item.type.tensor_type.shape.dim:
                    if dim.dim_value > 0:
                        shape.append(dim.dim_value)
                    elif dim.dim_param:
                        shape.append(dim.dim_param)
                    else:
                        shape.append(None)
                input_shapes.append(shape)
        except ImportError:
            if len(content) < 16:
                raise HTTPException(status_code=400, detail="ONNX文件内容过短")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"ONNX模型校验失败: {exc}")
        result = {
            "artifact_format": "onnx",
            "artifact_kind": "model_artifact",
            "artifact_label": "ONNX模型",
            "onnx_inputs": input_names,
            "onnx_outputs": output_names,
            "onnx_input_shapes": input_shapes,
        }
    elif lower_name.endswith((".pkl", ".joblib")):
        operation_count = 0
        joblib_container = False
        try:
            for _ in pickletools.genops(content):
                operation_count += 1
        except Exception as exc:
            # Joblib会在Pickle元数据中嵌入NumPy原始二进制数组。pickletools
            # 会在数组段停止，但前面的协议和对象结构仍可在不执行代码的情况下校验。
            joblib_container = content.startswith(b"\x80") and operation_count >= 10
            if not joblib_container:
                raise HTTPException(status_code=400, detail=f"PKL结构校验失败: {exc}")
        result = {
            "artifact_format": "joblib" if joblib_container else "pkl",
            "artifact_kind": "model_artifact",
            "artifact_label": "Joblib/PKL模型" if joblib_container else "PKL模型",
            "validation_note": (
                "已识别Joblib中的Pickle元数据与NumPy二进制数据段，未执行反序列化"
                if joblib_container else "已完成无执行的Pickle结构校验"
            ),
        }
    elif lower_name.endswith(".exe") and content.startswith(b"MZ"):
        result = {
            "artifact_format": "exe",
            "artifact_kind": "deployment_bundle",
            "artifact_label": "Windows EXE程序",
            "validation_note": "已验证PE文件头，运行兼容性需在Windows环境测试",
        }
    else:
        raise HTTPException(
            status_code=400,
            detail="不支持的文件格式。算法商店支持 py、pkl 和 onnx",
        )

    return {
        **result,
        "validation_status": "validated",
        "source_filename": filename,
        "source_size": len(content),
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
    }


def _detect_training_capability(db: Session, validation: dict, content: bytes) -> dict:
    """Inspect only artifacts that exactly match a trusted platform model blob."""
    if validation.get("artifact_kind") != "model_artifact":
        return {"artifact_trainable": validation.get("artifact_kind") == "algorithm_source"}
    if validation.get("artifact_format") == "onnx":
        return {
            "artifact_trainable": False,
            "training_capability_reason": "ONNX为推理计算图，不包含平台续训状态",
        }

    trusted_model = _find_trusted_model(db, content)
    if not trusted_model:
        return {
            "artifact_trainable": False,
            "trusted_artifact": False,
            "training_capability_reason": "无法确认文件来自平台可信训练产物，仅开放推理能力",
        }

    try:
        model = joblib.load(io.BytesIO(content))
    except Exception as exc:
        return {
            "artifact_trainable": False,
            "trusted_artifact": True,
            "source_model_id": trusted_model[0],
            "training_capability_reason": f"可信模型加载失败: {exc}",
        }

    capability = {
        "artifact_trainable": False,
        "trusted_artifact": True,
        "source_model_id": trusted_model[0],
        "source_model_framework": trusted_model[2],
        "task_type": trusted_model[1] or validation.get("task_type") or "regression",
    }
    if type(model).__name__ == "PyTorchPredictor" and hasattr(model, "model"):
        capability.update({
            "artifact_trainable": True,
            "resume_training_mode": "fine_tune",
            "runtime_model_type": "mlp",
            "training_adapter": "resume_pytorch",
            "training_capability_label": "可微调",
            "training_capability_reason": "包含可信PyTorch网络与权重，将使用新优化器继续训练",
            "n_features_in": int(getattr(model, "n_features_in_", 0) or 0),
        })
        return capability

    estimator = model
    if hasattr(model, "named_steps"):
        estimator = model.named_steps.get("model") or list(model.named_steps.values())[-1]
    estimator_name = type(estimator).__name__.lower()
    runtime_model_type = (
        "random_forest" if "randomforest" in estimator_name
        else "xgboost" if "xgb" in estimator_name
        else "lightgbm" if "lgbm" in estimator_name
        else None
    )
    if hasattr(estimator, "partial_fit") and runtime_model_type:
        capability.update({
            "artifact_trainable": True,
            "resume_training_mode": "partial_fit",
            "runtime_model_type": runtime_model_type,
            "training_adapter": f"resume_{runtime_model_type}",
            "training_capability_label": "可增量训练",
            "training_capability_reason": "模型实现partial_fit，可使用新数据增量更新",
        })
    elif (
        runtime_model_type
        and hasattr(estimator, "n_estimators")
        and "warm_start" in getattr(estimator, "get_params", lambda: {})()
    ):
        capability.update({
            "artifact_trainable": True,
            "resume_training_mode": "warm_start",
            "runtime_model_type": runtime_model_type,
            "training_adapter": f"resume_{runtime_model_type}",
            "training_capability_label": "可继续训练",
            "training_capability_reason": "模型支持warm_start，将在已有模型上增加训练",
        })
    else:
        capability["training_capability_reason"] = "模型类型未提供安全的继续训练接口"
    return capability


def _find_trusted_model(db: Session, content: bytes):
    digest = hashlib.sha256(content).hexdigest()
    rows = db.execute(text(
        "SELECT id, model_type, framework, model_blob FROM models WHERE model_blob IS NOT NULL"
    )).fetchall()
    for row in rows:
        raw = bytes(row[3]) if row[3] is not None else b""
        if raw and hashlib.sha256(raw).hexdigest() == digest:
            return row
    return None


def _extract_bundle_model(content: bytes) -> Optional[bytes]:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            for member in archive.getmembers():
                if member.isfile() and Path(member.name).name == "model.pkl":
                    source = archive.extractfile(member)
                    return source.read(MAX_ARTIFACT_BYTES + 1) if source else None
    except (tarfile.TarError, OSError):
        return None
    return None


def _register_evaluable_model(
    db: Session,
    algorithm,
    metadata: dict,
    artifact_path: Path,
    content: bytes,
    organization_id: Optional[int],
) -> dict:
    """Register safe model artifacts so the model-evaluation selector can list them."""
    from models.core import Model, ModelStatus, ModelVersion

    artifact_kind = metadata.get("artifact_kind")
    artifact_format = metadata.get("artifact_format")
    model_blob = None
    source_model = None
    framework = metadata.get("source_model_framework") or algorithm.framework
    registered_artifact_path = artifact_path

    if artifact_kind == "model_artifact" and artifact_format in {"pkl", "joblib"}:
        source_model = _find_trusted_model(db, content)
        if not source_model:
            return {"evaluation_ready": False, "evaluation_reason": "外部PKL来源不可确认"}
        model_blob = content
        framework = source_model[2]
    elif artifact_kind == "deployment_bundle" and artifact_format == "docker_bundle":
        embedded_path = Path(str(metadata.get("embedded_model_path") or "")).resolve()
        bundle_root = Path("/app/uploads/docker-bundles").resolve()
        if bundle_root not in embedded_path.parents or not embedded_path.is_file():
            return {"evaluation_ready": False, "evaluation_reason": "Docker包内ONNX未安全提取"}
        registered_artifact_path = embedded_path
        framework = "onnx"
    elif artifact_kind == "deployment_bundle":
        embedded_model = _extract_bundle_model(content)
        source_model = _find_trusted_model(db, embedded_model) if embedded_model else None
        if not source_model:
            return {"evaluation_ready": False, "evaluation_reason": "部署包内模型来源不可确认"}
        model_blob = embedded_model
        framework = source_model[2]
    elif artifact_format == "onnx":
        framework = "onnx"
    else:
        return {"evaluation_ready": False, "evaluation_reason": "该产物类型不支持模型评估"}

    task_type = (
        (source_model[1] if source_model else None)
        or metadata.get("task_type")
        or algorithm.category
        or "regression"
    )
    registered = Model(
        name=algorithm.name,
        description=f"由算法商店封装产物自动注册（algorithm_store_id={algorithm.id}）",
        model_type=task_type,
        framework=framework,
        status=ModelStatus.READY,
        organization_id=organization_id,
        model_blob=model_blob,
    )
    db.add(registered)
    db.flush()
    parameters = {
        "algorithm_store_id": algorithm.id,
        "artifact_format": artifact_format,
        "artifact_kind": artifact_kind,
        "artifact_sha256": metadata.get("artifact_sha256"),
        "source_filename": metadata.get("source_filename"),
        "source_model_id": source_model[0] if source_model else None,
        "task_type": task_type,
        "evaluation_ready": True,
    }
    version = ModelVersion(
        model_id=registered.id,
        version_number=algorithm.version or "v1",
        file_path=str(registered_artifact_path),
        parameters=parameters,
        is_deployed=False,
    )
    db.add(version)
    db.flush()
    return {
        "evaluation_ready": True,
        "registered_model_id": registered.id,
        "registered_model_version_id": version.id,
        "registered_model_framework": framework,
    }


@router.get("/adapters")
def list_adapters():
    """List deterministic adapter mappings used by upload and training forms."""
    from services.training_adapter_registry import list_training_adapters
    return {"items": list_training_adapters()}

@router.get("/models")
def list_algorithm_models(
    category: str = Query(None), search: str = Query(None),
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from models.core import AlgorithmModel
    from services.algorithm_source_service import algorithm_asset_type, ensure_algorithm_source_contract
    changed = False
    for candidate in db.query(AlgorithmModel).all():
        if algorithm_asset_type(candidate) == "uploaded_source":
            ensure_algorithm_source_contract(db, candidate)
            changed = _backfill_control_parameter_candidates(candidate) or changed
    if changed:
        db.commit()
    return get_algorithm_store().list_models(db, category=category, search=search, page=page, size=size)


@router.post("/models")
def create_algorithm_model(req: AlgorithmCreateReq, db: Session = Depends(get_db)):
    """Reject metadata-only records; every custom store item must have a real file."""
    raise HTTPException(400, "算法商店不再创建无文件记录，请上传 .py 或可继续训练的 .pkl 文件")


@router.post(
    "/models/upload",
    dependencies=[Depends(require_permission("training:write"))],
)
async def upload_algorithm_artifact(
    file: UploadFile = File(...),
    name: str = Form(...),
    category: str = Form("custom"),
    task_type: str = Form("regression"),
    algorithm_type: str = Form("random_forest"),
    framework: str = Form("sklearn"),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    tags: str = Form("[]"),
    entry_point: str = Form(""),
    params_schema: str = Form(""),
    dependency_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Persist and validate source archives and packaged deployment artifacts."""
    from models.core import AlgorithmModel

    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="算法名称不能为空")
    original_name = Path(file.filename or "").name
    if not original_name or original_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名无效")
    content = await file.read(MAX_ARTIFACT_BYTES + 1)
    validation = _validate_artifact(original_name, content)
    source_identity = None
    if validation.get("artifact_format") in {"python", "source_archive"}:
        if validation.get("artifact_format") == "source_archive":
            project = _source_archive_entry(content, entry_point)
            validation.update({
                "source_code": project["source"],
                "source_entry_point": project["entry"],
                "source_python_files": project["python_files"],
            })
            control_contract = _read_control_contract_from_archive(content)
            if control_contract:
                validation["control_contract"] = control_contract
                task_type = "control_optimization"
                category = "control_optimization"
            entry_point = project["entry"]
        source_identity = _infer_python_source_identity(validation.get("source_code") or "")
        framework = source_identity["framework"]
        algorithm_type = source_identity["algorithm_type"]
    elif validation.get("artifact_format") == "onnx":
        framework = "onnx"
        algorithm_type = "onnx_inference"
    elif validation.get("artifact_format") == "docker_bundle":
        framework = "docker"
        algorithm_type = "docker_bundle"
    try:
        parsed_tags = json.loads(tags) if tags else []
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="tags必须是JSON数组")
    if not isinstance(parsed_tags, list):
        raise HTTPException(status_code=400, detail="tags必须是JSON数组")

    # A source file can state its real business type more reliably than the
    # upload form default.  For example, scripts that sort by a time column and
    # use TimeSeriesSplit/rolling windows are genuine time-series predictors,
    # even though they execute through the generic user-script runtime.
    if (
        task_type != "control_optimization"
        and source_identity
        and source_identity.get("detected_task_type") == "time_series_forecasting"
    ):
        task_type = "time_series_forecasting"
        category = "time_series_forecasting"

    metadata, spec = _adapter_metadata(task_type, algorithm_type, framework, validation)
    if validation.get("control_contract"):
        metadata["control_contract"] = validation["control_contract"]
    if source_identity:
        for legacy_key in (
            "training_adapter", "runtime_model_type", "runtime_task_type", "split_strategy"
        ):
            metadata.pop(legacy_key, None)
        spec = None
        metadata.update({
            "detected_framework": source_identity["framework"],
            "detected_algorithm_type": source_identity["algorithm_type"],
            "source_detection_confidence": source_identity["confidence"],
            "source_execution_mode": "execute_user_script",
            "detected_task_type": source_identity.get("detected_task_type"),
        })
    if validation.get("artifact_format") in {"python", "source_archive"}:
        from services.algorithm_source_service import extract_python_training_contract
        contract = extract_python_training_contract(validation.get("source_code") or "")
        metadata["source_contract"] = contract
        if not params_schema.strip():
            params_schema = json.dumps(contract["schema"], ensure_ascii=False)
        if validation.get("artifact_format") == "python" and not entry_point.strip() and contract.get("entry_function"):
            entry_point = str(contract["entry_function"])
    capability = _detect_training_capability(db, metadata, content)
    metadata.update(capability)
    if validation.get("artifact_format") in {"pkl", "joblib"}:
        framework = str(capability.get("source_model_framework") or "packaged_model")
        algorithm_type = str(capability.get("runtime_model_type") or "packaged_model")
        task_type = str(capability.get("task_type") or task_type or "regression")
        category = task_type
        metadata.update({
            "framework": framework,
            "algorithm_type": algorithm_type,
            "task_type": task_type,
        })
    # Python 源码由隔离的 UserScriptTrainer 执行，不要求能映射到内置适配器。
    is_trainable = bool(metadata.get("artifact_trainable"))
    metadata["artifact_trainable"] = is_trainable
    metadata["dependency_id"] = dependency_id
    if original_name.lower().endswith(".pkl") and not is_trainable:
        raise HTTPException(
            status_code=400,
            detail=f"该 PKL 无法安全继续训练：{metadata.get('training_capability_reason') or '未识别继续训练能力'}",
        )
    metadata["asset_type"] = (
        "uploaded_source" if validation.get("artifact_format") in {"python", "source_archive"}
        else "inference_model" if original_name.lower().endswith(".onnx")
        else "deployment_bundle" if validation.get("artifact_format") == "docker_bundle"
        else "finetune_model"
    )

    model = AlgorithmModel(
        name=clean_name,
        category=category or task_type,
        description=description,
        algorithm=algorithm_type,
        framework=framework,
        difficulty="intermediate",
        version=version,
        status="published",
        tags=parsed_tags,
        entry_point=entry_point,
        params_schema=params_schema,
        metrics=dict(metadata),
    )
    artifact_path = None
    try:
        db.add(model)
        db.flush()
        artifact_dir = ALGORITHM_ARTIFACT_DIR / str(model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / original_name
        artifact_path.write_bytes(content)
        stored_metadata = {**metadata, "artifact_path": str(artifact_path)}
        if validation.get("artifact_format") == "source_archive":
            source_root = artifact_dir / "project"
            _extract_source_project(content, source_root)
            stored_metadata.update({
                "source_project_root": str(source_root),
                "source_entry_point": validation["source_entry_point"],
                "source_python_files": validation["source_python_files"],
            })
            control_candidates = _dmc_control_parameter_candidates(source_root)
            if control_candidates:
                stored_metadata["control_parameter_candidates"] = control_candidates
                stored_metadata["control_parameter_detection"] = {"source": "project_json", "count": len(control_candidates)}
        if validation.get("artifact_format") == "docker_bundle":
            from services.docker_bundle_service import prepare_docker_bundle
            try:
                stored_metadata.update(prepare_docker_bundle(content, model.id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        model.metrics = stored_metadata
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(model, "metrics")
        registration = _register_evaluable_model(
            db,
            model,
            stored_metadata,
            artifact_path,
            content,
            current_user.get("organization_id"),
        )
        stored_metadata.update(registration)
        model.metrics = dict(stored_metadata)
        flag_modified(model, "metrics")
        db.commit()
        db.refresh(model)
        metadata = stored_metadata
    except Exception:
        db.rollback()
        if artifact_path and artifact_path.exists():
            artifact_path.unlink()
        raise

    return {
        "id": model.id,
        "name": model.name,
        "message": "文件上传并校验成功",
        "artifact": {
            key: value for key, value in metadata.items()
            if key.startswith("artifact_") or key in {
                "validation_status", "validation_note", "source_filename",
                "source_size", "archive_files", "onnx_inputs", "onnx_outputs",
                "onnx_input_shapes",
                "docker_context_path", "embedded_model_path",
                "docker_build_status", "runtime_files_regenerated",
            }
        },
        "parameter_detection": {
            "training_candidates": len((metadata.get("source_contract") or {}).get("parameter_candidates") or []),
            "control_candidates": len(metadata.get("control_parameter_candidates") or []),
        },
        "training_adapter": None if source_identity else (
            spec.to_dict() if spec and is_trainable else {
                "id": metadata.get("training_adapter"),
                "name": metadata.get("training_capability_label"),
                "mode": metadata.get("resume_training_mode"),
            } if is_trainable else None
        ),
        "execution_mode": "execute_user_script" if source_identity else metadata.get("resume_training_mode"),
        "trainable": is_trainable,
        "evaluation_ready": bool(metadata.get("evaluation_ready")),
        "registered_model_id": metadata.get("registered_model_id"),
    }


@router.put("/models/{model_id}")
def update_algorithm_model(
    model_id: int,
    req: AlgorithmUpdateReq,
    db: Session = Depends(get_db),
):
    """Update an algorithm and deterministically recompute its adapter."""
    from models.core import AlgorithmModel

    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "算法不存在")
    from services.algorithm_source_service import algorithm_asset_type
    if algorithm_asset_type(model) == "builtin":
        raise HTTPException(403, "内置算法定义不可修改；请在沙箱中另存工作区副本")
    data = req.dict(exclude_unset=True)
    for field in (
        "name", "category", "framework", "description", "version", "tags",
        "entry_point", "params_schema",
    ):
        if field in data:
            setattr(model, field, data[field])

    current_meta = model.metrics if isinstance(model.metrics, dict) else {}
    task_type = data.get("task_type") or current_meta.get("task_type") or model.category
    algorithm_type = (
        data.get("algorithm_type") or current_meta.get("algorithm_type")
        or model.algorithm or model.framework
    )
    model.algorithm = algorithm_type
    merged_metrics = dict(current_meta)
    if data.get("metrics"):
        merged_metrics.update(data["metrics"])
    model.metrics, spec = _adapter_metadata(
        task_type, algorithm_type, model.framework, merged_metrics
    )
    db.commit(); db.refresh(model)
    return {
        **model.to_dict(),
        "training_adapter": spec.to_dict() if spec else None,
        "trainable": spec is not None,
    }

@router.get("/models/{model_id}")
def get_algorithm_model(model_id: int, db: Session = Depends(get_db)):
    m = get_algorithm_store().get_model(db, model_id)
    if not m: raise HTTPException(status_code=404, detail="方法不存在")
    return m

MANUAL_ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".md", ".ppt", ".pptx", ".xls", ".xlsx"}
MAX_MANUAL_BYTES = 50 * 1024 * 1024


@router.post(
    "/models/{model_id}/manual",
    dependencies=[Depends(require_permission("training:write"))],
)
async def upload_algorithm_manual(
    model_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Attach one replaceable user manual to an algorithm-store item."""
    from models.core import AlgorithmModel

    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "算法不存在")
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or filename in {".", ".."} or suffix not in MANUAL_ALLOWED_SUFFIXES:
        raise HTTPException(400, "说明书仅支持 PDF、Word、文本、Markdown、PPT 或 Excel 文件")
    content = await file.read(MAX_MANUAL_BYTES + 1)
    if not content:
        raise HTTPException(400, "说明书文件为空")
    if len(content) > MAX_MANUAL_BYTES:
        raise HTTPException(413, "说明书不能超过 50MB")

    manual_dir = ALGORITHM_ARTIFACT_DIR / str(model_id) / "manual"
    manual_dir.mkdir(parents=True, exist_ok=True)
    # A manual is a replaceable companion document, never a source artifact.
    for existing in manual_dir.iterdir():
        if existing.is_file():
            existing.unlink()
    target = manual_dir / filename
    temp = manual_dir / f".{filename}.uploading"
    temp.write_bytes(content)
    temp.replace(target)

    metrics = dict(model.metrics or {})
    metrics["manual"] = {
        "filename": filename,
        "path": str(target.relative_to(ALGORITHM_ARTIFACT_DIR)),
        "size_bytes": len(content),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    model.metrics = metrics
    db.commit()
    return {"manual": metrics["manual"]}


@router.get("/models/{model_id}/manual")
def download_algorithm_manual(
    model_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Download the manual currently attached to an algorithm."""
    from models.core import AlgorithmModel

    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "算法不存在")
    manual = (model.metrics or {}).get("manual") if isinstance(model.metrics, dict) else None
    if not isinstance(manual, dict):
        raise HTTPException(404, "该算法暂未上传说明书")
    relative = Path(str(manual.get("path") or ""))
    root = ALGORITHM_ARTIFACT_DIR.resolve()
    target = (root / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or root not in target.parents or not target.is_file():
        raise HTTPException(404, "说明书文件不存在")
    return FileResponse(target, filename=str(manual.get("filename") or target.name))


@router.get("/models/{model_id}/download")
def download_algorithm_model(model_id: int, db: Session = Depends(get_db)):
    """Download the real uploaded artifact or saved source; never masquerade metadata as code."""
    model = get_algorithm_store().get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="算法不存在")
    metrics = model.get("metrics") if isinstance(model.get("metrics"), dict) else {}
    artifact_path = metrics.get("artifact_path")
    if not artifact_path and metrics.get("source_filename"):
        candidate = ALGORITHM_ARTIFACT_DIR / str(model_id) / Path(metrics["source_filename"]).name
        if candidate.is_file():
            artifact_path = str(candidate)
    if artifact_path:
        path = Path(artifact_path).resolve()
        root = ALGORITHM_ARTIFACT_DIR.resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="算法文件不存在")
        return FileResponse(
            str(path),
            media_type="application/octet-stream",
            filename=metrics.get("source_filename") or path.name,
        )
    inline_code = metrics.get("code")
    if isinstance(inline_code, str) and inline_code.strip():
        filename = f"algorithm-{model_id}.py"
        return Response(
            content=inline_code.encode("utf-8"),
            media_type="text/x-python; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    editable_root = metrics.get("editable_source_root")
    editable_entry = str(metrics.get("editable_entry_point") or "main.py")
    if editable_root:
        root = Path(editable_root).resolve()
        path = (root / editable_entry).resolve()
        artifact_root = ALGORITHM_ARTIFACT_DIR.resolve()
        if artifact_root in root.parents and root in path.parents and path.is_file():
            return FileResponse(
                str(path),
                media_type="text/x-python" if path.suffix.lower() == ".py" else "text/plain",
                filename=path.name,
            )
    raise HTTPException(
        status_code=404,
        detail="该算法没有可下载的上传文件或已保存源码；元数据不会再伪装成代码文件",
    )


class ArtifactPredictReq(BaseModel):
    X: List[List[float]]


@router.post(
    "/models/{model_id}/predict",
    dependencies=[Depends(require_permission("training:read"))],
)
def predict_algorithm_artifact(
    model_id: int,
    req: ArtifactPredictReq,
    db: Session = Depends(get_db),
):
    """Run a validated ONNX artifact directly from the algorithm store."""
    from models.core import AlgorithmModel
    from services.onnx_exporter import get_onnx_inference_service

    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not algorithm:
        raise HTTPException(status_code=404, detail="算法不存在")
    metrics = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    if metrics.get("artifact_format") not in {"onnx", "docker_bundle"}:
        raise HTTPException(status_code=400, detail="该条目不是ONNX或Docker推理模型")

    raw_path = (
        metrics.get("embedded_model_path")
        if metrics.get("artifact_format") == "docker_bundle"
        else metrics.get("artifact_path")
    )
    path = Path(str(raw_path or "")).resolve()
    allowed_roots = [ALGORITHM_ARTIFACT_DIR.resolve(), Path("/app/uploads/docker-bundles").resolve()]
    if not any(root in path.parents for root in allowed_roots) or not path.is_file():
        raise HTTPException(status_code=404, detail="ONNX 模型文件不存在")
    if not req.X or any(not row for row in req.X):
        raise HTTPException(status_code=400, detail="请至少提供一行非空测试数据")
    widths = {len(row) for row in req.X}
    if len(widths) != 1:
        raise HTTPException(status_code=400, detail="每行测试数据的特征数量必须一致")
    values = np.asarray(req.X, dtype=np.float32)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise HTTPException(status_code=400, detail="测试数据必须是由有限数值组成的二维数组")

    try:
        service = get_onnx_inference_service()
        session = service.load_model(str(path), f"algorithm-store-{model_id}")
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"当前直接运行仅支持单输入ONNX模型，检测到 {len(inputs)} 个输入",
            )
        expected = inputs[0].shape[-1] if inputs[0].shape else None
        if isinstance(expected, int) and expected > 0 and values.shape[1] != expected:
            raise HTTPException(
                status_code=400,
                detail=f"输入有 {values.shape[1]} 个特征，但模型需要 {expected} 个",
            )
        predictions = np.asarray(service.predict(session, values))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ONNX运行失败: {exc}") from exc

    return {
        "algorithm_id": model_id,
        "registered_model_id": metrics.get("registered_model_id"),
        "model_name": algorithm.name,
        "input_name": inputs[0].name,
        "output_name": session.get_outputs()[0].name,
        "n_samples": len(req.X),
        "input_samples": req.X,
        "predictions": (
            predictions.tolist()
            if predictions.ndim > 0
            else [predictions.item()]
        ),
    }


def _docker_builder_call(method: str, path: str, payload: Optional[dict] = None, timeout: int = 30) -> dict:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    base_url = os.getenv("DOCKER_BUILDER_URL", "http://docker-builder:8010").rstrip("/")
    token = os.getenv("JWT_SECRET_KEY", "")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={
            "X-Builder-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = str(exc)
        raise HTTPException(status_code=exc.code, detail=detail or "Docker构建器请求失败") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail=f"Docker构建器不可用: {exc}") from exc


def _get_docker_bundle(db: Session, model_id: int):
    from models.core import AlgorithmModel

    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not algorithm:
        raise HTTPException(status_code=404, detail="算法不存在")
    metrics = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    if metrics.get("artifact_format") != "docker_bundle":
        raise HTTPException(status_code=400, detail="该条目不是Docker部署包")
    return algorithm, metrics


def _save_docker_job(db: Session, algorithm, metrics: dict, job: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    updated = dict(metrics)
    updated.update({
        "docker_build_job_id": job.get("job_id"),
        "docker_build_status": job.get("status"),
        "docker_build_progress": job.get("progress", 0),
        "docker_build_stage": job.get("stage"),
        "docker_image_name": job.get("image_name"),
        "docker_image_id": job.get("image_id"),
        "docker_container_name": job.get("container_name"),
        "docker_service_url": job.get("service_url"),
        "docker_build_error": job.get("error"),
    })
    algorithm.metrics = updated
    flag_modified(algorithm, "metrics")
    db.commit()


@router.post(
    "/models/{model_id}/docker/build",
    dependencies=[Depends(require_permission("deployment:write"))],
)
def build_docker_bundle(model_id: int, db: Session = Depends(get_db)):
    algorithm, metrics = _get_docker_bundle(db, model_id)
    job = _docker_builder_call("POST", "/jobs", {"algorithm_id": model_id})
    _save_docker_job(db, algorithm, metrics, job)
    return job


@router.get(
    "/models/{model_id}/docker/status",
    dependencies=[Depends(require_permission("deployment:read"))],
)
def docker_bundle_status(model_id: int, db: Session = Depends(get_db)):
    algorithm, metrics = _get_docker_bundle(db, model_id)
    job_id = metrics.get("docker_build_job_id")
    if not job_id:
        return {
            "algorithm_id": model_id,
            "status": "not_built",
            "stage": "尚未构建",
            "progress": 0,
            "logs": [],
        }
    job = _docker_builder_call("GET", f"/jobs/{job_id}")
    _save_docker_job(db, algorithm, metrics, job)
    return job


@router.post(
    "/models/{model_id}/docker/run",
    dependencies=[Depends(require_permission("deployment:write"))],
)
def run_docker_bundle(model_id: int, db: Session = Depends(get_db)):
    from urllib.error import URLError
    from urllib.request import urlopen

    algorithm, metrics = _get_docker_bundle(db, model_id)
    job_id = metrics.get("docker_build_job_id")
    if not job_id:
        raise HTTPException(status_code=409, detail="请先构建Docker镜像")
    job = _docker_builder_call("POST", f"/jobs/{job_id}/run")
    _save_docker_job(db, algorithm, metrics, job)
    health = None
    last_error = None
    for _ in range(30):
        try:
            with urlopen(f"{job['service_url']}/health", timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))
                break
        except (URLError, OSError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    if health is None:
        raise HTTPException(status_code=502, detail=f"Docker容器已启动但健康检查失败: {last_error}")
    return {**job, "health": health}


@router.post(
    "/models/{model_id}/docker/predict",
    dependencies=[Depends(require_permission("deployment:write"))],
)
def predict_docker_bundle(model_id: int, req: ArtifactPredictReq, db: Session = Depends(get_db)):
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    _, metrics = _get_docker_bundle(db, model_id)
    service_url = metrics.get("docker_service_url")
    if metrics.get("docker_build_status") != "running" or not service_url:
        raise HTTPException(status_code=409, detail="Docker临时容器尚未运行")
    started = time.perf_counter()
    request = Request(
        f"{service_url}/predict",
        data=json.dumps({"features": req.X}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Docker容器推理失败: {exc}") from exc
    return {
        "algorithm_id": model_id,
        "n_samples": len(req.X),
        "input_samples": req.X,
        "predictions": result.get("prediction"),
        "outputs": result.get("outputs"),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


@router.post(
    "/models/{model_id}/docker/stop",
    dependencies=[Depends(require_permission("deployment:write"))],
)
def stop_docker_bundle(model_id: int, db: Session = Depends(get_db)):
    algorithm, metrics = _get_docker_bundle(db, model_id)
    job_id = metrics.get("docker_build_job_id")
    if not job_id:
        raise HTTPException(status_code=409, detail="没有Docker构建任务")
    job = _docker_builder_call("POST", f"/jobs/{job_id}/stop")
    _save_docker_job(db, algorithm, metrics, job)
    return job

@router.get("/models/{model_id}/compatible-datasets")
def get_compatible_datasets(
    model_id: int,
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    datasets = get_algorithm_store().get_compatible_datasets(db, model_id, page=page, size=size)
    return datasets

class TrainReq(BaseModel):
    dataset_id: int
    name: Optional[str] = None
    target_column: str = ""
    feature_columns: List[str] = Field(default_factory=list)
    hyperparams: Dict[str, Any] = Field(default_factory=dict)
    hyperparameter_source: Literal["default", "manual", "file"] = "default"
    source_execution_mode: Optional[str] = None  # "execute_user_script" = 执行用户上传的脚本
    model_folder_id: Optional[int] = None


class ControlRunReq(BaseModel):
    """Configuration for an uploaded control/optimisation project run."""
    # 数据集回放沿用已有完整过程数据；参数化仿真由平台生成虚拟过程轨迹，
    # 因而不应强制要求用户先上传一个并不会被使用的数据集。
    simulation_mode: Literal["dataset_replay", "parameterized", "realtime_stream"] = "dataset_replay"
    dataset_id: Optional[int] = None
    name: Optional[str] = None
    time_column: str = ""
    state_columns: List[str] = Field(default_factory=list)
    control_columns: List[str] = Field(default_factory=list)
    disturbance_columns: List[str] = Field(default_factory=list)
    setpoint: Optional[float] = None
    constraints: Dict[str, Any] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    controller_config: Optional[Dict[str, Any]] = None
    realtime: Dict[str, Any] = Field(default_factory=dict)

@router.post(
    "/models/{model_id}/train",
    dependencies=[Depends(require_permission("training:write"))],
)
def train_with_method(
    model_id: int,
    req: TrainReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Train an algorithm-store model through the same safe adapter path as path 1."""
    from api.real_training import DatasetJobTrainingRequest, create_training_job_from_dataset
    from models.core import AlgorithmModel
    from services.algorithm_source_service import algorithm_asset_type
    from services.training_adapter_registry import normalize_task_type, resolve_algorithm_model

    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not algorithm:
        raise HTTPException(status_code=404, detail="算法不存在")
    from services.algorithm_source_service import ensure_algorithm_source_contract
    source_contract = ensure_algorithm_source_contract(db, algorithm)
    metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    asset_type = algorithm_asset_type(algorithm)
    if str(metadata.get("task_type") or algorithm.category) == "control_optimization":
        raise HTTPException(
            status_code=409,
            detail="控制优化算法不走监督训练；请在算法商店中使用“运行控制仿真”。",
        )
    is_resume = metadata.get("artifact_kind") == "model_artifact"
    if is_resume:
        if not metadata.get("artifact_trainable"):
            raise HTTPException(status_code=400, detail="该封装模型仅支持推理，不能继续训练")
        adapter = None
        task_type = metadata.get("task_type") or "regression"
        runtime_model_type = metadata.get("runtime_model_type")
        if not runtime_model_type:
            raise HTTPException(status_code=400, detail="封装模型缺少继续训练运行类型")
        source_execution_mode = None
    elif asset_type == "uploaded_source":
        artifact_path = Path(str(metadata.get("artifact_path") or ""))
        artifact_format = str(metadata.get("artifact_format") or "")
        if not artifact_path.is_file() or artifact_format not in {"python", "source_archive"}:
            raise HTTPException(status_code=400, detail="上传算法缺少可执行的 Python 源码或算法项目包")
        adapter = None
        business_task_type = normalize_task_type(metadata.get("task_type") or algorithm.category)
        # 上传源码仍由 user_script 执行，但必须保留其业务任务类型供训练、模型管理和大盘展示使用。
        task_type = business_task_type if business_task_type in {"classification", "regression", "time_series_forecasting"} else "regression"
        runtime_model_type = "user_script"
        source_execution_mode = "execute_user_script"
    else:
        adapter = resolve_algorithm_model(algorithm)
        if not adapter:
            raise HTTPException(
                status_code=400,
                detail="该内置算法无法匹配训练适配器，请补充任务类型和算法类型。",
            )
        task_type = adapter.task_type
        runtime_model_type = adapter.runtime_model_type
        source_execution_mode = None

    source_data_contract = (
        source_contract.get("data_contract")
        if isinstance(source_contract, dict) and isinstance(source_contract.get("data_contract"), dict)
        else {}
    )
    effective_target = req.target_column.strip()
    effective_features = list(req.feature_columns)
    if asset_type == "uploaded_source":
        effective_target = effective_target or str(source_data_contract.get("target_column") or "").strip()
        if not effective_features and isinstance(source_data_contract.get("feature_columns"), list):
            effective_features = list(source_data_contract["feature_columns"])

    if not effective_target:
        raise HTTPException(status_code=400, detail="请选择预测目标列")
    if effective_target in effective_features:
        raise HTTPException(status_code=400, detail="预测目标列不能同时作为特征列")
    if len(effective_features) != len(set(effective_features)):
        raise HTTPException(status_code=400, detail="特征列不能重复")

    algorithm.status = "training"
    db.commit()
    try:
        result = create_training_job_from_dataset(
            DatasetJobTrainingRequest(
                name=req.name or f"{algorithm.name}-训练",
                task_type=task_type,
                model_type=runtime_model_type,
                dataset_id=req.dataset_id,
                target_column=effective_target,
                feature_columns=effective_features or None,
                hyperparams=json.dumps(req.hyperparams, ensure_ascii=False),
                hyperparameter_source=req.hyperparameter_source,
                source_execution_mode=source_execution_mode,
                algorithm_id=None if is_resume else model_id,
                resume_algorithm_id=model_id if is_resume else None,
                model_folder_id=req.model_folder_id,
            ),
            db=db,
            current_user=current_user,
        )
        return {
            "message": "继续训练已启动" if is_resume else "训练已启动",
            "training_mode": metadata.get("resume_training_mode") if is_resume else (
                "execute_uploaded_source" if source_execution_mode else "builtin_adapter"
            ),
            "result": result,
        }
    except Exception:
        db.rollback()
        failed_algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
        if failed_algorithm:
            failed_algorithm.status = "failed"
            db.commit()
        raise


@router.get("/models/{model_id}/control-config-template")
def download_control_config_template(model_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    from models.core import AlgorithmModel
    from services.control_project_runtime import ControlProjectRuntimeError, get_control_config_template
    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not algorithm:
        raise HTTPException(404, "算法不存在")
    try:
        template = get_control_config_template(algorithm.metrics if isinstance(algorithm.metrics, dict) else {})
    except ControlProjectRuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content=json.dumps(template["config"], ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="control-config-template.json"'})


@router.post(
    "/models/{model_id}/control-runs",
    dependencies=[Depends(require_permission("training:write"))],
)
def run_control_algorithm_project(
    model_id: int,
    req: ControlRunReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Run an uploaded MPC/FOPDT-like source project with data replay or a virtual process."""
    from api.tenant_access import assert_organization_access
    from models.core import AlgorithmModel, Dataset
    from services.algorithm_source_service import algorithm_asset_type
    from services.control_project_runtime import ControlProjectRuntimeError, run_control_project

    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if not algorithm:
        raise HTTPException(404, "算法不存在")
    metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    if algorithm_asset_type(algorithm) != "uploaded_source" or str(metadata.get("task_type") or algorithm.category) != "control_optimization":
        raise HTTPException(409, "只有上传的控制优化算法项目可以运行控制仿真")
    simulation_mode = str(req.simulation_mode or "dataset_replay")
    dataset = None
    if simulation_mode == "dataset_replay":
        if not req.dataset_id:
            raise HTTPException(400, "数据集回放方式必须选择训练数据集")
        dataset = db.query(Dataset).filter(Dataset.id == req.dataset_id).first()
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        assert_organization_access(dataset.organization_id, current_user)
    if simulation_mode == "realtime_stream":
        realtime_config = req.realtime or {}
        transport = str(realtime_config.get("transport") or "mqtt").strip().lower()
        if transport not in {"mqtt", "package_mqtt", "zmq"}:
            raise HTTPException(400, "实时接入协议仅支持 MQTT 或 ZMQ")
        if transport == "zmq":
            endpoint = str(realtime_config.get("input_endpoint") or "").strip()
            if not endpoint.startswith("tcp://"):
                raise HTTPException(400, "ZMQ 实时接入必须填写 tcp:// 格式的输入地址")
            if bool(realtime_config.get("publish_outputs")) and not str(realtime_config.get("output_endpoint") or "").strip().startswith("tcp://"):
                raise HTTPException(400, "启用控制输出发布时必须填写 tcp:// 格式的输出地址")
        elif bool(realtime_config.get("publish_outputs")):
            raise HTTPException(400, "MQTT 实时接入当前仅订阅数据并保存结果，不会向现场发布控制指令")
        try:
            duration_seconds = int(realtime_config.get("duration_seconds") or 60)
        except (TypeError, ValueError):
            raise HTTPException(400, "实时运行时长必须是秒数")
        # 请求本身和后端控制运行的最大超时均为 30 分钟，留出清理和结果归档余量。
        if not 5 <= duration_seconds <= 1500:
            raise HTTPException(400, "实时运行时长需在 5 秒到 25 分钟之间")
    control_contract = metadata.get("control_contract") if isinstance(metadata.get("control_contract"), dict) else None
    if control_contract and simulation_mode != "realtime_stream":
        _validate_control_run_parameters(control_contract, simulation_mode, req.parameters)
    config = {
        "name": (req.name or f"{algorithm.name}-控制运行").strip(),
        "simulation_mode": simulation_mode,
        "time_column": req.time_column.strip(),
        "state_columns": list(dict.fromkeys(item.strip() for item in req.state_columns if item.strip())),
        "control_columns": list(dict.fromkeys(item.strip() for item in req.control_columns if item.strip())),
        "disturbance_columns": list(dict.fromkeys(item.strip() for item in req.disturbance_columns if item.strip())),
        "setpoint": req.setpoint,
        "constraints": req.constraints,
        "parameters": req.parameters,
        "controller_config": req.controller_config,
        "realtime": dict(req.realtime or {}),
    }
    try:
        return run_control_project(
            algorithm_id=model_id,
            algorithm_name=algorithm.name,
            metadata=metadata,
            dataset=dataset,
            config=config,
            organization_id=current_user.get("organization_id"),
        )
    except ControlProjectRuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/control-runs")
def list_all_control_runs(
    current_user: dict = Depends(require_auth),
):
    """List all control simulation records visible to the current tenant."""
    from api.tenant_access import is_admin
    from services.control_project_runtime import list_runs
    return {"items": list_runs(None, current_user.get("organization_id"), is_admin=is_admin(current_user))}


@router.delete(
    "/control-runs/{run_id}",
    dependencies=[Depends(require_permission("training:write"))],
)
def delete_control_run(
    run_id: str,
    current_user: dict = Depends(require_auth),
):
    from api.tenant_access import is_admin
    from services.control_project_runtime import ControlProjectRuntimeError, delete_run
    try:
        delete_run(run_id, current_user.get("organization_id"), is_admin=is_admin(current_user))
    except ControlProjectRuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"message": "控制仿真任务已删除"}


@router.get("/models/{model_id}/control-runs")
def list_control_algorithm_runs(
    model_id: int,
    current_user: dict = Depends(require_auth),
):
    from api.tenant_access import is_admin
    from services.control_project_runtime import list_runs
    return {"items": list_runs(model_id, current_user.get("organization_id"), is_admin=is_admin(current_user))}


@router.get("/models/{model_id}/control-runs/{run_id}/outputs/{output_index}")
def download_control_algorithm_output_by_index(
    model_id: int,
    run_id: str,
    output_index: int,
    current_user: dict = Depends(require_auth),
):
    """Download a recorded output by index, avoiding archive-path encoding issues."""
    from api.tenant_access import is_admin
    from services.control_project_runtime import CONTROL_RUNS_ROOT, ControlProjectRuntimeError, get_run
    try:
        record = get_run(run_id, current_user.get("organization_id"), is_admin=is_admin(current_user))
    except ControlProjectRuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    if int(record.get("algorithm_id") or 0) != model_id:
        raise HTTPException(404, "控制运行记录不存在")
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    if output_index < 0 or output_index >= len(outputs):
        raise HTTPException(404, "控制运行输出不存在")
    item = outputs[output_index] if isinstance(outputs[output_index], dict) else {}
    relative = Path(str(item.get("path") or ""))
    if relative.is_absolute() or ".." in relative.parts or not str(relative).startswith("outputs/"):
        raise HTTPException(400, "输出文件路径无效")
    target = (CONTROL_RUNS_ROOT / run_id / relative).resolve()
    run_root = (CONTROL_RUNS_ROOT / run_id).resolve()
    if run_root not in target.parents or not target.is_file():
        raise HTTPException(404, "输出文件不存在")
    return FileResponse(target, filename=str(item.get("name") or target.name))


@router.get("/models/{model_id}/control-runs/{run_id}/logs/{stream_name}")
def get_control_algorithm_run_log(
    model_id: int,
    run_id: str,
    stream_name: Literal["stdout", "stderr", "runtime"],
    current_user: dict = Depends(require_auth),
):
    """Return the complete persisted process log for troubleshooting."""
    from api.tenant_access import is_admin
    from services.control_project_runtime import CONTROL_RUNS_ROOT, ControlProjectRuntimeError, get_run
    try:
        record = get_run(run_id, current_user.get("organization_id"), is_admin=is_admin(current_user))
    except ControlProjectRuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    if int(record.get("algorithm_id") or 0) != model_id:
        raise HTTPException(404, "控制运行记录不存在")
    run_root = (CONTROL_RUNS_ROOT / run_id).resolve()
    target = (run_root / f"{stream_name}.log").resolve()
    if run_root not in target.parents:
        raise HTTPException(400, "运行日志路径无效")
    if target.is_file():
        return Response(target.read_bytes(), media_type="text/plain; charset=utf-8")
    # Runs created before the merged runtime log was introduced still retain
    # stdout/stderr.  Present them as one readable stream instead of making
    # historical diagnostics disappear after the platform upgrade.
    if stream_name == "runtime":
        chunks: list[bytes] = []
        for label, name in ((b"=== stdout ===\n", "stdout.log"), (b"\n=== stderr / driver diagnostics ===\n", "stderr.log")):
            source = (run_root / name).resolve()
            if run_root in source.parents and source.is_file():
                chunks.extend((label, source.read_bytes(), b"\n"))
        if chunks:
            return Response(b"".join(chunks), media_type="text/plain; charset=utf-8")
    raise HTTPException(404, "完整运行日志不存在")


@router.get("/models/{model_id}/control-runs/{run_id}/files/{file_path:path}")
def download_control_algorithm_output(
    model_id: int,
    run_id: str,
    file_path: str,
    current_user: dict = Depends(require_auth),
):
    from api.tenant_access import is_admin
    from services.control_project_runtime import CONTROL_RUNS_ROOT, ControlProjectRuntimeError, get_run
    try:
        record = get_run(run_id, current_user.get("organization_id"), is_admin=is_admin(current_user))
    except ControlProjectRuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    if int(record.get("algorithm_id") or 0) != model_id:
        raise HTTPException(404, "控制运行记录不存在")
    relative = Path(file_path)
    if relative.is_absolute() or ".." in relative.parts or not str(relative).startswith("outputs/"):
        raise HTTPException(400, "输出文件路径无效")
    target = (CONTROL_RUNS_ROOT / run_id / relative).resolve()
    run_root = (CONTROL_RUNS_ROOT / run_id).resolve()
    if run_root not in target.parents or not target.is_file():
        raise HTTPException(404, "输出文件不存在")
    return FileResponse(target, filename=target.name)


@router.delete("/models/{model_id}")
def delete_algorithm_model(model_id: int, db: Session = Depends(get_db)):
    """删除算法模型（预设方法从内存移除，自定义方法从数据库删除）"""
    from models.core import AlgorithmModel
    from services.algorithm_source_service import algorithm_asset_type
    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == model_id).first()
    if model and algorithm_asset_type(model) == "builtin":
        raise HTTPException(status_code=403, detail="内置算法不可删除")
    try:
        metrics = model.metrics if model and isinstance(model.metrics, dict) else {}
        if metrics.get("artifact_format") == "docker_bundle":
            try:
                _docker_builder_call("DELETE", f"/algorithms/{model_id}")
            except HTTPException:
                from services.docker_bundle_service import remove_docker_bundle
                remove_docker_bundle(model_id)
        result = get_algorithm_store().delete_method(db, model_id)
        artifact_dir = ALGORITHM_ARTIFACT_DIR / str(model_id)
        if artifact_dir.is_dir():
            shutil.rmtree(artifact_dir)
        return {"message": "删除成功", "result": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
@router.get("/categories")
def list_categories():
    store = get_algorithm_store()
    return {"total": len(store.get_categories()), "items": store.get_categories()}


class PromoteReq(BaseModel):
    name: str
    algorithm: str
    framework: str = "Scikit-learn"
    model_type: str = "random_forest"
    task_type: str = "regression"
    target_col: str = ""
    feature_cols: list = []
    metrics: dict = {}
    hyperparameters: dict = {}
    dataset_name: str = ""

@router.post("/promote")
def promote_to_store(req: PromoteReq, db: Session = Depends(get_db)):
    """Deprecated: AutoML results are experiment selection records, not algorithms."""
    raise HTTPException(status_code=410, detail="AutoML 结果保存在选型记录中，不能创建重复算法")

@router.get("/recommend")
def recommend(task: str = Query(None), category: str = Query(None), db: Session = Depends(get_db)):
    return get_algorithm_store().recommend(db, task, category)

class PublishReq(BaseModel):
    pass

@router.post("/models/{model_id}/publish")
def publish_model(model_id: int, db: Session = Depends(get_db)):
    """发布训练好的算法到模型管理"""
    try:
        result = get_algorithm_store().publish_method(db, model_id)
        return {"message": "发布成功", "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/models/{model_id}/unpublish")
def unpublish_model(model_id: int, db: Session = Depends(get_db)):
    """从模型管理下架"""
    try:
        result = get_algorithm_store().unpublish_method(db, model_id)
        return {"message": "下架成功", "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PublishFromCodeReq(BaseModel):
    name: str
    description: str = ""
    code: str
    language: str = "python"
    filename: str = "main.py"
    task_type: str = "regression"
    algorithm_type: str = "mlp"
    framework: str = "pytorch"
    version: str = "1.0.0"
    tags: List[str] = Field(default_factory=list)
    dependency_id: Optional[int] = None
    source_origin: str = "sandbox"


@router.post(
    "/publish-from-code",
    dependencies=[Depends(require_permission("training:write"))],
)
def publish_from_code(
    req: PublishFromCodeReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Save the current sandbox Python source as a real algorithm-store artifact."""
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="算法名称不能为空")
    if not req.code or not req.code.strip():
        raise HTTPException(status_code=400, detail="当前源码为空")
    if req.language.lower() != "python":
        raise HTTPException(status_code=400, detail="目前只支持将 Python 源码保存为算法")
    filename = Path(req.filename or "main.py").name
    if not filename.lower().endswith(".py"):
        raise HTTPException(status_code=400, detail="算法入口文件必须是 .py 文件")
    content = req.code.encode("utf-8")
    validation = _validate_artifact(filename, content)
    metadata, spec = _adapter_metadata(
        req.task_type,
        req.algorithm_type,
        req.framework,
        validation,
    )
    is_trainable = spec is not None
    metadata.update({
        "asset_type": "uploaded_source",
        "artifact_trainable": is_trainable,
        "dependency_id": req.dependency_id,
        "source_origin": req.source_origin,
    })
    if not is_trainable:
        metadata["training_capability_reason"] = "生成源码尚未匹配平台训练适配器，可在沙箱中编辑和运行"

    from models.core import AlgorithmModel
    from sqlalchemy.orm.attributes import flag_modified

    clean_tags = list(dict.fromkeys([
        str(tag).strip() for tag in req.tags if str(tag).strip()
    ]))
    model = AlgorithmModel(
        name=req.name.strip(),
        category=req.task_type,
        description=(req.description or "保存的 Python 算法源码"),
        algorithm=req.algorithm_type,
        framework=req.framework,
        difficulty="intermediate",
        version=req.version.strip() or "1.0.0",
        status="published",
        tags=clean_tags,
        entry_point=filename,
        use_case="由代码生成器创建" if req.source_origin == "code_generator" else "由沙箱实验室创建",
        metrics=dict(metadata),
    )
    artifact_path = None
    try:
        db.add(model)
        db.flush()
        artifact_dir = ALGORITHM_ARTIFACT_DIR / str(model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        artifact_path.write_bytes(content)
        metadata.update({
            "artifact_path": str(artifact_path),
            "source_code": req.code,
        })
        model.metrics = dict(metadata)
        flag_modified(model, "metrics")
        db.commit()
        db.refresh(model)
    except Exception:
        db.rollback()
        if artifact_path and artifact_path.is_file():
            artifact_path.unlink()
        raise
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "category": model.category,
        "status": model.status,
        "source_filename": filename,
        "training_adapter": spec.to_dict() if spec else None,
        "artifact_trainable": is_trainable,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "message": "新算法已保存到算法商店" if is_trainable else "源码已保存到算法商店；当前仅支持沙箱运行",
    }


# ─── 沙箱基础环境 / 依赖配置 / 可运行构建 ───
class BaseEnvironmentReq(BaseModel):
    name: str
    python_version: str
    compute_type: str = "cpu"
    cuda_version: Optional[str] = None


class BaseEnvironmentUpdateReq(BaseModel):
    name: Optional[str] = None
    compute_type: Optional[str] = None
    cuda_version: Optional[str] = None


class DependencySetReq(BaseModel):
    name: str
    description: str = ""
    requirements: str = ""
    special_packages: List[str] = Field(default_factory=list)


class RuntimeBuildReq(BaseModel):
    base_environment_id: int
    dependency_set_id: int
    name: Optional[str] = None


@router.get("/runtime-bases")
def list_runtime_bases():
    from services.sandbox_environment_service import list_base_environments
    items = list_base_environments()
    return {"total": len(items), "items": items}


@router.post("/runtime-bases", dependencies=[Depends(require_permission("training:write"))])
def create_runtime_base(req: BaseEnvironmentReq):
    from services.sandbox_environment_service import create_base_environment
    return create_base_environment(req.model_dump())


@router.put("/runtime-bases/{base_id}", dependencies=[Depends(require_permission("training:write"))])
def update_runtime_base(base_id: int, req: BaseEnvironmentUpdateReq):
    from services.sandbox_environment_service import update_base_environment
    return update_base_environment(base_id, req.model_dump(exclude_none=True))


@router.delete("/runtime-bases/{base_id}", dependencies=[Depends(require_permission("training:write"))])
def remove_runtime_base(base_id: int):
    from services.sandbox_environment_service import delete_base_environment
    delete_base_environment(base_id)
    return {"status": "deleted", "id": base_id}


@router.get("/dependency-sets")
def list_sandbox_dependency_sets():
    from services.sandbox_environment_service import list_dependency_sets
    items = list_dependency_sets()
    return {"total": len(items), "items": items}


@router.get("/special-packages")
def list_sandbox_special_packages():
    from services.sandbox_environment_service import list_special_packages
    items = list_special_packages()
    return {"total": len(items), "items": items}


@router.post("/dependency-sets", dependencies=[Depends(require_permission("training:write"))])
def create_sandbox_dependency_set(req: DependencySetReq):
    from services.sandbox_environment_service import create_dependency_set
    return create_dependency_set(req.model_dump())


@router.put("/dependency-sets/{dependency_id}", dependencies=[Depends(require_permission("training:write"))])
def update_sandbox_dependency_set(dependency_id: int, req: DependencySetReq):
    from services.sandbox_environment_service import update_dependency_set
    return update_dependency_set(dependency_id, req.model_dump())


@router.delete("/dependency-sets/{dependency_id}", dependencies=[Depends(require_permission("training:write"))])
def remove_sandbox_dependency_set(dependency_id: int):
    from services.sandbox_environment_service import delete_dependency_set
    delete_dependency_set(dependency_id)
    return {"status": "deleted", "id": dependency_id}


@router.get("/runtime-builds")
def list_sandbox_runtime_builds():
    from services.sandbox_environment_service import list_runtime_builds
    items = list_runtime_builds()
    return {"total": len(items), "items": items}


@router.post("/runtime-builds", dependencies=[Depends(require_permission("training:write"))])
def create_sandbox_runtime_build(req: RuntimeBuildReq, background_tasks: BackgroundTasks):
    from services.sandbox_environment_service import build_runtime, create_runtime_build
    item = create_runtime_build(req.model_dump())
    if not item.get("system"):
        background_tasks.add_task(build_runtime, int(item["id"]))
    return {"message": "环境构建已提交" if not item.get("system") else "基础环境已就绪", "item": item}


@router.post("/runtime-builds/{build_id}/rebuild", dependencies=[Depends(require_permission("training:write"))])
def rebuild_sandbox_runtime(build_id: int, background_tasks: BackgroundTasks):
    from services.sandbox_environment_service import build_runtime, create_runtime_build, list_runtime_builds
    current = next((item for item in list_runtime_builds() if int(item["id"]) == build_id), None)
    if not current:
        raise HTTPException(404, "运行环境构建不存在")
    if current.get("system"):
        raise HTTPException(409, "平台基础运行环境无需重建")
    item = create_runtime_build({
        "base_environment_id": current["base_environment_id"],
        "dependency_set_id": current["dependency_set_id"],
        "name": current["name"],
    })
    background_tasks.add_task(build_runtime, int(item["id"]))
    return {"message": "环境重建已提交", "item": item}


@router.post("/runtime-builds/{build_id}/source-install-decision", dependencies=[Depends(require_permission("training:write"))])
def decide_runtime_source_install(build_id: int, approved: bool, background_tasks: BackgroundTasks):
    from services.sandbox_environment_service import approve_source_install, build_runtime
    item = approve_source_install(build_id, approved)
    if approved:
        background_tasks.add_task(build_runtime, int(build_id))
    return {"message": "已确认源码安装，正在继续构建" if approved else "已取消源码安装", "item": item}


@router.delete("/runtime-builds/{build_id}", dependencies=[Depends(require_permission("training:write"))])
def remove_sandbox_runtime_build(build_id: int):
    from services.sandbox_environment_service import delete_runtime_build
    delete_runtime_build(build_id)
    return {"status": "deleted", "id": build_id}



@router.get("/stats/usage")
def usage_stats(start_date: str = None, end_date: str = None):
    """算法使用统计(演示数据)。前端 AlgorithmStore 使用统计 tab 调用。"""
    return {"stats": [
        {"algorithm_name": "LightGBM 回归", "downloads": 128, "runs": 342, "success_rate": 0.94, "avg_runtime": 12.5, "last_used": "2026-07-14"},
        {"algorithm_name": "XGBoost 分类", "downloads": 96, "runs": 271, "success_rate": 0.91, "avg_runtime": 18.2, "last_used": "2026-07-13"},
        {"algorithm_name": "Random Forest", "downloads": 84, "runs": 198, "success_rate": 0.89, "avg_runtime": 24.1, "last_used": "2026-07-12"},
        {"algorithm_name": "MLP 神经网络", "downloads": 52, "runs": 120, "success_rate": 0.85, "avg_runtime": 45.7, "last_used": "2026-07-10"},
    ], "total_runs": 931, "total_downloads": 360}


# ─── 依赖管理(持久化到 algorithm_store_envs.json)───
import os as _os, json as _json
from datetime import datetime as _dt
_DEPS_FILE = _os.path.join(_os.path.dirname(__file__), "..", "uploads", "algorithm_store_envs.json")


def _load_deps() -> list:
    try:
        if _os.path.exists(_DEPS_FILE):
            with open(_DEPS_FILE) as f:
                data = _json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    # 首次:种几个默认依赖环境,避免列表空
    return [
        {"id": 1, "name": "PyTorch 2.1 (CPU)", "python_version": "3.11", "cuda_version": None,
         "packages": [{"name": "torch", "version": "2.1.0"}, {"name": "scikit-learn", "version": "1.4"}],
         "package_count": 2, "algorithms_count": 0, "created_at": _dt.now().isoformat()},
        {"id": 2, "name": "Scikit-learn 经典环境", "python_version": "3.11", "cuda_version": None,
         "packages": [{"name": "scikit-learn", "version": "1.4"}, {"name": "pandas", "version": "2.2"}, {"name": "numpy", "version": "1.26"}],
         "package_count": 3, "algorithms_count": 0, "created_at": _dt.now().isoformat()},
    ]


def _save_deps():
    try:
        _os.makedirs(_os.path.dirname(_DEPS_FILE), exist_ok=True)
        with open(_DEPS_FILE, "w") as f:
            _json.dump(_deps, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"[algorithm_store] 保存依赖环境失败: {e}")


_deps: list[dict] = _load_deps()
_save_deps()  # 首次落盘默认环境


def _next_dep_id() -> int:
    return (max((d.get("id", 0) for d in _deps), default=0)) + 1


@router.get("/dependencies")
def list_dependencies():
    # 返回 dependencies 键(前端 loadDependencies 读 res.dependencies);同时保留 items 兼容
    return {"dependencies": _deps, "items": _deps, "total": len(_deps)}

@router.get("/dependencies/{did}")
def get_dependency(did: int):
    for d in _deps:
        if d.get("id") == did:
            return d
    raise HTTPException(404, "不存在")

@router.post("/dependencies")
def create_dependency(req: dict):
    req["id"] = _next_dep_id()
    req.setdefault("created_at", _dt.now().isoformat())
    req["package_count"] = len(req.get("packages", []) or [])
    req.setdefault("algorithms_count", 0)
    _deps.append(req)
    _save_deps()
    return req

@router.put("/dependencies/{did}")
def update_dependency(did: int, req: dict):
    for i, d in enumerate(_deps):
        if d.get("id") == did:
            d.update(req)
            d["package_count"] = len(d.get("packages", []) or [])
            _save_deps()
            return d
    raise HTTPException(404, "不存在")

@router.delete("/dependencies/{did}")
def delete_dependency(did: int):
    for i, d in enumerate(_deps):
        if d.get("id") == did:
            _deps.pop(i)
            _save_deps()
            return {"status": "deleted"}
    raise HTTPException(404, "不存在")
