"""授权判定引擎：按调用方身份与角色匹配规则，多规则命中时有稳定优先级。

判定规则（确定性，不随机、不静默放行）：
1. 收集所有命中 (caller, route) 的规则；没有命中 -> 拒绝（no_matching_rule）。
2. 命中的规则里取 priority 数值最小的一批为最高优先级。
3. 最高优先级的一批规则 effect 一致 -> 以该 effect 为最终结论。
4. 最高优先级的一批规则 effect 不一致 -> 判定为冲突，明确拒绝（conflict）。
"""

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

    caller 与 role 必须且只能设置一个；priority 数值越小优先级越高。
    """

    name: str
    effect: Effect
    route: str
    caller: str | None = None
    role: str | None = None
    priority: int = 100

    def __post_init__(self) -> None:
        if (self.caller is None) == (self.role is None):
            raise ValueError(
                f"rule {self.name!r}: exactly one of caller/role must be set"
            )
        if not self.route:
            raise ValueError(f"rule {self.name!r}: route must be non-empty")

    def matches(self, caller: Caller, route: str) -> bool:
        if self.route != route:
            return False
        if self.caller is not None:
            return self.caller == caller.name
        return self.role in caller.roles


@dataclass(frozen=True)
class AuthzDecision:
    """授权判定结论。"""

    allowed: bool
    reason: str
    matched_rules: tuple[str, ...] = ()


class Authorizer:
    """授权判定器。"""

    def __init__(self, rules: list[AuthzRule]) -> None:
        self._rules = list(rules)

    def decide(self, caller: Caller, route: str) -> AuthzDecision:
        """对 caller + 路由做判定，返回确定性的结论。"""
        matched = [r for r in self._rules if r.matches(caller, route)]
        if not matched:
            return AuthzDecision(allowed=False, reason="no_matching_rule")

        best_priority = min(r.priority for r in matched)
        best = sorted(
            (r for r in matched if r.priority == best_priority),
            key=lambda r: r.name,
        )
        names = tuple(r.name for r in best)
        effects = {r.effect for r in best}
        if len(effects) > 1:
            return AuthzDecision(
                allowed=False, reason="conflict", matched_rules=names
            )
        effect = best[0].effect
        return AuthzDecision(
            allowed=effect is Effect.ALLOW,
            reason=f"rule:{best[0].name}",
            matched_rules=names,
        )
