"""授权判定测试：优先级、冲突、身份缺失/未知、无命中规则。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.authz import AuthzRule, Authorizer, Effect
from gateway.config import PROXY_ROUTE
from gateway.identity import Caller
from gateway.upstream import MockUpstream
from main import create_app
from tests.conftest import make_config


def error_code(response) -> str:
    return response.json()["error"]["code"]


class TestIdentity:
    """身份解析：缺失或无法判定时明确拒绝，不静默放行。"""

    def test_missing_identity_is_rejected(self, client: TestClient) -> None:
        resp = client.post(PROXY_ROUTE, json={})
        assert resp.status_code == 401
        assert error_code(resp) == "identity_missing"

    def test_unknown_identity_is_rejected(self, client: TestClient) -> None:
        resp = client.post(PROXY_ROUTE, headers={"x-api-key": "key-nobody"}, json={})
        assert resp.status_code == 401
        assert error_code(resp) == "identity_unknown"

    def test_missing_and_unknown_have_distinct_codes(
        self, client: TestClient
    ) -> None:
        missing = client.post(PROXY_ROUTE, json={})
        unknown = client.post(
            PROXY_ROUTE, headers={"x-api-key": "key-nobody"}, json={}
        )
        assert error_code(missing) != error_code(unknown)


class TestAuthorization:
    """授权判定：优先级稳定、冲突明确拒绝、无命中明确拒绝。"""

    def test_allowed_caller_passes(self, client: TestClient) -> None:
        resp = client.post(PROXY_ROUTE, headers={"x-api-key": "key-alice"}, json={})
        assert resp.status_code == 200
        assert resp.json()["caller"] == "alice"

    def test_explicit_deny_beats_lower_priority_allow(
        self, client: TestClient
    ) -> None:
        # bob 同时命中 deny(priority=10) 与 allow(priority=50)，deny 优先
        resp = client.post(PROXY_ROUTE, headers={"x-api-key": "key-bob"}, json={})
        assert resp.status_code == 403
        assert error_code(resp) == "authz_denied"
        assert "deny-bob" in resp.json()["error"]["message"]

    def test_no_matching_rule_is_denied(self, client: TestClient) -> None:
        # carol 没有任何角色，无规则命中，必须明确拒绝
        resp = client.post(PROXY_ROUTE, headers={"x-api-key": "key-carol"}, json={})
        assert resp.status_code == 403
        assert error_code(resp) == "authz_denied"
        assert "no_matching_rule" in resp.json()["error"]["message"]

    def test_conflicting_rules_are_rejected_with_distinct_code(self) -> None:
        # 同一优先级同时命中 allow 与 deny -> 冲突，明确拒绝且原因可区分
        rules = [
            AuthzRule(
                name="allow-alice",
                effect=Effect.ALLOW,
                route=PROXY_ROUTE,
                caller="alice",
                priority=10,
            ),
            AuthzRule(
                name="deny-readers",
                effect=Effect.DENY,
                route=PROXY_ROUTE,
                role="reader",
                priority=10,
            ),
        ]
        app = create_app(make_config(authz_rules=rules), MockUpstream())
        resp = TestClient(app).post(
            PROXY_ROUTE, headers={"x-api-key": "key-alice"}, json={}
        )
        assert resp.status_code == 403
        assert error_code(resp) == "authz_conflict"
        message = resp.json()["error"]["message"]
        assert "allow-alice" in message and "deny-readers" in message

    def test_decision_is_deterministic_across_repeated_calls(self) -> None:
        # 同样的输入重复判定，结论必须稳定一致
        rules = [
            AuthzRule(
                name="deny-bob",
                effect=Effect.DENY,
                route=PROXY_ROUTE,
                caller="bob",
                priority=10,
            ),
            AuthzRule(
                name="allow-readers",
                effect=Effect.ALLOW,
                route=PROXY_ROUTE,
                role="reader",
                priority=50,
            ),
        ]
        authorizer = Authorizer(rules)
        bob = Caller("bob", frozenset({"reader"}))
        decisions = {authorizer.decide(bob, PROXY_ROUTE) for _ in range(20)}
        assert len(decisions) == 1
        assert not decisions.pop().allowed
