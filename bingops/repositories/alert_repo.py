"""告警事件闭环层数据访问层。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError
from bingops.models.alert import AlertEvent, AlertRule, MonitoringSource

# 统计分组的列映射（day 维度按 first_seen_at 截断到天）
_GROUP_DIMS = {
    "source": "source",
    "rule_code": "rule_code",
    "group_id": "group_id",
}


class AlertEventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, event: AlertEvent) -> AlertEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def update(self, event: AlertEvent) -> AlertEvent:
        await self.session.flush()
        return event

    async def get_by_id(self, event_id: int) -> AlertEvent | None:
        result = await self.session.execute(
            select(AlertEvent).where(AlertEvent.id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_active_firing(
        self, source: str, rule_code: str,
    ) -> AlertEvent | None:
        result = await self.session.execute(
            select(AlertEvent).where(
                AlertEvent.source == source,
                AlertEvent.rule_code == rule_code,
                AlertEvent.status == "firing",
            )
        )
        return result.scalar_one_or_none()

    async def list_active_firings(self) -> list[AlertEvent]:
        """全部活跃 firing（stale 扫描输入；事件量小，无需分页）。"""
        result = await self.session.execute(
            select(AlertEvent).where(AlertEvent.status == "firing")
        )
        return list(result.scalars().all())

    async def list(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        rule_code: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AlertEvent], int]:
        query = select(AlertEvent)
        if status:
            query = query.where(AlertEvent.status == status)
        if source:
            query = query.where(AlertEvent.source == source)
        if rule_code:
            query = query.where(AlertEvent.rule_code == rule_code)
        if since is not None:
            query = query.where(AlertEvent.first_seen_at >= since)
        if until is not None:
            query = query.where(AlertEvent.first_seen_at < until)

        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        query = query.order_by(AlertEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def stats_grouped(
        self,
        *,
        group_by: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """按维度聚合各状态事件数（firing 计数即当前活跃数）。"""
        if group_by == "day":
            dim = func.to_char(AlertEvent.first_seen_at, "YYYY-MM-DD").label("key")
        else:
            dim = getattr(AlertEvent, _GROUP_DIMS[group_by]).label("key")

        base = select(
            dim,
            func.count().filter(AlertEvent.status == "firing").label("firing_count"),
            func.count().filter(AlertEvent.status == "resolved").label("resolved_count"),
            func.count().filter(AlertEvent.status == "error").label("error_count"),
        ).group_by(dim)
        if since is not None:
            base = base.where(AlertEvent.first_seen_at >= since)
        if until is not None:
            base = base.where(AlertEvent.first_seen_at < until)

        rows = (await self.session.execute(base)).all()
        return [
            {
                "key": row.key,
                "firing_count": row.firing_count or 0,
                "resolved_count": row.resolved_count or 0,
                "error_count": row.error_count or 0,
            }
            for row in rows
        ]

    async def avg_resolve_seconds(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> float | None:
        """平均恢复时长（first_seen_at → resolved_at），仅 resolved 事件。"""
        query = select(
            func.avg(func.extract("epoch", AlertEvent.resolved_at - AlertEvent.first_seen_at))
        ).where(
            AlertEvent.status == "resolved",
            AlertEvent.resolved_at.is_not(None),
        )
        if since is not None:
            query = query.where(AlertEvent.first_seen_at >= since)
        if until is not None:
            query = query.where(AlertEvent.first_seen_at < until)
        result = await self.session.execute(query)
        value = result.scalar()
        return float(value) if value is not None else None


class AlertRuleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, rule: AlertRule) -> AlertRule:
        self.session.add(rule)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"alert rule already exists: {rule.source}/{rule.code}") from exc
        return rule

    async def update(self, rule: AlertRule) -> AlertRule:
        await self.session.flush()
        return rule

    async def delete(self, rule: AlertRule) -> None:
        await self.session.delete(rule)
        await self.session.flush()

    async def get_by_id(self, rule_id: int) -> AlertRule | None:
        result = await self.session.execute(
            select(AlertRule).where(AlertRule.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def get_by_source_code(self, source: str, code: str) -> AlertRule | None:
        result = await self.session.execute(
            select(AlertRule).where(
                AlertRule.source == source,
                AlertRule.code == code,
            )
        )
        return result.scalar_one_or_none()

    async def list_agent_rules(self) -> list[tuple[AlertRule, MonitoringSource]]:
        """启用规则 + 启用数据源联查（分发体输入；未绑定源的规则不分发）。"""
        query = (
            select(AlertRule, MonitoringSource)
            .join(MonitoringSource, AlertRule.source_id == MonitoringSource.id)
            .where(AlertRule.enabled.is_(True), MonitoringSource.enabled.is_(True))
            .order_by(AlertRule.id)
        )
        result = await self.session.execute(query)
        return [(row[0], row[1]) for row in result.all()]


class MonitoringSourceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, src: MonitoringSource) -> MonitoringSource:
        self.session.add(src)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"monitoring source already exists: {src.name}") from exc
        return src

    async def update(self, src: MonitoringSource) -> MonitoringSource:
        await self.session.flush()
        return src

    async def delete(self, src: MonitoringSource) -> None:
        await self.session.delete(src)
        await self.session.flush()

    async def get_by_id(self, source_id: int) -> MonitoringSource | None:
        result = await self.session.execute(
            select(MonitoringSource).where(MonitoringSource.id == source_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[MonitoringSource]:
        result = await self.session.execute(
            select(MonitoringSource).order_by(MonitoringSource.id)
        )
        return list(result.scalars().all())
