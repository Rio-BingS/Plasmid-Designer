"""管理员账号引导

启动时按环境变量 ADMIN_EMAIL / ADMIN_PASSWORD（可选 ADMIN_USERNAME，默认 admin）：
- 该邮箱用户不存在 → 创建为管理员（邮箱视为已验证）
- 存在但非管理员   → 提升为管理员
- 已是管理员       → 不动（不覆盖密码——改密请直接改数据库或换环境变量+删号重建）
未配置环境变量时什么都不做（开发环境保持原状）。
"""

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("plasmid_designer.bootstrap")


def bootstrap_admin() -> Optional[str]:
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return None

    from app.database import SessionLocal, create_user, get_user_by_email, update_user
    from app.auth.jwt_auth import hash_password

    db = SessionLocal()
    try:
        user = get_user_by_email(db, settings.ADMIN_EMAIL)
        if user is None:
            create_user(
                db,
                email=settings.ADMIN_EMAIL,
                username=settings.ADMIN_USERNAME or "admin",
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                is_admin=True,
                email_verified=True,
            )
            logger.info("已创建管理员账号 %s", settings.ADMIN_EMAIL)
            return "created"
        if not user.is_admin:
            update_user(db, user.id, is_admin=True, email_verified=True)
            logger.info("已将现有用户 %s 提升为管理员", settings.ADMIN_EMAIL)
            return "promoted"
        return None
    finally:
        db.close()
