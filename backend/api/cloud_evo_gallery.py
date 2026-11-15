"""云图演化动画·gallery 页 API (FEAT-SIM-DATAHOUSE)

gallery 页可播：列出已入库的演化动画（mp4/gif + 海报 png），
复用 papers 免鉴权图床模式——只读 GET 放行单文件（mp4/png），
写操作仍需认证。

路由：
  GET  /api/v1/cloud-evo/gallery          —— 列出全部动画（含可播 url）
  GET  /api/v1/cloud-evo/gallery/{case}/video —— 免鉴权直读 mp4/gif（可播）
  GET  /api/v1/cloud-evo/gallery/{case}/poster —— 免鉴权直读海报 png
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/cloud-evo", tags=["云图演化动画·gallery"])

GALLERY_INDEX = Path(os.environ.get(
    "CLOUD_EVO_GALLERY_INDEX", "artifacts/cloud_evo/gallery_index.json"))
_RENDER_ROOT = Path(os.environ.get(
    "CLOUD_EVO_RENDER_ROOT", "artifacts/cloud_evo"))

# 文件名白名单（防路径穿越）
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _load_index() -> list:
    try:
        if not GALLERY_INDEX.is_file():
            return []
        return json.loads(GALLERY_INDEX.read_text()).get("items", [])
    except Exception:
        return []


@router.get("/gallery")
def gallery():
    """列出全部已入库动画（含可播 url）。"""
    items = _load_index()
    for it in items:
        case = it.get("case", "")
        it["video_url"] = f"/api/v1/cloud-evo/gallery/{case}/video"
        it["poster_url"] = f"/api/v1/cloud-evo/gallery/{case}/poster"
    return {"total": len(items), "items": items}


def _resolve(case: str, kind: str) -> Path:
    if not _SAFE_NAME.match(case):
        raise HTTPException(422, "bad case name")
    # 渲染产物在 <case>_rendered/ 下（capture 钩子写）或 <case>/ 下（fieldgen 写）
    for base in (_RENDER_ROOT / case / "_rendered", _RENDER_ROOT / case):
        if kind == "video":
            for ext in (".mp4", ".gif"):
                p = base / f"{case}_evo{ext}"
                if p.is_file():
                    return p
        elif kind == "poster":
            p = base / f"{case}_poster.png"
            if p.is_file():
                return p
    raise HTTPException(404, "not found")


@router.get("/gallery/{case}/video")
def video(case: str):
    """免鉴权直读 mp4/gif（可播）。"""
    p = _resolve(case, "video")
    media = "video/mp4" if p.suffix == ".mp4" else "image/gif"
    return FileResponse(p, media_type=media)


@router.get("/gallery/{case}/poster")
def poster(case: str):
    """免鉴权直读海报 png。"""
    p = _resolve(case, "poster")
    return FileResponse(p, media_type="image/png")
