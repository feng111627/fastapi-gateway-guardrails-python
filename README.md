# FastAPI 网关守护（Gateway Guardrails）

在 FastAPI 项目上实现的一层网关侧访问控制与流量保护。调用方身份与上游服务
均为本地模拟，授权、限流、上游故障三类情况的处理结果稳定且可复现。

## 请求处理链路

`POST /api/data` 上的每个请求依次经过：

1. **身份解析**（`gateway/identity.py`）：从 `x-api-key` 请求头解析调用方。
2. **授权判定**（`gateway/authz.py`）：按身份/角色匹配规则。
3. **速率限制**（`gateway/ratelimit.py`）：固定窗口计数。
4. **并发限制**（`gateway/ratelimit.py`）：并发槽位，请求结束释放。
5. **熔断检查**（`gateway/circuitbreaker.py`）：熔断中直接拒绝。
6. **上游调用**（`gateway/upstream.py`）：本地模拟上游，错误归一化，
   成败结果反馈给熔断器。

任何一步失败都会以可区分的错误码拒绝，响应格式为：

```json
{"error": {"code": "rate_limited", "message": "..."}}
```

## 错误码一览

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `identity_missing` | 401 | 未提供 `x-api-key` |
| `identity_unknown` | 401 | api key 不在注册表中 |
| `authz_denied` | 403 | 无规则命中或命中显式拒绝规则 |
| `authz_conflict` | 403 | 最高优先级规则冲突（allow 与 deny 同级） |
| `rate_limited` | 429 | 超过速率额度 |
| `concurrency_limited` | 429 | 并发槽位已满 |
| `upstream_timeout` | 504 | 上游调用超时 |
| `upstream_business_error` | 502 | 上游返回业务错误 |
| `upstream_internal_error` | 502 | 上游内部异常 |
| `circuit_open` | 503 | 熔断器断开，请求未打到上游 |

## 授权优先级规则

授权规则（`AuthzRule`）按 `caller`（身份）或 `role`（角色）之一匹配，
`priority` 数值越小优先级越高。判定是确定性的，不随机、不静默放行：

1. 收集所有命中 `(调用方, 路由)` 的规则；**没有命中 → 拒绝**
   （`authz_denied`，原因 `no_matching_rule`）。
2. 取 `priority` 数值最小的一批规则为最高优先级。
3. 该批规则 effect 一致 → 以该 effect 为最终结论
   （例如身份级 `deny` 优先级 10 高于角色级 `allow` 优先级 50 时，拒绝生效）。
4. 该批规则 effect 不一致（allow 与 deny 同级）→ **判定为冲突，明确拒绝**
   （`authz_conflict`），响应中列出冲突的规则名。

## 限流规则与配置

限流按 `(调用方, 接口)` 两个维度计数，配置见 `RateLimitPolicy`：

| 字段 | 含义 |
| --- | --- |
| `max_requests` | 固定窗口内最大请求数 |
| `window_seconds` | 窗口长度（秒），窗口过期后计数重置 |
| `max_concurrency` | 同一 (调用方, 接口) 的最大在途并发数 |

- 窗口内额度用完后一律拒绝（`rate_limited`），不透支；计数由 `asyncio`
  锁保护，并发争抢下不超发、不漏计。
- 并发槽位 `acquire`/`release` 配对，请求结束（含出错）必定释放；被拒的
  请求不占槽位，不影响已经通过的请求。

## 熔断规则与配置

熔断器（`CircuitBreaker`）状态机，配置见 `CircuitBreakerPolicy`：

| 字段 | 含义 |
| --- | --- |
| `failure_threshold` | 连续失败达到该次数后断开 |
| `recovery_seconds` | 断开后等待该时长才允许探测 |
| `half_open_max_probes` | 半开状态最多放行的探测请求数 |

状态迁移（确定且可观察）：

- **closed**：正常放行；连续失败达阈值 → **open**。
- **open**：直接拒绝（`circuit_open`），请求不会打到上游；断开满
  `recovery_seconds` 后，下一个请求进入 **half_open**。
- **half_open**：限量放行探测请求；探测成功 → **closed**（计数清零），
  探测失败 → 回到 **open** 并重新计时。

当前熔断状态可通过 `GET /gateway/state` 观察。

## 上游故障归一化

`MockUpstream` 用固定行为或行为脚本（`set_script`）驱动，保证可复现。
`call_upstream_normalized` 把三类故障归一化：超时 → `upstream_timeout`
（504），业务错误 → `upstream_business_error`（502），内部异常（含未预期
异常）→ `upstream_internal_error`（502）。三类失败都会计入熔断器。

## 配置方式

默认配置在 `gateway/config.py` 的 `default_config()`：

- 调用方注册表：`key-alice`（reader+writer）、`key-bob`（reader）、
  `key-carol`（无角色）。
- 授权规则：`deny-bob`（priority 10）优先于 `allow-readers`（priority 50）。
- 限流：10 秒窗口 5 次、最大并发 3。
- 熔断：连续失败 3 次断开，1 秒后允许 1 个探测请求。

自定义配置：构造 `GatewayConfig` 并传给 `main.create_app(config, upstream)`。

## 本地验证

```bash
# 安装依赖
uv sync

# 运行全部测试（-v 输出可看到每条用例覆盖的业务与结果）
uv run pytest -v

# 启动服务（随机端口，也可 uvicorn main:app --port 8000）
uv run python main.py
```

手动验证示例（服务运行在 8000 端口时）：

```bash
# 缺少身份 -> 401 identity_missing
curl -s -X POST localhost:8000/api/data

# alice 被放行 -> 200
curl -s -X POST localhost:8000/api/data -H 'x-api-key: key-alice' -d '{}'

# bob 被身份级 deny 规则拒绝 -> 403 authz_denied
curl -s -X POST localhost:8000/api/data -H 'x-api-key: key-bob' -d '{}'

# carol 无规则命中 -> 403 authz_denied (no_matching_rule)
curl -s -X POST localhost:8000/api/data -H 'x-api-key: key-carol' -d '{}'

# 观察熔断器状态
curl -s localhost:8000/gateway/state
```

## 测试覆盖

| 测试文件 | 覆盖业务 |
| --- | --- |
| `tests/test_authz.py` | 身份缺失/未知、授权优先级、规则冲突、无命中拒绝、判定确定性 |
| `tests/test_ratelimit.py` | 速率限额与窗口重置、按调用方隔离、并发下不超发不漏计、并发槽位释放、被拒请求不影响已通过请求 |
| `tests/test_circuitbreaker.py` | 三类上游故障归一化、熔断触发与熔断期拦截、恢复放行、探测失败重新断开、半开探测限量 |
