"""网关配置装配：授权规则、限流策略、熔断策略与本地调用方注册表。"""

from __future__ import annotations

from dataclasses import dataclass

from .authz import AuthzRule
from .circuitbreaker import CircuitBreakerPolicy
from .identity import Caller
from .ratelimit import RateLimitPolicy


@dataclass(frozen=True)
class GatewayConfig:
    callers: dict[str, Caller]
    authz_rules: list[AuthzRule]
    rate_limit: RateLimitPolicy
    circuit_breaker: CircuitBreakerPolicy


def default_config() -> GatewayConfig:
    """构造本地演示用的默认配置。"""
    raise NotImplementedError
