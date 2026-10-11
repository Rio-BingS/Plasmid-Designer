"""测序记录按属主保留回归锁（审查 02 M4）

此前全库 2000 条按创建时间淘汰、不看属主：一个匿名 IP 一小时的批量
（20 次 × 120 条）就能把全站登录用户的持久化记录挤出数据库。现在：
- 每个登录用户、匿名桶各自有上限，只淘汰写入者自己桶里的最旧记录
- 全库总上限兜底：先删匿名，再从记录最多的用户那里删
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app import sequencing_store as store

pytestmark = pytest.mark.skipif(not store.db_enabled(), reason="仅 database 模式落库")

# 远未来时间：比库里其他用例留下的记录都新，淘汰结果只取决于本文件写入
_T0 = datetime(2100, 1, 1)
_seq = [0]


def _user():
    from app.auth.jwt_auth import hash_password
    from app.database import SessionLocal, create_user
    email = f"ret-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        return create_user(db, email=email, username=email.split("@")[0],
                           hashed_password=hash_password("Passw0rd!!"),
                           email_verified=True).id
    finally:
        db.close()


def _persist(owner_id):
    """直接落库一条记录；created_at 单调递增，淘汰顺序可预期"""
    _seq[0] += 1
    aid = f"seq_ret{uuid.uuid4().hex[:9]}"
    store.persist_record({
        "analysis_id": aid, "sample_name": "r", "reference": "ACGT" * 20,
        "features": [], "owner_id": owner_id,
        "created_at": (_T0 + timedelta(seconds=_seq[0])).isoformat(),
        "access_token_hash": None if owner_id else "0" * 64,
        "engine": "internal", "conclusion": "ok", "reads": [], "variants": [],
        "consensus": {"coverage_percent": 100.0}, "_trace_data": {},
    })
    return aid


def _exists(aid):
    return store.load_record_meta(aid) is not None


def _count():
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB
    db = SessionLocal()
    try:
        return db.query(SequencingAnalysisDB.id).count()
    finally:
        db.close()


@pytest.fixture
def caps(monkeypatch):
    monkeypatch.setattr(store, "MAX_DB_RECORDS", 0)
    monkeypatch.setattr(store, "MAX_DB_RECORDS_PER_OWNER", 3)
    monkeypatch.setattr(store, "MAX_DB_ANON_RECORDS", 4)
    yield monkeypatch
    # 清掉本文件写入的远未来记录，免得影响同进程其他用例的淘汰顺序
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB as M
    db = SessionLocal()
    try:
        db.query(M).filter(M.created_at >= _T0).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_anonymous_flood_does_not_evict_user_records(caps):
    alice, bob = _user(), _user()
    mine = [_persist(alice) for _ in range(3)] + [_persist(bob) for _ in range(2)]
    anon = [_persist(None) for _ in range(12)]
    assert all(_exists(a) for a in mine)
    # 匿名桶只保留最新 4 条
    assert [_exists(a) for a in anon] == [False] * 8 + [True] * 4


def test_user_over_cap_only_evicts_own_oldest(caps):
    alice, bob = _user(), _user()
    b = [_persist(bob) for _ in range(2)]
    a = [_persist(alice) for _ in range(5)]
    assert [_exists(x) for x in a] == [False, False, True, True, True]
    assert all(_exists(x) for x in b)


def test_global_ceiling_drains_anonymous_then_largest_owner(caps):
    from app.database import SessionLocal
    db = SessionLocal()
    try:  # 本用例需要匿名桶为空才能观察第二阶段；只清匿名记录
        store._delete_oldest(db, None, 10**9)
        db.commit()
    finally:
        db.close()
    caps.setattr(store, "MAX_DB_RECORDS_PER_OWNER", 100)
    caps.setattr(store, "MAX_DB_ANON_RECORDS", 100)
    big, small = _user(), _user()
    big_ids = [_persist(big) for _ in range(6)]
    small_ids = [_persist(small) for _ in range(2)]
    anon = [_persist(None) for _ in range(2)]
    ceiling = _count() - 3
    caps.setattr(store, "MAX_DB_RECORDS", ceiling)
    newest = _persist(small)  # 超出 4 条：先删 2 条匿名，再从 big 删 2 条最旧
    assert _count() == ceiling
    assert not any(_exists(a) for a in anon)
    assert all(_exists(x) for x in small_ids + [newest])
    assert [_exists(x) for x in big_ids] == [False, False] + [True] * 4


def test_defaults_are_per_owner():
    assert store.MAX_DB_RECORDS_PER_OWNER > 0 and store.MAX_DB_ANON_RECORDS > 0
    assert store.MAX_DB_RECORDS >= store.MAX_DB_RECORDS_PER_OWNER + store.MAX_DB_ANON_RECORDS
