"""
Kafka API — 事件流状态查询、主题管理、手动事件发送
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kafka", tags=["Kafka事件流"])


class TestEventRequest(BaseModel):
    """手动发送测试事件"""
    event_type: str = "test"
    payload: Dict[str, Any] = {"msg": "hello"}


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/status")
def kafka_status():
    """
    检查 Kafka 连接状态，返回可用主题列表与分区详情。

    示例响应:
      {
        "available": true,
        "bootstrap_servers": ["kafka:9092"],
        "topics": ["foundation-training-events", "foundation-model-events"],
        "topic_details": {
          "foundation-training-events": {"partitions": [0]},
          ...
        }
      }
    """
    try:
        from services.kafka_bridge import get_kafka_status

        status = get_kafka_status()
        return status
    except ImportError:
        return {
            "available": False,
            "bootstrap_servers": ["kafka:9092"],
            "topics": [],
            "topic_details": {},
            "error": "kafka_bridge module not available",
        }
    except Exception as exc:
        logger.error("Kafka status check failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Kafka status check failed: {exc}")


@router.get("/topics")
def list_topics():
    """
    列出 Kafka 集群中所有主题（包括系统主题）。

    封装 /status 中的 topics 信息，返回纯列表。
    """
    try:
        from services.kafka_bridge import get_kafka_status

        status = get_kafka_status()
        return {
            "topics": status.get("topics", []),
            "topic_details": status.get("topic_details", {}),
            "kafka_available": status.get("available", False),
        }
    except ImportError:
        return {"topics": [], "topic_details": {}, "kafka_available": False}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/events")
def send_test_event(req: TestEventRequest):
    """
    手动发送一条测试事件到 Kafka（训练事件主题）。

    请求体:
      {
        "event_type": "test",
        "payload": {"msg": "hello", "key": "value"}
      }

    响应:
      {
        "sent": true/false,
        "event_type": "test",
        "topic": "foundation-training-events",
        "kafka_available": true/false
      }
    """
    try:
        from services.kafka_bridge import send_test_event

        result = send_test_event(
            event_type=req.event_type,
            payload=req.payload,
        )
        return result
    except ImportError:
        return {
            "sent": False,
            "event_type": req.event_type,
            "topic": "foundation-training-events",
            "kafka_available": False,
            "error": "kafka_bridge module not available",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
def kafka_health():
    """
    Kafka 健康检查端点 — 轻量级，仅返回可用性。
    """
    try:
        from services.kafka_bridge import is_kafka_available

        available = is_kafka_available()
        return {
            "kafka": "up" if available else "down",
            "available": available,
        }
    except ImportError:
        return {"kafka": "unknown", "available": False}
    except Exception as exc:
        return {"kafka": "error", "available": False, "detail": str(exc)}
