"""站点设置升级与故障回归锁

- 升级：v2.4.x 库没有 features_initialized 列，列迁移若把存量行留在 FALSE，
  首次读取会把管理员收紧过的功能清单整体覆盖为全开放默认值。
- 故障：读取站点设置抛异常时沿用最近一次成功读取的值（保留管理员的
  收紧）；从未读成功过才按默认功能清单，不能让设计主路径整体 403。
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


class _Row:
    registration_open = True
    email_verification_required = False
    features_initialized = True

    def __init__(self, anon, user):
        self.anonymous_features = json.dumps(anon)
        self.user_features = json.dumps(user)


def _boom(_db):
    raise RuntimeError("db down")


@pytest.fixture
def fresh_settings(monkeypatch):
    """清空 TTL 缓存与 last-known-good，用例结束后再清一次"""
    site_settings.invalidate_cache()
    monkeypatch.setattr(site_settings, "_last_good", None, raising=False)
    yield
    site_settings.invalidate_cache()


def test_read_failure_without_history_falls_back_to_defaults(monkeypatch, fresh_settings):
    monkeypatch.setattr(site_settings, "get_site_settings_row", _boom)
    data = site_settings.get_settings(force_refresh=True)
    assert set(data["anonymous_features"]) == set(DEFAULT_ANONYMOUS_FEATURES)
    assert set(data["user_features"]) == set(DEFAULT_USER_FEATURES)
    # 注册本身依赖数据库，兜底时仍收紧
    assert data["registration_open"] is False
    assert data["email_verification_required"] is True


def test_read_failure_keeps_last_good_admin_restrictions(monkeypatch, fresh_settings):
    monkeypatch.setattr(site_settings, "get_site_settings_row",
                        lambda _db: _Row([], ["design"]))
    first = site_settings.get_settings(force_refresh=True)
    assert first["anonymous_features"] == []
    monkeypatch.setattr(site_settings, "get_site_settings_row", _boom)
    # invalidate（管理员保存会调用）不应丢掉 last-known-good
    site_settings.invalidate_cache()
    data = site_settings.get_settings(force_refresh=True)
    assert data["anonymous_features"] == [], "库抖动时管理员关闭的功能被重新打开"
    assert data["user_features"] == ["design"]
    assert data["registration_open"] is True
    # 调用方改动返回值不得污染 last-known-good
    data["user_features"].append("codon")
    assert site_settings.get_settings(force_refresh=True)["user_features"] == ["design"]


def test_read_failure_result_is_cached_for_ttl(monkeypatch, fresh_settings):
    calls = []

    def _count(_db):
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(site_settings, "get_site_settings_row", _count)
    site_settings.get_settings()
    site_settings.get_settings()
    assert len(calls) == 1, "库故障时每个请求都去连库"


def test_db_down_does_not_403_design_main_path(monkeypatch, fresh_settings):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(site_settings, "get_site_settings_row", _boom)
    client = TestClient(app)
    r = client.get("/api/codon-tables")
    assert r.status_code == 200, r.text
    r = client.post("/api/design", json={"sequence": "MK", "sequence_type": "protein"})
    assert r.status_code != 403, r.text


def test_db_down_keeps_admin_closed_feature_403(monkeypatch, fresh_settings):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.features import ALL_FEATURES

    monkeypatch.setattr(site_settings, "get_site_settings_row",
                        lambda _db: _Row([f for f in ALL_FEATURES if f != "codon"],
                                         list(ALL_FEATURES)))
    site_settings.get_settings(force_refresh=True)
    monkeypatch.setattr(site_settings, "get_site_settings_row", _boom)
    site_settings.invalidate_cache()
    client = TestClient(app)
    assert client.get("/api/codon-tables").status_code == 403
