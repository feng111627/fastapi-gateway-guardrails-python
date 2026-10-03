"""速率限制与并发限制：按 (调用方, 接口) 维度，asyncio 锁保证并发安全。

- RateLimiter：固定窗口计数，窗口内额度用完后一律拒绝，不透支。
- ConcurrencyLimiter：并发槽位，acquire/release 配对；被拒的请求不占槽位，
  因此不会影响已经通过的请求。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .errors import ErrorCode, GatewayError


@dataclass(frozen=True)
class RateLimitPolicy:
    """限流策略：固定窗口 + 最大并发。"""

    max_requests: int
    window_seconds: float
    max_concurrency: int


@dataclass
class _Window:
    start: float
    count: int = 0


class RateLimiter:
    """固定窗口限流器，key 为 (caller, route)。"""

    def __init__(self, policy: RateLimitPolicy) -> None:
        self._policy = policy
        self._lock = asyncio.Lock()
        self._windows: dict[tuple[str, str], _Window] = {}

    async def acquire(self, caller: str, route: str) -> None:
        """占用一份速率额度，超限时抛 GatewayError(RATE_LIMITED)。"""
        now = time.monotonic()
        async with self._lock:
            key = (caller, route)
            window = self._windows.get(key)
            if window is None or now - window.start >= self._policy.window_seconds:
                window = _Window(start=now)
                self._windows[key] = window
            if window.count >= self._policy.max_requests:
                raise GatewayError(
                    ErrorCode.RATE_LIMITED,
                    f"rate limit exceeded for {caller} on {route}",
                    http_status=429,
                )
            window.count += 1

    async def usage(self, caller: str, route: str) -> int:
        """当前窗口已用额度（可观察性，供测试与监控）。"""
        async with self._lock:
            window = self._windows.get((caller, route))
            return 0 if window is None else window.count


class ConcurrencyLimiter:
    """并发限制器，key 为 (caller, route)，需配对 acquire/release。"""

    def __init__(self, policy: RateLimitPolicy) -> None:
        self._policy = policy
        self._lock = asyncio.Lock()
        self._inflight: dict[tuple[str, str], int] = {}

    async def acquire(self, caller: str, route: str) -> None:
        """占用一个并发槽位，满时抛 GatewayError(CONCURRENCY_LIMITED)。"""
        async with self._lock:
            key = (caller, route)
            current = self._inflight.get(key, 0)
            if current >= self._policy.max_concurrency:
                raise GatewayError(
                    ErrorCode.CONCURRENCY_LIMITED,
                    f"concurrency limit exceeded for {caller} on {route}",
                    http_status=429,
                )
            self._inflight[key] = current + 1

    async def release(self, caller: str, route: str) -> None:
        """释放一个并发槽位。"""
        async with self._lock:
            key = (caller, route)
            current = self._inflight.get(key, 0)
            if current <= 0:
                return
            if current == 1:
                del self._inflight[key]
            else:
                self._inflight[key] = current - 1

    async def inflight(self, caller: str, route: str) -> int:
        """当前在途并发数（可观察性）。"""
        async with self._lock:
            return self._inflight.get((caller, route), 0)
