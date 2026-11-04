"""CORS_ORIGINS 配置解析回归测试

背景：pydantic-settings 对 list 类型字段会先做 JSON 解码再进 validator，
环境变量传 "*" 直接 SettingsError（2026-09 Docker 部署实机暴露）。
现字段声明为 str，解析收敛在 cors_origins_list 属性。
"""

import os
from importlib import reload

import pytest


@pytest.fixture()
def settings_cls(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    import app.config as config_module

    reload(config_module)
    return config_module.Settings


def test_default_wildcard(settings_cls):
    assert settings_cls().cors_origins_list == ["*"]


def test_plain_star(settings_cls):
    assert settings_cls(CORS_ORIGINS="*").cors_origins_list == ["*"]


def test_comma_separated(settings_cls):
    got = settings_cls(CORS_ORIGINS="https://a.com, https://b.com").cors_origins_list
    assert got == ["https://a.com", "https://b.com"]


def test_json_array(settings_cls):
    got = settings_cls(CORS_ORIGINS='["https://a.com","*"]').cors_origins_list
    assert got == ["https://a.com", "*"]


def test_empty_falls_back_to_wildcard(settings_cls):
    assert settings_cls(CORS_ORIGINS="").cors_origins_list == ["*"]
