"""授权判定：按调用方身份与角色匹配规则，冲突时明确拒绝。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.errors import ErrorCode
from app.identity import CallerIdentity


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class AuthzRule:
    """授权规则。priority 数值越大优先级越高。"""

    name: str
    effect: Effect
    priority: int
    caller: str | None = None       # None 表示匹配任意调用方
    role: str | None = None         # None 表示不要求角色
    path_prefix: str | None = None  # None 表示匹配任意路径


@dataclass(frozen=True)
class AuthzDecision:
    """授权结论：允许或携带可区分原因的拒绝。"""

    allowed: bool
    code: ErrorCode | None = None
    detail: str = ""
    matched_rules: tuple[str, ...] = ()


def _matches(rule: AuthzRule, identity: CallerIdentity, path: str) -> bool:
    if rule.caller is not None and rule.caller != identity.caller:
        return False
    if rule.role is not None and rule.role not in identity.roles:
        return False
    if rule.path_prefix is not None and not path.startswith(rule.path_prefix):
        return False
    return True


def authorize(identity: CallerIdentity, path: str, rules: list[AuthzRule]) -> AuthzDecision:
    """对身份+路径做授权判定，返回确定性的结论。

    判定规则（保证稳定、可复现）：
    1. 收集所有命中的规则；无命中 -> 默认拒绝（AUTHZ_NO_RULE）。
    2. 取最高 priority 的规则集合。
    3. 若该集合中同时存在 allow 与 deny -> 冲突，拒绝（AUTHZ_CONFLICT）。
    4. 否则按该集合唯一的效果给出结论；deny -> AUTHZ_DENIED。
    """
    matched = [r for r in rules if _matches(r, identity, path)]
    if not matched:
        return AuthzDecision(
            allowed=False,
            code=ErrorCode.AUTHZ_NO_RULE,
            detail="no authorization rule matched; default deny",
        )

    top_priority = max(r.priority for r in matched)
    top = tuple(sorted(r.name for r in matched if r.priority == top_priority))
    effects = {r.effect for r in matched if r.priority == top_priority}

    if len(effects) > 1:
        return AuthzDecision(
            allowed=False,
            code=ErrorCode.AUTHZ_CONFLICT,
            detail=f"conflicting rules at priority {top_priority}: {', '.join(top)}",
            matched_rules=top,
        )

    if effects == {Effect.ALLOW}:
        return AuthzDecision(allowed=True, matched_rules=top)

    return AuthzDecision(
        allowed=False,
        code=ErrorCode.AUTHZ_DENIED,
        detail=f"explicitly denied by rule(s): {', '.join(top)}",
        matched_rules=top,
    )
