# FastAPI 网关防护（Guardrails）

在 FastAPI 网关上实现访问控制与流量保护：身份解析、授权判定、速率/并发限制、
上游故障归一化与熔断。上游服务由本地模拟（`FakeUpstream`），行为可脚本化，
授权、限流、熔断三类场景的处理结果稳定且可复现。

## 请求管线

每个 `/proxy/{path}` 请求按固定顺序经过以下环节，任一环节拒绝都会返回
**可区分的错误码**，且不会影响其他请求：

```
身份解析 -> 授权判定 -> 速率限制 -> 并发限制 -> 熔断检查 -> 上游调用
```

### 调用方身份（本地模拟）

通过请求头携带身份：

| 请求头           | 说明                         |
| ---------------- | ---------------------------- |
| `X-Caller-Id`    | 调用方标识，必填，非空       |
| `X-Caller-Roles` | 逗号分隔的角色列表，可为空   |

缺失返回 `401 identity_missing`，空串返回 `401 identity_invalid`，绝不静默放行。

## 授权规则与优先级

规则定义见 `app/authz.py` 的 `AuthzRule`：

```python
AuthzRule(
    name="allow-admin",     # 规则名（用于审计与冲突报告）
    effect=Effect.ALLOW,    # allow / deny
    priority=50,            # 数值越大优先级越高
    caller="alice",         # 限定调用方，None 表示任意
    role="admin",           # 要求具备的角色，None 表示不要求
    path_prefix="/proxy/",  # 限定路径前缀，None 表示任意
)
```

判定逻辑（`authorize()`，确定性、可复现）：

1. 收集所有命中规则；**无命中 -> 默认拒绝** `403 authz_no_rule`。
2. 取**最高 priority** 的规则集合，低优先级规则不参与结论。
3. 该集合中 **allow 与 deny 同时存在 -> 冲突拒绝** `403 authz_conflict`，
   并在 `detail` 中列出冲突规则名；不会随机挑选一条生效。
4. 集合效果唯一时：allow 放行；deny 返回 `403 authz_denied`。

## 限流与并发

配置见 `app/config.py` 的 `GuardrailSettings`：

| 配置项                      | 默认     | 说明                                   |
| --------------------------- | -------- | -------------------------------------- |
| `default_rate_limit`        | 10 次/秒 | 固定窗口速率限制，按 `调用方+接口` 维度 |
| `max_concurrency_per_key`   | 5        | 同一 `调用方+接口` 的最大并发数         |

- 速率超限返回 `429 rate_limited`；并发超限**立即拒绝**（不排队）返回
  `429 concurrency_limited`。
- 额度检查与扣减在锁内完成，并发下不超发、不漏计；被拒绝的请求不消耗额度，
  也不影响已通过/在途请求。
- 授权失败的请求在限流之前被拒绝，不占用任何额度。

## 上游故障归一化与熔断

上游调用的三类故障归一化为对外可区分的错误（`app/errors.py`）：

| 场景           | HTTP | 错误码                 |
| -------------- | ---- | ---------------------- |
| 上游超时       | 504  | `upstream_timeout`     |
| 上游业务错误   | 502  | `upstream_error`       |
| 上游内部异常   | 502  | `upstream_unavailable` |
| 熔断中快速失败 | 503  | `circuit_open`         |

熔断器（`app/circuit_breaker.py`）状态机与配置：

| 配置项                      | 默认  | 说明                               |
| --------------------------- | ----- | ---------------------------------- |
| `breaker_failure_threshold` | 3     | 连续失败达到该次数后打开熔断       |
| `breaker_cooldown_seconds`  | 5.0   | 打开后的冷却时长，冷却结束进入半开 |
| `breaker_half_open_probes`  | 1     | 半开状态允许的探测请求数           |
| `upstream_timeout_seconds`  | 2.0   | 上游调用超时                       |

- **closed**：正常放行；连续失败（成功会清零计数）达阈值 -> **open**。
- **open**：请求快速失败返回 `503 circuit_open`，**不会打到上游**。
- **half_open**：放行至多 `half_open_probes` 个探测请求；探测成功 ->
  closed 恢复放行，探测失败 -> 重新 open 并重新计时冷却。
- 当前熔断状态可通过 `GET /health` 的 `breaker` 字段观察。

## 本地模拟上游

`FakeUpstream` 通过脚本预置行为序列，结果可复现：

```python
upstream = FakeUpstream()
upstream.script("boom", "boom", "boom", "ok")  # 前 3 次异常，之后恢复
# 可选行为：ok(200) / biz_error(422) / timeout(超时) / boom(内部异常)
# 脚本耗尽后重复最后一个行为；upstream.calls 记录实际到达上游的请求
```

## 本地验证

```bash
# 运行全部测试（输出可见每条用例覆盖的业务与结果）
uv run pytest tests/ -v

# 测试覆盖三类关键路径：
#   tests/test_authz.py           授权优先级、冲突拒绝、身份缺失、默认拒绝
#   tests/test_rate_limit.py      并发下额度不超发、维度隔离、并发上限与释放
#   tests/test_circuit_breaker.py 故障归一化、熔断打开/快速失败/半开恢复

# 本地启动网关（默认配置 + 模拟上游）
uv run python main.py

# 手动验证示例
curl -i http://127.0.0.1:8000/proxy/public/doc                              # 401 identity_missing
curl -i -H 'X-Caller-Id: alice' -H 'X-Caller-Roles: admin' \
     http://127.0.0.1:8000/proxy/public/doc                                 # 200
curl -i http://127.0.0.1:8000/health                                        # 查看熔断状态
```

## 目录结构

```
app/
  config.py           防护配置（限流/并发/熔断/超时）
  errors.py           统一错误码与 HTTP 状态映射
  identity.py         调用方身份解析
  authz.py            授权规则与优先级判定
  rate_limit.py       固定窗口限流器 + 并发限制器
  circuit_breaker.py  熔断器状态机
  upstream.py         上游协议 + 本地模拟上游
  gateway.py          请求管线装配
main.py               本地启动入口
tests/                关键路径测试
```
