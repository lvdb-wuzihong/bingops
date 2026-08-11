"""初始化种子数据脚本。

用途：
- 插入预置角色和权限（如果 SQL schema.sql 已插入可跳过）
- 创建默认 admin 用户（密码需通过环境变量指定）

执行方式：
    BINGOPS_ADMIN_PASSWORD=your_password python -m bingops.scripts.init_data
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env 文件到 os.environ
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from bingops.core.config import settings
from bingops.core.security import hash_password
from bingops.models.role import Permission, Role
from bingops.models.user import User
from bingops.models.user_role import RolePermission, UserRole

logger = logging.getLogger("bingops.scripts.init_data")

# ── 权限清单 ──────────────────────────────────────────────────────────────────

PERMISSIONS: list[tuple[str, str]] = [
    ("host:list", "查看主机列表"),
    ("host:get", "查看主机详情"),
    ("host:create", "创建主机"),
    ("host:update", "更新主机"),
    ("host:delete", "删除主机"),
    ("host_group:list", "查看主机组列表"),
    ("host_group:create", "创建主机组"),
    ("host_group:update", "更新主机组"),
    ("host_group:delete", "删除主机组"),
    ("deploy:list", "查看部署列表"),
    ("deploy:get", "查看部署详情"),
    ("deploy:execute", "执行部署"),
    ("deploy:cancel", "取消部署"),
    ("playbook:list", "查看 Playbook 列表"),
    ("playbook:create", "创建 Playbook"),
    ("playbook:update", "更新 Playbook"),
    ("playbook:delete", "删除 Playbook"),
    ("credential:list", "查看凭据列表"),
    ("credential:create", "创建凭据"),
    ("credential:update", "更新凭据"),
    ("credential:delete", "删除凭据"),
    ("task:list", "查看任务列表"),
    ("task:get", "查看任务详情"),
    ("task:create", "创建任务"),
    ("task:cancel", "取消任务"),
    ("user:list", "查看用户列表"),
    ("user:create", "创建用户"),
    ("user:update", "更新用户"),
    ("user:delete", "删除用户"),
    ("user:assign_role", "分配角色"),
    ("role:list", "查看角色列表"),
    ("role:create", "创建角色"),
    ("role:update", "更新角色"),
    ("role:delete", "删除角色"),
    ("audit:list", "查看审计日志"),
    ("audit:get", "查看审计详情"),
    ("tag:list", "查看标签列表"),
    ("tag:create", "创建标签"),
    ("tag:update", "更新标签"),
    ("tag:delete", "删除标签"),
    ("ticket:list", "查看工单"),
    ("ticket:create", "创建工单"),
    ("ticket:update", "更新工单"),
    ("ticket:assign", "指派工单"),
    ("ticket:delete", "删除工单"),
]

ROLES: list[tuple[str, str, str, bool]] = [
    ("admin", "管理员", "超级管理员，拥有所有权限", True),
    ("operator", "运维操作员", "可执行部署、管理主机等操作", True),
    ("viewer", "只读查看", "只能查看资源，不能修改", True),
    ("auditor", "审计员", "可查看全部数据和审计日志", True),
]


async def init_data() -> None:
    """初始化种子数据。"""
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # 1. 插入权限
        for code, name in PERMISSIONS:
            result = await session.execute(select(Permission).where(Permission.code == code))
            if result.scalar_one_or_none() is None:
                session.add(Permission(code=code, name=name))
        await session.flush()
        logger.info("Permissions initialized: %d entries", len(PERMISSIONS))

        # 2. 插入角色
        for code, name, desc, is_system in ROLES:
            result = await session.execute(select(Role).where(Role.code == code))
            if result.scalar_one_or_none() is None:
                session.add(Role(code=code, name=name, description=desc, is_system=is_system))
        await session.flush()
        logger.info("Roles initialized: %d entries", len(ROLES))

        # 3-5. 角色权限分配（幂等：如已存在则跳过）
        from sqlalchemy import func
        rp_count_result = await session.execute(select(func.count()).select_from(RolePermission))
        rp_count = rp_count_result.scalar() or 0

        if rp_count > 0:
            logger.info("Role permissions already exist (%d entries), skipping", rp_count)
        else:
            all_perms_result = await session.execute(select(Permission))
            all_perms = list(all_perms_result.scalars().all())

            for code, filter_fn in [
                ("admin", lambda _: True),
                ("operator", lambda c: not c.startswith("user:") and not c.startswith("role:")),
                ("viewer", lambda c: c.endswith(":list") or c.endswith(":get")),
                ("auditor", lambda c: c.endswith(":list") or c.endswith(":get") or c.startswith("audit:")),
            ]:
                role_result = await session.execute(select(Role).where(Role.code == code))
                role = role_result.scalar_one()
                for perm in all_perms:
                    if filter_fn(perm.code):
                        session.add(RolePermission(role_id=role.id, permission_id=perm.id))

            await session.flush()
            logger.info("Role permissions assigned")

        # 6. 创建默认 admin 用户
        admin_role_result = await session.execute(select(Role).where(Role.code == "admin"))
        admin_role = admin_role_result.scalar_one()
        admin_password = os.environ.get("BINGOPS_ADMIN_PASSWORD", "admin123456")
        result = await session.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none() is None:
            admin_user = User(
                username="admin",
                email="admin@bingops.local",
                password_hash=hash_password(admin_password),
                display_name="Administrator",
                is_superuser=True,
            )
            session.add(admin_user)
            await session.flush()
            session.add(UserRole(user_id=admin_user.id, role_id=admin_role.id))
            await session.flush()
            logger.info("Default admin user created (username: admin)")
        else:
            logger.info("Admin user already exists, skipping")

        await session.commit()
        logger.info("Seed data initialization completed")

    await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_data())
