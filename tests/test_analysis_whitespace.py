"""分析接口统一去空白（审查 06 M2 / 04 I1）

粘贴的多行序列此前只有前端的酶切消化去空白：/restriction-sites 跨行
漏检位点、/orfs 长度与坐标被换行撑大、/gc-analysis 被空白稀释、
/digest 直连 API 时片段长度含换行。后端请求模型现统一先去空白。
"""

import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis_routes import router


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _wrap(seq: str, width: int = 60, sep: str = "\n") -> str:
    return sep.join(seq[i:i + width] for i in range(0, len(seq), width))


def _post(client, path, body):
    r = client.post(f"/api/analysis/{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _orf_seq() -> str:
    rnd = random.Random(7)
    body = "".join(rnd.choice(["GCT", "GGC", "AAA", "CTG", "GAA"]) for _ in range(60))
    return "CC" + "ATG" + body + "TAA" + "CC"


def test_restriction_sites_ignores_line_breaks(client):
    flat = "AAAAAGAATTCAAAAA"
    messy = "AAAAAGAAT\r\nTC AA\tAAA"
    got = _post(client, "restriction-sites", {"sequence": messy, "enzymes": ["EcoRI"]})
    want = _post(client, "restriction-sites", {"sequence": flat, "enzymes": ["EcoRI"]})
    assert got["total"] == 1
    assert got["sites"] == want["sites"]


def test_orfs_ignore_line_breaks(client):
    flat = _orf_seq()
    got = _post(client, "orfs", {"sequence": _wrap(flat, sep="\r\n"), "min_length": 90})
    want = _post(client, "orfs", {"sequence": flat, "min_length": 90})
    assert got["orfs"] and got["orfs"] == want["orfs"]
    assert got["orfs"][0]["length"] == 186


def test_gc_analysis_ignores_line_breaks(client):
    flat = "GC" * 100
    got = _post(client, "gc-analysis", {"sequence": _wrap(flat, 60), "window_size": 50})
    assert got["total_gc_content"] == 100.0


def test_digest_ignores_line_breaks(client):
    flat = "A" * 40 + "GAATTC" + "T" * 40
    got = _post(client, "digest", {"sequence": _wrap(flat, 10), "enzymes": ["EcoRI"]})
    assert [f["size"] for f in got["fragments"]] == [41, 45]


def test_analyze_ignores_cr_and_tab(client):
    flat = "GC" * 100
    got = _post(client, "analyze", {"sequence": _wrap(flat, 50, "\r\n\t")})
    assert got["sequence_length"] == 200


def test_compatibility_strips_both_sequences(client):
    def _check(ins, vec):
        return _post(client, "compatibility",
                     {"insert_sequence": ins, "vector_sequence": vec, "enzymes": ["EcoRI"]})

    flat = _check("CCCCGAATTCAAAA", "AAGAATTCAA")
    assert _check("CCCC\r\nGAAT\tTCAAAA", "AAGAA\r\n TTCAA") == flat
    assert flat["issues"], "对照组应报出插入片段内的 EcoRI 位点"


def test_whitespace_does_not_count_towards_length_limit(client):
    from app.analysis_routes import MAX_ANALYSIS_SEQ_BP
    seq = "A" * MAX_ANALYSIS_SEQ_BP
    r = client.post("/api/analysis/gc-analysis", json={
        "sequence": _wrap(seq, 60), "window_size": 1000, "step_size": 1000})
    assert r.status_code == 200, r.text
