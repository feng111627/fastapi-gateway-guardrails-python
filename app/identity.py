"""调用方身份解析：从请求头解析本地模拟的调用方身份。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.errors import ErrorCode


class IdentityError(Exception):
    """身份解析失败，携带可区分的错误码。"""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class CallerIdentity:
    """调用方身份：caller 标识 + 角色集合。"""

    caller: str
    roles: frozenset[str]


CALLER_HEADER = "x-caller-id"
ROLES_HEADER = "x-caller-roles"


def parse_identity(request: Request) -> CallerIdentity:
    """从请求头解析身份。缺失或非法时抛 IdentityError，绝不静默放行。

    头部约定：
    - X-Caller-Id: 调用方标识，必填，非空
    - X-Caller-Roles: 逗号分隔的角色列表，可空（空表示无角色）
    """
    caller = request.headers.get(CALLER_HEADER)
    if caller is None:
        raise IdentityError(ErrorCode.IDENTITY_MISSING, "missing X-Caller-Id header")
    caller = caller.strip()
    if not caller:
        raise IdentityError(ErrorCode.IDENTITY_INVALID, "empty X-Caller-Id header")

    raw_roles = request.headers.get(ROLES_HEADER, "")
    roles = frozenset(r.strip() for r in raw_roles.split(",") if r.strip())
    return CallerIdentity(caller=caller, roles=roles)
