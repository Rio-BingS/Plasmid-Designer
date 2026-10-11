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
