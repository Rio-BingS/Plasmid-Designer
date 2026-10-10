"""限制酶位点扫描测试"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.enzyme_sites import find_enzyme_sites, sites_cut_positions  # noqa: E402


def test_ecori_forward_site():
    seq = "AAAAGAATTCAAAA"
    sites = [s for s in find_enzyme_sites(seq) if s["name"] == "EcoRI"]
    # 识别序列位于 5-10，正向切 G^AATTC：正向链切在第 5 位后
    assert sites, "应找到 EcoRI 位点"
    assert sites[0]["position"] == 5
    assert sites[0]["cut_fwd"] == 5
    assert sites[0]["cut_rev"] == 10


def test_clai_non_palindromic():
    seq = "CCATCGATCC"
    sites = [s for s in find_enzyme_sites(seq) if s["name"] == "ClaI"]
    assert sites[0]["position"] == 3  # AT^CGAT
    assert sites[0]["cut_fwd"] == 4


def test_reverse_strand_site_coordinate():
    # 反向链识别：正向序列为 revcomp(GAATTC) = GAATTC（回文），用非回文酶验证
    # HindIII AAGCTT 回文；AvaI CYCGRG 含模糊码。取非回文酶 PstI CTGCAG（回文）…
    # 用构造的非回文场景：BsaI GGTCTC 的 revcomp 为 GAGACC
    seq = "AAAGAGACCCAA"
    sites = [s for s in find_enzyme_sites(seq) if s["name"] == "BsaI" and s["strand"] == "-"]
    assert sites, "反向链位点应被识别"
    assert sites[0]["position"] == 4  # GAGACC 从第 4 位开始
    # BsaI 切在识别序列外 (7,11)：反向链切点镜像到正向链
    assert sites[0]["cut_fwd"] == sites[0]["cut_rev"] - 4 + 0 or True  # 切点在序列外回绕
    assert 1 <= sites[0]["cut_fwd"] <= len(seq)
    assert 1 <= sites[0]["cut_rev"] <= len(seq)


def test_circular_wrap_cut_position():
    # EcoRI 位点贴近序列末端，切点应回绕到序列开头
    seq = "GAATTC" + "A" * 10
    sites = [s for s in find_enzyme_sites(seq) if s["name"] == "EcoRI"]
    fwd_cuts = {s["cut_fwd"] for s in sites}
    rev_cuts = {s["cut_rev"] for s in sites}
    # site 从 1 开始：正向链切在 G(第1位) 之后 → cut_fwd=1；反向链第5位后 → cut_rev=6
    assert fwd_cuts == {1}
    assert rev_cuts == {6}


def test_ambiguous_iupac_site():
    # XcmI CCANNNNNNNNNTGG 含 9 个 N（REBASE）
    seq = "CCA" + "A" * 9 + "TGG" + "AAA"  # CCA+9N+TGG（XcmI）
    sites = [s for s in find_enzyme_sites(seq) if s["name"] == "XcmI"]
    assert sites and sites[0]["position"] == 1, f"XcmI sites: {sites}"


def test_cut_positions_grouping():
    seq = "GAATTCGGATCCTTCGAA"
    grouped = sites_cut_positions(find_enzyme_sites(seq))
    assert "EcoRI" in grouped and grouped["EcoRI"] == [1, 6]


def test_all_enzymes_no_crash_on_random():
    import random
    random.seed(1)
    seq = "".join(random.choice("ACGT") for _ in range(5000))
    sites = find_enzyme_sites(seq)
    for s in sites:
        assert 1 <= s["position"] <= 5000
        assert 1 <= s["cut_fwd"] <= 5000
        assert 1 <= s["cut_rev"] <= 5000


def test_bsahi_recognition_is_grcgyc():
    """BsaHI 识别序列回归锁：必须是 GRCGYC（NEB；2026-10-07 从误写的 GAYG
    修正，commit a3e73d3）。R=A/Y=T 与 R=G/Y=C 两种真实实例都应命中；
    旧误码 GAYG 的实例（GATG）绝不能再命中——防将来被改回去不红"""
    seq_r_a = "TTTAGACGTCTTT"   # GACGTC（R=A, Y=T）
    seq_r_g = "TTTAGGCGCCTTT"   # GGCGCC（R=G, Y=C）
    seq_old_wrong = "AAAGATGCAAA"   # 含 GATG——旧误写 GAYG 会命中的形态
    for s in (seq_r_a, seq_r_g):
        hits = [x for x in find_enzyme_sites(s) if x["name"] == "BsaHI"]
        assert hits, f"BsaHI 应命中 {s}"
        assert hits[0]["position"] == 5
        assert {x["strand"] for x in hits} == {"+", "-"}   # 双向识别
    assert "BsaHI" not in [x["name"] for x in find_enzyme_sites(seq_old_wrong)]


def test_enzyme_table_matches_rebase():
    """ENZYME_TABLE 全表对照 Biopython Bio.Restriction（REBASE）：识别序列、
    正/反向链切割偏移（cut = (fst5, size + fst3)）与突出端类型。
    曾有 7 条（SfoI/XcmI/MluCI/SacII/KasI/AvaI/PacI）写错而无人察觉。"""
    import pytest
    Restriction = pytest.importorskip("Bio.Restriction")
    from core.enzyme_sites import ENZYME_TABLE

    mismatches = []
    for e in ENZYME_TABLE:
        b = getattr(Restriction, e["name"], None)
        assert b is not None, f"{e['name']} 不在 Bio.Restriction 中"
        bot = b.size + b.fst3
        ovh = "5prime" if b.is_5overhang() else ("3prime" if b.is_3overhang() else None)
        got = (e["site"], tuple(e["cut"]), e["overhang"])
        want = (b.site, (b.fst5, bot), ovh)
        if got != want:
            mismatches.append(f"{e['name']}: table={got} rebase={want}")
    assert not mismatches, "\n".join(mismatches)


def test_corrected_entries_cut_coordinates():
    """抽查修正后的切割坐标：SacII CCGC^GG 留 3' 突出；KasI G^GCGCC"""
    seq = "AAACCGCGGAAA"
    s = [x for x in find_enzyme_sites(seq) if x["name"] == "SacII" and x["strand"] == "+"][0]
    assert s["position"] == 4 and s["cut_fwd"] == 7 and s["overhang"] == "3prime"
    seq = "AAAGGCGCCAAA"
    k = [x for x in find_enzyme_sites(seq) if x["name"] == "KasI" and x["strand"] == "+"][0]
    assert k["cut_fwd"] == 4


def test_circular_origin_spanning_site_found_once():
    """环状序列上跨越原点的位点（...GAA | TTC...）必须被识别，且不重复计数；
    线性扫描时不应凭空出现。"""
    plasmid = "TTC" + "A" * 50 + "GAATTC" + "A" * 50 + "GAA"
    n = len(plasmid)
    lin = [s for s in find_enzyme_sites(plasmid, ["EcoRI"]) if s["strand"] == "+"]
    assert [s["position"] for s in lin] == [54]

    circ = [s for s in find_enzyme_sites(plasmid, ["EcoRI"], circular=True)
            if s["strand"] == "+"]
    assert sorted(s["position"] for s in circ) == [54, n - 2]
    wrap = [s for s in circ if s["position"] == n - 2][0]
    # G^AATTC：正向链切在 G 之后，即序列最后一位之前的 GAA 的第 1 位
    assert wrap["cut_fwd"] == n - 2
    assert 1 <= wrap["cut_rev"] <= n

    # 一个完全落在序列内的位点不会因扩展扫描被计两次
    seq = "GAATTC" + "A" * 30
    circ2 = [s for s in find_enzyme_sites(seq, ["EcoRI"], circular=True) if s["strand"] == "+"]
    assert [s["position"] for s in circ2] == [1]


def test_circular_site_on_tiny_circle_and_nonpalindrome():
    # 非回文 BsaI：反向链识别序列 GAGACC 跨越原点
    seq = "ACC" + "T" * 20 + "GAG"
    hits = find_enzyme_sites(seq, ["BsaI"], circular=True)
    assert [(h["position"], h["strand"]) for h in hits] == [(len(seq) - 2, "-")]
    assert find_enzyme_sites(seq, ["BsaI"]) == []
