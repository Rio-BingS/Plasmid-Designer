"""站点管理 API（仅管理员）prefix=/api/admin

- 站点设置：注册开关、邮箱验证开关、各层级功能开关
- 用户管理：列表、角色/状态/验证标记、删除
自我保护：不能禁用或删除自己；不能降级/删除最后一位管理员。
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth.jwt_auth import get_admin_user, User
from app.config import settings
from app.database import (
    get_db, get_users, get_user_by_id, update_user, delete_user, count_admins,
)
from app.features import FEATURE_REGISTRY
from app import site_settings

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_admin_user)])


# ==================== 模型 ====================

class AdminSettingsUpdate(BaseModel):
    registration_open: Optional[bool] = None
    email_verification_required: Optional[bool] = None
    anonymous_features: Optional[List[str]] = None
    user_features: Optional[List[str]] = None


class AdminSettingsResponse(BaseModel):
    registration_open: bool
    email_verification_required: bool
    anonymous_features: List[str]
    user_features: List[str]
    feature_keys: List[str]  # 全部可配置功能（key -> label 用 feature_labels）
    feature_labels: dict
    mail_provider: str  # 当前发信渠道（只读，配置在环境变量/.env）
    mail_from: str


class AdminUserUpdate(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    email_verified: Optional[bool] = None


class AdminUserInfo(BaseModel):
    id: str
    email: str
    username: str
    is_admin: bool
    is_active: bool
    email_verified: bool
    created_at: Optional[datetime] = None


# ==================== 站点设置 ====================

@router.get("/settings", response_model=AdminSettingsResponse)
async def read_settings(db: Session = Depends(get_db)):
    s = site_settings.get_settings()
    return AdminSettingsResponse(
        registration_open=s["registration_open"],
        email_verification_required=s["email_verification_required"],
        anonymous_features=s["anonymous_features"],
        user_features=s["user_features"],
        feature_keys=list(FEATURE_REGISTRY.keys()),
        feature_labels={k: v["label"] for k, v in FEATURE_REGISTRY.items()},
        mail_provider=settings.MAIL_PROVIDER,
        mail_from=settings.MAIL_FROM,
    )


@router.put("/settings", response_model=AdminSettingsResponse)
async def write_settings(patch: AdminSettingsUpdate):
    s = site_settings.update_settings(patch.model_dump(exclude_none=True))
    return AdminSettingsResponse(
        registration_open=s["registration_open"],
        email_verification_required=s["email_verification_required"],
        anonymous_features=s["anonymous_features"],
        user_features=s["user_features"],
        feature_keys=list(FEATURE_REGISTRY.keys()),
        feature_labels={k: v["label"] for k, v in FEATURE_REGISTRY.items()},
        mail_provider=settings.MAIL_PROVIDER,
        mail_from=settings.MAIL_FROM,
    )


# ==================== 用户管理 ====================

def _to_info(u) -> AdminUserInfo:
    return AdminUserInfo(
        id=u.id, email=u.email, username=u.username,
        is_admin=u.is_admin, is_active=u.is_active,
        email_verified=bool(getattr(u, "email_verified", True)),
        created_at=u.created_at,
    )


@router.get("/users", response_model=List[AdminUserInfo])
async def list_all_users(db: Session = Depends(get_db)):
    return [_to_info(u) for u in get_users(db, limit=1000)]


@router.put("/users/{user_id}", response_model=AdminUserInfo)
async def modify_user(user_id: str, patch: AdminUserUpdate,
                      admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    target = get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    if patch.is_active is False and target.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用自己的账号")
    # 降级/删除最后一位在任管理员会导致管理面板永久失守
    losing_admin = (target.is_admin and patch.is_admin is False) or (target.is_admin and patch.is_active is False)
    if losing_admin and count_admins(db) <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能移除最后一位管理员")

    fields = patch.model_dump(exclude_none=True)
    updated = update_user(db, user_id, **fields)
    return _to_info(updated)


@router.delete("/users/{user_id}")
async def remove_user(user_id: str, admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己的账号")
    target = get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target.is_admin and count_admins(db) <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除最后一位管理员")
    delete_user(db, user_id)
    return {"message": "用户已删除"}
