"""限流加固回归锁

- 只有 TRUSTED_PROXIES 内的对端才能用 X-Real-IP / X-Forwarded-For 改写限流维度
- 认证档按「IP + 规范化目标邮箱」计数，不看（可随意更换的）令牌 sub
- 打包/导出下载与 NCBI 代理回到更严格的独立档位
"""

from datetime import datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import rate_limit
from app.auth.jwt_auth import ALGORITHM, SECRET_KEY
from app.main import app


def _req(peer, headers=None, method="POST", path="/api/auth/login"):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": (peer, 5555), "query_string": b""}
    return Request(scope)


# ---------------- 可信代理 ----------------

@pytest.mark.parametrize("peer", ["10.1.2.3", "172.17.0.9", "192.168.1.5", "8.8.8.8"])
def test_forwarding_headers_ignored_from_untrusted_peers(peer):
    r = _req(peer, {"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.8"})
    assert rate_limit.get_client_ip(r) == peer


def test_loopback_proxy_trusted_by_default():
    assert rate_limit.get_client_ip(_req("127.0.0.1", {"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"
    assert rate_limit.get_client_ip(_req("::1", {"X-Real-IP": "203.0.113.9"})) == "203.0.113.9"


def test_configured_proxy_cidr(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXIES", "127.0.0.1, 172.16.0.0/12")
    assert rate_limit.get_client_ip(_req("172.17.0.9", {"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"
    assert rate_limit.get_client_ip(_req("10.0.0.1", {"X-Real-IP": "203.0.113.7"})) == "10.0.0.1"


def test_xff_takes_rightmost_untrusted_hop(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXIES", "127.0.0.1,172.16.0.0/12")
    r = _req("127.0.0.1", {"X-Forwarded-For": "1.1.1.1, 203.0.113.5, 172.18.0.2"})
    assert rate_limit.get_client_ip(r) == "203.0.113.5"


def test_invalid_trusted_proxy_entries_ignored(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXIES", "not-an-ip, 127.0.0.1")
    assert rate_limit.get_client_ip(_req("127.0.0.1", {"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"
