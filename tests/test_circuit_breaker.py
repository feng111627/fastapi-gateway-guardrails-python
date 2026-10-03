"""熔断与上游故障归一化测试：覆盖错误分类、熔断打开、快速失败、恢复放行。"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.authz import AuthzRule, Effect
from app.config import GuardrailSettings, RateLimitRule
from app.gateway import create_app
from app.upstream import FakeUpstream

ALLOW_ALL = [AuthzRule(name="allow-all", effect=Effect.ALLOW, priority=1)]
HEADERS = {"X-Caller-Id": "caller-a"}


def _settings(**overrides) -> GuardrailSettings:
    base = dict(
        default_rate_limit=RateLimitRule(max_requests=1000, window_seconds=60.0),
        max_concurrency_per_key=100,
        upstream_timeout_seconds=0.2,
        breaker_failure_threshold=3,
        breaker_cooldown_seconds=0.2,
        breaker_half_open_probes=1,
    )
    base.update(overrides)
    return GuardrailSettings(**base)


def _build(settings: GuardrailSettings, script: tuple[str, ...]):
    upstream = FakeUpstream()
    upstream.script(*script)
    app = create_app(settings=settings, upstream=upstream, rules=ALLOW_ALL)
    return app, upstream


async def _get(app, path: str = "/proxy/x") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, headers=HEADERS)


class TestUpstreamErrorMapping:
    """上游故障归一化为对外可区分的错误。"""

    def test_上游超时归一化为504(self):
        app, _ = _build(_settings(), ("timeout",))
        resp = asyncio.run(_get(app))
        assert resp.status_code == 504
        assert resp.json()["error"]["code"] == "upstream_timeout"

    def test_上游业务错误归一化为502(self):
        app, _ = _build(_settings(), ("biz_error",))
        resp = asyncio.run(_get(app))
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "upstream_error"

    def test_上游内部异常归一化为502且与业务错误可区分(self):
        app, _ = _build(_settings(), ("boom",))
        resp = asyncio.run(_get(app))
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "upstream_unavailable"

    def test_上游正常响应透传(self):
        app, _ = _build(_settings(), ("ok",))
        resp = asyncio.run(_get(app))
        assert resp.status_code == 200
        assert resp.json()["source"] == "fake-upstream"


class TestCircuitBreaker:
    """熔断状态机：打开 -> 快速失败 -> 冷却 -> 半开探测 -> 恢复。"""

    def test_连续失败达到阈值后熔断打开并快速失败(self):
        # 3 次 boom 触发熔断，第 4 个请求应被熔断拦截、不打到上游
        app, upstream = _build(_settings(), ("boom",))
        codes = [asyncio.run(_get(app)).status_code for _ in range(4)]
        assert codes[:3] == [502, 502, 502]
        assert codes[3] == 503
        assert len(upstream.calls) == 3  # 第 4 次没有打到上游

    def test_熔断期间错误码可区分且行为确定(self):
        app, upstream = _build(_settings(), ("boom",))
        for _ in range(3):
            asyncio.run(_get(app))
        # 熔断打开期间连续请求都稳定返回 circuit_open
        for _ in range(3):
            resp = asyncio.run(_get(app))
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "circuit_open"
        assert len(upstream.calls) == 3

    def test_冷却后半开探测成功则恢复放行(self):
        # 先 3 次失败打开熔断；之后上游恢复健康，冷却后探测成功并关闭
        app, upstream = _build(_settings(), ("boom", "boom", "boom", "ok"))
        for _ in range(3):
            asyncio.run(_get(app))
        assert asyncio.run(_get(app)).status_code == 503  # 熔断中

        time.sleep(0.25)  # 等待冷却结束
        resp = asyncio.run(_get(app))  # 半开探测，上游已恢复
        assert resp.status_code == 200
        assert resp.json()["source"] == "fake-upstream"

        # 恢复后行为确定：后续请求正常放行
        resp = asyncio.run(_get(app))
        assert resp.status_code == 200
        assert len(upstream.calls) == 5

    def test_半开探测失败则重新熔断(self):
        app, upstream = _build(_settings(), ("boom",))
        for _ in range(3):
            asyncio.run(_get(app))

        time.sleep(0.25)  # 冷却结束，进入半开
        resp = asyncio.run(_get(app))  # 探测仍失败
        assert resp.status_code == 502
        # 重新熔断：下一个请求快速失败，不再打到上游
        calls_before = len(upstream.calls)
        resp = asyncio.run(_get(app))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "circuit_open"
        assert len(upstream.calls) == calls_before

    def test_熔断状态可通过健康端点观察(self):
        app, _ = _build(_settings(), ("boom",))

        async def health() -> str:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return (await client.get("/health")).json()["breaker"]

        assert asyncio.run(health()) == "closed"
        for _ in range(3):
            asyncio.run(_get(app))
        assert asyncio.run(health()) == "open"
        time.sleep(0.25)
        assert asyncio.run(health()) == "half_open"

    def test_成功响应会重置连续失败计数(self):
        # 失败 2 次（未达阈值 3）后成功 1 次，计数清零；再失败 2 次仍不熔断
        app, upstream = _build(
            _settings(), ("boom", "boom", "ok", "boom", "boom", "ok")
        )
        codes = [asyncio.run(_get(app)).status_code for _ in range(6)]
        assert codes == [502, 502, 200, 502, 502, 200]
        assert len(upstream.calls) == 6  # 始终未熔断
