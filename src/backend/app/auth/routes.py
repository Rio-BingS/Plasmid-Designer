"""
用户认证 API 路由（数据库版本）
统一使用数据库存储，prefix=/api/auth

注册流程按站点设置（site_settings）分流：
- registration_open=False                    → 403 拒绝注册
- email_verification_required=False          → 注册即登录（历史行为）
- email_verification_required=True           → 创建未验证账号 + 发送 6 位验证码，
  凭 verify_token 完成 /verify-email 换取登录令牌（/resend-verification 可重发）
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from .jwt_auth import (
    UserCreate, UserLogin, User, Token,
    verify_password, create_access_token, create_verify_token, decode_verify_token,
    hash_password, get_current_user, get_current_user_required, db_user_to_user
)
from app.config import settings
from app.database import (
    get_db, get_users, create_user as db_create_user, get_user_by_email, get_user_by_id,
    get_site_settings_row, create_email_verification, get_latest_email_verification,
    consume_email_verification, update_user,
)
from app.features import features_for_tier, tier_for
from app.mailer import send_email, verification_email_html

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

CODE_TTL_MINUTES = 10
RESEND_COOLDOWN_SECONDS = 60


class RegisterResponse(BaseModel):
    """注册响应：验证开启时返回 verify_token（无 access_token），关闭时等价 Token"""
    access_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = 24 * 3600
    user: Optional[User] = None
    requires_verification: bool = False
    verify_token: Optional[str] = None
    email: Optional[str] = None
    mail_sent: bool = False


class VerifyEmailRequest(BaseModel):
    verify_token: str
    code: str


class ResendVerificationRequest(BaseModel):
    verify_token: str


class SiteConfigResponse(BaseModel):
    """公开站点配置：前端据此隐藏功能入口、控制注册表单"""
    registration_open: bool
    email_verification_required: bool
    tier: str  # anonymous / user / admin
    features: dict  # {"anonymous": [...], "user": [...]}
    effective_features: list  # 按当前请求者层级（管理员全量）


# ==================== 内部工具 ====================

def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str, user_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{code}:{settings.SECRET_KEY}".encode()).hexdigest()


async def _issue_verification_code(db: Session, user, is_resend: bool) -> bool:
    """生成并（尝试）发送验证码，返回是否发送成功。60 秒重发冷却。"""
    latest = get_latest_email_verification(db, user.id)
    if latest is not None and (datetime.utcnow() - latest.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        wait = RESEND_COOLDOWN_SECONDS - (datetime.utcnow() - latest.created_at).total_seconds()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"验证码发送过于频繁，请 {int(wait) + 1} 秒后再试",
        )
    code = _generate_code()
    create_email_verification(
        db, user_id=user.id, email=user.email,
        code_hash=_hash_code(code, user.id),
        expires_at=datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    sent = await send_email(
        user.email,
        "Plasmid Designer 注册验证码",
        verification_email_html(code, CODE_TTL_MINUTES),
    )
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="验证码邮件发送失败，请稍后重试或联系管理员检查邮件服务配置",
        )
    return True


# ==================== 端点 ====================

@router.post("/register", response_model=RegisterResponse)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    用户注册

    - 站点可关闭注册
    - 开启邮箱验证时：创建未验证账号并发送 6 位验证码
    - 未开启时：创建账号并直接返回认证令牌（历史行为）
    """
    site = _row_to_settings(get_site_settings_row(db))
    if not site["registration_open"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="站点已关闭注册，请联系管理员")

    # 检查邮箱是否已注册
    existing_user = get_user_by_email(db, user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已注册"
        )

    # 验证密码
    if user_data.password != user_data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两次输入的密码不一致"
        )

    # 密码强度检查
    if len(user_data.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码长度至少8位"
        )

    hashed_pw = hash_password(user_data.password)

    if not site["email_verification_required"]:
        db_user = db_create_user(
            db=db, email=user_data.email, username=user_data.username,
            hashed_password=hashed_pw, email_verified=True,
        )
        user = db_user_to_user(db_user)
        return RegisterResponse(
            access_token=create_access_token(user), expires_in=24 * 3600, user=user)

    # 开启邮箱验证：先建未验证账号，再发码
    db_user = db_create_user(
        db=db, email=user_data.email, username=user_data.username,
        hashed_password=hashed_pw, email_verified=False,
    )
    code = _generate_code()
    create_email_verification(
        db, user_id=db_user.id, email=db_user.email,
        code_hash=_hash_code(code, db_user.id),
        expires_at=datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    sent = await send_email(
        db_user.email, "Plasmid Designer 注册验证码",
        verification_email_html(code, CODE_TTL_MINUTES),
    )
    return RegisterResponse(
        requires_verification=True,
        verify_token=create_verify_token(db_user.id),
        email=db_user.email,
        mail_sent=sent,
    )


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """
    用户登录

    - 验证邮箱和密码
    - 开启邮箱验证时，未验证账号返回 verify_token 引导补验证
    """
    # 查找用户
    user = get_user_by_email(db, credentials.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误"
        )

    # 验证密码
    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误"
        )

    # 检查用户状态
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )

    # 邮箱验证开关打开时拦截未验证账号（带 verify_token 供前端引导补验证）
    site = _row_to_settings(get_site_settings_row(db))
    if site["email_verification_required"] and not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "EMAIL_NOT_VERIFIED",
                "message": "邮箱尚未验证，请输入注册时收到的验证码完成验证",
                "email": user.email,
                "verify_token": create_verify_token(user.id),
            },
        )

    # 生成令牌
    user_response = db_user_to_user(user)
    access_token = create_access_token(user_response)

    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=24 * 3600,
        user=user_response
    )


@router.post("/verify-email", response_model=Token)
async def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    """校验验证码完成注册：成功即视为邮箱已验证并直接登录"""
    user_id = decode_verify_token(payload.verify_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="验证链接已过期，请重新注册或登录后重发验证码")

    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户不存在或已被禁用")

    if user.email_verified:
        user_response = db_user_to_user(user)
        return Token(access_token=create_access_token(user_response),
                     expires_in=24 * 3600, user=user_response)

    row = get_latest_email_verification(db, user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先获取验证码")
    if row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="验证码已过期，请重新发送")
    if _hash_code(payload.code.strip(), user_id) != row.code_hash:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误")

    consume_email_verification(db, row)
    update_user(db, user_id, email_verified=True)
    user_response = db_user_to_user(get_user_by_id(db, user_id))
    return Token(access_token=create_access_token(user_response),
                 expires_in=24 * 3600, user=user_response)


@router.post("/resend-verification")
async def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    """重发注册验证码（60 秒冷却）"""
    user_id = decode_verify_token(payload.verify_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证链接已过期，请重新注册")
    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户不存在或已被禁用")
    if user.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该账号已完成验证，请直接登录")
    await _issue_verification_code(db, user, is_resend=True)
    return {"message": "验证码已重新发送", "email": user.email,
            "verify_token": create_verify_token(user.id)}


@router.get("/site-config", response_model=SiteConfigResponse)
async def site_config(current_user: Optional[User] = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """公开站点配置：注册开关、邮箱验证开关、各级别可用功能（前端渲染依据）"""
    site = _row_to_settings(get_site_settings_row(db))
    tier = tier_for(current_user)
    return SiteConfigResponse(
        registration_open=site["registration_open"],
        email_verification_required=site["email_verification_required"],
        tier=tier,
        features={
            "anonymous": site["anonymous_features"],
            "user": site["user_features"],
        },
        effective_features=features_for_tier(site, tier),
    )


def _row_to_settings(row) -> dict:
    """site_settings 单行 → dict（空清单回退默认，保持旧行为）"""
    import json as _json
    from app.features import DEFAULT_ANONYMOUS_FEATURES, DEFAULT_USER_FEATURES, valid_features

    anon = valid_features(_json.loads(row.anonymous_features or "[]"))
    user_f = valid_features(_json.loads(row.user_features or "[]"))
    return {
        "registration_open": bool(row.registration_open),
        "email_verification_required": bool(row.email_verification_required),
        "anonymous_features": anon or list(DEFAULT_ANONYMOUS_FEATURES),
        "user_features": user_f or list(DEFAULT_USER_FEATURES),
    }


@router.get("/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_user_required)):
    """获取当前用户信息"""
    return current_user


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user_required)):
    """
    用户登出

    注意：JWT 是无状态的，真正的登出需要服务端维护黑名单
    这里只返回成功消息，客户端应删除本地令牌
    """
    return {"message": "登出成功"}


@router.get("/verify")
async def verify_token(current_user: User = Depends(get_current_user)):
    """
    验证令牌有效性

    - 返回用户信息如果令牌有效
    - 返回 null 如果令牌无效或未提供
    """
    if current_user:
        return {"valid": True, "user": current_user}
    return {"valid": False, "user": None}


@router.get("/users", response_model=list[User])
async def list_users(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    """列出所有用户（管理员）"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    users = get_users(db)
    return [db_user_to_user(u) for u in users]
