"""网关装配：把身份解析、授权、限流、熔断、上游调用串成请求管线。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.authz import AuthzRule, authorize
from app.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.config import GuardrailSettings
from app.errors import STATUS_BY_CODE, ErrorCode
from app.identity import IdentityError, parse_identity
from app.rate_limit import ConcurrencyLimiter, RateLimiter
from app.upstream import UpstreamClient, UpstreamError, UpstreamTimeoutError


def _error_response(code: ErrorCode, detail: str) -> JSONResponse:
    """统一错误响应：HTTP 状态码 + 可区分的错误码与原因。"""
    return JSONResponse(
        status_code=STATUS_BY_CODE[code],
        content={"error": {"code": str(code), "detail": detail}},
    )


def create_app(
    settings: GuardrailSettings,
    upstream: UpstreamClient,
    rules: list[AuthzRule],
) -> FastAPI:
    """构建带防护能力的网关应用。

    请求管线顺序：身份解析 -> 授权 -> 速率限制 -> 并发限制 -> 熔断检查 -> 上游调用。
    任一环节拒绝都返回可区分的错误，且不影响其他请求。
    """
    app = FastAPI(title="gateway-guardrails")
    rate_limiter = RateLimiter(
        max_requests=settings.default_rate_limit.max_requests,
        window_seconds=settings.default_rate_limit.window_seconds,
    )
    concurrency_limiter = ConcurrencyLimiter(settings.max_concurrency_per_key)
    breaker = CircuitBreaker(
        failure_threshold=settings.breaker_failure_threshold,
        cooldown_seconds=settings.breaker_cooldown_seconds,
        half_open_probes=settings.breaker_half_open_probes,
    )
    app.state.breaker = breaker

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "breaker": str(breaker.state)}

    @app.api_route("/proxy/{path:path}", methods=["GET", "POST"])
    async def proxy(request: Request, path: str) -> JSONResponse:
        route_path = f"/proxy/{path}"

        # 1. 身份解析：缺失/非法身份直接拒绝
        try:
            identity = parse_identity(request)
        except IdentityError as exc:
            return _error_response(exc.code, exc.detail)

        # 2. 授权：无规则命中、规则冲突、显式拒绝都明确拒绝
        decision = authorize(identity, route_path, rules)
        if not decision.allowed:
            assert decision.code is not None
            return _error_response(decision.code, decision.detail)

        # 3. 速率限制：按 调用方+接口 维度
        key = f"{identity.caller}:{route_path}"
        if not await rate_limiter.allow(key):
            return _error_response(
                ErrorCode.RATE_LIMITED,
                f"rate limit exceeded for {identity.caller} on {route_path}",
            )

        # 4. 并发限制：超限立即拒绝，不排队
        async with concurrency_limiter.acquire(key) as acquired:
            if not acquired:
                return _error_response(
                    ErrorCode.CONCURRENCY_LIMITED,
                    f"too many concurrent requests for {identity.caller} on {route_path}",
                )

            # 5. 熔断检查：打开期间快速失败，不打到上游
            try:
                await breaker.before_call()
            except CircuitOpenError:
                return _error_response(
                    ErrorCode.CIRCUIT_OPEN,
                    f"upstream circuit is {breaker.state}; request rejected",
                )

            # 6. 上游调用：超时/业务错误/内部异常归一化为可区分错误
            try:
                response = await asyncio.wait_for(
                    upstream.call(route_path, settings.upstream_timeout_seconds),
                    timeout=settings.upstream_timeout_seconds,
                )
            except (asyncio.TimeoutError, UpstreamTimeoutError):
                await breaker.on_failure()
                return _error_response(
                    ErrorCode.UPSTREAM_TIMEOUT,
                    f"upstream timed out after {settings.upstream_timeout_seconds}s",
                )
            except UpstreamError as exc:
                await breaker.on_failure()
                return _error_response(ErrorCode.UPSTREAM_UNAVAILABLE, str(exc))

            if response.status >= 400:
                await breaker.on_failure()
                return _error_response(
                    ErrorCode.UPSTREAM_ERROR,
                    f"upstream returned status {response.status}",
                )

            await breaker.on_success()
            return JSONResponse(status_code=response.status, content=response.body)

    return app
