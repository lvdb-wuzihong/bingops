"""MCP 工具注册入口：import 副作用完成 @mcp.tool 注册。

P0 范围（设计文档 §5 路线图）：A 组（apps/resources）+ C 组（tickets/jobs）+ 写白名单。
"""

import bingops.models  # noqa: F401  先注册完整 ORM registry（MCP 可被绕过 main.py 直接 import）
from bingops.mcp.tools import apps, jobs, resources, tickets, writes  # noqa: F401
