"""测序参考序列文件解析测试（core/sanger/reference_parser.py + /api/sequencing/analyze）"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from core.sanger.reference_parser import parse_reference, ReferenceParseError  # noqa: E402
from abif_utils import make_ab1  # noqa: E402

REF_SEQ = ("acgt" * 30).upper()  # 120 bp


def _origin_block(seq: str) -> str:
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        groups = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
        lines.append(f"{i + 1:>9} {groups}")
    return "\n".join(lines)


GENBANK_TEXT = f"""LOCUS       TestRef          120 bp    DNA     circular SYN 01-JAN-2026
DEFINITION  test construct.
ACCESSION   testref
FEATURES             Location/Qualifiers
     source          1..{len(REF_SEQ)}
                     /organism="synthetic construct"
     CDS             complement(21..80)
                     /label="GFP"
                     /note="green fluorescent protein"
     promoter        5..20
                     /label="T7 prom"
ORIGIN
{_origin_block(REF_SEQ)}
//
"""


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ==================== 解析器单测 ====================

def test_parse_genbank_extracts_features():
    seq, features = parse_reference("construct.gb", GENBANK_TEXT.encode())
    assert seq == REF_SEQ
    names = {f["name"]: f for f in features}
    assert "source" not in {f["name"] for f in features}
    gfp = names["GFP"]
    assert (gfp["start"], gfp["end"], gfp["strand"]) == (21, 80, "-")
    assert gfp["description"].startswith("green fluorescent")
    t7 = names["T7 prom"]
    assert (t7["start"], t7["end"], t7["strand"]) == (5, 20, "+")


def test_parse_fasta_sequence_only():
    fasta = f">my_construct some desc\n{REF_SEQ[:60]}\n{REF_SEQ[60:]}\n"
    seq, features = parse_reference("ref.fasta", fasta.encode())
    assert seq == REF_SEQ
    assert features == []


def test_parse_fasta_multi_record_rejected():
    fasta = f">a\n{REF_SEQ[:60]}\n>b\n{REF_SEQ[:60]}\n"
    with pytest.raises(ReferenceParseError, match="2 条序列"):
        parse_reference("ref.fa", fasta.encode())


def test_parse_unsupported_extension():
    with pytest.raises(ReferenceParseError, match="无法识别的参考文件类型"):
        parse_reference("notes.txt", b"ACGT")


def test_parse_corrupted_genbank_raises():
    with pytest.raises(ReferenceParseError):
        parse_reference("bad.gb", b"this is not genbank at all")


def test_parse_snapgene_requires_reader():
    """扩展名 .dna：装有 snapgene-reader 时走真解析，未装时报可读错误"""
    try:
        import snapgene_reader  # noqa: F401
        pytest.skip("snapgene-reader 已安装，无法触发 ImportError 分支")
    except ImportError:
        pass
    with pytest.raises(ReferenceParseError, match="snapgene-reader"):
        parse_reference("plasmid.dna", b"\x00\x01fake")


def test_parse_snapgene_coordinates_are_one_based(monkeypatch):
    """snapgene_reader 返回 0-based 半开区间（start 已减 1，end 原样保留）：
    解析器必须把 start 加回 1，否则所有特征整体左移 1bp、长度多 1，
    CDS 会误报"长度不是 3 的倍数"且翻译阅读框整体错位（MX.dna 实测案例）"""
    import snapgene_reader

    fake = {
        "seq": REF_SEQ,
        "features": [
            # 模拟库输出：真实 1-based 区间 21..80 → 库返回 start=20, end=80
            {"start": 20, "end": 80, "strand": "1", "type": "CDS", "name": "GFP",
             "segments": [{"@range": "21-80"}]},
        ],
    }
    monkeypatch.setattr(snapgene_reader, "snapgene_file_to_dict", lambda path: fake)
    seq, features = parse_reference("plasmid.dna", b"SNAPGENE-BYTES")
    gfp = features[0]
    assert (gfp["start"], gfp["end"], gfp["strand"]) == (21, 80, "+")
    assert gfp["end"] - gfp["start"] + 1 == 60  # 1-based 闭区间长度


# ==================== 通用分析端点集成 ====================

def test_analyze_upload_endpoint_full_flow(client):
    mutated = list(REF_SEQ)
    mutated[50] = "A" if mutated[50] != "A" else "G"
    blob = make_ab1("".join(mutated), [40] * len(REF_SEQ))

    resp = client.post(
        "/api/sequencing/analyze",
        files=[
            ("reference", ("my_construct.gb", GENBANK_TEXT.encode(), "application/octet-stream")),
            ("reads", ("r1.ab1", blob, "application/octet-stream")),
        ],
        data={"min_q": "20", "allow_decompose": "false"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sample_name"] == "my_construct"
    assert data["reference_length"] == len(REF_SEQ)
    assert data["reads"][0]["identity"] > 0.99
    assert len(data["variants"]) == 1
    # 突变落在 GFP 特征内 → 注释来自参考文件的特征表
    assert any(f["name"] == "GFP" for f in data["variants"][0]["features"])
    assert data["consensus"]["sequence"]


def test_analyze_upload_rejects_bad_reference(client):
    blob = make_ab1(REF_SEQ, [40] * len(REF_SEQ))
    resp = client.post(
        "/api/sequencing/analyze",
        files=[
            ("reference", ("notes.txt", b"not a sequence file", "text/plain")),
            ("reads", ("r1.ab1", blob, "application/octet-stream")),
        ],
    )
    assert resp.status_code == 400
    assert "无法识别" in resp.json()["detail"]


def test_vector_genbank_export_round_trips(client):
    """载体 GenBank 导出必须是 Biopython 可解析的（测序页深链参考回传依赖）"""
    vectors = client.get("/api/vectors").json()
    target = vectors[0]
    resp = client.get(f"/api/vectors/{target['id']}/sequence?format=genbank")
    assert resp.status_code == 200
    seq, features = parse_reference("vec.gb", resp.text.encode())
    assert len(seq) > 500
    # 导出的特征坐标必须落回序列内
    assert features, "导出的 GenBank 应带特征表"
    assert all(f["end"] <= len(seq) for f in features)


# ==================== 参考拓扑（环状/线性）====================

def _gb(seq: str, topology: str) -> str:
    return (f"LOCUS       TopoRef          {len(seq)} bp    DNA     {topology} SYN 01-JAN-2026\n"
            "DEFINITION  topology test.\nACCESSION   toporef\n"
            "FEATURES             Location/Qualifiers\n"
            f"     source          1..{len(seq)}\n"
            "                     /organism=\"synthetic construct\"\n"
            f"ORIGIN\n{_origin_block(seq)}\n//\n")


def test_parse_reference_topology_flags():
    from core.sanger.reference_parser import parse_reference_topology
    assert parse_reference_topology("a.gb", _gb(REF_SEQ, "circular").encode())[2] is True
    assert parse_reference_topology("a.gb", _gb(REF_SEQ, "linear").encode())[2] is False
    assert parse_reference_topology("a.fasta", f">r\n{REF_SEQ}\n".encode())[2] is None
    # 旧接口保持二元组
    seq, feats = parse_reference("a.gb", _gb(REF_SEQ, "circular").encode())
    assert seq == REF_SEQ


def test_parse_snapgene_topology(monkeypatch):
    import snapgene_reader
    from core.sanger.reference_parser import parse_reference_topology
    fake = {"seq": REF_SEQ, "features": [], "dna": {"topology": "circular"}}
    monkeypatch.setattr(snapgene_reader, "snapgene_file_to_dict", lambda path: fake)
    seq, _f, circ = parse_reference_topology("p.dna", b"SNAPGENE-BYTES")
    assert seq == REF_SEQ and circ is True


def _wrap_case():
    import random
    rng = random.Random(17)
    ref = "".join(rng.choice("ACGT") for _ in range(900))
    read = list(ref[750:] + ref[:200])          # 跨原点 350bp
    read[100] = "A" if read[100] != "A" else "C"   # 参考 851
    read[250] = "A" if read[250] != "A" else "C"   # 参考 101
    return ref, "".join(read)


def test_analyze_upload_circular_genbank_wraps_origin(client):
    """环状 GenBank：跨原点 read 的摘要带折回区段，覆盖拆两段，两处差异都在"""
    ref, read = _wrap_case()
    resp = client.post(
        "/api/sequencing/analyze",
        files=[("reference", ("circ.gb", _gb(ref, "circular").encode(), "application/octet-stream")),
               ("reads", ("w.ab1", make_ab1(read, [40] * len(read)), "application/octet-stream"))],
        data={"allow_decompose": "false"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["circular"] is True
    r0 = d["reads"][0]
    assert r0["wraps_origin"] is True
    assert (r0["ref_start"], r0["ref_end"]) == (751, 1100)
    assert r0["ref_segments"] == [[751, 900], [1, 200]]
    assert d["coverage_ranges"] == [[1, 200], [751, 900]]
    assert sorted(v["ref_pos"] for v in d["variants"]) == [101, 851]


def test_analyze_upload_linear_genbank_not_wrapped(client):
    """GenBank 标 linear：按线性比对，read 摘要不带跨原点字段（向后兼容）"""
    ref, read = _wrap_case()
    resp = client.post(
        "/api/sequencing/analyze",
        files=[("reference", ("lin.gb", _gb(ref, "linear").encode(), "application/octet-stream")),
               ("reads", ("w.ab1", make_ab1(read, [40] * len(read)), "application/octet-stream"))],
        data={"allow_decompose": "false"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["circular"] is False
    r0 = d["reads"][0]
    assert "wraps_origin" not in r0 and "ref_segments" not in r0
    assert (r0["ref_start"], r0["ref_end"]) == (1, 200)
    assert d["coverage_ranges"] == [[1, 200]]
    assert [v["ref_pos"] for v in d["variants"]] == [101]
