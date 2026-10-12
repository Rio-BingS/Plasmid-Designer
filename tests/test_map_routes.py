"""/api/map/* 路由、map 功能开关、载体/设计图谱端点改为 MapDocument"""

import io
import json

import pytest
from Bio import SeqIO
from fastapi.testclient import TestClient

from app import site_settings
from app.features import ALL_FEATURES, FEATURE_REGISTRY, SPLIT_INHERIT

SEQ = ("ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA" * 5)[:300]
GB = (
    "LOCUS       TESTPLAS                 300 bp    DNA     circular SYN 01-JAN-2026\n"
    "FEATURES             Location/Qualifiers\n"
    "     CDS             join(281..300,1..40)\n"
    "                     /label=\"wrapCDS\"\n"
    "ORIGIN\n"
    + "".join(f"{i + 1:>9} " + " ".join(SEQ[i:i + 60][j:j + 10].lower() for j in range(0, 60, 10))
              + "\n" for i in range(0, 300, 60))
    + "//\n"
)


def _pet28a_sequence() -> str:
    from pathlib import Path

    import yaml
    path = Path(__file__).resolve().parent.parent / "data" / "vectors" / "pET-28a.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["sequence"]


@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    state = {"s": {
        "registration_open": True, "email_verification_required": False,
        "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES),
    }}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state["s"])))
    return TestClient(app), state


def _doc_ok(d):
    assert d["schema"] == "plasmid-map/1"
    assert d["length"] == len(d["sequence"])
    assert set(d["meta"]) == {"source_format", "enzyme_set", "warnings"}
    for e in d["enzymes"]:
        assert e["cut_count"] == len(e["sites"]) >= 1


def test_feature_flag_registered_and_inherits_from_analysis():
    assert FEATURE_REGISTRY["map"]["label"] == "质粒图谱"
    assert "map" in ALL_FEATURES
    assert SPLIT_INHERIT["map"] == "analysis"


def test_map_routes_gated_by_feature(client):
    c, state = client
    state["s"]["anonymous_features"] = [f for f in ALL_FEATURES if f != "map"]
    r = c.get("/api/map/enzyme-sets")
    assert r.status_code == 403 and "质粒图谱" in r.json()["detail"]
    state["s"]["anonymous_features"] = list(ALL_FEATURES)
    assert c.get("/api/map/enzyme-sets").status_code == 200


def test_parse_genbank_upload(client):
    c, _ = client
    r = c.post("/api/map/parse", files={"file": ("my.gb", GB.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    d = r.json()
    _doc_ok(d)
    assert d["meta"]["source_format"] == "genbank" and d["topology"] == "circular"
    f = d["features"][0]
    assert (f["name"], f["start"], f["end"], f["source"]) == ("wrapCDS", 281, 40, "file")
    assert f["segments"] == [{"start": 281, "end": 300}, {"start": 1, "end": 40}]
    assert f["id"] == "file-1"
    # 有特征的文件默认不自动注释
    assert all(x["source"] == "file" for x in d["features"])


def test_parse_json_text_with_options(client):
    c, _ = client
    r = c.post("/api/map/parse?enzyme_set=golden_gate&orf_min_aa=200",
               json={"text": _pet28a_sequence(), "name": "pasted", "topology": "circular"})
    assert r.status_code == 200, r.text
    d = r.json()
    _doc_ok(d)
    assert d["name"] == "pasted" and d["meta"]["source_format"] == "raw"
    assert d["meta"]["enzyme_set"] == "golden_gate"
    assert {e["name"] for e in d["enzymes"]} <= {"BsaI", "BsmBI", "BbsI", "SapI", "Esp3I", "PaqCI"}
    assert all(o["aa_length"] >= 200 for o in d["orfs"])
    # 无特征 → 自动注释
    names = {f["name"] for f in d["features"] if f["source"] == "auto"}
    assert {"KanR", "lacI", "T7 promoter"} <= names


def test_parse_errors(client, monkeypatch):
    c, _ = client
    r = c.post("/api/map/parse", json={"text": "ACGTQQ"})
    assert r.status_code == 422 and "非法字符" in r.json()["detail"]
    r = c.post("/api/map/parse", files={"file": ("x.xlsx", b"PK\x03\x04junk", "application/zip")})
    assert r.status_code == 415
    r = c.post("/api/map/parse", content=b"hello", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    from app.routes import map_routes
    monkeypatch.setattr(map_routes, "MAP_MAX_UPLOAD_BYTES", 1000)
    r = c.post("/api/map/parse", files={"file": ("big.fasta", b">a\n" + b"A" * 5000, "text/plain")})
    assert r.status_code == 413
    monkeypatch.setattr(map_routes, "MAP_MAX_UPLOAD_BYTES", 10 ** 7)
    monkeypatch.setattr(map_routes, "MAP_MAX_LENGTH", 100)
    r = c.post("/api/map/parse", json={"text": "A" * 101})
    assert r.status_code == 413 and "过长" in r.json()["detail"]


def test_analyze_recomputes_and_keeps_ids(client):
    c, _ = client
    feats = [{"id": "keep", "name": "x", "type": "cds", "start": 1, "end": 30, "strand": 1,
              "source": "file"},
             {"id": "old-auto", "name": "stale", "type": "promoter", "start": 50, "end": 60,
              "strand": 1, "source": "auto",
              "auto": {"library": "common", "identity": 1, "coverage": 1, "fragment": False}}]
    body = {"sequence": SEQ, "topology": "linear", "features": feats,
            "enzymes": ["EcoRI", "NotARealEnzyme"], "orf_min_aa": 10, "annotate": True}
    r = c.post("/api/map/analyze", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    _doc_ok(d)
    assert [f["id"] for f in d["features"]] == ["keep"]  # 旧 auto 特征被重算替换
    assert d["meta"]["enzyme_set"] == "custom"
    assert any("NotARealEnzyme" in w for w in d["meta"]["warnings"])
    r = c.post("/api/map/analyze", json={**body, "annotate": False})
    assert [f["id"] for f in r.json()["features"]] == ["keep", "old-auto"]
    r = c.post("/api/map/analyze", json={"sequence": SEQ, "topology": "ring"})
    assert r.status_code == 422


def test_enzyme_sets_endpoint(client):
    c, _ = client
    sets = {s["id"]: s for s in c.get("/api/map/enzyme-sets").json()}
    assert {"common", "unique6", "commercial", "golden_gate"} <= set(sets)
    for s in sets.values():
        assert s["name"] and s["description"] and s["enzymes"]


def test_export_genbank_round_trip(client):
    c, _ = client
    d = c.post("/api/map/parse", files={"file": ("my.gb", GB.encode(), "text/plain")}).json()
    d["features"][0]["color"] = "#ABCDEF"
    r = c.post("/api/map/export/genbank", json=d)
    assert r.status_code == 200, r.text
    assert 'filename="TESTPLAS.gb"' in r.headers["content-disposition"]
    rec = SeqIO.read(io.StringIO(r.text), "genbank")
    assert str(rec.seq) == SEQ and rec.annotations["topology"] == "circular"
    f = rec.features[0]
    assert f.qualifiers["ApEinfo_fwdcolor"] == ["#abcdef"]
    d["features"][0]["end"] = 999
    assert c.post("/api/map/export/genbank", json=d).status_code == 422


def test_vector_map_returns_map_document(client):
    c, _ = client
    r = c.get("/api/vectors/pET-28a/map")
    assert r.status_code == 200, r.text
    d = r.json()
    _doc_ok(d)
    assert d["meta"]["source_format"] == "vector" and d["topology"] == "circular"
    assert d["features"] and all(f["source"] == "vector" for f in d["features"])
    kan = next(f for f in d["features"] if f["name"] == "KanR")
    assert (kan["type"], kan["start"], kan["end"], kan["strand"]) == ("marker", 3995, 4810, 1)
    assert "enzyme_sites" not in d
    assert c.get("/api/vectors/nope/map").status_code == 404


def test_vector_list_items_carry_map_preview(client):
    c, _ = client
    from app.cache import cache
    cache.backend.clear_pattern("vector_list:*")
    r = c.get("/api/vectors")
    assert r.status_code == 200, r.text
    items = {v["id"]: v for v in r.json()}
    from core.plasmid_map.types import FEATURE_TYPE_SET as MAP_FEATURE_TYPES
    for v in items.values():
        p = v["map_preview"]
        assert p and p["topology"] == "circular" and p["length"] >= 1000
        assert "sequence" not in p
        for f in p["features"]:
            assert set(f) == {"name", "type", "start", "end", "strand"}
            assert f["type"] in MAP_FEATURE_TYPES and f["strand"] in (1, -1, 0)
            assert 1 <= f["start"] <= p["length"] and 1 <= f["end"] <= p["length"]
    puc = items["pUC19"]["map_preview"]
    assert puc["length"] == 2686
    by = {f["name"]: f for f in puc["features"]}
    assert (by["AmpR (bla)"]["type"], by["AmpR (bla)"]["start"], by["AmpR (bla)"]["strand"]) == \
        ("marker", 1626, -1)
    assert by["MCS"]["type"] == "mcs" and by["lac operator"]["type"] == "protein_bind"
    # 与 /api/vectors/{id}/map 同一套归一
    full = c.get("/api/vectors/pUC19/map").json()
    assert [(f["name"], f["type"], f["start"], f["end"], f["strand"]) for f in full["features"]] == \
        [(f["name"], f["type"], f["start"], f["end"], f["strand"]) for f in puc["features"]]
    # 第二次走缓存，形状不变
    assert c.get("/api/vectors").json() == r.json()


def test_large_documents_not_cached_and_total_bytes_bounded(client, monkeypatch):
    """按序列化体积决定是否缓存；本进程缓存总字节超限时淘汰最早写入的"""
    import app.routes.map_routes as M
    from app.cache import cache

    c, _ = client
    monkeypatch.setattr(M, "_cache_sizes", type(M._cache_sizes)())
    stored = {}
    monkeypatch.setattr(cache.backend, "set", lambda k, v, ttl=None: stored.__setitem__(k, v) or True)
    monkeypatch.setattr(cache.backend, "get", lambda k: stored.get(k))
    monkeypatch.setattr(cache.backend, "delete", lambda k: stored.pop(k, None) is not None)

    monkeypatch.setattr(M, "MAP_CACHE_MAX_DOC_BYTES", 100)
    r = c.post("/api/map/parse", json={"text": SEQ, "annotate": False})
    assert r.status_code == 200 and not stored

    monkeypatch.setattr(M, "MAP_CACHE_MAX_DOC_BYTES", 10 ** 7)
    size = M._doc_bytes(r.json())
    monkeypatch.setattr(M, "MAP_CACHE_MAX_TOTAL_BYTES", int(size * 2.5))
    for i in range(4):
        rr = c.post("/api/map/parse", json={"text": SEQ, "annotate": False, "name": f"n{i}"})
        assert rr.status_code == 200
    assert len(stored) == 2 and len(M._cache_sizes) == 2
    assert all(f'"n{i}"' not in json.dumps(v) for v in stored.values() for i in (0, 1))
    assert sum(v[0] for v in M._cache_sizes.values()) <= int(size * 2.5) + 200
