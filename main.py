"""本地启动入口：用默认配置与本地模拟上游启动网关。"""

import uvicorn

from app.authz import AuthzRule, Effect
from app.config import GuardrailSettings
from app.gateway import create_app
from app.upstream import FakeUpstream

upstream = FakeUpstream()
upstream.script("ok")

app = create_app(
    settings=GuardrailSettings(),
    upstream=upstream,
    rules=[
        AuthzRule(name="admins", effect=Effect.ALLOW, priority=100, role="admin"),
        AuthzRule(name="readers", effect=Effect.ALLOW, priority=10, role="reader",
                  path_prefix="/proxy/public"),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
