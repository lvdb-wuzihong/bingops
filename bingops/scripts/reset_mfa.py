"""MFA 重置与登录解锁运维脚本。

使用场景：管理员丢失 TOTP 设备（无法登录）或账号因连续失败被锁定。
无管理员登录态时由运维在服务器执行（需能读取 .env 的 DATABASE_URL）。

用法：
    python -m bingops.scripts.reset_mfa <username>            # 仅清空 MFA 绑定
    python -m bingops.scripts.reset_mfa <username> --unlock   # 同时解除登录锁定

退出码：0 = 成功，1 = 用户不存在。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bingops.core.config import settings
from bingops.models.user import User


async def run(username: str, unlock: bool) -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user is None:
                print(f"user not found: {username}", file=sys.stderr)
                return 1

            user.mfa_enabled = False
            user.totp_secret = None
            user.mfa_bound_at = None
            if unlock:
                user.failed_login_count = 0
                user.last_failed_login_at = None
                user.locked_until = None

            await session.commit()
            actions = ["MFA cleared"]
            if unlock:
                actions.append("login lock cleared")
            print(f"reset_mfa: {username} — {', '.join(actions)}")
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a user's MFA binding and/or login lock")
    parser.add_argument("username", help="平台用户名（非飞书 open_id）")
    parser.add_argument(
        "--unlock",
        action="store_true",
        help="同时清除失败计数与锁定截止时间",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.username, args.unlock)))


if __name__ == "__main__":
    main()
