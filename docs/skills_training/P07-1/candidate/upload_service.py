"""P07-1 候选实现：数据集文件上传端点（被评审对象）。

刻意按「只有 happy-path 测试的端点」的常见形态实现：
- 令牌认证存在（happy path 覆盖），但数据集属主校验缺失；
- 直接 `await file.read()` 全量入内存后落盘，无尺寸上限；
- 先写终态文件再做可解析性校验，失败不回滚；
- 客户端 filename 直接参与路径拼接；
- Content-Range / 偏移被忽略。
评审结论见 ../P07-1.md，可执行取证见 ../probe_upload.py。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

UPLOAD_ROOT = Path(os.environ.get("P07_UPLOAD_ROOT", "/tmp/p07_uploads"))
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("P07_DB", str(UPLOAD_ROOT / "meta.sqlite")))

# 演示用静态令牌表（非真实密钥）
TOKENS = {"tok-alice": "alice", "tok-bob": "bob"}

app = FastAPI(title="P07 upload candidate", version="0.1.0")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS datasets("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "owner TEXT NOT NULL,"
            "name TEXT NOT NULL,"
            "path TEXT,"
            "rows INTEGER DEFAULT 0,"
            "updated_at TEXT)"
        )


_init_db()


def _user(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(None, 1)[1].strip()
    user = TOKENS.get(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return user


def _row(dataset_id: int) -> sqlite3.Row:
    with _db() as c:
        r = c.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return r


def _count_rows(path: Path, name: str) -> int:
    """最小可解析性检查：CSV 需有表头 + 至少 1 行数据，且不含 NUL。"""
    if not name.lower().endswith(".csv"):
        return 0
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("binary content in csv")
    text = raw.decode("utf-8", errors="strict")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("csv needs header plus at least one data row")
    return len(lines) - 1


@app.post("/api/v1/datasets")
async def create_dataset(payload: dict, authorization: str | None = Header(default=None)):
    user = _user(authorization)
    with _db() as c:
        cur = c.execute(
            "INSERT INTO datasets(owner, name, updated_at) VALUES(?,?,?)",
            (user, str(payload.get("name", "dataset")), datetime.now(timezone.utc).isoformat()),
        )
        return {"dataset_id": cur.lastrowid}


@app.post("/api/v1/datasets/{dataset_id}/upload")
async def upload_dataset_file(
    dataset_id: int,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    user = _user(authorization)                       # 认证
    ds = _row(dataset_id)                             # 存在性；**(无属主校验)**
    stored_name = file.filename or f"dataset_{dataset_id}.csv"
    target = UPLOAD_ROOT / stored_name                # 客户端名直接拼路径
    content = await file.read()                       # 全量读入内存，无上限
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)                       # 先落终态文件
    try:
        rows = _count_rows(target, stored_name)       # 再校验
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc  # 不回滚
    with _db() as c:
        c.execute(
            "UPDATE datasets SET path=?, rows=?, updated_at=? WHERE id=?",
            (str(target), rows, datetime.now(timezone.utc).isoformat(), dataset_id),
        )
    return {"dataset_id": dataset_id, "bytes": len(content), "rows": rows,
            "stored_name": stored_name, "actor": user}


@app.get("/api/v1/datasets/{dataset_id}/download")
async def download_dataset_file(dataset_id: int):
    ds = _row(dataset_id)                             # 无认证
    if not ds["path"]:
        raise HTTPException(status_code=404, detail="dataset has no file")
    return FileResponse(ds["path"])


@app.get("/api/v1/datasets/{dataset_id}/meta")
async def dataset_meta(dataset_id: int, authorization: str | None = Header(default=None)):
    _user(authorization)
    ds = _row(dataset_id)
    return {k: ds[k] for k in ("id", "owner", "name", "path", "rows", "updated_at")}