"""
模型市场API
"""
from typing import List, Optional

from utils.dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.marketplace_service import get_marketplace

router = APIRouter(prefix="/marketplace", tags=["模型市场"])


class PublishModelReq(BaseModel):
    model_id: str
    name: str
    description: str
    category: str
    tags: List[str] = []
    documentation: str = ""
    license: str = "MIT"


class ReviewReq(BaseModel):
    rating: int
    comment: str = ""


@router.post("/publish")
def publish_model(req: PublishModelReq, current_user: dict = Depends(get_current_user)):
    """发布模型到市场"""
    marketplace = get_marketplace()

    listing = marketplace.publish_model(
        model_id=req.model_id,
        name=req.name,
        description=req.description,
        category=req.category,
        owner_id=current_user.get("user_id", 0),
        owner_name=current_user.get("username", "unknown"),
        tags=req.tags,
        documentation=req.documentation,
        license=req.license,
    )

    return listing


@router.get("/models")
def search_models(
    query: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
    sort_by: str = "downloads",
    page: int = 1,
    size: int = 20,
):
    """搜索模型"""
    marketplace = get_marketplace()
    tag_list = tags.split(",") if tags else None

    return marketplace.search_models(
        query=query,
        category=category,
        tags=tag_list,
        sort_by=sort_by,
        page=page,
        size=size,
    )


@router.get("/models/{listing_id}")
def get_model(listing_id: str):
    """获取模型详情"""
    marketplace = get_marketplace()
    model = marketplace.get_listing(listing_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.post("/models/{listing_id}/download")
def download_model(listing_id: str):
    """下载模型"""
    marketplace = get_marketplace()
    success = marketplace.increment_download(listing_id)
    if success:
        return {"message": "下载计数已更新"}
    raise HTTPException(status_code=404, detail="模型不存在")


@router.post("/models/{listing_id}/reviews")
def add_review(
    listing_id: str, req: ReviewReq, current_user: dict = Depends(get_current_user)
):
    """添加评论"""
    marketplace = get_marketplace()

    try:
        review = marketplace.add_review(
            listing_id=listing_id,
            user_id=current_user.get("user_id", 0),
            rating=req.rating,
            comment=req.comment,
        )
        return review
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/models/{listing_id}/reviews")
def get_reviews(listing_id: str):
    """获取评论"""
    marketplace = get_marketplace()
    reviews = marketplace.get_reviews(listing_id)
    return {"total": len(reviews), "items": reviews}


@router.get("/categories")
def get_categories():
    """获取模型分类"""
    from services.marketplace_service import ModelCategory

    return {
        "categories": [
            {"id": c.value, "name": c.name.replace("_", " ").title()}
            for c in ModelCategory
        ]
    }
