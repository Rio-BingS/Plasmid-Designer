"""
数据库配置和模型
使用 SQLite 作为开发数据库，生产环境可切换到 PostgreSQL
"""

from sqlalchemy import create_engine, Column, String, DateTime, Float, Boolean, Integer, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

# 数据库路径
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./plasmid_designer.db"
)

# check_same_thread 是 SQLite 专用参数，其他方言（如 PostgreSQL）不接受
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args
)

# 会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基类
Base = declarative_base()


# ==================== 用户模型 ====================

class UserDB(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(String(50), primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关联
    designs = relationship("DesignDB", back_populates="user")


# ==================== 设计任务模型 ====================

class DesignDB(Base):
    """设计任务表"""
    __tablename__ = "designs"
    
    id = Column(String(50), primary_key=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=True)  # 允许匿名用户
    
    # 输入
    input_sequence = Column(Text, nullable=False)
    sequence_type = Column(String(20), default="amino_acid")
    sequence_name = Column(String(100), default="insert")
    
    # 参数
    vector_id = Column(String(50), default="pET-28a")
    cloning_method = Column(String(20), default="gibson")
    target_species = Column(String(20), default="ecoli")
    optimize_codons = Column(Boolean, default=True)
    gc_min = Column(Float, default=40.0)
    gc_max = Column(Float, default=60.0)
    
    # 输出
    optimized_sequence = Column(Text, nullable=True)
    cai = Column(Float, nullable=True)
    gc_content = Column(Float, nullable=True)
    final_length = Column(Integer, nullable=True)
    
    # 状态
    status = Column(String(20), default="pending")
    validation_passed = Column(Boolean, default=False)
    
    # 时间
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # 关联
    user = relationship("UserDB", back_populates="designs")
    primers = relationship("PrimerDB", back_populates="design", cascade="all, delete-orphan")
    warnings = relationship("DesignWarningDB", back_populates="design", cascade="all, delete-orphan")
    errors = relationship("DesignErrorDB", back_populates="design", cascade="all, delete-orphan")


class PrimerDB(Base):
    """引物表"""
    __tablename__ = "primers"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    design_id = Column(String(50), ForeignKey("designs.id"), nullable=False)
    
    name = Column(String(100), nullable=False)
    sequence = Column(String(500), nullable=False)
    full_sequence = Column(String(500), nullable=False)
    tm = Column(Float, nullable=False)
    gc_content = Column(Float, nullable=False)
    length = Column(Integer, nullable=False)
    overhang = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    
    design = relationship("DesignDB", back_populates="primers")


class DesignWarningDB(Base):
    """设计警告表"""
    __tablename__ = "design_warnings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    design_id = Column(String(50), ForeignKey("designs.id"), nullable=False)
    message = Column(Text, nullable=False)
    
    design = relationship("DesignDB", back_populates="warnings")


class DesignErrorDB(Base):
    """设计错误表"""
    __tablename__ = "design_errors"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    design_id = Column(String(50), ForeignKey("designs.id"), nullable=False)
    message = Column(Text, nullable=False)
    
    design = relationship("DesignDB", back_populates="errors")


# ==================== 批量任务模型 ====================

class BatchJobDB(Base):
    """批量任务表"""
    __tablename__ = "batch_jobs"
    
    id = Column(String(50), primary_key=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=True)
    
    total = Column(Integer, default=0)
    completed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    status = Column(String(20), default="pending")
    errors = Column(Text, default="[]")  # JSON 数组字符串
    
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # 关联
    design_ids = relationship("BatchDesignDB", back_populates="batch_job", cascade="all, delete-orphan")


class BatchDesignDB(Base):
    """批量任务-设计关联表"""
    __tablename__ = "batch_designs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(50), ForeignKey("batch_jobs.id"), nullable=False)
    design_id = Column(String(50), ForeignKey("designs.id"), nullable=False)
    sequence_name = Column(String(100), nullable=True)
    
    batch_job = relationship("BatchJobDB", back_populates="design_ids")


# ==================== 载体模型 ====================

class VectorDB(Base):
    """载体表（用户上传的载体）"""
    __tablename__ = "vectors"
    
    id = Column(String(50), primary_key=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=True)
    
    name = Column(String(200), nullable=False)
    source = Column(String(50), default="custom")
    sequence = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    vector_type = Column(String(50), default="expression")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关联
    features = relationship("VectorFeatureDB", back_populates="vector", cascade="all, delete-orphan")


class VectorFeatureDB(Base):
    """载体特征表"""
    __tablename__ = "vector_features"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    vector_id = Column(String(50), ForeignKey("vectors.id"), nullable=False)
    
    name = Column(String(200), nullable=False)
    feature_type = Column(String(50), nullable=False)
    start = Column(Integer, nullable=False)
    end = Column(Integer, nullable=False)
    strand = Column(String(1), default="+")
    description = Column(Text, nullable=True)
    
    vector = relationship("VectorDB", back_populates="features")


# ==================== 数据库工具 ====================

def get_db():
    """获取数据库会话（依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库"""
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库表已创建")


def drop_db():
    """删除所有表（开发用）"""
    Base.metadata.drop_all(bind=engine)
    print("🗑️ 数据库表已删除")


if __name__ == "__main__":
    init_db()
