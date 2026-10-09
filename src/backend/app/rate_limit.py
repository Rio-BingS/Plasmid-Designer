"""
速率限制中间件
防止 API 滥用，支持多种限制策略
"""
import time
import ipaddress
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, Callable
from fastapi import Request, HTTPException, Depends
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import logging
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


class RateLimitExceeded(HTTPException):
    """速率限制超出异常"""
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )


class InMemoryRateLimiter:
    """内存速率限制器"""

    # key 总量上限：防止伪造 IP 无限制造新 key 撑爆内存（KNOWN_ISSUES 1.2/2.2）
    MAX_TRACKED_KEYS = 10_000

    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = Lock()

    def _prune_keys_locked(self, now: float) -> None:
        """key 数量超限时回收内存：先删整段无活跃记录的 key，
        仍超限则淘汰「最近一次活跃时间」最旧的 key。必须在持有 self._lock 时调用。"""
        if len(self._requests) <= self.MAX_TRACKED_KEYS:
            return
        # 超过最长窗口（upload 的 3600s）无活跃记录的 key 已完全过期，可整体删除
        for key in [k for k, ts in self._requests.items() if not ts or ts[-1] <= now - 3600]:
            del self._requests[key]
        if len(self._requests) > self.MAX_TRACKED_KEYS:
            overflow = len(self._requests) - self.MAX_TRACKED_KEYS
            ordered = sorted(
                self._requests.items(),
                key=lambda item: item[1][-1] if item[1] else 0.0,
            )
            for key, _ in ordered[:overflow]:
                del self._requests[key]
    
    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """
        检查是否允许请求
        
        Args:
            key: 限制键（如 IP 地址或用户 ID）
            max_requests: 时间窗口内最大请求数
            window_seconds: 时间窗口（秒）
        
        Returns:
            (是否允许, 剩余秒数)
        """
        now = time.time()
        window_start = now - window_seconds
        
        with self._lock:
            # 清理过期记录
            self._requests[key] = [
                ts for ts in self._requests[key] 
                if ts > window_start
            ]
            
            # 检查是否超过限制
            if len(self._requests[key]) >= max_requests:
                oldest = min(self._requests[key])
                retry_after = int(oldest + window_seconds - now) + 1
                return False, retry_after
            
            # 记录请求
            self._requests[key].append(now)
            self._prune_keys_locked(now)
            return True, 0
    
    def get_remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        """获取剩余请求次数"""
        now = time.time()
        window_start = now - window_seconds
        
        with self._lock:
            self._requests[key] = [
                ts for ts in self._requests[key] 
                if ts > window_start
            ]
            return max(0, max_requests - len(self._requests[key]))


class RedisRateLimiter:
    """Redis 速率限制器（分布式）"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """使用 Redis 滑动窗口算法"""
        try:
            now = time.time()
            window_start = now - window_seconds
            
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, window_seconds)
            results = pipe.execute()
            
            count = results[1]
            if count >= max_requests:
                # 获取最早请求时间
                oldest = self.redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    retry_after = int(oldest[0][1] + window_seconds - now) + 1
                    return False, retry_after
                return False, window_seconds
            
            return True, 0
        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            # Redis 失败时允许请求
            return True, 0


# 限制策略配置
RATE_LIMITS = {
    # API 端点限制
    "default": {"requests": 100, "window": 60},  # 100 请求/分钟
    "design": {"requests": 10, "window": 60},    # 10 设计任务/分钟
    "batch": {"requests": 3, "window": 60},      # 3 批量任务/分钟
    "upload": {"requests": 20, "window": 3600},  # 20 上传/小时
    "auth": {"requests": 5, "window": 60},       # 5 登录尝试/分钟
    
    # 用户级别限制（已登录用户更高配额）
    # 生效条件：request.state.user 由 AuthStateMiddleware（app/auth/middleware.py）
    # 从 Bearer Token 解析写入；匿名请求仍按 IP 维度限流。
    "user_default": {"requests": 200, "window": 60},
    "user_design": {"requests": 30, "window": 60},
    "user_batch": {"requests": 10, "window": 60},
}

# 全局限制器实例
limiter = InMemoryRateLimiter()


@lru_cache(maxsize=4096)
def _is_trusted_proxy(host: str) -> bool:
    """socket 直连对端为回环/私网地址时视为可信前置代理（nginx 位于 docker 内网/本机）。

    公网客户端直连后端时，其自带的 X-Real-IP / X-Forwarded-For 一律不采信，
    防止伪造请求头获得全新限流 key 绕过限流（KNOWN_ISSUES 1.2）。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def get_client_ip(request: Request) -> str:
    """获取客户端 IP（限流维度）。

    仅当对端是可信内网代理时才采信 X-Real-IP——nginx.conf 中
    `proxy_set_header X-Real-IP $remote_addr` 强制覆写，客户端无法伪造；
    X-Forwarded-For 只取最后一段（由本机代理追加的那段）。
    对端为公网地址时直接使用 socket 对端地址。
    """
    client_host = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(client_host):
        return client_host

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        try:
            ipaddress.ip_address(real_ip)
            return real_ip
        except ValueError:
            pass  # 非法值视为未提供，继续走 XFF / 对端地址

    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return client_host


def get_user_id(request: Request) -> Optional[str]:
    """获取用户 ID（如果已登录）"""
    user = getattr(request.state, "user", None)
    if user:
        return str(user.get("id", "anonymous"))
    return None


def get_rate_limit_key(request: Request, endpoint: str) -> str:
    """生成速率限制键"""
    user_id = get_user_id(request)
    ip = get_client_ip(request)
    
    if user_id:
        return f"rate:user:{user_id}:{endpoint}"
    return f"rate:ip:{ip}:{endpoint}"


# ==================== 路由级限流分类（终审 C-04） ====================
# 规则表：(HTTP 方法, 路径模板) → 限流档位。模板里 {param} 之后用前缀
# 匹配（/api/design/ 覆盖 /api/design/{id}）。
_ENDPOINT_RULES: List[Tuple[str, str, str]] = [
    # 测序上传（含批量）
    ("POST", "/api/sequencing/analyze-batch", "upload"),
    ("POST", "/api/sequencing/analyze", "upload"),
    ("POST", "/api/designs/{id}/sequencing/analyze", "upload"),
    ("POST", "/api/vectors/{id}/sequencing/analyze", "upload"),
    # 批量设计
    ("POST", "/api/design/batch", "batch"),
    # 单条设计
    ("POST", "/api/design", "design"),
    # 认证写操作（登录爆破面）
    ("POST", "/api/auth/login", "auth"),
    ("POST", "/api/auth/register", "auth"),
    ("POST", "/api/auth/verify", "auth"),
    ("POST", "/api/auth/resend-code", "auth"),
    # 其余上传/导入类写操作
    ("POST", "/api/vectors/import", "upload"),
]


def classify_endpoint(method: str, path: str) -> str:
    """按（方法 + 路径模板）判定限流档位。

    只读请求（GET/HEAD/OPTIONS）一律走宽松的 default 档——轮询设计进度、
    拉取峰图、checkAuth 都是高频只读，计入业务写配额会让用户在提交一次
    后就被 429 卡死（终审 C-04 实测：匿名 POST /api/design 后第 10 次轮询
    429；GET /api/auth/verify 第 6 次 429 → 前端 catch 直接 clearAuth
    把用户强制登出）。
    """
    method = (method or "GET").upper()
    for rule_method, template, bucket in _ENDPOINT_RULES:
        if rule_method != method:
            continue
        prefix = template.split("{")[0]
        if path == template or path.startswith(prefix):
            return bucket
    # 未命中规则表：只读一律 default，写操作按路径回退业务档
    if method in ("GET", "HEAD", "OPTIONS"):
        return "default"
    if "/sequencing" in path:
        return "upload"
    if "/design" in path:
        return "design"
    if "/auth" in path:
        return "auth"
    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""
    
    def __init__(self, app, limits: Dict = None):
        super().__init__(app)
        self.limits = limits or RATE_LIMITS
    
    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和静态文件
        if request.url.path in ["/health", "/"] or request.url.path.startswith(("/static", "/assets")):
            return await call_next(request)

        # 终审 C-05：预检 OPTIONS 不计入限流配额（浏览器对跨域预检无法重试，
        # 计入配额会让正常前端被 429 卡死；429 响应本身已由最外层 CORS 补头）
        if request.method == "OPTIONS":
            return await call_next(request)

        # 确定端点类型
        endpoint = self._get_endpoint_type(request)
        limit_config = self.limits.get(endpoint, self.limits["default"])
        
        # 检查用户级别限制
        user_id = get_user_id(request)
        if user_id:
            user_endpoint = f"user_{endpoint}"
            if user_endpoint in self.limits:
                limit_config = self.limits[user_endpoint]
        
        # 生成限制键
        key = get_rate_limit_key(request, endpoint)
        
        # 检查限制
        allowed, retry_after = limiter.is_allowed(
            key,
            limit_config["requests"],
            limit_config["window"]
        )
        
        if not allowed:
            logger.warning(f"Rate limit exceeded: {key}")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    "retry_after": retry_after
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit_config["requests"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after)
                }
            )
        
        # 添加限制头
        response = await call_next(request)
        
        remaining = limiter.get_remaining(
            key,
            limit_config["requests"],
            limit_config["window"]
        )
        
        response.headers["X-RateLimit-Limit"] = str(limit_config["requests"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + limit_config["window"])
        
        return response
    
    # 终审 C-04：此前按路径子串分类——轮询 GET /api/design/{id} 被 "/design"
    # 命中写档（实测提交后第 10 次轮询即 429），GET /api/auth/verify、/auth/me
    # 也计入 auth 档（第 6 次 429），前端 checkAuth 收到 429 就清会话 →
    # 用户被强制登出。改为按（HTTP 方法 + 路由模板）匹配：只读端点统一走
    # 宽松的 default 档，只有真正的写操作才计入业务配额。
    def _get_endpoint_type(self, request: Request) -> str:
        """按（方法 + 路由模板）确定限流档位

        匹配顺序：先查精确的 (method, path) 特殊规则，再按路由模板前缀
        匹配，最后回退 method 判定（写操作按业务档、只读一律 default）。
        """
        return classify_endpoint(request.method, request.url.path)


# 依赖注入：用于单个路由的速率限制
def rate_limit(endpoint: str = "default"):
    """速率限制依赖"""
    async def dependency(request: Request):
        limit_config = RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
        key = get_rate_limit_key(request, endpoint)
        
        allowed, retry_after = limiter.is_allowed(
            key,
            limit_config["requests"],
            limit_config["window"]
        )
        
        if not allowed:
            raise RateLimitExceeded(retry_after)
        
        return True
    
    return dependency


# 装饰器版本
def rate_limited(endpoint: str = "default"):
    """速率限制装饰器"""
    def decorator(func):
        async def wrapper(*args, request: Request = None, **kwargs):
            limit_config = RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
            key = get_rate_limit_key(request, endpoint)
            
            allowed, retry_after = limiter.is_allowed(
                key,
                limit_config["requests"],
                limit_config["window"]
            )
            
            if not allowed:
                raise RateLimitExceeded(retry_after)
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator
