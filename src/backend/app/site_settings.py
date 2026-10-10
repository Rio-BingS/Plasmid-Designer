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

# 读取失败时的兜底：全部收紧（见 _fetch）
_FAIL_CLOSED = {
    "registration_open": False,
    "email_verification_required": True,
    "anonymous_features": [],
    "user_features": [],
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
        # 终审 D-02：功能清单列是 nullable=False default="[]"，NULL 与
        # 「管理员显式清空」不可区分，此前 `if not data[...]` 把空集当成
        # 未初始化强行补默认（fail-open：关闭全部功能保存后又被覆盖回
        # 默认值）。用显式 features_initialized 标记：未初始化 → 补默认并
        # 置位；已初始化 → 完全尊重存储值（空集就是空集）。
        if not getattr(row, "features_initialized", False):
            row.anonymous_features = json.dumps(DEFAULT_ANONYMOUS_FEATURES)
            row.user_features = json.dumps(DEFAULT_USER_FEATURES)
            row.features_initialized = True
            save_site_settings_row(db, row)
            data = _row_to_dict(row)
        return data
    except Exception:
        # 读取失败（表未建/库不可用等）一律 fail-closed：关闭注册、访客与普通
        # 用户功能清单为空。此前按默认值放行会让管理员收紧的限制在数据库
        # 抖动时整体失效；管理员不受功能清单约束，仍可进入后台排查
        logger.error("站点设置读取失败，按最严配置拒绝（fail-closed）", exc_info=True)
        return dict(_FAIL_CLOSED, anonymous_features=[], user_features=[])
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
        # 终审 D-02：管理员保存过功能清单即视为已初始化（显式空集保持为空）
        if "anonymous_features" in patch or "user_features" in patch:
            row.features_initialized = True
        save_site_settings_row(db, row)
    finally:
        db.close()
    invalidate_cache()
    return get_settings(force_refresh=True)
