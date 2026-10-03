"""测试公共装置：构造可复现的网关应用与配置。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway.authz import AuthzRule, Effect
from gateway.circuitbreaker import CircuitBreakerPolicy
from gateway.config import PROXY_ROUTE, GatewayConfig
from gateway.identity import Caller
from gateway.ratelimit import RateLimitPolicy
from gateway.upstream import MockUpstream
from main import create_app


def make_config(
    *,
    authz_rules: list[AuthzRule] | None = None,
    max_requests: int = 100,
    window_seconds: float = 60.0,
    max_concurrency: int = 100,
    failure_threshold: int = 3,
    recovery_seconds: float = 0.1,
    half_open_max_probes: int = 1,
) -> GatewayConfig:
    """构造测试配置，默认额度宽松，各用例按需收紧。"""
    return GatewayConfig(
        callers={
            "key-alice": Caller("alice", frozenset({"reader", "writer"})),
            "key-bob": Caller("bob", frozenset({"reader"})),
            "key-carol": Caller("carol", frozenset()),
        },
        authz_rules=authz_rules
        if authz_rules is not None
        else [
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
        ],
        rate_limit=RateLimitPolicy(
            max_requests=max_requests,
            window_seconds=window_seconds,
            max_concurrency=max_concurrency,
        ),
        circuit_breaker=CircuitBreakerPolicy(
            failure_threshold=failure_threshold,
            recovery_seconds=recovery_seconds,
            half_open_max_probes=half_open_max_probes,
        ),
    )


@pytest.fixture()
def upstream() -> MockUpstream:
    return MockUpstream()


@pytest.fixture()
def client(upstream: MockUpstream) -> TestClient:
    return TestClient(create_app(make_config(), upstream))
