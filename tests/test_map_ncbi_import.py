"""/api/map/import/ncbi：accession 校验、efetch 参数、错误映射、缓存、限流档位。

网络一律打桩：urllib.request.urlopen 在本文件里被替换成抛错的哨兵，
任何漏网的真实外呼都会让用例失败。"""

import io
import json
import socket
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app import site_settings
from app.cache import cache
from app.features import ALL_FEATURES
from core.plasmid_map import ncbi
from core.plasmid_map.parsers import MapParseError

SEQ = ("ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA" * 5)[:300]


def _gb(name="L09137", length=300, topology="circular"):
    origin = "".join(
        f"{i + 1:>9} " + " ".join(SEQ[i:i + 60][j:j + 10].lower() for j in range(0, 60, 10)) + "\n"
        for i in range(0, 300, 60))
    return (
        f"LOCUS       {name:<16}{length:>12} bp    DNA     {topology:<8} SYN 01-JAN-2026\n"
        "DEFINITION  test record.\n"
        f"ACCESSION   {name}\n"
        "FEATURES             Location/Qualifiers\n"
        "     CDS             1..60\n"
        "                     /label=\"testCDS\"\n"
        "ORIGIN\n" + origin + "//\n"
    )


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("测试中不允许真实网络请求")
    monkeypatch.setattr(ncbi.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(ncbi, "_min_interval", lambda key: 0.0)
    for k in ("NCBI_API_KEY", "NCBI_EMAIL", "NCBI_TOOL"):
        monkeypatch.delenv(k, raising=False)
    cache.backend.clear_pattern("map:*")
    yield
    cache.backend.clear_pattern("map:*")


# ---------------------------------------------------------------- accession

@pytest.mark.parametrize("raw,expected", [
    ("L09137", "L09137"), ("m77789.1", "M77789.1"), (" J01749 ", "J01749"),
    ("NC_005213.1", "NC_005213.1"), ("NZ_CP009072.1", "NZ_CP009072.1"),
    ("AAAA02000001", "AAAA02000001"), ("U 49845", "U49845"),
])
def test_accession_valid(raw, expected):
    assert ncbi.normalize_accession(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "pUC19", "12345678", "L09137;rm -rf", "L0913", "L09137.1.2", "A" * 30,
    "L09137&db=protein", "../etc/passwd",
])
def test_accession_invalid(raw):
    with pytest.raises(MapParseError) as ei:
        ncbi.normalize_accession(raw)
    assert ei.value.status == 422


# ---------------------------------------------------------------- efetch

def _capture_opener(body: bytes, seen: list):
    def _open(req, timeout=None):
        seen.append((req, timeout))
        return _Resp(body)
    return _open


def test_fetch_builds_efetch_request(monkeypatch):
    monkeypatch.setenv("NCBI_EMAIL", "lab@example.org")
    monkeypatch.setenv("NCBI_API_KEY", "k123")
    seen = []
    text = ncbi.fetch_genbank_text("l09137", max_bytes=10 ** 6, max_length=10 ** 6,
                                   timeout=7, opener=_capture_opener(_gb().encode(), seen))
    assert text.startswith("LOCUS")
    req, timeout = seen[0]
    assert timeout == 7
    url = urllib.parse.urlsplit(req.full_url)
    assert url.netloc == "eutils.ncbi.nlm.nih.gov" and url.path.endswith("/efetch.fcgi")
    q = dict(urllib.parse.parse_qsl(url.query))
    assert q == {"db": "nuccore", "id": "L09137", "rettype": "gbwithparts", "retmode": "text",
                 "tool": "PlasmidDesigner", "email": "lab@example.org", "api_key": "k123"}


def test_fetch_without_optional_env_has_tool_only():
    seen = []
    ncbi.fetch_genbank_text("L09137", max_bytes=10 ** 6, max_length=10 ** 6,
                            opener=_capture_opener(_gb().encode(), seen))
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(seen[0][0].full_url).query))
    assert "email" not in q and "api_key" not in q and q["tool"] == "PlasmidDesigner"


def _raiser(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


@pytest.mark.parametrize("exc,status", [
    (urllib.error.HTTPError("u", 400, "Bad Request", {}, None), 404),
    (urllib.error.HTTPError("u", 429, "Too Many", {}, None), 503),
    (urllib.error.HTTPError("u", 500, "Server Error", {}, None), 502),
    (socket.timeout("timed out"), 504),
    (urllib.error.URLError(socket.timeout("timed out")), 504),
    (urllib.error.URLError("no route"), 502),
])
def test_fetch_error_mapping(exc, status):
    with pytest.raises(MapParseError) as ei:
        ncbi.fetch_genbank_text("L09137", max_bytes=10 ** 6, max_length=10 ** 6, opener=_raiser(exc))
    assert ei.value.status == status


def test_fetch_non_genbank_body_is_not_found():
    for body in (b"", b"Error: F a i l e d  to understand id: X\n"):
        with pytest.raises(MapParseError) as ei:
            ncbi.fetch_genbank_text("L09137", max_bytes=10 ** 6, max_length=10 ** 6,
                                    opener=_capture_opener(body, []))
        assert ei.value.status == 404


def test_fetch_enforces_length_and_size_caps():
    with pytest.raises(MapParseError) as ei:
        ncbi.fetch_genbank_text("L09137", max_bytes=10 ** 6, max_length=299,
                                opener=_capture_opener(_gb().encode(), []))
    assert ei.value.status == 413 and "300" in str(ei.value)
    big = _gb().encode() + b" " * 5000
    with pytest.raises(MapParseError) as ei:
        ncbi.fetch_genbank_text("L09137", max_bytes=1000, max_length=10 ** 6,
                                opener=_capture_opener(big, []))
    assert ei.value.status == 413


# ---------------------------------------------------------------- 路由

@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    s = {"registration_open": True, "email_verification_required": False,
         "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(s)))
    return TestClient(app), s


@pytest.fixture()
def fake_fetch(monkeypatch):
    from app.routes import map_routes

    calls = []

    def _fetch(acc, max_bytes, max_length):
        calls.append((acc, max_bytes, max_length))
        if acc == "X99999":
            raise MapParseError("NCBI 中未找到 accession X99999", 404)
        return _gb(acc)
    monkeypatch.setattr(map_routes, "fetch_genbank_text", _fetch)
    return calls


def test_import_returns_map_document_and_caches(client, fake_fetch):
    from app.routes.models import MAP_MAX_LENGTH
    from app.routes.map_routes import MAP_MAX_UPLOAD_BYTES

    c, _ = client
    r = c.post("/api/map/import/ncbi", json={"accession": "l09137", "enzyme_set": "common"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["schema"] == "plasmid-map/1" and d["name"] == "L09137"
    assert d["length"] == 300 and d["topology"] == "circular"
    assert d["meta"]["source_format"] == "genbank" and d["meta"]["enzyme_set"] == "common"
    assert any(f["name"] == "testCDS" for f in d["features"])
    assert fake_fetch == [("L09137", MAP_MAX_UPLOAD_BYTES, MAP_MAX_LENGTH)]
    # 同一 accession + 选项：MapDocument 缓存命中；换酶组：文本缓存命中，都不再外呼
    assert c.post("/api/map/import/ncbi", json={"accession": "L09137", "enzyme_set": "common"}).json() == d
    r2 = c.post("/api/map/import/ncbi", json={"accession": "L09137", "enzyme_set": "golden_gate"})
    assert r2.status_code == 200 and r2.json()["meta"]["enzyme_set"] == "golden_gate"
    assert len(fake_fetch) == 1


def test_import_errors(client, fake_fetch):
    c, _ = client
    r = c.post("/api/map/import/ncbi", json={"accession": "pUC19"})
    assert r.status_code == 422 and "accession" in r.json()["detail"]
    r = c.post("/api/map/import/ncbi", json={"accession": "X99999"})
    assert r.status_code == 404 and "未找到" in r.json()["detail"]
    assert c.post("/api/map/import/ncbi", json={}).status_code == 422
    assert fake_fetch == [("X99999", fake_fetch[0][1], fake_fetch[0][2])]


def test_import_gated_by_map_feature(client, fake_fetch):
    c, s = client
    s["anonymous_features"] = [f for f in ALL_FEATURES if f != "map"]
    assert c.post("/api/map/import/ncbi", json={"accession": "L09137"}).status_code == 403
    assert fake_fetch == []


def test_import_uses_ncbi_rate_limit_bucket():
    from app.rate_limit import RATE_LIMITS, classify_endpoint

    assert classify_endpoint("POST", "/api/map/import/ncbi") == "ncbi"
    assert RATE_LIMITS["ncbi"]["requests"] <= 10
    # 其他图谱写端点不受影响
    assert classify_endpoint("POST", "/api/map/parse") == "default"
