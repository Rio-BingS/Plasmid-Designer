"""
数据库配置和模型
使用 SQLite 作为开发数据库，生产环境可切换到 PostgreSQL
"""

from sqlalchemy import create_engine, Column, String, DateTime, Float, Boolean, Integer, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from pathlib import Path

from app.config import settings

# 数据库连接串 — 单一来源 settings.DATABASE_URL（读 .env / 环境变量）。
# 此前此处用 os.getenv 直读进程环境变量、不读 .env，导致 .env 里的 DATABASE_URL
# 对实际建库静默不生效（KNOWN_ISSUES 2.4）
DATABASE_URL = settings.DATABASE_URL

# SQLite 库文件所在目录不存在时自动创建（默认位于 DATA_DIR 下）
if DATABASE_URL.startswith("sqlite"):
    _db_file = DATABASE_URL.split("///", 1)[-1].split("?")[0]
    _db_parent = Path(_db_file).parent
    if str(_db_parent) not in ("", "."):
        _db_parent.mkdir(parents=True, exist_ok=True)

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
    # 注册邮箱验证状态；历史用户经 init_db 迁移回填为 True，不会被新开关锁死
    email_verified = Column(Boolean, default=False)
    # 个人功能权限覆盖（JSON 数组字符串，键见 features.FEATURE_REGISTRY）；
    # NULL = 跟随「普通用户」层级默认，设值后精确覆盖该用户可用功能
    allowed_features = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    designs = relationship("DesignDB", back_populates="user")


# ==================== 站点设置模型 ====================

class SiteSettingsDB(Base):
    """站点设置（单行表，管理员可改）

    功能开关（anonymous_features / user_features）为 JSON 数组字符串，
    键值含义见 app/features.py 的 FEATURE_REGISTRY；
    feature_migrations 记录已执行的功能清单一次性迁移（逗号分隔迁移 id），
    保证拆分键继承只在升级后跑一次，不与管理员后续的显式勾选冲突
    """
    __tablename__ = "site_settings"

    id = Column(Integer, primary_key=True, default=1)
    registration_open = Column(Boolean, default=True)
    email_verification_required = Column(Boolean, default=False)
    anonymous_features = Column(Text, nullable=False, default="[]")
    user_features = Column(Text, nullable=False, default="[]")
    feature_migrations = Column(String(200), nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== 邮箱验证码模型 ====================

class EmailVerificationDB(Base):
    """注册邮箱验证码（哈希存储，10 分钟有效，一次性）"""
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    code_hash = Column(String(128), nullable=False)
    purpose = Column(String(30), nullable=False, default="register")
    expires_at = Column(DateTime, nullable=False)
    consumed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    # 完整构建体（此前不落库，DB 模式重启/缓存过期后导出与图谱静默降级）
    construct_sequence = Column(Text, nullable=True)
    construct_features = Column(Text, nullable=True)  # JSON 数组字符串
    insert_start = Column(Integer, nullable=True)
    insert_end = Column(Integer, nullable=True)
    vector_name = Column(String(100), nullable=True)
    
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
    _migrate_users_table()
    _migrate_designs_table()
    _migrate_site_settings_table()
    Base.metadata.create_all(bind=engine)
    _migrate_feature_lists()
    print("✅ 数据库表已创建")


def _migrate_site_settings_table():
    """site_settings 表轻量迁移：补齐后加的列（create_all 不改旧表）"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "site_settings" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("site_settings")}
    with engine.begin() as conn:
        if "feature_migrations" not in cols:
            conn.execute(text(
                "ALTER TABLE site_settings ADD COLUMN feature_migrations VARCHAR(200) "
                "NOT NULL DEFAULT ''"))
            print("✅ site_settings 表已迁移：新增 feature_migrations 列")


def _migrate_feature_lists(eng=None):
    """功能清单一次性数据迁移：注册表拆分键继承（app.features.SPLIT_INHERIT）。

    存量库的功能数组不含拆分出的新键（sequencing_batch），若不迁移，原本可用
    批量测序的人群升级后即失去入口——含被拆键的清单（站点两级矩阵 + 用户个人
    覆盖）自动补上新键，保持拆分前行为。site_settings.feature_migrations 标记
    已补齐的新键，保证只跑一次：之后管理员显式取消勾选不会被启动迁移覆盖。
    必须在 create_all 与列迁移之后调用（读取 feature_migrations 列）。
    """
    import json as _json

    from sqlalchemy import inspect, text

    from app.features import SPLIT_INHERIT, valid_features

    eng = eng or engine
    insp = inspect(eng)
    if "site_settings" not in insp.get_table_names() or "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("site_settings")}
    if "feature_migrations" not in cols:
        return
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT anonymous_features, user_features, feature_migrations "
            "FROM site_settings WHERE id = 1")).first()
        if row is None:
            return
        anon, userf, marker = row
        done = {m.strip() for m in (marker or "").split(",") if m.strip()}
        pending = [k for k in SPLIT_INHERIT if k not in done]
        if not pending:
            return

        def _patch(raw):
            try:
                keys = _json.loads(raw or "[]")
            except ValueError:
                return raw or "[]"
            for new in pending:
                src = SPLIT_INHERIT[new]
                if src in keys and new not in keys:
                    keys.append(new)
            return _json.dumps(valid_features(keys))

        conn.execute(text(
            "UPDATE site_settings SET anonymous_features = :a, user_features = :u, "
            "feature_migrations = :m WHERE id = 1"),
            {"a": _patch(anon), "u": _patch(userf),
             "m": ",".join(sorted(done | set(pending)))})
        # 用户个人功能覆盖同步补齐（get_current_user 每请求查库，重启后即时生效）
        for uid, raw in conn.execute(text(
                "SELECT id, allowed_features FROM users WHERE allowed_features IS NOT NULL")).fetchall():
            patched = _patch(raw)
            if patched != raw:
                conn.execute(text("UPDATE users SET allowed_features = :v WHERE id = :i"),
                             {"v": patched, "i": uid})
        print(f"✅ 功能清单迁移完成：补齐拆分键 {pending}")


def _migrate_designs_table():
    """designs 表轻量迁移：补齐后加的构建体列（create_all 不改旧表）"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "designs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("designs")}
    stmts = {
        "construct_sequence": "ALTER TABLE designs ADD COLUMN construct_sequence TEXT NULL",
        "construct_features": "ALTER TABLE designs ADD COLUMN construct_features TEXT NULL",
        "insert_start": "ALTER TABLE designs ADD COLUMN insert_start INTEGER NULL",
        "insert_end": "ALTER TABLE designs ADD COLUMN insert_end INTEGER NULL",
        "vector_name": "ALTER TABLE designs ADD COLUMN vector_name VARCHAR(100) NULL",
    }
    with engine.begin() as conn:
        for col, ddl in stmts.items():
            if col not in cols:
                conn.execute(text(ddl))
                print(f"✅ designs 表已迁移：新增 {col} 列")


def _migrate_users_table():
    """users 表轻量迁移：create_all 只建新表不改旧表，后加的列需手工 ALTER 补齐。
    - email_verified：历史用户回填为已验证（邮箱验证开关打开时才会校验该字段，
      回填保证存量账号不被新开关锁死）
    - allowed_features：个人功能权限覆盖，NULL 即跟随层级默认，无需回填
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        if "email_verified" not in cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("UPDATE users SET email_verified = TRUE"))
            print("✅ users 表已迁移：新增 email_verified 列（存量用户回填为已验证）")
        if "allowed_features" not in cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN allowed_features TEXT NULL"))
            print("✅ users 表已迁移：新增 allowed_features 列（个人功能权限覆盖）")


def drop_db():
    """删除所有表（开发用）"""
    Base.metadata.drop_all(bind=engine)
    print("🗑️ 数据库表已删除")


if __name__ == "__main__":
    init_db()
