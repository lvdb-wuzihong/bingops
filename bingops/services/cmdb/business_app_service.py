"""CMDB 业务应用管理服务。"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.core.exceptions import ConflictError, NotFoundError, ValidationError
from bingops.models.cmdb.business_app import CmdbBusinessApp
from bingops.repositories.cmdb.business_app_repo import CmdbBusinessAppRepo
from bingops.schemas.cmdb.business_app import BusinessAppCreate, BusinessAppUpdate

logger = logging.getLogger(f"bingops.{__name__}")


async def list_apps(
    session: AsyncSession,
    *,
    team: str | None = None,
    owner: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CmdbBusinessApp], int]:
    """分页查询业务应用列表。"""
    repo = CmdbBusinessAppRepo(session)
    return await repo.list_apps(team=team, owner=owner, keyword=keyword, page=page, page_size=page_size)


async def get_app(session: AsyncSession, app_id: int) -> CmdbBusinessApp:
    """获取业务应用详情。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))
    return app


async def create_app(session: AsyncSession, payload: BusinessAppCreate) -> CmdbBusinessApp:
    """创建业务应用。"""
    repo = CmdbBusinessAppRepo(session)

    existing = await repo.get_by_app_code(payload.app_code)
    if existing is not None:
        raise ConflictError("CmdbBusinessApp", f"app_code '{payload.app_code}' already exists")

    app = CmdbBusinessApp(
        app_code=payload.app_code,
        name=payload.name,
        description=payload.description,
        team=payload.team,
        owner=payload.owner,
        department=payload.department,
        labels=payload.labels,
        repo_url=payload.repo_url,
        pipelines=payload.pipelines,
    )
    app = await repo.create(app)
    await session.commit()

    logger.info("CMDB business app created", extra={"app_code": payload.app_code})
    return app


async def update_app(
    session: AsyncSession, app_id: int, payload: BusinessAppUpdate,
) -> CmdbBusinessApp:
    """更新业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(app, field, value)

    app = await repo.update(app)
    await session.commit()

    logger.info("CMDB business app updated", extra={"app_id": app_id})
    return app


async def delete_app(session: AsyncSession, app_id: int) -> None:
    """删除业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    await repo.delete(app)
    await session.commit()
    logger.info("CMDB business app deleted", extra={"app_id": app_id})


# ── 应用-资源关联物化（附录 B #13）───────────────────────────────

# 应用只绑服务级 CI（workload/中间件/RDS/入口），不绑 Pod/Node/ECS 等基础设施层
SERVICE_LEVEL_MODEL_CODES = {
    "k8s_workload", "k8s_service",
    "aliyun_rds", "aliyun_redis", "aliyun_amqp", "aliyun_clb", "aliyun_nlb",
    "gcp_cloudsql", "gcp_redis",
}

# 应用标签键：云/手动标签用 app，K8s labels 经归一化带 k8s: 前缀
APP_TAG_KEYS = ("app", "k8s:app")
# 环境标签键（应用资源列表的 env 维度，同双键约定）
ENV_TAG_KEYS = ("env", "k8s:env")


async def refresh_app_links_from_tags(session: AsyncSession, resource) -> None:
    """按资源当前标签重算 tag 派生应用关联（不 commit，随消费事务提交）。

    仅服务级 CI 参与；manual 关联不受影响。
    """
    from bingops.models.cmdb.model import CmdbModel
    from bingops.models.cmdb.tag import CmdbResourceTag
    from bingops.repositories.cmdb.app_resource_repo import CmdbAppResourceRepo

    model = await session.get(CmdbModel, resource.model_id)
    if model is None or model.code not in SERVICE_LEVEL_MODEL_CODES:
        return

    rows = await session.execute(
        select(CmdbResourceTag).where(
            CmdbResourceTag.resource_id == resource.id,
            CmdbResourceTag.tag_key.in_(APP_TAG_KEYS),
        )
    )
    values = {t.tag_value for t in rows.scalars().all() if t.tag_value}
    app_ids: set[int] = set()
    if values:
        result = await session.execute(
            select(CmdbBusinessApp).where(CmdbBusinessApp.app_code.in_(values))
        )
        app_ids = {a.id for a in result.scalars().all()}

    await CmdbAppResourceRepo(session).replace_tag_links(resource.id, app_ids)


async def bind_resource(session: AsyncSession, app_id: int, resource_id: int) -> None:
    """手动绑定应用与资源（service-level CI 校验）。"""
    from bingops.models.cmdb.model import CmdbModel
    from bingops.repositories.cmdb.app_resource_repo import CmdbAppResourceRepo
    from bingops.repositories.cmdb.resource_repo import CmdbResourceRepo

    await get_app(session, app_id)
    resource = await CmdbResourceRepo(session).get_by_id(resource_id)
    if resource is None:
        raise NotFoundError("CmdbResource", str(resource_id))
    model = await session.get(CmdbModel, resource.model_id)
    if model is None or model.code not in SERVICE_LEVEL_MODEL_CODES:
        raise ValidationError(
            f"resource model '{model.code if model else '?'}' is not service-level; "
            "apps only bind workload/service/middleware/db/entry CIs"
        )
    repo = CmdbAppResourceRepo(session)
    if await repo.get_link(app_id, resource_id) is None:
        await repo.add_manual(app_id, resource_id)
    await session.commit()


async def unbind_resource(session: AsyncSession, app_id: int, resource_id: int) -> None:
    """解绑应用与资源。"""
    from bingops.repositories.cmdb.app_resource_repo import CmdbAppResourceRepo

    link = await CmdbAppResourceRepo(session).get_link(app_id, resource_id)
    if link is not None:
        await CmdbAppResourceRepo(session).remove_link(link)
    await session.commit()


async def list_app_resources(
    session: AsyncSession, app_id: int, env: str | None = None,
) -> list[dict]:
    """应用下的资源列表（join 资源与模型 code）。

    每项附带 env（取自 env/k8s:env 标签）与 region，供前端按环境分组；
    env 参数非空时服务端过滤。
    """
    from bingops.models.cmdb.app_resource import CmdbAppResource
    from bingops.models.cmdb.model import CmdbModel
    from bingops.models.cmdb.resource import CmdbResource
    from bingops.models.cmdb.tag import CmdbResourceTag

    await get_app(session, app_id)
    rows = await session.execute(
        select(CmdbAppResource, CmdbResource, CmdbModel)
        .join(CmdbResource, CmdbResource.id == CmdbAppResource.resource_id)
        .join(CmdbModel, CmdbModel.id == CmdbResource.model_id)
        .where(CmdbAppResource.app_id == app_id)
    )
    items = rows.all()

    resource_ids = [res.id for _, res, _ in items]
    env_map: dict[int, str] = {}
    if resource_ids:
        tag_rows = await session.execute(
            select(CmdbResourceTag.resource_id, CmdbResourceTag.tag_value).where(
                CmdbResourceTag.resource_id.in_(resource_ids),
                CmdbResourceTag.tag_key.in_(ENV_TAG_KEYS),
            )
        )
        for rid, value in tag_rows.all():
            env_map.setdefault(rid, value)

    result = []
    for link, res, model in items:
        res_env = env_map.get(res.id)
        if env is not None and res_env != env:
            continue
        result.append({
            "resource_id": res.id,
            "name": res.name,
            "provider": res.provider,
            "model_code": model.code,
            "status": res.status,
            "region": res.region,
            "env": res_env,
            "source": link.source,
        })
    return result


async def list_resource_apps(session: AsyncSession, resource_id: int) -> list[dict]:
    """资源归属的应用列表。"""
    from bingops.models.cmdb.app_resource import CmdbAppResource

    rows = await session.execute(
        select(CmdbAppResource, CmdbBusinessApp)
        .join(CmdbBusinessApp, CmdbBusinessApp.id == CmdbAppResource.app_id)
        .where(CmdbAppResource.resource_id == resource_id)
    )
    return [
        {"app_id": app.id, "app_code": app.app_code, "name": app.name, "source": link.source}
        for link, app in rows.all()
    ]
