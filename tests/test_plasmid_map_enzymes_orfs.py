"""core.plasmid_map 酶切位点（契约 MapEnzyme）与环状感知 ORF"""

from pathlib import Path

import yaml

from core.plasmid_map.enzymes import enzyme_sets, find_map_enzymes, scan_enzyme
from core.plasmid_map.orfs import find_orfs
from core.seq_utils import revcomp

VECTORS = Path(__file__).resolve().parent.parent / "data" / "vectors"


def _vector_seq(vid: str) -> str:
    for p in VECTORS.glob("*.yaml"):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if d.get("id") == vid:
            return d["sequence"].upper()
    raise KeyError(vid)


# ==================== 酶切 ====================

def test_enzyme_cuts_match_biopython_both_orientations():
    from Bio import Restriction as R
    from Bio.Seq import Seq

    seq = _vector_seq("pET-28a")
    for name in ("EcoRI", "BsmBI", "BbsI", "SapI", "XcmI", "NdeI", "AflIII"):
        e = scan_enzyme(seq, name, circular=True)
        bio = sorted(getattr(R, name).search(Seq(seq), linear=False))
        assert sorted({s["cut_top"] % len(seq) + 1 for s in e["sites"]}) == bio, name
        assert e["cut_count"] == len(bio)


def test_enzyme_record_shape_and_overhangs():
    seq = "AAAA" + "GAATTC" + "AAAA" + "GGTCTC" + "A" * 30 + "GAGACC" + "A" * 40
    ecori = scan_enzyme(seq, "EcoRI", circular=False)
    assert (ecori["recognition"], ecori["ends"], ecori["overhang"]) == ("G^AATTC", "5'", "AATT")
    assert ecori["sites"] == [{"position": 5, "cut_top": 5, "cut_bottom": 9, "strand": 1,
                               "overhang": "AATT"}]
    assert "methylation" not in ecori
    bsai = scan_enzyme(seq, "BsaI", circular=False)
    assert bsai["recognition"] == "GGTCTC(1/5)" and bsai["ends"] == "5'"
    fwd, rev = bsai["sites"]
    # 正向位点 15..20：切在识别序列下游 N1/N5；反向位点（GAGACC，51..56）：
    # 切在上游——底链隔 1 nt、顶链隔 5 nt。5' 突出端在正向坐标里恒为 bottom > top
    assert fwd["strand"] == 1 and (fwd["position"], fwd["cut_top"], fwd["cut_bottom"]) == (15, 21, 25)
    assert rev["strand"] == -1 and (rev["position"], rev["cut_top"], rev["cut_bottom"]) == (51, 45, 49)
    pst = scan_enzyme("AA" + "CTGCAG" + "AA", "PstI", circular=False)
    assert pst["ends"] == "3'" and pst["sites"][0]["cut_top"] - pst["sites"][0]["cut_bottom"] == 4
    sma = scan_enzyme("AA" + "CCCGGG" + "AA", "SmaI", circular=False)
    assert sma["ends"] == "blunt" and sma["overhang"] is None


def test_circular_wraparound_site_and_linear_end_drop():
    seq = "ATTC" + "C" * 40 + "GA"  # GA|ATTC 跨原点
    assert scan_enzyme(seq, "EcoRI", circular=False)["cut_count"] == 0
    e = scan_enzyme(seq, "EcoRI", circular=True)
    assert e["cut_count"] == 1
    assert e["sites"][0] == {"position": len(seq) - 1, "cut_top": len(seq) - 1,
                             "cut_bottom": 3, "strand": 1, "overhang": "AATT"}
    # 线性分子末端的 Type IIS：切点落在分子外 → 不算切割
    assert scan_enzyme("A" * 20 + "GGTCTC" + "AA", "BsaI", circular=False)["cut_count"] == 0


def test_enzyme_sets_and_only_cutters_returned():
    sets = {s["id"]: s for s in enzyme_sets()}
    assert set(sets) >= {"common", "unique6", "commercial", "golden_gate"}
    assert "EcoRI" in sets["common"]["enzymes"] and len(sets["common"]["enzymes"]) == 51
    assert set(sets["golden_gate"]["enzymes"]) == {"BsaI", "BsmBI", "BbsI", "SapI", "Esp3I", "PaqCI"}
    assert set(sets["unique6"]["enzymes"]) < set(sets["commercial"]["enzymes"])
    assert "MluCI" not in sets["unique6"]["enzymes"]  # 4 碱基识别
    found = find_map_enzymes("AAGAATTCAA", ["EcoRI", "BamHI"], circular=False)
    assert [e["name"] for e in found] == ["EcoRI"]


def test_site_overhang_actual_bases_type_iis_and_degenerate():
    # BsaI GGTCTC(1/5)：正向位点突出端 = 位点后第 2..5 位；反向位点（GAGACC）在上游
    seq = "AAAA" + "GGTCTC" + "A" + "CTGA" + "T" * 30 + "GTCA" + "G" + "GAGACC" + "A" * 40
    bsai = scan_enzyme(seq, "BsaI", circular=False)
    assert bsai["overhang"] == "NNNN"  # 酶级仍是 REBASE 模板
    fwd, rev = bsai["sites"]
    assert fwd["overhang"] == "CTGA"
    assert rev["overhang"] == "GTCA"  # 正向链读法（底链上是 TGAC）
    for s in bsai["sites"]:
        lo, hi = sorted((s["cut_top"], s["cut_bottom"]))
        assert s["overhang"] == seq[lo:hi]
    # 简并识别：AvaI C^YCGRG → 实际碱基
    ava = scan_enzyme("AA" + "CTCGAG" + "AA" + "CCCGGG" + "AA", "AvaI", circular=False)
    assert ava["overhang"] == "YCGR"
    assert [s["overhang"] for s in ava["sites"]] == ["TCGA", "CCGG"]
    # 3' 突出端同样取两切点之间的正链碱基；平末端不给 overhang
    pst = scan_enzyme("AA" + "CTGCAG" + "AA", "PstI", circular=False)
    assert pst["sites"][0]["overhang"] == "TGCA"
    sma = scan_enzyme("AA" + "CCCGGG" + "AA", "SmaI", circular=False)
    assert "overhang" not in sma["sites"][0]


def test_site_overhang_across_origin():
    # BsaI 位点在末尾，切点与突出端跨原点
    body = "C" * 40
    seq = "TGCA" + body + "GGTCTC" + "A"  # 位点后 N1 = 'A'，突出端 = 开头 TGCA
    e = scan_enzyme(seq, "BsaI", circular=True)
    s = e["sites"][0]
    assert s["overhang"] == "TGCA"
    assert (s["cut_top"], s["cut_bottom"]) == (len(seq), 4)


def test_methylation_dam_dcm_blocking():
    from core.plasmid_map.methylation import methylation_kinds

    assert methylation_kinds("XbaI") == ["dam"] and methylation_kinds("StuI") == ["dcm"]
    assert methylation_kinds("EcoRI") is None and methylation_kinds("BamHI") is None
    # XbaI：后接 TC 或前接 GA（GATC 跨位点边界）被 Dam 阻断，否则不受影响
    seq = "AAA" + "TCTAGATC" + "AAAA" + "GATCTAGA" + "AAAA" + "TCTAGAAA"
    x = scan_enzyme(seq, "XbaI", circular=False)
    assert x["methylation"] == ["dam"]
    assert [s.get("blocked_by") for s in x["sites"]] == [["dam"], ["dam"], None]
    # ClaI：ATCGATC / GATCGAT
    c = scan_enzyme("AA" + "ATCGATC" + "AA" + "ATCGATA" + "AA", "ClaI", circular=False)
    assert [s.get("blocked_by") for s in c["sites"]] == [["dam"], None]
    # StuI：AGGCCTGG（CCTGG 为 Dcm 位点）；BamHI 含 GATC 但不敏感
    st = scan_enzyme("AA" + "AGGCCTGG" + "AA" + "AGGCCTAA" + "AA", "StuI", circular=False)
    assert st["methylation"] == ["dcm"]
    assert [s.get("blocked_by") for s in st["sites"]] == [["dcm"], None]
    bam = scan_enzyme("AAGGATCCAA", "BamHI", circular=False)
    assert "methylation" not in bam and "blocked_by" not in bam["sites"][0]
    # 非回文 BsaI：反向位点 GAGACC 后接 WGG（CCWGG 跨边界）
    b = scan_enzyme("A" * 10 + "GAGACCAGG" + "A" * 10 + "GAGACCTAA" + "A" * 10,
                    "BsaI", circular=False)
    assert b["methylation"] == ["dcm"]
    assert [s.get("blocked_by") for s in b["sites"]] == [["dcm"], None]
    # 正向位点 GGTCTC 前接 CCW：CCWGGTCTC
    b2 = scan_enzyme("A" * 10 + "CCTGGTCTC" + "A" * 20, "BsaI", circular=False)
    assert b2["sites"][0]["blocked_by"] == ["dcm"]


def test_methylation_context_across_origin():
    # 环状：XbaI 在末尾，阻断所需的 TC 在开头
    seq = "TC" + "G" * 30 + "TCTAGA"
    circ = scan_enzyme(seq, "XbaI", circular=True)
    assert circ["sites"][0]["blocked_by"] == ["dam"]
    lin = scan_enzyme(seq + "A", "XbaI", circular=False)
    assert "blocked_by" not in lin["sites"][0]


def test_puc19_xbai_not_dam_blocked():
    # pUC19 MCS 的 XbaI（TCTAGAGTC）不与 GATC 重叠，常规菌株质粒可切
    x = scan_enzyme(_vector_seq("pUC19"), "XbaI", circular=True)
    assert x["cut_count"] == 1 and "blocked_by" not in x["sites"][0]


# ==================== ORF ====================

def test_orfs_both_strands_and_circular_wrap():
    body = "GCT" * 120  # 120 aa
    orf = "ATG" + body + "TAA"
    seq = "CC" + orf + "GG" * 20
    got = find_orfs(seq, circular=False, min_aa=100)
    assert got == [{"start": 3, "end": 2 + len(orf), "strand": 1, "frame": 2, "aa_length": 121}]
    # 负链
    rc = revcomp(seq)
    neg = find_orfs(rc, circular=False, min_aa=100)
    assert neg[0]["strand"] == -1 and neg[0]["aa_length"] == 121
    assert (neg[0]["start"], neg[0]["end"]) == (len(rc) - (2 + len(orf)) + 1, len(rc) - 2)
    # 旋转让 ORF 跨原点：线性找不到，环状报一次且 start > end
    rot = seq[100:] + seq[:100]
    assert not [o for o in find_orfs(rot, circular=False, min_aa=100) if o["strand"] == 1]
    circ = [o for o in find_orfs(rot, circular=True, min_aa=100) if o["strand"] == 1]
    assert len(circ) == 1 and circ[0]["aa_length"] == 121
    assert circ[0]["start"] > circ[0]["end"]


def test_orfs_vector_kanr_found():
    seq = _vector_seq("pET-28a")
    orfs = find_orfs(seq, circular=True, min_aa=100)
    assert {"start": 3995, "end": 4810, "strand": 1} in [
        {k: o[k] for k in ("start", "end", "strand")} for o in orfs]


# ==================== 修饰依赖型酶与位点上限（v2.12.0 审查 H3） ====================

MOD_DEP = {"AbaSI", "MspJI", "FspEI", "SgeI", "LpnPI"}


def test_modification_dependent_enzymes_excluded_from_sets():
    from core.enzyme_sites import get_enzyme, is_modification_dependent
    from core.plasmid_map.enzymes import enzyme_detail, suppliers

    sets = {s["id"]: set(s["enzymes"]) for s in enzyme_sets()}
    for sid in ("commercial", "unique6", "type_iis", "golden_gate", "common"):
        assert not (sets[sid] & MOD_DEP), sid
        assert not any(is_modification_dependent(get_enzyme(n)) for n in sets[sid]), sid
    # 规则推导：≤3 个确定碱基且切在位点外 → 5 把全中；SibEnzyme 5mC 依赖酶走名单
    assert all(is_modification_dependent(get_enzyme(n)) for n in MOD_DEP)
    assert is_modification_dependent(get_enzyme("GlaI"))
    assert not sets["commercial"] & {"GlaI", "BisI", "KroI"}
    # 普通的低特异性酶与 Dam 依赖的 DpnI 仍在
    for n in ("CviJI", "SetI", "DpnI", "MluCI", "EcoRI", "BsaI"):
        assert not is_modification_dependent(get_enzyme(n)), n
        assert n in sets["commercial"], n
    assert not any(MOD_DEP & set(r["enzymes"]) for r in suppliers())
    d = enzyme_detail("AbaSI")  # 详情仍可查，带标记
    assert d is not None and d["modification_dependent"] is True
    assert enzyme_detail("EcoRI")["modification_dependent"] is False


def test_sites_capped_per_enzyme_with_true_cut_count(monkeypatch):
    import core.plasmid_map.enzymes as E

    seq = ("GAATTC" + "A" * 20) * 30
    full = scan_enzyme(seq, "EcoRI", circular=True)
    assert full["cut_count"] == 30 and "sites_truncated" not in full
    monkeypatch.setattr(E, "MAX_SITES_PER_ENZYME", 7)
    warns = []
    out = find_map_enzymes(seq, ["EcoRI", "BamHI"], circular=True, warnings=warns)
    (e,) = out
    assert e["cut_count"] == 30 and e["sites_truncated"] is True
    assert e["sites"] == full["sites"][:7]  # 按位置保留最前面的
    assert warns and "EcoRI" in warns[0]


def test_total_site_budget_drops_most_frequent_cutters_first(monkeypatch):
    import core.plasmid_map.enzymes as E

    seq = ("GAATTC" + "A" * 4 + "GGATCC" + "A" * 4 + "AGCT" + "TT" + "AGCT" + "TT") * 20
    monkeypatch.setattr(E, "MAX_TOTAL_SITES", 45)
    warns = []
    out = {e["name"]: e for e in find_map_enzymes(seq, ["EcoRI", "BamHI", "AluI"], True,
                                                 warnings=warns)}
    assert out["AluI"]["cut_count"] == 40 and out["AluI"]["sites"] == []
    assert out["AluI"]["sites_truncated"] is True
    assert len(out["EcoRI"]["sites"]) == 20 and "sites_truncated" not in out["EcoRI"]
    assert sum(len(e["sites"]) for e in out.values()) <= 45
    assert warns


def test_commercial_document_size_bounded():
    """50 kb 随机序列 + commercial：旧实现 91 494 个位点 / 8.3 MB（AbaSI 等占大头）"""
    import json
    import random

    from core.plasmid_map.document import build_document

    rng = random.Random(0)
    seq = "".join(rng.choice("ACGT") for _ in range(50_000))
    d = build_document(name="x", sequence=seq, topology="circular", source_format="raw",
                       annotate=False, enzyme_set="commercial")
    names = {e["name"] for e in d["enzymes"]}
    assert not names & MOD_DEP
    assert len(json.dumps(d)) < 5_000_000
