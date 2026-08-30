"""
用户认证模块
- JWT Token 工具
- 密码加密 (bcrypt)
- Pydantic 模型
- 认证依赖函数（基于数据库）
"""

from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, EmailStr
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import jwt
import logging

from app.config import settings
from app.database import get_db
from app.database.crud import get_user_by_id, get_user_by_email

# 配置 — 密钥来自环境变量/配置；未设置时使用开发默认值并告警
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

if SECRET_KEY == "dev-insecure-secret-key-change-me":
    logging.getLogger("plasmid_designer.auth").warning(
        "SECRET_KEY 未配置，正在使用开发默认值——重启后所有令牌失效，生产环境必须设置环境变量 SECRET_KEY"
    )

# 密码加密
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer Token 认证
security = HTTPBearer(auto_error=False)


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


# ==================== 密码工具 ====================

def hash_password(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


# ==================== JWT 工具 ====================

def create_access_token(user: User) -> str:
    """生成访问令牌"""
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user.id,
        "email": user.email,
        "username": user.username,
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[TokenData]:
    """解码令牌"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return TokenData(
            user_id=payload.get("sub"),
            email=payload.get("email")
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ==================== 辅助函数 ====================

def db_user_to_user(db_user) -> User:
    """转换数据库用户模型为响应模型"""
    return User(
        id=db_user.id,
        email=db_user.email,
        username=db_user.username,
        is_active=db_user.is_active,
        is_admin=db_user.is_admin,
        created_at=db_user.created_at
    )


# ==================== 认证依赖 ====================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """获取当前用户（可选认证）"""
    if credentials is None:
        return None

    token = credentials.credentials
    token_data = decode_token(token)

    if token_data is None:
        return None

    db_user = get_user_by_id(db, token_data.user_id)
    if db_user is None:
        return None

    return db_user_to_user(db_user)


async def get_current_user_required(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """获取当前用户（必须认证）"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    token_data = decode_token(token)

    if token_data is None:
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
        )

    if not db_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )

    return db_user_to_user(db_user)


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
