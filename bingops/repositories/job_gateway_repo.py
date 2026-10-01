"""中转网关数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.job_gateway import JobGateway


class JobGatewayRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, gateway: JobGateway) -> JobGateway:
        self.session.add(gateway)
        await self.session.flush()
        return gateway

    async def update(self, gateway: JobGateway) -> JobGateway:
        await self.session.flush()
        return gateway

    async def delete(self, gateway: JobGateway) -> None:
        await self.session.delete(gateway)
        await self.session.flush()

    async def get_by_id(self, gateway_id: int) -> JobGateway | None:
        result = await self.session.execute(
            select(JobGateway).where(JobGateway.id == gateway_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> JobGateway | None:
        result = await self.session.execute(
            select(JobGateway).where(JobGateway.name == name)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[JobGateway], int]:
        query = select(JobGateway)
        if keyword:
            query = query.where(
                JobGateway.name.ilike(f"%{keyword}%")
                | JobGateway.host.ilike(f"%{keyword}%")
            )
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0
        query = (
            query.order_by(JobGateway.name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def list_active(self) -> list[JobGateway]:
        """全部启用中的网关：选路时在内存里按 vpc_id 比对（数量级很小）。"""
        result = await self.session.execute(
            select(JobGateway)
            .where(JobGateway.is_active.is_(True))
            .order_by(JobGateway.name)
        )
        return list(result.scalars().all())
