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

    async def find_by_name_and_zone(
        self, model_id: int, provider: str, name: str, zone: str, cloud_account: str,
    ) -> CmdbResource | None:
        """按 name+zone+账号查（GCP disk users URL 解析出的实例名+zone 匹配 compute）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider == provider,
                CmdbResource.name == name,
                CmdbResource.zone == zone,
                CmdbResource.cloud_account == cloud_account,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def list_by_ids(self, resource_ids: list[int]) -> list[CmdbResource]:
        """按 ID 集合批量查资源（排除软删；拓扑子图节点装载用）。"""
        if not resource_ids:
            return []
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.id.in_(resource_ids),
                CmdbResource.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def list_alive_by_model(self, model_id: int) -> list[CmdbResource]:
        """某模型全部未软删资源（CSI 桥接反向孤儿认领遍历用，量级小）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_by_provider_id(
        self,
        model_id: int,
        provider: str,
        provider_id: str,
        cloud_account: str,
        include_deleted: bool = False,
    ) -> CmdbResource | None:
        """根据云厂商 ID 查询资源（用于去重/幂等）。

        Args:
            include_deleted: 是否包含软删除记录（upsert 复活路径需要，
                否则同名重建会撞 provider_id 唯一约束）。
        """
        stmt = select(CmdbResource).where(
            CmdbResource.model_id == model_id,
            CmdbResource.provider == provider,
            CmdbResource.provider_id == provider_id,
            CmdbResource.cloud_account == cloud_account,
        )
        if not include_deleted:
            stmt = stmt.where(CmdbResource.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_provider_id(
        self,
        model_id: int,
        provider_id: str,
        cloud_account: str,
    ) -> CmdbResource | None:
        """按 (model_id, provider_id, cloud_account) 查找，不过滤 provider。

        用于 k8s_cluster 这类 provider 本身是待解析结果的场景
        （集群实例是厂商的唯一事实源，不能拿厂商反查）。
        """
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider_id == provider_id,
                CmdbResource.cloud_account == cloud_account,
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

    async def find_by_provider_id_any_account(
        self, model_id: int, provider: str, provider_id: str,
    ) -> CmdbResource | None:
        """跨账号按 provider_id 查（K8s 节点→云主机桥接：节点侧不知云账号）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider == provider,
                CmdbResource.provider_id == provider_id,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def find_by_provider_id_any_provider(
        self, model_id: int, provider_id: str, cloud_account: str,
    ) -> CmdbResource | None:
        """不按 provider 过滤的 provider_id 查找（K8s 层级边建边用）。

        子资源继承集群托管厂商 provider（ACK=aliyun / GKE=gcp / 自建=k8s），
        硬编码 provider 会漏匹配；provider_id 带集群前缀 + cloud_account
        在同模型内已唯一。
        """
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider_id == provider_id,
                CmdbResource.cloud_account == cloud_account,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def find_by_name_any_account(
        self, model_id: int, provider: str, name: str,
    ) -> CmdbResource | None:
        """跨账号按 name 查（GKE providerID 解析出的是实例名而非数字 ID）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider == provider,
                CmdbResource.name == name,
                CmdbResource.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def list_by_field_value(
        self, model_id: int, provider: str, field_code: str, value: str,
    ) -> list[CmdbResource]:
        """按 fields JSONB 字段值查（internal_ip/private_ip 等桥接匹配）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.provider == provider,
                CmdbResource.deleted_at.is_(None),
                CmdbResource.fields[field_code].as_string() == value,
            )
        )
        return list(result.scalars().all())

    async def find_by_field_text(
        self, model_id: int, field_code: str, value: str,
    ) -> CmdbResource | None:
        """按 fields JSONB 键的文本值匹配未软删资源（LB 桥接按 address/dns_name）。"""
        result = await self._session.execute(
            select(CmdbResource).where(
                CmdbResource.model_id == model_id,
                CmdbResource.fields[field_code].astext == value,
                CmdbResource.deleted_at.is_(None),
            )
        )
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
