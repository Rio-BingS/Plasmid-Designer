"""安全加固回归：CSRF 豁免路径 fail-closed、XFF 畸形跳不再拖垮整条链

- 豁免表此前按 path.rstrip("/") 精确匹配，但含 //、. / ..、%2e 的路径可能在
  代理/框架层被归一化成另一个路由，先豁免再执行等于开 CSRF 口子；
- XFF 解析遇到无法解析的跳会 break 回退到代理 IP，所有客户端挤进同一个
  限流桶（一个畸形条目即可触发）。
"""
from types import SimpleNamespace

from app.auth.middleware import CsrfMiddleware
from app.rate_limit import get_client_ip


def test_exempt_path_requires_literal_match():
    is_exempt = CsrfMiddleware._is_exempt_path
    assert is_exempt("/api/auth/login")
    assert is_exempt("/api/auth/login/")          # 末尾斜杠仍豁免
    assert is_exempt("/api/auth/verify-email")

    # 只有「归一化后才会等于豁免路径」的写法一律不豁免
    assert not is_exempt("/api/auth//login")
    assert not is_exempt("/api/auth/./login")
    assert not is_exempt("/api/auth/login/../designs")
    assert not is_exempt("/api/auth/%2elogin")
    assert not is_exempt("/api/auth/login%2f..%2fdesigns")
    assert not is_exempt("/api/auth/login%5c..%5cdesigns")
    assert not is_exempt("/api/designs")


def _req(peer, xff=None, real=None):
    headers = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    if real is not None:
        headers["X-Real-IP"] = real
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_untrusted_peer_ignores_forwarded_headers():
    """对端不是可信代理时，转发头一概不采信"""
    assert get_client_ip(_req("198.51.100.7", xff="203.0.113.9")) == "198.51.100.7"


def test_malformed_hop_does_not_collapse_chain():
    """中间夹一个无法解析的跳时，仍取最右侧的有效非可信地址"""
    assert get_client_ip(
        _req("127.0.0.1", xff="203.0.113.9, garbage, 127.0.0.1")) == "203.0.113.9"


def test_all_trusted_or_invalid_falls_back_to_peer():
    assert get_client_ip(_req("127.0.0.1", xff="127.0.0.1, ::1")) == "127.0.0.1"
    assert get_client_ip(_req("127.0.0.1", xff="not-an-ip")) == "127.0.0.1"


def test_x_real_ip_preferred_for_trusted_peer():
    assert get_client_ip(
        _req("127.0.0.1", xff="203.0.113.9", real="198.51.100.5")) == "198.51.100.5"
