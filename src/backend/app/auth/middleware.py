"""认证状态中间件

尽力解析登录 JWT（Bearer 头或会话 Cookie）并将用户信息写入 request.state.user，
供下游中间件（速率限制的 user_* 配额）与路由读取。

职责边界：只做令牌签名与有效期校验，不查数据库、不拒绝任何请求——
鉴权与用户存在性校验仍是 get_current_user / get_current_user_required 的职责。

挂载顺序：必须通过 add_middleware 添加在 RateLimitMiddleware 之后
（后添加者为外层，先于其执行），用户级限流才能看到 request.state.user。
"""

import logging
from typing import Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class AuthStateMiddleware(BaseHTTPMiddleware):
    """解析登录令牌，将最小用户信息注入 request.state.user。"""

    async def dispatch(self, request: Request, call_next):
        user = self._resolve_user(request)
        if user is not None:
            request.state.user = user
        return await call_next(request)

    @staticmethod
    def _resolve_user(request: Request) -> Optional[Dict]:
        try:
            # 函数内导入：jwt_auth 依赖数据库模块，避免其随中间件被提前加载
            from app.auth.jwt_auth import decode_token, extract_token

            # Bearer 头优先，其次浏览器会话 Cookie
            token, _source = extract_token(request)
            if not token:
                return None
            token_data = decode_token(token)
        except Exception:  # pragma: no cover - 防御性兜底
            return None

        if token_data is None or not token_data.user_id:
            return None

        # 仅含限流/追踪所需的最小字段；不做数据库校验，
        # 已注销用户残留的有效令牌至多命中其限流键，不产生鉴权效果
        return {"id": token_data.user_id, "email": token_data.email}


# ==================== CSRF 防护 ====================

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "XMLHttpRequest"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 凭据换令牌的端点不读取现有会话 Cookie，不在检查范围：否则 Cookie
# 罐里残留旧会话的脚本客户端（requests.Session 等）会连重新登录都被拒
_CSRF_EXEMPT_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
})


class CsrfMiddleware(BaseHTTPMiddleware):
    """Cookie 认证的写请求必须带 X-Requested-With: XMLHttpRequest。

    会话 Cookie 会被浏览器自动附带，跨站页面可以借它发起表单 POST；
    自定义请求头则只有同源脚本（或通过 CORS 预检的白名单来源）才能
    设置——跨站表单/图片/导航都带不上。SameSite=Lax 已挡住大部分跨站
    POST，这里再兜底一层（同站子域、旧浏览器）。

    仅在「请求带会话 Cookie 且没有 Bearer 头」时检查：Bearer 头本身
    就无法被跨站页面伪造，API / 脚本客户端不受影响。
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS and self._needs_check(request):
            if request.headers.get(CSRF_HEADER, "") != CSRF_HEADER_VALUE:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 校验失败：Cookie 会话的写请求须携带 "
                                       f"{CSRF_HEADER}: {CSRF_HEADER_VALUE} 请求头"},
                )
        return await call_next(request)

    @staticmethod
    def _needs_check(request: Request) -> bool:
        from app.auth.jwt_auth import AUTH_COOKIE_NAME

        if request.url.path.rstrip("/") in _CSRF_EXEMPT_PATHS:
            return False
        if not request.cookies.get(AUTH_COOKIE_NAME):
            return False
        auth = request.headers.get("Authorization", "")
        return not (auth.lower().startswith("bearer ") and auth[7:].strip())
