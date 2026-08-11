"""Kafka 异步客户端封装。

基于 aiokafka 封装 Consumer 和 Producer，提供统一的生命周期管理。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import BaseModel

from bingops.core.config import settings

logger = logging.getLogger(f"bingops.{__name__}")

# 消息处理函数签名：接收解析后的 Pydantic 模型，返回 None
MessageHandler = Callable[[Any], Awaitable[None]]


class KafkaClient:
    """Kafka 客户端，管理 Consumer 生命周期和消息分发。"""

    def __init__(self) -> None:
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._handlers: dict[str, tuple[type[BaseModel], MessageHandler]] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start_consumer(
        self, topics: list[str] | None = None, pattern: str | None = None,
    ) -> None:
        """启动 Kafka Consumer。

        两种订阅方式（二选一）：
        - topics：字面 topic 列表，启动时对每个 topic 请求 metadata；
        - pattern：正则订阅，coordinator 按集群 metadata 动态匹配实际 topic，
          新 topic 自动发现，无需重启。注意正则不能走 *topics 传入，
          否则会被当字面 topic 名请求 metadata 导致 UnknownTopicOrPartitionError。
        """
        if bool(topics) == bool(pattern):
            raise ValueError("Provide exactly one of `topics` or `pattern`")

        self._consumer = AIOKafkaConsumer(
            *(topics or []),
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        if pattern:
            self._consumer.subscribe(pattern=pattern)

        await self._consumer.start()
        self._running = True
        logger.info(
            "Kafka consumer started",
            extra={"topics": topics, "pattern": pattern, "group": settings.kafka_consumer_group},
        )

    async def start_producer(self) -> None:
        """启动 Kafka Producer（可选，CMDB 服务一般只消费，但关系重建可能需要发送消息）。"""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self._producer.start()
        logger.info("Kafka producer started")

    def register_handler(
        self,
        topic_pattern: str,
        model_class: type[BaseModel],
        handler: MessageHandler,
    ) -> None:
        """注册 Topic → (Schema模型, 处理函数) 映射。"""
        self._handlers[topic_pattern] = (model_class, handler)
        logger.info("Kafka handler registered", extra={"topic": topic_pattern})

    async def consume_loop(self) -> None:
        """主消费循环，分发消息到对应 handler。"""
        if self._consumer is None:
            raise RuntimeError("Consumer not started")

        async for msg in self._consumer:
            if not self._running:
                break

            topic = msg.topic
            handler_entry = self._resolve_handler(topic)
            if handler_entry is None:
                logger.warning("No handler for topic", extra={"topic": topic})
                continue

            model_class, handler = handler_entry
            try:
                payload = model_class.model_validate(msg.value)
                await handler(payload)
            except Exception:
                logger.exception(
                    "Error processing Kafka message",
                    extra={"topic": topic, "partition": msg.partition, "offset": msg.offset},
                )

    def _resolve_handler(self, topic: str) -> tuple[type[BaseModel], MessageHandler] | None:
        """根据实际 Topic 名匹配注册的 pattern handler。

        支持 pattern 中的 {xxx} 占位符匹配，如：
        - 注册 'k8s-events-{cluster_id}' → 匹配 'k8s-events-ack-cn-shanghai'
        """
        # 精确匹配
        if topic in self._handlers:
            return self._handlers[topic]

        # pattern 匹配
        for pattern, entry in self._handlers.items():
            if _pattern_matches(pattern, topic):
                return entry

        return None

    async def send(self, topic: str, value: dict | BaseModel) -> None:
        """发送消息（需要 Producer 已启动）。"""
        if self._producer is None:
            raise RuntimeError("Producer not started")

        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")

        await self._producer.send_and_wait(topic, value)

    async def stop(self) -> None:
        """停止 Consumer 和 Producer。"""
        self._running = False
        for task in self._tasks:
            task.cancel()

        if self._consumer is not None:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")

        if self._producer is not None:
            await self._producer.stop()
            logger.info("Kafka producer stopped")

    def start_background(self) -> asyncio.Task:
        """在后台启动消费循环。"""
        task = asyncio.create_task(self.consume_loop(), name="kafka-consume-loop")
        self._tasks.append(task)
        return task


def _pattern_matches(pattern: str, topic: str) -> bool:
    """简单模式匹配：'k8s-events-{cluster_id}' 匹配 'k8s-events-xxx'。"""
    # 把 pattern 中的 {xxx} 替换为通配
    parts = pattern.split("{")
    if len(parts) == 1:
        return pattern == topic

    # 检查前缀
    prefix = parts[0]
    if not topic.startswith(prefix):
        return False

    # 对于简单的单占位符 pattern（如 k8s-events-{cluster_id}），只检查前缀
    return True
