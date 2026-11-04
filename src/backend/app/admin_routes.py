"""站点管理 API（仅管理员）prefix=/api/admin

- 站点设置：注册开关、邮箱验证开关、各层级功能开关、各层级批量 read 上限
- 用户管理：列表、角色/状态/验证标记、个人功能权限与 read 上限覆盖、删除
自我保护：不能禁用或删除自己；不能降级/删除最后一位管理员。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt_auth import get_admin_user, User
from app.config import settings
from app.database import (
    get_db, get_users, get_user_by_id, update_user, delete_user, count_admins,
)
from app.features import FEATURE_REGISTRY, valid_features
from app import batch_read_limits as brl
from app import site_settings

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_admin_user)])


# ==================== 模型 ====================

class AdminSettingsUpdate(BaseModel):
    registration_open: Optional[bool] = None
    email_verification_required: Optional[bool] = None
    anonymous_features: Optional[List[str]] = None
    user_features: Optional[List[str]] = None
    # 批量测序 read 上限（按层级）：{tier: {max_reads_per_group, max_total_reads}}，
    # 只改提交了的层级/字段；值为 null 表示清除（回到服务器默认）。取值在路由内
    # 手工校验，以便返回中文 422
    batch_read_limits: Optional[Dict[str, Dict[str, Any]]] = None


class AdminSettingsResponse(BaseModel):
    registration_open: bool
    email_verification_required: bool
    anonymous_features: List[str]
    user_features: List[str]
    feature_keys: List[str]  # 全部可配置功能（key -> label 用 feature_labels）
    feature_labels: dict
    mail_provider: str  # 当前发信渠道（只读，配置在环境变量/.env）
    mail_from: str
    # 批量测序 read 上限：各层级设置（null = 服务器默认）+ 只读的服务器默认、
    # 安全上限与其他可能先触发的请求级限制（供管理面板提示）
    batch_read_limits: Dict[str, Dict[str, Optional[int]]] = {}
    batch_read_limit_defaults: Dict[str, int] = {}
    batch_read_limit_hard_max: Dict[str, int] = {}
    batch_max_files: int = 0
    batch_upload_max_mb: int = 0
    # 分块上传的批次级体积上限（批量页实际生效；上面两项只约束单请求入口）
    batch_session_max_mb: int = 0


class AdminUserUpdate(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    email_verified: Optional[bool] = None
    # None=不改；空数组=清除覆盖（跟随层级默认）；非空=精确覆盖
    allowed_features: Optional[List[str]] = None
    # 批量 read 上限个人覆盖：不传=不改；显式 null=清除覆盖（跟随所在分组）
    batch_max_reads_per_group: Optional[Any] = None
    batch_max_total_reads: Optional[Any] = None


class AdminUserInfo(BaseModel):
    id: str
    email: str
    username: str
    is_admin: bool
    is_active: bool
    email_verified: bool
    allowed_features: Optional[List[str]] = None
    batch_max_reads_per_group: Optional[int] = None
    batch_max_total_reads: Optional[int] = None
    created_at: Optional[datetime] = None


# ==================== 站点设置 ====================

def _settings_response(s: dict) -> AdminSettingsResponse:
    from app import sequencing_upload_sessions as sessions
    from app.routes import sequencing_routes as sr

    return AdminSettingsResponse(
        registration_open=s["registration_open"],
        email_verification_required=s["email_verification_required"],
        anonymous_features=s["anonymous_features"],
        user_features=s["user_features"],
        feature_keys=list(FEATURE_REGISTRY.keys()),
        feature_labels={k: v["label"] for k, v in FEATURE_REGISTRY.items()},
        mail_provider=settings.MAIL_PROVIDER,
        mail_from=settings.MAIL_FROM,
        batch_read_limits=brl.normalize_tier_limits(s.get("batch_read_limits")),
        batch_read_limit_defaults={brl.PER_GROUP: sr.MAX_BATCH_READS_PER_GROUP,
                                   brl.TOTAL: sr.MAX_BATCH_TOTAL_READS},
        batch_read_limit_hard_max={f: brl.hard_max(f) for f in brl.FIELDS},
        batch_max_files=sr.MAX_BATCH_FILES,
        batch_upload_max_mb=sr.MAX_BATCH_REQUEST_BYTES // (1024 * 1024),
        batch_session_max_mb=sessions.SESSION_MAX_BYTES // (1024 * 1024),
    )


def _validated_tier_limits(raw: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Optional[int]]]:
    """校验管理员提交的层级上限；不合法 → 422（中文提示）"""
    out: Dict[str, Dict[str, Optional[int]]] = {}
    for tier, vals in raw.items():
        if tier not in brl.TIERS:
            raise HTTPException(status_code=422, detail=f"未知的用户分组：{tier}")
        if not isinstance(vals, dict):
            raise HTTPException(status_code=422, detail="read 上限格式错误")
        out[tier] = {}
        for field, v in vals.items():
            if field not in brl.FIELDS:
                raise HTTPException(status_code=422, detail=f"未知的 read 上限字段：{field}")
            try:
                out[tier][field] = brl.validate_limit(v, field, f"「{brl.TIER_LABELS[tier]}」分组")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
    return out


@router.get("/settings", response_model=AdminSettingsResponse)
async def read_settings(db: Session = Depends(get_db)):
    return _settings_response(site_settings.get_settings())


@router.put("/settings", response_model=AdminSettingsResponse)
async def write_settings(patch: AdminSettingsUpdate):
    data = patch.model_dump(exclude_none=True)
    if patch.batch_read_limits is not None:
        # exclude_none 会吞掉嵌套的 null（null = 清除该项），单独处理
        data["batch_read_limits"] = _validated_tier_limits(patch.batch_read_limits)
    return _settings_response(site_settings.update_settings(data))


# ==================== 用户管理 ====================

def _to_info(u) -> AdminUserInfo:
    import json as _json

    raw = getattr(u, "allowed_features", None)
    try:
        override = _json.loads(raw) if raw else None
    except (TypeError, ValueError):
        override = None
    return AdminUserInfo(
        id=u.id, email=u.email, username=u.username,
        is_admin=u.is_admin, is_active=u.is_active,
        email_verified=bool(getattr(u, "email_verified", True)),
        allowed_features=override,
        batch_max_reads_per_group=getattr(u, "batch_max_reads_per_group", None),
        batch_max_total_reads=getattr(u, "batch_max_total_reads", None),
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
    # 批量 read 上限个人覆盖：显式 null 清除覆盖（跟随所在分组），故按
    # fields_set 判断「是否提交」，而非 exclude_none
    for field, col in brl.USER_COLUMNS.items():
        fields.pop(col, None)
        if col in patch.model_fields_set:
            try:
                fields[col] = brl.validate_limit(getattr(patch, col), field, "个人")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
    if "allowed_features" in fields:
        # 个人功能权限：清洗未知键；空数组存 NULL 表示清除覆盖（跟随层级默认）
        import json as _json

        cleaned = valid_features(fields["allowed_features"])
        fields["allowed_features"] = _json.dumps(cleaned) if cleaned else None
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
