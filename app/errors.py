"""网关对外统一错误模型：所有拒绝/故障都归一化为可区分的错误码。"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """对外可区分的错误码。"""

    IDENTITY_MISSING = "identity_missing"        # 缺少调用方身份头
    IDENTITY_INVALID = "identity_invalid"        # 身份头格式非法
    AUTHZ_NO_RULE = "authz_no_rule"              # 无任何规则命中，默认拒绝
    AUTHZ_CONFLICT = "authz_conflict"            # 同优先级规则冲突，拒绝
    AUTHZ_DENIED = "authz_denied"                # 命中显式拒绝规则
    RATE_LIMITED = "rate_limited"                # 超过速率额度
    CONCURRENCY_LIMITED = "concurrency_limited"  # 超过并发额度
    UPSTREAM_TIMEOUT = "upstream_timeout"        # 上游超时
    UPSTREAM_ERROR = "upstream_error"            # 上游业务错误（4xx/5xx 透传归一化）
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"  # 上游内部异常/连接失败
    CIRCUIT_OPEN = "circuit_open"                # 熔断中，快速失败


# 错误码 -> HTTP 状态码，集中定义保证稳定可复现
STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.IDENTITY_MISSING: 401,
    ErrorCode.IDENTITY_INVALID: 401,
    ErrorCode.AUTHZ_NO_RULE: 403,
    ErrorCode.AUTHZ_CONFLICT: 403,
    ErrorCode.AUTHZ_DENIED: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.CONCURRENCY_LIMITED: 429,
    ErrorCode.UPSTREAM_TIMEOUT: 504,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.UPSTREAM_UNAVAILABLE: 502,
    ErrorCode.CIRCUIT_OPEN: 503,
}
