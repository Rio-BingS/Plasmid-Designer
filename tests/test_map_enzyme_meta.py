"""酶详情 / 厂商表 / type_iis 酶组（REBASE via Bio.Restriction）"""

import json

import pytest
from fastapi.testclient import TestClient

from app import site_settings
from app.features import ALL_FEATURES
from core.enzyme_sites import get_enzyme, is_type_iis
from core.plasmid_map.enzymes import enzyme_detail, enzyme_sets, suppliers


@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    s = {"registration_open": True, "email_verification_required": False,
         "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(s)))
    return TestClient(app), s


def test_enzyme_detail_bsai():
    d = enzyme_detail("BsaI")
    assert d["site"] == "GGTCTC" and d["recognition"] == "GGTCTC(1/5)"
    assert d["type_iis"] is True and d["golden_gate"] is True
    assert {"code": "N", "name": "New England Biolabs"} in d["suppliers"]
    # Eco31I（Thermo）是 BsaI 的同切酶
    assert "Eco31I" in d["isoschizomers"] and "BsaI" not in d["isoschizomers"]
    assert d["methylation"] == ["dcm"]
    assert "opt_temp" not in d and "inact_temp" not in d  # Bio 的占位值不暴露


def test_enzyme_detail_neoschizomers_and_plain_enzyme():
    d = enzyme_detail("SmaI")
    assert d["type_iis"] is False and d["golden_gate"] is False
    assert "XmaI" in d["neoschizomers"] and "XmaI" not in d["isoschizomers"]
    assert "methylation" not in d
    codes = [s["code"] for s in enzyme_detail("EcoRI")["suppliers"]]
    assert codes == sorted(codes) and {"N", "B"} <= set(codes)
    assert enzyme_detail("SapI")["golden_gate"] is False  # 3 nt 突出端
    assert enzyme_detail("NoSuchI") is None


def test_suppliers_table():
    rows = suppliers()
    by = {r["code"]: r for r in rows}
    assert by["N"]["name"] == "New England Biolabs"
    assert by["B"]["name"] == "Thermo Fisher Scientific"
    assert "BsaI" in by["N"]["enzymes"] and "Eco31I" in by["B"]["enzymes"]
    assert "BsaI" not in by["B"]["enzymes"]
    counts = [len(r["enzymes"]) for r in rows]
    assert counts == sorted(counts, reverse=True)
    for r in rows:
        assert all(get_enzyme(n) for n in r["enzymes"])


def test_type_iis_enzyme_set():
    sets = {s["id"]: s for s in enzyme_sets()}
    tiis = sets["type_iis"]["enzymes"]
    assert {"BsaI", "SapI", "MlyI", "FokI", "PaqCI"} <= set(tiis)
    assert "EcoRI" not in tiis
    assert all(is_type_iis(get_enzyme(n)) for n in tiis)
    # 原 golden_gate 酶组不变
    assert set(sets["golden_gate"]["enzymes"]) == {"BsaI", "BsmBI", "BbsI", "SapI", "Esp3I", "PaqCI"}


def test_enzyme_detail_endpoint(client):
    c, _ = client
    r = c.get("/api/map/enzymes/BbsI")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "BbsI" and d["type_iis"] and "BpiI" in d["isoschizomers"]
    assert c.get("/api/map/enzymes/NoSuchI").status_code == 404
    assert c.get("/api/map/enzymes/" + "x" * 40).status_code == 422


def test_suppliers_endpoint_and_feature_flag(client):
    c, s = client
    r = c.get("/api/map/suppliers")
    assert r.status_code == 200
    assert r.json()[0]["code"] == "N"
    s["anonymous_features"] = [f for f in ALL_FEATURES if f != "map"]
    assert c.get("/api/map/suppliers").status_code == 403
    assert c.get("/api/map/enzymes/BsaI").status_code == 403


def test_enzyme_sets_endpoint_has_type_iis(client):
    c, _ = client
    ids = [x["id"] for x in c.get("/api/map/enzyme-sets").json()]
    assert "type_iis" in ids
