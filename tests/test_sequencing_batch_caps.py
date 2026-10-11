"""批量测序每组/整批 read 数上限回归锁（审查 02 M6）

批量此前只限文件总数与分组数，单个分组可塞近 400 条 read，一个请求就能
占住并发槽数十分钟。超限须在任何比对开始前以 413 + 中文提示拒绝。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import sequencing_batch
from app.main import app
from app.routes import sequencing_routes as sr
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"
REF_B = "ATGCCCGGT" * 12 + "TAA"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    monkeypatch.setattr(sr, "MAX_BATCH_READS_PER_GROUP", 2, raising=False)
    monkeypatch.setattr(sr, "MAX_BATCH_TOTAL_READS", 3, raising=False)
    calls = []
    real = sequencing_batch.analyze
    monkeypatch.setattr(sequencing_batch, "analyze",
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))
    return calls


def _ref(name, seq):
    return ("files", (f"{name}.fasta", f">{name}\n{seq}\n".encode(),
                      "application/octet-stream"))


def _read(name, seq):
    return ("files", (name, make_ab1(seq, [40] * len(seq)), "application/octet-stream"))


def _post(files):
    return TestClient(app).post("/api/sequencing/analyze-batch", files=files,
                                data={"min_q": "20"})


def test_reads_per_group_cap_413_before_any_alignment(_env):
    files = [_ref("PA", REF_A)] + [_read(f"PA-{i}.ab1", REF_A) for i in range(3)]
    r = _post(files)
    assert r.status_code == 413, r.text
    detail = r.json()["detail"]
    assert "PA" in detail and "3 条" in detail and "每组上限（2 条）" in detail
    assert _env == []                      # 一条都没比对


def test_total_reads_cap_413(_env):
    files = [_ref("PA", REF_A), _ref("PB", REF_B),
             _read("PA-1.ab1", REF_A), _read("PA-2.ab1", REF_A),
             _read("PB-1.ab1", REF_B), _read("PB-2.ab1", REF_B)]
    r = _post(files)
    assert r.status_code == 413, r.text
    assert "共有 4 条" in r.json()["detail"] and "拆分为多个批次" in r.json()["detail"]
    assert _env == []


def test_within_caps_and_unreferenced_reads_not_counted(_env):
    # 无图谱的 read 不跑比对，不计入上限
    files = [_ref("PA", REF_A), _read("PA-1.ab1", REF_A), _read("PA-2.ab1", REF_A),
             _read("ZZ-1.ab1", REF_B), _read("ZZ-2.ab1", REF_B)]
    r = _post(files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [it["status"] for it in body["items"]] == ["analyzed"]
    assert len(_env) == 1

