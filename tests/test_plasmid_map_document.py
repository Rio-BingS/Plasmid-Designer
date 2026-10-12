"""core.plasmid_map MapDocument 组装与 GenBank 导出往返"""

from core.plasmid_map import build_document, parse_file
from core.plasmid_map.document import document_from_record
from core.plasmid_map.genbank import document_to_genbank


def _gb(seq: str, features: str, topology: str = "circular", name: str = "TESTPLAS") -> str:
    lines = [f"LOCUS       {name:<16}{len(seq):>12} bp    DNA     {topology:<8} SYN 01-JAN-2026",
             "DEFINITION  test.", "FEATURES             Location/Qualifiers", features.rstrip("\n"),
             "ORIGIN"]
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60].lower()
        lines.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10)))
    lines.append("//")
    return "\n".join(lines) + "\n"


SEQ300 = ("ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA" * 5)[:300]


# ==================== GenBank 导出往返 ====================

def test_genbank_export_round_trip():
    feats = (
        "     CDS             join(281..300,1..40)\n"
        "                     /label=\"wrapCDS\"\n"
        "                     /ApEinfo_fwdcolor=\"#FF0000\"\n"
        "     promoter        complement(50..80)\n"
        "                     /label=\"Pfoo\"\n"
        "     misc_feature    join(190..200,210..220)\n"
        "                     /label=\"split\"\n"
        "     primer_bind     230..249\n"
        "                     /label=\"fwd1\"\n"
    )
    rec = parse_file("p.gb", _gb(SEQ300, feats).encode())
    doc = document_from_record(rec, annotate=False)
    doc["features"].append({"id": "x", "name": "autoT7", "type": "promoter", "start": 120,
                            "end": 138, "strand": 1, "source": "auto", "color": "#123456",
                            "auto": {"library": "common", "identity": 1.0, "coverage": 1.0,
                                     "fragment": False}})
    text = document_to_genbank(doc)
    assert "/ApEinfo_fwdcolor=\"#123456\"" in text and "auto-annotated" in text
    again = parse_file("again.gb", text.encode())
    assert again.topology == "circular" and again.sequence == SEQ300

    def key(fs):
        return sorted((f["name"], f["type"], f["start"], f["end"], f["strand"],
                       tuple((s["start"], s["end"]) for s in f.get("segments") or []),
                       f.get("color")) for f in fs)
    assert key(again.features) == key(doc["features"])
    assert [(p["name"], p["start"], p["end"]) for p in again.primers] == [("fwd1", 230, 249)]


def test_build_document_shape_and_stable_ids():
    doc = build_document(name="t", sequence=SEQ300, topology="linear", source_format="raw",
                         features=[{"name": "a", "type": "cds", "start": 10, "end": 20,
                                    "strand": 1, "source": "file", "id": "keep-me"},
                                   {"name": "b", "type": "bogus", "start": 5, "end": 9},
                                   {"name": "bad", "type": "cds", "start": 290, "end": 3}],
                         annotate=False)
    assert doc["schema"] == "plasmid-map/1" and doc["length"] == 300
    assert [f["id"] for f in doc["features"]] == ["file-1", "keep-me"]
    assert doc["features"][0]["type"] == "misc_feature"
    assert any("坐标无效" in w for w in doc["meta"]["warnings"])  # 线性上不允许跨原点
    assert set(doc) == {"schema", "name", "topology", "length", "sequence", "features",
                        "enzymes", "orfs", "primers", "meta"}


def _pet28a() -> str:
    from pathlib import Path

    import yaml
    p = Path(__file__).resolve().parent.parent / "data" / "vectors" / "pET-28a.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))["sequence"].upper()


def test_document_drops_auto_duplicates_of_existing_features():
    seq = _pet28a()
    existing = [{"name": "kanamycin resistance", "type": "marker", "start": 3995, "end": 4810,
                 "strand": 1, "source": "file"}]
    doc = build_document(name="p", sequence=seq, topology="circular", source_format="genbank",
                         features=existing, annotate=True)
    kan = [f for f in doc["features"] if f["start"] == 3995]
    assert [f["source"] for f in kan] == ["file"]
    assert any(f["source"] == "auto" and f["name"] == "lacI" for f in doc["features"])
    # 默认：文件已有特征时不自动注释
    doc2 = build_document(name="p", sequence=seq, topology="circular", source_format="genbank",
                          features=existing)
    assert [f["source"] for f in doc2["features"]] == ["file"]


# ==================== /parse 产出符合 MapDocument 约束（v2.12.0 审查 M2） ====================

def _join(n, step=3):
    return "join(" + ",".join(f"{step * i + 1}..{step * i + 1}" for i in range(n)) + ")"


def _wrap_loc(loc: str) -> str:
    out, cur = [], loc
    while cur:
        out.append(cur[:58])
        cur = cur[58:]
    return "     CDS             " + out[0] + "\n" + "".join(" " * 21 + x + "\n" for x in out[1:])


def test_parsed_document_validates_against_contract_limits():
    import random

    from app.routes.models import MapDocument

    rng = random.Random(2)
    seq = "".join(rng.choice("ACGT") for _ in range(3000))
    many_q = "".join(f"                     /q{i}=\"v{i}\"\n" for i in range(150))
    feats = (
        _wrap_loc(_join(250))                                   # 250 段 > 200 → 丢弃告警
        + "     misc_feature    100..200\n" + many_q              # 150 条限定符 → 截到 100
        + "     primer_bind     1..1500\n                     /label=\"longprimer\"\n"
        + "     primer_bind     complement(2000..2020)\n                     /label=\"p1\"\n"
    )
    doc = document_from_record(parse_file("x.gb", _gb(seq, feats).encode()), annotate=False)
    MapDocument.model_validate(doc)  # 不抛 = 能原样提交给 /analyze、/export/genbank
    warns = " ".join(doc["meta"]["warnings"])
    assert "分段超过 200" in warns and "限定符超过 100" in warns
    mf = next(f for f in doc["features"] if f["type"] == "misc_feature")
    assert len(mf["qualifiers"]) == 100
    # 过长的 primer_bind 作为特征保留，不进 primers（MapPrimer.sequence ≤ 1000）
    assert [p["name"] for p in doc["primers"]] == ["p1"]
    assert any(f["name"] == "longprimer" and f["type"] == "primer_bind" for f in doc["features"])


def test_huge_join_rejected_before_biopython():
    import time

    import pytest

    from core.plasmid_map import MapParseError

    seq = "ACGT" * 75_000
    gb = _gb(seq, _wrap_loc(_join(80_000)))
    t = time.time()
    with pytest.raises(MapParseError) as e:
        parse_file("x.gb", gb.encode())
    assert e.value.status == 422 and "分段过多" in str(e.value)
    assert time.time() - t < 1.0


def test_snapgene_huge_segment_count_rejected():
    import struct

    import pytest

    from core.plasmid_map import MapParseError

    def packet(t, data):
        return struct.pack(">BI", t, len(data)) + data
    seq = "ACGT" * 1000
    segs = "".join(f'<Segment range="{2 * i + 1}-{2 * i + 1}"/>' for i in range(1500))
    xml = f'<?xml version="1.0"?><Features><Feature name="f" type="CDS">{segs}</Feature></Features>'
    data = (packet(0x09, struct.pack(">8sHHH", b"SnapGene", 1, 15, 19))
            + packet(0x00, bytes([1]) + seq.encode()) + packet(0x0A, xml.encode()))
    with pytest.raises(MapParseError) as e:
        parse_file("x.dna", data)
    assert e.value.status == 422


def test_build_document_caps_orfs_primers_and_segments(monkeypatch):
    import core.plasmid_map.document as D

    monkeypatch.setattr(D, "MAX_ORFS", 3)
    monkeypatch.setattr(D, "MAX_PRIMERS", 2)
    seq = ("ATG" + "GCT" * 12 + "TAA") * 10
    feats = [{"name": "s", "type": "cds", "start": 1, "end": 300, "strand": 1,
              "segments": [{"start": i + 1, "end": i + 1} for i in range(D.MAX_SEGMENTS + 1)]}]
    prims = [{"name": f"p{i}", "start": 1, "end": 10, "strand": 1} for i in range(4)]
    doc = build_document(name="x", sequence=seq, topology="linear", source_format="raw",
                         features=feats, primers=prims, annotate=False, orf_min_aa=10)
    assert len(doc["orfs"]) == 3 and len(doc["primers"]) == 2 and doc["features"] == []
    w = " ".join(doc["meta"]["warnings"])
    assert "ORF 超过 3" in w and "引物超过 2" in w
