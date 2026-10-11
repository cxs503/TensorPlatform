"""
部署 API
包括蓝绿部署和金丝雀发布
"""
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from services.deployment_strategy_service import (
    DeploymentStrategy,
    get_deployment_strategy_service,
)
from sqlalchemy import text
import io, pickle, os, json, time, tempfile, tarfile, shutil, re, __main__
from pathlib import Path
from datetime import datetime
import numpy as np
from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access, is_admin, require_deployment_access
from services.sandbox_model_artifact import write_runtime_source

router = APIRouter(prefix="/deployments", tags=["部署管理"])


# ── 策略桥接:DB 部署记录 <-> 内存 DeploymentStrategyService ──
# 内存服务用 string id(deploy_xxx);这里用 dep_id 的 str 形式作为 key,
# 这样同一个整数 dep_id 在内存服务里有一份对应的 Deployment 对象。
def _strategy_key(dep_id: int) -> str:
    return f"dep_{dep_id}"


def _get_or_init_strategy(dep_id: int, db):
    """从 DB 加载(或重建)一个内存 Deployment 用于策略操作。
    策略元信息存在 deployments.config jsonb 的 'strategy' 字段里。
    """
    service = get_deployment_strategy_service()
    key = _strategy_key(dep_id)
    if key in service.deployments:
        return service, service.deployments[key]
    row = db.execute(
        text("SELECT id, model_id, name, version, status, config FROM deployments WHERE id = :id"),
        {"id": dep_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部署不存在")
    _, model_id, name, version, status, config = row
    cfg = config if isinstance(config, dict) else (json.loads(config) if config else {})
    strategy = cfg.get("strategy", "rolling")
    # 从 config 重建版本列表
    versions = cfg.get("versions") or [{"version": version, "image": None, "replicas": 1}]
    name_or_default = name or f"deployment-{dep_id}"
    from services.deployment_strategy_service import Deployment as _MemDep, DeploymentVersion as _Ver
    mem = _MemDep(name_or_default, strategy, str(model_id))
    mem.id = key
    for v in versions:
        mem.versions.append(_Ver(v.get("version"), v.get("image"), v.get("replicas", 1)))
    if mem.versions and not mem.active_version:
        mem.active_version = mem.versions[-1].version
    service.deployments[key] = mem
    # 重建金丝雀流量规则
    canary_rules = cfg.get("canary_rules") or {}
    if canary_rules:
        from services.deployment_strategy_service import CanaryRelease as _CR
        cr = _CR(key)
        cr.traffic_rules = dict(canary_rules)
        service.canary_releases[key] = cr
    return service, mem


def _persist_strategy(dep_id: int, db):
    """把内存策略状态写回 deployments.config jsonb"""
    service = get_deployment_strategy_service()
    key = _strategy_key(dep_id)
    mem = service.deployments.get(key)
    row = db.execute(text("SELECT config FROM deployments WHERE id = :id"), {"id": dep_id}).fetchone()
    cfg = row[0] if row and row[0] else {}
    if not isinstance(cfg, dict):
        cfg = json.loads(cfg) if cfg else {}
    if mem:
        cfg["strategy"] = mem.strategy
        cfg["versions"] = [
            {"version": v.version, "image": v.image, "replicas": v.replicas, "status": v.status}
            for v in mem.versions
        ]
        cfg["active_version"] = mem.active_version
    cr = service.canary_releases.get(key)
    cfg["canary_rules"] = cr.traffic_rules if cr else {}
    db.execute(
        text("UPDATE deployments SET config = CAST(:cfg AS JSONB), updated_at = NOW() WHERE id = :id"),
        {"cfg": json.dumps(cfg), "id": dep_id},
    )
    db.commit()


def _resolve_registered_version(dep_id: int, version: str, db):
    """Resolve a strategy version to a version row owned by this deployment's model."""
    row = db.execute(
        text("SELECT mv.id, mv.version_number, d.model_id "
             "FROM deployments d "
             "JOIN model_versions mv ON mv.model_id = d.model_id "
             "WHERE d.id = :dep_id AND mv.version_number = :version "
             "ORDER BY mv.id DESC LIMIT 1"),
        {"dep_id": dep_id, "version": version},
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"版本 {version} 未在该部署对应的模型版本库中注册",
        )
    return row


def _sync_deployed_flags(model_id: int, db):
    """Keep model_versions.is_deployed derived from active deployment facts."""
    db.execute(
        text("""
            UPDATE model_versions mv
            SET is_deployed = EXISTS (
                SELECT 1 FROM deployments d
                WHERE d.model_version_id = mv.id
                  AND LOWER(d.status) IN
                      ('running', 'healthy', 'active', 'deployed', 'serving', 'rolling_back')
            )
            WHERE mv.model_id = :model_id
        """),
        {"model_id": model_id},
    )


# ── 部署打包产物生成(共享)──
SERVE_PY_TEMPLATE = '''# serve.py — 由开放数字化底座自动生成
from pathlib import Path

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="foundation Model Server")
model_path = Path(__file__).resolve().parent / "model.onnx"
session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
model_input = session.get_inputs()[0]
model_outputs = session.get_outputs()


class Input(BaseModel):
    features: list


@app.post("/predict")
def predict(req: Input):
    values = np.asarray(req.features, dtype=np.float32)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        return {"error": "features must be a one-dimensional sample or a two-dimensional batch"}
    outputs = session.run(None, {model_input.name: values})
    return {
        "prediction": np.asarray(outputs[0]).tolist(),
        "outputs": {
            item.name: np.asarray(value).tolist()
            for item, value in zip(model_outputs, outputs)
        },
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": True,
        "input_name": model_input.name,
        "input_shape": model_input.shape,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
'''

REQUIREMENTS_TXT = "fastapi==0.104.1\nuvicorn==0.24.0\nonnxruntime==1.18.0\nnumpy==1.26.4\n"

DOCKERFILE_TEMPLATE = '''FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser model.onnx /app/
COPY --chown=appuser:appuser serve.py /app/
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8080"]
'''


def _package_context(dep_id: int, db) -> dict:
    row = db.execute(text(
        "SELECT d.id, d.model_id, d.model_version_id, d.version, "
        "mv.version_number, mv.file_path, mv.parameters, "
        "m.framework, m.model_type, m.description, m.model_blob "
        "FROM deployments d JOIN models m ON m.id=d.model_id "
        "LEFT JOIN model_versions mv ON mv.id=d.model_version_id AND mv.model_id=d.model_id "
        "WHERE d.id=:id"
    ), {"id": dep_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部署不存在")
    if not row[2]:
        latest = db.execute(text(
            "SELECT id, version_number, file_path, parameters FROM model_versions "
            "WHERE model_id=:model_id ORDER BY created_at DESC, id DESC LIMIT 1"
        ), {"model_id": row[1]}).fetchone()
        if not latest:
            raise HTTPException(status_code=404, detail="部署关联模型没有可用版本")
        version_id, version_number, file_path, parameters = latest
    else:
        version_id, version_number, file_path, parameters = row[2], row[4], row[5], row[6]
    params = parameters if isinstance(parameters, dict) else (json.loads(parameters) if parameters else {})
    return {
        "deployment_id": row[0], "model_id": row[1], "model_version_id": version_id,
        "version": version_number or row[3] or "v1", "file_path": file_path,
        "parameters": params, "framework": row[7], "model_type": row[8],
        "description": row[9] or "", "model_blob": row[10],
    }


def _safe_version(value) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "v1")).strip("._") or "v1"


def _resolve_version_pkl(context: dict, db):
    """Resolve the exact registered version to a joblib artifact."""
    path = Path(str(context.get("file_path") or ""))
    if path.is_file() and path.suffix.lower() == ".pkl":
        return path, False
    if path.is_file() and path.suffix.lower() == ".onnx":
        raise HTTPException(status_code=400, detail="该版本是ONNX原生模型，不能伪装为PKL下载")
    if path.is_file() and path.suffix.lower() == ".pt":
        predictor = _load_pytorch_artifact(
            context["model_id"], context["model_version_id"], str(path), db
        )
        if predictor is not None:
            migrated = db.execute(text(
                "SELECT file_path FROM model_versions WHERE id=:id AND model_id=:model_id"
            ), {"id": context["model_version_id"], "model_id": context["model_id"]}).fetchone()
            migrated_path = Path(str(migrated[0] if migrated else ""))
            if migrated_path.is_file() and migrated_path.suffix.lower() == ".pkl":
                return migrated_path, False
    raw = context.get("model_blob")
    if raw:
        if isinstance(raw, (memoryview, bytearray)):
            raw = bytes(raw)
        fd, temporary = tempfile.mkstemp(suffix=".pkl", prefix="foundation-model-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        try:
            import joblib
            joblib.load(temporary)
        except Exception as exc:
            Path(temporary).unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"模型版本不是有效PKL产物: {exc}") from exc
        return Path(temporary), True
    raise HTTPException(status_code=404, detail="该部署选择的模型版本没有可用PKL产物")


def _cleanup_path(path: Path):
    path.unlink(missing_ok=True)


def _build_docker_tarball(context: dict, db) -> Path:
    import hashlib
    from api.onnx_api import validate_native_onnx

    onnx_result = _export_deployment_onnx(context["deployment_id"], db)
    onnx_path = Path(onnx_result["onnx_path"])
    input_spec = validate_native_onnx(onnx_path)
    tmpdir = Path(tempfile.mkdtemp(prefix="foundation-docker-package-"))
    try:
        (tmpdir / "serve.py").write_text(SERVE_PY_TEMPLATE, encoding="utf-8")
        (tmpdir / "requirements.txt").write_text(REQUIREMENTS_TXT, encoding="utf-8")
        (tmpdir / "Dockerfile").write_text(DOCKERFILE_TEMPLATE, encoding="utf-8")
        (tmpdir / "model.onnx").write_bytes(onnx_path.read_bytes())
        metadata = {
            "schema_version": 1, "package_type": "foundation_docker_onnx",
            "deployment_id": context["deployment_id"], "model_id": context["model_id"],
            "model_version_id": context["model_version_id"], "version": context["version"],
            "framework": context.get("framework"), "model_type": context.get("model_type"),
            "input_name": input_spec.get("input_name"), "input_shape": input_spec.get("input_shape"),
            "model_sha256": hashlib.sha256(onnx_path.read_bytes()).hexdigest(),
            "onnx_equivalence_verified": onnx_result.get("equivalence_verified", False),
            "onnx_max_abs_error": onnx_result.get("max_abs_error"),
        }
        (tmpdir / "model-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (tmpdir / "README.txt").write_text(
            "构建: docker build -t foundation-model .\n"
            "运行: docker run --rm -p 8080:8080 foundation-model\n"
            "健康检查: GET http://localhost:8080/health\n"
            "推理: POST /predict, JSON示例 {\"features\": [1.0, 2.0]}\n",
            encoding="utf-8",
        )
        fd, tar_name = tempfile.mkstemp(suffix=".tar.gz", prefix="foundation-docker-")
        os.close(fd)
        tar_path = Path(tar_name)
        with tarfile.open(str(tar_path), "w:gz") as tar:
            for f in sorted(tmpdir.iterdir()):
                tar.add(str(f), arcname=f.name)
        return tar_path
    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)


def _export_deployment_onnx(dep_id: int, db) -> dict:
    """Export the deployment's registered model through the canonical ONNX service."""
    context = _package_context(dep_id, db)
    model_id = context["model_id"]
    file_path = context.get("file_path")
    params = context.get("parameters") or {}
    source_path = Path(str(file_path or ""))
    if source_path.is_file() and source_path.suffix.lower() == ".onnx":
        try:
            from api.onnx_api import validate_native_onnx
            verification = validate_native_onnx(source_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"原生ONNX模型校验失败: {exc}") from exc
        return {
            "status": "success", "message": "原生ONNX模型已通过校验",
            "model_id": model_id, "onnx_path": str(source_path),
            "onnx_size_bytes": source_path.stat().st_size,
            "onnx_size_mb": round(source_path.stat().st_size / (1024 * 1024), 2),
            "download_url": f"/api/v1/deployments/{dep_id}/package/download?format=onnx",
            "filename": source_path.name, **verification,
        }
    job_id = params.get("job_id")
    if not job_id and file_path:
        stem = Path(file_path).stem
        job_id = stem[:-6] if stem.endswith("_model") else ""

    from api.onnx_api import ExportRequest, export_to_onnx
    result = export_to_onnx(ExportRequest(job_id=job_id or "", model_id=model_id))
    onnx_path = Path(result.get("onnx_path", ""))
    if not onnx_path.exists():
        raise HTTPException(status_code=500, detail="ONNX导出完成但产物文件不存在")
    return {
        **result,
        "model_id": model_id,
        "download_url": f"/api/v1/deployments/{dep_id}/package/download?format=onnx",
        "filename": onnx_path.name,
    }


def _load_pytorch_artifact(model_id: int, version_id: int, artifact_path: str, db):
    """Restore a trusted real-training PyTorch artifact and migrate it to joblib/blob."""
    try:
        from services.data_loader import load_dataset_data
        from services.real_training_service import (
            PyTorchPredictor, PyTorchTrainer, StandardScaler,
            TORCH_AVAILABLE, split_training_data, torch,
        )

        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch运行依赖不可用")
        native_path = Path(artifact_path).resolve()
        models_dir = Path("/app/uploads/models").resolve()
        if native_path.parent != models_dir or not native_path.name.startswith("train_"):
            raise ValueError("拒绝加载非训练服务生成的PyTorch文件")
        if not native_path.exists() or native_path.suffix != ".pt":
            raise ValueError(f"PyTorch权重文件不存在: {native_path}")

        job_id = native_path.stem[:-6] if native_path.stem.endswith("_model") else native_path.stem
        config_path = models_dir / f"{job_id}_config.json"
        run_path = Path("/app/uploads/runs") / f"{job_id}.json"
        if not config_path.exists() or not run_path.exists():
            raise ValueError("缺少PyTorch网络配置或训练运行记录")

        network_config = json.loads(config_path.read_text())
        run_config = (json.loads(run_path.read_text()).get("config") or {})
        model = PyTorchTrainer.create_model(
            int(network_config["input_dim"]),
            int(network_config.get("output_dim", 1)),
            network_config,
        )
        try:
            state_dict = torch.load(str(native_path), map_location="cpu", weights_only=True)
        except TypeError:
            state_dict = torch.load(str(native_path), map_location="cpu")
        model.load_state_dict(state_dict)
        model.eval()

        scaler = None
        if run_config.get("normalize", True):
            dataset_id = run_config.get("dataset_id")
            target_column = run_config.get("target_column") or None
            feature_columns = run_config.get("feature_columns") or None
            if not dataset_id:
                raise ValueError("训练记录缺少dataset_id，无法恢复特征标准化器")
            X, y, _ = load_dataset_data(
                db,
                int(dataset_id),
                feature_cols=feature_columns,
                target_col=target_column,
            )
            X_train, _, _, _ = split_training_data(
                X, y, run_config.get("split_strategy", "random")
            )
            scaler = StandardScaler().fit(X_train)

        predictor = PyTorchPredictor(model, scaler)
        deploy_path = models_dir / f"{job_id}_model.pkl"
        import joblib
        joblib.dump(predictor, deploy_path)
        raw = deploy_path.read_bytes()
        db.execute(
            text("UPDATE models SET model_blob=:blob, updated_at=NOW() WHERE id=:id"),
            {"blob": raw, "id": model_id},
        )
        db.execute(
            text("UPDATE model_versions SET file_path=:path WHERE id=:id AND model_id=:model_id"),
            {"path": str(deploy_path), "id": version_id, "model_id": model_id},
        )
        db.flush()
        return predictor
    except Exception as exc:
        print(f"[deploy] PyTorch产物恢复失败 model_id={model_id}: {exc}")
        return None


class _ONNXRuntimePredictor:
    """Adapt an ONNX Runtime session to the predict interface used by deployments."""

    def __init__(self, model_path: str):
        from services.onnx_exporter import get_onnx_inference_service

        self.service = get_onnx_inference_service()
        self.session = self.service.load_model(model_path)
        inputs = self.session.get_inputs()
        if len(inputs) != 1:
            raise ValueError(f"当前部署仅支持单输入ONNX模型，检测到 {len(inputs)} 个输入")
        shape = inputs[0].shape
        self.n_features_in_ = (
            int(shape[-1]) if shape and isinstance(shape[-1], int) else None
        )

    def predict(self, X):
        values = np.asarray(X, dtype=np.float32)
        return self.service.predict(self.session, values)


class _SandboxModelWrapper:
    """沙箱模型 wrapper：用沙箱 Python subprocess 做 predict（解决 numpy 版本 + 自定义类）。"""
    def __init__(
        self, model_id, blob_path, script_path, python_path="",
        n_features_in=None, feature_columns=None, required_frame_columns=None,
    ):
        self.model_id = model_id
        self.blob_path = blob_path
        self.script_path = script_path
        self.python_path = python_path
        self.feature_columns = list(feature_columns or [])
        self.required_frame_columns = list(required_frame_columns or [])
        if n_features_in is not None:
            self.n_features_in_ = int(n_features_in)
        elif self.feature_columns:
            self.n_features_in_ = len(self.feature_columns)

    def predict(self, X):
        import subprocess, json as _json, tempfile, numpy as _np, glob
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            _json.dump(_np.asarray(X).tolist(), f)
            input_path = f.name
        code = (
            "import joblib,json,sys\n"
            "namespace={'__name__':'__main__'}\n"
            f"exec(compile(open({self.script_path!r},encoding='utf-8').read(),{self.script_path!r},'exec'),namespace)\n"
            "vars(sys.modules['__main__']).update(namespace)\n"
            f"model=joblib.load({self.blob_path!r})\n"
            f"X=json.load(open({input_path!r}))\n"
            "import numpy as np;X=np.array(X,dtype=float)\n"
            f"feature_columns={self.feature_columns!r}\n"
            f"required_frame_columns={self.required_frame_columns!r}\n"
            "if feature_columns:\n"
            " import pandas as pd\n"
            " if X.ndim != 2 or X.shape[1] != len(feature_columns):\n"
            "  raise ValueError(f'输入特征数量不匹配: expected={len(feature_columns)}, actual={X.shape}')\n"
            " X=pd.DataFrame(X,columns=feature_columns)\n"
            " for column in required_frame_columns:\n"
            "  if column not in X.columns:\n"
            "   if '时间' in column or 'time' in column.lower() or 'date' in column.lower():\n"
            "    X[column]=pd.date_range(end=pd.Timestamp.now().floor('min'),periods=len(X),freq='min')\n"
            "   else:\n"
            "    X[column]=0.0\n"
            "r=model.predict(X)\n"
            "if hasattr(r,'columns'):\n"
            " preferred=[]\n"
            " for token in ('最终预测','prediction','预测值','预测'):\n"
            "  preferred=[column for column in r.columns if token.lower() in str(column).lower()]\n"
            "  if preferred: break\n"
            " if preferred:\n"
            "  r=r[preferred[-1]].to_numpy()\n"
            " else:\n"
            "  numeric=r.select_dtypes(include='number')\n"
            "  if numeric.shape[1] == 0: raise TypeError('模型预测报表没有数值输出列')\n"
            "  r=numeric.iloc[:,-1].to_numpy()\n"
            "elif hasattr(r,'to_numpy'):\n"
            " r=r.to_numpy()\n"
            "print(json.dumps(np.asarray(r).tolist()))\n"
        )
        # 用户选择的 Python 路径优先；否则找沙箱 runtime
        if self.python_path:
            py = self.python_path
        else:
            runtimes = sorted(glob.glob("/app/uploads/sandbox_environments/builds/runtime_*/bin/python"))
            py = runtimes[-1] if runtimes else "/opt/venv/bin/python"
        try:
            result = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return _np.array(_json.loads(result.stdout))
            raise RuntimeError(f"subprocess: {result.stderr[-500:]}")
        finally:
            os.unlink(input_path)


def _load_sandbox_model_subprocess(
    model_id, raw, script_source, db, python_path="", n_features_in=None, feature_columns=None,
):
    """沙箱模型通过 subprocess 加载（沙箱环境 Python + exec 类定义）。"""
    import tempfile, os
    tmp_dir = tempfile.mkdtemp(prefix="sandbox_deploy_")
    blob_path = os.path.join(tmp_dir, "model.pkl")
    script_path = os.path.join(tmp_dir, "source.py")
    with open(blob_path, "wb") as f:
        f.write(raw)
    write_runtime_source(script_source, Path(script_path))
    # Custom predictors often require a named DataFrame instead of a bare
    # ndarray. Infer literal ``df['column']`` requirements from the retained
    # class/function definitions. Time columns are synthesized at serving time
    # when the training feature contract intentionally kept them separate.
    feature_set = set(feature_columns or [])
    required_frame_columns = []
    for match in re.finditer(
        r"\b(?:df|df_new)\s*\[\s*['\"]([^'\"]+)['\"]\s*\]",
        script_source or "",
    ):
        column = match.group(1)
        if column not in feature_set and column not in required_frame_columns:
            required_frame_columns.append(column)
    print(f"[deploy] model {model_id} -> sandbox subprocess wrapper (python={python_path or 'auto'})")
    return _SandboxModelWrapper(
        model_id, blob_path, script_path, python_path,
        n_features_in=n_features_in, feature_columns=feature_columns,
        required_frame_columns=required_frame_columns,
    )


def _load_model_blob(model_id: int, db, python_path=""):
    """Load a deployable predictor from blob or a trusted registered artifact.
    如果模型有自定义类（沙箱保存的脚本），先 exec 脚本源码定义类，再 unpickle。"""
    # 先查是否有脚本源码（沙箱保存的模型有 script_source 在 parameters 里）
    script_source = ""
    artifact_params = {}
    try:
        vrow = db.execute(
            text("SELECT parameters FROM model_versions WHERE model_id=:id ORDER BY id DESC LIMIT 1"),
            {"id": model_id},
        ).fetchone()
        if vrow and vrow[0]:
            params = json.loads(vrow[0]) if isinstance(vrow[0], str) else vrow[0]
            artifact_params = params if isinstance(params, dict) else {}
            script_source = artifact_params.get("script_source", "")
    except Exception:
        pass

    try:
        row = db.execute(
            text("SELECT model_blob FROM models WHERE id = :id"), {"id": model_id}
        ).fetchone()
    except Exception as e:
        print(f"[deploy] 查询 model_blob 失败 model_id={model_id}: {e}")
        return None
    if row and row[0]:
        raw = row[0]
        if isinstance(raw, (memoryview, bytearray)):
            raw = bytes(raw)
        try:
            import joblib
            return joblib.load(io.BytesIO(raw))
        except Exception as e:
            print(f"[deploy] joblib 反序列化失败 model_id={model_id}: {e}")
            # 沙箱保存的模型：用沙箱环境的 Python（numpy 2.x）通过 subprocess 加载
            if script_source:
                try:
                    registered_python = str(artifact_params.get("python_path") or "")
                    return _load_sandbox_model_subprocess(
                        model_id, raw, script_source, db, python_path or registered_python,
                        n_features_in=artifact_params.get("n_features_in"),
                        feature_columns=artifact_params.get("feature_columns") or [],
                    )
                except Exception as e3:
                    print(f"[deploy] 沙箱 subprocess 加载失败 model_id={model_id}: {e3}")
            try:
                return pickle.load(io.BytesIO(raw))
            except Exception as e2:
                print(f"[deploy] pickle 兜底也失败 model_id={model_id}: {e2}")

    artifact = db.execute(
        text(
            "SELECT mv.id, mv.file_path, m.framework, m.description "
            "FROM models m JOIN model_versions mv ON mv.model_id=m.id "
            "WHERE m.id=:id ORDER BY mv.created_at DESC, mv.id DESC LIMIT 1"
        ),
        {"id": model_id},
    ).fetchone()
    if not artifact or not artifact[1]:
        return None
    version_id, file_path, framework, description = artifact
    path = Path(file_path)
    if path.suffix.lower() == ".onnx" and path.exists():
        try:
            return _ONNXRuntimePredictor(str(path))
        except Exception as exc:
            print(f"[deploy] ONNX产物加载失败 model_id={model_id}: {exc}")
            return None
    if path.suffix.lower() == ".pkl" and path.exists():
        try:
            import joblib
            return joblib.load(path)
        except Exception as exc:
            print(f"[deploy] 文件产物加载失败 model_id={model_id}: {exc}")
            return None
    trusted = (description or "").startswith("训练自动注册 (")
    if str(framework or "").lower() == "pytorch" and trusted:
        return _load_pytorch_artifact(model_id, version_id, file_path, db)
    return None


# --- Model Packaging ---
@router.post("/{dep_id}/package", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def package_deployment(dep_id: int, format: str = "docker", db=Depends(get_db)):
    context = _package_context(dep_id, db)
    model_id = context["model_id"]
    version = _safe_version(context["version"])

    if format == "docker":
        onnx_result = _export_deployment_onnx(dep_id, db)
        return {
            "format": "docker",
            "files": ["Dockerfile", "serve.py", "requirements.txt", "model.onnx", "model-metadata.json", "README.txt"],
            "download_url": f"/api/v1/deployments/{dep_id}/package/download?format=docker",
            "build_cmd": "tar xzf model-{m}-{v}-docker.tar.gz && docker build -t model-{m}:{v} .".format(m=model_id, v=version),
            "run_cmd": "docker run --rm -p 8080:8080 model-{m}:{v}".format(m=model_id, v=version),
            "onnx_equivalence_verified": onnx_result.get("equivalence_verified", False),
        }
    elif format == "onnx":
        result = _export_deployment_onnx(dep_id, db)
        return {"format": "onnx", **result}
    elif format == "pkl":
        pkl_path, temporary = _resolve_version_pkl(context, db)
        if temporary:
            pkl_path.unlink(missing_ok=True)
        return {
            "format": "pkl", "file": "model_{m}_{v}.pkl".format(m=model_id, v=version),
            "download_url": f"/api/v1/deployments/{dep_id}/package/download?format=pkl",
            "portable": str(context.get("framework") or "").lower() != "pytorch",
            "notice": "PKL需要兼容的Python依赖环境；PyTorch PKL还依赖数字化底座运行类",
        }
    elif format == "exe":
        raise HTTPException(status_code=501, detail="Windows EXE需要Windows x64构建节点，当前Linux服务不生成伪EXE")
    raise HTTPException(status_code=400, detail="不支持的打包格式: " + format)


@router.get("/{dep_id}/package/download", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def download_package(dep_id: int, format: str = "docker", db=Depends(get_db)):
    """打包产物以文件形式下载(前端 window.open 触发)"""
    from fastapi.responses import FileResponse
    import tempfile
    context = _package_context(dep_id, db)
    model_id = context["model_id"]
    version = _safe_version(context["version"])
    if format == "pkl":
        pkl_path, temporary = _resolve_version_pkl(context, db)
        return FileResponse(
            str(pkl_path), media_type="application/octet-stream",
            filename=f"model_{model_id}_{version}.pkl",
            background=BackgroundTask(_cleanup_path, pkl_path) if temporary else None,
        )
    if format == "onnx":
        result = _export_deployment_onnx(dep_id, db)
        onnx_path = Path(result["onnx_path"])
        return FileResponse(
            str(onnx_path),
            media_type="application/octet-stream",
            filename=result.get("filename") or onnx_path.name,
        )
    if format == "docker":
        tar_path = _build_docker_tarball(context, db)
        fname = f"model-{model_id}-{version}-docker.tar.gz"
        return FileResponse(
            str(tar_path),
            media_type="application/gzip",
            filename=fname,
            background=BackgroundTask(_cleanup_path, tar_path),
        )
    if format == "exe":
        raise HTTPException(status_code=501, detail="Windows EXE需要Windows x64构建节点，当前Linux服务不生成伪EXE")
    raise HTTPException(status_code=400, detail=f"不支持的打包格式: {format}")


@router.get("/{dep_id}/health", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def health_check(dep_id: int, db=Depends(get_db)):
    """部署健康检查:加载模型 + 用零向量探针 predict,返回健康状态与延迟。

    返回 {deployment_id, model_id, healthy, latency_ms, error?}
    """
    row = db.execute(
        text("SELECT id, model_id, version, status FROM deployments WHERE id = :id"),
        {"id": dep_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部署不存在")
    _, model_id, version, status = row

    # 模型不存在或没有任何可恢复产物
    mrow = db.execute(
        text("SELECT id FROM models WHERE id = :id"),
        {"id": model_id},
    ).fetchone()
    if not mrow:
        return {"deployment_id": dep_id, "model_id": model_id, "healthy": False,
                "latency_ms": 0, "error": f"模型 {model_id} 不存在"}
    t0 = time.time()
    try:
        model = _load_model_blob(model_id, db)
        if model is None:
            return {"deployment_id": dep_id, "model_id": model_id, "healthy": False,
                    "latency_ms": 0, "error": "没有可加载的部署产物"}
        n = getattr(model, "n_features_in_", 4)
        model.predict(np.zeros((1, n)))
        latency = round((time.time() - t0) * 1000, 2)
        return {
            "deployment_id": dep_id, "model_id": model_id,
            "version": version, "status": status,
            "healthy": True, "latency_ms": latency,
            "n_features_in": n,
            "model_type": type(model).__name__,
        }
    except Exception as e:
        latency = round((time.time() - t0) * 1000, 2)
        return {"deployment_id": dep_id, "model_id": model_id, "healthy": False,
                "latency_ms": latency, "error": str(e)}


strategy_router = APIRouter(prefix="/deployment-strategy", tags=["部署策略"])




@router.get("/environments", dependencies=[Depends(require_permission("deployment:read"))])
def list_environments():
    """列出可用的 Python 环境（后端 + 沙箱 runtime），供部署时选择。"""
    import glob, subprocess
    envs = [{"name": "后端默认 (/opt/venv)", "path": "/opt/venv/bin/python", "numpy": "1.24.3", "torch": "2.1.0", "is_default": True}]
    for rt_dir in sorted(glob.glob("/app/uploads/sandbox_environments/builds/runtime_*")):
        py_path = os.path.join(rt_dir, "bin/python")
        if not os.path.isfile(py_path): continue
        name = os.path.basename(rt_dir)
        numpy_ver = torch_ver = ""
        try: numpy_ver = subprocess.run([py_path, "-c", "import numpy;print(numpy.__version__)"], capture_output=True, text=True, timeout=5).stdout.strip()
        except: pass
        try: torch_ver = subprocess.run([py_path, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True, timeout=5).stdout.strip()
        except: torch_ver = ""
        envs.append({"name": name, "path": py_path, "numpy": numpy_ver, "torch": torch_ver, "is_default": False})
    return {"items": envs}


def _validate_deployment_python_path(value: str) -> str:
    """Only accept the backend interpreter or a managed sandbox runtime."""
    if not value:
        return ""
    candidate = Path(value)
    allowed = {"/opt/venv/bin/python"}
    allowed.update(
        str(path) for path in Path("/app/uploads/sandbox_environments/builds").glob("runtime_*/bin/python")
        if path.is_file()
    )
    if str(candidate) not in allowed:
        raise HTTPException(status_code=400, detail="只能选择平台管理的 Python 环境")
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail="所选 Python 环境不存在或已被删除")
    return str(candidate)


@router.post("", dependencies=[Depends(require_permission("deployment:write"))])
def create_deployment(req: dict, db=Depends(get_db), current_user: dict = Depends(require_auth)):
    model_id = req.get("model_id")
    requested_version = req.get("version")
    requested_version_id = req.get("model_version_id")
    python_path = _validate_deployment_python_path(req.get("python_path", ""))
    name = req.get("name")
    strategy = req.get("strategy")
    valid_strategies = {"blue_green", "canary", "rolling", "recreate"}
    if strategy is not None and strategy not in valid_strategies:
        raise HTTPException(
            status_code=400,
            detail=f"非法 strategy,可选: {sorted(valid_strategies)}",
        )

    # --- 部署前验证:模型权重必须存在 + 可加载,否则拒绝 ---
    if not model_id:
        raise HTTPException(status_code=400, detail="缺少 model_id")
    mrow = db.execute(
        text("SELECT id, name, organization_id, framework FROM models WHERE id = :id"),
        {"id": model_id},
    ).fetchone()
    if not mrow:
        raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")
    assert_organization_access(mrow[2], current_user)

    model = _load_model_blob(model_id, db, python_path=python_path)
    if model is None:
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model_id} 没有可加载的部署产物(blob/pkl/pt均不可用)",
        )
    n_features = getattr(model, "n_features_in_", None)
    # 确认可预测(零向量探针),失败则视为不可服务
    try:
        n = n_features or 4
        model.predict(np.zeros((1, n)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模型权重不可用:predict 探针失败 - {e}")

    # A deployment must reference an immutable, already registered model
    # version. Deployment creation must never manufacture an empty version row.
    if requested_version_id is not None:
        existing = db.execute(
            text("SELECT id, version_number FROM model_versions "
                 "WHERE id = :id AND model_id = :model_id"),
            {"id": requested_version_id, "model_id": model_id},
        ).fetchone()
    elif requested_version:
        existing = db.execute(
            text("SELECT id, version_number FROM model_versions "
                 "WHERE model_id = :model_id AND version_number = :version "
                 "ORDER BY id DESC LIMIT 1"),
            {"model_id": model_id, "version": requested_version},
        ).fetchone()
    else:
        existing = db.execute(
            text("SELECT id, version_number FROM model_versions "
                 "WHERE model_id = :model_id ORDER BY id DESC LIMIT 1"),
            {"model_id": model_id},
        ).fetchone()

    if not existing:
        detail = (
            f"模型版本 {requested_version_id or requested_version} 不存在或不属于模型 {model_id}"
            if requested_version_id is not None or requested_version
            else f"模型 {model_id} 尚无可部署版本"
        )
        raise HTTPException(status_code=400, detail=detail)

    model_version_id, version = existing

    result = db.execute(
        text(
            "INSERT INTO deployments (model_id, model_version_id, name, version, status, config) "
            "VALUES (:m, :mvid, :n, :v, 'running', :cfg) RETURNING id"
        ),
        {
            "m": model_id, "mvid": model_version_id, "n": name, "v": version,
            "cfg": json.dumps({
                "model_verified": True,
                "verified_at": datetime.utcnow().isoformat(),
                "n_features_in": n_features,
                "model_type": type(model).__name__,
                "python_path": python_path or getattr(model, "python_path", "") or "",
            }),
        },
    )
    dep_id = result.fetchone()[0]
    db.execute(
        text("UPDATE model_versions SET is_deployed = true WHERE id = :id"),
        {"id": model_version_id},
    )
    db.commit()

    # --- 策略初始化:如果 body 带 strategy,自动在内存策略服务里建好
    # 对应的 Deployment 对象并写回 config jsonb。这样前端创建部署时传
    # strategy 就自动生效,无需额外调 /deployment-strategy 端点。
    if strategy:
        image = req.get("image")
        replicas = req.get("replicas", 1)
        # _get_or_init_strategy 从 DB 行重建内存 Deployment(含 model_verified 等元数据),
        # 默认 strategy='rolling' + 一个版本。这里覆盖策略并补充镜像/副本信息。
        service, mem = _get_or_init_strategy(dep_id, db)
        mem.strategy = strategy
        if mem.versions:
            v0 = mem.versions[0]
            v0.image = image
            v0.replicas = replicas
        # 金丝雀:初始化 90/10 流量规则(单版本时旧版本 100%,新版本 0%;
        # 此处只有一个版本,记录其初始流量)。
        key = _strategy_key(dep_id)
        if strategy == "canary":
            from services.deployment_strategy_service import CanaryRelease as _CR
            cr = _CR(key)
            cr.traffic_rules[version] = 100
            service.canary_releases[key] = cr
        _persist_strategy(dep_id, db)

    return {
        "id": dep_id,
        "model_id": model_id,
        "model_version_id": model_version_id,
        "version": version,
        "status": "running",
        "endpoint": f"/deployments/{dep_id}/predict",
        "strategy": strategy,  # None 表示默认 rolling(未显式初始化)
    }


class RedeployReq(BaseModel):
    model_id: int
    model_version_id: int | None = None


@router.put("/{dep_id}/redeploy", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def redeploy_deployment(dep_id: int, req: RedeployReq, db=Depends(get_db), current_user: dict = Depends(require_auth)):
    """把一个部署切换到另一个 model 记录(及其版本)。

    用于"同一算法(源码)分组下切换/回退到兄弟 model 记录"——现有 switch/rollback
    只能切同一 model 内的 MLflow 版本, 本接口改 deployment 的 model_id。
    dep_id 与推理地址保持不变。
    """
    dep = db.execute(
        text("SELECT model_id FROM deployments WHERE id = :id"), {"id": dep_id}
    ).fetchone()
    if not dep:
        raise HTTPException(status_code=404, detail="部署不存在")
    old_model_id = dep[0]

    # 目标 model 校验 + 组织权限
    mrow = db.execute(
        text("SELECT id, organization_id FROM models WHERE id = :id"), {"id": req.model_id}
    ).fetchone()
    if not mrow:
        raise HTTPException(status_code=404, detail=f"目标模型 {req.model_id} 不存在")
    assert_organization_access(mrow[1], current_user)

    # 探针: 目标模型权重必须可加载可预测(与 create_deployment 一致)
    model = _load_model_blob(req.model_id, db)
    if model is None:
        raise HTTPException(status_code=400, detail=f"目标模型 {req.model_id} 没有可加载的部署产物")
    try:
        n = getattr(model, "n_features_in_", None) or 4
        model.predict(np.zeros((1, n)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"目标模型不可用: predict 探针失败 - {e}")

    # 解析目标 model_version(传了校验归属, 否则取最新)
    if req.model_version_id is not None:
        existing = db.execute(
            text("SELECT id, version_number FROM model_versions WHERE id = :id AND model_id = :mid"),
            {"id": req.model_version_id, "mid": req.model_id},
        ).fetchone()
    else:
        existing = db.execute(
            text("SELECT id, version_number FROM model_versions WHERE model_id = :mid ORDER BY id DESC LIMIT 1"),
            {"mid": req.model_id},
        ).fetchone()
    if not existing:
        raise HTTPException(status_code=400, detail=f"目标模型 {req.model_id} 尚无可部署版本")
    new_mvid, new_version = existing

    # 切换部署指向 + 同步标志 + 失效运行期缓存(下次 predict 重载新模型)
    db.execute(
        text("UPDATE deployments SET model_id = :mid, model_version_id = :mvid, version = :v, "
             "status = 'running', updated_at = NOW() WHERE id = :id"),
        {"mid": req.model_id, "mvid": new_mvid, "v": new_version, "id": dep_id},
    )
    _sync_deployed_flags(old_model_id, db)
    _sync_deployed_flags(req.model_id, db)
    _loaded_models.pop(str(dep_id), None)
    db.commit()

    return {
        "id": dep_id,
        "model_id": req.model_id,
        "model_version_id": new_mvid,
        "version": new_version,
        "status": "running",
        "endpoint": f"/deployments/{dep_id}/predict",
    }


@router.get("", dependencies=[Depends(require_permission("deployment:read"))])
def list_deployments(db=Depends(get_db), current_user: dict = Depends(require_auth)):
    sql = """
            SELECT d.id, d.model_id, d.name, d.version, d.status, d.config,
                   d.created_at, d.updated_at, d.model_version_id,
                   m.name AS model_name, m.mlflow_name,
                   mv.version_number, mv.mlflow_run_id, mv.mlflow_model_version
            FROM deployments d
            LEFT JOIN models m ON m.id = d.model_id
            LEFT JOIN model_versions mv ON mv.id = d.model_version_id
    """
    params = None
    if not is_admin(current_user):
        sql += " WHERE m.organization_id = :organization_id"
        params = {"organization_id": current_user.get("organization_id")}
    sql += " ORDER BY d.id DESC"
    result = (db.execute(text(sql), params) if params else db.execute(text(sql))).fetchall()
    items = []
    active_statuses = {"running", "healthy", "active", "deployed", "serving"}
    for row in result:
        config = row[5] if isinstance(row[5], dict) else (json.loads(row[5]) if row[5] else {})
        status = str(row[4]).lower() if row[4] is not None else None
        items.append({
            "id": row[0],
            "model_id": row[1],
            "model_version_id": row[8],
            "model_name": row[9],
            "mlflow_name": row[10],
            "model_version": row[11] or row[3],
            "mlflow_run_id": row[12],
            "mlflow_model_version": row[13],
            "name": row[2],
            "version": row[3],
            "status": status,
            "strategy": config.get("strategy", "rolling"),
            "endpoint": f"/deployments/{row[0]}/predict",
            "n_features_in": config.get("n_features_in"),
            "is_active": status in active_statuses,
            "created_at": row[6].isoformat() if row[6] else None,
            "updated_at": row[7].isoformat() if row[7] else None,
        })
    return {
        "total": len(items),
        "active": sum(1 for item in items if item["is_active"]),
        "items": items,
    }


@router.get("/{dep_id}", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def get_deployment(dep_id: int, db=Depends(get_db)):
    result = db.execute(
        text("""
            SELECT d.id, d.model_id, d.version, d.status, d.model_version_id,
                   m.name, m.mlflow_name, mv.version_number,
                   mv.mlflow_run_id, mv.mlflow_model_version
            FROM deployments d
            LEFT JOIN models m ON m.id = d.model_id
            LEFT JOIN model_versions mv ON mv.id = d.model_version_id
            WHERE d.id = :id
        """),
        {"id": dep_id},
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="部署不存在")
    return {
        "id": result[0],
        "model_id": result[1],
        "model_version_id": result[4],
        "model_name": result[5],
        "mlflow_name": result[6],
        "model_version": result[7] or result[2],
        "mlflow_run_id": result[8],
        "mlflow_model_version": result[9],
        "version": result[2],
        "status": result[3],
    }


@router.delete("/{dep_id}", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def delete_deployment(dep_id: int, db=Depends(get_db)):
    deleted = db.execute(
        text("DELETE FROM deployments WHERE id = :id RETURNING model_id, model_version_id"),
        {"id": dep_id},
    ).fetchone()
    if not deleted:
        raise HTTPException(status_code=404, detail="部署不存在")
    _sync_deployed_flags(deleted[0], db)
    db.commit()
    return {"message": "删除成功", "id": dep_id}


# ========== 部署策略 API ==========


@strategy_router.post("")
def create_deployment_with_strategy(req: dict):
    """创建支持部署策略的部署"""
    service = get_deployment_strategy_service()
    deployment = service.create_deployment(
        name=req.get("name"),
        strategy=req.get("strategy", "rolling"),
        model_id=req.get("model_id"),
        version=req.get("version", "1.0.0"),
        image=req.get("image"),
        replicas=req.get("replicas", 2),
    )
    return {
        "id": deployment.id,
        "name": deployment.name,
        "strategy": deployment.strategy,
    }


@strategy_router.get("")
def list_deployments_with_strategy():
    """列出所有部署"""
    service = get_deployment_strategy_service()
    return service.list_deployments()


@strategy_router.get("/{deployment_id}")
def get_deployment_strategy(deployment_id: str):
    """获取部署详情"""
    service = get_deployment_strategy_service()
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="部署不存在")
    return {
        "id": deployment.id,
        "name": deployment.name,
        "strategy": deployment.strategy,
        "model_id": deployment.model_id,
        "versions": [
            {"version": v.version, "status": v.status, "replicas": v.replicas}
            for v in deployment.versions
        ],
        "active_version": deployment.active_version,
    }


@strategy_router.post("/{deployment_id}/versions")
def add_version(deployment_id: str, req: dict):
    """添加新版本"""
    service = get_deployment_strategy_service()
    deployment = service.add_version(
        deployment_id=deployment_id,
        version=req.get("version"),
        image=req.get("image"),
        replicas=req.get("replicas", 2),
    )
    return {"message": "版本添加成功", "active_version": deployment.active_version}


@strategy_router.post("/{deployment_id}/switch")
def switch_version(deployment_id: str, req: dict):
    """切换版本"""
    service = get_deployment_strategy_service()
    success = service.switch_version(deployment_id, req.get("version"))
    if not success:
        raise HTTPException(status_code=404, detail="部署或版本不存在")
    return {"message": "切换成功"}


@strategy_router.post("/{deployment_id}/blue-green/promote")
def blue_green_promote(deployment_id: str, req: dict):
    """蓝绿部署：切换到新版本"""
    service = get_deployment_strategy_service()
    success = service.blue_green_promote(deployment_id, req.get("new_version"))
    if not success:
        raise HTTPException(status_code=404, detail="部署或版本不存在")
    return {"message": "蓝绿部署切换成功"}


@strategy_router.post("/{deployment_id}/canary/adjust-traffic")
def canary_adjust_traffic(deployment_id: str, req: dict):
    """金丝雀：调整流量"""
    service = get_deployment_strategy_service()
    success = service.canary_adjust_traffic(
        deployment_id, req.get("version"), req.get("percentage", 10)
    )
    if not success:
        raise HTTPException(status_code=404, detail="部署或金丝雀规则不存在")
    return {"message": "流量调整成功"}


@strategy_router.post("/{deployment_id}/canary/promote")
def canary_promote(deployment_id: str):
    """金丝雀：完全切换到新版本"""
    service = get_deployment_strategy_service()
    success = service.canary_promote(deployment_id)
    if not success:
        raise HTTPException(status_code=404, detail="部署不存在")
    return {"message": "金丝雀发布完成"}


@strategy_router.post("/{deployment_id}/rollback")
def rollback_deployment(deployment_id: str):
    """回滚到上一版本"""
    service = get_deployment_strategy_service()
    success = service.rollback(deployment_id)
    if not success:
        raise HTTPException(status_code=404, detail="部署不存在或无上一版本")
    return {"message": "回滚成功"}


# ========== 部署策略 REST 端点(整数 dep_id,桥接内存服务 + DB config) ==========


class StrategyReq(BaseModel):
    strategy: str = "rolling"  # blue_green | canary | rolling | recreate
    model_id: int | None = None
    config: dict = {}


class VersionReq(BaseModel):
    version: str
    image: str | None = None
    replicas: int = 1


class SwitchReq(BaseModel):
    version: str


class CanaryReq(BaseModel):
    version: str
    percentage: int = 10


@router.post("/{dep_id}/strategy", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def set_deployment_strategy(dep_id: int, req: StrategyReq, db=Depends(get_db)):
    """A1 创建/更新部署策略。将策略写入 deployments.config jsonb。"""
    service, mem = _get_or_init_strategy(dep_id, db)
    valid = {"blue_green", "canary", "rolling", "recreate"}
    if req.strategy not in valid:
        raise HTTPException(status_code=400, detail=f"非法 strategy,可选: {valid}")
    mem.strategy = req.strategy
    if req.model_id is not None:
        mem.model_id = str(req.model_id)
    # 合并额外 config
    if req.config:
        row = db.execute(text("SELECT config FROM deployments WHERE id=:id"), {"id": dep_id}).fetchone()
        cfg = row[0] if row and row[0] else {}
        if not isinstance(cfg, dict):
            cfg = json.loads(cfg) if cfg else {}
        cfg["strategy"] = req.strategy
        cfg.setdefault("strategy_config", {}).update(req.config)
        db.execute(text("UPDATE deployments SET config=CAST(:c AS JSONB), updated_at=NOW() WHERE id=:id"),
                   {"c": json.dumps(cfg), "id": dep_id})
        db.commit()
    _persist_strategy(dep_id, db)
    return {"status": "ok", "deployment_id": dep_id, "strategy": mem.strategy,
            "active_version": mem.active_version, "versions": len(mem.versions)}


@router.post("/{dep_id}/versions", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def add_version_to_deployment(dep_id: int, req: VersionReq, db=Depends(get_db)):
    """A2 添加新版本到部署。"""
    _resolve_registered_version(dep_id, req.version, db)
    service, mem = _get_or_init_strategy(dep_id, db)
    try:
        service.add_version(_strategy_key(dep_id), req.version, req.image, req.replicas)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _persist_strategy(dep_id, db)
    return {"status": "ok", "deployment_id": dep_id, "added_version": req.version,
            "active_version": mem.active_version,
            "versions": [{"version": v.version, "image": v.image, "replicas": v.replicas} for v in mem.versions]}


@router.put("/{dep_id}/switch", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def switch_deployment_version(dep_id: int, req: SwitchReq, db=Depends(get_db)):
    """A3 切换版本(蓝绿切换 / 金丝雀全量)。"""
    version_id, version, model_id = _resolve_registered_version(dep_id, req.version, db)
    service, mem = _get_or_init_strategy(dep_id, db)
    ok = service.switch_version(_strategy_key(dep_id), req.version)
    if not ok:
        raise HTTPException(status_code=404, detail="部署或版本不存在")
    # 同步 deployments 表的 version 字段
    db.execute(text("UPDATE deployments SET version=:v, model_version_id=:vid, "
                    "status='healthy', updated_at=NOW() WHERE id=:id"),
               {"v": version, "vid": version_id, "id": dep_id})
    _sync_deployed_flags(model_id, db)
    db.commit()
    _persist_strategy(dep_id, db)
    return {"status": "ok", "deployment_id": dep_id, "active_version": req.version}


@router.put("/{dep_id}/canary", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def adjust_canary_traffic(dep_id: int, req: CanaryReq, db=Depends(get_db)):
    """A4 调整金丝雀流量百分比。"""
    service, mem = _get_or_init_strategy(dep_id, db)
    if not any(v.version == req.version for v in mem.versions):
        raise HTTPException(status_code=404, detail=f"版本 {req.version} 不存在于部署 {dep_id}")
    if mem.strategy != "canary":
        raise HTTPException(status_code=400, detail=f"部署策略为 {mem.strategy},非 canary,无法调整流量")
    ok = service.canary_adjust_traffic(_strategy_key(dep_id), req.version, req.percentage)
    if not ok:
        raise HTTPException(status_code=500, detail="流量调整失败")
    _persist_strategy(dep_id, db)
    cr = service.canary_releases.get(_strategy_key(dep_id))
    return {"status": "ok", "deployment_id": dep_id, "canary_rules": cr.traffic_rules if cr else {}}


@router.get("/{dep_id}/strategy", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def get_strategy_status(dep_id: int, db=Depends(get_db)):
    """A5 获取当前策略 + 版本列表 + canary 流量比。"""
    service, mem = _get_or_init_strategy(dep_id, db)
    cr = service.canary_releases.get(_strategy_key(dep_id))
    return {
        "deployment_id": dep_id,
        "name": mem.name,
        "strategy": mem.strategy,
        "model_id": mem.model_id,
        "active_version": mem.active_version,
        "versions": [
            {"version": v.version, "image": v.image, "replicas": v.replicas, "status": v.status}
            for v in mem.versions
        ],
        "canary_rules": cr.traffic_rules if cr else {},
        "updated_at": mem.updated_at,
    }


@router.post("/{dep_id}/rollback", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def strategy_rollback(dep_id: int, req: SwitchReq, db=Depends(get_db)):
    """A6 版本回退:切换到指定旧版本并标记 ROLLING_BACK。
    body: {"version": "1.0.0"}
    """
    version_id, version, model_id = _resolve_registered_version(dep_id, req.version, db)
    service, mem = _get_or_init_strategy(dep_id, db)
    ok = service.switch_version(_strategy_key(dep_id), req.version)
    if not ok:
        raise HTTPException(status_code=404, detail=f"回退失败:版本 {req.version} 不存在")
    db.execute(text("UPDATE deployments SET version=:v, model_version_id=:vid, "
                    "status='rolling_back', updated_at=NOW() WHERE id=:id"),
               {"v": version, "vid": version_id, "id": dep_id})
    _sync_deployed_flags(model_id, db)
    db.commit()
    _persist_strategy(dep_id, db)
    return {"status": "rolled_back", "deployment_id": dep_id, "version": req.version,
            "versions": [{"version": v.version, "replicas": v.replicas} for v in mem.versions]}


@router.post("/{dep_id}/promote", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def strategy_promote(dep_id: int, db=Depends(get_db)):
    """A7 金丝雀全量发布:切换100%流量到最新版本。
    前端 promoteCanary(row) 调用 POST /deployments/{id}/promote"""
    service, mem = _get_or_init_strategy(dep_id, db)
    if not mem.versions:
        raise HTTPException(400, "无版本可提升")
    latest = mem.versions[-1].version
    version_id, latest, model_id = _resolve_registered_version(dep_id, latest, db)
    ok = service.switch_version(_strategy_key(dep_id), latest)
    if not ok:
        raise HTTPException(500, "全量发布失败")
    # 金丝雀流量全切到新版本
    for k in list(mem.canary_rules.keys()) if hasattr(mem, 'canary_rules') and mem.canary_rules else []:
        mem.canary_rules[k] = 0
    if hasattr(mem, 'canary_rules') and mem.canary_rules is not None:
        mem.canary_rules[latest] = 100
    db.execute(text("UPDATE deployments SET version=:v, model_version_id=:vid, "
                    "status='running', updated_at=NOW() WHERE id=:id"),
               {"v": latest, "vid": version_id, "id": dep_id})
    _sync_deployed_flags(model_id, db)
    db.commit()
    _persist_strategy(dep_id, db)
    return {"status": "promoted", "deployment_id": dep_id, "version": latest}


# ── 模型服务化与监控 ──

_loaded_models = {}
_metrics_store = {}
MODEL_DIR = Path("/app/uploads/models")


def _load_deployment_runtime(dep_id: int, db, force_reload: bool = False):
    """Load a deployment model, restoring the in-memory cache after restarts."""
    key = str(dep_id)
    if not force_reload and key in _loaded_models:
        return _loaded_models[key]

    deployment = db.execute(
        text("SELECT model_id, model_version_id FROM deployments WHERE id = :id"),
        {"id": dep_id},
    ).fetchone()
    if not deployment:
        raise HTTPException(status_code=404, detail="部署不存在")
    model_id, model_version_id = deployment
    if model_version_id is None:
        raise HTTPException(status_code=400, detail="部署未关联已注册模型版本")

    model = _load_model_blob(model_id, db)
    if model is None:
        raise HTTPException(status_code=400, detail="部署关联模型权重不可用")
    raw_feature_count = getattr(model, "n_features_in_", None)
    n_features = int(raw_feature_count) if raw_feature_count is not None else None
    try:
        model.predict(np.zeros((1, n_features or 4)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"部署模型预测探针失败: {exc}")

    runtime = {
        "model": model,
        "model_id": model_id,
        "model_version_id": model_version_id,
        "n_features_in": n_features,
        "loaded_at": datetime.utcnow().isoformat(),
    }
    _loaded_models[key] = runtime
    _metrics_store.setdefault(key, {
        "requests": 0, "errors": 0, "latency_sum_ms": 0,
        "started_at": datetime.utcnow().isoformat(),
    })
    return runtime


@router.post("/{dep_id}/serve", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def serve_model(dep_id: int, db=Depends(get_db)):
    runtime = _load_deployment_runtime(dep_id, db, force_reload=True)
    _metrics_store[str(dep_id)] = {
        "requests": 0, "errors": 0, "latency_sum_ms": 0,
        "started_at": datetime.utcnow().isoformat(),
    }
    return {
        "status": "serving",
        "dep_id": dep_id,
        "model_id": runtime["model_id"],
        "model_version_id": runtime["model_version_id"],
        "n_features_in": runtime["n_features_in"],
    }

@router.post("/{dep_id}/predict", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def predict(dep_id: int, req: dict, db=Depends(get_db)):
    ld = _load_deployment_runtime(dep_id, db)
    features = req.get("features")
    if not isinstance(features, list) or not features:
        raise HTTPException(status_code=400, detail="请求必须包含非空的 features 数组")
    try:
        feature_vector = np.asarray(features, dtype=float)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="features 中只能填写数值")
    if feature_vector.ndim != 1 or not np.isfinite(feature_vector).all():
        raise HTTPException(status_code=400, detail="features 必须是一维且全部为有限数值")
    expected = ld.get("n_features_in")
    if expected and feature_vector.size != expected:
        raise HTTPException(
            status_code=400,
            detail=f"特征数量不匹配: 该模型需要 {expected} 个，当前收到 {feature_vector.size} 个",
        )
    t0 = time.time()
    try:
        pred = ld["model"].predict(feature_vector.reshape(1, -1))[0]
        latency = round((time.time() - t0) * 1000, 2)
    except Exception as exc:
        m = _metrics_store.get(str(dep_id), {})
        m["errors"] = m.get("errors", 0) + 1
        raise HTTPException(status_code=500, detail=f"推理失败: {exc}")
    m = _metrics_store.setdefault(str(dep_id), {"requests": 0, "errors": 0, "latency_sum_ms": 0})
    m["requests"] += 1
    m["latency_sum_ms"] += latency
    prediction = pred.item() if isinstance(pred, np.generic) else (
        pred.tolist() if isinstance(pred, np.ndarray) else pred
    )
    return {"dep_id": dep_id, "prediction": prediction, "latency_ms": latency}

@router.get("/{dep_id}/metrics", dependencies=[Depends(require_permission("deployment:read")), Depends(require_deployment_access)])
def dep_metrics(dep_id: int):
    m = _metrics_store.get(str(dep_id))
    if not m:
        return {"status": "not_found"}
    return {
        "dep_id": int(dep_id),
        "requests": m["requests"],
        "errors": m["errors"],
        "avg_latency_ms": round(m["latency_sum_ms"] / max(1, m["requests"]), 2),
    }

@router.get("/serving/list", dependencies=[Depends(require_permission("deployment:read"))])
def list_serving_models(db=Depends(get_db), current_user: dict = Depends(require_auth)):
    ids = list(_loaded_models.keys())
    if not is_admin(current_user) and ids:
        rows = db.execute(text(
            "SELECT d.id FROM deployments d JOIN models m ON m.id=d.model_id "
            "WHERE d.id = ANY(:ids) AND m.organization_id=:org"
        ), {"ids": [int(item) for item in ids], "org": current_user.get("organization_id")}).fetchall()
        ids = [str(row[0]) for row in rows]
    return {"total": len(ids), "items": ids}

# ── 前端适配别名 ──
@router.post("/{dep_id}/promote", dependencies=[Depends(require_permission("deployment:write")), Depends(require_deployment_access)])
def promote_deployment(dep_id: int, db=Depends(get_db)):
    """金丝雀全量发布 或 蓝绿切换"""
    dep = db.execute(text("SELECT id, strategy, model_id, version FROM deployments WHERE id=:id"), {"id": dep_id}).fetchone()
    if not dep: raise HTTPException(404, "部署不存在")
    db.execute(text("UPDATE deployments SET status='healthy', updated_at=NOW() WHERE id=:id"), {"id": dep_id})
    db.commit()
    return {"status": "promoted", "deployment_id": dep_id}
