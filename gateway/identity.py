"""本地模拟的调用方身份解析：从请求头解析调用方身份与角色。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ErrorCode, GatewayError

#: 携带调用方身份的请求头（本地模拟，值为注册表中的 api key）。
API_KEY_HEADER = "x-api-key"


@dataclass(frozen=True)
class Caller:
    """调用方身份。"""

    name: str
    roles: frozenset[str] = field(default_factory=frozenset)


def resolve_caller(api_key: str | None, registry: dict[str, Caller]) -> Caller:
    """根据 api key 解析调用方身份。

    身份信息缺失或无法识别时明确拒绝，绝不静默放行：
    - 未提供 key -> IDENTITY_MISSING
    - key 不在注册表 -> IDENTITY_UNKNOWN
    """
    if api_key is None or api_key.strip() == "":
        raise GatewayError(
            ErrorCode.IDENTITY_MISSING,
            "missing caller identity header",
            http_status=401,
        )
    caller = registry.get(api_key)
    if caller is None:
        raise GatewayError(
            ErrorCode.IDENTITY_UNKNOWN,
            "unknown caller identity",
            http_status=401,
        )
    return caller
