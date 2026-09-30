import io
import tarfile

import pytest
from fastapi import HTTPException

from api.algorithm_store import _validate_artifact


REQUIRED = {
    "Dockerfile": b"FROM python:3.11-slim\n",
    "serve.py": b"app = None\n",
    "requirements.txt": b"onnxruntime==1.18.0\n",
    "model.onnx": b"fake-for-archive-structure-test",
    "model-metadata.json": b"{}",
    "README.txt": b"test\n",
}


def _tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_platform_docker_tar_is_recognized():
    result = _validate_artifact("model-docker.tar.gz", _tar_bytes(REQUIRED))

    assert result["artifact_format"] == "docker_bundle"
    assert result["artifact_kind"] == "deployment_bundle"
    assert result["artifact_label"] == "Docker部署包"
    assert sorted(result["archive_files"]) == sorted(REQUIRED)


def test_docker_tar_rejects_path_traversal():
    files = dict(REQUIRED)
    files["../escape.txt"] = b"blocked"

    with pytest.raises(HTTPException) as exc:
        _validate_artifact("model-docker.tar.gz", _tar_bytes(files))

    assert exc.value.status_code == 400
    assert "不安全路径" in exc.value.detail


def test_docker_tar_requires_complete_runtime_files():
    files = dict(REQUIRED)
    files.pop("model.onnx")

    with pytest.raises(HTTPException) as exc:
        _validate_artifact("model-docker.tar.gz", _tar_bytes(files))

    assert exc.value.status_code == 400
    assert "完整运行文件" in exc.value.detail
