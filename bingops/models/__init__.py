"""ORM 模型注册入口：显式导入全部模型模块，确保 SQLAlchemy registry 完整。

供模型初始化顺序敏感的场景使用（如 MCP 工具包、脚本直接 import），
常规 FastAPI 路由链（api/v1 → dependencies）会自然加载全部模型。
"""

from bingops.models import (
    base,  # noqa: F401
    cmdb,  # noqa: F401
    jobs,  # noqa: F401
    role,  # noqa: F401
    ticket,  # noqa: F401
    user,  # noqa: F401
    user_role,  # noqa: F401
)
