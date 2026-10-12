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
    monkeypatch.setattr(src, "_MIN_INTERVAL", {})  # 免等待
    for k in ("ONLINE_IMPORT_ENABLED", "ONLINE_TIMEOUT", "SNAPGENE_BASE", "ENSEMBL_BASE"):
        monkeypatch.delenv(k, raising=False)
    cache.backend.clear_pattern("online:*")
    yield
    cache.backend.clear_pattern("online:*")


# ---------------------------------------------------------------- 参数校验

@pytest.mark.parametrize("fn,raw,expected", [
    (src.validate_set_id, "yeast_plasmids", "yeast_plasmids"),
    (src.validate_set_id, "pet_and_duet_vectors_(novagen)", "pet_and_duet_vectors_(novagen)"),
    (src.validate_plasmid_id, " pYES2 ", "pYES2"),
    (src.validate_plasmid_id, "SARS Genome", "SARS Genome"),
    (src.validate_ensembl_id, "ENST00000288602", "ENST00000288602"),
    (src.validate_addgene_id, "#52961", "52961"),
])
def test_valid_identifiers(fn, raw, expected):
    assert fn(raw) == expected


@pytest.mark.parametrize("raw", ["", "../etc/passwd", "a/b", "x?y=1", "A" * 130, "%2e%2e"])
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
    meta = {"seqObjectId": "244694", "mapPngUrl": "https://x/a.png",
            "genbankUrl": "https://x/a.gbk", "snapgeneUrl": "https://x/a.dna"}
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
    from app.config import settings
    d = tmp_path / "vectors"
    d.mkdir()
    monkeypatch.setattr(settings, "VECTORS_DIR", str(d))
    return d


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
    first = c.get("/api/vectors/import/snapgene/sets")
    second = c.get("/api/vectors/import/snapgene/sets")
    assert first.status_code == 200 and first.json()["count"] == 1
    assert second.json() == first.json() and len(calls) == 1

    idx = c.get("/api/vectors/import/snapgene/sets/yeast_plasmids")
    assert idx.status_code == 200, idx.text
    assert idx.json()["count"] == 1 and idx.json()["plasmids"][0]["plasmid_id"] == "pYES2"
    assert c.get("/api/vectors/import/snapgene/sets/..%2Fetc").status_code in (404, 422)


def test_snapgene_preview_and_import(client, vectors_dir, monkeypatch):
    record = src.snapgene_record(_snapgene_bytes(), name="pYES2")

    async def _fetch(set_id, plasmid_id):
        return record, "https://www.snapgene.com/local/x"
    monkeypatch.setattr(osr, "_snapgene_fetch", _fetch)

    c, _ = client
    prev = c.get("/api/vectors/import/snapgene/preview",
                 params={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"})
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
    r2 = c.get("/api/vectors/import/addgene/244694", params={"try_sequence": 1})
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
    assert classify_endpoint("GET", "/api/vectors/import/snapgene/sets") == "default"
    assert classify_endpoint("GET", "/api/vectors/import/sources") == "default"


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
    a = c.get("/api/vectors/import/snapgene/sets")
    b = c.get("/api/map/import/snapgene/sets")
    assert a.status_code == b.status_code == 200
    assert b.json() == a.json() and len(calls) == 1

    ia = c.get("/api/vectors/import/snapgene/sets/yeast_plasmids")
    ib = c.get("/api/map/import/snapgene/sets/yeast_plasmids")
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
               json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["schema"] == "plasmid-map/1" and d["name"] == "pYES2"
    assert d["length"] == 300 and d["topology"] == "circular"
    assert any(f["name"] == "myCDS" for f in d["features"])
    from app.routes.models import MAP_MAX_LENGTH
    assert calls == [("yeast_plasmids", "pYES2", MAP_MAX_LENGTH)]

    r2 = c.post("/api/map/import/snapgene",
                json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2"})
    assert r2.json() == d and len(calls) == 1        # MapDocument 缓存命中，不重复下载
    # 换酶组：按选项分键 → 重新构建（但不再下载集合索引）
    r3 = c.post("/api/map/import/snapgene",
                json={"set_id": "yeast_plasmids", "plasmid_id": "pYES2",
                      "enzyme_set": "golden_gate"})
    assert r3.status_code == 200 and r3.json()["meta"]["enzyme_set"] == "golden_gate"
    assert len(calls) == 2


def test_map_import_snapgene_errors_and_gating(client, vectors_dir, monkeypatch):
    def _boom(set_id, plasmid_id, max_length=None):
        raise MapParseError("上游未找到或拒绝访问（HTTP 404）", 404)
    monkeypatch.setattr(oi, "snapgene_plasmid_record", _boom)

    c, s = client
    r = c.post("/api/map/import/snapgene", json={"set_id": "s", "plasmid_id": "p"})
    assert r.status_code == 404 and "404" in r.json()["detail"]
    assert c.post("/api/map/import/snapgene", json={}).status_code == 422

    s["anonymous_features"] = [f for f in ALL_FEATURES if f != "map"]
    assert c.get("/api/map/import/sources").status_code == 403
    assert c.post("/api/map/import/snapgene",
                  json={"set_id": "s", "plasmid_id": "p"}).status_code == 403


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
