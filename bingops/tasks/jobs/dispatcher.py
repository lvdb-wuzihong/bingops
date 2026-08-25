"""任务系统 Kafka 分发器。

持有共享 KafkaClient 实例（startup 注入），供 job_service 生产 dispatch 消息、
event_consumer 触发自动回滚时使用，避免 startup ↔ service/consumer 循环导入。
"""

from __future__ import annotations

from bingops.kafka.client import KafkaClient
from bingops.schemas.jobs import JOB_DISPATCH_TOPIC, JobDispatchMessage

_kafka_client: KafkaClient | None = None


def set_kafka_client(client: KafkaClient) -> None:
    """startup 启动 Kafka 客户端后注入。"""
    global _kafka_client
    _kafka_client = client


def get_kafka_client() -> KafkaClient:
    """获取 Kafka 客户端（未启动时抛 RuntimeError）。"""
    if _kafka_client is None:
        raise RuntimeError("Kafka client not started (BINGOPS_KAFKA_ENABLED=false?)")
    return _kafka_client


async def send_dispatch(message: JobDispatchMessage) -> None:
    """下发任务/回滚消息到 job-dispatch topic。"""
    await get_kafka_client().send(JOB_DISPATCH_TOPIC, message)
