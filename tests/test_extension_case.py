"""扩展名大小写不敏感 + .DNA 文件命名规范（回归锁）

命名规范：`.DNA` 文件优先命名为「编号 + 半角空格 + 质粒名称」，
如 `10001 PNPM5.DNA`；编号或名称全局唯一时也可只写其一
（`10001.DNA` / `PNPM5.DNA`），三者表示同一质粒。

扩展名在分派表里一律是小写（{".dna"} 等），因此每个入口都必须先归一化，
否则按规范写成大写 `.DNA` 的文件会 415 或被当成未知文件静默忽略。
本文件按入口逐个锁住：

- core.plasmid_map.parsers         图谱页 / 载体导入（detect_format / parse_file）
- core.sanger.reference_parser     测序参考图谱（parse_reference_topology）
- app.routes.sequencing_routes     批量交付归类（_classify_batch_blobs）
- core.sanger.batch                图谱文件名 → 质粒名称的多级匹配
"""

import struct

from app.routes.sequencing_routes import _classify_batch_blobs
from core.plasmid_map.parsers import detect_format, parse_file
from core.sanger.batch import _match_ref_name, match_files, norm_stem
from core.sanger.reference_parser import parse_reference_topology

SEQ = ("ATGCGT" * 50)  # 300 bp


def _snapgene_bytes(seq: str = SEQ, circular: bool = True) -> bytes:
    """最小 SnapGene 容器：cookie + DNA 包 + 空特征 XML（与真实 .dna 同结构）"""
    def packet(t, data):
        return struct.pack(">BI", t, len(data)) + data

    cookie = packet(0x09, struct.pack(">8sHHH", b"SnapGene", 1, 15, 19))
    dna = packet(0x00, bytes([1 if circular else 0]) + seq.encode())
    xml = (
        '<?xml version="1.0"?><Features nextValidID="2">'
        '<Feature recentID="0" name="myCDS" directionality="1" type="CDS">'
        '<Segment range="4-33" color="#993366" type="standard"/></Feature>'
        '</Features>'
    ).encode()
    return cookie + dna + packet(0x0A, xml)


# ==================== 扩展名大小写 ====================

def test_detect_format_ignores_extension_case():
    """图谱解析的格式判定：大写/混合大小写与全小写等价"""
    cases = [
        ("10001 PNPM5.DNA", "snapgene"),
        ("10001.DNA", "snapgene"),
        ("10001 PNPM5.Dna", "snapgene"),
        ("10001 PNPM5.dNa", "snapgene"),
        ("10001 PNPM5.GB", "genbank"),
        ("10001 PNPM5.GenBank", "genbank"),
        ("10001 PNPM5.GBK", "genbank"),
        ("10001 PNPM5.FASTA", "fasta"),
        ("10001 PNPM5.Fa", "fasta"),
    ]
    for name, fmt in cases:
        assert detect_format(name) == fmt, name


def test_parse_file_accepts_uppercase_dna():
    """按规范的 `10001 PNPM5.DNA` 直接解析；文件名主干中的空格原样保留"""
    rec = parse_file("10001 PNPM5.DNA", _snapgene_bytes())
    assert rec.source_format == "snapgene"
    assert rec.topology == "circular"
    assert rec.sequence == SEQ
    assert rec.name == "10001 PNPM5"


def test_parse_file_uppercase_linear_topology():
    """大写扩展名不影响拓扑判定（线性件不能被当环状）"""
    rec = parse_file("10001-1 pET-30a.DNA", _snapgene_bytes(circular=False))
    assert rec.topology == "linear"
    assert rec.name == "10001-1 pET-30a"


def test_reference_parser_accepts_uppercase_dna():
    """测序参考图谱解析：.DNA 与 .dna 同一条路径"""
    seq, _features, circular = parse_reference_topology(
        "10001 PNPM5.DNA", _snapgene_bytes())
    assert seq == SEQ
    assert circular is True


def test_batch_classify_accepts_uppercase_extensions():
    """批量交付归类：大写扩展名照样分成参考图谱与 read"""
    reads, refs, ignored = _classify_batch_blobs([
        ("10001 PNPM5.DNA", _snapgene_bytes()),
        ("10001 PNPM5-F1.AB1", b"\x00fake-ab1"),
    ])
    assert ignored == []
    assert [r["ext"] for r in refs] == [".dna"]
    assert [r["ext"] for r in reads] == [".ab1"]
    # 主干归一化后与 Excel 里的质粒名称同形（去空白、小写）
    assert refs[0]["stem"] == norm_stem("10001 PNPM5") == "10001pnpm5"


# ==================== 命名规范：三种写法等价 ====================

def test_number_name_or_both_match_same_plasmid():
    """"编号 名称"「仅编号」「仅名称」都指向同一质粒"""
    names = {"10001 PNPM5"}
    for stem in ("10001 pnpm5", "10001", "pnpm5"):
        plasmid, _how = _match_ref_name(stem, names)
        assert plasmid == "10001 PNPM5", stem


def test_sub_number_suffix_matches():
    """编号带分支后缀（10001-1）时三种写法同样成立"""
    names = {"10001-1 pET-30a"}
    for stem in ("10001-1 pet-30a", "10001-1", "pet-30a", "pet30a"):
        assert _match_ref_name(stem, names)[0] == "10001-1 pET-30a", stem


def test_number_only_refused_when_ambiguous():
    """同一编号被多个质粒共用时，仅写编号无法唯一确定 → 不猜"""
    plasmid, how = _match_ref_name("10001", {"10001 PNPM5", "10001 ABC"})
    assert plasmid is None
    assert "无法唯一确定" in how


def test_batch_grouping_matches_naming_convention_end_to_end():
    """规范命名的 .DNA 与 .ab1 经归类后能正确归到信息表那一行"""
    reads, refs, _ignored = _classify_batch_blobs([
        ("10001 PNPM5.DNA", _snapgene_bytes()),
        ("10001 PNPM5-F1.ab1", b"\x00fake-ab1"),
    ])
    rows = [{"name": "10001 PNPM5", "clone": "",
             "primers": ["10001 PNPM5-F1", "10001 PNPM5-R1"]}]
    per_plasmid, unmatched = match_files(rows, reads + refs)
    assert unmatched == []
    slot = per_plasmid["10001 PNPM5"]
    assert slot["reference"]["file"]["name"] == "10001 PNPM5.DNA"
    assert slot["reads"][0]["file"]["name"] == "10001 PNPM5-F1.ab1"
