"""速率限制与并发限制：按调用方 + 接口维度，并发安全。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitPolicy:
    """限流策略：固定窗口。"""

    max_requests: int
    window_seconds: float
    max_concurrency: int


class RateLimiter:
    """固定窗口限流器，key 为 (caller, route)。"""

    def __init__(self, policy: RateLimitPolicy) -> None:
        self._policy = policy
        self._lock = asyncio.Lock()

    async def acquire(self, caller: str, route: str) -> None:
        """占用一份速率额度，超限时抛 GatewayError(RATE_LIMITED)。"""
        raise NotImplementedError


class ConcurrencyLimiter:
    """并发限制器，key 为 (caller, route)，需配对 acquire/release。"""

    def __init__(self, policy: RateLimitPolicy) -> None:
        self._policy = policy
        self._lock = asyncio.Lock()

    async def acquire(self, caller: str, route: str) -> None:
        """占用一个并发槽位，满时抛 GatewayError(CONCURRENCY_LIMITED)。"""
        raise NotImplementedError

    async def release(self, caller: str, route: str) -> None:
        """释放一个并发槽位。"""
        raise NotImplementedError
