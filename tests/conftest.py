"""测试公共夹具：构建带本地模拟上游的网关客户端。"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from starlette.testclient import TestClient

from app.authz import AuthzRule, Effect
from app.config import GuardrailSettings, RateLimitRule
from app.gateway import create_app
from app.upstream import FakeUpstream

DEFAULT_RULES = [
    # 高优先级：显式封禁 mallory
    AuthzRule(name="deny-mallory", effect=Effect.DENY, priority=100, caller="mallory"),
    # 管理员全通
    AuthzRule(name="allow-admin", effect=Effect.ALLOW, priority=50, role="admin"),
    # reader 角色可访问 /proxy/public 前缀
    AuthzRule(
        name="allow-reader-public",
        effect=Effect.ALLOW,
        priority=10,
        role="reader",
        path_prefix="/proxy/public",
    ),
]

HEADERS_ALICE = {"X-Caller-Id": "alice", "X-Caller-Roles": "admin"}
HEADERS_BOB = {"X-Caller-Id": "bob", "X-Caller-Roles": "reader"}
HEADERS_MALLORY = {"X-Caller-Id": "mallory", "X-Caller-Roles": "admin"}


@pytest.fixture()
def make_client() -> Callable[..., tuple[TestClient, FakeUpstream]]:
    """工厂夹具：按指定配置构建 (TestClient, FakeUpstream)。"""

    def _make(
        *,
        settings: GuardrailSettings | None = None,
        rules: list[AuthzRule] | None = None,
        script: tuple[str, ...] = ("ok",),
    ) -> tuple[TestClient, FakeUpstream]:
        upstream = FakeUpstream()
        upstream.script(*script)
        app = create_app(
            settings=settings or GuardrailSettings(),
            upstream=upstream,
            rules=rules if rules is not None else list(DEFAULT_RULES),
        )
        return TestClient(app), upstream

    return _make


@pytest.fixture()
def permissive_settings() -> GuardrailSettings:
    """不限流的配置，用于隔离测试授权与熔断。"""
    return GuardrailSettings(
        default_rate_limit=RateLimitRule(max_requests=1000, window_seconds=60.0),
        max_concurrency_per_key=1000,
    )
