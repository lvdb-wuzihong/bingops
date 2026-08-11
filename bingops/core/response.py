"""统一响应构建器。"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def success_response(
    data: Any = None,
    message: str = "success",
    http_status: int = 200,
) -> JSONResponse:
    """构建成功响应。"""
    return JSONResponse(
        status_code=http_status,
        content={
            "code": 0,
            "message": message,
            "data": data,
            "request_id": "",
        },
    )


def paginated_response(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
) -> JSONResponse:
    """构建分页响应。"""
    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
    return success_response(
        data={
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }
    )
