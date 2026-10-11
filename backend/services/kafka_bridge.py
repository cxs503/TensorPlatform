"""
Kafka 桥接服务 — 将训练管线的关键事件实时推送到 Kafka。

用法:
    from services.kafka_bridge import send_training_event, send_model_event
    send_training_event("training.started", {"job_id": "xxx", "model_type": "rf"})
    # 下方 metrics 仅为调用示例,实际应由训练管线传入真实评估指标:
    send_model_event("model.registered", "model_123", {"accuracy": 0.95})

Kafka 不可用时自动降级为本地日志，不中断业务流程。
"""
from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kafka 客户端（延迟初始化，避免启动时阻塞）
# ---------------------------------------------------------------------------
_producer: Optional[Any] = None
_kafka_available: Optional[bool] = None  # None = 尚未检测

KAFKA_BOOTSTRAP_SERVERS = ["kafka:9092"]
TRAINING_TOPIC = "foundation-training-events"
MODEL_TOPIC = "foundation-model-events"


def _get_producer():
    """延迟创建 KafkaProducer，失败时返回 None（不抛异常）。"""
    global _producer, _kafka_available

    if _kafka_available is False:
        return None
    if _producer is not None:
        return _producer

    try:
        # 先快速检测 Kafka 是否可达
        sock = socket.create_connection(("kafka", 9092), timeout=2.0)
        sock.close()

        from kafka import KafkaProducer

        _producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks=1,
            retries=2,
            max_block_ms=5000,
            request_timeout_ms=10000,
            api_version_auto_timeout_ms=5000,
        )
        _kafka_available = True
        logger.info("Kafka producer connected to %s", KAFKA_BOOTSTRAP_SERVERS)
        return _producer
    except Exception as exc:
        _kafka_available = False
        _producer = None
        logger.warning(
            "Kafka unavailable (%s) — events will be logged locally", exc
        )
        return None


def is_kafka_available() -> bool:
    """检查 Kafka 当前是否可用（触发一次连接探测）。"""
    return _get_producer() is not None


def _send(topic: str, key: Optional[str], payload: Dict[str, Any]) -> bool:
    """底层发送方法，带降级日志。返回是否成功发送。"""
    envelope = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        **payload,
    }

    producer = _get_producer()
    if producer is None:
        logger.info("[kafka-fallback] topic=%s key=%s payload=%s", topic, key, json.dumps(envelope, ensure_ascii=False))
        return False

    try:
        future = producer.send(topic, key=key, value=envelope)
        # 同步等待确认（acks=1 很快）
        record_metadata = future.get(timeout=10)
        logger.debug(
            "Kafka event sent: topic=%s partition=%d offset=%d",
            record_metadata.topic,
            record_metadata.partition,
            record_metadata.offset,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Kafka send failed (%s), fallback to log: topic=%s key=%s",
            exc, topic, key,
        )
        return False


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def send_training_event(event_type: str, payload: Dict[str, Any]) -> bool:
    """
    发送训练相关事件到 'foundation-training-events' 主题。

    常用 event_type:
      - training.started
      - training.progress
      - training.completed
      - training.failed
    """
    payload = {**payload, "event_type": event_type}
    return _send(TRAINING_TOPIC, key=event_type, payload=payload)


def send_model_event(event_type: str, model_id: str, metrics: Optional[Dict[str, Any]] = None) -> bool:
    """
    发送模型事件到 'foundation-model-events' 主题。

    event_type 示例:
      - model.registered
      - model.deployed
      - model.retired
      - model.metrics_updated
    """
    payload = {
        "event_type": event_type,
        "model_id": model_id,
    }
    if metrics:
        payload["metrics"] = metrics
    return _send(MODEL_TOPIC, key=model_id, payload=payload)


def get_kafka_status() -> Dict[str, Any]:
    """获取 Kafka 连接状态与主题列表。"""
    available = is_kafka_available()

    status = {
        "available": available,
        "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "topics": [],
        "topic_details": {},
    }

    if available and _producer is not None:
        try:
            # 获取主题列表 (kafka-python 2.x API)
            cluster_meta = _producer._metadata
            topic_names = list(cluster_meta.topics()) if hasattr(cluster_meta, 'topics') else []
            status["topics"] = sorted(topic_names)
            for t in topic_names:
                try:
                    parts = _producer.partitions_for(t)
                    status["topic_details"][t] = {
                        "partitions": sorted(parts) if parts else [],
                    }
                except Exception:
                    if hasattr(cluster_meta, 'partitions_for_topic'):
                        parts = cluster_meta.partitions_for_topic(t)
                        status["topic_details"][t] = {
                            "partitions": sorted(parts) if parts else [],
                        }
        except Exception as exc:
            logger.warning("Failed to fetch topic metadata: %s", exc)
            status["topics_error"] = str(exc)

    return status


def send_test_event(event_type: str = "test", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    发送测试事件，返回发送结果。
    """
    payload = payload or {"msg": "hello from foundation"}
    success = send_training_event(event_type, payload)
    return {
        "sent": success,
        "event_type": event_type,
        "topic": TRAINING_TOPIC,
        "kafka_available": _kafka_available,
        "payload": payload,
    }
