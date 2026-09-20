"""
速率限制 API 路由
提供速率限制管理和查询接口
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, List
from app.rate_limit import limiter, RATE_LIMITS, get_rate_limit_key, get_client_ip
from app.auth.jwt_auth import get_admin_user

router = APIRouter(prefix="/api/rate-limit", tags=["rate-limit"])


@router.get("/status", dependencies=[Depends(get_admin_user)])
async def get_rate_limit_status(request: Request) -> Dict:
    """
    获取当前请求的速率限制状态
    
    Returns:
        各端点的限制配置和剩余次数
    """
    ip = get_client_ip(request)
    status = {"client_ip": ip, "endpoints": {}}
    
    for endpoint, config in RATE_LIMITS.items():
        if endpoint.startswith("user_"):
            continue
        
        key = get_rate_limit_key(request, endpoint)
        remaining = limiter.get_remaining(
            key,
            config["requests"],
            config["window"]
        )
        
        status["endpoints"][endpoint] = {
            "limit": config["requests"],
            "window_seconds": config["window"],
            "remaining": remaining
        }
    
    return status


@router.get("/config", dependencies=[Depends(get_admin_user)])
async def get_rate_limit_config() -> Dict:
    """
    获取速率限制配置
    
    Returns:
        所有限制策略配置
    """
    return {
        "limits": RATE_LIMITS,
        "description": {
            "default": "全局默认限制",
            "design": "设计任务限制",
            "batch": "批量任务限制",
            "upload": "文件上传限制",
            "auth": "认证请求限制"
        }
    }


@router.get("/health")
async def rate_limit_health() -> Dict:
    """速率限制健康检查"""
    return {
        "status": "healthy",
        "backend": "memory"
    }
