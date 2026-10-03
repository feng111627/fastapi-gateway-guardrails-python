"""熔断器：上游持续失败时断开，避免请求继续打到上游。

状态机（迁移确定且可观察）：
- CLOSED：正常放行；连续失败达到 failure_threshold 次 -> OPEN。
- OPEN：直接拒绝（CIRCUIT_OPEN），请求不会打到上游；
  断开满 recovery_seconds 后，下一个请求进入 HALF_OPEN 作为探测。
- HALF_OPEN：最多放行 half_open_max_probes 个探测请求，超出拒绝；
  探测成功 -> CLOSED（计数清零），探测失败 -> 回到 OPEN 并重新计时。
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass

from .errors import ErrorCode, GatewayError


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    """熔断策略。"""

    failure_threshold: int
    recovery_seconds: float
    half_open_max_probes: int


class CircuitBreaker:
    """单上游熔断器。"""

    def __init__(self, policy: CircuitBreakerPolicy) -> None:
        self._policy = policy
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def before_call(self) -> None:
        """调用上游前检查，熔断中抛 GatewayError(CIRCUIT_OPEN)。"""
        async with self._lock:
            if self._state is CircuitState.CLOSED:
                return
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if time.monotonic() - self._opened_at >= self._policy.recovery_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_inflight = 0
                else:
                    raise GatewayError(
                        ErrorCode.CIRCUIT_OPEN,
                        "circuit breaker is open",
                        http_status=503,
                    )
            # HALF_OPEN：限量放行探测请求
            if self._half_open_inflight >= self._policy.half_open_max_probes:
                raise GatewayError(
                    ErrorCode.CIRCUIT_OPEN,
                    "circuit breaker half-open probe limit reached",
                    http_status=503,
                )
            self._half_open_inflight += 1

    async def on_success(self) -> None:
        """记录一次成功：半开探测成功即闭合，闭合态清零失败计数。"""
        async with self._lock:
            self._consecutive_failures = 0
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._opened_at = None
                self._half_open_inflight = 0

    async def on_failure(self) -> None:
        """记录一次失败：达到阈值断开；半开探测失败立即重新断开。"""
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0
                return
            self._consecutive_failures += 1
            if (
                self._state is CircuitState.CLOSED
                and self._consecutive_failures >= self._policy.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
