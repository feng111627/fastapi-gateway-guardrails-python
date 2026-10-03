"""授权判定测试：覆盖优先级、冲突拒绝、身份缺失、默认拒绝。"""

from __future__ import annotations

from app.authz import AuthzRule, Effect
from tests.conftest import (
    DEFAULT_RULES,
    HEADERS_ALICE,
    HEADERS_BOB,
    HEADERS_MALLORY,
)


class TestIdentity:
    """调用方身份解析。"""

    def test_缺少身份头时拒绝并返回401(self, make_client, permissive_settings):
        client, _ = make_client(settings=permissive_settings)
        resp = client.get("/proxy/public/x")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "identity_missing"

    def test_身份头为空串时拒绝并区分原因(self, make_client, permissive_settings):
        client, _ = make_client(settings=permissive_settings)
        resp = client.get("/proxy/public/x", headers={"X-Caller-Id": "   "})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "identity_invalid"


class TestAuthorization:
    """授权规则优先级与冲突。"""

    def test_管理员角色命中高优先级允许规则(self, make_client, permissive_settings):
        client, upstream = make_client(settings=permissive_settings)
        resp = client.get("/proxy/private/data", headers=HEADERS_ALICE)
        assert resp.status_code == 200
        assert upstream.calls == ["/proxy/private/data"]

    def test_reader角色只能访问public前缀(self, make_client, permissive_settings):
        client, _ = make_client(settings=permissive_settings)
        ok = client.get("/proxy/public/doc", headers=HEADERS_BOB)
        assert ok.status_code == 200
        denied = client.get("/proxy/private/doc", headers=HEADERS_BOB)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "authz_no_rule"

    def test_无任何规则命中时默认拒绝(self, make_client, permissive_settings):
        client, upstream = make_client(settings=permissive_settings)
        resp = client.get(
            "/proxy/public/x", headers={"X-Caller-Id": "nobody", "X-Caller-Roles": ""}
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "authz_no_rule"
        assert upstream.calls == []  # 未放行到上游

    def test_高优先级拒绝规则压过低优先级允许(self, make_client, permissive_settings):
        # mallory 同时命中 deny-mallory(100) 与 allow-admin(50)，高优先级 deny 生效
        client, _ = make_client(settings=permissive_settings)
        resp = client.get("/proxy/anything", headers=HEADERS_MALLORY)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "authz_denied"
        assert "deny-mallory" in resp.json()["error"]["detail"]

    def test_同优先级规则冲突时明确拒绝(self, make_client, permissive_settings):
        # 构造同优先级 allow+deny 冲突，必须拒绝且原因可区分，不能随机选一条
        rules = [
            AuthzRule(name="allow-a", effect=Effect.ALLOW, priority=10, caller="carol"),
            AuthzRule(name="deny-a", effect=Effect.DENY, priority=10, caller="carol"),
        ]
        client, upstream = make_client(settings=permissive_settings, rules=rules)
        resp = client.get("/proxy/x", headers={"X-Caller-Id": "carol"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "authz_conflict"
        assert upstream.calls == []

    def test_冲突判定结果稳定可复现(self, make_client, permissive_settings):
        rules = [
            AuthzRule(name="allow-a", effect=Effect.ALLOW, priority=10, caller="carol"),
            AuthzRule(name="deny-a", effect=Effect.DENY, priority=10, caller="carol"),
        ]
        client, _ = make_client(settings=permissive_settings, rules=rules)
        results = {
            client.get("/proxy/x", headers={"X-Caller-Id": "carol"}).json()["error"]["code"]
            for _ in range(5)
        }
        assert results == {"authz_conflict"}

    def test_被拒绝的请求不会消耗后续请求的判定(self, make_client, permissive_settings):
        client, _ = make_client(settings=permissive_settings)
        client.get("/proxy/private/x", headers=HEADERS_BOB)  # 拒绝
        resp = client.get("/proxy/public/x", headers=HEADERS_BOB)  # 仍正常放行
        assert resp.status_code == 200
