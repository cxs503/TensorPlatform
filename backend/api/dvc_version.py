"""
DVC 数据版本控制 API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import subprocess, os, json
from datetime import datetime

router = APIRouter(prefix="/dvc", tags=["数据版本"])

DVC_REMOTE = os.getenv("DVC_REMOTE", "minio-storage")
DVC_DIR = os.getenv("DVC_DATA_DIR", "/app/data/dvc")

class VersionInfo(BaseModel):
    id: str
    dataset: str
    version: str
    message: str
    created_at: str
    size: str = ""

class CommitRequest(BaseModel):
    dataset: str
    message: str = ""
    files: List[str] = []

class CheckoutRequest(BaseModel):
    dataset: str
    version: str

@router.get("/datasets")
async def list_datasets():
    """列出所有 DVC 跟踪的数据集"""
    datasets = []
    if not os.path.isdir(DVC_DIR):
        os.makedirs(DVC_DIR, exist_ok=True)
    try:
        import dvc.repo
        import dvc.api
        for name in os.listdir(DVC_DIR):
            dpath = os.path.join(DVC_DIR, name)
            if os.path.isdir(os.path.join(dpath, ".dvc")):
                datasets.append({"name": name, "path": dpath})
    except Exception:
        pass
    return {"datasets": datasets, "total": len(datasets)}

@router.get("/versions/{dataset}")
async def list_versions(dataset: str):
    """列出数据集的版本历史"""
    dpath = os.path.join(DVC_DIR, dataset)
    if not os.path.isdir(dpath):
        return {"versions": [], "dataset": dataset}
    versions = []
    try:
        result = subprocess.run(
            ["dvc", "log"], cwd=dpath, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            for line in lines:
                parts = line.strip().split(maxsplit=2)
                if len(parts) >= 2:
                    versions.append({
                        "id": parts[0],
                        "version": parts[0],
                        "message": parts[1] if len(parts) > 1 else "",
                        "created_at": ""
                    })
    except Exception as e:
        pass
    return {"versions": versions, "dataset": dataset, "total": len(versions)}

@router.post("/commit")
async def commit_version(req: CommitRequest):
    """提交新版本"""
    dpath = os.path.join(DVC_DIR, req.dataset)
    os.makedirs(dpath, exist_ok=True)
    if not os.path.isdir(os.path.join(dpath, ".dvc")):
        subprocess.run(["dvc", "init"], cwd=dpath, capture_output=True, text=True, timeout=30)
        subprocess.run(["dvc", "remote", "add", "-d", DVC_REMOTE, f"s3://datasets/{req.dataset}"],
                       cwd=dpath, capture_output=True, text=True, timeout=30)
    for f in req.files:
        fpath = os.path.join(dpath, f)
        if os.path.exists(fpath):
            subprocess.run(["dvc", "add", f], cwd=dpath, capture_output=True, text=True, timeout=60)
    result = subprocess.run(
        ["dvc", "commit", "-m", req.message or f"auto commit {datetime.now().isoformat()}"],
        cwd=dpath, capture_output=True, text=True, timeout=60
    )
    return {"status": "committed", "dataset": req.dataset, "output": result.stdout[:500]}

@router.post("/checkout")
async def checkout_version(req: CheckoutRequest):
    """切换到指定版本"""
    dpath = os.path.join(DVC_DIR, req.dataset)
    if not os.path.isdir(dpath):
        raise HTTPException(404, "数据集不存在")
    result = subprocess.run(
        ["dvc", "checkout", req.version], cwd=dpath, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise HTTPException(500, result.stderr[:500])
    return {"status": "checked_out", "dataset": req.dataset, "version": req.version}

@router.post("/push")
async def push_dataset(dataset: str):
    """推送到远程存储 (MinIO)"""
    dpath = os.path.join(DVC_DIR, dataset)
    if not os.path.isdir(dpath):
        raise HTTPException(404, "数据集不存在")
    result = subprocess.run(
        ["dvc", "push"], cwd=dpath, capture_output=True, text=True, timeout=120
    )
    return {"status": "pushed" if result.returncode == 0 else "push_failed",
            "dataset": dataset, "output": result.stdout[:500]}

@router.post("/pull")
async def pull_dataset(dataset: str):
    """从远程存储拉取"""
    dpath = os.path.join(DVC_DIR, dataset)
    os.makedirs(dpath, exist_ok=True)
    if not os.path.isdir(os.path.join(dpath, ".dvc")):
        raise HTTPException(404, "数据集未初始化")
    result = subprocess.run(
        ["dvc", "pull"], cwd=dpath, capture_output=True, text=True, timeout=120
    )
    return {"status": "pulled" if result.returncode == 0 else "pull_failed",
            "dataset": dataset, "output": result.stdout[:500]}

@router.get("/diff/{dataset}")
async def diff_versions(dataset: str, v1: str = "HEAD~1", v2: str = "HEAD"):
    """对比两个版本差异"""
    dpath = os.path.join(DVC_DIR, dataset)
    if not os.path.isdir(dpath):
        raise HTTPException(404, "数据集不存在")
    result = subprocess.run(
        ["dvc", "diff", v1, v2], cwd=dpath, capture_output=True, text=True, timeout=30
    )
    diffs = []
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            diffs.append({"change": line.strip()})
    return {"dataset": dataset, "v1": v1, "v2": v2, "diffs": diffs}
