"""FastAPI 网关入口：装配身份解析、授权、限流、熔断与上游代理。"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.errors import GatewayError


def create_app() -> FastAPI:
    app = FastAPI(title="gateway-guardrails")

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code.value, "message": exc.message}},
        )

    @app.get("/")
    def read_root() -> dict:
        return {"Hello": "World"}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
