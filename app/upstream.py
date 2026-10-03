"""上游服务抽象与本地模拟实现：行为可脚本化，结果可复现。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UpstreamResponse:
    """上游响应：状态码 + 业务负载。"""

    status: int
    body: dict


class UpstreamError(Exception):
    """上游内部异常（连接失败等）。"""


class UpstreamTimeoutError(Exception):
    """上游调用超时。"""


class UpstreamClient(Protocol):
    """上游客户端协议，便于替换真实实现。"""

    async def call(self, path: str, timeout_seconds: float) -> UpstreamResponse: ...


class FakeUpstream:
    """本地模拟上游：按脚本队列依次返回行为，支持超时/错误/异常。

    脚本行为：
    - "ok"        -> 200 + 业务负载
    - "biz_error" -> 422 业务错误
    - "timeout"   -> 永不返回，由调用方超时截断
    - "boom"      -> 抛出 UpstreamError（模拟连接失败等内部异常）
    脚本耗尽后默认重复最后一个行为，保证结果可复现。
    """

    def __init__(self) -> None:
        self._script: list[str] = []
        self._cursor = 0
        self.calls: list[str] = []

    def script(self, *behaviors: str) -> None:
        """预置行为序列：ok / biz_error / timeout / boom。"""
        valid = {"ok", "biz_error", "timeout", "boom"}
        unknown = set(behaviors) - valid
        if unknown:
            raise ValueError(f"unknown behaviors: {unknown}")
        self._script = list(behaviors)
        self._cursor = 0

    def _next_behavior(self) -> str:
        if not self._script:
            return "ok"
        behavior = self._script[min(self._cursor, len(self._script) - 1)]
        self._cursor += 1
        return behavior

    async def call(self, path: str, timeout_seconds: float) -> UpstreamResponse:
        self.calls.append(path)
        behavior = self._next_behavior()
        if behavior == "boom":
            raise UpstreamError(f"upstream connection failed for {path}")
        if behavior == "timeout":
            await asyncio.sleep(timeout_seconds + 1.0)
            raise UpstreamTimeoutError(f"upstream did not respond for {path}")
        if behavior == "biz_error":
            return UpstreamResponse(status=422, body={"detail": "business rule violated"})
        return UpstreamResponse(status=200, body={"echo": path, "source": "fake-upstream"})
