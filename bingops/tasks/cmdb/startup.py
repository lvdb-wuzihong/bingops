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

    # 订阅 Topic：固定正则订阅，是否处理由 cmdb_sync_tasks 数据表驱动
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

    固定正则订阅 ^(k8s-events-.*|cloud-sync-.*)：aiokafka 周期性刷新 metadata
    自动发现新 topic，新接入集群/云厂商无需改配置、无需重启。
    订阅层不做任何业务过滤，同步与否完全由 cmdb_sync_tasks 数据表驱动
    （未配置任务或任务禁用 → 消息直接跳过）。
    """
    k8s_prefix = settings.kafka_k8s_topic_pattern.split("{")[0]
    cloud_prefix = settings.kafka_cloud_topic_pattern.split("{")[0]
    return [f"^({k8s_prefix}.*|{cloud_prefix}.*)"]
