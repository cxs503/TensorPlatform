"""Bridge algorithm-store source records into per-user editable workspaces."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from services.builtin_algorithm_registry import BUILTIN_SPECS, builtin_source


ALGORITHM_ARTIFACT_DIR = Path("/app/uploads/algorithms")
MAX_EDITABLE_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512
MAX_ARCHIVE_UNPACKED_BYTES = 100 * 1024 * 1024

ASSET_TYPE_LABELS = {
    "builtin": "内置算法",
    "uploaded_source": "上传算法",
    "finetune_model": "可继续训练模型",
    "inference_model": "推理模型",
    "deployment_bundle": "Docker部署包",
    "unsupported": "历史不支持制品",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_SOURCE_PARAM_ALIASES = {
    "hidden_units": "hidden_layers",
    "lr": "learning_rate",
}

_SOURCE_TUNABLE_CONSTANTS = {
    "N_TRIALS", "EPOCHS", "NUM_EPOCHS", "BATCH_SIZE", "LEARNING_RATE", "LR",
    "N_ESTIMATORS", "MAX_DEPTH", "SEED", "TRAIN_RATIO", "VAL_RATIO", "TEST_RATIO",
}


def _json_schema_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"


_PARAMETER_NAME_EXCLUSIONS = re.compile(
    r"(?:^|_)(?:path|file|dir|column|columns|target|label|feature|data|dataset|output|result)(?:_|$)",
    re.IGNORECASE,
)


def _parameter_label(key: str) -> str:
    return key


def _is_tunable_literal(name: str, value: Any) -> bool:
    return (
        name.isidentifier()
        and not _PARAMETER_NAME_EXCLUSIONS.search(name)
        and isinstance(value, (bool, int, float, str))
        and not isinstance(value, bytes)
    )


def _literal_or_none(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return None


def extract_python_training_contract(source: str) -> dict[str, Any]:
    """Statically extract a safe training contract without importing user code."""
    tree = ast.parse(source)
    assignments: dict[str, Any] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        try:
            assignments[targets[0].id] = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue

    def first_string(*names: str) -> str | None:
        return next((assignments[name] for name in names if isinstance(assignments.get(name), str) and assignments[name]), None)

    def first_string_list(*names: str) -> list[str]:
        for name in names:
            value = assignments.get(name)
            if isinstance(value, (list, tuple)) and value and all(isinstance(item, str) for item in value):
                return list(value)
        return []

    data_contract = {
        "mode": "full_dataset",
        "target_column": first_string("TARGET", "TARGET_COL", "TARGET_COLUMN", "Y_COL", "LABEL"),
        "time_column": first_string("TIME_COL", "TIME_COLUMN", "TIMESTAMP_COL", "DATE_COL"),
        "feature_columns": first_string_list(
            "RAW_FEATURE_COLS", "FEATURE_COLUMNS", "FEATURE_COLS", "INPUT_COLUMNS", "X_COLS"
        ),
    }
    data_contract["detected"] = bool(
        data_contract["target_column"] or data_contract["time_column"] or data_contract["feature_columns"]
    )
    candidates: list[tuple[int, ast.FunctionDef]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            score = 2 if node.name.startswith("train_") else 1 if "train" in node.name else 0
            if score:
                candidates.append((score, node))
    entry = max(candidates, key=lambda item: item[0])[1] if candidates else None

    # Some valid uploaded algorithms are executable research/training scripts
    # rather than a library exposing train_*.  Treat them as a script entry
    # only when the AST gives strong, non-executing evidence of a full training
    # flow: read data, fit an estimator, and persist a model.
    def _has_attr_call(names: set[str]) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in names
            for node in ast.walk(tree)
        )

    has_data_load = _has_attr_call({"read_csv", "read_parquet", "read_excel"})
    has_fit = _has_attr_call({"fit", "fit_transform"})
    has_model_save = _has_attr_call({"dump", "save_model", "save"})
    has_main_guard = any(
        isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Compare)
            and isinstance(child.left, ast.Name)
            and child.left.id == "__name__"
            for child in ast.walk(node.test)
        )
        for node in tree.body
    )
    script_entry = entry is None and has_data_load and has_fit and has_model_save
    entry_name = entry.name if entry is not None else "__main__" if script_entry else None
    entry_kind = "function" if entry is not None else "script" if script_entry else None
    defaults: dict[str, Any] = {}

    for name in _SOURCE_TUNABLE_CONSTANTS:
        value = assignments.get(name)
        if isinstance(value, (bool, int, float, list, tuple)):
            defaults[name] = list(value) if isinstance(value, tuple) else value

    if entry is not None:
        positional = list(entry.args.args)
        for argument, default_node in zip(
            positional[-len(entry.args.defaults):] if entry.args.defaults else [],
            entry.args.defaults,
        ):
            try:
                value = ast.literal_eval(default_node)
            except (TypeError, ValueError):
                continue
            if isinstance(value, str) and ("param" in argument.arg or "config" in argument.arg):
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    defaults.update(parsed)

        for node in ast.walk(entry):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            try:
                value = ast.literal_eval(node.args[1])
            except (TypeError, ValueError):
                continue
            defaults.setdefault(node.args[0].value, value)

    # Candidate parameters are deliberately gathered without importing source
    # code.  They are a convenience form, not a claim that the platform knows
    # every semantic detail of an arbitrary project.
    parameter_candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(key: str, value: Any, origin: str, *, choices: list[Any] | None = None) -> None:
        if not _is_tunable_literal(key, value):
            return
        current = parameter_candidates.get(key)
        if current and current.get("origin") == "top_level_constant":
            return
        item: dict[str, Any] = {
            "key": key,
            "label": _parameter_label(key),
            "type": _json_schema_type(value),
            "default": value,
            "origin": origin,
            "confidence": "high" if origin in {"top_level_constant", "argparse"} else "medium",
        }
        if choices and all(isinstance(choice, (str, int, float, bool)) for choice in choices):
            item["type"] = "select"
            item["options"] = choices[:100]
        parameter_candidates[key] = item

    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if len(targets) != 1 or not isinstance(targets[0], ast.Name) or node.value is None:
            continue
        value = _literal_or_none(node.value)
        if value is not None:
            add_candidate(targets[0].id, value, "top_level_constant")

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and str(node.args[0].value).startswith("--")
        ):
            continue
        flag = str(node.args[0].value).lstrip("-")
        key = flag.replace("-", "_")
        kwargs = {item.arg: item.value for item in node.keywords if item.arg}
        default = _literal_or_none(kwargs["default"]) if "default" in kwargs else None
        if default is None and any(item.arg == "action" and _literal_or_none(item.value) in {"store_true", "store_false"} for item in node.keywords):
            default = False
        if default is None:
            continue
        choices = _literal_or_none(kwargs["choices"]) if "choices" in kwargs else None
        add_candidate(key, default, "argparse", choices=choices if isinstance(choices, (list, tuple)) else None)

    # Training-call literals are also genuine configuration points, even when
    # an executable research script did not declare a top-level constant.
    # Keep this deliberately narrow: every candidate below has a matching
    # temporary-source override in user_script_trainer.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = (
            node.func.attr if isinstance(node.func, ast.Attribute)
            else node.func.id if isinstance(node.func, ast.Name)
            else ""
        )
        keyword_values = {
            item.arg: _literal_or_none(item.value)
            for item in node.keywords if item.arg
        }
        if call_name == "optimize" and isinstance(keyword_values.get("n_trials"), int):
            add_candidate("n_trials", keyword_values["n_trials"], "training_call")
            parameter_candidates["n_trials"]["label"] = "参数搜索次数（n_trials）"
        if call_name == "early_stopping":
            rounds = (
                _literal_or_none(node.args[0]) if node.args
                else keyword_values.get("stopping_rounds")
            )
            if isinstance(rounds, int):
                add_candidate("early_stopping_rounds", rounds, "training_call")
                parameter_candidates["early_stopping_rounds"]["label"] = "早停等待次数"
        if call_name == "TimeSeriesSplit" and isinstance(keyword_values.get("n_splits"), int):
            add_candidate("cv_splits", keyword_values["n_splits"], "training_call")
            parameter_candidates["cv_splits"]["label"] = "时序交叉验证折数"

    for key, value in defaults.items():
        add_candidate(key, value, "declared_default")

    canonical: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    for key, value in defaults.items():
        mapped = _SOURCE_PARAM_ALIASES.get(key, key)
        canonical[mapped] = value
        if mapped != key:
            aliases[key] = mapped

    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    has_linear = "Linear" in attributes
    has_batch_norm = any(name.startswith("BatchNorm") for name in attributes)
    has_dropout = "Dropout" in attributes
    if has_linear:
        canonical.setdefault("dropout", 0.2 if has_dropout else 0.0)
        canonical["batch_norm"] = has_batch_norm
    lower_source = source.lower()
    normalize_features = "standardscaler" in lower_source
    normalize_target = any(token in lower_source for token in ("scaler_y", "y_scaler", "target_scaler"))
    if normalize_features:
        canonical["normalize"] = True
    if normalize_target:
        canonical["normalize_target"] = True

    properties = {
        item["key"]: {"type": item["type"], "default": item["default"], "description": f"静态识别来源：{item['origin']}"}
        for item in parameter_candidates.values()
    }
    return {
        "version": 1,
        "execution_mode": "execute_user_script",
        "entry_function": entry_name,
        "entry_kind": entry_kind,
        "script_has_main_guard": has_main_guard,
        "model_family": "mlp" if has_linear else None,
        # Parameters explicitly declared by the uploaded function remain in
        # their source-level names for display/editing. ``defaults`` below is
        # the canonical runtime contract and may also include inferred model
        # semantics such as BatchNorm and target normalization.
        "declared_defaults": defaults,
        "defaults": canonical,
        "parameter_aliases": aliases,
        "parameter_candidates": list(parameter_candidates.values()),
        "architecture": {
            "batch_norm": has_batch_norm,
            "dropout": has_dropout,
            "normalize_features": normalize_features,
            "normalize_target": normalize_target,
        },
        "data_contract": data_contract,
        "schema": {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
            "defaults": defaults,
        },
    }


def _safe_artifact_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).resolve()
    root = ALGORITHM_ARTIFACT_DIR.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def algorithm_asset_type(model) -> str:
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    if metrics.get("asset_type") == "builtin" and metrics.get("builtin_key") in BUILTIN_SPECS:
        return "builtin"
    filename = str(metrics.get("source_filename") or "").lower()
    if (
        filename.endswith(".pkl")
        and metrics.get("artifact_kind") == "model_artifact"
        and metrics.get("artifact_trainable") is True
    ):
        return "finetune_model"
    if (
        filename.endswith(".onnx")
        and metrics.get("artifact_kind") == "model_artifact"
        and metrics.get("artifact_format") == "onnx"
    ):
        return "inference_model"
    if (
        filename.endswith((".tar.gz", ".tgz"))
        and metrics.get("artifact_kind") == "deployment_bundle"
        and metrics.get("artifact_format") == "docker_bundle"
    ):
        return "deployment_bundle"
    if (
        metrics.get("artifact_kind") == "algorithm_source"
        and metrics.get("artifact_format") in {"python", "source_archive"}
    ) or (isinstance(metrics.get("code"), str) and metrics["code"].strip()):
        return "uploaded_source"
    return "unsupported"


def sync_builtin_algorithms(db) -> None:
    """Ensure one immutable algorithm-store row exists for every AutoML built-in."""
    from models.core import AlgorithmModel

    existing = {}
    for model in db.query(AlgorithmModel).all():
        metrics = model.metrics if isinstance(model.metrics, dict) else {}
        key = metrics.get("builtin_key")
        if metrics.get("asset_type") == "builtin" and key:
            existing[key] = model
    changed = False
    for key, spec in BUILTIN_SPECS.items():
        if key in existing:
            continue
        model = AlgorithmModel(
            name=spec["name"],
            category="builtin",
            description=spec["description"],
            algorithm=key,
            framework=spec["framework"],
            difficulty="builtin",
            version="1.0.0",
            status="published",
            tags=["AutoML", "内置算法", *spec["tasks"]],
            metrics={
                "asset_type": "builtin",
                "builtin_key": key,
                "supported_tasks": spec["tasks"],
                "artifact_trainable": False,
            },
            entry_point=f"build_{key}",
        )
        db.add(model)
        changed = True
    if changed:
        db.commit()


def source_info(model) -> dict[str, Any]:
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    asset_type = algorithm_asset_type(model)
    if asset_type == "builtin":
        key = metrics["builtin_key"]
        source = builtin_source(key)
        return {
            "mode": "builtin",
            "editable": True,
            "has_original_source": True,
            "entry": f"{key}_custom.py",
            "source": source,
            "revision": _sha256(source.encode("utf-8")),
            "label": "AutoML 内置算法（不开放沙箱编辑）",
        }
    if asset_type == "finetune_model":
        artifact = _safe_artifact_path(metrics.get("artifact_path"))
        if not artifact:
            return {"mode": "unsupported", "editable": False, "has_original_source": False, "entry": None, "revision": None, "label": "PKL 文件缺失"}
        return {
            "mode": "model",
            "editable": False,
            "has_original_source": False,
            "path": artifact,
            "entry": None,
            "revision": _sha256(artifact.read_bytes()),
            "label": "在算法商店配置继续训练",
        }
    editable_root = metrics.get("editable_source_root")
    if editable_root:
        root = Path(editable_root).resolve()
        artifact_root = ALGORITHM_ARTIFACT_DIR.resolve()
        entry = str(metrics.get("editable_entry_point") or "main.py")
        entry_path = (root / entry).resolve()
        if artifact_root in root.parents and root.is_dir() and root in entry_path.parents and entry_path.is_file():
            return {
                "mode": "companion",
                "editable": True,
                "has_original_source": False,
                "root": root,
                "entry": entry,
                "revision": _sha256(entry_path.read_bytes()),
                "label": "配套代码",
            }

    artifact = _safe_artifact_path(metrics.get("artifact_path"))
    kind = metrics.get("artifact_kind")
    artifact_format = metrics.get("artifact_format")
    if artifact and kind == "algorithm_source" and artifact_format == "python":
        return {
            "mode": "python",
            "editable": True,
            "has_original_source": True,
            "path": artifact,
            "entry": artifact.name,
            "revision": _sha256(artifact.read_bytes()),
            "label": "原始 Python 源码",
        }
    if artifact and kind == "algorithm_source" and artifact_format == "source_archive":
        return {
            "mode": "archive",
            "editable": True,
            "has_original_source": True,
            "path": artifact,
            "entry": None,
            "revision": _sha256(artifact.read_bytes()),
            "label": "原始源码压缩包",
        }
    if isinstance(metrics.get("code"), str) and metrics["code"].strip():
        return {
            "mode": "inline",
            "editable": True,
            "has_original_source": True,
            "entry": "main.py",
            "revision": _sha256(metrics["code"].encode("utf-8")),
            "label": "数据库内联源码",
        }

    artifact_kind = metrics.get("artifact_kind")
    return {
        "mode": "generated",
        "editable": True,
        "has_original_source": False,
        "entry": "main.py",
        "revision": None,
        "label": "模型配套调用代码" if artifact_kind in {"model_artifact", "deployment_bundle"} else "可编辑起始代码",
    }


def ensure_algorithm_source_contract(db, model) -> dict[str, Any] | None:
    """Backfill or refresh the static contract for an uploaded Python algorithm."""
    if algorithm_asset_type(model) != "uploaded_source":
        return None
    info = source_info(model)
    if info.get("mode") == "python":
        source = info["path"].read_text(encoding="utf-8")
    elif info.get("mode") == "inline":
        metrics = model.metrics if isinstance(model.metrics, dict) else {}
        source = str(metrics.get("code") or "")
    elif info.get("mode") == "archive":
        # Archive uploads store their validated selected entry source in metadata;
        # use it to backfill parameter candidates without unpacking or executing.
        metrics = model.metrics if isinstance(model.metrics, dict) else {}
        source = str(metrics.get("source_code") or "")
    else:
        return None
    if not source.strip():
        return None
    contract = extract_python_training_contract(source)
    metrics = dict(model.metrics or {})
    changed = metrics.get("source_contract") != contract
    schema_text = json.dumps(contract["schema"], ensure_ascii=False)
    if model.params_schema != schema_text:
        model.params_schema = schema_text
        changed = True
    # "__main__" describes a top-level script execution mode, not a filename;
    # do not overwrite the upload's real file entry point with that marker.
    if (
        contract.get("entry_function")
        and contract["entry_function"] != "__main__"
        and model.entry_point != contract["entry_function"]
    ):
        model.entry_point = contract["entry_function"]
        changed = True
    if metrics.get("source_contract") != contract:
        metrics["source_contract"] = contract
        model.metrics = metrics
        flag_modified(model, "metrics")
    if changed:
        db.commit()
        db.refresh(model)
    return contract


def algorithm_summary(model) -> dict[str, Any]:
    info = source_info(model)
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    asset_type = algorithm_asset_type(model)
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description or "",
        "framework": model.framework,
        "algorithm": model.algorithm,
        "version": model.version,
        "source_mode": info["mode"],
        "source_label": info["label"],
        "has_original_source": info["has_original_source"],
        "artifact_kind": metrics.get("artifact_kind"),
        "artifact_format": metrics.get("artifact_format"),
        "source_filename": metrics.get("source_filename"),
        "dependency_id": metrics.get("dependency_id"),
        "asset_type": asset_type,
        "asset_type_label": ASSET_TYPE_LABELS[asset_type],
        "sandbox_policy": (
            "overwrite_source" if asset_type == "uploaded_source"
            else "unavailable"
        ),
        "sandbox_read_only": False,
        "downloadable": asset_type in {
            "uploaded_source", "finetune_model", "inference_model",
            "deployment_bundle",
        },
    }


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise HTTPException(400, f"源码包包含不安全路径：{name}")
    return normalized


def _extract_source_archive(archive_path: Path, destination: Path) -> list[str]:
    files: list[str] = []
    total_size = 0
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise HTTPException(400, "源码包文件数量超过限制")
            for info in infos:
                name = _safe_member_name(info.filename)
                if info.is_dir():
                    continue
                total_size += info.file_size
                if total_size > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise HTTPException(400, "源码包解压后超过 100 MB")
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
                files.append(name)
    else:
        try:
            archive = tarfile.open(archive_path, mode="r:*")
        except tarfile.TarError as exc:
            raise HTTPException(400, f"源码包无法读取：{exc}")
        with archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise HTTPException(400, "源码包文件数量超过限制")
            for member in members:
                name = _safe_member_name(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise HTTPException(400, f"源码包包含不允许的链接或设备文件：{name}")
                if not member.isfile():
                    continue
                total_size += max(member.size, 0)
                if total_size > MAX_ARCHIVE_UNPACKED_BYTES:
                    raise HTTPException(400, "源码包解压后超过 100 MB")
                source = archive.extractfile(member)
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read() if source else b"")
                files.append(name)
    return files


def _generated_code(model, artifact_relative: str | None = None) -> str:
    metrics = model.metrics if isinstance(model.metrics, dict) else {}
    artifact_format = str(metrics.get("artifact_format") or "")
    algorithm = str(model.algorithm or "").lower()
    header = (
        f'"""算法商店 ID {model.id}：{model.name}\n'
        "该文件由平台生成，原记录没有可编辑源码；保存后将成为该算法的配套源码。\n"
        '"""\n'
    )
    if artifact_format == "onnx":
        artifact = artifact_relative or "artifacts/model.onnx"
        return header + f'''from pathlib import Path
import onnxruntime as ort

MODEL_PATH = Path(__file__).parent / {artifact!r}

def load_model():
    return ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])

if __name__ == "__main__":
    session = load_model()
    print("模型输入:", [(item.name, item.shape, item.type) for item in session.get_inputs()])
    print("模型输出:", [(item.name, item.shape, item.type) for item in session.get_outputs()])
'''
    if artifact_format in {"pkl", "joblib"}:
        artifact = artifact_relative or "artifacts/model.pkl"
        return header + f'''from pathlib import Path
import joblib

MODEL_PATH = Path(__file__).parent / {artifact!r}

def load_model():
    return joblib.load(MODEL_PATH)

if __name__ == "__main__":
    model = load_model()
    print("已加载模型:", type(model).__name__)
    print("请在此补充符合模型输入特征的推理数据。")
'''
    if "random" in algorithm or "forest" in algorithm:
        return header + '''from sklearn.ensemble import RandomForestRegressor

def build_model(**params):
    defaults = {"n_estimators": 100, "random_state": 42}
    defaults.update(params)
    return RandomForestRegressor(**defaults)

if __name__ == "__main__":
    print(build_model())
'''
    if "xgboost" in algorithm or "xgb" in algorithm:
        return header + '''from xgboost import XGBRegressor

def build_model(**params):
    defaults = {"n_estimators": 100, "random_state": 42}
    defaults.update(params)
    return XGBRegressor(**defaults)

if __name__ == "__main__":
    print(build_model())
'''
    if "lightgbm" in algorithm or "lgbm" in algorithm:
        return header + '''from lightgbm import LGBMRegressor

def build_model(**params):
    defaults = {"n_estimators": 100, "random_state": 42}
    defaults.update(params)
    return LGBMRegressor(**defaults)

if __name__ == "__main__":
    print(build_model())
'''
    if "mlp" in algorithm:
        return header + '''import torch
from torch import nn

class MLP(nn.Module):
    def __init__(self, input_dim=8, hidden_dim=64, output_dim=1):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x):
        return self.network(x)

if __name__ == "__main__":
    print(MLP())
'''
    return header + '''def run(*args, **kwargs):
    """在这里实现算法逻辑。"""
    return {"status": "ready", "args": args, "kwargs": kwargs}

if __name__ == "__main__":
    print(run())
'''


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned[:48] or "algorithm"


def import_to_workspace(model, workspace: Path) -> dict[str, Any]:
    asset_type = algorithm_asset_type(model)
    if asset_type != "uploaded_source":
        if asset_type == "builtin":
            raise HTTPException(400, "AutoML 内置算法不在沙箱中编辑")
        if asset_type == "finetune_model":
            raise HTTPException(400, "PKL 的数据集与超参数请直接在算法商店的继续训练界面配置")
        raise HTTPException(400, "该历史制品不是可在沙箱编辑的 Python 上传算法")
    info = source_info(model)
    if info["mode"] == "unsupported":
        raise HTTPException(400, "算法源码不存在，无法载入沙箱")
    project_relative = Path("algorithms") / f"algorithm_{model.id}_{_slug(model.name)}"
    project = workspace / project_relative
    manifest_path = project / ".foundation_algorithm.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = project / manifest["entry"]
            expected_policy = "overwrite_source"
            if (
                manifest.get("algorithm_id") == model.id
                and manifest.get("asset_type") == asset_type
                and manifest.get("save_policy") == expected_policy
                and entry.is_file()
            ):
                return {**manifest, "project_path": project_relative.as_posix(), "reused": True}
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    if project.exists():
        shutil.rmtree(project)
    project.mkdir(parents=True, exist_ok=True)

    mode = info["mode"]
    entry = "main.py"
    files: list[str] = []
    if mode == "python":
        entry = info["path"].name
        shutil.copy2(info["path"], project / entry)
        files = [entry]
    elif mode == "inline":
        code = (model.metrics or {}).get("code", "")
        (project / entry).write_text(code, encoding="utf-8")
        files = [entry]
    elif mode == "archive":
        files = _extract_source_archive(info["path"], project)
        python_files = sorted(name for name in files if name.lower().endswith(".py"))
        if not python_files:
            raise HTTPException(400, "源码包中没有可编辑的 Python 文件")
        requested = str(model.entry_point or "").split(":", 1)[0].replace(".", "/")
        requested_py = requested + ".py" if requested and not requested.endswith(".py") else requested
        entry = requested_py if requested_py in python_files else python_files[0]
    elif mode == "companion":
        shutil.copytree(info["root"], project, dirs_exist_ok=True)
        entry = info["entry"]
        files = [item.relative_to(project).as_posix() for item in project.rglob("*") if item.is_file()]
    else:
        metrics = model.metrics if isinstance(model.metrics, dict) else {}
        artifact = _safe_artifact_path(metrics.get("artifact_path"))
        artifact_relative = None
        if (
            artifact
            and metrics.get("artifact_format") in {"onnx", "pkl", "joblib"}
            and artifact.stat().st_size <= MAX_EDITABLE_ARTIFACT_BYTES
        ):
            artifact_dir = project / "artifacts"
            artifact_dir.mkdir(exist_ok=True)
            shutil.copy2(artifact, artifact_dir / artifact.name)
            artifact_relative = f"artifacts/{artifact.name}"
            files.append(artifact_relative)
        (project / entry).write_text(_generated_code(model, artifact_relative), encoding="utf-8")
        files.insert(0, entry)

    manifest = {
        "algorithm_id": model.id,
        "algorithm_name": model.name,
        "entry": entry,
        "source_mode": mode,
        "source_label": info["label"],
        "has_original_source": info["has_original_source"],
        "revision": info["revision"],
        "files": files,
        "asset_type": asset_type,
        "save_policy": "overwrite_source",
        "read_only_files": [],
        "parameter_file": None,
        "download_artifact": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "project_path": project_relative.as_posix(), "reused": False}


def _replace_archive_member(archive_path: Path, member_name: str, content: bytes) -> None:
    member_name = _safe_member_name(member_name)
    with tempfile.NamedTemporaryFile(dir=archive_path.parent, prefix=".source-", delete=False) as stream:
        temp_path = Path(stream.name)
    replaced = False
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(temp_path, "w") as target:
                for info in source.infolist():
                    name = _safe_member_name(info.filename)
                    if name == member_name and not info.is_dir():
                        target.writestr(info, content)
                        replaced = True
                    else:
                        target.writestr(info, source.read(info) if not info.is_dir() else b"")
        else:
            write_mode = "w:gz" if archive_path.name.lower().endswith((".tar.gz", ".tgz")) else "w"
            with tarfile.open(archive_path, "r:*") as source, tarfile.open(temp_path, write_mode) as target:
                for member in source.getmembers():
                    name = _safe_member_name(member.name)
                    if name == member_name and member.isfile():
                        member.size = len(content)
                        target.addfile(member, io.BytesIO(content))
                        replaced = True
                    else:
                        extracted = source.extractfile(member) if member.isfile() else None
                        target.addfile(member, extracted)
        if not replaced:
            raise HTTPException(404, "该文件不属于原源码包")
        temp_path.replace(archive_path)
    finally:
        temp_path.unlink(missing_ok=True)


def overwrite_algorithm_source(
    db,
    model,
    workspace: Path,
    project_path: str,
    relative_path: str,
    content: str,
    expected_revision: str | None,
) -> dict[str, Any]:
    if algorithm_asset_type(model) != "uploaded_source":
        raise HTTPException(403, "只有用户上传的 Python 算法可以覆盖算法商店原源码")
    project = (workspace / project_path).resolve()
    try:
        project.relative_to(workspace.resolve())
    except ValueError:
        raise HTTPException(403, "工作区路径无效")
    manifest_path = project / ".foundation_algorithm.json"
    if not manifest_path.is_file():
        raise HTTPException(400, "该目录不是从算法商店导入的项目")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("algorithm_id") != model.id:
        raise HTTPException(400, "算法与导入项目不匹配")
    member = _safe_member_name(relative_path)
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        raise HTTPException(413, "单个源码文件不能超过 5 MB")
    if member.lower().endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise HTTPException(400, f"Python 语法错误：第 {exc.lineno} 行 {exc.msg}")

    info = source_info(model)
    if expected_revision and info.get("revision") and expected_revision != info["revision"]:
        raise HTTPException(409, "算法源码已被其他操作修改，请重新导入后再覆盖")
    raw = content.encode("utf-8")
    metrics = dict(model.metrics or {})
    if info["mode"] == "python":
        if Path(member).name != info["path"].name:
            raise HTTPException(400, "单文件算法只能覆盖其原始 Python 文件")
        temp = info["path"].with_name(f".{info['path'].name}.tmp")
        temp.write_bytes(raw)
        temp.replace(info["path"])
        metrics["source_code"] = content
        metrics["source_size"] = len(raw)
        metrics["artifact_sha256"] = _sha256(raw)
        contract = extract_python_training_contract(content)
        metrics["source_contract"] = contract
        model.params_schema = json.dumps(contract["schema"], ensure_ascii=False)
        if contract.get("entry_function"):
            model.entry_point = contract["entry_function"]
    elif info["mode"] == "inline":
        if member != "main.py":
            raise HTTPException(400, "内联算法只能覆盖 main.py")
        metrics["code"] = content
    elif info["mode"] == "archive":
        _replace_archive_member(info["path"], member, raw)
        archive_raw = info["path"].read_bytes()
        metrics["source_size"] = len(archive_raw)
        metrics["artifact_sha256"] = _sha256(archive_raw)
    else:
        editable_root = ALGORITHM_ARTIFACT_DIR / str(model.id) / "editable_source"
        target = (editable_root / member).resolve()
        if editable_root.resolve() not in target.parents:
            raise HTTPException(403, "配套源码路径无效")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        metrics["editable_source_root"] = str(editable_root)
        metrics["editable_entry_point"] = member
        metrics["editable_source_origin"] = "companion" if metrics.get("artifact_kind") else "generated"
        metrics["editable_source_sha256"] = _sha256(raw)

    model.metrics = metrics
    flag_modified(model, "metrics")
    db.commit()
    db.refresh(model)
    updated = source_info(model)
    manifest.update({
        "source_mode": updated["mode"],
        "source_label": updated["label"],
        "has_original_source": updated["has_original_source"],
        "revision": updated["revision"],
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "algorithm_id": model.id,
        "path": member,
        "source_mode": updated["mode"],
        "source_label": updated["label"],
        "has_original_source": updated["has_original_source"],
        "revision": updated["revision"],
        "message": "算法原源码已覆盖" if updated["has_original_source"] else "配套源码已保存，原模型制品未改变",
    }
