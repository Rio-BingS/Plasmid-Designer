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
