"""
请求追踪中间件
生成请求 ID、记录请求/响应、性能监控
"""

import time
import uuid
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.logging_config import get_logger

logger = get_logger("request")


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """请求追踪中间件"""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # 生成请求 ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # 记录开始时间
        start_time = time.time()
        
        # 获取客户端信息
        client_host = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("User-Agent", "unknown")
        
        # 记录请求
        logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "client_ip": client_host,
                "user_agent": user_agent[:100]
            }
        )
        
        try:
            # 处理请求
            response = await call_next(request)
            
            # 计算处理时间
            process_time = (time.time() - start_time) * 1000  # 毫秒
            
            # 记录响应
            logger.info(
                f"Request completed: {request.method} {request.url.path} -> {response.status_code}",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "process_time_ms": round(process_time, 2)
                }
            )
            
            # 添加响应头
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = f"{process_time:.2f}ms"
            
            return response
            
        except Exception as e:
            # 记录异常
            process_time = (time.time() - start_time) * 1000
            
            logger.error(
                f"Request failed: {request.method} {request.url.path}",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "process_time_ms": round(process_time, 2),
                    "error": str(e)
                }
            )
            raise


class SlowRequestMiddleware(BaseHTTPMiddleware):
    """慢请求监控中间件"""
    
    SLOW_REQUEST_THRESHOLD_MS = 1000  # 1秒
    
    def __init__(self, app: ASGIApp, threshold_ms: int = None):
        super().__init__(app)
        self.threshold_ms = threshold_ms or self.SLOW_REQUEST_THRESHOLD_MS
    
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()
        
        response = await call_next(request)
        
        process_time = (time.time() - start_time) * 1000
        
        if process_time > self.threshold_ms:
            logger.warning(
                f"Slow request: {request.method} {request.url.path} took {process_time:.2f}ms",
                extra={
                    "process_time_ms": round(process_time, 2),
                    "threshold_ms": self.threshold_ms
                }
            )
        
        return response


def setup_middleware(app):
    """配置中间件"""
    from app.logging_config import get_logger
    
    log = get_logger("middleware")
    
    # 请求追踪
    app.add_middleware(RequestTrackingMiddleware)
    log.info("RequestTrackingMiddleware configured")
    
    # 慢请求监控
    app.add_middleware(SlowRequestMiddleware, threshold_ms=2000)
    log.info("SlowRequestMiddleware configured (threshold: 2000ms)")
