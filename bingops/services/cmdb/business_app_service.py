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

    # 业务域归属校验 + 依赖声明中 internal app_code 存在性校验
    await _validate_business_id(session, payload.business_id)
    await _validate_dependencies_app_codes(session, payload.dependencies)

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
        business_id=payload.business_id,
        dependencies=payload.dependencies,
    )
    app = await repo.create(app)
    backfilled = await _backfill_app_links_on_create(session, app)
    await session.commit()

    logger.info(
        "CMDB business app created",
        extra={"app_code": payload.app_code, "backfilled_resource_links": backfilled},
    )
    return app


async def _backfill_app_links_on_create(session: AsyncSession, app: CmdbBusinessApp) -> int:
    """创建应用后回填存量归集：资源事件先于应用创建到达时，标签已写但无应用可挂。

    扫描已带 app/k8s:app=<app_code> 标签且为服务级 CI 的未删资源，补建 tag 关联。
    """
    from bingops.models.cmdb.model import CmdbModel
    from bingops.models.cmdb.resource import CmdbResource
    from bingops.models.cmdb.tag import CmdbResourceTag
    from bingops.repositories.cmdb.app_resource_repo import CmdbAppResourceRepo

    rows = await session.execute(
        select(CmdbResource.id)
        .join(CmdbModel, CmdbModel.id == CmdbResource.model_id)
        .join(CmdbResourceTag, CmdbResourceTag.resource_id == CmdbResource.id)
        .where(
            CmdbModel.code.in_(SERVICE_LEVEL_MODEL_CODES),
            CmdbResource.deleted_at.is_(None),
            CmdbResourceTag.tag_key.in_(APP_TAG_KEYS),
            CmdbResourceTag.tag_value == app.app_code,
        )
    )
    resource_ids = {rid for (rid,) in rows.all()}
    if not resource_ids:
        return 0
    added = await CmdbAppResourceRepo(session).add_tag_links(app.id, resource_ids)
    if added:
        logger.info(
            "App tag links backfilled on creation",
            extra={"app_code": app.app_code, "linked_resources": added},
        )
    return added


async def update_app(
    session: AsyncSession, app_id: int, payload: BusinessAppUpdate,
) -> CmdbBusinessApp:
    """更新业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    update_data = payload.model_dump(exclude_unset=True)
    if "business_id" in update_data and update_data["business_id"] is not None:
        await _validate_business_id(session, update_data["business_id"])
    if "dependencies" in update_data and update_data["dependencies"] is not None:
        await _validate_dependencies_app_codes(session, update_data["dependencies"])
    for field, value in update_data.items():
        setattr(app, field, value)

    app = await repo.update(app)
    await session.commit()

    logger.info("CMDB business app updated", extra={"app_id": app_id})
    return app


async def _validate_business_id(session: AsyncSession, business_id: int | None) -> None:
    """业务域归属校验：指定的业务域必须存在。"""
    if business_id is None:
        return
    from bingops.repositories.cmdb.business_app_repo import CmdbBusinessDomainRepo

    if await CmdbBusinessDomainRepo(session).get_by_id(business_id) is None:
        raise NotFoundError("CmdbBusinessDomain", str(business_id))


async def _validate_dependencies_app_codes(
    session: AsyncSession, dependencies: list | None,
) -> None:
    """依赖声明中 internal 的 app_code 必须真实存在（防拼错静默失效）。"""
    from bingops.repositories.cmdb.business_app_repo import CmdbBusinessAppRepo

    repo = CmdbBusinessAppRepo(session)
    for dep in dependencies or []:
        if dep.get("type") == "internal":
            app_code = dep.get("app_code") or ""
            if await repo.get_by_app_code(app_code) is None:
                raise ValidationError(
                    f"dependency app_code '{app_code}' does not exist"
                )


async def delete_app(session: AsyncSession, app_id: int) -> None:
    """删除业务应用。"""
    repo = CmdbBusinessAppRepo(session)
    app = await repo.get_by_id(app_id)
    if app is None:
        raise NotFoundError("CmdbBusinessApp", str(app_id))

    await repo.delete(app)
    await session.commit()
    logger.info("CMDB business app deleted", extra={"app_id": app_id})


# ── 业务域 ───────────────────────────────────────────────────────────────────


async def list_domains(session: AsyncSession) -> list[dict]:
    """业务域列表（含归属应用数）。"""
    from bingops.repositories.cmdb.business_app_repo import CmdbBusinessDomainRepo

    domains = await CmdbBusinessDomainRepo(session).list_domains()
    items = []
    for d in domains:
        items.append({
            "id": d.id,
            "code": d.code,
            "name": d.name,
            "owner": d.owner,
            "description": d.description,
            "app_count": await CmdbBusinessDomainRepo(session).count_apps(d.id),
        })
    return items


async def create_domain(session: AsyncSession, payload) -> object:
    """创建业务域（code 全局唯一）。"""
    from bingops.models.cmdb.business_app import CmdbBusinessDomain
    from bingops.repositories.cmdb.business_app_repo import CmdbBusinessDomainRepo
    from bingops.schemas.cmdb.business_app import BusinessDomainCreate

    assert isinstance(payload, BusinessDomainCreate)
    repo = CmdbBusinessDomainRepo(session)
    if await repo.get_by_code(payload.code) is not None:
        raise ConflictError("CmdbBusinessDomain", f"code '{payload.code}' already exists")
    domain = await repo.create(CmdbBusinessDomain(
        name=payload.name,
        code=payload.code,
        owner=payload.owner,
        description=payload.description,
    ))
    await session.commit()
    logger.info("CMDB business domain created", extra={"code": payload.code})
    return domain


async def update_domain(session: AsyncSession, domain_id: int, payload) -> object:
    """更新业务域。"""
    from bingops.repositories.cmdb.business_app_repo import CmdbBusinessDomainRepo

    repo = CmdbBusinessDomainRepo(session)
    domain = await repo.get_by_id(domain_id)
    if domain is None:
        raise NotFoundError("CmdbBusinessDomain", str(domain_id))
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(domain, field, value)
    await session.commit()
    logger.info("CMDB business domain updated", extra={"domain_id": domain_id})
    return domain


def list_dependents(app: object, all_apps: list) -> list[dict]:
    """被依赖清单：其他应用 dependencies 中 internal 引用了本应用 app_code。"""
    dependents = []
    for other in all_apps:
        if other.id == app.id:
            continue
        for dep in other.dependencies or []:
            if dep.get("type") == "internal" and dep.get("app_code") == app.app_code:
                dependents.append({
                    "id": other.id,
                    "app_code": other.app_code,
                    "name": other.name,
                    "owner": other.owner,
                })
                break
    return dependents


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
