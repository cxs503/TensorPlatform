"""Validation helpers for model artifacts produced by sandbox runs."""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class SandboxModelArtifactError(ValueError):
    """Raised when a sandbox artifact cannot be restored as a predictor."""


def runtime_source(script_source: str) -> str:
    """Keep definitions/imports needed to restore classes without running training code."""
    try:
        tree = ast.parse(script_source or "")
    except SyntaxError as exc:
        raise SandboxModelArtifactError(f"模型配套源码语法错误：{exc}") from exc

    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError):
                continue
            body.append(node)
    return ast.unparse(ast.Module(body=body, type_ignores=[])) + "\n"


def write_runtime_source(script_source: str, destination: Path) -> Path:
    destination.write_text(runtime_source(script_source), encoding="utf-8")
    return destination


def inspect_sandbox_artifact(
    artifact_path: Path,
    script_source: str,
    python_path: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Load an artifact in its sandbox Python and return a small deployment contract."""
    artifact_path = Path(artifact_path)
    if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
        raise SandboxModelArtifactError("本次运行没有生成有效模型文件")
    python = Path(str(python_path or ""))
    if not python.is_file():
        raise SandboxModelArtifactError("本次运行使用的 Python 环境已经不可用")

    inspector = '''import json, sys
namespace = {"__name__": "__main__"}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec"), namespace)
vars(sys.modules["__main__"]).update(namespace)
import joblib
model = joblib.load(sys.argv[2])
wrapper_key = None
wrapper_columns = []
if isinstance(model, dict):
    for column_key in ("feature_columns", "selected_features", "features"):
        value = model.get(column_key)
        if isinstance(value, (list, tuple)):
            wrapper_columns = list(value)
            break
    candidates = []
    for key in ("model", "estimator", "predictor", "pipeline", "lgb_model", "xgb_model", "rf_model"):
        value = model.get(key)
        if callable(getattr(value, "predict", None)):
            candidates.append((key, value))
    if len(candidates) == 1:
        wrapper_key, model = candidates[0]
        # Normalize a metadata wrapper into the platform's deployable contract.
        # Multi-model ensemble dictionaries remain rejected because silently
        # choosing one member would change their prediction semantics.
        joblib.dump(model, sys.argv[2])
predict = getattr(model, "predict", None)
if not callable(predict):
    raise TypeError("模型对象没有可调用的 predict 方法；若保存的是字典，请确保其中只有一个 model/estimator/predictor/lgb_model 等预测器")
n_features = getattr(model, "n_features_in_", None)
if n_features is None:
    columns = getattr(model, "feature_columns", None)
    if isinstance(columns, (list, tuple)):
        n_features = len(columns)
if n_features is None and wrapper_columns:
    n_features = len(wrapper_columns)
payload = {
    "model_type": type(model).__name__,
    "n_features_in": int(n_features) if n_features is not None else None,
    "feature_columns": list(getattr(model, "feature_columns", []) or wrapper_columns or [])[:500],
    "normalized_wrapper_key": wrapper_key,
}
print("ODF_ARTIFACT_META=" + json.dumps(payload, ensure_ascii=False))
'''
    with tempfile.TemporaryDirectory(prefix="sandbox-artifact-check-") as tmp:
        source_path = write_runtime_source(script_source, Path(tmp) / "source.py")
        inspector_path = Path(tmp) / "artifact_probe.py"
        inspector_path.write_text(inspector, encoding="utf-8")
        try:
            result = subprocess.run(
                [str(python), str(inspector_path), str(source_path), str(artifact_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxModelArtifactError("模型制品验证超时") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()[-800:]
        raise SandboxModelArtifactError(f"模型制品无法在所选沙箱环境加载：{detail}")
    prefix = "ODF_ARTIFACT_META="
    line = next((item for item in reversed(result.stdout.splitlines()) if item.startswith(prefix)), "")
    if not line:
        raise SandboxModelArtifactError("模型制品验证没有返回部署元数据")
    try:
        return json.loads(line[len(prefix):])
    except json.JSONDecodeError as exc:
        raise SandboxModelArtifactError("模型制品部署元数据格式无效") from exc
