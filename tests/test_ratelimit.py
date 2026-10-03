"""限流测试：速率限制、并发限制、并发下不超发不漏计、拒绝不影响已通过请求。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from gateway.config import PROXY_ROUTE
from gateway.errors import ErrorCode, GatewayError
from gateway.ratelimit import ConcurrencyLimiter, RateLimiter, RateLimitPolicy
from gateway.upstream import MockUpstream
from main import create_app
from tests.conftest import make_config

ALICE = {"x-api-key": "key-alice"}
BOB = {"x-api-key": "key-bob"}


def error_code(response) -> str:
    return response.json()["error"]["code"]


class TestRateLimitHttp:
    """HTTP 层：固定窗口速率限制，按 (调用方, 接口) 维度计数。"""

    def test_exceeding_quota_is_rejected_with_distinct_code(self) -> None:
        app = create_app(make_config(max_requests=2), MockUpstream())
        client = TestClient(app)
        codes = [
            client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert error_code(resp) == "rate_limited"

    def test_quota_is_per_caller(self) -> None:
        # alice 用尽额度不影响 bob（bob 被授权规则放行需单独配置）
        from gateway.authz import AuthzRule, Effect

        rules = [
            AuthzRule(
                name="allow-readers",
                effect=Effect.ALLOW,
                route=PROXY_ROUTE,
                role="reader",
                priority=50,
            )
        ]
        app = create_app(
            make_config(authz_rules=rules, max_requests=1), MockUpstream()
        )
        client = TestClient(app)
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 200
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 429
        assert client.post(PROXY_ROUTE, headers=BOB, json={}).status_code == 200

    def test_window_reset_allows_new_requests(self) -> None:
        app = create_app(
            make_config(max_requests=1, window_seconds=0.05), MockUpstream()
        )
        client = TestClient(app)
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 200
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 429
        import time

        time.sleep(0.06)
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 200


class TestRateLimiterConcurrency:
    """单元层：并发争抢下额度不超发、不漏计。"""

    def test_no_overissue_under_concurrency(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            limiter = RateLimiter(
                RateLimitPolicy(
                    max_requests=5, window_seconds=60.0, max_concurrency=100
                )
            )
            results: list[str] = []

            async def attempt() -> None:
                try:
                    await limiter.acquire("alice", PROXY_ROUTE)
                    results.append("ok")
                except GatewayError as exc:
                    assert exc.code is ErrorCode.RATE_LIMITED
                    results.append("rejected")

            await asyncio.gather(*(attempt() for _ in range(20)))
            usage = await limiter.usage("alice", PROXY_ROUTE)
            return results.count("ok"), results.count("rejected"), usage

        ok, rejected, usage = asyncio.run(scenario())
        assert ok == 5  # 恰好放行额度内请求，不超发
        assert rejected == 15
        assert usage == 5  # 计数与放行数一致，不漏计


class TestConcurrencyLimiter:
    """并发限制：槽位满即拒绝，被拒请求不占槽位、不影响已通过请求。"""

    def test_slots_are_not_overissued(self) -> None:
        async def scenario() -> tuple[int, int]:
            limiter = ConcurrencyLimiter(
                RateLimitPolicy(
                    max_requests=100, window_seconds=60.0, max_concurrency=3
                )
            )
            acquired = 0
            for _ in range(10):
                try:
                    await limiter.acquire("alice", PROXY_ROUTE)
                    acquired += 1
                except GatewayError as exc:
                    assert exc.code is ErrorCode.CONCURRENCY_LIMITED
            inflight = await limiter.inflight("alice", PROXY_ROUTE)
            return acquired, inflight

        acquired, inflight = asyncio.run(scenario())
        assert acquired == 3
        assert inflight == 3  # 被拒的 7 次没有占用槽位

    def test_release_frees_slot_for_next_request(self) -> None:
        async def scenario() -> None:
            limiter = ConcurrencyLimiter(
                RateLimitPolicy(
                    max_requests=100, window_seconds=60.0, max_concurrency=1
                )
            )
            await limiter.acquire("alice", PROXY_ROUTE)
            try:
                await limiter.acquire("alice", PROXY_ROUTE)
                raise AssertionError("should have been rejected")
            except GatewayError:
                pass
            await limiter.release("alice", PROXY_ROUTE)
            await limiter.acquire("alice", PROXY_ROUTE)  # 释放后可再次获取
            assert await limiter.inflight("alice", PROXY_ROUTE) == 1

        asyncio.run(scenario())

    def test_rejected_request_does_not_affect_inflight_http(self) -> None:
        # HTTP 层端到端：慢上游 + max_concurrency=1，并发两请求，
        # 一个正常完成，一个被拒为 concurrency_limited，且最终无槽位泄漏
        upstream = MockUpstream(latency_seconds=0.1, timeout_seconds=1.0)
        app = create_app(make_config(max_concurrency=1), upstream)
        client = TestClient(app)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.post, PROXY_ROUTE, headers=ALICE, json={})
                for _ in range(2)
            ]
            responses = [f.result() for f in futures]

        codes = sorted(r.status_code for r in responses)
        assert codes == [200, 429]
        rejected = [r for r in responses if r.status_code == 429][0]
        assert error_code(rejected) == "concurrency_limited"
        # 槽位已释放：后续请求可以正常通过
        assert client.post(PROXY_ROUTE, headers=ALICE, json={}).status_code == 200
