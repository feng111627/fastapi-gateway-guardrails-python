"""FastAPI 网关入口：装配身份解析、授权、限流、熔断与上游代理。

请求处理顺序（每一步失败都会以可区分的错误码拒绝）：
1. 身份解析 -> 2. 授权判定 -> 3. 速率限制 -> 4. 并发限制
-> 5. 熔断检查 -> 6. 调用上游（错误归一化，成败反馈给熔断器）
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.authz import Authorizer
from gateway.circuitbreaker import CircuitBreaker
from gateway.config import PROXY_ROUTE, GatewayConfig, default_config
from gateway.errors import ErrorCode, GatewayError
from gateway.identity import API_KEY_HEADER, resolve_caller
from gateway.ratelimit import ConcurrencyLimiter, RateLimiter
from gateway.upstream import MockUpstream, call_upstream_normalized


def create_app(
    config: GatewayConfig | None = None,
    upstream: MockUpstream | None = None,
) -> FastAPI:
    config = config or default_config()
    upstream = upstream or MockUpstream()

    authorizer = Authorizer(config.authz_rules)
    rate_limiter = RateLimiter(config.rate_limit)
    concurrency_limiter = ConcurrencyLimiter(config.rate_limit)
    circuit_breaker = CircuitBreaker(config.circuit_breaker)

    app = FastAPI(title="gateway-guardrails")
    app.state.config = config
    app.state.upstream = upstream
    app.state.circuit_breaker = circuit_breaker

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code.value, "message": exc.message}},
        )

    @app.get("/")
    def read_root() -> dict:
        return {"Hello": "World"}

    @app.get("/gateway/state")
    async def gateway_state() -> dict:
        """可观察性端点：暴露熔断器状态等内部状态。"""
        return {"circuit_breaker": circuit_breaker.state.value}

    @app.post(PROXY_ROUTE)
    async def proxy(request: Request) -> dict:
        route = PROXY_ROUTE

        # 1. 身份解析：缺失/未知一律明确拒绝
        caller = resolve_caller(request.headers.get(API_KEY_HEADER), config.callers)

        # 2. 授权判定：无命中/冲突/显式拒绝都明确拒绝
        decision = authorizer.decide(caller, route)
        if not decision.allowed:
            if decision.reason == "conflict":
                raise GatewayError(
                    ErrorCode.AUTHZ_CONFLICT,
                    f"conflicting rules: {', '.join(decision.matched_rules)}",
                    http_status=403,
                )
            raise GatewayError(
                ErrorCode.AUTHZ_DENIED,
                f"access denied ({decision.reason})",
                http_status=403,
            )

        # 3. 速率限制
        await rate_limiter.acquire(caller.name, route)

        # 4. 并发限制（acquire 成功后必须在 finally 中释放）
        await concurrency_limiter.acquire(caller.name, route)
        try:
            # 5. 熔断检查
            await circuit_breaker.before_call()
            # 6. 调用上游并归一化错误，成败反馈给熔断器
            try:
                payload = await request.json() if await request.body() else {}
                result = await call_upstream_normalized(upstream, payload)
            except GatewayError:
                await circuit_breaker.on_failure()
                raise
            await circuit_breaker.on_success()
            return {"caller": caller.name, "result": result}
        finally:
            await concurrency_limiter.release(caller.name, route)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
