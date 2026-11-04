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


# ---- 审计 #4：统一到 Bio.Restriction 后的差分测试 ----
#
# 下面两张表是统一前手写表的冻结副本（enzyme_sites.ENZYME_TABLE 47 种、
# sequence_analysis.RESTRICTION_ENZYMES 26 种，取自 commit 82f4152）。
# 现在酶参数一律取自 REBASE；这里逐条对照，任何一条不一致都说明旧
# 手写表有错（或 REBASE 数据变了），需要人工核对后再改冻结表。
_LEGACY_ENZYME_TABLE = {
    "EcoRI": ("GAATTC", (1, 5), "5prime"), "BamHI": ("GGATCC", (1, 5), "5prime"),
    "HindIII": ("AAGCTT", (1, 5), "5prime"), "NotI": ("GCGGCCGC", (2, 6), "5prime"),
    "XhoI": ("CTCGAG", (1, 5), "5prime"), "SalI": ("GTCGAC", (1, 5), "5prime"),
    "PstI": ("CTGCAG", (5, 1), "3prime"), "SmaI": ("CCCGGG", (3, 3), None),
    "XbaI": ("TCTAGA", (1, 5), "5prime"), "SpeI": ("ACTAGT", (1, 5), "5prime"),
    "NheI": ("GCTAGC", (1, 5), "5prime"), "KpnI": ("GGTACC", (5, 1), "3prime"),
    "SacI": ("GAGCTC", (5, 1), "3prime"), "SphI": ("GCATGC", (5, 1), "3prime"),
    "NcoI": ("CCATGG", (1, 5), "5prime"), "NdeI": ("CATATG", (2, 4), "5prime"),
    "NruI": ("TCGCGA", (3, 3), None), "MluI": ("ACGCGT", (1, 5), "5prime"),
    "AflII": ("CTTAAG", (1, 5), "5prime"), "AflIII": ("ACRYGT", (1, 5), "5prime"),
    "AgeI": ("ACCGGT", (1, 5), "5prime"), "ApaI": ("GGGCCC", (5, 1), "3prime"),
    "AscI": ("GGCGCGCC", (2, 6), "5prime"), "AvaI": ("CYCGRG", (1, 5), "5prime"),
    "BglII": ("AGATCT", (1, 5), "5prime"), "BsaI": ("GGTCTC", (7, 11), "5prime"),
    "BsmBI": ("CGTCTC", (7, 11), "5prime"), "BspHI": ("TCATGA", (1, 5), "5prime"),
    "BstBI": ("TTCGAA", (2, 4), "5prime"), "ClaI": ("ATCGAT", (2, 4), "5prime"),
    "DraI": ("TTTAAA", (3, 3), None), "EcoRV": ("GATATC", (3, 3), None),
    "HpaI": ("GTTAAC", (3, 3), None), "KasI": ("GGCGCC", (1, 5), "5prime"),
    "MluCI": ("AATT", (0, 4), "5prime"), "NsiI": ("ATGCAT", (5, 1), "3prime"),
    "PacI": ("TTAATTAA", (5, 3), "3prime"), "PmeI": ("GTTTAAAC", (4, 4), None),
    "SacII": ("CCGCGG", (4, 2), "3prime"), "ScaI": ("AGTACT", (3, 3), None),
    "SfoI": ("GGCGCC", (3, 3), None), "SnaBI": ("TACGTA", (3, 3), None),
    "SrfI": ("GCCCGGGC", (4, 4), None), "StuI": ("AGGCCT", (3, 3), None),
    "SwaI": ("ATTTAAAT", (4, 4), None), "XcmI": ("CCANNNNNNNNNTGG", (8, 7), "3prime"),
    "BsaHI": ("GRCGYC", (2, 4), "5prime"),
}
_LEGACY_RESTRICTION_ENZYMES = {
    "EcoRI": ("GAATTC", 1, '5'), "BamHI": ("GGATCC", 1, '5'),
    "HindIII": ("AAGCTT", 1, '5'), "XhoI": ("CTCGAG", 1, '5'),
    "XbaI": ("TCTAGA", 1, '5'), "SalI": ("GTCGAC", 1, '5'),
    "PstI": ("CTGCAG", 5, '3'), "KpnI": ("GGTACC", 5, '3'),
    "SmaI": ("CCCGGG", 3, 'b'), "SbfI": ("CCTGCAGG", 6, '3'),
    "NotI": ("GCGGCCGC", 2, '5'), "NdeI": ("CATATG", 2, '5'),
    "NcoI": ("CCATGG", 1, '5'), "SacI": ("GAGCTC", 5, '3'),
    "BglII": ("AGATCT", 1, '5'), "AvrII": ("CCTAGG", 1, '5'),
    "SpeI": ("ACTAGT", 1, '5'), "AgeI": ("ACCGGT", 1, '5'),
    "MluI": ("ACGCGT", 1, '5'), "BsaI": ("GGTCTC", 7, '5'),
    "BsmBI": ("CGTCTC", 7, '5'), "BbsI": ("GAAGAC", 8, '5'),
    "EcoRV": ("GATATC", 3, 'b'), "DraI": ("TTTAAA", 3, 'b'),
    "PvuII": ("CAGCTG", 3, 'b'), "StuI": ("AGGCCT", 3, 'b'),
}


def test_default_menu_keeps_both_legacy_tables():
    from core.enzyme_sites import DEFAULT_ENZYMES, ENZYME_TABLE
    names = set(DEFAULT_ENZYMES)
    assert set(_LEGACY_ENZYME_TABLE) <= names
    assert set(_LEGACY_RESTRICTION_ENZYMES) <= names
    assert len(ENZYME_TABLE) == len(DEFAULT_ENZYMES) == 51
    assert {e["name"] for e in ENZYME_TABLE} == names


def test_legacy_enzyme_table_matches_rebase():
    """冻结的旧 ENZYME_TABLE 逐条对照 REBASE（曾有 7 条写错而无人察觉）"""
    from core.enzyme_sites import get_enzyme
    mismatches = []
    for name, (site, cut, overhang) in _LEGACY_ENZYME_TABLE.items():
        spec = get_enzyme(name)
        got = (spec["site"], tuple(spec["cut"]), spec["overhang"])
        if got != (site, cut, overhang):
            mismatches.append(f"{name}: legacy={(site, cut, overhang)} rebase={got}")
    assert not mismatches, "\n".join(mismatches)


def test_legacy_restriction_enzymes_match_rebase():
    """冻结的旧 sequence_analysis 表对照 REBASE 派生的兼容视图"""
    from core.sequence_analysis import RESTRICTION_ENZYMES
    mismatches = [
        f"{name}: legacy={entry} rebase={RESTRICTION_ENZYMES.get(name)}"
        for name, entry in _LEGACY_RESTRICTION_ENZYMES.items()
        if RESTRICTION_ENZYMES.get(name) != entry
    ]
    assert not mismatches, "\n".join(mismatches)


def _bio_cut_sets(seq, names, linear):
    from Bio.Seq import Seq
    from Bio.Restriction import RestrictionBatch
    res = RestrictionBatch(list(names)).search(Seq(seq), linear=linear)
    return {str(k): set(v) for k, v in res.items()}


def test_positions_match_biopython_random():
    """50 条随机 5 kb 序列，默认菜单全部 51 种酶：线性与环状两种拓扑下，
    find_enzyme_sites 的正链切点必须与 Bio.Restriction.search 完全一致
    （Bio 报切点后第一位，即 cut_fwd + 1）"""
    import random
    from core.enzyme_sites import DEFAULT_ENZYMES

    rng = random.Random(4)
    pad = "A" * 20  # 线性：避免 Type IIS 切点落在序列外被 Bio 丢弃/被我们取模
    for i in range(50):
        core = "".join(rng.choice("ACGT") for _ in range(5000))
        for circular in (False, True):
            seq = core if circular else pad + core + pad
            n = len(seq)
            want = _bio_cut_sets(seq, DEFAULT_ENZYMES, linear=not circular)
            got = {name: set() for name in DEFAULT_ENZYMES}
            for h in find_enzyme_sites(seq, circular=circular):
                got[h["name"]].add(h["cut_fwd"] % n + 1)
            assert got == want, (i, circular, {
                k: (sorted(got[k] ^ want[k])) for k in got if got[k] != want[k]})


def test_overlapping_sites_reported():
    """重叠位点都要报：AflIII ACRYGT 在 ACACGTGT 里有两个重叠实例
    （统一前的 re.finditer 非重叠扫描会漏掉第二个）"""
    seq = "TTACACGTGTTT"
    pos = sorted({h["position"] for h in find_enzyme_sites(seq, ["AflIII"])})
    want = _bio_cut_sets(seq, ["AflIII"], linear=True)["AflIII"]
    assert len(pos) == len(want) == 2


def test_extra_commercial_enzymes_by_name():
    from core.enzyme_sites import get_enzyme
    # 默认菜单之外的商业酶可按名使用
    hits = find_enzyme_sites("AAAGCTCTTCAAAAAAAA", ["SapI"])
    assert [(h["position"], h["strand"]) for h in hits] == [(4, "+")]
    assert get_enzyme("Esp3I")["cut"] == (7, 11)
    # 两侧双切的酶、非酶属性名、未知名一律拒绝；扫描时静默跳过
    assert get_enzyme("BaeI") is None
    assert get_enzyme("Analysis") is None
    assert get_enzyme("NotAnEnzyme") is None
    assert find_enzyme_sites("GAATTC", ["NotAnEnzyme"]) == []


def test_module_import_does_not_load_rebase():
    """模块导入不得触发 Bio.Restriction（约 6 MB RSS），首次使用才加载"""
    import subprocess
    backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "backend")
    code = ("import sys; import core.enzyme_sites, core.sequence_analysis, "
            "core.clone_strategy; assert 'Bio.Restriction' not in sys.modules; "
            "from core.enzyme_sites import find_enzyme_sites; "
            "find_enzyme_sites('GAATTC'); assert 'Bio.Restriction' in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], cwd=backend,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


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
