"""
文件上传服务 API
支持多种文件类型上传、下载、删除和管理
"""
import logging
import os
import shutil
import uuid
from datetime import datetime
from typing import List, Optional

from database.config import get_db
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()

# 配置
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_EXTENSIONS = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp"},
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md"},
    "data": {"csv", "json", "xml", "yaml", "yml", "parquet", "feather"},
    "model": {"pkl", "pth", "pt", "onnx", "h5", "joblib"},
    "archive": {"zip", "tar", "gz", "rar", "7z"},
    "code": {"py", "js", "ts", "java", "cpp", "c", "h", "sql", "sh"},
}

# 扁平化所有允许的后缀
ALL_ALLOWED_EXTENSIONS = set()
for exts in ALLOWED_EXTENSIONS.values():
    ALL_ALLOWED_EXTENSIONS.update(exts)


def get_file_category(filename: str) -> str:
    """根据文件扩展名判断文件类别"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for category, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"


def validate_file(filename: str, file_size: int) -> bool:
    """验证文件是否合法"""
    # 检查文件大小
    if file_size > MAX_FILE_SIZE:
        return False

    # 检查扩展名
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALL_ALLOWED_EXTENSIONS


def save_file(file: UploadFile, category: str = "general") -> dict:
    """保存上传的文件"""
    # 生成唯一文件名
    original_filename = file.filename or "unnamed"
    ext = (
        original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    )
    unique_filename = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex

    # 创建目录
    category_dir = os.path.join(UPLOAD_DIR, category)
    os.makedirs(category_dir, exist_ok=True)

    # 保存文件
    file_path = os.path.join(category_dir, unique_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 获取文件大小
    file_size = os.path.getsize(file_path)

    return {
        "filename": unique_filename,
        "original_filename": original_filename,
        "path": file_path,
        "category": category,
        "size": file_size,
        "extension": ext,
    }


@router.post("/upload", tags=["文件上传"])
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form(default="general"),
    db: Session = Depends(get_db),
):
    """
    上传单个文件

    - category: 文件类别 (image, document, data, model, archive, code, general)
    """
    try:
        # 验证文件
        file_content = await file.read()
        file_size = len(file_content)

        if not validate_file(file.filename or "", file_size):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型或文件过大 (最大 {MAX_FILE_SIZE // 1024 // 1024}MB)",
            )

        # 重置文件指针
        await file.seek(0)

        # 保存文件
        file_info = save_file(file, category)

        # 记录到数据库
        try:
            file_id = db.execute(
                text(
                    """
                    INSERT INTO uploaded_files
                    (filename, original_filename, category, file_path, file_size, extension, uploaded_at)
                    VALUES (:filename, :original_filename, :category, :file_path, :file_size, :extension, :uploaded_at)
                    RETURNING id
                """
                ),
                {
                    "filename": file_info["filename"],
                    "original_filename": file_info["original_filename"],
                    "category": file_info["category"],
                    "file_path": file_info["path"],
                    "file_size": file_info["size"],
                    "extension": file_info["extension"],
                    "uploaded_at": datetime.now(),
                },
            ).fetchone()[0]
            db.commit()
        except Exception as e:
            logger.warning(f"记录文件到数据库失败: {e}")
            file_id = None

        return {
            "success": True,
            "file_id": file_id,
            "filename": file_info["filename"],
            "original_filename": file_info["original_filename"],
            "category": file_info["category"],
            "size": file_info["size"],
            "size_formatted": f"{file_info['size'] / 1024 / 1024:.2f} MB",
            "extension": file_info["extension"],
            "upload_time": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.post("/upload/batch", tags=["文件上传"])
async def upload_batch_files(
    request: Request,
    files: List[UploadFile] = File(...),
    category: str = Form(default="general"),
    db: Session = Depends(get_db),
):
    """批量上传文件"""
    results = []
    errors = []

    for file in files:
        try:
            file_content = await file.read()
            file_size = len(file_content)

            if not validate_file(file.filename or "", file_size):
                errors.append({"filename": file.filename, "error": "不支持的文件类型或文件过大"})
                continue

            await file.seek(0)
            file_info = save_file(file, category)

            # 记录到数据库
            try:
                file_id = db.execute(
                    text(
                        """
                        INSERT INTO uploaded_files
                        (filename, original_filename, category, file_path, file_size, extension, uploaded_at)
                        VALUES (:filename, :original_filename, :category, :file_path, :file_size, :extension, :uploaded_at)
                        RETURNING id
                    """
                    ),
                    {
                        "filename": file_info["filename"],
                        "original_filename": file_info["original_filename"],
                        "category": file_info["category"],
                        "file_path": file_info["path"],
                        "file_size": file_info["size"],
                        "extension": file_info["extension"],
                        "uploaded_at": datetime.now(),
                    },
                ).fetchone()[0]
                db.commit()
            except Exception as e:
                logger.warning(f"记录文件到数据库失败: {e}")
                file_id = None

            results.append(
                {
                    "success": True,
                    "file_id": file_id,
                    "filename": file_info["filename"],
                    "original_filename": file_info["original_filename"],
                    "size": file_info["size"],
                }
            )

        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})

    return {
        "total": len(files),
        "success_count": len(results),
        "error_count": len(errors),
        "results": results,
        "errors": errors,
    }


@router.get("/files", tags=["文件上传"])
async def list_files(
    request: Request,
    category: Optional[str] = Query(None, description="文件类别"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取文件列表"""
    try:
        offset = (page - 1) * page_size

        # 构建查询
        count_query = "SELECT COUNT(*) FROM uploaded_files"
        data_query = """
            SELECT id, filename, original_filename, category, file_size, extension, uploaded_at
            FROM uploaded_files
        """

        params = {}
        if category:
            count_query += " WHERE category = :category"
            data_query += " WHERE category = :category"
            params["category"] = category

        data_query += " ORDER BY uploaded_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = page_size
        params["offset"] = offset

        total = db.execute(
            text(count_query),
            {k: v for k, v in params.items() if k != "limit" and k != "offset"},
        ).fetchone()[0]
        files = db.execute(text(data_query), params).fetchall()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "items": [
                {
                    "id": f[0],
                    "filename": f[1],
                    "original_filename": f[2],
                    "category": f[3],
                    "size": f[4],
                    "size_formatted": f"{f[4] / 1024 / 1024:.2f} MB"
                    if f[4]
                    else "0 MB",
                    "extension": f[5],
                    "uploaded_at": f[6].isoformat() if f[6] else None,
                }
                for f in files
            ],
        }
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        return {"total": 0, "items": []}


@router.get("/files/{file_id}", tags=["文件上传"])
async def get_file_info(file_id: int, db: Session = Depends(get_db)):
    """获取文件详细信息"""
    try:
        file = db.execute(
            text("SELECT * FROM uploaded_files WHERE id = :id"), {"id": file_id}
        ).fetchone()

        if not file:
            raise HTTPException(status_code=404, detail="文件不存在")

        return {
            "id": file[0],
            "filename": file[1],
            "original_filename": file[2],
            "category": file[3],
            "file_path": file[4],
            "file_size": file[5],
            "extension": file[6],
            "uploaded_at": file[7].isoformat() if file[7] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取文件信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取文件信息失败")


@router.get("/download/{file_id}", tags=["文件上传"])
async def download_file(file_id: int, db: Session = Depends(get_db)):
    """下载文件"""
    try:
        file = db.execute(
            text(
                "SELECT filename, original_filename, file_path FROM uploaded_files WHERE id = :id"
            ),
            {"id": file_id},
        ).fetchone()

        if not file:
            raise HTTPException(status_code=404, detail="文件不存在")

        file_path = file[2]
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件物理路径不存在")

        return FileResponse(
            path=file_path,
            filename=file[1] or file[0],
            media_type="application/octet-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        raise HTTPException(status_code=500, detail="下载失败")


@router.delete("/files/{file_id}", tags=["文件上传"])
async def delete_file(file_id: int, db: Session = Depends(get_db)):
    """删除文件"""
    try:
        file = db.execute(
            text("SELECT filename, file_path FROM uploaded_files WHERE id = :id"),
            {"id": file_id},
        ).fetchone()

        if not file:
            raise HTTPException(status_code=404, detail="文件不存在")

        # 删除物理文件
        file_path = file[1]
        if os.path.exists(file_path):
            os.remove(file_path)

        # 删除数据库记录
        db.execute(text("DELETE FROM uploaded_files WHERE id = :id"), {"id": file_id})
        db.commit()

        return {"success": True, "message": "文件已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除文件失败: {e}")
        raise HTTPException(status_code=500, detail="删除失败")


@router.get("/categories", tags=["文件上传"])
async def get_categories(request: Request, db: Session = Depends(get_db)):
    """获取文件类别统计"""
    try:
        categories = db.execute(
            text(
                """
                SELECT category, COUNT(*) as count, SUM(file_size) as total_size
                FROM uploaded_files
                GROUP BY category
            """
            )
        ).fetchall()

        return {
            "items": [
                {
                    "category": c[0],
                    "count": c[1],
                    "total_size": c[2] or 0,
                    "total_size_formatted": f"{(c[2] or 0) / 1024 / 1024:.2f} MB",
                }
                for c in categories
            ]
        }
    except Exception as e:
        logger.error(f"获取类别统计失败: {e}")
        return {"items": []}


@router.get("/stats", tags=["文件上传"])
async def get_upload_stats(request: Request, db: Session = Depends(get_db)):
    """获取上传统计信息"""
    try:
        total_files = db.execute(
            text("SELECT COUNT(*) FROM uploaded_files")
        ).fetchone()[0]
        total_size = db.execute(
            text("SELECT COALESCE(SUM(file_size), 0) FROM uploaded_files")
        ).fetchone()[0]

        today = datetime.now().date()
        today_files = db.execute(
            text(
                "SELECT COUNT(*) FROM uploaded_files WHERE DATE(uploaded_at) = :today"
            ),
            {"today": today},
        ).fetchone()[0]

        return {
            "total_files": total_files,
            "total_size": total_size,
            "total_size_formatted": f"{total_size / 1024 / 1024:.2f} MB",
            "today_uploads": today_files,
        }
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        return {"total_files": 0, "total_size": 0, "today_uploads": 0}
