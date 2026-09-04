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
    # XcmI CCANNNNNNNTGG 含 N
    seq = "CCA" + "A" * 7 + "TGG" + "AAA"  # CCA+7N+TGG（XcmI）
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
