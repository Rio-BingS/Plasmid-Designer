"""限流计数表淘汰回归锁（审查 02 M1）

- key 超上限后，每次放行不得再全表排序（旧实现 ~5 ms/请求，单进程 ~192 req/s）
- 洪泛普通 key 不能把按账号的登录失败计数 rate:login_fail:* 挤出去
- 淘汰顺序按最近活跃：仍在活跃的 key 不被先淘汰
"""

import time

import pytest

from app import rate_limit
from app.rate_limit import InMemoryRateLimiter


def _fill(lim, n, prefix="rate:ip:2001:db8::", window=60):
    for i in range(n):
        lim.is_allowed(f"{prefix}{i:x}:default", 100, window)


def test_over_cap_eviction_is_cheap():
    lim = InMemoryRateLimiter()
    lim.MAX_TRACKED_KEYS = 10_000
    _fill(lim, 10_000)
    n = 2000
    t = time.perf_counter()
    _fill(lim, n, prefix="rate:ip:2001:db8:1::")
    per_req = (time.perf_counter() - t) / n
    # 旧实现 ~5 ms/请求（10k 规模全表排序）；O(1) 淘汰远低于 1 ms
    assert per_req < 0.001, f"超限后单请求 {per_req * 1000:.2f} ms"
    assert len(lim._requests) == 10_000


def test_eviction_keeps_recently_active_keys():
    lim = InMemoryRateLimiter()
    lim.MAX_TRACKED_KEYS = 100
    lim.is_allowed("rate:ip:1.1.1.1:upload", 20, 3600)
    _fill(lim, 99)
    # 1.1.1.1 再次活跃 → 移到表尾，新 key 先挤掉最旧的洪泛 key
    lim.is_allowed("rate:ip:1.1.1.1:upload", 20, 3600)
    _fill(lim, 50, prefix="rate:ip:2001:db8:2::")
    assert "rate:ip:1.1.1.1:upload" in lim._requests
    assert lim.get_remaining("rate:ip:1.1.1.1:upload", 20, 3600) == 18


def test_login_fail_counter_survives_key_flood():
    lim = InMemoryRateLimiter()
    lim.MAX_TRACKED_KEYS = 100
    key = "rate:login_fail:victim@example.com"
    for _ in range(3):
        assert lim.is_allowed(key, 3, 900)[0]
    assert lim.is_allowed(key, 3, 900)[0] is False
    # 攻击者用大量新 IP / 邮箱制造普通 key，试图把失败计数冲掉
    _fill(lim, 1000)
    _fill(lim, 1000, prefix="rate:auth:6.6.6.6:x")
    ok, retry = lim.is_allowed(key, 3, 900)
    assert ok is False and retry > 0


def test_login_fail_counter_survives_flood_via_global_limiter(monkeypatch):
    monkeypatch.setitem(rate_limit.RATE_LIMITS, "login_account", {"requests": 2, "window": 900})
    monkeypatch.setattr(rate_limit.limiter, "MAX_TRACKED_KEYS", 50)
    email = "Victim@Example.com "
    assert rate_limit.acquire_login_attempt(email) == 0
    assert rate_limit.acquire_login_attempt(email) == 0
    _fill(rate_limit.limiter, 500)
    assert rate_limit.acquire_login_attempt(email) > 0
    rate_limit.reset_login_failures(email)
    assert rate_limit.acquire_login_attempt(email) == 0


def test_protected_keys_only_expired_evicted(monkeypatch):
    lim = InMemoryRateLimiter()
    lim.MAX_PROTECTED_KEYS = 5
    lim.MAX_PROTECTED_KEYS_HARD = 1000
    now = [1_000_000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])
    for i in range(5):
        lim.is_allowed(f"rate:login_fail:old{i}@x", 10, 900)
    now[0] += 901  # old* 已全部过期
    for i in range(10):
        lim.is_allowed(f"rate:login_fail:new{i}@x", 10, 900)
    keys = set(lim._requests.protected)
    assert not any(k.startswith("rate:login_fail:old") for k in keys)
    # 未过期的 new* 一个都不淘汰（软上限只回收过期项）
    assert {f"rate:login_fail:new{i}@x" for i in range(10)} <= keys


def test_protected_hard_cap_bounds_memory():
    lim = InMemoryRateLimiter()
    lim.MAX_PROTECTED_KEYS = 5
    lim.MAX_PROTECTED_KEYS_HARD = 20
    for i in range(50):
        lim.is_allowed(f"rate:login_fail:u{i}@x", 10, 900)
    assert len(lim._requests.protected) == 20


def test_get_remaining_does_not_create_keys():
    lim = InMemoryRateLimiter()
    assert lim.get_remaining("rate:ip:9.9.9.9:default", 100, 60) == 100
    assert "rate:ip:9.9.9.9:default" not in lim._requests
    assert len(lim._requests) == 0


def test_clear_resets_both_tables():
    lim = InMemoryRateLimiter()
    lim.is_allowed("rate:ip:1.2.3.4:default", 1, 60)
    lim.is_allowed("rate:login_fail:a@b", 1, 900)
    lim._requests.clear()
    assert len(lim._requests) == 0
    assert lim.is_allowed("rate:login_fail:a@b", 1, 900)[0]


@pytest.mark.parametrize("window", [60, 3600])
def test_window_semantics_unchanged(monkeypatch, window):
    lim = InMemoryRateLimiter()
    now = [5_000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])
    assert lim.is_allowed("k", 2, window) == (True, 0)
    now[0] += 1
    assert lim.is_allowed("k", 2, window) == (True, 0)
    ok, retry = lim.is_allowed("k", 2, window)
    assert ok is False and retry == window  # 最早一次 + window - now + 1
    now[0] += window
    assert lim.is_allowed("k", 2, window)[0]
