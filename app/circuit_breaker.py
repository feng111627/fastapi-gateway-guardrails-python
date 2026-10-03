"""熔断器：上游持续失败时打开，冷却后半开探测，恢复后关闭。"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """熔断打开期间调用被快速拒绝。"""


class CircuitBreaker:
    """按上游服务维度熔断，状态迁移确定且可观察。

    状态机：
    - closed：正常放行；连续失败达到 failure_threshold 后进入 open。
    - open：快速失败；冷却 cooldown_seconds 后进入 half_open。
    - half_open：放行至多 half_open_probes 个探测请求；
      探测成功 -> closed，探测失败 -> 重新 open 并重新计时冷却。
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
        half_open_probes: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_probes = half_open_probes
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._in_flight_probes = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> BreakerState:
        # open 状态冷却到期后对外表现为 half_open（惰性迁移，便于观察）
        if self._state == BreakerState.OPEN and self._cooldown_elapsed():
            return BreakerState.HALF_OPEN
        return self._state

    def _cooldown_elapsed(self) -> bool:
        return time.monotonic() - self._opened_at >= self._cooldown_seconds

    async def before_call(self) -> None:
        """调用上游前检查；熔断中抛 CircuitOpenError，半开时占用探测名额。"""
        async with self._lock:
            if self._state == BreakerState.OPEN:
                if not self._cooldown_elapsed():
                    raise CircuitOpenError("circuit is open")
                self._state = BreakerState.HALF_OPEN
                self._in_flight_probes = 0
            if self._state == BreakerState.HALF_OPEN:
                if self._in_flight_probes >= self._half_open_probes:
                    raise CircuitOpenError("circuit is half-open, probe budget exhausted")
                self._in_flight_probes += 1

    async def on_success(self) -> None:
        """上游调用成功：重置失败计数，半开则关闭。"""
        async with self._lock:
            self._consecutive_failures = 0
            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.CLOSED
                self._in_flight_probes = 0

    async def on_failure(self) -> None:
        """上游调用失败：累计失败，达到阈值则打开熔断。"""
        async with self._lock:
            self._consecutive_failures += 1
            if self._state == BreakerState.HALF_OPEN:
                # 探测失败：重新打开并重新计时冷却
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._in_flight_probes = 0
            elif self._consecutive_failures >= self._failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
