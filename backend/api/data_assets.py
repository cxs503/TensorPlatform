"""
数据资产 API — 数据产品登记/上架/估值
"""
from fastapi import APIRouter, Depends, HTTPException
from database.config import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from models.core import DataProduct

router = APIRouter(prefix="/data-assets", tags=["数据资产"])


@router.get("")
def list_assets(db: Session = Depends(get_db)):
    products = db.query(DataProduct).order_by(DataProduct.id).all()
    return {"total": len(products), "items": [p.to_dict() for p in products]}


@router.post("")
def create_asset(req: dict, db: Session = Depends(get_db)):
    name = req.get("name")
    if not name:
        raise HTTPException(400, "产品名称必填")
    product = DataProduct(
        name=name,
        category=req.get("category", "能源"),
        description=req.get("description", ""),
        format=req.get("format", "CSV"),
        size_desc=req.get("size_desc", ""),
        price=req.get("price", 0),
        status="draft",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"id": product.id, "name": product.name, "status": product.status}


@router.put("/{asset_id}/publish")
def publish_asset(asset_id: int, db: Session = Depends(get_db)):
    db.query(DataProduct).filter(DataProduct.id == asset_id).update({
        DataProduct.status: "published",
    })
    db.commit()
    return {"status": "published"}


@router.get("/stats")
def asset_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(DataProduct.id)).scalar()
    published = db.query(func.count(DataProduct.id)).filter(
        DataProduct.status == "published"
    ).scalar()
    total_value = db.query(func.coalesce(func.sum(DataProduct.price), 0)).scalar()
    return {"total": total, "published": published, "total_value": total_value}
