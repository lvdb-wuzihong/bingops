"""用户数据访问层。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.user import User
from bingops.models.user_role import UserRole


class UserRepo:
    """用户 Repository，封装所有用户相关数据库操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: str) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_feishu_open_id(self, open_id: str) -> User | None:
        result = await self._session.execute(select(User).where(User.feishu_open_id == open_id))
        return result.scalar_one_or_none()

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
    ) -> tuple[list[User], int]:
        query = select(User)
        count_query = select(User.id)

        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(
                (User.username.ilike(like_pattern))
                | (User.display_name.ilike(like_pattern))
                | (User.email.ilike(like_pattern))
            )
            count_query = count_query.where(
                (User.username.ilike(like_pattern))
                | (User.display_name.ilike(like_pattern))
                | (User.email.ilike(like_pattern))
            )

        # 总数
        from sqlalchemy import func
        total_result = await self._session.execute(
            select(func.count()).select_from(count_query.subquery())
        )
        total = total_result.scalar() or 0

        # 分页
        query = query.order_by(User.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(query)
        users = list(result.scalars().all())

        return users, total

    async def create(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def update(self, user: User) -> User:
        await self._session.flush()
        return user

    async def delete(self, user_id: str) -> None:
        await self._session.execute(delete(User).where(User.id == user_id))

    async def update_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def replace_roles(self, user_id: str, role_ids: list[str]) -> None:
        """全量替换用户角色。"""
        await self._session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in role_ids:
            self._session.add(UserRole(user_id=user_id, role_id=role_id))
        await self._session.flush()
