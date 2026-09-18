"""站点设置读写（管理员可改的站点级开关）

存储于 site_settings 单行表（数据库版认证同一套连接）；
读侧带 3 秒 TTL 缓存——功能开关在每个 API 请求上都会被检查，
缓存把高频读压到可忽略，管理端保存时主动失效。
"""

import json
import logging
import threading
import time
from typing import Optional

from app.database import get_site_settings_row, save_site_settings_row
from app.features import (
    DEFAULT_ANONYMOUS_FEATURES, DEFAULT_USER_FEATURES, valid_features
)

logger = logging.getLogger("plasmid_designer.site_settings")

CACHE_TTL_SECONDS = 3.0
_cache_lock = threading.Lock()
_cache: Optional[dict] = None
_cache_at: float = 0.0

_DEFAULTS = {
    "registration_open": True,
    "email_verification_required": False,
    "anonymous_features": list(DEFAULT_ANONYMOUS_FEATURES),
    "user_features": list(DEFAULT_USER_FEATURES),
}


def _row_to_dict(row) -> dict:
    return {
        "registration_open": bool(row.registration_open),
        "email_verification_required": bool(row.email_verification_required),
        "anonymous_features": valid_features(json.loads(row.anonymous_features or "[]")),
        "user_features": valid_features(json.loads(row.user_features or "[]")),
    }


def _fetch() -> dict:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = get_site_settings_row(db)
        data = _row_to_dict(row)
        # 存量行为兼容：开关列建库默认 False/True，功能清单为空时补默认
        if not data["anonymous_features"]:
            row.anonymous_features = json.dumps(DEFAULT_ANONYMOUS_FEATURES)
            save_site_settings_row(db, row)
            data = _row_to_dict(row)
        if not data["user_features"]:
            row.user_features = json.dumps(DEFAULT_USER_FEATURES)
            save_site_settings_row(db, row)
            data = _row_to_dict(row)
        return data
    except Exception:
        # site_settings 表尚未建（旧库未跑过新版 init_db）等情形：按默认值放行，
        # 保持既有行为；日志告警便于发现
        logger.warning("站点设置读取失败，按默认配置放行", exc_info=True)
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _DEFAULTS.items()}
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover
            pass


def get_settings(force_refresh: bool = False) -> dict:
    """站点设置（带 TTL 缓存；管理员保存后调用 invalidate）"""
    global _cache, _cache_at
    now = time.monotonic()
    with _cache_lock:
        if not force_refresh and _cache is not None and now - _cache_at < CACHE_TTL_SECONDS:
            return _cache
    data = _fetch()
    with _cache_lock:
        _cache = data
        _cache_at = time.monotonic()
    return data


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def update_settings(patch: dict) -> dict:
    """管理员保存：仅接受已知键；功能清单先经 valid_features 清洗"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = get_site_settings_row(db)
        if "registration_open" in patch:
            row.registration_open = bool(patch["registration_open"])
        if "email_verification_required" in patch:
            row.email_verification_required = bool(patch["email_verification_required"])
        if "anonymous_features" in patch:
            row.anonymous_features = json.dumps(valid_features(patch["anonymous_features"]))
        if "user_features" in patch:
            row.user_features = json.dumps(valid_features(patch["user_features"]))
        save_site_settings_row(db, row)
    finally:
        db.close()
    invalidate_cache()
    return get_settings(force_refresh=True)
