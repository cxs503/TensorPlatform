"""Safe import support for platform Docker deployment bundles."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import onnx


DOCKER_BUNDLE_ROOT = Path("/app/uploads/docker-bundles")
REQUIRED_FILES = {
    "Dockerfile",
    "serve.py",
    "requirements.txt",
    "model.onnx",
    "model-metadata.json",
    "README.txt",
}


def _read_member(archive: tarfile.TarFile, basename: str, limit: int) -> bytes:
    matches = [
        member for member in archive.getmembers()
        if member.isfile() and Path(member.name).name == basename
    ]
    if len(matches) != 1:
        raise ValueError(f"Docker包必须且只能包含一个 {basename}")
    source = archive.extractfile(matches[0])
    if source is None:
        raise ValueError(f"无法读取Docker包文件 {basename}")
    payload = source.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"Docker包文件 {basename} 超过大小限制")
    return payload


def _onnx_shape(value_info: Any) -> list[Any]:
    shape = []
    for dim in value_info.type.tensor_type.shape.dim:
        if dim.dim_value > 0:
            shape.append(dim.dim_value)
        elif dim.dim_param:
            shape.append(dim.dim_param)
        else:
            shape.append(None)
    return shape


def prepare_docker_bundle(content: bytes, algorithm_id: int) -> dict[str, Any]:
    """Extract only model data, then regenerate every executable runtime file."""
    from api.deployment import DOCKERFILE_TEMPLATE, REQUIREMENTS_TXT, SERVE_PY_TEMPLATE

    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
        basenames = {Path(member.name).name for member in archive.getmembers() if member.isfile()}
        missing = sorted(REQUIRED_FILES - basenames)
        if missing:
            raise ValueError(f"Docker部署包缺少文件: {', '.join(missing)}")
        model_bytes = _read_member(archive, "model.onnx", 256 * 1024 * 1024)
        metadata_bytes = _read_member(archive, "model-metadata.json", 1024 * 1024)

    try:
        model = onnx.load_model_from_string(model_bytes)
        onnx.checker.check_model(model)
    except Exception as exc:
        raise ValueError(f"Docker包内model.onnx校验失败: {exc}") from exc
    if len(model.graph.input) != 1:
        raise ValueError(f"当前Docker运行组件仅支持单输入ONNX，检测到 {len(model.graph.input)} 个输入")

    try:
        source_metadata = json.loads(metadata_bytes.decode("utf-8"))
        if not isinstance(source_metadata, dict):
            raise ValueError("元数据根节点不是对象")
    except Exception as exc:
        raise ValueError(f"model-metadata.json无效: {exc}") from exc

    bundle_root = DOCKER_BUNDLE_ROOT / str(int(algorithm_id))
    context = bundle_root / "context"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    context.mkdir(parents=True, exist_ok=True)

    model_path = context / "model.onnx"
    model_path.write_bytes(model_bytes)
    (context / "serve.py").write_text(SERVE_PY_TEMPLATE, encoding="utf-8")
    (context / "requirements.txt").write_text(REQUIREMENTS_TXT, encoding="utf-8")
    (context / "Dockerfile").write_text(DOCKERFILE_TEMPLATE, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "package_type": "foundation_docker_onnx",
        "algorithm_id": int(algorithm_id),
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "source_metadata": source_metadata,
        "input_names": [item.name for item in model.graph.input],
        "input_shapes": [_onnx_shape(item) for item in model.graph.input],
        "output_names": [item.name for item in model.graph.output],
        "runtime_files_regenerated": True,
    }
    manifest_path = context / "model-metadata.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (context / "README.txt").write_text(
        "该目录由数字化底座从上传包中安全重建。\n"
        "构建: docker build -t foundation-imported-model .\n"
        "运行: docker run --rm -p 8080:8080 foundation-imported-model\n",
        encoding="utf-8",
    )

    return {
        "docker_context_path": str(context),
        "embedded_model_path": str(model_path),
        "docker_manifest_path": str(manifest_path),
        "docker_manifest": manifest,
        "onnx_inputs": manifest["input_names"],
        "onnx_input_shapes": manifest["input_shapes"],
        "onnx_outputs": manifest["output_names"],
        "docker_build_status": "not_built",
        "runtime_files_regenerated": True,
    }


def remove_docker_bundle(algorithm_id: int) -> None:
    root = (DOCKER_BUNDLE_ROOT / str(int(algorithm_id))).resolve()
    if DOCKER_BUNDLE_ROOT.resolve() in root.parents and root.is_dir():
        shutil.rmtree(root)
