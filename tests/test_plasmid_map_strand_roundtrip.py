"""负链 / 跨原点特征的片段顺序：GenBank 导出 → 重新读入 → extract 与契约一致

契约：segments 按生物学顺序（负链沿负链 5'→3'，即坐标降序），
extract_feature 逐段取反向互补后拼接。导出的 GenBank 由 Biopython 读回后
SeqFeature.extract 必须等于契约序列；SnapGene .dna 解析出的段顺序同样
须是生物学顺序（v2.12.0 负链跨原点段被写/读成正向顺序，序列与翻译错误）。
"""

import io
import random
import struct

import pytest
from Bio import SeqIO

from core.plasmid_map import parse_file, parse_text
from core.plasmid_map.document import build_document
from core.plasmid_map.genbank import document_to_genbank
from core.plasmid_map.parsers import extract_feature, extract_span
from core.seq_utils import revcomp

L = 1000
_RNG = random.Random(5)
SEQ = "".join(_RNG.choice("ACGT") for _ in range(L))


def _fwd(segs):
    """正向链顺序拼接（不取互补）"""
    return "".join(extract_span(SEQ, s, e, 1) for s, e in segs)


# (名称, start, end, strand, segments 生物学顺序, 期望序列)
CASES = [
    ("plus-wrap", 900, 100, 1, None, _fwd([(900, 100)])),
    ("minus-wrap", 900, 100, -1, None, revcomp(_fwd([(900, 100)]))),
    ("plus-join", 10, 200, 1, [(10, 50), (100, 200)], _fwd([(10, 50), (100, 200)])),
    ("minus-join", 10, 200, -1, [(100, 200), (10, 50)], revcomp(_fwd([(10, 50), (100, 200)]))),
    # 内含子 900..949 被跳过，第二段跨原点
    ("plus-join-wrap", 800, 50, 1, [(800, 899), (950, 50)], _fwd([(800, 899), (950, 50)])),
    ("minus-join-wrap", 800, 50, -1, [(950, 50), (800, 899)],
     revcomp(_fwd([(800, 899), (950, 50)]))),
]


def _doc(start, end, strand, segs):
    f = {"name": "x", "type": "cds", "start": start, "end": end, "strand": strand}
    if segs:
        f["segments"] = [{"start": s, "end": e} for s, e in segs]
    return {"name": "t", "topology": "circular", "sequence": SEQ, "features": [f]}


@pytest.mark.parametrize("name,start,end,strand,segs,expected", CASES, ids=[c[0] for c in CASES])
def test_export_reparse_extract_matches_contract(name, start, end, strand, segs, expected):
    doc = _doc(start, end, strand, segs)
    assert extract_feature(SEQ, doc["features"][0]) == expected
    gb = document_to_genbank(doc)
    rec = SeqIO.read(io.StringIO(gb), "genbank")
    assert str(rec.features[0].extract(rec.seq)) == expected
    back = parse_text(gb)
    f = back.features[0]
    assert (f["start"], f["end"], f["strand"]) == (start, end, strand)
    assert extract_feature(back.sequence, f) == expected
    # 第二轮往返结果不变
    gb2 = document_to_genbank({"name": "t", "topology": "circular", "sequence": SEQ,
                               "features": [f]})
    rec2 = SeqIO.read(io.StringIO(gb2), "genbank")
    assert str(rec2.features[0].extract(rec2.seq)) == expected


def test_minus_wrap_written_in_biological_order():
    gb = document_to_genbank(_doc(900, 100, -1, None))
    assert "complement(join(900..1000,1..100))" in gb


def test_built_document_round_trip_keeps_order():
    """build_document（sanitize_features）不重排 segments"""
    doc = build_document(name="t", sequence=SEQ, topology="circular", source_format="raw",
                         features=_doc(800, 50, -1, [(950, 50), (800, 899)])["features"],
                         annotate=False)
    f = doc["features"][0]
    assert [(s["start"], s["end"]) for s in f["segments"]] == [(950, 50), (800, 899)]
    rec = SeqIO.read(io.StringIO(document_to_genbank(doc)), "genbank")
    assert str(rec.features[0].extract(rec.seq)) == revcomp(_fwd([(800, 899), (950, 50)]))


# ==================== SnapGene .dna ====================

def _snapgene(features_xml: str) -> bytes:
    def packet(t, data):
        return struct.pack(">BI", t, len(data)) + data
    cookie = packet(0x09, struct.pack(">8sHHH", b"SnapGene", 1, 15, 19))
    dna = packet(0x00, bytes([1]) + SEQ.encode())
    xml = f'<?xml version="1.0"?><Features>{features_xml}</Features>'.encode()
    return cookie + dna + packet(0x0A, xml)


SG_CASES = [
    # (名称, directionality, Segment ranges（SnapGene 按坐标升序）, 期望 start/end, 期望序列)
    ("sg-plus-wrap", 1, ["901-100"], (901, 100), _fwd([(901, 100)])),
    ("sg-minus-wrap", 2, ["901-100"], (901, 100), revcomp(_fwd([(901, 100)]))),
    ("sg-minus-join", 2, ["11-50", "101-200"], (11, 200), revcomp(_fwd([(11, 50), (101, 200)]))),
    ("sg-minus-join-wrap", 2, ["801-900", "951-50"], (801, 50),
     revcomp(_fwd([(801, 900), (951, 50)]))),
    ("sg-plus-join-wrap", 1, ["801-900", "951-50"], (801, 50), _fwd([(801, 900), (951, 50)])),
]


@pytest.mark.parametrize("name,direc,ranges,span,expected", SG_CASES, ids=[c[0] for c in SG_CASES])
def test_snapgene_segments_biological_order(name, direc, ranges, span, expected):
    segs = "".join(f'<Segment range="{r}" color="#00ff00"/>' for r in ranges)
    data = _snapgene(f'<Feature name="f" directionality="{direc}" type="CDS">{segs}</Feature>')
    rec = parse_file("t.dna", data)
    f = rec.features[0]
    assert (f["start"], f["end"]) == span
    assert extract_feature(rec.sequence, f) == expected
    # .dna → 导出 GenBank → Biopython extract 也一致
    gb = document_to_genbank({"name": "t", "topology": "circular", "sequence": SEQ,
                              "features": [f]})
    sf = SeqIO.read(io.StringIO(gb), "genbank").features[0]
    assert str(sf.extract(SeqIO.read(io.StringIO(gb), "genbank").seq)) == expected
