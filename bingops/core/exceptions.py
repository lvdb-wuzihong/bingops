"""平台异常类层级定义。

所有业务异常必须继承 BingOpsError，禁止直接使用内置异常。
"""

from __future__ import annotations


class BingOpsError(Exception):
    """平台基础异常，所有业务异常的根类。"""

    def __init__(
        self,
        message: str = "Internal server error",
        code: int = 50001,
        http_status: int = 500,
    ) -> None:
        self.message = message
        self.code = code
        self.http_status = http_status
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
        }


# ── 客户端错误 (4xx) ──────────────────────────────────────────────────────────


class ValidationError(BingOpsError):
    """参数校验异常。"""

    def __init__(self, message: str = "Validation failed", errors: list[dict] | None = None):
        super().__init__(message, code=40001, http_status=422)
        self.errors = errors or []


class AuthenticationError(BingOpsError):
    """认证失败异常。"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code=40101, http_status=401)


class PermissionDeniedError(BingOpsError):
    """权限不足异常。"""

    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, code=40301, http_status=403)


class NotFoundError(BingOpsError):
    """资源不存在异常。"""

    def __init__(self, resource: str, identifier: str):
        message = f"{resource} not found: {identifier}"
        super().__init__(message, code=40401, http_status=404)
        self.resource = resource
        self.identifier = identifier


class ConflictError(BingOpsError):
    """资源冲突异常。"""

    def __init__(self, resource: str, detail: str):
        message = f"{resource} conflict: {detail}"
        super().__init__(message, code=40901, http_status=409)


# ── 服务端错误 (5xx) ──────────────────────────────────────────────────────────


class InternalError(BingOpsError):
    """内部错误异常。"""

    def __init__(self, detail: str = "Unexpected internal error"):
        super().__init__(detail, code=50001, http_status=500)


class ExternalServiceError(BingOpsError):
    """外部服务调用失败。"""

    def __init__(self, service: str, detail: str, http_status: int = 502):
        message = f"External service '{service}' failed: {detail}"
        super().__init__(message, code=50201, http_status=http_status)
        self.service = service
