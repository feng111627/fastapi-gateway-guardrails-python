"""本地模拟的上游服务与错误归一化。

MockUpstream 用固定的行为模式或行为脚本驱动，保证测试结果可复现；
call_upstream_normalized 把超时、业务错误、内部异常统一归一化为
携带可区分错误码的 GatewayError。
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field

from .errors import ErrorCode, GatewayError


class UpstreamBehavior(str, enum.Enum):
    """模拟上游的行为模式。"""

    OK = "ok"
    TIMEOUT = "timeout"
    BUSINESS_ERROR = "business_error"
    INTERNAL_ERROR = "internal_error"


class UpstreamBusinessError(Exception):
    """上游返回的业务错误（如参数校验失败）。"""


class UpstreamInternalError(Exception):
    """上游内部异常（如未捕获的服务端错误）。"""


@dataclass
class MockUpstream:
    """可编程的本地模拟上游。

    behavior 为默认行为；set_script 可设定按调用顺序依次表现的行为，
    脚本用完后保持最后一个行为，保证多次调用的结果可复现。
    """

    behavior: UpstreamBehavior = UpstreamBehavior.OK
    latency_seconds: float = 0.0
    timeout_seconds: float = 0.05
    call_count: int = 0
    _script: list[UpstreamBehavior] = field(default_factory=list)

    def set_script(self, behaviors: list[UpstreamBehavior]) -> None:
        """设定按调用顺序依次表现的行为脚本，用完后保持最后一个行为。"""
        if not behaviors:
            raise ValueError("script must be non-empty")
        self._script = list(behaviors)

    def _next_behavior(self) -> UpstreamBehavior:
        if not self._script:
            return self.behavior
        if len(self._script) == 1:
            return self._script[0]
        return self._script.pop(0)

    async def call(self, payload: dict) -> dict:
        """模拟一次上游调用，按行为返回结果或抛出对应异常。"""
        self.call_count += 1
        behavior = self._next_behavior()
        if behavior is UpstreamBehavior.TIMEOUT:
            # 睡到远超超时时间，由调用方的 wait_for 判定超时
            await asyncio.sleep(self.timeout_seconds * 10 + 0.1)
            raise AssertionError("unreachable")
        await asyncio.sleep(self.latency_seconds)
        if behavior is UpstreamBehavior.BUSINESS_ERROR:
            raise UpstreamBusinessError("upstream rejected the request")
        if behavior is UpstreamBehavior.INTERNAL_ERROR:
            raise UpstreamInternalError("upstream crashed")
        return {"upstream": "mock", "echo": payload}


async def call_upstream_normalized(upstream: MockUpstream, payload: dict) -> dict:
    """调用上游并把超时/业务错误/内部异常归一化为 GatewayError。"""
    try:
        return await asyncio.wait_for(
            upstream.call(payload), timeout=upstream.timeout_seconds
        )
    except (TimeoutError, asyncio.TimeoutError):
        raise GatewayError(
            ErrorCode.UPSTREAM_TIMEOUT,
            "upstream call timed out",
            http_status=504,
        ) from None
    except UpstreamBusinessError as exc:
        raise GatewayError(
            ErrorCode.UPSTREAM_BUSINESS_ERROR, str(exc), http_status=502
        ) from None
    except UpstreamInternalError as exc:
        raise GatewayError(
            ErrorCode.UPSTREAM_INTERNAL_ERROR, str(exc), http_status=502
        ) from None
    except GatewayError:
        raise
    except Exception as exc:  # 未预期的异常也归一化，不外泄细节
        raise GatewayError(
            ErrorCode.UPSTREAM_INTERNAL_ERROR,
            f"unexpected upstream error: {type(exc).__name__}",
            http_status=502,
        ) from None
