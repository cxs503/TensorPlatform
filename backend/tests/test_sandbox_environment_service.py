import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from services import sandbox_environment_service as service


def configure_isolated_state(tmp_path, monkeypatch):
    root = tmp_path / "environments"
    python_root = root / "pythons"
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    python_dir = python_root / f"cpython-{version}.99-linux-x86_64-gnu" / "bin"
    python_dir.mkdir(parents=True)
    (python_dir / "python").symlink_to(sys.executable)
    monkeypatch.setattr(service, "ROOT", root)
    monkeypatch.setattr(service, "PYTHON_ROOT", python_root)
    monkeypatch.setattr(service, "BUILDS_ROOT", root / "builds")
    monkeypatch.setattr(service, "BASES_FILE", root / "base_environments.json")
    monkeypatch.setattr(service, "DEPENDENCIES_FILE", root / "dependency_sets.json")
    monkeypatch.setattr(service, "BUILDS_FILE", root / "runtime_builds.json")
    monkeypatch.setattr(service, "_bases", [{
        "id": 1, "name": "test base", "python_version": version,
        "compute_type": "cpu", "cuda_version": None, "system": True,
    }])
    monkeypatch.setattr(service, "_dependencies", [{
        "id": 1, "name": "Python standard library", "requirements": "",
        "package_count": 0, "system": True,
    }])
    monkeypatch.setattr(service, "_builds", [])
    (root / "builds").mkdir(parents=True)
    return root


def test_requirement_validation_blocks_pip_options_and_urls():
    assert service.normalize_requirements("numpy>=1.24,<3\npandas==2.0.3")[1] == [
        "numpy>=1.24,<3", "pandas==2.0.3",
    ]
    with pytest.raises(HTTPException):
        service.normalize_requirements("--index-url https://example.com/simple")
    with pytest.raises(HTTPException):
        service.normalize_requirements("package @ https://example.com/package.whl")


def test_cpu_pytorch_uses_trusted_cpu_index(tmp_path):
    command = service._pip_install_command(
        Path("/runtime/bin/python"),
        tmp_path / "requirements.in",
        {"compute_type": "cpu"},
        ["torch==2.1.0+cpu", "numpy==1.24.4"],
    )

    assert command[command.index("--extra-index-url") + 1] == service.PYTORCH_CPU_INDEX_URL
    assert command[command.index("--find-links") + 1] == str(service.WHEELHOUSE_ROOT)
    assert command[-2:] == ["-r", str(tmp_path / "requirements.in")]


def test_cached_cpu_pytorch_wheel_skips_remote_index(tmp_path, monkeypatch):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "torch-2.1.0+cpu-cp311-cp311-linux_x86_64.whl").touch()
    monkeypatch.setattr(service, "WHEELHOUSE_ROOT", wheelhouse)

    command = service._pip_install_command(
        Path("/runtime/bin/python"),
        tmp_path / "requirements.in",
        {"compute_type": "cpu", "actual_python_version": "3.11.15"},
        ["torch==2.1.0+cpu", "numpy==1.24.4"],
    )

    assert command[command.index("--find-links") + 1] == str(wheelhouse)
    assert "--extra-index-url" not in command


@pytest.mark.parametrize("requirement", ["torch", "torch==2.1.0", "torch>=2.1"])
def test_cpu_pytorch_requires_explicit_cpu_wheel(tmp_path, requirement):
    with pytest.raises(HTTPException, match="CPU版本"):
        service._pip_install_command(
            Path("/runtime/bin/python"),
            tmp_path / "requirements.in",
            {"compute_type": "cpu"},
            [requirement],
        )


def test_regular_dependencies_do_not_add_pytorch_index(tmp_path):
    command = service._pip_install_command(
        Path("/runtime/bin/python"),
        tmp_path / "requirements.in",
        {"compute_type": "cpu"},
        ["numpy==1.24.4"],
    )

    assert "--extra-index-url" not in command


def test_system_build_exposes_real_managed_python(tmp_path, monkeypatch):
    configure_isolated_state(tmp_path, monkeypatch)

    builds = service.list_runtime_builds()

    assert len(builds) == 1
    assert builds[0]["available"] is True
    assert Path(builds[0]["python_path"]).is_file()
    assert builds[0]["mode"] == "managed_python"


def test_empty_dependency_build_creates_isolated_venv(tmp_path, monkeypatch):
    configure_isolated_state(tmp_path, monkeypatch)
    dependency = service.create_dependency_set({
        "name": "empty dependency set", "requirements": "", "description": "test",
    })
    build = service.create_runtime_build({
        "base_environment_id": 1, "dependency_set_id": dependency["id"], "name": "test runtime",
    })

    service.build_runtime(build["id"])
    built = next(item for item in service.list_runtime_builds() if item["id"] == build["id"])

    assert built["status"] == "ready"
    assert built["available"] is True
    assert built["progress"] == 100
    assert built["progress_stage"] == "构建完成"
    assert "已安装并锁定" in built["progress_detail"]
    assert Path(built["python_path"]).is_file()
    assert built["runtime_digest"].startswith("sha256:")
