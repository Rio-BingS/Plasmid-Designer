"""限流加固回归锁

- 只有 TRUSTED_PROXIES 内的对端才能用 X-Real-IP / X-Forwarded-For 改写限流维度
- 认证档按「IP + 规范化目标邮箱」计数，不看（可随意更换的）令牌 sub
- 打包/导出下载与 NCBI 代理回到更严格的独立档位
"""

from datetime import datetime, timedelta, timezone

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


# ---------------- 认证档 key ----------------

def test_auth_key_ignores_token_sub():
    forged = jwt.encode({"sub": "anything-1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        SECRET_KEY, algorithm=ALGORITHM)
    from app.auth.middleware import AuthStateMiddleware
    r = _req("8.8.8.8", {"Authorization": f"Bearer {forged}"})
    r.state.user = AuthStateMiddleware._resolve_user(r)
    key = rate_limit.get_rate_limit_key(r, "auth", "victim@example.com")
    assert "anything-1" not in key
    assert key == "rate:auth:8.8.8.8:victim@example.com"


def _login(c, email, token=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"}, headers=h)


def test_login_limit_per_ip_and_email_not_bypassed_by_tokens(monkeypatch):
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth", {"requests": 3, "window": 60})
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth_ip", {"requests": 100, "window": 60})
    c = TestClient(app)
    codes = []
    for i in range(5):
        tok = jwt.encode({"sub": f"rotating-{i}", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                         SECRET_KEY, algorithm=ALGORITHM)
        codes.append(_login(c, "  Victim@Example.com ", tok).status_code)
    assert codes[:3] != [429] * 3 and codes[3:] == [429, 429], codes
    # 另一个目标邮箱有独立配额
    assert _login(c, "other@example.com").status_code != 429


def test_auth_ip_aggregate_limit(monkeypatch):
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth", {"requests": 100, "window": 60})
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth_ip", {"requests": 4, "window": 60})
    c = TestClient(app)
    codes = [_login(c, f"spray{i}@example.com").status_code for i in range(6)]
    assert codes[4:] == [429, 429], codes


# ---------------- 重型只读档 ----------------

@pytest.mark.parametrize("method,path,bucket", [
    ("GET", "/api/design/batch/b1/download", "export"),
    ("GET", "/api/design/batch/b1/report", "export"),
    ("GET", "/api/design/d1/download/genbank", "export"),
    ("GET", "/api/design/d1/download/primers", "export"),
    ("GET", "/api/analysis/design/d1/export", "export"),
    ("GET", "/api/sequencing/batches/seqbatch_x/report", "export"),
    ("GET", "/api/sequencing/analyses/seq_x/consensus/export", "export"),
    ("GET", "/api/vectors/search/ncbi", "ncbi"),
    ("GET", "/api/vectors/preview/ncbi/NC_000913", "ncbi"),
    # 轮询类只读仍是 default
    ("GET", "/api/design/d1", "default"),
    ("GET", "/api/design/batch/b1", "default"),
    ("GET", "/api/design/d1/map", "default"),
    ("GET", "/api/sequencing/analyses/seq_x", "default"),
])
def test_heavy_reads_have_own_buckets(method, path, bucket):
    assert rate_limit.classify_endpoint(method, path) == bucket
    assert rate_limit.RATE_LIMITS[bucket]["requests"] <= rate_limit.RATE_LIMITS["default"]["requests"]


def test_export_bucket_stricter_than_default():
    assert rate_limit.RATE_LIMITS["export"]["requests"] < rate_limit.RATE_LIMITS["default"]["requests"]
    assert rate_limit.RATE_LIMITS["ncbi"]["requests"] < rate_limit.RATE_LIMITS["default"]["requests"]


# ---------------- IPv6 前缀聚合（审查 02 H1） ----------------

@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("2001:db8:0:1::1", "2001:db8:0:1::/64"),
    ("2001:db8:0:1:ffff:ffff:ffff:ffff", "2001:db8:0:1::/64"),
    ("2001:DB8:0:1::abcd", "2001:db8:0:1::/64"),
    ("fe80::1%eth0", "fe80::/64"),
    ("unknown", "unknown"),
    ("testclient", "testclient"),
])
def test_normalize_ip_for_key(raw, expected):
    assert rate_limit.normalize_ip_for_key(raw) == expected


def test_ipv6_same_64_shares_bucket():
    k1 = rate_limit.get_rate_limit_key(_req("2001:db8::1", path="/api/design"), "design")
    k2 = rate_limit.get_rate_limit_key(_req("2001:db8::2", path="/api/design"), "design")
    k3 = rate_limit.get_rate_limit_key(_req("2001:db8:0:1::1", path="/api/design"), "design")
    assert k1 == k2 == "rate:ip:2001:db8::/64:design"
    assert k3 != k1


def test_ipv6_auth_key_aggregated_and_ipv4_mapped():
    k1 = rate_limit.get_rate_limit_key(_req("2001:db8::1"), "auth", "v@example.com")
    k2 = rate_limit.get_rate_limit_key(_req("2001:db8::beef"), "auth", "v@example.com")
    assert k1 == k2 == "rate:auth:2001:db8::/64:v@example.com"
    k4 = rate_limit.get_rate_limit_key(_req("::ffff:8.8.8.8"), "auth", "v@example.com")
    assert k4 == rate_limit.get_rate_limit_key(_req("8.8.8.8"), "auth", "v@example.com")


def test_ipv6_forwarded_client_aggregated():
    # 可信代理透传的 IPv6 真实地址同样按 /64 聚合
    r1 = _req("127.0.0.1", {"X-Real-IP": "2001:db8:aa::1"}, path="/api/design")
    r2 = _req("127.0.0.1", {"X-Real-IP": "2001:db8:aa::99"}, path="/api/design")
    assert rate_limit.get_rate_limit_key(r1, "design") == rate_limit.get_rate_limit_key(r2, "design")


def test_ipv6_rotation_within_64_hits_auth_ip_limit(monkeypatch):
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth", {"requests": 100, "window": 60})
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth_ip", {"requests": 3, "window": 60})
    # TestClient 无法设置对端地址，经可信回环代理用 X-Real-IP 模拟 IPv6 客户端
    c = TestClient(app, client=("127.0.0.1", 50000))
    codes = [c.post("/api/auth/login", json={"email": f"s{i}@example.com", "password": "x" * 8},
                    headers={"X-Real-IP": f"2001:db8:5::{i + 1:x}"}).status_code
             for i in range(5)]
    assert codes[3:] == [429, 429], codes


# ---------------- 按账号的登录失败闸（审查 02 H1） ----------------

_GOOD_PASS = "Passw0rd!!"


def _new_account():
    import uuid
    from app.auth.jwt_auth import hash_password
    from app.database import SessionLocal, create_user
    email = f"acct-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        create_user(db, email=email, username=email.split("@")[0],
                    hashed_password=hash_password(_GOOD_PASS), email_verified=True)
    finally:
        db.close()
    return email


@pytest.fixture
def account_limit(monkeypatch):
    # 放宽含 IP 的两档，只让按账号的闸生效
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth", {"requests": 100, "window": 60})
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "auth_ip", {"requests": 100, "window": 60})
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "login_account", {"requests": 3, "window": 900})
    return TestClient(app, client=("127.0.0.1", 50000))


def _login_from(c, ip, email, password="wrong-pass-1"):
    # 经可信回环代理用 X-Real-IP 模拟每次换一个客户端 IP
    return c.post("/api/auth/login", json={"email": email, "password": password},
                  headers={"X-Real-IP": ip})


def test_login_account_limit_independent_of_ip(account_limit):
    c, email = account_limit, _new_account()
    codes = [_login_from(c, f"198.51.100.{i + 1}", email).status_code for i in range(3)]
    assert codes == [401, 401, 401]
    r = _login_from(c, "203.0.113.200", email)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    body = r.json()
    assert set(body) == {"detail", "retry_after"} and body["retry_after"] > 0
    # 锁定期内即使口令正确也拦截（否则 200/429 之差就是口令探针）
    assert _login_from(c, "203.0.113.201", email, _GOOD_PASS).status_code == 429
    # 其他账号不受影响；邮箱大小写/空白归一到同一账号
    assert _login_from(c, "203.0.113.202", _new_account()).status_code == 401
    assert _login_from(c, "203.0.113.203", f"  {email.upper()} ").status_code == 429


def test_login_account_limit_does_not_leak_existence(account_limit):
    c = account_limit
    existing, missing = _new_account(), "nobody-here@example.com"
    seq_a = [_login_from(c, f"198.51.100.{i + 1}", existing) for i in range(4)]
    seq_b = [_login_from(c, f"198.51.100.{i + 1}", missing) for i in range(4)]
    assert [r.status_code for r in seq_a] == [r.status_code for r in seq_b] == [401, 401, 401, 429]
    assert seq_a[0].json() == seq_b[0].json()
    assert set(seq_a[3].json()) == set(seq_b[3].json())
    assert seq_a[3].headers["X-RateLimit-Limit"] == seq_b[3].headers["X-RateLimit-Limit"]


def test_login_success_resets_account_failures(account_limit):
    c, email = account_limit, _new_account()
    assert [_login_from(c, "198.51.100.1", email).status_code for _ in range(2)] == [401, 401]
    assert _login_from(c, "198.51.100.1", email, _GOOD_PASS).status_code == 200
    # 成功后计数清零：又可失败 2 次，再成功一次仍放行
    assert [_login_from(c, "198.51.100.2", email).status_code for _ in range(2)] == [401, 401]
    assert _login_from(c, "198.51.100.3", email, _GOOD_PASS).status_code == 200


def test_login_account_default_tier_configured():
    cfg = rate_limit.RATE_LIMITS["login_account"]
    assert 0 < cfg["requests"] <= 20 and cfg["window"] >= 300
