"""限流与并发控制测试：覆盖额度不超发、维度隔离、并发上限、拒绝不影响在途请求。"""

from __future__ import annotations

import asyncio

import httpx

from app.authz import AuthzRule, Effect
from app.config import GuardrailSettings, RateLimitRule
from app.gateway import create_app
from app.upstream import FakeUpstream

ALLOW_ALL = [AuthzRule(name="allow-all", effect=Effect.ALLOW, priority=1)]
HEADERS_A = {"X-Caller-Id": "caller-a"}
HEADERS_B = {"X-Caller-Id": "caller-b"}


def _build(settings: GuardrailSettings, script: tuple[str, ...] = ("ok",)):
    upstream = FakeUpstream()
    upstream.script(*script)
    app = create_app(settings=settings, upstream=upstream, rules=ALLOW_ALL)
    return app, upstream


def _async_client(app: httpx.ASGITransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestRateLimit:
    """按调用方+接口维度的速率限制。"""

    def test_超过速率额度的请求返回429且原因可区分(self, make_client):
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=2, window_seconds=60.0)
        )
        client, _ = make_client(settings=settings, rules=ALLOW_ALL)
        codes = [
            client.get("/proxy/x", headers=HEADERS_A).status_code for _ in range(4)
        ]
        assert codes == [200, 200, 429, 429]
        resp = client.get("/proxy/x", headers=HEADERS_A)
        assert resp.json()["error"]["code"] == "rate_limited"

    def test_限流按调用方隔离互不影响(self, make_client):
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=1, window_seconds=60.0)
        )
        client, _ = make_client(settings=settings, rules=ALLOW_ALL)
        assert client.get("/proxy/x", headers=HEADERS_A).status_code == 200
        assert client.get("/proxy/x", headers=HEADERS_A).status_code == 429
        # 另一个调用方不受影响
        assert client.get("/proxy/x", headers=HEADERS_B).status_code == 200

    def test_限流按接口维度隔离(self, make_client):
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=1, window_seconds=60.0)
        )
        client, _ = make_client(settings=settings, rules=ALLOW_ALL)
        assert client.get("/proxy/x", headers=HEADERS_A).status_code == 200
        assert client.get("/proxy/y", headers=HEADERS_A).status_code == 200
        assert client.get("/proxy/x", headers=HEADERS_A).status_code == 429

    def test_并发请求下额度不超发(self):
        """50 个并发请求抢 10 个额度：恰好 10 个通过，一个不多。"""
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=10, window_seconds=60.0),
            max_concurrency_per_key=100,
        )
        app, upstream = _build(settings)

        async def run() -> list[int]:
            async with _async_client(app) as client:
                responses = await asyncio.gather(
                    *(client.get("/proxy/x", headers=HEADERS_A) for _ in range(50))
                )
            return [r.status_code for r in responses]

        codes = asyncio.run(run())
        assert codes.count(200) == 10
        assert codes.count(429) == 40
        assert len(upstream.calls) == 10  # 打到上游的次数与放行数一致，不漏计

    def test_被拒绝的请求不消耗额度也不影响已通过的(self):
        """授权失败的请求不占用速率额度。"""
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=2, window_seconds=60.0)
        )
        rules = ALLOW_ALL + [
            AuthzRule(name="deny-path", effect=Effect.DENY, priority=10,
                      path_prefix="/proxy/blocked")
        ]
        upstream = FakeUpstream()
        upstream.script("ok")
        app = create_app(settings=settings, upstream=upstream, rules=rules)

        async def run() -> list[int]:
            async with _async_client(app) as client:
                # 先发 5 个会被授权拒绝的请求
                blocked = await asyncio.gather(
                    *(client.get("/proxy/blocked", headers=HEADERS_A) for _ in range(5))
                )
                # 正常请求的额度不受前面拒绝影响
                allowed = await asyncio.gather(
                    *(client.get("/proxy/ok", headers=HEADERS_A) for _ in range(3))
                )
            return [r.status_code for r in blocked] + [r.status_code for r in allowed]

        codes = asyncio.run(run())
        assert codes[:5] == [403] * 5
        assert codes[5:] == [200, 200, 429]  # 额度 2 个完整保留


class TestConcurrencyLimit:
    """按调用方+接口维度的并发限制。"""

    def test_并发超限时立即拒绝且在途请求不受影响(self):
        """3 个并发槽，5 个慢请求同时到达：3 个成功、2 个拒绝，成功的都正常完成。"""
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=100, window_seconds=60.0),
            max_concurrency_per_key=3,
        )
        upstream = FakeUpstream()
        upstream.script("ok")

        original_call = upstream.call

        async def slow_call(path: str, timeout_seconds: float):
            await asyncio.sleep(0.05)  # 模拟慢上游，让并发窗口重叠
            return await original_call(path, timeout_seconds)

        upstream.call = slow_call  # type: ignore[method-assign]
        app = create_app(settings=settings, upstream=upstream, rules=ALLOW_ALL)

        async def run() -> tuple[list[int], list[dict]]:
            async with _async_client(app) as client:
                responses = await asyncio.gather(
                    *(client.get("/proxy/slow", headers=HEADERS_A) for _ in range(5))
                )
            return [r.status_code for r in responses], [r.json() for r in responses]

        codes, bodies = asyncio.run(run())
        assert codes.count(200) == 3
        assert codes.count(429) == 2
        rejected = [b for c, b in zip(codes, bodies) if c == 429]
        assert all(b["error"]["code"] == "concurrency_limited" for b in rejected)
        # 已通过的请求都拿到了完整的上游响应
        passed = [b for c, b in zip(codes, bodies) if c == 200]
        assert all(b.get("source") == "fake-upstream" for b in passed)

    def test_并发槽在请求结束后释放(self):
        settings = GuardrailSettings(
            default_rate_limit=RateLimitRule(max_requests=100, window_seconds=60.0),
            max_concurrency_per_key=1,
        )
        app, _ = _build(settings)

        async def run() -> list[int]:
            async with _async_client(app) as client:
                # 串行发 3 个请求：每个结束后槽位释放，都应成功
                return [
                    (await client.get("/proxy/x", headers=HEADERS_A)).status_code
                    for _ in range(3)
                ]

        assert asyncio.run(run()) == [200, 200, 200]
