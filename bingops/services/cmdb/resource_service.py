"""CMDB 资源实例业务服务（v2 动态模型）。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.resource import CmdbResource
from bingops.repositories.cmdb.model_repo import CmdbModelRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.schemas.cmdb.resource import ResourceCreate, ResourceUpdate

logger = logging.getLogger(f"bingops.{__name__}")


async def search_resource_options(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    model_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """选择器轻量搜索（工单关联资源等下拉场景）。

    返回仅含渲染所需字段的字典列表：id/name/model_code/provider/region/status。
    """
    rows = await CmdbResourceRepo(session).search_options(
        keyword=keyword, model_id=model_id, status=status, limit=limit,
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "model_code": code,
            "provider": r.provider,
            "region": r.region,
            "status": r.status,
        }
        for r, code in rows
    ]


async def list_resources(
    session: AsyncSession,
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
    """分页查询资源列表。"""
    repo = CmdbResourceRepo(session)
    return await repo.list_resources(
        model_id=model_id,
        provider=provider,
        status=status,
        cloud_account=cloud_account,
        region=region,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


async def get_resource(session: AsyncSession, resource_id: int) -> CmdbResource:
    """获取资源详情。"""
    repo = CmdbResourceRepo(session)
    resource = await repo.get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", resource_id)
    return resource


async def create_resource(session: AsyncSession, payload: ResourceCreate) -> CmdbResource:
    """创建资源实例。

    通过 (model_id, provider, provider_id, cloud_account) 唯一约束去重，
    如果已存在则抛出 ConflictError。
    """
    # 校验模型存在且启用
    model_repo = CmdbModelRepo(session)
    model = await model_repo.get_model(payload.model_id)
    if not model:
        raise NotFoundError("CmdbModel", payload.model_id)
    if not model.is_enabled:
        raise ValidationError(f"Model '{model.code}' is disabled")

    repo = CmdbResourceRepo(session)

    # 云厂商资源去重
    if payload.provider and payload.provider_id:
        existing = await repo.get_by_provider_id(
            model_id=payload.model_id,
            provider=payload.provider,
            provider_id=payload.provider_id,
            cloud_account=payload.cloud_account or "",
        )
        if existing is not None:
            raise ConflictError(
                "CmdbResource",
                f"resource already exists: {payload.provider}/{model.code}/{payload.provider_id}",
            )

    resource = CmdbResource(
        model_id=payload.model_id,
        provider=payload.provider,
        provider_id=payload.provider_id,
        cloud_account=payload.cloud_account,
        name=payload.name,
        region=payload.region,
        zone=payload.zone,
        status=payload.status,
        fields=payload.fields,
        source="manual",
    )
    resource = await repo.create(resource)
    await session.commit()

    logger.info(
        "CMDB resource created",
        extra={"resource_id": resource.id, "model_id": payload.model_id},
    )
    return resource


async def update_resource(
    session: AsyncSession, resource_id: int, payload: ResourceUpdate,
) -> CmdbResource:
    """更新资源实例。"""
    repo = CmdbResourceRepo(session)
    resource = await repo.get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", resource_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(resource, field, value)

    resource = await repo.update(resource)
    await session.commit()

    logger.info("CMDB resource updated", extra={"resource_id": resource_id})
    return resource


async def delete_resource(session: AsyncSession, resource_id: int) -> None:
    """软删除资源实例。"""
    repo = CmdbResourceRepo(session)
    resource = await repo.get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", resource_id)

    await repo.soft_delete(resource)
    await session.commit()

    logger.info("CMDB resource deleted", extra={"resource_id": resource_id})


async def get_resource_stats(session: AsyncSession) -> dict:
    """获取资源统计（按模型/状态/云厂商分组）。"""
    repo = CmdbResourceRepo(session)
    by_model = await repo.count_by_model()
    by_status = await repo.count_by_status()
    by_provider = await repo.count_by_provider()
    total = await repo.total_count()
    return {
        "total": total,
        "by_model": {str(k): v for k, v in by_model.items()},
        "by_status": by_status,
        "by_provider": by_provider,
    }
