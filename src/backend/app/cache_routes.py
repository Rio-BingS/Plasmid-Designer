"""
缓存 API 路由
提供缓存管理接口
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Optional
from app.cache import cache
from app.auth.jwt_auth import get_admin_user

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.get("/stats", dependencies=[Depends(get_admin_user)])
async def get_cache_stats() -> Dict:
    """
    获取缓存统计信息
    
    Returns:
        缓存类型、键数量、内存使用等
    """
    return cache.get_stats()


@router.post("/clear")
async def clear_cache(pattern: str = "*", _: object = Depends(get_admin_user)) -> Dict:
    """
    清除缓存
    
    Args:
        pattern: 缓存键模式，默认清除所有
    
    Returns:
        清除的键数量
    """
    count = cache.backend.clear_pattern(pattern)
    return {
        "cleared": count,
        "pattern": pattern
    }


@router.post("/invalidate/design/{design_id}", dependencies=[Depends(get_admin_user)])
async def invalidate_design_cache(design_id: str) -> Dict:
    """
    使设计缓存失效
    
    Args:
        design_id: 设计任务 ID
    """
    success = cache.invalidate_design(design_id)
    return {"invalidated": success, "design_id": design_id}


@router.post("/invalidate/vector/{vector_id}", dependencies=[Depends(get_admin_user)])
async def invalidate_vector_cache(vector_id: str) -> Dict:
    """
    使载体缓存失效
    
    Args:
        vector_id: 载体 ID
    """
    success = cache.invalidate_vector(vector_id)
    return {"invalidated": success, "vector_id": vector_id}


@router.get("/health")
async def cache_health_check() -> Dict:
    """
    缓存健康检查
    """
    stats = cache.get_stats()
    return {
        "status": "healthy" if stats.get("available") else "degraded",
        "backend": stats.get("type", "unknown"),
        "details": stats
    }
