"""
数据库 CRUD 操作
"""

from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import uuid

from .models import (
    UserDB, DesignDB, PrimerDB, DesignWarningDB, DesignErrorDB,
    BatchJobDB, BatchDesignDB, VectorDB, VectorFeatureDB
)


# ==================== 用户 CRUD ====================

def create_user(
    db: Session,
    email: str,
    username: str,
    hashed_password: str,
    is_admin: bool = False
) -> UserDB:
    """创建用户"""
    user = UserDB(
        id=f"user_{uuid.uuid4().hex[:12]}",
        email=email,
        username=username,
        hashed_password=hashed_password,
        is_admin=is_admin
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> Optional[UserDB]:
    """通过邮箱获取用户"""
    return db.query(UserDB).filter(UserDB.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[UserDB]:
    """通过 ID 获取用户"""
    return db.query(UserDB).filter(UserDB.id == user_id).first()


def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[UserDB]:
    """获取用户列表"""
    return db.query(UserDB).offset(skip).limit(limit).all()


# ==================== 设计任务 CRUD ====================

def create_design(
    db: Session,
    input_sequence: str,
    sequence_type: str = "amino_acid",
    sequence_name: str = "insert",
    vector_id: str = "pET-28a",
    cloning_method: str = "gibson",
    user_id: Optional[str] = None,
    id: Optional[str] = None,
    **kwargs
) -> DesignDB:
    """创建设计任务（可指定既有 design_id，供存储层双写复用）"""
    design = DesignDB(
        id=id or f"design_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        input_sequence=input_sequence,
        sequence_type=sequence_type,
        sequence_name=sequence_name,
        vector_id=vector_id,
        cloning_method=cloning_method,
        **kwargs
    )
    db.add(design)
    db.commit()
    db.refresh(design)
    return design


def get_design(db: Session, design_id: str) -> Optional[DesignDB]:
    """获取设计任务"""
    return db.query(DesignDB).filter(DesignDB.id == design_id).first()


def get_designs_by_user(db: Session, user_id: str, skip: int = 0, limit: int = 50) -> List[DesignDB]:
    """获取用户的设计任务列表"""
    return db.query(DesignDB).filter(DesignDB.user_id == user_id).offset(skip).limit(limit).all()


def update_design(db: Session, design_id: str, **kwargs) -> Optional[DesignDB]:
    """更新设计任务"""
    design = db.query(DesignDB).filter(DesignDB.id == design_id).first()
    if design:
        for key, value in kwargs.items():
            if hasattr(design, key):
                setattr(design, key, value)
        db.commit()
        db.refresh(design)
    return design


def add_primer(
    db: Session,
    design_id: str,
    name: str,
    sequence: str,
    full_sequence: str,
    tm: float,
    gc_content: float,
    length: int,
    overhang: Optional[str] = None,
    notes: Optional[str] = None
) -> PrimerDB:
    """添加引物"""
    primer = PrimerDB(
        design_id=design_id,
        name=name,
        sequence=sequence,
        full_sequence=full_sequence,
        tm=tm,
        gc_content=gc_content,
        length=length,
        overhang=overhang,
        notes=notes
    )
    db.add(primer)
    db.commit()
    db.refresh(primer)
    return primer


def add_warning(db: Session, design_id: str, message: str) -> DesignWarningDB:
    """添加警告"""
    warning = DesignWarningDB(design_id=design_id, message=message)
    db.add(warning)
    db.commit()
    return warning


def add_error(db: Session, design_id: str, message: str) -> DesignErrorDB:
    """添加错误"""
    error = DesignErrorDB(design_id=design_id, message=message)
    db.add(error)
    db.commit()
    return error


# ==================== 批量任务 CRUD ====================

def create_batch_job(db: Session, total: int, user_id: Optional[str] = None, id: Optional[str] = None) -> BatchJobDB:
    """创建批量任务（可指定既有 batch_id，供存储层双写复用）"""
    batch = BatchJobDB(
        id=id or f"batch_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        total=total
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def get_batch_job(db: Session, batch_id: str) -> Optional[BatchJobDB]:
    """获取批量任务"""
    return db.query(BatchJobDB).filter(BatchJobDB.id == batch_id).first()


def update_batch_job(db: Session, batch_id: str, **kwargs) -> Optional[BatchJobDB]:
    """更新批量任务"""
    batch = db.query(BatchJobDB).filter(BatchJobDB.id == batch_id).first()
    if batch:
        for key, value in kwargs.items():
            if hasattr(batch, key):
                setattr(batch, key, value)
        db.commit()
        db.refresh(batch)
    return batch


def add_batch_design(db: Session, batch_id: str, design_id: str, sequence_name: Optional[str] = None) -> BatchDesignDB:
    """添加批量任务-设计关联"""
    bd = BatchDesignDB(
        batch_id=batch_id,
        design_id=design_id,
        sequence_name=sequence_name
    )
    db.add(bd)
    db.commit()
    return bd


# ==================== 载体 CRUD ====================

def create_vector(
    db: Session,
    name: str,
    sequence: str,
    user_id: Optional[str] = None,
    source: str = "custom",
    description: Optional[str] = None,
    vector_type: str = "expression"
) -> VectorDB:
    """创建载体"""
    vector = VectorDB(
        id=f"vector_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        name=name,
        sequence=sequence,
        source=source,
        description=description,
        vector_type=vector_type
    )
    db.add(vector)
    db.commit()
    db.refresh(vector)
    return vector


def get_vector(db: Session, vector_id: str) -> Optional[VectorDB]:
    """获取载体"""
    return db.query(VectorDB).filter(VectorDB.id == vector_id).first()


def get_vectors(db: Session, skip: int = 0, limit: int = 100) -> List[VectorDB]:
    """获取载体列表"""
    return db.query(VectorDB).offset(skip).limit(limit).all()


def delete_vector(db: Session, vector_id: str) -> bool:
    """删除载体"""
    vector = db.query(VectorDB).filter(VectorDB.id == vector_id).first()
    if vector:
        db.delete(vector)
        db.commit()
        return True
    return False


def add_vector_feature(
    db: Session,
    vector_id: str,
    name: str,
    feature_type: str,
    start: int,
    end: int,
    strand: str = "+",
    description: Optional[str] = None
) -> VectorFeatureDB:
    """添加载体特征"""
    feature = VectorFeatureDB(
        vector_id=vector_id,
        name=name,
        feature_type=feature_type,
        start=start,
        end=end,
        strand=strand,
        description=description
    )
    db.add(feature)
    db.commit()
    return feature
