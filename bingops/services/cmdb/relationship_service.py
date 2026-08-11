"""CMDB 关系管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError
from bingops.models.cmdb.relationship import CmdbBelongsTo, CmdbRelatesTo
from bingops.repositories.cmdb.relationship_repo import CmdbRelationshipRepo
from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo
from bingops.schemas.cmdb.relationship import BelongsToCreate, RelatesToCreate

logger = logging.getLogger(f"bingops.{__name__}")


async def _ensure_resource_exists(session: AsyncSession, resource_id: int) -> None:
    """校验资源是否存在。"""
    resource_repo = CmdbResourceRepo(session)
    resource = await resource_repo.get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", str(resource_id))


# ── 从属关系 ────────────────────────────────────────────────────────────────────


async def add_belongs_to(session: AsyncSession, payload: BelongsToCreate) -> CmdbBelongsTo:
    """创建从属关系（child → parent）。"""
    if payload.child_id == payload.parent_id:
        raise ConflictError("CmdbBelongsTo", "child_id and parent_id cannot be the same")

    await _ensure_resource_exists(session, payload.child_id)
    await _ensure_resource_exists(session, payload.parent_id)

    repo = CmdbRelationshipRepo(session)
    relation = CmdbBelongsTo(
        child_id=payload.child_id,
        parent_id=payload.parent_id,
        relation_type=payload.relation_type,
        source="manual",
    )
    relation = await repo.create_belongs_to(relation)
    await session.commit()

    logger.info(
        "CMDB belongs_to relation created",
        extra={"child_id": payload.child_id, "parent_id": payload.parent_id, "type": payload.relation_type},
    )
    return relation


async def remove_belongs_to(session: AsyncSession, relation_id: int) -> None:
    """删除从属关系。"""
    repo = CmdbRelationshipRepo(session)
    relation = await repo.delete_belongs_to(relation_id)
    if relation is None:
        raise NotFoundError("CmdbBelongsTo", str(relation_id))
    await session.commit()
    logger.info("CMDB belongs_to relation deleted", extra={"relation_id": relation_id})


async def get_children(
    session: AsyncSession, resource_id: int, relation_type: str | None = None,
) -> list[CmdbBelongsTo]:
    """获取某资源的所有子资源关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_children(resource_id, relation_type)


async def get_parents(
    session: AsyncSession, resource_id: int, relation_type: str | None = None,
) -> list[CmdbBelongsTo]:
    """获取某资源的所有父资源关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_parents(resource_id, relation_type)


# ── 关联关系 ────────────────────────────────────────────────────────────────────


async def add_relates_to(session: AsyncSession, payload: RelatesToCreate) -> CmdbRelatesTo:
    """创建关联关系（source → target）。"""
    if payload.source_id == payload.target_id:
        raise ConflictError("CmdbRelatesTo", "source_id and target_id cannot be the same")

    await _ensure_resource_exists(session, payload.source_id)
    await _ensure_resource_exists(session, payload.target_id)

    repo = CmdbRelationshipRepo(session)
    relation = CmdbRelatesTo(
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation_type=payload.relation_type,
        attributes=payload.attributes,
        source="manual",
    )
    relation = await repo.create_relates_to(relation)
    await session.commit()

    logger.info(
        "CMDB relates_to relation created",
        extra={"source_id": payload.source_id, "target_id": payload.target_id, "type": payload.relation_type},
    )
    return relation


async def remove_relates_to(session: AsyncSession, relation_id: int) -> None:
    """删除关联关系。"""
    repo = CmdbRelationshipRepo(session)
    relation = await repo.delete_relates_to(relation_id)
    if relation is None:
        raise NotFoundError("CmdbRelatesTo", str(relation_id))
    await session.commit()
    logger.info("CMDB relates_to relation deleted", extra={"relation_id": relation_id})


async def get_relations_from(
    session: AsyncSession, resource_id: int, relation_type: str | None = None,
) -> list[CmdbRelatesTo]:
    """获取从某资源出发的所有关联关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_relations_from(resource_id, relation_type)


async def get_relations_to(
    session: AsyncSession, resource_id: int, relation_type: str | None = None,
) -> list[CmdbRelatesTo]:
    """获取指向某资源的所有关联关系。"""
    repo = CmdbRelationshipRepo(session)
    return await repo.get_relations_to(resource_id, relation_type)
