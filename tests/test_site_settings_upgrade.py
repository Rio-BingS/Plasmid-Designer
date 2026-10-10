"""站点设置升级与故障回归锁

- 升级：v2.4.x 库没有 features_initialized 列，列迁移若把存量行留在 FALSE，
  首次读取会把管理员收紧过的功能清单整体覆盖为全开放默认值。
- 故障：读取站点设置抛异常时必须 fail-closed，而不是按默认值放行。
"""

import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import site_settings
from app.database import Base
from app.database import models as db_models
from app.features import DEFAULT_ANONYMOUS_FEATURES, DEFAULT_USER_FEATURES


@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    """模拟 v2.4.x 库：建表后删掉 features_initialized 列"""
    eng = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    Base.metadata.create_all(bind=eng)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO site_settings (id, registration_open, email_verification_required, "
            "anonymous_features, user_features, features_initialized, feature_migrations) "
            "VALUES (1, 1, 0, :a, :u, 0, 'sequencing_batch')"),
            {"a": json.dumps(["design"]), "u": json.dumps(["design", "codon"])})
        c.execute(text("ALTER TABLE site_settings DROP COLUMN features_initialized"))
    monkeypatch.setattr(db_models, "engine", eng)
    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=eng))
    site_settings.invalidate_cache()
    yield eng
    site_settings.invalidate_cache()
    eng.dispose()


def test_upgrade_keeps_admin_restricted_feature_lists(legacy_engine):
    db_models._migrate_site_settings_table()
    db_models._migrate_feature_lists(legacy_engine)
    cols = {c["name"] for c in inspect(legacy_engine).get_columns("site_settings")}
    assert "features_initialized" in cols
    data = site_settings.get_settings(force_refresh=True)
    assert data["anonymous_features"] == ["design"], "升级把管理员限制重置为全开放"
    assert set(data["user_features"]) == {"design", "codon"}


def test_upgrade_fills_only_empty_lists_like_old_version(legacy_engine):
    with legacy_engine.begin() as c:
        c.execute(text("UPDATE site_settings SET anonymous_features = '[]' WHERE id = 1"))
    db_models._migrate_site_settings_table()
    with legacy_engine.connect() as c:
        anon, userf, flag = c.execute(text(
            "SELECT anonymous_features, user_features, features_initialized "
            "FROM site_settings WHERE id = 1")).one()
    assert bool(flag) is True
    assert set(json.loads(anon)) == set(DEFAULT_ANONYMOUS_FEATURES)
    assert set(json.loads(userf)) == {"design", "codon"}
    assert set(DEFAULT_USER_FEATURES)  # 默认清单本身非空（防御性）

