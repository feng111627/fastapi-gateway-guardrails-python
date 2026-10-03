"""本地模拟的调用方身份解析：从请求头解析调用方身份与角色。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Caller:
    """调用方身份。"""

    name: str
    roles: frozenset[str] = field(default_factory=frozenset)


# 本地模拟的调用方注册表：api key -> Caller
CALLER_REGISTRY: dict[str, Caller] = {}


def resolve_caller(api_key: str | None) -> Caller:
    """根据 api key 解析调用方身份。

    缺失或未知时抛出 GatewayError，绝不静默放行。
    """
    raise NotImplementedError
