"""BoundedMirror（designs_db / batch_jobs 内存镜像治理）单元测试

第二轮审计 P2：内存镜像此前无界增长，匿名持续提交合法请求即可线性撑大
进程内存。治理模式与 sequencing _ANALYSES 一致：容量上限 + TTL + 访问
续期，只清镜像（存储层仍是权威，淘汰后读取自动回源）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import mirror_store  # noqa: E402
from app.mirror_store import BoundedMirror  # noqa: E402


class _FakeClock:
    """可控单调时钟：Windows time.monotonic 分辨率可达 ~15ms，
    真实 sleep 撑不出可分辨的时间戳先后"""

    def __init__(self):
        self.now = 1000.0

    def advance(self, dt):
        self.now += dt

    def monotonic(self):
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = _FakeClock()
    monkeypatch.setattr(mirror_store.time, "monotonic", c.monotonic)
    return c


def test_capacity_cap_evicts_oldest(clock):
    m = BoundedMirror(max_items=3)
    for i in range(5):
        m[f"k{i}"] = i
        clock.advance(1)
    assert len(m) == 3
    # 最旧的两个被淘汰，最新的三个保留
    assert set(m) == {"k2", "k3", "k4"}


def test_access_refreshes_recency(clock):
    m = BoundedMirror(max_items=3)
    for i in range(3):
        m[f"k{i}"] = i
        clock.advance(1)
    _ = m["k0"]  # 访问续期：k0 变成最新
    clock.advance(1)
    m["k3"] = 3  # 触发淘汰，应删的是 k1（最久未访问）
    assert set(m) == {"k0", "k2", "k3"}


def test_ttl_expiry(clock):
    m = BoundedMirror(max_items=10, ttl_seconds=5)
    m["old"] = 1
    clock.advance(6)
    m["new"] = 2  # 写入触发 sweep，old 超 TTL 被清
    assert "old" not in m
    assert m["new"] == 2


def test_ttl_not_expired_entry_survives(clock):
    m = BoundedMirror(max_items=10, ttl_seconds=5)
    m["fresh"] = 1
    clock.advance(3)
    m["other"] = 2
    assert "fresh" in m and "other" in m


def test_dict_semantics_preserved():
    """len()/in/get/pop/clear 原生语义全部可用（测试与旧代码依赖这些）。"""
    m = BoundedMirror()
    m["a"] = 1
    assert len(m) == 1 and "a" in m
    assert m.get("a") == 1 and m.get("b") is None
    assert m.pop("a") == 1 and "a" not in m
    m["c"] = 3
    m.clear()
    assert len(m) == 0
