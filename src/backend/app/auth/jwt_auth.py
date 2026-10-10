"""
用户认证模块
- JWT Token 工具
- 密码加密 (bcrypt)
- Pydantic 模型
- 认证依赖函数（基于数据库）
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import jwt

from app.config import settings, validate_secret_key
from app.database import get_db
from app.database.crud import get_user_by_id, get_user_by_email, is_token_revoked

# 配置 — 密钥来自环境变量/配置。导入即校验：占位/过短/低熵密钥直接抛错，
# 不依赖 lifespan，也不因 DEBUG 豁免（签发与验签都用到它，无从绕过）
SECRET_KEY = settings.SECRET_KEY
validate_secret_key(SECRET_KEY)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# 密码加密
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer Token 认证（API / 脚本客户端）；浏览器走 httpOnly Cookie
security = HTTPBearer(auto_error=False)

# 浏览器会话 Cookie：httpOnly（脚本读不到，XSS 偷不走令牌）、SameSite=Lax、
# Path=/api（只随 API 请求发送）。有效期与令牌一致
AUTH_COOKIE_NAME = "pd_session"
AUTH_COOKIE_PATH = "/api"
AUTH_COOKIE_MAX_AGE = ACCESS_TOKEN_EXPIRE_HOURS * 3600


# ==================== 模型 ====================

class UserBase(BaseModel):
    """用户基础模型"""
    email: EmailStr
    username: str


class UserCreate(UserBase):
    """用户注册请求"""
    password: str
    confirm_password: str


class UserLogin(BaseModel):
    """用户登录请求"""
    email: EmailStr
    password: str


class User(UserBase):
    """用户完整信息"""
    id: str
    is_active: bool = True
    is_admin: bool = False
    email_verified: bool = True
    # 个人功能权限覆盖（None = 跟随层级默认；键见 features.FEATURE_REGISTRY）
    allowed_features: Optional[list] = None
    created_at: Optional[datetime] = None


class Token(BaseModel):
    """认证令牌"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User


class TokenData(BaseModel):
    """令牌数据"""
    user_id: Optional[str] = None
    email: Optional[str] = None
    # 令牌唯一 ID（登出黑名单键；旧版令牌无此声明）
    jti: Optional[str] = None
    # 签发时的用户令牌版本（旧版令牌无此声明，按 0 处理）
    token_version: int = 0
    expires_at: Optional[datetime] = None


# ==================== 密码工具 ====================

# bcrypt 只取输入的前 72 字节：bcrypt 4.x 会静默截断，5.x 直接抛错。
# 显式截断让两端语义一致，且注册时即确定边界——73 字节起的长密码
# 不会被静默砍到与 72 字节前缀等价而不留痕
_BCRYPT_MAX_PASSWORD_BYTES = 72


def _clamp_password(password: str) -> str:
    """把密码截到 bcrypt 实际参与哈希的前 72 字节（按 UTF-8 字节计）"""
    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES].decode(
        "utf-8", errors="ignore"
    )


def hash_password(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(_clamp_password(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(_clamp_password(plain_password), hashed_password)


# ==================== JWT 工具 ====================

def create_access_token(user: User, token_version: int = 0) -> str:
    """生成访问令牌。

    jti：每枚令牌唯一，服务端登出按它吊销单个会话；
    tv：签发时的用户令牌版本，禁用/改密递增后旧令牌整体失效
    """
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user.id,
        "email": user.email,
        "username": user.username,
        "jti": uuid.uuid4().hex,
        "tv": int(token_version or 0),
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[TokenData]:
    """解码登录令牌；带 purpose 的流程令牌（如邮箱验证）一律拒绝"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose"):
            # purpose 令牌（create_verify_token 签发）不能当登录令牌使用
            return None
        exp = payload.get("exp")
        return TokenData(
            user_id=payload.get("sub"),
            email=payload.get("email"),
            jti=payload.get("jti") or None,
            token_version=int(payload.get("tv") or 0),
            expires_at=datetime.utcfromtimestamp(exp) if isinstance(exp, (int, float)) else None,
        )
    except (jwt.ExpiredSignatureError, TypeError, ValueError):
        return None
    except jwt.InvalidTokenError:
        return None


# ==================== 邮箱验证令牌（purpose 限定，不能当登录令牌用） ====================

VERIFY_TOKEN_EXPIRE_MINUTES = 30


def create_verify_token(user_id: str) -> str:
    """邮箱验证流程令牌：30 分钟有效，仅用于 verify-email / resend 接口"""
    expire = datetime.utcnow() + timedelta(minutes=VERIFY_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "purpose": "verify_email", "exp": expire, "iat": datetime.utcnow()}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_verify_token(token: str) -> Optional[str]:
    """校验验证令牌，返回 user_id；签名/过期/用途不符均返回 None"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    if payload.get("purpose") != "verify_email" or not payload.get("sub"):
        return None
    return payload["sub"]


# ==================== 辅助函数 ====================

def db_user_to_user(db_user) -> User:
    """转换数据库用户模型为响应模型"""
    import json as _json

    raw = getattr(db_user, "allowed_features", None)
    try:
        override = _json.loads(raw) if raw else None
    except (TypeError, ValueError):
        override = None
    return User(
        id=db_user.id,
        email=db_user.email,
        username=db_user.username,
        is_active=db_user.is_active,
        is_admin=db_user.is_admin,
        # 旧库迁移前列可能不存在（迁移由 init_db 保证先于请求发生）
        email_verified=getattr(db_user, "email_verified", True),
        allowed_features=override,
        created_at=db_user.created_at
    )


# ==================== 会话 Cookie ====================

def _cookie_secure(request: Request) -> bool:
    """Secure 标记：配置 true/false 优先；auto 按请求实际协议判定"""
    mode = (settings.AUTH_COOKIE_SECURE or "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    if request.url.scheme == "https":
        return True
    # 反向代理终止 TLS：仅当对端是可信代理时才采信 X-Forwarded-Proto
    from app.rate_limit import _is_trusted_proxy

    peer = request.client.host if request.client else ""
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return proto == "https" and _is_trusted_proxy(peer)


def set_auth_cookie(response: Response, request: Request, token: str) -> None:
    """登录/注册/验证成功后下发会话 Cookie"""
    response.set_cookie(
        AUTH_COOKIE_NAME, token,
        max_age=AUTH_COOKIE_MAX_AGE, path=AUTH_COOKIE_PATH,
        httponly=True, samesite="lax", secure=_cookie_secure(request),
    )


def clear_auth_cookie(response: Response, request: Request) -> None:
    """删除会话 Cookie（属性须与下发时一致，浏览器才认作同一个）"""
    response.delete_cookie(
        AUTH_COOKIE_NAME, path=AUTH_COOKIE_PATH,
        httponly=True, samesite="lax", secure=_cookie_secure(request),
    )


def extract_token(request: Request) -> "tuple[Optional[str], Optional[str]]":
    """取请求携带的登录令牌 → (token, 来源)；来源为 "header" / "cookie" / None。

    Authorization: Bearer 优先（API / 脚本客户端），否则读会话 Cookie。
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token, "header"
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token, "cookie"
    return None, None


# ==================== 认证依赖 ====================

def _user_from_token(token: str, db: Session) -> User:
    """校验登录令牌并返回用户：无效/过期/已吊销/用户不存在 → 401，用户被禁用 → 403"""
    token_data = decode_token(token)
    if token_data is None or not token_data.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db_user = get_user_by_id(db, token_data.user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not db_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )
    # 已登出（jti 黑名单）或签发后用户令牌版本已递增（禁用过/改过密码）
    if (token_data.token_version != (getattr(db_user, "token_version", 0) or 0)
            or (token_data.jti and is_token_revoked(db, token_data.jti))):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return db_user_to_user(db_user)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """获取当前用户（可选认证）。

    未携带令牌 → 匿名（None）；Bearer 头携带了令牌但无效/过期/用户不存在
    → 401，用户被禁用 → 403。此前一律降级为匿名，受限/禁用用户只要弄坏
    令牌就能按访客权限继续调用，前端也无从得知会话已失效。

    会话 Cookie 失效（过期/吊销）在可选认证下按匿名处理：浏览器端的
    httpOnly Cookie 前端删不掉，若这里 401，残留 Cookie 会让匿名访问
    处处 401；而丢弃 Cookie 本就等同匿名，不构成绕过。必须登录的端点
    （get_current_user_required）仍 401，前端据此清理登录态。
    """
    token, source = extract_token(request)
    if token is None:
        return None
    if source == "cookie":
        try:
            return _user_from_token(token, db)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                return None
            raise
    return _user_from_token(token, db)


async def get_current_user_soft(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """宽松版：任何令牌问题都视为匿名。仅供 /auth/verify、/auth/site-config
    这类「探测会话状态」的公开端点使用，不得用于权限判断。"""
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


async def get_current_user_required(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """获取当前用户（必须认证；Bearer 头或会话 Cookie 均可）"""
    token, _source = extract_token(request)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _user_from_token(token, db)


async def get_admin_user(
    current_user: User = Depends(get_current_user_required)
) -> User:
    """获取管理员用户"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user
