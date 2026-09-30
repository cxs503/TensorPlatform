"""Internal-only Docker build worker for validated platform bundle contexts."""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="foundation Docker Builder", docs_url=None, redoc_url=None)
CONTEXT_ROOT = Path("/app/uploads/docker-bundles")
JOB_ROOT = Path("/app/uploads/docker-builder-jobs")
DOCKER_BIN = os.getenv("DOCKER_BIN", "/usr/bin/docker")
DOCKER_NETWORK = os.getenv("DOCKER_RUNTIME_NETWORK", "open-digital-foundation_foundation-network")
BUILDER_TOKEN = os.getenv("JWT_SECRET_KEY", "")
_lock = threading.Lock()


class BuildRequest(BaseModel):
    algorithm_id: int = Field(gt=0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authorize(x_builder_token: str | None = Header(default=None)) -> None:
    if not BUILDER_TOKEN or not x_builder_token or not secrets.compare_digest(x_builder_token, BUILDER_TOKEN):
        raise HTTPException(status_code=403, detail="构建器认证失败")


def _job_path(job_id: str) -> Path:
    if not re.fullmatch(r"docker-[0-9a-f]{12}", job_id):
        raise HTTPException(status_code=400, detail="构建任务ID无效")
    return JOB_ROOT / f"{job_id}.json"


def _read_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Docker构建任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_job(job: dict[str, Any]) -> None:
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    path = _job_path(job["job_id"])
    temporary = path.with_suffix(".tmp")
    with _lock:
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _append_log(job: dict[str, Any], line: str) -> None:
    logs = job.setdefault("logs", [])
    logs.append(line.rstrip())
    if len(logs) > 1000:
        del logs[:-1000]


def _context_for(algorithm_id: int) -> Path:
    context = (CONTEXT_ROOT / str(int(algorithm_id)) / "context").resolve()
    root = CONTEXT_ROOT.resolve()
    if root not in context.parents or not context.is_dir():
        raise HTTPException(status_code=404, detail="Docker受控构建目录不存在")
    required = {"Dockerfile", "serve.py", "requirements.txt", "model.onnx", "model-metadata.json"}
    missing = sorted(name for name in required if not (context / name).is_file())
    if missing:
        raise HTTPException(status_code=400, detail=f"Docker受控构建目录缺少: {', '.join(missing)}")
    return context


def _build(job_id: str) -> None:
    job = _read_job(job_id)
    context = _context_for(job["algorithm_id"])
    job.update(status="building", stage="构建镜像", progress=5, started_at=_now())
    _write_job(job)
    command = [DOCKER_BIN, "build", "--pull=false", "-t", job["image_name"], str(context)]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            _append_log(job, line)
            match = re.search(r"Step\s+(\d+)/(\d+)", line)
            if match:
                step, total = int(match.group(1)), max(1, int(match.group(2)))
                job["progress"] = min(95, 5 + round(step / total * 90))
                job["stage"] = f"构建镜像 {step}/{total}"
            _write_job(job)
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"docker build退出码 {return_code}")
        inspect = subprocess.run(
            [DOCKER_BIN, "image", "inspect", job["image_name"], "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        job.update(
            status="success",
            stage="构建完成",
            progress=100,
            image_id=inspect.stdout.strip(),
            completed_at=_now(),
            error=None,
        )
    except Exception as exc:
        _append_log(job, f"ERROR: {exc}")
        job.update(status="failed", stage="构建失败", completed_at=_now(), error=str(exc))
    _write_job(job)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [DOCKER_BIN, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return {"status": "healthy", "docker_server_version": result.stdout.strip()}
    except Exception as exc:
        return {"status": "degraded", "error": str(exc)}


@app.post("/jobs", dependencies=[Depends(_authorize)])
def create_build(req: BuildRequest) -> dict[str, Any]:
    _context_for(req.algorithm_id)
    job_id = f"docker-{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "algorithm_id": req.algorithm_id,
        "status": "queued",
        "stage": "等待构建",
        "progress": 0,
        "image_name": f"foundation-imported-model:{req.algorithm_id}",
        "image_id": None,
        "container_name": None,
        "service_url": None,
        "logs": [],
        "error": None,
        "created_at": _now(),
    }
    _write_job(job)
    threading.Thread(target=_build, args=(job_id,), daemon=True).start()
    return job


@app.get("/jobs/{job_id}", dependencies=[Depends(_authorize)])
def get_job(job_id: str) -> dict[str, Any]:
    return _read_job(job_id)


@app.post("/jobs/{job_id}/run", dependencies=[Depends(_authorize)])
def run_job(job_id: str) -> dict[str, Any]:
    job = _read_job(job_id)
    if job.get("status") not in {"success", "running", "stopped"}:
        raise HTTPException(status_code=409, detail="镜像尚未构建成功")
    container_name = f"kp-imported-{job['algorithm_id']}-{job_id[-6:]}"
    subprocess.run([DOCKER_BIN, "rm", "-f", container_name], capture_output=True, text=True)
    command = [
        DOCKER_BIN, "run", "-d", "--name", container_name,
        "--network", DOCKER_NETWORK,
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--label", "foundation.docker-bundle=true",
        "--label", f"foundation.algorithm-id={job['algorithm_id']}",
        job["image_name"],
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=(exc.stderr or exc.stdout or str(exc)).strip()) from exc
    job.update(
        status="running",
        stage="临时容器运行中",
        container_id=result.stdout.strip(),
        container_name=container_name,
        service_url=f"http://{container_name}:8080",
        started_container_at=_now(),
    )
    _write_job(job)
    return job


@app.post("/jobs/{job_id}/stop", dependencies=[Depends(_authorize)])
def stop_job(job_id: str) -> dict[str, Any]:
    job = _read_job(job_id)
    container_name = job.get("container_name")
    if container_name:
        subprocess.run([DOCKER_BIN, "rm", "-f", container_name], capture_output=True, text=True)
    job.update(status="stopped", stage="临时容器已停止", stopped_at=_now(), service_url=None)
    _write_job(job)
    return job


@app.delete("/algorithms/{algorithm_id}", dependencies=[Depends(_authorize)])
def cleanup_algorithm(algorithm_id: int) -> dict[str, Any]:
    removed_containers = []
    if JOB_ROOT.is_dir():
        for path in JOB_ROOT.glob("docker-*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("algorithm_id") != algorithm_id:
                continue
            if job.get("container_name"):
                subprocess.run([DOCKER_BIN, "rm", "-f", job["container_name"]], capture_output=True, text=True)
                removed_containers.append(job["container_name"])
            path.unlink(missing_ok=True)
    image_name = f"foundation-imported-model:{algorithm_id}"
    subprocess.run([DOCKER_BIN, "image", "rm", "-f", image_name], capture_output=True, text=True)
    bundle_root = CONTEXT_ROOT / str(algorithm_id)
    if bundle_root.is_dir():
        shutil.rmtree(bundle_root)
    return {"algorithm_id": algorithm_id, "removed_containers": removed_containers, "image": image_name}
