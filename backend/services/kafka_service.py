"""
Kafka数据流服务
用于实时数据处理和事件流
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
    from kafka.admin import NewTopic
    from kafka.errors import KafkaError

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    print("Kafka not available")


class KafkaService:
    """Kafka数据流服务"""

    def __init__(self):
        self.bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self.client_id = os.getenv("KAFKA_CLIENT_ID", "open-foundation")
        self.producer: Optional[KafkaProducer] = None
        self._connect_producer()

    def _connect_producer(self):
        """连接Kafka生产者"""
        if not KAFKA_AVAILABLE:
            return

        try:
            self.producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                client_id=self.client_id,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
        except KafkaError as e:
            print(f"Failed to connect producer: {e}")
            self.producer = None

    def create_topic(
        self, topic_name: str, num_partitions: int = 3, replication_factor: int = 1
    ) -> bool:
        """创建Kafka主题"""
        if not KAFKA_AVAILABLE:
            print(f"Mock: Created topic {topic_name}")
            return True

        try:
            admin = KafkaAdminClient(
                bootstrap_servers=self.bootstrap_servers, client_id=self.client_id
            )
            topic = NewTopic(
                name=topic_name,
                num_partitions=num_partitions,
                replication_factor=replication_factor,
            )
            admin.create_topics([topic])
            admin.close()
            return True
        except KafkaError as e:
            print(f"Topic creation failed: {e}")
            return False

    def publish(
        self, topic: str, message: Dict[str, Any], key: Optional[str] = None
    ) -> bool:
        """
        发布消息到Kafka主题

        Args:
            topic: 主题名称
            message: 消息内容
            key: 消息键

        Returns:
            是否成功
        """
        if not KAFKA_AVAILABLE:
            print(f"Mock: Published to {topic}: {message}")
            return True

        if not self.producer:
            print("Producer not connected")
            return False

        try:
            future = self.producer.send(topic, key=key, value=message)
            future.get(timeout=10)
            return True
        except KafkaError as e:
            print(f"Publish failed: {e}")
            return False

    def publish_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        发布平台事件

        Args:
            event_type: 事件类型 (dataset_uploaded, model_trained, deployment_created等)
            data: 事件数据
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        return self.publish("foundation-events", event, key=event_type)

    def consume(
        self,
        topic: str,
        group_id: str = "foundation-consumer",
        auto_offset_reset: str = "earliest",
    ) -> List[Dict]:
        """
        消费Kafka消息

        Args:
            topic: 主题名称
            group_id: 消费者组ID
            auto_offset_reset: 起始偏移量

        Returns:
            消息列表
        """
        if not KAFKA_AVAILABLE:
            return []

        try:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=group_id,
                auto_offset_reset=auto_offset_reset,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            )

            messages = []
            for message in consumer:
                messages.append(message.value)
                if len(messages) >= 100:  # 限制获取数量
                    break

            consumer.close()
            return messages
        except KafkaError as e:
            print(f"Consume failed: {e}")
            return []

    def list_topics(self) -> List[str]:
        """列出所有主题"""
        if not KAFKA_AVAILABLE:
            return ["foundation-events", "foundation-datasets", "foundation-models"]

        try:
            admin = KafkaAdminClient(
                bootstrap_servers=self.bootstrap_servers, client_id=self.client_id
            )
            topics = admin.list_topics()
            admin.close()
            return topics
        except KafkaError:
            return []


# 预定义主题
TOPICS = {
    "EVENTS": "foundation-events",
    "DATASETS": "foundation-datasets",
    "MODELS": "foundation-models",
    "TRAINING": "foundation-training",
    "INFERENCE": "foundation-inference",
    "METRICS": "foundation-metrics",
    "ALERTS": "foundation-alerts",
}


# 全局实例
_kafka_service: Optional[KafkaService] = None


def get_kafka_service() -> KafkaService:
    """获取Kafka服务实例"""
    global _kafka_service
    if _kafka_service is None:
        _kafka_service = KafkaService()
    return _kafka_service


def publish_event(event_type: str, data: Dict[str, Any]) -> bool:
    """发布平台事件"""
    return get_kafka_service().publish_event(event_type, data)


def publish_dataset_event(
    dataset_id: int, action: str, metadata: Dict[str, Any]
) -> bool:
    """发布数据集事件"""
    return publish_event(
        "dataset_" + action, {"dataset_id": dataset_id, "metadata": metadata}
    )


def publish_model_event(model_id: str, action: str, metadata: Dict[str, Any]) -> bool:
    """发布模型事件"""
    return publish_event(
        "model_" + action, {"model_id": model_id, "metadata": metadata}
    )
