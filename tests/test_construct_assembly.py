"""克隆组装：按克隆方法真实模拟切点、切除区间与连接处碱基

用户反馈：全基因合成 / PCR 引物设计出的片段与载体组装后，「图谱上没有准确
识别位点、还残留着应该被限制酶除去的部分」。

根因：三种克隆方法共用一套「把 MCS 注释区间整段替换」的组装——
* Gibson 本应只线性化，却删掉了整段 MCS；
* 双酶切的真实切点不在 MCS 区间内时，该被切走的片段继续留在图谱里；
* Golden Gate 两个 Type IIS 切点之间的片段同样没被切掉。

这里锁住 core/construct_assembly.py 的三种方案与元件的坐标语义。
"""

import pytest

from core.construct_assembly import (
    apply_plan, cassette_direction, physical_sites, pick_flanking, plan_gibson,
    plan_golden_gate, plan_restriction,
)
from core.seq_utils import translate as translate_dna_checked

# 300 bp 的合成环状载体：BamHI(60) 与 XhoI(100) 相隔 40 bp，两端还有 EcoRI(200)
VECTOR = (
    "ATCCGGATATAGTTCCTCCTTTCAGCAAAAAACCCCTCAAGACCCGTTTAGAGGCCCCAAGGGGTTAT"
    "GCTAGTTATTGCTCAGCGGTGGCAGCAGCCAACTCAGCTTCCTTTCGGGCTTTGTTAGCAGCCGGATC"
    "TCAGTGGTGGTGGTGGTGGTGCTCGAGTGCGGCCGCAAGCTTGTCGACGGAGCTCGAATTCGGATCCG"
    "CGACCCATTTGCTGTCCACCAGTCATGCTAGCCATATGGCTGCCGCGCGGCACCAGGCCGCTGCTGTG"
    "ATGATGATGATGATGGCTGCTGCCCATGGTATATCTCCTTCTTAAAGTTAAACAAAATTATTTCTAGA"
)
# 340 bp：BamHI 切点 198、XhoI 切点 158（Ve 之间 40 bp 的废片段）
assert len(VECTOR) == 340


def _sites(enzyme):
    return [(s["position"], s["cut_fwd"]) for s in physical_sites(VECTOR, enzyme)]


def _plan_restriction(e5, e3, window=(150, 200), warnings=None):
    warns = warnings if warnings is not None else []
    return plan_restriction(VECTOR, e5, e3, window=window, circular=True, warnings=warns), warns


# ==================== 位点扫描 ====================

def test_physical_sites_dedupes_palindromic_double_report():
    """回文酶在扫描里正反链各报一条，物理位点仍只有一个"""
    for enzyme in ("BamHI", "XhoI", "EcoRI"):
        cuts = [s["cut_fwd"] for s in physical_sites(VECTOR, enzyme)]
        assert cuts, enzyme
        assert len(cuts) == len(set(cuts))


# ==================== 双酶切 ====================

def test_restriction_removes_the_stuffer_between_cuts():
    plan, warns = _plan_restriction("XhoI", "BamHI")
    assert plan is not None
    assert {plan.left_cut, plan.right_cut} == {158, 198}
    # 切点之间那 40 bp 必须被切走，且不告警「不在 MCS 区间内」
    assert plan.removed == 40
    assert not [w for w in warns if "不在 MCS 区间内" in w]


def test_restriction_construct_removes_stuffer_and_restores_sites():
    """构建体 = 骨架 + 补回位点残留的基因；废片段一个碱基都不剩"""
    plan, _ = _plan_restriction("XhoI", "BamHI")
    gene = "ATG" + "GAA" * 20 + "TAA"
    construct, features, istart, iend, _w = apply_plan(
        VECTOR, [], plan, insert=gene, insert_name="g")
    assert len(construct) == len(VECTOR) - plan.removed + len(plan.head) + len(gene) + len(plan.tail)
    assert construct[istart - 1:iend] == gene
    assert plan.head + gene + plan.tail in construct
    # 被切走的那 40 bp 不应再出现在构建体里
    removed = VECTOR[plan.left_cut:plan.right_cut]
    assert removed and removed not in construct
    # 位点在两端复原
    assert construct[istart - 1 - len(plan.head):istart - 1] == plan.head
    assert construct[iend:iend + len(plan.tail)] == plan.tail


def test_restriction_reverse_assignment_inserts_reverse_complement():
    """引物端是 e5，但载体上 e3 的切点在前：粘端只能反着配，基因按负链插入

    pET-28a 的表达盒整个在负链上（T7 promoter 368-386 为 '-'），BamHI 5' /
    XhoI 3' 的基因必须以反向互补进构建体，否则图谱上 CDS/ORF 整个是反的。
    """
    plan, warns = _plan_restriction("BamHI", "XhoI")
    assert plan is not None and plan.insert_reverse is True
    assert any("负链" in w for w in warns)

    gene = "ATG" + "GAA" * 20 + "TAA"
    construct, features, istart, iend, _w = apply_plan(VECTOR, [], plan, insert=gene, insert_name="g")
    from core.seq_utils import revcomp

    assert construct[istart - 1:iend] == revcomp(gene)
    assert features[0]["strand"] == "-"
    # 按注释链翻译出来仍是目标蛋白（translate 默认在终止符处截断）
    assert translate_dna_checked(revcomp(construct[istart - 1:iend])) == "M" + "E" * 20


def test_restriction_forward_assignment_keeps_gene_on_plus_strand():
    plan, _warns = _plan_restriction("XhoI", "BamHI")
    assert plan is not None and plan.insert_reverse is False
    gene = "ATG" + "GAA" * 20 + "TAA"
    construct, features, istart, iend, _w = apply_plan(VECTOR, [], plan, insert=gene)
    assert construct[istart - 1:iend] == gene
    assert features[0]["strand"] == "+"


def test_restriction_single_site_is_point_insertion():
    plan, warns = _plan_restriction("BamHI", "BamHI")
    assert plan is not None and plan.left_cut == plan.right_cut
    assert plan.removed == 0
    gene = "ATG" + "GAA" * 20
    construct, _f, istart, iend, _w = apply_plan(VECTOR, [], plan, insert=gene)
    # 单酶切：位点在基因两端各出现一次
    assert construct[istart - 1 - len(plan.head):istart - 1] == plan.head
    assert construct[iend:iend + len(plan.tail)] == plan.tail


# 把上面的 340 bp 载体整体转过 170 bp：XhoI/BamHI 的切点跑到编号原点两侧，
# 「两个切点之间」的 40 bp 废片段变成跨原点的一小段（一次性载体常见情形：
# 上传文件的 ORIGIN 是任意的，MCS 往往就压在原点附近）
WRAPPED_VECTOR = VECTOR[170:] + VECTOR[:170]


def test_restriction_ignores_the_wrapped_direction_when_mcs_point_sits_on_a_cut():
    """推测 MCS 是个点、点又正好落在某把酶的切点上时，方向不能选反

    旧口径按「切除区间是否包含 MCS 点」打分：包含 158 的那一侧是 (198, 158]
    = 整圈 300 bp，于是选它 → apply_plan 判「跨原点」→ 回退成 MCS 整段替换，
    废片段没被切走、基因落在位点之外（pET-30a 实测就是这样）。
    """
    plan, warns = _plan_restriction("XhoI", "BamHI", window=(158, 158))
    assert plan is not None
    assert (plan.left_cut, plan.right_cut) == (158, 198)
    assert plan.removed == 40
    assert not [w for w in warns if "超过载体一半" in w]


def test_restriction_assembles_cut_pair_across_origin():
    """切除区间跨原点时旋转组装，不再回退成「MCS 整段替换」"""
    xhoi = physical_sites(WRAPPED_VECTOR, "XhoI")[0]["cut_fwd"]
    bamhi = physical_sites(WRAPPED_VECTOR, "BamHI")[0]["cut_fwd"]
    assert bamhi < xhoi                      # 40 bp 废片段跨原点
    warns: list = []
    plan = plan_restriction(WRAPPED_VECTOR, "BamHI", "XhoI", window=(xhoi, xhoi),
                            circular=True, warnings=warns)
    assert plan is not None
    assert (plan.left_cut, plan.right_cut) == (xhoi, bamhi)
    assert plan.removed == 40
    # 左切点是 XhoI（enzyme_3）：粘端反着配，基因只能以反向互补进构建体
    assert plan.insert_reverse is True

    gene = "ATG" + "GAA" * 20 + "TAA"
    construct, features, istart, iend, _w = apply_plan(
        WRAPPED_VECTOR, [], plan, insert=gene, insert_name="g", circular=True)
    assert features[0]["strand"] == "-"
    assert len(construct) == (len(WRAPPED_VECTOR) - plan.removed
                             + len(plan.head) + len(gene) + len(plan.tail))
    from core.seq_utils import revcomp

    assert construct[istart - 1:iend] == revcomp(gene)
    # 两个位点在连接处复原，基因仍夹在它们之间
    assert construct[istart - 1 - len(plan.head):istart - 1] == plan.head
    assert construct[iend:iend + len(plan.tail)] == plan.tail
    assert physical_sites(construct, "XhoI") and physical_sites(construct, "BamHI")


def test_restriction_without_sites_returns_none_with_warning():
    warns = []
    plan = plan_restriction(VECTOR, "PmeI", "EcoRI", window=(150, 200),
                            circular=True, warnings=warns)
    assert plan is None
    assert any("没有" in w and "PmeI" in w for w in warns)


# ==================== Golden Gate ====================

def test_golden_gate_removes_between_type_iis_cuts():
    # 造一个带两个 BsaI 位点的载体：GGTCTC N + 4 nt overhang
    vector = "AAAA" + "GGTCTC" + "A" + "GATC" + "TTTTTTTTTTTTTTTTTTTT" + \
             "GGTCTC" + "A" + "CGTA" + "CCCC"
    warns = []
    plan = plan_golden_gate(vector, "BsaI", "GATC", "CGTA",
                            window=(20, 40), circular=True, warnings=warns)
    assert plan is not None and plan.removed > 0
    construct, _f, istart, iend, _w = apply_plan(vector, [], plan, insert="ATGAAATAA")
    # 两个位点之间（含 overhang）被切走，插入片段两端补 overhang
    assert construct[istart - 1 - 4:istart - 1] == "GATC"
    assert construct[iend:iend + 4] == "CGTA"
    assert plan.head == "GATC" and plan.tail == "CGTA"


def test_golden_gate_without_sites_returns_none_with_warning():
    warns = []
    plan = plan_golden_gate(VECTOR, "BsaI", "AATG", "GCTT", window=(150, 200),
                            circular=True, warnings=warns)
    assert plan is None
    assert any("没有" in w and "BsaI" in w for w in warns)


# ==================== Gibson ====================

def test_gibson_never_removes_backbone():
    plan = plan_gibson(VECTOR, 123)
    assert plan.left_cut == plan.right_cut == 123
    construct, _f, istart, iend, warnings = apply_plan(VECTOR, [], plan, insert="ATGAAATAA")
    assert len(construct) == len(VECTOR) + 9
    assert construct[:123] + construct[123 + 9:] == VECTOR
    assert istart == 124 and iend == 132
    assert warnings == []


# ==================== 元件坐标 ====================

def test_elements_inside_removed_region_are_dropped_and_downstream_shifted():
    plan, _ = _plan_restriction("XhoI", "BamHI")
    left, right = plan.left_cut, plan.right_cut
    elements = [
        {"name": "up", "type": "promoter", "start": 1, "end": 20},
        {"name": "stuffer", "type": "misc_feature", "start": left + 5, "end": right - 5},
        {"name": "down", "type": "CDS", "start": right + 1, "end": right + 10},
    ]
    gene = "ATG" + "GAA" * 20
    construct, features, istart, _iend, warnings = apply_plan(
        VECTOR, elements, plan, insert=gene, insert_name="g")
    names = {f["name"]: f for f in features}
    assert "stuffer" not in names
    assert any("stuffer" in w for w in warnings)
    assert names["up"]["start"] == 1
    delta = len(plan.head) + len(gene) + len(plan.tail) - plan.removed
    assert names["down"]["start"] == right + 1 + delta


def test_point_insertion_grows_feature_that_contains_it():
    """Gibson 常在 MCS 中间线性化：MCS 元件被撑开，而不是被截断"""
    plan = plan_gibson(VECTOR, 80)
    elements = [{"name": "MCS", "type": "multiple_cloning_site", "start": 60, "end": 100}]
    _c, features, _s, _e, warnings = apply_plan(VECTOR, elements, plan, insert="ATGAAATAA")
    mcs = next(f for f in features if f["name"] == "MCS")
    assert (mcs["start"], mcs["end"]) == (60, 109)
    assert warnings == []


# ==================== 切点挑选 ====================

def test_pick_flanking_prefers_pairs_inside_the_window():
    """pET-28a 的 MCS 中点并不在两个切点之间，只看中点会把主链当废片段"""
    cuts = [100, 140, 1000]
    picked = pick_flanking(cuts, cuts, window=(90, 150), length=3000, circular=True)
    assert picked is not None
    left, right, penalty = picked
    assert penalty == 0
    assert {left, right} == {100, 140}


def test_pick_flanking_without_window_takes_shortest_arc():
    picked = pick_flanking([100, 140, 1000], [100, 140, 1000],
                           window=None, length=3000, circular=True)
    assert picked is not None and picked[2] == 0
    assert {picked[0], picked[1]} == {100, 140}

# ==================== 端到端：设计结果与图谱 ====================

def _run(method, **kw):
    from app.design_service import run_design
    from app.routes.models import DesignRequest, SequenceType

    req = DesignRequest(
        sequence="ATG" + "GAA" * 20 + "TAA",
        sequence_type=SequenceType.DNA,
        sequence_name="geneX",
        vector_id="pET-28a",
        cloning_method=method,
        **kw,
    )
    return run_design(f"design_{method.value}_{len(kw)}", req)


def test_run_design_restriction_removes_mcs_stuffer_end_to_end():
    """pET-28a + XhoI/BamHI：40 bp 废片段消失，构建体长度与图谱对得上"""
    from app.design_service import get_vector_library
    from app.map_documents import design_map_document
    from app.routes.models import CloningMethod, DesignStatus

    vector = get_vector_library().get_vector("pET-28a")
    res = _run(CloningMethod.RESTRICTION, enzyme_5="XhoI", enzyme_3="BamHI")
    assert res.status == DesignStatus.COMPLETED, res.errors

    # 载体 5369 bp，切走 158..198 的 40 bp，补回 TCGAG(5)/G(1) 与基因
    assert (len(res.construct_sequence)
            == len(vector.sequence) - 40 + 5 + len(res.optimized_sequence) + 1)
    stuffer = vector.sequence[158:198]
    assert stuffer not in res.construct_sequence
    # 两端位点复原：TCGAG + 基因 … 基因 + G + GATCC
    assert res.construct_sequence[res.insert_start - 6:res.insert_start - 1] == "TCGAG"
    assert res.construct_sequence[res.insert_end:res.insert_end + 6] == "GGATCC"

    doc = design_map_document(res)
    assert doc["length"] == len(res.construct_sequence)
    insert = next(f for f in doc["features"] if f["source"] == "design")
    assert (insert["start"], insert["end"]) == (res.insert_start, res.insert_end)
    # 图谱的酶切位点是从**构建体序列**重算的：复原的 XhoI/BamHI 位点必须在
    names = {e["name"] for e in doc.get("enzymes", [])}
    assert {"XhoI", "BamHI"} <= names


def test_run_design_gibson_keeps_backbone_and_mcs_end_to_end():
    from app.design_service import get_vector_library
    from app.routes.models import CloningMethod, DesignStatus

    vector = get_vector_library().get_vector("pET-28a")
    res = _run(CloningMethod.GIBSON, insert_pos=None)
    assert res.status == DesignStatus.COMPLETED, res.errors
    assert len(res.construct_sequence) == len(vector.sequence) + len(res.optimized_sequence)
    # 载体序列一个碱基都没丢（只在插入点断开）
    p = res.insert_start - 1
    assert res.construct_sequence[:p] + res.construct_sequence[p + len(res.optimized_sequence):] \
        == vector.sequence

# ==================== 插入方向（负链插入） ====================

def test_run_design_pet28a_bamhi_xhoi_gene_on_minus_strand():
    """pET-28a 的表达盒在负链：BamHI 5' / XhoI 3' 的基因必须反向插入

    载体特征里 T7 promoter(368-386)、RBS、His-tag、T7-tag 全在 '-' 链，
    MCS 的转录方向是 NcoI(296) → BamHI(198) → XhoI(158)。按正链插入会让
    图谱上的 CDS/ORF 与启动子反向。
    """
    from app.design_service import get_vector_library
    from app.map_documents import design_map_document
    from app.routes.models import CloningMethod, DesignStatus
    from core.seq_utils import revcomp, translate

    res = _run(CloningMethod.RESTRICTION, enzyme_5="BamHI", enzyme_3="XhoI")
    assert res.status == DesignStatus.COMPLETED, res.errors

    span = res.construct_sequence[res.insert_start - 1:res.insert_end]
    gene = res.optimized_sequence
    assert span == revcomp(gene), "基因在正链上应为反向互补"
    assert translate(span, stop_at_stop=False) != translate(gene, stop_at_stop=False)
    assert translate(revcomp(span), stop_at_stop=False) == translate(gene, stop_at_stop=False)

    doc = design_map_document(res)
    insert = next(f for f in doc["features"] if f["source"] == "design")
    assert insert["strand"] == -1

    # 载体自己的表达盒也在负链，方向一致
    vector = get_vector_library().get_vector("pET-28a")
    t7 = next(e for e in vector.elements if e.name == "T7 promoter")
    assert str(t7.strand).strip() in ("-", "-1")


def test_run_design_pet28a_xhoi_bamhi_gene_on_plus_strand():
    """对调两端酶后基因落在正链（与载体表达盒相反，供用户按需选择）"""
    from app.design_service import get_vector_library
    from app.map_documents import design_map_document
    from app.routes.models import CloningMethod, DesignStatus

    res = _run(CloningMethod.RESTRICTION, enzyme_5="XhoI", enzyme_3="BamHI")
    assert res.status == DesignStatus.COMPLETED, res.errors
    span = res.construct_sequence[res.insert_start - 1:res.insert_end]
    assert span == res.optimized_sequence

    doc = design_map_document(res)
    insert = next(f for f in doc["features"] if f["source"] == "design")
    assert insert["strand"] == 1


def test_run_design_gibson_insert_stays_on_plus_strand():
    """Gibson 由同源臂定方向，插入片段在正链"""
    from app.map_documents import design_map_document
    from app.routes.models import CloningMethod, DesignStatus

    res = _run(CloningMethod.GIBSON)
    assert res.status == DesignStatus.COMPLETED, res.errors
    span = res.construct_sequence[res.insert_start - 1:res.insert_end]
    assert span == res.optimized_sequence
    doc = design_map_document(res)
    insert = next(f for f in doc["features"] if f["source"] == "design")
    assert insert["strand"] == 1

# ==================== 表达方向自检（cassette_direction） ====================

def _promoter(name, start, end, strand, kind="promoter"):
    return {"name": name, "type": kind, "start": start, "end": end,
            "strand": strand}


def test_cassette_direction_plus_promoter_upstream_means_plus_insert():
    elements = [_promoter("CMV promoter", 100, 118, "+")]
    strand, promoter = cassette_direction(elements, 160, 220)
    assert strand == "+"
    assert promoter["start"] == 100


def test_cassette_direction_minus_promoter_downstream_means_minus_insert():
    elements = [_promoter("T7 promoter", 260, 278, "-")]
    strand, promoter = cassette_direction(elements, 160, 220)
    assert strand == "-"
    assert promoter["end"] == 278


def test_cassette_direction_detects_promoter_by_name_even_if_type_differs():
    elements = [_promoter("T7 promoter", 260, 278, "-", kind="regulatory")]
    strand, _ = cassette_direction(elements, 160, 220)
    assert strand == "-"


def test_cassette_direction_refuses_bidirectional_cassette():
    elements = [
        _promoter("CMV promoter", 100, 118, "+"),
        _promoter("T7 promoter", 260, 278, "-"),
    ]
    assert cassette_direction(elements, 160, 220) == (None, None)


def test_cassette_direction_ignores_promoter_out_of_range():
    elements = [_promoter("T7 promoter", 5000, 5018, "-")]
    assert cassette_direction(elements, 160, 220) == (None, None)


def test_cassette_direction_ignores_wrapped_cut_pair():
    elements = [_promoter("T7 promoter", 260, 278, "-")]
    assert cassette_direction(elements, 300, 40) == (None, None)


