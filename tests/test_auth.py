"""
认证模块单元测试
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database.models import Base, UserDB
from app.database import get_db
from app.auth.jwt_auth import (
    hash_password, verify_password, create_access_token, decode_token,
    User, UserCreate, UserLogin
)


# ==================== 测试数据库配置 ====================

TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture
def test_engine():
    """创建测试数据库引擎"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_session(test_engine):
    """创建测试会话"""
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestSession()
    yield session
    session.close()


# ==================== 密码加密测试 ====================

class TestPasswordHashing:
    """密码加密测试"""
    
    def test_hash_password(self):
        """测试密码哈希生成"""
        password = "testPassword123"
        hashed = hash_password(password)
        
        assert hashed != password
        assert len(hashed) > 0
        assert hashed.startswith("$2b$")  # bcrypt 格式
    
    def test_verify_password_correct(self):
        """测试正确密码验证"""
        password = "testPassword123"
        hashed = hash_password(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self):
        """测试错误密码验证"""
        password = "testPassword123"
        hashed = hash_password(password)
        
        assert verify_password("wrongPassword", hashed) is False
    
    def test_different_passwords_different_hash(self):
        """测试相同密码生成不同哈希（bcrypt 有盐）"""
        password = "testPassword123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)
        
        # 不同哈希但都能验证
        assert hash1 != hash2
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)


# ==================== JWT Token 测试 ====================

class TestJWTToken:
    """JWT Token 测试"""
    
    def test_create_access_token(self):
        """测试创建访问令牌"""
        user = User(
            id="user_test123",
            email="test@example.com",
            username="testuser",
            is_active=True,
            is_admin=False
        )
        
        token = create_access_token(user)
        
        assert token is not None
        assert len(token) > 0
        assert token.count('.') == 2  # JWT 格式：header.payload.signature
    
    def test_decode_valid_token(self):
        """测试解码有效令牌"""
        user = User(
            id="user_test123",
            email="test@example.com",
            username="testuser",
            is_active=True,
            is_admin=False
        )
        
        token = create_access_token(user)
        token_data = decode_token(token)
        
        assert token_data is not None
        assert token_data.user_id == "user_test123"
        assert token_data.email == "test@example.com"
    
    def test_decode_invalid_token(self):
        """测试解码无效令牌"""
        invalid_token = "invalid.token.here"
        token_data = decode_token(invalid_token)
        
        assert token_data is None
    
    def test_decode_empty_token(self):
        """测试解码空令牌"""
        token_data = decode_token("")
        assert token_data is None


# ==================== 用户模型测试 ====================

class TestUserModel:
    """用户模型测试"""
    
    def test_create_user_db(self, test_session):
        """测试数据库创建用户"""
        user = UserDB(
            id="user_test123",
            email="test@example.com",
            username="testuser",
            hashed_password=hash_password("password123"),
            is_active=True,
            is_admin=False
        )
        
        test_session.add(user)
        test_session.commit()
        
        # 查询验证
        saved_user = test_session.query(UserDB).filter(
            UserDB.email == "test@example.com"
        ).first()
        
        assert saved_user is not None
        assert saved_user.username == "testuser"
        assert saved_user.email == "test@example.com"
    
    def test_unique_email_constraint(self, test_session):
        """测试邮箱唯一性约束"""
        user1 = UserDB(
            id="user_test1",
            email="same@example.com",
            username="user1",
            hashed_password="hash1"
        )
        test_session.add(user1)
        test_session.commit()
        
        # 尝试创建相同邮箱用户
        user2 = UserDB(
            id="user_test2",
            email="same@example.com",
            username="user2",
            hashed_password="hash2"
        )
        test_session.add(user2)
        
        with pytest.raises(Exception):  # 应该抛出完整性错误
            test_session.commit()


# ==================== Pydantic 模型测试 ====================

class TestPydanticModels:
    """Pydantic 模型测试"""
    
    def test_user_create_valid(self):
        """测试有效的用户创建数据"""
        data = UserCreate(
            email="test@example.com",
            username="testuser",
            password="password123",
            confirm_password="password123"
        )
        
        assert data.email == "test@example.com"
        assert data.username == "testuser"
    
    def test_user_login_valid(self):
        """测试有效的登录数据"""
        data = UserLogin(
            email="test@example.com",
            password="password123"
        )
        
        assert data.email == "test@example.com"
    
    def test_user_create_invalid_email(self):
        """测试无效邮箱"""
        with pytest.raises(ValueError):
            UserCreate(
                email="invalid-email",
                username="testuser",
                password="password123",
                confirm_password="password123"
            )


# ==================== 运行测试 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
