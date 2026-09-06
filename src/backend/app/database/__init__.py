"""
数据库模块
"""

from .models import (
    Base, engine, SessionLocal, get_db, init_db, drop_db,
    UserDB, DesignDB, PrimerDB, DesignWarningDB, DesignErrorDB,
    BatchJobDB, BatchDesignDB, VectorDB, VectorFeatureDB,
    SiteSettingsDB, EmailVerificationDB
)
from .crud import (
    # 用户
    create_user, get_user_by_email, get_user_by_id, get_users,
    update_user, delete_user, count_admins,
    # 站点设置
    get_site_settings_row, save_site_settings_row,
    # 邮箱验证码
    create_email_verification, get_latest_email_verification, consume_email_verification,
    # 设计
    create_design, get_design, get_designs_by_user, update_design,
    add_primer, add_warning, add_error,
    # 批量
    create_batch_job, get_batch_job, update_batch_job, add_batch_design,
    # 载体
    create_vector, get_vector, get_vectors, delete_vector, add_vector_feature
)

__all__ = [
    # 模型
    "Base", "engine", "SessionLocal", "get_db", "init_db", "drop_db",
    "UserDB", "DesignDB", "PrimerDB", "DesignWarningDB", "DesignErrorDB",
    "BatchJobDB", "BatchDesignDB", "VectorDB", "VectorFeatureDB",
    "SiteSettingsDB", "EmailVerificationDB",
    # CRUD
    "create_user", "get_user_by_email", "get_user_by_id", "get_users",
    "update_user", "delete_user", "count_admins",
    "get_site_settings_row", "save_site_settings_row",
    "create_email_verification", "get_latest_email_verification", "consume_email_verification",
    "create_design", "get_design", "get_designs_by_user", "update_design",
    "add_primer", "add_warning", "add_error",
    "create_batch_job", "get_batch_job", "update_batch_job", "add_batch_design",
    "create_vector", "get_vector", "get_vectors", "delete_vector", "add_vector_feature"
]
