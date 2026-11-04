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


def require_any_feature(*features: str):
    """生成路由依赖：任一功能对当前层级开放即放行。

    用于拆分后仍被多个功能共用的端点（如测序分析记录的查看/导出/删除，
    单样品与批量分析都会用到）——只有全部相关功能都关闭才拒绝。
    """
    for f in features:
        if f not in FEATURE_REGISTRY:
            raise ValueError(f"未知功能键: {f}")

    async def _dep(user: Optional[User] = Depends(get_current_user)) -> None:
        site = site_settings.get_settings()
        if not any(is_feature_allowed_for_user(site, user, f) for f in features):
            labels = "、".join(FEATURE_REGISTRY[f]["label"] for f in features)
            tier = tier_for(user)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"「{labels}」均未对{_TIER_CN.get(tier, tier)}开放，请联系管理员",
            )

    return _dep
