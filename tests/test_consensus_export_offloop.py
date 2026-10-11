"""共识导出的 N 屏蔽与排版不得阻塞事件循环（审查 02 M5）

200 kb 共识序列配上碎片化覆盖区间时，逐碱基扫描全部区间是
O(len × 区间数) 的秒级 CPU；此前在 async 路由里同步执行。
"""

import asyncio
import json
import os
import random
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.routes.sequencing_routes as seq_routes  # noqa: E402
from app.main import app  # noqa: E402
from abif_utils import make_ab1  # noqa: E402


@pytest.fixture(autouse=True)
def _all_features_open(monkeypatch):
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _naive_mask(seq, segs):
    return "".join("N" if not any(s <= i + 1 <= e for s, e in segs) else b
                   for i, b in enumerate(seq))


def test_genbank_export_renders_off_event_loop(monkeypatch):
    client = TestClient(app)
    ref = ">ref\n" + "AAG" * 40
    blob = make_ab1("AAG" * 20, [40] * 60)
    r = client.post("/api/sequencing/analyze",
                    files={"reference": ("ref.fasta", ref, "text/plain"),
                           "reads": ("r1.ab1", blob, "application/octet-stream")})
    assert r.status_code == 200, r.text
    aid = r.json()["analysis_id"]
    hdr = {"X-Access-Token": r.json()["access_token"]}

    seen = []
    real = seq_routes.format_locus_line

    def probe(*a, **kw):
        seen.append(_on_event_loop())
        return real(*a, **kw)

    monkeypatch.setattr(seq_routes, "format_locus_line", probe)
    gb = client.get(f"/api/sequencing/analyses/{aid}/consensus/export",
                    params={"format": "genbank", "covered_only": "true"}, headers=hdr)
    assert gb.status_code == 200
    assert "N-masked" in gb.text
    assert seen == [False]
    client.delete(f"/api/sequencing/analyses/{aid}", headers=hdr)


def test_mask_matches_reference_semantics():
    rnd = random.Random(7)
    seq = "".join(rnd.choice("ACGT") for _ in range(500))
    cases = [[], [(1, 500)], [(1, 1)], [(500, 500)], [(10, 20), (15, 30)],
             [(0, 5)], [(490, 600)], [(30, 10)]]
    for _ in range(20):
        cases.append([tuple(sorted((rnd.randint(1, 500), rnd.randint(1, 500))))
                      for _ in range(rnd.randint(1, 6))])
    for segs in cases:
        assert seq_routes._mask_uncovered(seq, segs) == _naive_mask(seq, segs), segs


def test_mask_is_linear_for_fragmented_coverage():
    seq = "ACGT" * 50_000  # 200 kb
    segs = [(i, i + 99) for i in range(1, 200_000, 200)]  # 1000 段
    t0 = time.perf_counter()
    out = seq_routes._mask_uncovered(seq, segs)
    assert time.perf_counter() - t0 < 1.0
    assert len(out) == len(seq) and out[:100] == seq[:100] and out[100] == "N"
