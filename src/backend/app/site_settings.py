"""站点设置读写（管理员可改的站点级开关）

存储于 site_settings 单行表（数据库版认证同一套连接）；
读侧带 3 秒 TTL 缓存——功能开关在每个 API 请求上都会被检查，
缓存把高频读压到可忽略，管理端保存时主动失效。
读取失败时沿用最近一次成功值；从未成功过则功能清单按默认值（见 get_settings）。
"""

import json
import logging
import threading
import time
from typing import Optional

from app.batch_read_limits import empty_tier_limits, normalize_tier_limits
from app.database import get_site_settings_row, save_site_settings_row
from app.features import (
    DEFAULT_ANONYMOUS_FEATURES, DEFAULT_USER_FEATURES, valid_features
)

logger = logging.getLogger("plasmid_designer.site_settings")

CACHE_TTL_SECONDS = 3.0
_cache_lock = threading.Lock()
_cache: Optional[dict] = None
_cache_at: float = 0.0
# 最近一次成功读取的设置（last-known-good）：不受 TTL 与 invalidate 影响，
# 读取失败时优先沿用，管理员收紧过的功能清单在数据库抖动时仍然生效
_last_good: Optional[dict] = None


def _fallback_defaults() -> dict:
    """从未成功读取过时的兜底：功能清单按出厂默认，注册类开关收紧。

    库不可用时整站 403 会让设计主路径（本不依赖数据库）一起瘫痪；
    注册/验证本身依赖数据库，收紧它们不影响可用性"""
    return {
        "registration_open": False,
        "email_verification_required": True,
        "anonymous_features": list(DEFAULT_ANONYMOUS_FEATURES),
        "user_features": list(DEFAULT_USER_FEATURES),
        # 层级 read 上限全部未设置 = 服务器默认（环境变量），与升级前一致
        "batch_read_limits": empty_tier_limits(),
    }


def _copy(data: dict) -> dict:
    def _one(v):
        if isinstance(v, list):
            return list(v)
        if isinstance(v, dict):
            return {k2: _one(v2) for k2, v2 in v.items()}
        return v
    return {k: _one(v) for k, v in data.items()}


def parse_batch_read_limits(raw: Optional[str]) -> dict:
    """site_settings.batch_read_limits 列（JSON 文本）→ 清洗后的层级上限"""
    try:
        return normalize_tier_limits(json.loads(raw) if raw else None)
    except (TypeError, ValueError):
        return empty_tier_limits()


def _row_to_dict(row) -> dict:
    return {
        "registration_open": bool(row.registration_open),
        "email_verification_required": bool(row.email_verification_required),
        "anonymous_features": valid_features(json.loads(row.anonymous_features or "[]")),
        "user_features": valid_features(json.loads(row.user_features or "[]")),
        "batch_read_limits": parse_batch_read_limits(getattr(row, "batch_read_limits", None)),
    }


def _fetch() -> Optional[dict]:
    """读库；失败返回 None（由 get_settings 决定兜底）"""
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
        logger.error("站点设置读取失败", exc_info=True)
        return None
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover
            pass


def get_settings(force_refresh: bool = False) -> dict:
    """站点设置（带 TTL 缓存；管理员保存后调用 invalidate）"""
    global _cache, _cache_at, _last_good
    now = time.monotonic()
    with _cache_lock:
        if not force_refresh and _cache is not None and now - _cache_at < CACHE_TTL_SECONDS:
            return _cache
    data = _fetch()
    with _cache_lock:
        if data is not None:
            _last_good = _copy(data)
        elif _last_good is not None:
            # 读取失败：沿用最近一次成功值（保留管理员的收紧）
            logger.warning("站点设置读取失败，沿用最近一次成功读取的配置")
            data = _copy(_last_good)
        else:
            logger.warning("站点设置读取失败且无可用缓存，功能清单按默认值")
            data = _fallback_defaults()
        # 兜底结果同样按 TTL 缓存，库故障时不必每个请求都去连库
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
