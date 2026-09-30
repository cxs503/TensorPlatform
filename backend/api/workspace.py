"""
个人工作空间 API — 独立开发空间，文件管理，代码执行，一键发布组件
"""
import os, json, re, shutil, uuid, threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.config import get_db
from api.deps import require_auth, require_permission
from api.tenant_access import scope_query
from services.workspace_runtime import (
    DATASET_SUFFIXES,
    MAX_SOURCE_BYTES,
    WorkspaceRuntimeError,
    inspect_workspace_inputs,
    list_runtimes,
    resolve_runtime,
    run_workspace_snapshot,
)
from services.sandbox_model_artifact import (
    SandboxModelArtifactError,
    inspect_sandbox_artifact,
)

# 沙箱异步运行状态（run_id → {"status": "running"|"done"|"error", ...}）
_workspace_runs: dict[str, dict] = {}
_WORKSPACE_RUNS_DIR = Path("/app/uploads/workspace_runs")
from services.algorithm_source_service import (
    algorithm_asset_type,
    algorithm_summary,
    import_to_workspace,
    overwrite_algorithm_source,
    sync_builtin_algorithms,
)
from services.llm_service import get_llm_service


def _save_model_to_registry(
    run_id, stdout_dir, response, current_user, entry_path, script_source="", environment_id=None,
    training_name="", model_folder_id=None,
):
    """Validate this run's own artifact and register it with its exact runtime."""
    from sqlalchemy import text as _text
    from database.config import SessionLocal
    model_file = Path(stdout_dir) / "model.pkl"
    if not model_file.is_file() or model_file.stat().st_size <= 0:
        raise SandboxModelArtifactError(
            "训练已完成，但脚本没有生成模型制品。请将带 predict 方法的模型对象使用 "
            "joblib.dump(model, os.environ['ODF_MODEL_OUT_PATH']) 保存后重新运行。"
        )
    if model_file.stat().st_size > 500 * 1024 * 1024:
        raise SandboxModelArtifactError("模型制品超过 500 MB，不能在线注册")

    runtime = resolve_runtime(environment_id)
    artifact_meta = inspect_sandbox_artifact(
        model_file, script_source, runtime.get("python_path") or "",
    )
    model_blob = model_file.read_bytes()
    metrics = response.get("analysis", {}).get("metrics", {})
    parameters = {
        "run_id": run_id,
        "source": "sandbox",
        "script_source": script_source[:50000],
        "artifact_kind": "sandbox_predictor",
        "artifact_format": "pkl",
        "environment_id": runtime.get("id"),
        "environment_name": runtime.get("name"),
        "runtime_digest": runtime.get("runtime_digest"),
        "python_path": runtime.get("python_path"),
        "n_features_in": artifact_meta.get("n_features_in"),
        "feature_columns": artifact_meta.get("feature_columns") or [],
        "predictor_type": artifact_meta.get("model_type"),
        "training_name": training_name,
    }
    db2 = SessionLocal()
    try:
        mid = db2.execute(_text(
            "INSERT INTO models (name, description, model_type, framework, status, organization_id, folder_id, created_at, updated_at) "
            "VALUES (:name, :desc, 'sandbox_run', 'user_script', 'READY', :org, :folder_id, NOW(), NOW()) RETURNING id"
        ), {
            "name": training_name or f"代码训练-{Path(entry_path).stem}",
            "desc": f"代码编辑器训练产物 (run_id={run_id})",
            "org": current_user.get("organization_id"), "folder_id": model_folder_id,
        }).scalar()
        db2.execute(_text("UPDATE models SET model_blob = :blob WHERE id = :mid"), {"blob": model_blob, "mid": mid})
        db2.execute(_text(
            "INSERT INTO model_versions (model_id, version_number, file_path, metrics, parameters, is_deployed, created_at) "
            "VALUES (:mid, 'v1', :fp, :met, :par, false, NOW())"
        ), {
            "mid": mid, "fp": str(model_file),
            "met": json.dumps({k: float(v) for k, v in metrics.items()}) if metrics else None,
            "par": json.dumps(parameters),
        })
        db2.commit()
        print(f"[workspace] 模型 #{mid} 已注册 (run_id={run_id}, {len(model_blob)} 字节)")
        return {
            "status": "success", "model_id": mid, "version": "v1",
            "artifact_path": str(model_file), "predictor_type": artifact_meta.get("model_type"),
            "n_features_in": artifact_meta.get("n_features_in"),
        }
    except Exception:
        db2.rollback()
        raise
    finally:
        db2.close()
from services.sandbox_ai_assistant_service import SandboxAssistantError, run_sandbox_assistant

router = APIRouter(prefix="/workspace", tags=["个人工作空间"])

# 工作空间根目录
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/app/workspaces"))
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# ─── Schemas ───
class FileItem(BaseModel):
    name: str
    path: str
    type: str  # "file" or "dir"
    size: int = 0
    modified: str = ""

class SaveFileReq(BaseModel):
    path: str  # relative path within workspace
    content: str

class CodeRunReq(BaseModel):
    code: str
    entry_path: str
    files: Optional[dict] = None  # unsaved editor contents, overlaid only in a temporary snapshot
    environment_id: Optional[int] = None
    environment_name: Optional[str] = None


DATASET_UPLOAD_SUFFIXES = DATASET_SUFFIXES | {".json", ".txt"}
HYPERPARAMETER_UPLOAD_SUFFIXES = {".json", ".txt", ".yaml", ".yml", ".cfg", ".conf"}

class PublishReq(BaseModel):
    name: str
    description: str
    file_path: str  # which file to publish as component
    category: str = "custom"
    tags: List[str] = []

class CreateProjectReq(BaseModel):
    name: str
    template: str = "blank"  # blank, ml, data_analysis, deep_learning

class AlgorithmSourceSaveReq(BaseModel):
    project_path: str
    relative_path: str
    content: str
    expected_revision: Optional[str] = None

class SandboxAssistantReq(BaseModel):
    action: str
    code: str
    selected_code: Optional[str] = None
    instruction: Optional[str] = None
    error_log: Optional[str] = None
    language: str = "python"
    file_path: str = ""
    environment_name: Optional[str] = None

# ─── Helpers ───
def get_user_workspace(user_id: str) -> Path:
    """Get or create user's workspace directory"""
    ws = WORKSPACE_ROOT / f"user_{user_id}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws

def resolve_workspace_path(ws: Path, raw_path: str = "", *, allow_root: bool = False) -> Path:
    """Resolve a user path and reject traversal, prefix tricks and workspace-root mutation."""
    target = (ws / raw_path).resolve()
    try:
        target.relative_to(ws.resolve())
    except ValueError:
        raise HTTPException(403, "禁止访问工作空间以外的路径")
    if target == ws.resolve() and not allow_root:
        raise HTTPException(400, "必须指定工作区内的文件或目录")
    return target

def get_user_id(current_user: dict = Depends(require_auth)) -> str:
    """Resolve the authenticated user; never share a fallback workspace."""
    return str(current_user["id"])

def run_history_dir(ws: Path) -> Path:
    """Private, file-backed experiment records for a user's sandbox."""
    target = ws / ".runs"
    target.mkdir(parents=True, exist_ok=True)
    return target

def save_run_record(ws: Path, record: dict) -> None:
    (run_history_dir(ws) / f"{record['id']}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _normalize_training_name(value: str, entry_path: str) -> str:
    name = str(value or "").strip() or f"{Path(entry_path).stem or '代码'}-训练"
    if len(name) > 128:
        raise HTTPException(400, "训练名称不能超过128个字符")
    if any(ord(character) < 32 for character in name):
        raise HTTPException(400, "训练名称不能包含换行或控制字符")
    return name

def assert_workspace_file_writable(ws: Path, target: Path) -> None:
    """Enforce imported-project read-only files at the API boundary."""
    current = target.parent
    workspace_root = ws.resolve()
    while current != workspace_root and workspace_root in current.parents:
        manifest_path = current / ".foundation_algorithm.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                relative = target.relative_to(current).as_posix()
                if manifest.get("asset_type") in {"builtin", "finetune_model"}:
                    raise HTTPException(403, "该类型不再开放沙箱编辑")
                if relative in set(manifest.get("read_only_files") or []):
                    raise HTTPException(403, "该继续训练脚本由平台维护，只能修改参数文件")
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            return
        current = current.parent

# ─── File Management ───
# The code editor is intentionally source-oriented.  Binary model/data/output files
# remain available through their own modules, but must never be decoded as text.
EDITABLE_WORKSPACE_SUFFIXES = {
    ".py", ".pyi", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".txt", ".md", ".rst", ".sh", ".bash", ".js", ".ts", ".html", ".css", ".sql", ".xml",
}
HIDDEN_WORKSPACE_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", ".runs", "artifacts", "output", ".foundation_results"}
TEXT_FILE_NAMES = {"readme", "readme.md", "license", "makefile", "dockerfile", "requirements.txt"}


def _is_editable_workspace_file(path: Path) -> bool:
    return path.suffix.lower() in EDITABLE_WORKSPACE_SUFFIXES or path.name.lower() in TEXT_FILE_NAMES


def _display_workspace_name(name: str) -> str:
    """Repair legacy GBK ZIP names for display only; paths remain byte-for-byte stable."""
    try:
        repaired = name.encode("cp437").decode("gb18030")
        if any("\u4e00" <= char <= "\u9fff" for char in repaired):
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


@router.get("/files")
def list_files(path: str = "", user_id: str = Depends(get_user_id)):
    """列出工作空间文件"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, path, allow_root=True)

    if not target.exists():
        return {"path": path, "files": [], "total": 0}

    files = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            if item.name in {".foundation_algorithm.json"} or item.name in HIDDEN_WORKSPACE_DIRECTORIES or item.is_symlink():
                continue
            # Model binaries stay downloadable from the linked store record and are
            # intentionally not exposed as text-editor files.
            if item.name == "artifacts" and (target / ".foundation_algorithm.json").is_file():
                continue
            if item.is_dir():
                manifest_path = item / ".foundation_algorithm.json"
                if manifest_path.is_file():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if manifest.get("asset_type") in {"builtin", "finetune_model"}:
                            continue
                    except (OSError, json.JSONDecodeError):
                        pass
            if item.is_file() and not _is_editable_workspace_file(item):
                continue
            stat = item.stat()
            files.append(FileItem(
                name=_display_workspace_name(item.name),
                path=str(item.relative_to(ws)),
                type="dir" if item.is_dir() else "file",
                size=stat.st_size if item.is_file() else 0,
                modified=datetime.fromtimestamp(stat.st_mtime).isoformat()
            ))
    except PermissionError:
        pass
    
    return {"path": path, "files": [f.model_dump() for f in files], "total": len(files)}

@router.get("/files/read")
def read_file(path: str, user_id: str = Depends(get_user_id)):
    """读取文件内容"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, path)
    if not target.exists():
        raise HTTPException(404, "文件不存在")
    if not target.is_file():
        raise HTTPException(400, "不是文件")
    if not _is_editable_workspace_file(target):
        raise HTTPException(415, "该文件不是可编辑文本文件，已从代码编辑器中隐藏")
    if target.stat().st_size > MAX_SOURCE_BYTES:
        raise HTTPException(413, "文本文件超过代码编辑器的5MB限制")
    try:
        content = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "该文件不是 UTF-8 文本，已从代码编辑器中隐藏") from exc
    return {"path": path, "content": content, "size": target.stat().st_size}

@router.post("/files/save")
def save_file(req: SaveFileReq, user_id: str = Depends(get_user_id)):
    """保存/创建文件"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, req.path)
    assert_workspace_file_writable(ws, target)
    if len(req.content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "单个代码文件不能超过 5 MB")
    
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding='utf-8')
    
    return {"path": req.path, "status": "saved", "size": target.stat().st_size}


@router.post("/files/create")
def create_file_without_overwrite(req: SaveFileReq, user_id: str = Depends(get_user_id)):
    """Create a confirmed new file and refuse accidental replacement."""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, req.path)
    assert_workspace_file_writable(ws, target)
    if target.exists():
        raise HTTPException(409, "同名文件已经存在，请修改文件名")
    if len(req.content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "单个代码文件不能超过 5 MB")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"path": req.path, "status": "created", "size": target.stat().st_size}

@router.post("/files/upload")
def upload_file(
    file: UploadFile = File(...),
    dest_path: str = Form(""),
    user_id: str = Depends(get_user_id)
):
    """上传文件到工作空间"""
    ws = get_user_workspace(user_id)
    target_dir = resolve_workspace_path(ws, dest_path, allow_root=True)
    
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "uploaded_file").name
    target = resolve_workspace_path(ws, str((target_dir / safe_name).relative_to(ws)))
    assert_workspace_file_writable(ws, target)
    
    with open(target, "wb") as f:
        f.write(file.file.read())
    
    return {"name": target.name, "path": str(target.relative_to(ws)), "size": target.stat().st_size}

@router.delete("/files/delete")
def delete_file(path: str, user_id: str = Depends(get_user_id)):
    """删除文件或目录"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, path)
    if not target.exists():
        raise HTTPException(404, "文件不存在")
    
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    
    return {"path": path, "status": "deleted"}

@router.post("/files/mkdir")
def create_directory(path: str, user_id: str = Depends(get_user_id)):
    """创建目录"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, path)
    
    target.mkdir(parents=True, exist_ok=True)
    return {"path": path, "status": "created"}

# ─── Code Execution ───
@router.get("/environments")
def list_workspace_environments(user_id: str = Depends(get_user_id)):
    """Return environments that the backend has actually validated for execution."""
    items = list_runtimes()
    return {"total": len(items), "items": items}


@router.post("/ai-assistant")
async def sandbox_ai_assistant(
    req: SandboxAssistantReq,
    user_id: str = Depends(get_user_id),
):
    """Analyze or propose an in-memory edit; never read or write workspace files."""
    del user_id  # authentication is the only workspace dependency for this stateless request
    try:
        return await run_in_threadpool(
            run_sandbox_assistant,
            get_llm_service(),
            req.model_dump(),
        )
    except SandboxAssistantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI编程助手调用失败：{str(exc)[:300]}") from exc


@router.post("/run/preflight")
def preflight_code_run(req: CodeRunReq, user_id: str = Depends(get_user_id)):
    """Detect missing file inputs without executing user code."""
    return inspect_workspace_inputs(
        workspace=get_user_workspace(user_id),
        entry_path=req.entry_path,
        files=req.files,
        code=req.code,
    )


async def _read_run_upload(file: UploadFile, role: str) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    allowed_suffixes = (
        HYPERPARAMETER_UPLOAD_SUFFIXES
        if role == "hyperparameters" else DATASET_UPLOAD_SUFFIXES
    )
    if suffix not in allowed_suffixes:
        raise HTTPException(400, f"不支持的{role}文件类型：{suffix or '无扩展名'}")
    limit = 1024 * 1024 if role == "hyperparameters" else 100 * 1024 * 1024
    content = await file.read(limit + 1)
    if not content:
        raise HTTPException(400, f"{role}文件为空")
    if len(content) > limit:
        raise HTTPException(413, f"{role}文件超过大小限制")
    return {"filename": Path(file.filename or f"{role}.txt").name, "content": content}


def _read_platform_dataset(dataset) -> dict:
    source = Path(dataset.file_path or "")
    if not source.is_file():
        raise HTTPException(400, "该数据集没有可用的数据文件")
    suffix = source.suffix.lower()
    if suffix not in DATASET_UPLOAD_SUFFIXES:
        raise HTTPException(400, f"该数据集文件类型暂不支持运行：{suffix or '无扩展名'}")
    size = source.stat().st_size
    if size <= 0:
        raise HTTPException(400, "该数据集文件为空")
    if size > 100 * 1024 * 1024:
        raise HTTPException(413, "该数据集超过 100 MB，不能用于在线沙箱运行")
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(dataset.name or "data")
    )[:60]
    return {
        "filename": f"{dataset.id}_{safe_name or 'data'}{suffix}",
        "content": source.read_bytes(),
    }


@router.post("/run/with-inputs")
async def run_code_with_inputs(
    code: str = Form(...),
    entry_path: str = Form(...),
    files_json: str = Form("{}"),
    environment_id: Optional[int] = Form(None),
    environment_name: Optional[str] = Form(None),
    training_name: str = Form(""),
    model_folder_id: Optional[int] = Form(None),
    dataset_file: Optional[UploadFile] = File(None),
    dataset_id: Optional[int] = Form(None),
    field_roles_json: str = Form(""),
    hyperparameters_file: Optional[UploadFile] = File(None),
    save_model: str = Form("false"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Run code with one-time files that are never persisted to the workspace."""
    try:
        files = json.loads(files_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "未保存文件内容格式无效") from exc
    if not isinstance(files, dict):
        raise HTTPException(400, "未保存文件内容必须是对象")
    training_name = _normalize_training_name(training_name, entry_path)
    if model_folder_id is not None:
        from models.core import ModelFolder
        folder = scope_query(db.query(ModelFolder).filter(ModelFolder.id == model_folder_id), ModelFolder.organization_id, current_user).first()
        if not folder:
            raise HTTPException(404, "模型分类不存在或无权使用")
    if dataset_file and dataset_id is not None:
        raise HTTPException(400, "上传数据集和平台数据集只能选择一种")
    input_files = {}
    if dataset_file:
        input_files["dataset"] = await _read_run_upload(dataset_file, "dataset")
    elif dataset_id is not None:
        from models.core import Dataset

        dataset = scope_query(
            db.query(Dataset).filter(Dataset.id == dataset_id),
            Dataset.organization_id,
            current_user,
        ).first()
        if not dataset:
            raise HTTPException(404, "数据集不存在或无权访问")
        input_files["dataset"] = _read_platform_dataset(dataset)
        if field_roles_json.strip():
            try:
                field_roles = json.loads(field_roles_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "字段用途配置格式无效") from exc
            if not isinstance(field_roles, dict):
                raise HTTPException(400, "字段用途配置必须是对象")
            # 只保留运行脚本会使用的字段，避免把前端候选列表等展示数据带进运行环境。
            normalized_roles = {
                "time_column": field_roles.get("time_column") or None,
                "id_columns": field_roles.get("id_columns") or [],
                "feature_columns": field_roles.get("feature_columns") or [],
                "target_column": field_roles.get("target_column") or None,
                "apply_target_override": bool(field_roles.get("apply_target_override")),
                "apply_feature_override": bool(field_roles.get("apply_feature_override")),
            }
            if (not isinstance(normalized_roles["id_columns"], list)
                    or not isinstance(normalized_roles["feature_columns"], list)):
                raise HTTPException(400, "字段用途中的特征列和标识列必须是列表")
            role_bytes = json.dumps(normalized_roles, ensure_ascii=False).encode("utf-8")
            if len(role_bytes) > 64 * 1024:
                raise HTTPException(400, "字段用途配置过大")
            input_files["field_roles"] = {
                "filename": "field_roles.json",
                "content": role_bytes,
            }
    if hyperparameters_file:
        input_files["hyperparameters"] = await _read_run_upload(
            hyperparameters_file, "hyperparameters"
        )

    ws = get_user_workspace(user_id)
    run_id = str(uuid.uuid4())
    started_at = datetime.now().isoformat()
    stdout_dir = str(_WORKSPACE_RUNS_DIR / run_id)
    _WORKSPACE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    Path(stdout_dir).mkdir(parents=True, exist_ok=True)
    _workspace_runs[run_id] = {"status": "running", "stdout_dir": stdout_dir, "pid": None}

    def _worker():
        import os as _os, signal as _signal
        # 把子进程 PID 存起来供停止用
        import services.workspace_runtime as _rt
        _orig_run = _rt.run_workspace_snapshot
        def _patched_run(*args, **kwargs):
            # 在 run_workspace_snapshot 里没法拿 PID，改用环境变量传递
            return _orig_run(*args, **kwargs)
        try:
            response = run_workspace_snapshot(
                workspace=ws, entry_path=entry_path, files=files, code=code,
                environment_id=environment_id, input_files=input_files,
                stdout_dir=stdout_dir,
            )
            response["run_id"] = run_id
            response["training_name"] = training_name
            registry_result = None
            if save_model.lower() == "true" and response.get("status") == "success":
                try:
                    registry_result = _save_model_to_registry(
                        run_id, stdout_dir, response, current_user, entry_path, code,
                        environment_id=(response.get("environment") or {}).get("id") or environment_id,
                        training_name=training_name, model_folder_id=model_folder_id,
                    )
                except Exception as exc:
                    registry_result = {"status": "error", "message": str(exc)}
                    response.setdefault("analysis", {}).setdefault("warnings", []).append(str(exc))
                    print(f"[workspace] 模型注册失败: {exc}")
            if registry_result is not None:
                response["model_registry"] = registry_result
            save_run_record(ws, {
                "id": run_id, "started_at": started_at, "finished_at": datetime.now().isoformat(),
                "training_name": training_name,
                "status": response["status"], "returncode": response["returncode"],
                "environment": response["environment"], "entry_path": entry_path,
                "output": response["output"][-8000:], "error": response["error"][-8000:],
                "code_preview": code[:500], "inputs": response.get("inputs", {}),
                "duration_ms": response.get("duration_ms"), "analysis": response.get("analysis", {}),
                "model_registry": registry_result,
            })
            _workspace_runs[run_id] = {"status": "done", "response": response, "training_name": training_name}
        except Exception as exc:
            _workspace_runs[run_id] = {"status": "error", "error": str(exc), "training_name": training_name}

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    _workspace_runs[run_id]["thread"] = t
    return {"run_id": run_id, "status": "running", "training_name": training_name}


@router.post("/control-simulations")
async def run_workspace_control_simulation(
    code: str = Form(...),
    entry_path: str = Form(...),
    files_json: str = Form("{}"),
    environment_id: Optional[int] = Form(None),
    simulation_name: str = Form(""),
    dataset_id: int = Form(...),
    time_column: str = Form(""),
    state_columns_json: str = Form("[]"),
    control_columns_json: str = Form("[]"),
    disturbance_columns_json: str = Form("[]"),
    setpoint: Optional[float] = Form(None),
    algorithm_id: Optional[int] = Form(None),
    algorithm_name: str = Form(""),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Run the current editor snapshot as a recorded control simulation task."""
    from models.core import Dataset
    from services.control_project_runtime import ControlProjectRuntimeError, run_control_project
    try:
        files = json.loads(files_json or "{}")
        state_columns = json.loads(state_columns_json or "[]")
        control_columns = json.loads(control_columns_json or "[]")
        disturbance_columns = json.loads(disturbance_columns_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "控制仿真配置格式无效") from exc
    if not isinstance(files, dict) or not all(isinstance(value, str) for value in files.values()):
        raise HTTPException(400, "编辑器快照格式无效")
    for columns, label in ((state_columns, "被控变量"), (control_columns, "控制变量"), (disturbance_columns, "扰动变量")):
        if not isinstance(columns, list) or not all(isinstance(value, str) for value in columns):
            raise HTTPException(400, f"{label}配置无效")
    entry = Path(entry_path)
    if entry.is_absolute() or ".." in entry.parts or entry.suffix.lower() != ".py":
        raise HTTPException(400, "仿真入口文件无效")
    dataset = scope_query(db.query(Dataset).filter(Dataset.id == dataset_id), Dataset.organization_id, current_user).first()
    if not dataset:
        raise HTTPException(404, "数据集不存在或无权访问")
    # A reloaded editor may no longer have its in-memory imported-algorithm context.
    # Recover it from the stable workspace import folder so its verified environment
    # is still used instead of the minimal default sandbox.
    resolved_algorithm_id = int(algorithm_id or 0)
    resolved_environment_id = environment_id
    algorithm_display = algorithm_name.strip()
    inferred = re.search(r"(?:^|/)algorithms/algorithm_(\d+)_", entry.as_posix())
    if (not resolved_algorithm_id) and inferred:
        from models.core import AlgorithmModel
        model = db.query(AlgorithmModel).filter(AlgorithmModel.id == int(inferred.group(1))).first()
        if model:
            resolved_algorithm_id = int(model.id)
            algorithm_display = algorithm_display or model.name
            metadata = model.metrics if isinstance(model.metrics, dict) else {}
            configured_environment_id = metadata.get("dependency_id")
            if configured_environment_id:
                resolved_environment_id = int(configured_environment_id)
    algorithm_display = algorithm_display or f"代码编辑器：{entry.name}"
    config = {
        "name": simulation_name.strip() or f"{algorithm_display}-仿真",
        "time_column": time_column.strip(),
        "state_columns": list(dict.fromkeys(value.strip() for value in state_columns if value.strip())),
        "control_columns": list(dict.fromkeys(value.strip() for value in control_columns if value.strip())),
        "disturbance_columns": list(dict.fromkeys(value.strip() for value in disturbance_columns if value.strip())),
        "setpoint": setpoint, "constraints": {}, "parameters": {}, "source": "code_editor",
    }
    metadata = {"source_project_root": str(get_user_workspace(user_id)), "source_entry_point": str(entry), "dependency_id": environment_id}
    snapshot = dict(files); snapshot[str(entry)] = code
    try:
        return await run_in_threadpool(
            run_control_project,
            algorithm_id=int(algorithm_id or 0), algorithm_name=algorithm_display,
            metadata=metadata, dataset=dataset, config=config,
            organization_id=current_user.get("organization_id"), source_files=snapshot,
        )
    except ControlProjectRuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str):
    """手动停止运行：写一个 stop 标记文件，进程检测到后退出（或杀进程组）。"""
    run = _workspace_runs.get(run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    if run["status"] != "running":
        raise HTTPException(400, "运行已结束")
    # 写 stop 标记到 stdout_dir
    stdout_dir = run.get("stdout_dir", "")
    if stdout_dir:
        stop_file = Path(stdout_dir) / ".stop"
        stop_file.touch()
    _workspace_runs[run_id] = {**run, "status": "stopped"}
    return {"run_id": run_id, "status": "stopped"}


@router.get("/runs/{run_id}/stdout")
def get_run_stdout(run_id: str, offset: int = 0):
    """增量读 stdout（前端轮询，offset 是上次读到的字节数）。"""
    run = _workspace_runs.get(run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    stdout_path = Path(run.get("stdout_dir", "")) / "stdout.log"
    if not stdout_path.is_file():
        return {"content": "", "offset": 0, "eof": run["status"] != "running", "status": run["status"]}
    data = stdout_path.read_bytes()
    chunk = data[offset:].decode("utf-8", errors="replace")
    return {"content": chunk, "offset": len(data), "eof": run["status"] != "running", "status": run["status"]}


@router.get("/runs/{run_id}/result")
def get_run_result(run_id: str):
    """运行结束后获取完整结果（analysis/metrics/series）。"""
    run = _workspace_runs.get(run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    if run["status"] == "running":
        return {"status": "running"}
    if run["status"] == "error":
        return {
            "status": "error", "error": run.get("error", "未知错误"),
            "training_name": run.get("training_name"),
        }
    return {"status": "done", **run.get("response", {})}


@router.post("/run")
def run_code(req: CodeRunReq, user_id: str = Depends(get_user_id)):
    """Run unsaved editor content in a disposable snapshot; never overwrite source files."""
    ws = get_user_workspace(user_id)
    run_id = str(uuid.uuid4())
    started_at = datetime.now().isoformat()
    try:
        response = run_workspace_snapshot(
            workspace=ws,
            entry_path=req.entry_path,
            files=req.files,
            code=req.code,
            environment_id=req.environment_id,
        )
        save_run_record(ws, {
            "id": run_id, "started_at": started_at, "finished_at": datetime.now().isoformat(),
            "status": response["status"], "returncode": response["returncode"],
            "environment": response["environment"], "entry_path": req.entry_path,
            "output": response["output"][-8000:], "error": response["error"][-8000:],
            "code_preview": req.code[:500], "inputs": response.get("inputs", {}),
            "duration_ms": response.get("duration_ms"), "analysis": response.get("analysis", {}),
        })
        return {**response, "run_id": run_id}
    except WorkspaceRuntimeError as e:
        response = {"output": "", "error": str(e), "status": "error"}
        save_run_record(ws, {"id": run_id, "started_at": started_at, "finished_at": datetime.now().isoformat(),
                             "status": "error", "environment": {"id": req.environment_id, "name": req.environment_name},
                             "entry_path": req.entry_path, "output": "", "error": str(e), "code_preview": req.code[:500],
                             "inputs": {}, "duration_ms": 0, "analysis": {}})
        return {**response, "run_id": run_id}

@router.get("/runs")
def list_runs(limit: int = 30, user_id: str = Depends(get_user_id)):
    """List latest sandbox experiment records without exposing workspace internals."""
    ws = get_user_workspace(user_id)
    records = []
    for item in run_history_dir(ws).glob("*.json"):
        try:
            records.append(json.loads(item.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    records.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return {"total": len(records), "items": records[:max(1, min(limit, 100))]}

# ─── Dataset Access ───
@router.get("/datasets")
def list_workspace_datasets(
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """列出可访问的数据集"""
    try:
        from models.core import Dataset
        datasets = scope_query(db.query(Dataset), Dataset.organization_id, current_user).order_by(Dataset.created_at.desc()).all()
        available = [
            dataset for dataset in datasets
            if dataset.file_path
            and Path(dataset.file_path).is_file()
            and Path(dataset.file_path).stat().st_size > 0
            and Path(dataset.file_path).suffix.lower() in DATASET_UPLOAD_SUFFIXES
        ]
        return {
            "total": len(available),
            "items": [{
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "row_count": d.row_count,
                "size_bytes": d.size_bytes,
                "stage": d.stage,
                "available": True,
            } for d in available]
        }
    except Exception as e:
        return {"total": 0, "items": [], "error": str(e)}

@router.post("/datasets/{dataset_id}/attach")
def attach_dataset_to_workspace(
    dataset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Copy an authorized, file-backed dataset into the user's persistent workspace."""
    from models.core import Dataset

    dataset = scope_query(
        db.query(Dataset).filter(Dataset.id == dataset_id),
        Dataset.organization_id,
        current_user,
    ).first()
    if not dataset:
        raise HTTPException(404, "数据集不存在或无权访问")
    if not dataset.file_path or not Path(dataset.file_path).is_file():
        raise HTTPException(400, "该数据集只有记录，没有可载入的数据文件")
    source = Path(dataset.file_path)
    if source.stat().st_size <= 0:
        raise HTTPException(400, "数据集文件为空")
    if source.stat().st_size > 100 * 1024 * 1024:
        raise HTTPException(413, "数据集超过 100 MB，不能附加到在线沙箱")
    suffix = source.suffix.lower() or ".csv"
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in dataset.name)[:60]
    relative = Path("datasets") / f"dataset_{dataset.id}_{safe_name or 'data'}{suffix}"
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, relative.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "dataset_id": dataset.id,
        "name": dataset.name,
        "path": relative.as_posix(),
        "size_bytes": target.stat().st_size,
        "row_count": dataset.row_count,
    }

# ─── Algorithm Store Source Bridge ───
@router.get("/algorithms")
def list_workspace_algorithms(
    search: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    """List algorithm-store entries with an honest description of their editable source."""
    from models.core import AlgorithmModel

    sync_builtin_algorithms(db)
    query = db.query(AlgorithmModel)
    if search.strip():
        term = search.strip()
        clauses = [AlgorithmModel.name.ilike(f"%{term}%")]
        if term.isdigit():
            clauses.append(AlgorithmModel.id == int(term))
        query = query.filter(or_(*clauses))
    models = query.order_by(AlgorithmModel.created_at.desc(), AlgorithmModel.id.desc()).limit(200).all()
    models = [model for model in models if algorithm_asset_type(model) == "uploaded_source"]
    return {"total": len(models), "items": [algorithm_summary(model) for model in models]}

@router.post(
    "/algorithms/{algorithm_id}/import",
    dependencies=[Depends(require_permission("training:write"))],
)
def import_algorithm_from_store(
    algorithm_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    from models.core import AlgorithmModel

    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == algorithm_id).first()
    if not model:
        raise HTTPException(404, "算法不存在")
    if algorithm_asset_type(model) == "builtin":
        raise HTTPException(400, "AutoML 内置算法不在沙箱中编辑")
    if algorithm_asset_type(model) == "finetune_model":
        raise HTTPException(400, "PKL 请直接在算法商店配置数据集和超参数后继续训练")
    result = import_to_workspace(model, get_user_workspace(user_id))
    return {**result, "dependency_id": (model.metrics or {}).get("dependency_id")}

@router.put(
    "/algorithms/{algorithm_id}/source",
    dependencies=[Depends(require_permission("training:write"))],
)
def save_algorithm_source(
    algorithm_id: int,
    req: AlgorithmSourceSaveReq,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    from models.core import AlgorithmModel

    model = db.query(AlgorithmModel).filter(AlgorithmModel.id == algorithm_id).first()
    if not model:
        raise HTTPException(404, "算法不存在")
    return overwrite_algorithm_source(
        db=db,
        model=model,
        workspace=get_user_workspace(user_id),
        project_path=req.project_path,
        relative_path=req.relative_path,
        content=req.content,
        expected_revision=req.expected_revision,
    )

# ─── Publish as Component ───
@router.post("/publish")
def publish_component(req: PublishReq, user_id: str = Depends(get_user_id)):
    """将工作空间中的代码发布为共享算法组件"""
    ws = get_user_workspace(user_id)
    target = resolve_workspace_path(ws, req.file_path)
    if not target.exists():
        raise HTTPException(404, "文件不存在")
    
    code_content = target.read_text(encoding='utf-8')
    
    # Store in algorithm store
    component_dir = Path("/data/foundation_components") / user_id
    component_dir.mkdir(parents=True, exist_ok=True)
    
    component_id = str(uuid.uuid4())[:8]
    component_file = component_dir / f"{component_id}.py"
    component_file.write_text(code_content, encoding='utf-8')
    
    # Save metadata
    meta = {
        "id": component_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "tags": req.tags,
        "author_id": user_id,
        "source_file": req.file_path,
        "created_at": datetime.now().isoformat(),
        "file": str(component_file)
    }
    meta_file = component_dir / f"{component_id}.json"
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    
    return {
        "status": "published",
        "component": {
            "id": component_id,
            "name": req.name,
            "description": req.description,
            "category": req.category,
            "tags": req.tags,
        }
    }

# ─── Project Templates ───
@router.post("/projects")
def create_project(req: CreateProjectReq, user_id: str = Depends(get_user_id)):
    """创建项目（带模板）"""
    ws = get_user_workspace(user_id)
    project_name = req.name.strip()
    if not project_name or len(project_name) > 64 or not re.fullmatch(r"[\w\-\u4e00-\u9fff]+", project_name):
        raise HTTPException(400, "项目名称只能包含中文、字母、数字、下划线和连字符，且不超过 64 个字符")
    if req.template not in {"blank", "ml", "data_analysis", "deep_learning"}:
        raise HTTPException(400, "不支持的项目模板")
    project_dir = resolve_workspace_path(ws, project_name)
    if project_dir.exists():
        raise HTTPException(409, "同名项目已存在")
    project_dir.mkdir(parents=True)
    
    templates = {
        "blank": {"main.py": ""},
        "ml": {
            "train.py": '''"""机器学习训练脚本"""
import pandas as pd
import numpy as np
import json
import os
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
import joblib

def load_data():
    """加载数据 - 从平台数据集获取"""
    # TODO: 替换为实际数据加载
    X = np.random.rand(200, 5)
    y = X[:, 0] * 10 + X[:, 1] * 5 + np.random.randn(200) * 2
    return X, y

def train():
    X, y = load_data()
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
    
    model = RandomForestRegressor(n_estimators=100, max_depth=10)
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    print(f"R²: {r2_score(y_test, y_pred):.4f}")
    print(f"RMSE: {np.sqrt(mean_squared_error(y_test, y_pred)):.4f}")

    result_path = os.getenv("ODF_RESULT_PATH")
    if result_path:
        with open(result_path, "w", encoding="utf-8") as result_file:
            json.dump({
                "task_type": "regression",
                "metrics": {
                    "r2": float(r2_score(y_test, y_pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
                },
                "y_true": y_test.tolist(),
                "y_pred": y_pred.tolist(),
                "feature_importance": {
                    f"feature_{index + 1}": float(value)
                    for index, value in enumerate(model.feature_importances_)
                },
            }, result_file, ensure_ascii=False)

    model_path = os.environ["ODF_MODEL_OUT_PATH"]
    joblib.dump(model, model_path)
    print(f"模型已保存: {model_path}")
    return model

if __name__ == "__main__":
    train()
''',
            "requirements.txt": "pandas\nnumpy\nscikit-learn\njoblib\n",
        },
        "data_analysis": {
            "analysis.py": '''"""数据分析脚本"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def analyze():
    """数据探索分析"""
    # 生成示例数据
    np.random.seed(42)
    df = pd.DataFrame({
        '温度': np.random.normal(85, 5, 200),
        '压力': np.random.normal(2.5, 0.2, 200),
        '转速': np.random.normal(1450, 50, 200),
        '功率': np.random.normal(500, 30, 200),
    })
    
    print("=== 数据概览 ===")
    print(df.describe())
    print(f"\\n=== 相关性 ===")
    print(df.corr())
    
    # 绘制分布图
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, col in zip(axes.flat, df.columns):
        ax.hist(df[col], bins=30, alpha=0.7, edgecolor='black')
        ax.set_title(col)
        ax.set_xlabel('值')
        ax.set_ylabel('频次')
    plt.tight_layout()
    plt.savefig('distribution.png', dpi=100)
    print("\\n分布图已保存: distribution.png")

if __name__ == "__main__":
    analyze()
''',
        },
        "deep_learning": {
            "train.py": '''"""深度学习训练脚本 - PyTorch"""
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import json
import os
import joblib

class SimpleNN(nn.Module):
    def __init__(self, input_dim=10, hidden_dim=64, output_dim=1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim)
        )
    
    def forward(self, x):
        return self.net(x)

class TorchPredictor:
    def __init__(self, model, input_dim=10):
        self.model = model.eval()
        self.n_features_in_ = input_dim

    def predict(self, values):
        array = np.asarray(values, dtype=np.float32)
        with torch.no_grad():
            return self.model(torch.tensor(array)).cpu().numpy().reshape(-1)

def train():
    # 生成示例数据
    X = torch.randn(500, 10)
    y = X[:, 0:1] * 3 + X[:, 1:2] * 2 + torch.randn(500, 1) * 0.5
    
    model = SimpleNN()
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    loss_fn = nn.MSELoss()
    loss_history = []
    
    for epoch in range(50):
        optimizer.zero_grad()
        pred = model(X)
        loss = loss_fn(pred, y)
        loss.backward()
        optimizer.step()
        loss_history.append({"epoch": epoch + 1, "loss": float(loss.item())})
        
        if (epoch + 1) % 10 == 0:
            print(f"Epoch {epoch+1}/50, Loss: {loss.item():.4f}")

    result_path = os.getenv("ODF_RESULT_PATH")
    if result_path:
        with open(result_path, "w", encoding="utf-8") as result_file:
            json.dump({
                "task_type": "regression",
                "metrics": {"loss": float(loss.item())},
                "series": loss_history,
                "y_true": y.detach().reshape(-1).tolist(),
                "y_pred": pred.detach().reshape(-1).tolist(),
            }, result_file, ensure_ascii=False)

    model_path = os.environ["ODF_MODEL_OUT_PATH"]
    joblib.dump(TorchPredictor(model, input_dim=10), model_path)
    print(f"模型已保存: {model_path}")

if __name__ == "__main__":
    train()
''',
            "requirements.txt": "torch\nnumpy\njoblib\n",
        }
    }
    
    template_files = templates[req.template]
    for fname, content in template_files.items():
        (project_dir / fname).write_text(content, encoding='utf-8')
    
    return {
        "project": project_name,
        "template": req.template,
        "files": list(template_files.keys()),
        "path": str(project_dir.relative_to(ws))
    }

# ─── Workspace Info ───
@router.get("/info")
def workspace_info(user_id: str = Depends(get_user_id)):
    """获取工作空间概览信息"""
    ws = get_user_workspace(user_id)
    total_files = sum(1 for _ in ws.rglob("*") if _.is_file())
    total_size = sum(_.stat().st_size for _ in ws.rglob("*") if _.is_file())
    
    return {
        "user_id": user_id,
        "root": str(ws),
        "total_files": total_files,
        "total_size_bytes": total_size,
        "disk_usage_mb": round(total_size / 1024 / 1024, 2)
    }
