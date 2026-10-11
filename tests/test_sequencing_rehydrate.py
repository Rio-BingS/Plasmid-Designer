"""测序记录回灌回归锁（审查 02 M3）

内存缓存未命中时从数据库回灌：
- 必须先鉴权；无权请求不得解压峰图、不得写入内存缓存
- 回灌与登记共用容量淘汰，驻留记录数/峰图数不超过 MAX_STORED/MAX_TRACE_STORED
"""

import pytest
from fastapi.testclient import TestClient

from app import sequencing_store
from app.main import app
from app.routes import sequencing_routes as sr

pytestmark = pytest.mark.skipif(not sequencing_store.db_enabled(),
                                reason="回灌只在 database 模式下发生")


def _fake_result(n_traces=1):
    return {"engine": "internal", "conclusion": "ok", "reads": [], "variants": [],
            "consensus": {"sequence": "ACGT" * 20, "coverage_percent": 100.0},
            "coverage_ranges": [], "errors": [],
            "traces": [{"A": list(range(200)), "C": [], "G": [], "T": []}] * n_traces}


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(sr, "MAX_STORED", 6)
    monkeypatch.setattr(sr, "MAX_TRACE_STORED", 3)
    with sr._STORE_LOCK:
        sr._ANALYSES.clear()
    yield
    with sr._STORE_LOCK:
        sr._ANALYSES.clear()


def _make(n):
    out = []
    for i in range(n):
        aid, tok, _ = sr._register_analysis(f"s{i}", "ACGT" * 20, [], _fake_result())
        out.append((aid, tok))
    with sr._STORE_LOCK:
        sr._ANALYSES.clear()
    return out


def _cached_stats(ids):
    with sr._STORE_LOCK:
        cached = [a for a in ids if a in sr._ANALYSES]
        with_trace = [a for a in cached if sr._ANALYSES[a].get("_trace_data")]
    return cached, with_trace


def test_unauthorized_rehydrate_does_no_heavy_work(small_caps, monkeypatch):
    recs = _make(8)
    calls = []
    real = getattr(sequencing_store, "load_trace", None)
    monkeypatch.setattr(sequencing_store, "load_trace",
                        lambda aid: calls.append(aid) or real(aid), raising=False)
    c = TestClient(app)
    codes = {c.get(f"/api/sequencing/analyses/{aid}").status_code for aid, _ in recs}
    codes |= {c.get(f"/api/sequencing/analyses/{aid}/trace/0",
                    headers={"X-Access-Token": "wrong"}).status_code for aid, _ in recs}
    assert codes == {403}
    assert calls == []  # 无权请求不解压峰图
    cached, _ = _cached_stats([a for a, _ in recs])
    assert cached == []  # 也不驻留内存


def test_authorized_rehydrate_respects_caps(small_caps):
    recs = _make(10)
    c = TestClient(app)
    for aid, tok in recs:
        r = c.get(f"/api/sequencing/analyses/{aid}", headers={"X-Access-Token": tok})
        assert r.status_code == 200
    ids = [a for a, _ in recs]
    cached, with_trace = _cached_stats(ids)
    assert len(cached) <= sr.MAX_STORED
    assert len(with_trace) <= sr.MAX_TRACE_STORED
    # 最近回灌的记录保留峰图，可直接取
    last_aid, last_tok = recs[-1]
    r = c.get(f"/api/sequencing/analyses/{last_aid}/trace/0",
              headers={"X-Access-Token": last_tok})
    assert r.status_code == 200 and r.json()["A"][:3] == [0, 1, 2]


def test_rehydrate_missing_is_404(small_caps):
    c = TestClient(app)
    assert c.get("/api/sequencing/analyses/seq_doesnotexist").status_code == 404


def test_load_record_compat_includes_trace(small_caps):
    (aid, _tok), = _make(1)
    rec = sequencing_store.load_record(aid)
    assert rec["analysis_id"] == aid and rec["_trace_data"][0]["A"][:2] == [0, 1]
    meta = sequencing_store.load_record_meta(aid)
    assert "_trace_data" not in meta and meta.get("access_token_hash")
