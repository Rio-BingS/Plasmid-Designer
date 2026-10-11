"""GenBank 复合位置（join/order）特征逐段展开与下游 CDS 判读

回归锁：join(2701..3000,1..300) 曾被取整体跨度解析成 1..3000，整条
质粒都被注释为该 CDS，任何位置的变异都报"位于 ampR 内"，CDS 报告
也按并不存在的 3000bp 阅读框计算密码子。
"""

import io
import os
import random
import sys

from Bio import SeqIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.genbank_format import format_locus_line
from core.seq_utils import revcomp, CODON_TO_AA
from core.sanger.annotator import annotate_variant
from core.sanger.pipeline import _build_cds_reports
from core.sanger.reference_parser import parse_reference, parse_reference_topology

L = 3000
STOPS = {"TAA", "TAG", "TGA"}
SENSE = sorted(c for c in CODON_TO_AA if c not in STOPS)


def _origin_block(seq: str) -> str:
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        groups = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
        lines.append(f"{i + 1:>9} {groups}")
    return "\n".join(lines)


def _genbank(seq: str, feature_lines: str) -> bytes:
    return f"""{format_locus_line("JoinRef", len(seq), topology="circular", date="01-JAN-2026")}
DEFINITION  synthetic origin-spanning construct.
ACCESSION   joinref
FEATURES             Location/Qualifiers
     source          1..{len(seq)}
                     /organism="synthetic construct"
{feature_lines}ORIGIN
{_origin_block(seq.lower())}
//
""".encode()


def _coding(rng: random.Random) -> str:
    """600 nt 编码序列：ATG + 198 个有义密码子 + TAA（无内部终止）"""
    return "ATG" + "".join(rng.choice(SENSE) for _ in range(198)) + "TAA"


def _background(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("ACGT") for _ in range(n))


def _plus_join_construct():
    """正链 CDS join(2701..3000,1..300)：编码前 300 nt 在 2701..3000，后 300 nt 在 1..300"""
    rng = random.Random(10)
    cds = _coding(rng)
    seq = cds[300:] + _background(rng, 2400) + cds[:300]
    assert len(seq) == L
    gb = _genbank(seq, (
        "     CDS             join(2701..3000,1..300)\n"
        '                     /label="ampR"\n'
        "     promoter        1001..1100\n"
        '                     /label="P1"\n'
    ))
    return seq, cds, gb


def _minus_join_construct():
    """负链 CDS complement(join(2701..3000,1..300))：编码方向先 1..300 后 2701..3000"""
    rng = random.Random(11)
    cds = _coding(rng)
    seq = revcomp(cds[:300]) + _background(rng, 2400) + revcomp(cds[300:])
    assert len(seq) == L
    gb = _genbank(seq, (
        "     CDS             complement(join(2701..3000,1..300))\n"
        '                     /label="kanR"\n'
    ))
    return seq, cds, gb


def _mutant_base(codon: str, idx: int) -> str:
    """挑一个使该密码子发生氨基酸改变（且不变终止）的替换碱基"""
    aa = CODON_TO_AA[codon]
    for b in "ACGT":
        mut = codon[:idx] + b + codon[idx + 1:]
        if b != codon[idx] and CODON_TO_AA[mut] not in (aa, "*"):
            return b
    raise AssertionError("no missense base")


def _sub(pos: int, ref_base: str, alt_base: str) -> dict:
    return {"ref_pos": pos, "type": "substitution", "ref_base": ref_base,
            "alt_base": alt_base, "length": 1, "confidence": "high"}


# ==================== 解析器 ====================

def test_join_emits_one_record_per_part_with_group_key():
    seq, _cds, gb = _plus_join_construct()
    out_seq, feats, circular = parse_reference_topology("join.gb", gb)
    assert out_seq == seq and circular is True
    amp = [f for f in feats if f["name"] == "ampR"]
    assert len(amp) == 2
    by_idx = sorted(amp, key=lambda f: f["segment_index"])
    assert [(f["start"], f["end"]) for f in by_idx] == [(2701, 3000), (1, 300)]
    assert {f["feature_id"] for f in amp} == {amp[0]["feature_id"]}
    assert all(f["segment_count"] == 2 and f["strand"] == "+" and f["type"] == "CDS"
               for f in amp)
    # 单段特征不带分组键（输出形状与改动前一致）
    p1 = next(f for f in feats if f["name"] == "P1")
    assert set(p1) == {"name", "type", "start", "end", "strand", "description"}


def test_minus_join_segment_order_follows_coding_direction():
    _seq, _cds, gb = _minus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    kan = sorted((f for f in feats if f["name"] == "kanR"), key=lambda f: f["segment_index"])
    assert [(f["start"], f["end"], f["strand"]) for f in kan] == [(1, 300, "-"), (2701, 3000, "-")]


def test_existing_fixture_simple_features_unchanged():
    """复用既有 GenBank 夹具重新解析：单段特征的数量与坐标与 Biopython
    原始位置逐一相符；追加一个 join 特征只多出它的两段，其余记录不变"""
    from test_reference_parser import GENBANK_TEXT

    _seq, feats = parse_reference("construct.gb", GENBANK_TEXT.encode())
    record = next(SeqIO.parse(io.StringIO(GENBANK_TEXT), "genbank"))
    simple = [f for f in record.features
              if f.type != "source" and len(f.location.parts) == 1]
    assert len(feats) == len(simple) == 2
    assert sorted((f["start"], f["end"]) for f in feats) == sorted(
        (int(f.location.start) + 1, int(f.location.end)) for f in simple)

    with_join = GENBANK_TEXT.replace(
        "ORIGIN\n",
        "     CDS             join(101..120,1..4)\n"
        '                     /label="wrap"\n'
        "ORIGIN\n",
    )
    _seq2, feats2 = parse_reference("construct.gb", with_join.encode())
    non_compound = [f for f in feats2 if "feature_id" not in f]
    assert non_compound == feats
    assert len(feats2) == len(feats) + 2


# ==================== 注释器 ====================

def test_variant_outside_parts_hits_no_feature():
    seq, _cds, gb = _plus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    v = annotate_variant(_sub(1500, seq[1499], "A" if seq[1499] != "A" else "C"), feats, seq)
    assert v["features"] == []
    assert v["codon_change"] is None and v["aa_change"] is None


def test_variant_in_second_part_maps_to_joined_codon():
    seq, cds, gb = _plus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    # 参考 50 → 编码偏移 300 + 49 = 349 → 第 117 个密码子第 2 位
    off = 349
    codon = cds[off - off % 3: off - off % 3 + 3]
    alt = _mutant_base(codon, off % 3)
    assert seq[49] == cds[off]
    v = annotate_variant(_sub(50, seq[49], alt), feats, seq)
    assert v["features"] == [{"name": "ampR", "type": "CDS"}]  # 同一特征只报一次
    mut = codon[:1] + alt + codon[2:]
    assert v["codon_change"] == f"{codon}>{mut}"
    assert v["aa_change"] == f"ampR:{CODON_TO_AA[codon]}117{CODON_TO_AA[mut]}"


def test_codon_straddling_origin_junction():
    """密码子跨段（2999,3000 | 1）时按拼接序列取密码子"""
    rng = random.Random(12)
    cds = "ATG" + "".join(rng.choice(SENSE) for _ in range(198)) + "TAA"
    # 第一段 299 nt（2702..3000），密码子 100 = 编码 297..299 跨段
    seq = cds[299:] + _background(rng, L - 600) + cds[:299]
    feats = [
        {"name": "x", "type": "CDS", "start": 2702, "end": 3000, "strand": "+",
         "feature_id": 3, "segment_index": 0, "segment_count": 2},
        {"name": "x", "type": "CDS", "start": 1, "end": 301, "strand": "+",
         "feature_id": 3, "segment_index": 1, "segment_count": 2},
    ]
    codon = cds[297:300]
    alt = _mutant_base(codon, 1)
    assert seq[2999] == cds[298]  # 参考 3000 = 密码子 100 第 2 位，第 3 位在参考 1
    v = annotate_variant(_sub(3000, seq[2999], alt), feats, seq)
    assert v["codon_change"] == f"{codon}>{codon[0]}{alt}{codon[2]}"
    assert v["aa_change"].startswith(f"x:{CODON_TO_AA[codon]}100")


def test_minus_join_variant_codon():
    seq, cds, gb = _minus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    # 参考 251（负链第一段 1..300）→ 编码偏移 300 - 251 = 49 → 第 17 个密码子第 2 位
    off = 49
    codon = cds[48:51]
    alt_coding = _mutant_base(codon, 1)
    assert revcomp(seq[250]) == cds[off]
    v = annotate_variant(_sub(251, seq[250], revcomp(alt_coding)), feats, seq)
    assert v["features"] == [{"name": "kanR", "type": "CDS"}]
    mut = codon[0] + alt_coding + codon[2]
    assert v["aa_change"] == f"kanR:{CODON_TO_AA[codon]}17{CODON_TO_AA[mut]}"


# ==================== CDS 报告 ====================

def _full_cov():
    return {"diffs": [], "covered_ranges": [(1, L)], "coverage_percent": 100.0}


def test_cds_report_joined_cds_codon_and_coords():
    seq, cds, gb = _plus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    off = 349
    codon = cds[348:351]
    alt = _mutant_base(codon, 1)
    mut = codon[0] + alt + codon[2]
    reports = _build_cds_reports(seq, feats, [_sub(50, seq[49], alt)], _full_cov())
    assert len(reports) == 1
    cr = reports[0]
    assert (cr["start"], cr["end"]) == (2701, 300)
    assert cr["segments"] == [[2701, 3000], [1, 300]]
    assert cr["ref_protein_length"] == 199
    assert cr["coverage_status"] == "full"
    assert cr["aa_changes"] == [f"{CODON_TO_AA[codon]}117{CODON_TO_AA[mut]}"]
    assert off // 3 + 1 == 117


def test_cds_report_joined_cds_ignores_variant_between_parts():
    seq, _cds, gb = _plus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    alt = "A" if seq[1499] != "A" else "C"
    cr = _build_cds_reports(seq, feats, [_sub(1500, seq[1499], alt)], _full_cov())[0]
    assert cr["protein_identical"] is True
    assert cr["aa_changes"] == []


def test_cds_report_deletion_across_origin_is_inframe():
    seq, _cds, gb = _plus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    # 删除 2999、3000、1 三个碱基：跨原点但在拼接序列中连续 → 框内缺失
    dele = {"ref_pos": 2999, "type": "deletion", "ref_base": seq[2998:3000] + seq[0],
            "alt_base": "-", "length": 3, "confidence": "high"}
    cr = _build_cds_reports(seq, feats, [dele], _full_cov())[0]
    assert cr["frameshift_count"] == 0
    assert "frameshift_variant" not in cr["consequences"]


def test_cds_report_minus_join_and_partial_coverage():
    seq, cds, gb = _minus_join_construct()
    _s, feats = parse_reference("join.gb", gb)
    codon = cds[48:51]
    alt_coding = _mutant_base(codon, 1)
    mut = codon[0] + alt_coding + codon[2]
    cov = {"diffs": [], "covered_ranges": [(1, 300)], "coverage_percent": 10.0}
    cr = _build_cds_reports(seq, feats, [_sub(251, seq[250], revcomp(alt_coding))], cov)[0]
    assert cr["segments"] == [[1, 300], [2701, 3000]]
    assert cr["strand"] == "-"
    assert cr["covered_percent"] == 50.0
    assert cr["aa_changes"] == [f"{CODON_TO_AA[codon]}17{CODON_TO_AA[mut]}"]


def test_analyze_end_to_end_with_origin_spanning_cds():
    """全链路：环状参考 + 跨原点 read，join CDS 的变异注释与 CDS 报告均按拼接坐标"""
    from abif_utils import make_ab1
    from core.sanger.pipeline import analyze

    seq, cds, gb = _plus_join_construct()
    _s, feats = parse_reference_topology("join.gb", gb)[:2]
    codon = cds[348:351]
    alt = _mutant_base(codon, 1)
    mutant = seq[:49] + alt + seq[50:]
    read = mutant[2600:] + mutant[:450]  # 跨原点：2601..3000 + 1..450
    result = analyze([("t.ab1", make_ab1(read, [40] * len(read)))], seq, feats, circular=True)
    hits = [v for v in result["variants"] if v["ref_pos"] == 50]
    assert hits and hits[0]["features"] == [{"name": "ampR", "type": "CDS"}]
    assert hits[0]["aa_change"].startswith(f"ampR:{CODON_TO_AA[codon]}117")
    cr = [c for c in result["cds_reports"] if c["name"] == "ampR"]
    assert len(cr) == 1 and cr[0]["segments"] == [[2701, 3000], [1, 300]]
    assert cr[0]["protein_identical"] is False
