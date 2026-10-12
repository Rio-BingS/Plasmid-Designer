"""core.plasmid_map 解析与类型归一：GenBank / FASTA / SnapGene .dna / 粘贴文本"""

import struct

import pytest

from core.plasmid_map import MapParseError, parse_file, parse_text
from core.plasmid_map.types import normalize_color, normalize_type
from core.seq_utils import revcomp


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


# ==================== 解析 ====================

def test_genbank_join_wrap_colors_and_types():
    feats = (
        "     CDS             join(281..300,1..40)\n"
        "                     /label=\"wrapCDS\"\n"
        "                     /ApEinfo_fwdcolor=\"#FF0000\"\n"
        "     regulatory      complement(50..80)\n"
        "                     /regulatory_class=\"promoter\"\n"
        "                     /label=\"Pfoo\"\n"
        "                     /note=\"color: #00ff00\"\n"
        "     CDS             100..180\n"
        "                     /gene=\"bla\"\n"
        "                     /product=\"beta-lactamase\"\n"
        "     gene            100..180\n"
        "                     /gene=\"bla\"\n"
        "     misc_feature    join(190..200,210..220)\n"
        "                     /label=\"split\"\n"
        "     primer_bind     complement(230..249)\n"
        "                     /label=\"rev1\"\n"
    )
    rec = parse_file("p.gb", _gb(SEQ300, feats).encode())
    assert rec.source_format == "genbank" and rec.topology == "circular"
    by = {f["name"]: f for f in rec.features}
    assert set(by) == {"wrapCDS", "Pfoo", "bla", "split"}  # gene 与同跨度 CDS 去重
    w = by["wrapCDS"]
    assert (w["start"], w["end"], w["strand"]) == (281, 40, 1)
    assert w["segments"] == [{"start": 281, "end": 300}, {"start": 1, "end": 40}]
    assert w["color"] == "#ff0000" and w["type"] == "cds"
    p = by["Pfoo"]
    assert (p["type"], p["strand"], p["color"]) == ("promoter", -1, "#00ff00")
    assert "notes" not in p  # 颜色 note 不是描述
    assert by["bla"]["type"] == "marker"
    s = by["split"]
    assert (s["start"], s["end"]) == (190, 220) and len(s["segments"]) == 2
    assert rec.primers == [{"name": "rev1", "start": 230, "end": 249, "strand": -1,
                            "sequence": revcomp(SEQ300[229:249])}]


def test_genbank_linear_topology_and_multi_record_warning():
    text = _gb(SEQ300, "", topology="linear") + _gb(SEQ300[:100], "", name="SECOND")
    rec = parse_text(text)
    assert rec.topology == "linear" and len(rec.sequence) == 300
    assert any("2 条记录" in w for w in rec.warnings)


def test_fasta_and_raw_text():
    rec = parse_file("x.fasta", b">myseq circular\nACGTACGTAC\nGGGG\n")
    assert (rec.name, rec.sequence, rec.topology) == ("myseq", "ACGTACGTACGGGG", "circular")
    raw = parse_text("  1 acgu ACGT\n 61 nnnn ", name="pasted", topology="linear")
    assert (raw.sequence, raw.source_format, raw.topology, raw.name) == \
        ("ACGTACGTNNNN", "raw", "linear", "pasted")
    assert parse_text("ACGT").warnings  # 未标拓扑 → 默认环状并告警


def test_parse_errors_have_statuses():
    with pytest.raises(MapParseError) as e:
        parse_text("ACGT-XYZ")
    assert e.value.status == 422
    with pytest.raises(MapParseError) as e:
        parse_text("A" * 101, max_length=100)
    assert e.value.status == 413
    with pytest.raises(MapParseError) as e:
        parse_file("x.bin", b"\x00\x01\x02 not a sequence file")
    assert e.value.status == 415


def _snapgene_bytes(seq: str, circular: bool = True) -> bytes:
    def packet(t, data):
        return struct.pack(">BI", t, len(data)) + data
    cookie = packet(0x09, struct.pack(">8sHHH", b"SnapGene", 1, 15, 19))
    dna = packet(0x00, bytes([1 if circular else 0]) + seq.encode())
    xml = (
        '<?xml version="1.0"?><Features nextValidID="2">'
        '<Feature recentID="0" name="myCDS" directionality="1" type="CDS">'
        '<Segment range="4-33" color="#993366" type="standard"/>'
        '<Q name="note"><V text="a test CDS"/></Q></Feature>'
        '<Feature recentID="1" name="P1" directionality="2" type="promoter">'
        '<Segment range="290-20" color="#ffffff"/></Feature>'
        '</Features>'
    ).encode()
    return cookie + dna + packet(0x0A, xml)


def test_snapgene_dna_features_colors_wrap():
    rec = parse_file("plasmid.dna", _snapgene_bytes(SEQ300))
    assert rec.source_format == "snapgene" and rec.topology == "circular"
    assert rec.name == "plasmid" and rec.sequence == SEQ300
    by = {f["name"]: f for f in rec.features}
    c = by["myCDS"]
    assert (c["start"], c["end"], c["strand"], c["color"], c["notes"]) == \
        (4, 33, 1, "#993366", "a test CDS")
    p = by["P1"]
    assert (p["start"], p["end"], p["strand"], p["type"]) == (290, 20, -1, "promoter")


@pytest.mark.parametrize("raw,name,extra,expected", [
    ("CDS", "KanR", "", "marker"),
    ("CDS", "lacI", "lac repressor", "cds"),
    ("CDS", "6xHis", "", "tag"),
    ("rep_origin", "ori", "", "rep_origin"),
    ("misc_feature", "SV40 poly(A) signal", "", "polyA_signal"),
    ("misc_feature", "CMV enhancer", "", "enhancer"),
    ("misc_feature", "5' LTR", "", "ltr"),
    ("misc_feature", "MCS", "", "mcs"),
    ("multiple_cloning_site", "x", "", "mcs"),
    ("resistance", "AmpR", "", "marker"),
    ("origin", "f1 ori", "", "rep_origin"),
    ("regulatory", "RBS", "", "rbs"),
    ("regulatory", "lac operator", "", "protein_bind"),
    ("other", "tac promoter", "", "promoter"),
    ("ncRNA", "gRNA", "", "misc_rna"),
    ("sig_peptide", "ss", "", "signal_peptide"),
    ("CDS", "TetR", "tet repressor", "cds"),  # SnapGene：TetR 是阻遏蛋白不是抗性
    ("weird_key", "thing", "", "misc_feature"),
])
def test_normalize_type(raw, name, extra, expected):
    assert normalize_type(raw, name, None, extra) == expected


def test_normalize_color():
    assert normalize_color("#AbC") == "#aabbcc"
    assert normalize_color("color: #12ab34") == "#12ab34"
    assert normalize_color("red") is None


def test_mixed_strand_join_and_order_warn():
    """混合链 join 与 order() 的语义契约里存不下：解析时告警，不再静默丢失"""
    feats = (
        "     misc_feature    join(10..20,complement(30..40))\n"
        "                     /label=\"mixed\"\n"
        "     misc_feature    order(50..60,70..80)\n"
        "                     /label=\"ord\"\n"
    )
    rec = parse_text(_gb(SEQ300, feats))
    by = {f["name"]: f for f in rec.features}
    assert by["mixed"]["strand"] == 0
    w = " ".join(rec.warnings)
    assert "跨正负两条链" in w and "order()" in w
