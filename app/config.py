"""网关防护配置：集中定义限流、并发、熔断、上游超时等参数。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RateLimitRule:
    """单条限流规则：window_seconds 内最多 max_requests 个请求。"""

    max_requests: int
    window_seconds: float


@dataclass(frozen=True)
class GuardrailSettings:
    """网关防护总配置。"""

    # 速率限制：key 为 "caller:path" 维度，default 为兜底
    default_rate_limit: RateLimitRule = field(
        default_factory=lambda: RateLimitRule(max_requests=10, window_seconds=1.0)
    )
    # 每个调用方+接口的最大并发数
    max_concurrency_per_key: int = 5
    # 上游超时（秒）
    upstream_timeout_seconds: float = 2.0
    # 熔断：连续失败达到阈值后打开
    breaker_failure_threshold: int = 3
    # 熔断打开后冷却时长（秒），冷却结束进入半开
    breaker_cooldown_seconds: float = 5.0
    # 半开状态下允许的探测请求数
    breaker_half_open_probes: int = 1
