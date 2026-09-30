"""
Kafka数据流API
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Kafka服务
from services.kafka_service import TOPICS, get_kafka_service, publish_event

router = APIRouter(prefix="/streaming", tags=["数据流"])


class EventPublishRequest(BaseModel):
    event_type: str
    data: Dict[str, Any]


class TopicCreateRequest(BaseModel):
    topic_name: str
    num_partitions: int = 3
    replication_factor: int = 1


@router.get("/topics")
def list_topics():
    """列出所有Kafka主题"""
    kafka = get_kafka_service()
    topics = kafka.list_topics()
    return {"total": len(topics), "items": topics}


@router.post("/topics")
def create_topic(req: TopicCreateRequest):
    """创建Kafka主题"""
    kafka = get_kafka_service()
    success = kafka.create_topic(
        req.topic_name, req.num_partitions, req.replication_factor
    )
    if success:
        return {"message": f"Topic {req.topic_name} created"}
    raise HTTPException(status_code=500, detail="Failed to create topic")


@router.post("/events")
def publish_event_endpoint(req: EventPublishRequest):
    """发布事件到Kafka"""
    success = publish_event(req.event_type, req.data)
    if success:
        return {"message": "Event published"}
    raise HTTPException(status_code=500, detail="Failed to publish event")


@router.get("/events/{event_type}")
def get_events(event_type: str, limit: int = 10):
    """获取特定类型的事件"""
    kafka = get_kafka_service()
    messages = kafka.consume(f"foundation-{event_type}", auto_offset_reset="latest")
    return {"total": len(messages[:limit]), "items": messages[:limit]}


@router.get("/predefined-topics")
def get_predefined_topics():
    """获取预定义的Kafka主题"""
    return {"topics": TOPICS}


@router.post("/events/dataset")
def publish_dataset_event(dataset_id: int, action: str):
    """发布数据集事件"""
    from services.kafka_service import publish_dataset_event as pd_event

    success = pd_event(dataset_id, action, {})
    if success:
        return {"message": "Dataset event published"}
    raise HTTPException(status_code=500, detail="Failed to publish event")


@router.post("/events/model")
def publish_model_event(model_id: str, action: str):
    """发布模型事件"""
    from services.kafka_service import publish_model_event as pm_event

    success = pm_event(model_id, action, {})
    if success:
        return {"message": "Model event published"}
    raise HTTPException(status_code=500, detail="Failed to publish event")
