"""CMDB Kafka 消费者启动器。

负责在应用启动时初始化 Kafka Consumer，注册消息处理器，启动消费循环。
在应用关闭时优雅停止消费者。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bingops.core.config import settings
from bingops.kafka.client import KafkaClient
from bingops.schemas.cmdb.kafka_messages import CloudResourceMessage, K8sResourceMessage
from bingops.tasks.cmdb.cloud_consumer import create_cloud_handler
from bingops.tasks.cmdb.k8s_consumer import create_k8s_handler

logger = logging.getLogger(f"bingops.{__name__}")

# 全局 Kafka 客户端实例
_kafka_client: KafkaClient | None = None


async def start_cmdb_kafka_consumer(session_factory: async_sessionmaker[AsyncSession]) -> KafkaClient:
    """启动 CMDB Kafka 消费者。

    注册 K8s 事件和云资源同步的 Topic Handler，启动后台消费循环。

    Returns:
        KafkaClient 实例，用于在 shutdown 时停止消费者。
    """
    global _kafka_client

    client = KafkaClient()

    # 注册 Handler
    k8s_handler = create_k8s_handler(session_factory)
    cloud_handler = create_cloud_handler(session_factory)

    client.register_handler(
        settings.kafka_k8s_topic_pattern,
        K8sResourceMessage,
        k8s_handler,
    )
    client.register_handler(
        settings.kafka_cloud_topic_pattern,
        CloudResourceMessage,
        cloud_handler,
    )

    # 订阅 Topic（使用 pattern 订阅）
    # aiokafka 支持 pattern 订阅，但需要实际的 topic 名称
    # 这里先使用通配 topic，实际部署时根据集群和云厂商配置具体 topic
    topics = _resolve_topics()

    if not topics:
        logger.warning("No Kafka topics configured, skipping consumer startup")
        _kafka_client = client
        return client

    await client.start_consumer(topics)
    client.start_background()

    _kafka_client = client
    logger.info("CMDB Kafka consumer started", extra={"topics": topics})
    return client


async def stop_cmdb_kafka_consumer() -> None:
    """停止 CMDB Kafka 消费者。"""
    global _kafka_client
    if _kafka_client is not None:
        await _kafka_client.stop()
        _kafka_client = None
        logger.info("CMDB Kafka consumer stopped")


def _resolve_topics() -> list[str]:
    """解析需要订阅的 Kafka Topics。

    可以通过环境变量配置具体的 topic 列表，
    或者根据 pattern 动态生成。

    示例环境变量：
        BINGOPS_KAFKA_K8S_TOPICS=k8s-events-ack-cn-shanghai,k8s-events-gke-asia-east1
        BINGOPS_KAFKA_CLOUD_TOPICS=cloud-sync-aliyun,cloud-sync-gcp
    """
    topics: list[str] = []

    # 从额外配置读取具体 topic 列表（如果有的话）
    k8s_topics = getattr(settings, "kafka_k8s_topics", "")
    cloud_topics = getattr(settings, "kafka_cloud_topics", "")

    if k8s_topics:
        topics.extend(t.strip() for t in k8s_topics.split(",") if t.strip())
    if cloud_topics:
        topics.extend(t.strip() for t in cloud_topics.split(",") if t.strip())

    return topics
