"""批量测序：文件名对不上时按图谱内部 CDS/gene 名称反查质粒名称

命名规范允许 .DNA 只写编号（10001.DNA），此时文件名里没有质粒名称。
文件内的 CDS 通常带基因名（/gene、/label、/product），先按 CDS 名称
反查信息表的质粒名称，候选唯一才认领；多个 CDS 指向不同质粒不猜。
"""

from app.sequencing_batch import rescue_references_by_features
from core.sanger.batch import (
    feature_alias_names,
    match_reference_aliases,
)

SEQ = ("ATGGCTGCT" * 33).ljust(300, "A")[:300]


def _genbank(cds_names, seq=SEQ) -> bytes:
    """用 Biopython 写一段带 CDS /gene 名称的合法 GenBank"""
    import io

    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord

    rec = SeqRecord(Seq(seq), id="probe", name="probe", description="probe")
    rec.annotations.update({"topology": "circular", "molecule_type": "DNA"})
    rec.features = [
        SeqFeature(FeatureLocation(0, len(seq)), type="CDS",
                   qualifiers={"gene": [name]})
        for name in cds_names
    ]
    buf = io.StringIO()
    SeqIO.write(rec, buf, "genbank")
    return buf.getvalue().encode()


def _ref(name: str, cds_names) -> dict:
    return {"name": name, "ext": ".gb", "stem": name.rsplit(".", 1)[0].lower(),
            "bytes": _genbank(cds_names)}


def _groups(*plasmids):
    return [{"plasmid": p, "clone": "", "reference": None, "reads": []}
            for p in plasmids]


def _rows(*plasmids):
    return [{"name": p, "clone": "", "primers": []} for p in plasmids]


def test_feature_alias_names_keeps_only_cds_and_gene():
    feats = [
        {"type": "CDS", "name": "PNPM5"},
        {"type": "gene", "name": "pnp"},
        {"type": "promoter", "name": "T7"},
        {"type": "CDS", "name": "pnpm5"},   # 归一化后重复
        {"type": "CDS", "name": "  "},
        {"type": "rep_origin", "name": "ori"},
    ]
    assert feature_alias_names(feats) == ["PNPM5", "pnp"]


def test_feature_alias_names_flat_empty():
    assert feature_alias_names([]) == []
    assert feature_alias_names(None) == []


def test_match_reference_aliases_unique_hit():
    plasmid, how = match_reference_aliases(["PNPM5"], {"10001 PNPM5"})
    assert plasmid == "10001 PNPM5"
    assert "CDS 名称" in how


def test_match_reference_aliases_conflict_refused():
    plasmid, how = match_reference_aliases(
        ["PNPM5", "ABC"], {"10001 PNPM5", "10002 ABC"})
    assert plasmid is None
    assert "无法唯一确定" in how


def test_rescue_attaches_reference_by_cds_name():
    """文件名只有编号时，用图谱内 CDS 名称认领质粒"""
    groups = _groups("10001 PNPM5")
    ref = _ref("10001.gb", ["PNPM5"])
    unmatched = [{"file": ref, "reason": "文件名不匹配任何质粒名称"}]
    left = rescue_references_by_features(groups, unmatched, _rows("10001 PNPM5"))
    assert left == []
    assert groups[0]["reference"]["file"] is ref
    assert "CDS 名称" in groups[0]["reference"]["how"]


def test_rescue_leaves_ambiguous_file_unmatched():
    groups = _groups("10001 PNPM5", "10002 ABC")
    ref = _ref("10001.gb", ["PNPM5", "ABC"])
    reason = "文件名同时匹配多个质粒名称（10001 PNPM5、10002 ABC），无法唯一确定"
    unmatched = [{"file": ref, "reason": reason}]
    left = rescue_references_by_features(
        groups, unmatched, _rows("10001 PNPM5", "10002 ABC"))
    assert len(left) == 1
    # 改名也定不下来时保留原说明（比「CDS 名称也不匹配」更具体）
    assert left[0]["reason"] == reason
    assert all(g["reference"] is None for g in groups)


def test_rescue_skips_reads_and_missing_names():
    groups = _groups("10001 PNPM5")
    read = {"name": "10001-F1.ab1", "ext": ".ab1", "stem": "10001-f1", "bytes": b"x"}
    unmatched = [{"file": read, "reason": "未出现在任何质粒的引物列中"}]
    left = rescue_references_by_features(groups, unmatched, _rows("10001 PNPM5"))
    assert left == unmatched
    assert groups[0]["reference"] is None


def test_rescue_keeps_reason_when_plasmid_already_has_reference():
    """同一质粒已有图谱时，第二张图谱仍保持未匹配（原说明更准确）"""
    groups = _groups("10001 PNPM5")
    first = _ref("10001 PNPM5.gb", ["PNPM5"])
    groups[0]["reference"] = {"file": first, "how": "文件名与质粒名称精确匹配"}
    second = _ref("extra.gb", ["PNPM5"])
    unmatched = [{"file": second, "reason": "文件名不匹配任何质粒名称"}]
    left = rescue_references_by_features(groups, unmatched, _rows("10001 PNPM5"))
    assert len(left) == 1
    assert groups[0]["reference"]["file"] is first


def test_rescue_without_excel_does_nothing():
    """无信息表模式没有可反查的质粒名称，保持原样"""
    groups = _groups("10001 PNPM5")
    ref = _ref("10001.gb", ["PNPM5"])
    unmatched = [{"file": ref, "reason": "文件名不匹配任何质粒名称"}]
    assert rescue_references_by_features(groups, unmatched, None) == unmatched
    assert groups[0]["reference"] is None
