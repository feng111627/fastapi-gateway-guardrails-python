"""网关配置装配：授权规则、限流策略、熔断策略与本地调用方注册表。"""

from __future__ import annotations

from dataclasses import dataclass

from .authz import AuthzRule, Effect
from .circuitbreaker import CircuitBreakerPolicy
from .identity import Caller
from .ratelimit import RateLimitPolicy

#: 对外代理的路由
PROXY_ROUTE = "/api/data"


@dataclass(frozen=True)
class GatewayConfig:
    callers: dict[str, Caller]
    authz_rules: list[AuthzRule]
    rate_limit: RateLimitPolicy
    circuit_breaker: CircuitBreakerPolicy


def default_config() -> GatewayConfig:
    """构造本地演示用的默认配置。

    调用方（api key -> 身份/角色）：
    - key-alice -> alice，角色 reader + writer
    - key-bob   -> bob，角色 reader
    - key-carol -> carol，无角色（用于演示无命中规则被拒）

    授权规则（priority 数值越小优先级越高）：
    - 10: 拒绝 bob 访问（身份级显式拒绝，优先于角色放行）
    - 50: 放行 reader 角色
    """
    callers = {
        "key-alice": Caller("alice", frozenset({"reader", "writer"})),
        "key-bob": Caller("bob", frozenset({"reader"})),
        "key-carol": Caller("carol", frozenset()),
    }
    authz_rules = [
        AuthzRule(
            name="deny-bob",
            effect=Effect.DENY,
            route=PROXY_ROUTE,
            caller="bob",
            priority=10,
        ),
        AuthzRule(
            name="allow-readers",
            effect=Effect.ALLOW,
            route=PROXY_ROUTE,
            role="reader",
            priority=50,
        ),
    ]
    return GatewayConfig(
        callers=callers,
        authz_rules=authz_rules,
        rate_limit=RateLimitPolicy(
            max_requests=5, window_seconds=10.0, max_concurrency=3
        ),
        circuit_breaker=CircuitBreakerPolicy(
            failure_threshold=3, recovery_seconds=1.0, half_open_max_probes=1
        ),
    )
