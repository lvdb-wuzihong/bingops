"""凭据目录数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bingops.models.credential import Credential


class CredentialRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, credential: Credential) -> Credential:
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def update(self, credential: Credential) -> Credential:
        await self.session.flush()
        return credential

    async def delete(self, credential: Credential) -> None:
        await self.session.delete(credential)
        await self.session.flush()

    async def get_by_id(self, credential_id: int) -> Credential | None:
        result = await self.session.execute(
            select(Credential).where(Credential.id == credential_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Credential | None:
        result = await self.session.execute(
            select(Credential).where(Credential.name == name)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        kind: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Credential], int]:
        query = select(Credential)
        if kind:
            query = query.where(Credential.kind == kind)
        if keyword:
            query = query.where(
                or_(
                    Credential.name.ilike(f"%{keyword}%"),
                    Credential.login_user.ilike(f"%{keyword}%"),
                    Credential.remark.ilike(f"%{keyword}%"),
                )
            )

        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0

        query = query.order_by(Credential.kind, Credential.name).offset(
            (page - 1) * page_size
        ).limit(page_size)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def resolve(
        self, kind: str, cloud_account: str | None, region: str | None,
    ) -> list[Credential]:
        """按适用范围匹配启用中的凭据（NULL = 通配）。

        返回列表交给调用方判歧义：唯一命中才自动采用，多命中必须问人，
        不能靠排序猜——猜错的后果是用错账号连上生产机。
        """
        query = select(Credential).where(
            Credential.kind == kind,
            Credential.is_active.is_(True),
            or_(Credential.cloud_account.is_(None), Credential.cloud_account == cloud_account),
            or_(Credential.region.is_(None), Credential.region == region),
        )
        result = await self.session.execute(query.order_by(Credential.name))
        return list(result.scalars().all())

    async def get_default(self, kind: str) -> Credential | None:
        result = await self.session.execute(
            select(Credential).where(
                Credential.kind == kind,
                Credential.is_active.is_(True),
                Credential.is_default.is_(True),
            )
        )
        return result.scalars().first()

    async def clear_default(self, kind: str, except_id: int | None = None) -> None:
        """同 kind 只允许一个默认条目（设新默认时自动降级旧默认）。"""
        query = update(Credential).where(
            Credential.kind == kind, Credential.is_default.is_(True)
        )
        if except_id is not None:
            query = query.where(Credential.id != except_id)
        await self.session.execute(query.values(is_default=False))
