"""
模型市场服务
支持模型发布、发现、分享、协作、评分评论
"""
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ModelStatus(str, Enum):
    """模型状态"""

    DRAFT = "draft"
    PUBLISHED = "published"
    REVIEWING = "reviewing"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class ModelCategory(str, Enum):
    """模型分类"""

    COMPUTER_VISION = "computer_vision"
    NLP = "nlp"
    TIME_SERIES = "time_series"
    RECOMMENDATION = "recommendation"
    OTHER = "other"


class ModelMarketplace:
    """模型市场服务"""

    def __init__(self):
        self.models: Dict[str, Dict] = {}
        self.reviews: Dict[str, List[Dict]] = {}

    def publish_model(
        self,
        model_id: str,
        name: str,
        description: str,
        category: str,
        owner_id: int,
        owner_name: str,
        tags: List[str] = None,
        documentation: str = "",
        license: str = "MIT",
    ) -> Dict:
        """发布模型到市场"""
        listing_id = f"listing_{uuid.uuid4().hex[:12]}"

        listing = {
            "listing_id": listing_id,
            "model_id": model_id,
            "name": name,
            "description": description,
            "category": category,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "tags": tags or [],
            "documentation": documentation,
            "license": license,
            "status": ModelStatus.PUBLISHED,
            "downloads": 0,
            "rating": 0.0,
            "rating_count": 0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        self.models[listing_id] = listing
        return listing

    def get_listing(self, listing_id: str) -> Optional[Dict]:
        """获取模型列表"""
        return self.models.get(listing_id)

    def search_models(
        self,
        query: str = None,
        category: str = None,
        tags: List[str] = None,
        sort_by: str = "downloads",
        page: int = 1,
        size: int = 20,
    ) -> Dict:
        """搜索模型"""
        results = list(self.models.values())

        # 过滤
        if query:
            query_lower = query.lower()
            results = [
                r
                for r in results
                if query_lower in r.get("name", "").lower()
                or query_lower in r.get("description", "").lower()
            ]

        if category:
            results = [r for r in results if r.get("category") == category]

        if tags:
            results = [
                r for r in results if any(tag in r.get("tags", []) for tag in tags)
            ]

        # 排序
        if sort_by == "downloads":
            results.sort(key=lambda x: x.get("downloads", 0), reverse=True)
        elif sort_by == "rating":
            results.sort(key=lambda x: x.get("rating", 0), reverse=True)
        elif sort_by == "newest":
            results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        # 分页
        total = len(results)
        items = results[(page - 1) * size : page * size]

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": items,
        }

    def increment_download(self, listing_id: str) -> bool:
        """增加下载量"""
        if listing_id in self.models:
            self.models[listing_id]["downloads"] += 1
            return True
        return False

    def add_review(
        self,
        listing_id: str,
        user_id: int,
        rating: int,
        comment: str = "",
    ) -> Dict:
        """添加评论"""
        if listing_id not in self.models:
            raise ValueError("Model not found")

        review = {
            "review_id": f"review_{uuid.uuid4().hex[:8]}",
            "listing_id": listing_id,
            "user_id": user_id,
            "rating": rating,
            "comment": comment,
            "created_at": datetime.now().isoformat(),
        }

        if listing_id not in self.reviews:
            self.reviews[listing_id] = []
        self.reviews[listing_id].append(review)

        # 更新平均评分
        reviews = self.reviews[listing_id]
        avg_rating = sum(r["rating"] for r in reviews) / len(reviews)
        self.models[listing_id]["rating"] = round(avg_rating, 1)
        self.models[listing_id]["rating_count"] = len(reviews)

        return review

    def get_reviews(self, listing_id: str) -> List[Dict]:
        """获取评论"""
        return self.reviews.get(listing_id, [])

    def update_listing(self, listing_id: str, updates: Dict) -> bool:
        """更新模型信息"""
        if listing_id not in self.models:
            return False

        self.models[listing_id].update(updates)
        self.models[listing_id]["updated_at"] = datetime.now().isoformat()
        return True

    def delete_listing(self, listing_id: str) -> bool:
        """删除模型"""
        if listing_id in self.models:
            self.models[listing_id]["status"] = ModelStatus.DEPRECATED
            return True
        return False


# 全局实例
_marketplace: Optional[ModelMarketplace] = None


def get_marketplace() -> ModelMarketplace:
    """获取模型市场"""
    global _marketplace
    if _marketplace is None:
        _marketplace = ModelMarketplace()
    return _marketplace
