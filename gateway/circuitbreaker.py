"""熔断器：上游持续失败时断开，避免请求继续打到上游。"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass


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
    """单上游熔断器，状态迁移确定且可观察。"""

    def __init__(self, policy: CircuitBreakerPolicy) -> None:
        self._policy = policy
        self._state = CircuitState.CLOSED
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self) -> None:
        """调用上游前检查，熔断中抛 GatewayError(CIRCUIT_OPEN)。"""
        raise NotImplementedError

    async def on_success(self) -> None:
        """记录一次成功。"""
        raise NotImplementedError

    async def on_failure(self) -> None:
        """记录一次失败，达到阈值则断开。"""
        raise NotImplementedError
