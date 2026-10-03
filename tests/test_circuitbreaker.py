"""熔断与上游故障测试：错误归一化、熔断触发、熔断期拒绝、恢复放行。"""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from gateway.circuitbreaker import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    CircuitState,
)
from gateway.config import PROXY_ROUTE
from gateway.errors import ErrorCode, GatewayError
from gateway.upstream import MockUpstream, UpstreamBehavior
from main import create_app
from tests.conftest import make_config

ALICE = {"x-api-key": "key-alice"}


def error_code(response) -> str:
    return response.json()["error"]["code"]


def make_client(
    upstream: MockUpstream, **cb_kwargs
) -> TestClient:
    return TestClient(create_app(make_config(**cb_kwargs), upstream))


class TestUpstreamErrorNormalization:
    """上游的超时、业务错误、内部异常归一化为可区分的对外错误。"""

    def test_timeout_is_normalized(self) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.TIMEOUT)
        client = make_client(upstream)
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 504
        assert error_code(resp) == "upstream_timeout"

    def test_business_error_is_normalized(self) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.BUSINESS_ERROR)
        client = make_client(upstream)
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 502
        assert error_code(resp) == "upstream_business_error"

    def test_internal_error_is_normalized(self) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.INTERNAL_ERROR)
        client = make_client(upstream)
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 502
        assert error_code(resp) == "upstream_internal_error"

    def test_three_failure_kinds_have_distinct_codes(self) -> None:
        codes = set()
        for behavior in (
            UpstreamBehavior.TIMEOUT,
            UpstreamBehavior.BUSINESS_ERROR,
            UpstreamBehavior.INTERNAL_ERROR,
        ):
            client = make_client(MockUpstream(behavior=behavior))
            resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
            codes.add(error_code(resp))
        assert codes == {
            "upstream_timeout",
            "upstream_business_error",
            "upstream_internal_error",
        }


class TestCircuitBreaker:
    """熔断：持续失败触发断开，熔断期请求不再打到上游，恢复后确定放行。"""

    def test_circuit_opens_after_threshold_and_stops_calling_upstream(
        self,
    ) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.INTERNAL_ERROR)
        client = make_client(upstream, failure_threshold=3)

        for _ in range(3):
            resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
            assert resp.status_code == 502
        assert upstream.call_count == 3
        assert client.get("/gateway/state").json()["circuit_breaker"] == "open"

        # 熔断期间：请求被直接拒绝，不再打到上游
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 503
        assert error_code(resp) == "circuit_open"
        assert upstream.call_count == 3

    def test_circuit_recovers_after_recovery_window(self) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.INTERNAL_ERROR)
        client = make_client(
            upstream, failure_threshold=2, recovery_seconds=0.1
        )
        for _ in range(2):
            client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert client.get("/gateway/state").json()["circuit_breaker"] == "open"

        # 上游恢复健康，等待恢复窗口后探测请求应放行并成功
        upstream.behavior = UpstreamBehavior.OK
        time.sleep(0.15)
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 200
        assert client.get("/gateway/state").json()["circuit_breaker"] == "closed"

        # 恢复放行后行为确定：后续请求持续成功
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 200

    def test_failed_probe_reopens_circuit(self) -> None:
        upstream = MockUpstream(behavior=UpstreamBehavior.INTERNAL_ERROR)
        client = make_client(
            upstream, failure_threshold=2, recovery_seconds=0.1
        )
        for _ in range(2):
            client.post(PROXY_ROUTE, headers=ALICE, json={})

        # 恢复窗口后上游仍然失败：探测失败，重新断开
        time.sleep(0.15)
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 502
        assert client.get("/gateway/state").json()["circuit_breaker"] == "open"

        # 再次熔断期间依旧直接拒绝
        resp = client.post(PROXY_ROUTE, headers=ALICE, json={})
        assert resp.status_code == 503
        assert error_code(resp) == "circuit_open"

    def test_half_open_probe_count_is_limited(self) -> None:
        # 单元层：半开状态只放行 half_open_max_probes 个探测请求
        async def scenario() -> None:
            breaker = CircuitBreaker(
                CircuitBreakerPolicy(
                    failure_threshold=1,
                    recovery_seconds=0.01,
                    half_open_max_probes=1,
                )
            )
            await breaker.before_call()
            await breaker.on_failure()
            assert breaker.state is CircuitState.OPEN
            await asyncio.sleep(0.02)
            await breaker.before_call()  # 第一个探测请求放行
            assert breaker.state is CircuitState.HALF_OPEN
            try:
                await breaker.before_call()  # 超出探测限量，拒绝
                raise AssertionError("should have been rejected")
            except GatewayError as exc:
                assert exc.code is ErrorCode.CIRCUIT_OPEN

        asyncio.run(scenario())
