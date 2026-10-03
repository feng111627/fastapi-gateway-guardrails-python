"""统一对外错误模型：所有拒绝/故障都归一化为可区分的错误码。"""

from __future__ import annotations

import enum


class ErrorCode(str, enum.Enum):
    """对外可区分的错误码。"""

    # 身份与授权
    IDENTITY_MISSING = "identity_missing"
    IDENTITY_UNKNOWN = "identity_unknown"
    AUTHZ_DENIED = "authz_denied"
    AUTHZ_CONFLICT = "authz_conflict"
    # 流量保护
    RATE_LIMITED = "rate_limited"
    CONCURRENCY_LIMITED = "concurrency_limited"
    # 上游与熔断
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_BUSINESS_ERROR = "upstream_business_error"
    UPSTREAM_INTERNAL_ERROR = "upstream_internal_error"
    CIRCUIT_OPEN = "circuit_open"


class GatewayError(Exception):
    """网关统一异常，携带对外错误码与 HTTP 状态码。"""

    def __init__(self, code: ErrorCode, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
