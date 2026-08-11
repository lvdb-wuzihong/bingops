"""CMDB 资源实例数据访问层（v2 动态模型）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.cmdb.resource import CmdbResource


class CmdbResourceRepo:
    """CMDB 资源实例 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, resource_id: int) -> CmdbResource | None:
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.id == resource_id,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_provider_id(
        self,
        model_id: int,
        provider: str,
        provider_id: str,
        cloud_account: str,
    ) -> CmdbResource | None:
        """根据云厂商 ID 查询资源（用于去重/幂等）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider == provider,
                CmdbResource.provider_id == provider_id,
                CmdbResource.cloud_account == cloud_account,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_resources(
        self,
        *,
        model_id: int | None = None,
        provider: str | None = None,
        status: str | None = None,
        cloud_account: str | None = None,
        region: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CmdbResource], int]:
        """分页查询资源实例列表。"""
        query = select(CmdbResource).where(CmdbResource.deleted_at.is_(None))
        count_query = select(CmdbResource.id).where(CmdbResource.deleted_at.is_(None))

        if model_id:
            query = query.where(CmdbResource.model_id == model_id)
            count_query = count_query.where(CmdbResource.model_id == model_id)
        if provider:
            query = query.where(CmdbResource.provider == provider)
            count_query = count_query.where(CmdbResource.provider == provider)
        if status:
            query = query.where(CmdbResource.status == status)
            count_query = count_query.where(CmdbResource.status == status)
        if cloud_account:
            query = query.where(CmdbResource.cloud_account == cloud_account)
            count_query = count_query.where(CmdbResource.cloud_account == cloud_account)
        if region:
            query = query.where(CmdbResource.region == region)
            count_query = count_query.where(CmdbResource.region == region)
        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(CmdbResource.name.ilike(like_pattern))
            count_query = count_query.where(CmdbResource.name.ilike(like_pattern))

        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        query = query.order_by(CmdbResource.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        resources = list(result.scalars().all())

        return resources, total

    async def find_by_name(
        self,
        model_id: int,
        cloud_account: str,
        name: str,
        namespace: str | None = None,
    ) -> CmdbResource | None:
        """按模型 + 集群 + 名称（可选 fields.namespace）定位资源，关系重建用。"""
        query = select(CmdbResource).where(
            CmdbResource.model_id == model_id,
            CmdbResource.cloud_account == cloud_account,
            CmdbResource.name == name,
            CmdbResource.deleted_at.is_(None),
        )
        if namespace is not None:
            query = query.where(CmdbResource.fields["namespace"].astext == namespace)
        result = await self._session.execute(query)
        return result.scalars().first()

    async def create(self, resource: CmdbResource) -> CmdbResource:
        self._session.add(resource)
        await self._session.flush()
        return resource

    async def update(self, resource: CmdbResource) -> CmdbResource:
        await self._session.flush()
        return resource

    async def soft_delete(self, resource: CmdbResource) -> None:
        resource.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def count_by_model(self) -> dict[int, int]:
        """按模型统计实例数量。"""
        result = await self._session.execute(
            select(CmdbResource.model_id, func.count())
            .where(CmdbResource.deleted_at.is_(None))
            .group_by(CmdbResource.model_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def count_by_status(self) -> dict[str, int]:
        """按状态统计。"""
        result = await self._session.execute(
            select(CmdbResource.status, func.count())
            .where(CmdbResource.deleted_at.is_(None))
            .group_by(CmdbResource.status)
        )
        return {row[0]: row[1] for row in result.all()}

    async def count_by_provider(self) -> dict[str, int]:
        """按云厂商统计。"""
        result = await self._session.execute(
            select(CmdbResource.provider, func.count())
            .where(CmdbResource.deleted_at.is_(None), CmdbResource.provider.isnot(None))
            .group_by(CmdbResource.provider)
        )
        return {row[0]: row[1] for row in result.all()}

    async def total_count(self) -> int:
        """总实例数。"""
        result = await self._session.execute(
            select(func.count()).select_from(
                select(CmdbResource.id).where(CmdbResource.deleted_at.is_(None)).subquery()
            )
        )
        return result.scalar() or 0
