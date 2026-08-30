"""
速率限制中间件
防止 API 滥用，支持多种限制策略
"""
import time
from typing import Dict, Optional, Callable
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
    
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = Lock()
    
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


def get_client_ip(request: Request) -> str:
    """获取客户端 IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""
    
    def __init__(self, app, limits: Dict = None):
        super().__init__(app)
        self.limits = limits or RATE_LIMITS
    
    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和静态文件
        if request.url.path in ["/health", "/"] or request.url.path.startswith(("/static", "/assets")):
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
    
    def _get_endpoint_type(self, request: Request) -> str:
        """根据路径确定端点类型"""
        path = request.url.path
        
        if "/design/batch" in path:
            return "batch"
        elif "/design" in path:
            return "design"
        elif "/upload" in path or "/import" in path:
            return "upload"
        elif "/auth" in path:
            return "auth"
        return "default"


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
