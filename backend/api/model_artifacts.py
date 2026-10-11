"""Persistent model-package generation, integrity validation and benchmarking."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access, is_admin
from database.config import get_db


router = APIRouter(prefix="/model-artifacts", tags=["模型制品验证"])
ARTIFACT_DIR = Path(os.getenv("MODEL_PACKAGE_DIR", "/app/uploads/model_packages"))
SUPPORTED_FORMATS = {"onnx", "pkl", "docker"}
DOCKER_REQUIRED_FILES = {
    "Dockerfile", "serve.py", "requirements.txt", "model.onnx",
    "model-metadata.json", "README.txt",
}
PYTHON_DOCKER_REQUIRED_FILES = {
    "Dockerfile", "serve.py", "requirements.txt", "model.pkl",
    "runtime_source.py", "model-metadata.json", "README.txt",
}


PYTHON_SERVE_TEMPLATE = '''# serve.py — foundation user-script predictor runtime
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
METADATA = json.loads((ROOT / "model-metadata.json").read_text(encoding="utf-8"))
namespace = {"__name__": "__main__"}
source_path = ROOT / "runtime_source.py"
exec(compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec"), namespace)
vars(sys.modules["__main__"]).update(namespace)
MODEL = joblib.load(ROOT / "model.pkl")
FEATURE_COLUMNS = list(METADATA.get("feature_columns") or [])
TIME_COLUMN = METADATA.get("time_column")
TARGET_COLUMN = METADATA.get("target_column")

app = FastAPI(title="foundation Python Model Server")


class Input(BaseModel):
    features: list


def _frame(features):
    if not features:
        raise ValueError("features must not be empty")
    if isinstance(features[0], dict):
        frame = pd.DataFrame(features)
    else:
        values = np.asarray(features, dtype=float)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        if values.ndim != 2:
            raise ValueError("features must be one sample or a batch")
        if FEATURE_COLUMNS and values.shape[1] != len(FEATURE_COLUMNS):
            raise ValueError(f"expected {len(FEATURE_COLUMNS)} features, got {values.shape[1]}")
        frame = pd.DataFrame(values, columns=FEATURE_COLUMNS or None)
    if TIME_COLUMN and TIME_COLUMN in frame.columns and "时间" not in frame.columns:
        frame["时间"] = frame[TIME_COLUMN]
    elif TIME_COLUMN and TIME_COLUMN not in frame.columns and "时间" not in frame.columns:
        frame[TIME_COLUMN] = pd.date_range(end=pd.Timestamp.now().floor("min"), periods=len(frame), freq="min")
        frame["时间"] = frame[TIME_COLUMN]
    return frame


def _prediction_values(result):
    if isinstance(result, pd.DataFrame):
        columns = list(result.columns)
        preferred = TARGET_COLUMN if TARGET_COLUMN in columns else None
        if preferred is None:
            for token in ("最终预测", "预测结果", "预测值", "prediction", "predicted", "y_pred"):
                preferred = next((column for column in columns if token.lower() in str(column).lower()), None)
                if preferred is not None:
                    break
        if preferred is None:
            numeric = list(result.select_dtypes(include=[np.number]).columns)
            if len(numeric) == 1:
                preferred = numeric[0]
        if preferred is None:
            raise ValueError("predict returned multiple columns without a recognizable final prediction")
        result = result[preferred]
    elif isinstance(result, dict):
        preferred = next((key for key in (TARGET_COLUMN, "prediction", "predictions", "预测值", "最终预测") if key and key in result), None)
        if preferred is None:
            preferred = next((key for key in result if "最终预测" in str(key)), None)
        if preferred is None:
            raise ValueError("predict returned a mapping without a recognizable prediction")
        result = result[preferred]
    values = np.asarray(result)
    if values.ndim == 2 and values.shape[1] == 1:
        values = values[:, 0]
    if values.ndim > 1:
        raise ValueError("predict must produce one final value per sample")
    return values.reshape(-1)


@app.post("/predict")
def predict(request: Input):
    try:
        frame = _frame(request.features)
        result = MODEL.predict(frame)
        return {"prediction": _prediction_values(result).tolist()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": True,
        "model_type": type(MODEL).__name__,
        "feature_columns": FEATURE_COLUMNS,
        "time_column": TIME_COLUMN,
    }
'''


PYTHON_DOCKERFILE_TEMPLATE = '''FROM __PYTHON_IMAGE__
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser model.pkl runtime_source.py model-metadata.json serve.py /app/
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8080"]
'''


class ArtifactCreateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: int
    format: str


class ArtifactBenchmarkRequest(BaseModel):
    X: Any
    n_runs: int = Field(default=100, ge=1, le=10000)


def _json_value(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _safe_name(value: Any) -> str:
    import re
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", str(value or "model"))
    return cleaned.strip("._") or "model"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_context(db, model_id: int, current_user: dict) -> dict:
    row = db.execute(text("""
        SELECT m.id, m.name, m.framework, m.model_type, m.model_blob,
               m.organization_id, mv.id, mv.version_number, mv.file_path,
               mv.parameters
        FROM models m
        LEFT JOIN LATERAL (
            SELECT id, version_number, file_path, parameters
            FROM model_versions WHERE model_id=m.id
            ORDER BY id DESC LIMIT 1
        ) mv ON TRUE
        WHERE m.id=:model_id
    """), {"model_id": model_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    assert_organization_access(row[5], current_user)
    if not row[6]:
        raise HTTPException(status_code=400, detail="模型没有已登记版本，无法生成制品")
    parameters = _json_value(row[9])
    job_id = parameters.get("job_id") or parameters.get("training_job_id")
    if job_id:
        job_row = db.execute(text("""
            SELECT dataset_id, hyperparameters
            FROM training_jobs
            WHERE training_job_id=:job_id
            ORDER BY id DESC LIMIT 1
        """), {"job_id": str(job_id)}).fetchone()
        if job_row:
            job_config = _json_value(job_row[1])
            # The training job owns the complete input contract (notably the
            # selected time column). Keep immutable version metadata dominant.
            parameters = {
                **job_config,
                **parameters,
                "dataset_id": parameters.get("dataset_id") or job_row[0],
                "source_data_contract": (
                    parameters.get("source_data_contract")
                    or job_config.get("source_data_contract")
                ),
                "source_time_column": (
                    parameters.get("source_time_column")
                    or job_config.get("source_time_column")
                ),
            }
    return {
        "model_id": row[0], "model_name": row[1], "framework": row[2],
        "model_type": row[3], "model_blob": row[4], "organization_id": row[5],
        "model_version_id": row[6], "version": row[7] or "v1",
        "file_path": row[8], "parameters": parameters,
    }


def _export_onnx(context: dict) -> tuple[Path, dict]:
    source = Path(str(context.get("file_path") or ""))
    if source.is_file() and source.suffix.lower() == ".onnx":
        from api.onnx_api import validate_native_onnx
        report = validate_native_onnx(source)
        return source, report
    from api.onnx_api import ExportRequest, export_to_onnx
    params = context.get("parameters") or {}
    job_id = params.get("job_id") or params.get("training_job_id") or ""
    result = export_to_onnx(ExportRequest(
        job_id=str(job_id), model_id=int(context["model_id"])
    ))
    path = Path(str(result.get("onnx_path") or ""))
    if not path.is_file():
        raise HTTPException(status_code=500, detail="ONNX转换完成但没有生成有效文件")
    return path, result


def _resolve_pkl(context: dict, db) -> tuple[Path, bool]:
    # Reuse the deployment-side exact-version resolver, including PyTorch migration.
    from api.deployment import _resolve_version_pkl
    deployment_context = {
        **context,
        "deployment_id": None,
    }
    return _resolve_version_pkl(deployment_context, db)


def _write_docker_bundle(context: dict, onnx_path: Path, onnx_report: dict) -> Path:
    from api.deployment import DOCKERFILE_TEMPLATE, REQUIREMENTS_TXT, SERVE_PY_TEMPLATE
    from api.onnx_api import validate_native_onnx

    input_spec = validate_native_onnx(onnx_path)
    work = Path(tempfile.mkdtemp(prefix="foundation-artifact-docker-"))
    try:
        (work / "serve.py").write_text(SERVE_PY_TEMPLATE, encoding="utf-8")
        (work / "requirements.txt").write_text(REQUIREMENTS_TXT, encoding="utf-8")
        (work / "Dockerfile").write_text(DOCKERFILE_TEMPLATE, encoding="utf-8")
        shutil.copy2(onnx_path, work / "model.onnx")
        metadata = {
            "schema_version": 1,
            "package_type": "foundation_docker_onnx",
            "model_id": context["model_id"],
            "model_version_id": context["model_version_id"],
            "version": context["version"],
            "framework": context.get("framework"),
            "model_type": context.get("model_type"),
            "input_name": input_spec.get("input_name"),
            "input_shape": input_spec.get("input_shape"),
            "model_sha256": _sha256(work / "model.onnx"),
            "onnx_equivalence_verified": onnx_report.get("equivalence_verified", False),
            "onnx_max_abs_error": onnx_report.get("max_abs_error"),
        }
        (work / "model-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (work / "README.txt").write_text(
            "构建: docker build -t foundation-model .\n"
            "运行: docker run --rm -p 8080:8080 foundation-model\n"
            "健康检查: GET http://localhost:8080/health\n"
            "推理: POST /predict, JSON示例 {\"features\": [1.0, 2.0]}\n",
            encoding="utf-8",
        )
        fd, raw_path = tempfile.mkstemp(suffix=".tar.gz", prefix="foundation-artifact-")
        os.close(fd)
        package = Path(raw_path)
        with tarfile.open(package, "w:gz") as archive:
            for item in sorted(work.iterdir()):
                archive.add(item, arcname=item.name)
        return package
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _is_user_script_predictor(context: dict) -> bool:
    parameters = context.get("parameters") or {}
    return (
        parameters.get("artifact_kind") == "user_script_predictor"
        or parameters.get("source_execution_mode") == "execute_user_script"
        or str(context.get("framework") or "").lower() == "user_script"
    )


def _python_runtime_requirements(context: dict) -> tuple[list[str], dict]:
    parameters = context.get("parameters") or {}
    environment_id = parameters.get("environment_id") or parameters.get("runtime_environment_id")
    if not environment_id:
        raise ValueError("用户脚本模型缺少训练环境ID，无法生成可复现Docker包")
    from services.sandbox_environment_service import get_runtime_build
    runtime = get_runtime_build(int(environment_id))
    locked = [str(item).strip() for item in runtime.get("locked_packages") or [] if str(item).strip()]
    if not locked:
        raise ValueError("训练环境没有锁定依赖，无法生成可复现Docker包")
    # runtime_source.py intentionally preserves the script's imports so custom
    # predictor classes can be restored exactly. Therefore the Docker runtime
    # must retain the complete locked training environment.
    requirements = list(locked)
    requirements.extend(["fastapi==0.104.1", "uvicorn==0.24.0"])
    return requirements, runtime


def _write_python_docker_bundle(context: dict, pkl_path: Path) -> Path:
    from services.sandbox_model_artifact import write_runtime_source

    parameters = context.get("parameters") or {}
    script_source = parameters.get("script_source")
    if not script_source:
        raise ValueError("用户脚本模型缺少配套源码，无法恢复自定义预测器")
    requirements, runtime = _python_runtime_requirements(context)
    data_contract = _json_value(parameters.get("source_data_contract"))
    feature_columns = list(
        parameters.get("feature_columns") or data_contract.get("feature_columns") or []
    )
    work = Path(tempfile.mkdtemp(prefix="foundation-python-docker-"))
    try:
        shutil.copy2(pkl_path, work / "model.pkl")
        write_runtime_source(script_source, work / "runtime_source.py")
        (work / "serve.py").write_text(PYTHON_SERVE_TEMPLATE, encoding="utf-8")
        (work / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
        version_parts = str(runtime.get("python_version") or "3.11").split(".")
        python_minor = ".".join(version_parts[:2]) if len(version_parts) >= 2 else "3.11"
        base_image = f"python:{python_minor}-slim-bookworm"
        dockerfile = PYTHON_DOCKERFILE_TEMPLATE.replace("__PYTHON_IMAGE__", base_image)
        (work / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        metadata = {
            "schema_version": 1,
            "package_type": "foundation_docker_python",
            "model_id": context["model_id"],
            "model_version_id": context["model_version_id"],
            "version": context["version"],
            "framework": context.get("framework"),
            "model_type": context.get("model_type"),
            "predictor_type": parameters.get("predictor_type"),
            "feature_columns": feature_columns,
            "expected_features": len(feature_columns) or parameters.get("n_features_in"),
            "time_column": (
                parameters.get("time_column")
                or parameters.get("source_time_column")
                or data_contract.get("time_column")
            ),
            "target_column": parameters.get("target_column") or data_contract.get("target_column"),
            "model_sha256": _sha256(work / "model.pkl"),
            "runtime_source_sha256": _sha256(work / "runtime_source.py"),
            "runtime_environment_id": runtime.get("id"),
            "runtime_digest": runtime.get("runtime_digest"),
            "python_version": runtime.get("python_version"),
            "base_image": base_image,
            "locked_packages": requirements,
        }
        (work / "model-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (work / "README.txt").write_text(
            "构建: docker build -t foundation-python-model .\n"
            "运行: docker run --rm -p 8080:8080 foundation-python-model\n"
            "健康检查: GET http://localhost:8080/health\n"
            "推理: POST /predict，features支持字段对象数组或按feature_columns顺序的数值数组。\n",
            encoding="utf-8",
        )
        fd, raw_path = tempfile.mkstemp(suffix=".tar.gz", prefix="foundation-python-artifact-")
        os.close(fd)
        package = Path(raw_path)
        with tarfile.open(package, "w:gz") as archive:
            for item in sorted(work.iterdir()):
                archive.add(item, arcname=item.name)
        return package
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _onnx_inspection(path: Path) -> dict:
    from api.onnx_api import validate_native_onnx
    report = validate_native_onnx(path)
    return {
        "valid": True,
        "validation_level": "runtime_load",
        "message": "ONNX格式及运行时加载校验通过",
        **report,
    }


def _docker_onnx(package: Path) -> tuple[Path, dict]:
    file_descriptor, temporary_name = tempfile.mkstemp(
        suffix=".onnx", prefix="foundation-bundle-"
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(package, "r:gz") as archive:
            names = set(archive.getnames())
            missing = sorted(DOCKER_REQUIRED_FILES - names)
            if missing:
                raise ValueError("Docker包缺少文件: " + "、".join(missing))
            member = archive.getmember("model.onnx")
            if not member.isfile() or member.size > 512 * 1024 * 1024:
                raise ValueError("Docker包内model.onnx无效或超过512MB")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("无法读取Docker包内model.onnx")
            with temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            metadata_member = archive.getmember("model-metadata.json")
            metadata_source = archive.extractfile(metadata_member)
            metadata = json.loads(metadata_source.read().decode("utf-8")) if metadata_source else {}
        expected = metadata.get("model_sha256")
        actual = _sha256(temporary)
        if expected and expected != actual:
            raise ValueError("Docker包内模型校验和与元数据不一致")
        return temporary, metadata
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _docker_package_type(package: Path) -> tuple[str, dict]:
    with tarfile.open(package, "r:gz") as archive:
        names = set(archive.getnames())
        if "model-metadata.json" not in names:
            raise ValueError("Docker包缺少model-metadata.json")
        metadata_source = archive.extractfile(archive.getmember("model-metadata.json"))
        metadata = json.loads(metadata_source.read().decode("utf-8")) if metadata_source else {}
    package_type = str(metadata.get("package_type") or "")
    if package_type not in {"foundation_docker_onnx", "foundation_docker_python"}:
        raise ValueError(f"未知Docker包类型: {package_type or '未标记'}")
    return package_type, metadata


def _extract_python_docker(package: Path) -> tuple[Path, dict]:
    work = Path(tempfile.mkdtemp(prefix="foundation-python-bundle-check-"))
    try:
        with tarfile.open(package, "r:gz") as archive:
            names = set(archive.getnames())
            missing = sorted(PYTHON_DOCKER_REQUIRED_FILES - names)
            if missing:
                raise ValueError("Python Docker包缺少文件: " + "、".join(missing))
            for name in PYTHON_DOCKER_REQUIRED_FILES:
                member = archive.getmember(name)
                if not member.isfile() or member.size > 512 * 1024 * 1024:
                    raise ValueError(f"Docker包内{name}无效或超过大小限制")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"无法读取Docker包内{name}")
                with (work / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        metadata = json.loads((work / "model-metadata.json").read_text(encoding="utf-8"))
        if metadata.get("package_type") != "foundation_docker_python":
            raise ValueError("Docker包不是开放底座Python模型运行包")
        if metadata.get("model_sha256") != _sha256(work / "model.pkl"):
            raise ValueError("Docker包内PKL校验和与元数据不一致")
        if metadata.get("runtime_source_sha256") != _sha256(work / "runtime_source.py"):
            raise ValueError("Docker包内运行源码校验和与元数据不一致")
        return work, metadata
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _validate_python_bundle_runtime(work: Path, metadata: dict) -> dict:
    environment_id = metadata.get("runtime_environment_id")
    from services.sandbox_environment_service import get_runtime_build
    runtime = get_runtime_build(int(environment_id))
    if runtime.get("runtime_digest") != metadata.get("runtime_digest"):
        raise ValueError("Docker包记录的训练环境摘要与当前平台环境不一致")
    python_path = runtime.get("python_path")
    inspector = (
        "import sys,joblib\n"
        "namespace={'__name__':'__main__'}\n"
        "exec(compile(open(sys.argv[1],encoding='utf-8').read(),sys.argv[1],'exec'),namespace)\n"
        "vars(sys.modules['__main__']).update(namespace)\n"
        "model=joblib.load(sys.argv[2])\n"
        "assert callable(getattr(model,'predict',None))\n"
        "print(type(model).__name__)\n"
    )
    import subprocess
    result = subprocess.run(
        [str(python_path), "-c", inspector, str(work / "runtime_source.py"), str(work / "model.pkl")],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise ValueError("包内PKL无法在锁定训练环境加载: " + (result.stderr or result.stdout)[-800:])
    return {
        "valid": True,
        "validation_level": "bundle_runtime_load",
        "message": "Python Docker包结构、校验和、运行源码及训练环境加载均通过",
        "predictor_class": result.stdout.strip().splitlines()[-1],
        "package_metadata": metadata,
    }


def _inspect_artifact(path: Path, artifact_format: str, model_id: int, db) -> dict:
    if artifact_format == "onnx":
        return _onnx_inspection(path)
    if artifact_format == "docker":
        package_type, _ = _docker_package_type(path)
        if package_type == "foundation_docker_python":
            extracted_dir, metadata = _extract_python_docker(path)
            try:
                return _validate_python_bundle_runtime(extracted_dir, metadata)
            finally:
                shutil.rmtree(extracted_dir, ignore_errors=True)
        extracted, metadata = _docker_onnx(path)
        try:
            report = _onnx_inspection(extracted)
            return {
                **report,
                "validation_level": "bundle_runtime_load",
                "message": "Docker包结构、校验和及包内ONNX运行时加载均通过",
                "package_metadata": metadata,
            }
        finally:
            extracted.unlink(missing_ok=True)
    if artifact_format == "pkl":
        from api.model_evaluation import _load_serialized_model, _load_training_context
        context = _load_training_context(db, model_id)
        predictor, restored = _load_serialized_model(
            str(path), context.get("code_snapshot_path"), context.get("script_source")
        )
        if not callable(getattr(predictor, "predict", None)):
            raise ValueError("PKL可加载，但对象没有predict方法")
        return {
            "valid": True,
            "validation_level": "deserialize",
            "message": "PKL反序列化及predict接口校验通过",
            "predictor_class": predictor.__class__.__name__,
            "restored_from_snapshot": restored,
            "expected_features": getattr(predictor, "n_features_in_", None),
        }
    raise ValueError("不支持的制品格式")


def _artifact_row(db, artifact_id: int, current_user: dict):
    row = db.execute(text("""
        SELECT a.id, a.model_id, a.model_version_id, a.format, a.file_name,
               a.file_path, a.size_bytes, a.sha256, a.validation_status,
               a.validation_report, a.benchmark_result, a.organization_id,
               a.created_at, a.validated_at, m.name AS model_name
        FROM model_artifacts a JOIN models m ON m.id=a.model_id
        WHERE a.id=:id
    """), {"id": artifact_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_organization_access(row["organization_id"], current_user)
    return row


def _serialize(row) -> dict:
    item = dict(row)
    item.pop("file_path", None)
    for key in ("created_at", "validated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    item["size_mb"] = round((item.get("size_bytes") or 0) / 1024 / 1024, 3)
    return item


@router.get("", dependencies=[Depends(require_permission("deployment:read"))])
def list_artifacts(db=Depends(get_db), current_user: dict = Depends(require_auth)):
    sql = """
        SELECT a.id, a.model_id, a.model_version_id, a.format, a.file_name,
               a.size_bytes, a.sha256, a.validation_status, a.validation_report,
               a.benchmark_result, a.organization_id, a.created_at, a.validated_at,
               m.name AS model_name
        FROM model_artifacts a JOIN models m ON m.id=a.model_id
    """
    params = {}
    if not is_admin(current_user):
        sql += " WHERE a.organization_id=:organization_id"
        params["organization_id"] = current_user.get("organization_id")
    sql += " ORDER BY a.id DESC"
    rows = db.execute(text(sql), params).mappings().all()
    return {"total": len(rows), "items": [_serialize(row) for row in rows]}


@router.post("", dependencies=[Depends(require_permission("deployment:write"))])
def create_artifact(
    request: ArtifactCreateRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    artifact_format = request.format.strip().lower()
    if artifact_format == "exe":
        raise HTTPException(
            status_code=501,
            detail="Windows EXE需要Windows x64构建节点，当前Linux服务器无法生成和验证",
        )
    if artifact_format not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail="目前支持ONNX、PKL和Docker构建包")

    context = _model_context(db, request.model_id, current_user)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = False
    source = None
    initial_report = {}
    try:
        if artifact_format == "onnx":
            source, initial_report = _export_onnx(context)
            extension = ".onnx"
        elif artifact_format == "pkl":
            source, temporary = _resolve_pkl(context, db)
            extension = ".pkl"
        else:
            if _is_user_script_predictor(context):
                pkl_path, pkl_temporary = _resolve_pkl(context, db)
                try:
                    source = _write_python_docker_bundle(context, pkl_path)
                finally:
                    if pkl_temporary:
                        Path(pkl_path).unlink(missing_ok=True)
                initial_report = {
                    "package_type": "foundation_docker_python",
                    "message": "已按用户脚本模型生成可复现Python Docker运行包",
                }
            else:
                onnx_path, initial_report = _export_onnx(context)
                source = _write_docker_bundle(context, onnx_path, initial_report)
            temporary = True
            extension = ".tar.gz"

        artifact_name = (
            f"{_safe_name(context['model_name'])}-ID{context['model_id']}-"
            f"{_safe_name(context['version'])}-{uuid.uuid4().hex[:8]}{extension}"
        )
        destination = ARTIFACT_DIR / artifact_name
        shutil.copy2(source, destination)
        try:
            report = {**initial_report, **_inspect_artifact(
                destination, artifact_format, context["model_id"], db
            )}
            status = "valid"
        except Exception as exc:
            report = {"valid": False, "message": str(exc)}
            status = "invalid"
        result = db.execute(text("""
            INSERT INTO model_artifacts
                (model_id, model_version_id, format, file_name, file_path,
                 size_bytes, sha256, validation_status, validation_report,
                 organization_id, created_by, created_at, validated_at)
            VALUES
                (:model_id, :version_id, :format, :file_name, :file_path,
                 :size_bytes, :sha256, :status, CAST(:report AS JSONB),
                 :organization_id, :created_by, NOW(), NOW())
            RETURNING id
        """), {
            "model_id": context["model_id"],
            "version_id": context["model_version_id"],
            "format": artifact_format,
            "file_name": artifact_name,
            "file_path": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
            "status": status,
            "report": json.dumps(report, ensure_ascii=False),
            "organization_id": context["organization_id"],
            "created_by": current_user.get("id"),
        })
        artifact_id = result.scalar_one()
        db.commit()
        row = _artifact_row(db, artifact_id, current_user)
        return _serialize(row)
    finally:
        if temporary and source:
            Path(source).unlink(missing_ok=True)


def _validate_input(value: Any) -> tuple[np.ndarray, Optional[pd.DataFrame]]:
    if not isinstance(value, list) or not value:
        raise HTTPException(status_code=400, detail="测试数据必须是非空JSON数组")
    if all(isinstance(item, dict) for item in value):
        frame = pd.DataFrame(value)
        numeric = frame.select_dtypes(include=[np.number])
        if numeric.empty:
            raise HTTPException(status_code=400, detail="对象数组中没有数值特征")
        return numeric.to_numpy(dtype=float), frame
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        value = [value]
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"测试数据必须是数值数组或对象数组: {exc}")
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1] or not np.isfinite(matrix).all():
        raise HTTPException(status_code=400, detail="数值测试数据必须是包含有限值的二维数组")
    return matrix, None


def _onnx_predictor(path: Path):
    import onnxruntime as ort
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise ValueError(f"性能测试只支持单输入制品，检测到{len(inputs)}个输入")
    model_input = inputs[0]

    def predict(values):
        return np.asarray(session.run(None, {
            model_input.name: np.asarray(values, dtype=np.float32)
        })[0])
    return predict, model_input.shape, session.get_outputs()[0].name


def _pkl_predictor(path: Path, model_id: int, db, object_frame: Optional[pd.DataFrame]):
    from api.model_evaluation import (
        _EvaluationArtifact, _extract_prediction_values, _load_serialized_model,
        _load_training_context, _predict_for_evaluation,
    )
    context = _load_training_context(db, model_id)
    predictor, restored = _load_serialized_model(
        str(path), context.get("code_snapshot_path"), context.get("script_source")
    )

    def predict(values):
        matrix = np.asarray(values, dtype=float)
        if object_frame is not None:
            frame = object_frame.copy()
        else:
            contract = _json_value(context.get("source_data_contract"))
            columns = contract.get("feature_columns") or context.get("feature_columns") or []
            frame = pd.DataFrame(matrix, columns=columns) if len(columns) == matrix.shape[1] else pd.DataFrame(matrix)
        artifact = _EvaluationArtifact(predictor, context, restored)
        if restored:
            return _predict_for_evaluation(
                artifact, matrix, frame,
                context.get("target_column") or _json_value(context.get("source_data_contract")).get("target_column"),
            )
        return _extract_prediction_values(predictor.predict(matrix), context.get("target_column"))
    expected = getattr(predictor, "n_features_in_", None)
    return predict, [None, expected] if expected else None, "prediction"


def _python_bundle_predictor(
    work: Path,
    metadata: dict,
    object_frame: Optional[pd.DataFrame],
):
    """Load and exercise the exact model/source pair contained in a Docker bundle."""
    from api.model_evaluation import (
        _EvaluationArtifact,
        _extract_prediction_values,
        _load_serialized_model,
        _predict_for_evaluation,
    )

    predictor, restored = _load_serialized_model(
        str(work / "model.pkl"), str(work / "runtime_source.py")
    )
    feature_columns = list(metadata.get("feature_columns") or [])
    target_column = metadata.get("target_column")
    time_column = metadata.get("time_column")
    context = {
        "artifact_kind": "user_script_predictor",
        "source_execution_mode": "execute_user_script",
        "feature_columns": feature_columns,
        "target_column": target_column,
        "time_column": time_column,
        "source_time_column": time_column,
        "source_data_contract": {
            "feature_columns": feature_columns,
            "target_column": target_column,
            "time_column": time_column,
        },
    }

    def predict(values):
        matrix = np.asarray(values, dtype=float)
        if object_frame is not None:
            frame = object_frame.copy()
        elif feature_columns and len(feature_columns) == matrix.shape[1]:
            frame = pd.DataFrame(matrix, columns=feature_columns)
        else:
            frame = pd.DataFrame(matrix)
        if time_column and time_column not in frame.columns and "时间" not in frame.columns:
            frame[time_column] = pd.date_range(
                end=pd.Timestamp.now().floor("min"), periods=len(frame), freq="min"
            )
        if time_column and time_column in frame.columns and "时间" not in frame.columns:
            frame["时间"] = frame[time_column]
        artifact = _EvaluationArtifact(predictor, context, restored)
        if restored:
            return _predict_for_evaluation(artifact, matrix, frame, target_column)
        return _extract_prediction_values(predictor.predict(frame), target_column)

    expected = metadata.get("expected_features") or len(feature_columns) or None
    return predict, [None, int(expected)] if expected else None, "prediction"


@router.post("/{artifact_id}/validate", dependencies=[Depends(require_permission("deployment:write"))])
def validate_artifact(
    artifact_id: int,
    request: ArtifactBenchmarkRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    row = _artifact_row(db, artifact_id, current_user)
    path = Path(row["file_path"])
    if not path.is_file() or _sha256(path) != row["sha256"]:
        raise HTTPException(status_code=400, detail="制品文件缺失或校验和已改变")
    matrix, object_frame = _validate_input(request.X)
    extracted = None
    try:
        if row["format"] == "onnx":
            predictor, input_shape, output_name = _onnx_predictor(path)
        elif row["format"] == "docker":
            package_type, _ = _docker_package_type(path)
            if package_type == "foundation_docker_python":
                extracted, metadata = _extract_python_docker(path)
                _validate_python_bundle_runtime(extracted, metadata)
                predictor, input_shape, output_name = _python_bundle_predictor(
                    extracted, metadata, object_frame
                )
            else:
                extracted, _ = _docker_onnx(path)
                predictor, input_shape, output_name = _onnx_predictor(extracted)
        elif row["format"] == "pkl":
            predictor, input_shape, output_name = _pkl_predictor(
                path, row["model_id"], db, object_frame
            )
        else:
            raise HTTPException(status_code=400, detail="该制品格式不支持性能测试")

        expected = input_shape[-1] if input_shape else None
        if (
            object_frame is None
            and isinstance(expected, int)
            and expected > 0
            and matrix.shape[1] != expected
        ):
            raise HTTPException(
                status_code=400,
                detail=f"测试数据有{matrix.shape[1]}个特征，但制品需要{expected}个",
            )
        predictions = predictor(matrix)
        latencies = []
        for _ in range(request.n_runs):
            started = time.perf_counter()
            predictor(matrix)
            latencies.append((time.perf_counter() - started) * 1000)
        values = np.asarray(latencies, dtype=float)
        result = {
            "valid": True,
            "artifact_id": artifact_id,
            "format": row["format"],
            "n_runs": request.n_runs,
            "n_samples": int(matrix.shape[0]),
            "input_shape": input_shape,
            "output_name": output_name,
            "avg_ms": round(float(values.mean()), 4),
            "p50_ms": round(float(np.percentile(values, 50)), 4),
            "p99_ms": round(float(np.percentile(values, 99)), 4),
            "throughput": round(float(request.n_runs * matrix.shape[0] * 1000 / values.sum()), 2),
            "predictions": np.asarray(predictions).tolist(),
        }
        db.execute(text("""
            UPDATE model_artifacts SET validation_status='valid',
                benchmark_result=CAST(:result AS JSONB), validated_at=NOW()
            WHERE id=:id
        """), {"id": artifact_id, "result": json.dumps(result, ensure_ascii=False)})
        db.commit()
        return result
    except HTTPException:
        raise
    except Exception as exc:
        db.execute(text("""
            UPDATE model_artifacts SET validation_status='invalid',
                validation_report=validation_report || CAST(:report AS JSONB),
                validated_at=NOW() WHERE id=:id
        """), {
            "id": artifact_id,
            "report": json.dumps({"benchmark_error": str(exc)}, ensure_ascii=False),
        })
        db.commit()
        raise HTTPException(status_code=400, detail=f"制品运行验证失败: {exc}") from exc
    finally:
        if extracted:
            if extracted.is_dir():
                shutil.rmtree(extracted, ignore_errors=True)
            else:
                extracted.unlink(missing_ok=True)


@router.get("/{artifact_id}/download", dependencies=[Depends(require_permission("deployment:read"))])
def download_artifact(
    artifact_id: int,
    db=Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    row = _artifact_row(db, artifact_id, current_user)
    path = Path(row["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="制品文件不存在")
    media = "application/gzip" if row["format"] == "docker" else "application/octet-stream"
    return FileResponse(str(path), media_type=media, filename=row["file_name"])


@router.delete("/{artifact_id}", dependencies=[Depends(require_permission("deployment:write"))])
def delete_artifact(
    artifact_id: int,
    db=Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    row = _artifact_row(db, artifact_id, current_user)
    Path(row["file_path"]).unlink(missing_ok=True)
    db.execute(text("DELETE FROM model_artifacts WHERE id=:id"), {"id": artifact_id})
    db.commit()
    return {"deleted": True, "id": artifact_id}
