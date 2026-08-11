"""CMDB 标签管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.tag import CmdbResourceTag, CmdbTagDefinition
from bingops.repositories.cmdb.tag_repo import CmdbTagRepo
from bingops.schemas.cmdb.tag import ResourceTagCreate, TagDefinitionCreate, TagDefinitionUpdate

logger = logging.getLogger(f"bingops.{__name__}")


# ── 标签定义 ────────────────────────────────────────────────────────────────────


async def create_tag_definition(session: AsyncSession, payload: TagDefinitionCreate) -> CmdbTagDefinition:
    """创建标签定义。"""
    repo = CmdbTagRepo(session)

    existing = await repo.get_tag_definition_by_key(payload.tag_key)
    if existing is not None:
        raise ConflictError("CmdbTagDefinition", f"tag_key '{payload.tag_key}' already exists")

    # enum 类型必须提供 allowed_values
    if payload.value_type == "enum" and not payload.allowed_values:
        raise ValidationError("enum type tag must have allowed_values")

    allowed = payload.allowed_values  # Pydantic 已经是 list | None
    tag_def = CmdbTagDefinition(
        tag_key=payload.tag_key,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        value_type=payload.value_type,
        allowed_values=allowed,
        editable=payload.editable,
    )
    tag_def = await repo.create_tag_definition(tag_def)
    await session.commit()

    logger.info("CMDB tag definition created", extra={"tag_key": payload.tag_key})
    return tag_def


async def get_tag_definition(session: AsyncSession, tag_def_id: int) -> CmdbTagDefinition:
    """获取标签定义详情。"""
    repo = CmdbTagRepo(session)
    tag_def = await repo.get_tag_definition_by_id(tag_def_id)
    if tag_def is None:
        raise NotFoundError("CmdbTagDefinition", str(tag_def_id))
    return tag_def


async def list_tag_definitions(
    session: AsyncSession,
    *,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CmdbTagDefinition], int]:
    """分页查询标签定义列表。"""
    repo = CmdbTagRepo(session)
    return await repo.list_tag_definitions(category=category, page=page, page_size=page_size)


async def update_tag_definition(
    session: AsyncSession, tag_def_id: int, payload: TagDefinitionUpdate,
) -> CmdbTagDefinition:
    """更新标签定义。"""
    repo = CmdbTagRepo(session)
    tag_def = await repo.get_tag_definition_by_id(tag_def_id)
    if tag_def is None:
        raise NotFoundError("CmdbTagDefinition", str(tag_def_id))

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tag_def, field, value)

    tag_def = await repo.update_tag_definition(tag_def)
    await session.commit()

    logger.info("CMDB tag definition updated", extra={"tag_def_id": tag_def_id})
    return tag_def


async def delete_tag_definition(session: AsyncSession, tag_def_id: int) -> None:
    """删除标签定义。"""
    repo = CmdbTagRepo(session)
    tag_def = await repo.get_tag_definition_by_id(tag_def_id)
    if tag_def is None:
        raise NotFoundError("CmdbTagDefinition", str(tag_def_id))

    # 系统标签不允许删除
    if tag_def.category == "system":
        raise ValidationError("System tags cannot be deleted")

    await repo.delete_tag_definition(tag_def)
    await session.commit()
    logger.info("CMDB tag definition deleted", extra={"tag_def_id": tag_def_id})


# ── 资源标签 ────────────────────────────────────────────────────────────────────


async def add_resource_tag(session: AsyncSession, payload: ResourceTagCreate) -> CmdbResourceTag:
    """为资源打标签。"""
    repo = CmdbTagRepo(session)

    # 校验标签定义是否存在
    tag_def = await repo.get_tag_definition_by_key(payload.tag_key)
    if tag_def is not None:
        # 非 editable 的标签不允许手动打标
        if not tag_def.editable and payload.source == "manual":
            raise ValidationError(f"Tag '{payload.tag_key}' is not editable")

        # enum 类型校验值
        if tag_def.value_type == "enum" and tag_def.allowed_values:
            if payload.tag_value not in tag_def.allowed_values:
                raise ValidationError(
                    f"Invalid value '{payload.tag_value}' for enum tag '{payload.tag_key}'"
                )

    tag = CmdbResourceTag(
        resource_id=payload.resource_id,
        tag_key=payload.tag_key,
        tag_value=payload.tag_value,
        source=payload.source,
    )
    tag = await repo.add_resource_tag(tag)
    await session.commit()

    logger.info(
        "CMDB resource tag added",
        extra={"resource_id": payload.resource_id, "tag_key": payload.tag_key},
    )
    return tag


async def remove_resource_tag(
    session: AsyncSession, resource_id: int, tag_key: str, source: str | None = None,
) -> None:
    """移除资源的标签。"""
    repo = CmdbTagRepo(session)
    count = await repo.remove_resource_tag(resource_id, tag_key, source)
    if count == 0:
        raise NotFoundError("CmdbResourceTag", f"{resource_id}/{tag_key}")
    await session.commit()
    logger.info(
        "CMDB resource tag removed",
        extra={"resource_id": resource_id, "tag_key": tag_key},
    )


async def get_resource_tags(session: AsyncSession, resource_id: int) -> list[CmdbResourceTag]:
    """获取某资源的所有标签。"""
    repo = CmdbTagRepo(session)
    return await repo.get_resource_tags(resource_id)


async def find_resources_by_tag(
    session: AsyncSession, tag_key: str, tag_value: str | None = None,
) -> list[int]:
    """按标签查询资源 ID 列表。"""
    repo = CmdbTagRepo(session)
    return await repo.find_resources_by_tag(tag_key, tag_value)
