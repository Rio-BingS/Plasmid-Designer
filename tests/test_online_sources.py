"""在线序列来源导入（SnapGene 在线库 / Ensembl / Addgene）与载体落盘形状回归锁

网络一律打桩：`urllib.request.urlopen` 在本文件里被替换成抛错的哨兵，
任何漏网的真实外呼都会让用例失败；需要构造上游响应的用例走各函数的
`opener=` 注入点或直接 monkeypatch 路由层符号。

覆盖：
* core.online_sources：参数校验、集合/索引解析、.dna 魔法串与体积上限、
  上游错误到 HTTP 状态的映射、Ensembl 只收 DNA、Addgene 绝不绕登录
* 路由：来源清单、集合/索引缓存、预览、落库（管理员）、门控、Addgene 指引
* 落盘形状回归：导入/上传写出的 YAML 必须能被 VectorLibrary 加载
  （历史上 features 缺 name → KeyError → 载体静默消失；.dna 上传被当文本
  解析 → 空序列）
"""

import io
import json
import socket
import struct
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app import online_import as oi
from app import site_settings
from app.cache import cache
from app.features import ALL_FEATURES
from app.main import app
from app.routes import online_source_routes as osr
from core import online_sources as src
from core.plasmid_map.parsers import MapParseError

SEQ300 = ("ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA" * 5)[:300]


# ---------------------------------------------------------------- 打桩工具

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener(body, seen=None):
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()

    def _open(req, timeout=None):
        if seen is not None:
            seen.append((req, timeout))
        return _Resp(payload)
    return _open


def _raiser(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


def _snapgene_bytes(seq: str = SEQ300, circular: bool = True) -> bytes:
    """最小合法 SnapGene .dna（与 tests/test_plasmid_map_parsers.py 同一构造）"""
    def packet(t, data):
        return struct.pack(">BI", t, len(data)) + data
    cookie = packet(0x09, struct.pack(">8sHHH", b"SnapGene", 1, 15, 19))
    dna = packet(0x00, bytes([1 if circular else 0]) + seq.encode())
    xml = (
        '<?xml version="1.0"?><Features nextValidID="2">'
        '<Feature recentID="0" name="myCDS" directionality="1" type="CDS">'
        '<Segment range="4-33" color="#993366" type="standard"/>'
        '<Q name="note"><V text="a test CDS"/></Q></Feature>'
        '</Features>'
    ).encode()
    return cookie + dna + packet(0x0A, xml)


SETS_XML = (b'<?xml version="1.0"?><Sets>'
            b'<Set name="Yeast Plasmids" path="yeast_plasmids"/>'
            b'<Set name="CRISPR Plasmids" path="crispr_plasmids"/></Sets>')
INDEX_XML = (b'<?xml version="1.0"?><Plasmids>'
             b'<Plasmid name="pYES2" url="pYES2" filename="pYES2.dna" index="0"/>'
             b'<Plasmid name="SARS Genome" url="SARS_Genome" filename="SARS Genome.dna" index="1"/>'
             b'</Plasmids>')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("测试中不允许真实网络请求")
    monkeypatch.setattr(src.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(src, "_default_open", _boom)  # 自建 opener 同样禁网
    monkeypatch.setattr(src, "_MIN_INTERVAL", {})  # 免等待
    monkeypatch.setattr(src, "_next_slot", {})     # 节流时间槽不跨用例
    monkeypatch.setattr(src, "_addgene_index_cache", {})
    for k in ("ONLINE_IMPORT_ENABLED", "ONLINE_TIMEOUT", "SNAPGENE_BASE", "ENSEMBL_BASE",
              "UNIPROT_BASE", "ADDGENE_SESSION_COOKIE"):
        monkeypatch.delenv(k, raising=False)
    # SnapGene 在线库默认开启，仍显式设 true 以免受外部环境影响，默认值与关闭行为另有专门用例
    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "true")
    cache.backend.clear_pattern("online:*")
    yield
    cache.backend.clear_pattern("online:*")


# ---------------------------------------------------------------- 参数校验

@pytest.mark.parametrize("fn,raw,expected", [
    (src.validate_set_id, "yeast_plasmids", "yeast_plasmids"),
    (src.validate_set_id, "pet_and_duet_vectors_(novagen)", "pet_and_duet_vectors_(novagen)"),
    (src.validate_plasmid_id, " pYES2 ", "pYES2"),
    (src.validate_plasmid_id, "SARS Genome", "SARS Genome"),
    # 索引里的真实 ID 带括号/加号/撇号/&（19 个集合 2822 行里有 488 个）：
    # 搜索不校验 ID、导入才校验，白名单时代的症状就是「搜得到、导不进」
    (src.validate_plasmid_id, "pET-28a(+)", "pET-28a(+)"),
    (src.validate_plasmid_id, "pcDNA3.1(+)", "pcDNA3.1(+)"),
    (src.validate_plasmid_id, "pBluescript_II_KS(-)", "pBluescript_II_KS(-)"),
    (src.validate_plasmid_id, "pCDF-2_Ek_LIC_(linearized)", "pCDF-2_Ek_LIC_(linearized)"),
    (src.validate_plasmid_id, "O'Brien & Sons", "O'Brien & Sons"),
    (src.validate_ensembl_id, "ENST00000288602", "ENST00000288602"),
    (src.validate_addgene_id, "#52961", "52961"),
])
def test_valid_identifiers(fn, raw, expected):
    assert fn(raw) == expected


@pytest.mark.parametrize("raw", ["", "../etc/passwd", "a/b", "x?y=1", "A" * 130,
                                 "%2e%2e", "a\\b", "a#b", "a\r\nb"])
def test_invalid_set_and_plasmid_ids(raw):
    for fn in (src.validate_set_id, src.validate_plasmid_id):
        with pytest.raises(MapParseError) as ei:
            fn(raw)
        assert ei.value.status == 422


@pytest.mark.parametrize("raw", ["", "ENSG 1", "id;rm", "A" * 80, "<script>"])
def test_invalid_ensembl_ids(raw):
    with pytest.raises(MapParseError) as ei:
        src.validate_ensembl_id(raw)
    assert ei.value.status == 422


@pytest.mark.parametrize("raw", ["", "abc", "12/34", "1" * 11])
def test_invalid_addgene_ids(raw):
    with pytest.raises(MapParseError) as ei:
        src.validate_addgene_id(raw)
    assert ei.value.status == 422


def test_import_switch(monkeypatch):
    assert src.import_enabled() is True
    monkeypatch.setenv("ONLINE_IMPORT_ENABLED", "false")
    assert src.import_enabled() is False
    with pytest.raises(MapParseError) as ei:
        src.snapgene_sets()
    assert ei.value.status == 503


# ---------------------------------------------------------------- SnapGene

def test_snapgene_sets_and_index_parsing():
    sets = src.snapgene_sets(opener=_opener(SETS_XML))
    assert sets == [{"set_id": "yeast_plasmids", "name": "Yeast Plasmids"},
                    {"set_id": "crispr_plasmids", "name": "CRISPR Plasmids"}]
    rows = src.snapgene_set_index("yeast_plasmids", opener=_opener(INDEX_XML))
    assert [r["plasmid_id"] for r in rows] == ["pYES2", "SARS_Genome"]
    assert rows[1]["filename"] == "SARS Genome.dna" and rows[1]["index"] == 1


def test_snapgene_bad_xml_is_upstream_error():
    for fn, arg in ((src.snapgene_sets, None), (src.snapgene_set_index, "yeast_plasmids")):
        with pytest.raises(MapParseError) as ei:
            (fn(opener=_opener(b"<Sets><Set")) if arg is None else fn(arg, opener=_opener(b"<Sets><Set")))
        assert ei.value.status == 502


def test_snapgene_url_and_magic():
    seen = []
    data = src.snapgene_plasmid_bytes("yeast_plasmids", "pYES2", opener=_opener(_snapgene_bytes(), seen))
    assert data[:16].find(b"SnapGene") >= 0
    url = urllib.parse.urlsplit(seen[0][0].full_url)
    assert url.path.endswith("/fetch.php")
    q = dict(urllib.parse.parse_qsl(url.query))
    assert q == {"set": "yeast_plasmids", "plasmid": "pYES2"}
    with pytest.raises(MapParseError) as ei:
        src.snapgene_plasmid_bytes("yeast_plasmids", "pYES2", opener=_opener(b"<html>404</html>"))
    assert ei.value.status == 415 and "不是 SnapGene" in str(ei.value)


def test_snapgene_size_cap():
    with pytest.raises(MapParseError) as ei:
        src.snapgene_plasmid_bytes("yeast_plasmids", "pYES2", max_bytes=100,
                                   opener=_opener(_snapgene_bytes()))
    assert ei.value.status == 413


def test_snapgene_zip_requires_pk():
    ok = src.snapgene_set_zip_bytes("yeast_plasmids", opener=_opener(b"PK\x03\x04rest"))
    assert ok.startswith(b"PK")
    with pytest.raises(MapParseError) as ei:
        src.snapgene_set_zip_bytes("yeast_plasmids", opener=_opener(b"<html>nope"))
    assert ei.value.status == 502


def test_snapgene_record_goes_through_project_parser():
    rec = src.snapgene_record(_snapgene_bytes(), name="pYES2")
    assert rec.source_format == "snapgene" and rec.topology == "circular"
    assert rec.sequence == SEQ300 and rec.name == "pYES2"
    by = {f["name"]: f for f in rec.features}
    assert (by["myCDS"]["start"], by["myCDS"]["end"], by["myCDS"]["strand"]) == (4, 33, 1)


def test_snapgene_record_prefers_index_name():
    """实测 .dna 内部名是状态标记（本机真实文件里为 "REVIEWED"，无名文件为
    <unknown name>），集合索引里的名字才可靠——给了名字就必须覆盖内部名。"""
    data = _snapgene_bytes()
    assert src.snapgene_record(data, name="SARS Genome").name == "SARS Genome"
    assert src.snapgene_record(data, name="pYES2").name == "pYES2"
    # 不给名字时走解析器回退（文件名主干），不得凭空造出索引名
    assert src.snapgene_record(data).name != "SARS Genome"


@pytest.mark.parametrize("exc,status", [
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), 404),
    (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), 404),
    (urllib.error.HTTPError("u", 429, "Too Many", {}, None), 503),
    (urllib.error.HTTPError("u", 500, "Server", {}, None), 502),
    (socket.timeout("t"), 504),
    (urllib.error.URLError(socket.timeout("t")), 504),
    (urllib.error.URLError("no route"), 502),
])
def test_upstream_error_mapping(exc, status):
    with pytest.raises(MapParseError) as ei:
        src.snapgene_sets(opener=_raiser(exc))
    assert ei.value.status == status


# ---------------------------------------------------------------- Ensembl

def test_ensembl_sequence_request_and_record():
    seen = []
    fasta = f">ENST00000288602 cdna\n{SEQ300}\n".encode()
    text = src.ensembl_sequence("ENST00000288602", "cds", opener=_opener(fasta, seen))
    assert text.startswith(">")
    url = urllib.parse.urlsplit(seen[0][0].full_url)
    assert url.netloc == "rest.ensembl.org" and url.path.endswith("/sequence/id/ENST00000288602")
    assert dict(urllib.parse.parse_qsl(url.query)) == {"type": "cds", "content-type": "text/x-fasta"}

    rec = src.ensembl_record("ENST00000288602", "cds", opener=_opener(fasta))
    assert rec.sequence == SEQ300 and rec.topology == "linear"
    assert rec.source_format == "ensembl:cds" and any("线性" in w for w in rec.warnings)


def test_ensembl_rejects_protein_and_non_fasta():
    with pytest.raises(MapParseError) as ei:
        src.ensembl_sequence("ENST00000288602", "protein")
    assert ei.value.status == 422 and "蛋白" in str(ei.value)
    with pytest.raises(MapParseError) as ei:
        src.ensembl_record("ENST00000288602", "cds", opener=_opener(b'{"error":"bad id"}'))
    assert ei.value.status == 502


def test_ensembl_lookup_params():
    seen = []
    src.ensembl_lookup("ENSG00000139618", expand=True,
                       opener=_opener({"display_name": "BRCA2"}, seen))
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(seen[0][0].full_url).query))
    assert q == {"content-type": "application/json", "expand": "1"}


# ---------------------------------------------------------------- Addgene

def test_addgene_never_bypasses_login():
    meta = {"seqObjectId": "244694", "mapPngUrl": "https://media.addgene.org/a.png",
            "genbankUrl": "https://media.addgene.org/a.gbk",
            "snapgeneUrl": "https://media.addgene.org/a.dna"}
    assert src.addgene_metadata("244694", opener=_opener(meta))["seqObjectId"] == "244694"
    # 元数据里没有序列直链 → 同样按"需登录"处理
    with pytest.raises(src.AddgeneLoginRequired):
        src.addgene_sequence_bytes("244694", opener=_opener({"seqObjectId": "1"}))

    # 直链 404（登录墙）→ 403 + 指引，绝不返回空序列
    def _api_ok_file_404(req, timeout=None):
        if "api/get-sequence-file-collection" in req.full_url:
            return _Resp(json.dumps(meta).encode())
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with pytest.raises(src.AddgeneLoginRequired) as ei:
        src.addgene_sequence_bytes("244694", opener=_api_ok_file_404)
    assert ei.value.status == 403 and "不绕过登录" in str(ei.value)


def test_addgene_local_index(tmp_path):
    idx = tmp_path / "addgeneProcessed.json"
    idx.write_text(json.dumps([
        {"id": 52961, "name": "lentiCRISPR v2"},
        {"id": 52962, "name": "lentiCRISPR v2 blast"},
        {"id": 48138, "name": "PX458"},
    ]), encoding="utf-8")
    p = str(idx)
    assert [e["id"] for e in src.addgene_local_index("52961", index_path=p)] == [52961]
    assert [e["id"] for e in src.addgene_local_index("lenticrispr", limit=1, index_path=p)] == [52961]
    assert src.addgene_local_index("", index_path=p) == []
    assert src.addgene_local_index("nope", index_path=p) == []
    assert src.addgene_local_index("52961", index_path=str(tmp_path / "missing.json")) == []
    idx.write_text("{ not json", encoding="utf-8")
    assert src.addgene_local_index("52961", index_path=p) == []


# ---------------------------------------------------------------- 路由

@pytest.fixture()
def client(monkeypatch):
    s = {"registration_open": True, "email_verification_required": False,
         "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(s)))
    return TestClient(app), s


@pytest.fixture()
def admin_headers():
    import uuid
    from app.auth.jwt_auth import create_access_token, db_user_to_user, hash_password
    from app.database import SessionLocal, create_user
    email = f"adm-online-{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = create_user(db, email=email, username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!!"), is_admin=True,
                        email_verified=True)
        return {"Authorization": f"Bearer {create_access_token(db_user_to_user(u))}"}
    finally:
        db.close()


@pytest.fixture()
def vectors_dir(tmp_path, monkeypatch):
    """把载体目录指到临时目录。

    注意 **两个模块都要打**：`tests/test_config_cors.py` 会 reload(app.config)
    造出新的 settings 实例，而 `app.design_service` 是 `from app.config import
    settings`（旧对象）——只打 `app.config.settings` 时路由写进临时目录、
    载体库缓存却仍从真实 data/vectors 加载（表现为刚导入的载体 404，
    与 KNOWN_ISSUES「reload(app.config) 陷阱」同源）。
    同时清掉载体响应缓存：`vector:<id>` 会跨用例命中，断言到别的用例的载体。
    """
    from app import config, design_service
    d = tmp_path / "vectors"
    d.mkdir()
    for mod in (config, design_service):
        monkeypatch.setattr(mod.settings, "VECTORS_DIR", str(d))
    design_service.invalidate_vector_library_cache()
    cache.backend.clear_pattern("vector:*")
    yield d
    design_service.invalidate_vector_library_cache()
    cache.backend.clear_pattern("vector:*")


def test_sources_endpoint_lists_licenses(client, vectors_dir):
    c, _ = client
    r = c.get("/api/vectors/import/sources")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["disclaimer"]
    ids = {s["id"]: s for s in body["sources"]}
    assert set(ids) >= {"snapgene", "ensembl", "addgene", "ncbi"}
    assert "GraphPad" in ids["snapgene"]["license"] and ids["snapgene"]["importable"] is True
    assert ids["addgene"]["importable"] is False and "登录" in ids["addgene"]["note"]


def test_snapgene_sets_cached(client, vectors_dir, monkeypatch):
    calls = []

    def _sets():
        calls.append(1)
        return [{"set_id": "yeast_plasmids", "name": "Yeast Plasmids"}]
    monkeypatch.setattr(osr.src, "snapgene_sets", _sets)
    monkeypatch.setattr(osr.src, "snapgene_set_index",
                        lambda set_id, **kw: [{"plasmid_id": "pYES2", "name": "pYES2",
                                               "filename": "pYES2.dna", "index": 0}])

    c, _ = client
    first = c.get("/api/vectors/import/snapgene/sets", headers=_user())
    second = c.get("/api/vectors/import/snapgene/sets", headers=_user())
    assert first.status_code == 200 and first.json()["count"] == 1
    assert second.json() == first.json() and len(calls) == 1

    idx = c.get("/api/vectors/import/snapgene/sets/yeast_plasmids", headers=_user())
    assert idx.status_code == 200, idx.text
    assert idx.json()["count"] == 1 and idx.json()["plasmids"][0]["plasmid_id"] == "pYES2"
    assert c.get("/api/vectors/import/snapgene/sets/..%2Fetc", headers=_user()).status_code in (404, 422)


def test_snapgene_unknown_set_is_404_not_502():
    """上游对不存在的集合回 200 + 空包体：给 404 + 提示改用质粒名搜索（此前 502 解析失败）"""
    with pytest.raises(src.OnlineSourceError) as ei:
        src.snapgene_set_index("pet-30a", opener=_opener(b""))
    assert ei.value.status == 404 and "pet-30a" in str(ei.value) and "质粒名" in str(ei.value)


def _search_stubs(monkeypatch, failing=()):
    sets = [{"set_id": "basic", "name": "Basic"}, {"set_id": "pet", "name": "pET & Duet"},
            {"set_id": "broken", "name": "Broken"}]
    index = {
        "basic": [{"plasmid_id": "pUC19", "name": "pUC19", "filename": "pUC19.dna", "index": 0}],
        "pet": [{"plasmid_id": "pET-30a(+)", "name": "pET-30a(+)", "filename": "pET-30a(+).dna", "index": 0},
                {"plasmid_id": "pET-30b(+)", "name": "pET-30b(+)", "filename": "x.dna", "index": 1},
                {"plasmid_id": "pET-30_Ek_LIC", "name": "pET-30 Ek/LIC", "filename": "y.dna", "index": 2}],
    }

    def _idx(set_id, **kw):
        if set_id in failing or set_id not in index:
            raise src.OnlineSourceError("boom", 502)
        return index[set_id]
    monkeypatch.setattr(oi.src, "snapgene_sets", lambda **kw: sets)
    monkeypatch.setattr(oi.src, "snapgene_set_index", _idx)


def test_snapgene_search_across_sets(client, vectors_dir, monkeypatch):
    _search_stubs(monkeypatch)
    c, _ = client
    for base in ("/api/vectors", "/api/map"):
        r = c.get(f"{base}/import/snapgene/search", params={"q": "pet-30a"}, headers=_user())
        assert r.status_code == 200, r.text
        body = r.json()
        assert [p["plasmid_id"] for p in body["plasmids"]] == ["pET-30a(+)"]
        assert body["plasmids"][0]["set_id"] == "pet" and body["plasmids"][0]["set_name"] == "pET & Duet"
        assert body["failed_sets"] == ["broken"]
    # 前缀命中排在包含命中之前；大小写/连字符不敏感
    r = c.get("/api/map/import/snapgene/search", params={"q": "PET 30"}, headers=_user())
    assert [p["plasmid_id"] for p in r.json()["plasmids"]][:2] == ["pET-30a(+)", "pET-30b(+)"]
    assert r.json()["count"] == 3
    assert c.get("/api/map/import/snapgene/search", params={"q": "-"}, headers=_user()).status_code == 422
    assert c.get("/api/map/import/snapgene/search", params={"q": "pet"}).status_code == 401


def test_snapgene_preview_and_import(client, vectors_dir, monkeypatch):
    record = src.snapgene_record(_snapgene_bytes(), name="pYES2")

    async def _fetch(set_id, plasmid_id):
        return record, "https://www.snapgene.com/local/x"
    monkeypatch.setattr(osr, "_snapgene_fetch", _fetch)

    c, _ = client
    prev = c.get("/api/vectors/import/snapgene/preview",
                 params={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}, headers=_user())
    assert prev.status_code == 200, prev.text
    d = prev.json()
    assert d["length"] == 300 and d["topology"] == "circular"
    assert d["features"] and d["features"][0]["name"] == "myCDS"
    assert "GraphPad" in d["license"]

    # 写操作需管理员
    assert c.post("/api/vectors/import/snapgene",
                  json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}).status_code in (401, 403)
    r = c.post("/api/vectors/import/snapgene",
               json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"},
               headers=_admin())
    assert r.status_code == 200, r.text
    vec = r.json()["vector"]
    assert vec["id"] == "snapgene_pYES2" and vec["length"] == 300 and vec["features"] == 1
    # 落盘形状必须能被载体库加载（历史 bug：features 缺 name → KeyError → 静默消失）
    from core.vector_library import VectorLibrary
    lib = VectorLibrary()
    assert lib.load_from_directory(str(vectors_dir)) == 1
    loaded = lib.get_vector("snapgene_pYES2")
    assert loaded is not None and loaded.sequence == SEQ300
    assert loaded.elements[0].name == "myCDS" and loaded.elements[0].strand == "+"
    assert "www.snapgene.com/resources" in loaded.description  # 引用要求落进了数据


def test_ensembl_import(client, vectors_dir, monkeypatch):
    fasta = f">ENST00000288602 cds\n{SEQ300}\n".encode()
    real_record = src.ensembl_record  # 先取原函数，避免自递归

    def _record(identifier, seq_type="cds", name=None, **kw):
        return real_record(identifier, seq_type, name=name, opener=_opener(fasta))
    monkeypatch.setattr(osr.src, "ensembl_record", _record)

    c, _ = client
    prev = c.get("/api/vectors/import/ensembl/preview",
                 params={"identifier": "ENST00000288602", "seq_type": "cds"})
    assert prev.status_code == 200 and prev.json()["topology"] == "linear"
    assert c.get("/api/vectors/import/ensembl/preview",
                 params={"identifier": "ENST00000288602", "seq_type": "protein"}
                 ).status_code == 422
    r = c.post("/api/vectors/import/ensembl",
               json={"identifier": "ENST00000288602", "seq_type": "cds", "name": "myCDS"},
               headers=_admin())
    assert r.status_code == 200, r.text
    v = r.json()["vector"]
    assert v["topology"] == "linear" and v["length"] == 300
    assert (vectors_dir / "ensembl_ENST00000288602_cds.yaml").is_file()


def test_addgene_routes(client, vectors_dir, monkeypatch):
    meta = {"seqObjectId": "244694", "mapPngUrl": "https://x/a.png",
            "genbankUrl": "https://x/a.gbk"}
    monkeypatch.setattr(osr.src, "addgene_metadata",
                        lambda oid, **kw: meta)
    monkeypatch.setattr(osr.src, "addgene_sequence_bytes",
                        lambda oid, **kw: (_ for _ in ()).throw(
                            src.AddgeneLoginRequired("Addgene 全长序列需要登录…不绕过登录")))

    c, _ = client
    r = c.get("/api/vectors/import/addgene/244694")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sequence_available"] is False and d["metadata"]["seqObjectId"] == "244694"
    assert "上传文件" in d["note"]
    r2 = c.get("/api/vectors/import/addgene/244694", params={"try_sequence": 1},
               headers=_admin())
    assert r2.status_code == 403 and "绕过登录" in r2.json()["detail"]
    assert c.get("/api/vectors/import/addgene/abc").status_code == 422


def test_upload_dna_and_fasta_go_through_map_parser(client, vectors_dir):
    c, _ = client
    r = c.post("/api/vectors/import/upload",
               files={"file": ("pYES2.dna", _snapgene_bytes(), "application/octet-stream")},
               headers=_admin())
    assert r.status_code == 200, r.text
    v = r.json()["vector"]
    # 历史 bug：.dna 被当文本 GenBank 解析 → 空序列、0 特征
    assert v["length"] == 300 and v["features"] == 1 and v["name"] == "pYES2"

    r2 = c.post("/api/vectors/import/upload",
                files={"file": ("x.fasta", f">frag1 linear\n{SEQ300}\n".encode(), "text/plain")},
                headers=_admin())
    assert r2.status_code == 200 and r2.json()["vector"]["length"] == 300

    r3 = c.post("/api/vectors/import/upload",
                files={"file": ("bad.exe", b"MZ\x90\x00", "application/octet-stream")},
                headers=_admin())
    assert r3.status_code == 400

    from core.vector_library import VectorLibrary
    lib = VectorLibrary()
    assert lib.load_from_directory(str(vectors_dir)) == 2


def test_online_routes_gated_by_vectors_feature(client, vectors_dir):
    c, s = client
    s["anonymous_features"] = [f for f in ALL_FEATURES if f != "vectors"]
    assert c.get("/api/vectors/import/sources").status_code == 403


def test_rate_limit_buckets_unchanged():
    from app.rate_limit import classify_endpoint
    assert classify_endpoint("POST", "/api/vectors/import/snapgene") == "upload"
    assert classify_endpoint("POST", "/api/vectors/import/ensembl") == "upload"
    assert classify_endpoint("GET", "/api/vectors/import/sources") == "default"


@pytest.mark.parametrize("path", [
    "/api/vectors/import/snapgene/sets",
    "/api/vectors/import/snapgene/sets/yeast_plasmids",
    "/api/vectors/import/snapgene/preview",
    "/api/vectors/import/ensembl/lookup/ENSG00000139618",
    "/api/vectors/import/ensembl/preview",
    "/api/map/import/snapgene/sets",
    "/api/map/import/snapgene/sets/yeast_plasmids",
    "/api/map/import/snapgene/search",
    "/api/vectors/import/snapgene/search",
])
def test_outbound_proxy_gets_use_ncbi_bucket(path):
    """会外呼的只读代理两侧口径一致，都走 ncbi 档（审查 M4）"""
    from app.rate_limit import classify_endpoint
    assert classify_endpoint("GET", path) == "ncbi"


# ---------------------------------------------------------------- 落盘形状回归

def test_legacy_feature_without_name_still_loads(tmp_path):
    """NCBI/批量导入的历史 YAML 只有 {type,start,end}：此前整份载体加载失败"""
    from core.vector_library import VectorLibrary
    (tmp_path / "legacy.yaml").write_text(
        "id: ncbi_L09137\nname: L09137\nsequence: ACGTACGTACGT\n"
        "features:\n- type: CDS\n  start: 1\n  end: 6\n", encoding="utf-8")
    lib = VectorLibrary()
    assert lib.load_from_directory(str(tmp_path)) == 1
    v = lib.get_vector("ncbi_L09137")
    assert v is not None and v.elements[0].name == "CDS" and v.elements[0].strand == "+"


def test_legacy_export_writes_feature_names(tmp_path):
    """external_vector_importer.export_to_yaml 也要写 name/strand"""
    from core.external_vector_importer import ExternalVector, VectorLibraryManager
    from core.vector_library import VectorLibrary

    mgr = VectorLibraryManager(data_dir=str(tmp_path))
    mgr.vectors["ncbi_X1"] = ExternalVector(
        id="ncbi_X1", name="X1", source="ncbi", sequence="ACGTACGTACGT",
        description="", vector_type="imported", host=[], antibiotic_resistance=[],
        features=[{"type": "CDS", "start": 2, "end": 7}])
    assert mgr.export_to_yaml(str(tmp_path)) == 1
    lib = VectorLibrary()
    assert lib.load_from_directory(str(tmp_path)) == 1
    assert lib.get_vector("ncbi_X1").elements[0].name == "CDS"


# ---------------------------------------------------------------- 图谱页在线导入

def test_online_sources_shared_between_map_and_vectors(client, monkeypatch):
    """集合/索引在载体库与图谱页之间共用缓存：同一进程只外呼一次"""
    calls = []

    def _sets():
        calls.append(1)
        return [{"set_id": "yeast_plasmids", "name": "Yeast Plasmids"}]
    monkeypatch.setattr(oi.src, "snapgene_sets", _sets)
    monkeypatch.setattr(oi.src, "snapgene_set_index",
                        lambda set_id, **kw: [{"plasmid_id": "pYES2", "name": "pYES2",
                                               "filename": "pYES2.dna", "index": 0}])

    c, _ = client
    a = c.get("/api/vectors/import/snapgene/sets", headers=_user())
    b = c.get("/api/map/import/snapgene/sets", headers=_user())
    assert a.status_code == b.status_code == 200
    assert b.json() == a.json() and len(calls) == 1

    ia = c.get("/api/vectors/import/snapgene/sets/yeast_plasmids", headers=_user())
    ib = c.get("/api/map/import/snapgene/sets/yeast_plasmids", headers=_user())
    assert ia.json() == ib.json() and ib.json()["count"] == 1


def test_map_sources_payload_matches_vectors(client, vectors_dir):
    c, _ = client
    a = c.get("/api/vectors/import/sources").json()
    b = c.get("/api/map/import/sources").json()
    assert a == b and {s["id"] for s in b["sources"]} >= {"snapgene", "ensembl", "addgene", "ncbi"}


def test_map_import_snapgene_builds_document_once(client, vectors_dir, monkeypatch):
    """图谱页从 SnapGene 在线库打开：一次下载 → MapDocument，同参数第二次走缓存"""
    record = src.snapgene_record(_snapgene_bytes(), name="pYES2")
    calls = []

    def _record(set_id, plasmid_id, max_length=None):
        calls.append((set_id, plasmid_id, max_length))
        return record, "https://www.snapgene.com/local/fetch.php?set=x", "pYES2"
    monkeypatch.setattr(oi, "snapgene_plasmid_record", _record)

    c, _ = client
    r = c.post("/api/map/import/snapgene",
               json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}, headers=_user())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["schema"] == "plasmid-map/1" and d["name"] == "pYES2"
    assert d["length"] == 300 and d["topology"] == "circular"
    assert any(f["name"] == "myCDS" for f in d["features"])
    from app.routes.models import MAP_MAX_LENGTH
    assert calls == [("yeast_plasmids", "pYES2", MAP_MAX_LENGTH)]

    r2 = c.post("/api/map/import/snapgene",
                json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}, headers=_user())
    assert r2.json() == d and len(calls) == 1        # MapDocument 缓存命中，不重复下载
    # 换酶组：按选项分键 → 重新构建（但不再下载集合索引）
    r3 = c.post("/api/map/import/snapgene",
                json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2",
                      "enzyme_set": "golden_gate"}, headers=_user())
    assert r3.status_code == 200 and r3.json()["meta"]["enzyme_set"] == "golden_gate"
    assert len(calls) == 2


def test_map_import_snapgene_errors_and_gating(client, vectors_dir, monkeypatch):
    def _boom(set_id, plasmid_id, max_length=None):
        raise MapParseError("上游未找到或拒绝访问（HTTP 404）", 404)
    monkeypatch.setattr(oi, "snapgene_plasmid_record", _boom)

    c, s = client
    r = c.post("/api/map/import/snapgene", json={"set_id": "s", "plasmid_id": "p"}, headers=_user())
    assert r.status_code == 404 and "404" in r.json()["detail"]
    assert c.post("/api/map/import/snapgene", json={}, headers=_user()).status_code == 422

    s["anonymous_features"] = [f for f in ALL_FEATURES if f != "map"]
    assert c.get("/api/map/import/sources").status_code == 403
    assert c.post("/api/map/import/snapgene",
                  json={"set_id": "s", "plasmid_id": "p"}).status_code == 403


def test_snapgene_ids_with_parens_and_plus_import(client, vectors_dir, monkeypatch):
    """索引里的经典载体 ID 带括号/加号（pET-28a(+)）：搜得到就必须导得进。

    2026-10-18 回归：`_PLASMID_ID_RE` 白名单不认 `()+'&`，搜索列表正常（不校验 ID），
    点导入/预览一律 422「序列 ID 无效」——19 个集合 2822 行索引里 488 个 ID 受影响。
    """
    sid, pid = "pet_and_duet_vectors_(novagen)", "pET-28a(+)"
    row = {"name": "pET-28a(+)", "plasmid_id": pid, "filename": "pET-28a(+).dna", "index": 0}
    monkeypatch.setattr(oi.src, "snapgene_sets",
                        lambda **kw: [{"set_id": sid, "name": "pET & Duet"}])
    monkeypatch.setattr(oi.src, "snapgene_set_index", lambda set_id, **kw: [row])
    monkeypatch.setattr(oi.src, "snapgene_plasmid_bytes",
                        lambda *a, **kw: _snapgene_bytes())

    c, _ = client
    # 搜索：跨集合搜索能列出它（这里顺带确认 set_id 里的括号也不影响）
    hits = c.get("/api/map/import/snapgene/search", params={"q": "pet-28a"}, headers=_user())
    assert hits.status_code == 200, hits.text
    assert [p["plasmid_id"] for p in hits.json()["plasmids"]] == [pid]
    assert hits.json()["plasmids"][0]["set_id"] == sid

    # 图谱页导入：此前这里是 422
    r = c.post("/api/map/import/snapgene", json={"set_id": sid, "plasmid_id": pid},
               headers=_user())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["length"] == 300 and any(f["name"] == "myCDS" for f in d["features"])

    # 载体库侧预览与落库（同一套校验）
    prev = c.get("/api/vectors/import/snapgene/preview",
                 params={"set_id": sid, "plasmid_id": pid}, headers=_user())
    assert prev.status_code == 200, prev.text
    saved = c.post("/api/vectors/import/snapgene", json={"set_id": sid, "plasmid_id": pid},
                   headers=_admin())
    assert saved.status_code == 200, saved.text
    # 落库 ID 走 safe_slug：`(+)` 归一成下划线后去掉尾部 `._`
    assert saved.json()["vector"]["id"] == "snapgene_pET-28a"


def test_map_import_ensembl_linear_fragment(client, vectors_dir, monkeypatch):
    fasta = f">ENST00000288602 cds\n{SEQ300}\n".encode()
    seen = []
    real_record = src.ensembl_record  # 先取原函数，避免自递归

    def _record(identifier, seq_type="cds", name=None, max_length=None, **kw):
        seen.append((identifier, seq_type, max_length))
        return real_record(identifier, seq_type, name=name,
                           max_length=max_length, opener=_opener(fasta))
    monkeypatch.setattr(oi.src, "ensembl_record", _record)

    c, _ = client
    r = c.post("/api/map/import/ensembl",
               json={"identifier": "ENST00000288602", "seq_type": "cds"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["length"] == 300 and d["topology"] == "linear"
    from app.routes.models import MAP_MAX_LENGTH
    assert seen == [("ENST00000288602", "cds", MAP_MAX_LENGTH)]
    assert c.post("/api/map/import/ensembl",
                  json={"identifier": "ENST00000288602", "seq_type": "protein"}
                  ).status_code == 422


def test_snapgene_record_enforces_max_length():
    """图谱上限（MAP_MAX_LENGTH）在解析阶段就生效，超长 413"""
    with pytest.raises(MapParseError) as ei:
        src.snapgene_record(_snapgene_bytes(), name="pYES2", max_length=299)
    assert ei.value.status == 413


def test_online_map_rate_limit_bucket():
    from app.rate_limit import classify_endpoint
    assert classify_endpoint("POST", "/api/map/import/snapgene") == "ncbi"
    assert classify_endpoint("POST", "/api/map/import/ensembl") == "ncbi"
    assert classify_endpoint("GET", "/api/map/import/snapgene/sets") == "ncbi"
    assert classify_endpoint("GET", "/api/map/import/sources") == "default"
    assert classify_endpoint("POST", "/api/map/import/ncbi") == "ncbi"


def _admin():
    """模块级管理员令牌（各用例共用，DB 由 conftest 隔离）"""
    global _ADMIN_HEADERS
    if _ADMIN_HEADERS is None:
        import uuid
        from app.auth.jwt_auth import create_access_token, db_user_to_user, hash_password
        from app.database import SessionLocal, create_user
        email = f"adm-online-{uuid.uuid4().hex[:8]}@example.com"
        db = SessionLocal()
        try:
            u = create_user(db, email=email, username=email.split("@")[0],
                            hashed_password=hash_password("Passw0rd!!"), is_admin=True,
                            email_verified=True)
            _ADMIN_HEADERS = {"Authorization": f"Bearer {create_access_token(db_user_to_user(u))}"}
        finally:
            db.close()
    return _ADMIN_HEADERS


_ADMIN_HEADERS = None
_USER_HEADERS = None


def _user():
    """模块级普通登录用户令牌（SnapGene 在线库只读端点需登录，审查 H2）"""
    global _USER_HEADERS
    if _USER_HEADERS is None:
        import uuid
        from app.auth.jwt_auth import create_access_token, db_user_to_user, hash_password
        from app.database import SessionLocal, create_user
        email = f"usr-online-{uuid.uuid4().hex[:8]}@example.com"
        db = SessionLocal()
        try:
            u = create_user(db, email=email, username=email.split("@")[0],
                            hashed_password=hash_password("Passw0rd!!"), is_admin=False,
                            email_verified=True)
            _USER_HEADERS = {"Authorization": f"Bearer {create_access_token(db_user_to_user(u))}"}
        finally:
            db.close()
    return _USER_HEADERS


# ================================================================ 出处记录（data_provenance）
# 导入的载体除描述里那句可读标注外，还必须有结构化出处：内置载体早有
# data_provenance（来源库/集合/日期），导入载体此前只在 description 里写了一句，
# 界面无法结构化展示、日后也无法按 URL 重取。

def _gbk(seq: str = SEQ300, features: str = "", topology: str = "circular",
         name: str = "TESTPLAS") -> bytes:
    """可被 Biopython 接受的 GenBank 文本（与 test_plasmid_map_parsers._gb 同构）"""
    lines = [f"LOCUS       {name:<16}{len(seq):>12} bp    DNA     {topology:<8} SYN 01-JAN-2026",
             "DEFINITION  test.", "FEATURES             Location/Qualifiers", features.rstrip("\n"),
             "ORIGIN"]
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60].lower()
        lines.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10)))
    lines.append("//")
    return ("\n".join(lines) + "\n").encode()


def _load_vector(vectors_dir, vid):
    from core.vector_library import VectorLibrary
    lib = VectorLibrary()
    lib.load_from_directory(str(vectors_dir))
    return lib.get_vector(vid)


def test_snapgene_import_records_provenance(client, vectors_dir, monkeypatch):
    record = src.snapgene_record(_snapgene_bytes(), name="pYES2")
    url = "https://www.snapgene.com/local/fetch.php?set=yeast_plasmids&plasmid=pYES2"

    async def _fetch(set_id, plasmid_id):
        return record, url
    monkeypatch.setattr(osr, "_snapgene_fetch", _fetch)

    c, _ = client
    r = c.post("/api/vectors/import/snapgene",
               json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}, headers=_admin())
    assert r.status_code == 200, r.text
    prov = r.json()["vector"]["provenance"]
    assert prov["source"] == "SnapGene 在线质粒库" and prov["url"] == url
    assert prov["set"] == "yeast_plasmids" and prov["plasmid"] == "pYES2"
    assert prov["fetched_at"].endswith("Z") and "GraphPad" in prov["license"]
    assert prov["citation"] == "www.snapgene.com/resources"

    # 落盘 → 重新加载（VectorLibrary）后出处仍在，且与回执一致
    v = _load_vector(vectors_dir, "snapgene_pYES2")
    assert v is not None and v.provenance == prov
    # 详情接口把出处带给前端（详情页的「出处 / 许可」块）
    d = c.get("/api/vectors/snapgene_pYES2")
    assert d.status_code == 200, d.text
    assert d.json()["data_provenance"] == prov


def test_upload_and_ncbi_style_provenance_shape(tmp_path):
    """上传/批量导入的历史路径也写出 data_provenance，且加载端容错非 dict"""
    from app.vector_import import provenance_block, record_from_parts, save_record
    from core.external_vector_importer import ExternalVector, VectorLibraryManager
    from core.vector_library import VectorLibrary

    rec = record_from_parts(name="up", sequence=SEQ300)
    save_record(rec, source="genbank", vectors_dir=str(tmp_path),
                provenance=provenance_block("本地上传文件", filename="x.gbk", format="genbank"))
    manager = VectorLibraryManager()
    manager.vectors["ncbi_L09137"] = ExternalVector(
        id="ncbi_L09137", name="L09137", sequence=SEQ300, source="NCBI",
        description="from NCBI", vector_type="cloning", host=[], antibiotic_resistance=[],
        features=[{"name": "f", "type": "CDS", "start": 1, "end": 30}],
        url="https://www.ncbi.nlm.nih.gov/nuccore/L09137")
    manager.export_to_yaml(str(tmp_path))

    lib = VectorLibrary()
    assert lib.load_from_directory(str(tmp_path)) == 2
    assert lib.get_vector("genbank_up").provenance["filename"] == "x.gbk"
    ncbi = lib.get_vector("ncbi_L09137")
    assert ncbi.provenance["url"].endswith("/L09137") and ncbi.provenance["source"] == "NCBI"

    # 脏数据（data_provenance 不是 dict）不能让整份载体加载失败
    (tmp_path / "dirty.yaml").write_text(
        "id: dirty\nname: dirty\nsequence: ACGTACGTACGT\ndata_provenance: oops\n"
        "features:\n- name: f\n  type: CDS\n  start: 1\n  end: 3\n", encoding="utf-8")
    lib2 = VectorLibrary()
    assert lib2.load_from_directory(str(tmp_path)) == 3
    assert lib2.get_vector("dirty").provenance == {}


def test_save_map_as_vector_endpoint(client, vectors_dir):
    """图谱页「另存为载体」：序列/特征由图谱页提交，落盘后载体库能加载"""
    c, _ = client
    payload = {
        "name": "myConstruct",
        "sequence": SEQ300,
        "topology": "circular",
        "features": [{"name": "insert", "type": "insert", "start": 1, "end": 30,
                      "strand": 1, "notes": "来自图谱页"}],
        "source": "paste",
        "description": "由图谱页保存",
    }
    assert c.post("/api/vectors/import/from-map", json=payload).status_code in (401, 403)

    r = c.post("/api/vectors/import/from-map", json=payload, headers=_admin())
    assert r.status_code == 200, r.text
    vec = r.json()["vector"]
    assert vec["id"] == "paste_myConstruct" and vec["length"] == 300
    assert vec["features"] == 1 and vec["topology"] == "circular"

    v = _load_vector(vectors_dir, "paste_myConstruct")
    assert v is not None and len(v.sequence) == 300
    assert v.elements[0].name == "insert"
    assert v.provenance["source"] == "图谱页保存" and v.provenance["origin"] == "paste"

    # 图谱类型别名：mcs/rep_origin 不再落成 other（导入载体的类型保真）
    alias = c.post("/api/vectors/import/from-map",
                   json=dict(payload, name="typedMap", features=[
                       {"name": "MCS", "type": "mcs", "start": 1, "end": 20},
                       {"name": "ori", "type": "rep_origin", "start": 21, "end": 60},
                       {"name": "ins", "type": "insert", "start": 61, "end": 90}]),
                   headers=_admin())
    assert alias.status_code == 200, alias.text
    vt = _load_vector(vectors_dir, "paste_typedMap")
    assert [e.element_type.value for e in vt.elements] == [
        "multiple_cloning_site", "origin", "other"]

    # 非法碱基 422；超 MAP_MAX_LENGTH 被请求模型挡下（同为 4xx，不会静默截断）
    assert c.post("/api/vectors/import/from-map",
                  json=dict(payload, sequence="ACGTZ"), headers=_admin()).status_code == 422
    from app.routes.models import MAP_MAX_LENGTH
    huge = c.post("/api/vectors/import/from-map",
                  json=dict(payload, sequence="ACGT" * (MAP_MAX_LENGTH // 4 + 1)),
                  headers=_admin())
    assert huge.status_code in (413, 422)
    # 名称必填
    assert c.post("/api/vectors/import/from-map",
                  json=dict(payload, name=""), headers=_admin()).status_code == 422

    # 线性拓扑与 strand=-1 原样保留
    lin = c.post("/api/vectors/import/from-map",
                 json=dict(payload, name="linFrag", topology="linear",
                           features=[{"name": "rev", "type": "cds", "start": 1, "end": 30,
                                      "strand": -1}]), headers=_admin())
    assert lin.status_code == 200 and lin.json()["vector"]["topology"] == "linear"
    v2 = _load_vector(vectors_dir, "paste_linFrag")
    assert v2.topology == "linear" and v2.elements[0].strand == "-"
    # 线性片段不再被当环状质粒（列表/详情/图谱三处口径一致）
    assert c.get("/api/vectors/paste_linFrag").json()["topology"] == "linear"
    mp = next(v for v in c.get("/api/vectors").json() if v["id"] == "paste_linFrag")
    assert mp["topology"] == "linear" and mp["map_preview"]["topology"] == "linear"


# ================================================================ Addgene（用户自备凭据）

def test_addgene_cookie_only_sent_to_addgene(monkeypatch):
    """凭据只发给 https 的 addgene.org：直链指向别处或是 http 时不带 Cookie"""
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "SESSION=secret-token")
    assert src.addgene_has_session() is True
    assert src.addgene_session_cookie() == "SESSION=secret-token"
    for host in ("https://www.addgene.org/api/x/", "https://addgene.org/a.gbk",
                 "https://media.addgene.org/a.gbk", "https://www.addgene.org:443/a.gbk"):
        assert src._addgene_cookie_for(host) == "SESSION=secret-token"
    for evil in ("https://evil.example.com/a.gbk", "https://addgene.org.evil.com/a.gbk",
                 "http://127.0.0.1/a.gbk", "http://www.addgene.org/a.gbk",
                 "file:///etc/passwd", "https://evil.com@x.example/a"):
        assert src._addgene_cookie_for(evil) is None

    monkeypatch.delenv("ADDGENE_SESSION_COOKIE")
    assert src.addgene_has_session() is False and src.addgene_session_cookie() == ""
    assert src._addgene_cookie_for("https://www.addgene.org/api/x/") is None


def test_addgene_record_uses_session_and_parses(monkeypatch):
    meta = {"seqObjectId": "52961", "genbankUrl": "https://www.addgene.org/x.gbk"}
    monkeypatch.setattr(src, "addgene_metadata", lambda oid, **kw: meta)
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "SESSION=abc")
    seen = []
    rec = src.addgene_record("52961", name="lentiCRISPRv2",
                             opener=_opener(_gbk(), seen))
    assert len(rec.sequence) == 300 and rec.name == "lentiCRISPRv2"
    assert seen[0][0].get_header("Cookie") == "SESSION=abc"

    # 凭据过期/无权限 → 403 且提示更新凭据（仍然不代登录）
    with pytest.raises(src.AddgeneLoginRequired) as ei:
        src.addgene_record("52961",
                           opener=_raiser(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)))
    assert ei.value.status == 403 and "过期" in str(ei.value)

    # 无凭据时：匿名失败 → 403 + 「不绕过登录」指引，且绝不返回空序列
    monkeypatch.delenv("ADDGENE_SESSION_COOKIE")
    with pytest.raises(src.AddgeneLoginRequired) as ei2:
        src.addgene_record("52961",
                           opener=_raiser(urllib.error.HTTPError("u", 404, "Not Found", {}, None)))
    assert ei2.value.status == 403 and "不绕过登录" in str(ei2.value)


def test_addgene_import_route(client, vectors_dir, monkeypatch):
    record = src.snapgene_record(_snapgene_bytes(), name="lentiCRISPRv2")

    def _rec(oid, **kw):
        assert oid == "52961" and kw.get("fmt") == "dna"
        return record
    monkeypatch.setattr(osr.src, "addgene_record", _rec)

    c, _ = client
    assert c.post("/api/vectors/import/addgene/52961").status_code in (401, 403)
    r = c.post("/api/vectors/import/addgene/52961", params={"fmt": "dna"}, headers=_admin())
    assert r.status_code == 200, r.text
    v = r.json()["vector"]
    assert v["id"] == "addgene_52961" and v["length"] == 300
    assert v["provenance"]["credential"] == "用户自备会话"
    assert "Addgene" in v["provenance"]["source"]
    assert _load_vector(vectors_dir, "addgene_52961") is not None
    # fmt 只接受 genbank/dna
    assert c.post("/api/vectors/import/addgene/52961", params={"fmt": "exe"},
                  headers=_admin()).status_code == 422


def test_addgene_login_required_surfaces_403(client, vectors_dir, monkeypatch):
    def _boom(oid, **kw):
        raise src.AddgeneLoginRequired("Addgene 全长序列需要登录…不绕过登录")
    monkeypatch.setattr(osr.src, "addgene_record", _boom)
    c, _ = client
    r = c.post("/api/vectors/import/addgene/52961", headers=_admin())
    assert r.status_code == 403 and "绕过登录" in r.json()["detail"]


# ================================================================ UniProt（蛋白库反查）

UNIPROT_ENTRY = {
    "primaryAccession": "P04637",
    "uniProtkbId": "P53_HUMAN",
    "entryType": "UniProtKB reviewed (Swiss-Prot)",
    "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
    "genes": [{"geneName": {"value": "TP53"}}],
    "organism": {"scientificName": "Homo sapiens"},
    "sequence": {"length": 393},
    "uniProtKBCrossReferences": [
        {"database": "EMBL", "id": "X54156",
         "properties": [{"key": "ProteinId", "value": "CAA38098.1"},
                        {"key": "MoleculeType", "value": "mRNA"}]},
        {"database": "RefSeq", "id": "NM_000546.6"},
        {"database": "Ensembl", "id": "ENST00000269305.9",
         "properties": [{"key": "ProteinId", "value": "ENST00000269305.9"}]},
        {"database": "CCDS", "id": "CCDS11118.1"},
        {"database": "PDB", "id": "1TUP"},
        {"database": "STRING", "id": "9606.ENSP00000269305"},
    ],
}


def test_uniprot_search_and_entry(monkeypatch):
    seen = []
    rows = src.uniprot_search("TP53", limit=2, opener=_opener({"results": [UNIPROT_ENTRY]}, seen))
    assert len(rows) == 1 and rows[0]["accession"] == "P04637"
    assert rows[0]["gene"] == "TP53" and rows[0]["length"] == 393 and rows[0]["reviewed"] is True
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0][0].full_url).query)
    assert q["size"] == ["2"] and q["query"] == ["TP53"] and q["fields"]

    src.uniprot_search("TP53", reviewed_only=True, opener=_opener({"results": []}, seen))
    q2 = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[1][0].full_url).query)
    assert "reviewed:true" in q2["query"][0]

    entry = src.uniprot_entry("P04637", opener=_opener(UNIPROT_ENTRY))
    assert entry["entry_id"] == "P53_HUMAN" and entry["name"].startswith("Cellular tumor")
    # 只有核酸库进候选：蛋白/结构/互作库（PDB、STRING）不算取 DNA 的入口
    cands = src.nucleotide_candidates(entry)
    accs = {c["accession"] for c in cands}
    assert accs == {"X54156", "NM_000546.6", "ENST00000269305", "CCDS11118.1"}
    assert all(c["url"].startswith("https://") for c in cands)
    assert next(c for c in cands if c["accession"] == "ENST00000269305")["source"] == "ensembl"

    # 参数校验与上游异常映射
    with pytest.raises(MapParseError) as ei:
        src.uniprot_search("", opener=_opener({"results": []}))
    assert ei.value.status == 422
    with pytest.raises(MapParseError) as ei2:
        src.uniprot_entry("bad/id", opener=_opener({}))
    assert ei2.value.status == 422
    with pytest.raises(MapParseError) as ei3:
        src.uniprot_search("TP53", opener=_opener({"nope": 1}))
    assert ei3.value.status == 502


def test_uniprot_fasta_and_search_shape(monkeypatch):
    fasta = b">sp|P04637|P53_HUMAN Cellular tumor antigen p53\nMEEPQSDPSV\n"
    assert src.uniprot_fasta("P04637", opener=_opener(fasta)).startswith(">sp|P04637")
    with pytest.raises(MapParseError) as ei:
        src.uniprot_fasta("P04637", opener=_opener(b"not fasta"))
    assert ei.value.status == 502
    # 条目里没有推荐名时退回提交名
    view = src._uniprot_entry_view({
        "primaryAccession": "A0A1", "proteinDescription":
            {"submissionNames": [{"fullName": {"value": "Putative thing"}}]},
        "uniProtKBCrossReferences": []})
    assert view["name"] == "Putative thing" and view["reviewed"] is False


def test_uniprot_routes(client, monkeypatch):
    monkeypatch.setattr(osr.src, "uniprot_search",
                        lambda q, **kw: [src._uniprot_entry_view(UNIPROT_ENTRY)])
    monkeypatch.setattr(osr.src, "uniprot_entry",
                        lambda acc, **kw: src._uniprot_entry_view(UNIPROT_ENTRY))
    monkeypatch.setattr(osr.src, "uniprot_fasta", lambda acc, **kw: ">sp|P04637|\nMEEPQ\n")

    c, _ = client
    r = c.get("/api/vectors/import/uniprot/search", params={"query": "TP53"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["results"][0]["accession"] == "P04637"
    # 检索是精简字段（不带交叉引用），核酸入口一律由条目端点给出
    assert "nucleotide" not in body["results"][0] and "GET" in body["note"]
    assert "CC BY" in body["license"]

    e = c.get("/api/vectors/import/uniprot/P04637")
    assert e.status_code == 200, e.text
    assert e.json()["entry"]["gene"] == "TP53" and e.json()["nucleotide"]
    assert "fasta" not in e.json()
    f = c.get("/api/vectors/import/uniprot/P04637", params={"with_fasta": 1})
    assert f.json()["fasta"].startswith(">sp|P04637")

    assert c.get("/api/vectors/import/uniprot/bad/id").status_code == 404  # 路径分段 → 未匹配
    assert c.get("/api/vectors/import/uniprot/search", params={"query": ""}).status_code == 422


def test_sources_payload_lists_uniprot_and_addgene_session(client, vectors_dir, monkeypatch):
    c, _ = client
    ids = {s["id"]: s for s in c.get("/api/vectors/import/sources").json()["sources"]}
    assert set(ids) >= {"snapgene", "ensembl", "addgene", "ncbi", "uniprot"}
    assert ids["uniprot"]["importable"] is False and "CC BY" in ids["uniprot"]["license"]
    assert "蛋白库" in ids["uniprot"]["note"]
    assert ids["addgene"]["requires_login"] is True
    assert ids["addgene"]["session_configured"] is False and ids["addgene"]["importable"] is False

    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "SESSION=x")
    ids2 = {s["id"]: s for s in c.get("/api/vectors/import/sources",
                                        headers=_admin()).json()["sources"]}
    assert ids2["addgene"]["session_configured"] is True
    assert ids2["addgene"]["importable"] is True and "已配置" in ids2["addgene"]["note"]
    # 会话是否配置只告诉管理员（审查 M1）：匿名与普通用户一律 False
    for headers in (None, _user()):
        for path in ("/api/vectors/import/sources", "/api/map/import/sources"):
            ad = {s["id"]: s for s in c.get(path, headers=headers or {}).json()["sources"]}
            assert ad["addgene"]["session_configured"] is False
            assert ad["addgene"]["importable"] is False and "已配置" not in ad["addgene"]["note"]


def test_online_import_rate_limit_buckets_for_new_endpoints():
    from app.rate_limit import classify_endpoint
    assert classify_endpoint("POST", "/api/vectors/import/from-map") == "upload"
    assert classify_endpoint("POST", "/api/vectors/import/addgene/52961") == "upload"
    assert classify_endpoint("GET", "/api/vectors/import/uniprot/search") == "ncbi"
    assert classify_endpoint("GET", "/api/vectors/import/uniprot/P04637") == "ncbi"
    assert classify_endpoint("GET", "/api/vectors/import/addgene/52961") == "ncbi"
    assert classify_endpoint("GET", "/api/vectors/import/addgene/search") == "default"
    assert classify_endpoint("GET", "/api/vectors/import/sources") == "default"


# ================================================================ 重定向与 scheme（审查 H1）
# 走真实的 urllib OpenerDirector（src._default_open → build_opener + 重定向处理器），
# 只把最底层的 http(s)_open 换成内存里的"互联网"，从而真实执行 urllib 的 3xx 处理链。

import email.message
import urllib.request
import urllib.response

_REAL_DEFAULT_OPEN = src._default_open


class _FakeNet:
    """url → (status, headers, body)；记录每次真正发出的请求（含 unredirected 头）"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def handlers(self):
        net = self

        def _serve(req):
            url = req.full_url
            hdrs = {k.lower(): v for k, v in req.header_items()}
            net.requests.append((url, hdrs))
            status, headers, body = net.routes.get(url, (404, {}, b"nope"))
            msg = email.message.Message()
            for k, v in headers.items():
                msg[k] = v
            resp = urllib.response.addinfourl(io.BytesIO(body), msg, url, status)
            resp.msg = "fake"
            return resp

        class _Https(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return _serve(req)

        class _Http(urllib.request.HTTPHandler):
            def http_open(self, req):
                return _serve(req)

        return [_Https(), _Http()]


@pytest.fixture()
def fake_net(monkeypatch):
    def _make(routes):
        net = _FakeNet(routes)
        monkeypatch.setattr(src, "_default_open", _REAL_DEFAULT_OPEN)
        monkeypatch.setattr(src, "_EXTRA_HANDLERS", net.handlers())
        return net
    return _make


def test_stdlib_redirect_would_leak_plain_cookie_header(fake_net):
    """对照组：标准 urllib 自动重定向会把普通 Cookie 头带到第三方主机（H1 的根因），
    而 add_unredirected_header 加的头不会被带走。"""
    net = fake_net({
        "https://sequences.addgene.org/x.gbk": (302, {"Location": "https://evil.example/x"}, b""),
        "https://evil.example/x": (200, {}, b"LOCUS"),
    })
    std = urllib.request.build_opener(*net.handlers())
    std.open(urllib.request.Request("https://sequences.addgene.org/x.gbk",
                                    headers={"Cookie": "sessionid=SECRET"})).read()
    assert net.requests[-1][0] == "https://evil.example/x"
    assert net.requests[-1][1].get("cookie") == "sessionid=SECRET"   # 泄露

    net.requests.clear()
    req = urllib.request.Request("https://sequences.addgene.org/x.gbk")
    req.add_unredirected_header("Cookie", "sessionid=SECRET")
    std.open(req).read()
    assert net.requests[0][1].get("cookie") == "sessionid=SECRET"
    assert "cookie" not in net.requests[1][1]


def test_cookie_not_sent_across_host_redirect(fake_net, monkeypatch):
    """Addgene 直链 302 到第三方主机：不跟随（主机不在白名单），且第三方从未收到请求"""
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=SECRET")
    net = fake_net({
        "https://www.addgene.org/api/get-sequence-file-collection/52961/":
            (200, {}, json.dumps({"genbankUrl": "https://sequences.addgene.org/x.gbk"}).encode()),
        "https://sequences.addgene.org/x.gbk":
            (302, {"Location": "https://bucket.s3.example.com/x.gbk?sig=1"}, b""),
        "https://bucket.s3.example.com/x.gbk?sig=1": (200, {}, _gbk()),
    })
    with pytest.raises(src.UnsafeUrlError) as ei:
        src.addgene_sequence_bytes("52961")
    assert "白名单" in str(ei.value)
    urls = [u for u, _ in net.requests]
    assert "https://bucket.s3.example.com/x.gbk?sig=1" not in urls
    # 元数据接口本身不带 Cookie（只有直链下载才带）
    assert "cookie" not in net.requests[0][1]
    assert net.requests[1][1].get("cookie") == "sessionid=SECRET"


def test_cookie_dropped_on_cross_host_hop_within_addgene(fake_net, monkeypatch):
    """addgene 子域之间跳转（白名单内）：允许跟随，但跨主机后不再带 Cookie"""
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=SECRET")
    net = fake_net({
        "https://www.addgene.org/api/get-sequence-file-collection/52961/":
            (200, {}, json.dumps({"genbankUrl": "https://sequences.addgene.org/x.gbk"}).encode()),
        "https://sequences.addgene.org/x.gbk":
            (302, {"Location": "https://media.addgene.org/x.gbk"}, b""),
        "https://media.addgene.org/x.gbk":
            (302, {"Location": "https://sequences.addgene.org/final.gbk"}, b""),
        "https://sequences.addgene.org/final.gbk": (200, {}, _gbk()),
    })
    data = src.addgene_sequence_bytes("52961")
    assert data.startswith(b"LOCUS")
    hops = {u: h for u, h in net.requests}
    assert hops["https://sequences.addgene.org/x.gbk"].get("cookie") == "sessionid=SECRET"
    assert "cookie" not in hops["https://media.addgene.org/x.gbk"]
    # 跳回原主机也不再恢复凭据（一旦跨主机即永久丢弃）
    assert "cookie" not in hops["https://sequences.addgene.org/final.gbk"]


def test_redirect_downgrade_to_http_rejected(fake_net, monkeypatch):
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=SECRET")
    net = fake_net({
        "https://sequences.addgene.org/x.gbk":
            (301, {"Location": "http://sequences.addgene.org/x.gbk"}, b""),
        "http://sequences.addgene.org/x.gbk": (200, {}, _gbk()),
    })
    with pytest.raises(src.UnsafeUrlError):
        src._http_get("https://sequences.addgene.org/x.gbk", max_bytes=1 << 20,
                      cookie="sessionid=SECRET")
    assert [u for u, _ in net.requests] == ["https://sequences.addgene.org/x.gbk"]


def test_redirect_hop_limit_and_relative_location(fake_net):
    base = src.ENSEMBL_BASE
    net = fake_net({
        f"{base}/a": (302, {"Location": "/b"}, b""),
        f"{base}/b": (307, {"Location": f"{base}/c"}, b""),
        f"{base}/c": (200, {}, b"ok"),
        f"{base}/loop": (302, {"Location": f"{base}/loop"}, b""),
    })
    assert src._http_get(f"{base}/a", max_bytes=100) == b"ok"
    with pytest.raises(src.OnlineSourceError) as ei:
        src._http_get(f"{base}/loop", max_bytes=100)
    assert ei.value.status == 502 and "跳" in str(ei.value)
    assert sum(1 for u, _ in net.requests if u.endswith("/loop")) == src._MAX_REDIRECTS + 1


def test_redirect_to_private_or_unknown_host_rejected(fake_net):
    """SnapGene/Ensembl/UniProt 外呼被上游 302 到内网/任意主机：拒绝（SSRF 面）"""
    net = fake_net({
        f"{src.ENSEMBL_BASE}/x": (302, {"Location": "https://169.254.169.254/latest"}, b""),
    })
    with pytest.raises(src.UnsafeUrlError):
        src._http_get(f"{src.ENSEMBL_BASE}/x", max_bytes=100)
    assert all("169.254" not in u for u, _ in net.requests)


@pytest.mark.parametrize("direct", [
    "file:///etc/passwd", "http://www.addgene.org/x.gbk", "ftp://www.addgene.org/x.gbk",
    "data:text/plain,LOCUS", "https://evil.example.com/x.gbk",
    "https://www.addgene.org.evil.com/x.gbk",
])
def test_addgene_direct_link_scheme_and_host_checked(monkeypatch, direct):
    """上游 JSON 给出的直链：非 https / 非 addgene 主机一律拒绝，且根本不外呼"""
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=SECRET")
    monkeypatch.setattr(src, "addgene_metadata", lambda oid, **kw: {"genbankUrl": direct})
    calls = []
    with pytest.raises(src.UnsafeUrlError):
        src.addgene_sequence_bytes("52961", opener=_opener(b"LOCUS", calls))
    assert calls == []


def test_checked_host_uses_bare_hostname():
    assert src._checked_host("https://www.addgene.org:443/a") == "www.addgene.org"
    assert src._checked_host(f"{src.SNAPGENE_BASE}/sets.php?xml") == "www.snapgene.com"
    for bad in ("http://rest.ensembl.org/x", "https://rest.ensembl.org.evil.com/x",
                "https://user@evil.com/x", "file:///etc/passwd", ""):
        with pytest.raises(src.UnsafeUrlError):
            src._checked_host(bad)


# ================================================================ SnapGene 在线库开关（审查 H2）

_SNAPGENE_READ_ENDPOINTS = [
    ("GET", "/api/vectors/import/snapgene/sets", None),
    ("GET", "/api/vectors/import/snapgene/sets/yeast_plasmids", None),
    ("GET", "/api/vectors/import/snapgene/preview?set_id=yeast_plasmids&plasmid_id=pYES2", None),
    ("GET", "/api/map/import/snapgene/sets", None),
    ("GET", "/api/map/import/snapgene/sets/yeast_plasmids", None),
    ("POST", "/api/map/import/snapgene", {"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}),
]


def _call(c, method, url, body, headers=None):
    if method == "GET":
        return c.get(url, headers=headers or {})
    return c.post(url, json=body, headers=headers or {})


def _no_upstream(monkeypatch):
    """任何 SnapGene 外呼都算失败：开关/鉴权必须在外呼之前拦下"""
    def _boom(*a, **k):
        raise AssertionError("不应外呼 SnapGene")
    for name in ("snapgene_sets", "snapgene_set_index", "snapgene_plasmid_bytes"):
        monkeypatch.setattr(src, name, _boom)
    monkeypatch.setattr(oi, "snapgene_plasmid_record", _boom)


def test_snapgene_online_default_on(monkeypatch):
    monkeypatch.delenv("SNAPGENE_ONLINE_ENABLED", raising=False)
    assert src.snapgene_online_enabled() is True
    # 与总开关相互独立：SnapGene 可单独关闭
    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "false")
    assert src.snapgene_online_enabled() is False and src.import_enabled() is True
    for raw, want in (("true", True), ("1", True), ("on", True), ("false", False),
                      ("0", False), ("off", False), ("", True), ("nope", True)):
        monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", raw)
        assert src.snapgene_online_enabled() is want


def test_snapgene_core_refuses_when_off(monkeypatch):
    """核心层也兜底：开关关闭时连 opener 都不调用"""
    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "false")
    seen = []
    for fn, args in ((src.snapgene_sets, ()), (src.snapgene_set_index, ("yeast_plasmids",)),
                     (src.snapgene_plasmid_bytes, ("yeast_plasmids", "pYES2"))):
        with pytest.raises(MapParseError) as ei:
            fn(*args, opener=_opener(SETS_XML, seen))
        assert ei.value.status == 404 and "SNAPGENE_ONLINE_ENABLED" in str(ei.value)
    assert seen == []


@pytest.mark.parametrize("method,url,body", _SNAPGENE_READ_ENDPOINTS)
def test_snapgene_endpoints_404_when_off(client, vectors_dir, monkeypatch, method, url, body):
    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "false")
    _no_upstream(monkeypatch)
    c, _ = client
    for headers in (None, _user(), _admin()):
        r = _call(c, method, url, body, headers)
        assert r.status_code == 404, r.text
        assert "SNAPGENE_ONLINE_ENABLED" in r.json()["detail"]


def test_snapgene_save_404_when_off_even_for_admin(client, vectors_dir, monkeypatch):
    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "false")
    _no_upstream(monkeypatch)
    c, _ = client
    body = {"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}
    for headers in (None, _user(), _admin()):
        r = c.post("/api/vectors/import/snapgene", json=body, headers=headers or {})
        assert r.status_code == 404 and "SNAPGENE_ONLINE_ENABLED" in r.json()["detail"]
    assert list(vectors_dir.iterdir()) == []


@pytest.mark.parametrize("method,url,body", _SNAPGENE_READ_ENDPOINTS)
def test_snapgene_endpoints_require_login_when_on(client, vectors_dir, monkeypatch,
                                                  method, url, body):
    _no_upstream(monkeypatch)
    c, _ = client
    r = _call(c, method, url, body)
    assert r.status_code == 401 and "登录" in r.json()["detail"]


def test_snapgene_save_stays_admin_only_when_on(client, vectors_dir, monkeypatch):
    _no_upstream(monkeypatch)
    c, _ = client
    body = {"set_id": "yeast_plasmids", "plasmid_id": "pYES2"}
    assert c.post("/api/vectors/import/snapgene", json=body).status_code == 401
    assert c.post("/api/vectors/import/snapgene", json=body, headers=_user()).status_code == 403


def test_sources_payload_reports_snapgene_flag(client, vectors_dir, monkeypatch):
    c, _ = client
    on = c.get("/api/map/import/sources").json()
    sg = next(s for s in on["sources"] if s["id"] == "snapgene")
    assert on["snapgene_online_enabled"] is True and sg["enabled"] is True
    assert sg["requires_login"] is True and sg["importable"] is True

    monkeypatch.setenv("SNAPGENE_ONLINE_ENABLED", "false")
    for path in ("/api/map/import/sources", "/api/vectors/import/sources"):
        off = c.get(path).json()
        sg = next(s for s in off["sources"] if s["id"] == "snapgene")
        assert off["snapgene_online_enabled"] is False
        assert sg["enabled"] is False and sg["importable"] is False
        assert "SNAPGENE_ONLINE_ENABLED" in sg["note"]
        # 其他来源不受 SnapGene 开关影响
        assert off["enabled"] is True


# ================================================================ Addgene try_sequence 仅管理员（审查 M1）

def test_addgene_try_sequence_admin_only(client, vectors_dir, monkeypatch):
    """配置了部署方会话时，匿名/普通用户不能驱动它去下载全长序列"""
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=SECRET")
    monkeypatch.setattr(osr.src, "addgene_metadata", lambda oid, **kw: {"seqObjectId": oid})
    calls = []

    def _seq(oid, **kw):
        calls.append(oid)
        return b"LOCUS x"
    monkeypatch.setattr(osr.src, "addgene_sequence_bytes", _seq)

    c, _ = client
    anon = c.get("/api/vectors/import/addgene/52961", params={"try_sequence": "true"})
    assert anon.status_code == 401
    user = c.get("/api/vectors/import/addgene/52961", params={"try_sequence": "true"},
                 headers=_user())
    assert user.status_code == 403
    assert calls == []          # 未经管理员，绝不以部署方账号外呼

    # 不带 try_sequence 的元数据仍对匿名开放，但不暴露会话是否配置
    meta = c.get("/api/vectors/import/addgene/52961")
    assert meta.status_code == 200 and meta.json()["session_configured"] is False
    assert calls == []

    adm = c.get("/api/vectors/import/addgene/52961", params={"try_sequence": "true"},
                headers=_admin())
    assert adm.status_code == 200, adm.text
    body = adm.json()
    assert body["sequence_available"] is True and body["sequence_bytes"] == 7
    assert body["session_configured"] is True and calls == ["52961"]


# ================================================================ Addgene 会话仅适合单用户（审查 M2）

class _FakeQuery:
    def __init__(self, n):
        self.n = n

    def filter(self, *a, **k):
        return self

    def count(self):
        return self.n


class _FakeDB:
    def __init__(self, n):
        self.n = n

    def query(self, *a):
        return _FakeQuery(self.n)

    def close(self):
        pass


@pytest.mark.parametrize("cookie,registration,users,expect", [
    ("", True, 5, None),                        # 未配置会话：不告警
    ("sessionid=S", False, 1, None),            # 单用户自托管：不告警
    ("sessionid=S", True, 1, "开放注册"),
    ("sessionid=S", False, 3, "3 个启用账号"),
])
def test_addgene_shared_session_warning(monkeypatch, caplog, cookie, registration, users, expect):
    import app.database as dbmod
    if cookie:
        monkeypatch.setenv("ADDGENE_SESSION_COOKIE", cookie)
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: {"registration_open": registration})
    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _FakeDB(users))
    msg = oi.addgene_shared_session_warning()
    if expect is None:
        assert msg is None
    else:
        assert msg and expect in msg and "单用户" in msg and "上传文件" in msg
        assert cookie not in msg            # 绝不回显 Cookie
    caplog.clear()
    with caplog.at_level("WARNING", logger="plasmid_designer.online_import"):
        oi.log_addgene_shared_session_warning()
    warned = [r for r in caplog.records if r.name == "plasmid_designer.online_import"]
    assert bool(warned) is (expect is not None)
    assert all("sessionid=S" not in r.getMessage() for r in warned)


def test_addgene_shared_session_warning_tolerates_db_errors(monkeypatch):
    import app.database as dbmod
    monkeypatch.setenv("ADDGENE_SESSION_COOKIE", "sessionid=S")

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(site_settings, "get_settings", _boom)
    monkeypatch.setattr(dbmod, "SessionLocal", _boom)
    assert oi.addgene_shared_session_warning() is None


# ================================================================ 节流（审查 M3）

def test_throttle_does_not_serialize_different_hosts(monkeypatch):
    """A 站点在排队 sleep 时，B 站点的请求不能被同一把锁卡住（旧实现锁内 sleep）"""
    import threading
    import time as _time
    monkeypatch.setattr(src, "_MIN_INTERVAL", {"a.example": 0.6, "b.example": 0.6})
    src._throttle("a.example")                     # 占掉 a 的当前槽
    t = threading.Thread(target=src._throttle, args=("a.example",))   # 需等 ~0.6 s
    t.start()
    _time.sleep(0.05)                              # 确保线程已进入 sleep
    t0 = _time.monotonic()
    src._throttle("b.example")
    elapsed = _time.monotonic() - t0
    t.join()
    assert elapsed < 0.3, f"不同站点被串行化：B 等了 {elapsed:.2f}s"


def test_throttle_reserves_slots_and_bounds_queue(monkeypatch):
    waits = []
    monkeypatch.setattr(src.time, "sleep", lambda s: waits.append(round(s, 1)))
    monkeypatch.setattr(src, "_MIN_INTERVAL", {"x.example": 1.0})
    monkeypatch.setenv("ONLINE_THROTTLE_MAX_WAIT", "1.5")
    src._throttle("x.example")
    src._throttle("x.example")
    assert waits == [1.0]                          # 第二个请求排到下一个槽
    with pytest.raises(src.OnlineSourceError) as ei:
        src._throttle("x.example")                 # 需等 ~2 s > 1.5 s 上限
    assert ei.value.status == 429 and "排队" in str(ei.value)
    assert waits == [1.0]                          # 被拒的请求不 sleep、不占槽
    assert src._next_slot["x.example"] - src.time.monotonic() < 2.1


def test_throttle_queue_full_maps_to_http_429(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(src, "_MIN_INTERVAL", {"rest.ensembl.org": 100.0})
    monkeypatch.setenv("ONLINE_THROTTLE_MAX_WAIT", "1")
    monkeypatch.setattr(src, "_next_slot", {"rest.ensembl.org": src.time.monotonic() + 50})
    r = c.get("/api/vectors/import/ensembl/lookup/ENSG00000139618")
    assert r.status_code == 429 and "排队" in r.json()["detail"]


# ================================================================ 出处 URL 只收 http(s)（审查 M5）

_MAP_PAYLOAD = {"name": "urlCheck", "sequence": SEQ300, "topology": "circular",
                "features": [], "source": "paste"}


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", " JavaScript:fetch('//x/'+document.cookie)", "data:text/html,<b>",
    "vbscript:msgbox", "file:///etc/passwd", "//evil.example/x", "ftp://x/y", "http://",
    "https://exa mple.com",
])
def test_save_map_as_vector_rejects_non_http_url(client, vectors_dir, bad):
    c, _ = client
    r = c.post("/api/vectors/import/from-map", json=dict(_MAP_PAYLOAD, url=bad),
               headers=_admin())
    assert r.status_code == 422, r.text
    assert list(vectors_dir.iterdir()) == []


def test_save_map_as_vector_accepts_http_url(client, vectors_dir):
    c, _ = client
    for i, ok in enumerate(("https://www.ncbi.nlm.nih.gov/nuccore/L09137", "HTTP://example.org/a?b=1", "")):
        r = c.post("/api/vectors/import/from-map",
                   json=dict(_MAP_PAYLOAD, name=f"urlOk{i}", url=f"  {ok}  "), headers=_admin())
        assert r.status_code == 200, r.text
        assert r.json()["vector"]["url"] == ok.strip()
        assert r.json()["vector"]["provenance"].get("url", "") == ok.strip()


def test_provenance_url_sanitized_on_read(client, vectors_dir):
    """手改 YAML / 旧版本写入的 javascript: 链接：读取时丢弃，不下发给前端"""
    (vectors_dir / "evil.yaml").write_text(
        "id: evil_vec\nname: evil\nsequence: ACGTACGTACGT\n"
        "data_provenance:\n  source: x\n  url: \"javascript:alert(document.cookie)\"\n"
        "  map_url: \"data:text/html,x\"\n  doc_url: \"https://example.org/doc\"\n"
        "features:\n- name: f\n  type: CDS\n  start: 1\n  end: 3\n", encoding="utf-8")
    v = _load_vector(vectors_dir, "evil_vec")
    assert v is not None and v.provenance == {"source": "x", "doc_url": "https://example.org/doc"}
    from app.design_service import invalidate_vector_library_cache
    invalidate_vector_library_cache()
    c, _ = client
    d = c.get("/api/vectors/evil_vec")
    assert d.status_code == 200, d.text
    assert "url" not in d.json()["data_provenance"]
    assert "javascript" not in d.text


def test_save_record_drops_non_http_urls(tmp_path):
    from app.vector_import import provenance_block, record_from_parts, save_record
    rec = record_from_parts(name="direct", sequence=SEQ300)
    res = save_record(rec, source="x", vectors_dir=str(tmp_path), url="javascript:alert(1)",
                      provenance=provenance_block("t", url="javascript:alert(1)",
                                                  page_url="https://ok.example/p"))
    assert res["url"] == "" and "url" not in res["provenance"]
    assert res["provenance"]["page_url"] == "https://ok.example/p"
    text = next(tmp_path.glob("*.yaml")).read_text(encoding="utf-8")
    assert "javascript" not in text


# ================================================================ 低优先级修复（审查 L1–L7）

@pytest.mark.parametrize("raw,expected", [
    ("polyA_signal", "regulatory"), ("polya_signal", "regulatory"), ("POLYA_SIGNAL", "regulatory"),
    ("rep_origin", "origin"), ("Rep_Origin", "origin"), ("mcs", "multiple_cloning_site"),
])
def test_type_aliases_case_insensitive(raw, expected):
    """L1：别名表键大写时 .lower() 查表永远命不中（polyA_signal 落成 other）"""
    from core.vector_library import VectorLibrary
    v = VectorLibrary()._parse_vector_dict({
        "id": "t", "name": "t", "sequence": "ACGT" * 10,
        "features": [{"name": "f", "type": raw, "start": 1, "end": 4}]})
    assert v.elements[0].element_type.value == expected


def _rec(name="v", seq=SEQ300):
    from app.vector_import import record_from_parts
    return record_from_parts(name=name, sequence=seq)


def test_save_record_refuses_to_overwrite_other_vector(tmp_path):
    """L2：文件名撞车（不同 ID 归一到同一文件）→ 409，原文件保持不变"""
    from app.vector_import import VectorIdConflict, save_record
    (tmp_path / "snapgene_A_B.yaml").write_text(
        "id: someone_else\nname: x\nsequence: ACGT\nfeatures: []\n", encoding="utf-8")
    with pytest.raises(VectorIdConflict) as ei:
        save_record(_rec(), source="snapgene", vectors_dir=str(tmp_path), vector_id="snapgene_A B")
    assert ei.value.status_code == 409 and "someone_else" in ei.value.detail
    assert "someone_else" in (tmp_path / "snapgene_A_B.yaml").read_text(encoding="utf-8")

    # 读不出 id 的文件同样不覆盖
    (tmp_path / "x_bad.yaml").write_text("{ not yaml", encoding="utf-8")
    with pytest.raises(VectorIdConflict):
        save_record(_rec(), source="x", vectors_dir=str(tmp_path), vector_id="x_bad")


def test_save_record_same_id_overwrite_is_reported(tmp_path):
    """同一 ID 再次导入：允许覆盖（更新），但回执与告警写明 replaced"""
    from app.vector_import import save_record
    first = save_record(_rec(), source="snapgene", vectors_dir=str(tmp_path),
                        vector_id="snapgene_SARS Genome")
    assert first["id"] == "snapgene_SARS_Genome"          # ID 与文件名同一套归一
    assert first["replaced"] is False
    again = save_record(_rec(seq=SEQ300[:200]), source="snapgene", vectors_dir=str(tmp_path),
                        vector_id="snapgene_SARS_Genome")
    assert again["replaced"] is True and any("覆盖" in w for w in again["warnings"])
    v = _load_vector(tmp_path, "snapgene_SARS_Genome")
    assert v is not None and len(v.sequence) == 200


def test_from_map_conflict_returns_409(client, vectors_dir):
    (vectors_dir / "paste_clash.yaml").write_text(
        "id: other\nname: o\nsequence: ACGT\nfeatures: []\n", encoding="utf-8")
    c, _ = client
    r = c.post("/api/vectors/import/from-map", json=dict(_MAP_PAYLOAD, name="clash"),
               headers=_admin())
    assert r.status_code == 409 and "other" in r.json()["detail"]


def test_ncbi_and_batch_import_paths_write_topology(tmp_path):
    """L3：NCBI / 批量 GenBank 导入写出 LOCUS 行的真实拓扑（此前线性记录被画成环状）"""
    from core.external_vector_importer import (GenBankImporter, NCBIClient,
                                               VectorLibraryManager, locus_topology)
    lin, circ = _gbk(topology="linear", name="LINFRAG").decode(), _gbk(name="CIRCP").decode()
    assert locus_topology(lin) == "linear" and locus_topology(circ) == "circular"
    assert locus_topology("no locus here") == "circular"

    manager = VectorLibraryManager()
    v1 = NCBIClient()._parse_genbank_response(lin, "AB000001")
    v2 = GenBankImporter()._parse_genbank(circ)
    assert v1.topology == "linear" and v2.topology == "circular"
    manager.vectors = {v1.id: v1, v2.id: v2}
    assert manager.export_to_yaml(str(tmp_path)) == 2
    assert _load_vector(tmp_path, "ncbi_AB000001").topology == "linear"
    assert _load_vector(tmp_path, "genbank_CIRCP").topology == "circular"


def test_addgene_local_index_cached_until_file_changes(tmp_path, monkeypatch):
    """L4：17 万条索引不再每次请求整读，按 mtime/大小失效"""
    import os
    monkeypatch.setattr(src, "_addgene_index_cache", {})
    reads = []
    real = src._read_addgene_index

    def _counting(path):
        reads.append(path)
        return real(path)
    monkeypatch.setattr(src, "_read_addgene_index", _counting)

    idx = tmp_path / "addgeneProcessed.json"
    idx.write_text(json.dumps([{"id": 52961, "name": "lentiCRISPR v2"}, "junk"]), encoding="utf-8")
    p = str(idx)
    assert [e["id"] for e in src.addgene_local_index("lenti", index_path=p)] == [52961]
    assert [e["id"] for e in src.addgene_local_index("52961", index_path=p)] == [52961]
    assert len(reads) == 1

    idx.write_text(json.dumps([{"id": 1, "name": "pNEW lenti"}]), encoding="utf-8")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert [e["id"] for e in src.addgene_local_index("lenti", index_path=p)] == [1]
    assert len(reads) == 2

    idx.write_text("{ broken", encoding="utf-8")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000_000))
    assert src.addgene_local_index("lenti", index_path=p) == []


def test_upload_enforces_length_and_hides_server_path(client, vectors_dir, monkeypatch):
    """L5：上传解析按 MAP_MAX_LENGTH 限长；回执不暴露服务器绝对路径"""
    from app.routes import vector_routes
    c, _ = client
    monkeypatch.setattr(vector_routes, "MAP_MAX_LENGTH", 100)
    r = c.post("/api/vectors/import/upload",
               files={"file": ("big.fasta", f">big\n{SEQ300}\n".encode(), "text/plain")},
               headers=_admin())
    assert r.status_code == 413, r.text
    assert list(vectors_dir.iterdir()) == []

    monkeypatch.setattr(vector_routes, "MAP_MAX_LENGTH", 10_000)
    ok = c.post("/api/vectors/import/upload",
                files={"file": ("ok.fasta", f">ok\n{SEQ300}\n".encode(), "text/plain")},
                headers=_admin())
    assert ok.status_code == 200, ok.text
    assert str(vectors_dir) not in ok.text and "path" not in ok.json()["vector"]
    # 其他落库端点（另存为载体）同样不带路径
    fm = c.post("/api/vectors/import/from-map", json=dict(_MAP_PAYLOAD, name="noPath"),
                headers=_admin())
    assert fm.status_code == 200 and "path" not in fm.json()["vector"]
    assert str(vectors_dir) not in fm.text


def test_upload_unexpected_errors_are_generic(client, vectors_dir, monkeypatch):
    import core.plasmid_map.parsers as parsers
    from app import vector_import
    c, _ = client
    real_parse = parsers.parse_file

    def _parse_boom(*a, **k):
        raise RuntimeError("secret internal /srv/path")
    monkeypatch.setattr(parsers, "parse_file", _parse_boom)
    r = c.post("/api/vectors/import/upload",
               files={"file": ("x.fasta", f">x\n{SEQ300}\n".encode(), "text/plain")},
               headers=_admin())
    assert r.status_code == 400 and "secret" not in r.text
    monkeypatch.setattr(parsers, "parse_file", real_parse)

    def _save_boom(*a, **k):
        raise OSError(f"disk full at {vectors_dir}")
    monkeypatch.setattr(vector_import, "save_record", _save_boom)
    r2 = c.post("/api/vectors/import/upload",
                files={"file": ("x.fasta", f">x\n{SEQ300}\n".encode(), "text/plain")},
                headers=_admin())
    assert r2.status_code == 500 and str(vectors_dir) not in r2.text


def test_errors_do_not_echo_upstream_bytes():
    """L6：非 .dna 响应（如登录页 HTML）不回显进错误详情"""
    page = b"<html><body>Login required; csrftoken=abc123</body></html>"
    with pytest.raises(MapParseError) as ei:
        src.assert_snapgene_dna(page, "x/y")
    assert ei.value.status == 415
    assert "csrftoken" not in str(ei.value) and "<html" not in str(ei.value)


def test_sources_payload_strips_base_userinfo(client, monkeypatch):
    monkeypatch.setattr(src, "ENSEMBL_BASE", "https://user:s3cret@proxy.example:8443/ensembl")
    monkeypatch.setattr(src, "UNIPROT_BASE", "https://rest.uniprot.org")
    c, _ = client
    body = c.get("/api/map/import/sources")
    assert body.status_code == 200 and "s3cret" not in body.text and "user:" not in body.text
    ids = {s["id"]: s for s in body.json()["sources"]}
    assert ids["ensembl"]["url"] == "https://proxy.example:8443/ensembl"
    assert ids["uniprot"]["url"] == "https://rest.uniprot.org"
