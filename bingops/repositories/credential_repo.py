"""凭据目录数据访问层。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
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
                    Credential.vault_path.ilike(f"%{keyword}%"),
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

    # v36 删除 resolve() / get_default() / clear_default()：它们服务于
    # “按机器适用范围自动匹配凭据 + 同 kind 默认项”的解析链，而该链已在
    # v34 被“执行时从目录人选”取代——没有调用方的查询方法就是死代码。
