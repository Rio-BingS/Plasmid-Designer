"""API 功能门控：把「某层级能不能用某功能」的站点设置落到 FastAPI 依赖上

用法（main.py）：app.include_router(design_router, dependencies=[Depends(require_feature("design"))])
管理员不受限制；后端拒绝前前端已按 site-config 隐藏入口，这里兜底防直连 API。
"""

from typing import Optional

from fastapi import Depends, HTTPException, status

from app.auth.jwt_auth import User, get_current_user
from app.features import FEATURE_REGISTRY, is_feature_allowed_for_user, tier_for
from app import site_settings

_TIER_CN = {"anonymous": "未登录访客", "user": "普通用户", "admin": "管理员"}


def require_feature(feature: str):
    """生成路由依赖：站点设置未对当前层级开放该功能时返回 403"""
    if feature not in FEATURE_REGISTRY:
        raise ValueError(f"未知功能键: {feature}")

    async def _dep(user: Optional[User] = Depends(get_current_user)) -> None:
        tier = tier_for(user)
        if not is_feature_allowed_for_user(site_settings.get_settings(), user, feature):
            label = FEATURE_REGISTRY[feature]["label"]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"「{label}」未对{_TIER_CN.get(tier, tier)}开放，请联系管理员",
            )

    return _dep
