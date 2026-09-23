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


# ── 应用拓扑（G6 数据源）─────────────────────────────────────────────────

# 应用拓扑中展示的资源层：入口/服务/中间件/存储（host 基础设施层走资源拓扑，防爆）
TOPOLOGY_RESOURCE_LAYERS = ("access", "service", "middleware", "storage")


async def get_app_topology(session: AsyncSession, app_id: int, env: str | None = None) -> dict:
    """以应用为中心的拓扑子图（G6 数据源：nodes + edges 一次返回）。

    节点：本应用 + 依赖/被依赖的应用 + 外部依赖 + 该应用的入口/中间件/
    存储资源（按模型 layer 过滤，host 层不进应用拓扑防爆炸）。
    边：depends_on（声明出向）、depended_by（被依赖）、external_dependency
    （三方）、hosts_resource（归属资源）、shared_resource（其他应用 →
    共享资源，存储级耦合信号）。
    env 参数：按环境标签（env/k8s:env）过滤资源节点——依赖声明是应用级
    的不受 env 影响；共享推断只在过滤后的资源集上计算。
    """
    from bingops.repositories.cmdb.app_resource_repo import CmdbAppResourceRepo
    from bingops.repositories.cmdb.model_repo import CmdbModelRepo

    app = await get_app(session, app_id)
    all_apps, _ = await list_apps(session, page=1, page_size=500)
    apps_by_code = {a.app_code: a for a in all_apps}
    apps_by_id = {a.id: a for a in all_apps}

    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def add_node(node: dict) -> None:
        if node["id"] not in seen:
            seen.add(node["id"])
            nodes.append(node)

    def app_node(a, is_center: bool = False) -> dict:
        return {
            "id": f"app:{a.id}", "type": "app", "name": a.name,
            "app_code": a.app_code, "owner": a.owner,
            "business_id": a.business_id, "is_center": is_center,
        }

    add_node(app_node(app, is_center=True))

    # 出向依赖：internal → 应用节点；external → 外部节点
    for dep in app.dependencies or []:
        if dep.get("type") == "internal":
            target = apps_by_code.get(dep.get("app_code") or "")
            if target is None:
                continue  # 历史数据引用已删应用
            add_node(app_node(target))
            edges.append({
                "source": f"app:{app.id}", "target": f"app:{target.id}",
                "relation": "depends_on", "note": dep.get("note") or "",
            })
        elif dep.get("type") == "external":
            ext_key = dep.get("url") or dep.get("name") or ""
            ext_id = f"external:{ext_key}"
            add_node({
                "id": ext_id, "type": "external",
                "name": dep.get("name") or ext_key, "url": dep.get("url") or "",
            })
            edges.append({
                "source": f"app:{app.id}", "target": ext_id,
                "relation": "external_dependency",
            })

    # 被依赖：其他应用 → 本应用
    for d in list_dependents(app, all_apps):
        target = apps_by_id.get(d["id"])
        if target is None:
            continue
        add_node(app_node(target))
        edges.append({
            "source": f"app:{target.id}", "target": f"app:{app.id}",
            "relation": "depended_by",
        })

    # 归属资源（layer ∈ access/service/middleware/storage 才进应用拓扑；env 可选过滤）
    resources = await list_app_resources(session, app_id, env)
    all_models = await CmdbModelRepo(session).list_models()
    layer_by_code = {m.code: m.layer for m in all_models}
    kept = [
        r for r in resources
        if layer_by_code.get(r["model_code"]) in TOPOLOGY_RESOURCE_LAYERS
    ]
    resource_ids = [r["resource_id"] for r in kept]

    # 共享推断：这些资源中被其他应用归集的 → 共享标记 + 其他应用节点
    shared_links = await CmdbAppResourceRepo(session).list_shared_with_apps(
        resource_ids, app_id,
    )
    shared_resource_ids = {rid for rid, _ in shared_links}
    shared_app_ids = {aid for _, aid in shared_links}

    for r in kept:
        rid = f"resource:{r['resource_id']}"
        add_node({
            "id": rid, "type": "resource", "name": r["name"],
            "model_code": r["model_code"], "layer": layer_by_code.get(r["model_code"]),
            "provider": r["provider"], "env": r.get("env"),
            "shared": r["resource_id"] in shared_resource_ids,
        })
        edges.append({
            "source": f"app:{app.id}", "target": rid,
            "relation": "hosts_resource",
        })

    # 共享边：其他应用 → 共享资源节点（新应用节点入图）
    for rid, other_app_id in shared_links:
        other = apps_by_id.get(other_app_id)
        if other is None:
            continue
        add_node(app_node(other))
        edges.append({
            "source": f"app:{other.id}",
            "target": f"resource:{rid}",
            "relation": "shared_resource",
        })

    return {"center_id": f"app:{app.id}", "nodes": nodes, "edges": edges}


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

    仅服务级 CI 参与；manual 关联不受影响。标签值支持英文逗号分隔多应用
    （如 `app=app-a,app-b`，平台级共享中间件一次打标归集 N 应用）；
    GCP/AWS/K8s 的 label 值格式容不下逗号，多值约定仅用于平台手动标签。
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
    values = {
        part.strip()
        for t in rows.scalars().all() if t.tag_value
        for part in t.tag_value.split(",")
        if part.strip()
    }
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
