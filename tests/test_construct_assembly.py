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
    apply_plan, physical_sites, pick_flanking, plan_gibson, plan_golden_gate,
    plan_restriction,
)

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


def test_restriction_warns_when_enzyme_order_is_reversed():
    """引物端是 e5，但载体上 e3 的切点在前：基因会反向插入，必须告警"""
    _plan, warns = _plan_restriction("BamHI", "XhoI")
    assert any("反向插入" in w for w in warns)


def test_restriction_single_site_is_point_insertion():
    plan, warns = _plan_restriction("BamHI", "BamHI")
    assert plan is not None and plan.left_cut == plan.right_cut
    assert plan.removed == 0
    gene = "ATG" + "GAA" * 20
    construct, _f, istart, iend, _w = apply_plan(VECTOR, [], plan, insert=gene)
    # 单酶切：位点在基因两端各出现一次
    assert construct[istart - 1 - len(plan.head):istart - 1] == plan.head
    assert construct[iend:iend + len(plan.tail)] == plan.tail


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
