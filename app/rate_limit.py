"""限流与并发控制：固定窗口速率限制 + 按 key 的并发信号量。"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator


class RateLimiter:
    """固定窗口限流器，按 key 独立计数，协程安全。

    每个 key 维护 (窗口起点, 已用额度)。额度检查与扣减在同一个锁内完成，
    并发下不会超发；被拒绝的请求不扣减额度，不影响已通过的请求。
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        """尝试为 key 获取一个额度，成功返回 True，超限返回 False。"""
        now = time.monotonic()
        async with self._lock:
            start, used = self._windows.get(key, (now, 0))
            if now - start >= self._window_seconds:
                start, used = now, 0
            if used >= self._max_requests:
                self._windows[key] = (start, used)
                return False
            self._windows[key] = (start, used + 1)
            return True


class ConcurrencyLimiter:
    """按 key 的并发限制器，超限时立即拒绝而不排队。

    计数与检查在同一锁内完成，保证不超发；释放时计数减一，
    被拒绝的请求不会占用槽位，也不影响在途请求。
    """

    def __init__(self, max_concurrency: int) -> None:
        self._max_concurrency = max_concurrency
        self._in_flight: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[bool]:
        """进入时尝试占用并发槽；占用失败 yield False，成功则退出时释放。"""
        async with self._lock:
            current = self._in_flight.get(key, 0)
            if current >= self._max_concurrency:
                acquired = False
            else:
                self._in_flight[key] = current + 1
                acquired = True
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            async with self._lock:
                remaining = self._in_flight.get(key, 0) - 1
                if remaining <= 0:
                    self._in_flight.pop(key, None)
                else:
                    self._in_flight[key] = remaining
