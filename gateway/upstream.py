"""本地模拟的上游服务与错误归一化。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class UpstreamBehavior(str, enum.Enum):
    """模拟上游的行为模式。"""

    OK = "ok"
    TIMEOUT = "timeout"
    BUSINESS_ERROR = "business_error"
    INTERNAL_ERROR = "internal_error"


@dataclass
class MockUpstream:
    """可编程的本地模拟上游，行为由队列或固定模式驱动，保证可复现。"""

    behavior: UpstreamBehavior = UpstreamBehavior.OK
    latency_seconds: float = 0.0
    timeout_seconds: float = 0.05
    call_count: int = 0
    _script: list[UpstreamBehavior] = field(default_factory=list)

    def set_script(self, behaviors: list[UpstreamBehavior]) -> None:
        """设定按调用顺序依次表现的行为脚本，用完后保持最后一个行为。"""
        raise NotImplementedError

    async def call(self, payload: dict) -> dict:
        """模拟一次上游调用，按行为抛出对应异常或返回结果。"""
        raise NotImplementedError


async def call_upstream_normalized(upstream: MockUpstream, payload: dict) -> dict:
    """调用上游并把超时/业务错误/内部异常归一化为 GatewayError。"""
    raise NotImplementedError
