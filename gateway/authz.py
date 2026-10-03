"""授权判定引擎：按调用方身份与角色匹配规则，多规则命中时有稳定优先级。"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .identity import Caller


class Effect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class AuthzRule:
    """一条授权规则。

    priority 数值越小优先级越高；caller 为 None 表示按角色匹配。
    """

    name: str
    effect: Effect
    route: str
    caller: str | None = None
    role: str | None = None
    priority: int = 100


@dataclass(frozen=True)
class AuthzDecision:
    """授权判定结论。"""

    allowed: bool
    reason: str
    matched_rules: tuple[str, ...] = ()


class Authorizer:
    """授权判定器。"""

    def __init__(self, rules: list[AuthzRule]) -> None:
        self._rules = rules

    def decide(self, caller: Caller, route: str) -> AuthzDecision:
        """对 caller板 + 路由做判定，返回确定性的结论。"""
        raise NotImplementedError
