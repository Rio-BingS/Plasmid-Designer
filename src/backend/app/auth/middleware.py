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
